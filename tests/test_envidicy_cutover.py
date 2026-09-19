"""Default-off cutover policy; isolated stores and no provider/ID network calls."""
from dataclasses import replace
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.schemas import SessionIssueRequest
from app.services.auth_cutover import auto_login_enabled, id_only_enabled
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


@pytest.mark.parametrize("path,body", [
    ("/auth/password/login", {"email": "legacy@example.test", "password": "test-local-password-123!"}),
    ("/auth/invites/accept", {"token": "not-an-invite", "name": "Test person"}),
])
def test_local_login_and_onboarding_are_closed(api, path, body):
    only_id(api)
    result = api.browser.post(path, json=body)
    assert result.status_code == 410, result.text
    assert result.json()["error"]["code"] == "envidicy_id_login_required"
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)


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
    response = api.browser.get("/auth/facebook/start", params={"intent": intent})
    assert response.status_code == 410


def test_old_social_callback_consumes_state_without_exchange_or_session(api):
    calls = []
    class Adapter:
        def fetch_identity(self, *_args):
            calls.append(True)
            raise AssertionError("Legacy callback must not exchange after cutover")
    api.main.app.state.oauth_adapters["facebook"] = Adapter()
    nonce = "old-social-browser-nonce"
    state = api.main._oauth_state_store().create_state(provider="facebook", next_path="/portal", nonce=nonce,
                                                       ttl_minutes=5)
    api.browser.cookies.set(api.main.settings.oauth_nonce_cookie_name, nonce, domain="dash.envidicy.kz", path="/")
    only_id(api)
    response = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "old-code"})
    assert response.status_code == 410, response.text
    assert not calls and not api.browser.cookies.get(api.main.settings.auth_cookie_name)
    replay = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "old-code"})
    assert replay.status_code == 302 and "oauth_error=" in replay.headers["location"]


def test_provider_connect_does_not_accept_closed_human_session(api):
    token = local_token(api)
    only_id(api)
    response = api.browser.get("/auth/facebook/start", params={"intent": "connect"},
                               headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401


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


@pytest.mark.parametrize("failure", ["disabled", "bad_secret", "bad_switch"])
def test_broken_id_configuration_never_reopens_legacy_login(api, failure):
    token = local_token(api)
    only_id(api)
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
    }).status_code in {410, 503}


def test_dedicated_metrics_and_cron_auth_survive_cutover(api):
    token = local_token(api)
    only_id(api)
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
