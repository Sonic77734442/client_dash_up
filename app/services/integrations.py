from __future__ import annotations

import os
from datetime import date, datetime, timezone

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
from app.services.ad_accounts import canonical_external_account_id


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
    stored_credentials: int = 0,
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
    if stored_credentials > 0:
        sources.append("stored_credentials")

    p = provider.lower().strip()
    if p == "meta":
        has_env = bool(os.getenv("META_ACCESS_TOKEN", "").strip())
        if has_env:
            sources.append("env_credentials")
        elif stored_credentials == 0:
            missing.append("META_ACCESS_TOKEN")
        if has_env or stored_credentials > 0:
            sync_ready = True
            readiness_reason = "Ready via configured provider credentials"
        return (
            "configured" if sync_ready else "missing",
            "Token configured" if sync_ready else "META access token not set",
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
        elif stored_credentials == 0:
            missing.extend(missing_env)
        if has_env or stored_credentials > 0:
            sync_ready = True
            readiness_reason = "Ready via configured Google Ads credentials"
        return (
            "configured" if sync_ready else "missing",
            "OAuth credentials configured" if sync_ready else "Google Ads credentials are incomplete",
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
        elif stored_credentials > 0:
            sync_ready = True
            readiness_reason = "Ready via configured provider credentials"
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


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _as_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_date(value: object) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _latest_data_date_for_account(
    account: AdAccountOut,
    data_jobs: List[AdAccountSyncJobOut],
) -> Optional[date]:
    """Return the newest date for which there is evidence that rows exist.

    Newer writers can persist an exact ``latest_data_date`` in account metadata or
    job metadata. Legacy jobs only contain a requested range, so ``date_from`` is
    used as a conservative lower bound when rows were actually written. Using
    ``date_to`` would incorrectly declare a provider fresh when a long backfill
    returned only an old row.
    """

    metadata = account.metadata if isinstance(account.metadata, dict) else {}
    for key in ("latest_data_date", "last_data_at", "stats_latest_data_date"):
        parsed = _parse_date(metadata.get(key))
        if parsed:
            return parsed

    candidates: List[date] = []
    for job in data_jobs:
        request_meta = job.request_meta if isinstance(job.request_meta, dict) else {}
        exact = _parse_date(request_meta.get("latest_data_date"))
        if exact:
            candidates.append(exact)
            continue
        lower_bound = _parse_date(request_meta.get("date_from"))
        if lower_bound:
            candidates.append(lower_bound)
    return max(candidates) if candidates else None


def _derive_status(
    *,
    auth_state: str,
    active_accounts_count: int,
    accounts_with_data_count: int,
    error_accounts_count: int,
    never_synced_accounts_count: int,
    stale_accounts_count: int,
    assignment_conflict_accounts_count: int,
) -> tuple[str, Optional[str]]:
    if active_accounts_count == 0 and auth_state in {"missing", "disabled"}:
        return "disconnected", "No linked accounts and provider is not configured"
    if active_accounts_count == 0:
        return "warning", "Provider is configured but has no active ad accounts"
    if assignment_conflict_accounts_count:
        return "error", f"{assignment_conflict_accounts_count} active account mapping(s) have ambiguous client ownership"
    if error_accounts_count:
        return "error", f"Latest sync failed for {error_accounts_count} active account(s)"
    if auth_state in {"missing", "disabled"}:
        return "warning", "Provider auth is not fully configured"
    if never_synced_accounts_count:
        return "warning", f"{never_synced_accounts_count} active account(s) have never been synced"
    if accounts_with_data_count < active_accounts_count:
        missing = active_accounts_count - accounts_with_data_count
        return "warning", f"No stored metric rows for {missing} active account(s)"
    if stale_accounts_count:
        return "warning", f"Data or sync heartbeat is stale for {stale_accounts_count} active account(s)"
    return "healthy", "All active accounts are covered by fresh syncs and metric rows"


def build_integrations_overview(
    *,
    accounts: List[AdAccountOut],
    sync_jobs: List[AdAccountSyncJobOut],
    provider_configs: List[AuthProviderConfigOut],
    identities: Optional[List[AuthIdentityOut]] = None,
    ready_credentials_by_provider: Optional[Dict[str, int]] = None,
    now: Optional[datetime] = None,
) -> IntegrationsOverviewResponse:
    identities = identities or []
    ready_credentials_by_provider = ready_credentials_by_provider or {}
    current_time = _as_naive_utc(now) or _utcnow()
    sync_stale_after_hours = _positive_int_env("INTEGRATION_HEALTH_STALE_TTL_HOURS", 48)
    data_stale_after_days = _positive_int_env("AD_STATS_STALE_AFTER_DAYS", 3)
    providers_in_data = {a.platform for a in accounts if a.platform}
    providers_in_cfg = {c.provider for c in provider_configs if c.provider}
    providers_in_identities = {_normalize_provider_name(i.provider) for i in identities if i.provider}
    providers_in_credentials = {
        _normalize_provider_name(provider)
        for provider, count in ready_credentials_by_provider.items()
        if provider and count > 0
    }
    providers = sorted(
        {
            _normalize_provider_name(p)
            for p in providers_in_data.union(providers_in_cfg).union(providers_in_identities).union(providers_in_credentials)
            if p
        }
    )
    identity_groups: Dict[tuple[str, str], List[AdAccountOut]] = {}
    for account in accounts:
        if account.status != "active":
            continue
        provider = _normalize_provider_name(account.platform or "")
        identity = (provider, canonical_external_account_id(provider, account.external_account_id))
        identity_groups.setdefault(identity, []).append(account)
    assignment_conflict_ids = {
        account.id
        for group in identity_groups.values()
        if len(group) > 1
        for account in group
    }

    by_provider_accounts: Dict[str, List[AdAccountOut]] = {p: [] for p in providers}
    for acc in accounts:
        p = _normalize_provider_name(acc.platform or "")
        if p in by_provider_accounts:
            by_provider_accounts[p].append(acc)

    by_provider_jobs: Dict[str, List[AdAccountSyncJobOut]] = {p: [] for p in providers}
    account_provider: Dict[UUID, str] = {a.id: _normalize_provider_name(a.platform or "") for a in accounts}
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

        active_accounts = [account for account in p_accounts if account.status == "active"]
        assignment_conflict_accounts_count = len(
            [account for account in active_accounts if account.id in assignment_conflict_ids]
        )
        latest_job_by_account: Dict[UUID, AdAccountSyncJobOut] = {}
        data_jobs_by_account: Dict[UUID, List[AdAccountSyncJobOut]] = {}
        for job in p_jobs:
            latest_job_by_account.setdefault(job.ad_account_id, job)
            if job.status == "success" and int(job.records_synced or 0) > 0:
                data_jobs_by_account.setdefault(job.ad_account_id, []).append(job)

        error_accounts_count = 0
        never_synced_accounts_count = 0
        stale_accounts_count = 0
        successfully_synced_accounts_count = 0
        accounts_with_data_count = 0
        account_data_dates: List[date] = []
        current_error_jobs: List[AdAccountSyncJobOut] = []

        for account in active_accounts:
            latest_job = latest_job_by_account.get(account.id)
            latest_attempt_at = _as_naive_utc(latest_job.started_at if latest_job else account.last_sync_at)
            latest_status = latest_job.status if latest_job else account.sync_status
            if latest_attempt_at is None:
                never_synced_accounts_count += 1
            elif latest_status == "error":
                error_accounts_count += 1
                if latest_job:
                    current_error_jobs.append(latest_job)
            elif latest_status == "success":
                successfully_synced_accounts_count += 1

            data_jobs = data_jobs_by_account.get(account.id, [])
            latest_data_date = _latest_data_date_for_account(account, data_jobs)
            has_rows = bool(data_jobs) or latest_data_date is not None
            if has_rows:
                accounts_with_data_count += 1
            if latest_data_date:
                account_data_dates.append(latest_data_date)

            stale_sync = (
                latest_attempt_at is None
                or (current_time - latest_attempt_at).total_seconds() > sync_stale_after_hours * 3600
            )
            stale_data = (
                latest_data_date is None
                or max(0, (current_time.date() - latest_data_date).days) > data_stale_after_days
            )
            if stale_sync or stale_data:
                stale_accounts_count += 1

        latest_data_date = max(account_data_dates) if account_data_dates else None
        stale_days = max(0, (current_time.date() - latest_data_date).days) if latest_data_date else None
        active_accounts_count = len(active_accounts)
        coverage_percent = (
            round((accounts_with_data_count / active_accounts_count) * 100, 2)
            if active_accounts_count
            else 0.0
        )

        auth_state, token_hint, sources, missing_requirements, sync_ready, sync_readiness_reason = _provider_auth_state(
            provider,
            cfg_map.get(provider),
            identity_linked_users=identity_count_by_provider.get(provider, 0),
            stored_credentials=ready_credentials_by_provider.get(provider, 0),
        )
        status, status_reason = _derive_status(
            auth_state=auth_state,
            active_accounts_count=active_accounts_count,
            accounts_with_data_count=accounts_with_data_count,
            error_accounts_count=error_accounts_count,
            never_synced_accounts_count=never_synced_accounts_count,
            stale_accounts_count=stale_accounts_count,
            assignment_conflict_accounts_count=assignment_conflict_accounts_count,
        )
        current_error = max(current_error_jobs, key=lambda x: x.started_at) if current_error_jobs else None
        safe_error = _sanitize_error_message(current_error.error_message if current_error else None)

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
            active_accounts_count=active_accounts_count,
            successfully_synced_accounts_count=successfully_synced_accounts_count,
            accounts_with_data_count=accounts_with_data_count,
            error_accounts_count=error_accounts_count,
            never_synced_accounts_count=never_synced_accounts_count,
            stale_accounts_count=stale_accounts_count,
            assignment_conflict_accounts_count=assignment_conflict_accounts_count,
            coverage_percent=coverage_percent,
            rows_present=accounts_with_data_count > 0,
            latest_data_date=latest_data_date,
            stale_days=stale_days,
            affected_clients_count=len({a.client_id for a in p_accounts}),
            last_heartbeat_at=last_heartbeat,
            last_successful_sync_at=last_success.started_at if last_success else None,
            last_error_time=last_error.started_at if last_error else None,
            last_error_safe=safe_error,
            reconnect_available=True,
        )
        provider_rows.append(row)

        for j in p_jobs[:8]:
            level = (
                "success"
                if j.status == "success" and int(j.records_synced or 0) > 0
                else ("warning" if j.status == "success" else "error")
            )
            if j.status == "success" and int(j.records_synced or 0) == 0:
                msg = "Sync completed but returned no metric rows"
            else:
                msg = (
                    "Sync completed successfully"
                    if j.status == "success"
                    else (_sanitize_error_message(j.error_message) or "Sync failed")
                )
            all_events.append(
                IntegrationEventOut(
                    provider=provider,
                    level=level,
                    title=(
                        "Sync Completed"
                        if level == "success"
                        else ("No Data Returned" if level == "warning" else "Sync Failed")
                    )
                    + f": {provider.title()}",
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
    total_active_accounts = sum(x.active_accounts_count for x in provider_rows)
    total_accounts_with_data = sum(x.accounts_with_data_count for x in provider_rows)
    latest_data_dates = [x.latest_data_date for x in provider_rows if x.latest_data_date]
    errors_24h = 0
    for event in events:
        occurred_at = _as_naive_utc(event.occurred_at)
        age_seconds = (current_time - occurred_at).total_seconds() if occurred_at else -1
        if event.level == "error" and 0 <= age_seconds <= 86400:
            errors_24h += 1

    summary = {
        "connected_providers": len(provider_rows),
        "healthy_connections": healthy,
        "warning_connections": warning,
        "critical_issues": error + disconnected,
        "active_nodes": total_active_accounts,
        "active_accounts": total_active_accounts,
        "accounts_with_data": total_accounts_with_data,
        "coverage_percent": (
            round((total_accounts_with_data / total_active_accounts) * 100, 2)
            if total_active_accounts
            else 0.0
        ),
        "never_synced_accounts": sum(x.never_synced_accounts_count for x in provider_rows),
        "error_accounts": sum(x.error_accounts_count for x in provider_rows),
        "stale_accounts": sum(x.stale_accounts_count for x in provider_rows),
        "assignment_conflict_accounts": sum(x.assignment_conflict_accounts_count for x in provider_rows),
        "rows_present": total_accounts_with_data > 0,
        "latest_data_date": max(latest_data_dates) if latest_data_dates else None,
        "data_quality": (
            "healthy"
            if provider_rows and healthy == len(provider_rows)
            else ("insufficient_data" if total_accounts_with_data == 0 else "attention_required")
        ),
        "total_errors_24h": errors_24h,
    }

    return IntegrationsOverviewResponse(summary=summary, providers=provider_rows, events=events)


