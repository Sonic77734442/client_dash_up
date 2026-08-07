from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import RLock
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException

from app.db import sqlite_conn
from app.schemas import (
    AssignmentConflictCandidateOut,
    AssignmentConflictGroupOut,
    AssignmentConflictGroupSummaryOut,
    AssignmentConflictLatestStatOut,
    AssignmentConflictListResponse,
    AssignmentConflictListSummaryOut,
    AssignmentConflictResolveRequest,
    AssignmentConflictResolveResponse,
    BudgetHistoryOut,
)
from app.services.ad_accounts import (
    AdAccountStore,
    InMemoryAdAccountStore,
    SqliteAdAccountStore,
    canonical_external_account_id,
    normalize_account_platform,
)
from app.services.ad_stats import AdStatsStore, InMemoryAdStatsStore
from app.services.audit_log import AuditLogStore, InMemoryAuditLogStore, SqliteAuditLogStore
from app.services.budgets import BudgetStore, InMemoryBudgetStore, SqliteBudgetStore
from app.services.clients import ClientStore
from app.services.platform_admin import InMemoryPlatformAdminStore, PlatformAdminStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _error(status_code: int, code: str, message: str, details: Optional[dict] = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "details": details or {}},
    )


@dataclass(frozen=True)
class _AccountSnapshot:
    id: UUID
    client_id: UUID
    client_name: str
    client_status: str
    name: str
    status: str
    platform: str
    external_account_id: str
    currency: str
    updated_at: datetime


@dataclass(frozen=True)
class _BudgetSnapshot:
    id: UUID
    account_id: UUID
    status: str
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class _StatSnapshot:
    account_id: UUID
    date: date
    spend: float
    impressions: int
    clicks: int
    conversions: float
    updated_at: datetime


Identity = Tuple[str, str]


class AssignmentConflictService:
    """List and atomically resolve legacy ambiguous ad-account assignments."""

    def __init__(
        self,
        *,
        account_store: AdAccountStore,
        client_store: ClientStore,
        ad_stats_store: AdStatsStore,
        budget_store: BudgetStore,
        platform_admin_store: PlatformAdminStore,
        audit_log_store: AuditLogStore,
    ):
        self.account_store = account_store
        self.client_store = client_store
        self.ad_stats_store = ad_stats_store
        self.budget_store = budget_store
        self.platform_admin_store = platform_admin_store
        self.audit_log_store = audit_log_store
        self._lock = RLock()

    @staticmethod
    def _identity(account: _AccountSnapshot) -> Optional[Identity]:
        platform = normalize_account_platform(account.platform)
        external_id = canonical_external_account_id(platform, account.external_account_id)
        if not platform or not external_id:
            return None
        return platform, external_id

    @staticmethod
    def _group_id(identity: Identity) -> str:
        digest = hashlib.sha256(f"{identity[0]}\0{identity[1]}".encode("utf-8")).hexdigest()
        return f"acg_{digest}"

    @staticmethod
    def _group_version(
        identity: Identity,
        accounts: Sequence[_AccountSnapshot],
        budgets: Sequence[_BudgetSnapshot],
    ) -> str:
        payload = {
            "identity": list(identity),
            "accounts": [
                {
                    "id": str(account.id),
                    "client_id": str(account.client_id),
                    "client_status": account.client_status,
                    "status": account.status,
                    "platform": account.platform,
                    "external_account_id": account.external_account_id,
                    "updated_at": account.updated_at.isoformat(),
                }
                for account in sorted(accounts, key=lambda item: str(item.id))
            ],
            "active_budgets": [
                {
                    "id": str(budget.id),
                    "account_id": str(budget.account_id),
                    "status": budget.status,
                    "version": budget.version,
                    "updated_at": budget.updated_at.isoformat(),
                }
                for budget in sorted(budgets, key=lambda item: str(item.id))
                if budget.status == "active"
            ],
        }
        packed = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return f"acv_{hashlib.sha256(packed.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _active_conflict_groups(accounts: Sequence[_AccountSnapshot]) -> Dict[Identity, List[_AccountSnapshot]]:
        groups: Dict[Identity, List[_AccountSnapshot]] = {}
        for account in accounts:
            if account.status != "active" or account.client_status != "active":
                continue
            identity = AssignmentConflictService._identity(account)
            if identity:
                groups.setdefault(identity, []).append(account)
        return {identity: rows for identity, rows in groups.items() if len(rows) > 1}

    @staticmethod
    def _latest_stats_by_account(stats: Iterable[_StatSnapshot]) -> Dict[UUID, _StatSnapshot]:
        latest: Dict[UUID, _StatSnapshot] = {}
        for stat in stats:
            current = latest.get(stat.account_id)
            if current is None or (stat.date, stat.updated_at) > (current.date, current.updated_at):
                latest[stat.account_id] = stat
        return latest

    def _build_group(
        self,
        identity: Identity,
        accounts: Sequence[_AccountSnapshot],
        budgets: Sequence[_BudgetSnapshot],
        stats: Sequence[_StatSnapshot],
    ) -> AssignmentConflictGroupOut:
        ordered_accounts = sorted(accounts, key=lambda item: str(item.id))
        budget_counts: Dict[UUID, int] = {}
        for budget in budgets:
            if budget.status == "active":
                budget_counts[budget.account_id] = budget_counts.get(budget.account_id, 0) + 1
        latest = self._latest_stats_by_account(stats)
        candidates: List[AssignmentConflictCandidateOut] = []
        for account in ordered_accounts:
            latest_stat = latest.get(account.id)
            candidates.append(
                AssignmentConflictCandidateOut(
                    account_id=account.id,
                    client_id=account.client_id,
                    client_name=account.client_name,
                    client_status=account.client_status,
                    account_name=account.name,
                    account_status=account.status,
                    platform=normalize_account_platform(account.platform),
                    external_account_id=account.external_account_id,
                    currency=account.currency,
                    latest_stat=(
                        AssignmentConflictLatestStatOut(
                            date=latest_stat.date,
                            spend=latest_stat.spend,
                            impressions=latest_stat.impressions,
                            clicks=latest_stat.clicks,
                            conversions=latest_stat.conversions,
                        )
                        if latest_stat
                        else None
                    ),
                    active_budget_count=budget_counts.get(account.id, 0),
                )
            )
        latest_dates = [candidate.latest_stat.date for candidate in candidates if candidate.latest_stat]
        return AssignmentConflictGroupOut(
            group_id=self._group_id(identity),
            group_version=self._group_version(identity, ordered_accounts, budgets),
            platform=identity[0],
            canonical_external_account_id=identity[1],
            account_ids=[account.id for account in ordered_accounts],
            candidates=candidates,
            summary=AssignmentConflictGroupSummaryOut(
                candidate_count=len(candidates),
                active_candidate_count=sum(
                    candidate.account_status == "active" and candidate.client_status == "active"
                    for candidate in candidates
                ),
                client_count=len({candidate.client_id for candidate in candidates}),
                active_budget_count=sum(candidate.active_budget_count for candidate in candidates),
                latest_stat_date=max(latest_dates) if latest_dates else None,
            ),
        )

    @staticmethod
    def _audit_payload(
        payload: AssignmentConflictResolveRequest,
        result: AssignmentConflictResolveResponse,
    ) -> dict:
        return {
            "note": payload.note,
            "loser_budget_policy": payload.loser_budget_policy,
            "winner_account_id": str(result.winner_account_id),
            "loser_account_ids": [str(account_id) for account_id in result.loser_account_ids],
            "archived_budget_ids": [str(budget_id) for budget_id in result.archived_budget_ids],
            "sync_required": result.sync_required,
            "before": result.before.model_dump(mode="json"),
            "after": result.after.model_dump(mode="json"),
        }

    @classmethod
    def _insert_sqlite_audit(
        cls,
        conn,
        *,
        payload: AssignmentConflictResolveRequest,
        result: AssignmentConflictResolveResponse,
        actor_user_id: UUID,
        actor_role: str,
    ) -> None:
        winner = next(
            candidate for candidate in result.after.candidates if candidate.account_id == result.winner_account_id
        )
        conn.execute(
            """
            INSERT INTO audit_logs
            (event_type, resource_type, resource_id, actor_user_id, actor_role, tenant_client_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ad_account.assignment_conflict_resolved",
                "ad_account_assignment_conflict",
                result.group_id,
                str(actor_user_id),
                actor_role,
                str(winner.client_id),
                json.dumps(cls._audit_payload(payload, result), separators=(",", ":"), ensure_ascii=True),
                result.resolved_at.isoformat(),
            ),
        )

    @staticmethod
    def _sqlite_accounts(conn) -> List[_AccountSnapshot]:
        rows = conn.execute(
            """
            SELECT a.*, c.name AS client_name, c.status AS client_status
            FROM ad_accounts a
            JOIN clients c ON c.id=a.client_id
            """
        ).fetchall()
        return [
            _AccountSnapshot(
                id=UUID(row["id"]),
                client_id=UUID(row["client_id"]),
                client_name=str(row["client_name"]),
                client_status=str(row["client_status"]),
                name=str(row["name"]),
                status=str(row["status"]),
                platform=str(row["platform"]),
                external_account_id=str(row["external_account_id"]),
                currency=str(row["currency"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _sqlite_budgets(conn, account_ids: Sequence[UUID]) -> List[_BudgetSnapshot]:
        if not account_ids:
            return []
        placeholders = ",".join("?" for _ in account_ids)
        rows = conn.execute(
            f"""
            SELECT id, account_id, status, version, updated_at
            FROM budgets
            WHERE scope='account' AND account_id IN ({placeholders})
            """,
            [str(account_id) for account_id in account_ids],
        ).fetchall()
        return [
            _BudgetSnapshot(
                id=UUID(row["id"]),
                account_id=UUID(row["account_id"]),
                status=str(row["status"]),
                version=int(row["version"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _sqlite_stats(conn, account_ids: Sequence[UUID]) -> List[_StatSnapshot]:
        if not account_ids:
            return []
        placeholders = ",".join("?" for _ in account_ids)
        rows = conn.execute(
            f"""
            SELECT ad_account_id, date, spend, impressions, clicks, conversions, updated_at
            FROM ad_stats
            WHERE ad_account_id IN ({placeholders})
            """,
            [str(account_id) for account_id in account_ids],
        ).fetchall()
        return [
            _StatSnapshot(
                account_id=UUID(row["ad_account_id"]),
                date=date.fromisoformat(row["date"]),
                spend=float(row["spend"] or 0),
                impressions=int(row["impressions"] or 0),
                clicks=int(row["clicks"] or 0),
                conversions=float(row["conversions"] or 0),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def _memory_accounts(self) -> List[_AccountSnapshot]:
        rows: List[_AccountSnapshot] = []
        for account in self.account_store.list(status="all"):
            client = self.client_store.get(account.client_id)
            if not client:
                continue
            rows.append(
                _AccountSnapshot(
                    id=account.id,
                    client_id=account.client_id,
                    client_name=client.name,
                    client_status=client.status,
                    name=account.name,
                    status=account.status,
                    platform=account.platform,
                    external_account_id=account.external_account_id,
                    currency=account.currency,
                    updated_at=account.updated_at,
                )
            )
        return rows

    def _memory_budgets(self, account_ids: Sequence[UUID]) -> List[_BudgetSnapshot]:
        wanted = set(account_ids)
        return [
            _BudgetSnapshot(
                id=budget.id,
                account_id=budget.account_id,
                status=budget.status,
                version=budget.version,
                updated_at=budget.updated_at,
            )
            for budget in self.budget_store.list(status="all")
            if budget.scope == "account" and budget.account_id in wanted
        ]

    def _memory_stats(self, account_ids: Sequence[UUID]) -> List[_StatSnapshot]:
        wanted = set(account_ids)
        if not isinstance(self.ad_stats_store, InMemoryAdStatsStore):
            return []
        return [
            _StatSnapshot(
                account_id=stat.ad_account_id,
                date=stat.date,
                spend=float(stat.spend or 0),
                impressions=int(stat.impressions or 0),
                clicks=int(stat.clicks or 0),
                conversions=float(stat.conversions or 0),
                updated_at=stat.updated_at,
            )
            for stat in self.ad_stats_store.items.values()
            if stat.ad_account_id in wanted
        ]

    @staticmethod
    def _sqlite_allowed_client_ids(
        conn,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID],
    ) -> Optional[set[UUID]]:
        actor = conn.execute(
            "SELECT role, status FROM users WHERE id=?",
            (str(actor_user_id),),
        ).fetchone()
        if actor_role == "admin":
            if not actor or actor["role"] != "admin" or actor["status"] != "active":
                raise _error(403, "forbidden", "Active platform administrator access is required")
            return None
        if actor_role != "agency" or agency_id is None:
            raise _error(403, "assignment_conflict_scope_denied", "Active agency management access is required")
        member = conn.execute(
            """
            SELECT 1
            FROM agency_members member
            JOIN agencies agency ON agency.id=member.agency_id
            JOIN users actor ON actor.id=member.user_id
            WHERE member.agency_id=?
              AND member.user_id=?
              AND member.status='active'
              AND member.role IN ('owner','manager')
              AND agency.status='active'
              AND actor.status='active'
              AND actor.role='agency'
            """,
            (str(agency_id), str(actor_user_id)),
        ).fetchone()
        if not member:
            raise _error(403, "agency_manage_forbidden", "Owner or manager access is required")
        rows = conn.execute(
            "SELECT client_id FROM agency_client_access WHERE agency_id=?",
            (str(agency_id),),
        ).fetchall()
        return {UUID(row["client_id"]) for row in rows}

    def _memory_allowed_client_ids(
        self,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID],
    ) -> Optional[set[UUID]]:
        auth_store = getattr(self.platform_admin_store, "auth_store", None)
        actor = auth_store.get_user(actor_user_id) if auth_store else None
        if actor_role == "admin":
            if not actor or actor.role != "admin" or actor.status != "active":
                raise _error(403, "forbidden", "Active platform administrator access is required")
            return None
        if (
            actor_role != "agency"
            or agency_id is None
            or not isinstance(self.platform_admin_store, InMemoryPlatformAdminStore)
        ):
            raise _error(403, "assignment_conflict_scope_denied", "Active agency management access is required")
        agency = self.platform_admin_store.agencies.get(agency_id)
        member = self.platform_admin_store.members.get(f"{agency_id}:{actor_user_id}")
        if (
            not agency
            or agency.status != "active"
            or not actor
            or actor.status != "active"
            or actor.role != "agency"
            or not member
            or member.status != "active"
            or member.role not in {"owner", "manager"}
        ):
            raise _error(403, "agency_manage_forbidden", "Owner or manager access is required")
        return {
            access.client_id
            for access in self.platform_admin_store.clients.values()
            if access.agency_id == agency_id
        }

    @staticmethod
    def _assert_group_scope(candidate_client_ids: set[UUID], allowed_client_ids: Optional[set[UUID]]) -> None:
        if allowed_client_ids is None:
            return
        if not candidate_client_ids.issubset(allowed_client_ids):
            raise _error(
                403,
                "assignment_conflict_scope_denied",
                "Every conflict candidate must belong to the selected active agency",
            )

    def list_conflicts(
        self,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID] = None,
    ) -> AssignmentConflictListResponse:
        if isinstance(self.account_store, SqliteAdAccountStore):
            with sqlite_conn(self.account_store.db_path) as conn:
                allowed = self._sqlite_allowed_client_ids(
                    conn,
                    actor_user_id=actor_user_id,
                    actor_role=actor_role,
                    agency_id=agency_id,
                )
                accounts = self._sqlite_accounts(conn)
                groups = self._active_conflict_groups(accounts)
                items: List[AssignmentConflictGroupOut] = []
                for identity, candidates in groups.items():
                    if allowed is not None and not {row.client_id for row in candidates}.issubset(allowed):
                        continue
                    ids = [row.id for row in candidates]
                    items.append(
                        self._build_group(
                            identity,
                            candidates,
                            self._sqlite_budgets(conn, ids),
                            self._sqlite_stats(conn, ids),
                        )
                    )
        else:
            with self._lock:
                allowed = self._memory_allowed_client_ids(
                    actor_user_id=actor_user_id,
                    actor_role=actor_role,
                    agency_id=agency_id,
                )
                groups = self._active_conflict_groups(self._memory_accounts())
                items = []
                for identity, candidates in groups.items():
                    if allowed is not None and not {row.client_id for row in candidates}.issubset(allowed):
                        continue
                    ids = [row.id for row in candidates]
                    items.append(
                        self._build_group(
                            identity,
                            candidates,
                            self._memory_budgets(ids),
                            self._memory_stats(ids),
                        )
                    )

        items.sort(key=lambda item: (item.platform, item.canonical_external_account_id, item.group_id))
        return AssignmentConflictListResponse(
            items=items,
            count=len(items),
            summary=AssignmentConflictListSummaryOut(
                conflict_groups=len(items),
                conflicted_accounts=sum(item.summary.candidate_count for item in items),
                active_budgets=sum(item.summary.active_budget_count for item in items),
            ),
        )

    @staticmethod
    def _identity_rows_for_group(
        accounts: Sequence[_AccountSnapshot],
        group_id: str,
    ) -> Tuple[Optional[Identity], List[_AccountSnapshot]]:
        grouped: Dict[Identity, List[_AccountSnapshot]] = {}
        for account in accounts:
            identity = AssignmentConflictService._identity(account)
            if identity:
                grouped.setdefault(identity, []).append(account)
        for identity, rows in grouped.items():
            if AssignmentConflictService._group_id(identity) == group_id:
                return identity, rows
        return None, []

    def resolve_conflict(
        self,
        group_id: str,
        payload: AssignmentConflictResolveRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID] = None,
    ) -> AssignmentConflictResolveResponse:
        if isinstance(self.account_store, SqliteAdAccountStore):
            return self._resolve_sqlite(
                group_id,
                payload,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                agency_id=agency_id,
            )
        return self._resolve_memory(
            group_id,
            payload,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            agency_id=agency_id,
        )

    def _resolve_sqlite(
        self,
        group_id: str,
        payload: AssignmentConflictResolveRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID],
    ) -> AssignmentConflictResolveResponse:
        if (
            not isinstance(self.account_store, SqliteAdAccountStore)
            or not isinstance(self.budget_store, SqliteBudgetStore)
            or not isinstance(self.audit_log_store, SqliteAuditLogStore)
            or self.audit_log_store.db_path != self.account_store.db_path
        ):
            raise _error(500, "assignment_conflict_store_mismatch", "SQLite resolver stores are inconsistent")
        resolved_at = _utcnow()
        with sqlite_conn(self.account_store.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            accounts = self._sqlite_accounts(conn)
            identity, identity_rows = self._identity_rows_for_group(accounts, group_id)
            if identity is None:
                raise _error(404, "assignment_conflict_not_found", "Assignment conflict group was not found")

            allowed = self._sqlite_allowed_client_ids(
                conn,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                agency_id=agency_id,
            )
            expected_set = set(payload.expected_account_ids)
            expected_rows = [row for row in identity_rows if row.id in expected_set]
            scope_rows = expected_rows or identity_rows
            self._assert_group_scope({row.client_id for row in scope_rows}, allowed)

            active_rows = [
                row for row in identity_rows if row.status == "active" and row.client_status == "active"
            ]
            if len(active_rows) < 2:
                raise _error(409, "assignment_conflict_stale", "Conflict was already resolved or changed")

            active_ids = {row.id for row in active_rows}
            self._assert_group_scope({row.client_id for row in active_rows}, allowed)
            budgets = self._sqlite_budgets(conn, [row.id for row in active_rows])
            stats = self._sqlite_stats(conn, [row.id for row in active_rows])
            before = self._build_group(identity, active_rows, budgets, stats)
            if active_ids != expected_set or before.group_version != payload.group_version:
                raise _error(
                    409,
                    "assignment_conflict_stale",
                    "Conflict candidates or version changed; refresh before resolving",
                    {
                        "current_account_ids": [str(account_id) for account_id in before.account_ids],
                        "current_group_version": before.group_version,
                    },
                )
            if payload.winner_account_id not in active_ids:
                raise _error(400, "assignment_conflict_winner_invalid", "Winner must be an active conflict candidate")

            loser_ids = sorted(active_ids - {payload.winner_account_id}, key=str)
            loser_budget_rows = conn.execute(
                f"""
                SELECT *
                FROM budgets
                WHERE scope='account'
                  AND status='active'
                  AND account_id IN ({','.join('?' for _ in loser_ids)})
                ORDER BY id
                """,
                [str(account_id) for account_id in loser_ids],
            ).fetchall()
            if loser_budget_rows and payload.loser_budget_policy == "reject":
                raise _error(
                    409,
                    "assignment_conflict_budgets_present",
                    "Loser accounts have active budgets; choose archive policy explicitly",
                    {
                        "active_budget_count": len(loser_budget_rows),
                        "loser_account_ids": [str(account_id) for account_id in loser_ids],
                    },
                )

            archived_budget_ids: List[UUID] = []
            if payload.loser_budget_policy == "archive":
                for row in loser_budget_rows:
                    existing = self.budget_store._to_budget(row)
                    new_version = int(existing.version) + 1
                    conn.execute(
                        "UPDATE budgets SET status='archived', version=?, updated_at=? WHERE id=?",
                        (new_version, resolved_at.isoformat(), str(existing.id)),
                    )
                    updated_row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(existing.id),)).fetchone()
                    updated = self.budget_store._to_budget(updated_row)
                    conn.execute(
                        """
                        INSERT INTO budget_history (budget_id, changed_at, changed_by, previous_values, new_values)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(existing.id),
                            resolved_at.isoformat(),
                            str(actor_user_id),
                            json.dumps(existing.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True),
                            json.dumps(updated.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True),
                        ),
                    )
                    archived_budget_ids.append(existing.id)

            conn.execute(
                f"UPDATE ad_accounts SET status='archived', updated_at=? WHERE id IN ({','.join('?' for _ in loser_ids)})",
                [resolved_at.isoformat(), *[str(account_id) for account_id in loser_ids]],
            )
            refreshed = self._sqlite_accounts(conn)
            after_rows = [row for row in refreshed if row.id in active_ids]
            after_budgets = self._sqlite_budgets(conn, [row.id for row in after_rows])
            after_stats = self._sqlite_stats(conn, [row.id for row in after_rows])
            after = self._build_group(identity, after_rows, after_budgets, after_stats)
            result = AssignmentConflictResolveResponse(
                group_id=group_id,
                winner_account_id=payload.winner_account_id,
                loser_account_ids=loser_ids,
                archived_budget_ids=archived_budget_ids,
                before=before,
                after=after,
                sync_required=True,
                resolved_at=resolved_at,
            )
            self._insert_sqlite_audit(
                conn,
                payload=payload,
                result=result,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
            )
            conn.commit()

        return result

    def _resolve_memory(
        self,
        group_id: str,
        payload: AssignmentConflictResolveRequest,
        *,
        actor_user_id: UUID,
        actor_role: str,
        agency_id: Optional[UUID],
    ) -> AssignmentConflictResolveResponse:
        if (
            not isinstance(self.account_store, InMemoryAdAccountStore)
            or not isinstance(self.budget_store, InMemoryBudgetStore)
            or not isinstance(self.audit_log_store, InMemoryAuditLogStore)
        ):
            raise _error(500, "assignment_conflict_store_mismatch", "In-memory resolver stores are inconsistent")
        with self._lock:
            resolved_at = _utcnow()
            accounts = self._memory_accounts()
            identity, identity_rows = self._identity_rows_for_group(accounts, group_id)
            if identity is None:
                raise _error(404, "assignment_conflict_not_found", "Assignment conflict group was not found")
            allowed = self._memory_allowed_client_ids(
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                agency_id=agency_id,
            )
            expected_set = set(payload.expected_account_ids)
            expected_rows = [row for row in identity_rows if row.id in expected_set]
            self._assert_group_scope({row.client_id for row in (expected_rows or identity_rows)}, allowed)

            active_rows = [
                row for row in identity_rows if row.status == "active" and row.client_status == "active"
            ]
            if len(active_rows) < 2:
                raise _error(409, "assignment_conflict_stale", "Conflict was already resolved or changed")
            active_ids = {row.id for row in active_rows}
            self._assert_group_scope({row.client_id for row in active_rows}, allowed)
            budgets = self._memory_budgets([row.id for row in active_rows])
            stats = self._memory_stats([row.id for row in active_rows])
            before = self._build_group(identity, active_rows, budgets, stats)
            if active_ids != expected_set or before.group_version != payload.group_version:
                raise _error(
                    409,
                    "assignment_conflict_stale",
                    "Conflict candidates or version changed; refresh before resolving",
                    {
                        "current_account_ids": [str(account_id) for account_id in before.account_ids],
                        "current_group_version": before.group_version,
                    },
                )
            loser_ids = sorted(active_ids - {payload.winner_account_id}, key=str)
            if payload.winner_account_id not in active_ids:
                raise _error(400, "assignment_conflict_winner_invalid", "Winner must be an active conflict candidate")

            loser_budgets = [
                budget
                for budget in self.budget_store.items.values()
                if budget.scope == "account"
                and budget.status == "active"
                and budget.account_id in set(loser_ids)
            ]
            if loser_budgets and payload.loser_budget_policy == "reject":
                raise _error(
                    409,
                    "assignment_conflict_budgets_present",
                    "Loser accounts have active budgets; choose archive policy explicitly",
                    {
                        "active_budget_count": len(loser_budgets),
                        "loser_account_ids": [str(account_id) for account_id in loser_ids],
                    },
                )

            staged_accounts = dict(self.account_store.items)
            for loser_id in loser_ids:
                existing = staged_accounts[loser_id]
                staged_accounts[loser_id] = existing.model_copy(
                    update={"status": "archived", "updated_at": resolved_at}
                )

            staged_budgets = dict(self.budget_store.items)
            staged_history: List[Tuple[UUID, BudgetHistoryOut]] = []
            archived_budget_ids: List[UUID] = []
            if payload.loser_budget_policy == "archive":
                for existing in loser_budgets:
                    updated = existing.model_copy(
                        update={
                            "status": "archived",
                            "version": existing.version + 1,
                            "updated_at": resolved_at,
                        }
                    )
                    staged_budgets[existing.id] = updated
                    staged_history.append(
                        (
                            existing.id,
                            BudgetHistoryOut(
                                id=len(self.budget_store.hist.get(existing.id, [])) + 1,
                                budget_id=existing.id,
                                changed_at=resolved_at,
                                changed_by=actor_user_id,
                                previous_values=existing.model_dump(mode="json"),
                                new_values=updated.model_dump(mode="json"),
                            ),
                        )
                    )
                    archived_budget_ids.append(existing.id)

            after_rows: List[_AccountSnapshot] = []
            for account_id in sorted(active_ids, key=str):
                account = staged_accounts[account_id]
                client = self.client_store.get(account.client_id)
                after_rows.append(
                    _AccountSnapshot(
                        id=account.id,
                        client_id=account.client_id,
                        client_name=client.name,
                        client_status=client.status,
                        name=account.name,
                        status=account.status,
                        platform=account.platform,
                        external_account_id=account.external_account_id,
                        currency=account.currency,
                        updated_at=account.updated_at,
                    )
                )
            after_budget_snapshots = [
                _BudgetSnapshot(
                    id=budget.id,
                    account_id=budget.account_id,
                    status=budget.status,
                    version=budget.version,
                    updated_at=budget.updated_at,
                )
                for budget in staged_budgets.values()
                if budget.scope == "account" and budget.account_id in active_ids
            ]
            after = self._build_group(identity, after_rows, after_budget_snapshots, stats)

            result = AssignmentConflictResolveResponse(
                group_id=group_id,
                winner_account_id=payload.winner_account_id,
                loser_account_ids=loser_ids,
                archived_budget_ids=archived_budget_ids,
                before=before,
                after=after,
                sync_required=True,
                resolved_at=resolved_at,
            )
            winner = next(
                candidate for candidate in result.after.candidates if candidate.account_id == result.winner_account_id
            )
            self.audit_log_store.create(
                event_type="ad_account.assignment_conflict_resolved",
                resource_type="ad_account_assignment_conflict",
                resource_id=result.group_id,
                actor_user_id=actor_user_id,
                actor_role=actor_role,
                tenant_client_id=winner.client_id,
                payload=self._audit_payload(payload, result),
            )

            self.account_store.items = staged_accounts
            self.budget_store.items = staged_budgets
            for budget_id, history in staged_history:
                self.budget_store.hist.setdefault(budget_id, []).append(history)
            return result
