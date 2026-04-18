#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
TOKEN="${TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "TOKEN is required"
  exit 1
fi

echo "[probe] integrations overview"
ov=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/integrations/overview")
echo "$ov" | python3 -c 'import json, sys
try:
    body = json.load(sys.stdin)
except Exception as exc:
    print(f"failed_to_parse_integrations_overview_json: {exc}")
    sys.exit(1)
for p in body.get("providers", []):
    print(
        "provider={provider} status={status} ready={ready} source={source} missing={missing}".format(
            provider=p.get("provider"),
            status=p.get("status"),
            ready=p.get("sync_ready"),
            source=",".join(p.get("connection_sources") or []),
            missing=",".join(p.get("missing_requirements") or []),
        )
    )'

echo "[probe] trigger forced sync for all visible accounts"
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$API_BASE/ad-accounts/sync/run" \
  -d '{"force":true}' >/dev/null

echo "[probe] summarize latest sync jobs"
curl -fsS -H "Authorization: Bearer $TOKEN" "$API_BASE/ad-accounts/sync/jobs?status=all&limit=200" | python3 -c 'import json, sys
from collections import Counter
try:
    body = json.load(sys.stdin)
except Exception as exc:
    print(f"failed_to_parse_sync_jobs_json: {exc}")
    sys.exit(1)
rows = body.get("items", [])
by_status = Counter(r.get("status") for r in rows)
by_code = Counter((r.get("error_code") or "none") for r in rows)
by_cat = Counter((r.get("error_category") or "none") for r in rows)
retryable = sum(1 for r in rows if r.get("retryable"))
print("jobs=", len(rows))
print("status=", dict(by_status))
print("error_code=", dict(by_code))
print("error_category=", dict(by_cat))
print("retryable_jobs=", retryable)'

echo "[probe] done"
