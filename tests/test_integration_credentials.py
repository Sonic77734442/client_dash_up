from fastapi.testclient import TestClient
from uuid import UUID

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-cred@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    token = issued.json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return admin.json()


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


def test_integration_credentials_resolution_priority_client_over_agency_over_global():
    reset_state()
    c = mk_client("Acme")

    # Global
    r = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "global",
            "credentials": {"refresh_token": "rt-global"},
        },
    )
    assert r.status_code == 200

    store = app.state.integration_credential_store
    resolved = store.resolve_for_client(provider="google", client_id=UUID(c["id"]))
    assert resolved is not None
    assert resolved.scope_type == "global"
    assert resolved.credentials["refresh_token"] == "rt-global"

    # Agency + binding
    agency = client.post("/platform/agencies", json={"name": "Acme Agency", "status": "active", "plan": "starter"})
    assert agency.status_code == 200
    agency_id = agency.json()["id"]
    bind = client.post(f"/platform/agencies/{agency_id}/clients", json={"client_id": c["id"]})
    assert bind.status_code == 200

    r = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "agency",
            "scope_id": agency_id,
            "credentials": {"refresh_token": "rt-agency"},
        },
    )
    assert r.status_code == 200

    resolved = store.resolve_for_client(provider="google", client_id=UUID(c["id"]))
    assert resolved is not None
    assert resolved.scope_type == "agency"
    assert resolved.credentials["refresh_token"] == "rt-agency"

    # Client
    r = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "client",
            "scope_id": c["id"],
            "credentials": {"refresh_token": "rt-client"},
        },
    )
    assert r.status_code == 200

    resolved = store.resolve_for_client(provider="google", client_id=UUID(c["id"]))
    assert resolved is not None
    assert resolved.scope_type == "client"
    assert resolved.credentials["refresh_token"] == "rt-client"


def test_discovery_receives_resolved_tenant_credentials():
    reset_state()
    c = mk_client("Acme")

    r = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "client",
            "scope_id": c["id"],
            "credentials": {"access_token": "meta-client-token"},
        },
    )
    assert r.status_code == 200

    captured = {"credentials": None}

    def fake_discoverer(creds=None):
        captured["credentials"] = creds
        return [{"external_account_id": "m-1", "name": "Meta One", "currency": "USD"}]

    app.state.ad_account_discovery_service.discoverers = {"meta": fake_discoverer}

    run = client.post("/ad-accounts/discover", json={"provider": "meta", "client_id": c["id"]})
    assert run.status_code == 200
    assert captured["credentials"] is not None
    assert captured["credentials"]["access_token"] == "meta-client-token"


def test_sync_receives_resolved_tenant_credentials():
    reset_state()
    c = mk_client("Nova")
    acc = mk_account(c["id"], "google", "1234567890")

    r = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "client",
            "scope_id": c["id"],
            "credentials": {"refresh_token": "google-client-token"},
        },
    )
    assert r.status_code == 200

    captured = {"credentials": None}

    def fake_fetcher(external_id, date_from, date_to, creds=None):
        captured["credentials"] = creds
        return [{"date": date_from}]

    app.state.ad_account_sync_service.provider_fetchers = {"google": fake_fetcher}

    run = client.post("/ad-accounts/sync/run", json={"account_ids": [acc["id"]]})
    assert run.status_code == 200
    assert captured["credentials"] is not None
    assert captured["credentials"]["refresh_token"] == "google-client-token"
