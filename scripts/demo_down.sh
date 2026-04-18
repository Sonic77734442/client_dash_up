#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACK_PID_FILE="$ROOT_DIR/storage/demo_backend.pid"
FRONT_PID_FILE="$ROOT_DIR/storage/demo_frontend.pid"

stop_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true
    fi
    rm -f "$pid_file"
  fi
}

stop_pid "$BACK_PID_FILE"
stop_pid "$FRONT_PID_FILE"

echo "demo services stopped"
