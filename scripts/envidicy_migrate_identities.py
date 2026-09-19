"""Migrate only reviewed legacy-user to ID bindings; dry-run unless --apply.

The manifest must be an absolute, owner-owned POSIX regular file, mode 0600,
without symlinks/hardlinks. Windows production apply is intentionally unsupported.
Required fields are documented by app.services.envidicy_migration.CONTRACT.
Repeat the identical manifest (including run_id/order/evidence refs) to resume;
the database audit rows are atomic checkpoints. Output contains no user data.
Deploy trusted session-origin handling and schema first. This command neither
installs schema, imports users into ID/My, grants access nor enables cutover.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.envidicy_migration import IdentityMigration, IdentityMigrationError, read_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.environ["DATABASE_AUTO_MIGRATE"] = "false"
    try:
        manifest = read_manifest(args.manifest_file)
        from app.settings import get_settings
        result = IdentityMigration(get_settings().budgets_db_path).run(manifest, apply=args.apply)
    except IdentityMigrationError as exc:
        result = {"status": "failed", "code": str(exc)}
    except Exception:
        result = {"status": "failed", "code": "migration_unavailable"}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
