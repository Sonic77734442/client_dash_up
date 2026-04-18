#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"

cd "$ROOT_DIR"

echo "[rollback] stopping current stack"
docker compose -f "$COMPOSE_FILE" down

echo "[rollback] restore sqlite backup if provided"
if [[ -n "${SQLITE_BACKUP_FILE:-}" ]]; then
  "$ROOT_DIR/scripts/restore_sqlite.sh" "$SQLITE_BACKUP_FILE"
fi

echo "[rollback] starting stack"
docker compose -f "$COMPOSE_FILE" up -d --build

echo "[rollback] done"
