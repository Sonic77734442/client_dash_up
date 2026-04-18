from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import (
    AuthIdentityLink,
    AuthIdentityOut,
    AuthProviderConfigCreate,
    AuthProviderConfigOut,
    SessionIssueRequest,
    SessionIssueResponse,
    SessionValidationResponse,
    UserClientAccessCreate,
    UserClientAccessOut,
    UserCreate,
    UserOut,
)


ROLE_ACCESS_MODEL = {
    "admin": {
        "scope": "global",
        "description": "Can access all tenants/clients and all internal resources.",
    },
    "agency": {
        "scope": "assigned-tenants",
        "description": "Can access only clients explicitly assigned in user_client_access.",
    },
    "client": {
        "scope": "assigned-tenants",
        "description": "Can access only own assigned clients, no cross-tenant visibility.",
    },
}


class AuthStore(Protocol):
    def create_user(self, payload: UserCreate) -> UserOut: ...
    def get_user(self, user_id: UUID) -> Optional[UserOut]: ...
    def find_user_by_email(self, email: str) -> Optional[UserOut]: ...
    def list_users(self) -> List[UserOut]: ...
    def find_identity(self, provider: str, provider_user_id: str) -> Optional[AuthIdentityOut]: ...
    def link_identity(self, payload: AuthIdentityLink) -> AuthIdentityOut: ...
    def list_identities(self, user_id: Optional[UUID] = None) -> List[AuthIdentityOut]: ...
    def assign_client_access(self, payload: UserClientAccessCreate) -> UserClientAccessOut: ...
    def list_client_access(self, user_id: Optional[UUID] = None) -> List[UserClientAccessOut]: ...
    def issue_session(self, payload: SessionIssueRequest) -> SessionIssueResponse: ...
    def validate_session(self, token: str) -> SessionValidationResponse: ...
    def refresh_session(self, token: str, ttl_minutes: int) -> SessionValidationResponse: ...
    def revoke_session(self, token: str) -> Dict[str, object]: ...
    def upsert_provider_config(self, payload: AuthProviderConfigCreate) -> AuthProviderConfigOut: ...
    def list_provider_configs(self) -> List[AuthProviderConfigOut]: ...


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SqliteAuthStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_user(row) -> UserOut:
        return UserOut(
            id=UUID(row["id"]),
            email=row["email"],
            name=row["name"],
            role=row["role"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_identity(row) -> AuthIdentityOut:
        return AuthIdentityOut(
            id=UUID(row["id"]),
            user_id=UUID(row["user_id"]),
            provider=row["provider"],
            provider_user_id=row["provider_user_id"],
            email=row["email"],
            email_verified=bool(row["email_verified"]) if row["email_verified"] is not None else None,
            raw_profile=json.loads(row["raw_profile"]) if row["raw_profile"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_access(row) -> UserClientAccessOut:
        return UserClientAccessOut(
            id=UUID(row["id"]),
            user_id=UUID(row["user_id"]),
            client_id=UUID(row["client_id"]),
            role=row["role"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_provider_config(row) -> AuthProviderConfigOut:
        return AuthProviderConfigOut(
            id=UUID(row["id"]),
            provider=row["provider"],
            client_id=row["client_id"],
            client_secret=row["client_secret"],
            redirect_uri=row["redirect_uri"],
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create_user(self, payload: UserCreate) -> UserOut:
        now = datetime.utcnow().isoformat()
        user_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO users (id, email, name, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, payload.email, payload.name, payload.role, payload.status, now, now),
                )
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"User conflict: {exc}")
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._to_user(row)

    def get_user(self, user_id: UUID) -> Optional[UserOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (str(user_id),)).fetchone()
        return self._to_user(row) if row else None

    def find_user_by_email(self, email: str) -> Optional[UserOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
        return self._to_user(row) if row else None

    def list_users(self) -> List[UserOut]:
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY updated_at DESC").fetchall()
        return [self._to_user(r) for r in rows]

    def find_identity(self, provider: str, provider_user_id: str) -> Optional[AuthIdentityOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM auth_identities WHERE provider=? AND provider_user_id=?",
                (provider, provider_user_id),
            ).fetchone()
        return self._to_identity(row) if row else None

    def link_identity(self, payload: AuthIdentityLink) -> AuthIdentityOut:
        if not self.get_user(payload.user_id):
            raise HTTPException(status_code=400, detail="user_id does not exist")

        now = datetime.utcnow().isoformat()
        identity_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT * FROM auth_identities WHERE provider=? AND provider_user_id=?",
                (payload.provider, payload.provider_user_id),
            ).fetchone()
            if existing and str(existing["user_id"]) != str(payload.user_id):
                raise HTTPException(status_code=409, detail="provider identity already linked to another internal user")

            if existing:
                conn.execute(
                    """
                    UPDATE auth_identities
                    SET email=?, email_verified=?, raw_profile=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        payload.email,
                        int(payload.email_verified) if payload.email_verified is not None else None,
                        json.dumps(payload.raw_profile, separators=(",", ":"), ensure_ascii=True) if payload.raw_profile else None,
                        now,
                        existing["id"],
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM auth_identities WHERE id=?", (existing["id"],)).fetchone()
                return self._to_identity(row)

            try:
                conn.execute(
                    """
                    INSERT INTO auth_identities
                    (id, user_id, provider, provider_user_id, email, email_verified, raw_profile, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity_id,
                        str(payload.user_id),
                        payload.provider,
                        payload.provider_user_id,
                        payload.email,
                        int(payload.email_verified) if payload.email_verified is not None else None,
                        json.dumps(payload.raw_profile, separators=(",", ":"), ensure_ascii=True) if payload.raw_profile else None,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Identity conflict: {exc}")
            row = conn.execute("SELECT * FROM auth_identities WHERE id=?", (identity_id,)).fetchone()
        return self._to_identity(row)

    def list_identities(self, user_id: Optional[UUID] = None) -> List[AuthIdentityOut]:
        with sqlite_conn(self.db_path) as conn:
            if user_id:
                rows = conn.execute("SELECT * FROM auth_identities WHERE user_id=? ORDER BY updated_at DESC", (str(user_id),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM auth_identities ORDER BY updated_at DESC").fetchall()
        return [self._to_identity(r) for r in rows]

    def assign_client_access(self, payload: UserClientAccessCreate) -> UserClientAccessOut:
        if not self.get_user(payload.user_id):
            raise HTTPException(status_code=400, detail="user_id does not exist")

        now = datetime.utcnow().isoformat()
        access_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            client_exists = conn.execute("SELECT id FROM clients WHERE id=?", (str(payload.client_id),)).fetchone()
            if not client_exists:
                raise HTTPException(status_code=400, detail="client_id does not exist")

            existing = conn.execute(
                "SELECT * FROM user_client_access WHERE user_id=? AND client_id=?",
                (str(payload.user_id), str(payload.client_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_client_access SET role=?, updated_at=? WHERE id=?",
                    (payload.role, now, existing["id"]),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM user_client_access WHERE id=?", (existing["id"],)).fetchone()
                return self._to_access(row)

            conn.execute(
                """
                INSERT INTO user_client_access (id, user_id, client_id, role, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (access_id, str(payload.user_id), str(payload.client_id), payload.role, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM user_client_access WHERE id=?", (access_id,)).fetchone()
        return self._to_access(row)

    def list_client_access(self, user_id: Optional[UUID] = None) -> List[UserClientAccessOut]:
        with sqlite_conn(self.db_path) as conn:
            if user_id:
                rows = conn.execute("SELECT * FROM user_client_access WHERE user_id=? ORDER BY updated_at DESC", (str(user_id),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM user_client_access ORDER BY updated_at DESC").fetchall()
        return [self._to_access(r) for r in rows]

    def issue_session(self, payload: SessionIssueRequest) -> SessionIssueResponse:
        user = self.get_user(payload.user_id)
        if not user or user.status != "active":
            raise HTTPException(status_code=400, detail="cannot issue session for inactive/missing user")

        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=payload.ttl_minutes)
        token = secrets.token_urlsafe(36)
        session_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at, revoked_at, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    session_id,
                    str(payload.user_id),
                    _token_hash(token),
                    expires_at.isoformat(),
                    json.dumps(payload.metadata, separators=(",", ":"), ensure_ascii=True) if payload.metadata else None,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
        return SessionIssueResponse(
            token=token,
            session_id=UUID(session_id),
            user_id=payload.user_id,
            expires_at=expires_at,
        )

    def validate_session(self, token: str) -> SessionValidationResponse:
        now = datetime.utcnow()
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash=?",
                (_token_hash(token),),
            ).fetchone()
            if not row:
                return SessionValidationResponse(valid=False, reason="not_found")

            if row["revoked_at"]:
                return SessionValidationResponse(valid=False, reason="revoked")

            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                return SessionValidationResponse(valid=False, reason="expired")

            user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            if not user or user["status"] != "active":
                return SessionValidationResponse(valid=False, reason="user_inactive")

            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now.isoformat(), row["id"]))
            conn.commit()
            return SessionValidationResponse(
                valid=True,
                reason=None,
                session_id=UUID(row["id"]),
                user_id=UUID(row["user_id"]),
                user_role=user["role"],
                expires_at=expires_at,
            )

    def revoke_session(self, token: str) -> Dict[str, object]:
        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT id FROM sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
            if not row:
                return {"status": "not_found"}
            conn.execute("UPDATE sessions SET revoked_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
            conn.commit()
        return {"status": "revoked"}

    def refresh_session(self, token: str, ttl_minutes: int) -> SessionValidationResponse:
        current = self.validate_session(token)
        if not current.valid or not current.session_id:
            return current
        now = datetime.utcnow()
        new_exp = now + timedelta(minutes=ttl_minutes)
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET expires_at=?, updated_at=? WHERE id=?",
                (new_exp.isoformat(), now.isoformat(), str(current.session_id)),
            )
            conn.commit()
        return SessionValidationResponse(
            valid=True,
            reason=None,
            session_id=current.session_id,
            user_id=current.user_id,
            user_role=current.user_role,
            expires_at=new_exp,
        )

    def upsert_provider_config(self, payload: AuthProviderConfigCreate) -> AuthProviderConfigOut:
        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM auth_provider_configs WHERE provider=?", (payload.provider,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE auth_provider_configs
                    SET client_id=?, client_secret=?, redirect_uri=?, enabled=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        payload.client_id,
                        payload.client_secret,
                        payload.redirect_uri,
                        int(payload.enabled),
                        now,
                        existing["id"],
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM auth_provider_configs WHERE id=?", (existing["id"],)).fetchone()
                return self._to_provider_config(row)

            cfg_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO auth_provider_configs (id, provider, client_id, client_secret, redirect_uri, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cfg_id,
                    payload.provider,
                    payload.client_id,
                    payload.client_secret,
                    payload.redirect_uri,
                    int(payload.enabled),
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM auth_provider_configs WHERE id=?", (cfg_id,)).fetchone()
            return self._to_provider_config(row)

    def list_provider_configs(self) -> List[AuthProviderConfigOut]:
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM auth_provider_configs ORDER BY provider ASC").fetchall()
        return [self._to_provider_config(r) for r in rows]


class InMemoryAuthStore:
    def __init__(self):
        self.users: Dict[UUID, UserOut] = {}
        self.identities: Dict[str, AuthIdentityOut] = {}
        self.access: Dict[str, UserClientAccessOut] = {}
        self.sessions: Dict[str, Dict[str, object]] = {}
        self.provider_cfg: Dict[str, AuthProviderConfigOut] = {}

    def create_user(self, payload: UserCreate) -> UserOut:
        now = datetime.utcnow()
        if payload.email and any(u.email == payload.email for u in self.users.values()):
            raise HTTPException(status_code=409, detail="User conflict: duplicate email")
        rec = UserOut(id=uuid4(), email=payload.email, name=payload.name, role=payload.role, status=payload.status, created_at=now, updated_at=now)
        self.users[rec.id] = rec
        return rec

    def get_user(self, user_id: UUID) -> Optional[UserOut]:
        return self.users.get(user_id)

    def find_user_by_email(self, email: str) -> Optional[UserOut]:
        for u in self.users.values():
            if (u.email or "").lower() == (email or "").lower():
                return u
        return None

    def list_users(self) -> List[UserOut]:
        return sorted(self.users.values(), key=lambda x: x.updated_at, reverse=True)

    def find_identity(self, provider: str, provider_user_id: str) -> Optional[AuthIdentityOut]:
        return self.identities.get(f"{provider}:{provider_user_id}")

    def link_identity(self, payload: AuthIdentityLink) -> AuthIdentityOut:
        if payload.user_id not in self.users:
            raise HTTPException(status_code=400, detail="user_id does not exist")
        key = f"{payload.provider}:{payload.provider_user_id}"
        existing = self.identities.get(key)
        now = datetime.utcnow()
        if existing and existing.user_id != payload.user_id:
            raise HTTPException(status_code=409, detail="provider identity already linked to another internal user")
        if existing:
            rec = existing.model_copy(update={"email": payload.email, "email_verified": payload.email_verified, "raw_profile": payload.raw_profile, "updated_at": now})
        else:
            rec = AuthIdentityOut(
                id=uuid4(),
                user_id=payload.user_id,
                provider=payload.provider,
                provider_user_id=payload.provider_user_id,
                email=payload.email,
                email_verified=payload.email_verified,
                raw_profile=payload.raw_profile,
                created_at=now,
                updated_at=now,
            )
        self.identities[key] = rec
        return rec

    def list_identities(self, user_id: Optional[UUID] = None) -> List[AuthIdentityOut]:
        rows = list(self.identities.values())
        if user_id:
            rows = [x for x in rows if x.user_id == user_id]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def assign_client_access(self, payload: UserClientAccessCreate) -> UserClientAccessOut:
        if payload.user_id not in self.users:
            raise HTTPException(status_code=400, detail="user_id does not exist")
        key = f"{payload.user_id}:{payload.client_id}"
        now = datetime.utcnow()
        existing = self.access.get(key)
        if existing:
            rec = existing.model_copy(update={"role": payload.role, "updated_at": now})
        else:
            rec = UserClientAccessOut(id=uuid4(), user_id=payload.user_id, client_id=payload.client_id, role=payload.role, created_at=now, updated_at=now)
        self.access[key] = rec
        return rec

    def list_client_access(self, user_id: Optional[UUID] = None) -> List[UserClientAccessOut]:
        rows = list(self.access.values())
        if user_id:
            rows = [x for x in rows if x.user_id == user_id]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def issue_session(self, payload: SessionIssueRequest) -> SessionIssueResponse:
        user = self.users.get(payload.user_id)
        if not user or user.status != "active":
            raise HTTPException(status_code=400, detail="cannot issue session for inactive/missing user")
        now = datetime.utcnow()
        exp = now + timedelta(minutes=payload.ttl_minutes)
        token = secrets.token_urlsafe(36)
        sid = uuid4()
        self.sessions[_token_hash(token)] = {
            "session_id": sid,
            "user_id": payload.user_id,
            "expires_at": exp,
            "revoked": False,
        }
        return SessionIssueResponse(token=token, session_id=sid, user_id=payload.user_id, expires_at=exp)

    def validate_session(self, token: str) -> SessionValidationResponse:
        now = datetime.utcnow()
        s = self.sessions.get(_token_hash(token))
        if not s:
            return SessionValidationResponse(valid=False, reason="not_found")
        if s["revoked"]:
            return SessionValidationResponse(valid=False, reason="revoked")
        if s["expires_at"] <= now:
            return SessionValidationResponse(valid=False, reason="expired")
        u = self.users.get(s["user_id"])
        if not u or u.status != "active":
            return SessionValidationResponse(valid=False, reason="user_inactive")
        return SessionValidationResponse(valid=True, reason=None, session_id=s["session_id"], user_id=u.id, user_role=u.role, expires_at=s["expires_at"])

    def revoke_session(self, token: str) -> Dict[str, object]:
        s = self.sessions.get(_token_hash(token))
        if not s:
            return {"status": "not_found"}
        s["revoked"] = True
        return {"status": "revoked"}

    def refresh_session(self, token: str, ttl_minutes: int) -> SessionValidationResponse:
        current = self.validate_session(token)
        if not current.valid or not current.session_id:
            return current
        now = datetime.utcnow()
        new_exp = now + timedelta(minutes=ttl_minutes)
        key = _token_hash(token)
        if key in self.sessions:
            self.sessions[key]["expires_at"] = new_exp
        return SessionValidationResponse(
            valid=True,
            reason=None,
            session_id=current.session_id,
            user_id=current.user_id,
            user_role=current.user_role,
            expires_at=new_exp,
        )

    def upsert_provider_config(self, payload: AuthProviderConfigCreate) -> AuthProviderConfigOut:
        now = datetime.utcnow()
        existing = self.provider_cfg.get(payload.provider)
        if existing:
            rec = existing.model_copy(update={"client_id": payload.client_id, "client_secret": payload.client_secret, "redirect_uri": payload.redirect_uri, "enabled": payload.enabled, "updated_at": now})
        else:
            rec = AuthProviderConfigOut(
                id=uuid4(),
                provider=payload.provider,
                client_id=payload.client_id,
                client_secret=payload.client_secret,
                redirect_uri=payload.redirect_uri,
                enabled=payload.enabled,
                created_at=now,
                updated_at=now,
            )
        self.provider_cfg[payload.provider] = rec
        return rec

    def list_provider_configs(self) -> List[AuthProviderConfigOut]:
        return [self.provider_cfg[k] for k in sorted(self.provider_cfg.keys())]
