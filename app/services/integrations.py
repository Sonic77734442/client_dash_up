from __future__ import annotations

import os
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Dict, List, Optional
from uuid import UUID

from app.schemas import (
    AdAccountOut,
    AdAccountSyncJobOut,
    AuthIdentityOut,
    AuthProviderConfigOut,
    IntegrationEventOut,
    IntegrationProviderOut,
    IntegrationsOverviewResponse,
)


def _sanitize_error_message(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    msg = str(raw).strip().lower()
    if not msg:
        return None
    if "expired" in msg or "unauthorized" in msg or "invalid token" in msg:
        return "Authentication expired or invalid. Reconnect provider."
    if "scope" in msg or "permission" in msg or "forbidden" in msg or "access" in msg:
        return "Insufficient permissions for required API scopes."
    if "rate" in msg or "throttl" in msg or "quota" in msg:
        return "Provider is rate-limiting requests. Retry later or reduce sync load."
    if "not set" in msg or "credentials" in msg or "credential" in msg:
        return "Provider credentials are missing or incomplete."
    return "Provider request failed. Check diagnostics and retry sync."


def _normalize_provider_name(value: str) -> str:
    v = (value or "").lower().strip()
    if v in {"facebook", "meta"}:
        return "meta"
    if v in {"google", "google_ads"}:
        return "google"
    if v in {"tiktok", "tt"}:
        return "tiktok"
    return v


def _provider_auth_state(
    provider: str,
    cfg: Optional[AuthProviderConfigOut],
    *,
    identity_linked_users: int = 0,
) -> tuple[str, str, List[str], List[str], bool, str]:
    sources: List[str] = []
    missing: List[str] = []
    sync_ready = False
    readiness_reason = "Provider credentials are incomplete"

    if cfg and not cfg.enabled:
        return "disabled", "Integration disabled in provider config", sources, missing, False, "Provider is disabled"
    if cfg and cfg.enabled:
        sources.append("provider_config")
    if identity_linked_users > 0:
        sources.append("identity_link")

    p = provider.lower().strip()
    if p == "meta":
        has_env = bool(os.getenv("META_ACCESS_TOKEN", "").strip())
        if has_env:
            sources.append("env_credentials")
        else:
            missing.append("META_ACCESS_TOKEN")
        if has_env or identity_linked_users > 0:
            sync_ready = True
            readiness_reason = "Ready via configured token or linked OAuth identity"
        return (
            "configured" if sync_ready else "missing",
            "Token configured" if has_env else "META access token not set",
            sources,
            missing,
            sync_ready,
            readiness_reason,
        )
    if p == "google":
        required = [
            "GOOGLE_ADS_DEVELOPER_TOKEN",
            "GOOGLE_ADS_CLIENT_ID",
            "GOOGLE_ADS_CLIENT_SECRET",
            "GOOGLE_ADS_REFRESH_TOKEN",
        ]
        missing_env = [k for k in required if not bool(os.getenv(k, "").strip())]
        has_env = not missing_env
        if has_env:
            sources.append("env_credentials")
        else:
            missing.extend(missing_env)
        if has_env or identity_linked_users > 0:
            sync_ready = True
            readiness_reason = "Ready via configured credentials or linked Google identity"
        return (
            "configured" if sync_ready else "missing",
            "OAuth credentials configured" if has_env else "Google Ads credentials are incomplete",
            sources,
            missing,
            sync_ready,
            readiness_reason,
        )
    if p == "tiktok":
        has_env = bool(os.getenv("TIKTOK_ACCESS_TOKEN", "").strip())
        if has_env:
            sources.append("env_credentials")
            sync_ready = True
            readiness_reason = "Ready via configured token"
        else:
            missing.append("TIKTOK_ACCESS_TOKEN")
        return (
            "configured" if sync_ready else "missing",
            "Token configured" if has_env else "TIKTOK_ACCESS_TOKEN is not set",
            sources,
            missing,
            sync_ready,
            readiness_reason,
        )

    if cfg and cfg.enabled:
        return "configured", "Provider config enabled", sources, missing, False, "Provider config exists, sync credentials not detected"
    return "missing", "Provider credentials not found", sources, missing, False, "Provider credentials not found"


def _provider_scopes(provider: str) -> List[str]:
    p = provider.lower().strip()
    if p == "meta":
        return ["ads_read", "manage_pages"]
    if p == "google":
        return ["adwords"]
    if p == "tiktok":
        return ["video.list", "user.info"]
    return []


def _derive_status(
    *,
    auth_state: str,
    linked_accounts_count: int,
    last_success: Optional[AdAccountSyncJobOut],
    last_error: Optional[AdAccountSyncJobOut],
) -> tuple[str, Optional[str]]:
    if linked_accounts_count == 0 and auth_state in {"missing", "disabled"}:
        return "disconnected", "No linked accounts and provider is not configured"

    if last_error and (not last_success or last_error.started_at >= last_success.started_at):
        return "error", "Latest sync attempt failed"

    if auth_state in {"missing", "disabled"}:
        return "warning", "Provider auth is not fully configured"

    if last_success:
        return "healthy", "Latest sync completed successfully"

    return "warning", "No successful sync yet"


def build_integrations_overview(
    *,
    accounts: List[AdAccountOut],
    sync_jobs: List[AdAccountSyncJobOut],
    provider_configs: List[AuthProviderConfigOut],
    identities: Optional[List[AuthIdentityOut]] = None,
) -> IntegrationsOverviewResponse:
    identities = identities or []
    providers_in_data = {a.platform for a in accounts if a.platform}
    providers_in_cfg = {c.provider for c in provider_configs if c.provider}
    providers_in_identities = {_normalize_provider_name(i.provider) for i in identities if i.provider}
    providers = sorted(
        {
            _normalize_provider_name(p)
            for p in providers_in_data.union(providers_in_cfg).union(providers_in_identities)
            if p
        }
    )

    by_provider_accounts: Dict[str, List[AdAccountOut]] = {p: [] for p in providers}
    for acc in accounts:
        p = (acc.platform or "").lower().strip()
        if p in by_provider_accounts:
            by_provider_accounts[p].append(acc)

    by_provider_jobs: Dict[str, List[AdAccountSyncJobOut]] = {p: [] for p in providers}
    account_provider: Dict[UUID, str] = {a.id: (a.platform or "").lower().strip() for a in accounts}
    for job in sync_jobs:
        p = account_provider.get(job.ad_account_id)
        if p in by_provider_jobs:
            by_provider_jobs[p].append(job)

    cfg_map: Dict[str, AuthProviderConfigOut] = {
        _normalize_provider_name(c.provider): c for c in provider_configs
    }
    identity_count_by_provider: Dict[str, int] = {}
    unique_users_by_provider: Dict[str, set[UUID]] = {}
    for identity in identities:
        p = _normalize_provider_name(identity.provider)
        unique_users_by_provider.setdefault(p, set()).add(identity.user_id)
    for p, users in unique_users_by_provider.items():
        identity_count_by_provider[p] = len(users)

    provider_rows: List[IntegrationProviderOut] = []
    all_events: List[IntegrationEventOut] = []

    for provider in providers:
        p_accounts = by_provider_accounts.get(provider, [])
        p_jobs = sorted(by_provider_jobs.get(provider, []), key=lambda x: x.started_at, reverse=True)
        last_success = next((j for j in p_jobs if j.status == "success"), None)
        last_error = next((j for j in p_jobs if j.status == "error"), None)
        last_heartbeat = p_jobs[0].started_at if p_jobs else None

        auth_state, token_hint, sources, missing_requirements, sync_ready, sync_readiness_reason = _provider_auth_state(
            provider,
            cfg_map.get(provider),
            identity_linked_users=identity_count_by_provider.get(provider, 0),
        )
        status, status_reason = _derive_status(
            auth_state=auth_state,
            linked_accounts_count=len(p_accounts),
            last_success=last_success,
            last_error=last_error,
        )
        safe_error = _sanitize_error_message(last_error.error_message if last_error else None)

        row = IntegrationProviderOut(
            provider=provider,
            status=status,
            status_reason=status_reason,
            auth_state=auth_state,
            token_hint=token_hint,
            connection_sources=sources,
            missing_requirements=missing_requirements,
            identity_linked_users=identity_count_by_provider.get(provider, 0),
            sync_ready=sync_ready,
            sync_readiness_reason=sync_readiness_reason,
            scopes=_provider_scopes(provider),
            linked_accounts_count=len(p_accounts),
            affected_clients_count=len({a.client_id for a in p_accounts}),
            last_heartbeat_at=last_heartbeat,
            last_successful_sync_at=last_success.started_at if last_success else None,
            last_error_time=last_error.started_at if last_error else None,
            last_error_safe=safe_error,
            reconnect_available=True,
        )
        provider_rows.append(row)

        for j in p_jobs[:8]:
            level = "success" if j.status == "success" else "error"
            msg = "Sync completed successfully" if j.status == "success" else (_sanitize_error_message(j.error_message) or "Sync failed")
            all_events.append(
                IntegrationEventOut(
                    provider=provider,
                    level=level,
                    title=("Sync Completed" if level == "success" else "Sync Failed") + f": {provider.title()}",
                    message=msg,
                    occurred_at=j.started_at,
                    sync_job_id=j.id,
                )
            )

    all_events.sort(key=lambda x: x.occurred_at, reverse=True)
    events = all_events[:30]

    healthy = len([x for x in provider_rows if x.status == "healthy"])
    warning = len([x for x in provider_rows if x.status == "warning"])
    error = len([x for x in provider_rows if x.status == "error"])
    disconnected = len([x for x in provider_rows if x.status == "disconnected"])

    summary = {
        "connected_providers": len(provider_rows),
        "healthy_connections": healthy,
        "warning_connections": warning,
        "critical_issues": error + disconnected,
        "active_nodes": len(accounts),
        "total_errors_24h": len([e for e in events if e.level == "error" and (_utcnow() - e.occurred_at).total_seconds() <= 86400]),
    }

    return IntegrationsOverviewResponse(summary=summary, providers=provider_rows, events=events)


