from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdAccountOut, AdAccountSyncJobOut
from app.services.integrations import build_integrations_overview


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


def test_integrations_overview_requires_real_google_credentials_not_only_an_identity_link(monkeypatch):
    reset_state()
    for key in (
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
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
    assert google["sync_ready"] is False

    stored = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "global",
            "connection_key": "google:test",
            "credentials": {
                "client_id": "g-client",
                "client_secret": "g-secret",
                "refresh_token": "g-refresh",
                "developer_token": "g-developer",
            },
        },
    )
    assert stored.status_code == 200

    with_credentials = client.get("/integrations/overview")
    assert with_credentials.status_code == 200
    google = {p["provider"]: p for p in with_credentials.json()["providers"]}["google"]
    assert google["sync_ready"] is True
    assert "stored_credentials" in google["connection_sources"]


def test_integrations_overview_is_not_available_to_client_role():
    reset_state()
    tenant = mk_client("Read-only client")
    user = client.post(
        "/auth/internal/users",
        json={"email": "client-integrations@test.local", "name": "Client", "role": "client", "status": "active"},
    ).json()
    access = client.post(
        "/auth/internal/access",
        json={"user_id": user["id"], "client_id": tenant["id"], "role": "client"},
    )
    assert access.status_code == 200
    token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user["id"], "ttl_minutes": 60},
    ).json()["token"]

    denied = client.get(
        "/integrations/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"


def test_provider_health_requires_fresh_coverage_for_every_active_account(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "configured-for-test")
    client_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    created_at = datetime(2026, 1, 1)

    def account(account_id, external_id):
        return AdAccountOut(
            id=account_id,
            client_id=client_id,
            platform="meta",
            external_account_id=external_id,
            name=external_id,
            currency="USD",
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )

    historical_success = AdAccountSyncJobOut(
        id=uuid4(),
        ad_account_id=first_id,
        provider="meta",
        status="success",
        started_at=datetime(2026, 6, 1),
        finished_at=datetime(2026, 6, 1),
        records_synced=12,
        request_meta={"date_from": "2026-05-01", "date_to": "2026-06-01"},
        created_at=datetime(2026, 6, 1),
    )

    result = build_integrations_overview(
        accounts=[account(first_id, "one"), account(second_id, "two")],
        sync_jobs=[historical_success],
        provider_configs=[],
        now=datetime(2026, 8, 6),
    )
    provider = result.providers[0]

    assert provider.status == "warning"
    assert provider.active_accounts_count == 2
    assert provider.accounts_with_data_count == 1
    assert provider.never_synced_accounts_count == 1
    assert provider.stale_accounts_count == 2
    assert provider.coverage_percent == 50.0
    assert provider.latest_data_date.isoformat() == "2026-05-01"
    assert result.summary["data_quality"] == "attention_required"


def test_empty_success_is_not_treated_as_healthy(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "configured-for-test")
    now = datetime(2026, 8, 6, 12, 0)
    account_id = uuid4()
    account = AdAccountOut(
        id=account_id,
        client_id=uuid4(),
        platform="meta",
        external_account_id="empty",
        name="empty",
        currency="USD",
        status="active",
        created_at=now,
        updated_at=now,
    )
    empty_success = AdAccountSyncJobOut(
        id=uuid4(),
        ad_account_id=account_id,
        provider="meta",
        status="success",
        started_at=now,
        finished_at=now,
        records_synced=0,
        request_meta={"date_from": "2026-08-06", "date_to": "2026-08-06"},
        created_at=now,
    )

    provider = build_integrations_overview(
        accounts=[account],
        sync_jobs=[empty_success],
        provider_configs=[],
        now=now,
    ).providers[0]

    assert provider.status == "warning"
    assert provider.rows_present is False
    assert provider.accounts_with_data_count == 0


def test_legacy_duplicate_assignments_are_reported_as_configuration_errors(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "configured-for-test")
    now = datetime(2026, 8, 6, 12, 0)

    def account(client_id, account_id, external_id):
        return AdAccountOut(
            id=account_id,
            client_id=client_id,
            platform="meta",
            external_account_id=external_id,
            name=external_id,
            currency="USD",
            status="active",
            created_at=now,
            updated_at=now,
        )

    result = build_integrations_overview(
        accounts=[
            account(uuid4(), uuid4(), "act_998877"),
            account(uuid4(), uuid4(), "998877"),
        ],
        sync_jobs=[],
        provider_configs=[],
        now=now,
    )
    provider = result.providers[0]

    assert provider.status == "error"
    assert provider.assignment_conflict_accounts_count == 2
    assert "ambiguous client ownership" in (provider.status_reason or "")
    assert result.summary["assignment_conflict_accounts"] == 2
    assert result.summary["critical_issues"] == 1
