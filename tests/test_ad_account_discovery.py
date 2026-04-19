from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-discovery@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name: str):
    res = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert res.status_code == 200
    return res.json()


def mk_account(client_id: str, platform: str, external: str, name: str, status: str = "active"):
    res = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external,
            "name": name,
            "currency": "USD",
            "status": status,
        },
    )
    assert res.status_code == 200
    return res.json()


def test_discover_requires_client_id_when_multiple_clients_available():
    reset_state()
    mk_client("Acme")
    mk_client("Nova")

    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta"})
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "client_id_required"


def test_discover_uses_single_client_when_client_id_not_provided():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta"})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1
    assert body["client_id"] == c["id"]


def test_discover_creates_accounts_for_selected_client():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": c["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["providers_failed"] == {}
    assert body["items"][0]["platform"] == "meta"
    assert body["items"][0]["client_id"] == c["id"]
    assert body["items"][0]["external_account_id"] == "m-1"


def test_discover_updates_existing_account_when_upsert_enabled():
    reset_state()
    c = mk_client("Acme")
    existing = mk_account(c["id"], "google", "1234567890", "Old Name", status="archived")
    app.state.ad_account_discovery_service.discoverers = {
        "google": lambda: [{"external_account_id": "1234567890", "name": "New Name", "currency": "EUR"}]
    }

    res = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": c["id"], "upsert_existing": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    updated = body["items"][0]
    assert updated["id"] == existing["id"]
    assert updated["name"] == "New Name"
    assert updated["currency"] == "EUR"
    assert updated["status"] == "active"


def test_discover_returns_provider_failures_without_crashing():
    reset_state()
    c = mk_client("Acme")
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: (_ for _ in ()).throw(RuntimeError("meta unavailable")),
    }

    res = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": c["id"]})
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["providers_failed"]["meta"] == "meta unavailable"
