from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    r = client.post("/_testing/use-inmemory-stores")
    assert r.status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    ).json()
    token = client.post("/auth/internal/sessions/issue", json={"user_id": admin["id"], "ttl_minutes": 60}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})


def test_error_envelope_not_found():
    reset_state()
    res = client.get("/clients/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
    body = res.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "not_found"
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["details"], dict)


def test_error_envelope_validation():
    reset_state()
    # Missing required `name`
    res = client.post("/clients", json={"status": "active", "default_currency": "USD"})
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Validation failed"
    assert "errors" in body["error"]["details"]


def test_error_envelope_conflict_business_rule():
    reset_state()
    c = client.post("/clients", json={"name": "Acme", "status": "active", "default_currency": "USD"}).json()
    a = client.post(
        "/ad-accounts",
        json={
            "client_id": c["id"],
            "platform": "meta",
            "external_account_id": "env-meta",
            "name": "Meta",
            "currency": "USD",
            "status": "active",
        },
    ).json()

    first = client.post(
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
        },
    )
    assert first.status_code == 200

    conflict = client.post(
        "/budgets",
        json={
            "client_id": c["id"],
            "scope": "account",
            "account_id": a["id"],
            "amount": "900.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-15",
            "end_date": "2026-05-15",
        },
    )
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["error"]["code"] == "conflict"
    assert isinstance(body["error"]["message"], str)
