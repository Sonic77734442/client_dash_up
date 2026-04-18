#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker-compose.prod.yml}"
STATE_FILE="${STATE_FILE:-$ROOT_DIR/storage/active_slot.txt}"
ACTIVE_API_PORT="${ACTIVE_API_PORT:-8000}"
ACTIVE_WEB_PORT="${ACTIVE_WEB_PORT:-5173}"
CANDIDATE_API_PORT="${CANDIDATE_API_PORT:-18000}"
CANDIDATE_WEB_PORT="${CANDIDATE_WEB_PORT:-15173}"

cmd="${1:-deploy}"
mkdir -p "$ROOT_DIR/storage"
active_slot="blue"
if [[ -f "$STATE_FILE" ]]; then
  active_slot="$(cat "$STATE_FILE")"
fi
if [[ "$active_slot" != "blue" && "$active_slot" != "green" ]]; then
  active_slot="blue"
fi
if [[ "$active_slot" == "blue" ]]; then
  candidate_slot="green"
else
  candidate_slot="blue"
fi

candidate_project="envidicy-${candidate_slot}"
active_project="envidicy-${active_slot}"

case "$cmd" in
  deploy)
    echo "[bg] deploy candidate slot: $candidate_slot"
    (
      cd "$ROOT_DIR"
      API_BIND_PORT="$CANDIDATE_API_PORT" WEB_BIND_PORT="$CANDIDATE_WEB_PORT" \
      PROM_BIND_PORT=0 GRAFANA_BIND_PORT=0 ALERTMGR_BIND_PORT=0 \
      docker compose -f "$COMPOSE_FILE" -p "$candidate_project" up -d --build api frontend
    )

    echo "[bg] wait candidate ready"
    for i in {1..30}; do
      if curl -fsS "http://127.0.0.1:${CANDIDATE_API_PORT}/readyz" >/dev/null 2>&1; then
        break
      fi
      sleep 2
      if [[ "$i" == "30" ]]; then
        echo "[bg] candidate readiness timeout"
        exit 1
      fi
    done

    echo "[bg] canary release check"
    TOKEN="${TOKEN:-}" API_BASE="http://127.0.0.1:${CANDIDATE_API_PORT}" RUN_TESTS=0 RUN_FRONTEND_BUILD=0 \
      "$ROOT_DIR/scripts/release_check.sh"

    echo "[bg] candidate deploy complete on ports api:${CANDIDATE_API_PORT}, web:${CANDIDATE_WEB_PORT}"
    ;;

  promote)
    echo "[bg] promote candidate slot: $candidate_slot"

    (
      cd "$ROOT_DIR"
      docker compose -f "$COMPOSE_FILE" -p "$active_project" stop api frontend || true
      docker compose -f "$COMPOSE_FILE" -p "$active_project" rm -f api frontend || true

      API_BIND_PORT="$ACTIVE_API_PORT" WEB_BIND_PORT="$ACTIVE_WEB_PORT" \
      PROM_BIND_PORT=0 GRAFANA_BIND_PORT=0 ALERTMGR_BIND_PORT=0 \
      docker compose -f "$COMPOSE_FILE" -p "$candidate_project" up -d --build api frontend
    )

    echo "$candidate_slot" > "$STATE_FILE"

    echo "[bg] verify promoted slot"
    TOKEN="${TOKEN:-}" API_BASE="http://127.0.0.1:${ACTIVE_API_PORT}" RUN_TESTS=0 RUN_FRONTEND_BUILD=0 \
      "$ROOT_DIR/scripts/release_check.sh"

    echo "[bg] promoted. active slot: $candidate_slot"
    ;;

  status)
    echo "active_slot=$active_slot"
    echo "candidate_slot=$candidate_slot"
    echo "active_project=$active_project"
    echo "candidate_project=$candidate_project"
    ;;

  *)
    echo "usage: $0 <deploy|promote|status>"
    exit 1
    ;;
esac
