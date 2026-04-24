from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def bootstrap_admin_token() -> str:
    admin = client.post(
        "/auth/internal/users",
        json={"email": "alerts.admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    token = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60}).json()["token"]
    return token


def mk_client(admin_token: str) -> dict:
    row = client.post(
        "/clients",
        json={"name": "Alerts Client", "status": "active", "default_currency": "USD"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert row.status_code == 200
    return row.json()


def mk_account(admin_token: str, client_id: str, platform: str, external_id: str) -> dict:
    row = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external_id,
            "name": f"{platform}:{external_id}",
            "currency": "USD",
            "status": "active",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert row.status_code == 200
    return row.json()


def test_sync_blocked_alert_open_and_resolve():
    reset_state()
    admin_token = bootstrap_admin_token()
    c = mk_client(admin_token)
    account = mk_account(admin_token, c["id"], "google", "1234567890")

    def blocked_fetcher(_external, _date_from, _date_to, _creds=None):
        raise HTTPException(status_code=403, detail="customer_not_enabled")

    app.state.ad_account_sync_service.provider_fetchers = {"google": blocked_fetcher}
    run = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account["id"]], "force": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert run.status_code == 200

    alerts = client.get("/alerts?status=open", headers={"Authorization": f"Bearer {admin_token}"})
    assert alerts.status_code == 200
    opened = alerts.json()
    blocked = next((x for x in opened if x["code"] == "account.blocked_or_disabled"), None)
    assert blocked is not None
    assert blocked["ad_account_id"] == account["id"]

    def ok_fetcher(_external, date_from, _date_to, _creds=None):
        return [{"date": date_from, "impressions": 1, "clicks": 1, "spend": 1}]

    app.state.ad_account_sync_service.provider_fetchers = {"google": ok_fetcher}
    run_ok = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [account["id"]], "force": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert run_ok.status_code == 200

    resolved = client.get("/alerts?status=resolved", headers={"Authorization": f"Bearer {admin_token}"})
    assert resolved.status_code == 200
    assert any(x["fingerprint"] == blocked["fingerprint"] for x in resolved.json())


def test_discovery_auth_alert_open_and_resolve():
    reset_state()
    admin_token = bootstrap_admin_token()
    c = mk_client(admin_token)

    def failing_discoverer(_creds=None):
        raise Exception("permission denied")

    app.state.ad_account_discovery_service.discoverers = {"google": failing_discoverer}

    bad = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": c["id"], "upsert_existing": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert bad.status_code == 200
    assert bad.json()["providers_failed"].get("google")

    alerts = client.get("/alerts?status=open", headers={"Authorization": f"Bearer {admin_token}"})
    assert alerts.status_code == 200
    opened = alerts.json()
    auth_failed = next((x for x in opened if x["code"] == "discovery.auth_failed"), None)
    assert auth_failed is not None

    def ok_discoverer(_creds=None):
        return [{"external_account_id": "111", "name": "Google 111", "currency": "USD"}]

    app.state.ad_account_discovery_service.discoverers = {"google": ok_discoverer}
    ok = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": c["id"], "upsert_existing": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ok.status_code == 200
    assert ok.json()["providers_failed"] == {}

    resolved = client.get("/alerts?status=resolved", headers={"Authorization": f"Bearer {admin_token}"})
    assert resolved.status_code == 200
    assert any(x["fingerprint"] == auth_failed["fingerprint"] for x in resolved.json())


def test_alerts_endpoints_are_admin_only():
    reset_state()
    admin_token = bootstrap_admin_token()
    c = mk_client(admin_token)

    client_user = client.post(
        "/auth/internal/users",
        json={"email": "alerts.client@test.local", "name": "Client", "role": "client", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert client_user.status_code == 200
    grant = client.post(
        "/auth/internal/access",
        json={"user_id": client_user.json()["id"], "client_id": c["id"], "role": "client"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert grant.status_code == 200
    client_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": client_user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]

    listed = client.get("/alerts?status=all", headers={"Authorization": f"Bearer {client_token}"})
    assert listed.status_code == 403
