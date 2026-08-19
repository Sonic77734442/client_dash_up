#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SqliteProviderBudgetCommandStore


CONFIRMATION_PHRASE = "ALL API INSTANCES ARE STOPPED"


def _result(*, ok: bool, **values: object) -> None:
    print(json.dumps({"ok": ok, **values}, sort_keys=True))


def _read_only_in_progress_count(db_path: Path) -> int:
    # `mode=ro` plus query_only guarantees a dry-run cannot initialize or
    # migrate a database. This matters for backups and pre-deploy inspection.
    uri = f"{db_path.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_budget_commands'"
        ).fetchone()
        if not exists:
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM provider_budget_commands WHERE status='in_progress'"
        ).fetchone()
    return int(row[0] or 0)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Count interrupted Meta budget commands or, during a fully offline maintenance window, "
            "move them to UNKNOWN and quarantine their targets."
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
        help="Apply offline recovery; without this flag the command is count-only",
    )
    parser.add_argument(
        "--confirm-all-api-stopped",
        default="",
        metavar="PHRASE",
        help=f'For --apply, pass the exact phrase: "{CONFIRMATION_PHRASE}"',
    )
    args = parser.parse_args(argv)

    raw_path = str(args.db_path or "").strip()
    if not raw_path:
        _result(ok=False, code="provider_budget_recovery_db_missing", message="An existing database path is required.")
        return 2
    try:
        db_path = Path(raw_path).expanduser().resolve(strict=True)
    except OSError:
        _result(ok=False, code="provider_budget_recovery_db_missing", message="The selected database does not exist.")
        return 2
    if not db_path.is_file():
        _result(ok=False, code="provider_budget_recovery_db_invalid", message="The selected database path is not a file.")
        return 2
    if args.apply and args.confirm_all_api_stopped != CONFIRMATION_PHRASE:
        _result(
            ok=False,
            code="provider_budget_recovery_offline_confirmation_required",
            message="Stop every API/blue-green instance and pass the exact offline confirmation phrase.",
        )
        return 2

    try:
        before = _read_only_in_progress_count(db_path)
        if args.apply:
            store = SqliteProviderBudgetCommandStore(str(db_path))
            recovered = store.recover_interrupted_in_progress_offline(
                all_api_instances_stopped=True
            )
            after = store.count_interrupted_in_progress()
        else:
            recovered = 0
            after = before
    except Exception:
        # Count-only output and a constant error prevent database/provider
        # details from leaking into deployment logs.
        _result(
            ok=False,
            code="provider_budget_recovery_failed",
            message="Provider budget recovery failed safely.",
        )
        return 2

    _result(
        ok=True,
        dry_run=not args.apply,
        in_progress_before=before,
        recovered=recovered,
        in_progress_after=after,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
