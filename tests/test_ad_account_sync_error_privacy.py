"""Provider exception contents never become persisted or public diagnostics."""
import json
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from app.runtime_db import runtime_conn
from app.schemas import AdAccountCreate, AdAccountPatch, AdAccountSyncJobOut, ClientCreate
from app.services.ad_account_sync import (
    AdAccountSyncService,
    InMemoryAdAccountSyncJobStore,
    MissingScopedCredentialsError,
    SqliteAdAccountSyncJobStore,
)
from app.services.ad_accounts import InMemoryAdAccountStore, SqliteAdAccountStore
from app.services.ad_stats import InMemoryAdStatsStore, SqliteAdStatsStore
from app.services.clients import InMemoryClientStore, SqliteClientStore
from app.services.meta_connection import MetaCredentialReconnectRequiredError
from app.services.provider_metrics import ProviderPayloadValidationError
from app.services.sync_diagnostics import BLOCKED_ACCOUNT_MESSAGE, PERMISSION_MESSAGE, safe_sync_error_message


PRIVATE = (
    "https://provider.example/insights?access_token=synthetic-access-secret "
    "Authorization: Bearer synthetic-bearer-secret "
    'provider_body={"refresh_token":"synthetic-refresh-secret","email":"private@example.test"}'
)
SECRETS = ("synthetic-access-secret", "synthetic-bearer-secret", "synthetic-refresh-secret", "private@example.test")


@pytest.fixture(params=["memory", "sqlite"])
def stores(request, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    path = str(tmp_path / "sync-errors.sqlite")
    if request.param == "sqlite":
        clients = SqliteClientStore(path)
        accounts = SqliteAdAccountStore(path, clients)
        stats = SqliteAdStatsStore(path, accounts)
        jobs = SqliteAdAccountSyncJobStore(path)
    else:
        clients = InMemoryClientStore()
        accounts = InMemoryAdAccountStore(clients)
        stats = InMemoryAdStatsStore(accounts)
        jobs = InMemoryAdAccountSyncJobStore()
    tenant = clients.create(ClientCreate(name="Private provider failure"))
    account = accounts.create(AdAccountCreate(client_id=tenant.id, platform="meta", external_account_id="123", name="Meta"))
    return accounts, stats, jobs, account, path if request.param == "sqlite" else None


@pytest.mark.parametrize("exc,code,category,retryable", [
    (HTTPException(502, detail=PRIVATE), "provider_unavailable", "provider", True),
    (HTTPException(502, detail="API key permission scopes mismatch " + PRIVATE), "provider_unavailable", "provider", True),
    (HTTPException(429, detail={"message": PRIVATE, "code": PRIVATE}), "rate_limited", "rate_limit", True),
    (HTTPException(401, detail={"detail": PRIVATE}), "auth_failed", "auth", False),
    (HTTPException(403, detail={"message": "customer_not_enabled " + PRIVATE}), "auth_failed", "auth", False),
    (HTTPException(400, detail={"provider_body": PRIVATE}), "invalid_request", "validation", False),
    (RuntimeError(PRIVATE), "auth_failed", "auth", False),
    (RuntimeError("temporary gateway outage provider_body=synthetic-access-secret"), "provider_unavailable", "provider", True),
    (RuntimeError("unexpected provider_body=synthetic-access-secret"), "unknown_error", "unknown", False),
    (RuntimeError("REQUESTED_METRICS_FOR_MANAGER " + PRIVATE), "invalid_request", "validation", False),
    (ProviderPayloadValidationError(PRIVATE), "provider_payload_invalid", "validation", False),
    (MissingScopedCredentialsError(PRIVATE), "provider_credentials_missing", "configuration", False),
    (MetaCredentialReconnectRequiredError("meta_token_expired", PRIVATE), "provider_reconnect_required", "auth", False),
    (MetaCredentialReconnectRequiredError(PRIVATE, PRIVATE), "provider_reconnect_required", "auth", False),
])
def test_provider_exceptions_are_safe_before_storage_and_keep_retry_policy(stores, exc, code, category, retryable):
    accounts, stats, jobs, account, path = stores

    def fail(*_args):
        raise exc

    service = AdAccountSyncService(accounts, jobs, stats, provider_fetchers={"meta": fail})
    result = service.run_sync(account_ids=[account.id], date_from=date(2026, 9, 1), date_to=date(2026, 9, 1), force=True)
    assert result.failed == 1 and result.success == 0
    assert result.retry_scheduled == int(retryable)
    job = result.jobs[0]
    assert (job.error_code, job.error_category, job.retryable) == (code, category, retryable)
    assert bool(job.next_retry_at) is retryable
    assert job.error_message and len(job.error_message) < 250
    persisted = jobs.list(account_id=account.id)[0]
    current = accounts.get(account.id)
    assert persisted.error_message == current.metadata["sync_error"] == job.error_message
    serialized = persisted.model_dump_json() + current.model_dump_json()
    if path:
        with runtime_conn(path) as conn:
            stored_error = conn.execute("SELECT error_message FROM ad_account_sync_jobs WHERE id=?", (str(job.id),)).fetchone()
            stored_account = conn.execute("SELECT metadata FROM ad_accounts WHERE id=?", (str(account.id),)).fetchone()
        serialized += str(stored_error["error_message"]) + str(stored_account["metadata"])
    assert all(value not in serialized for value in SECRETS)
    if isinstance(exc, MetaCredentialReconnectRequiredError):
        expected = "meta_token_expired" if exc.code == "meta_token_expired" else "meta_reconnect_required"
        assert current.metadata["meta_connection_diagnostic_code"] == expected
        assert "Reconnect Meta Ads" in job.error_message


@pytest.mark.parametrize("platform", ["meta", "google", "tiktok"])
@pytest.mark.parametrize("historical", [False, True])
def test_api_run_jobs_account_and_diagnostics_do_not_expose_provider_error(platform, historical):
    from test_ad_account_sync import app, client, mk_account, mk_client, reset_state

    reset_state()
    tenant = mk_client("Safe API errors")
    account = mk_account(tenant["id"], platform, "1234567890")

    def fail(*_args):
        raise HTTPException(503, detail={"message": PRIVATE, "provider_body": PRIVATE})

    app.state.ad_account_sync_service.provider_fetchers = {platform: fail}
    response = client.post("/ad-accounts/sync/run", json={"account_ids": [account["id"]], "force": True})
    assert response.status_code == 200
    assert response.json()["retry_scheduled"] == 1
    replies = [response]
    if historical:
        # Simulate a record written by a pre-fix binary, not a new sync error.
        job_store = app.state.ad_account_sync_service.job_store
        job = next(iter(job_store.items.values()))
        job_store.items[job.id] = job.model_copy(update={"error_message": PRIVATE})
        account_store = app.state.ad_account_store
        previous = account_store.items[UUID(account["id"])]
        account_store.items[previous.id] = previous.model_copy(update={
            "sync_error": PRIVATE, "metadata": {**previous.metadata, "sync_error": PRIVATE},
        })
    for route in (
        f"/ad-accounts/sync/jobs?account_id={account['id']}",
        f"/ad-accounts/{account['id']}",
        f"/ad-accounts/sync/diagnostics?client_id={tenant['id']}",
        "/alerts?status=all",
    ):
        reply = client.get(route)
        assert reply.status_code == 200, reply.text
        replies.append(reply)
    assert all(value not in reply.text for reply in replies for value in SECRETS)


@pytest.mark.parametrize("kind", ["blocked", "permissions"])
def test_actionable_diagnostics_and_alerts_survive_without_provider_body(kind):
    from test_ad_account_sync import app, client, mk_account, mk_client, reset_state

    reset_state()
    tenant = mk_client("Blocked account diagnostics")
    account = mk_account(tenant["id"], "google", "1234567890")

    def fail(*_args):
        status, message = (403, "customer_not_enabled") if kind == "blocked" else (502, "API key permission scopes mismatch")
        raise HTTPException(status, detail={"message": message + " " + PRIVATE})

    app.state.ad_account_sync_service.provider_fetchers = {"google": fail}
    response = client.post("/ad-accounts/sync/run", json={"account_ids": [account["id"]], "force": True})
    assert response.status_code == 200
    expected_code = "auth_failed" if kind == "blocked" else "provider_unavailable"
    expected_message = BLOCKED_ACCOUNT_MESSAGE if kind == "blocked" else PERMISSION_MESSAGE
    assert response.json()["jobs"][0]["error_code"] == expected_code
    assert response.json()["jobs"][0]["error_message"] == expected_message
    alerts = client.get("/alerts?status=open")
    assert alerts.status_code == 200
    if kind == "blocked":
        blocked = next(item for item in alerts.json() if item["code"] == "account.blocked_or_disabled")
        assert blocked["severity"] == "critical" and blocked["ad_account_id"] == account["id"]
    else:
        assert response.json()["retry_scheduled"] == 1
        assert any(item["code"] == "provider.unavailable" for item in alerts.json())
        overview = client.get("/integrations/overview")
        assert overview.status_code == 200
        google = next(item for item in overview.json()["providers"] if item["provider"] == "google")
        assert "permission" in google["last_error_safe"].lower()
        assert all(value not in overview.text for value in SECRETS)
    assert all(value not in alerts.text for value in SECRETS)


@pytest.mark.parametrize("marker", ["customer_not_enabled", "not enabled", "blocked", "disabled", "suspended"])
def test_historical_blocked_diagnostics_remain_actionable_without_raw_body(marker):
    assert safe_sync_error_message("auth_failed", marker + " " + PRIVATE) == BLOCKED_ACCOUNT_MESSAGE


@pytest.mark.parametrize("code", [None, "provider_unavailable", "unknown_legacy_code"])
def test_historical_errors_are_hidden_on_read_without_rewriting_stored_evidence(stores, code):
    accounts, _stats, jobs, account, path = stores
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    historical = AdAccountSyncJobOut(id=uuid4(), ad_account_id=account.id, provider="meta", status="error",
                                     started_at=now, finished_at=now, created_at=now,
                                     error_message=PRIVATE, error_code=code)
    returned = jobs.create(historical)
    account_result = accounts.patch(account.id, AdAccountPatch(metadata={"sync_error": PRIVATE, "sync_error_code": code}))
    snapshots = [returned, account_result, jobs.list(account_id=account.id)[0],
                 jobs.latest_by_account_ids([account.id])[account.id], accounts.get(account.id), accounts.list()[0]]
    assert all(value not in model.model_dump_json() for model in snapshots for value in SECRETS)
    if path:
        with runtime_conn(path) as conn:
            stored_job = conn.execute("SELECT error_message FROM ad_account_sync_jobs WHERE id=?", (str(historical.id),)).fetchone()
            stored_account = conn.execute("SELECT metadata FROM ad_accounts WHERE id=?", (str(account.id),)).fetchone()
        assert stored_job["error_message"] == PRIVATE
        assert json.loads(stored_account["metadata"])["sync_error"] == PRIVATE
    else:
        assert jobs.items[historical.id].error_message == PRIVATE
        assert accounts.items[account.id].metadata["sync_error"] == PRIVATE


@pytest.mark.parametrize("code,action", [
    ("meta_bound_credential_missing", "rediscover"),
    ("meta_credential_selection_required", "Rediscover"),
    ("meta_rediscovery_required", "Rediscover"),
    ("meta_permissions_missing", "grant advertising access"),
    ("meta_access_token_missing", "Connect Meta Ads"),
    ("meta_connection_unverified", "verify"),
])
def test_known_meta_reconnect_diagnostics_keep_safe_user_action(code, action):
    exc = MetaCredentialReconnectRequiredError(code, PRIVATE)
    message = AdAccountSyncService._to_error_message(exc)
    assert action in message
    assert all(value not in message for value in SECRETS)
