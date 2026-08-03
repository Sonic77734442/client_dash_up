from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.services.oauth as oauth_module
from app.main import _oauth_provider_config_or_400, app
from app.schemas import AuthIdentityLink, ClientCreate, UserCreate
from app.services.oauth import (
    ExternalIdentityPayload,
    FacebookOAuthAdapter,
    GoogleOAuthAdapter,
    OAuthProviderConfig,
)


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def set_facebook_auth_env(monkeypatch):
    monkeypatch.setenv("FACEBOOK_AUTH_CLIENT_ID", "facebook-auth-client")
    monkeypatch.setenv("FACEBOOK_AUTH_CLIENT_SECRET", "facebook-auth-secret")
    monkeypatch.setenv(
        "FACEBOOK_AUTH_REDIRECT_URI",
        "https://dashboard.example/api/backend/auth/facebook/callback",
    )


def set_facebook_connect_env(monkeypatch):
    monkeypatch.setenv("FACEBOOK_CLIENT_ID", "facebook-business-client")
    monkeypatch.setenv("FACEBOOK_CLIENT_SECRET", "facebook-business-secret")
    monkeypatch.setenv(
        "FACEBOOK_REDIRECT_URI",
        "https://dashboard.example/api/backend/auth/facebook/callback",
    )
    monkeypatch.setenv("FACEBOOK_LOGIN_CONFIG_ID", "business-config-id")


class FakeProviderAdapter:
    provider = "facebook"

    def build_authorize_url(self, cfg, state: str) -> str:
        return f"https://provider.example/auth?state={state}&client_id={cfg.client_id}"

    def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
        assert code == "ok-code"
        return ExternalIdentityPayload(
            provider_user_id="fb-user-123",
            email="oauth.user@example.com",
            email_verified=True,
            name="OAuth User",
            raw_profile={"id": "fb-user-123", "email": "oauth.user@example.com"},
        )


class FakeGoogleAdapter:
    provider = "google"

    def build_authorize_url(self, cfg, state: str) -> str:
        return f"https://provider.example/google-auth?state={state}&client_id={cfg.client_id}"

    def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
        assert code == "ok-code"
        return ExternalIdentityPayload(
            provider_user_id="google-user-123",
            email="agency.user@example.com",
            email_verified=True,
            name="Agency User",
            raw_profile={"sub": "google-user-123", "email": "agency.user@example.com"},
            oauth_tokens={"refresh_token": "rt-agency-123", "access_token": "at-agency-123"},
        )


def test_oauth_start_redirects_to_provider_url_with_state(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}

    cfg = client.post(
        "/auth/provider-configs",
        json={
            "provider": "facebook",
            "client_id": "fb-client",
            "client_secret": "fb-secret",
            "redirect_uri": "http://127.0.0.1:8000/auth/facebook/callback",
            "enabled": True,
        },
    )
    assert cfg.status_code == 200

    res = client.get("/auth/facebook/start?next=/platform/agencies", follow_redirects=False)
    assert res.status_code == 302
    location = res.headers.get("location")
    assert location and location.startswith("https://provider.example/auth?")
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    assert qs.get("state")


def test_oauth_redirect_uri_environment_override(monkeypatch):
    reset_state()
    cfg = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "google-client",
            "client_secret": "google-secret",
            "redirect_uri": "https://api.example/auth/google/callback",
            "enabled": True,
        },
    )
    assert cfg.status_code == 200
    expected = "https://dashboard.example/api/backend/auth/google/callback"
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", expected)
    resolved = _oauth_provider_config_or_400("google")
    assert resolved.redirect_uri == expected


def test_facebook_oauth_uses_separate_environment_credentials(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    set_facebook_connect_env(monkeypatch)

    login = _oauth_provider_config_or_400("facebook", intent="login")
    connect = _oauth_provider_config_or_400("facebook", intent="connect")

    assert login.client_id == "facebook-auth-client"
    assert login.client_secret == "facebook-auth-secret"
    assert login.redirect_uri == "https://dashboard.example/api/backend/auth/facebook/callback"
    assert login.config_id is None
    assert login.intent == "login"

    assert connect.client_id == "facebook-business-client"
    assert connect.client_secret == "facebook-business-secret"
    assert connect.redirect_uri == "https://dashboard.example/api/backend/auth/facebook/callback"
    assert connect.config_id == "business-config-id"
    assert connect.intent == "connect"


def test_facebook_login_requires_separate_auth_environment(monkeypatch):
    reset_state()
    for key in (
        "FACEBOOK_AUTH_CLIENT_ID",
        "FACEBOOK_AUTH_CLIENT_SECRET",
        "FACEBOOK_AUTH_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        _oauth_provider_config_or_400("facebook", intent="login")

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "facebook_auth_not_configured"


def test_oauth_callback_issues_internal_session_and_redirects_frontend(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}

    cfg = client.post(
        "/auth/provider-configs",
        json={
            "provider": "facebook",
            "client_id": "fb-client",
            "client_secret": "fb-secret",
            "redirect_uri": "http://127.0.0.1:8000/auth/facebook/callback",
            "enabled": True,
        },
    )
    assert cfg.status_code == 200
    approved_user = app.state.auth_store.create_user(
        UserCreate(
            email="oauth.user@example.com",
            name="Approved OAuth User",
            role="client",
            status="active",
        )
    )
    app.state.auth_store.link_identity(
        AuthIdentityLink(
            user_id=approved_user.id,
            provider="facebook",
            provider_user_id="facebook-auth-client:fb-user-123",
            email="oauth.user@example.com",
            email_verified=True,
            raw_profile={"id": "fb-user-123", "email": "oauth.user@example.com"},
        )
    )

    start = client.get("/auth/facebook/start?next=/platform/agencies", follow_redirects=False)
    assert start.status_code == 302
    start_loc = start.headers["location"]
    state = parse_qs(urlparse(start_loc).query)["state"][0]

    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    cb_loc = callback.headers["location"]
    assert cb_loc.startswith("http://localhost:3000/login/success?")
    parsed = urlparse(cb_loc)
    params = parse_qs(parsed.query)
    next_path = params.get("next", [""])[0]
    assert next_path == "/platform/agencies"
    assert parsed.fragment == ""

    set_cookie = callback.headers.get("set-cookie", "")
    assert "ops_session=" in set_cookie

    # cookie-auth path should work without bearer header.
    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["id"] == str(approved_user.id)
    assert body["user"]["email"] == "oauth.user@example.com"
    assert body["session"]["valid"] is True


def test_oauth_callback_rejects_invalid_state(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}

    cfg = client.post(
        "/auth/provider-configs",
        json={
            "provider": "facebook",
            "client_id": "fb-client",
            "client_secret": "fb-secret",
            "redirect_uri": "http://127.0.0.1:8000/auth/facebook/callback",
            "enabled": True,
        },
    )
    assert cfg.status_code == 200

    # prime nonce cookie via start
    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    assert start.status_code == 302

    bad = client.get("/auth/facebook/callback?code=ok-code&state=bad", follow_redirects=False)
    assert bad.status_code == 302
    assert parse_qs(urlparse(bad.headers["location"]).query)["oauth_error"] == ["provider_error"]


def test_oauth_connect_flow_keeps_current_agency_user_and_saves_agency_credentials():
    reset_state()
    app.state.oauth_adapters = {"google": FakeGoogleAdapter()}

    # Admin bootstrap
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    admin_token = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60}).json()["token"]

    # Agency user + agency membership
    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency.user@example.com", "name": "Agency User", "role": "agency", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_user.status_code == 200
    agency = client.post(
        "/platform/agencies",
        json={"name": "Agency One", "status": "active", "plan": "starter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency.status_code == 200
    member = client.post(
        f"/platform/agencies/{agency.json()['id']}/members",
        json={"user_id": agency_user.json()["id"], "role": "owner", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert member.status_code == 200

    # OAuth provider config for Google
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

    # Existing agency session (connect mode)
    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": agency_user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]
    client.cookies.set("ops_session", agency_token)

    start = client.get("/auth/google/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    callback = client.get(f"/auth/google/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302

    # Connect authorization is integration-only and must not create an auth identity.
    identities = app.state.auth_store.list_identities()
    google_identity = next((x for x in identities if x.provider == "google" and x.provider_user_id == "google-user-123"), None)
    assert google_identity is None

    # Credentials must be auto-saved in agency scope.
    creds = app.state.integration_credential_store.list(status="all", provider="google")
    assert len(creds) == 1
    assert creds[0].scope_type == "agency"
    assert str(creds[0].scope_id) == agency.json()["id"]
    assert creds[0].connection_key == "google:google-user-123"
    assert creds[0].credentials.get("refresh_token") == "rt-agency-123"


def test_oauth_connect_flow_adds_second_google_connection_for_same_agency():
    reset_state()

    class FakeGoogleAdapterMulti:
        provider = "google"

        def build_authorize_url(self, cfg, state: str) -> str:
            return f"https://provider.example/google-auth?state={state}&client_id={cfg.client_id}"

        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            if code == "ok-code-1":
                return ExternalIdentityPayload(
                    provider_user_id="google-user-123",
                    email="agency.user@example.com",
                    email_verified=True,
                    name="Agency User",
                    raw_profile={"sub": "google-user-123", "email": "agency.user@example.com"},
                    oauth_tokens={"refresh_token": "rt-agency-123", "access_token": "at-agency-123"},
                )
            if code == "ok-code-2":
                return ExternalIdentityPayload(
                    provider_user_id="google-user-456",
                    email="agency.user@example.com",
                    email_verified=True,
                    name="Agency User",
                    raw_profile={"sub": "google-user-456", "email": "agency.user@example.com"},
                    oauth_tokens={"refresh_token": "rt-agency-456", "access_token": "at-agency-456"},
                )
            raise AssertionError("Unexpected code")

    app.state.oauth_adapters = {"google": FakeGoogleAdapterMulti()}

    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin2@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    admin_token = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60}).json()["token"]

    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency.user@example.com", "name": "Agency User", "role": "agency", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_user.status_code == 200
    agency = client.post(
        "/platform/agencies",
        json={"name": "Agency Multi", "status": "active", "plan": "starter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency.status_code == 200
    member = client.post(
        f"/platform/agencies/{agency.json()['id']}/members",
        json={"user_id": agency_user.json()["id"], "role": "owner", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert member.status_code == 200

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

    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": agency_user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]
    client.cookies.set("ops_session", agency_token)

    start1 = client.get("/auth/google/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert start1.status_code == 302
    state1 = parse_qs(urlparse(start1.headers["location"]).query)["state"][0]
    callback1 = client.get(f"/auth/google/callback?code=ok-code-1&state={state1}", follow_redirects=False)
    assert callback1.status_code == 302

    start2 = client.get("/auth/google/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert start2.status_code == 302
    state2 = parse_qs(urlparse(start2.headers["location"]).query)["state"][0]
    callback2 = client.get(f"/auth/google/callback?code=ok-code-2&state={state2}", follow_redirects=False)
    assert callback2.status_code == 302

    creds = app.state.integration_credential_store.list(status="all", provider="google")
    agency_creds = [x for x in creds if x.scope_type == "agency" and str(x.scope_id) == agency.json()["id"]]
    assert len(agency_creds) == 2
    keys = {x.connection_key for x in agency_creds}
    assert keys == {"google:google-user-123", "google:google-user-456"}


def test_oauth_connect_flow_overwrite_mode_uses_explicit_connection_key():
    reset_state()

    class FakeGoogleAdapterOverwrite:
        provider = "google"

        def build_authorize_url(self, cfg, state: str) -> str:
            return f"https://provider.example/google-auth?state={state}&client_id={cfg.client_id}"

        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="google-user-new",
                email="agency.user@example.com",
                email_verified=True,
                name="Agency User",
                raw_profile={"sub": "google-user-new", "email": "agency.user@example.com"},
                oauth_tokens={"refresh_token": "rt-overwrite", "access_token": "at-overwrite"},
            )

    app.state.oauth_adapters = {"google": FakeGoogleAdapterOverwrite()}

    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin3@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    admin_token = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60}).json()["token"]

    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency.user@example.com", "name": "Agency User", "role": "agency", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_user.status_code == 200
    agency = client.post(
        "/platform/agencies",
        json={"name": "Agency Overwrite", "status": "active", "plan": "starter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency.status_code == 200
    member = client.post(
        f"/platform/agencies/{agency.json()['id']}/members",
        json={"user_id": agency_user.json()["id"], "role": "owner", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert member.status_code == 200

    # Seed existing credential that should be overwritten by explicit key.
    seeded = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "agency",
            "scope_id": agency.json()["id"],
            "connection_key": "google:mcc-777",
            "credentials": {"refresh_token": "old-token"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert seeded.status_code == 200

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

    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": agency_user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]
    client.cookies.set("ops_session", agency_token)

    start = client.get(
        "/auth/google/start?next=/sync-monitor&intent=connect&connect_mode=overwrite&connection_key=google:mcc-777",
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302

    creds = app.state.integration_credential_store.list(status="all", provider="google")
    agency_creds = [x for x in creds if x.scope_type == "agency" and str(x.scope_id) == agency.json()["id"]]
    assert len(agency_creds) == 1
    assert agency_creds[0].connection_key == "google:mcc-777"
    assert agency_creds[0].credentials.get("refresh_token") == "rt-overwrite"

    cb_loc = callback.headers["location"]
    parsed = urlparse(cb_loc)
    next_path = parse_qs(parsed.query).get("next", [""])[0]
    assert next_path == "/sync-monitor"


def test_oauth_provider_scopes_depend_on_login_or_connect_intent():
    base = {
        "provider": "google",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "redirect_uri": "https://example.test/callback",
        "enabled": True,
    }

    google_login = parse_qs(
        urlparse(GoogleOAuthAdapter().build_authorize_url(OAuthProviderConfig(**base, intent="login"), "state")).query
    )
    google_connect = parse_qs(
        urlparse(GoogleOAuthAdapter().build_authorize_url(OAuthProviderConfig(**base, intent="connect"), "state")).query
    )
    assert "adwords" not in google_login["scope"][0]
    assert "adwords" in google_connect["scope"][0]
    assert "access_type" not in google_login
    assert google_connect["access_type"] == ["offline"]

    facebook_base = {**base, "provider": "facebook", "config_id": "business-config-id"}
    facebook_login_url = FacebookOAuthAdapter().build_authorize_url(
        OAuthProviderConfig(**facebook_base, intent="login"),
        "state",
    )
    facebook_login = parse_qs(urlparse(facebook_login_url).query)
    assert urlparse(facebook_login_url).path == "/v26.0/dialog/oauth"
    assert set(facebook_login["scope"][0].split(",")) == {"public_profile", "email"}
    assert facebook_login["response_type"] == ["code"]
    assert "config_id" not in facebook_login
    assert "override_default_response_type" not in facebook_login

    facebook_connect_url = FacebookOAuthAdapter().build_authorize_url(
        OAuthProviderConfig(**facebook_base, intent="connect"),
        "state",
    )
    facebook_connect = parse_qs(urlparse(facebook_connect_url).query)
    assert urlparse(facebook_connect_url).path == "/v26.0/dialog/oauth"
    assert facebook_connect["config_id"] == ["business-config-id"]
    assert facebook_connect["response_type"] == ["code"]
    assert facebook_connect["override_default_response_type"] == ["true"]
    assert "scope" not in facebook_connect

    with pytest.raises(HTTPException) as missing_config_error:
        FacebookOAuthAdapter().build_authorize_url(
            OAuthProviderConfig(**{**facebook_base, "config_id": None}, intent="connect"),
            "state",
        )
    assert missing_config_error.value.detail["code"] == "facebook_business_config_required"


def test_facebook_login_fetches_email_profile_field(monkeypatch):
    calls = []

    class StubResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class StubClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get(self, url, params, headers=None):
            calls.append((url, params, headers))
            if url.endswith("/oauth/access_token"):
                return StubResponse({"access_token": "token", "token_type": "bearer"})
            return StubResponse({"id": "fb-user", "name": "FB User", "email": "fb@example.com"})

    monkeypatch.setattr(oauth_module.httpx, "Client", lambda timeout: StubClient())
    cfg = OAuthProviderConfig(
        provider="facebook",
        client_id="facebook-auth-client",
        client_secret="facebook-auth-secret",
        redirect_uri="https://dashboard.example/api/backend/auth/facebook/callback",
        enabled=True,
        intent="login",
    )

    identity = FacebookOAuthAdapter().fetch_identity(cfg, "code")

    assert calls[0][1]["client_id"] == "facebook-auth-client"
    assert calls[0][1]["client_secret"] == "facebook-auth-secret"
    assert calls[1][1]["fields"] == "id,name,email"
    assert calls[1][2] == {"Authorization": "Bearer token"}
    assert "access_token" not in calls[1][1]
    assert identity.email == "fb@example.com"


def test_facebook_connect_callback_reuses_business_credentials_from_state(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    set_facebook_connect_env(monkeypatch)
    seen = []

    class TrackingFacebookAdapter(FakeProviderAdapter):
        def build_authorize_url(self, cfg, state: str) -> str:
            seen.append(("start", cfg.intent, cfg.client_id, cfg.client_secret, cfg.config_id))
            return f"https://provider.example/auth?state={state}&client_id={cfg.client_id}"

        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            seen.append(("callback", cfg.intent, cfg.client_id, cfg.client_secret, cfg.config_id))
            return super().fetch_identity(cfg, code)

    app.state.oauth_adapters = {"facebook": TrackingFacebookAdapter()}
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@facebook.test", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    session = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    )
    assert session.status_code == 200
    client.cookies.set("ops_session", session.json()["token"])

    start = client.get("/auth/facebook/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302

    expected = ("connect", "facebook-business-client", "facebook-business-secret", "business-config-id")
    assert seen[0][1:] == expected
    assert seen[1][1:] == expected
    assert app.state.auth_store.list_identities() == []
    assert client.cookies.get("ops_session") == session.json()["token"]
    assert "ops_session=" not in callback.headers.get("set-cookie", "")


def test_facebook_login_auto_provisions_isolated_client_workspace(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}
    foreign_client = app.state.client_store.create(
        ClientCreate(name="Foreign tenant", status="active", default_currency="USD")
    )

    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state}", follow_redirects=False)

    assert callback.status_code == 302
    assert urlparse(callback.headers["location"]).path == "/login/success"
    assert "ops_session=" in callback.headers.get("set-cookie", "")

    me = client.get("/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["user"]["role"] == "client"
    assert me_body["user"]["status"] == "active"
    assert me_body["session"]["global_access"] is False
    assert me_body["session"]["access_scope"] == "assigned"
    assert len(me_body["session"]["accessible_client_ids"]) == 1
    own_client_id = me_body["session"]["accessible_client_ids"][0]
    assert own_client_id != str(foreign_client.id)

    access_rows = app.state.auth_store.list_client_access(user_id=UUID(me_body["user"]["id"]))
    assert len(access_rows) == 1
    assert str(access_rows[0].client_id) == own_client_id
    assert access_rows[0].role == "client"
    identities = app.state.auth_store.list_identities(user_id=UUID(me_body["user"]["id"]))
    assert len(identities) == 1
    assert identities[0].provider == "facebook"
    assert identities[0].provider_user_id == "facebook-auth-client:fb-user-123"

    visible_clients = client.get("/clients")
    assert visible_clients.status_code == 200
    assert visible_clients.json()["count"] == 1
    assert visible_clients.json()["items"][0]["id"] == own_client_id
    assert client.get(f"/clients/{foreign_client.id}").status_code == 403

    accounts = client.get(f"/ad-accounts?client_id={own_client_id}")
    assert accounts.status_code == 200
    assert accounts.json()["count"] == 0
    budgets = client.get(f"/budgets?client_id={own_client_id}")
    assert budgets.status_code == 200
    assert budgets.json()["count"] == 0

    csrf = client.cookies.get("ops_csrf")
    create_another = client.post(
        "/clients",
        json={"name": "Escalation attempt"},
        headers={"X-CSRF-Token": csrf},
    )
    assert create_another.status_code == 403
    assert create_another.json()["error"]["code"] == "forbidden"

    users_before = len(app.state.auth_store.list_users())
    clients_before = len(app.state.client_store.list(status="all"))
    access_before = len(app.state.auth_store.list_client_access())
    repeat_start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    repeat_state = parse_qs(urlparse(repeat_start.headers["location"]).query)["state"][0]
    repeat_callback = client.get(
        f"/auth/facebook/callback?code=ok-code&state={repeat_state}",
        follow_redirects=False,
    )
    assert repeat_callback.status_code == 302
    assert urlparse(repeat_callback.headers["location"]).path == "/login/success"
    assert len(app.state.auth_store.list_users()) == users_before
    assert len(app.state.client_store.list(status="all")) == clients_before
    assert len(app.state.auth_store.list_client_access()) == access_before


def test_facebook_login_rejects_linked_inactive_user_without_issuing_session(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}
    pending_user = app.state.auth_store.create_user(
        UserCreate(
            email="oauth.user@example.com",
            name="Pending OAuth User",
            role="client",
            status="inactive",
        )
    )
    app.state.auth_store.link_identity(
        AuthIdentityLink(
            user_id=pending_user.id,
            provider="facebook",
            provider_user_id="facebook-auth-client:fb-user-123",
            email="oauth.user@example.com",
            email_verified=True,
            raw_profile={"id": "fb-user-123", "email": "oauth.user@example.com"},
        )
    )

    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state}", follow_redirects=False)

    assert callback.status_code == 302
    assert parse_qs(urlparse(callback.headers["location"]).query)["oauth_error"] == ["access_pending"]
    identities = app.state.auth_store.list_identities()
    assert len(identities) == 1
    assert identities[0].user_id == pending_user.id
    assert "ops_session=" not in callback.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("admin", "active"),
        ("admin", "inactive"),
        ("agency", "active"),
        ("agency", "inactive"),
        ("client", "active"),
        ("client", "inactive"),
    ],
)
def test_facebook_unlinked_email_collision_requires_explicit_account_link(monkeypatch, role, status):
    reset_state()
    set_facebook_auth_env(monkeypatch)
    app.state.oauth_adapters = {"facebook": FakeProviderAdapter()}
    existing_user = app.state.auth_store.create_user(
        UserCreate(
            email="oauth.user@example.com",
            name="Existing User",
            role=role,
            status=status,
        )
    )

    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state_value = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state_value}", follow_redirects=False)

    assert callback.status_code == 302
    assert parse_qs(urlparse(callback.headers["location"]).query)["oauth_error"] == ["account_link_required"]
    assert app.state.auth_store.get_user(existing_user.id).role == role
    assert app.state.auth_store.get_user(existing_user.id).status == status
    assert app.state.auth_store.list_identities(user_id=existing_user.id) == []
    assert app.state.auth_store.list_client_access(user_id=existing_user.id) == []
    assert app.state.client_store.list(status="all") == []
    assert "ops_session=" not in callback.headers.get("set-cookie", "")


def test_facebook_login_without_email_still_gets_isolated_client_workspace(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)

    class FacebookWithoutEmailAdapter(FakeProviderAdapter):
        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="fb-user-without-email",
                email=None,
                email_verified=None,
                name="No Email User",
                raw_profile={"id": "fb-user-without-email", "name": "No Email User"},
            )

    app.state.oauth_adapters = {"facebook": FacebookWithoutEmailAdapter()}
    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state_value = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state_value}", follow_redirects=False)

    assert callback.status_code == 302
    assert urlparse(callback.headers["location"]).path == "/login/success"
    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] is None
    assert body["user"]["role"] == "client"
    assert body["session"]["global_access"] is False
    assert len(body["session"]["accessible_client_ids"]) == 1


def test_facebook_unverified_email_is_contact_only_not_canonical_user_email(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)

    class FacebookUnverifiedEmailAdapter(FakeProviderAdapter):
        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="fb-user-unverified-email",
                email="  Pending.Contact@Example.COM  ",
                email_verified=None,
                name="Pending Contact",
                raw_profile={"id": "fb-user-unverified-email", "email": "  Pending.Contact@Example.COM  "},
            )

    app.state.oauth_adapters = {"facebook": FacebookUnverifiedEmailAdapter()}
    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state_value = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state_value}", follow_redirects=False)

    assert callback.status_code == 302
    me = client.get("/auth/me").json()
    assert me["user"]["email"] is None
    identities = app.state.auth_store.list_identities(user_id=UUID(me["user"]["id"]))
    assert len(identities) == 1
    assert identities[0].email == "pending.contact@example.com"
    assert identities[0].email_verified is None


def test_facebook_email_collision_normalizes_case_and_whitespace(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)

    class FacebookSpacedEmailAdapter(FakeProviderAdapter):
        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="fb-user-spaced-email",
                email="  existing.user@example.com  ",
                email_verified=None,
                name="Collision User",
                raw_profile={"id": "fb-user-spaced-email"},
            )

    app.state.oauth_adapters = {"facebook": FacebookSpacedEmailAdapter()}
    existing = app.state.auth_store.create_user(
        UserCreate(
            email=" Existing.User@Example.COM ",
            name="Existing User",
            role="client",
            status="active",
        )
    )
    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state_value = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state_value}", follow_redirects=False)

    assert callback.status_code == 302
    assert parse_qs(urlparse(callback.headers["location"]).query)["oauth_error"] == ["account_link_required"]
    assert app.state.auth_store.list_identities(user_id=existing.id) == []
    assert app.state.client_store.list(status="all") == []


def test_facebook_linked_subject_ignores_changed_profile_email(monkeypatch):
    reset_state()
    set_facebook_auth_env(monkeypatch)

    class FacebookChangedEmailAdapter(FakeProviderAdapter):
        def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
            assert code == "ok-code"
            return ExternalIdentityPayload(
                provider_user_id="fb-user-123",
                email="changed.profile@example.com",
                email_verified=True,
                name="Changed Profile",
                raw_profile={"id": "fb-user-123", "email": "changed.profile@example.com"},
            )

    app.state.oauth_adapters = {"facebook": FacebookChangedEmailAdapter()}
    linked_user = app.state.auth_store.create_user(
        UserCreate(
            email="original.user@example.com",
            name="Original User",
            role="client",
            status="active",
        )
    )
    app.state.auth_store.link_identity(
        AuthIdentityLink(
            user_id=linked_user.id,
            provider="facebook",
            provider_user_id="facebook-auth-client:fb-user-123",
            email="original.user@example.com",
            email_verified=True,
            raw_profile={"id": "fb-user-123", "email": "original.user@example.com"},
        )
    )

    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    state_value = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state_value}", follow_redirects=False)

    assert callback.status_code == 302
    me = client.get("/auth/me").json()
    assert me["user"]["id"] == str(linked_user.id)
    assert me["user"]["email"] == "original.user@example.com"
    assert len(app.state.auth_store.list_users()) == 1
    assert app.state.client_store.list(status="all") == []
    identities = app.state.auth_store.list_identities(user_id=linked_user.id)
    assert len(identities) == 1
    assert identities[0].email == "original.user@example.com"


def test_oauth_login_does_not_create_integration_credentials_even_when_provider_returns_tokens():
    reset_state()
    app.state.oauth_adapters = {"google": FakeGoogleAdapter()}
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

    start = client.get("/auth/google/start?next=/&intent=login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    assert app.state.integration_credential_store.list(status="all") == []


def test_oauth_connect_requires_an_existing_session_and_provider_denial_redirects_safely():
    reset_state()
    app.state.oauth_adapters = {"google": FakeGoogleAdapter()}
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

    start = client.get("/auth/google/start?next=/sync-monitor&intent=connect", follow_redirects=False)
    assert start.status_code == 401
    assert start.json()["error"]["code"] == "session_required"

    login_start = client.get("/auth/google/start?next=/&intent=login", follow_redirects=False)
    state = parse_qs(urlparse(login_start.headers["location"]).query)["state"][0]
    denied = client.get(f"/auth/google/callback?error=access_denied&state={state}", follow_redirects=False)
    assert denied.status_code == 302
    assert parse_qs(urlparse(denied.headers["location"]).query)["oauth_error"] == ["access_denied"]

    missing_state = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert missing_state.status_code == 302
    assert parse_qs(urlparse(missing_state.headers["location"]).query)["oauth_error"] == ["provider_error"]
