from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence

import httpx

from app.services.meta_version import meta_graph_api_version


META_VALIDATION_VERSION = 1
# Synchronization needs read access. ``ads_management`` also satisfies that
# requirement, while optional Business Portfolio enumeration and future write
# controls enforce their additional permissions at their own boundary.
DEFAULT_REQUIRED_PERMISSIONS = ("ads_read",)
ADS_READ_EQUIVALENTS = frozenset({"ads_read", "ads_management"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetaConnectionValidationError(RuntimeError):
    """A safe, user-actionable Meta connection validation failure.

    Provider payloads and access tokens deliberately never become part of the
    exception text. The code is stable enough for the API/UI to map to a
    reconnect action without exposing Meta's raw response.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


class MetaCredentialReconnectRequiredError(RuntimeError):
    """The exact Meta credential for an account must be reconnected."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class MetaCredentialDiagnostic:
    status: str
    code: str
    message: str
    reconnect_required: bool
    expires_at: Optional[int] = None


def required_meta_permissions(value: Optional[object] = None) -> tuple[str, ...]:
    raw = value
    if raw is None:
        raw = os.getenv("META_REQUIRED_PERMISSIONS", "")
    if isinstance(raw, str):
        parsed = [part.strip().lower() for part in raw.replace(" ", ",").split(",")]
    elif isinstance(raw, Iterable):
        parsed = [str(part or "").strip().lower() for part in raw]
    else:
        parsed = []
    normalized = tuple(dict.fromkeys(part for part in parsed if part))
    return normalized or DEFAULT_REQUIRED_PERMISSIONS


def _permission_satisfied(required: str, granted: set[str]) -> bool:
    if required == "ads_read":
        return bool(ADS_READ_EQUIVALENTS.intersection(granted))
    return required in granted


def _missing_permissions(required: Sequence[str], granted: set[str]) -> list[str]:
    return [permission for permission in required if not _permission_satisfied(permission, granted)]


def _positive_epoch(value: object) -> Optional[int]:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _integer(value: object, *, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_granular_scopes(value: object) -> list[Dict[str, object]]:
    if not isinstance(value, list):
        return []
    safe: list[Dict[str, object]] = []
    for row in value[:100]:
        if not isinstance(row, Mapping):
            continue
        scope = str(row.get("scope") or "").strip().lower()[:200]
        if not scope:
            continue
        target_value = row.get("target_ids")
        target_ids = (
            [str(target or "").strip()[:200] for target in target_value[:500] if str(target or "").strip()]
            if isinstance(target_value, list)
            else []
        )
        safe.append({"scope": scope, "target_ids": target_ids})
    return safe


def _response_json(response: object, *, code: str) -> Mapping[str, object]:
    status_code = _integer(getattr(response, "status_code", 0))
    if status_code < 200 or status_code >= 300:
        raise MetaConnectionValidationError(
            code,
            "Meta could not verify this connection. Reconnect Meta Ads and try again.",
        )
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception as exc:
        raise MetaConnectionValidationError(
            code,
            "Meta returned an invalid verification response. Reconnect Meta Ads and try again.",
        ) from exc
    if not isinstance(payload, Mapping):
        raise MetaConnectionValidationError(
            code,
            "Meta returned an invalid verification response. Reconnect Meta Ads and try again.",
        )
    return payload


def _validate_with_get(
    request_get: Callable[..., object],
    *,
    access_token: str,
    app_id: str,
    app_secret: str,
    config_id: str,
    expected_user_id: str,
    token_payload: Mapping[str, object],
    required_permissions: Sequence[str],
    now: datetime,
) -> Dict[str, object]:
    try:
        graph_root = f"https://graph.facebook.com/{meta_graph_api_version()}"
    except Exception as exc:
        raise MetaConnectionValidationError(
            "meta_connection_validation_config_invalid",
            "Meta Business connection verification is not configured correctly.",
        ) from exc
    try:
        debug_response = request_get(
            f"{graph_root}/debug_token",
            params={"input_token": access_token},
            headers={"Authorization": f"Bearer {app_id}|{app_secret}"},
        )
    except MetaConnectionValidationError:
        raise
    except Exception as exc:
        raise MetaConnectionValidationError(
            "meta_token_validation_unavailable",
            "Meta connection verification is temporarily unavailable. Try connecting again.",
        ) from exc

    debug_payload = _response_json(debug_response, code="meta_token_validation_failed")
    debug_data = debug_payload.get("data")
    if not isinstance(debug_data, Mapping):
        raise MetaConnectionValidationError(
            "meta_token_validation_failed",
            "Meta returned an invalid token verification response. Reconnect Meta Ads.",
        )
    if debug_data.get("is_valid") is not True:
        raise MetaConnectionValidationError(
            "meta_token_invalid",
            "The Meta authorization is no longer valid. Reconnect Meta Ads.",
        )

    token_app_id = str(debug_data.get("app_id") or "").strip()
    if not token_app_id or token_app_id != app_id:
        raise MetaConnectionValidationError(
            "meta_token_app_mismatch",
            "The Meta authorization belongs to a different application. Reconnect Meta Ads.",
        )

    token_user_id = str(debug_data.get("user_id") or "").strip()
    if not token_user_id or token_user_id != expected_user_id:
        raise MetaConnectionValidationError(
            "meta_token_user_mismatch",
            "Meta returned a different account than the one that authorized this connection.",
        )

    # Meta does not consistently include a Business Login configuration id in
    # debug_token. The callback configuration is nevertheless server-bound by
    # the consumed OAuth state. When Meta does return the id, require an exact
    # match instead of silently accepting a token minted by another config.
    debug_config_id = str(
        debug_data.get("config_id") or debug_data.get("configuration_id") or ""
    ).strip()
    if debug_config_id and debug_config_id != config_id:
        raise MetaConnectionValidationError(
            "meta_business_config_mismatch",
            "The Meta authorization used a different Business Login configuration.",
        )

    scopes_value = debug_data.get("scopes")
    if not isinstance(scopes_value, list):
        raise MetaConnectionValidationError(
            "meta_token_scopes_missing",
            "Meta did not return the permissions needed to verify this connection. Reconnect Meta Ads.",
        )
    debug_scopes = {str(scope or "").strip().lower() for scope in scopes_value if str(scope or "").strip()}

    try:
        permissions_response = request_get(
            f"{graph_root}/me/permissions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except Exception as exc:
        raise MetaConnectionValidationError(
            "meta_permissions_validation_failed",
            "Meta permissions verification is temporarily unavailable. Try connecting again.",
        ) from exc
    permissions_payload = _response_json(
        permissions_response,
        code="meta_permissions_validation_failed",
    )
    permission_rows = permissions_payload.get("data")
    if not isinstance(permission_rows, list):
        raise MetaConnectionValidationError(
            "meta_permissions_validation_failed",
            "Meta did not return a valid permissions list. Reconnect Meta Ads.",
        )
    granted_permissions = {
        str(row.get("permission") or "").strip().lower()
        for row in permission_rows
        if isinstance(row, Mapping)
        and str(row.get("status") or "").strip().lower() == "granted"
        and str(row.get("permission") or "").strip()
    }
    missing_debug = _missing_permissions(required_permissions, debug_scopes)
    missing_grants = _missing_permissions(required_permissions, granted_permissions)
    missing = sorted(set(missing_debug).union(missing_grants))
    if missing:
        raise MetaConnectionValidationError(
            "meta_permissions_missing",
            "Meta Ads access is incomplete. Reconnect and grant the requested advertising permissions.",
        )

    now_epoch = int(now.timestamp())
    expires_at = _positive_epoch(debug_data.get("expires_at"))
    data_access_expires_at = _positive_epoch(debug_data.get("data_access_expires_at"))
    if expires_at is None:
        expires_in = _positive_epoch(token_payload.get("expires_in"))
        if expires_in is not None:
            expires_at = now_epoch + expires_in
    if expires_at is None:
        raise MetaConnectionValidationError(
            "meta_token_expiry_missing",
            "Meta did not return a verifiable token expiry. Reconnect Meta Ads.",
        )
    if expires_at <= now_epoch:
        raise MetaConnectionValidationError(
            "meta_token_expired",
            "The Meta authorization has expired. Reconnect Meta Ads.",
        )
    if data_access_expires_at is not None and data_access_expires_at <= now_epoch:
        raise MetaConnectionValidationError(
            "meta_data_access_expired",
            "Meta data access has expired. Reconnect Meta Ads.",
        )
    absolute_expires_at = min(
        epoch for epoch in (expires_at, data_access_expires_at) if epoch is not None
    )

    safe_granular_scopes = _safe_granular_scopes(debug_data.get("granular_scopes"))
    return {
        "meta_validation_version": META_VALIDATION_VERSION,
        "meta_validated_at": now.astimezone(timezone.utc).isoformat(),
        "meta_app_id": app_id,
        "meta_business_config_id": config_id,
        "meta_business_config_source": "oauth_state",
        "meta_user_id": token_user_id,
        "meta_scopes": sorted(debug_scopes),
        "meta_granted_permissions": sorted(granted_permissions),
        "meta_granular_scopes": safe_granular_scopes,
        "meta_expires_at": expires_at,
        "meta_data_access_expires_at": data_access_expires_at,
        "meta_absolute_expires_at": absolute_expires_at,
    }


def validate_meta_business_connection(
    *,
    access_token: object,
    app_id: object,
    app_secret: object,
    config_id: object,
    expected_user_id: object,
    token_payload: Optional[Mapping[str, object]] = None,
    required_permissions: Optional[object] = None,
    now: Optional[datetime] = None,
    request_get: Optional[Callable[..., object]] = None,
) -> Dict[str, object]:
    """Validate a Business Login token and return safe persisted metadata.

    The returned dictionary contains no new secret. Callers merge it into the
    credential payload only after every app/user/permission/expiry check passes.
    """

    token = str(access_token or "").strip()
    expected_app_id = str(app_id or "").strip()
    secret = str(app_secret or "").strip()
    expected_config_id = str(config_id or "").strip()
    user_id = str(expected_user_id or "").strip()
    if not token or not expected_app_id or not secret or not expected_config_id or not user_id:
        raise MetaConnectionValidationError(
            "meta_connection_validation_config_missing",
            "Meta Business connection verification is not configured correctly.",
        )
    current = now or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    permissions = required_meta_permissions(required_permissions)
    payload = token_payload or {}

    if request_get is not None:
        return _validate_with_get(
            request_get,
            access_token=token,
            app_id=expected_app_id,
            app_secret=secret,
            config_id=expected_config_id,
            expected_user_id=user_id,
            token_payload=payload,
            required_permissions=permissions,
            now=current,
        )

    with httpx.Client(timeout=20.0) as client:
        return _validate_with_get(
            client.get,
            access_token=token,
            app_id=expected_app_id,
            app_secret=secret,
            config_id=expected_config_id,
            expected_user_id=user_id,
            token_payload=payload,
            required_permissions=permissions,
            now=current,
        )


def diagnose_meta_credentials(
    credentials: Optional[Mapping[str, object]],
    *,
    expected_app_id: Optional[object] = None,
    expected_config_id: Optional[object] = None,
    required_permissions: Optional[object] = None,
    now: Optional[datetime] = None,
) -> MetaCredentialDiagnostic:
    """Return a safe local reconnect diagnostic without calling Meta."""

    values = credentials or {}
    if not str(values.get("access_token") or "").strip():
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_access_token_missing",
            message="Meta Ads is not connected. Connect Meta Ads to continue.",
            reconnect_required=True,
        )
    if _integer(values.get("meta_validation_version")) != META_VALIDATION_VERSION:
        return MetaCredentialDiagnostic(
            status="unverified",
            code="meta_connection_unverified",
            message="This Meta connection predates verification. Reconnect Meta Ads to verify it.",
            reconnect_required=True,
        )

    stored_app_id = str(values.get("meta_app_id") or "").strip()
    required_app_id = str(
        expected_app_id if expected_app_id is not None else os.getenv("FACEBOOK_CLIENT_ID", "")
    ).strip()
    if required_app_id and stored_app_id != required_app_id:
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_token_app_mismatch",
            message="This Meta connection belongs to a different application. Reconnect Meta Ads.",
            reconnect_required=True,
        )
    stored_config_id = str(values.get("meta_business_config_id") or "").strip()
    required_config_id = str(
        expected_config_id
        if expected_config_id is not None
        else os.getenv("FACEBOOK_LOGIN_CONFIG_ID", "")
    ).strip()
    if required_config_id and stored_config_id != required_config_id:
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_business_config_mismatch",
            message="This Meta connection uses an outdated Business Login configuration. Reconnect Meta Ads.",
            reconnect_required=True,
        )
    if not str(values.get("meta_user_id") or "").strip():
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_token_user_missing",
            message="The Meta account owner could not be verified. Reconnect Meta Ads.",
            reconnect_required=True,
        )

    granted_value = values.get("meta_granted_permissions")
    granted = {
        str(permission or "").strip().lower()
        for permission in granted_value
        if str(permission or "").strip()
    } if isinstance(granted_value, list) else set()
    if _missing_permissions(required_meta_permissions(required_permissions), granted):
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_permissions_missing",
            message="Meta Ads permissions are incomplete. Reconnect and grant advertising access.",
            reconnect_required=True,
        )

    expires_at = _positive_epoch(values.get("meta_absolute_expires_at"))
    if expires_at is None:
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_token_expiry_missing",
            message="The Meta connection expiry cannot be verified. Reconnect Meta Ads.",
            reconnect_required=True,
        )
    current = now or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds_remaining = expires_at - int(current.timestamp())
    if seconds_remaining <= 0:
        return MetaCredentialDiagnostic(
            status="error",
            code="meta_token_expired",
            message="The Meta authorization has expired. Reconnect Meta Ads.",
            reconnect_required=True,
            expires_at=expires_at,
        )
    if seconds_remaining <= 7 * 24 * 60 * 60:
        return MetaCredentialDiagnostic(
            status="warning",
            code="meta_token_expiring",
            message="The Meta authorization expires soon. Reconnect Meta Ads to avoid interrupted sync.",
            reconnect_required=False,
            expires_at=expires_at,
        )
    return MetaCredentialDiagnostic(
        status="ready",
        code="meta_connection_ready",
        message="Meta Ads connection is verified.",
        reconnect_required=False,
        expires_at=expires_at,
    )


def require_usable_validated_meta_credentials(
    credentials: Optional[Mapping[str, object]],
    *,
    expected_app_id: Optional[object] = None,
    expected_config_id: Optional[object] = None,
    required_permissions: Optional[object] = None,
    allow_unverified_legacy: bool = True,
    now: Optional[datetime] = None,
) -> MetaCredentialDiagnostic:
    diagnostic = diagnose_meta_credentials(
        credentials,
        expected_app_id=expected_app_id,
        expected_config_id=expected_config_id,
        required_permissions=required_permissions,
        now=now,
    )
    if diagnostic.status == "unverified" and allow_unverified_legacy:
        return diagnostic
    if diagnostic.reconnect_required:
        raise MetaCredentialReconnectRequiredError(diagnostic.code, diagnostic.message)
    return diagnostic
