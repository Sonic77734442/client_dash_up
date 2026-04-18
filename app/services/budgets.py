from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import (
    BudgetCreate,
    BudgetHistoryOut,
    BudgetOut,
    BudgetPatch,
    BudgetTransferOut,
    BudgetTransferRequest,
    BudgetTransferResponse,
)


getcontext().prec = 28
MONEY_PLACES = Decimal("0.01")
PERCENT_PLACES = Decimal("0.01")


class BudgetStore(Protocol):
    def create(self, payload: BudgetCreate) -> BudgetOut: ...

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[BudgetOut]: ...

    def get(self, budget_id: UUID) -> Optional[BudgetOut]: ...

    def patch(self, budget_id: UUID, payload: BudgetPatch) -> BudgetOut: ...

    def archive(self, budget_id: UUID) -> BudgetOut: ...

    def transfer(self, source_budget_id: UUID, payload: BudgetTransferRequest) -> BudgetTransferResponse: ...

    def history(self, budget_id: UUID) -> List[BudgetHistoryOut]: ...

    def list_transfers(
        self,
        budget_id: UUID,
        *,
        direction: str = "all",
        limit: int = 50,
    ) -> List[BudgetTransferOut]: ...

    def resolve_effective(
        self,
        *,
        client_id: Optional[UUID],
        account_id: Optional[UUID],
        period_start: date,
        period_end: date,
    ) -> Optional[BudgetOut]: ...


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _q_percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)


def _validate_period(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date cannot be after end_date")


def _validate_scope(scope: str, account_id: Optional[UUID]) -> None:
    if scope == "client" and account_id is not None:
        raise HTTPException(status_code=400, detail="scope='client' requires account_id=null")
    if scope == "account" and account_id is None:
        raise HTTPException(status_code=400, detail="scope='account' requires account_id")


def _date_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    return start_a <= end_b and end_a >= start_b


class SqliteBudgetStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(self.db_path)

    def _to_budget(self, row) -> BudgetOut:
        return BudgetOut(
            id=UUID(row["id"]),
            client_id=UUID(row["client_id"]),
            scope=row["scope"],
            account_id=UUID(row["account_id"]) if row["account_id"] else None,
            amount=Decimal(str(row["amount"])),
            currency=row["currency"],
            period_type=row["period_type"],
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            status=row["status"],
            version=int(row["version"]),
            note=row["note"],
            created_by=UUID(row["created_by"]) if row["created_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _to_history(self, row) -> BudgetHistoryOut:
        return BudgetHistoryOut(
            id=int(row["id"]),
            budget_id=UUID(row["budget_id"]),
            changed_at=datetime.fromisoformat(row["changed_at"]),
            changed_by=UUID(row["changed_by"]) if row["changed_by"] else None,
            previous_values=json.loads(row["previous_values"]),
            new_values=json.loads(row["new_values"]),
        )

    def _to_transfer(self, row) -> BudgetTransferOut:
        return BudgetTransferOut(
            id=int(row["id"]),
            source_budget_id=UUID(row["source_budget_id"]),
            target_budget_id=UUID(row["target_budget_id"]),
            amount=Decimal(str(row["amount"])),
            note=row["note"],
            changed_by=UUID(row["changed_by"]) if row["changed_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _assert_no_overlap(
        self,
        *,
        conn,
        scope: str,
        client_id: UUID,
        account_id: Optional[UUID],
        start_date: date,
        end_date: date,
        exclude_budget_id: Optional[UUID] = None,
    ) -> None:
        params: List[object] = [start_date.isoformat(), end_date.isoformat()]
        exclude_sql = ""
        if exclude_budget_id:
            exclude_sql = " AND id<>?"
            params.append(str(exclude_budget_id))

        if scope == "client":
            query = (
                "SELECT 1 FROM budgets WHERE status='active' AND scope='client' AND client_id=? "
                "AND start_date<=? AND end_date>=?"
                + exclude_sql
                + " LIMIT 1"
            )
            params.insert(0, str(client_id))
        else:
            query = (
                "SELECT 1 FROM budgets WHERE status='active' AND scope='account' AND account_id=? "
                "AND start_date<=? AND end_date>=?"
                + exclude_sql
                + " LIMIT 1"
            )
            params.insert(0, str(account_id))

        row = conn.execute(query, params).fetchone()
        if row:
            scope_id = str(client_id) if scope == "client" else str(account_id)
            raise HTTPException(status_code=409, detail=f"Active budget overlap for {scope} scope id={scope_id}")

    def _assert_client_account_allocation_limit(
        self,
        *,
        conn,
        scope: str,
        client_id: UUID,
        account_id: Optional[UUID],
        amount: Decimal,
        start_date: date,
        end_date: date,
        status: str,
        exclude_budget_id: Optional[UUID] = None,
    ) -> None:
        if status != "active":
            return

        exclude_sql = " AND id<>?" if exclude_budget_id else ""
        exclude_params: List[object] = [str(exclude_budget_id)] if exclude_budget_id else []

        # Client budget cannot be lower than already allocated account budgets in overlapping period.
        if scope == "client":
            rows = conn.execute(
                """
                SELECT amount
                FROM budgets
                WHERE status='active'
                  AND scope='account'
                  AND client_id=?
                  AND start_date<=?
                  AND end_date>=?
                """
                + exclude_sql,
                [str(client_id), end_date.isoformat(), start_date.isoformat(), *exclude_params],
            ).fetchall()
            accounts_total = _q_money(
                sum((_q_money(Decimal(str(r["amount"]))) for r in rows), Decimal("0"))
            )
            if accounts_total > _q_money(amount):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Client budget is lower than allocated active account budgets "
                        f"for overlapping period ({accounts_total} > {_q_money(amount)})."
                    ),
                )
            return

        # Account budget(s) cannot exceed active client budget for overlapping period.
        client_budget_row = conn.execute(
            """
            SELECT id, amount, start_date, end_date
            FROM budgets
            WHERE status='active'
              AND scope='client'
              AND client_id=?
              AND start_date<=?
              AND end_date>=?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (str(client_id), end_date.isoformat(), start_date.isoformat()),
        ).fetchone()
        if not client_budget_row:
            return

        client_budget_amount = _q_money(Decimal(str(client_budget_row["amount"])))
        client_budget_id = str(client_budget_row["id"])
        client_start = str(client_budget_row["start_date"])
        client_end = str(client_budget_row["end_date"])

        sum_rows = conn.execute(
            """
            SELECT amount
            FROM budgets
            WHERE status='active'
              AND scope='account'
              AND client_id=?
              AND start_date<=?
              AND end_date>=?
            """
            + exclude_sql,
            [str(client_id), client_end, client_start, *exclude_params],
        ).fetchall()
        account_total = _q_money(
            sum((_q_money(Decimal(str(r["amount"]))) for r in sum_rows), Decimal("0"))
        )
        projected_total = account_total + _q_money(amount)
        if projected_total > client_budget_amount:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Account budget allocation exceeds client budget cap "
                    f"for period {client_start}..{client_end} "
                    f"({projected_total} > {client_budget_amount}; client_budget_id={client_budget_id})."
                ),
            )

    def create(self, payload: BudgetCreate) -> BudgetOut:
        _validate_period(payload.start_date, payload.end_date)
        _validate_scope(payload.scope, payload.account_id)

        now = datetime.utcnow().isoformat()
        budget_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_no_overlap(
                conn=conn,
                scope=payload.scope,
                client_id=payload.client_id,
                account_id=payload.account_id,
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
            self._assert_client_account_allocation_limit(
                conn=conn,
                scope=payload.scope,
                client_id=payload.client_id,
                account_id=payload.account_id,
                amount=payload.amount,
                start_date=payload.start_date,
                end_date=payload.end_date,
                status="active",
            )
            conn.execute(
                """
                INSERT INTO budgets
                (id, client_id, scope, account_id, amount, currency, period_type, start_date, end_date, status, version, note, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?)
                """,
                (
                    budget_id,
                    str(payload.client_id),
                    payload.scope,
                    str(payload.account_id) if payload.account_id else None,
                    str(_q_money(payload.amount)),
                    payload.currency,
                    payload.period_type,
                    payload.start_date.isoformat(),
                    payload.end_date.isoformat(),
                    payload.note,
                    str(payload.created_by) if payload.created_by else None,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
        return self._to_budget(row)

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[BudgetOut]:
        where = ["1=1"]
        params: List[object] = []

        if client_id:
            where.append("client_id=?")
            params.append(str(client_id))
        if account_id:
            where.append("account_id=?")
            params.append(str(account_id))
        effective_status = status or "active"
        if effective_status != "all":
            where.append("status=?")
            params.append(effective_status)
        if date_from:
            where.append("end_date>=?")
            params.append(date_from.isoformat())
        if date_to:
            where.append("start_date<=?")
            params.append(date_to.isoformat())

        q = f"SELECT * FROM budgets WHERE {' AND '.join(where)} ORDER BY updated_at DESC"
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(q, params).fetchall()
        return [self._to_budget(r) for r in rows]

    def get(self, budget_id: UUID) -> Optional[BudgetOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(budget_id),)).fetchone()
        return self._to_budget(row) if row else None

    def patch(self, budget_id: UUID, payload: BudgetPatch) -> BudgetOut:
        existing = self.get(budget_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        data = existing.model_dump()
        patch = payload.model_dump(exclude_unset=True)
        changed_by = patch.pop("changed_by", None)
        for key, value in patch.items():
            data[key] = value

        if not patch:
            return existing

        _validate_period(data["start_date"], data["end_date"])
        _validate_scope(data["scope"], data["account_id"])

        candidate_normalized = {
            "client_id": data["client_id"],
            "scope": data["scope"],
            "account_id": data["account_id"],
            "amount": _q_money(Decimal(str(data["amount"]))),
            "currency": data["currency"],
            "period_type": data["period_type"],
            "start_date": data["start_date"],
            "end_date": data["end_date"],
            "status": data["status"],
            "note": data["note"],
            "created_by": data["created_by"],
        }
        existing_normalized = {
            "client_id": existing.client_id,
            "scope": existing.scope,
            "account_id": existing.account_id,
            "amount": _q_money(Decimal(str(existing.amount))),
            "currency": existing.currency,
            "period_type": existing.period_type,
            "start_date": existing.start_date,
            "end_date": existing.end_date,
            "status": existing.status,
            "note": existing.note,
            "created_by": existing.created_by,
        }
        if candidate_normalized == existing_normalized:
            return existing

        now = datetime.utcnow().isoformat()
        new_version = int(existing.version) + 1
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if data["status"] == "active":
                self._assert_no_overlap(
                    conn=conn,
                    scope=data["scope"],
                    client_id=data["client_id"],
                    account_id=data["account_id"],
                    start_date=data["start_date"],
                    end_date=data["end_date"],
                    exclude_budget_id=budget_id,
                )
                self._assert_client_account_allocation_limit(
                    conn=conn,
                    scope=data["scope"],
                    client_id=data["client_id"],
                    account_id=data["account_id"],
                    amount=candidate_normalized["amount"],
                    start_date=data["start_date"],
                    end_date=data["end_date"],
                    status=data["status"],
                    exclude_budget_id=budget_id,
                )

            conn.execute(
                """
                UPDATE budgets
                SET client_id=?, scope=?, account_id=?, amount=?, currency=?, period_type=?, start_date=?, end_date=?, status=?,
                    version=?, note=?, created_by=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(data["client_id"]),
                    data["scope"],
                    str(data["account_id"]) if data["account_id"] else None,
                    str(candidate_normalized["amount"]),
                    data["currency"],
                    data["period_type"],
                    data["start_date"].isoformat(),
                    data["end_date"].isoformat(),
                    data["status"],
                    new_version,
                    data["note"],
                    str(data["created_by"]) if data["created_by"] else None,
                    now,
                    str(budget_id),
                ),
            )
            previous_values = json.dumps(existing.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True)
            new_values_obj = dict(data)
            new_values_obj["version"] = new_version
            new_values_obj["updated_at"] = now
            new_values = json.dumps(BudgetOut(**new_values_obj, id=budget_id, created_at=existing.created_at).model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True)
            conn.execute(
                """
                INSERT INTO budget_history (budget_id, changed_at, changed_by, previous_values, new_values)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(budget_id),
                    now,
                    str(changed_by) if changed_by else None,
                    previous_values,
                    new_values,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(budget_id),)).fetchone()
        return self._to_budget(row)

    def archive(self, budget_id: UUID) -> BudgetOut:
        existing = self.get(budget_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE budgets SET status='archived', updated_at=? WHERE id=?",
                (now, str(budget_id)),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(budget_id),)).fetchone()
        return self._to_budget(row)

    def transfer(self, source_budget_id: UUID, payload: BudgetTransferRequest) -> BudgetTransferResponse:
        source_existing = self.get(source_budget_id)
        if not source_existing:
            raise HTTPException(status_code=404, detail="Budget not found")
        if source_existing.scope != "account" or not source_existing.account_id:
            raise HTTPException(status_code=400, detail="Transfer is supported only for account-scope budgets")
        if source_existing.status != "active":
            raise HTTPException(status_code=400, detail="Source budget must be active")
        if source_existing.account_id == payload.target_account_id:
            raise HTTPException(status_code=400, detail="target_account_id must differ from source account_id")

        transfer_amount = _q_money(payload.amount)
        source_amount = _q_money(Decimal(str(source_existing.amount)))
        if transfer_amount > source_amount:
            raise HTTPException(status_code=400, detail="Transfer amount exceeds source budget amount")

        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            source_row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(source_budget_id),)).fetchone()
            if not source_row:
                raise HTTPException(status_code=404, detail="Budget not found")
            source_current = self._to_budget(source_row)

            target_row = conn.execute(
                """
                SELECT * FROM budgets
                WHERE status='active'
                  AND scope='account'
                  AND client_id=?
                  AND account_id=?
                  AND start_date<=?
                  AND end_date>=?
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (
                    str(source_current.client_id),
                    str(payload.target_account_id),
                    source_current.end_date.isoformat(),
                    source_current.start_date.isoformat(),
                ),
            ).fetchone()

            source_prev_json = json.dumps(source_current.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True)
            new_source_amount = _q_money(_q_money(Decimal(str(source_current.amount))) - transfer_amount)
            new_source_version = int(source_current.version) + 1
            conn.execute(
                """
                UPDATE budgets SET amount=?, version=?, updated_at=?, note=?
                WHERE id=?
                """,
                (
                    str(new_source_amount),
                    new_source_version,
                    now,
                    payload.note if payload.note is not None else source_current.note,
                    str(source_budget_id),
                ),
            )
            source_updated_row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(source_budget_id),)).fetchone()
            source_updated = self._to_budget(source_updated_row)
            conn.execute(
                """
                INSERT INTO budget_history (budget_id, changed_at, changed_by, previous_values, new_values)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(source_budget_id),
                    now,
                    str(payload.changed_by) if payload.changed_by else None,
                    source_prev_json,
                    json.dumps(source_updated.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True),
                ),
            )

            if target_row:
                target_current = self._to_budget(target_row)
                target_prev_json = json.dumps(target_current.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True)
                target_new_amount = _q_money(_q_money(Decimal(str(target_current.amount))) + transfer_amount)
                target_new_version = int(target_current.version) + 1
                conn.execute(
                    """
                    UPDATE budgets SET amount=?, version=?, updated_at=?, note=?
                    WHERE id=?
                    """,
                    (
                        str(target_new_amount),
                        target_new_version,
                        now,
                        payload.note if payload.note is not None else target_current.note,
                        str(target_current.id),
                    ),
                )
                target_updated_row = conn.execute("SELECT * FROM budgets WHERE id=?", (str(target_current.id),)).fetchone()
                target_updated = self._to_budget(target_updated_row)
                conn.execute(
                    """
                    INSERT INTO budget_history (budget_id, changed_at, changed_by, previous_values, new_values)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(target_current.id),
                        now,
                        str(payload.changed_by) if payload.changed_by else None,
                        target_prev_json,
                        json.dumps(target_updated.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True),
                    ),
                )
            else:
                new_target_id = str(uuid4())
                target_new_amount = transfer_amount
                conn.execute(
                    """
                    INSERT INTO budgets
                    (id, client_id, scope, account_id, amount, currency, period_type, start_date, end_date, status, version, note, created_by, created_at, updated_at)
                    VALUES (?, ?, 'account', ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?)
                    """,
                    (
                        new_target_id,
                        str(source_current.client_id),
                        str(payload.target_account_id),
                        str(target_new_amount),
                        source_current.currency,
                        source_current.period_type,
                        source_current.start_date.isoformat(),
                        source_current.end_date.isoformat(),
                        payload.note or f"Transfer from budget {source_current.id}",
                        str(payload.changed_by) if payload.changed_by else None,
                        now,
                        now,
                    ),
                )
                target_updated_row = conn.execute("SELECT * FROM budgets WHERE id=?", (new_target_id,)).fetchone()
                target_updated = self._to_budget(target_updated_row)

            conn.execute(
                """
                INSERT INTO budget_transfers (source_budget_id, target_budget_id, amount, note, changed_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_budget_id),
                    str(target_updated.id),
                    str(transfer_amount),
                    payload.note,
                    str(payload.changed_by) if payload.changed_by else None,
                    now,
                ),
            )

            conn.commit()
            return BudgetTransferResponse(
                source_budget=source_updated,
                target_budget=target_updated,
                transferred_amount=transfer_amount,
            )

    def history(self, budget_id: UUID) -> List[BudgetHistoryOut]:
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM budget_history WHERE budget_id=? ORDER BY changed_at DESC, id DESC",
                (str(budget_id),),
            ).fetchall()
        return [self._to_history(r) for r in rows]

    def list_transfers(
        self,
        budget_id: UUID,
        *,
        direction: str = "all",
        limit: int = 50,
    ) -> List[BudgetTransferOut]:
        safe_limit = max(1, min(limit, 200))
        if direction not in {"all", "incoming", "outgoing"}:
            direction = "all"
        if direction == "incoming":
            where_sql = "target_budget_id=?"
            params = [str(budget_id)]
        elif direction == "outgoing":
            where_sql = "source_budget_id=?"
            params = [str(budget_id)]
        else:
            where_sql = "(source_budget_id=? OR target_budget_id=?)"
            params = [str(budget_id), str(budget_id)]
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM budget_transfers
                WHERE """
                + where_sql
                + """
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ).fetchall()
        return [self._to_transfer(r) for r in rows]

    def resolve_effective(
        self,
        *,
        client_id: Optional[UUID],
        account_id: Optional[UUID],
        period_start: date,
        period_end: date,
    ) -> Optional[BudgetOut]:
        if not client_id:
            return None

        with sqlite_conn(self.db_path) as conn:
            if account_id:
                row = conn.execute(
                    """
                    SELECT *
                    FROM budgets
                    WHERE status='active'
                      AND scope='account'
                      AND client_id=?
                      AND account_id=?
                      AND end_date>=?
                      AND start_date<=?
                    ORDER BY version DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (str(client_id), str(account_id), period_start.isoformat(), period_end.isoformat()),
                ).fetchone()
                if row:
                    return self._to_budget(row)

            row = conn.execute(
                """
                SELECT *
                FROM budgets
                WHERE status='active'
                  AND scope='client'
                  AND client_id=?
                  AND end_date>=?
                  AND start_date<=?
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (str(client_id), period_start.isoformat(), period_end.isoformat()),
            ).fetchone()
            return self._to_budget(row) if row else None


class InMemoryBudgetStore:
    def __init__(self):
        self.items: Dict[UUID, BudgetOut] = {}
        self.hist: Dict[UUID, List[BudgetHistoryOut]] = {}
        self.transfers: List[BudgetTransferOut] = []

    def _assert_no_overlap(
        self,
        *,
        scope: str,
        client_id: UUID,
        account_id: Optional[UUID],
        start_date: date,
        end_date: date,
        exclude_budget_id: Optional[UUID] = None,
    ) -> None:
        for b in self.items.values():
            if b.status != "active":
                continue
            if exclude_budget_id and b.id == exclude_budget_id:
                continue
            if not _date_overlap(start_date, end_date, b.start_date, b.end_date):
                continue
            if scope == "client" and b.scope == "client" and b.client_id == client_id:
                raise HTTPException(status_code=409, detail=f"Active budget overlap for client scope id={client_id}")
            if scope == "account" and b.scope == "account" and b.account_id == account_id:
                raise HTTPException(status_code=409, detail=f"Active budget overlap for account scope id={account_id}")

    def _assert_client_account_allocation_limit(
        self,
        *,
        scope: str,
        client_id: UUID,
        account_id: Optional[UUID],
        amount: Decimal,
        start_date: date,
        end_date: date,
        status: str,
        exclude_budget_id: Optional[UUID] = None,
    ) -> None:
        if status != "active":
            return

        rows = [
            b
            for b in self.items.values()
            if b.status == "active" and (exclude_budget_id is None or b.id != exclude_budget_id) and b.client_id == client_id
        ]

        if scope == "client":
            accounts_total = _q_money(
                sum(
                    (_q_money(Decimal(str(b.amount))) for b in rows if b.scope == "account" and _date_overlap(start_date, end_date, b.start_date, b.end_date)),
                    Decimal("0"),
                )
            )
            if accounts_total > _q_money(amount):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Client budget is lower than allocated active account budgets "
                        f"for overlapping period ({accounts_total} > {_q_money(amount)})."
                    ),
                )
            return

        client_budgets = [
            b
            for b in rows
            if b.scope == "client" and _date_overlap(start_date, end_date, b.start_date, b.end_date)
        ]
        if not client_budgets:
            return
        client_budget = sorted(client_budgets, key=lambda x: x.updated_at, reverse=True)[0]
        account_total = _q_money(
            sum(
                (
                    _q_money(Decimal(str(b.amount)))
                    for b in rows
                    if b.scope == "account" and _date_overlap(client_budget.start_date, client_budget.end_date, b.start_date, b.end_date)
                ),
                Decimal("0"),
            )
        )
        projected_total = account_total + _q_money(amount)
        if projected_total > _q_money(Decimal(str(client_budget.amount))):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Account budget allocation exceeds client budget cap "
                    f"for period {client_budget.start_date.isoformat()}..{client_budget.end_date.isoformat()} "
                    f"({projected_total} > {_q_money(Decimal(str(client_budget.amount)))}; client_budget_id={client_budget.id})."
                ),
            )

    def create(self, payload: BudgetCreate) -> BudgetOut:
        _validate_period(payload.start_date, payload.end_date)
        _validate_scope(payload.scope, payload.account_id)
        self._assert_no_overlap(
            scope=payload.scope,
            client_id=payload.client_id,
            account_id=payload.account_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        self._assert_client_account_allocation_limit(
            scope=payload.scope,
            client_id=payload.client_id,
            account_id=payload.account_id,
            amount=payload.amount,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status="active",
        )

        now = datetime.utcnow()
        rec = BudgetOut(
            id=uuid4(),
            client_id=payload.client_id,
            scope=payload.scope,
            account_id=payload.account_id,
            amount=_q_money(payload.amount),
            currency=payload.currency,
            period_type=payload.period_type,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status="active",
            version=1,
            note=payload.note,
            created_by=payload.created_by,
            created_at=now,
            updated_at=now,
        )
        self.items[rec.id] = rec
        return rec

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[BudgetOut]:
        rows = list(self.items.values())
        if client_id:
            rows = [r for r in rows if r.client_id == client_id]
        if account_id:
            rows = [r for r in rows if r.account_id == account_id]
        effective_status = status or "active"
        if effective_status != "all":
            rows = [r for r in rows if r.status == effective_status]
        if date_from:
            rows = [r for r in rows if r.end_date >= date_from]
        if date_to:
            rows = [r for r in rows if r.start_date <= date_to]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def get(self, budget_id: UUID) -> Optional[BudgetOut]:
        return self.items.get(budget_id)

    def patch(self, budget_id: UUID, payload: BudgetPatch) -> BudgetOut:
        existing = self.get(budget_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")

        update = payload.model_dump(exclude_unset=True)
        changed_by = update.pop("changed_by", None)
        if not update:
            return existing
        merged = {**existing.model_dump(), **update}
        _validate_period(merged["start_date"], merged["end_date"])
        _validate_scope(merged["scope"], merged["account_id"])

        candidate_normalized = {
            "client_id": merged["client_id"],
            "scope": merged["scope"],
            "account_id": merged["account_id"],
            "amount": _q_money(Decimal(str(merged["amount"]))),
            "currency": merged["currency"],
            "period_type": merged["period_type"],
            "start_date": merged["start_date"],
            "end_date": merged["end_date"],
            "status": merged["status"],
            "note": merged["note"],
            "created_by": merged["created_by"],
        }
        existing_normalized = {
            "client_id": existing.client_id,
            "scope": existing.scope,
            "account_id": existing.account_id,
            "amount": _q_money(Decimal(str(existing.amount))),
            "currency": existing.currency,
            "period_type": existing.period_type,
            "start_date": existing.start_date,
            "end_date": existing.end_date,
            "status": existing.status,
            "note": existing.note,
            "created_by": existing.created_by,
        }
        if candidate_normalized == existing_normalized:
            return existing

        if merged["status"] == "active":
            self._assert_no_overlap(
                scope=merged["scope"],
                client_id=merged["client_id"],
                account_id=merged["account_id"],
                start_date=merged["start_date"],
                end_date=merged["end_date"],
                exclude_budget_id=budget_id,
            )
            self._assert_client_account_allocation_limit(
                scope=merged["scope"],
                client_id=merged["client_id"],
                account_id=merged["account_id"],
                amount=candidate_normalized["amount"],
                start_date=merged["start_date"],
                end_date=merged["end_date"],
                status=merged["status"],
                exclude_budget_id=budget_id,
            )

        rec = existing.model_copy(
            update={
                **update,
                "amount": candidate_normalized["amount"],
                "version": existing.version + 1,
                "updated_at": datetime.utcnow(),
            }
        )
        self.items[budget_id] = rec

        hist = BudgetHistoryOut(
            id=len(self.hist.get(budget_id, [])) + 1,
            budget_id=budget_id,
            changed_at=datetime.utcnow(),
            changed_by=changed_by,
            previous_values=existing.model_dump(mode="json"),
            new_values=rec.model_dump(mode="json"),
        )
        self.hist.setdefault(budget_id, []).append(hist)
        return rec

    def archive(self, budget_id: UUID) -> BudgetOut:
        existing = self.get(budget_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Budget not found")
        rec = existing.model_copy(update={"status": "archived", "updated_at": datetime.utcnow()})
        self.items[budget_id] = rec
        return rec

    def transfer(self, source_budget_id: UUID, payload: BudgetTransferRequest) -> BudgetTransferResponse:
        source = self.get(source_budget_id)
        if not source:
            raise HTTPException(status_code=404, detail="Budget not found")
        if source.scope != "account" or not source.account_id:
            raise HTTPException(status_code=400, detail="Transfer is supported only for account-scope budgets")
        if source.status != "active":
            raise HTTPException(status_code=400, detail="Source budget must be active")
        if source.account_id == payload.target_account_id:
            raise HTTPException(status_code=400, detail="target_account_id must differ from source account_id")

        transfer_amount = _q_money(payload.amount)
        source_amount = _q_money(Decimal(str(source.amount)))
        if transfer_amount > source_amount:
            raise HTTPException(status_code=400, detail="Transfer amount exceeds source budget amount")

        now = datetime.utcnow()
        source_next = source.model_copy(
            update={
                "amount": _q_money(source_amount - transfer_amount),
                "version": source.version + 1,
                "updated_at": now,
                "note": payload.note if payload.note is not None else source.note,
            }
        )
        self.items[source.id] = source_next
        self.hist.setdefault(source.id, []).append(
            BudgetHistoryOut(
                id=len(self.hist.get(source.id, [])) + 1,
                budget_id=source.id,
                changed_at=now,
                changed_by=payload.changed_by,
                previous_values=source.model_dump(mode="json"),
                new_values=source_next.model_dump(mode="json"),
            )
        )

        candidates = [
            b
            for b in self.items.values()
            if b.status == "active"
            and b.scope == "account"
            and b.client_id == source.client_id
            and b.account_id == payload.target_account_id
            and _date_overlap(source.start_date, source.end_date, b.start_date, b.end_date)
        ]
        if candidates:
            target = sorted(candidates, key=lambda x: (x.version, x.updated_at), reverse=True)[0]
            target_next = target.model_copy(
                update={
                    "amount": _q_money(_q_money(Decimal(str(target.amount))) + transfer_amount),
                    "version": target.version + 1,
                    "updated_at": now,
                    "note": payload.note if payload.note is not None else target.note,
                }
            )
            self.items[target.id] = target_next
            self.hist.setdefault(target.id, []).append(
                BudgetHistoryOut(
                    id=len(self.hist.get(target.id, [])) + 1,
                    budget_id=target.id,
                    changed_at=now,
                    changed_by=payload.changed_by,
                    previous_values=target.model_dump(mode="json"),
                    new_values=target_next.model_dump(mode="json"),
                )
            )
        else:
            target_next = BudgetOut(
                id=uuid4(),
                client_id=source.client_id,
                scope="account",
                account_id=payload.target_account_id,
                amount=transfer_amount,
                currency=source.currency,
                period_type=source.period_type,
                start_date=source.start_date,
                end_date=source.end_date,
                status="active",
                version=1,
                note=payload.note or f"Transfer from budget {source.id}",
                created_by=payload.changed_by,
                created_at=now,
                updated_at=now,
            )
            self.items[target_next.id] = target_next

        self.transfers.append(
            BudgetTransferOut(
                id=len(self.transfers) + 1,
                source_budget_id=source_next.id,
                target_budget_id=target_next.id,
                amount=transfer_amount,
                note=payload.note,
                changed_by=payload.changed_by,
                created_at=now,
            )
        )

        return BudgetTransferResponse(
            source_budget=source_next,
            target_budget=target_next,
            transferred_amount=transfer_amount,
        )

    def history(self, budget_id: UUID) -> List[BudgetHistoryOut]:
        return list(reversed(self.hist.get(budget_id, [])))

    def list_transfers(
        self,
        budget_id: UUID,
        *,
        direction: str = "all",
        limit: int = 50,
    ) -> List[BudgetTransferOut]:
        safe_limit = max(1, min(limit, 200))
        if direction == "incoming":
            rows = [t for t in self.transfers if t.target_budget_id == budget_id]
        elif direction == "outgoing":
            rows = [t for t in self.transfers if t.source_budget_id == budget_id]
        else:
            rows = [t for t in self.transfers if t.source_budget_id == budget_id or t.target_budget_id == budget_id]
        rows.sort(key=lambda x: x.created_at, reverse=True)
        return rows[:safe_limit]

    def resolve_effective(
        self,
        *,
        client_id: Optional[UUID],
        account_id: Optional[UUID],
        period_start: date,
        period_end: date,
    ) -> Optional[BudgetOut]:
        if not client_id:
            return None
        candidates = [
            b
            for b in self.items.values()
            if b.status == "active" and b.client_id == client_id and b.end_date >= period_start and b.start_date <= period_end
        ]
        if account_id:
            account_rows = [b for b in candidates if b.scope == "account" and b.account_id == account_id]
            account_rows.sort(key=lambda x: (x.version, x.updated_at), reverse=True)
            if account_rows:
                return account_rows[0]
        client_rows = [b for b in candidates if b.scope == "client" and b.account_id is None]
        client_rows.sort(key=lambda x: (x.version, x.updated_at), reverse=True)
        return client_rows[0] if client_rows else None


@dataclass
class FinancialMetrics:
    budget: Optional[Decimal]
    spend: Decimal
    remaining: Optional[Decimal]
    usage_percent: Optional[Decimal]
    expected_spend_to_date: Optional[Decimal]
    forecast_spend: Optional[Decimal]
    pace_status: Optional[str]
    pace_delta: Optional[Decimal]
    pace_delta_percent: Optional[Decimal]
    date_policy: str


def calculate_financial_metrics(
    *,
    spend: Decimal,
    budget: Optional[Decimal],
    period_start: date,
    period_end: date,
    as_of_date: date,
) -> FinancialMetrics:
    spend_q = _q_money(spend)
    policy = "UTC calendar dates, inclusive period day-count (start/end included)."

    if budget is None:
        return FinancialMetrics(
            budget=None,
            spend=spend_q,
            remaining=None,
            usage_percent=None,
            expected_spend_to_date=None,
            forecast_spend=None,
            pace_status=None,
            pace_delta=None,
            pace_delta_percent=None,
            date_policy=policy,
        )

    budget_q = _q_money(budget)
    total_days = Decimal(max((period_end - period_start).days + 1, 1))
    clamped = min(max(as_of_date, period_start), period_end)
    elapsed_days = Decimal((clamped - period_start).days + 1)
    ratio = (elapsed_days / total_days) if total_days > 0 else Decimal("0")

    expected = _q_money(budget_q * ratio)
    forecast = _q_money(spend_q / ratio) if ratio > 0 else None
    remaining = _q_money(budget_q - spend_q)
    usage_percent = None if budget_q == 0 else _q_percent((spend_q / budget_q) * Decimal("100"))

    pace_delta = _q_money(spend_q - expected)
    pace_delta_percent = None
    if expected > 0:
        pace_delta_percent = _q_percent((pace_delta / expected) * Decimal("100"))

    if budget_q == 0:
        pace = "overspending" if spend_q > 0 else "on_track"
    elif expected <= 0:
        pace = "overspending" if spend_q > 0 else "on_track"
    elif spend_q > expected * Decimal("1.10"):
        pace = "overspending"
    elif spend_q < expected * Decimal("0.90"):
        pace = "underspending"
    else:
        pace = "on_track"

    return FinancialMetrics(
        budget=budget_q,
        spend=spend_q,
        remaining=remaining,
        usage_percent=usage_percent,
        expected_spend_to_date=expected,
        forecast_spend=forecast,
        pace_status=pace,
        pace_delta=pace_delta,
        pace_delta_percent=pace_delta_percent,
        date_policy=policy,
    )


def utc_today_date() -> date:
    return datetime.now(timezone.utc).date()
