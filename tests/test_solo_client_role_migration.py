from __future__ import annotations

import sqlite3
from uuid import uuid4

from app.db import init_sqlite


def test_legacy_sqlite_users_role_migration_preserves_auth_graph(tmp_path):
    db_path = tmp_path / "legacy-users-role.db"
    user_id = str(uuid4())
    client_id = str(uuid4())
    identity_id = str(uuid4())
    session_id = str(uuid4())
    access_id = str(uuid4())
    now = "2026-08-10T12:00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE users (
              id TEXT PRIMARY KEY,
              email TEXT NULL UNIQUE,
              name TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('admin','agency','client')),
              password_hash TEXT NULL,
              status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE clients (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              legal_name TEXT NULL,
              status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','archived')),
              default_currency TEXT NOT NULL DEFAULT 'USD',
              timezone TEXT NULL,
              notes TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE auth_identities (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id),
              provider TEXT NOT NULL,
              provider_user_id TEXT NOT NULL,
              email TEXT NULL,
              email_verified INTEGER NULL,
              raw_profile TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(provider, provider_user_id)
            );
            CREATE TABLE sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id),
              token_hash TEXT NOT NULL UNIQUE,
              expires_at TEXT NOT NULL,
              revoked_at TEXT NULL,
              metadata TEXT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE user_client_access (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id),
              client_id TEXT NOT NULL REFERENCES clients(id),
              role TEXT NOT NULL CHECK (role IN ('agency','client')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(user_id, client_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, 'client', ?, 'active', ?, ?)",
            (user_id, "legacy@solo.test", "Legacy", "password-hash", now, now),
        )
        conn.execute(
            "INSERT INTO clients VALUES (?, 'Legacy client', NULL, 'active', 'USD', NULL, NULL, ?, ?)",
            (client_id, now, now),
        )
        conn.execute(
            "INSERT INTO auth_identities VALUES (?, ?, 'google', 'google-1', ?, 1, NULL, ?, ?)",
            (identity_id, user_id, "legacy@solo.test", now, now),
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, 'token-hash', '2027-01-01T00:00:00', NULL, NULL, ?, ?)",
            (session_id, user_id, now, now),
        )
        conn.execute(
            "INSERT INTO user_client_access VALUES (?, ?, ?, 'client', ?, ?)",
            (access_id, user_id, client_id, now, now),
        )
        conn.commit()

    init_sqlite(str(db_path))

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        user = conn.execute(
            "SELECT email, role, password_hash FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        assert user == ("legacy@solo.test", "client", "password-hash")
        assert conn.execute("SELECT user_id FROM auth_identities WHERE id=?", (identity_id,)).fetchone() == (user_id,)
        assert conn.execute("SELECT user_id FROM sessions WHERE id=?", (session_id,)).fetchone() == (user_id,)
        assert conn.execute(
            "SELECT user_id, client_id, role FROM user_client_access WHERE id=?",
            (access_id,),
        ).fetchone() == (user_id, client_id, "client")
        conn.execute(
            "INSERT INTO users (id, email, name, role, password_hash, status, created_at, updated_at) "
            "VALUES (?, ?, 'Owner', 'solo_client', NULL, 'active', ?, ?)",
            (str(uuid4()), "owner@solo.test", now, now),
        )
        conn.commit()
