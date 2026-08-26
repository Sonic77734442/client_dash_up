from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg import connect, sql

from app.db import SqliteProviderBudgetCommandStore
from app.runtime_db import (
    DatabaseMigrationError,
    assert_postgres_schema_current,
    migrate_postgres,
    runtime_conn,
)
from app.schemas import (
    AdAccountCreate,
    AdAccountSyncJobOut,
    AdStatsIngestRequest,
    AuthIdentityLink,
    AuthProviderConfigCreate,
    BudgetCreate,
    ClientCreate,
    IntegrationCredentialCreate,
    OperationalActionExecuteRequest,
    SessionIssueRequest,
    UserCreate,
)
from app.services.ad_account_sync import SqliteAdAccountSyncJobStore
from app.services.ad_accounts import SqliteAdAccountStore
from app.services.ad_stats import SqliteAdStatsStore
from app.services.alerts import AlertSignal, SqliteAlertStore
from app.services.audit_log import SqliteAuditLogStore
from app.services.auth_arch import SqliteAuthStore
from app.services.budgets import SqliteBudgetStore
from app.services.clients import SqliteClientStore
from app.services.credential_crypto import CredentialKeyring
from app.services.integration_credentials import SqliteIntegrationCredentialStore
from app.services.oauth import SqliteOAuthStateStore
from app.services.operational_actions import SqliteOperationalActionStore
from app.services.provider_budget_commands import (
    ActorRole,
    BudgetAuthorizationSnapshot,
    BudgetChangeRequest,
    BudgetCommandAttempt,
    BudgetCommandStatus,
    BudgetCredentialAuditSnapshot,
)
from scripts.recover_provider_budget_commands import CONFIRMATION_PHRASE, main as recovery_main


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _keyring() -> CredentialKeyring:
    return CredentialKeyring(keys={"test": bytes([41]) * 32}, active_key_id="test")


@pytest.fixture(autouse=True)
def clean_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "postgresql")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_AUTO_MIGRATE", "false")
    migrate_postgres(TEST_DATABASE_URL)
    with connect(TEST_DATABASE_URL, autocommit=True) as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name <> 'schema_migrations'
                ORDER BY table_name
                """
            ).fetchall()
        ]
        if table_names:
            conn.execute(
                sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(
                        sql.Identifier("public", table_name) for table_name in table_names
                    )
                )
            )


def test_postgres_store_roundtrip_for_native_and_json_types():
    db_path = "ignored-by-postgres-runtime"
    keyring = _keyring()
    clients = SqliteClientStore(db_path)
    accounts = SqliteAdAccountStore(db_path, clients)
    stats = SqliteAdStatsStore(db_path, accounts)
    budgets = SqliteBudgetStore(db_path)
    auth = SqliteAuthStore(db_path, keyring=keyring)
    credentials = SqliteIntegrationCredentialStore(db_path, keyring=keyring)

    client = clients.create(
        ClientCreate(name="PostgreSQL client", timezone="UTC", notes="native round-trip")
    )
    account = accounts.create(
        AdAccountCreate(
            client_id=client.id,
            platform="meta",
            external_account_id="act_12345",
            name="Meta account",
            metadata={"nested": {"enabled": True}, "labels": ["a", "b"]},
        )
    )
    assert account.external_account_id == "12345"
    assert account.metadata == {"nested": {"enabled": True}, "labels": ["a", "b"]}

    ingest = AdStatsIngestRequest.model_validate(
        {
            "rows": [
                {
                    "ad_account_id": str(account.id),
                    "date": "2026-08-01",
                    "platform": "meta",
                    "impressions": 100,
                    "clicks": 10,
                    "spend": "12.34",
                    "conversions": "2.00",
                }
            ]
        }
    )
    assert stats.ingest(ingest, idempotency_key="postgres-idempotency")["inserted"] == 1
    assert stats.ingest(ingest, idempotency_key="postgres-idempotency")["idempotency"]["replayed"] is True
    stat = stats.list(account_id=account.id)[0]
    assert stat.date == date(2026, 8, 1)
    assert stat.spend == Decimal("12.34")

    budget = budgets.create(
        BudgetCreate(
            client_id=client.id,
            scope="client",
            amount=Decimal("500.00"),
            currency="USD",
            period_type="monthly",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
    )
    assert budget.amount == Decimal("500.00")
    assert budget.start_date == date(2026, 8, 1)

    user = auth.create_user(
        UserCreate(email="postgres@example.com", name="Postgres User", role="admin")
    )
    identity = auth.link_identity(
        AuthIdentityLink(
            user_id=user.id,
            provider="google",
            provider_user_id="provider-user-1",
            email="postgres@example.com",
            email_verified=True,
            raw_profile={"locale": "en", "flags": [1, 2]},
        )
    )
    assert identity.email_verified is True
    assert identity.raw_profile == {"locale": "en", "flags": [1, 2]}
    issued = auth.issue_session(
        SessionIssueRequest(
            user_id=user.id,
            ttl_minutes=30,
            metadata={"source": "postgres-test", "scopes": ["read", "write"]},
        )
    )
    assert auth.validate_session(issued.token).valid is True

    sync_now = datetime(2026, 8, 26, 10, 0, 0)
    sync_job = SqliteAdAccountSyncJobStore(db_path).create(
        AdAccountSyncJobOut(
            id=uuid4(),
            ad_account_id=account.id,
            provider="meta",
            status="error",
            started_at=sync_now,
            finished_at=sync_now + timedelta(seconds=2),
            records_synced=0,
            error_message="temporary",
            error_code="provider_unavailable",
            error_category="transient",
            retryable=True,
            attempt=2,
            next_retry_at=sync_now + timedelta(minutes=5),
            request_meta={"range": {"days": 30}, "manual": False},
            created_by=user.id,
            created_at=sync_now,
        )
    )
    assert sync_job.retryable is True
    assert sync_job.request_meta == {"range": {"days": 30}, "manual": False}

    provider_cfg = auth.upsert_provider_config(
        AuthProviderConfigCreate(
            provider="google",
            client_id="client-id",
            client_secret="postgres-oauth-secret",
            redirect_uri="https://example.com/auth/google/callback",
            enabled=True,
        )
    )
    assert provider_cfg.client_secret == "postgres-oauth-secret"
    credential = credentials.upsert(
        IntegrationCredentialCreate(
            provider="google",
            scope_type="client",
            scope_id=client.id,
            credentials={"refresh_token": "provider-token", "customer_ids": ["123"]},
            created_by=user.id,
        )
    )
    assert credential.credentials["refresh_token"] == "provider-token"

    action = SqliteOperationalActionStore(db_path).create(
        OperationalActionExecuteRequest(
            action="review",
            scope="account",
            scope_id=str(account.id),
            title="Review account",
            reason="PostgreSQL parity",
            metrics={"spend": 12.34, "healthy": True},
            client_id=client.id,
            account_id=account.id,
        ),
        created_by=user.id,
    )
    assert action.metrics["healthy"] is True

    audit = SqliteAuditLogStore(db_path).create(
        event_type="postgres.checked",
        resource_type="client",
        resource_id=str(client.id),
        actor_user_id=user.id,
        actor_role="admin",
        tenant_client_id=client.id,
        payload={"nested": {"ok": True}},
    )
    assert audit.payload == {"nested": {"ok": True}}

    alert = SqliteAlertStore(db_path).raise_alert(
        AlertSignal(
            code="postgres_check",
            severity="low",
            title="PostgreSQL checked",
            message="Runtime parity works",
            fingerprint="postgres-runtime-check",
            provider="meta",
            client_id=client.id,
            ad_account_id=account.id,
            context={"attempt": 1},
        )
    )
    assert alert.context == {"attempt": 1}

    with runtime_conn(db_path) as conn:
        stored_cfg = conn.execute(
            "SELECT client_secret FROM auth_provider_configs WHERE id=?",
            (str(provider_cfg.id),),
        ).fetchone()
    assert "postgres-oauth-secret" not in stored_cfg["client_secret"]


def test_postgres_rejects_schema_from_unknown_newer_release():
    migration_name = "9999_future_release.sql"
    with connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO schema_migrations(name, checksum) VALUES (%s, %s)",
            (migration_name, "future-checksum"),
        )
    try:
        with pytest.raises(DatabaseMigrationError, match="unknown migrations"):
            assert_postgres_schema_current(TEST_DATABASE_URL)
        with pytest.raises(DatabaseMigrationError, match="unknown to this release"):
            migrate_postgres(TEST_DATABASE_URL)
    finally:
        with connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE name=%s", (migration_name,))


def test_postgres_advisory_transactions_close_oauth_lease_and_assignment_races():
    db_path = "ignored-by-postgres-runtime"
    clients = SqliteClientStore(db_path)
    first_client = clients.create(ClientCreate(name="First client"))
    second_client = clients.create(ClientCreate(name="Second client"))
    accounts = SqliteAdAccountStore(db_path, clients)

    def create_account(client_id, external_id):
        try:
            return accounts.create(
                AdAccountCreate(
                    client_id=client_id,
                    platform="facebook",
                    external_account_id=external_id,
                    name=f"Account {client_id}",
                )
            )
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: create_account(*args),
                [(first_client.id, "act_999-00"), (second_client.id, "999-00")],
            )
        )
    assert sum(not isinstance(result, HTTPException) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1

    oauth = SqliteOAuthStateStore(db_path)
    state = oauth.create_state("google", "/", "nonce", ttl_minutes=5)

    def consume_state():
        try:
            return oauth.consume_state("google", state.state, "nonce")
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        consumed = list(pool.map(lambda _: consume_state(), range(2)))
    assert sum(not isinstance(result, HTTPException) for result in consumed) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 400 for result in consumed) == 1

    leases = SqliteAdAccountSyncJobStore(db_path)
    now = datetime(2026, 8, 26, 12, 0, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        lease_tokens = list(
            pool.map(
                lambda _: leases.acquire_lease(
                    lease_key="sync-scheduler", now=now, ttl_seconds=120
                ),
                range(2),
            )
        )
    assert sum(token is not None for token in lease_tokens) == 1


def test_postgres_provider_budget_ledger_is_durable_and_fenced(capsys):
    db_path = "ignored-by-postgres-runtime"
    keyring = _keyring()
    clients = SqliteClientStore(db_path)
    client = clients.create(ClientCreate(name="Budget ledger client"))
    account = SqliteAdAccountStore(db_path, clients).create(
        AdAccountCreate(
            client_id=client.id,
            platform="meta",
            external_account_id="123456",
            name="Ledger account",
        )
    )
    auth = SqliteAuthStore(db_path, keyring=keyring)
    user = auth.create_user(
        UserCreate(email="ledger@example.com", name="Ledger Actor", role="solo_client")
    )
    credential = SqliteIntegrationCredentialStore(db_path, keyring=keyring).upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=client.id,
            connection_key="meta:ledger",
            credentials={"access_token": "never-written-to-ledger"},
            created_by=user.id,
        )
    )
    request = BudgetChangeRequest(
        client_id=client.id,
        ad_account_id=account.id,
        provider_account_id="123456",
        credential_id=credential.id,
        agency_id=None,
        target_type="campaign",
        provider_target_id="777",
        field="daily_budget",
        amount_minor=2500,
        expected_current_minor=1000,
        currency="USD",
        reason="PostgreSQL ledger integration test",
    )
    authorization = BudgetAuthorizationSnapshot(
        actor_role=ActorRole.SOLO_CLIENT,
        selected_agency_id=None,
        agency_member_role=None,
        agency_active=False,
        can_admin_provider_write=False,
        can_manage_spend_cap=False,
        has_client_access=True,
        agency_client_bound=False,
        solo_client_owned=True,
    )
    observed_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    credential_snapshot = BudgetCredentialAuditSnapshot(
        revision=1,
        meta_app_id="meta-app",
        meta_business_config_id="business-config",
        meta_user_id="meta-user",
        granted_permissions=("ads_management",),
        validation_version=1,
        validated_at=observed_at,
    )
    store = SqliteProviderBudgetCommandStore(db_path)
    submitted = store.create_or_replay(
        request=request,
        actor_user_id=user.id,
        authorization_snapshot=authorization,
        credential_snapshot=credential_snapshot,
        idempotency_key="postgres-ledger-0001",
        request_hash="a" * 64,
        preview_hash="b" * 64,
        observed_before_minor=1000,
        now=observed_at,
    )
    assert submitted.replayed is False
    competing = store.create_or_replay(
        request=replace(request, amount_minor=3000),
        actor_user_id=user.id,
        authorization_snapshot=authorization,
        credential_snapshot=credential_snapshot,
        idempotency_key="postgres-ledger-0002",
        request_hash="c" * 64,
        preview_hash="d" * 64,
        observed_before_minor=1000,
        now=observed_at,
    )
    first_lock = store.acquire_target_lock(submitted.command)
    assert first_lock
    assert SqliteProviderBudgetCommandStore(db_path).acquire_target_lock(competing.command) is None
    claimed = store.claim_queued(submitted.command.id, now=observed_at)
    assert claimed.status == BudgetCommandStatus.IN_PROGRESS

    finished = store.finish_execution(
        claimed.id,
        status=BudgetCommandStatus.APPLIED,
        attempt=BudgetCommandAttempt(
            attempt_no=1,
            started_at=observed_at,
            finished_at=observed_at,
            outcome=BudgetCommandStatus.APPLIED,
            observed_before_minor=1000,
            confirmed_after_minor=2500,
            provider_trace_id="trace-postgres",
        ),
        confirmed_after_minor=2500,
        provider_trace_id="trace-postgres",
        error=None,
        now=observed_at,
    )
    assert finished.status == BudgetCommandStatus.APPLIED
    reopened = SqliteProviderBudgetCommandStore(db_path).get(finished.id)
    assert reopened.confirmed_after_minor == 2500
    assert reopened.attempts[0].provider_trace_id == "trace-postgres"
    assert reopened.credential_snapshot.revision == 1

    interrupted_submission = store.create_or_replay(
        request=replace(request, provider_target_id="778", amount_minor=3500),
        actor_user_id=user.id,
        authorization_snapshot=authorization,
        credential_snapshot=credential_snapshot,
        idempotency_key="postgres-ledger-0003",
        request_hash="e" * 64,
        preview_hash="f" * 64,
        observed_before_minor=1000,
        now=observed_at,
    )
    second_lock = store.acquire_target_lock(interrupted_submission.command)
    assert second_lock
    interrupted = store.claim_queued(interrupted_submission.command.id, now=observed_at)
    assert interrupted.status == BudgetCommandStatus.IN_PROGRESS

    assert recovery_main([]) == 0
    dry_run = capsys.readouterr().out
    assert '"dry_run": true' in dry_run
    assert '"in_progress_before": 1' in dry_run
    assert recovery_main(
        ["--apply", "--confirm-all-api-stopped", CONFIRMATION_PHRASE]
    ) == 0
    applied = capsys.readouterr().out
    assert '"recovered": 1' in applied
    recovered = store.get(interrupted.id)
    assert recovered.status == BudgetCommandStatus.UNKNOWN
    assert recovered.attempts[-1].outcome == BudgetCommandStatus.UNKNOWN
    assert recovered.attempts[-1].error is not None
    assert recovered.attempts[-1].error.retryable is False
    assert recovered.attempts[-1].reconciliation is False


def test_postgres_main_client_invite_transaction_and_readiness():
    # app.main is imported by most of the SQLite suite during collection. Run
    # this smoke in a fresh interpreter so the explicit backend selection is
    # tested exactly as it is during a Render process start.
    program = textwrap.dedent(
        """
        from fastapi.testclient import TestClient
        from app.main import app

        api = TestClient(app)
        admin = api.post('/auth/internal/users', json={
            'email': 'postgres-invite-admin@example.com',
            'name': 'Postgres Invite Admin',
            'role': 'admin',
            'status': 'active',
        })
        assert admin.status_code == 200, admin.text
        invited = api.post('/auth/internal/users', json={
            'email': 'postgres-invited@example.com',
            'name': 'Postgres Invited User',
            'role': 'client',
            'status': 'active',
        })
        assert invited.status_code == 200, invited.text

        def token_for(user_id):
            issued = api.post('/auth/internal/sessions/issue', json={
                'user_id': user_id,
                'ttl_minutes': 60,
            })
            assert issued.status_code == 200, issued.text
            return issued.json()['token']

        admin_token = token_for(admin.json()['id'])
        invited_token = token_for(invited.json()['id'])
        tenant = api.post('/clients', json={
            'name': 'Postgres Invite Tenant',
            'default_currency': 'USD',
        }, headers={'Authorization': f'Bearer {admin_token}'})
        assert tenant.status_code == 200, tenant.text
        issue = api.post(
            f"/clients/{tenant.json()['id']}/invites",
            json={'email': invited.json()['email'], 'expires_in_days': 7},
            headers={'Authorization': f'Bearer {admin_token}'},
        )
        assert issue.status_code == 200, issue.text
        accepted = api.post('/auth/invites/accept',
            json={'token': issue.json()['invite_token']},
            headers={'Authorization': f'Bearer {invited_token}'},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['invite']['status'] == 'accepted'
        ready = api.get('/readyz', headers={'Authorization': f'Bearer {admin_token}'})
        assert ready.status_code == 200, ready.text
        assert ready.json()['database_backend'] == 'postgresql'
        assert ready.json()['checks']['postgresql'] is True
        """
    )
    environment = dict(os.environ)
    environment.update(
        {
            "APP_ENV": "test",
            "ENABLE_TEST_ENDPOINTS": "true",
            "DATABASE_BACKEND": "postgresql",
            "DATABASE_URL": TEST_DATABASE_URL,
            "DATABASE_AUTO_MIGRATE": "false",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
