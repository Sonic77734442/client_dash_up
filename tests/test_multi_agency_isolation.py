from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.services.oauth import ExternalIdentityPayload


client = TestClient(app)


class FakeGoogleConnectAdapter:
    provider = "google"

    def build_authorize_url(self, cfg, state: str) -> str:
        return f"https://provider.example/google-auth?state={state}&client_id={cfg.client_id}"

    def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
        assert code == "ok-code"
        return ExternalIdentityPayload(
            provider_user_id="multi-agency-google-user",
            email="multi-agency-owner@test.local",
            email_verified=True,
            name="Multi Agency Owner",
            raw_profile={"sub": "multi-agency-google-user"},
            oauth_tokens={"refresh_token": "tenant-refresh-token"},
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _reset_state() -> None:
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def _bootstrap_two_agencies(*, member_role: str = "owner") -> tuple[str, str, str, str]:
    admin = client.post(
        "/auth/internal/users",
        json={"email": "multi-admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    admin_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    ).json()["token"]
    owner = client.post(
        "/auth/internal/users",
        json={
            "email": "multi-agency-owner@test.local",
            "name": "Multi Agency Owner",
            "role": "agency",
            "status": "active",
        },
        headers=_auth(admin_token),
    )
    assert owner.status_code == 200

    agency_ids: list[str] = []
    for name in ("North Agency", "South Agency"):
        agency = client.post(
            "/platform/agencies",
            json={"name": name, "status": "active", "plan": "starter"},
            headers=_auth(admin_token),
        )
        assert agency.status_code == 200
        agency_ids.append(agency.json()["id"])
        member = client.post(
            f"/platform/agencies/{agency.json()['id']}/members",
            json={"user_id": owner.json()["id"], "role": member_role, "status": "active"},
            headers=_auth(admin_token),
        )
        assert member.status_code == 200

    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": owner.json()["id"], "ttl_minutes": 60},
        headers=_auth(admin_token),
    ).json()["token"]
    return admin_token, agency_token, agency_ids[0], agency_ids[1]


def test_multi_agency_oauth_connect_requires_selection_and_writes_one_scope(monkeypatch):
    _reset_state()
    admin_token, agency_token, first_agency_id, second_agency_id = _bootstrap_two_agencies()
    app.state.oauth_adapters = {"google": FakeGoogleConnectAdapter()}
    monkeypatch.setattr("app.main.google_ads.detect_login_customer_id", lambda _credentials: None)

    configured = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "redirect_uri": "https://dashboard.example/auth/google/callback",
            "enabled": True,
        },
        headers=_auth(admin_token),
    )
    assert configured.status_code == 200

    client.cookies.set("ops_session", agency_token)
    missing = client.get("/auth/google/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert missing.status_code == 409
    assert _error_code(missing) == "selection_required"
    assert app.state.integration_credential_store.list(status="all") == []

    start = client.get(
        f"/auth/google/start?next=/sync-monitor&intent=connect&agency_id={first_agency_id}",
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302

    credentials = app.state.integration_credential_store.list(status="all", provider="google")
    assert len(credentials) == 1
    assert str(credentials[0].scope_id) == first_agency_id
    assert str(credentials[0].scope_id) != second_agency_id


def test_multi_agency_discovery_inbox_is_created_and_bound_only_in_selected_agency():
    _reset_state()
    _admin_token, agency_token, first_agency_id, second_agency_id = _bootstrap_two_agencies()
    app.state.ad_account_discovery_service.discoverers = {
        "meta": lambda: [{"external_account_id": "meta-selected", "name": "Selected Meta", "currency": "USD"}]
    }

    missing = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta"},
        headers=_auth(agency_token),
    )
    assert missing.status_code == 409
    assert _error_code(missing) == "selection_required"

    discovered = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "agency_id": first_agency_id},
        headers=_auth(agency_token),
    )
    assert discovered.status_code == 200
    inbox_id = discovered.json()["client_id"]
    first_bindings = app.state.platform_admin_store.list_clients(UUID(first_agency_id))
    second_bindings = app.state.platform_admin_store.list_clients(UUID(second_agency_id))
    assert [str(row.client_id) for row in first_bindings] == [inbox_id]
    assert all(str(row.client_id) != inbox_id for row in second_bindings)
    inbox = app.state.client_store.get(UUID(inbox_id))
    assert inbox is not None
    assert inbox.notes == f"system:discovery_inbox:agency:{first_agency_id}"


def test_multi_agency_discovery_uses_credentials_only_from_selected_agency():
    _reset_state()
    admin_token, agency_token, first_agency_id, second_agency_id = _bootstrap_two_agencies()
    tenant = client.post(
        "/clients",
        json={"name": "Shared tenant", "status": "active", "default_currency": "USD"},
        headers=_auth(admin_token),
    )
    assert tenant.status_code == 200
    for agency_id in (first_agency_id, second_agency_id):
        bound = client.post(
            f"/platform/agencies/{agency_id}/clients",
            json={"client_id": tenant.json()["id"]},
            headers=_auth(admin_token),
        )
        assert bound.status_code == 200
    for agency_id, token in ((first_agency_id, "first-token"), (second_agency_id, "second-token")):
        credential = client.post(
            "/platform/integration-credentials",
            json={
                "provider": "meta",
                "scope_type": "agency",
                "scope_id": agency_id,
                "connection_key": f"meta:{agency_id}",
                "credentials": {"access_token": token},
            },
            headers=_auth(admin_token),
        )
        assert credential.status_code == 200

    observed_tokens: list[str] = []

    def discover_meta(credentials):
        observed_tokens.append(str(credentials.get("access_token")))
        return []

    app.state.ad_account_discovery_service.discoverers = {"meta": discover_meta}
    discovered = client.post(
        "/ad-accounts/discover",
        json={
            "provider": "meta",
            "client_id": tenant.json()["id"],
            "agency_id": first_agency_id,
        },
        headers=_auth(agency_token),
    )
    assert discovered.status_code == 200
    assert observed_tokens == ["first-token"]


def test_multi_agency_client_creation_requires_selection_and_assigns_only_selected_agency():
    _reset_state()
    _admin_token, agency_token, first_agency_id, second_agency_id = _bootstrap_two_agencies()
    payload = {"name": "Selected tenant", "status": "active", "default_currency": "USD"}

    missing = client.post("/clients", json=payload, headers=_auth(agency_token))
    assert missing.status_code == 409
    assert _error_code(missing) == "selection_required"
    assert app.state.client_store.list(status="all") == []

    created = client.post(
        f"/clients?agency_id={second_agency_id}",
        json=payload,
        headers=_auth(agency_token),
    )
    assert created.status_code == 200
    created_id = created.json()["id"]
    first_bindings = app.state.platform_admin_store.list_clients(UUID(first_agency_id))
    second_bindings = app.state.platform_admin_store.list_clients(UUID(second_agency_id))
    assert all(str(row.client_id) != created_id for row in first_bindings)
    assert [str(row.client_id) for row in second_bindings] == [created_id]


def test_agency_member_without_manage_role_cannot_create_client():
    _reset_state()
    _admin_token, agency_token, first_agency_id, _second_agency_id = _bootstrap_two_agencies(member_role="member")
    denied = client.post(
        f"/clients?agency_id={first_agency_id}",
        json={"name": "Forbidden tenant", "status": "active", "default_currency": "USD"},
        headers=_auth(agency_token),
    )
    assert denied.status_code == 403
    assert _error_code(denied) == "agency_manage_forbidden"
    assert app.state.client_store.list(status="all") == []
