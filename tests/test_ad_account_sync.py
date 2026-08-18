from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.schemas import AdAccountPatch
from app.services.ad_account_sync import AdAccountSyncService


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-sync@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name: str):
    res = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert res.status_code == 200
    return res.json()


def mk_account(client_id: str, platform: str, external: str):
    res = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external,
            "name": f"{platform}-{external}",
            "currency": "USD",
            "status": "active",
        },
    )
    assert res.status_code == 200
    return res.json()


def seed_account_metadata(account_id: str, metadata: dict):
    """Seed server-owned sync state without going through the public API."""
    return app.state.ad_account_store.patch(
        UUID(account_id),
        AdAccountPatch(metadata=metadata),
    )


def test_sync_run_updates_account_sync_fields_and_job_log():
    reset_state()
    c = mk_client("Acme")
    ok_acc = mk_account(c["id"], "meta", "meta-1")
    bad_acc = mk_account(c["id"], "google", "google-1")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"date": date_from}],
        "google": lambda external, date_from, date_to: (_ for _ in ()).throw(HTTPException(status_code=502, detail="provider down")),
    }

    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [ok_acc["id"], bad_acc["id"]], "date_from": "2026-04-01", "date_to": "2026-04-02"},
    )
    assert run.status_code == 200
    body = run.json()
    assert body["processed"] == 2
    assert body["skipped"] == 0
    assert body["success"] == 1
    assert body["failed"] == 1
    assert body["retry_scheduled"] == 1

    listed = client.get("/ad-accounts?status=all")
    assert listed.status_code == 200
    by_id = {x["id"]: x for x in listed.json()["items"]}
    assert by_id[ok_acc["id"]]["sync_status"] == "success"
    assert by_id[bad_acc["id"]]["sync_status"] == "error"
    assert by_id[bad_acc["id"]]["sync_error"] == "provider down"
    assert by_id[bad_acc["id"]]["sync_error_category"] == "provider"
    assert by_id[bad_acc["id"]]["sync_retryable"] is True
    assert by_id[bad_acc["id"]]["sync_next_retry_at"] is not None
    assert by_id[ok_acc["id"]]["last_sync_at"] is not None

    jobs = client.get("/ad-accounts/sync/jobs?status=all")
    assert jobs.status_code == 200
    assert jobs.json()["count"] == 2
    failed_job = next(x for x in jobs.json()["items"] if x["status"] == "error")
    assert failed_job["error_category"] == "provider"
    assert failed_job["retryable"] is True
    assert failed_job["attempt"] == 1
    assert failed_job["next_retry_at"] is not None

    stats = client.get("/ad-stats?date_from=2026-04-01&date_to=2026-04-02")
    assert stats.status_code == 200
    assert stats.json()["count"] == 1
    assert stats.json()["items"][0]["ad_account_id"] == ok_acc["id"]


def test_sync_jobs_list_filters_by_account_id():
    reset_state()
    c = mk_client("Nova")
    a1 = mk_account(c["id"], "meta", "m-1")
    a2 = mk_account(c["id"], "meta", "m-2")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"id": external, "date": date_from}],
    }

    assert client.post("/ad-accounts/sync/run", json={"account_ids": [a1["id"], a2["id"]]}).status_code == 200

    jobs_a1 = client.get(f"/ad-accounts/sync/jobs?account_id={a1['id']}&status=all")
    assert jobs_a1.status_code == 200
    assert jobs_a1.json()["count"] == 1
    assert jobs_a1.json()["items"][0]["ad_account_id"] == a1["id"]


def test_sync_run_empty_selection_processes_zero():
    reset_state()
    c = mk_client("Empty selection")
    mk_account(c["id"], "meta", "must-not-sync")
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"id": external, "date": date_from}],
    }
    run = client.post("/ad-accounts/sync/run", json={"account_ids": []})
    assert run.status_code == 200
    body = run.json()
    assert body["requested"] == 0
    assert body["processed"] == 0


def test_sync_run_supports_client_filter():
    reset_state()
    c1 = mk_client("Tenant 1")
    c2 = mk_client("Tenant 2")
    a1 = mk_account(c1["id"], "meta", "m-1")
    mk_account(c2["id"], "meta", "m-2")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: [{"id": external, "date": date_from}],
    }

    run = client.post("/ad-accounts/sync/run", json={"client_id": c1["id"]})
    assert run.status_code == 200
    body = run.json()
    assert body["processed"] == 1
    assert body["jobs"][0]["ad_account_id"] == a1["id"]


def test_sync_preserves_none_vs_empty_scoped_credentials():
    reset_state()
    tenant = mk_client("Credential sentinel sync")
    account = mk_account(tenant["id"], "meta", "sentinel-meta")
    service = app.state.ad_account_sync_service
    seen = []
    service.provider_fetchers = {
        "meta": lambda _external, date_from, _date_to, credentials=None: seen.append(credentials) or [{"date": date_from}],
    }

    service.credential_candidates_resolver = lambda *_args: []
    first = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account["id"]], "date_from": "2026-08-01", "date_to": "2026-08-01", "force": True},
    )
    assert first.status_code == 200

    service.credential_candidates_resolver = lambda *_args: [{}]
    second = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account["id"]], "date_from": "2026-08-02", "date_to": "2026-08-02", "force": True},
    )
    assert second.status_code == 200

    assert seen == [None, {}]


@pytest.mark.parametrize("resolved_candidates", ([], [None]))
def test_agency_sync_without_scoped_credentials_never_uses_global_env(
    monkeypatch,
    resolved_candidates,
):
    reset_state()
    tenant = mk_client("No agency sync credentials")
    account = mk_account(tenant["id"], "meta", "agency-meta")
    service = app.state.ad_account_sync_service
    calls = []
    monkeypatch.setenv("META_ACCESS_TOKEN", "platform-global-token-must-not-be-used")
    service.credential_candidates_resolver = lambda *_args: resolved_candidates
    service.provider_fetchers = {
        "meta": lambda _external, _date_from, _date_to, credentials=None: calls.append(credentials) or [],
    }

    result = service.run_sync(
        account_ids=[UUID(account["id"])],
        user_id=uuid4(),
        force=True,
    )

    assert calls == []
    assert result.success == 0
    assert result.failed == 1
    assert result.jobs[0].error_code == "provider_credentials_missing"
    assert result.jobs[0].error_category == "configuration"
    assert result.jobs[0].retryable is False


def test_sync_retry_backoff_skips_until_force():
    reset_state()
    c = mk_client("Backoff")
    acc = mk_account(c["id"], "meta", "meta-backoff")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: (_ for _ in ()).throw(HTTPException(status_code=503, detail="temporary outage")),
    }

    first = client.post("/ad-accounts/sync/run", json={"account_ids": [acc["id"]]})
    assert first.status_code == 200
    body1 = first.json()
    assert body1["processed"] == 1
    assert body1["failed"] == 1
    assert body1["retry_scheduled"] == 1

    second = client.post("/ad-accounts/sync/run", json={"account_ids": [acc["id"]]})
    assert second.status_code == 200
    body2 = second.json()
    assert body2["processed"] == 0
    assert body2["skipped"] == 1

    forced = client.post("/ad-accounts/sync/run", json={"account_ids": [acc["id"]], "force": True})
    assert forced.status_code == 200
    body3 = forced.json()
    assert body3["processed"] == 1
    assert body3["failed"] == 1

    jobs = client.get(f"/ad-accounts/sync/jobs?account_id={acc['id']}&status=all")
    assert jobs.status_code == 200
    assert jobs.json()["count"] == 2
    latest = jobs.json()["items"][0]
    prev = jobs.json()["items"][1]
    assert latest["attempt"] == prev["attempt"] + 1


def test_sync_error_classification_for_google_manager_metrics_error():
    exc = RuntimeError(
        "errors { error_code { query_error: REQUESTED_METRICS_FOR_MANAGER } "
        "message: 'Metrics cannot be requested for a manager account.' }"
    )
    code, category, retryable = AdAccountSyncService._classify_error(exc)
    assert code == "invalid_request"
    assert category == "validation"
    assert retryable is False


def test_sync_auto_date_range_uses_one_time_backfill_before_incremental_mode():
    reset_state()
    c = mk_client("Auto Range")
    fresh = mk_account(c["id"], "meta", "meta-fresh")
    existing = mk_account(c["id"], "meta", "meta-existing")
    seed_account_metadata(existing["id"], {"last_sync_at": "2026-04-10T12:00:00"})

    seen = []

    def fake_fetcher(external_id, date_from, date_to, creds=None):
        seen.append((external_id, date_from, date_to))
        return []

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {"meta": fake_fetcher}

    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [fresh["id"], existing["id"]], "date_to": "2026-04-20", "force": True},
    )
    assert run.status_code == 200

    by_external = {external_id: (date_from, date_to) for (external_id, date_from, date_to) in seen}
    expected_fresh_from = "2025-10-23"
    # Without history_backfill_completed_at marker, both accounts run one-time backfill.
    assert by_external["meta-existing"] == (expected_fresh_from, "2026-04-20")
    assert by_external["meta-fresh"] == (expected_fresh_from, "2026-04-20")


def test_sync_rounds_money_fields_before_ingest():
    reset_state()
    c = mk_client("Money Precision")
    acc = mk_account(c["id"], "google", "g-precise")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "google": lambda external, date_from, date_to: [
            {"date": date_from, "impressions": 100, "clicks": 10, "spend": 158.629157, "conversions": 1.9876}
        ],
    }

    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_from": "2026-04-01", "date_to": "2026-04-01", "force": True},
    )
    assert run.status_code == 200
    assert run.json()["success"] == 1
    assert run.json()["failed"] == 0

    stats = client.get("/ad-stats?date_from=2026-04-01&date_to=2026-04-01&platform=google")
    assert stats.status_code == 200
    assert stats.json()["count"] == 1
    row = stats.json()["items"][0]
    assert row["spend"] == "158.63"
    assert row["conversions"] == "1.99"

    job = run.json()["jobs"][0]
    assert job["request_meta"]["latest_data_date"] == "2026-04-01"
    account = client.get(f"/ad-accounts/{acc['id']}").json()
    assert account["metadata"]["latest_data_date"] == "2026-04-01"


@pytest.mark.parametrize(
    ("platform", "date_field"),
    [
        ("meta", "date"),
        ("meta", "date_start"),
        ("google", "day"),
        ("tiktok", "stat_time_day"),
    ],
)
def test_sync_accepts_inclusive_boundaries_for_all_provider_date_fields(platform, date_field):
    reset_state()
    tenant = mk_client(f"Boundary {platform} {date_field}")
    account = mk_account(tenant["id"], platform, f"boundary-{platform}-{date_field}")
    app.state.ad_account_sync_service.provider_fetchers = {
        platform: lambda _external, date_from, date_to, _credentials=None: [
            {date_field: date_from, "spend": 1},
            {date_field: date_to, "spend": 2},
        ]
    }

    run = client.post(
        "/ad-accounts/sync/run",
        json={
            "account_ids": [account["id"]],
            "date_from": "2026-04-01",
            "date_to": "2026-04-02",
            "force": True,
        },
    )

    assert run.status_code == 200
    assert run.json()["success"] == 1
    assert run.json()["jobs"][0]["request_meta"]["latest_data_date"] == "2026-04-02"
    stats = client.get(
        f"/ad-stats?account_id={account['id']}&date_from=2026-04-01&date_to=2026-04-02"
    )
    assert stats.status_code == 200
    assert sorted(row["date"] for row in stats.json()["items"]) == ["2026-04-01", "2026-04-02"]


@pytest.mark.parametrize(
    "invalid_rows",
    [
        [{"date": "2026-04-01", "spend": 1}, {"spend": 2}],
        [{"date": "2026-04-01T00:00:00", "spend": 1}],
        [{"date": "2026-03-31", "spend": 1}],
        [{"date": "2026-04-03", "spend": 1}],
    ],
    ids=["missing-date-no-partial-ingest", "malformed-date", "before-range", "after-range"],
)
def test_invalid_provider_row_dates_fail_closed_without_metrics_or_freshness(invalid_rows):
    reset_state()
    tenant = mk_client("Invalid Provider Dates")
    account = mk_account(tenant["id"], "meta", "invalid-provider-dates")
    prior_freshness = {
        "last_sync_at": "2026-03-30T12:00:00",
        "last_data_at": "2026-03-30",
        "latest_data_date": "2026-03-30",
    }
    seed_account_metadata(account["id"], prior_freshness)
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda *_args, **_kwargs: invalid_rows,
    }

    run = client.post(
        "/ad-accounts/sync/run",
        json={
            "account_ids": [account["id"]],
            "date_from": "2026-04-01",
            "date_to": "2026-04-02",
            "force": True,
        },
    )

    assert run.status_code == 200
    body = run.json()
    assert body["success"] == 0
    assert body["failed"] == 1
    job = body["jobs"][0]
    assert job["records_synced"] == 0
    assert job["error_code"] == "provider_payload_invalid"
    assert job["error_category"] == "validation"
    assert job["retryable"] is False
    assert job["request_meta"]["latest_data_date"] is None

    stats = client.get(
        f"/ad-stats?account_id={account['id']}&date_from=2026-04-01&date_to=2026-04-02"
    )
    assert stats.status_code == 200
    assert stats.json()["count"] == 0

    stored_account = client.get(f"/ad-accounts/{account['id']}").json()
    assert stored_account["last_sync_at"] == prior_freshness["last_sync_at"]
    assert stored_account["metadata"].get("last_data_at") == prior_freshness["last_data_at"]
    assert stored_account["metadata"].get("latest_data_date") == prior_freshness["latest_data_date"]
    assert stored_account["metadata"].get("history_backfill_completed_at") is None
    assert stored_account["metadata"]["sync_data_status"] == "error"


def test_empty_provider_response_records_attempt_but_does_not_mark_data_fresh():
    reset_state()
    c = mk_client("No Data")
    acc = mk_account(c["id"], "meta", "meta-empty")
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: [],
    }

    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_from": "2026-04-01", "date_to": "2026-04-02", "force": True},
    )
    assert run.status_code == 200
    assert run.json()["success"] == 1
    job = run.json()["jobs"][0]
    assert job["records_synced"] == 0
    assert job["request_meta"]["empty_response"] is True
    assert job["request_meta"]["provider_rows_received"] == 0

    account = client.get(f"/ad-accounts/{acc['id']}").json()
    assert account["last_sync_at"] is None
    assert account["metadata"]["last_sync_attempt_at"] is not None
    assert account["metadata"]["last_sync_success_at"] is not None
    assert account["metadata"]["sync_data_status"] == "empty"
    assert account["metadata"].get("last_data_at") is None
    assert account["metadata"].get("history_backfill_completed_at") is None

    diagnostics = client.get(f"/ad-accounts/sync/diagnostics?client_id={c['id']}")
    assert diagnostics.status_code == 200
    diagnostic_body = diagnostics.json()
    item = diagnostic_body["items"][0]
    assert item["sync_state"] == "no_data"
    assert "no metric rows" in item["diagnostic_message"]
    assert "date range" in item["action_hint"]
    assert item["last_sync_at"] is None
    assert diagnostic_body["summary"]["no_data"] == 1
    assert diagnostic_body["summary"]["error"] == 0
    assert diagnostic_body["summary"]["healthy"] == 0


def test_empty_initial_response_keeps_historical_backfill_window_for_next_attempt():
    reset_state()
    c = mk_client("No Data Retry")
    acc = mk_account(c["id"], "meta", "meta-empty-retry")
    seen = []

    def empty_fetcher(external, date_from, date_to, creds=None):
        seen.append((date_from, date_to))
        return []

    app.state.ad_account_sync_service.provider_fetchers = {"meta": empty_fetcher}
    first = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_to": "2026-04-20", "force": True},
    )
    second = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_to": "2026-04-21", "force": True},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert seen == [("2025-10-23", "2026-04-20"), ("2025-10-24", "2026-04-21")]


def test_explicit_short_sync_does_not_mark_full_history_backfill_complete():
    reset_state()
    c = mk_client("Short Explicit Window")
    acc = mk_account(c["id"], "meta", "meta-short-window")
    seen = []

    def fetcher(external, date_from, date_to, creds=None):
        seen.append((date_from, date_to))
        return [{"date": date_from}]

    app.state.ad_account_sync_service.provider_fetchers = {"meta": fetcher}
    short = client.post(
        "/ad-accounts/sync/run",
        json={
            "account_ids": [acc["id"]],
            "date_from": "2026-03-22",
            "date_to": "2026-04-20",
            "force": True,
        },
    )
    assert short.status_code == 200
    after_short = client.get(f"/ad-accounts/{acc['id']}").json()
    assert after_short["metadata"].get("history_backfill_completed_at") is None

    backfill = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_to": "2026-04-21", "force": True},
    )
    assert backfill.status_code == 200
    assert seen == [("2026-03-22", "2026-04-20"), ("2025-10-24", "2026-04-21")]
    after_backfill = client.get(f"/ad-accounts/{acc['id']}").json()
    assert after_backfill["metadata"]["history_backfill_completed_at"] is not None


def test_incremental_sync_uses_latest_metric_date_with_overlap_not_request_heartbeat():
    reset_state()
    c = mk_client("Delayed Provider Data")
    acc = mk_account(c["id"], "meta", "meta-delayed-data")
    seed_account_metadata(
        acc["id"],
        {
            "history_backfill_completed_at": "2026-08-01T00:00:00",
            "latest_data_date": "2026-08-04",
            "last_data_at": "2026-08-04",
            "last_sync_at": "2026-08-06T12:00:00",
            "sync_status": "success",
        },
    )
    seen = []

    def fetcher(external, date_from, date_to, creds=None):
        seen.append((date_from, date_to))
        return [{"date": date_to}]

    app.state.ad_account_sync_service.provider_fetchers = {"meta": fetcher}
    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_to": "2026-08-06", "force": True},
    )
    assert run.status_code == 200
    assert seen == [("2026-08-02", "2026-08-06")]


def test_archived_parent_is_excluded_from_active_registry_stats_and_sync(monkeypatch):
    reset_state()
    c = mk_client("Archived Workspace")
    acc = mk_account(c["id"], "meta", "meta-archived-parent")
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: [{"date": "2026-04-01", "spend": 25}],
    }
    synced = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "date_from": "2026-04-01", "date_to": "2026-04-01", "force": True},
    )
    assert synced.status_code == 200
    assert synced.json()["success"] == 1

    archived = client.delete(f"/clients/{c['id']}")
    assert archived.status_code == 200

    assert client.get("/ad-accounts").json()["count"] == 0
    assert client.get("/ad-accounts?status=all").json()["count"] == 1
    assert client.get("/ad-stats?date_from=2026-04-01&date_to=2026-04-01").json()["count"] == 0
    assert client.get(
        f"/ad-stats?client_id={c['id']}&date_from=2026-04-01&date_to=2026-04-01"
    ).json()["count"] == 1

    manual = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [acc["id"]], "force": True},
    )
    assert manual.status_code == 200
    assert manual.json()["requested"] == 0

    monkeypatch.setenv("SYNC_CRON_ENABLED", "true")
    monkeypatch.setenv("SYNC_CRON_SECRET", "archive-test-secret")
    due = client.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "archive-test-secret"})
    assert due.status_code == 200
    assert due.json()["selected"] == 0

    create_account = client.post(
        "/ad-accounts",
        json={
            "client_id": c["id"],
            "platform": "google",
            "external_account_id": "1234567890",
            "name": "Should not be created",
            "currency": "USD",
            "status": "active",
        },
    )
    assert create_account.status_code == 409
    assert create_account.json()["error"]["code"] == "client_not_active"


def test_due_sync_endpoint_is_protected_and_idempotent(monkeypatch):
    reset_state()
    c = mk_client("Scheduled")
    acc = mk_account(c["id"], "meta", "meta-scheduled")
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: [{"date": date_to}],
    }
    monkeypatch.setenv("SYNC_CRON_ENABLED", "true")
    monkeypatch.setenv("SYNC_CRON_SECRET", "test-cron-secret")

    denied = client.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "wrong"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_sync_cron_secret"

    first = client.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "test-cron-secret"})
    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert first.json()["selected_account_ids"] == [acc["id"]]
    assert first.json()["processed"] == 1
    assert first.json()["success"] == 1

    second = client.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "test-cron-secret"})
    assert second.status_code == 200
    assert second.json()["selected"] == 0
    assert second.json()["processed"] == 0


def test_due_sync_endpoint_returns_already_running_when_lease_is_held(monkeypatch):
    reset_state()
    c = mk_client("Lease")
    mk_account(c["id"], "meta", "meta-lease")
    monkeypatch.setenv("SYNC_CRON_ENABLED", "true")
    monkeypatch.setenv("SYNC_CRON_SECRET", "test-cron-secret")
    service = app.state.ad_account_sync_service
    lease_token = service.job_store.acquire_lease(
        lease_key="ad-account-sync-due",
        now=datetime.now(timezone.utc).replace(tzinfo=None),
        ttl_seconds=120,
    )
    assert lease_token
    try:
        response = client.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "test-cron-secret"})
        assert response.status_code == 200
        assert response.json()["status"] == "already_running"
        assert response.json()["lease_acquired"] is False
    finally:
        service.job_store.release_lease(lease_key="ad-account-sync-due", lease_token=lease_token)


def test_manual_sync_is_skipped_while_shared_provider_run_lease_is_held():
    reset_state()
    c = mk_client("Manual Lease")
    acc = mk_account(c["id"], "meta", "meta-manual-lease")
    calls = []
    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: calls.append(external) or [{"date": date_to}],
    }
    lease_token = service.job_store.acquire_lease(
        lease_key="ad-account-sync-run",
        now=datetime.now(timezone.utc).replace(tzinfo=None),
        ttl_seconds=120,
    )
    assert lease_token
    try:
        response = client.post(
            "/ad-accounts/sync/run",
            json={"account_ids": [acc["id"]], "force": True},
        )
        assert response.status_code == 200
        assert response.json()["processed"] == 0
        assert response.json()["skipped"] == 1
        assert calls == []
    finally:
        service.job_store.release_lease(lease_key="ad-account-sync-run", lease_token=lease_token)


def test_legacy_duplicate_assignments_are_quarantined_from_sync_stats_and_diagnostics():
    reset_state()
    first_client = mk_client("Legacy duplicate A")
    second_client = mk_client("Legacy duplicate B")
    first = mk_account(first_client["id"], "meta", "act_445566")
    second = mk_account(second_client["id"], "meta", "different-before-legacy-import")

    # Simulate a row that predates the current canonical assignment guards.
    store = app.state.ad_account_store
    second_id = UUID(second["id"])
    store.items[second_id] = store.items[second_id].model_copy(update={"external_account_id": "445566"})

    ingested = client.post(
        "/ad-stats/ingest",
        json={
            "rows": [
                {
                    "ad_account_id": first["id"],
                    "date": "2026-08-01",
                    "platform": "meta",
                    "impressions": 100,
                    "clicks": 10,
                    "spend": "10.00",
                    "conversions": "1.00",
                },
                {
                    "ad_account_id": second["id"],
                    "date": "2026-08-01",
                    "platform": "meta",
                    "impressions": 200,
                    "clicks": 20,
                    "spend": "20.00",
                    "conversions": "2.00",
                },
            ]
        },
    )
    assert ingested.status_code == 200

    calls = []
    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: calls.append(external) or []
    }
    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [first["id"], second["id"]], "force": True},
    )
    assert run.status_code == 200
    assert run.json()["processed"] == 0
    assert calls == []

    stats = client.get("/ad-stats?date_from=2026-08-01&date_to=2026-08-01")
    assert stats.status_code == 200
    assert stats.json()["count"] == 0

    diagnostics = client.get("/ad-accounts/sync/diagnostics")
    assert diagnostics.status_code == 200
    conflicted = [item for item in diagnostics.json()["items"] if item["error_code"] == "assignment_conflict"]
    assert {item["ad_account_id"] for item in conflicted} == {first["id"], second["id"]}
    assert all(item["error_category"] == "configuration" for item in conflicted)


def test_due_sync_executes_retry_only_after_retry_time():
    reset_state()
    c = mk_client("Due Retry")
    acc = mk_account(c["id"], "meta", "meta-due-retry")
    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: (_ for _ in ()).throw(
            HTTPException(status_code=503, detail="temporary outage")
        ),
    }
    failed = client.post("/ad-accounts/sync/run", json={"account_ids": [acc["id"]]})
    assert failed.status_code == 200
    assert failed.json()["retry_scheduled"] == 1

    not_due = service.run_due_sync(batch_size=10, stale_after_hours=24)
    assert not_due.selected_account_ids == []

    latest = service.job_store.latest_by_account_ids([UUID(acc["id"])])
    job = next(iter(latest.values()))
    service.job_store.items[job.id] = job.model_copy(
        update={"next_retry_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)}
    )
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to, creds=None: [{"date": date_to}],
    }

    due = service.run_due_sync(batch_size=10, stale_after_hours=24)
    assert [str(account_id) for account_id in due.selected_account_ids] == [acc["id"]]
    assert due.sync_result is not None
    assert due.sync_result.success == 1
    assert due.sync_result.failed == 0


def test_in_process_scheduler_starts_and_stops_with_app_lifespan(monkeypatch):
    monkeypatch.setenv("SYNC_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SYNC_SCHEDULER_INITIAL_DELAY_SECONDS", "300")

    with TestClient(app):
        task = app.state.sync_scheduler_task
        assert task is not None
        assert task.done() is False

    assert app.state.sync_scheduler_task is None
