from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-integrations@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name: str):
    r = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert r.status_code == 200
    return r.json()


def mk_account(client_id: str, platform: str, external: str):
    r = client.post(
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
    assert r.status_code == 200
    return r.json()


def test_integrations_overview_returns_provider_health_and_sanitized_events():
    reset_state()
    c = mk_client("Acme")
    meta_acc = mk_account(c["id"], "meta", "meta-1")
    g_acc = mk_account(c["id"], "google", "google-1")

    service = app.state.ad_account_sync_service
    service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"date": date_from}],
        "google": lambda external, date_from, date_to: (_ for _ in ()).throw(HTTPException(status_code=502, detail="API key permission scopes mismatch")),
    }

    run = client.post("/ad-accounts/sync/run", json={"account_ids": [meta_acc["id"], g_acc["id"]]})
    assert run.status_code == 200

    res = client.get("/integrations/overview")
    assert res.status_code == 200
    body = res.json()

    providers = {p["provider"]: p for p in body["providers"]}
    assert "meta" in providers
    assert "google" in providers
    assert providers["meta"]["status"] in {"healthy", "warning", "error", "disconnected"}
    assert providers["google"]["status"] in {"healthy", "warning", "error", "disconnected"}

    # Sanitized (no raw provider detail dump)
    google_error = providers["google"].get("last_error_safe") or ""
    assert "scope" in google_error.lower() or "permission" in google_error.lower()
    assert "api key permission scopes mismatch" not in google_error.lower()

    assert isinstance(body["summary"]["connected_providers"], int)
    assert isinstance(body["summary"]["critical_issues"], int)

    assert isinstance(body["events"], list)
    if body["events"]:
        evt = body["events"][0]
        assert evt["level"] in {"success", "warning", "error"}
        assert "message" in evt


def test_integrations_overview_accounts_for_google_identity_link():
    reset_state()
    c = mk_client("Identity Tenant")
    _ = mk_account(c["id"], "google", "g-identity-1")

    # Simulate agency user that signed in with Google OAuth identity.
    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency-identity@test.local", "name": "Agency", "role": "agency", "status": "active"},
    ).json()
    linked = client.post(
        "/auth/internal/identities/link",
        json={
            "user_id": agency_user["id"],
            "provider": "google",
            "provider_user_id": "google-user-123",
            "email": "agency-identity@test.local",
            "email_verified": True,
        },
    )
    assert linked.status_code == 200

    cfg = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "g-client",
            "client_secret": "g-secret",
            "redirect_uri": "http://127.0.0.1:8000/auth/google/callback",
            "enabled": True,
        },
    )
    assert cfg.status_code == 200

    res = client.get("/integrations/overview")
    assert res.status_code == 200
    providers = {p["provider"]: p for p in res.json()["providers"]}
    google = providers["google"]
    assert google["identity_linked_users"] >= 1
    assert "identity_link" in google["connection_sources"]
    assert google["sync_ready"] is True
