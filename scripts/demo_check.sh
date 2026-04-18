#!/usr/bin/env bash
set -euo pipefail

API_BASE="${1:-http://127.0.0.1:8000}"
SEED_FILE="$(cd "$(dirname "$0")/.." && pwd)/storage/demo_seed.json"

if [[ ! -f "$SEED_FILE" ]]; then
  echo "seed file missing: $SEED_FILE"
  exit 1
fi

TOKEN="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path('storage/demo_seed.json')
obj = json.loads(p.read_text())
print(obj['sessions']['admin_token'])
PY
)"

curl -fsS "$API_BASE/healthz" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/integrations/overview" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/insights/overview?date_from=2026-04-01&date_to=2026-04-30" >/dev/null
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/agency/overview?date_from=2026-04-01&date_to=2026-04-30" >/dev/null

echo "demo check passed"
