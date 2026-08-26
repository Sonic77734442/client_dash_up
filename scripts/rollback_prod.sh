#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"
PROD_ENV_FILE="${PROD_ENV_FILE:-$ROOT_DIR/.env.prod}"

if [[ ! -f "$PROD_ENV_FILE" ]]; then
  echo "[rollback] Compose env file not found: $PROD_ENV_FILE"
  exit 1
fi

cd "$ROOT_DIR"

echo "[rollback] stopping current stack"
PROD_ENV_FILE="$PROD_ENV_FILE" docker compose --env-file "$PROD_ENV_FILE" -f "$COMPOSE_FILE" down

echo "[rollback] restore sqlite backup if provided"
if [[ -n "${SQLITE_BACKUP_FILE:-}" ]]; then
  "$ROOT_DIR/scripts/restore_sqlite.sh" "$SQLITE_BACKUP_FILE"
fi

echo "[rollback] starting stack"
PROD_ENV_FILE="$PROD_ENV_FILE" docker compose --env-file "$PROD_ENV_FILE" -f "$COMPOSE_FILE" up -d --build

echo "[rollback] done"
