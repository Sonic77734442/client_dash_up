from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import IntegrationCredentialCreate, IntegrationCredentialOut, IntegrationCredentialPatch


def _normalize_provider(value: str) -> str:
    p = (value or "").strip().lower()
    if p == "facebook":
        return "meta"
    return p


class IntegrationCredentialStore(Protocol):
    def upsert(self, payload: IntegrationCredentialCreate) -> IntegrationCredentialOut: ...
    def list(
        self,
        *,
        status: str = "active",
        provider: Optional[str] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]: ...
    def patch(self, credential_id: UUID, payload: IntegrationCredentialPatch) -> IntegrationCredentialOut: ...
    def archive(self, credential_id: UUID) -> IntegrationCredentialOut: ...
    def resolve_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[IntegrationCredentialOut]: ...


class SqliteIntegrationCredentialStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_out(row) -> IntegrationCredentialOut:
        return IntegrationCredentialOut(
            id=UUID(row["id"]),
            provider=row["provider"],
            scope_type=row["scope_type"],
            scope_id=UUID(row["scope_id"]) if row["scope_id"] else None,
            credentials=json.loads(row["credentials_json"]) if row["credentials_json"] else {},
            status=row["status"],
            created_by=UUID(row["created_by"]) if row["created_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def upsert(self, payload: IntegrationCredentialCreate) -> IntegrationCredentialOut:
        now = datetime.utcnow().isoformat()
        cred_id = str(uuid4())
        provider = _normalize_provider(payload.provider)
        scope_id = str(payload.scope_id) if payload.scope_id else None
        if payload.scope_type == "global" and scope_id is not None:
            raise HTTPException(status_code=400, detail="scope_id must be null for global scope")
        if payload.scope_type in {"agency", "client"} and scope_id is None:
            raise HTTPException(status_code=400, detail="scope_id is required for agency/client scope")
        with sqlite_conn(self.db_path) as conn:
            if scope_id is None:
                existing = conn.execute(
                    """
                    SELECT * FROM integration_credentials
                    WHERE provider=? AND scope_type=? AND scope_id IS NULL
                    """,
                    (provider, payload.scope_type),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT * FROM integration_credentials
                    WHERE provider=? AND scope_type=? AND scope_id=?
                    """,
                    (provider, payload.scope_type, scope_id),
                ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE integration_credentials
                    SET credentials_json=?, status='active', created_by=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        json.dumps(payload.credentials, separators=(",", ":"), ensure_ascii=True),
                        str(payload.created_by) if payload.created_by else existing["created_by"],
                        now,
                        existing["id"],
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (existing["id"],)).fetchone()
                return self._to_out(row)

            conn.execute(
                """
                INSERT INTO integration_credentials
                (id, provider, scope_type, scope_id, credentials_json, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    cred_id,
                    provider,
                    payload.scope_type,
                    scope_id,
                    json.dumps(payload.credentials, separators=(",", ":"), ensure_ascii=True),
                    str(payload.created_by) if payload.created_by else None,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (cred_id,)).fetchone()
        return self._to_out(row)

    def list(
        self,
        *,
        status: str = "active",
        provider: Optional[str] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]:
        where = ["1=1"]
        params: List[object] = []
        if status != "all":
            where.append("status=?")
            params.append(status)
        if provider:
            where.append("provider=?")
            params.append(_normalize_provider(provider))
        if scope_type:
            where.append("scope_type=?")
            params.append(scope_type)
        if scope_id:
            where.append("scope_id=?")
            params.append(str(scope_id))
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM integration_credentials WHERE {' AND '.join(where)} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._to_out(r) for r in rows]

    def patch(self, credential_id: UUID, payload: IntegrationCredentialPatch) -> IntegrationCredentialOut:
        patch = payload.model_dump(exclude_unset=True)
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Integration credential not found")
            if not patch:
                return self._to_out(existing)
            now = datetime.utcnow().isoformat()
            data = {
                "provider": _normalize_provider(patch.get("provider", existing["provider"])),
                "scope_type": patch.get("scope_type", existing["scope_type"]),
                "scope_id": str(patch["scope_id"]) if "scope_id" in patch and patch["scope_id"] else existing["scope_id"],
                "credentials_json": json.dumps(
                    patch.get("credentials", json.loads(existing["credentials_json"] or "{}")),
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                "status": patch.get("status", existing["status"]),
                "created_by": str(patch["created_by"]) if patch.get("created_by") else existing["created_by"],
                "updated_at": now,
            }
            if data["scope_type"] == "global" and data["scope_id"] is not None:
                raise HTTPException(status_code=400, detail="scope_id must be null for global scope")
            if data["scope_type"] in {"agency", "client"} and data["scope_id"] is None:
                raise HTTPException(status_code=400, detail="scope_id is required for agency/client scope")
            conn.execute(
                """
                UPDATE integration_credentials
                SET provider=?, scope_type=?, scope_id=?, credentials_json=?, status=?, created_by=?, updated_at=?
                WHERE id=?
                """,
                (
                    data["provider"],
                    data["scope_type"],
                    data["scope_id"],
                    data["credentials_json"],
                    data["status"],
                    data["created_by"],
                    data["updated_at"],
                    str(credential_id),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
        return self._to_out(row)

    def archive(self, credential_id: UUID) -> IntegrationCredentialOut:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration credential not found")
            now = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE integration_credentials SET status='archived', updated_at=? WHERE id=?",
                (now, str(credential_id)),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
        return self._to_out(updated)

    def resolve_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[IntegrationCredentialOut]:
        provider_norm = _normalize_provider(provider)
        with sqlite_conn(self.db_path) as conn:
            # 1) client-scoped credential.
            client_row = conn.execute(
                """
                SELECT * FROM integration_credentials
                WHERE provider=? AND scope_type='client' AND scope_id=? AND status='active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (provider_norm, str(client_id)),
            ).fetchone()
            if client_row:
                return self._to_out(client_row)

            # 2) agency-scoped credential for an agency that has this client.
            if user_id:
                agency_row = conn.execute(
                    """
                    SELECT ic.*
                    FROM integration_credentials ic
                    JOIN agency_client_access aca ON aca.agency_id = ic.scope_id
                    JOIN agencies a ON a.id = aca.agency_id
                    JOIN agency_members am ON am.agency_id = a.id
                    JOIN users u ON u.id = am.user_id
                    WHERE ic.provider=?
                      AND ic.scope_type='agency'
                      AND ic.status='active'
                      AND aca.client_id=?
                      AND a.status='active'
                      AND am.user_id=?
                      AND am.status='active'
                      AND u.status='active'
                    ORDER BY ic.updated_at DESC
                    LIMIT 1
                    """,
                    (provider_norm, str(client_id), str(user_id)),
                ).fetchone()
            else:
                agency_row = conn.execute(
                    """
                    SELECT ic.*
                    FROM integration_credentials ic
                    JOIN agency_client_access aca ON aca.agency_id = ic.scope_id
                    JOIN agencies a ON a.id = aca.agency_id
                    WHERE ic.provider=?
                      AND ic.scope_type='agency'
                      AND ic.status='active'
                      AND aca.client_id=?
                      AND a.status='active'
                    ORDER BY ic.updated_at DESC
                    LIMIT 1
                    """,
                    (provider_norm, str(client_id)),
                ).fetchone()
            if agency_row:
                return self._to_out(agency_row)

            # 3) global credential.
            global_row = conn.execute(
                """
                SELECT * FROM integration_credentials
                WHERE provider=? AND scope_type='global' AND status='active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (provider_norm,),
            ).fetchone()
            if global_row:
                return self._to_out(global_row)
        return None


class InMemoryIntegrationCredentialStore:
    def __init__(self):
        self.items: Dict[UUID, IntegrationCredentialOut] = {}

    def upsert(self, payload: IntegrationCredentialCreate) -> IntegrationCredentialOut:
        provider = _normalize_provider(payload.provider)
        for row in self.items.values():
            if row.provider == provider and row.scope_type == payload.scope_type and row.scope_id == payload.scope_id:
                updated = IntegrationCredentialOut(
                    id=row.id,
                    provider=provider,
                    scope_type=payload.scope_type,
                    scope_id=payload.scope_id,
                    credentials=payload.credentials,
                    status="active",
                    created_by=payload.created_by or row.created_by,
                    created_at=row.created_at,
                    updated_at=datetime.utcnow(),
                )
                self.items[row.id] = updated
                return updated
        now = datetime.utcnow()
        created = IntegrationCredentialOut(
            id=uuid4(),
            provider=provider,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            credentials=payload.credentials,
            status="active",
            created_by=payload.created_by,
            created_at=now,
            updated_at=now,
        )
        self.items[created.id] = created
        return created

    def list(
        self,
        *,
        status: str = "active",
        provider: Optional[str] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]:
        rows = list(self.items.values())
        if status != "all":
            rows = [r for r in rows if r.status == status]
        if provider:
            p = _normalize_provider(provider)
            rows = [r for r in rows if r.provider == p]
        if scope_type:
            rows = [r for r in rows if r.scope_type == scope_type]
        if scope_id:
            rows = [r for r in rows if r.scope_id == scope_id]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def patch(self, credential_id: UUID, payload: IntegrationCredentialPatch) -> IntegrationCredentialOut:
        existing = self.items.get(credential_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Integration credential not found")
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return existing
        updated = IntegrationCredentialOut(
            id=existing.id,
            provider=_normalize_provider(patch.get("provider", existing.provider)),
            scope_type=patch.get("scope_type", existing.scope_type),
            scope_id=patch.get("scope_id", existing.scope_id),
            credentials=patch.get("credentials", existing.credentials),
            status=patch.get("status", existing.status),
            created_by=patch.get("created_by", existing.created_by),
            created_at=existing.created_at,
            updated_at=datetime.utcnow(),
        )
        self.items[credential_id] = updated
        return updated

    def archive(self, credential_id: UUID) -> IntegrationCredentialOut:
        return self.patch(credential_id, IntegrationCredentialPatch(status="archived"))

    def resolve_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[IntegrationCredentialOut]:
        p = _normalize_provider(provider)
        rows = [x for x in self.items.values() if x.status == "active" and x.provider == p]
        client_rows = [x for x in rows if x.scope_type == "client" and x.scope_id == client_id]
        if client_rows:
            client_rows.sort(key=lambda x: x.updated_at, reverse=True)
            return client_rows[0]
        agency_rows = [x for x in rows if x.scope_type == "agency"]
        if agency_rows:
            agency_rows.sort(key=lambda x: x.updated_at, reverse=True)
            return agency_rows[0]
        global_rows = [x for x in rows if x.scope_type == "global"]
        if global_rows:
            global_rows.sort(key=lambda x: x.updated_at, reverse=True)
            return global_rows[0]
        return None
