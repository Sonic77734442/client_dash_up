from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_db import (  # noqa: E402
    assert_postgres_schema_current,
    database_backend,
    runtime_conn,
    table_exists,
)
from app.services.auth_secret_crypto import (  # noqa: E402
    AuthProviderSecretCipher,
    auth_provider_secret_key_id,
    is_encrypted_auth_provider_secret,
)
from app.services.credential_crypto import CredentialCryptoError, CredentialKeyring  # noqa: E402


@dataclass(frozen=True)
class RotationResult:
    scanned: int
    legacy: int
    stale_key: int
    current: int
    rotated: int
    dry_run: bool


def rotate_auth_provider_secrets(
    db_path: str,
    *,
    keyring: CredentialKeyring,
    dry_run: bool,
) -> RotationResult:
    if not keyring.enabled or not keyring.active_key_id:
        raise RuntimeError("auth_provider_secret_encryption_unavailable")
    cipher = AuthProviderSecretCipher(keyring)
    with runtime_conn(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not table_exists(conn, "auth_provider_configs"):
            if database_backend() == "postgresql":
                raise RuntimeError("auth_provider_config_schema_missing")
            return RotationResult(0, 0, 0, 0, 0, dry_run)
        rows = conn.execute(
            "SELECT id, provider, client_secret FROM auth_provider_configs ORDER BY id"
        ).fetchall()
        legacy = 0
        stale_key = 0
        current = 0
        rotated = 0
        for row in rows:
            stored = row["client_secret"]
            encrypted = is_encrypted_auth_provider_secret(stored)
            plaintext = cipher.decrypt(
                stored,
                config_id=row["id"],
                provider=row["provider"],
            )
            if encrypted and auth_provider_secret_key_id(stored) == keyring.active_key_id:
                current += 1
                continue
            if encrypted:
                stale_key += 1
            else:
                legacy += 1
            if dry_run:
                continue
            replacement = cipher.encrypt(
                plaintext,
                config_id=row["id"],
                provider=row["provider"],
            )
            updated = conn.execute(
                """
                UPDATE auth_provider_configs
                SET client_secret=?
                WHERE id=? AND client_secret=?
                """,
                (replacement, row["id"], stored),
            )
            if updated.rowcount != 1:
                raise RuntimeError("auth_provider_secret_rotation_race")
            rotated += 1
        if not dry_run:
            conn.commit()
    return RotationResult(
        scanned=len(rows),
        legacy=legacy,
        stale_key=stale_key,
        current=current,
        rotated=rotated,
        dry_run=dry_run,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or atomically rotate auth provider client secrets."
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("BUDGETS_DB_PATH", "./storage/budgets.db"),
        help="SQLite database path; ignored by the PostgreSQL runtime connection.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply rotation. Without this flag the command is read-only.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    db_path = str(args.db_path)
    if database_backend() == "sqlite":
        path = Path(db_path)
        if not path.is_file():
            print("Auth provider secret rotation refused: existing SQLite file required", file=sys.stderr)
            return 1
    try:
        if database_backend() == "postgresql":
            # Fail closed on an empty, stale, or accidentally selected database
            # before reporting rotation counts.
            assert_postgres_schema_current()
        result = rotate_auth_provider_secrets(
            db_path,
            keyring=CredentialKeyring.from_env(),
            dry_run=not args.apply,
        )
    except (CredentialCryptoError, RuntimeError):
        print("Auth provider secret rotation failed safely", file=sys.stderr)
        return 1
    except Exception:
        print("Auth provider secret rotation failed safely", file=sys.stderr)
        return 1

    mode = "dry-run" if result.dry_run else "apply"
    print(
        f"Auth provider secret rotation {mode}: scanned={result.scanned} "
        f"legacy={result.legacy} stale_key={result.stale_key} "
        f"current={result.current} rotated={result.rotated}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
