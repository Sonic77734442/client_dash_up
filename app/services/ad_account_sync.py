from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import AdAccountOut, AdAccountPatch, AdAccountSyncJobOut, AdStatWrite, AdStatsIngestRequest
from app.services.ad_accounts import AdAccountStore, active_assignment_conflict_ids
from app.services.date_utils import meta_safe_date_from
from app.services.meta_connection import (
    MetaCredentialDiagnostic,
    MetaCredentialReconnectRequiredError,
    require_usable_validated_meta_credentials,
)
from app.services.providers import google_ads, meta, tiktok
from app.services.ad_stats import AdStatsStore


class AdAccountSyncJobStore(Protocol):
    def create(self, job: AdAccountSyncJobOut) -> AdAccountSyncJobOut: ...
    def list(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]: ...
    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]: ...
    def acquire_lease(self, *, lease_key: str, now: datetime, ttl_seconds: int) -> Optional[str]: ...
    def renew_lease(self, *, lease_key: str, lease_token: str, now: datetime, ttl_seconds: int) -> bool: ...
    def release_lease(self, *, lease_key: str, lease_token: str) -> None: ...


class SqliteAdAccountSyncJobStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_job(row) -> AdAccountSyncJobOut:
        return AdAccountSyncJobOut(
            id=UUID(row["id"]),
            ad_account_id=UUID(row["ad_account_id"]),
            provider=row["provider"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            records_synced=int(row["records_synced"] or 0),
            error_message=row["error_message"],
            error_code=row["error_code"] if "error_code" in row.keys() else None,
            error_category=row["error_category"] if "error_category" in row.keys() else None,
            retryable=bool(row["retryable"]) if "retryable" in row.keys() and row["retryable"] is not None else False,
            attempt=int(row["attempt"]) if "attempt" in row.keys() and row["attempt"] is not None else 1,
            next_retry_at=datetime.fromisoformat(row["next_retry_at"])
            if "next_retry_at" in row.keys() and row["next_retry_at"]
            else None,
            request_meta=json.loads(row["request_meta"]) if row["request_meta"] else None,
            created_by=UUID(row["created_by"]) if row["created_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create(self, job: AdAccountSyncJobOut) -> AdAccountSyncJobOut:
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO ad_account_sync_jobs
                (id, ad_account_id, provider, status, started_at, finished_at, records_synced, error_message, error_code, error_category, retryable, attempt, next_retry_at, request_meta, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job.id),
                    str(job.ad_account_id),
                    job.provider,
                    job.status,
                    job.started_at.isoformat(),
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.records_synced,
                    job.error_message,
                    job.error_code,
                    job.error_category,
                    int(job.retryable),
                    job.attempt,
                    job.next_retry_at.isoformat() if job.next_retry_at else None,
                    json.dumps(job.request_meta, separators=(",", ":"), ensure_ascii=True) if job.request_meta else None,
                    str(job.created_by) if job.created_by else None,
                    job.created_at.isoformat(),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM ad_account_sync_jobs WHERE id=?", (str(job.id),)).fetchone()
        return self._to_job(row)

    def list(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]:
        where = ["1=1"]
        params: List[object] = []
        if account_id:
            where.append("ad_account_id=?")
            params.append(str(account_id))
        if status and status != "all":
            where.append("status=?")
            params.append(status)
        params.append(max(1, min(limit, 500)))
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM ad_account_sync_jobs WHERE {' AND '.join(where)} ORDER BY started_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._to_job(r) for r in rows]

    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]:
        if not account_ids:
            return {}
        out: Dict[UUID, AdAccountSyncJobOut] = {}
        with sqlite_conn(self.db_path) as conn:
            for account_id in account_ids:
                row = conn.execute(
                    "SELECT * FROM ad_account_sync_jobs WHERE ad_account_id=? ORDER BY started_at DESC LIMIT 1",
                    (str(account_id),),
                ).fetchone()
                if row:
                    out[account_id] = self._to_job(row)
        return out

    def acquire_lease(self, *, lease_key: str, now: datetime, ttl_seconds: int) -> Optional[str]:
        lease_token = str(uuid4())
        lease_until = now + timedelta(seconds=max(30, int(ttl_seconds)))
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT lease_until FROM ad_account_sync_leases WHERE lease_key=?",
                (lease_key,),
            ).fetchone()
            if row and row["lease_until"]:
                try:
                    active_until = datetime.fromisoformat(row["lease_until"])
                except Exception:
                    active_until = now
                if active_until > now:
                    conn.rollback()
                    return None
            conn.execute(
                """
                INSERT INTO ad_account_sync_leases (lease_key, lease_token, lease_until, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lease_key) DO UPDATE SET
                  lease_token=excluded.lease_token,
                  lease_until=excluded.lease_until,
                  updated_at=excluded.updated_at
                """,
                (lease_key, lease_token, lease_until.isoformat(), now.isoformat()),
            )
            conn.commit()
        return lease_token

    def release_lease(self, *, lease_key: str, lease_token: str) -> None:
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                "DELETE FROM ad_account_sync_leases WHERE lease_key=? AND lease_token=?",
                (lease_key, lease_token),
            )
            conn.commit()

    def renew_lease(self, *, lease_key: str, lease_token: str, now: datetime, ttl_seconds: int) -> bool:
        lease_until = now + timedelta(seconds=max(30, int(ttl_seconds)))
        with sqlite_conn(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE ad_account_sync_leases
                SET lease_until=?, updated_at=?
                WHERE lease_key=? AND lease_token=?
                """,
                (lease_until.isoformat(), now.isoformat(), lease_key, lease_token),
            )
            conn.commit()
        return int(cursor.rowcount or 0) == 1


class InMemoryAdAccountSyncJobStore:
    def __init__(self):
        self.items: Dict[UUID, AdAccountSyncJobOut] = {}
        self.leases: Dict[str, tuple[str, datetime]] = {}
        self._lease_lock = threading.Lock()

    def create(self, job: AdAccountSyncJobOut) -> AdAccountSyncJobOut:
        self.items[job.id] = job
        return job

    def list(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]:
        rows = list(self.items.values())
        if account_id:
            rows = [r for r in rows if r.ad_account_id == account_id]
        if status and status != "all":
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda x: x.started_at, reverse=True)
        return rows[: max(1, min(limit, 500))]

    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]:
        result: Dict[UUID, AdAccountSyncJobOut] = {}
        rows = sorted(self.items.values(), key=lambda x: x.started_at, reverse=True)
        wanted = set(account_ids)
        for row in rows:
            if row.ad_account_id in wanted and row.ad_account_id not in result:
                result[row.ad_account_id] = row
        return result

    def acquire_lease(self, *, lease_key: str, now: datetime, ttl_seconds: int) -> Optional[str]:
        with self._lease_lock:
            existing = self.leases.get(lease_key)
            if existing and existing[1] > now:
                return None
            lease_token = str(uuid4())
            self.leases[lease_key] = (lease_token, now + timedelta(seconds=max(30, int(ttl_seconds))))
            return lease_token

    def release_lease(self, *, lease_key: str, lease_token: str) -> None:
        with self._lease_lock:
            existing = self.leases.get(lease_key)
            if existing and existing[0] == lease_token:
                self.leases.pop(lease_key, None)

    def renew_lease(self, *, lease_key: str, lease_token: str, now: datetime, ttl_seconds: int) -> bool:
        with self._lease_lock:
            existing = self.leases.get(lease_key)
            if not existing or existing[0] != lease_token:
                return False
            self.leases[lease_key] = (
                lease_token,
                now + timedelta(seconds=max(30, int(ttl_seconds))),
            )
            return True


@dataclass
class SyncRunResult:
    requested: int
    processed: int
    skipped: int
    success: int
    failed: int
    retry_scheduled: int
    jobs: List[AdAccountSyncJobOut]
    started_at: datetime
    finished_at: datetime


@dataclass
class DueSyncRunResult:
    lease_acquired: bool
    selected_account_ids: List[UUID]
    sync_result: Optional[SyncRunResult]
    started_at: datetime
    finished_at: datetime


class ProviderPayloadValidationError(ValueError):
    """Raised when provider metrics cannot be safely assigned to the requested window."""


class MissingScopedCredentialsError(RuntimeError):
    """A tenant-scoped sync has no tenant-owned provider credentials."""


class AdAccountSyncService:
    DEFAULT_INITIAL_LOOKBACK_DAYS = 180
    PROVIDER_DATE_FIELDS = ("date", "date_start", "day", "stat_time_day")
    ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def _eligible_active_accounts(self) -> List[AdAccountOut]:
        """Return active accounts whose parent client is also active.

        Client archival is intentionally reversible, so child rows stay intact.
        They must not, however, continue consuming provider quota or appearing in
        scheduled work while their parent client is archived/inactive.
        """
        accounts = self.account_store.list(status="active")
        client_store = getattr(self.account_store, "client_store", None)
        if client_store is None:
            eligible = accounts
        else:
            active_client_ids = {client.id for client in client_store.list(status="active")}
            eligible = [account for account in accounts if account.client_id in active_client_ids]
        conflict_ids = active_assignment_conflict_ids(self.account_store)
        return [account for account in eligible if account.id not in conflict_ids]

    def __init__(
        self,
        account_store: AdAccountStore,
        job_store: AdAccountSyncJobStore,
        ad_stats_store: AdStatsStore,
        *,
        provider_fetchers: Optional[Dict[str, Callable[..., List[Dict[str, object]]]]] = None,
        credential_resolver: Optional[Callable[[str, UUID, Optional[UUID]], Optional[Dict[str, object]]]] = None,
        credential_candidates_resolver: Optional[Callable[[str, UUID, Optional[UUID]], List[Dict[str, object]]]] = None,
    ):
        self.account_store = account_store
        self.job_store = job_store
        self.ad_stats_store = ad_stats_store
        self.credential_resolver = credential_resolver
        self.credential_candidates_resolver = credential_candidates_resolver
        try:
            self.initial_lookback_days = max(
                1,
                int(str(os.getenv("AD_SYNC_INITIAL_LOOKBACK_DAYS", self.DEFAULT_INITIAL_LOOKBACK_DAYS))),
            )
        except Exception:
            self.initial_lookback_days = self.DEFAULT_INITIAL_LOOKBACK_DAYS
        try:
            self.incremental_overlap_days = max(
                1,
                min(30, int(str(os.getenv("AD_SYNC_INCREMENTAL_OVERLAP_DAYS", "3")))),
            )
        except Exception:
            self.incremental_overlap_days = 3
        self.provider_fetchers = provider_fetchers or {
            "meta": self._fetch_meta_daily,
            "google": self._fetch_google_daily,
            "tiktok": self._fetch_tiktok_daily,
        }

    @classmethod
    def _validated_provider_rows(
        cls,
        rows: List[Dict[str, object]],
        *,
        requested_from: str,
        requested_to: str,
    ) -> List[Dict[str, object]]:
        """Validate every row before ingesting any of them.

        Provider payloads are untrusted. In particular, assigning a missing or
        malformed date to the request boundary fabricates fresh metrics. Accept
        only an exact ISO calendar date from a known provider field and require
        it to be inside the requested inclusive range.
        """
        range_from = date.fromisoformat(requested_from)
        range_to = date.fromisoformat(requested_to)
        validated: List[Dict[str, object]] = []

        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ProviderPayloadValidationError(
                    f"Provider payload row {index} is not an object"
                )

            parsed_fields: List[tuple[str, date]] = []
            for field in cls.PROVIDER_DATE_FIELDS:
                raw_value = row.get(field)
                if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
                    continue
                if not isinstance(raw_value, str):
                    raise ProviderPayloadValidationError(
                        f"Provider payload row {index} has a non-string {field}"
                    )
                value = raw_value.strip()
                if not cls.ISO_DATE_PATTERN.fullmatch(value):
                    raise ProviderPayloadValidationError(
                        f"Provider payload row {index} has invalid ISO date in {field}"
                    )
                try:
                    parsed_value = date.fromisoformat(value)
                except ValueError as exc:
                    raise ProviderPayloadValidationError(
                        f"Provider payload row {index} has invalid calendar date in {field}"
                    ) from exc
                parsed_fields.append((field, parsed_value))

            if not parsed_fields:
                raise ProviderPayloadValidationError(
                    f"Provider payload row {index} is missing a date"
                )

            row_date = parsed_fields[0][1]
            if any(candidate != row_date for _, candidate in parsed_fields[1:]):
                raise ProviderPayloadValidationError(
                    f"Provider payload row {index} contains conflicting dates"
                )
            if row_date < range_from or row_date > range_to:
                raise ProviderPayloadValidationError(
                    f"Provider payload row {index} date is outside the requested range"
                )

            normalized = dict(row)
            normalized["date"] = row_date.isoformat()
            validated.append(normalized)

        return validated

    @staticmethod
    def _to_int(value: object) -> int:
        try:
            return int(float(str(value or 0)))
        except Exception:
            return 0

    @staticmethod
    def _to_float(value: object) -> float:
        try:
            return float(str(value or 0))
        except Exception:
            return 0.0

    @staticmethod
    def _to_money_2(value: object) -> float:
        try:
            return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return 0.0

    def _ingest_provider_rows(
        self,
        *,
        account_id: UUID,
        platform: str,
        rows: List[Dict[str, object]],
    ) -> int:
        if not rows:
            return 0
        payload = AdStatsIngestRequest(
            rows=[
                AdStatWrite(
                    ad_account_id=account_id,
                    date=str(row["date"]),
                    platform=platform,
                    impressions=self._to_int(row.get("impressions")),
                    clicks=self._to_int(row.get("clicks")),
                    spend=self._to_money_2(row.get("spend")),
                    conversions=self._to_money_2(row.get("conversions")) if row.get("conversions") is not None else None,
                )
                for row in rows
            ]
        )
        result = self.ad_stats_store.ingest(payload)
        return int(result.get("total") or 0)

    @staticmethod
    def _to_error_message(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("detail") or detail)
            return str(detail)
        return str(exc)

    @staticmethod
    def _classify_error(exc: Exception) -> tuple[str, str, bool]:
        if isinstance(exc, ProviderPayloadValidationError):
            return ("provider_payload_invalid", "validation", False)
        if isinstance(exc, MissingScopedCredentialsError):
            return ("provider_credentials_missing", "configuration", False)
        if isinstance(exc, MetaCredentialReconnectRequiredError):
            return ("provider_reconnect_required", "auth", False)
        raw = AdAccountSyncService._to_error_message(exc).lower()
        if isinstance(exc, HTTPException):
            status = int(exc.status_code or 0)
            if status in {401, 403}:
                return ("auth_failed", "auth", False)
            if status in {429}:
                return ("rate_limited", "rate_limit", True)
            if status in {500, 502, 503, 504}:
                return ("provider_unavailable", "provider", True)
            if status in {400, 404, 422}:
                return ("invalid_request", "validation", False)
        if "requested_metrics_for_manager" in raw or "metrics cannot be requested for a manager account" in raw:
            return ("invalid_request", "validation", False)
        if "unauthorized" in raw or "forbidden" in raw or "scope" in raw or "permission" in raw or "token" in raw:
            return ("auth_failed", "auth", False)
        if "user_permission_denied" in raw or "customer_not_enabled" in raw:
            return ("auth_failed", "auth", False)
        if any(
            needle in raw
            for needle in (
                "rate limit",
                "rate-limited",
                "rate_limited",
                "quota",
                "throttl",
                "too many requests",
                "resource exhausted",
            )
        ):
            return ("rate_limited", "rate_limit", True)
        if re.search(r"\b(timeout|temporar(?:y|ily)?|unavailable|connection|gateway)\b", raw):
            return ("provider_unavailable", "provider", True)
        if "invalid" in raw or "bad request" in raw or "missing" in raw:
            return ("invalid_request", "validation", False)
        return ("unknown_error", "unknown", False)

    @staticmethod
    def _provider_credentials(candidate: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
        if candidate is None:
            return None
        return {key: value for key, value in candidate.items() if not key.startswith("__")}

    @staticmethod
    def _next_retry_at(*, now: datetime, attempt: int) -> datetime:
        # Exponential backoff: 1m,2m,4m,8m... capped at 60m.
        delay_minutes = min(60, max(1, 2 ** max(0, attempt - 1)))
        return now + timedelta(minutes=delay_minutes)

    @staticmethod
    def _parse_last_sync_date(raw: object) -> Optional[date]:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw.date()
        value = str(raw).strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except Exception:
            return None

    def _resolve_date_range_for_account(
        self,
        *,
        account,
        explicit_from: Optional[date],
        explicit_to: date,
    ) -> tuple[str, str]:
        meta = account.metadata or {}
        backfill_completed_at = self._parse_last_sync_date(meta.get("history_backfill_completed_at"))
        if explicit_from:
            from_date = explicit_from
        else:
            if backfill_completed_at:
                latest_data_date = self._parse_last_sync_date(meta.get("latest_data_date"))
                if not latest_data_date:
                    latest_data_date = self._parse_last_sync_date(meta.get("last_data_at"))
                if not latest_data_date:
                    # Legacy fallback only. New writers persist the exact newest
                    # metric date separately from the request heartbeat.
                    latest_data_date = self._parse_last_sync_date(getattr(account, "last_sync_at", None))
                if not latest_data_date:
                    latest_data_date = self._parse_last_sync_date(meta.get("last_sync_at"))
                if latest_data_date:
                    from_date = latest_data_date - timedelta(days=self.incremental_overlap_days - 1)
                else:
                    from_date = explicit_to - timedelta(days=self.initial_lookback_days - 1)
            else:
                # One-time historical backfill for account, then incremental mode.
                from_date = explicit_to - timedelta(days=self.initial_lookback_days - 1)
        if from_date > explicit_to:
            from_date = explicit_to
        return from_date.isoformat(), explicit_to.isoformat()

    @staticmethod
    def _fetch_meta_daily(
        external_id: str,
        date_from: str,
        date_to: str,
        credentials: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, object]]:
        return meta.fetch_daily(external_id, meta_safe_date_from(date_from), date_to, credentials)

    @staticmethod
    def _fetch_google_daily(
        external_id: str,
        date_from: str,
        date_to: str,
        credentials: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, object]]:
        customer_id = google_ads.valid_customer_id_or_none(external_id)
        if not customer_id:
            raise HTTPException(status_code=400, detail="Invalid Google customer id")
        return google_ads.fetch_daily(customer_id, date_from, date_to, credentials)

    @staticmethod
    def _fetch_tiktok_daily(
        external_id: str,
        date_from: str,
        date_to: str,
        credentials: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, object]]:
        advertiser_id = tiktok.normalize_advertiser_id(external_id)
        return tiktok.fetch_daily(advertiser_id, date_from, date_to, credentials)

    def run_sync(
        self,
        *,
        account_ids: Optional[List[UUID]] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        created_by: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        force: bool = False,
    ) -> SyncRunResult:
        started_at = _utcnow()
        sync_to = date_to or started_at.date()

        accounts = self._eligible_active_accounts()
        if account_ids is not None:
            wanted = set(account_ids)
            accounts = [a for a in accounts if a.id in wanted]
        if platform:
            accounts = [a for a in accounts if a.platform == platform]

        jobs: List[AdAccountSyncJobOut] = []
        success = 0
        failed = 0
        skipped = 0
        retry_scheduled = 0
        latest = self.job_store.latest_by_account_ids([a.id for a in accounts])

        for account in accounts:
            s_at = _utcnow()
            provider = str(account.platform or "").lower().strip()
            account_meta = account.metadata or {}
            is_initial_backfill = (
                date_from is None
                and self._parse_last_sync_date(account_meta.get("history_backfill_completed_at")) is None
            )
            from_str, to_str = self._resolve_date_range_for_account(
                account=account,
                explicit_from=date_from,
                explicit_to=sync_to,
            )
            fetcher = self.provider_fetchers.get(provider)
            prev = latest.get(account.id)
            if (
                not force
                and prev
                and prev.status == "error"
                and prev.retryable
                and prev.next_retry_at
                and s_at < prev.next_retry_at
            ):
                skipped += 1
                continue
            attempt = 1
            if prev and prev.status == "error" and prev.retryable:
                attempt = int(prev.attempt or 1) + 1
            used_meta_diagnostic: Optional[MetaCredentialDiagnostic] = None
            meta_diagnostic_code: Optional[str] = None
            if not fetcher:
                err = f"Provider not supported: {provider}"
                status = "error"
                records = 0
                error_message = err
                error_code = "provider_not_supported"
                error_category = "configuration"
                retryable = False
                next_retry_at = None
                used_credential_id: Optional[str] = None
                provider_rows_count = 0
                latest_data_date: Optional[str] = None
            else:
                used_credential_id = None
                provider_rows_count = 0
                latest_data_date = None
                try:
                    if provider == "meta" and bool(account_meta.get("meta_rediscovery_required")):
                        raise MetaCredentialReconnectRequiredError(
                            "meta_rediscovery_required",
                            "This Meta ad account moved to another client. Rediscover it before syncing.",
                        )
                    credential_candidates: List[Optional[Dict[str, object]]] = []
                    if self.credential_candidates_resolver:
                        credential_candidates = self.credential_candidates_resolver(provider, account.client_id, user_id) or []
                    elif self.credential_resolver:
                        single = self.credential_resolver(provider, account.client_id, user_id)
                        if single is not None:
                            credential_candidates = [single]
                    if user_id is not None:
                        credential_candidates = [
                            candidate for candidate in credential_candidates if candidate is not None
                        ]
                    if not credential_candidates:
                        if user_id is None:
                            # Admin/scheduler runs may intentionally use the
                            # platform-owned legacy environment credential path.
                            credential_candidates = [None]
                        else:
                            raise MissingScopedCredentialsError(
                                "Provider credentials are missing or incomplete."
                            )

                    preferred_credential_id = str((account.metadata or {}).get("integration_credential_id") or "").strip()
                    if provider == "meta" and preferred_credential_id:
                        exact_candidates = [
                            candidate
                            for candidate in credential_candidates
                            if str((candidate or {}).get("__credential_id") or "") == preferred_credential_id
                        ]
                        if not exact_candidates:
                            raise MetaCredentialReconnectRequiredError(
                                "meta_bound_credential_missing",
                                "The Meta connection assigned to this ad account is no longer active. Reconnect Meta Ads and rediscover the account.",
                            )
                        # Meta accounts imported through Business Login are bound
                        # to the exact credential that discovered them. Never
                        # silently fall through to another tenant's token.
                        credential_candidates = exact_candidates[:1]
                    elif preferred_credential_id:
                        preferred = [c for c in credential_candidates if str((c or {}).get("__credential_id") or "") == preferred_credential_id]
                        remaining = [c for c in credential_candidates if str((c or {}).get("__credential_id") or "") != preferred_credential_id]
                        credential_candidates = [*preferred, *remaining]
                    elif provider == "meta" and len(credential_candidates) > 1:
                        raise MetaCredentialReconnectRequiredError(
                            "meta_credential_selection_required",
                            "This Meta ad account is not assigned to one connection. Rediscover the account before syncing.",
                        )

                    used_credential_id: Optional[str] = None
                    last_exc: Optional[Exception] = None
                    rows: List[Dict[str, object]] = []
                    for candidate in credential_candidates:
                        used_credential_id = str((candidate or {}).get("__credential_id") or "").strip() or None
                        provider_credentials = self._provider_credentials(candidate)
                        try:
                            if provider == "meta" and provider_credentials:
                                used_meta_diagnostic = require_usable_validated_meta_credentials(
                                    provider_credentials,
                                    allow_unverified_legacy=True,
                                )
                            try:
                                rows = fetcher(account.external_account_id, from_str, to_str, provider_credentials)
                            except TypeError:
                                # Backward-compatible path for tests/custom fetchers with legacy signature.
                                rows = fetcher(account.external_account_id, from_str, to_str)
                            last_exc = None
                            break
                        except Exception as exc:
                            last_exc = exc
                            continue
                    if last_exc is not None:
                        raise last_exc
                    rows = list(rows or [])
                    provider_rows_count = len(rows)
                    rows = self._validated_provider_rows(
                        rows,
                        requested_from=from_str,
                        requested_to=to_str,
                    )
                    if rows:
                        latest_data_date = max(str(row["date"]) for row in rows)
                    status = "success"
                    records = self._ingest_provider_rows(
                        account_id=account.id,
                        platform=provider,
                        rows=rows,
                    )
                    error_message = None
                    error_code = None
                    error_category = None
                    retryable = False
                    next_retry_at = None
                    attempt = 1
                except Exception as exc:
                    status = "error"
                    records = 0
                    error_message = self._to_error_message(exc)
                    error_code, error_category, retryable = self._classify_error(exc)
                    if isinstance(exc, MetaCredentialReconnectRequiredError):
                        meta_diagnostic_code = exc.code
                    next_retry_at = self._next_retry_at(now=s_at, attempt=attempt) if retryable else None

            f_at = _utcnow()
            job = self.job_store.create(
                AdAccountSyncJobOut(
                    id=uuid4(),
                    ad_account_id=account.id,
                    provider=provider,
                    status=status,
                    started_at=s_at,
                    finished_at=f_at,
                    records_synced=records,
                    error_message=error_message,
                    error_code=error_code,
                    error_category=error_category,
                    retryable=retryable,
                    attempt=attempt,
                    next_retry_at=next_retry_at,
                    request_meta={
                        "date_from": from_str,
                        "date_to": to_str,
                        "provider_rows_received": provider_rows_count,
                        "empty_response": status == "success" and provider_rows_count == 0,
                        "latest_data_date": latest_data_date,
                    },
                    created_by=created_by,
                    created_at=f_at,
                )
            )
            jobs.append(job)

            next_meta = dict(account.metadata or {})
            next_meta["last_sync_attempt_at"] = s_at.isoformat()
            next_meta["sync_status"] = status
            next_meta["sync_error"] = error_message
            next_meta["sync_error_code"] = error_code
            next_meta["sync_error_category"] = error_category
            next_meta["sync_retryable"] = retryable
            next_meta["sync_next_retry_at"] = next_retry_at.isoformat() if next_retry_at else None
            next_meta["sync_attempt"] = attempt
            next_meta["last_sync_job_id"] = str(job.id)
            next_meta["sync_data_status"] = (
                "received" if status == "success" and provider_rows_count > 0 else "empty" if status == "success" else "error"
            )
            if provider == "meta" and used_meta_diagnostic is not None:
                next_meta["meta_connection_status"] = used_meta_diagnostic.status
                next_meta["meta_connection_diagnostic_code"] = used_meta_diagnostic.code
            elif provider == "meta" and error_code == "provider_reconnect_required":
                next_meta["meta_connection_status"] = "error"
                next_meta["meta_connection_diagnostic_code"] = (
                    meta_diagnostic_code or "meta_reconnect_required"
                )
            if status == "success":
                next_meta["last_sync_success_at"] = f_at.isoformat()
                if provider_rows_count > 0:
                    # Keep the legacy last_sync_at field, but only advance it when the
                    # provider actually returned metric rows. Empty API responses no
                    # longer make account data look fresh.
                    next_meta["last_sync_at"] = f_at.isoformat()
                    next_meta["last_data_at"] = latest_data_date
                    next_meta["latest_data_date"] = latest_data_date
                if used_credential_id:
                    next_meta["integration_credential_id"] = used_credential_id
                if (
                    provider_rows_count > 0
                    and is_initial_backfill
                    and not next_meta.get("history_backfill_completed_at")
                ):
                    next_meta["history_backfill_completed_at"] = f_at.isoformat()
                    next_meta["history_backfill_window_days"] = self.initial_lookback_days
            self.account_store.patch(account.id, AdAccountPatch(metadata=next_meta))

            if status == "success":
                success += 1
            else:
                failed += 1
                if retryable and next_retry_at:
                    retry_scheduled += 1

        finished_at = _utcnow()
        return SyncRunResult(
            requested=len(account_ids) if account_ids is not None else len(accounts),
            processed=len(jobs),
            skipped=skipped,
            success=success,
            failed=failed,
            retry_scheduled=retry_scheduled,
            jobs=jobs,
            started_at=started_at,
            finished_at=finished_at,
        )

    def run_sync_exclusive(
        self,
        *,
        account_ids: Optional[List[UUID]] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        created_by: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        force: bool = False,
    ) -> SyncRunResult:
        """Serialize all automatic, login and manual provider sync runs.

        A renewable database lease prevents duplicate provider load and SQLite
        races across Render instances. The heartbeat keeps long batches from
        outliving a fixed lease TTL.
        """
        started_at = _utcnow()
        try:
            lease_seconds = max(60, int(os.getenv("AD_SYNC_RUN_LEASE_SECONDS", "1800")))
        except (TypeError, ValueError):
            lease_seconds = 1800
        lease_key = "ad-account-sync-run"
        lease_token = self.job_store.acquire_lease(
            lease_key=lease_key,
            now=started_at,
            ttl_seconds=lease_seconds,
        )
        if not lease_token:
            requested = len(account_ids or [])
            return SyncRunResult(
                requested=requested,
                processed=0,
                skipped=requested,
                success=0,
                failed=0,
                retry_scheduled=0,
                jobs=[],
                started_at=started_at,
                finished_at=_utcnow(),
            )

        stop_heartbeat = threading.Event()

        def renew_while_running() -> None:
            interval = max(10, lease_seconds // 3)
            while not stop_heartbeat.wait(interval):
                renewed = self.job_store.renew_lease(
                    lease_key=lease_key,
                    lease_token=lease_token,
                    now=_utcnow(),
                    ttl_seconds=lease_seconds,
                )
                if not renewed:
                    return

        heartbeat = threading.Thread(
            target=renew_while_running,
            name="ad-account-sync-lease-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            return self.run_sync(
                account_ids=account_ids,
                platform=platform,
                date_from=date_from,
                date_to=date_to,
                created_by=created_by,
                user_id=user_id,
                force=force,
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1)
            self.job_store.release_lease(lease_key=lease_key, lease_token=lease_token)

    def run_due_sync(
        self,
        *,
        batch_size: int = 10,
        stale_after_hours: int = 24,
        lease_seconds: int = 900,
    ) -> DueSyncRunResult:
        """Run one idempotent batch for active accounts that are actually due.

        Retryable errors are selected only after ``next_retry_at``. Non-retryable
        errors require a human/configuration fix and are deliberately not hammered
        by the scheduler. Successful accounts are selected again after the stale
        interval, and accounts with no job are selected for their initial sync.
        """

        started_at = _utcnow()
        lease_key = "ad-account-sync-due"
        lease_token = self.job_store.acquire_lease(
            lease_key=lease_key,
            now=started_at,
            ttl_seconds=max(30, int(lease_seconds)),
        )
        if not lease_token:
            return DueSyncRunResult(
                lease_acquired=False,
                selected_account_ids=[],
                sync_result=None,
                started_at=started_at,
                finished_at=_utcnow(),
            )

        try:
            accounts = self._eligible_active_accounts()
            latest = self.job_store.latest_by_account_ids([a.id for a in accounts])
            stale_cutoff = started_at - timedelta(hours=max(1, int(stale_after_hours)))
            due: List[tuple[int, datetime, object]] = []

            for account in accounts:
                job = latest.get(account.id)
                if not job:
                    due.append((1, datetime.min, account))
                    continue
                if job.status == "error":
                    if job.retryable and (not job.next_retry_at or job.next_retry_at <= started_at):
                        due.append((0, job.next_retry_at or job.started_at, account))
                    continue
                completed_at = job.finished_at or job.started_at
                if completed_at <= stale_cutoff:
                    due.append((2, completed_at, account))

            due.sort(key=lambda item: (item[0], item[1], str(item[2].id)))
            selected = [item[2] for item in due[: max(1, min(int(batch_size), 100))]]
            selected_ids = [account.id for account in selected]
            if not selected_ids:
                return DueSyncRunResult(
                    lease_acquired=True,
                    selected_account_ids=[],
                    sync_result=None,
                    started_at=started_at,
                    finished_at=_utcnow(),
                )

            sync_result = self.run_sync_exclusive(account_ids=selected_ids, force=False)
            return DueSyncRunResult(
                lease_acquired=True,
                selected_account_ids=selected_ids,
                sync_result=sync_result,
                started_at=started_at,
                finished_at=_utcnow(),
            )
        finally:
            self.job_store.release_lease(lease_key=lease_key, lease_token=lease_token)

    def list_jobs(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]:
        return self.job_store.list(account_id=account_id, status=status, limit=limit)

    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]:
        return self.job_store.latest_by_account_ids(account_ids)

