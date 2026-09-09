import sqlite3

import pytest

from app.db import init_sqlite, sqlite_conn


def test_context_commits_and_closes_connection(tmp_path):
    path = str(tmp_path / "commit.db")
    with sqlite_conn(path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO items VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    with sqlite_conn(path) as reopened:
        assert reopened.execute("SELECT id FROM items").fetchone()["id"] == 1


def test_exception_rolls_back_and_closes_connection(tmp_path):
    path = str(tmp_path / "rollback.db")
    with sqlite_conn(path) as setup:
        setup.execute("CREATE TABLE items (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="operation failed"):
        with sqlite_conn(path) as conn:
            conn.execute("INSERT INTO items VALUES (1)")
            raise RuntimeError("operation failed")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    with sqlite_conn(path) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_commit_failure_still_releases_handle_and_rolls_back(tmp_path):
    path = str(tmp_path / "failed-commit.db")
    with sqlite_conn(path) as setup:
        setup.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        setup.execute("CREATE TABLE child (id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite_conn(path) as conn:
            conn.execute("INSERT INTO child VALUES (99)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    with sqlite_conn(path) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0


def test_direct_connection_api_and_foreign_keys_are_preserved(tmp_path):
    conn = sqlite_conn(str(tmp_path / "direct.db"))
    try:
        assert isinstance(conn, sqlite3.Connection)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("SELECT 7 AS value").fetchone()["value"] == 7
        conn.commit()
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_schema_initialization_closes_its_connection(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    opened = []

    def tracked_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    init_sqlite(str(tmp_path / "initialized.db"))
    assert opened
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")
