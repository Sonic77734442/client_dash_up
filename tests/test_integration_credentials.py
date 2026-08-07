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


def test_admin_sync_and_discovery_can_use_bound_agency_credentials():
    reset_state()
    tenant = mk_client("Agency credential tenant")
    unrelated = mk_client("Unrelated tenant")
    account = mk_account(tenant["id"], "google", "1234567890")

    agency = client.post(
        "/platform/agencies",
        json={"name": "Credential Agency", "status": "active", "plan": "starter"},
    ).json()
    assert client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
    ).status_code == 200
    assert client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "agency",
            "scope_id": agency["id"],
            "credentials": {"refresh_token": "agency-refresh-token"},
        },
    ).status_code == 200

    store = app.state.integration_credential_store
    assert store.resolve_for_client(provider="google", client_id=UUID(unrelated["id"])) is None

    captured = {"sync": None, "discovery": None}

    def fake_fetcher(external_id, date_from, date_to, creds=None):
        captured["sync"] = creds
        return [{"date": date_to}]

    def fake_discoverer(creds=None):
        captured["discovery"] = creds
        return [{"external_account_id": "0987654321", "name": "Agency Google", "currency": "USD"}]

    app.state.ad_account_sync_service.provider_fetchers = {"google": fake_fetcher}
    app.state.ad_account_discovery_service.discoverers = {"google": fake_discoverer}

    synced = client.post("/ad-accounts/sync/run", json={"account_ids": [account["id"]], "force": True})
    discovered = client.post(
        "/ad-accounts/discover",
        json={"provider": "google", "client_id": tenant["id"]},
    )

    assert synced.status_code == 200
    assert synced.json()["success"] == 1
    assert captured["sync"]["refresh_token"] == "agency-refresh-token"
    assert discovered.status_code == 200
    assert discovered.json()["created"] == 1
    assert captured["discovery"]["refresh_token"] == "agency-refresh-token"


def test_integration_credentials_support_multiple_connection_keys_per_scope():
    reset_state()
    c = mk_client("Multi")

    first = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "client",
            "scope_id": c["id"],
            "connection_key": "google:mcc-111",
            "credentials": {"refresh_token": "rt-111"},
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "client",
            "scope_id": c["id"],
            "connection_key": "google:mcc-222",
            "credentials": {"refresh_token": "rt-222"},
        },
    )
    assert second.status_code == 200

    update_first = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "client",
            "scope_id": c["id"],
            "connection_key": "google:mcc-111",
            "credentials": {"refresh_token": "rt-111-updated"},
        },
    )
    assert update_first.status_code == 200

    store = app.state.integration_credential_store
    resolved_many = store.resolve_many_for_client(provider="google", client_id=UUID(c["id"]))
    assert len(resolved_many) == 2
    keys = {row.connection_key for row in resolved_many}
    assert keys == {"google:mcc-111", "google:mcc-222"}
    token_by_key = {row.connection_key: row.credentials.get("refresh_token") for row in resolved_many}
    assert token_by_key["google:mcc-111"] == "rt-111-updated"
    assert token_by_key["google:mcc-222"] == "rt-222"


def test_only_agency_owner_or_manager_can_archive_shared_connection():
    reset_state()
    agency = client.post(
        "/platform/agencies",
        json={"name": "Credential role guard", "status": "active", "plan": "starter"},
    ).json()

    actors = {}
    for role in ("owner", "manager", "member"):
        user = client.post(
            "/auth/internal/users",
            json={
                "email": f"credential-{role}@test.local",
                "name": role,
                "role": "agency",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/platform/agencies/{agency['id']}/members",
            json={"user_id": user["id"], "role": role, "status": "active"},
        ).status_code == 200
        token = client.post(
            "/auth/internal/sessions/issue",
            json={"user_id": user["id"], "ttl_minutes": 60},
        ).json()["token"]
        actors[role] = {"Authorization": f"Bearer {token}"}

    credential_ids = []
    for connection_key in ("role-guard-manager", "role-guard-owner"):
        created = client.post(
            "/platform/integration-credentials",
            json={
                "provider": "meta",
                "scope_type": "agency",
                "scope_id": agency["id"],
                "connection_key": connection_key,
                "credentials": {"access_token": f"token-{connection_key}"},
            },
        )
        assert created.status_code == 200
        credential_ids.append(created.json()["id"])

    denied = client.delete(
        f"/me/integration-connections/{credential_ids[0]}",
        headers=actors["member"],
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "agency_manage_forbidden"

    assert client.delete(
        f"/me/integration-connections/{credential_ids[0]}",
        headers=actors["manager"],
    ).status_code == 200
    assert client.delete(
        f"/me/integration-connections/{credential_ids[1]}",
        headers=actors["owner"],
    ).status_code == 200


def test_agency_resolution_never_inherits_platform_global_credentials():
    reset_state()
    tenant = mk_client("Global isolation")
    created = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "global",
            "credentials": {"access_token": "platform-secret"},
        },
    )
    assert created.status_code == 200

    store = app.state.integration_credential_store
    trusted = store.resolve_many_for_client(provider="meta", client_id=UUID(tenant["id"]), user_id=None)
    agency_request = store.resolve_many_for_client(
        provider="meta",
        client_id=UUID(tenant["id"]),
        user_id=UUID("00000000-0000-0000-0000-000000000123"),
    )

    assert [row.scope_type for row in trusted] == ["global"]
    assert agency_request == []
