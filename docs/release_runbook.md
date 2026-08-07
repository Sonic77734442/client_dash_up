# Release Baseline Runbook

## Runtime baseline
- Python: `3.12+` required for release pipelines.
- Node.js: `20+` for frontend build.

## CI quality gate
CI workflow (`.github/workflows/ci.yml`) enforces:
1. Python and npm dependency vulnerability audits
2. migration sanity check (`scripts/check_migrations.py`)
3. sqlite schema init check
4. backend tests (`pytest -q`)
5. frontend lint and TypeScript checks
6. frontend production build (`npm run build`)

## Pre-release checklist
1. Verify the Render persistent disk is mounted at `/var/data`, `BUDGETS_DB_PATH=/var/data/budgets.db`, and create a timestamped SQLite backup before deploying.
2. Run backend tests and frontend build locally.
3. Verify `.env` production security values:
- `APP_ENV=production`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_COOKIE_SAMESITE=lax`
- `ALLOWED_ORIGINS=https://client-dash-up.vercel.app`
- `FRONTEND_BASE_URL=https://client-dash-up.vercel.app`
- `BUDGETS_DB_PATH=/var/data/budgets.db`
- `METRICS_BEARER_TOKEN=<long random secret>` when `OBSERVABILITY_PUBLIC=false`
4. Run release gate:
```bash
./scripts/release_check.sh
```
5. Validate runtime endpoints on deployed revision:
- `GET /healthz`
- `GET /readyz`
- authenticated `GET /metrics` (admin bearer token or dedicated metrics service token)
6. Validate monitoring stack:
- Prometheus target `envidicy_api` is `UP`
- Grafana datasource `Prometheus` is healthy

## Deploy flow (compose baseline)
Before starting compose, write the exact `METRICS_BEARER_TOKEN` value to
`./storage/metrics_token` with no quotes. The API data volume is mounted at
`/var/data`, matching `BUDGETS_DB_PATH=/var/data/budgets.db`.

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

## Backup (before schema change or deploy)

### SQLite
```bash
./scripts/backup_sqlite.sh
```

On Render, back up `/var/data/budgets.db` to a timestamped file on the same persistent disk and verify that both files exist before deployment. A relative path or an ephemeral filesystem is not production-safe.

## Rollback strategy

### SQLite
```bash
./scripts/restore_sqlite.sh backups/<backup_file>.db
```
For Render, stop writes, restore the verified `/var/data` backup, and restart the backend process. Then run smoke checks: `/health`, `/healthz`, `/readyz`, `/auth/me`, `/insights/overview`, `/agency/overview`.

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
# Same-origin authentication gate

Before promoting the frontend, configure Vercel with:

- `NEXT_PUBLIC_API_BASE=/api/backend`
- `API_UPSTREAM_BASE=https://client-dash-up.onrender.com`
- `NEXT_PUBLIC_ENABLE_TOKEN_LOGIN=false`

Configure the backend with `FRONTEND_BASE_URL=https://client-dash-up.vercel.app`,
`AUTH_COOKIE_SECURE=true`, and `AUTH_COOKIE_SAMESITE=lax`.

Register the following exact OAuth callback URLs in Google/Facebook and in the
backend provider configuration:

- `https://client-dash-up.vercel.app/api/backend/auth/google/callback`
- `https://client-dash-up.vercel.app/api/backend/auth/facebook/callback`

Release verification:

1. `GET /api/backend/healthz` returns `200`.
2. Password login returns two separate `Set-Cookie` headers for `ops_session`
   and `ops_csrf`.
3. `GET /api/backend/auth/me` succeeds after login without a bearer token.
4. Google and Facebook return through the Vercel callback URLs.
5. Logout invalidates the session and clears both cookies.
