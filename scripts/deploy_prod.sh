#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"

cd "$ROOT_DIR"

echo "[deploy] compose up"
docker compose -f "$COMPOSE_FILE" up -d --build

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
