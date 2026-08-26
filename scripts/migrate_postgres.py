from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime_db import (  # noqa: E402
    DatabaseConfigurationError,
    DatabaseMigrationError,
    assert_postgres_schema_current,
    migrate_postgres,
)


def main() -> int:
    try:
        applied = migrate_postgres()
        assert_postgres_schema_current()
    except (DatabaseConfigurationError, DatabaseMigrationError) as exc:
        print(f"PostgreSQL migration refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Connection and SQL errors are intentionally summarized. DATABASE_URL
        # and server details must never be printed by release tooling.
        print(f"PostgreSQL migration failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if applied:
        print(f"PostgreSQL migrations applied: {len(applied)}")
        for name in applied:
            print(f"  - {name}")
    else:
        print("PostgreSQL schema is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
