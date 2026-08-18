from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def _sql(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8").lower()


def test_integration_credential_migration_reconciles_connection_key_and_revision():
    sql = _sql("0019_encrypt_integration_credentials_and_bind_accounts.sql")
    add_connection = "add column if not exists connection_key text not null default 'default'"
    drop_old_index = "drop index if exists public.uq_integration_credentials_scope_provider"
    drop_null_unsafe_index = (
        "drop index if exists public.uq_integration_credentials_scope_provider_connection"
    )
    create_global_index = (
        "create unique index if not exists uq_integration_credentials_global_connection"
    )
    create_scoped_index = (
        "create unique index if not exists uq_integration_credentials_scoped_connection"
    )

    assert add_connection in " ".join(sql.split())
    assert drop_old_index in " ".join(sql.split())
    assert drop_null_unsafe_index in " ".join(sql.split())
    assert create_global_index in " ".join(sql.split())
    assert create_scoped_index in " ".join(sql.split())
    assert sql.index(drop_old_index) < sql.index(create_global_index)
    assert sql.index(drop_null_unsafe_index) < sql.index(create_global_index)
    assert sql.index("integration_credential_scope_duplicates") < sql.index(create_global_index)
    assert "where scope_type = 'global' and scope_id is null" in " ".join(sql.split())
    assert (
        "where scope_type in ('agency', 'client') and scope_id is not null"
        in " ".join(sql.split())
    )
    assert "add column if not exists semantic_revision integer not null default 1" in " ".join(sql.split())


def test_budget_ledger_migration_contains_durable_audit_snapshots():
    sql = _sql("0020_create_provider_budget_command_ledger.sql")
    assert sql.count("authorization_snapshot_json jsonb not null") == 2
    assert sql.count("credential_snapshot_json jsonb not null") == 2
