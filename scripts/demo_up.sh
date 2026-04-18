#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-5173}"
BACK_PID_FILE="$ROOT_DIR/storage/demo_backend.pid"
FRONT_PID_FILE="$ROOT_DIR/storage/demo_frontend.pid"
BACK_LOG="$ROOT_DIR/storage/demo_backend.log"
FRONT_LOG="$ROOT_DIR/storage/demo_frontend.log"

mkdir -p "$ROOT_DIR/storage"

cd "$ROOT_DIR"

python3 scripts/seed_demo.py > "$ROOT_DIR/storage/demo_seed.json"

if [[ -f "$BACK_PID_FILE" ]] && kill -0 "$(cat "$BACK_PID_FILE")" 2>/dev/null; then
  kill "$(cat "$BACK_PID_FILE")" || true
fi
if [[ -f "$FRONT_PID_FILE" ]] && kill -0 "$(cat "$FRONT_PID_FILE")" 2>/dev/null; then
  kill "$(cat "$FRONT_PID_FILE")" || true
fi

cd "$ROOT_DIR"
nohup .venv/bin/python -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" > "$BACK_LOG" 2>&1 &
echo $! > "$BACK_PID_FILE"

cd "$ROOT_DIR/frontend"
nohup npm run dev -- --hostname "$WEB_HOST" --port "$WEB_PORT" > "$FRONT_LOG" 2>&1 &
echo $! > "$FRONT_PID_FILE"

sleep 2

echo "demo_seed: $ROOT_DIR/storage/demo_seed.json"
echo "backend: http://$API_HOST:$API_PORT (pid $(cat "$BACK_PID_FILE"))"
echo "frontend: http://$WEB_HOST:$WEB_PORT (pid $(cat "$FRONT_PID_FILE"))"
echo "logs:"
echo "  - $BACK_LOG"
echo "  - $FRONT_LOG"
