from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import AdAccountPatch, AdAccountSyncJobOut
from app.services.ad_accounts import AdAccountStore
from app.services.date_utils import meta_safe_date_from
from app.services.providers import google_ads, meta, tiktok


class AdAccountSyncJobStore(Protocol):
    def create(self, job: AdAccountSyncJobOut) -> AdAccountSyncJobOut: ...
    def list(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]: ...
    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]: ...


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


class InMemoryAdAccountSyncJobStore:
    def __init__(self):
        self.items: Dict[UUID, AdAccountSyncJobOut] = {}

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


class AdAccountSyncService:
    def __init__(
        self,
        account_store: AdAccountStore,
        job_store: AdAccountSyncJobStore,
        *,
        provider_fetchers: Optional[Dict[str, Callable[[str, str, str], List[Dict[str, object]]]]] = None,
    ):
        self.account_store = account_store
        self.job_store = job_store
        self.provider_fetchers = provider_fetchers or {
            "meta": self._fetch_meta_daily,
            "google": self._fetch_google_daily,
            "tiktok": self._fetch_tiktok_daily,
        }

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
        if "unauthorized" in raw or "forbidden" in raw or "scope" in raw or "permission" in raw or "token" in raw:
            return ("auth_failed", "auth", False)
        if "rate" in raw or "quota" in raw or "throttl" in raw:
            return ("rate_limited", "rate_limit", True)
        if "timeout" in raw or "temporar" in raw or "unavailable" in raw or "connection" in raw or "gateway" in raw:
            return ("provider_unavailable", "provider", True)
        if "invalid" in raw or "bad request" in raw or "missing" in raw:
            return ("invalid_request", "validation", False)
        return ("unknown_error", "unknown", False)

    @staticmethod
    def _next_retry_at(*, now: datetime, attempt: int) -> datetime:
        # Exponential backoff: 1m,2m,4m,8m... capped at 60m.
        delay_minutes = min(60, max(1, 2 ** max(0, attempt - 1)))
        return now + timedelta(minutes=delay_minutes)

    @staticmethod
    def _fetch_meta_daily(external_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
        return meta.fetch_daily(external_id, meta_safe_date_from(date_from), date_to)

    @staticmethod
    def _fetch_google_daily(external_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
        customer_id = google_ads.valid_customer_id_or_none(external_id)
        if not customer_id:
            raise HTTPException(status_code=400, detail="Invalid Google customer id")
        return google_ads.fetch_daily(customer_id, date_from, date_to)

    @staticmethod
    def _fetch_tiktok_daily(external_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
        advertiser_id = tiktok.normalize_advertiser_id(external_id)
        return tiktok.fetch_daily(advertiser_id, date_from, date_to)

    def run_sync(
        self,
        *,
        account_ids: Optional[List[UUID]] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        created_by: Optional[UUID] = None,
        force: bool = False,
    ) -> SyncRunResult:
        started_at = datetime.utcnow()
        from_str = (date_from or started_at.date()).isoformat()
        to_str = (date_to or started_at.date()).isoformat()

        accounts = self.account_store.list(status="all")
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
            s_at = datetime.utcnow()
            provider = str(account.platform or "").lower().strip()
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
            if not fetcher:
                err = f"Provider not supported: {provider}"
                status = "error"
                records = 0
                error_message = err
                error_code = "provider_not_supported"
                error_category = "configuration"
                retryable = False
                next_retry_at = None
            else:
                try:
                    rows = fetcher(account.external_account_id, from_str, to_str)
                    status = "success"
                    records = len(rows)
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
                    next_retry_at = self._next_retry_at(now=s_at, attempt=attempt) if retryable else None

            f_at = datetime.utcnow()
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
                    request_meta={"date_from": from_str, "date_to": to_str},
                    created_by=created_by,
                    created_at=f_at,
                )
            )
            jobs.append(job)

            next_meta = dict(account.metadata or {})
            next_meta["last_sync_at"] = f_at.isoformat()
            next_meta["sync_status"] = status
            next_meta["sync_error"] = error_message
            next_meta["sync_error_code"] = error_code
            next_meta["sync_error_category"] = error_category
            next_meta["sync_retryable"] = retryable
            next_meta["sync_next_retry_at"] = next_retry_at.isoformat() if next_retry_at else None
            next_meta["sync_attempt"] = attempt
            next_meta["last_sync_job_id"] = str(job.id)
            self.account_store.patch(account.id, AdAccountPatch(metadata=next_meta))

            if status == "success":
                success += 1
            else:
                failed += 1
                if retryable and next_retry_at:
                    retry_scheduled += 1

        finished_at = datetime.utcnow()
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

    def list_jobs(self, *, account_id: Optional[UUID] = None, status: Optional[str] = None, limit: int = 50) -> List[AdAccountSyncJobOut]:
        return self.job_store.list(account_id=account_id, status=status, limit=limit)

    def latest_by_account_ids(self, account_ids: List[UUID]) -> Dict[UUID, AdAccountSyncJobOut]:
        return self.job_store.latest_by_account_ids(account_ids)
