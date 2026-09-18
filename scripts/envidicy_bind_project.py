"""Explicit mapping, not migration. Dry-run unless --apply is supplied.

Run only in the chosen Dash deployment, with its normal runtime database config.
This tool never discovers tenants by name/email or rewrites an existing mapping.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.envidicy_bridge import SqlEnvidicyStore
from app.runtime_db import assert_postgres_schema_current, database_backend
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--operator-ref", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    # This operator tool never installs schema, even in dry-run mode.
    os.environ["DATABASE_AUTO_MIGRATE"] = "false"
    config = get_settings()
    try:
        if database_backend() == "postgresql":
            assert_postgres_schema_current()
        elif not Path(config.budgets_db_path).is_file():
            raise ValueError("Existing initialized SQLite database required; no file was created")
        store = SqlEnvidicyStore(SimpleNamespace(db_path=config.budgets_db_path))
        result = store.bind_project(args.organization_id, args.project_id, args.client_id,
                                    operator_ref=args.operator_ref, apply=args.apply)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        # Database/driver exceptions may contain connection or SQL details.
        print("Binding failed; verify the runtime database and migration status", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
