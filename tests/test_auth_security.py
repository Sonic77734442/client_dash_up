from urllib.parse import parse_qs, urlparse
from dataclasses import replace

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.oauth import ExternalIdentityPayload


client = TestClient(app)


class FakeProviderAdapter:
    provider = "facebook"

    def build_authorize_url(self, cfg, state: str) -> str:
        return f"https://provider.example/auth?state={state}&client_id={cfg.client_id}"

    def fetch_identity(self, cfg, code: str) -> ExternalIdentityPayload:
        assert code == "ok-code"
        return ExternalIdentityPayload(
            provider_user_id="fb-user-security",
            email="oauth.security@example.com",
            email_verified=True,
            name="OAuth Security",
            raw_profile={"id": "fb-user-security", "email": "oauth.security@example.com"},
        )


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def test_cookie_session_requires_csrf_header_for_refresh_but_logout_is_exempt():
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

    start = client.get("/auth/facebook/start?next=/", follow_redirects=False)
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    callback = client.get(f"/auth/facebook/callback?code=ok-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    assert client.cookies.get("ops_csrf")

    denied = client.post("/auth/session/refresh")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "csrf_failed"

    ok = client.post("/auth/session/refresh", headers={"X-CSRF-Token": client.cookies.get("ops_csrf")})
    assert ok.status_code == 200

    # Logout is intentionally CSRF-exempt to support cross-domain frontend deployments.
    out = client.post("/auth/logout")
    assert out.status_code == 200


def test_rate_limit_triggers_on_auth_sensitive_routes():
    reset_state()
    status_codes = []
    # default limit is 60 requests per 60 seconds for /auth/*
    for _ in range(61):
        r = client.post("/auth/internal/sessions/validate", json={"token": "bad"})
        status_codes.append(r.status_code)

    assert status_codes[-1] == 429


def test_internal_plumbing_routes_require_admin_in_production_mode(monkeypatch):
    reset_state()
    original_settings = main_module.settings
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, app_env="production"))
    try:
        denied_unauth = client.post(
            "/auth/internal/users",
            json={"email": "prod-admin@example.com", "name": "Admin", "role": "admin", "status": "active"},
        )
        assert denied_unauth.status_code == 401

        admin = app.state.auth_store.create_user(
            main_module.UserCreate(email="prod-admin@example.com", name="Admin", role="admin", status="active")
        )
        agency = app.state.auth_store.create_user(
            main_module.UserCreate(email="prod-agency@example.com", name="Agency", role="agency", status="active")
        )
        agency_token = app.state.auth_store.issue_session(
            main_module.SessionIssueRequest(user_id=agency.id, ttl_minutes=60)
        ).token
        admin_token = app.state.auth_store.issue_session(
            main_module.SessionIssueRequest(user_id=admin.id, ttl_minutes=60)
        ).token

        denied_non_admin = client.get("/auth/internal/users", headers={"Authorization": f"Bearer {agency_token}"})
        assert denied_non_admin.status_code == 403

        ok_admin = client.get("/auth/internal/users", headers={"Authorization": f"Bearer {admin_token}"})
        assert ok_admin.status_code == 200
    finally:
        monkeypatch.setattr(main_module, "settings", original_settings)
