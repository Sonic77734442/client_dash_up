from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import AlertOut


@dataclass
class AlertSignal:
    code: str
    severity: str
    title: str
    message: str
    fingerprint: str
    provider: Optional[str] = None
    client_id: Optional[UUID] = None
    ad_account_id: Optional[UUID] = None
    context: Optional[Dict[str, object]] = None


class AlertStore(Protocol):
    def raise_alert(self, signal: AlertSignal) -> AlertOut: ...
    def resolve_by_fingerprint(self, fingerprint: str) -> Optional[AlertOut]: ...
    def acknowledge(self, alert_id: UUID, *, by_user_id: Optional[UUID]) -> AlertOut: ...
    def list(
        self,
        *,
        status: str = "open",
        severity: Optional[str] = None,
        provider: Optional[str] = None,
        client_id: Optional[UUID] = None,
        limit: int = 200,
    ) -> List[AlertOut]: ...
    def get(self, alert_id: UUID) -> Optional[AlertOut]: ...


class SqliteAlertStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        init_sqlite(db_path)

    @staticmethod
    def _to_out(row) -> AlertOut:
        return AlertOut(
            id=UUID(row["id"]),
            code=row["code"],
            severity=row["severity"],
            status=row["status"],
            title=row["title"],
            message=row["message"],
            fingerprint=row["fingerprint"],
            provider=row["provider"],
            client_id=UUID(row["client_id"]) if row["client_id"] else None,
            ad_account_id=UUID(row["ad_account_id"]) if row["ad_account_id"] else None,
            context=json.loads(row["context_json"]) if row["context_json"] else {},
            occurrences=int(row["occurrences"] or 0),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            acknowledged_at=datetime.fromisoformat(row["acknowledged_at"]) if row["acknowledged_at"] else None,
            acknowledged_by=UUID(row["acknowledged_by"]) if row["acknowledged_by"] else None,
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )

    def raise_alert(self, signal: AlertSignal) -> AlertOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM alerts WHERE fingerprint=?", (signal.fingerprint,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE alerts
                    SET code=?,
                        severity=?,
                        status='open',
                        title=?,
                        message=?,
                        provider=?,
                        client_id=?,
                        ad_account_id=?,
                        context_json=?,
                        occurrences=occurrences+1,
                        last_seen_at=?,
                        acknowledged_at=NULL,
                        acknowledged_by=NULL,
                        resolved_at=NULL
                    WHERE id=?
                    """,
                    (
                        signal.code,
                        signal.severity,
                        signal.title,
                        signal.message,
                        signal.provider,
                        str(signal.client_id) if signal.client_id else None,
                        str(signal.ad_account_id) if signal.ad_account_id else None,
                        json.dumps(signal.context or {}, separators=(",", ":"), ensure_ascii=True),
                        now,
                        existing["id"],
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM alerts WHERE id=?", (existing["id"],)).fetchone()
                return self._to_out(row)

            alert_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO alerts
                (id, code, severity, status, title, message, fingerprint, provider, client_id, ad_account_id,
                 context_json, occurrences, first_seen_at, last_seen_at, acknowledged_at, acknowledged_by, resolved_at)
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, NULL, NULL)
                """,
                (
                    alert_id,
                    signal.code,
                    signal.severity,
                    signal.title,
                    signal.message,
                    signal.fingerprint,
                    signal.provider,
                    str(signal.client_id) if signal.client_id else None,
                    str(signal.ad_account_id) if signal.ad_account_id else None,
                    json.dumps(signal.context or {}, separators=(",", ":"), ensure_ascii=True),
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
            return self._to_out(row)

    def resolve_by_fingerprint(self, fingerprint: str) -> Optional[AlertOut]:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM alerts WHERE fingerprint=?", (fingerprint,)).fetchone()
            if not existing:
                return None
            if existing["status"] == "resolved":
                return self._to_out(existing)
            conn.execute(
                "UPDATE alerts SET status='resolved', resolved_at=?, last_seen_at=? WHERE id=?",
                (now, now, existing["id"]),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (existing["id"],)).fetchone()
            return self._to_out(row)

    def acknowledge(self, alert_id: UUID, *, by_user_id: Optional[UUID]) -> AlertOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM alerts WHERE id=?", (str(alert_id),)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Alert not found")
            conn.execute(
                "UPDATE alerts SET status='acked', acknowledged_at=?, acknowledged_by=?, last_seen_at=? WHERE id=?",
                (now, str(by_user_id) if by_user_id else None, now, str(alert_id)),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (str(alert_id),)).fetchone()
            return self._to_out(row)

    def list(
        self,
        *,
        status: str = "open",
        severity: Optional[str] = None,
        provider: Optional[str] = None,
        client_id: Optional[UUID] = None,
        limit: int = 200,
    ) -> List[AlertOut]:
        where = ["1=1"]
        params: List[object] = []
        if status != "all":
            where.append("status=?")
            params.append(status)
        if severity:
            where.append("severity=?")
            params.append(severity)
        if provider:
            where.append("provider=?")
            params.append(provider.lower().strip())
        if client_id:
            where.append("client_id=?")
            params.append(str(client_id))
        params.append(max(1, min(limit, 500)))
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM alerts WHERE {' AND '.join(where)} ORDER BY last_seen_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._to_out(x) for x in rows]

    def get(self, alert_id: UUID) -> Optional[AlertOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM alerts WHERE id=?", (str(alert_id),)).fetchone()
        return self._to_out(row) if row else None


class InMemoryAlertStore:
    def __init__(self):
        self.items: Dict[UUID, AlertOut] = {}
        self.index: Dict[str, UUID] = {}

    def raise_alert(self, signal: AlertSignal) -> AlertOut:
        now = _utcnow()
        existing_id = self.index.get(signal.fingerprint)
        if existing_id and existing_id in self.items:
            row = self.items[existing_id]
            updated = row.model_copy(
                update={
                    "code": signal.code,
                    "severity": signal.severity,
                    "status": "open",
                    "title": signal.title,
                    "message": signal.message,
                    "provider": signal.provider,
                    "client_id": signal.client_id,
                    "ad_account_id": signal.ad_account_id,
                    "context": signal.context or {},
                    "occurrences": row.occurrences + 1,
                    "last_seen_at": now,
                    "acknowledged_at": None,
                    "acknowledged_by": None,
                    "resolved_at": None,
                }
            )
            self.items[updated.id] = updated
            return updated
        row = AlertOut(
            id=uuid4(),
            code=signal.code,
            severity=signal.severity,  # type: ignore[arg-type]
            status="open",
            title=signal.title,
            message=signal.message,
            fingerprint=signal.fingerprint,
            provider=signal.provider,
            client_id=signal.client_id,
            ad_account_id=signal.ad_account_id,
            context=signal.context or {},
            occurrences=1,
            first_seen_at=now,
            last_seen_at=now,
            acknowledged_at=None,
            acknowledged_by=None,
            resolved_at=None,
        )
        self.items[row.id] = row
        self.index[row.fingerprint] = row.id
        return row

    def resolve_by_fingerprint(self, fingerprint: str) -> Optional[AlertOut]:
        alert_id = self.index.get(fingerprint)
        if not alert_id:
            return None
        row = self.items.get(alert_id)
        if not row:
            return None
        if row.status == "resolved":
            return row
        updated = row.model_copy(update={"status": "resolved", "resolved_at": _utcnow(), "last_seen_at": _utcnow()})
        self.items[alert_id] = updated
        return updated

    def acknowledge(self, alert_id: UUID, *, by_user_id: Optional[UUID]) -> AlertOut:
        row = self.items.get(alert_id)
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        updated = row.model_copy(
            update={"status": "acked", "acknowledged_at": _utcnow(), "acknowledged_by": by_user_id, "last_seen_at": _utcnow()}
        )
        self.items[alert_id] = updated
        return updated

    def list(
        self,
        *,
        status: str = "open",
        severity: Optional[str] = None,
        provider: Optional[str] = None,
        client_id: Optional[UUID] = None,
        limit: int = 200,
    ) -> List[AlertOut]:
        rows = list(self.items.values())
        if status != "all":
            rows = [x for x in rows if x.status == status]
        if severity:
            rows = [x for x in rows if x.severity == severity]
        if provider:
            p = provider.lower().strip()
            rows = [x for x in rows if (x.provider or "").lower().strip() == p]
        if client_id:
            rows = [x for x in rows if x.client_id == client_id]
        rows.sort(key=lambda x: x.last_seen_at, reverse=True)
        return rows[: max(1, min(limit, 500))]

    def get(self, alert_id: UUID) -> Optional[AlertOut]:
        return self.items.get(alert_id)


