from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import IntegrationCredentialCreate
from app.services.oauth import ExternalIdentityPayload


client = TestClient(app)


def reset_state() -> None:
    client.cookies.clear()
    client.headers.clear()
    assert client.post("/_testing/use-inmemory-stores").status_code == 200


def create_user(email: str, role: str):
    response = client.post(
        "/auth/internal/users",
        json={"email": email, "name": email.split("@", 1)[0], "role": role, "status": "active"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def issue_token(user_id: str) -> str:
    response = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user_id, "ttl_minutes": 60},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_client(name: str, admin_token: str):
    response = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_account(client_id: str, external: str, admin_token: str, platform: str = "meta"):
    response = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external,
            "name": external,
            "currency": "USD",
            "status": "active",
        },
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    return response.json()


def assign(user_id: str, client_id: str, role: str = "client"):
    return client.post(
        "/auth/internal/access",
        json={"user_id": user_id, "client_id": client_id, "role": role},
    )


def provision_owner():
    admin = create_user(f"admin-{uuid4()}@solo.test", "admin")
    admin_token = issue_token(admin["id"])
    tenant = create_client("Solo tenant", admin_token)
    foreign = create_client("Foreign tenant", admin_token)
    owner = create_user(f"owner-{uuid4()}@solo.test", "solo_client")
    assert assign(owner["id"], tenant["id"]).status_code == 200
    owner_token = issue_token(owner["id"])
    return admin, admin_token, tenant, foreign, owner, owner_token


def budget_payload(client_id: str, *, scope: str, amount: str, account_id: str | None = None):
    return {
        "client_id": client_id,
        "scope": scope,
        "account_id": account_id,
        "amount": amount,
        "currency": "USD",
        "period_type": "monthly",
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
    }


def test_solo_owner_scope_and_provisioning_fail_closed():
    reset_state()
    _, admin_token, tenant, foreign, owner, owner_token = provision_owner()

    me = client.get("/auth/me", headers=auth(owner_token))
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "solo_client"
    assert me.json()["session"]["accessible_client_ids"] == [tenant["id"]]

    second = assign(owner["id"], foreign["id"])
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "solo_owner_provisioning_conflict"

    wrong_role = client.post(
        "/auth/internal/access",
        json={"user_id": owner["id"], "client_id": tenant["id"], "role": "agency"},
    )
    assert wrong_role.status_code == 409

    assert client.patch(f"/clients/{tenant['id']}", json={"name": "Changed"}, headers=auth(owner_token)).status_code == 403
    assert client.post(
        "/clients",
        json={"name": "Forbidden", "status": "active", "default_currency": "USD"},
        headers=auth(owner_token),
    ).status_code == 403
    assert client.get(
        "/agency/overview?date_from=2026-08-01&date_to=2026-08-31",
        headers=auth(owner_token),
    ).status_code == 403
    assert client.get("/platform/agencies", headers=auth(owner_token)).status_code == 403


def test_legacy_multi_tenant_solo_session_has_no_read_scope_and_no_sync_capability():
    reset_state()
    admin = create_user("admin-multi@solo.test", "admin")
    admin_token = issue_token(admin["id"])
    first = create_client("First", admin_token)
    second = create_client("Second", admin_token)
    user = create_user("legacy-multi@solo.test", "client")
    assert assign(user["id"], first["id"]).status_code == 200
    assert assign(user["id"], second["id"]).status_code == 200

    # Simulate a pre-guard legacy row rather than using the now-protected API.
    user_id = UUID(user["id"])
    app.state.auth_store.users[user_id] = app.state.auth_store.users[user_id].model_copy(
        update={"role": "solo_client"}
    )
    token = issue_token(user["id"])

    me = client.get("/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["session"]["accessible_client_ids"] == []
    listed = client.get("/clients?status=all", headers=auth(token))
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    sync = client.post(
        "/ad-accounts/sync/run",
        json={"client_id": first["id"]},
        headers=auth(token),
    )
    assert sync.status_code == 409
    assert sync.json()["error"]["code"] == "solo_owner_scope_invalid"


def test_solo_owner_credentials_discovery_and_sync_are_owner_and_tenant_scoped():
    reset_state()
    admin, admin_token, tenant, foreign, owner, owner_token = provision_owner()
    own_account = create_account(tenant["id"], "own-sync", admin_token)
    foreign_account = create_account(foreign["id"], "foreign-sync", admin_token)

    store = app.state.integration_credential_store
    store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(tenant["id"]),
            connection_key="admin-shared",
            credentials={"access_token": "admin-secret", "marker": "admin"},
            created_by=UUID(admin["id"]),
        )
    )
    own_credential = store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(tenant["id"]),
            connection_key=f"solo:{owner['id']}:meta:owner",
            credentials={"access_token": "owner-secret", "marker": "owner"},
            created_by=UUID(owner["id"]),
        )
    )

    visible = client.get("/me/integration-connections", headers=auth(owner_token))
    assert visible.status_code == 200
    assert [row["id"] for row in visible.json()["items"]] == [str(own_credential.id)]
    hidden_archive = client.delete(
        f"/me/integration-connections/{next(row.id for row in store.list(status='all') if row.created_by == UUID(admin['id']))}",
        headers=auth(owner_token),
    )
    assert hidden_archive.status_code == 403

    seen_discovery: list[dict] = []
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda credentials=None: seen_discovery.append(dict(credentials or {}))
        or [{"external_account_id": "discovered-own", "name": "Discovered", "currency": "USD"}]
    }
    discovered = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": tenant["id"]},
        headers=auth(owner_token),
    )
    assert discovered.status_code == 200, discovered.text
    assert [row["marker"] for row in seen_discovery] == ["owner"]
    assert all(row["client_id"] == tenant["id"] for row in discovered.json()["items"])

    cross_discovery = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": foreign["id"]},
        headers=auth(owner_token),
    )
    assert cross_discovery.status_code == 403

    seen_sync: list[dict] = []

    def fetcher(_external, date_from, _date_to, credentials=None):
        seen_sync.append(dict(credentials or {}))
        return [
            {
                "date": date_from,
                "impressions": 10,
                "clicks": 2,
                "spend": "3.00",
                "conversions": "1.00",
            }
        ]

    app.state.ad_account_sync_service.provider_fetchers = {"meta": fetcher}
    missing_scope = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [own_account["id"]], "date_from": "2026-08-01", "date_to": "2026-08-01"},
        headers=auth(owner_token),
    )
    assert missing_scope.status_code == 403
    assert missing_scope.json()["error"]["code"] == "solo_owner_client_mismatch"

    synced = client.post(
        "/ad-accounts/sync/run",
        json={
            "client_id": tenant["id"],
            "account_ids": [own_account["id"]],
            "date_from": "2026-08-01",
            "date_to": "2026-08-01",
        },
        headers=auth(owner_token),
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["success"] == 1
    assert [row["marker"] for row in seen_sync] == ["owner"]

    jobs_before = client.get("/ad-accounts/sync/jobs?status=all", headers=auth(owner_token)).json()["count"]
    mixed = client.post(
        "/ad-accounts/sync/run",
        json={
            "client_id": tenant["id"],
            "account_ids": [own_account["id"], foreign_account["id"]],
            "date_from": "2026-08-01",
            "date_to": "2026-08-01",
        },
        headers=auth(owner_token),
    )
    assert mixed.status_code == 403
    jobs_after = client.get("/ad-accounts/sync/jobs?status=all", headers=auth(owner_token)).json()["count"]
    assert jobs_after == jobs_before


def test_solo_owner_can_manage_only_own_tenant_budgets():
    reset_state()
    _, admin_token, tenant, foreign, owner, owner_token = provision_owner()
    first = create_account(tenant["id"], "budget-first", admin_token)
    second = create_account(tenant["id"], "budget-second", admin_token)
    foreign_account = create_account(foreign["id"], "budget-foreign", admin_token)

    cap = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="client", amount="1000.00"),
        headers=auth(owner_token),
    )
    assert cap.status_code == 200, cap.text
    source = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="account", account_id=first["id"], amount="600.00"),
        headers=auth(owner_token),
    )
    target = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="account", account_id=second["id"], amount="100.00"),
        headers=auth(owner_token),
    )
    assert source.status_code == target.status_code == 200

    moved = client.post(
        f"/budgets/{source.json()['id']}/transfer",
        json={"target_account_id": second["id"], "amount": "50.00"},
        headers=auth(owner_token),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["source_budget"]["amount"] == "550.00"
    assert moved.json()["target_budget"]["amount"] == "150.00"

    archived = client.delete(f"/budgets/{target.json()['id']}", headers=auth(owner_token))
    assert archived.status_code == 200
    restored = client.patch(
        f"/budgets/{target.json()['id']}",
        json={"status": "active", "changed_by": owner["id"]},
        headers=auth(owner_token),
    )
    assert restored.status_code == 200, restored.text

    cross_create = client.post(
        "/budgets",
        json=budget_payload(
            foreign["id"],
            scope="account",
            account_id=foreign_account["id"],
            amount="100.00",
        ),
        headers=auth(owner_token),
    )
    assert cross_create.status_code == 403

    viewer = create_user("viewer@solo.test", "client")
    assert assign(viewer["id"], tenant["id"]).status_code == 200
    viewer_token = issue_token(viewer["id"])
    denied = client.patch(
        f"/budgets/{cap.json()['id']}",
        json={"amount": "900.00"},
        headers=auth(viewer_token),
    )
    assert denied.status_code == 403


def test_solo_integrations_overview_excludes_foreign_identity_and_credentials():
    reset_state()
    admin, admin_token, tenant, _, owner, owner_token = provision_owner()
    create_account(tenant["id"], "overview-meta", admin_token)
    store = app.state.integration_credential_store
    store.upsert(
        IntegrationCredentialCreate(
            provider="meta",
            scope_type="client",
            scope_id=UUID(tenant["id"]),
            connection_key="admin-only",
            credentials={"access_token": "admin-secret"},
            created_by=UUID(admin["id"]),
        )
    )
    response = client.get("/integrations/overview", headers=auth(owner_token))
    assert response.status_code == 200, response.text
    providers = {row["provider"]: row for row in response.json()["providers"]}
    assert "stored_credentials" not in providers["meta"]["connection_sources"]


def test_password_login_sanitizes_misprovisioned_solo_scope():
    reset_state()
    admin = create_user("admin-password@solo.test", "admin")
    admin_token = issue_token(admin["id"])
    first = create_client("Password first", admin_token)
    second = create_client("Password second", admin_token)
    user = create_user("password-owner@solo.test", "client")
    assert assign(user["id"], first["id"]).status_code == 200
    assert assign(user["id"], second["id"]).status_code == 200
    user_id = UUID(user["id"])
    app.state.auth_store.users[user_id] = app.state.auth_store.users[user_id].model_copy(
        update={"role": "solo_client"}
    )
    app.state.auth_store.set_password(user_id, "StrongPass123")

    response = client.post(
        "/auth/password/login",
        json={"email": user["email"], "password": "StrongPass123"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["session"]["accessible_client_ids"] == []
    assert app.state.ad_account_sync_service.list_jobs(status="all", limit=50) == []


def test_solo_oauth_connect_pins_client_scope_and_namespaces_connection(monkeypatch):
    reset_state()
    _, _, tenant, foreign, owner, owner_token = provision_owner()

    class FakeGoogleAdapter:
        provider = "google"

        def build_authorize_url(self, cfg, state: str) -> str:
            return f"https://provider.example/google?state={state}&client_id={cfg.client_id}"

        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="solo-google-user",
                email=owner["email"],
                email_verified=True,
                name=owner["name"],
                raw_profile={"sub": "solo-google-user"},
                oauth_tokens={"refresh_token": "solo-refresh", "access_token": "solo-access"},
            )

    app.state.oauth_adapters = {"google": FakeGoogleAdapter()}
    monkeypatch.setattr("app.main.google_ads.detect_login_customer_id", lambda _credentials: "1234567890")
    config = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "redirect_uri": "http://127.0.0.1:8000/auth/google/callback",
            "enabled": True,
        },
    )
    assert config.status_code == 200
    client.cookies.set("ops_session", owner_token)

    mismatched = client.get(
        f"/auth/google/start?intent=connect&client_id={foreign['id']}",
        follow_redirects=False,
    )
    assert mismatched.status_code == 403

    start = client.get(
        f"/auth/google/start?intent=connect&client_id={tenant['id']}&next=/integrations",
        follow_redirects=False,
    )
    assert start.status_code == 302, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        f"/auth/google/callback?code=ok-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    rows = app.state.integration_credential_store.list(status="all", provider="google")
    assert len(rows) == 1
    assert rows[0].scope_type == "client"
    assert str(rows[0].scope_id) == tenant["id"]
    assert str(rows[0].created_by) == owner["id"]
    assert rows[0].connection_key == f"solo:{owner['id']}:google:1234567890"
