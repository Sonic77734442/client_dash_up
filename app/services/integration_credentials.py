from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import IntegrationCredentialCreate, IntegrationCredentialOut, IntegrationCredentialPatch
from app.services.ad_accounts import canonical_external_account_id, normalize_account_platform
from app.services.credential_crypto import (
    CredentialCryptoError,
    CredentialEncryptionUnavailable,
    CredentialKeyring,
    credential_envelope_key_id,
    is_encrypted_credential_envelope,
)


def _normalize_provider(value: str) -> str:
    p = (value or "").strip().lower()
    if p == "facebook":
        return "meta"
    return p


_PUBLIC_META_ID_FIELDS = (
    "meta_app_id",
    "meta_business_config_id",
)
_PUBLIC_META_EXPIRY_FIELDS = (
    "meta_expires_at",
    "meta_data_access_expires_at",
    "meta_absolute_expires_at",
)
_PUBLIC_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_PUBLIC_NUMERIC_ID_RE = re.compile(r"^[0-9]{1,64}$")
_PUBLIC_GOOGLE_ACCOUNT_ID_RE = re.compile(r"^[0-9-]{1,32}$")


def _safe_public_numeric_id(value: object) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text if _PUBLIC_NUMERIC_ID_RE.fullmatch(text) else None


def _safe_public_permission_list(value: object, *, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    safe: list[str] = []
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower()
        if _PUBLIC_PERMISSION_RE.fullmatch(normalized) and normalized not in safe:
            safe.append(normalized)
    return safe


def _safe_public_meta_granular_scopes(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    safe: list[dict[str, object]] = []
    for item in value[:100]:
        if not isinstance(item, Mapping):
            continue
        scope = item.get("scope")
        if not isinstance(scope, str):
            continue
        normalized_scope = scope.strip().lower()
        if not _PUBLIC_PERMISSION_RE.fullmatch(normalized_scope):
            continue
        target_value = item.get("target_ids")
        target_ids: list[str] = []
        if isinstance(target_value, list):
            for target in target_value[:500]:
                safe_target = _safe_public_numeric_id(target)
                if safe_target is not None and safe_target not in target_ids:
                    target_ids.append(safe_target)
        safe.append({"scope": normalized_scope, "target_ids": target_ids})
    return safe


def _safe_public_validated_at(value: object) -> Optional[str]:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def public_credential_diagnostics(credentials: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return a strict allowlist of non-secret connection diagnostics.

    This is intentionally not a redaction pass: unknown keys are absent even
    when their names do not look secret, and allowed nested values are rebuilt
    from their documented primitive fields. Internal credential consumers keep
    the original dictionary unchanged.
    """

    preview: dict[str, Any] = {}

    validation_version = credentials.get("meta_validation_version")
    if type(validation_version) is int and 0 < validation_version <= 1000:
        preview["meta_validation_version"] = validation_version

    validated_at = _safe_public_validated_at(credentials.get("meta_validated_at"))
    if validated_at is not None:
        preview["meta_validated_at"] = validated_at

    for field in _PUBLIC_META_ID_FIELDS:
        safe_id = _safe_public_numeric_id(credentials.get(field))
        if safe_id is not None:
            preview[field] = safe_id

    if credentials.get("meta_business_config_source") == "oauth_state":
        preview["meta_business_config_source"] = "oauth_state"

    for field in ("meta_scopes", "meta_granted_permissions"):
        permissions = _safe_public_permission_list(credentials.get(field))
        if permissions:
            preview[field] = permissions

    granular_scopes = _safe_public_meta_granular_scopes(credentials.get("meta_granular_scopes"))
    if granular_scopes:
        preview["meta_granular_scopes"] = granular_scopes

    for field in _PUBLIC_META_EXPIRY_FIELDS:
        value = credentials.get(field)
        if type(value) is int and value > 0:
            preview[field] = value

    business_ids = []
    business_value = credentials.get("business_ids")
    if isinstance(business_value, list):
        for item in business_value[:500]:
            safe_id = _safe_public_numeric_id(item)
            if safe_id is not None and safe_id not in business_ids:
                business_ids.append(safe_id)
    if business_ids:
        preview["business_ids"] = business_ids

    login_customer_id = credentials.get("login_customer_id")
    if isinstance(login_customer_id, (str, int)) and not isinstance(login_customer_id, bool):
        normalized_login_customer_id = str(login_customer_id).strip()
        if _PUBLIC_GOOGLE_ACCOUNT_ID_RE.fullmatch(normalized_login_customer_id):
            preview["login_customer_id"] = normalized_login_customer_id

    return sorted(preview), preview


@dataclass(frozen=True)
class CredentialStorageSecurity:
    credential_id: UUID
    semantic_revision: int
    encrypted: bool
    decryptable: bool
    legacy_unverified: bool
    key_id: Optional[str]
    needs_rotation: bool
    write_eligible: bool
    reason: str


@dataclass(frozen=True)
class ProviderAccountCredentialBinding:
    ad_account_id: UUID
    client_id: Optional[UUID]
    provider: str
    external_account_id: str
    credential_id: UUID
    bound_by: Optional[UUID]
    bound_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BoundIntegrationCredential:
    binding: ProviderAccountCredentialBinding
    credential: IntegrationCredentialOut
    security: CredentialStorageSecurity


@dataclass(frozen=True)
class CredentialRotationResult:
    scanned: int
    candidates: int
    rotated: int
    unchanged: int
    dry_run: bool


@dataclass(frozen=True)
class CredentialEncryptionSummary:
    total: int
    active: int
    encrypted: int
    legacy_plaintext: int
    decryptable: int
    write_eligible: int
    needs_rotation: int
    unavailable: int


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
    def resolve_many_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]: ...
    def get_security_status(self, credential_id: UUID) -> Optional[CredentialStorageSecurity]: ...
    def get_encryption_summary(self) -> CredentialEncryptionSummary: ...
    def rotate_credentials(self, *, dry_run: bool = True) -> CredentialRotationResult: ...
    def bind_provider_account(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
        credential_id: UUID,
        bound_by: Optional[UUID],
    ) -> ProviderAccountCredentialBinding: ...
    def resolve_bound_credential(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
    ) -> Optional[BoundIntegrationCredential]: ...


class SqliteIntegrationCredentialStore:
    def __init__(self, db_path: str, *, keyring: Optional[CredentialKeyring] = None):
        self.db_path = db_path
        self.keyring = keyring if keyring is not None else CredentialKeyring.from_env()
        init_sqlite(db_path)
        self._ensure_binding_schema()

    def _ensure_binding_schema(self) -> None:
        # Kept here instead of app.db so this security feature can be deployed
        # independently and older SQLite databases gain the table idempotently.
        with sqlite_conn(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_account_credential_bindings (
                  ad_account_id TEXT NOT NULL,
                  client_id TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  external_account_id TEXT NOT NULL,
                  credential_id TEXT NOT NULL,
                  bound_by TEXT NULL,
                  bound_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (ad_account_id, provider),
                  FOREIGN KEY (ad_account_id) REFERENCES ad_accounts(id) ON DELETE CASCADE,
                  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
                  FOREIGN KEY (credential_id) REFERENCES integration_credentials(id) ON DELETE RESTRICT,
                  FOREIGN KEY (bound_by) REFERENCES users(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_account_credential_bindings_credential
                  ON provider_account_credential_bindings(credential_id);
                CREATE INDEX IF NOT EXISTS idx_provider_account_credential_bindings_identity
                  ON provider_account_credential_bindings(provider, external_account_id);
                """
            )
            binding_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(provider_account_credential_bindings)"
                ).fetchall()
            }
            if "client_id" not in binding_cols:
                # Never infer/backfill historical binding ownership. A nullable
                # upgrade column makes every old row fail resolution until a
                # fresh verified discovery writes the exact client id.
                conn.execute(
                    "ALTER TABLE provider_account_credential_bindings ADD COLUMN client_id TEXT NULL"
                )
            conn.commit()

    @staticmethod
    def _aad_context(
        *,
        credential_id: object,
        provider: object,
        scope_type: object,
        scope_id: object,
        connection_key: object,
    ) -> dict[str, object]:
        return {
            "credential_id": credential_id,
            "provider": provider,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "connection_key": connection_key,
        }

    @classmethod
    def _row_aad_context(cls, row) -> dict[str, object]:
        return cls._aad_context(
            credential_id=row["id"],
            provider=row["provider"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
            connection_key=(row["connection_key"] if "connection_key" in row.keys() else "default"),
        )

    @staticmethod
    def _safe_storage_error(code: str, message: str, *, status_code: int = 503) -> HTTPException:
        return HTTPException(status_code=status_code, detail={"code": code, "message": message})

    @staticmethod
    def _parse_stored_document(row) -> dict[str, Any]:
        try:
            value = json.loads(row["credentials_json"] or "{}")
        except Exception as exc:
            raise SqliteIntegrationCredentialStore._safe_storage_error(
                "credential_storage_corrupt",
                "Stored integration credentials are unavailable. Reconnect the provider.",
            ) from exc
        if not isinstance(value, dict):
            raise SqliteIntegrationCredentialStore._safe_storage_error(
                "credential_storage_corrupt",
                "Stored integration credentials are unavailable. Reconnect the provider.",
            )
        return value

    def _deserialize_credentials(self, row) -> dict[str, Any]:
        document = self._parse_stored_document(row)
        if not is_encrypted_credential_envelope(document):
            # Legacy plaintext remains readable for existing reporting/sync. It
            # is never considered trustworthy enough for a provider money write.
            return document
        try:
            return self.keyring.decrypt_json(document, aad_context=self._row_aad_context(row))
        except CredentialEncryptionUnavailable as exc:
            raise self._safe_storage_error(
                "credential_decryption_key_unavailable",
                "Stored integration credentials cannot be decrypted with the configured keyring.",
            ) from exc
        except CredentialCryptoError as exc:
            raise self._safe_storage_error(
                "credential_decryption_failed",
                "Stored integration credentials failed integrity verification. Reconnect the provider.",
            ) from exc

    def _serialize_credentials(
        self,
        credentials: Dict[str, Any],
        *,
        credential_id: object,
        provider: object,
        scope_type: object,
        scope_id: object,
        connection_key: object,
    ) -> str:
        try:
            envelope = self.keyring.encrypt_json(
                credentials,
                aad_context=self._aad_context(
                    credential_id=credential_id,
                    provider=provider,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    connection_key=connection_key,
                ),
            )
        except CredentialCryptoError as exc:
            raise self._safe_storage_error(
                "credential_encryption_unavailable",
                "Credential storage encryption is not configured. Configure the keyring before reconnecting a provider.",
            ) from exc
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=True)

    def _to_out(self, row) -> IntegrationCredentialOut:
        return IntegrationCredentialOut(
            id=UUID(row["id"]),
            provider=row["provider"],
            scope_type=row["scope_type"],
            scope_id=UUID(row["scope_id"]) if row["scope_id"] else None,
            connection_key=str(row["connection_key"] or "default") if "connection_key" in row.keys() else "default",
            credentials=self._deserialize_credentials(row),
            status=row["status"],
            created_by=UUID(row["created_by"]) if row["created_by"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def upsert(self, payload: IntegrationCredentialCreate) -> IntegrationCredentialOut:
        now = _utcnow().isoformat()
        cred_id = str(uuid4())
        provider = _normalize_provider(payload.provider)
        scope_id = str(payload.scope_id) if payload.scope_id else None
        connection_key = str(payload.connection_key or "default").strip() or "default"
        if payload.scope_type == "global" and scope_id is not None:
            raise HTTPException(status_code=400, detail="scope_id must be null for global scope")
        if payload.scope_type in {"agency", "client"} and scope_id is None:
            raise HTTPException(status_code=400, detail="scope_id is required for agency/client scope")
        with sqlite_conn(self.db_path) as conn:
            # Serialize the read-then-write identity decision. The partial
            # unique indexes remain the final invariant, while this lock makes
            # concurrent reconnects deterministic upserts instead of exposing
            # a low-level UNIQUE error to one caller.
            conn.execute("BEGIN IMMEDIATE")
            if scope_id is None:
                existing = conn.execute(
                    """
                    SELECT * FROM integration_credentials
                    WHERE provider=? AND scope_type=? AND scope_id IS NULL AND connection_key=?
                    """,
                    (provider, payload.scope_type, connection_key),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT * FROM integration_credentials
                    WHERE provider=? AND scope_type=? AND scope_id=? AND connection_key=?
                    """,
                    (provider, payload.scope_type, scope_id, connection_key),
                ).fetchone()
            if existing:
                encrypted_credentials = self._serialize_credentials(
                    payload.credentials,
                    credential_id=existing["id"],
                    provider=provider,
                    scope_type=payload.scope_type,
                    scope_id=scope_id,
                    connection_key=connection_key,
                )
                conn.execute(
                    """
                    UPDATE integration_credentials
                    SET credentials_json=?, status='active', created_by=?, updated_at=?,
                        semantic_revision=semantic_revision+1
                    WHERE id=?
                    """,
                    (
                        encrypted_credentials,
                        str(payload.created_by) if payload.created_by else existing["created_by"],
                        now,
                        existing["id"],
                    ),
                )
                # A reconnect may represent a different provider principal or
                # authorization grant. Never carry an old money-write binding
                # across credential replacement; verified discovery must bind it again.
                conn.execute(
                    "DELETE FROM provider_account_credential_bindings WHERE credential_id=?",
                    (existing["id"],),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (existing["id"],)).fetchone()
                return self._to_out(row)

            encrypted_credentials = self._serialize_credentials(
                payload.credentials,
                credential_id=cred_id,
                provider=provider,
                scope_type=payload.scope_type,
                scope_id=scope_id,
                connection_key=connection_key,
            )
            conn.execute(
                """
                INSERT INTO integration_credentials
                (id, provider, scope_type, scope_id, connection_key, credentials_json, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    cred_id,
                    provider,
                    payload.scope_type,
                    scope_id,
                    connection_key,
                    encrypted_credentials,
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
            now = _utcnow().isoformat()
            if "scope_id" in patch:
                next_scope_id = str(patch["scope_id"]) if patch["scope_id"] is not None else None
            else:
                next_scope_id = existing["scope_id"]
            data = {
                "provider": _normalize_provider(patch.get("provider", existing["provider"])),
                "scope_type": patch.get("scope_type", existing["scope_type"]),
                "scope_id": next_scope_id,
                "connection_key": str(patch.get("connection_key", existing["connection_key"] or "default")).strip() or "default",
                "status": patch.get("status", existing["status"]),
                "created_by": str(patch["created_by"]) if patch.get("created_by") else existing["created_by"],
                "updated_at": now,
            }
            if data["scope_type"] == "global" and data["scope_id"] is not None:
                raise HTTPException(status_code=400, detail="scope_id must be null for global scope")
            if data["scope_type"] in {"agency", "client"} and data["scope_id"] is None:
                raise HTTPException(status_code=400, detail="scope_id is required for agency/client scope")
            aad_changed = any(
                str(data[field] or "") != str(existing[field] or "")
                for field in ("provider", "scope_type", "scope_id", "connection_key")
            )
            if "credentials" in patch or aad_changed:
                credentials = (
                    dict(patch.get("credentials") or {})
                    if "credentials" in patch
                    else self._deserialize_credentials(existing)
                )
                credentials_json = self._serialize_credentials(
                    credentials,
                    credential_id=existing["id"],
                    provider=data["provider"],
                    scope_type=data["scope_type"],
                    scope_id=data["scope_id"],
                    connection_key=data["connection_key"],
                )
            else:
                credentials_json = existing["credentials_json"]
            conn.execute(
                """
                UPDATE integration_credentials
                SET provider=?, scope_type=?, scope_id=?, connection_key=?, credentials_json=?, status=?, created_by=?, updated_at=?,
                    semantic_revision=semantic_revision+1
                WHERE id=?
                """,
                (
                    data["provider"],
                    data["scope_type"],
                    data["scope_id"],
                    data["connection_key"],
                    credentials_json,
                    data["status"],
                    data["created_by"],
                    data["updated_at"],
                    str(credential_id),
                ),
            )
            if (
                "credentials" in patch
                or aad_changed
                or str(data["status"]) != str(existing["status"])
            ):
                conn.execute(
                    "DELETE FROM provider_account_credential_bindings WHERE credential_id=?",
                    (str(credential_id),),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
        return self._to_out(row)

    def archive(self, credential_id: UUID) -> IntegrationCredentialOut:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM integration_credentials WHERE id=?", (str(credential_id),)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Integration credential not found")
            now = _utcnow().isoformat()
            conn.execute(
                "UPDATE integration_credentials SET status='archived', updated_at=?, semantic_revision=semantic_revision+1 WHERE id=?",
                (now, str(credential_id)),
            )
            conn.execute(
                "DELETE FROM provider_account_credential_bindings WHERE credential_id=?",
                (str(credential_id),),
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
        rows = self.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
        return rows[0] if rows else None

    def resolve_many_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]:
        provider_norm = _normalize_provider(provider)
        out: List[IntegrationCredentialOut] = []
        seen_ids: set[str] = set()

        def _push(row) -> None:
            if not row:
                return
            rid = str(row["id"])
            if rid in seen_ids:
                return
            seen_ids.add(rid)
            out.append(self._to_out(row))

        with sqlite_conn(self.db_path) as conn:
            # 1) client-scoped credential.
            client_rows = conn.execute(
                """
                SELECT * FROM integration_credentials
                WHERE provider=? AND scope_type='client' AND scope_id=? AND status='active'
                ORDER BY updated_at DESC, connection_key ASC
                """,
                (provider_norm, str(client_id)),
            ).fetchall()
            for row in client_rows:
                _push(row)

            # 2) agency-scoped credential for an agency that has this client.
            if user_id:
                agency_rows = conn.execute(
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
                    ORDER BY ic.updated_at DESC, ic.connection_key ASC
                    """,
                    (provider_norm, str(client_id), str(user_id)),
                ).fetchall()
            else:
                agency_rows = conn.execute(
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
                    ORDER BY ic.updated_at DESC, ic.connection_key ASC
                    """,
                    (provider_norm, str(client_id)),
                ).fetchall()
            for row in agency_rows:
                _push(row)

            # 3) Global credentials are platform-owned. They are available only
            # to trusted admin/scheduler resolution (user_id=None), never to an
            # agency request merely because that agency can see the client.
            if user_id is None:
                global_rows = conn.execute(
                    """
                    SELECT * FROM integration_credentials
                    WHERE provider=? AND scope_type='global' AND status='active'
                    ORDER BY updated_at DESC, connection_key ASC
                    """,
                    (provider_norm,),
                ).fetchall()
                for row in global_rows:
                    _push(row)
        return out

    def _security_status_for_row(self, row) -> CredentialStorageSecurity:
        credential_id = UUID(row["id"])
        semantic_revision = int(row["semantic_revision"] or 1)
        try:
            document = self._parse_stored_document(row)
        except HTTPException:
            return CredentialStorageSecurity(
                credential_id=credential_id,
                semantic_revision=semantic_revision,
                encrypted=False,
                decryptable=False,
                legacy_unverified=False,
                key_id=None,
                needs_rotation=False,
                write_eligible=False,
                reason="credential_storage_corrupt",
            )
        if not is_encrypted_credential_envelope(document):
            return CredentialStorageSecurity(
                credential_id=credential_id,
                semantic_revision=semantic_revision,
                encrypted=False,
                decryptable=True,
                legacy_unverified=True,
                key_id=None,
                needs_rotation=True,
                write_eligible=False,
                reason="credential_legacy_plaintext",
            )
        key_id = credential_envelope_key_id(document)
        if not key_id or not self.keyring.has_key(key_id):
            return CredentialStorageSecurity(
                credential_id=credential_id,
                semantic_revision=semantic_revision,
                encrypted=True,
                decryptable=False,
                legacy_unverified=False,
                key_id=key_id,
                needs_rotation=bool(key_id and key_id != self.keyring.active_key_id),
                write_eligible=False,
                reason="credential_decryption_key_unavailable",
            )
        try:
            self.keyring.decrypt_json(document, aad_context=self._row_aad_context(row))
        except CredentialCryptoError:
            return CredentialStorageSecurity(
                credential_id=credential_id,
                semantic_revision=semantic_revision,
                encrypted=True,
                decryptable=False,
                legacy_unverified=False,
                key_id=key_id,
                needs_rotation=key_id != self.keyring.active_key_id,
                write_eligible=False,
                reason="credential_integrity_failed",
            )
        needs_rotation = key_id != self.keyring.active_key_id
        return CredentialStorageSecurity(
            credential_id=credential_id,
            semantic_revision=semantic_revision,
            encrypted=True,
            decryptable=True,
            legacy_unverified=False,
            key_id=key_id,
            needs_rotation=needs_rotation,
            # A valid envelope under any still-configured key remains usable
            # while rotation runs. Removing that key immediately fails closed.
            write_eligible=True,
            reason="credential_rotation_recommended" if needs_rotation else "credential_encrypted",
        )

    def get_security_status(self, credential_id: UUID) -> Optional[CredentialStorageSecurity]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM integration_credentials WHERE id=?",
                (str(credential_id),),
            ).fetchone()
        return self._security_status_for_row(row) if row else None

    def get_encryption_summary(self) -> CredentialEncryptionSummary:
        """Return aggregate readiness only; never credential ids or payload values."""

        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM integration_credentials").fetchall()
        statuses = [self._security_status_for_row(row) for row in rows]
        return CredentialEncryptionSummary(
            total=len(rows),
            active=sum(1 for row in rows if row["status"] == "active"),
            encrypted=sum(1 for status in statuses if status.encrypted),
            legacy_plaintext=sum(1 for status in statuses if status.legacy_unverified),
            decryptable=sum(1 for status in statuses if status.decryptable),
            write_eligible=sum(
                1
                for row, status in zip(rows, statuses)
                if row["status"] == "active" and status.write_eligible
            ),
            needs_rotation=sum(1 for status in statuses if status.needs_rotation),
            unavailable=sum(1 for status in statuses if not status.decryptable),
        )

    def rotate_credentials(self, *, dry_run: bool = True) -> CredentialRotationResult:
        """Re-encrypt legacy/old-key rows under the active key, atomically.

        Dry-run is the default. Actual rotation first authenticates every
        candidate; one corrupt or undecryptable row aborts before any update.
        """

        if not self.keyring.enabled:
            raise self._safe_storage_error(
                "credential_encryption_unavailable",
                "Credential storage encryption is not configured.",
            )
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM integration_credentials ORDER BY created_at ASC, id ASC"
            ).fetchall()
            plans: list[tuple[object, dict[str, Any], str]] = []
            unchanged = 0

            def _decrypt_candidate(document: dict[str, Any], row) -> dict[str, Any]:
                try:
                    return self.keyring.decrypt_json(
                        document, aad_context=self._row_aad_context(row)
                    )
                except CredentialEncryptionUnavailable as exc:
                    raise self._safe_storage_error(
                        "credential_rotation_key_unavailable",
                        "A key required for credential rotation is unavailable.",
                    ) from exc
                except CredentialCryptoError as exc:
                    raise self._safe_storage_error(
                        "credential_rotation_integrity_failed",
                        "A stored credential failed integrity verification; rotation was not applied.",
                        status_code=409,
                    ) from exc

            for row in rows:
                document = self._parse_stored_document(row)
                key_id = credential_envelope_key_id(document)
                if is_encrypted_credential_envelope(document) and key_id == self.keyring.active_key_id:
                    # Still authenticate active-key rows before declaring them healthy.
                    _decrypt_candidate(document, row)
                    unchanged += 1
                    continue
                credentials = (
                    _decrypt_candidate(document, row)
                    if is_encrypted_credential_envelope(document)
                    else document
                )
                plans.append((row, credentials, row["credentials_json"]))

            if not dry_run:
                for row, credentials, previous_json in plans:
                    replacement = self._serialize_credentials(
                        credentials,
                        **self._row_aad_context(row),
                    )
                    cursor = conn.execute(
                        """
                        UPDATE integration_credentials
                        SET credentials_json=?
                        WHERE id=? AND credentials_json=?
                        """,
                        (replacement, row["id"], previous_json),
                    )
                    if int(cursor.rowcount or 0) != 1:
                        conn.rollback()
                        raise self._safe_storage_error(
                            "credential_rotation_conflict",
                            "Credential rotation encountered a concurrent update; retry safely.",
                            status_code=409,
                        )
                conn.commit()
            return CredentialRotationResult(
                scanned=len(rows),
                candidates=len(plans),
                rotated=0 if dry_run else len(plans),
                unchanged=unchanged,
                dry_run=bool(dry_run),
            )

    @staticmethod
    def _binding_to_out(row) -> ProviderAccountCredentialBinding:
        return ProviderAccountCredentialBinding(
            ad_account_id=UUID(row["ad_account_id"]),
            client_id=UUID(row["client_id"]) if row["client_id"] else None,
            provider=_normalize_provider(row["provider"]),
            external_account_id=str(row["external_account_id"]),
            credential_id=UUID(row["credential_id"]),
            bound_by=UUID(row["bound_by"]) if row["bound_by"] else None,
            bound_at=datetime.fromisoformat(row["bound_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _assert_binding_scope(conn, *, account_row, credential_row) -> None:
        scope_type = str(credential_row["scope_type"] or "").strip().lower()
        scope_id = str(credential_row["scope_id"] or "").strip()
        client_id = str(account_row["client_id"])
        if scope_type == "global" and not scope_id:
            return
        if scope_type == "client" and scope_id == client_id:
            return
        if scope_type == "agency" and scope_id:
            active_access = conn.execute(
                """
                SELECT 1
                FROM agency_client_access aca
                JOIN agencies a ON a.id=aca.agency_id
                WHERE aca.agency_id=? AND aca.client_id=? AND a.status='active'
                """,
                (scope_id, client_id),
            ).fetchone()
            if active_access:
                return
        raise SqliteIntegrationCredentialStore._safe_storage_error(
            "credential_binding_scope_mismatch",
            "Credential scope does not own the selected advertising account.",
            status_code=409,
        )

    @staticmethod
    def _has_active_assignment_conflict(
        conn,
        *,
        ad_account_id: UUID | str,
        provider: str,
        external_account_id: str,
    ) -> bool:
        """Detect legacy canonical duplicates without trusting metadata.

        Database guards prevent new duplicates, but historical rows can differ
        syntactically (for example ``facebook`` vs ``meta`` or ``act_123`` vs
        ``123``). Any second active account makes ownership ambiguous and must
        fail closed for provider money writes.
        """

        rows = conn.execute(
            """
            SELECT aa.id, aa.platform, aa.external_account_id
            FROM ad_accounts aa
            JOIN clients c ON c.id=aa.client_id
            WHERE aa.id<>? AND aa.status='active' AND c.status='active'
            """,
            (str(ad_account_id),),
        ).fetchall()
        return any(
            normalize_account_platform(row["platform"]) == provider
            and canonical_external_account_id(row["platform"], row["external_account_id"])
            == external_account_id
            for row in rows
        )

    def bind_provider_account(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
        credential_id: UUID,
        bound_by: Optional[UUID],
    ) -> ProviderAccountCredentialBinding:
        """Persist an explicit server-owned binding after verified discovery.

        Historical ``ad_accounts.metadata`` is deliberately ignored and never
        backfilled: a provider money write requires this separate record.
        """

        normalized_provider = normalize_account_platform(provider)
        normalized_external_id = canonical_external_account_id(
            normalized_provider, external_account_id
        )
        if not normalized_provider or not normalized_external_id:
            raise self._safe_storage_error(
                "credential_binding_identity_invalid",
                "Advertising account identity is invalid.",
                status_code=400,
            )
        with sqlite_conn(self.db_path) as conn:
            account = conn.execute(
                """
                SELECT aa.*
                FROM ad_accounts aa
                JOIN clients c ON c.id=aa.client_id
                WHERE aa.id=? AND aa.status='active' AND c.status='active'
                """,
                (str(ad_account_id),),
            ).fetchone()
            if not account:
                raise self._safe_storage_error(
                    "credential_binding_account_unavailable",
                    "An active advertising account and client are required.",
                    status_code=409,
                )
            account_provider = normalize_account_platform(account["platform"])
            account_external_id = canonical_external_account_id(
                account_provider, account["external_account_id"]
            )
            if (
                normalized_provider != account_provider
                or normalized_external_id != account_external_id
            ):
                raise self._safe_storage_error(
                    "credential_binding_account_mismatch",
                    "Advertising account identity does not match the server-owned account.",
                    status_code=409,
                )
            if self._has_active_assignment_conflict(
                conn,
                ad_account_id=ad_account_id,
                provider=normalized_provider,
                external_account_id=normalized_external_id,
            ):
                raise self._safe_storage_error(
                    "credential_binding_assignment_conflict",
                    "Advertising account ownership is ambiguous across active assignments.",
                    status_code=409,
                )
            credential = conn.execute(
                """
                SELECT * FROM integration_credentials
                WHERE id=? AND provider=? AND status='active'
                """,
                (str(credential_id), normalized_provider),
            ).fetchone()
            if not credential:
                raise self._safe_storage_error(
                    "credential_binding_credential_unavailable",
                    "An active provider credential is required.",
                    status_code=409,
                )
            self._assert_binding_scope(conn, account_row=account, credential_row=credential)
            now = _utcnow().isoformat()
            existing = conn.execute(
                """
                SELECT * FROM provider_account_credential_bindings
                WHERE ad_account_id=? AND provider=?
                """,
                (str(ad_account_id), normalized_provider),
            ).fetchone()
            bound_at = (
                existing["bound_at"]
                if existing
                and str(existing["credential_id"]) == str(credential_id)
                and str(existing["external_account_id"]) == normalized_external_id
                else now
            )
            conn.execute(
                """
                INSERT INTO provider_account_credential_bindings
                  (ad_account_id, client_id, provider, external_account_id, credential_id, bound_by, bound_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ad_account_id, provider) DO UPDATE SET
                  client_id=excluded.client_id,
                  external_account_id=excluded.external_account_id,
                  credential_id=excluded.credential_id,
                  bound_by=excluded.bound_by,
                  updated_at=excluded.updated_at
                """,
                (
                    str(ad_account_id),
                    str(account["client_id"]),
                    normalized_provider,
                    normalized_external_id,
                    str(credential_id),
                    str(bound_by) if bound_by else None,
                    bound_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT * FROM provider_account_credential_bindings
                WHERE ad_account_id=? AND provider=?
                """,
                (str(ad_account_id), normalized_provider),
            ).fetchone()
        return self._binding_to_out(row)

    def resolve_bound_credential(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
    ) -> Optional[BoundIntegrationCredential]:
        normalized_provider = normalize_account_platform(provider)
        normalized_external_id = canonical_external_account_id(
            normalized_provider, external_account_id
        )
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                  b.ad_account_id AS b_ad_account_id,
                  b.client_id AS b_client_id,
                  b.provider AS b_provider,
                  b.external_account_id AS b_external_account_id,
                  b.credential_id AS b_credential_id,
                  b.bound_by AS b_bound_by,
                  b.bound_at AS b_bound_at,
                  b.updated_at AS b_updated_at,
                  aa.*,
                  c.status AS client_status
                FROM provider_account_credential_bindings b
                JOIN ad_accounts aa ON aa.id=b.ad_account_id
                JOIN clients c ON c.id=aa.client_id
                WHERE b.ad_account_id=? AND b.provider=?
                """,
                (str(ad_account_id), normalized_provider),
            ).fetchone()
            if not row:
                return None
            account_external_id = canonical_external_account_id(
                row["platform"], row["external_account_id"]
            )
            if (
                row["status"] != "active"
                or row["client_status"] != "active"
                or str(row["b_client_id"] or "") != str(row["client_id"])
                or normalize_account_platform(row["platform"]) != normalized_provider
                or str(row["b_external_account_id"]) != normalized_external_id
                or account_external_id != normalized_external_id
            ):
                return None
            if self._has_active_assignment_conflict(
                conn,
                ad_account_id=ad_account_id,
                provider=normalized_provider,
                external_account_id=normalized_external_id,
            ):
                return None
            credential = conn.execute(
                """
                SELECT * FROM integration_credentials
                WHERE id=? AND provider=? AND status='active'
                """,
                (row["b_credential_id"], normalized_provider),
            ).fetchone()
            if not credential:
                return None
            try:
                self._assert_binding_scope(conn, account_row=row, credential_row=credential)
            except HTTPException:
                return None

        binding = ProviderAccountCredentialBinding(
            ad_account_id=UUID(row["b_ad_account_id"]),
            client_id=UUID(row["b_client_id"]) if row["b_client_id"] else None,
            provider=_normalize_provider(row["b_provider"]),
            external_account_id=str(row["b_external_account_id"]),
            credential_id=UUID(row["b_credential_id"]),
            bound_by=UUID(row["b_bound_by"]) if row["b_bound_by"] else None,
            bound_at=datetime.fromisoformat(row["b_bound_at"]),
            updated_at=datetime.fromisoformat(row["b_updated_at"]),
        )
        return BoundIntegrationCredential(
            binding=binding,
            credential=self._to_out(credential),
            security=self._security_status_for_row(credential),
        )

    def remove_provider_account_binding(self, *, ad_account_id: UUID, provider: str) -> bool:
        with sqlite_conn(self.db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM provider_account_credential_bindings
                WHERE ad_account_id=? AND provider=?
                """,
                (str(ad_account_id), normalize_account_platform(provider)),
            )
            conn.commit()
        return int(cursor.rowcount or 0) == 1


class InMemoryIntegrationCredentialStore:
    def __init__(
        self,
        agency_access_resolver: Optional[Callable[[UUID, UUID, Optional[UUID]], bool]] = None,
    ):
        self.items: Dict[UUID, IntegrationCredentialOut] = {}
        self.semantic_revisions: Dict[UUID, int] = {}
        self.bindings: Dict[tuple[UUID, str], ProviderAccountCredentialBinding] = {}
        self.agency_access_resolver = agency_access_resolver

    def upsert(self, payload: IntegrationCredentialCreate) -> IntegrationCredentialOut:
        provider = _normalize_provider(payload.provider)
        for row in self.items.values():
            if (
                row.provider == provider
                and row.scope_type == payload.scope_type
                and row.scope_id == payload.scope_id
                and row.connection_key == (payload.connection_key or "default")
            ):
                updated = IntegrationCredentialOut(
                    id=row.id,
                    provider=provider,
                    scope_type=payload.scope_type,
                    scope_id=payload.scope_id,
                    connection_key=payload.connection_key or "default",
                    credentials=payload.credentials,
                    status="active",
                    created_by=payload.created_by or row.created_by,
                    created_at=row.created_at,
                    updated_at=_utcnow(),
                )
                self.items[row.id] = updated
                self.semantic_revisions[row.id] = self.semantic_revisions.get(row.id, 1) + 1
                self.bindings = {
                    key: binding
                    for key, binding in self.bindings.items()
                    if binding.credential_id != row.id
                }
                return updated
        now = _utcnow()
        created = IntegrationCredentialOut(
            id=uuid4(),
            provider=provider,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            connection_key=payload.connection_key or "default",
            credentials=payload.credentials,
            status="active",
            created_by=payload.created_by,
            created_at=now,
            updated_at=now,
        )
        self.items[created.id] = created
        self.semantic_revisions[created.id] = 1
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
            connection_key=str(patch.get("connection_key", existing.connection_key or "default")).strip() or "default",
            credentials=patch.get("credentials", existing.credentials),
            status=patch.get("status", existing.status),
            created_by=patch.get("created_by", existing.created_by),
            created_at=existing.created_at,
            updated_at=_utcnow(),
        )
        self.items[credential_id] = updated
        self.semantic_revisions[credential_id] = self.semantic_revisions.get(credential_id, 1) + 1
        if any(
            field in patch
            for field in ("credentials", "provider", "scope_type", "scope_id", "connection_key", "status")
        ):
            self.bindings = {
                key: binding
                for key, binding in self.bindings.items()
                if binding.credential_id != credential_id
            }
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
        rows = self.resolve_many_for_client(provider=provider, client_id=client_id, user_id=user_id)
        return rows[0] if rows else None

    def resolve_many_for_client(
        self,
        *,
        provider: str,
        client_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> List[IntegrationCredentialOut]:
        p = _normalize_provider(provider)
        rows = [x for x in self.items.values() if x.status == "active" and x.provider == p]
        client_rows = [x for x in rows if x.scope_type == "client" and x.scope_id == client_id]
        client_rows.sort(key=lambda x: x.updated_at, reverse=True)
        agency_rows = [
            x
            for x in rows
            if x.scope_type == "agency"
            and x.scope_id is not None
            and self.agency_access_resolver is not None
            and self.agency_access_resolver(x.scope_id, client_id, user_id)
        ]
        agency_rows.sort(key=lambda x: x.updated_at, reverse=True)
        global_rows = [x for x in rows if x.scope_type == "global"] if user_id is None else []
        global_rows.sort(key=lambda x: x.updated_at, reverse=True)
        return [*client_rows, *agency_rows, *global_rows]

    def get_security_status(self, credential_id: UUID) -> Optional[CredentialStorageSecurity]:
        if credential_id not in self.items:
            return None
        # This store has no persistent bytes at rest. Treat credentials created
        # in it as authenticated only for isolated route/service tests; the
        # production application uses SqliteIntegrationCredentialStore.
        return CredentialStorageSecurity(
            credential_id=credential_id,
            semantic_revision=self.semantic_revisions.get(credential_id, 1),
            encrypted=True,
            decryptable=True,
            legacy_unverified=False,
            key_id="in-memory-test-only",
            needs_rotation=False,
            write_eligible=True,
            reason="credential_in_memory_test_store",
        )

    def get_encryption_summary(self) -> CredentialEncryptionSummary:
        active = [row for row in self.items.values() if row.status == "active"]
        return CredentialEncryptionSummary(
            total=len(self.items),
            active=len(active),
            encrypted=len(self.items),
            legacy_plaintext=0,
            decryptable=len(self.items),
            write_eligible=len(active),
            needs_rotation=0,
            unavailable=0,
        )

    def rotate_credentials(self, *, dry_run: bool = True) -> CredentialRotationResult:
        return CredentialRotationResult(
            scanned=len(self.items),
            candidates=0,
            rotated=0,
            unchanged=len(self.items),
            dry_run=bool(dry_run),
        )

    def bind_provider_account(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
        credential_id: UUID,
        bound_by: Optional[UUID],
    ) -> ProviderAccountCredentialBinding:
        normalized_provider = normalize_account_platform(provider)
        normalized_external_id = canonical_external_account_id(
            normalized_provider, external_account_id
        )
        credential = self.items.get(credential_id)
        if (
            credential is None
            or credential.status != "active"
            or normalize_account_platform(credential.provider) != normalized_provider
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "credential_binding_credential_unavailable",
                    "message": "An active provider credential is required.",
                },
            )
        now = _utcnow()
        previous = self.bindings.get((ad_account_id, normalized_provider))
        binding = ProviderAccountCredentialBinding(
            ad_account_id=ad_account_id,
            client_id=None,
            provider=normalized_provider,
            external_account_id=normalized_external_id,
            credential_id=credential_id,
            bound_by=bound_by,
            bound_at=(
                previous.bound_at
                if previous
                and previous.credential_id == credential_id
                and previous.external_account_id == normalized_external_id
                else now
            ),
            updated_at=now,
        )
        self.bindings[(ad_account_id, normalized_provider)] = binding
        return binding

    def resolve_bound_credential(
        self,
        *,
        ad_account_id: UUID,
        provider: str,
        external_account_id: str,
    ) -> Optional[BoundIntegrationCredential]:
        normalized_provider = normalize_account_platform(provider)
        normalized_external_id = canonical_external_account_id(
            normalized_provider, external_account_id
        )
        binding = self.bindings.get((ad_account_id, normalized_provider))
        if not binding or binding.external_account_id != normalized_external_id:
            return None
        credential = self.items.get(binding.credential_id)
        security = self.get_security_status(binding.credential_id)
        if (
            credential is None
            or credential.status != "active"
            or normalize_account_platform(credential.provider) != normalized_provider
            or security is None
        ):
            return None
        return BoundIntegrationCredential(
            binding=binding,
            credential=credential,
            security=security,
        )

    def remove_provider_account_binding(self, *, ad_account_id: UUID, provider: str) -> bool:
        return self.bindings.pop((ad_account_id, normalize_account_platform(provider)), None) is not None


