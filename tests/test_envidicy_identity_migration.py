from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from datetime import date
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.runtime_db import migrate_postgres, runtime_conn
from app.schemas import BudgetCreate, ClientCreate, UserClientAccessCreate, UserCreate
from app.services.auth_arch import SqliteAuthStore
from app.services.budgets import SqliteBudgetStore
from app.services.clients import SqliteClientStore
from app.services.envidicy_bridge import ISSUER, SqlEnvidicyStore
from app.services import envidicy_migration as migration


def manifest_for(*users):
    return {"contract_version": migration.CONTRACT, "run_id": str(uuid4()),
            "operator_ref": "fixture:approved-cutover",
            "rows": [{"legacy_user_id": str(user.id), "issuer": ISSUER,
                      "subject": "synthetic-id-subject-" + str(uuid4()),
                      "provenance_ref": "fixture:identity-proof", "validation_ref": "fixture:independent-review"}
                     for user in users]}


@pytest.fixture(params=["sqlite", "postgresql"])
def existing(request, monkeypatch, tmp_path):
    backend = request.param
    monkeypatch.setenv("DATABASE_BACKEND", backend)
    monkeypatch.setenv("DATABASE_AUTO_MIGRATE", "false")
    if backend == "postgresql":
        url = os.getenv("TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("TEST_DATABASE_URL required for isolated PostgreSQL rehearsal")
        parsed = urlsplit(url)
        assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        assert parsed.path.endswith(("_test", "_tests")), "Only a named loopback test database is permitted"
        monkeypatch.setenv("DATABASE_URL", url)
        migrate_postgres(url)
    path = str(tmp_path / "legacy.sqlite")
    auth = SqliteAuthStore(path)
    first = auth.create_user(UserCreate(name="Existing agency owner", email=f"legacy-{uuid4()}@example.invalid", role="agency"))
    second = auth.create_user(UserCreate(name="Existing client", email=f"legacy-{uuid4()}@example.invalid", role="client"))
    client = SqliteClientStore(path).create(ClientCreate(name="Existing report workspace"))
    auth.assign_client_access(UserClientAccessCreate(user_id=first.id, client_id=client.id, role="agency"))
    budget = SqliteBudgetStore(path).create(BudgetCreate(client_id=client.id, scope="client", amount="123.45", currency="USD",
        period_type="custom", start_date=date(2026, 1, 1), end_date=date(2026, 1, 31), created_by=first.id))
    with runtime_conn(path) as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", ("synthetic-existing-hash", str(first.id)))
    return SimpleNamespace(path=path, auth=auth, first=first, second=second, client=client, budget=budget,
                           tool=migration.IdentityMigration(path), store=SqlEnvidicyStore(auth))


def checkpoint_count(fixture, manifest):
    with runtime_conn(fixture.path) as conn:
        return conn.execute("SELECT count(*) AS total FROM envidicy_identity_migration_audit WHERE run_id=?", (manifest["run_id"],)).fetchone()["total"]


def business_snapshot(fixture):
    with runtime_conn(fixture.path) as conn:
        return {
            "user": dict(conn.execute("SELECT * FROM users WHERE id=?", (str(fixture.first.id),)).fetchone()),
            "client": dict(conn.execute("SELECT * FROM clients WHERE id=?", (str(fixture.client.id),)).fetchone()),
            "budget": dict(conn.execute("SELECT * FROM budgets WHERE id=?", (str(fixture.budget.id),)).fetchone()),
            "grants": [dict(row) for row in conn.execute("SELECT * FROM user_client_access WHERE user_id=?", (str(fixture.first.id),)).fetchall()],
        }


def test_dry_run_apply_repeat_preserve_original_user_and_business_links(existing):
    plan = manifest_for(existing.first)
    before = business_snapshot(existing)
    checked = existing.tool.run(plan)
    assert checked["status"] == "checked" and checked["checked"] == 1
    assert checkpoint_count(existing, plan) == 0
    assert existing.store.principal(existing.first.id) is None
    applied = existing.tool.run(plan, apply=True)
    assert applied["applied"] == 1 and applied["checkpoint_rows"] == 1 and applied["next_row"] is None
    repeated = existing.tool.run(plan, apply=True)
    assert repeated["unchanged"] == 1 and repeated["applied"] == 0
    assert checkpoint_count(existing, plan) == 1
    row = plan["rows"][0]
    assert existing.store.resolve_identity(row["issuer"], row["subject"]).id == existing.first.id
    assert business_snapshot(existing) == before


def test_exact_run_resumes_after_interruption(existing, monkeypatch):
    plan = manifest_for(existing.first, existing.second)
    original = existing.tool._row
    def interrupted(manifest, manifest_hash, number, row, *, apply):
        if number == 2:
            raise KeyboardInterrupt()
        return original(manifest, manifest_hash, number, row, apply=apply)
    monkeypatch.setattr(existing.tool, "_row", interrupted)
    with pytest.raises(KeyboardInterrupt):
        existing.tool.run(plan, apply=True)
    assert checkpoint_count(existing, plan) == 1
    monkeypatch.setattr(existing.tool, "_row", original)
    result = existing.tool.run(plan, apply=True)
    assert result["status"] == "applied" and result["unchanged"] == 1 and result["applied"] == 1
    assert checkpoint_count(existing, plan) == 2


def test_checkpoint_and_principal_commit_or_rollback_together(existing, monkeypatch):
    plan = manifest_for(existing.first)
    original = migration.runtime_conn
    @contextmanager
    def fail_audit(path):
        with original(path) as conn:
            def execute(sql, parameters=()):
                if sql.startswith("INSERT INTO envidicy_identity_migration_audit"):
                    raise RuntimeError("synthetic sensitive SQL detail must not escape")
                return conn.execute(sql, parameters)
            yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(migration, "runtime_conn", fail_audit)
    result = existing.tool.run(plan, apply=True)
    assert result["code"] == "migration_database_unavailable"
    assert "sensitive" not in json.dumps(result)
    assert existing.store.principal(existing.first.id) is None
    assert checkpoint_count(existing, plan) == 0


def test_prior_projection_or_other_identity_is_not_merged(existing):
    plan = manifest_for(existing.first)
    row = plan["rows"][0]
    projection = existing.store.resolve_identity(row["issuer"], row["subject"])
    result = existing.tool.run(plan, apply=True)
    assert result["code"] == "identity_binding_conflict"
    assert existing.store.principal(existing.first.id) is None
    assert existing.store.resolve_identity(row["issuer"], row["subject"]).id == projection.id
    assert checkpoint_count(existing, plan) == 0
    other = manifest_for(existing.first)
    assert existing.tool.run(other, apply=True)["applied"] == 1
    changed = manifest_for(existing.first)
    assert existing.tool.run(changed, apply=True)["code"] == "identity_binding_conflict"


def test_changed_manifest_or_checkpoint_binding_is_not_overwritten(existing):
    plan = manifest_for(existing.first)
    assert existing.tool.run(plan, apply=True)["applied"] == 1
    changed = copy.deepcopy(plan)
    changed["rows"][0]["validation_ref"] = "fixture:different-review"
    assert existing.tool.run(changed, apply=True)["code"] == "run_manifest_conflict"
    with runtime_conn(existing.path) as conn:
        conn.execute("DELETE FROM envidicy_id_principals WHERE user_id=?", (str(existing.first.id),))
    assert existing.tool.run(plan, apply=True)["code"] == "checkpoint_binding_conflict"
    assert existing.store.principal(existing.first.id) is None


def test_missing_inactive_and_duplicate_manifest_users_fail_closed(existing):
    plan = manifest_for(existing.first)
    with runtime_conn(existing.path) as conn:
        conn.execute("UPDATE users SET status='inactive' WHERE id=?", (str(existing.first.id),))
    assert existing.tool.run(plan, apply=True)["code"] == "existing_active_legacy_user_required"
    plan["rows"][0]["legacy_user_id"] = str(uuid4())
    assert existing.tool.run(plan, apply=True)["code"] == "existing_active_legacy_user_required"
    duplicate = manifest_for(existing.second, existing.second)
    with pytest.raises(migration.IdentityMigrationError, match="duplicate_manifest_mapping"):
        existing.tool.run(duplicate, apply=True)
    assert checkpoint_count(existing, plan) == 0


def test_concurrent_repeat_has_one_link_and_one_checkpoint(existing):
    plan = manifest_for(existing.first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: existing.tool.run(plan, apply=True), range(2)))
    assert all(result["status"] == "applied" for result in results)
    assert sum(result["applied"] for result in results) == 1
    assert sum(result["unchanged"] for result in results) == 1
    assert checkpoint_count(existing, plan) == 1


@pytest.mark.parametrize("shared_key", ["user", "principal"])
def test_concurrent_conflicting_manifests_never_replace_winner(existing, shared_key):
    first = manifest_for(existing.first)
    second = manifest_for(existing.first if shared_key == "user" else existing.second)
    if shared_key == "principal":
        second["rows"][0]["subject"] = first["rows"][0]["subject"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda plan: existing.tool.run(plan, apply=True), [first, second]))
    assert sum(result["status"] == "applied" for result in results) == 1
    assert [result["code"] for result in results if result["status"] == "failed"] == ["identity_binding_conflict"]
    winner = [first, second][next(index for index, result in enumerate(results) if result["status"] == "applied")]
    row = winner["rows"][0]
    assert str(existing.store.resolve_identity(row["issuer"], row["subject"]).id) == row["legacy_user_id"]
    assert checkpoint_count(existing, first) + checkpoint_count(existing, second) == 1


def test_exact_existing_binding_can_be_confirmed_without_mutation(existing):
    first = manifest_for(existing.first)
    assert existing.tool.run(first, apply=True)["applied"] == 1
    second = copy.deepcopy(first)
    second["run_id"] = str(uuid4())
    result = existing.tool.run(second, apply=True)
    assert result["unchanged"] == 1 and result["applied"] == 0 and result["checkpoint_rows"] == 1
    with runtime_conn(existing.path) as conn:
        row = conn.execute("SELECT action FROM envidicy_identity_migration_audit WHERE run_id=?", (second["run_id"],)).fetchone()
    assert row["action"] == "confirmed"


def test_postgres_schema_preflight_failure_cannot_attempt_a_binding(monkeypatch):
    monkeypatch.setattr(migration, "database_backend", lambda: "postgresql")
    def stale_schema():
        raise RuntimeError("fixture: stale schema with sensitive connection detail")
    def forbidden_connection(_):
        pytest.fail("Must not open a runtime connection after failed schema validation")
    monkeypatch.setattr(migration, "assert_postgres_schema_current", stale_schema)
    monkeypatch.setattr(migration, "runtime_conn", forbidden_connection)
    result = migration.IdentityMigration("unused").run(manifest_for(SimpleNamespace(id=uuid4())), apply=True)
    assert result["code"] == "migration_database_unavailable"
    assert "sensitive" not in json.dumps(result)


def test_missing_sqlite_and_uninitialized_schema_are_never_created(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    path = tmp_path / "absent.sqlite"
    plan = manifest_for(SimpleNamespace(id=uuid4()))
    assert migration.IdentityMigration(str(path)).run(plan)["code"] == "existing_sqlite_database_required"
    assert not path.exists()
    path.touch()
    assert migration.IdentityMigration(str(path)).run(plan)["code"] == "migration_database_unavailable"
    assert path.stat().st_size == 0


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="unexpected"),
    lambda p: p["rows"][0].update(email="not-an-identity@example.invalid"),
    lambda p: p["rows"][0].update(provenance_ref=""),
    lambda p: p["rows"][0].update(validation_ref=""),
    lambda p: p["rows"][0].update(issuer="https://other.invalid"),
    lambda p: p["rows"][0].update(subject="invalid\nsubject"),
    lambda p: p.update(operator_ref="not an approved reference"),
])
def test_manifest_requires_explicit_evidence_and_rejects_email_fields(mutation):
    plan = manifest_for(SimpleNamespace(id=uuid4()))
    mutation(plan)
    with pytest.raises(migration.IdentityMigrationError):
        migration.validate_manifest(plan)


@pytest.mark.parametrize("changes", [{"st_mode": stat.S_IFREG | 0o644}, {"st_mode": stat.S_IFLNK | 0o600},
    {"st_uid": 99}, {"st_nlink": 2}, {"st_size": 0}, {"st_size": migration.MAX_MANIFEST_BYTES + 1}])
def test_file_metadata_policy_rejects_unsafe_exports(changes):
    metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=42, st_nlink=1, st_size=100)
    for key, value in changes.items():
        setattr(metadata, key, value)
    with pytest.raises(migration.IdentityMigrationError, match="unsafe_manifest_file"):
        migration._check_file_metadata(metadata, 42)


def test_non_posix_protected_reader_fails_closed(monkeypatch):
    monkeypatch.setattr(migration, "os", SimpleNamespace(name="nt"))
    with pytest.raises(migration.IdentityMigrationError, match="requires_posix"):
        migration.read_manifest("C:/unread-sensitive-file.json")


@pytest.mark.skipif(os.name != "posix", reason="Actual Linux file-owner/no-follow rehearsal required")
def test_real_posix_file_reader_permissions_links_and_duplicate_json(tmp_path):
    path = tmp_path / "identity-manifest.json"
    plan = manifest_for(SimpleNamespace(id=uuid4()))
    path.write_text(json.dumps(plan), encoding="utf-8")
    path.chmod(0o600)
    assert migration.read_manifest(str(path)) == plan
    path.chmod(0o644)
    with pytest.raises(migration.IdentityMigrationError):
        migration.read_manifest(str(path))
    path.chmod(0o600)
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(migration.IdentityMigrationError):
        migration.read_manifest(str(link))
    hard = tmp_path / "hardlink.json"
    os.link(path, hard)
    with pytest.raises(migration.IdentityMigrationError):
        migration.read_manifest(str(path))
    hard.unlink()
    path.write_text('{"run_id":"first","run_id":"second"}', encoding="utf-8")
    with pytest.raises(migration.IdentityMigrationError, match="duplicate_manifest_field"):
        migration.read_manifest(str(path))


@pytest.mark.skipif(os.name != "posix", reason="Actual Linux CLI protected-file rehearsal required")
def test_real_posix_cli_dry_run_apply_resume(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    database = tmp_path / "cli-rehearsal.sqlite"
    auth = SqliteAuthStore(str(database))
    user = auth.create_user(UserCreate(name="Synthetic rehearsal", email=f"fixture-{uuid4()}@example.invalid", role="client"))
    plan = manifest_for(user)
    manifest = tmp_path / "protected-manifest.json"
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    manifest.chmod(0o600)
    repo = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "DATABASE_BACKEND": "sqlite",
                   "DATABASE_AUTO_MIGRATE": "false", "DATABASE_URL": "", "BUDGETS_DB_PATH": str(database)}
    command = [sys.executable, str(repo / "scripts/envidicy_migrate_identities.py"), "--manifest-file", str(manifest)]
    def run(*extra):
        output = subprocess.run([*command, *extra], cwd=repo, env=environment, capture_output=True, text=True, timeout=30)
        assert output.returncode == 0, output.stderr
        assert plan["rows"][0]["subject"] not in output.stdout
        assert str(user.id) not in output.stdout
        return json.loads(output.stdout)
    assert run()["checked"] == 1
    assert SqlEnvidicyStore(auth).principal(user.id) is None
    assert run("--apply")["applied"] == 1
    assert run("--apply")["unchanged"] == 1
    assert str(SqlEnvidicyStore(auth).resolve_identity(ISSUER, plan["rows"][0]["subject"]).id) == str(user.id)


def test_cli_defaults_dry_run_and_outputs_counts_not_mapping(monkeypatch, tmp_path, capsys):
    from scripts import envidicy_migrate_identities as cli
    plan = manifest_for(SimpleNamespace(id=uuid4()))
    calls = []
    monkeypatch.setattr(cli, "read_manifest", lambda _: plan)
    monkeypatch.setattr("app.settings.get_settings", lambda: SimpleNamespace(budgets_db_path=str(tmp_path / "fixture.sqlite")))
    class FakeMigration:
        def __init__(self, path):
            pass
        def run(self, value, *, apply=False):
            assert value == plan
            calls.append(apply)
            return {"status": "applied" if apply else "checked", "total": 1, "checked": int(not apply), "applied": int(apply)}
    monkeypatch.setattr(cli, "IdentityMigration", FakeMigration)
    monkeypatch.setattr("sys.argv", ["migration", "--manifest-file", "/protected/fixture.json"])
    assert cli.main() == 0
    monkeypatch.setattr("sys.argv", ["migration", "--manifest-file", "/protected/fixture.json", "--apply"])
    assert cli.main() == 0
    assert calls == [False, True]
    output = capsys.readouterr().out
    assert plan["rows"][0]["subject"] not in output and plan["rows"][0]["legacy_user_id"] not in output
