#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB_PATH="${DB_PATH:-$ROOT_DIR/storage/budgets.db}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"

mkdir -p "$BACKUP_DIR"
if [[ ! -f "$DB_PATH" ]]; then
  echo "sqlite db not found: $DB_PATH"
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/budgets_${STAMP}.db"
cp "$DB_PATH" "$OUT"
echo "$OUT"
