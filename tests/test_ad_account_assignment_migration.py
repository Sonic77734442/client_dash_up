import sqlite3

import pytest

from app.db import init_sqlite, sqlite_conn


def test_migration_preserves_legacy_duplicates_and_blocks_new_active_assignment(tmp_path):
    db_path = str(tmp_path / "legacy-assignments.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE clients (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              legal_name TEXT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              default_currency TEXT NOT NULL DEFAULT 'USD',
              timezone TEXT NULL,
              notes TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE ad_accounts (
              id TEXT PRIMARY KEY,
              client_id TEXT NOT NULL,
              platform TEXT NOT NULL,
              external_account_id TEXT NOT NULL,
              name TEXT NOT NULL,
              currency TEXT NOT NULL DEFAULT 'USD',
              timezone TEXT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              metadata TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            INSERT INTO clients (id, name, status, default_currency, created_at, updated_at)
            VALUES
              ('client-a', 'A', 'active', 'USD', '2026-01-01', '2026-01-01'),
              ('client-b', 'B', 'active', 'USD', '2026-01-01', '2026-01-01'),
              ('client-c', 'C', 'active', 'USD', '2026-01-01', '2026-01-01');
            INSERT INTO ad_accounts
              (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
            VALUES
              ('account-a', 'client-a', 'meta', 'legacy-shared', 'A', 'USD', 'active', '2026-01-01', '2026-01-01'),
              ('account-b', 'client-b', 'meta', 'legacy-shared', 'B', 'USD', 'active', '2026-01-01', '2026-01-01');
            """
        )

    init_sqlite(db_path)

    with sqlite_conn(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM ad_accounts WHERE platform='meta' AND external_account_id='legacy-shared'"
        ).fetchone()["n"]
        assert count == 2

        # Metadata/status observations on a preserved legacy row remain writable.
        conn.execute("UPDATE ad_accounts SET metadata='{}' WHERE id='account-a'")
        conn.execute("UPDATE ad_accounts SET status='active' WHERE id='account-a'")
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="assignment_conflict"):
            conn.execute(
                """
                INSERT INTO ad_accounts
                  (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
                VALUES ('account-c', 'client-c', 'meta', 'legacy-shared', 'C', 'USD', 'active', '2026-01-01', '2026-01-01')
                """
            )

        conn.execute(
            """
            INSERT INTO ad_accounts
              (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
            VALUES ('google-a', 'client-a', 'google', '123-456-7890', 'Google A', 'USD', 'active', '2026-01-01', '2026-01-01')
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="assignment_conflict"):
            conn.execute(
                """
                INSERT INTO ad_accounts
                  (id, client_id, platform, external_account_id, name, currency, status, created_at, updated_at)
                VALUES ('google-c', 'client-c', 'google', '1234567890', 'Google C', 'USD', 'active', '2026-01-01', '2026-01-01')
                """
            )
