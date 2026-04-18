#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <backup_file.db>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DB="${DB_PATH:-$ROOT_DIR/storage/budgets.db}"
SRC="$1"

if [[ ! -f "$SRC" ]]; then
  echo "backup file not found: $SRC"
  exit 1
fi

mkdir -p "$(dirname "$TARGET_DB")"
cp "$SRC" "$TARGET_DB"
echo "restored -> $TARGET_DB"
