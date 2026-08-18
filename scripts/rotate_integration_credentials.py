#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app.services.credential_crypto import (
    CredentialCryptoError,
    CredentialEncryptionConfigurationError,
    CredentialEncryptionUnavailable,
    CredentialKeyring,
    credential_envelope_key_id,
    is_encrypted_credential_envelope,
)
from app.services.integration_credentials import (
    CredentialRotationResult,
    SqliteIntegrationCredentialStore,
)


def _safe_error(code: str, message: str) -> None:
    print(json.dumps({"ok": False, "code": code, "message": message}, sort_keys=True))


def _read_only_rotation_plan(
    db_path: Path,
    *,
    keyring: CredentialKeyring,
) -> CredentialRotationResult:
    """Authenticate every stored envelope without initializing or writing SQLite."""
    if not keyring.enabled:
        raise SqliteIntegrationCredentialStore._safe_storage_error(
            "credential_encryption_unavailable",
            "Credential storage encryption is not configured.",
        )
    uri = f"{db_path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='integration_credentials'"
        ).fetchone()
        if not table:
            return CredentialRotationResult(
                scanned=0,
                candidates=0,
                rotated=0,
                unchanged=0,
                dry_run=True,
            )
        rows = conn.execute(
            "SELECT * FROM integration_credentials ORDER BY rowid ASC"
        ).fetchall()

    candidates = 0
    unchanged = 0
    for row in rows:
        document = SqliteIntegrationCredentialStore._parse_stored_document(row)
        if not is_encrypted_credential_envelope(document):
            candidates += 1
            continue
        try:
            keyring.decrypt_json(
                document,
                aad_context=SqliteIntegrationCredentialStore._row_aad_context(row),
            )
        except CredentialEncryptionUnavailable as exc:
            raise SqliteIntegrationCredentialStore._safe_storage_error(
                "credential_rotation_key_unavailable",
                "A key required for credential rotation is unavailable.",
            ) from exc
        except CredentialCryptoError as exc:
            raise SqliteIntegrationCredentialStore._safe_storage_error(
                "credential_rotation_integrity_failed",
                "A stored credential failed integrity verification; rotation was not applied.",
                status_code=409,
            ) from exc
        if credential_envelope_key_id(document) == keyring.active_key_id:
            unchanged += 1
        else:
            candidates += 1
    return CredentialRotationResult(
        scanned=len(rows),
        candidates=candidates,
        rotated=0,
        unchanged=unchanged,
        dry_run=True,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or atomically encrypt legacy integration credential rows. "
            "Encryption keys are read only from deployment environment variables."
        )
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("BUDGETS_DB_PATH", ""),
        help="Existing SQLite database path (defaults to BUDGETS_DB_PATH)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the rotation; without this flag the command is a dry-run",
    )
    args = parser.parse_args(argv)

    raw_path = str(args.db_path or "").strip()
    if not raw_path:
        _safe_error("credential_rotation_db_missing", "An existing database path is required.")
        return 2
    db_path = Path(raw_path).expanduser()
    try:
        resolved = db_path.resolve(strict=True)
    except OSError:
        _safe_error("credential_rotation_db_missing", "The selected database does not exist.")
        return 2
    if not resolved.is_file():
        _safe_error("credential_rotation_db_invalid", "The selected database path is not a file.")
        return 2

    try:
        keyring = CredentialKeyring.from_env()
        if args.apply:
            # Applying may initialize the current SQLite schema, so it remains
            # strictly separated from the default read-only audit path.
            store = SqliteIntegrationCredentialStore(str(resolved), keyring=keyring)
            result = store.rotate_credentials(dry_run=False)
        else:
            result = _read_only_rotation_plan(resolved, keyring=keyring)
    except CredentialEncryptionConfigurationError:
        _safe_error(
            "credential_rotation_keyring_invalid",
            "Credential encryption keyring configuration is invalid.",
        )
        return 2
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _safe_error(
            str(detail.get("code") or "credential_rotation_failed"),
            str(detail.get("message") or "Credential rotation failed safely."),
        )
        return 2
    except Exception:
        # Never serialize exception details: a dependency/database exception
        # must not accidentally carry plaintext credentials into CI or logs.
        _safe_error("credential_rotation_failed", "Credential rotation failed safely.")
        return 2

    print(json.dumps({"ok": True, **asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
