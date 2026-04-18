from __future__ import annotations

import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MIGR_DIR = ROOT / "db" / "migrations"
README = MIGR_DIR / "README.md"


def main() -> int:
    sql_files = sorted(p.name for p in MIGR_DIR.glob("*.sql"))
    if not sql_files:
        print("No migration files found")
        return 1

    ids = []
    for name in sql_files:
        m = re.match(r"^(\d{4})_.*\.sql$", name)
        if not m:
            print(f"Invalid migration filename format: {name}")
            return 1
        ids.append(int(m.group(1)))
        if (MIGR_DIR / name).stat().st_size == 0:
            print(f"Empty migration file: {name}")
            return 1

    expected = list(range(ids[0], ids[0] + len(ids)))
    if ids != expected:
        print(f"Migration ids not contiguous: {ids}")
        return 1

    readme_text = README.read_text(encoding="utf-8") if README.exists() else ""
    missing = [name for name in sql_files if name not in readme_text]
    if missing:
        print("README missing migration references:")
        for name in missing:
            print(f"  - {name}")
        return 1

    print(f"Migration sanity check passed: {len(sql_files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
