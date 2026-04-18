#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"
RUN_TESTS="${RUN_TESTS:-1}"
RUN_FRONTEND_BUILD="${RUN_FRONTEND_BUILD:-1}"

TOKEN="${TOKEN:-}"
if [[ -z "$TOKEN" && -f "$ROOT_DIR/storage/demo_seed.json" ]]; then
  TOKEN="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path('storage/demo_seed.json')
if p.exists():
    obj = json.loads(p.read_text())
    print(obj.get('sessions', {}).get('admin_token', ''))
PY
)"
fi

if [[ -z "$TOKEN" ]]; then
  echo "TOKEN is required (env TOKEN=...) or storage/demo_seed.json must exist"
  exit 1
fi

echo "[1/6] backend health"
curl -fsS "$API_BASE/health" >/dev/null
curl -fsS "$API_BASE/healthz" >/dev/null
curl -fsS "$API_BASE/readyz" >/dev/null

echo "[2/6] auth/me"
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/auth/me" >/dev/null

echo "[3/6] tenant-sensitive endpoints"
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/insights/overview?date_from=2026-04-01&date_to=2026-04-30" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/agency/overview?date_from=2026-04-01&date_to=2026-04-30" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/integrations/overview" >/dev/null

echo "[4/6] sync and budgets"
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/ad-accounts/sync/jobs?status=all&limit=10" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/budgets?status=active" >/dev/null

echo "[5/6] metrics endpoint"
curl -fsS "$API_BASE/metrics" >/dev/null

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "[6/6] pytest"
  (cd "$ROOT_DIR" && PYTHONPATH=. .venv/bin/pytest -q)
else
  echo "[6/6] pytest skipped"
fi

if [[ "$RUN_FRONTEND_BUILD" == "1" ]]; then
  echo "[extra] frontend build"
  (cd "$ROOT_DIR/frontend" && npm run build)
else
  echo "[extra] frontend build skipped"
fi

echo "release check passed"
