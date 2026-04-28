from __future__ import annotations

import json
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import List, Optional, Protocol
from uuid import UUID, uuid4

from app.db import init_sqlite, sqlite_conn
from app.schemas import OperationalActionExecuteRequest, OperationalActionOut


class OperationalActionStore(Protocol):
    def create(self, payload: OperationalActionExecuteRequest, *, created_by: Optional[UUID] = None) -> OperationalActionOut: ...
    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        scope: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[OperationalActionOut]: ...


class SqliteOperationalActionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_row(row) -> OperationalActionOut:
        return OperationalActionOut(
            id=UUID(row["id"]),
            action=row["action"],
            scope=row["scope"],
            scope_id=row["scope_id"],
            title=row["title"],
            reason=row["reason"],
            metrics=json.loads(row["metrics"]) if row["metrics"] else {},
            client_id=UUID(row["client_id"]) if row["client_id"] else None,
            account_id=UUID(row["account_id"]) if row["account_id"] else None,
            status=row["status"],
            created_by=UUID(row["created_by"]) if row["created_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create(self, payload: OperationalActionExecuteRequest, *, created_by: Optional[UUID] = None) -> OperationalActionOut:
        rec_id = str(uuid4())
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO operational_actions
                (id, action, scope, scope_id, title, reason, metrics, client_id, account_id, status, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    rec_id,
                    payload.action,
                    payload.scope,
                    payload.scope_id,
                    payload.title,
                    payload.reason,
                    json.dumps(payload.metrics, separators=(",", ":"), ensure_ascii=True),
                    str(payload.client_id) if payload.client_id else None,
                    str(payload.account_id) if payload.account_id else None,
                    str(created_by) if created_by else None,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM operational_actions WHERE id=?", (rec_id,)).fetchone()
        return self._to_row(row)

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        scope: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[OperationalActionOut]:
        where = ["1=1"]
        params: List[object] = []
        if client_id:
            where.append("client_id=?")
            params.append(str(client_id))
        if account_id:
            where.append("account_id=?")
            params.append(str(account_id))
        if scope:
            where.append("scope=?")
            params.append(scope)
        if status:
            where.append("status=?")
            params.append(status)
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM operational_actions WHERE {' AND '.join(where)} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [self._to_row(r) for r in rows]


class InMemoryOperationalActionStore:
    def __init__(self):
        self.items: List[OperationalActionOut] = []

    def create(self, payload: OperationalActionExecuteRequest, *, created_by: Optional[UUID] = None) -> OperationalActionOut:
        rec = OperationalActionOut(
            id=uuid4(),
            action=payload.action,
            scope=payload.scope,
            scope_id=payload.scope_id,
            title=payload.title,
            reason=payload.reason,
            metrics=dict(payload.metrics or {}),
            client_id=payload.client_id,
            account_id=payload.account_id,
            status="queued",
            created_by=created_by,
            created_at=_utcnow(),
        )
        self.items.insert(0, rec)
        return rec

    def list(
        self,
        *,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        scope: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[OperationalActionOut]:
        rows = list(self.items)
        if client_id:
            rows = [x for x in rows if x.client_id == client_id]
        if account_id:
            rows = [x for x in rows if x.account_id == account_id]
        if scope:
            rows = [x for x in rows if x.scope == scope]
        if status:
            rows = [x for x in rows if x.status == status]
        return rows


