from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db import SqliteProviderBudgetCommandStore, init_sqlite, sqlite_conn
from app.services.auth_arch import SqliteAuthStore
from app.services.platform_admin import SqlitePlatformAdminStore
from app.services.provider_budget_commands import (
    ActorRole,
    BudgetAuthorizationSnapshot,
    BudgetChangeRequest,
    BudgetCommandAttempt,
    BudgetCommandStatus,
    BudgetCredentialAuditSnapshot,
    CommandNotExecutableError,
    IdempotencyConflictError,
)
from scripts.recover_provider_budget_commands import CONFIRMATION_PHRASE, main as recovery_main


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _seed(db_path: str):
    init_sqlite(db_path)
    ids = {
        "client": uuid4(),
        "account": uuid4(),
        "credential": uuid4(),
        "user": uuid4(),
    }
    now = NOW.isoformat()
    with sqlite_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO clients (id,name,status,default_currency,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (str(ids["client"]), "Tenant", "active", "USD", now, now),
        )
        conn.execute(
            """
            INSERT INTO ad_accounts
              (id,client_id,platform,external_account_id,name,currency,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                str(ids["account"]),
                str(ids["client"]),
                "meta",
                "123456",
                "Meta",
                "USD",
                "active",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO users (id,email,name,role,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (str(ids["user"]), "actor@test.local", "Actor", "solo_client", "active", now, now),
        )
        conn.execute(
            """
            INSERT INTO integration_credentials
              (id,provider,scope_type,scope_id,connection_key,credentials_json,status,created_by,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(ids["credential"]),
                "meta",
                "client",
                str(ids["client"]),
                "meta:ledger",
                "{}",
                "active",
                str(ids["user"]),
                now,
                now,
            ),
        )
        conn.commit()
    return ids


def _request(ids, *, amount_minor: int = 2500):
    return BudgetChangeRequest(
        client_id=ids["client"],
        ad_account_id=ids["account"],
        provider_account_id="123456",
        credential_id=ids["credential"],
        agency_id=None,
        target_type="campaign",
        provider_target_id="777",
        field="daily_budget",
        amount_minor=amount_minor,
        expected_current_minor=1000,
        currency="USD",
        reason="Approved pacing correction",
    )


def _create_or_replay(store, **kwargs):
    kwargs.setdefault(
        "authorization_snapshot",
        BudgetAuthorizationSnapshot(
            actor_role=ActorRole.SOLO_CLIENT,
            selected_agency_id=None,
            agency_member_role=None,
            agency_active=False,
            can_admin_provider_write=False,
            can_manage_spend_cap=False,
            has_client_access=True,
            agency_client_bound=False,
            solo_client_owned=True,
        ),
    )
    kwargs.setdefault(
        "credential_snapshot",
        BudgetCredentialAuditSnapshot(
            revision=1,
            meta_app_id="meta-app",
            meta_business_config_id="business-config",
            meta_user_id="meta-user",
            granted_permissions=("ads_management",),
            validation_version=1,
            validated_at=NOW,
        ),
    )
    return store.create_or_replay(**kwargs)


def test_sqlite_command_ledger_is_durable_and_idempotent(tmp_path):
    db_path = str(tmp_path / "provider-budget-ledger.db")
    ids = _seed(db_path)
    first_store = SqliteProviderBudgetCommandStore(db_path)
    created = _create_or_replay(first_store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-ledger-0001",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=NOW,
    )
    assert created.replayed is False
    claimed = first_store.claim_queued(created.command.id, now=NOW)
    assert claimed.status == BudgetCommandStatus.IN_PROGRESS
    finished = first_store.finish_execution(
        created.command.id,
        status=BudgetCommandStatus.APPLIED,
        attempt=BudgetCommandAttempt(
            attempt_no=1,
            started_at=NOW,
            finished_at=NOW,
            outcome=BudgetCommandStatus.APPLIED,
            observed_before_minor=1000,
            confirmed_after_minor=2500,
            provider_trace_id="trace-1",
        ),
        confirmed_after_minor=2500,
        provider_trace_id="trace-1",
        error=None,
        now=NOW,
    )
    assert finished.status == BudgetCommandStatus.APPLIED

    reopened = SqliteProviderBudgetCommandStore(db_path)
    persisted = reopened.get(created.command.id)
    assert persisted is not None
    assert persisted.confirmed_after_minor == 2500
    assert persisted.attempts[0].provider_trace_id == "trace-1"
    assert persisted.credential_snapshot.revision == 1
    assert persisted.authorization_snapshot.actor_role == ActorRole.SOLO_CLIENT
    assert persisted.attempts[0].credential_snapshot == persisted.credential_snapshot
    assert persisted.attempts[0].authorization_snapshot == persisted.authorization_snapshot

    with sqlite_conn(db_path) as conn:
        conn.execute(
            "UPDATE integration_credentials SET semantic_revision=semantic_revision+1 WHERE id=?",
            (str(ids["credential"]),),
        )
        conn.execute("UPDATE users SET role='client' WHERE id=?", (str(ids["user"]),))
        conn.commit()
    historical = SqliteProviderBudgetCommandStore(db_path).get(created.command.id)
    assert historical is not None
    assert historical.credential_snapshot.revision == 1
    assert historical.authorization_snapshot.actor_role == ActorRole.SOLO_CLIENT
    replay = _create_or_replay(reopened,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-ledger-0001",
        request_hash="a" * 64,
        preview_hash="different-preview-is-not-persisted-on-replay",
        observed_before_minor=1000,
        now=NOW,
    )
    assert replay.replayed is True
    assert replay.command.id == created.command.id
    assert reopened.list(client_id=ids["client"])[0].id == created.command.id


def test_startup_migrates_old_budget_ledger_to_readable_unverified_snapshots(tmp_path):
    db_path = str(tmp_path / "provider-budget-old-schema.db")
    ids = _seed(db_path)
    store = SqliteProviderBudgetCommandStore(db_path)
    created = _create_or_replay(
        store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-old-schema-1",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command
    with sqlite_conn(db_path) as conn:
        conn.execute("ALTER TABLE provider_budget_commands DROP COLUMN authorization_snapshot_json")
        conn.execute("ALTER TABLE provider_budget_commands DROP COLUMN credential_snapshot_json")
        conn.execute("ALTER TABLE provider_budget_command_attempts DROP COLUMN authorization_snapshot_json")
        conn.execute("ALTER TABLE provider_budget_command_attempts DROP COLUMN credential_snapshot_json")
        conn.commit()

    migrated = SqliteProviderBudgetCommandStore(db_path).get(created.id)
    assert migrated is not None
    assert migrated.authorization_snapshot.legacy_unverified is True
    assert migrated.credential_snapshot.legacy_unverified is True
    assert migrated.credential_snapshot.meta_app_id == ""
    assert SqliteProviderBudgetCommandStore(db_path).list(client_id=ids["client"])[0].id == created.id


def test_sqlite_target_lease_serializes_store_instances(tmp_path):
    db_path = str(tmp_path / "provider-budget-lock.db")
    ids = _seed(db_path)
    first_store = SqliteProviderBudgetCommandStore(db_path)
    second_store = SqliteProviderBudgetCommandStore(db_path)
    first = _create_or_replay(first_store,
        request=_request(ids, amount_minor=2000),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-lock-0001",
        request_hash="c" * 64,
        preview_hash="d" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command
    second = _create_or_replay(second_store,
        request=_request(ids, amount_minor=3000),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-lock-0002",
        request_hash="e" * 64,
        preview_hash="f" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command

    first_token = first_store.acquire_target_lock(first)
    assert first_token
    assert second_store.acquire_target_lock(second) is None
    with sqlite_conn(db_path) as conn:
        lease = conn.execute("SELECT lease_until, updated_at FROM provider_budget_target_locks").fetchone()
    assert (datetime.fromisoformat(lease["lease_until"]) - datetime.fromisoformat(lease["updated_at"])) >= timedelta(
        seconds=599
    )
    first_store.release_target_lock(first, first_token)
    second_token = second_store.acquire_target_lock(second)
    assert second_token
    second_store.release_target_lock(second, second_token)


def test_expired_lease_is_not_stolen_while_original_command_is_in_progress(tmp_path):
    db_path = str(tmp_path / "provider-budget-expired-in-progress.db")
    ids = _seed(db_path)
    first_store = SqliteProviderBudgetCommandStore(db_path)
    second_store = SqliteProviderBudgetCommandStore(db_path)
    first = _create_or_replay(first_store,
        request=_request(ids, amount_minor=2000),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-expired-0001",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=datetime.now(timezone.utc),
    ).command
    second = _create_or_replay(second_store,
        request=_request(ids, amount_minor=3000),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-expired-0002",
        request_hash="c" * 64,
        preview_hash="d" * 64,
        observed_before_minor=1000,
        now=datetime.now(timezone.utc),
    ).command
    first_token = first_store.acquire_target_lock(first)
    assert first_token
    first_store.claim_queued(first.id, now=datetime.now(timezone.utc))
    recovery_check_at = datetime.now(timezone.utc)
    with sqlite_conn(db_path) as conn:
        conn.execute(
            "UPDATE provider_budget_commands SET updated_at=? WHERE id=?",
            ((recovery_check_at - timedelta(seconds=901)).isoformat(), str(first.id)),
        )
        conn.commit()
    assert first_store.renew_target_lock(first, first_token) is True
    # Runtime reads and a newly constructed store never reinterpret an active
    # process as crashed. Blue/green instances may share this SQLite file.
    assert SqliteProviderBudgetCommandStore(db_path).get(first.id).status == BudgetCommandStatus.IN_PROGRESS
    first_store.release_target_lock(first, first_token)
    with sqlite_conn(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM provider_budget_target_locks WHERE command_id=?",
            (str(first.id),),
        ).fetchone()
    with sqlite_conn(db_path) as conn:
        conn.execute(
            "UPDATE provider_budget_target_locks SET lease_until=? WHERE command_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), str(first.id)),
        )
        conn.commit()
    assert second_store.acquire_target_lock(second) is None
    # Even a missing lock row cannot remove the durable in-progress fence.
    with sqlite_conn(db_path) as conn:
        conn.execute("DELETE FROM provider_budget_target_locks WHERE command_id=?", (str(first.id),))
        conn.commit()
    assert second_store.acquire_target_lock(second) is None
    with pytest.raises(ValueError):
        first_store.recover_interrupted_in_progress_offline(all_api_instances_stopped=False)
    assert first_store.recover_interrupted_in_progress_offline(
        all_api_instances_stopped=True,
        now=datetime.now(timezone.utc) + timedelta(seconds=1),
    ) == 1
    assert first_store.get(first.id).status == BudgetCommandStatus.UNKNOWN


def test_preview_hash_is_consumed_only_once_even_with_a_new_idempotency_key(tmp_path):
    db_path = str(tmp_path / "provider-budget-preview-once.db")
    ids = _seed(db_path)
    store = SqliteProviderBudgetCommandStore(db_path)
    _create_or_replay(store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-preview-0001",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=NOW,
    )
    with pytest.raises(IdempotencyConflictError) as exc_info:
        _create_or_replay(store,
            request=_request(ids),
            actor_user_id=ids["user"],
            idempotency_key="sqlite-preview-0002",
            request_hash="a" * 64,
            preview_hash="b" * 64,
            observed_before_minor=1000,
            now=NOW,
        )
    assert exc_info.value.code == "preview_already_consumed"


def test_offline_recovery_quarantines_all_interrupted_writes_until_reconciled(tmp_path):
    db_path = str(tmp_path / "provider-budget-offline-recovery.db")
    ids = _seed(db_path)
    store = SqliteProviderBudgetCommandStore(db_path)
    started_at = datetime.now(timezone.utc)
    created = _create_or_replay(store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-stale-0001",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=started_at,
    ).command
    store.claim_queued(created.id, now=started_at)

    # Ordinary runtime operations and another store instance must leave the
    # command fenced forever; only the explicit all-APIs-stopped operation may
    # classify it as interrupted.
    assert store.get(created.id).status == BudgetCommandStatus.IN_PROGRESS
    assert SqliteProviderBudgetCommandStore(db_path).list(client_id=ids["client"])[0].status == BudgetCommandStatus.IN_PROGRESS
    assert store.count_interrupted_in_progress() == 1
    assert store.recover_interrupted_in_progress_offline(
        all_api_instances_stopped=True,
        now=started_at + timedelta(seconds=901),
    ) == 1
    recovered = store.get(created.id)
    assert recovered.status == BudgetCommandStatus.UNKNOWN
    assert recovered.error.code == "provider_execution_interrupted"
    assert recovered.attempts[-1].outcome == BudgetCommandStatus.UNKNOWN
    assert recovered.attempts[-1].actor_user_id is None
    assert store.target_quarantine_command_id(recovered.request) == recovered.id
    with pytest.raises(CommandNotExecutableError):
        store.claim_queued(created.id, now=started_at + timedelta(seconds=902))
    with pytest.raises(CommandNotExecutableError) as quarantined:
        _create_or_replay(store,
            request=_request(ids, amount_minor=3000),
            actor_user_id=ids["user"],
            idempotency_key="sqlite-stale-0002",
            request_hash="c" * 64,
            preview_hash="d" * 64,
            observed_before_minor=1000,
            now=started_at + timedelta(seconds=902),
        )
    assert quarantined.value.code == "meta_budget_target_quarantined"

    reconciled_at = started_at + timedelta(seconds=903)
    store.record_reconciliation(
        recovered.id,
        status=BudgetCommandStatus.APPLIED,
        attempt=BudgetCommandAttempt(
            attempt_no=2,
            started_at=reconciled_at,
            finished_at=reconciled_at,
            outcome=BudgetCommandStatus.APPLIED,
            confirmed_after_minor=2500,
            reconciliation=True,
            actor_user_id=ids["user"],
        ),
        confirmed_after_minor=2500,
        provider_trace_id="reconcile-read",
        error=None,
        now=reconciled_at,
    )
    assert store.target_quarantine_command_id(recovered.request) is None
    replacement = _create_or_replay(store,
        request=_request(ids, amount_minor=3000),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-stale-0002",
        request_hash="c" * 64,
        preview_hash="d" * 64,
        observed_before_minor=2500,
        now=reconciled_at,
    )
    assert replacement.replayed is False


def test_offline_recovery_script_is_count_only_by_default_and_requires_exact_phrase(tmp_path, capsys):
    missing_db = tmp_path / "provider-budget-missing.db"
    assert recovery_main(["--db-path", str(missing_db)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "provider_budget_recovery_db_missing"
    assert not missing_db.exists()

    empty_db = tmp_path / "provider-budget-empty.db"
    empty_db.touch()
    assert recovery_main(["--db-path", str(empty_db)]) == 0
    assert json.loads(capsys.readouterr().out)["in_progress_before"] == 0
    with sqlite_conn(str(empty_db)) as empty_conn:
        assert empty_conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] == 0

    db_path = str(tmp_path / "provider-budget-offline-script.db")
    ids = _seed(db_path)
    store = SqliteProviderBudgetCommandStore(db_path)
    command = _create_or_replay(store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-offline-script-1",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command
    store.claim_queued(command.id, now=NOW)

    with sqlite_conn(db_path) as conn:
        schema_before = tuple(
            (row["type"], row["name"], row["sql"])
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        )
        command_before = tuple(
            conn.execute(
                "SELECT id,status,attempt_count,updated_at FROM provider_budget_commands ORDER BY id"
            ).fetchone()
        )
    mtime_before = (tmp_path / "provider-budget-offline-script.db").stat().st_mtime_ns
    assert recovery_main(["--db-path", db_path]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run == {
        "ok": True,
        "dry_run": True,
        "in_progress_before": 1,
        "recovered": 0,
        "in_progress_after": 1,
    }
    assert (tmp_path / "provider-budget-offline-script.db").stat().st_mtime_ns == mtime_before
    with sqlite_conn(db_path) as conn:
        schema_after = tuple(
            (row["type"], row["name"], row["sql"])
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
        )
        command_after = tuple(
            conn.execute(
                "SELECT id,status,attempt_count,updated_at FROM provider_budget_commands ORDER BY id"
            ).fetchone()
        )
    assert schema_after == schema_before
    assert command_after == command_before
    assert store.get(command.id).status == BudgetCommandStatus.IN_PROGRESS

    assert recovery_main(
        ["--db-path", db_path, "--apply", "--confirm-all-api-stopped", "not stopped"]
    ) == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["code"] == "provider_budget_recovery_offline_confirmation_required"
    assert store.get(command.id).status == BudgetCommandStatus.IN_PROGRESS

    assert recovery_main(
        [
            "--db-path",
            db_path,
            "--apply",
            "--confirm-all-api-stopped",
            CONFIRMATION_PHRASE,
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied == {
        "ok": True,
        "dry_run": False,
        "in_progress_before": 1,
        "recovered": 1,
        "in_progress_after": 0,
    }
    recovered = store.get(command.id)
    assert recovered.status == BudgetCommandStatus.UNKNOWN
    assert store.target_quarantine_command_id(recovered.request) == recovered.id


def test_budget_ledger_blocks_hard_delete_of_referenced_user_with_stable_409(tmp_path):
    db_path = str(tmp_path / "provider-budget-user-retention.db")
    ids = _seed(db_path)
    store = SqliteProviderBudgetCommandStore(db_path)
    command = _create_or_replay(store,
        request=_request(ids),
        actor_user_id=ids["user"],
        idempotency_key="sqlite-retention-user-1",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command
    store.claim_queued(command.id, now=NOW)
    store.finish_execution(
        command.id,
        status=BudgetCommandStatus.APPLIED,
        attempt=BudgetCommandAttempt(
            attempt_no=1,
            started_at=NOW,
            finished_at=NOW,
            outcome=BudgetCommandStatus.APPLIED,
            actor_user_id=ids["user"],
            credential_id=ids["credential"],
        ),
        confirmed_after_minor=2500,
        provider_trace_id=None,
        error=None,
        now=NOW,
    )
    with pytest.raises(HTTPException) as exc_info:
        SqliteAuthStore(db_path).delete_user(ids["user"])
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "provider_budget_ledger_retention_required"
    assert exc_info.value.detail["details"] == {"history_count": 1, "unresolved_count": 0}
    assert SqliteAuthStore(db_path).get_user(ids["user"]) is not None


def test_unknown_budget_ledger_blocks_agency_and_credential_hard_delete(tmp_path):
    db_path = str(tmp_path / "provider-budget-agency-retention.db")
    ids = _seed(db_path)
    agency_id = uuid4()
    now = NOW.isoformat()
    with sqlite_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agencies (id,name,slug,status,plan,notes,created_at,updated_at)
            VALUES (?,?,?,'active','basic',NULL,?,?)
            """,
            (str(agency_id), "Ledger agency", f"ledger-{agency_id.hex[:8]}", now, now),
        )
        conn.commit()
    request = BudgetChangeRequest(
        **{
            **_request(ids).__dict__,
            "agency_id": agency_id,
        }
    )
    store = SqliteProviderBudgetCommandStore(db_path)
    command = _create_or_replay(store,
        request=request,
        actor_user_id=ids["user"],
        idempotency_key="sqlite-retention-agency-1",
        request_hash="c" * 64,
        preview_hash="d" * 64,
        observed_before_minor=1000,
        now=NOW,
    ).command
    store.claim_queued(command.id, now=NOW)
    store.finish_execution(
        command.id,
        status=BudgetCommandStatus.UNKNOWN,
        attempt=BudgetCommandAttempt(
            attempt_no=1,
            started_at=NOW,
            finished_at=NOW,
            outcome=BudgetCommandStatus.UNKNOWN,
            actor_user_id=ids["user"],
            credential_id=ids["credential"],
        ),
        confirmed_after_minor=None,
        provider_trace_id=None,
        error=None,
        now=NOW,
    )
    platform = SqlitePlatformAdminStore(db_path, SqliteAuthStore(db_path))
    with pytest.raises(HTTPException) as exc_info:
        platform.delete_agency(agency_id)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "provider_budget_ledger_retention_required"
    assert exc_info.value.detail["details"] == {"history_count": 1, "unresolved_count": 1}
    with sqlite_conn(db_path) as conn:
        assert conn.execute("SELECT 1 FROM agencies WHERE id=?", (str(agency_id),)).fetchone()
        assert conn.execute(
            "SELECT 1 FROM integration_credentials WHERE id=?", (str(ids["credential"]),)
        ).fetchone()
