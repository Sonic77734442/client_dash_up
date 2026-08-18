import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest

from app.db import IntegrationCredentialConfigurationError, init_sqlite, sqlite_conn
from app.schemas import IntegrationCredentialCreate
from app.services.credential_crypto import CredentialKeyring
from app.services.integration_credentials import SqliteIntegrationCredentialStore


def _keyring() -> CredentialKeyring:
    return CredentialKeyring(keys={"test": bytes([31]) * 32}, active_key_id="test")


def _credential(*, connection_key: str, token: str) -> IntegrationCredentialCreate:
    return IntegrationCredentialCreate(
        provider="meta",
        scope_type="global",
        scope_id=None,
        connection_key=connection_key,
        credentials={"access_token": token},
    )


def test_sqlite_partial_indexes_cover_global_and_scoped_scope_identities(tmp_path):
    db_path = tmp_path / "credential-indexes.db"
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())

    first = store.upsert(_credential(connection_key="default", token="first"))
    updated = store.upsert(_credential(connection_key="default", token="updated"))
    second = store.upsert(_credential(connection_key="other", token="other"))

    assert updated.id == first.id
    assert second.id != first.id
    assert len(store.list(status="all", provider="meta", scope_type="global")) == 2

    with sqlite_conn(str(db_path)) as conn:
        indexes = {
            row["name"]: str(row["sql"] or "").lower()
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='integration_credentials'"
            ).fetchall()
        }
        assert "uq_integration_credentials_scope_provider_connection" not in indexes
        assert (
            "where scope_type='global' and scope_id is null"
            in " ".join(indexes["uq_integration_credentials_global_connection"].split())
        )
        assert (
            "where scope_type in ('agency','client') and scope_id is not null"
            in " ".join(indexes["uq_integration_credentials_scoped_connection"].split())
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO integration_credentials
                  (id, provider, scope_type, scope_id, connection_key, credentials_json,
                   status, created_by, created_at, updated_at)
                VALUES (?, 'meta', 'global', NULL, 'default', '{}', 'active', NULL, ?, ?)
                """,
                (str(uuid4()), now, now),
            )


def test_concurrent_global_reconnects_are_one_deterministic_upsert(tmp_path):
    db_path = tmp_path / "credential-concurrent.db"
    store = SqliteIntegrationCredentialStore(str(db_path), keyring=_keyring())
    workers = 8
    barrier = Barrier(workers)

    def reconnect(index: int):
        barrier.wait()
        return store.upsert(
            _credential(connection_key="same-principal", token=f"token-{index}")
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(reconnect, range(workers)))

    assert len({row.id for row in rows}) == 1
    stored = store.list(status="all", provider="meta", scope_type="global")
    assert len(stored) == 1
    assert stored[0].credentials["access_token"] in {
        f"token-{index}" for index in range(workers)
    }


def test_sqlite_upgrade_fails_closed_when_legacy_global_duplicates_exist(tmp_path):
    db_path = tmp_path / "credential-duplicate-upgrade.db"
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    tokens = ("live-token-one", "live-token-two")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE integration_credentials (
              id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              scope_type TEXT NOT NULL,
              scope_id TEXT NULL,
              connection_key TEXT NOT NULL DEFAULT 'default',
              credentials_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_by TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX uq_integration_credentials_scope_provider_connection
            ON integration_credentials(provider, scope_type, scope_id, connection_key)
            """
        )
        for token in tokens:
            conn.execute(
                """
                INSERT INTO integration_credentials
                  (id, provider, scope_type, scope_id, connection_key, credentials_json,
                   status, created_by, created_at, updated_at)
                VALUES (?, 'meta', 'global', NULL, 'default', ?, 'active', NULL, ?, ?)
                """,
                (str(uuid4()), json.dumps({"access_token": token}), now, now),
            )
        conn.commit()

    with pytest.raises(IntegrationCredentialConfigurationError) as caught:
        init_sqlite(str(db_path))

    assert caught.value.code == "integration_credential_scope_duplicates"
    assert caught.value.duplicate_groups == 1
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT credentials_json FROM integration_credentials ORDER BY credentials_json"
        ).fetchall()
        assert len(rows) == 2
        assert {json.loads(row[0])["access_token"] for row in rows} == set(tokens)
        old_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='uq_integration_credentials_scope_provider_connection'"
        ).fetchone()
        assert old_index is not None
