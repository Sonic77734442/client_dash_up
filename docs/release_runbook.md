# Release Baseline Runbook

## Runtime baseline
- Python: `3.12+` required for release pipelines.
- Node.js: `20+` for frontend build.

## CI quality gate
CI workflow (`.github/workflows/ci.yml`) enforces:
1. Python and npm dependency vulnerability audits
2. migration sanity check (`scripts/check_migrations.py`)
3. SQLite schema init plus fresh/idempotent PostgreSQL 16 migrations
4. backend tests on SQLite plus PostgreSQL runtime/race contracts (`pytest -q`)
5. frontend lint and TypeScript checks
6. frontend production build (`npm run build`)

## Pre-release checklist
1. Keep `DATABASE_BACKEND=sqlite` for the compatibility release. Verify the Render persistent disk is mounted at `/var/data`, set `BUDGETS_DB_PATH=/var/data/budgets.db`, and create a timestamped SQLite backup before deploying.
2. Run backend tests and frontend build locally.
3. Verify `.env` production security values:
- `APP_ENV=production`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_COOKIE_SAMESITE=lax`
- `ALLOWED_ORIGINS=https://dash.envidicy.kz`
- `FRONTEND_BASE_URL=https://dash.envidicy.kz`
- `BUDGETS_DB_PATH=/var/data/budgets.db`
- `DATABASE_BACKEND=sqlite` until the PostgreSQL cutover step
- `DATABASE_AUTO_MIGRATE=false`
- `DATABASE_URL=<Render internal PostgreSQL URL>` only in Render's secret manager
- `INTEGRATION_CREDENTIAL_ENCRYPTION_KEYS=<JSON keyring secret>`
- `INTEGRATION_CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID=<active non-secret key id>`
- `METRICS_BEARER_TOKEN=<long random secret>` when `OBSERVABILITY_PUBLIC=false`
- `GRAFANA_ADMIN_PASSWORD=<unique random secret>` (required; no default password)
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

## Render PostgreSQL staged rollout

1. Provision PostgreSQL in the same Render region as the API and attach its
   internal connection string as the secret `DATABASE_URL`. Keep
   `DATABASE_BACKEND=sqlite`; an injected URL alone never changes storage.
2. Configure the encryption keyring, deploy the dual-read/encrypted-write build,
   verify OAuth and provider sync, then dry-run/apply both credential rotations.
3. For a new empty PostgreSQL database, run `python scripts/migrate_postgres.py`
   as Render's pre-deploy command. The runner has an advisory lock, ledger, and
   checksums; it refuses to adopt an existing untracked application schema.
4. If both the SQLite source and PostgreSQL target have been explicitly
   verified to contain zero application rows, record that evidence and skip
   data transfer. Otherwise stop scheduler/cron and all writes before moving
   existing SQLite data. Keep provider budget commands disabled during
   transfer. Preserve UUIDs, timestamps, encrypted envelopes, and serial
   sequences; validate row counts, conflicts, decryptability, and unresolved
   provider commands.
5. Set `DATABASE_BACKEND=postgresql` on one isolated API canary, leave
   `DATABASE_AUTO_MIGRATE=false`, keep every write path frozen, and smoke
   `/readyz`, login, tenant reads, audit reads, sync-lease reads, and backup
   creation. Verify a restore only against a separate disposable database/URL,
   never the canary or production database; the restore helper cleans and
   replaces its target. `/readyz` reports `database_backend=postgresql` to an
   authenticated admin.
6. Drain and stop every SQLite-backed API, scheduler, and worker. Set
   `DATABASE_BACKEND=postgresql` for the full fleet, redeploy, direct 100% of
   traffic to it, and verify every instance reports PostgreSQL in `/readyz`.
7. Enable the scheduler and provider money writes only after the full fleet is
   stable. A simple flag rollback to the unchanged SQLite source is safe only
   while writes are still frozen. After any live PostgreSQL write, rollback
   requires another write freeze plus an explicit restore/reverse transfer;
   never run writable SQLite and PostgreSQL fleets at the same time or merge
   their writes.

## Deploy flow (compose baseline)
Before starting compose, write the exact `METRICS_BEARER_TOKEN` value to
`./storage/metrics_token` with no quotes. The API data volume is mounted at
`/var/data`, matching `BUDGETS_DB_PATH=/var/data/budgets.db`.

```bash
cp .env.prod.example .env.prod
# Fill every required secret, including GRAFANA_ADMIN_PASSWORD, before deploy.
chmod 600 .env.prod storage/metrics_token
./scripts/deploy_prod.sh
```

The deploy, rollback, and blue/green scripts pass `.env.prod` to Compose
explicitly so it is used both for container environment values and Compose
variable interpolation. To use another secret file, set an absolute path with
`PROD_ENV_FILE=/path/to/env.prod`. Published ports bind to `127.0.0.1` by default;
override the matching `*_BIND_HOST` only when a service must be exposed directly.
Compose explicitly blanks `GRAFANA_ADMIN_PASSWORD` inside the API container.
For an existing `grafana_data` volume created with an old default password,
rotate the Grafana admin credential separately; changing the initialization
environment does not update an already-created Grafana user.

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

Configure the backend with `FRONTEND_BASE_URL=https://dash.envidicy.kz`,
`AUTH_COOKIE_SECURE=true`, and `AUTH_COOKIE_SAMESITE=lax`.

Register the following exact OAuth callback URLs in Google/Facebook and in the
backend provider configuration:

- `https://dash.envidicy.kz/api/backend/auth/google/callback`
- `https://dash.envidicy.kz/api/backend/auth/facebook/callback`

Release verification:

1. `GET /api/backend/healthz` returns `200`.
2. Password login returns two separate `Set-Cookie` headers for `ops_session`
   and `ops_csrf`.
3. `GET /api/backend/auth/me` succeeds after login without a bearer token.
4. Google and Facebook return through the Vercel callback URLs.
5. Logout invalidates the session and clears both cookies.
