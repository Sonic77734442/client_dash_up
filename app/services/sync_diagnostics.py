"""Fixed public sync diagnostics; never echo provider exception text or bodies."""
from __future__ import annotations


SYNC_ERROR_MESSAGES = {
    "provider_payload_invalid": "The provider returned invalid metric data. No rows were saved; check the account and requested date range.",
    "provider_credentials_missing": "Provider credentials are missing or incomplete. Connect the advertising account before syncing.",
    "provider_reconnect_required": "Reconnect Meta Ads and grant advertising access before syncing.",
    "auth_failed": "Provider authorization failed. Reconnect the advertising account and verify its permissions.",
    "rate_limited": "The provider rate limit was reached. Synchronization will retry after the scheduled delay.",
    "provider_unavailable": "The provider is temporarily unavailable. Synchronization will retry after the scheduled delay.",
    "invalid_request": "The provider rejected the sync request. Check the advertising account and requested date range.",
    "provider_not_supported": "Synchronization is not supported for this advertising provider.",
    "unknown_error": "Synchronization failed. Contact support with the sync job ID.",
}

# These are application-owned reconnect reasons, not provider-supplied messages.
META_RECONNECT_MESSAGES = {
    "meta_access_token_missing": "Meta Ads is not connected. Connect Meta Ads to continue.",
    "meta_connection_unverified": "This Meta connection predates verification. Reconnect Meta Ads to verify it.",
    "meta_token_app_mismatch": "This Meta connection belongs to a different application. Reconnect Meta Ads.",
    "meta_business_config_mismatch": "This Meta connection uses an outdated Business Login configuration. Reconnect Meta Ads.",
    "meta_token_user_missing": "The Meta account owner could not be verified. Reconnect Meta Ads.",
    "meta_permissions_missing": "Meta Ads permissions are incomplete. Reconnect Meta Ads and grant advertising access.",
    "meta_token_expiry_missing": "The Meta connection expiry cannot be verified. Reconnect Meta Ads.",
    "meta_token_expired": "The Meta authorization has expired. Reconnect Meta Ads.",
    "meta_bound_credential_missing": "The Meta connection assigned to this ad account is no longer active. Reconnect Meta Ads and rediscover the account.",
    "meta_credential_selection_required": "This Meta ad account is not assigned to one connection. Rediscover the account before syncing.",
    "meta_rediscovery_required": "This Meta ad account moved to another client. Rediscover it before syncing.",
    "meta_reconnect_required": SYNC_ERROR_MESSAGES["provider_reconnect_required"],
}

GOOGLE_MANAGER_MESSAGE = "Metrics cannot be requested for a manager account. Select an individual Google advertising account."
BLOCKED_ACCOUNT_MESSAGE = "The provider customer account is blocked, disabled, or not enabled. Check its status in the provider console before syncing."
PERMISSION_MESSAGE = "Provider permissions are incomplete. Check the required API scopes for this advertising account."
_BLOCKED_ACCOUNT_MARKERS = ("customer_not_enabled", "not enabled", "blocked", "disabled", "suspended")
_PUBLIC_MESSAGES = frozenset((*SYNC_ERROR_MESSAGES.values(), *META_RECONNECT_MESSAGES.values(), GOOGLE_MANAGER_MESSAGE, BLOCKED_ACCOUNT_MESSAGE, PERMISSION_MESSAGE))


def safe_meta_reconnect_code(code: object) -> str:
    return code if isinstance(code, str) and code in META_RECONNECT_MESSAGES else "meta_reconnect_required"


def safe_sync_error_message(code: object, message: object = None) -> str:
    """Also protect old stored rows without modifying historical database data."""
    if isinstance(message, str) and message in _PUBLIC_MESSAGES:
        return message
    if isinstance(message, str):
        # Preserve existing account-blocked alert semantics for new exceptions
        # and historical rows, but emit only application-owned fixed text.
        raw = message.lower()
        if any(marker in raw for marker in _BLOCKED_ACCOUNT_MARKERS):
            return BLOCKED_ACCOUNT_MESSAGE
        if code == "invalid_request" and (
            "requested_metrics_for_manager" in raw or "metrics cannot be requested for a manager account" in raw
        ):
            return GOOGLE_MANAGER_MESSAGE
        # Some SDKs wrap a permissions failure in HTTP 5xx. Preserve this
        # actionable hint independently of the existing status/retry policy.
        if any(marker in raw for marker in ("scope", "permission", "forbidden")):
            return PERMISSION_MESSAGE
    if isinstance(code, str) and code in SYNC_ERROR_MESSAGES:
        return SYNC_ERROR_MESSAGES[code]
    return SYNC_ERROR_MESSAGES["unknown_error"]


def safe_sync_metadata(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict) or metadata.get("sync_error") is None:
        return metadata
    return {**metadata, "sync_error": safe_sync_error_message(metadata.get("sync_error_code"), metadata["sync_error"])}
