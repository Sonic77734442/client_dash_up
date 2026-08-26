#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"
PROD_ENV_FILE="${PROD_ENV_FILE:-$ROOT_DIR/.env.prod}"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"

if [[ ! -f "$PROD_ENV_FILE" ]]; then
  echo "[deploy] Compose env file not found: $PROD_ENV_FILE"
  echo "[deploy] copy .env.prod.example to .env.prod and set production secrets"
  exit 1
fi

cd "$ROOT_DIR"

echo "[deploy] compose up"
PROD_ENV_FILE="$PROD_ENV_FILE" docker compose --env-file "$PROD_ENV_FILE" -f "$COMPOSE_FILE" up -d --build

echo "[deploy] waiting for backend"
for i in {1..30}; do
  if curl -fsS "$API_BASE/readyz" >/dev/null 2>&1; then
    echo "[deploy] backend ready"
    break
  fi
  sleep 2
  if [[ "$i" == "30" ]]; then
    echo "[deploy] backend readiness timeout"
    exit 1
  fi
done

echo "[deploy] release check"
TOKEN="${TOKEN:-}" API_BASE="$API_BASE" "$ROOT_DIR/scripts/release_check.sh"

echo "[deploy] done"
