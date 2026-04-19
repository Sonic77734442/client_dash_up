from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app


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


def test_sync_jobs_list_filters_by_account_id():
    reset_state()
    c = mk_client("Nova")
    a1 = mk_account(c["id"], "meta", "m-1")
    a2 = mk_account(c["id"], "meta", "m-2")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"id": external}],
    }

    assert client.post("/ad-accounts/sync/run", json={"account_ids": [a1["id"], a2["id"]]}).status_code == 200

    jobs_a1 = client.get(f"/ad-accounts/sync/jobs?account_id={a1['id']}&status=all")
    assert jobs_a1.status_code == 200
    assert jobs_a1.json()["count"] == 1
    assert jobs_a1.json()["items"][0]["ad_account_id"] == a1["id"]


def test_sync_run_empty_selection_processes_zero():
    reset_state()
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
        "meta": lambda external, date_from, date_to, creds=None: [{"id": external}],
    }

    run = client.post("/ad-accounts/sync/run", json={"client_id": c1["id"]})
    assert run.status_code == 200
    body = run.json()
    assert body["processed"] == 1
    assert body["jobs"][0]["ad_account_id"] == a1["id"]


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
