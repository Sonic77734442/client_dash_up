from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Callable, FrozenSet, Optional, Protocol, Tuple
from uuid import UUID, uuid4


class BudgetTargetType(str, Enum):
    CAMPAIGN = "campaign"
    AD_SET = "ad_set"
    ACCOUNT = "account"


class BudgetField(str, Enum):
    DAILY_BUDGET = "daily_budget"
    LIFETIME_BUDGET = "lifetime_budget"
    SPEND_CAP = "spend_cap"


class BudgetCommandStatus(str, Enum):
    PREVIEWED = "previewed"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    APPLIED = "applied"
    CONFLICT = "conflict"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActorRole(str, Enum):
    ADMIN = "admin"
    AGENCY = "agency"
    CLIENT = "client"
    SOLO_CLIENT = "solo_client"


class CredentialScope(str, Enum):
    GLOBAL = "global"
    AGENCY = "agency"
    CLIENT = "client"


class AgencyMemberRole(str, Enum):
    OWNER = "owner"
    MANAGER = "manager"
    MEMBER = "member"


class ProviderBudgetCommandError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BudgetValidationError(ProviderBudgetCommandError):
    pass


class BudgetPolicyError(ProviderBudgetCommandError):
    pass


class PreviewTokenError(ProviderBudgetCommandError):
    pass


class PreviewExpiredError(PreviewTokenError):
    pass


class IdempotencyConflictError(ProviderBudgetCommandError):
    pass


class BudgetConflictError(ProviderBudgetCommandError):
    pass


class CommandNotExecutableError(ProviderBudgetCommandError):
    pass


_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(r"(?i)(access_token|client_secret|appsecret_proof)=([^&\s]+)")
_JSON_SECRET = re.compile(
    r'''(?i)(["']?(?:access_token|client_secret|appsecret_proof)["']?\s*[:=]\s*["']?)([^"'\s,}&]+)'''
)


def sanitize_provider_message(
    value: object,
    *,
    secrets_to_redact: Tuple[str, ...] = (),
    max_length: int = 300,
) -> str:
    """Return a compact provider error safe for persistence and user responses."""
    message = " ".join(str(value or "Provider request failed").split())
    message = _BEARER_SECRET.sub("Bearer [REDACTED]", message)
    message = _QUERY_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", message)
    message = _JSON_SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", message)
    for secret in secrets_to_redact:
        secret_text = str(secret or "")
        if secret_text:
            message = message.replace(secret_text, "[REDACTED]")
    if len(message) > max_length:
        message = f"{message[: max_length - 3]}..."
    return message


@dataclass(frozen=True)
class SanitizedProviderError:
    code: str
    message: str
    subcode: Optional[str] = None
    trace_id: Optional[str] = None
    retryable: bool = False


class ProviderBudgetError(ProviderBudgetCommandError):
    """A definitive provider rejection or a read-only provider failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        subcode: Optional[str] = None,
        trace_id: Optional[str] = None,
        retryable: bool = False,
    ):
        safe_message = sanitize_provider_message(message)
        super().__init__(code, safe_message)
        self.provider_error = SanitizedProviderError(
            code=str(code or "provider_error"),
            message=safe_message,
            subcode=str(subcode) if subcode is not None else None,
            trace_id=str(trace_id) if trace_id else None,
            retryable=bool(retryable),
        )


class ProviderWriteOutcomeUnknown(ProviderBudgetCommandError):
    """The provider may have accepted the mutation; callers must only reconcile by reading."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        trace_id: Optional[str] = None,
    ):
        safe_message = sanitize_provider_message(message)
        super().__init__(code, safe_message)
        self.provider_error = SanitizedProviderError(
            code=str(code or "provider_write_unknown"),
            message=safe_message,
            trace_id=str(trace_id) if trace_id else None,
            retryable=False,
        )


def _enum_value(enum_type, value, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise BudgetValidationError(
            "invalid_enum",
            f"{field_name} must be one of: {allowed}",
        ) from exc


def _require_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise BudgetValidationError("invalid_uuid", f"{field_name} must be a UUID")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise BudgetValidationError("invalid_boolean", f"{field_name} must be a boolean")
    return value


def _require_minor_units(value: object, field_name: str, *, allow_zero: bool = True) -> int:
    if type(value) is not int:
        raise BudgetValidationError(
            "invalid_minor_units",
            f"{field_name} must be an integer in account minor units",
        )
    if value < 0 or (not allow_zero and value == 0):
        comparator = "greater than zero" if not allow_zero else "zero or greater"
        raise BudgetValidationError("invalid_minor_units", f"{field_name} must be {comparator}")
    return value


def _require_provider_id(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[0-9]+", normalized):
        raise BudgetValidationError(
            "invalid_provider_id",
            f"{field_name} must be a normalized numeric Meta object id",
        )
    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BudgetChangeRequest:
    client_id: UUID
    ad_account_id: UUID
    provider_account_id: str
    credential_id: UUID
    target_type: BudgetTargetType
    provider_target_id: str
    field: BudgetField
    amount_minor: int
    currency: str
    reason: str
    expected_current_minor: Optional[int] = None
    agency_id: Optional[UUID] = None
    source_action_id: Optional[UUID] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _require_uuid(self.client_id, "client_id"))
        object.__setattr__(self, "ad_account_id", _require_uuid(self.ad_account_id, "ad_account_id"))
        object.__setattr__(self, "credential_id", _require_uuid(self.credential_id, "credential_id"))
        if self.agency_id is not None:
            object.__setattr__(self, "agency_id", _require_uuid(self.agency_id, "agency_id"))
        if self.source_action_id is not None:
            object.__setattr__(self, "source_action_id", _require_uuid(self.source_action_id, "source_action_id"))

        target_type = _enum_value(BudgetTargetType, self.target_type, "target_type")
        budget_field = _enum_value(BudgetField, self.field, "field")
        object.__setattr__(self, "target_type", target_type)
        object.__setattr__(self, "field", budget_field)

        allowed_fields = {
            BudgetTargetType.CAMPAIGN: {BudgetField.DAILY_BUDGET, BudgetField.LIFETIME_BUDGET},
            BudgetTargetType.AD_SET: {BudgetField.DAILY_BUDGET, BudgetField.LIFETIME_BUDGET},
            BudgetTargetType.ACCOUNT: {BudgetField.SPEND_CAP},
        }
        if budget_field not in allowed_fields[target_type]:
            raise BudgetValidationError(
                "target_field_mismatch",
                f"{budget_field.value} cannot be changed on {target_type.value}",
            )

        account_id = _require_provider_id(self.provider_account_id, "provider_account_id")
        target_id = _require_provider_id(self.provider_target_id, "provider_target_id")
        if target_type == BudgetTargetType.ACCOUNT and target_id != account_id:
            raise BudgetValidationError(
                "account_target_mismatch",
                "Account spend_cap target must match provider_account_id",
            )
        object.__setattr__(self, "provider_account_id", account_id)
        object.__setattr__(self, "provider_target_id", target_id)

        amount = _require_minor_units(
            self.amount_minor,
            "amount_minor",
            allow_zero=budget_field == BudgetField.SPEND_CAP,
        )
        object.__setattr__(self, "amount_minor", amount)
        if self.expected_current_minor is not None:
            object.__setattr__(
                self,
                "expected_current_minor",
                _require_minor_units(self.expected_current_minor, "expected_current_minor"),
            )

        currency = str(self.currency or "").strip()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise BudgetValidationError(
                "invalid_currency",
                "currency must be an uppercase ISO-style three-letter code",
            )
        object.__setattr__(self, "currency", currency)

        reason = " ".join(str(self.reason or "").split())
        if not reason or len(reason) > 500:
            raise BudgetValidationError("invalid_reason", "reason is required and must be at most 500 characters")
        object.__setattr__(self, "reason", reason)

    def canonical_payload(self) -> dict:
        return {
            "provider": "meta",
            "client_id": str(self.client_id),
            "ad_account_id": str(self.ad_account_id),
            "provider_account_id": self.provider_account_id,
            "credential_id": str(self.credential_id),
            "agency_id": str(self.agency_id) if self.agency_id else None,
            "target_type": self.target_type.value,
            "provider_target_id": self.provider_target_id,
            "field": self.field.value,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "expected_current_minor": self.expected_current_minor,
            "reason": self.reason,
            "source_action_id": str(self.source_action_id) if self.source_action_id else None,
        }


@dataclass(frozen=True)
class BudgetSnapshot:
    target_type: BudgetTargetType
    provider_target_id: str
    provider_account_id: str
    field: BudgetField
    amount_minor: int
    currency: str
    observed_at: datetime
    provider_trace_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_type", _enum_value(BudgetTargetType, self.target_type, "target_type"))
        object.__setattr__(self, "field", _enum_value(BudgetField, self.field, "field"))
        object.__setattr__(self, "provider_target_id", _require_provider_id(self.provider_target_id, "provider_target_id"))
        object.__setattr__(self, "provider_account_id", _require_provider_id(self.provider_account_id, "provider_account_id"))
        object.__setattr__(self, "amount_minor", _require_minor_units(self.amount_minor, "amount_minor"))
        currency = str(self.currency or "").strip()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise BudgetValidationError("invalid_currency", "Provider snapshot currency is invalid")
        object.__setattr__(self, "currency", currency)
        if not isinstance(self.observed_at, datetime):
            raise BudgetValidationError("invalid_timestamp", "observed_at must be a datetime")
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at))


@dataclass(frozen=True)
class ProviderWriteReceipt:
    accepted: bool
    provider_trace_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted", _require_bool(self.accepted, "accepted"))


class ProviderBudgetAdapter(Protocol):
    def read_budget(self, request: BudgetChangeRequest) -> BudgetSnapshot: ...

    def write_budget(self, request: BudgetChangeRequest) -> ProviderWriteReceipt: ...


@dataclass(frozen=True)
class BudgetActorContext:
    user_id: UUID
    role: ActorRole
    accessible_client_ids: FrozenSet[UUID] = frozenset()
    selected_agency_id: Optional[UUID] = None
    agency_active: bool = False
    agency_member_role: Optional[AgencyMemberRole] = None
    agency_bound_client_ids: FrozenSet[UUID] = frozenset()
    solo_owner_client_id: Optional[UUID] = None
    can_admin_provider_write: bool = False
    can_manage_spend_cap: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_id", _require_uuid(self.user_id, "user_id"))
        object.__setattr__(self, "role", _enum_value(ActorRole, self.role, "role"))
        if self.selected_agency_id is not None:
            object.__setattr__(
                self,
                "selected_agency_id",
                _require_uuid(self.selected_agency_id, "selected_agency_id"),
            )
        if self.solo_owner_client_id is not None:
            object.__setattr__(
                self,
                "solo_owner_client_id",
                _require_uuid(self.solo_owner_client_id, "solo_owner_client_id"),
            )
        if self.agency_member_role is not None:
            object.__setattr__(
                self,
                "agency_member_role",
                _enum_value(AgencyMemberRole, self.agency_member_role, "agency_member_role"),
            )
        accessible = frozenset(
            _require_uuid(value, "accessible_client_id") for value in self.accessible_client_ids
        )
        agency_bound = frozenset(
            _require_uuid(value, "agency_bound_client_id") for value in self.agency_bound_client_ids
        )
        object.__setattr__(self, "accessible_client_ids", accessible)
        object.__setattr__(self, "agency_bound_client_ids", agency_bound)
        object.__setattr__(
            self,
            "agency_active",
            _require_bool(self.agency_active, "agency_active"),
        )
        object.__setattr__(
            self,
            "can_admin_provider_write",
            _require_bool(self.can_admin_provider_write, "can_admin_provider_write"),
        )
        object.__setattr__(
            self,
            "can_manage_spend_cap",
            _require_bool(self.can_manage_spend_cap, "can_manage_spend_cap"),
        )


@dataclass(frozen=True)
class BudgetTenantContext:
    client_id: UUID
    ad_account_id: UUID
    account_client_id: UUID
    client_active: bool
    account_active: bool
    account_platform: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _require_uuid(self.client_id, "client_id"))
        object.__setattr__(self, "ad_account_id", _require_uuid(self.ad_account_id, "ad_account_id"))
        object.__setattr__(self, "account_client_id", _require_uuid(self.account_client_id, "account_client_id"))
        object.__setattr__(self, "account_platform", str(self.account_platform or "").strip().lower())
        object.__setattr__(self, "client_active", _require_bool(self.client_active, "client_active"))
        object.__setattr__(self, "account_active", _require_bool(self.account_active, "account_active"))


@dataclass(frozen=True)
class BudgetCredentialContext:
    id: UUID
    provider: str
    status: str
    scope_type: CredentialScope
    scope_id: Optional[UUID]
    created_by: Optional[UUID]
    has_ads_management: bool
    revision: int
    meta_app_id: str
    meta_business_config_id: str
    meta_user_id: str
    granted_permissions: Tuple[str, ...]
    validation_version: int
    validated_at: datetime
    expired: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_uuid(self.id, "credential_id"))
        object.__setattr__(self, "provider", str(self.provider or "").strip().lower())
        object.__setattr__(self, "status", str(self.status or "").strip().lower())
        object.__setattr__(self, "scope_type", _enum_value(CredentialScope, self.scope_type, "credential_scope"))
        if self.scope_id is not None:
            object.__setattr__(self, "scope_id", _require_uuid(self.scope_id, "credential_scope_id"))
        if self.created_by is not None:
            object.__setattr__(self, "created_by", _require_uuid(self.created_by, "credential_created_by"))
        object.__setattr__(
            self,
            "has_ads_management",
            _require_bool(self.has_ads_management, "has_ads_management"),
        )
        if type(self.revision) is not int or self.revision < 1:
            raise BudgetValidationError("credential_revision_invalid", "credential revision must be a positive integer")
        object.__setattr__(self, "meta_app_id", str(self.meta_app_id or "").strip())
        object.__setattr__(
            self,
            "meta_business_config_id",
            str(self.meta_business_config_id or "").strip(),
        )
        object.__setattr__(self, "meta_user_id", str(self.meta_user_id or "").strip())
        if not self.meta_app_id or not self.meta_business_config_id or not self.meta_user_id:
            raise BudgetValidationError(
                "credential_validation_snapshot_incomplete",
                "validated Meta app, business configuration and user identities are required",
            )
        permissions = tuple(
            sorted(
                {
                    str(value or "").strip().lower()
                    for value in self.granted_permissions
                    if str(value or "").strip()
                }
            )
        )
        if not permissions:
            raise BudgetValidationError(
                "credential_validation_snapshot_incomplete",
                "validated Meta permissions are required",
            )
        object.__setattr__(self, "granted_permissions", permissions)
        if type(self.validation_version) is not int or self.validation_version < 1:
            raise BudgetValidationError(
                "credential_validation_snapshot_incomplete",
                "credential validation version must be a positive integer",
            )
        object.__setattr__(self, "validated_at", _aware_utc(self.validated_at))
        object.__setattr__(self, "expired", _require_bool(self.expired, "expired"))


@dataclass(frozen=True)
class BudgetExecutionAuthorization:
    """Fresh, non-secret authorization facts checked at the final write boundary."""

    actor: BudgetActorContext
    tenant: BudgetTenantContext
    credential: BudgetCredentialContext
    provider_account_id: str
    feature_enabled: bool
    client_rollout_allowed: bool
    binding_verified: bool
    spend_cap_enabled: bool

    def __post_init__(self) -> None:
        if not isinstance(self.actor, BudgetActorContext):
            raise BudgetValidationError("execution_actor_invalid", "Final execution actor context is invalid")
        if not isinstance(self.tenant, BudgetTenantContext):
            raise BudgetValidationError("execution_tenant_invalid", "Final execution tenant context is invalid")
        if not isinstance(self.credential, BudgetCredentialContext):
            raise BudgetValidationError("execution_credential_invalid", "Final execution credential context is invalid")
        object.__setattr__(
            self,
            "provider_account_id",
            _require_provider_id(self.provider_account_id, "provider_account_id"),
        )
        object.__setattr__(self, "feature_enabled", _require_bool(self.feature_enabled, "feature_enabled"))
        object.__setattr__(
            self,
            "client_rollout_allowed",
            _require_bool(self.client_rollout_allowed, "client_rollout_allowed"),
        )
        object.__setattr__(self, "binding_verified", _require_bool(self.binding_verified, "binding_verified"))
        object.__setattr__(
            self,
            "spend_cap_enabled",
            _require_bool(self.spend_cap_enabled, "spend_cap_enabled"),
        )


@dataclass(frozen=True)
class BudgetAuthorizationSnapshot:
    """Non-secret immutable proof of the authorization facts used for a command."""

    actor_role: Optional[ActorRole]
    selected_agency_id: Optional[UUID]
    agency_member_role: Optional[AgencyMemberRole]
    agency_active: bool
    can_admin_provider_write: bool
    can_manage_spend_cap: bool
    has_client_access: bool
    agency_client_bound: bool
    solo_client_owned: bool
    client_active: Optional[bool] = None
    account_active: Optional[bool] = None
    feature_enabled: Optional[bool] = None
    client_rollout_allowed: Optional[bool] = None
    binding_verified: Optional[bool] = None
    provider_account_id: Optional[str] = None
    spend_cap_enabled: Optional[bool] = None
    final_gate_verified: Optional[bool] = None
    final_gate_failure_code: Optional[str] = None
    snapshot_version: int = 1
    legacy_unverified: bool = False

    def canonical_payload(self) -> dict:
        return {
            "actor_role": self.actor_role.value if self.actor_role else None,
            "selected_agency_id": str(self.selected_agency_id) if self.selected_agency_id else None,
            "agency_member_role": self.agency_member_role.value if self.agency_member_role else None,
            "agency_active": bool(self.agency_active),
            "can_admin_provider_write": bool(self.can_admin_provider_write),
            "can_manage_spend_cap": bool(self.can_manage_spend_cap),
            "has_client_access": bool(self.has_client_access),
            "agency_client_bound": bool(self.agency_client_bound),
            "solo_client_owned": bool(self.solo_client_owned),
            "client_active": self.client_active,
            "account_active": self.account_active,
            "feature_enabled": self.feature_enabled,
            "client_rollout_allowed": self.client_rollout_allowed,
            "binding_verified": self.binding_verified,
            "provider_account_id": self.provider_account_id,
            "spend_cap_enabled": self.spend_cap_enabled,
            "final_gate_verified": self.final_gate_verified,
            "final_gate_failure_code": self.final_gate_failure_code,
            "snapshot_version": int(self.snapshot_version),
            "legacy_unverified": bool(self.legacy_unverified),
        }


@dataclass(frozen=True)
class BudgetCredentialAuditSnapshot:
    """Non-secret Meta validation proof. Credential identity stays internal."""

    revision: int
    meta_app_id: str
    meta_business_config_id: str
    meta_user_id: str
    granted_permissions: Tuple[str, ...]
    validation_version: int
    validated_at: Optional[datetime]
    final_gate_verified: Optional[bool] = None
    final_gate_failure_code: Optional[str] = None
    snapshot_version: int = 1
    legacy_unverified: bool = False

    def canonical_payload(self) -> dict:
        return {
            "revision": int(self.revision),
            "meta_app_id": self.meta_app_id,
            "meta_business_config_id": self.meta_business_config_id,
            "meta_user_id": self.meta_user_id,
            "granted_permissions": list(self.granted_permissions),
            "validation_version": int(self.validation_version),
            "validated_at": (
                _aware_utc(self.validated_at).isoformat()
                if self.validated_at is not None
                else None
            ),
            "final_gate_verified": self.final_gate_verified,
            "final_gate_failure_code": self.final_gate_failure_code,
            "snapshot_version": int(self.snapshot_version),
            "legacy_unverified": bool(self.legacy_unverified),
        }


def budget_authorization_snapshot(
    actor: BudgetActorContext,
    request: BudgetChangeRequest,
) -> BudgetAuthorizationSnapshot:
    return BudgetAuthorizationSnapshot(
        actor_role=actor.role,
        selected_agency_id=actor.selected_agency_id,
        agency_member_role=actor.agency_member_role,
        agency_active=actor.agency_active,
        can_admin_provider_write=actor.can_admin_provider_write,
        can_manage_spend_cap=actor.can_manage_spend_cap,
        has_client_access=(
            actor.role == ActorRole.ADMIN or request.client_id in actor.accessible_client_ids
        ),
        agency_client_bound=request.client_id in actor.agency_bound_client_ids,
        solo_client_owned=actor.solo_owner_client_id == request.client_id,
        snapshot_version=1,
        legacy_unverified=False,
    )


def budget_execution_authorization_snapshot(
    authorization: BudgetExecutionAuthorization,
    request: BudgetChangeRequest,
    *,
    final_gate_verified: bool = True,
    final_gate_failure_code: Optional[str] = None,
) -> BudgetAuthorizationSnapshot:
    """Persist the exact live gate facts used immediately before a provider write."""

    verified = _require_bool(final_gate_verified, "final_gate_verified")
    safe_failure_code: Optional[str] = None
    if not verified:
        candidate = str(final_gate_failure_code or "provider_budget_reauthorization_failed").strip().lower()
        safe_failure_code = (
            candidate
            if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", candidate)
            else "provider_budget_reauthorization_failed"
        )
    base = budget_authorization_snapshot(authorization.actor, request)
    return replace(
        base,
        client_active=authorization.tenant.client_active,
        account_active=authorization.tenant.account_active,
        feature_enabled=authorization.feature_enabled,
        client_rollout_allowed=authorization.client_rollout_allowed,
        binding_verified=authorization.binding_verified,
        provider_account_id=authorization.provider_account_id,
        spend_cap_enabled=authorization.spend_cap_enabled,
        final_gate_verified=verified,
        final_gate_failure_code=safe_failure_code,
        snapshot_version=2,
    )


def budget_execution_failure_snapshot(*, failure_code: str) -> BudgetAuthorizationSnapshot:
    """Record a failed live gate without presenting preview-time facts as current."""

    safe_code = str(failure_code or "provider_budget_reauthorization_failed").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", safe_code):
        safe_code = "provider_budget_reauthorization_failed"
    return BudgetAuthorizationSnapshot(
        # No live authorization object was produced. Keep the attempt actor id
        # separately, but never present preview-time role/scope as live facts.
        actor_role=None,
        selected_agency_id=None,
        agency_member_role=None,
        agency_active=False,
        can_admin_provider_write=False,
        can_manage_spend_cap=False,
        has_client_access=False,
        agency_client_bound=False,
        solo_client_owned=False,
        client_active=None,
        account_active=None,
        feature_enabled=None,
        client_rollout_allowed=None,
        binding_verified=None,
        provider_account_id=None,
        spend_cap_enabled=None,
        final_gate_verified=False,
        final_gate_failure_code=safe_code,
        snapshot_version=2,
        legacy_unverified=True,
    )


def budget_credential_failure_snapshot(*, failure_code: str) -> BudgetCredentialAuditSnapshot:
    """Explicit unknown sentinel for a final gate that returned no credential."""

    safe_code = str(failure_code or "provider_budget_reauthorization_failed").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", safe_code):
        safe_code = "provider_budget_reauthorization_failed"
    return BudgetCredentialAuditSnapshot(
        revision=0,
        meta_app_id="",
        meta_business_config_id="",
        meta_user_id="",
        granted_permissions=(),
        validation_version=0,
        validated_at=None,
        final_gate_verified=False,
        final_gate_failure_code=safe_code,
        snapshot_version=2,
        legacy_unverified=True,
    )


def budget_credential_audit_snapshot(
    credential: BudgetCredentialContext,
) -> BudgetCredentialAuditSnapshot:
    return BudgetCredentialAuditSnapshot(
        revision=credential.revision,
        meta_app_id=credential.meta_app_id,
        meta_business_config_id=credential.meta_business_config_id,
        meta_user_id=credential.meta_user_id,
        granted_permissions=credential.granted_permissions,
        validation_version=credential.validation_version,
        validated_at=credential.validated_at,
        snapshot_version=1,
        legacy_unverified=False,
    )


def budget_execution_credential_snapshot(
    credential: BudgetCredentialContext,
    *,
    final_gate_verified: bool,
    final_gate_failure_code: Optional[str] = None,
) -> BudgetCredentialAuditSnapshot:
    """Persist a fresh credential proof and whether the whole live gate passed."""

    verified = _require_bool(final_gate_verified, "final_gate_verified")
    safe_failure_code: Optional[str] = None
    if not verified:
        candidate = str(final_gate_failure_code or "provider_budget_reauthorization_failed").strip().lower()
        safe_failure_code = (
            candidate
            if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", candidate)
            else "provider_budget_reauthorization_failed"
        )
    return replace(
        budget_credential_audit_snapshot(credential),
        final_gate_verified=verified,
        final_gate_failure_code=safe_failure_code,
        snapshot_version=2,
    )


def authorization_snapshot_from_payload(payload: object) -> BudgetAuthorizationSnapshot:
    values = payload if isinstance(payload, dict) else {}
    role = values.get("actor_role", ActorRole.CLIENT.value)
    agency_role = values.get("agency_member_role")
    agency_id = values.get("selected_agency_id")
    return BudgetAuthorizationSnapshot(
        actor_role=(_enum_value(ActorRole, role, "actor_role") if role else None),
        selected_agency_id=UUID(str(agency_id)) if agency_id else None,
        agency_member_role=(
            _enum_value(AgencyMemberRole, agency_role, "agency_member_role")
            if agency_role
            else None
        ),
        agency_active=bool(values.get("agency_active", False)),
        can_admin_provider_write=bool(values.get("can_admin_provider_write", False)),
        can_manage_spend_cap=bool(values.get("can_manage_spend_cap", False)),
        has_client_access=bool(values.get("has_client_access", False)),
        agency_client_bound=bool(values.get("agency_client_bound", False)),
        solo_client_owned=bool(values.get("solo_client_owned", False)),
        client_active=(bool(values["client_active"]) if "client_active" in values and values["client_active"] is not None else None),
        account_active=(bool(values["account_active"]) if "account_active" in values and values["account_active"] is not None else None),
        feature_enabled=(bool(values["feature_enabled"]) if "feature_enabled" in values and values["feature_enabled"] is not None else None),
        client_rollout_allowed=(
            bool(values["client_rollout_allowed"])
            if "client_rollout_allowed" in values and values["client_rollout_allowed"] is not None
            else None
        ),
        binding_verified=(bool(values["binding_verified"]) if "binding_verified" in values and values["binding_verified"] is not None else None),
        provider_account_id=(str(values["provider_account_id"]) if values.get("provider_account_id") is not None else None),
        spend_cap_enabled=(
            bool(values["spend_cap_enabled"])
            if "spend_cap_enabled" in values and values["spend_cap_enabled"] is not None
            else None
        ),
        final_gate_verified=(
            bool(values["final_gate_verified"])
            if "final_gate_verified" in values and values["final_gate_verified"] is not None
            else None
        ),
        final_gate_failure_code=(
            str(values["final_gate_failure_code"])
            if values.get("final_gate_failure_code") is not None
            else None
        ),
        snapshot_version=int(values.get("snapshot_version", 0) or 0),
        legacy_unverified=bool(values.get("legacy_unverified", not bool(values))),
    )


def credential_snapshot_from_payload(payload: object) -> BudgetCredentialAuditSnapshot:
    values = payload if isinstance(payload, dict) else {}
    validated_at_value = values.get("validated_at")
    validated_at = (
        datetime.fromisoformat(str(validated_at_value).replace("Z", "+00:00"))
        if validated_at_value
        else None
    )
    return BudgetCredentialAuditSnapshot(
        revision=max(0, int(values.get("revision", 0) or 0)),
        meta_app_id=str(values.get("meta_app_id") or ""),
        meta_business_config_id=str(values.get("meta_business_config_id") or ""),
        meta_user_id=str(values.get("meta_user_id") or ""),
        granted_permissions=tuple(
            sorted(
                {
                    str(value or "").strip().lower()
                    for value in values.get("granted_permissions", [])
                    if str(value or "").strip()
                }
            )
        ),
        validation_version=max(0, int(values.get("validation_version", 0) or 0)),
        validated_at=(_aware_utc(validated_at) if validated_at is not None else None),
        final_gate_verified=(
            bool(values["final_gate_verified"])
            if "final_gate_verified" in values and values["final_gate_verified"] is not None
            else None
        ),
        final_gate_failure_code=(
            str(values["final_gate_failure_code"])
            if values.get("final_gate_failure_code") is not None
            else None
        ),
        snapshot_version=int(values.get("snapshot_version", 0) or 0),
        legacy_unverified=bool(values.get("legacy_unverified", not bool(values))),
    )


def authorize_budget_write(
    request: BudgetChangeRequest,
    *,
    actor: BudgetActorContext,
    tenant: BudgetTenantContext,
    credential: BudgetCredentialContext,
) -> None:
    """Fail-closed authorization independent of FastAPI and mutable account metadata."""
    if tenant.client_id != request.client_id or tenant.account_client_id != request.client_id:
        raise BudgetPolicyError("tenant_mismatch", "Advertising account does not belong to the requested client")
    if tenant.ad_account_id != request.ad_account_id:
        raise BudgetPolicyError("account_mismatch", "Advertising account identity does not match the request")
    if not tenant.client_active or not tenant.account_active:
        raise BudgetPolicyError("inactive_scope", "Client and advertising account must both be active")
    if tenant.account_platform != "meta":
        raise BudgetPolicyError("provider_mismatch", "Only Meta accounts are supported by this policy")

    if credential.id != request.credential_id:
        raise BudgetPolicyError("credential_mismatch", "Explicit credential binding does not match the request")
    if credential.provider != "meta" or credential.status != "active":
        raise BudgetPolicyError("credential_unavailable", "An active Meta credential is required")
    if credential.expired or not credential.has_ads_management:
        raise BudgetPolicyError("credential_permission_missing", "Credential must be current and grant ads_management")

    if actor.role != ActorRole.ADMIN and request.client_id not in actor.accessible_client_ids:
        raise BudgetPolicyError("tenant_access_denied", "Actor cannot access the requested client")

    if actor.role == ActorRole.CLIENT:
        raise BudgetPolicyError("client_read_only", "Client role cannot change provider budgets")

    if actor.role == ActorRole.SOLO_CLIENT:
        if actor.solo_owner_client_id != request.client_id:
            raise BudgetPolicyError("solo_scope_mismatch", "Solo owner may change budgets only in the owned client")
        if request.agency_id is not None:
            raise BudgetPolicyError("solo_agency_conflict", "Solo owner request cannot select an agency")
        if (
            credential.scope_type != CredentialScope.CLIENT
            or credential.scope_id != request.client_id
            or credential.created_by != actor.user_id
        ):
            raise BudgetPolicyError("solo_credential_mismatch", "Solo owner must use a credential created by that owner")
        if request.field == BudgetField.SPEND_CAP:
            raise BudgetPolicyError(
                "spend_cap_forbidden",
                "Solo owners cannot change an account spend_cap",
            )
        return

    if actor.role == ActorRole.AGENCY:
        if request.agency_id is None or actor.selected_agency_id != request.agency_id:
            raise BudgetPolicyError("agency_selection_required", "An active selected agency is required")
        if not actor.agency_active or request.client_id not in actor.agency_bound_client_ids:
            raise BudgetPolicyError("agency_client_access_denied", "Client is not actively bound to the selected agency")
        if actor.agency_member_role not in {AgencyMemberRole.OWNER, AgencyMemberRole.MANAGER}:
            raise BudgetPolicyError("agency_manage_forbidden", "Agency owner or manager access is required")
        if credential.scope_type != CredentialScope.AGENCY or credential.scope_id != request.agency_id:
            raise BudgetPolicyError("agency_credential_mismatch", "Selected agency must use its own Meta credential")
        if request.field == BudgetField.SPEND_CAP and not actor.can_manage_spend_cap:
            raise BudgetPolicyError(
                "spend_cap_forbidden",
                "Account spend_cap requires an explicit live role capability",
            )
        return

    if actor.role == ActorRole.ADMIN:
        if not actor.can_admin_provider_write:
            raise BudgetPolicyError("admin_write_not_delegated", "Admin provider write capability is required")
        if credential.scope_type != CredentialScope.GLOBAL or credential.scope_id is not None:
            raise BudgetPolicyError("admin_credential_mismatch", "Admin writes must use a platform-owned global credential")
        if request.field == BudgetField.SPEND_CAP and not actor.can_manage_spend_cap:
            raise BudgetPolicyError("spend_cap_forbidden", "Admin account spend_cap capability is required")
        return

    raise BudgetPolicyError("role_not_supported", "Actor role cannot change provider budgets")


def authorize_budget_reconciliation(
    request: BudgetChangeRequest,
    *,
    actor: BudgetActorContext,
    tenant: BudgetTenantContext,
    credential: BudgetCredentialContext,
) -> None:
    """Authorize a provider-read-only recovery against the current binding."""
    current_request = replace(request, credential_id=credential.id)
    if actor.role != ActorRole.ADMIN:
        recovery_actor = actor
        if request.field == BudgetField.SPEND_CAP and actor.role == ActorRole.AGENCY:
            # Recovery is read-only and must remain possible after the mutation
            # kill switch is disabled. Owner/manager eligibility, tenant scope,
            # binding and live credential are still enforced below.
            recovery_actor = replace(actor, can_manage_spend_cap=True)
        authorize_budget_write(
            current_request,
            actor=recovery_actor,
            tenant=tenant,
            credential=credential,
        )
        return
    if not actor.can_admin_provider_write:
        raise BudgetPolicyError("admin_write_not_delegated", "Admin provider recovery capability is required")
    if tenant.client_id != request.client_id or tenant.account_client_id != request.client_id:
        raise BudgetPolicyError("tenant_mismatch", "Advertising account does not belong to the requested client")
    if tenant.ad_account_id != request.ad_account_id:
        raise BudgetPolicyError("account_mismatch", "Advertising account identity does not match the request")
    if not tenant.client_active or not tenant.account_active or tenant.account_platform != "meta":
        raise BudgetPolicyError("inactive_scope", "An active Meta client and account are required")
    if credential.provider != "meta" or credential.status != "active":
        raise BudgetPolicyError("credential_unavailable", "An active Meta credential is required")
    if credential.expired or not credential.has_ads_management:
        raise BudgetPolicyError("credential_permission_missing", "Credential must be current and grant ads_management")
    valid_scope = (
        (credential.scope_type == CredentialScope.GLOBAL and credential.scope_id is None)
        or (credential.scope_type == CredentialScope.CLIENT and credential.scope_id == request.client_id)
        or (
            credential.scope_type == CredentialScope.AGENCY
            and request.agency_id is not None
            and credential.scope_id == request.agency_id
        )
    )
    if not valid_scope:
        raise BudgetPolicyError(
            "admin_recovery_credential_mismatch",
            "The current bound credential scope does not match the command tenant",
        )


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def command_idempotency_hash(
    request: BudgetChangeRequest,
    *,
    actor_user_id: UUID,
    preview_request_digest: str,
    observed_before_minor: int,
) -> str:
    return _sha256_payload(
        {
            "actor_user_id": str(actor_user_id),
            "request": request.canonical_payload(),
            "preview_request_hash": preview_request_digest,
            "observed_before_minor": observed_before_minor,
        }
    )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value) or len(value) % 4 == 1:
        raise PreviewTokenError("preview_invalid", "Preview token is malformed")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            f"{value}{padding}".encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise PreviewTokenError("preview_invalid", "Preview token is malformed") from exc
    if not hmac.compare_digest(_b64encode(decoded), value):
        raise PreviewTokenError("preview_invalid", "Preview token is malformed")
    return decoded


@dataclass(frozen=True)
class VerifiedPreview:
    request_hash: str
    actor_user_id: UUID
    observed_before_minor: int
    issued_at: datetime
    expires_at: datetime
    preview_hash: str
    authorization_snapshot_hash: str
    credential_snapshot_hash: str


@dataclass(frozen=True)
class BudgetPreview:
    request: BudgetChangeRequest
    observed_before_minor: int
    token: str
    request_hash: str
    preview_hash: str
    issued_at: datetime
    expires_at: datetime


class PreviewSigner:
    def __init__(self, secret: bytes | str, *, ttl_seconds: int = 300):
        if type(ttl_seconds) is not int:
            raise BudgetValidationError("preview_ttl_invalid", "Preview TTL must be an integer number of seconds")
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(secret_bytes) < 32:
            raise BudgetValidationError("preview_secret_weak", "Preview signing secret must be at least 32 bytes")
        if not 30 <= ttl_seconds <= 900:
            raise BudgetValidationError("preview_ttl_invalid", "Preview TTL must be between 30 and 900 seconds")
        self._secret = secret_bytes
        self.ttl_seconds = ttl_seconds

    def _context_proof(self, label: str, payload: object) -> str:
        # These proofs are returned to the browser inside the signed token.
        # Key them as well as signing the envelope so low-entropy internal ids
        # cannot be tested offline from their digests.
        material = label.encode("ascii") + b"\x00" + _canonical_json(payload)
        return hmac.new(self._secret, material, hashlib.sha256).hexdigest()

    def issue(
        self,
        request: BudgetChangeRequest,
        *,
        actor_user_id: UUID,
        authorization_snapshot: BudgetAuthorizationSnapshot,
        credential_snapshot: BudgetCredentialAuditSnapshot,
        observed_before_minor: int,
        now: Optional[datetime] = None,
    ) -> BudgetPreview:
        issued_at = _aware_utc(now or _utc_now())
        expires_at = issued_at + timedelta(seconds=self.ttl_seconds)
        authorization_snapshot_hash = self._context_proof(
            "authorization",
            authorization_snapshot.canonical_payload(),
        )
        credential_snapshot_hash = self._context_proof(
            "credential",
            credential_snapshot.canonical_payload(),
        )
        request_digest = self._context_proof(
            "request",
            {
                "actor_user_id": str(actor_user_id),
                "request": request.canonical_payload(),
                "authorization_snapshot": authorization_snapshot.canonical_payload(),
                "credential_snapshot": credential_snapshot.canonical_payload(),
            },
        )
        payload = {
            "v": 2,
            "request_hash": request_digest,
            "authorization_snapshot_hash": authorization_snapshot_hash,
            "credential_snapshot_hash": credential_snapshot_hash,
            "observed_before_minor": _require_minor_units(observed_before_minor, "observed_before_minor"),
            "issued_at": int(issued_at.timestamp()),
            "expires_at": int(expires_at.timestamp()),
            "nonce": secrets.token_urlsafe(18),
        }
        body = _b64encode(_canonical_json(payload))
        signature = _b64encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        token = f"{body}.{signature}"
        return BudgetPreview(
            request=request,
            observed_before_minor=observed_before_minor,
            token=token,
            request_hash=request_digest,
            preview_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def verify(
        self,
        token: str,
        request: BudgetChangeRequest,
        *,
        actor_user_id: UUID,
        authorization_snapshot: BudgetAuthorizationSnapshot,
        credential_snapshot: BudgetCredentialAuditSnapshot,
        now: Optional[datetime] = None,
    ) -> VerifiedPreview:
        try:
            body, provided_signature = str(token or "").split(".", 1)
        except ValueError as exc:
            raise PreviewTokenError("preview_invalid", "Preview token is malformed") from exc
        decoded_body = _b64decode(body)
        _b64decode(provided_signature)
        expected_signature = _b64encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise PreviewTokenError("preview_tampered", "Preview token signature is invalid")
        try:
            payload = json.loads(decoded_body.decode("utf-8"))
            if payload.get("v") != 2:
                raise ValueError("unsupported version")
            observed = _require_minor_units(payload["observed_before_minor"], "observed_before_minor")
            issued_at = datetime.fromtimestamp(int(payload["issued_at"]), tz=timezone.utc)
            expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc)
            request_digest = str(payload["request_hash"])
            token_authorization_hash = str(payload["authorization_snapshot_hash"])
            token_credential_hash = str(payload["credential_snapshot_hash"])
        except PreviewTokenError:
            raise
        except Exception as exc:
            raise PreviewTokenError("preview_invalid", "Preview token payload is invalid") from exc

        checked_at = _aware_utc(now or _utc_now())
        if checked_at >= expires_at:
            raise PreviewExpiredError("preview_expired", "Preview token has expired")
        if issued_at > checked_at + timedelta(seconds=60) or expires_at <= issued_at:
            raise PreviewTokenError("preview_invalid", "Preview token timestamps are invalid")
        current_authorization_hash = self._context_proof(
            "authorization",
            authorization_snapshot.canonical_payload(),
        )
        if not hmac.compare_digest(token_authorization_hash, current_authorization_hash):
            raise PreviewTokenError(
                "preview_authorization_changed",
                "Authorization changed after preview; create a new preview",
            )
        current_credential_hash = self._context_proof(
            "credential",
            credential_snapshot.canonical_payload(),
        )
        if not hmac.compare_digest(token_credential_hash, current_credential_hash):
            raise PreviewTokenError(
                "preview_credential_changed",
                "Meta connection changed after preview; create a new preview",
            )
        expected_request_digest = self._context_proof(
            "request",
            {
                "actor_user_id": str(actor_user_id),
                "request": request.canonical_payload(),
                "authorization_snapshot": authorization_snapshot.canonical_payload(),
                "credential_snapshot": credential_snapshot.canonical_payload(),
            },
        )
        if not hmac.compare_digest(request_digest, expected_request_digest):
            raise PreviewTokenError("preview_request_mismatch", "Preview does not match the actor or request")
        return VerifiedPreview(
            request_hash=request_digest,
            actor_user_id=actor_user_id,
            observed_before_minor=observed,
            issued_at=issued_at,
            expires_at=expires_at,
            preview_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            authorization_snapshot_hash=token_authorization_hash,
            credential_snapshot_hash=token_credential_hash,
        )


def validate_idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not 8 <= len(normalized) <= 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", normalized):
        raise BudgetValidationError(
            "idempotency_key_invalid",
            "Idempotency-Key must be 8-128 URL-safe characters",
        )
    return normalized


@dataclass(frozen=True)
class BudgetCommandAttempt:
    attempt_no: int
    started_at: datetime
    finished_at: datetime
    outcome: BudgetCommandStatus
    observed_before_minor: Optional[int] = None
    confirmed_after_minor: Optional[int] = None
    provider_trace_id: Optional[str] = None
    error: Optional[SanitizedProviderError] = None
    reconciliation: bool = False
    actor_user_id: Optional[UUID] = None
    credential_id: Optional[UUID] = None
    authorization_snapshot: Optional[BudgetAuthorizationSnapshot] = None
    credential_snapshot: Optional[BudgetCredentialAuditSnapshot] = None


@dataclass(frozen=True)
class ProviderBudgetCommand:
    id: UUID
    request: BudgetChangeRequest
    actor_user_id: UUID
    authorization_snapshot: BudgetAuthorizationSnapshot
    credential_snapshot: BudgetCredentialAuditSnapshot
    status: BudgetCommandStatus
    idempotency_key: str
    request_hash: str
    preview_hash: str
    observed_before_minor: int
    confirmed_after_minor: Optional[int]
    provider_trace_id: Optional[str]
    error: Optional[SanitizedProviderError]
    attempt_count: int
    attempts: Tuple[BudgetCommandAttempt, ...]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


@dataclass(frozen=True)
class CommandSubmission:
    command: ProviderBudgetCommand
    replayed: bool


class BudgetCommandStore(Protocol):
    def create_or_replay(
        self,
        *,
        request: BudgetChangeRequest,
        actor_user_id: UUID,
        authorization_snapshot: BudgetAuthorizationSnapshot,
        credential_snapshot: BudgetCredentialAuditSnapshot,
        idempotency_key: str,
        request_hash: str,
        preview_hash: str,
        observed_before_minor: int,
        now: datetime,
    ) -> CommandSubmission: ...

    def get(self, command_id: UUID) -> Optional[ProviderBudgetCommand]: ...

    def target_quarantine_command_id(self, request: BudgetChangeRequest) -> Optional[UUID]: ...

    def claim_queued(self, command_id: UUID, *, now: datetime) -> ProviderBudgetCommand: ...

    def finish_execution(
        self,
        command_id: UUID,
        *,
        status: BudgetCommandStatus,
        attempt: BudgetCommandAttempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error: Optional[SanitizedProviderError],
        now: datetime,
    ) -> ProviderBudgetCommand: ...

    def record_reconciliation(
        self,
        command_id: UUID,
        *,
        status: BudgetCommandStatus,
        attempt: BudgetCommandAttempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error: Optional[SanitizedProviderError],
        now: datetime,
    ) -> ProviderBudgetCommand: ...


def assert_budget_command_context_matches(
    command: ProviderBudgetCommand,
    *,
    actor: BudgetActorContext,
    credential: BudgetCredentialContext,
) -> None:
    """Fail before a provider POST when preview-time security facts changed."""

    current_authorization = budget_authorization_snapshot(actor, command.request)
    if not hmac.compare_digest(
        _sha256_payload(command.authorization_snapshot.canonical_payload()),
        _sha256_payload(current_authorization.canonical_payload()),
    ):
        raise BudgetConflictError(
            "provider_budget_authorization_changed",
            "Authorization changed after preview; no provider write was attempted",
        )
    current_credential = budget_credential_audit_snapshot(credential)
    if not hmac.compare_digest(
        _sha256_payload(command.credential_snapshot.canonical_payload()),
        _sha256_payload(current_credential.canonical_payload()),
    ):
        raise BudgetConflictError(
            "provider_budget_credential_changed",
            "Meta connection changed after preview; no provider write was attempted",
        )

class InMemoryBudgetCommandStore:
    """Reference store for tests and future adapters; it is not wired into production."""

    def __init__(self):
        self._items: dict[UUID, ProviderBudgetCommand] = {}
        self._idempotency: dict[tuple[UUID, str], UUID] = {}
        self._preview_hashes: dict[str, UUID] = {}
        self._target_quarantines: dict[tuple[str, str, str, str], UUID] = {}
        self._lock = RLock()

    @staticmethod
    def _target_key(request: BudgetChangeRequest) -> tuple[str, str, str, str]:
        return ("meta", request.target_type.value, request.provider_target_id, request.field.value)

    @staticmethod
    def _copy(command: ProviderBudgetCommand) -> ProviderBudgetCommand:
        return copy.deepcopy(command)

    @staticmethod
    def _attempt_with_snapshots(
        command: ProviderBudgetCommand,
        attempt: BudgetCommandAttempt,
    ) -> BudgetCommandAttempt:
        return replace(
            attempt,
            authorization_snapshot=(
                attempt.authorization_snapshot or command.authorization_snapshot
            ),
            credential_snapshot=(attempt.credential_snapshot or command.credential_snapshot),
        )

    def create_or_replay(
        self,
        *,
        request: BudgetChangeRequest,
        actor_user_id: UUID,
        authorization_snapshot: BudgetAuthorizationSnapshot,
        credential_snapshot: BudgetCredentialAuditSnapshot,
        idempotency_key: str,
        request_hash: str,
        preview_hash: str,
        observed_before_minor: int,
        now: datetime,
    ) -> CommandSubmission:
        key = (request.client_id, validate_idempotency_key(idempotency_key))
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                existing = self._items[existing_id]
                if not hmac.compare_digest(existing.request_hash, request_hash):
                    raise IdempotencyConflictError(
                        "idempotency_conflict",
                        "Idempotency-Key was already used with a different request",
                    )
                return CommandSubmission(command=self._copy(existing), replayed=True)
            if preview_hash in self._preview_hashes:
                raise IdempotencyConflictError(
                    "preview_already_consumed",
                    "Preview token was already consumed by another budget command",
                )
            if self._target_key(request) in self._target_quarantines:
                raise CommandNotExecutableError(
                    "meta_budget_target_quarantined",
                    "An earlier budget write has an unresolved outcome; reconcile it before another change",
                )
            created_at = _aware_utc(now)
            command = ProviderBudgetCommand(
                id=uuid4(),
                request=request,
                actor_user_id=actor_user_id,
                authorization_snapshot=authorization_snapshot,
                credential_snapshot=credential_snapshot,
                status=BudgetCommandStatus.QUEUED,
                idempotency_key=key[1],
                request_hash=request_hash,
                preview_hash=preview_hash,
                observed_before_minor=observed_before_minor,
                confirmed_after_minor=None,
                provider_trace_id=None,
                error=None,
                attempt_count=0,
                attempts=(),
                created_at=created_at,
                updated_at=created_at,
                completed_at=None,
            )
            self._items[command.id] = command
            self._idempotency[key] = command.id
            self._preview_hashes[preview_hash] = command.id
            return CommandSubmission(command=self._copy(command), replayed=False)

    def get(self, command_id: UUID) -> Optional[ProviderBudgetCommand]:
        with self._lock:
            command = self._items.get(command_id)
            return self._copy(command) if command else None

    def target_quarantine_command_id(self, request: BudgetChangeRequest) -> Optional[UUID]:
        with self._lock:
            return self._target_quarantines.get(self._target_key(request))

    def claim_queued(self, command_id: UUID, *, now: datetime) -> ProviderBudgetCommand:
        with self._lock:
            command = self._items.get(command_id)
            if command is None:
                raise CommandNotExecutableError("command_not_found", "Budget command was not found")
            if command.status != BudgetCommandStatus.QUEUED:
                raise CommandNotExecutableError(
                    "command_not_executable",
                    f"Budget command in {command.status.value} state cannot execute again",
                )
            claimed = replace(
                command,
                status=BudgetCommandStatus.IN_PROGRESS,
                attempt_count=command.attempt_count + 1,
                updated_at=_aware_utc(now),
            )
            self._items[command_id] = claimed
            return self._copy(claimed)

    def finish_execution(
        self,
        command_id: UUID,
        *,
        status: BudgetCommandStatus,
        attempt: BudgetCommandAttempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error: Optional[SanitizedProviderError],
        now: datetime,
    ) -> ProviderBudgetCommand:
        if status not in {
            BudgetCommandStatus.APPLIED,
            BudgetCommandStatus.CONFLICT,
            BudgetCommandStatus.FAILED,
            BudgetCommandStatus.UNKNOWN,
        }:
            raise BudgetValidationError("invalid_transition", "Execution must finish in a terminal or unknown state")
        with self._lock:
            command = self._items.get(command_id)
            if command is None or command.status != BudgetCommandStatus.IN_PROGRESS:
                raise CommandNotExecutableError("command_not_in_progress", "Budget command is not in progress")
            completed_at = _aware_utc(now) if status != BudgetCommandStatus.UNKNOWN else None
            attempt = self._attempt_with_snapshots(command, attempt)
            updated = replace(
                command,
                status=status,
                confirmed_after_minor=confirmed_after_minor,
                provider_trace_id=provider_trace_id,
                error=error,
                attempts=(*command.attempts, attempt),
                updated_at=_aware_utc(now),
                completed_at=completed_at,
            )
            self._items[command_id] = updated
            if status == BudgetCommandStatus.UNKNOWN:
                self._target_quarantines[self._target_key(command.request)] = command_id
            return self._copy(updated)

    def record_reconciliation(
        self,
        command_id: UUID,
        *,
        status: BudgetCommandStatus,
        attempt: BudgetCommandAttempt,
        confirmed_after_minor: Optional[int],
        provider_trace_id: Optional[str],
        error: Optional[SanitizedProviderError],
        now: datetime,
    ) -> ProviderBudgetCommand:
        if status not in {BudgetCommandStatus.APPLIED, BudgetCommandStatus.CONFLICT, BudgetCommandStatus.UNKNOWN}:
            raise BudgetValidationError("invalid_reconciliation", "Reconciliation cannot perform or retry a write")
        with self._lock:
            command = self._items.get(command_id)
            if command is None or command.status != BudgetCommandStatus.UNKNOWN:
                raise CommandNotExecutableError(
                    "command_not_reconcilable",
                    "Only an unknown command can be reconciled",
                )
            completed_at = _aware_utc(now) if status != BudgetCommandStatus.UNKNOWN else None
            attempt = self._attempt_with_snapshots(command, attempt)
            updated = replace(
                command,
                status=status,
                confirmed_after_minor=confirmed_after_minor,
                provider_trace_id=provider_trace_id or command.provider_trace_id,
                error=error,
                attempt_count=command.attempt_count + 1,
                attempts=(*command.attempts, attempt),
                updated_at=_aware_utc(now),
                completed_at=completed_at,
            )
            self._items[command_id] = updated
            target_key = self._target_key(command.request)
            if status == BudgetCommandStatus.UNKNOWN:
                self._target_quarantines[target_key] = command_id
            elif self._target_quarantines.get(target_key) == command_id:
                self._target_quarantines.pop(target_key, None)
            return self._copy(updated)


def _snapshot_matches_request(snapshot: BudgetSnapshot, request: BudgetChangeRequest) -> None:
    if (
        snapshot.target_type != request.target_type
        or snapshot.provider_target_id != request.provider_target_id
        or snapshot.provider_account_id != request.provider_account_id
        or snapshot.field != request.field
    ):
        raise ProviderBudgetError(
            "provider_target_mismatch",
            "Provider returned a budget target outside the requested advertising account",
        )
    if snapshot.currency != request.currency:
        raise ProviderBudgetError(
            "provider_currency_mismatch",
            "Provider account currency does not match the requested currency",
        )


def _internal_error(_exc: Exception) -> SanitizedProviderError:
    return SanitizedProviderError(
        code="provider_internal_error",
        # Unknown adapter exceptions can contain request headers or credentials.
        # Keep raw diagnostics in an access-controlled telemetry system, not in
        # the command/audit payload.
        message="Unexpected provider adapter failure",
        retryable=False,
    )


class ProviderBudgetCommandService:
    """Preview/queue/execute saga. Nothing is reachable until a caller wires it explicitly."""

    def __init__(
        self,
        *,
        adapter: ProviderBudgetAdapter,
        store: BudgetCommandStore,
        preview_signer: Optional[PreviewSigner] = None,
        clock: Callable[[], datetime] = _utc_now,
        before_provider_write: Optional[Callable[[ProviderBudgetCommand], None]] = None,
        reauthorize_before_write: Optional[
            Callable[[ProviderBudgetCommand], BudgetExecutionAuthorization]
        ] = None,
    ):
        self.adapter = adapter
        self.store = store
        self.preview_signer = preview_signer
        self.clock = clock
        self.before_provider_write = before_provider_write
        self.reauthorize_before_write = reauthorize_before_write

    def preview(
        self,
        request: BudgetChangeRequest,
        *,
        actor: BudgetActorContext,
        tenant: BudgetTenantContext,
        credential: BudgetCredentialContext,
    ) -> BudgetPreview:
        if self.preview_signer is None:
            raise BudgetValidationError("preview_signer_unavailable", "Budget preview signing is unavailable")
        authorize_budget_write(request, actor=actor, tenant=tenant, credential=credential)
        authorization_snapshot = budget_authorization_snapshot(actor, request)
        credential_snapshot = budget_credential_audit_snapshot(credential)
        snapshot = self.adapter.read_budget(request)
        _snapshot_matches_request(snapshot, request)
        if snapshot.amount_minor == request.amount_minor:
            raise BudgetConflictError(
                "provider_budget_no_change",
                "Requested budget already matches the current provider value",
            )
        if request.expected_current_minor is not None and snapshot.amount_minor != request.expected_current_minor:
            raise BudgetConflictError(
                "expected_budget_changed",
                "Provider budget changed before preview; refresh and confirm the new value",
            )
        return self.preview_signer.issue(
            request,
            actor_user_id=actor.user_id,
            authorization_snapshot=authorization_snapshot,
            credential_snapshot=credential_snapshot,
            observed_before_minor=snapshot.amount_minor,
            now=self.clock(),
        )

    def submit(
        self,
        request: BudgetChangeRequest,
        *,
        preview_token: str,
        idempotency_key: str,
        actor: BudgetActorContext,
        tenant: BudgetTenantContext,
        credential: BudgetCredentialContext,
    ) -> CommandSubmission:
        if self.preview_signer is None:
            raise BudgetValidationError("preview_signer_unavailable", "Budget preview signing is unavailable")
        authorize_budget_write(request, actor=actor, tenant=tenant, credential=credential)
        authorization_snapshot = budget_authorization_snapshot(actor, request)
        credential_snapshot = budget_credential_audit_snapshot(credential)
        now = self.clock()
        verified = self.preview_signer.verify(
            preview_token,
            request,
            actor_user_id=actor.user_id,
            authorization_snapshot=authorization_snapshot,
            credential_snapshot=credential_snapshot,
            now=now,
        )
        request_hash = command_idempotency_hash(
            request,
            actor_user_id=actor.user_id,
            preview_request_digest=verified.request_hash,
            observed_before_minor=verified.observed_before_minor,
        )
        return self.store.create_or_replay(
            request=request,
            actor_user_id=actor.user_id,
            authorization_snapshot=authorization_snapshot,
            credential_snapshot=credential_snapshot,
            idempotency_key=validate_idempotency_key(idempotency_key),
            request_hash=request_hash,
            preview_hash=verified.preview_hash,
            observed_before_minor=verified.observed_before_minor,
            now=now,
        )

    def execute(
        self,
        command_id: UUID,
        *,
        actor: BudgetActorContext,
        tenant: BudgetTenantContext,
        credential: BudgetCredentialContext,
    ) -> ProviderBudgetCommand:
        existing = self.store.get(command_id)
        if existing is None:
            raise CommandNotExecutableError("command_not_found", "Budget command was not found")
        if existing.actor_user_id != actor.user_id:
            raise BudgetPolicyError("command_actor_mismatch", "Only the command actor may execute this command")
        authorize_budget_write(existing.request, actor=actor, tenant=tenant, credential=credential)
        assert_budget_command_context_matches(existing, actor=actor, credential=credential)
        started_at = _aware_utc(self.clock())
        command = self.store.claim_queued(command_id, now=started_at)

        before_minor: Optional[int] = None
        after_minor: Optional[int] = None
        trace_id: Optional[str] = None
        error: Optional[SanitizedProviderError] = None
        status = BudgetCommandStatus.UNKNOWN
        write_accepted = False
        attempt_actor = actor
        attempt_credential = credential
        final_authorization: Optional[BudgetExecutionAuthorization] = None
        final_gate_attempted = False
        final_gate_verified: Optional[bool] = None

        try:
            before = self.adapter.read_budget(command.request)
            _snapshot_matches_request(before, command.request)
            before_minor = before.amount_minor
            trace_id = before.provider_trace_id
            expected = command.request.expected_current_minor
            if before_minor != command.observed_before_minor or (expected is not None and before_minor != expected):
                raise BudgetConflictError(
                    "provider_budget_changed",
                    "Provider budget changed after preview; no write was attempted",
                )

            if self.before_provider_write is not None:
                self.before_provider_write(command)
            if self.reauthorize_before_write is not None:
                # This callback performs every mutable store/config read after
                # all provider preflight reads and immediately before the only
                # provider mutation. No provider request is made between this
                # gate and write_budget.
                final_gate_attempted = True
                final_authorization = self.reauthorize_before_write(command)
                attempt_actor = final_authorization.actor
                attempt_credential = final_authorization.credential
                if attempt_actor.user_id != command.actor_user_id:
                    raise BudgetPolicyError(
                        "command_actor_mismatch",
                        "The live execution actor no longer matches the command actor",
                    )
                if not final_authorization.feature_enabled:
                    raise BudgetPolicyError(
                        "meta_budget_controls_disabled",
                        "Meta budget controls were disabled before provider execution",
                    )
                if not final_authorization.client_rollout_allowed:
                    raise BudgetPolicyError(
                        "meta_budget_client_not_allowed",
                        "Meta budget controls are no longer enabled for this client",
                    )
                if not final_authorization.binding_verified:
                    raise BudgetPolicyError(
                        "meta_budget_rediscovery_required",
                        "The trusted Meta account binding could not be verified",
                    )
                if final_authorization.provider_account_id != command.request.provider_account_id:
                    raise BudgetPolicyError(
                        "provider_budget_account_binding_changed",
                        "The trusted Meta account binding changed before provider execution",
                    )
                if (
                    command.request.field == BudgetField.SPEND_CAP
                    and not final_authorization.spend_cap_enabled
                ):
                    raise BudgetPolicyError(
                        "meta_spend_cap_disabled",
                        "Account spend_cap controls were disabled before provider execution",
                    )
                authorize_budget_write(
                    command.request,
                    actor=final_authorization.actor,
                    tenant=final_authorization.tenant,
                    credential=final_authorization.credential,
                )
                assert_budget_command_context_matches(
                    command,
                    actor=final_authorization.actor,
                    credential=final_authorization.credential,
                )
                final_gate_verified = True
            receipt = self.adapter.write_budget(command.request)
            write_accepted = bool(receipt.accepted)
            trace_id = receipt.provider_trace_id or trace_id
            if not receipt.accepted:
                raise ProviderWriteOutcomeUnknown(
                    "provider_write_unconfirmed",
                    "Provider did not return a definitive write acknowledgement",
                    trace_id=trace_id,
                )

            after = self.adapter.read_budget(command.request)
            _snapshot_matches_request(after, command.request)
            after_minor = after.amount_minor
            trace_id = after.provider_trace_id or trace_id
            status = (
                BudgetCommandStatus.APPLIED
                if after_minor == command.request.amount_minor
                else BudgetCommandStatus.UNKNOWN
            )
            if status == BudgetCommandStatus.UNKNOWN:
                error = SanitizedProviderError(
                    code="provider_readback_mismatch",
                    message="Provider read-back did not confirm the requested value",
                    trace_id=trace_id,
                    retryable=False,
                )
        except BudgetConflictError as exc:
            status = BudgetCommandStatus.CONFLICT
            error = SanitizedProviderError(code=exc.code, message=exc.message)
        except ProviderWriteOutcomeUnknown as exc:
            status = BudgetCommandStatus.UNKNOWN
            error = exc.provider_error
            trace_id = exc.provider_error.trace_id or trace_id
        except ProviderBudgetError as exc:
            # A ProviderBudgetError from write_budget is a definitive rejection.
            # Read-back errors after an accepted write are necessarily ambiguous.
            status = BudgetCommandStatus.UNKNOWN if write_accepted else BudgetCommandStatus.FAILED
            error = exc.provider_error
            trace_id = exc.provider_error.trace_id or trace_id
        except ProviderBudgetCommandError as exc:
            # A live authorization/configuration revocation is definitive and
            # occurs before the provider POST. Persist its safe domain code so
            # operators can distinguish it from an ambiguous write outcome.
            status = BudgetCommandStatus.FAILED
            error = SanitizedProviderError(
                code=str(exc.code or "provider_budget_reauthorization_failed"),
                message=sanitize_provider_message(exc.message),
                retryable=False,
            )
        except Exception as exc:
            status = BudgetCommandStatus.UNKNOWN if write_accepted else BudgetCommandStatus.FAILED
            error = _internal_error(exc)

        finished_at = _aware_utc(self.clock())
        attempt_authorization_snapshot: BudgetAuthorizationSnapshot
        if final_authorization is not None:
            attempt_authorization_snapshot = budget_execution_authorization_snapshot(
                final_authorization,
                command.request,
                final_gate_verified=final_gate_verified is True,
                final_gate_failure_code=(error.code if error is not None else None),
            )
        elif final_gate_attempted:
            attempt_authorization_snapshot = budget_execution_failure_snapshot(
                failure_code=(error.code if error is not None else "provider_budget_reauthorization_failed"),
            )
        else:
            attempt_authorization_snapshot = budget_authorization_snapshot(
                attempt_actor,
                command.request,
            )

        if final_authorization is not None:
            attempt_credential_snapshot = budget_execution_credential_snapshot(
                final_authorization.credential,
                final_gate_verified=final_gate_verified is True,
                final_gate_failure_code=(error.code if error is not None else None),
            )
            attempt_credential_id: Optional[UUID] = final_authorization.credential.id
        elif final_gate_attempted:
            attempt_credential_snapshot = budget_credential_failure_snapshot(
                failure_code=(
                    error.code if error is not None else "provider_budget_reauthorization_failed"
                ),
            )
            attempt_credential_id = None
        else:
            attempt_credential_snapshot = budget_credential_audit_snapshot(attempt_credential)
            attempt_credential_id = attempt_credential.id

        attempt = BudgetCommandAttempt(
            attempt_no=command.attempt_count,
            started_at=started_at,
            finished_at=finished_at,
            outcome=status,
            observed_before_minor=before_minor,
            confirmed_after_minor=after_minor,
            provider_trace_id=trace_id,
            error=error,
            reconciliation=False,
            actor_user_id=attempt_actor.user_id,
            credential_id=attempt_credential_id,
            authorization_snapshot=attempt_authorization_snapshot,
            credential_snapshot=attempt_credential_snapshot,
        )
        return self.store.finish_execution(
            command_id,
            status=status,
            attempt=attempt,
            confirmed_after_minor=after_minor if status == BudgetCommandStatus.APPLIED else None,
            provider_trace_id=trace_id,
            error=error,
            now=finished_at,
        )

    def reconcile(
        self,
        command_id: UUID,
        *,
        actor: BudgetActorContext,
        tenant: BudgetTenantContext,
        credential: BudgetCredentialContext,
        finalize_mismatch: bool = False,
    ) -> ProviderBudgetCommand:
        """Read-only reconciliation. It intentionally never calls write_budget."""
        command = self.store.get(command_id)
        if command is None:
            raise CommandNotExecutableError("command_not_found", "Budget command was not found")
        if command.status != BudgetCommandStatus.UNKNOWN:
            raise CommandNotExecutableError("command_not_reconcilable", "Only unknown commands can be reconciled")
        if command.actor_user_id != actor.user_id:
            successor_agency_manager = (
                actor.role == ActorRole.AGENCY
                and command.request.agency_id is not None
                and actor.selected_agency_id == command.request.agency_id
                and actor.agency_member_role in {AgencyMemberRole.OWNER, AgencyMemberRole.MANAGER}
            )
            delegated_admin = actor.role == ActorRole.ADMIN and actor.can_admin_provider_write
            if not successor_agency_manager and not delegated_admin:
                raise BudgetPolicyError(
                    "command_actor_mismatch",
                    "Only the original actor or a currently authorized successor may reconcile this command",
                )
        # Reconciliation is provider-read-only. A reconnect may have replaced
        # the original credential, so authorize the current explicit binding
        # against the same immutable tenant/agency request while preserving the
        # original credential id in the command ledger.
        authorize_budget_reconciliation(
            command.request,
            actor=actor,
            tenant=tenant,
            credential=credential,
        )

        started_at = _aware_utc(self.clock())
        after_minor: Optional[int] = None
        trace_id = command.provider_trace_id
        error: Optional[SanitizedProviderError] = None
        status = BudgetCommandStatus.UNKNOWN
        try:
            snapshot = self.adapter.read_budget(command.request)
            _snapshot_matches_request(snapshot, command.request)
            after_minor = snapshot.amount_minor
            trace_id = snapshot.provider_trace_id or trace_id
            if after_minor == command.request.amount_minor:
                status = BudgetCommandStatus.APPLIED
            elif finalize_mismatch:
                status = BudgetCommandStatus.CONFLICT
                error = SanitizedProviderError(
                    code="provider_reconciliation_mismatch",
                    message="Provider value differs from the requested value after reconciliation",
                    trace_id=trace_id,
                )
            else:
                error = SanitizedProviderError(
                    code="provider_reconciliation_pending",
                    message="Provider value is not yet confirmed; command remains unknown",
                    trace_id=trace_id,
                )
        except ProviderBudgetError as exc:
            error = exc.provider_error
            trace_id = exc.provider_error.trace_id or trace_id
        except Exception as exc:
            error = _internal_error(exc)

        finished_at = _aware_utc(self.clock())
        attempt = BudgetCommandAttempt(
            attempt_no=command.attempt_count + 1,
            started_at=started_at,
            finished_at=finished_at,
            outcome=status,
            confirmed_after_minor=(
                after_minor
                if status in {BudgetCommandStatus.APPLIED, BudgetCommandStatus.CONFLICT}
                else None
            ),
            provider_trace_id=trace_id,
            error=error,
            reconciliation=True,
            actor_user_id=actor.user_id,
            credential_id=credential.id,
            authorization_snapshot=budget_authorization_snapshot(actor, command.request),
            credential_snapshot=budget_credential_audit_snapshot(credential),
        )
        return self.store.record_reconciliation(
            command_id,
            status=status,
            attempt=attempt,
            confirmed_after_minor=after_minor if status == BudgetCommandStatus.APPLIED else None,
            provider_trace_id=trace_id,
            error=error,
            now=finished_at,
        )
