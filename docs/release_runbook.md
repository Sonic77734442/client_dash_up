# Release Baseline Runbook

## Runtime baseline
- Python: `3.11+` required for release pipelines.
- Node.js: `20+` for frontend build.

## CI quality gate
CI workflow (`.github/workflows/ci.yml`) enforces:
1. migration sanity check (`scripts/check_migrations.py`)
2. sqlite schema init check
3. backend tests (`pytest -q`)
4. frontend production build (`npm run build`)

## Pre-release checklist
1. Apply PostgreSQL migrations in order from `db/migrations/README.md`.
2. Run backend tests and frontend build locally.
3. Verify `.env` production security values:
- `APP_ENV=production`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_COOKIE_SAMESITE=lax|strict|none`
- `ALLOWED_ORIGINS` explicitly set (no wildcard in prod)
4. Run release gate:
```bash
./scripts/release_check.sh
```
5. Validate runtime endpoints on deployed revision:
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
6. Validate monitoring stack:
- Prometheus target `envidicy_api` is `UP`
- Grafana datasource `Prometheus` is healthy

## Deploy flow (compose baseline)
```bash
cp .env.prod.example .env.prod
./scripts/deploy_prod.sh
```

## Blue/Green rollout flow (compose baseline)
1. Deploy candidate slot and run canary smoke against candidate ports:
```bash
TOKEN=<admin_token> ./scripts/deploy_blue_green.sh deploy
```
2. Promote candidate to active ports:
```bash
TOKEN=<admin_token> ./scripts/deploy_blue_green.sh promote
```
3. Check slot state:
```bash
./scripts/deploy_blue_green.sh status
```

## Rollback flow (compose baseline)
1. Restore last DB backup (if needed):
```bash
SQLITE_BACKUP_FILE=backups/<backup_file>.db ./scripts/rollback_prod.sh
```
2. Re-run smoke checks:
```bash
./scripts/release_check.sh
```

## Backup (before migration)

### PostgreSQL
```bash
./scripts/backup_postgres.sh
```

### SQLite (local)
```bash
./scripts/backup_sqlite.sh
```

## Rollback strategy

### PostgreSQL
1. Put app in maintenance mode.
2. Restore latest successful dump:
```bash
./scripts/restore_postgres.sh backups/<dump_file>.dump
```
3. Re-deploy last known good backend image/commit.
4. Run smoke checks: `/health`, `/healthz`, `/readyz`, `/auth/me`, `/insights/overview`, `/agency/overview`.

### SQLite (local)
```bash
./scripts/restore_sqlite.sh backups/<backup_file>.db
```
Restart backend process.

## Operational telemetry baseline
- Access logs emit one JSON line per request with:
  - `request_id`, `method`, `path`, `status_code`, `duration_ms`, `client_ip`, `ts`
- Every response includes `X-Request-Id`.
- Metrics endpoint (`/metrics`) exposes:
  - `http_requests_total`
  - `http_request_duration_seconds_sum`
  - `http_request_duration_seconds_count`
  - `app_uptime_seconds`
- Prometheus alerts:
  - API down
  - high 5xx ratio
  - elevated average latency
  - sync-run failure spike
  - readiness probe failures

## Provider edge-case validation
Before promoting a release with integration changes:
```bash
TOKEN=<admin_token> API_BASE=http://127.0.0.1:8000 ./scripts/provider_edge_probe.sh
```
Capture:
- provider sync readiness and missing requirements
- sync error code/category distribution
- retryable failure count

## Notes
- Keep migrations forward-only in normal release path.
- Avoid hotfix SQL on production without adding matching migration file.
