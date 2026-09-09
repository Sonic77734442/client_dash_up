from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.runtime_db import init_runtime_database, runtime_conn
from app.schemas import AdAccountOut, AdStatOut, AdStatsIngestRequest, AdStatWrite
from app.services.ad_accounts import AdAccountStore, active_assignment_conflict_ids
from app.services.metric_aggregation import aggregate_metric_rows


getcontext().prec = 28
MONEY_PLACES = Decimal("0.01")


def _q(v: Decimal) -> Decimal:
    return v.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _to_decimal(v) -> Decimal:
    return Decimal(str(v or 0))


def _validate_row_account(ad_account_store: AdAccountStore, row: AdStatWrite):
    account = ad_account_store.get(row.ad_account_id)
    if not account:
        raise HTTPException(status_code=400, detail=f"ad_account_id not found: {row.ad_account_id}")
    if account.platform.strip().lower() != row.platform:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "platform_mismatch",
                "message": "Ad stat platform must match ad account platform",
                "details": {
                    "ad_account_id": str(account.id),
                    "account_platform": account.platform,
                    "row_platform": row.platform,
                },
            },
        )
    return account


def _stale_after_days() -> int:
    try:
        return max(1, int(os.getenv("AD_STATS_STALE_AFTER_DAYS", "3")))
    except (TypeError, ValueError):
        return 3


def _data_quality(
    *,
    rows: List[AdStatOut],
    scope_account_ids: set[UUID],
    as_of_date: Optional[date],
) -> Dict[str, object]:
    effective_as_of = as_of_date or _utcnow().date()
    relevant_rows = [row for row in rows if row.ad_account_id in scope_account_ids]
    latest_by_account: Dict[UUID, date] = {}
    for row in relevant_rows:
        previous = latest_by_account.get(row.ad_account_id)
        if previous is None or row.date > previous:
            latest_by_account[row.ad_account_id] = row.date

    accounts_with_data = set(latest_by_account)
    # Keep the public field name for backwards compatibility. For an explicit
    # archived client/account scope this count represents the selected
    # historical accounts so row_count/coverage stay consistent with totals.
    active_accounts_count = len(scope_account_ids)
    accounts_with_data_count = len(accounts_with_data)
    rows_present = bool(relevant_rows)
    stale_after_days = _stale_after_days()
    stale_days_by_account = {
        account_id: max(0, (effective_as_of - latest_date).days)
        for account_id, latest_date in latest_by_account.items()
    }
    stale_accounts_count = sum(days > stale_after_days for days in stale_days_by_account.values())

    # For a multi-account scope the safe freshness watermark is the oldest
    # account-level latest date. A fresh account must not hide a stale peer.
    latest_data_date = min(latest_by_account.values(), default=None)
    stale_days = max(stale_days_by_account.values(), default=None)

    if not rows_present:
        status = "insufficient_data"
    elif active_accounts_count and accounts_with_data_count < active_accounts_count:
        status = "partial"
    elif stale_accounts_count:
        status = "stale"
    else:
        status = "fresh"

    coverage_percent = (
        round((accounts_with_data_count / active_accounts_count) * 100, 2)
        if active_accounts_count
        else (100.0 if rows_present else 0.0)
    )
    return {
        "status": status,
        "rows_present": rows_present,
        "row_count": len(relevant_rows),
        "latest_data_date": latest_data_date.isoformat() if latest_data_date else None,
        "stale_days": stale_days,
        "stale_after_days": stale_after_days,
        "stale_accounts_count": stale_accounts_count,
        "active_accounts_count": active_accounts_count,
        "accounts_with_data_count": accounts_with_data_count,
        "accounts_without_data_count": max(0, active_accounts_count - accounts_with_data_count),
        "coverage_percent": coverage_percent,
    }


def _active_parent_client_ids(ad_account_store: AdAccountStore) -> Optional[set[UUID]]:
    client_store = getattr(ad_account_store, "client_store", None)
    if client_store is None:
        return None
    return {client.id for client in client_store.list(status="active")}


def _is_explicit_historical_scope(
    ad_account_store: AdAccountStore,
    *,
    client_id: Optional[UUID],
    account_id: Optional[UUID],
) -> bool:
    """An explicitly selected archived entity is a historical read.

    A client_id by itself is still an operational/current view while that
    client is active, so archived child accounts cannot leak into its totals.
    """
    client_store = getattr(ad_account_store, "client_store", None)
    account = ad_account_store.get(account_id) if account_id else None
    if account is not None and account.status != "active":
        return True

    effective_client_id = account.client_id if account is not None else client_id
    if effective_client_id is not None and client_store is not None:
        parent = client_store.get(effective_client_id)
        return parent is not None and parent.status != "active"
    return False


def _accounts_for_scope(
    ad_account_store: AdAccountStore,
    *,
    client_id: Optional[UUID],
    account_id: Optional[UUID],
    platform: Optional[str],
) -> List[AdAccountOut]:
    historical = _is_explicit_historical_scope(
        ad_account_store,
        client_id=client_id,
        account_id=account_id,
    )
    accounts = ad_account_store.list(status="all" if historical else "active")
    if not historical:
        active_parent_ids = _active_parent_client_ids(ad_account_store)
        if active_parent_ids is not None:
            accounts = [account for account in accounts if account.client_id in active_parent_ids]
    if client_id:
        accounts = [account for account in accounts if account.client_id == client_id]
    if account_id:
        accounts = [account for account in accounts if account.id == account_id]
    if platform:
        accounts = [account for account in accounts if account.platform == platform]

    conflict_ids = active_assignment_conflict_ids(ad_account_store)
    return [account for account in accounts if account.id not in conflict_ids]


def _empty_scope_currency(
    ad_account_store: AdAccountStore,
    scope_accounts: List[AdAccountOut],
    client_id: Optional[UUID],
) -> Optional[str]:
    currencies = {account.currency for account in scope_accounts}
    if len(currencies) == 1:
        return next(iter(currencies))
    if not scope_accounts and client_id:
        client_store = getattr(ad_account_store, "client_store", None)
        client = client_store.get(client_id) if client_store is not None else None
        if client is not None:
            return client.default_currency
    return None


class AdStatsStore(Protocol):
    def ingest(self, payload: AdStatsIngestRequest, *, idempotency_key: Optional[str] = None) -> Dict[str, object]: ...
    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[AdStatOut]: ...

    def aggregate(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: date,
        date_to: date,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, object]: ...


class SqliteAdStatsStore:
    def __init__(self, db_path: str, ad_account_store: AdAccountStore):
        self.db_path = db_path
        self.ad_account_store = ad_account_store
        init_runtime_database(db_path)

    @staticmethod
    def _to_stat(row) -> AdStatOut:
        return AdStatOut(
            id=UUID(row["id"]),
            ad_account_id=UUID(row["ad_account_id"]),
            date=date.fromisoformat(row["date"]),
            platform=row["platform"],
            impressions=int(row["impressions"] or 0),
            clicks=int(row["clicks"] or 0),
            spend=_q(_to_decimal(row["spend"])),
            conversions=_q(_to_decimal(row["conversions"])) if row["conversions"] is not None else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _payload_hash(payload: AdStatsIngestRequest) -> str:
        canonical_rows = sorted(
            [r.model_dump(mode="json") for r in payload.rows],
            key=lambda x: (x["ad_account_id"], x["date"], x["platform"]),
        )
        packed = json.dumps(canonical_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(packed.encode("utf-8")).hexdigest()

    def ingest(self, payload: AdStatsIngestRequest, *, idempotency_key: Optional[str] = None) -> Dict[str, object]:
        inserted = 0
        updated = 0
        now = _utcnow().isoformat()
        request_hash = self._payload_hash(payload)
        with runtime_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                prev = conn.execute(
                    "SELECT request_hash, response_json FROM ad_stats_ingest_idempotency WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if prev:
                    if prev["request_hash"] != request_hash:
                        raise HTTPException(status_code=409, detail="Idempotency key reuse with different payload")
                    payload_prev = json.loads(prev["response_json"])
                    idem_prev = payload_prev.get("idempotency") if isinstance(payload_prev, dict) else None
                    if isinstance(idem_prev, dict):
                        idem_prev["replayed"] = True
                    else:
                        payload_prev["idempotency"] = {"key": idempotency_key, "replayed": True}
                    return payload_prev
            for row in payload.rows:
                _validate_row_account(self.ad_account_store, row)

                stat_id = str(uuid4())
                existing = conn.execute(
                    "SELECT id FROM ad_stats WHERE ad_account_id=? AND date=? AND platform=?",
                    (str(row.ad_account_id), row.date.isoformat(), row.platform),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE ad_stats
                        SET impressions=?, clicks=?, spend=?, conversions=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            int(row.impressions or 0),
                            int(row.clicks or 0),
                            str(_q(_to_decimal(row.spend))),
                            str(_q(_to_decimal(row.conversions))) if row.conversions is not None else None,
                            now,
                            existing["id"],
                        ),
                    )
                    updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO ad_stats
                        (id, ad_account_id, date, platform, impressions, clicks, spend, conversions, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            stat_id,
                            str(row.ad_account_id),
                            row.date.isoformat(),
                            row.platform,
                            int(row.impressions or 0),
                            int(row.clicks or 0),
                            str(_q(_to_decimal(row.spend))),
                            str(_q(_to_decimal(row.conversions))) if row.conversions is not None else None,
                            now,
                            now,
                        ),
                    )
                    inserted += 1
            response = {"inserted": inserted, "updated": updated, "total": inserted + updated}
            if idempotency_key:
                response["idempotency"] = {"key": idempotency_key, "replayed": False}
                conn.execute(
                    """
                    INSERT INTO ad_stats_ingest_idempotency (idempotency_key, request_hash, response_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        request_hash,
                        json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                        now,
                    ),
                )
            conn.commit()
        return response

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[AdStatOut]:
        where = ["1=1"]
        params: List[object] = []

        if client_id:
            where.append("a.client_id=?")
            params.append(str(client_id))
        if account_id:
            where.append("s.ad_account_id=?")
            params.append(str(account_id))
        historical_scope = _is_explicit_historical_scope(
            self.ad_account_store,
            client_id=client_id,
            account_id=account_id,
        )
        if not historical_scope:
            # Operational views (global or an active client/account scope)
            # exclude archived/inactive mappings and parents. Selecting an
            # archived client/account explicitly remains a historical read.
            where.append("a.status='active'")
            where.append("EXISTS (SELECT 1 FROM clients c WHERE c.id=a.client_id AND c.status='active')")
        if platform:
            where.append("s.platform=?")
            params.append(platform)
        if date_from:
            where.append("s.date>=?")
            params.append(date_from.isoformat())
        if date_to:
            where.append("s.date<=?")
            params.append(date_to.isoformat())

        with runtime_conn(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT s.*
                FROM ad_stats s
                JOIN ad_accounts a ON a.id = s.ad_account_id
                WHERE {' AND '.join(where)}
                ORDER BY s.date DESC, s.updated_at DESC
                """,
                params,
            ).fetchall()
        conflict_ids = active_assignment_conflict_ids(self.ad_account_store)
        return [self._to_stat(r) for r in rows if UUID(r["ad_account_id"]) not in conflict_ids]

    def aggregate(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: date,
        date_to: date,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, object]:
        rows = self.list(
            client_id=client_id,
            account_id=account_id,
            platform=platform,
            date_from=date_from,
            date_to=date_to,
        )
        scope_accounts = _accounts_for_scope(
            self.ad_account_store,
            client_id=client_id,
            account_id=account_id,
            platform=platform,
        )

        account_map = {account.id: account for account in self.ad_account_store.list(status="all")}
        metric_rows = []
        for row in rows:
            account = account_map.get(row.ad_account_id)
            metric_rows.append({
                "account_id": str(row.ad_account_id),
                "client_id": str(account.client_id) if account else None,
                "name": account.name if account else None,
                "platform": row.platform,
                "currency": account.currency if account else None,
                "spend": row.spend,
                "impressions": row.impressions,
                "clicks": row.clicks,
                "conversions": row.conversions,
            })
        result = aggregate_metric_rows(
            metric_rows,
            empty_currency=_empty_scope_currency(self.ad_account_store, scope_accounts, client_id),
        )
        return {
            "data_quality": _data_quality(
                rows=rows,
                scope_account_ids={x.id for x in scope_accounts},
                as_of_date=as_of_date,
            ),
            **result,
        }


class InMemoryAdStatsStore:
    def __init__(self, ad_account_store: AdAccountStore):
        self.ad_account_store = ad_account_store
        self.items: Dict[str, AdStatOut] = {}
        self.idempotency: Dict[str, Dict[str, object]] = {}

    @staticmethod
    def _payload_hash(payload: AdStatsIngestRequest) -> str:
        canonical_rows = sorted(
            [r.model_dump(mode="json") for r in payload.rows],
            key=lambda x: (x["ad_account_id"], x["date"], x["platform"]),
        )
        packed = json.dumps(canonical_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(packed.encode("utf-8")).hexdigest()

    def ingest(self, payload: AdStatsIngestRequest, *, idempotency_key: Optional[str] = None) -> Dict[str, object]:
        req_hash = self._payload_hash(payload)
        if idempotency_key and idempotency_key in self.idempotency:
            prev = self.idempotency[idempotency_key]
            if prev["request_hash"] != req_hash:
                raise HTTPException(status_code=409, detail="Idempotency key reuse with different payload")
            replay = dict(prev["response"])
            replay["idempotency"] = {"key": idempotency_key, "replayed": True}
            return replay

        inserted = 0
        updated = 0
        for row in payload.rows:
            _validate_row_account(self.ad_account_store, row)
            key = f"{row.ad_account_id}:{row.date.isoformat()}:{row.platform}"
            now = _utcnow()
            if key in self.items:
                prev = self.items[key]
                self.items[key] = prev.model_copy(
                    update={
                        "impressions": int(row.impressions),
                        "clicks": int(row.clicks),
                        "spend": _q(_to_decimal(row.spend)),
                        "conversions": _q(_to_decimal(row.conversions)) if row.conversions is not None else None,
                        "updated_at": now,
                    }
                )
                updated += 1
            else:
                self.items[key] = AdStatOut(
                    id=uuid4(),
                    ad_account_id=row.ad_account_id,
                    date=row.date,
                    platform=row.platform,
                    impressions=int(row.impressions),
                    clicks=int(row.clicks),
                    spend=_q(_to_decimal(row.spend)),
                    conversions=_q(_to_decimal(row.conversions)) if row.conversions is not None else None,
                    created_at=now,
                    updated_at=now,
                )
                inserted += 1
        response = {"inserted": inserted, "updated": updated, "total": inserted + updated}
        if idempotency_key:
            response["idempotency"] = {"key": idempotency_key, "replayed": False}
            self.idempotency[idempotency_key] = {"request_hash": req_hash, "response": dict(response)}
        return response

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[AdStatOut]:
        rows = list(self.items.values())
        historical_scope = _is_explicit_historical_scope(
            self.ad_account_store,
            client_id=client_id,
            account_id=account_id,
        )
        if not historical_scope:
            active_parent_ids = _active_parent_client_ids(self.ad_account_store)
            eligible_account_ids = {
                account.id
                for account in self.ad_account_store.list(status="active")
                if active_parent_ids is None or account.client_id in active_parent_ids
            }
            rows = [row for row in rows if row.ad_account_id in eligible_account_ids]
        if client_id:
            rows = [x for x in rows if self.ad_account_store.get(x.ad_account_id) and self.ad_account_store.get(x.ad_account_id).client_id == client_id]
        if account_id:
            rows = [x for x in rows if x.ad_account_id == account_id]
        if platform:
            rows = [x for x in rows if x.platform == platform]
        if date_from:
            rows = [x for x in rows if x.date >= date_from]
        if date_to:
            rows = [x for x in rows if x.date <= date_to]
        rows.sort(key=lambda x: (x.date, x.updated_at), reverse=True)
        conflict_ids = active_assignment_conflict_ids(self.ad_account_store)
        return [row for row in rows if row.ad_account_id not in conflict_ids]

    def aggregate(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        platform: Optional[str] = None,
        date_from: date,
        date_to: date,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, object]:
        # Reuse SQL implementation logic by adapting rows view.
        # Keep behavior consistent with production store.
        rows = self.list(client_id=client_id, account_id=account_id, platform=platform, date_from=date_from, date_to=date_to)
        scope_accounts = _accounts_for_scope(
            self.ad_account_store,
            client_id=client_id,
            account_id=account_id,
            platform=platform,
        )

        account_map = {account.id: account for account in self.ad_account_store.list(status="all")}
        metric_rows = []
        for row in rows:
            account = account_map.get(row.ad_account_id)
            metric_rows.append({
                "account_id": str(row.ad_account_id),
                "client_id": str(account.client_id) if account else None,
                "name": account.name if account else None,
                "platform": row.platform,
                "currency": account.currency if account else None,
                "spend": row.spend,
                "impressions": row.impressions,
                "clicks": row.clicks,
                "conversions": row.conversions,
            })
        result = aggregate_metric_rows(
            metric_rows,
            empty_currency=_empty_scope_currency(self.ad_account_store, scope_accounts, client_id),
        )
        return {
            "data_quality": _data_quality(
                rows=rows,
                scope_account_ids={x.id for x in scope_accounts},
                as_of_date=as_of_date,
            ),
            **result,
        }

