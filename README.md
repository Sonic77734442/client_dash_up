# Envidicy Dashboard Backend (Digital-First)

Backend for digital analytics with normalized spend, manual budgets, and unified dashboard overview.

## Modules
- Clients (`/clients`)
- Ad Accounts (`/ad-accounts`)
- Ad Stats ingestion + aggregation (`/ad-stats`)
- Budgets + audit history (`/budgets`)
- Unified insights overview (`/insights/overview`)
- Operational recommendations (`/insights/operational`)
- Agency aggregation (`/agency/overview`)
- Local validation docs (`docs/auth_tenant_model.md`, `db/migrations/README.md`)
- Auth architecture prep (`/auth/*` internal endpoints, no OAuth flow yet)
- Platform admin provisioning (internal/admin-only): `/platform/agencies*`

## Core data invariants (must not be broken)
- Spend source of truth:
  - spend/metrics are sourced from ad platform APIs and normalized into `ad_stats`
  - platform-side campaign budgets are NOT used as client budget
- Budget source of truth:
  - client/account budgets are internal manual records in `budgets`
  - budget data is used only for pacing/remaining/forecast calculations
- No budget write-back:
  - this backend does NOT push internal budget values to Meta/Google/TikTok
  - ad platforms remain traffic sources; internal budgets remain governance data
- Tenant mapping:
  - external accounts are grouped under internal `client_id` via backend mapping
  - unmapped accounts must not pollute client financial rollups

## Budget rules
- `scope=client` => `account_id=null`
- `scope=account` => `account_id` is required
- Allocation cap policy:
  - account-scope budgets are limited by overlapping active client-scope budget
  - sum of active account-scope budgets (overlapping period) must be `<=` client budget
  - reducing a client budget below already allocated account budgets is rejected (`409`)
- Overlap prevention:
  - client scope conflicts only with active client-scope budgets for same `client_id`
  - account scope conflicts only with active account-scope budgets for same `account_id`
  - client/account scopes do not conflict with each other by default
- Enforcement model:
  - PostgreSQL: DB-enforced exclusion constraints (migration `0003_*`) + application checks
  - SQLite runtime: application-level checks in `BEGIN IMMEDIATE` write transaction
- `PATCH /budgets/{id}`:
  - history row is created only for meaningful business-field changes
  - no-op patch => no version bump, no history write
- `POST /budgets/{id}/transfer`:
  - atomic transfer between account-scope budgets within same client
  - decreases source and increases target in one transaction
  - creates/updates target account budget for overlapping period
- `GET /budgets/{id}/transfers`:
  - returns transfer audit rows for source/target participation
  - supports `direction=all|incoming|outgoing` and `limit` (1..200)
- `GET /budgets` status filter:
  - `active` (default)
  - `archived`
  - `all`

## Financial pace semantics
- UTC date basis
- Inclusive day count (`start_date` and `end_date` both included)
- `expected_spend_to_date = budget * (elapsed_days / total_days)`
- `forecast_spend = spend / (elapsed_days / total_days)`
- `pace_delta = spend - expected_spend_to_date`
- `pace_delta_percent = (pace_delta / expected_spend_to_date) * 100`
  - returns `null` when `expected_spend_to_date = 0`

## Operational insights engine (backend)
- Endpoint: `GET /insights/operational`
- Purpose: generate recommendation cards (`scale`, `cap`, `pause`, `review`) from spend efficiency + pacing signals.
- Inputs: same scope filters as overview (`client_id`, optional `account_id`, `date_from`, `date_to`, optional `as_of_date`).
- Output: ranked recommendation list with `priority`, `score`, `reason`, `metrics`.
- Rules are configurable via env JSON (no route-level hardcode):
  - `OPERATIONAL_INSIGHTS_RULES_JSON`
  - supported keys:
    - `max_items`
    - `min_spend_share_for_action`
    - `high_cpc_multiplier`
    - `low_cpc_multiplier`
    - `high_ctr_multiplier`
    - `low_ctr_multiplier`
    - `high_priority_score_threshold`
    - `medium_priority_score_threshold`
    - `pace_delta_abs_percent_for_review`
- Action execution endpoints:
  - `POST /insights/operational/actions` (queue/record selected action)
  - `GET /insights/operational/actions` (list action log by scope/client/account)

## Ad-stats idempotency (local validation level)
- `POST /ad-stats/ingest` accepts optional `Idempotency-Key` header.
- Same key + same payload => replay-safe response (`idempotency.replayed=true`).
- Same key + different payload => `409`.
- Upsert key for data rows remains `(ad_account_id, date, platform)`.

## Ad-accounts sync pipeline
- Sync run endpoint:
  - `POST /ad-accounts/sync/run`
  - optional filters: `account_ids[]`, `platform`, `date_from`, `date_to`
  - optional `force=true` bypasses retry-backoff skip checks
  - executes provider fetch per account and writes sync job rows
- Sync jobs endpoint:
  - `GET /ad-accounts/sync/jobs?account_id=&status=all|success|error&limit=...`
- Retry/backoff behavior:
  - failed retryable jobs are assigned `next_retry_at` using exponential backoff (1m,2m,4m... up to 60m)
  - next non-forced sync run skips accounts still in backoff window
  - `force=true` processes account immediately
- Error taxonomy on sync jobs/accounts:
  - `error_code` (`auth_failed|rate_limited|provider_unavailable|invalid_request|unknown_error|provider_not_supported`)
  - `error_category` (`auth|rate_limit|provider|validation|unknown|configuration`)
  - `retryable` and `next_retry_at`
- `GET /ad-accounts` now includes:
  - `last_sync_at`
  - `sync_status` (`success|error`)
  - `sync_error`
  - `sync_error_code`, `sync_error_category`, `sync_retryable`, `sync_next_retry_at`
- These fields are derived from sync runs / job history and are intended for Accounts Registry UI status cards and diagnostics.

## Auth & access architecture
- Internal users model is backend-owned (`users`).
- External identities are mapped in `auth_identities` (`provider + provider_user_id -> one internal user`).
- Backend-owned sessions/tokens are issued and validated by `/auth/internal/sessions/*`.
- Frontend-facing session endpoints:
  - `GET /auth/me`
  - `POST /auth/logout`
  - `POST /auth/session/refresh`
  - supports backend-owned `ops_session` httpOnly cookie (preferred)
  - Bearer token remains supported for local/internal tooling
- OAuth provider entrypoints:
  - `GET /auth/facebook/start` -> `GET /auth/facebook/callback`
  - `GET /auth/google/start` -> `GET /auth/google/callback`
  - callback resolves internal user via `auth_identities`, then issues backend-owned session token
  - callback sets `ops_session` httpOnly cookie and redirects to frontend login-success route
  - start/callback flow uses state + nonce cookie validation (double-submit) to harden CSRF protection
  - external auth does not grant authorization; tenant access still comes from role + `user_client_access`
- Lightweight provider-agnostic auth facade is available at `/auth/internal/facade/*`:
  - resolve/create internal user from external identity
  - link/upsert identity
  - issue/revoke/validate backend session context
- Conflict policy for facade resolve:
  - existing provider identity => resolves to the same internal user
  - new identity + existing email => `409` by default (no implicit merge)
  - explicit merge allowed only with `allow_email_merge=true`
- Provider config storage is in `auth_provider_configs` (Google/Facebook supported in baseline callback flow).
- All `/auth/internal/*` and `/auth/internal/facade/*` endpoints are temporary internal/admin-only plumbing for local validation.
- Agency provisioning for platform access is exposed via temporary internal/admin-only plumbing endpoints:
  - `POST /platform/agencies`
  - `GET /platform/agencies`
  - `PATCH /platform/agencies/{agency_id}`
  - `POST /platform/agencies/{agency_id}/members`
  - `POST /platform/agencies/{agency_id}/members/{member_id}/deactivate`
  - `DELETE /platform/agencies/{agency_id}/members/{member_id}`
  - `POST /platform/agencies/{agency_id}/clients`
  - `DELETE /platform/agencies/{agency_id}/clients/{access_id}`
  - `POST /platform/agencies/{agency_id}/invites`
  - `GET /platform/agencies/{agency_id}/invites`
  - `POST /platform/agencies/{agency_id}/invites/{invite_id}/revoke`
  - `POST /platform/agencies/{agency_id}/invites/{invite_id}/resend`
- Invite acceptance endpoint:
  - `POST /auth/invites/accept` (public onboarding path)
  - invite token is one-time and stored as hash in DB (`agency_invites.token_hash`)
- Agency client bindings materialize tenant grants into `user_client_access` for active agency members.
- ACL enforcement status (current):
  - business endpoints require authenticated session
  - tenant scope is enforced for non-admin users (`403` on cross-tenant access)
  - provider raw insights endpoints (`/meta/insights`, `/google/insights`, `/tiktok/insights`) are admin-only
  - cookie-auth mutation requests (`POST/PATCH/PUT/DELETE`) require CSRF double-submit header (`X-CSRF-Token`)
- sensitive auth/invite endpoints are rate-limited (in-memory baseline)
- runtime ops endpoints:
  - `GET /healthz` (liveness)
  - `GET /readyz` (readiness with store/db checks)
  - `GET /metrics` (Prometheus-style text metrics)
- request tracing baseline:
  - response header `X-Request-Id`
  - JSON access logs with latency/status/path

## Setup
Prerequisites:
- Python `3.12+` (recommended for release parity)
- Node.js `20+`

1. `python3 -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env`
5. `cp accounts.example.json accounts.json` (only for external ad API endpoints)
6. For production baseline config template:
  - `cp .env.prod.example .env`

## Demo seed (one command)
- Seed local demo data for frontend development:
  - `python3 scripts/seed_demo.py`
- Optional keep existing DB:
  - `python3 scripts/seed_demo.py --no-reset`
- Script creates:
  - demo internal users (admin/agency/client)
  - demo clients
  - demo ad accounts
  - demo budgets
  - demo ad_stats
  - demo sessions/tokens

## Demo quick start (presentation mode)
- Start backend + frontend + fresh seed:
  - `./scripts/demo_up.sh`
- Stop demo services:
  - `./scripts/demo_down.sh`
- Smoke-check core demo APIs:
  - `./scripts/demo_check.sh`
- Generated artifacts:
  - `storage/demo_seed.json` (tokens and seeded ids)
  - `storage/demo_backend.log`
  - `storage/demo_frontend.log`

## Sync transparency
- `GET /integrations/overview` is the source of truth for provider readiness (Meta/Google/TikTok).
- Frontend surfaces:
  - `connection_sources` (e.g. `identity_link`, `provider_config`, `env_credentials`)
  - `missing_requirements`
  - `sync_ready`

## Tenant provider credentials (MCC/BM per tenant)
- New internal/admin plumbing endpoints:
  - `POST /platform/integration-credentials`
  - `GET /platform/integration-credentials`
  - `PATCH /platform/integration-credentials/{credential_id}`
  - `DELETE /platform/integration-credentials/{credential_id}` (archives credential)
- Supported scopes:
  - `global` (fallback)
  - `agency` (shared by agency for bound clients)
  - `client` (highest priority for that client)
- Runtime resolution order for `discover` + `sync`:
  - `client` -> `agency` -> `global`
- This enables different Google MCC / Meta BM credentials per client or agency without changing global ENV.
  - `sync_readiness_reason`
  - `identity_linked_users`
- This is shown in Integrations Hub and Sync Monitor so agency onboarding state is explicit.

## Audit trail
- Unified audit log endpoint:
  - `GET /audit/logs` (admin-only)
- Sensitive actions now produce audit events:
  - tenant access assignments (`access.assigned`, `agency.client_access.assigned`)
  - budget mutations (`budget.created`, `budget.updated`, `budget.archived`, `budget.transferred`)
  - sync runs (`sync.run`)

## Release gate smoke
- One-command release checks:
  - `./scripts/release_check.sh`
- Inputs:
  - `TOKEN` (admin session token), optional when `storage/demo_seed.json` exists
  - `API_BASE` (default `http://127.0.0.1:8000`)
  - `RUN_TESTS=0` or `RUN_FRONTEND_BUILD=0` to skip heavy stages

## Production stack (docker compose)
- Compose file:
  - `docker-compose.prod.yml`
- Services:
  - `api` (`:8000`)
  - `frontend` (`:5173`)
  - `prometheus` (`:9090`)
  - `alertmanager` (`:9093`)
  - `grafana` (`:3001`)
- Run:
  - `cp .env.prod.example .env.prod`
  - `./scripts/deploy_prod.sh`
- Rollback baseline:
  - `SQLITE_BACKUP_FILE=backups/<file>.db ./scripts/rollback_prod.sh`
- Systemd template:
  - `deploy/systemd/envidicy-dashboard.service`
- Blue/green rollout baseline:
  - `./scripts/deploy_blue_green.sh deploy` (candidate slot + canary checks)
  - `./scripts/deploy_blue_green.sh promote` (switch active slot)
  - `./scripts/deploy_blue_green.sh status`

## SLO alerts and dashboards
- Prometheus alert rules:
  - `deploy/prometheus/alerts.yml`
- Alertmanager config:
  - `deploy/prometheus/alertmanager.yml`
- Grafana provisioning:
  - datasource: `deploy/grafana/provisioning/datasources/prometheus.yml`
  - dashboard provider: `deploy/grafana/provisioning/dashboards/dashboards.yml`
  - SLO dashboard JSON: `deploy/grafana/dashboards/slo-overview.json`

## Provider edge-case probe
- Synthetic resilience probe for sync providers:
  - `TOKEN=<admin_token> API_BASE=http://127.0.0.1:8000 ./scripts/provider_edge_probe.sh`

## Run
- `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Frontend (Next.js)
- Run frontend:
  - `cd frontend`
  - `npm install`
  - `npm run dev`
  - open `http://localhost:5173`
- Configure API base:
  - create `frontend/.env.local` with `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000`
- Session token:
  - use seeded token from `python3 scripts/seed_demo.py` output (`sessions.admin_token` or `sessions.agency_token`)

## Local migration stability
- Migration order and local commands are documented in `db/migrations/README.md`.
- SQLite local runtime schema is auto-initialized by `init_sqlite`.
- Migration sanity checker: `python scripts/check_migrations.py`

## PostgreSQL migrations
- `0001_create_budgets.sql`
- `0002_budget_scope_overlap_history.sql`
- `0003_budget_overlap_exclusion_constraints.sql`
- `0004_create_clients.sql`
- `0005_create_ad_accounts.sql`
- `0006_create_ad_stats.sql`
- `0007_create_ad_stats_ingest_idempotency.sql`
- `0008_create_auth_architecture_tables.sql`
- `0009_create_budget_transfers.sql`
- `0010_create_ad_account_sync_jobs.sql`
- `0011_create_platform_admin_agencies.sql`
- `0012_create_oauth_states.sql`
- `0013_alter_oauth_states_add_nonce.sql`
- `0014_create_agency_invites.sql`
- `0015_alter_ad_account_sync_jobs_retry_fields.sql`

## Deferred for later stage
- Full auth implementation (middleware/policies/token lifecycle)
- Full observability + SLO instrumentation

## CI release gate
- GitHub Actions workflow: `.github/workflows/ci.yml`
- Enforced checks:
  - migration sanity check
  - sqlite schema init check
  - `pytest -q`
  - `frontend npm run build`

## Backup / rollback
- See release runbook: `docs/release_runbook.md`
- helper scripts:
  - `./scripts/backup_sqlite.sh`
  - `./scripts/restore_sqlite.sh <backup_file.db>`
  - `./scripts/backup_postgres.sh` (requires `DATABASE_URL`)
  - `./scripts/restore_postgres.sh <backup_file.dump>` (requires `DATABASE_URL`)

## Standard error envelope
All application errors are returned as:
```json
{
  "error": {
    "code": "some_error_code",
    "message": "Human-readable message",
    "details": {}
  }
}
```

Examples:
```json
{
  "error": {
    "code": "conflict",
    "message": "Active budget overlap for account scope id=...",
    "details": {}
  }
}
```

```json
{
  "error": {
    "code": "validation_error",
    "message": "Validation failed",
    "details": {
      "errors": []
    }
  }
}
```
