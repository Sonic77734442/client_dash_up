from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import AdAccountCreate, AdAccountOut, AdAccountPatch
from app.services.clients import ClientStore


class AdAccountStore(Protocol):
    def create(self, payload: AdAccountCreate) -> AdAccountOut: ...
    def list(self, *, client_id: Optional[UUID] = None, status: Optional[str] = None) -> List[AdAccountOut]: ...
    def get(self, account_id: UUID) -> Optional[AdAccountOut]: ...
    def patch(self, account_id: UUID, payload: AdAccountPatch) -> AdAccountOut: ...
    def archive(self, account_id: UUID) -> AdAccountOut: ...


class SqliteAdAccountStore:
    def __init__(self, db_path: str, client_store: ClientStore):
        self.db_path = db_path
        self.client_store = client_store
        init_sqlite(db_path)

    @staticmethod
    def _to_account(row) -> AdAccountOut:
        metadata = json.loads(row["metadata"]) if row["metadata"] else None
        last_sync_at_raw = metadata.get("last_sync_at") if isinstance(metadata, dict) else None
        sync_status_raw = metadata.get("sync_status") if isinstance(metadata, dict) else None
        sync_error_raw = metadata.get("sync_error") if isinstance(metadata, dict) else None
        sync_error_code_raw = metadata.get("sync_error_code") if isinstance(metadata, dict) else None
        sync_error_category_raw = metadata.get("sync_error_category") if isinstance(metadata, dict) else None
        sync_retryable_raw = metadata.get("sync_retryable") if isinstance(metadata, dict) else None
        sync_next_retry_at_raw = metadata.get("sync_next_retry_at") if isinstance(metadata, dict) else None
        return AdAccountOut(
            id=UUID(row["id"]),
            client_id=UUID(row["client_id"]),
            platform=row["platform"],
            external_account_id=row["external_account_id"],
            name=row["name"],
            currency=row["currency"],
            timezone=row["timezone"],
            status=row["status"],
            metadata=metadata,
            last_sync_at=datetime.fromisoformat(last_sync_at_raw) if isinstance(last_sync_at_raw, str) else None,
            sync_status=sync_status_raw if sync_status_raw in {"success", "error"} else None,
            sync_error=str(sync_error_raw) if isinstance(sync_error_raw, str) else None,
            sync_error_code=str(sync_error_code_raw) if isinstance(sync_error_code_raw, str) else None,
            sync_error_category=str(sync_error_category_raw) if isinstance(sync_error_category_raw, str) else None,
            sync_retryable=bool(sync_retryable_raw) if isinstance(sync_retryable_raw, bool) else None,
            sync_next_retry_at=(
                datetime.fromisoformat(sync_next_retry_at_raw) if isinstance(sync_next_retry_at_raw, str) else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _assert_client_exists(self, client_id: UUID) -> None:
        if not self.client_store.get(client_id):
            raise HTTPException(status_code=400, detail="client_id does not exist")

    def create(self, payload: AdAccountCreate) -> AdAccountOut:
        self._assert_client_exists(payload.client_id)
        now = datetime.utcnow().isoformat()
        account_id = str(uuid4())
        with sqlite_conn(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO ad_accounts
                    (id, client_id, platform, external_account_id, name, currency, timezone, status, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        str(payload.client_id),
                        payload.platform,
                        payload.external_account_id,
                        payload.name,
                        payload.currency,
                        payload.timezone,
                        payload.status,
                        json.dumps(payload.metadata, separators=(",", ":"), ensure_ascii=True) if payload.metadata else None,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Account conflict: {exc}")
            row = conn.execute("SELECT * FROM ad_accounts WHERE id=?", (account_id,)).fetchone()
        return self._to_account(row)

    def list(self, *, client_id: Optional[UUID] = None, status: Optional[str] = None) -> List[AdAccountOut]:
        where = ["1=1"]
        params: List[object] = []
        if client_id:
            where.append("client_id=?")
            params.append(str(client_id))
        effective_status = status or "active"
        if effective_status != "all":
            where.append("status=?")
            params.append(effective_status)

        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(f"SELECT * FROM ad_accounts WHERE {' AND '.join(where)} ORDER BY updated_at DESC", params).fetchall()
        return [self._to_account(r) for r in rows]

    def get(self, account_id: UUID) -> Optional[AdAccountOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM ad_accounts WHERE id=?", (str(account_id),)).fetchone()
        return self._to_account(row) if row else None

    def patch(self, account_id: UUID, payload: AdAccountPatch) -> AdAccountOut:
        existing = self.get(account_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Ad account not found")

        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return existing
        data = {**existing.model_dump(), **patch}

        if data.get("client_id"):
            self._assert_client_exists(data["client_id"])

        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    UPDATE ad_accounts
                    SET client_id=?, platform=?, external_account_id=?, name=?, currency=?, timezone=?, status=?, metadata=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        str(data["client_id"]),
                        data["platform"],
                        data["external_account_id"],
                        data["name"],
                        data["currency"],
                        data["timezone"],
                        data["status"],
                        json.dumps(data["metadata"], separators=(",", ":"), ensure_ascii=True) if data.get("metadata") else None,
                        now,
                        str(account_id),
                    ),
                )
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Account conflict: {exc}")
            row = conn.execute("SELECT * FROM ad_accounts WHERE id=?", (str(account_id),)).fetchone()
        return self._to_account(row)

    def archive(self, account_id: UUID) -> AdAccountOut:
        existing = self.get(account_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Ad account not found")
        now = datetime.utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute("UPDATE ad_accounts SET status='archived', updated_at=? WHERE id=?", (now, str(account_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM ad_accounts WHERE id=?", (str(account_id),)).fetchone()
        return self._to_account(row)


class InMemoryAdAccountStore:
    def __init__(self, client_store: ClientStore):
        self.client_store = client_store
        self.items = {}

    def _assert_client_exists(self, client_id: UUID) -> None:
        if not self.client_store.get(client_id):
            raise HTTPException(status_code=400, detail="client_id does not exist")

    def _assert_unique(self, platform: str, external: str, exclude: Optional[UUID] = None) -> None:
        for item in self.items.values():
            if exclude and item.id == exclude:
                continue
            if item.platform == platform and item.external_account_id == external:
                raise HTTPException(status_code=409, detail="Account conflict: duplicate platform+external_account_id")

    @staticmethod
    def _sync_fields_from_metadata(metadata: Optional[dict]) -> dict:
        if not isinstance(metadata, dict):
            return {
                "last_sync_at": None,
                "sync_status": None,
                "sync_error": None,
                "sync_error_code": None,
                "sync_error_category": None,
                "sync_retryable": None,
                "sync_next_retry_at": None,
            }
        last_sync_at_raw = metadata.get("last_sync_at")
        sync_status_raw = metadata.get("sync_status")
        sync_error_raw = metadata.get("sync_error")
        sync_error_code_raw = metadata.get("sync_error_code")
        sync_error_category_raw = metadata.get("sync_error_category")
        sync_retryable_raw = metadata.get("sync_retryable")
        sync_next_retry_at_raw = metadata.get("sync_next_retry_at")
        return {
            "last_sync_at": datetime.fromisoformat(last_sync_at_raw) if isinstance(last_sync_at_raw, str) else None,
            "sync_status": sync_status_raw if sync_status_raw in {"success", "error"} else None,
            "sync_error": str(sync_error_raw) if isinstance(sync_error_raw, str) else None,
            "sync_error_code": str(sync_error_code_raw) if isinstance(sync_error_code_raw, str) else None,
            "sync_error_category": str(sync_error_category_raw) if isinstance(sync_error_category_raw, str) else None,
            "sync_retryable": bool(sync_retryable_raw) if isinstance(sync_retryable_raw, bool) else None,
            "sync_next_retry_at": (
                datetime.fromisoformat(sync_next_retry_at_raw) if isinstance(sync_next_retry_at_raw, str) else None
            ),
        }

    def create(self, payload: AdAccountCreate) -> AdAccountOut:
        self._assert_client_exists(payload.client_id)
        self._assert_unique(payload.platform, payload.external_account_id)
        now = datetime.utcnow()
        rec = AdAccountOut(
            id=uuid4(),
            client_id=payload.client_id,
            platform=payload.platform,
            external_account_id=payload.external_account_id,
            name=payload.name,
            currency=payload.currency,
            timezone=payload.timezone,
            status=payload.status,
            metadata=payload.metadata,
            **self._sync_fields_from_metadata(payload.metadata),
            created_at=now,
            updated_at=now,
        )
        self.items[rec.id] = rec
        return rec

    def list(self, *, client_id: Optional[UUID] = None, status: Optional[str] = None) -> List[AdAccountOut]:
        rows = list(self.items.values())
        if client_id:
            rows = [x for x in rows if x.client_id == client_id]
        effective_status = status or "active"
        if effective_status != "all":
            rows = [x for x in rows if x.status == effective_status]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def get(self, account_id: UUID) -> Optional[AdAccountOut]:
        return self.items.get(account_id)

    def patch(self, account_id: UUID, payload: AdAccountPatch) -> AdAccountOut:
        existing = self.get(account_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Ad account not found")
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return existing
        merged = {**existing.model_dump(), **patch}
        self._assert_client_exists(merged["client_id"])
        self._assert_unique(merged["platform"], merged["external_account_id"], exclude=account_id)
        sync_fields = self._sync_fields_from_metadata(merged.get("metadata"))
        rec = existing.model_copy(update={**patch, **sync_fields, "updated_at": datetime.utcnow()})
        self.items[account_id] = rec
        return rec

    def archive(self, account_id: UUID) -> AdAccountOut:
        existing = self.get(account_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Ad account not found")
        rec = existing.model_copy(update={"status": "archived", "updated_at": datetime.utcnow()})
        self.items[account_id] = rec
        return rec
