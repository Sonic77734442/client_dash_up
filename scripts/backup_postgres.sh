#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required"
  exit 1
fi

for tool in pg_dump pg_restore psql mktemp; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Required PostgreSQL backup tool is missing: $tool" >&2
    exit 1
  fi
done
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"
export LC_ALL=C

DUMP_VERSION="$(pg_dump --version)"
if [[ ! "$DUMP_VERSION" =~ ([0-9]+)\.[0-9]+ ]]; then
  echo "Cannot determine pg_dump major version" >&2
  exit 1
fi
DUMP_MAJOR="${BASH_REMATCH[1]}"
SERVER_VERSION="$(psql -X -At --no-password --set ON_ERROR_STOP=1 --dbname "$DATABASE_URL" --command 'SHOW server_version_num')"
SERVER_VERSION="${SERVER_VERSION//$'\r'/}"
if [[ ! "$SERVER_VERSION" =~ ^[0-9]+$ ]]; then
  echo "Cannot determine PostgreSQL server version" >&2
  exit 1
fi
SERVER_MAJOR=$((SERVER_VERSION / 10000))
if (( DUMP_MAJOR < SERVER_MAJOR )); then
  echo "pg_dump $DUMP_MAJOR cannot back up PostgreSQL $SERVER_MAJOR; install client tools for server major $SERVER_MAJOR or newer" >&2
  exit 1
fi
if (( DUMP_MAJOR > SERVER_MAJOR )); then
  echo "pg_dump $DUMP_MAJOR is newer than server $SERVER_MAJOR; use matching client tools if this backup must restore to PostgreSQL $SERVER_MAJOR" >&2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
PARTIAL="$(mktemp "$BACKUP_DIR/envidicy_${STAMP}_XXXXXX.partial")"
OUT="${PARTIAL%.partial}.dump"
cleanup_partial() { rm -f -- "$PARTIAL"; }
trap cleanup_partial EXIT

pg_dump --no-password --format=custom --file "$PARTIAL" "$DATABASE_URL"
# Do not publish a failed/truncated archive as a completed backup.
pg_restore --list "$PARTIAL" >/dev/null
mv -- "$PARTIAL" "$OUT"
trap - EXIT
echo "$OUT"
