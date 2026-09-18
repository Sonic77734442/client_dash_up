"""Explicit legacy identity migration; no user creation, email matching or grants.

Apply only after deploying trusted per-session provenance. The protected manifest
contains operator-reviewed identity evidence, not passwords or identity tokens.
Re-running the exact manifest/run_id resumes its database-backed checkpoints.
Real manifest-file operations are POSIX-only and require owner-only mode 0600.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from app.runtime_db import assert_postgres_schema_current, database_backend, runtime_conn
from app.services.envidicy_bridge import canonical_uuid, identity_key, utcnow

CONTRACT = "envidicy.dash.identity-migration.v1"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ROWS = 10000
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
ROW_FIELDS = {"legacy_user_id", "issuer", "subject", "provenance_ref", "validation_ref"}


class IdentityMigrationError(ValueError):
    """Only fixed, non-sensitive codes are returned to operators."""


def _error(code: str):
    raise IdentityMigrationError(code)


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        _error("invalid_manifest_shape")


def _reference(value):
    if not isinstance(value, str) or not REFERENCE.fullmatch(value):
        _error("invalid_evidence_reference")


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def validate_manifest(value: object) -> dict:
    _object(value, {"contract_version", "run_id", "operator_ref", "rows"})
    if value["contract_version"] != CONTRACT:
        _error("unsupported_manifest_contract")
    try:
        canonical_uuid(value["run_id"])
    except (ValueError, TypeError, AttributeError):
        _error("invalid_run_id")
    _reference(value["operator_ref"])
    rows = value["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        _error("invalid_manifest_rows")
    users, principals = set(), set()
    clean_rows = []
    for row in rows:
        _object(row, ROW_FIELDS)
        try:
            canonical_uuid(row["legacy_user_id"])
            key = identity_key(row["issuer"], row["subject"])
        except (ValueError, TypeError, AttributeError):
            _error("invalid_identity_mapping")
        _reference(row["provenance_ref"])
        _reference(row["validation_ref"])
        if row["legacy_user_id"] in users or key in principals:
            _error("duplicate_manifest_mapping")
        users.add(row["legacy_user_id"])
        principals.add(key)
        clean_rows.append(dict(row))
    # Snapshot caller-owned objects; no later mutation can change a running plan.
    return {"contract_version": CONTRACT, "run_id": value["run_id"],
            "operator_ref": value["operator_ref"], "rows": clean_rows}


def _file_policy() -> tuple[int, int]:
    if os.name != "posix" or not hasattr(os, "geteuid") or not hasattr(os, "O_NOFOLLOW"):
        _error("protected_manifest_requires_posix")
    return os.geteuid(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _check_file_metadata(metadata, owner: int):
    if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != owner or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_MANIFEST_BYTES):
        _error("unsafe_manifest_file")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _error("duplicate_manifest_field")
        result[key] = value
    return result


def read_manifest(filename: str) -> dict:
    """Read one protected regular file, never a symlink, pipe or shared export."""
    owner, flags = _file_policy()
    descriptor = None
    try:
        path = Path(filename)
        if not path.is_absolute() or path != path.resolve(strict=True):
            _error("unsafe_manifest_path")
        before = path.lstat()
        _check_file_metadata(before, owner)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _check_file_metadata(opened, owner)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _error("manifest_changed_during_read")
        chunks = bytearray()
        while len(chunks) <= MAX_MANIFEST_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_MANIFEST_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        _check_file_metadata(after, owner)
        fingerprint = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if fingerprint(opened) != fingerprint(after) or len(chunks) != opened.st_size:
            _error("manifest_changed_during_read")
        value = json.loads(chunks.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_manifest(value)
    except IdentityMigrationError:
        raise
    except (OSError, ValueError, UnicodeError):
        _error("manifest_unreadable")
    finally:
        if descriptor is not None:
            os.close(descriptor)


class IdentityMigration:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _preflight(self):
        # No init_sqlite/store constructor/migration runner: even dry-run must
        # target an existing, already initialized runtime database.
        if database_backend() == "postgresql":
            assert_postgres_schema_current()
        elif not Path(self.db_path).is_file():
            _error("existing_sqlite_database_required")
        with runtime_conn(self.db_path) as conn:
            conn.execute("SELECT user_id, issuer, subject FROM envidicy_id_principals WHERE 1=0")
            conn.execute("SELECT run_id, row_number, manifest_sha256, row_sha256 FROM envidicy_identity_migration_audit WHERE 1=0")

    def _row(self, manifest: dict, manifest_hash: str, row_number: int, row: dict, *, apply: bool) -> tuple[str, bool]:
        row_hash = _hash({"operator_ref": manifest["operator_ref"], **row})
        with runtime_conn(self.db_path) as conn:
            # Same lock protocol as resolve_identity/bind_project: SQLite write
            # transaction; PG shim takes the shared transaction advisory lock.
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM envidicy_identity_migration_audit WHERE run_id=? AND manifest_sha256<>? LIMIT 1",
                            (manifest["run_id"], manifest_hash)).fetchone():
                _error("run_manifest_conflict")
            checkpoint = conn.execute("SELECT row_sha256, user_id, issuer, subject FROM envidicy_identity_migration_audit WHERE run_id=? AND row_number=?",
                                      (manifest["run_id"], row_number)).fetchone()
            user = conn.execute("SELECT status FROM users WHERE id=?", (row["legacy_user_id"],)).fetchone()
            if not user or user["status"] != "active":
                _error("existing_active_legacy_user_required")
            bindings = conn.execute("SELECT user_id, issuer, subject FROM envidicy_id_principals WHERE user_id=? OR (issuer=? AND subject=?)",
                                    (row["legacy_user_id"], row["issuer"], row["subject"])).fetchall()
            exact = (len(bindings) == 1 and str(bindings[0]["user_id"]) == row["legacy_user_id"]
                     and bindings[0]["issuer"] == row["issuer"] and bindings[0]["subject"] == row["subject"])
            if bindings and not exact:
                _error("identity_binding_conflict")
            if checkpoint:
                if (checkpoint["row_sha256"] != row_hash or not exact
                        or str(checkpoint["user_id"]) != row["legacy_user_id"]
                        or checkpoint["issuer"] != row["issuer"] or checkpoint["subject"] != row["subject"]):
                    _error("checkpoint_binding_conflict")
                return "unchanged", True
            if not apply:
                return ("unchanged" if exact else "checked"), False
            now = utcnow().isoformat()
            if not exact:
                conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)",
                             (row["legacy_user_id"], row["issuer"], row["subject"], now))
            conn.execute("""INSERT INTO envidicy_identity_migration_audit
                (run_id,row_number,manifest_sha256,row_sha256,user_id,issuer,subject,operator_ref,provenance_ref,validation_ref,action,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (manifest["run_id"], row_number, manifest_hash, row_hash, row["legacy_user_id"],
                          row["issuer"], row["subject"], manifest["operator_ref"], row["provenance_ref"],
                          row["validation_ref"], "confirmed" if exact else "linked", now))
            return ("unchanged" if exact else "applied"), True

    def run(self, value: object, *, apply: bool = False) -> dict:
        manifest = validate_manifest(value)
        result = {"status": "applied" if apply else "checked", "run_id": manifest["run_id"],
                  "total": len(manifest["rows"]), "checked": 0, "applied": 0, "unchanged": 0,
                  "failed": 0, "checkpoint_rows": 0, "next_row": 1}
        try:
            self._preflight()
            manifest_hash = _hash(manifest)
            for number, row in enumerate(manifest["rows"], 1):
                result["next_row"] = number
                outcome, checkpointed = self._row(manifest, manifest_hash, number, row, apply=apply)
                result[outcome] += 1
                result["checkpoint_rows"] += int(checkpointed)
            result["next_row"] = None
            return result
        except Exception as exc:
            result.update(status="failed", failed=1,
                          code=str(exc) if isinstance(exc, IdentityMigrationError) else "migration_database_unavailable")
            return result
