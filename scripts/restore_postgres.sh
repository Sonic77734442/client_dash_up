#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <backup_file.dump> --confirm-replace-target"
  exit 1
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required"
  exit 1
fi

SRC="$1"
if [[ ! -f "$SRC" ]]; then
  echo "backup file not found: $SRC"
  exit 1
fi
if [[ "${2:-}" != "--confirm-replace-target" ]]; then
  echo "restore refused: this operation cleans and replaces the target database"
  echo "use only a disposable restore-verification target and pass --confirm-replace-target"
  exit 1
fi

pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$DATABASE_URL" "$SRC"
echo "PostgreSQL restore completed"
