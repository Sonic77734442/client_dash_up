from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def mk_admin_token() -> str:
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-audit@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    return issued.json()["token"]


def mk_agency_user() -> dict:
    row = client.post(
        "/auth/internal/users",
        json={"email": "agency-audit@test.local", "name": "Agency", "role": "agency", "status": "active"},
    )
    assert row.status_code == 200
    return row.json()


def test_audit_logs_cover_access_budget_and_sync_actions():
    reset_state()
    token = mk_admin_token()

    c = client.post(
        "/clients",
        json={"name": "Audit Tenant", "status": "active", "default_currency": "USD"},
        headers=auth_header(token),
    ).json()

    a = client.post(
        "/ad-accounts",
        json={
            "client_id": c["id"],
            "platform": "meta",
            "external_account_id": "audit-meta-1",
            "name": "Audit Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=auth_header(token),
    ).json()

    b = client.post(
        "/budgets",
        json={
            "client_id": c["id"],
            "scope": "account",
            "account_id": a["id"],
            "amount": "1000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "note": "audit budget",
        },
        headers=auth_header(token),
    )
    assert b.status_code == 200

    agency = mk_agency_user()
    assigned = client.post(
        "/auth/internal/access",
        json={"user_id": agency["id"], "client_id": c["id"], "role": "agency"},
        headers=auth_header(token),
    )
    assert assigned.status_code == 200

    sync = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [a["id"]]},
        headers=auth_header(token),
    )
    assert sync.status_code == 200

    logs = client.get("/audit/logs?limit=200", headers=auth_header(token))
    assert logs.status_code == 200
    event_types = {x["event_type"] for x in logs.json()}
    assert "budget.created" in event_types
    assert "access.assigned" in event_types
    assert "sync.run" in event_types


def test_audit_logs_admin_only():
    reset_state()
    token = mk_admin_token()

    agency = mk_agency_user()
    agency_token = client.post("/auth/internal/sessions/issue", json={"user_id": agency["id"], "ttl_minutes": 60}).json()["token"]

    denied = client.get("/audit/logs", headers=auth_header(agency_token))
    assert denied.status_code == 403

    ok = client.get("/audit/logs", headers=auth_header(token))
    assert ok.status_code == 200
