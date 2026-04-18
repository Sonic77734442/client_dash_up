# Local Migration Order

Apply in order (idempotent SQL files):

1. `0001_create_budgets.sql`
2. `0002_budget_scope_overlap_history.sql`
3. `0003_budget_overlap_exclusion_constraints.sql`
4. `0004_create_clients.sql`
5. `0005_create_ad_accounts.sql`
6. `0006_create_ad_stats.sql`
7. `0007_create_ad_stats_ingest_idempotency.sql`
8. `0008_create_auth_architecture_tables.sql`
9. `0009_create_budget_transfers.sql`
10. `0010_create_ad_account_sync_jobs.sql`
11. `0011_create_platform_admin_agencies.sql`
12. `0012_create_oauth_states.sql`
13. `0013_alter_oauth_states_add_nonce.sql`
14. `0014_create_agency_invites.sql`
15. `0015_alter_ad_account_sync_jobs_retry_fields.sql`

## Local PostgreSQL example
```bash
psql "$DATABASE_URL" -f db/migrations/0001_create_budgets.sql
psql "$DATABASE_URL" -f db/migrations/0002_budget_scope_overlap_history.sql
psql "$DATABASE_URL" -f db/migrations/0003_budget_overlap_exclusion_constraints.sql
psql "$DATABASE_URL" -f db/migrations/0004_create_clients.sql
psql "$DATABASE_URL" -f db/migrations/0005_create_ad_accounts.sql
psql "$DATABASE_URL" -f db/migrations/0006_create_ad_stats.sql
psql "$DATABASE_URL" -f db/migrations/0007_create_ad_stats_ingest_idempotency.sql
psql "$DATABASE_URL" -f db/migrations/0008_create_auth_architecture_tables.sql
psql "$DATABASE_URL" -f db/migrations/0009_create_budget_transfers.sql
psql "$DATABASE_URL" -f db/migrations/0010_create_ad_account_sync_jobs.sql
psql "$DATABASE_URL" -f db/migrations/0011_create_platform_admin_agencies.sql
psql "$DATABASE_URL" -f db/migrations/0012_create_oauth_states.sql
psql "$DATABASE_URL" -f db/migrations/0013_alter_oauth_states_add_nonce.sql
psql "$DATABASE_URL" -f db/migrations/0014_create_agency_invites.sql
psql "$DATABASE_URL" -f db/migrations/0015_alter_ad_account_sync_jobs_retry_fields.sql
```

## SQLite local runtime
For local API runtime, schema init is automatic from `app/db.py` (`init_sqlite`).
