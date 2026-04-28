from __future__ import annotations

import json
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from app.db import init_sqlite, sqlite_conn
from app.schemas import AuditLogOut


class AuditLogStore(Protocol):
    def create(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        actor_role: Optional[str] = None,
        tenant_client_id: Optional[UUID] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditLogOut: ...

    def list(
        self,
        *,
        event_type: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        tenant_client_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[AuditLogOut]: ...


class SqliteAuditLogStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_row(row) -> AuditLogOut:
        return AuditLogOut(
            id=int(row["id"]),
            event_type=row["event_type"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            actor_user_id=UUID(row["actor_user_id"]) if row["actor_user_id"] else None,
            actor_role=row["actor_role"],
            tenant_client_id=UUID(row["tenant_client_id"]) if row["tenant_client_id"] else None,
            payload=json.loads(row["payload"]) if row["payload"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        actor_role: Optional[str] = None,
        tenant_client_id: Optional[UUID] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditLogOut:
        now = _utcnow().isoformat()
        body = payload or {}
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_logs
                (event_type, resource_type, resource_id, actor_user_id, actor_role, tenant_client_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    resource_type,
                    resource_id,
                    str(actor_user_id) if actor_user_id else None,
                    actor_role,
                    str(tenant_client_id) if tenant_client_id else None,
                    json.dumps(body, separators=(",", ":"), ensure_ascii=True),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM audit_logs WHERE id = last_insert_rowid()").fetchone()
        return self._to_row(row)

    def list(
        self,
        *,
        event_type: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        tenant_client_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[AuditLogOut]:
        where = ["1=1"]
        params: List[object] = []
        if event_type:
            where.append("event_type=?")
            params.append(event_type)
        if actor_user_id:
            where.append("actor_user_id=?")
            params.append(str(actor_user_id))
        if tenant_client_id:
            where.append("tenant_client_id=?")
            params.append(str(tenant_client_id))
        params.append(max(1, min(limit, 500)))
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_logs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._to_row(r) for r in rows]


class InMemoryAuditLogStore:
    def __init__(self):
        self.items: List[AuditLogOut] = []
        self._next_id = 1

    def create(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        actor_role: Optional[str] = None,
        tenant_client_id: Optional[UUID] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> AuditLogOut:
        row = AuditLogOut(
            id=self._next_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            tenant_client_id=tenant_client_id,
            payload=dict(payload or {}),
            created_at=_utcnow(),
        )
        self._next_id += 1
        self.items.insert(0, row)
        return row

    def list(
        self,
        *,
        event_type: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        tenant_client_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[AuditLogOut]:
        rows = list(self.items)
        if event_type:
            rows = [x for x in rows if x.event_type == event_type]
        if actor_user_id:
            rows = [x for x in rows if x.actor_user_id == actor_user_id]
        if tenant_client_id:
            rows = [x for x in rows if x.tenant_client_id == tenant_client_id]
        return rows[: max(1, min(limit, 500))]


