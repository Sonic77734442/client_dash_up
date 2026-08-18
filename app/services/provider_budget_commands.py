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
        object.__setattr__(self, "expired", _require_bool(self.expired, "expired"))


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
        if request.field == BudgetField.SPEND_CAP:
            owner = actor.agency_member_role == AgencyMemberRole.OWNER
            if not owner and not actor.can_manage_spend_cap:
                raise BudgetPolicyError(
                    "spend_cap_forbidden",
                    "Account spend_cap requires agency owner access or an explicit capability",
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


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def preview_request_hash(request: BudgetChangeRequest, actor_user_id: UUID) -> str:
    return _sha256_payload(
        {
            "actor_user_id": str(actor_user_id),
            "request": request.canonical_payload(),
        }
    )


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
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
    except Exception as exc:
        raise PreviewTokenError("preview_invalid", "Preview token is malformed") from exc


@dataclass(frozen=True)
class VerifiedPreview:
    request_hash: str
    actor_user_id: UUID
    observed_before_minor: int
    issued_at: datetime
    expires_at: datetime
    preview_hash: str


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

    def issue(
        self,
        request: BudgetChangeRequest,
        *,
        actor_user_id: UUID,
        observed_before_minor: int,
        now: Optional[datetime] = None,
    ) -> BudgetPreview:
        issued_at = _aware_utc(now or _utc_now())
        expires_at = issued_at + timedelta(seconds=self.ttl_seconds)
        request_digest = preview_request_hash(request, actor_user_id)
        payload = {
            "v": 1,
            "actor_user_id": str(actor_user_id),
            "request_hash": request_digest,
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
        now: Optional[datetime] = None,
    ) -> VerifiedPreview:
        try:
            body, provided_signature = str(token or "").split(".", 1)
        except ValueError as exc:
            raise PreviewTokenError("preview_invalid", "Preview token is malformed") from exc
        expected_signature = _b64encode(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise PreviewTokenError("preview_tampered", "Preview token signature is invalid")
        try:
            payload = json.loads(_b64decode(body).decode("utf-8"))
            if payload.get("v") != 1:
                raise ValueError("unsupported version")
            token_actor_id = UUID(str(payload["actor_user_id"]))
            observed = _require_minor_units(payload["observed_before_minor"], "observed_before_minor")
            issued_at = datetime.fromtimestamp(int(payload["issued_at"]), tz=timezone.utc)
            expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc)
            request_digest = str(payload["request_hash"])
        except PreviewTokenError:
            raise
        except Exception as exc:
            raise PreviewTokenError("preview_invalid", "Preview token payload is invalid") from exc

        checked_at = _aware_utc(now or _utc_now())
        if checked_at >= expires_at:
            raise PreviewExpiredError("preview_expired", "Preview token has expired")
        if issued_at > checked_at + timedelta(seconds=60) or expires_at <= issued_at:
            raise PreviewTokenError("preview_invalid", "Preview token timestamps are invalid")
        expected_request_digest = preview_request_hash(request, actor_user_id)
        if token_actor_id != actor_user_id or not hmac.compare_digest(request_digest, expected_request_digest):
            raise PreviewTokenError("preview_request_mismatch", "Preview does not match the actor or request")
        return VerifiedPreview(
            request_hash=request_digest,
            actor_user_id=token_actor_id,
            observed_before_minor=observed,
            issued_at=issued_at,
            expires_at=expires_at,
            preview_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
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


@dataclass(frozen=True)
class ProviderBudgetCommand:
    id: UUID
    request: BudgetChangeRequest
    actor_user_id: UUID
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
        idempotency_key: str,
        request_hash: str,
        preview_hash: str,
        observed_before_minor: int,
        now: datetime,
    ) -> CommandSubmission: ...

    def get(self, command_id: UUID) -> Optional[ProviderBudgetCommand]: ...

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


class InMemoryBudgetCommandStore:
    """Reference store for tests and future adapters; it is not wired into production."""

    def __init__(self):
        self._items: dict[UUID, ProviderBudgetCommand] = {}
        self._idempotency: dict[tuple[UUID, str], UUID] = {}
        self._lock = RLock()

    @staticmethod
    def _copy(command: ProviderBudgetCommand) -> ProviderBudgetCommand:
        return copy.deepcopy(command)

    def create_or_replay(
        self,
        *,
        request: BudgetChangeRequest,
        actor_user_id: UUID,
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
            created_at = _aware_utc(now)
            command = ProviderBudgetCommand(
                id=uuid4(),
                request=request,
                actor_user_id=actor_user_id,
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
            return CommandSubmission(command=self._copy(command), replayed=False)

    def get(self, command_id: UUID) -> Optional[ProviderBudgetCommand]:
        with self._lock:
            command = self._items.get(command_id)
            return self._copy(command) if command else None

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
        preview_signer: PreviewSigner,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.adapter = adapter
        self.store = store
        self.preview_signer = preview_signer
        self.clock = clock

    def preview(
        self,
        request: BudgetChangeRequest,
        *,
        actor: BudgetActorContext,
        tenant: BudgetTenantContext,
        credential: BudgetCredentialContext,
    ) -> BudgetPreview:
        authorize_budget_write(request, actor=actor, tenant=tenant, credential=credential)
        snapshot = self.adapter.read_budget(request)
        _snapshot_matches_request(snapshot, request)
        if request.expected_current_minor is not None and snapshot.amount_minor != request.expected_current_minor:
            raise BudgetConflictError(
                "expected_budget_changed",
                "Provider budget changed before preview; refresh and confirm the new value",
            )
        return self.preview_signer.issue(
            request,
            actor_user_id=actor.user_id,
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
        authorize_budget_write(request, actor=actor, tenant=tenant, credential=credential)
        now = self.clock()
        verified = self.preview_signer.verify(
            preview_token,
            request,
            actor_user_id=actor.user_id,
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
        started_at = _aware_utc(self.clock())
        command = self.store.claim_queued(command_id, now=started_at)

        before_minor: Optional[int] = None
        after_minor: Optional[int] = None
        trace_id: Optional[str] = None
        error: Optional[SanitizedProviderError] = None
        status = BudgetCommandStatus.UNKNOWN
        write_accepted = False

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
        except Exception as exc:
            status = BudgetCommandStatus.UNKNOWN if write_accepted else BudgetCommandStatus.FAILED
            error = _internal_error(exc)

        finished_at = _aware_utc(self.clock())
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
            raise BudgetPolicyError("command_actor_mismatch", "Only the command actor may reconcile this command")
        authorize_budget_write(command.request, actor=actor, tenant=tenant, credential=credential)

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
            confirmed_after_minor=after_minor if status == BudgetCommandStatus.APPLIED else None,
            provider_trace_id=trace_id,
            error=error,
            reconciliation=True,
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
