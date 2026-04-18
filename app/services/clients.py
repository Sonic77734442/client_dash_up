from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import ClientCreate, ClientOut, ClientPatch


class ClientStore(Protocol):
    def create(self, payload: ClientCreate) -> ClientOut: ...
    def list(self, *, status: Optional[str] = None) -> List[ClientOut]: ...
    def get(self, client_id: UUID) -> Optional[ClientOut]: ...
    def patch(self, client_id: UUID, payload: ClientPatch) -> ClientOut: ...
    def archive(self, client_id: UUID) -> ClientOut: ...


class SqliteClientStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_client(row) -> ClientOut:
        return ClientOut(
            id=UUID(row["id"]),
            name=row["name"],
            legal_name=row["legal_name"],
            status=row["status"],
            default_currency=row["default_currency"],
            timezone=row["timezone"],
            notes=row["notes"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create(self, payload: ClientCreate) -> ClientOut:
        now = datetime.utcnow().isoformat()
        client_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO clients (id, name, legal_name, status, default_currency, timezone, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    payload.name,
                    payload.legal_name,
                    payload.status,
                    payload.default_currency,
                    payload.timezone,
                    payload.notes,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        return self._to_client(row)

    def list(self, *, status: Optional[str] = None) -> List[ClientOut]:
        effective_status = status or "active"
        where = "WHERE status=?" if effective_status != "all" else ""
        params = (effective_status,) if effective_status != "all" else ()
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(f"SELECT * FROM clients {where} ORDER BY updated_at DESC", params).fetchall()
        return [self._to_client(r) for r in rows]

    def get(self, client_id: UUID) -> Optional[ClientOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM clients WHERE id=?", (str(client_id),)).fetchone()
        return self._to_client(row) if row else None

    def patch(self, client_id: UUID, payload: ClientPatch) -> ClientOut:
        existing = self.get(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return existing
        data = {**existing.model_dump(), **patch}
        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute(
                """
                UPDATE clients
                SET name=?, legal_name=?, status=?, default_currency=?, timezone=?, notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["name"],
                    data["legal_name"],
                    data["status"],
                    data["default_currency"],
                    data["timezone"],
                    data["notes"],
                    now,
                    str(client_id),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM clients WHERE id=?", (str(client_id),)).fetchone()
        return self._to_client(row)

    def archive(self, client_id: UUID) -> ClientOut:
        existing = self.get(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute("UPDATE clients SET status='archived', updated_at=? WHERE id=?", (now, str(client_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM clients WHERE id=?", (str(client_id),)).fetchone()
        return self._to_client(row)


class InMemoryClientStore:
    def __init__(self):
        self.items = {}

    def create(self, payload: ClientCreate) -> ClientOut:
        now = datetime.utcnow()
        rec = ClientOut(
            id=uuid4(),
            name=payload.name,
            legal_name=payload.legal_name,
            status=payload.status,
            default_currency=payload.default_currency,
            timezone=payload.timezone,
            notes=payload.notes,
            created_at=now,
            updated_at=now,
        )
        self.items[rec.id] = rec
        return rec

    def list(self, *, status: Optional[str] = None) -> List[ClientOut]:
        effective_status = status or "active"
        rows = list(self.items.values())
        if effective_status != "all":
            rows = [x for x in rows if x.status == effective_status]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def get(self, client_id: UUID) -> Optional[ClientOut]:
        return self.items.get(client_id)

    def patch(self, client_id: UUID, payload: ClientPatch) -> ClientOut:
        existing = self.get(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        update = payload.model_dump(exclude_unset=True)
        if not update:
            return existing
        rec = existing.model_copy(update={**update, "updated_at": datetime.utcnow()})
        self.items[client_id] = rec
        return rec

    def archive(self, client_id: UUID) -> ClientOut:
        existing = self.get(client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        rec = existing.model_copy(update={"status": "archived", "updated_at": datetime.utcnow()})
        self.items[client_id] = rec
        return rec
