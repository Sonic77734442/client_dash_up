from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.main import app
from app.services.oauth import ExternalIdentityPayload


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


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


def test_oauth_start_redirects_to_provider_url_with_state():
    reset_state()
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


def test_oauth_callback_issues_internal_session_and_redirects_frontend():
    reset_state()
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
    fragment = parse_qs(parsed.fragment)
    assert fragment.get("token", [""])[0] != ""

    set_cookie = callback.headers.get("set-cookie", "")
    assert "ops_session=" in set_cookie

    # cookie-auth path should work without bearer header.
    me = client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "oauth.user@example.com"
    assert body["session"]["valid"] is True


def test_oauth_callback_rejects_invalid_state():
    reset_state()
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
    assert bad.status_code == 400
