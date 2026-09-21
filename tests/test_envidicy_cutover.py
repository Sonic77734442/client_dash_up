"""Production-forced/local-opt-in policy; isolated stores and no network calls."""
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
import pytest

from app.schemas import SessionIssueRequest
from app.services.auth_cutover import auto_login_enabled, id_only_enabled, redirect_legacy_credentials
from test_envidicy_routes import api, csrf, login  # shared isolated API fixture


@pytest.mark.parametrize("value", ["false", "0", "off", "no", " FALSE "])
def test_explicit_false_policy(value):
    assert id_only_enabled({"ENVIDICY_ID_ONLY_ENABLED": value}) is False
    assert id_only_enabled({}) is False


@pytest.mark.parametrize("value", ["true", "1", "on", "yes", " TRUE "])
def test_explicit_true_policy(value):
    assert id_only_enabled({"ENVIDICY_ID_ONLY_ENABLED": value}) is True


@pytest.mark.parametrize("value", ["", "treu", "enabled", "2"])
def test_invalid_policy_fails_closed(value):
    with pytest.raises(HTTPException) as error:
        id_only_enabled({"ENVIDICY_ID_ONLY_ENABLED": value})
    assert error.value.status_code == 503


@pytest.mark.parametrize("app_env", ["prod", "production", " Production "])
@pytest.mark.parametrize("switch", [None, "false", "0", "off", "no", "true"])
def test_production_forces_id_even_with_a_historical_false_switch(app_env, switch):
    environment = {"APP_ENV": app_env}
    if switch is not None:
        environment["ENVIDICY_ID_ONLY_ENABLED"] = switch
    assert id_only_enabled(environment) is True


@pytest.mark.parametrize("value", ["", "treu", "enabled", "2"])
def test_production_does_not_mask_a_malformed_switch(value):
    with pytest.raises(HTTPException) as error:
        id_only_enabled({"APP_ENV": "production", "ENVIDICY_ID_ONLY_ENABLED": value})
    assert error.value.status_code == 503


@pytest.mark.parametrize("app_env", ["development", "test", "staging", ""])
def test_nonproduction_retains_default_off_and_explicit_opt_in(app_env):
    assert id_only_enabled({"APP_ENV": app_env}) is False
    assert id_only_enabled({"APP_ENV": app_env, "ENVIDICY_ID_ONLY_ENABLED": "false"}) is False
    assert id_only_enabled({"APP_ENV": app_env, "ENVIDICY_ID_ONLY_ENABLED": "true"}) is True


@pytest.mark.parametrize("value,expected", [("false", False), ("0", False), ("off", False), ("no", False),
                                           ("true", True), ("1", True), ("on", True), ("yes", True), (" TRUE ", True)])
def test_auto_login_policy_is_explicit_and_default_off(value, expected):
    assert auto_login_enabled({}) is False
    assert auto_login_enabled({"ENVIDICY_ID_AUTO_LOGIN_ENABLED": value}) is expected


@pytest.mark.parametrize("value", ["", "treu", "enabled", "2"])
def test_invalid_auto_login_policy_fails_closed(value):
    with pytest.raises(HTTPException) as error:
        auto_login_enabled({"ENVIDICY_ID_AUTO_LOGIN_ENABLED": value})
    assert error.value.status_code == 503


def local_token(api):
    return api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60)).token


def only_id(api):
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "true")


def production_id(api):
    api.monkeypatch.setenv("APP_ENV", "production")
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "false")
    api.monkeypatch.setattr(api.main, "settings", replace(api.main.settings, app_env="production"))


def test_production_readiness_rejects_legacy_even_when_old_switch_is_false(api):
    token = local_token(api)
    production_id(api)
    api.monkeypatch.setenv("ENVIDICY_ID_AUTO_LOGIN_ENABLED", "false")
    public = api.browser.get("/auth/envidicy/config")
    assert public.status_code == 200
    assert public.json()["enabled"] is True
    assert public.json()["local_auth_enabled"] is False
    assert public.json()["auto_login"] is True
    assert public.json()["server_entry_ready"] is True
    assert api.browser.post("/auth/password/login", json={}).status_code == 303
    assert api.browser.post("/auth/invites/accept", json={}).status_code == 303
    assert api.browser.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert api.browser.post("/auth/internal/sessions/issue", json={"user_id": str(api.legacy.id)}).status_code == 410
    assert api.auth.get_user(api.legacy.id).role == "admin"
    assert api.auth.validate_session(token).valid
    assert not api.exchange_calls and not api.authority_calls


@pytest.mark.parametrize("path,body", [
    ("/auth/password/login", {"email": "legacy@example.test", "password": "test-local-password-123!"}),
    ("/auth/invites/accept", {"token": "not-an-invite", "name": "Test person"}),
])
def test_local_login_and_onboarding_are_closed(api, path, body):
    only_id(api)
    result = api.browser.post(path, json=body)
    assert result.status_code == 303, result.text
    assert result.headers["location"] == "/api/backend/auth/envidicy/start?next=%2Fportal"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in result.headers
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)


@pytest.mark.parametrize("path", ["/auth/password/login", "/auth/invites/accept"])
@pytest.mark.parametrize("suffix", ["", "/", "///"])
@pytest.mark.parametrize("content_type,body", [
    ("application/json", b'{"password":"discard-this-password",bad-json'),
    ("application/x-www-form-urlencoded", b"password=discard-this-password&token=discard-this-invite"),
])
def test_closed_credentials_redirect_before_body_validation_or_authentication(api, path, suffix, content_type, body):
    only_id(api)
    def unexpected(*_args, **_kwargs):
        raise AssertionError("Closed credentials must not reach stores or session dependencies")
    api.monkeypatch.setattr(api.auth, "authenticate_password", unexpected)
    api.monkeypatch.setattr(api.auth, "issue_session", unexpected)
    api.monkeypatch.setattr(api.main._auth_facade(), "get_session_context", unexpected)
    api.monkeypatch.setattr(api.main._platform_admin_store(), "accept_invite", unexpected)
    api.monkeypatch.setattr(api.main, "_accept_client_invite", unexpected)
    api.browser.cookies.set(api.main.settings.auth_cookie_name, "stale-cookie", domain="dash.envidicy.kz", path="/")
    response = api.browser.post(
        path + suffix + "?password=discard-query-password&next=https://external.example.test&state=discard-state",
        content=body, headers={"Content-Type": content_type},
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/api/backend/auth/envidicy/start?next=%2Fportal"
    assert "discard" not in response.headers["location"]
    assert "set-cookie" not in response.headers
    assert api.browser.cookies.get(api.main.settings.auth_cookie_name) == "stale-cookie"
    assert not api.exchange_calls and not api.authority_calls


@pytest.mark.parametrize("path", ["/auth/password/login", "/auth/invites/accept"])
def test_malformed_cutover_policy_returns_503_before_invalid_credentials_payload(api, path):
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "typo")
    response = api.browser.post(path, content=b"not-json", headers={"Content-Type": "application/json"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "envidicy_cutover_configuration_invalid"
    assert "location" not in response.headers and "set-cookie" not in response.headers


def test_credential_redirect_does_not_replace_preflight_or_rate_limiting(api):
    only_id(api)
    api.monkeypatch.setattr(api.main, "settings", replace(
        api.main.settings, auth_rate_limit_enabled=True, auth_rate_limit_auth_max_requests=1,
    ))
    assert api.browser.options("/auth/password/login").status_code == 204
    assert api.browser.post("/auth/password/login", json={}).status_code == 303
    throttled = api.browser.post("/auth/password/login", json={})
    assert throttled.status_code == 429 and "location" not in throttled.headers


@pytest.mark.parametrize("method,path", [
    ("GET", "/auth/password/login"), ("POST", "/auth/password/login/extra"),
    ("POST", "/auth/internal/sessions/issue"), ("POST", "/auth/internal/facade/external/resolve"),
    ("POST", "/auth/session/refresh"), ("POST", "/auth/logout"),
    ("POST", "/auth/envidicy/logout"), ("POST", "/ad-accounts/sync/due"),
])
def test_credential_redirect_is_not_a_global_auth_policy(api, method, path):
    only_id(api)
    assert redirect_legacy_credentials(method, path) is False


def test_default_dual_login_keeps_password_authentication_and_validation(api):
    assert not redirect_legacy_credentials("POST", "/auth/password/login")
    invalid = api.browser.post("/auth/password/login", content=b"not-json",
                               headers={"Content-Type": "application/json"})
    assert invalid.status_code == 422 and "location" not in invalid.headers
    response = api.browser.post("/auth/password/login", json={
        "email": api.legacy.email, "password": "test-local-password-123!",
    })
    assert response.status_code == 200, response.text
    assert api.auth.validate_session(api.browser.cookies.get(api.main.settings.auth_cookie_name)).valid
    assert not api.exchange_calls and not api.authority_calls


@pytest.mark.parametrize("path", ["/auth/me", "/auth/csrf", "/clients", "/auth/internal/users"])
def test_existing_local_human_session_is_rejected(api, path):
    token = local_token(api)
    assert api.browser.get(path, headers={"Authorization": "Bearer " + token}).status_code == 200
    only_id(api)
    rejected = api.browser.get(path, headers={"Authorization": "Bearer " + token})
    assert rejected.status_code == 401, rejected.text
    assert "envidicy_id_login_required" in rejected.text
    # Closing a route must not delete users, business data or session history.
    assert api.auth.get_user(api.legacy.id).role == "admin"
    assert api.auth.validate_session(token).valid


def test_old_session_cannot_refresh_but_can_logout(api):
    token = local_token(api)
    expiry = api.auth.validate_session(token).expires_at
    only_id(api)
    headers = {"Authorization": "Bearer " + token}
    assert api.browser.post("/auth/session/refresh", headers=headers).status_code == 401
    assert api.auth.validate_session(token).expires_at == expiry
    assert api.browser.post("/auth/logout", headers=headers).status_code == 200
    assert not api.auth.validate_session(token).valid


def test_live_provider_write_boundary_rechecks_cutover_policy(api):
    from starlette.requests import Request

    token = local_token(api)
    ctx = api.main.RequestContext(user_id=api.legacy.id, role="admin", global_access=True,
                                  accessible_client_ids=set())
    request = Request({"type": "http", "method": "POST", "path": "/unused",
                       "headers": [(b"authorization", ("Bearer " + token).encode("ascii"))]})
    only_id(api)
    with pytest.raises(HTTPException) as error:
        api.main._meta_budget_live_request_context(ctx, request)
    assert error.value.status_code == 401
    assert error.value.detail["code"] == "envidicy_id_login_required"


def test_internal_mint_and_dev_admin_bypass_are_closed(api):
    only_id(api)
    assert api.browser.get("/auth/internal/users").status_code == 401
    result = api.browser.post("/auth/internal/sessions/issue", json={"user_id": str(api.legacy.id)})
    assert result.status_code == 410


@pytest.mark.parametrize("intent", ["login", "link", "migrate"])
def test_legacy_social_starts_are_closed_before_provider_access(api, intent):
    only_id(api)
    def unexpected():
        raise AssertionError("Closed social login must not load provider adapters")
    api.monkeypatch.setattr(api.main, "_oauth_adapters", unexpected)
    response = api.browser.get("/auth/facebook/start", params={"intent": intent,
        "next": "/portal/reports?period=month#totals", "code": "discard-code", "state": "discard-state"})
    assert response.status_code == 303
    target = urlsplit(response.headers["location"])
    assert target.path == "/api/backend/auth/envidicy/start"
    assert parse_qs(target.query) == {"next": ["/portal/reports?period=month#totals"]}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("next_path", ["https://external.example.test", "//external.example.test",
                                      "/auth/envidicy/start", "/x/%2e%2e/register", "/x/../api"])
def test_closed_social_entry_rejects_unsafe_or_recursive_return(api, next_path):
    only_id(api)
    response = api.browser.get("/auth/facebook/start", params={"next": next_path})
    assert response.status_code == 303
    assert response.headers["location"] == "/api/backend/auth/envidicy/start?next=%2Fportal"


def test_closed_social_entry_defaults_to_portal_without_forwarding_query(api):
    only_id(api)
    response = api.browser.get("/auth/facebook/start?email=discard-email&password=discard-password")
    assert response.status_code == 303
    assert response.headers["location"] == "/api/backend/auth/envidicy/start?next=%2Fportal"


def test_old_social_callback_consumes_state_without_exchange_or_session(api):
    calls = []
    class Adapter:
        def fetch_identity(self, *_args):
            calls.append(True)
            raise AssertionError("Legacy callback must not exchange after cutover")
    api.main.app.state.oauth_adapters["facebook"] = Adapter()
    nonce = "old-social-browser-nonce"
    state = api.main._oauth_state_store().create_state(provider="facebook", next_path="/portal/reports?period=month#totals", nonce=nonce,
                                                       ttl_minutes=5)
    api.browser.cookies.set(api.main.settings.oauth_nonce_cookie_name, nonce, domain="dash.envidicy.kz", path="/")
    only_id(api)
    response = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "old-code"})
    assert response.status_code == 303, response.text
    target = urlsplit(response.headers["location"])
    assert target.path == "/api/backend/auth/envidicy/start"
    # The legacy OAuth state parser has always removed URL fragments.
    assert parse_qs(target.query) == {"next": ["/portal/reports?period=month"]}
    assert "old-code" not in response.headers["location"] and state.state not in response.headers["location"]
    assert "set-cookie" not in response.headers
    assert not calls and not api.browser.cookies.get(api.main.settings.auth_cookie_name)
    replay = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "old-code"})
    assert replay.status_code == 302 and "oauth_error=" in replay.headers["location"]


def test_provider_connect_does_not_accept_closed_human_session(api):
    token = local_token(api)
    only_id(api)
    response = api.browser.get("/auth/facebook/start", params={"intent": "connect"},
                               headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401


def test_pending_provider_connect_callback_is_not_reclassified_as_human_login(api):
    token = local_token(api)
    calls = []
    class Adapter:
        def fetch_identity(self, *_args):
            calls.append(True)
            raise AssertionError("A closed initiator cannot exchange provider codes")
    api.main.app.state.oauth_adapters["facebook"] = Adapter()
    nonce = "old-connect-browser-nonce"
    next_path = api.main._with_oauth_connect_options(
        "/portal", intent="connect", connect_mode="add", connection_key=None,
        agency_id=None, client_id=None, meta_config_id=None,
    )
    state = api.main._oauth_state_store().create_state(
        provider="facebook", next_path=next_path, nonce=nonce, ttl_minutes=5,
        initiator_user_id=api.legacy.id,
    )
    api.browser.cookies.set(api.main.settings.oauth_nonce_cookie_name, nonce, domain="dash.envidicy.kz", path="/")
    only_id(api)
    response = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "old-connect-code"},
                               headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401 and "location" not in response.headers
    assert not calls and "set-cookie" not in response.headers
    assert api.auth.validate_session(token).valid


def test_id_only_keeps_id_and_my_fail_closed_behavior(api):
    only_id(api)
    login(api)
    assert api.browser.get("/auth/me").status_code == 200
    assert api.browser.get("/clients").status_code == 200
    api.reply["access_state"] = "context_unavailable"
    assert api.browser.get("/clients").status_code == 503
    api.reply.update(access_state="not_granted", permissions=[])
    assert api.browser.get("/clients").status_code == 403
    assert api.browser.post("/auth/session/refresh", headers=csrf(api)).status_code == 401


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("failure", ["disabled", "bad_secret", "bad_switch"])
def test_broken_id_configuration_never_reopens_legacy_login(api, failure, production):
    token = local_token(api)
    production_id(api) if production else only_id(api)
    if failure == "disabled":
        api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", "false")
    elif failure == "bad_secret":
        api.monkeypatch.setenv("ENVIDICY_ID_CLIENT_SECRET", "bad")
    else:
        api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "typo")
    public = api.browser.get("/auth/envidicy/config")
    if failure == "bad_switch":
        assert public.status_code == 503
    else:
        assert public.status_code == 200
        assert public.json()["local_auth_enabled"] is False
        assert public.json()["enabled"] is False
    assert api.browser.get("/clients", headers={"Authorization": "Bearer " + token}).status_code in {401, 503}
    assert api.browser.post("/auth/password/login", json={
        "email": api.legacy.email, "password": "test-local-password-123!",
    }).status_code == (503 if failure == "bad_switch" else 303)


@pytest.mark.parametrize("production", [False, True])
def test_dedicated_metrics_and_cron_auth_survive_cutover(api, production):
    token = local_token(api)
    production_id(api) if production else only_id(api)
    api.monkeypatch.setattr(api.main, "settings", replace(api.main.settings, observability_public=False,
                                                        metrics_bearer_token="isolated-metrics-service"))
    assert api.browser.get("/metrics", headers={"Authorization": "Bearer " + token}).status_code == 404
    assert api.browser.get("/metrics", headers={"Authorization": "Bearer isolated-metrics-service"}).status_code == 200
    api.monkeypatch.setenv("SYNC_CRON_ENABLED", "true")
    api.monkeypatch.setenv("SYNC_CRON_SECRET", "isolated-cron-service")
    calls = []
    def execute(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(sync_result=None, lease_acquired=True, selected_account_ids=[],
                               started_at=api.main._utcnow(), finished_at=api.main._utcnow())
    api.monkeypatch.setattr(api.main, "_execute_due_sync_batch", execute)
    assert api.browser.post("/ad-accounts/sync/due").status_code == 401
    assert not calls
    assert api.browser.post("/ad-accounts/sync/due", headers={"X-Sync-Cron-Secret": "isolated-cron-service"}).status_code == 200
    assert len(calls) == 1
