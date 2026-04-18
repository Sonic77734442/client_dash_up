from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol
from uuid import UUID

from fastapi import HTTPException

from app.schemas import (
    AuthIdentityLink,
    ExternalIdentityResolveRequest,
    ExternalIdentityResolveResponse,
    SessionContextResponse,
    SessionIssueRequest,
    SessionValidationResponse,
    UserCreate,
)
from app.services.auth_arch import AuthStore, ROLE_ACCESS_MODEL


class ExternalAuthAdapter(Protocol):
    provider: str

    def normalize_identity(self, raw_payload: Dict[str, Any]) -> ExternalIdentityResolveRequest:
        """Convert provider-specific payload into provider-agnostic identity request."""


@dataclass
class AuthFacadeService:
    auth_store: AuthStore

    def resolve_or_create_from_external_identity(
        self,
        payload: ExternalIdentityResolveRequest,
    ) -> ExternalIdentityResolveResponse:
        # 1) if provider identity already linked => resolve user
        identity = self.auth_store.find_identity(payload.provider, payload.provider_user_id)
        user = None
        if identity:
            user = self.auth_store.get_user(identity.user_id)
            if not user:
                raise HTTPException(status_code=500, detail="identity linked to missing user")
        else:
            # 2) match by email if available
            if payload.email:
                matched = self.auth_store.find_user_by_email(payload.email)
                if matched and not payload.allow_email_merge:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "email already belongs to another internal user; "
                            "automatic merge is disabled by default"
                        ),
                    )
                user = matched

            # 3) create new internal user if needed
            if not user:
                user = self.auth_store.create_user(
                    UserCreate(
                        email=payload.email,
                        name=payload.name or payload.email or f"{payload.provider}:{payload.provider_user_id}",
                        role=payload.default_role,
                        status="active",
                    )
                )

        # 4) upsert identity link to resolved user
        identity = self.auth_store.link_identity(
            AuthIdentityLink(
                user_id=user.id,
                provider=payload.provider,
                provider_user_id=payload.provider_user_id,
                email=payload.email,
                email_verified=payload.email_verified,
                raw_profile=payload.raw_profile,
            )
        )

        # 5) issue backend session if requested
        session = None
        if payload.issue_session:
            session = self.auth_store.issue_session(
                SessionIssueRequest(
                    user_id=user.id,
                    ttl_minutes=payload.session_ttl_minutes,
                    metadata={"provider": payload.provider, "provider_user_id": payload.provider_user_id},
                )
            )

        return ExternalIdentityResolveResponse(user=user, identity=identity, session=session)

    def issue_session(self, user_id: UUID, ttl_minutes: int = 60):
        return self.auth_store.issue_session(SessionIssueRequest(user_id=user_id, ttl_minutes=ttl_minutes))

    def revoke_session(self, token: str):
        return self.auth_store.revoke_session(token)

    def validate_session(self, token: str) -> SessionValidationResponse:
        return self.auth_store.validate_session(token)

    def get_session_context(self, token: str) -> SessionContextResponse:
        valid = self.auth_store.validate_session(token)
        if not valid.valid:
            return SessionContextResponse(valid=False, reason=valid.reason)

        user = self.auth_store.get_user(valid.user_id) if valid.user_id else None
        if not user:
            return SessionContextResponse(valid=False, reason="user_not_found")

        # Role-based tenant access resolution
        role_cfg = ROLE_ACCESS_MODEL.get(user.role, {"scope": "assigned-tenants"})
        if role_cfg.get("scope") == "global":
            client_ids = []
            scope = "all"
            global_access = True
        else:
            access_rows = self.auth_store.list_client_access(user_id=user.id)
            client_ids = [x.client_id for x in access_rows]
            scope = "assigned"
            global_access = False

        return SessionContextResponse(
            valid=True,
            reason=None,
            session_id=valid.session_id,
            user_id=user.id,
            role=user.role,
            global_access=global_access,
            access_scope=scope,
            accessible_client_ids=client_ids,
            expires_at=valid.expires_at,
        )


class AdapterRegistry:
    def __init__(self):
        self._adapters: Dict[str, ExternalAuthAdapter] = {}

    def register(self, adapter: ExternalAuthAdapter) -> None:
        self._adapters[adapter.provider] = adapter

    def get(self, provider: str) -> Optional[ExternalAuthAdapter]:
        return self._adapters.get(provider)
