from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID


POSTGRES_TRANSACTION_LOCK_ID = 7_318_042_611_214
POSTGRES_MIGRATION_LOCK_ID = 7_318_042_611_215
_POSTGRES_URL_ENV = "DATABASE_URL"
_initialized_database_urls: set[str] = set()
_initialization_lock = threading.Lock()


class DatabaseConfigurationError(RuntimeError):
    """The configured runtime database cannot be used safely."""


class DatabaseMigrationError(RuntimeError):
    """The PostgreSQL schema is missing, stale, or has changed unexpectedly."""


def database_url() -> str:
    return os.getenv(_POSTGRES_URL_ENV, "").strip()


def database_backend() -> str:
    raw = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
    if raw in {"postgres", "postgresql"}:
        return "postgresql"
    if raw == "sqlite":
        return "sqlite"
    raise DatabaseConfigurationError(
        "DATABASE_BACKEND must be either 'sqlite' or 'postgresql'"
    )


def is_postgres_runtime() -> bool:
    return database_backend() == "postgresql"


def _load_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - exercised by deployment validation
        raise DatabaseConfigurationError(
            "DATABASE_URL is configured but the PostgreSQL driver is unavailable. "
            "Install the production requirements before starting the application."
        ) from exc
    return psycopg, dict_row


def _migration_directory() -> Path:
    return Path(__file__).resolve().parent.parent / "db" / "migrations"


def _migration_files() -> list[Path]:
    directory = _migration_directory()
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not files:
        raise DatabaseMigrationError(f"No PostgreSQL migrations found in {directory}")
    return files


_TRANSACTION_LINE_RE = re.compile(r"^\s*(?:BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE)


def _migration_body(path: Path) -> str:
    # A few legacy files wrap themselves in BEGIN/COMMIT. The runner owns the
    # transaction so a failed migration and its ledger entry always roll back
    # together. Only standalone lines are removed; PL/pgSQL BEGIN blocks stay.
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not _TRANSACTION_LINE_RE.match(line)).strip()


def _migration_checksum(path: Path) -> str:
    return hashlib.sha256(_migration_body(path).encode("utf-8")).hexdigest()


def migrate_postgres(url: Optional[str] = None) -> list[str]:
    target_url = (url or database_url()).strip()
    if not target_url:
        raise DatabaseConfigurationError("DATABASE_URL is required for PostgreSQL migrations")

    psycopg, dict_row = _load_psycopg()
    applied_now: list[str] = []
    with psycopg.connect(target_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (POSTGRES_MIGRATION_LOCK_ID,))
        try:
            ledger_exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations') AS table_name"
            ).fetchone()
            if not ledger_exists or ledger_exists["table_name"] is None:
                existing_tables = conn.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name IN (
                        'budgets', 'clients', 'ad_accounts', 'users',
                        'integration_credentials', 'provider_budget_commands'
                      )
                    ORDER BY table_name
                    """
                ).fetchall()
                if existing_tables:
                    names = ", ".join(row["table_name"] for row in existing_tables)
                    raise DatabaseMigrationError(
                        "Refusing to adopt an existing untracked PostgreSQL schema "
                        f"({names}). Inventory and baseline it explicitly before migration."
                    )
            with conn.transaction():
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.schema_migrations (
                      name text PRIMARY KEY,
                      checksum text NOT NULL,
                      applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )

            migration_files = _migration_files()
            expected_names = {path.name for path in migration_files}
            recorded_names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM public.schema_migrations"
                ).fetchall()
            }
            unknown = sorted(recorded_names - expected_names)
            if unknown:
                raise DatabaseMigrationError(
                    "PostgreSQL schema contains migrations unknown to this release: "
                    + ", ".join(unknown)
                )

            for path in migration_files:
                checksum = _migration_checksum(path)
                row = conn.execute(
                    "SELECT checksum FROM public.schema_migrations WHERE name=%s",
                    (path.name,),
                ).fetchone()
                if row:
                    if row["checksum"] != checksum:
                        raise DatabaseMigrationError(
                            f"Applied migration {path.name} no longer matches its recorded checksum"
                        )
                    continue

                body = _migration_body(path)
                with conn.transaction():
                    # No parameters means psycopg uses PostgreSQL's simple-query
                    # protocol, which safely accepts the DO blocks and multiple
                    # DDL statements present in the existing migration files.
                    conn.execute(body)
                    conn.execute(
                        "INSERT INTO public.schema_migrations(name, checksum) VALUES (%s, %s)",
                        (path.name, checksum),
                    )
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (POSTGRES_MIGRATION_LOCK_ID,))
    return applied_now


def assert_postgres_schema_current(url: Optional[str] = None) -> None:
    target_url = (url or database_url()).strip()
    if not target_url:
        raise DatabaseConfigurationError("DATABASE_URL is required for PostgreSQL schema validation")

    psycopg, dict_row = _load_psycopg()
    expected = {path.name: _migration_checksum(path) for path in _migration_files()}
    try:
        with psycopg.connect(target_url, autocommit=True, row_factory=dict_row) as conn:
            table_exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations') AS table_name"
            ).fetchone()
            if not table_exists or table_exists["table_name"] is None:
                raise DatabaseMigrationError(
                    "PostgreSQL schema is not initialized; run scripts/migrate_postgres.py first"
                )
            rows = conn.execute(
                "SELECT name, checksum FROM public.schema_migrations ORDER BY name"
            ).fetchall()
    except DatabaseMigrationError:
        raise
    except Exception as exc:
        raise DatabaseConfigurationError("Unable to connect to the configured PostgreSQL database") from exc

    recorded = {row["name"]: row["checksum"] for row in rows}
    missing = sorted(set(expected) - set(recorded))
    changed = sorted(name for name, checksum in expected.items() if recorded.get(name) not in {None, checksum})
    unknown = sorted(set(recorded) - set(expected))
    if missing or changed or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing migrations: {', '.join(missing)}")
        if changed:
            details.append(f"checksum mismatch: {', '.join(changed)}")
        if unknown:
            details.append(f"unknown migrations: {', '.join(unknown)}")
        raise DatabaseMigrationError(
            "PostgreSQL schema is not current (" + "; ".join(details) + ")"
        )


def _auto_migrate_enabled() -> bool:
    raw = os.getenv("DATABASE_AUTO_MIGRATE")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("APP_ENV", "development").strip().lower() not in {"prod", "production"}


def init_runtime_database(sqlite_path: str) -> None:
    if database_backend() == "sqlite":
        # Lazy import avoids an import cycle: app.db owns the SQLite DDL while
        # its provider-budget store uses this module for runtime connections.
        from app.db import init_sqlite

        init_sqlite(sqlite_path)
        return

    url = database_url()
    if not url:
        raise DatabaseConfigurationError(
            "DATABASE_URL is required when DATABASE_BACKEND=postgresql"
        )

    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    if cache_key in _initialized_database_urls:
        return
    with _initialization_lock:
        if cache_key in _initialized_database_urls:
            return
        if _auto_migrate_enabled():
            migrate_postgres(url)
        else:
            assert_postgres_schema_current(url)
        _initialized_database_urls.add(cache_key)


def _translate_qmark_sql(statement: str) -> str:
    """Translate SQLite qmark parameters without touching quoted question marks."""
    out: list[str] = []
    index = 0
    length = len(statement)
    quote: Optional[str] = None
    dollar_tag: Optional[str] = None
    line_comment = False
    block_comment = False

    while index < length:
        char = statement[index]
        following = statement[index + 1] if index + 1 < length else ""

        if line_comment:
            out.append(char)
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            out.append(char)
            if char == "*" and following == "/":
                out.append(following)
                index += 2
                block_comment = False
            else:
                index += 1
            continue
        if dollar_tag is not None:
            if statement.startswith(dollar_tag, index):
                out.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                out.append(char)
                index += 1
            continue
        if quote is not None:
            out.append(char)
            if char == quote:
                if following == quote:
                    out.append(following)
                    index += 2
                    continue
                quote = None
            elif char == "\\" and following:
                out.append(following)
                index += 2
                continue
            index += 1
            continue

        if char == "-" and following == "-":
            out.extend((char, following))
            index += 2
            line_comment = True
            continue
        if char == "/" and following == "*":
            out.extend((char, following))
            index += 2
            block_comment = True
            continue
        if char in {"'", '"'}:
            out.append(char)
            quote = char
            index += 1
            continue
        if char == "$":
            tag_match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", statement[index:])
            if tag_match:
                dollar_tag = tag_match.group(0)
                out.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if char == "?":
            out.append("%s")
        else:
            out.append(char)
        index += 1
    return "".join(out)


_JSON_COLUMNS = {
    "metadata",
    "raw_profile",
    "request_meta",
    "response_json",
    "credentials_json",
    "previous_values",
    "new_values",
    "authorization_snapshot_json",
    "credential_snapshot_json",
    "metrics",
    "payload",
    "context_json",
}


def _split_sql_list(value: str) -> list[str]:
    # Runtime INSERT lists contain identifiers/placeholders only. Keeping the
    # parser deliberately small makes an unexpected expression fail visibly
    # instead of silently casting the wrong parameter.
    return [part.strip() for part in value.split(",")]


def _cast_json_placeholders(statement: str) -> str:
    json_names = "|".join(sorted(_JSON_COLUMNS, key=len, reverse=True))
    statement = re.sub(
        rf"(?i)(\b(?:{json_names})\b\s*(?:=|<>|!=)\s*)\?(?!\s*::jsonb)",
        r"\1?::jsonb",
        statement,
    )

    insert_match = re.search(
        r"(?is)\bINSERT\s+INTO\s+(?:public\.)?[A-Za-z_][A-Za-z0-9_]*\s*"
        r"\((?P<columns>.*?)\)\s*VALUES\s*\((?P<values>.*?)\)",
        statement,
    )
    if not insert_match:
        return statement
    columns = _split_sql_list(insert_match.group("columns"))
    values = _split_sql_list(insert_match.group("values"))
    if len(columns) != len(values):
        return statement
    changed = False
    for index, column in enumerate(columns):
        column_name = column.strip().strip('"').lower()
        if column_name in _JSON_COLUMNS and values[index] == "?":
            values[index] = "?::jsonb"
            changed = True
    if not changed:
        return statement
    start, end = insert_match.span("values")
    return statement[:start] + ", ".join(values) + statement[end:]


def _compat_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    return value


class CompatRow(Mapping[str, Any]):
    """A psycopg row with the subset of sqlite3.Row behavior used by stores."""

    def __init__(self, values: Mapping[str, Any]):
        self._keys = tuple(values.keys())
        self._values = tuple(_compat_value(values[key]) for key in self._keys)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self):
        return self._mapping.keys()


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> Optional[CompatRow]:
        row = self._cursor.fetchone()
        return CompatRow(row) if row is not None else None

    def fetchall(self) -> list[CompatRow]:
        return [CompatRow(row) for row in self._cursor.fetchall()]

    def __iter__(self) -> Iterator[CompatRow]:
        for row in self._cursor:
            yield CompatRow(row)


class PostgresConnection:
    dialect = "postgresql"

    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> PostgresCursor:
        normalized = statement.strip().rstrip(";").upper()
        cursor = self._connection.cursor()
        if normalized == "BEGIN IMMEDIATE":
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (POSTGRES_TRANSACTION_LOCK_ID,),
            )
            return PostgresCursor(cursor)
        cursor.execute(_translate_qmark_sql(_cast_json_placeholders(statement)), parameters)
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def runtime_conn(sqlite_path: str):
    if database_backend() == "sqlite":
        from app.db import sqlite_conn

        return sqlite_conn(sqlite_path)
    url = database_url()
    if not url:
        raise DatabaseConfigurationError(
            "DATABASE_URL is required when DATABASE_BACKEND=postgresql"
        )
    psycopg, dict_row = _load_psycopg()
    return PostgresConnection(
        psycopg.connect(url, autocommit=False, row_factory=dict_row)
    )


def table_exists(conn, table_name: str) -> bool:
    if getattr(conn, "dialect", "sqlite") == "postgresql":
        row = conn.execute("SELECT to_regclass(?) AS table_name", (f"public.{table_name}",)).fetchone()
        return bool(row and row["table_name"])
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn, table_name: str) -> set[str]:
    if getattr(conn, "dialect", "sqlite") == "postgresql":
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=?
            """,
            (table_name,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


@contextmanager
def postgres_connection(url: Optional[str] = None):
    """Raw psycopg connection for explicit tooling and integration tests."""
    target_url = (url or database_url()).strip()
    if not target_url:
        raise DatabaseConfigurationError("DATABASE_URL is required")
    psycopg, dict_row = _load_psycopg()
    with psycopg.connect(target_url, autocommit=False, row_factory=dict_row) as conn:
        yield conn
