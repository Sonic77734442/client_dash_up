# Local Migration Order

Migrations are immutable, ordered SQL files. Apply them with the checked runner,
which records a SHA-256 checksum in `public.schema_migrations`, serializes
concurrent release jobs with a PostgreSQL advisory lock, and stops on the first
error.

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
16. `0016_create_integration_credentials.sql`
17. `0017_add_solo_client_role.sql`
18. `0018_normalize_user_client_access_roles.sql`
19. `0019_encrypt_integration_credentials_and_bind_accounts.sql`
20. `0020_create_provider_budget_command_ledger.sql`
21. `0021_runtime_postgres_parity.sql`

## PostgreSQL

For a new, empty database:

```bash
python scripts/migrate_postgres.py
```

The runner refuses an existing application schema that has no migration ledger.
Do not bypass that guard: first inventory and baseline such a database explicitly.
In production, run this command as the release/pre-deploy step and keep
`DATABASE_AUTO_MIGRATE=false` in the web service.

## SQLite local runtime
For local API runtime, schema init is automatic from `app/db.py` (`init_sqlite`).
