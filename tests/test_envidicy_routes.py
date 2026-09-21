"""API-level ID rollout regressions: real isolated SQLite, no external calls."""

from dataclasses import replace
from datetime import timedelta
import importlib
import json
from types import SimpleNamespace
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import dotenv
from fastapi.testclient import TestClient
import httpx
import pytest

from app.runtime_db import runtime_conn
from app.schemas import ClientCreate, SessionContextResponse, SessionIssueRequest, UserCreate
from app.services import envidicy_oidc as oidc
from app.services.auth_arch import SqliteAuthStore
from app.services.auth_facade import AuthFacadeService
from app.services.budgets import SqliteBudgetStore
from app.services.clients import SqliteClientStore
from app.services.envidicy_bridge import EnvidicyBridge, MyAuthorityClient, SqlEnvidicyStore, MY_URL, PRODUCT, utcnow


@pytest.fixture
def api(monkeypatch, tmp_path):
    # main creates default stores at import. Never load a real .env or use PG.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_TEST_ENDPOINTS", "true")
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("BUDGETS_DB_PATH", str(tmp_path / "bootstrap.sqlite"))
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("ENVIDICY_ID_ENABLED", "true")
    monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "false")
    monkeypatch.setenv("ENVIDICY_ID_CLIENT_SECRET", "test-only-id-secret-" + "x" * 32)
    for key in ("DATABASE_URL", "ENVIDICY_ID_CLIENT_SECRET_FILE", "ENVIDICY_MY_AUTHORITY_TOKEN_FILE", "ENVIDICY_MY_AUTHORITY_TOKEN", "ENVIDICY_ID_PILOT_SUBJECTS", "ENVIDICY_ID_AUTO_LOGIN_ENABLED"):
        monkeypatch.delenv(key, raising=False)

    def no_network(*_args, **_kwargs):
        raise AssertionError("Route tests must not make real outbound HTTP calls")
    async def no_async_network(*_args, **_kwargs):
        raise AssertionError("Route tests must not make real outbound HTTP calls")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_async_network)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "settings", replace(main.settings, app_env="test", enable_test_endpoints=True,
                                                   csrf_enforce_cookie_auth=True, auth_rate_limit_enabled=False))
    monkeypatch.setattr(main, "_schedule_login_auto_sync", lambda *_args, **_kwargs: None)
    previous_state = dict(main.app.state._state)
    main.use_inmemory_stores()
    path = str(tmp_path / "routes.sqlite")
    auth = SqliteAuthStore(path)
    clients = SqliteClientStore(path)
    budgets = SqliteBudgetStore(path)
    store = SqlEnvidicyStore(auth)
    mapped = clients.create(ClientCreate(name="Mapped project", default_currency="USD"))
    other = clients.create(ClientCreate(name="Unrelated project", default_currency="USD"))
    legacy = auth.create_user(UserCreate(name="Legacy admin", email="legacy@example.test", role="admin"))
    auth.set_password(legacy.id, "test-local-password-123!")
    organization, project = str(uuid4()), str(uuid4())
    store.bind_project(organization, project, str(mapped.id), operator_ref="isolated-regression", apply=True)
    reply = {"access_state": "ready", "permissions": [PRODUCT, PRODUCT + ".read"],
             "organization_id": organization, "project_id": project}
    authority_calls = []
    class Authority:
        def resolve(self, issuer, subject):
            authority_calls.append((issuer, subject))
            if reply["access_state"] == "context_unavailable":
                raise ValueError("isolated My outage")
            return dict(reply)
    bridge = EnvidicyBridge(store, clients, authority=Authority())
    main.app.state.auth_store = auth
    main.app.state.client_store = clients
    main.app.state.budget_store = budgets
    main.app.state.envidicy_bridge = bridge
    main.app.state.auth_facade = AuthFacadeService(auth, context_resolver=bridge.project_session)
    exchange_calls = []
    identity = oidc.EnvidicyIdentity(oidc.ISSUER, "isolated-id-subject", "ID person", legacy.email)
    async def exchange(_config, **kwargs):
        exchange_calls.append(kwargs)
        return identity
    monkeypatch.setattr(oidc, "exchange_code", exchange)
    browser = TestClient(main.app, base_url="https://dash.envidicy.kz", follow_redirects=False)
    state = SimpleNamespace(main=main, browser=browser, auth=auth, clients=clients, budgets=budgets, store=store,
                            path=path, mapped=mapped, other=other, legacy=legacy, reply=reply, identity=identity,
                            authority_calls=authority_calls, exchange_calls=exchange_calls, monkeypatch=monkeypatch)
    try:
        yield state
    finally:
        browser.close()
        main.app.state._state.clear()
        main.app.state._state.update(previous_state)


def start(api, next_path="/portal"):
    response = api.browser.get("/auth/envidicy/start", params={"next": next_path})
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).netloc == "id.envidicy.com"
    params = parse_qs(urlsplit(response.headers["location"]).query)
    return params, api.browser.cookies.get("ops_envidicy_tx")


def login(api, next_path="/portal"):
    params, browser = start(api, next_path)
    response = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "code-from-id"})
    assert_completed_return(response, next_path)
    return response, params, browser


def assert_completed_return(response, destination):
    location = urlsplit(response.headers["location"])
    assert response.status_code == 303
    assert not location.scheme and not location.netloc and not location.fragment
    assert location.path == "/login/success"
    # No OIDC code, state, token or identity is forwarded to the public page.
    assert parse_qs(location.query) == {"next": [destination]}


def csrf(api):
    return {api.main.settings.csrf_header_name: api.browser.cookies.get(api.main.settings.csrf_cookie_name)}


def plan(api, client_id=None, **changes):
    return {"client_id": str(client_id or api.mapped.id), "scope": "client", "amount": "100.00", "currency": "USD",
            "period_type": "custom", "start_date": "2026-09-01", "end_date": "2026-09-30", **changes}


def test_callback_uses_bound_pkce_nonce_ignores_email_and_is_single_use(api):
    params, browser = start(api)
    with runtime_conn(api.path) as conn:
        transaction = dict(conn.execute("SELECT * FROM envidicy_login_transactions").fetchone())
    assert transaction["state_hash"] != params["state"][0]
    assert transaction["browser_hash"] != browser
    response = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert api.exchange_calls == [{"code": "id-code", "code_verifier": transaction["code_verifier"], "expected_nonce": transaction["nonce"]}]
    me = api.browser.get("/auth/me").json()
    assert me["user"]["id"] != str(api.legacy.id)
    assert me["user"]["email"] is None and me["user"]["role"] == "client"
    assert me["session"]["auth_source"] == "envidicy_id"
    token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    assert api.auth.validate_session(token).expires_at <= api.main._utcnow() + timedelta(minutes=15, seconds=3)
    api.browser.cookies.set("ops_envidicy_tx", browser, domain="dash.envidicy.kz", path="/")
    replay = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    assert "oauth_error=" in replay.headers["location"]
    assert len(api.exchange_calls) == 1


def test_callback_cannot_be_completed_from_a_different_browser(api):
    params, _browser = start(api)
    stranger = TestClient(api.main.app, base_url="https://dash.envidicy.kz", follow_redirects=False)
    try:
        stranger.cookies.set("ops_envidicy_tx", oidc.generate_login_transaction().state)
        rejected = stranger.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "stolen-code"})
        assert "oauth_error=" in rejected.headers["location"] and not api.exchange_calls
        accepted = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "correct-code"})
        assert_completed_return(accepted, "/portal")
        assert len(api.exchange_calls) == 1
    finally:
        stranger.close()


def test_failed_id_exchange_preserves_previous_login_and_consumes_transaction(api):
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    api.browser.cookies.set(api.main.settings.auth_cookie_name, old.token, domain="dash.envidicy.kz", path="/")
    params, browser = start(api)
    calls = []
    async def rejected(*_args, **_kwargs):
        calls.append(True)
        raise oidc.EnvidicyOidcError("ID temporarily unavailable")
    api.monkeypatch.setattr(oidc, "exchange_code", rejected)
    callback = {"state": params["state"][0], "code": "id-code"}
    assert "oauth_error=" in api.browser.get("/auth/envidicy/callback", params=callback).headers["location"]
    assert api.auth.validate_session(old.token).valid
    assert api.browser.get("/auth/me").json()["user"]["id"] == str(api.legacy.id)
    api.browser.cookies.set("ops_envidicy_tx", browser, domain="dash.envidicy.kz", path="/")
    api.browser.get("/auth/envidicy/callback", params=callback)
    assert calls == [True]


def test_successful_id_exchange_revokes_only_the_previous_browser_session(api):
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    other_device = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    api.browser.cookies.set(api.main.settings.auth_cookie_name, old.token, domain="dash.envidicy.kz", path="/")
    login(api)
    assert not api.auth.validate_session(old.token).valid
    assert api.auth.validate_session(other_device.token).valid


def test_pilot_denial_creates_no_projection_and_preserves_legacy_session(api):
    api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", '["separate-test-subject"]')
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    api.browser.cookies.set(api.main.settings.auth_cookie_name, old.token, domain="dash.envidicy.kz", path="/")
    with runtime_conn(api.path) as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    params, _ = start(api, "/portal/reports?period=month#totals")
    callback = api.browser.get("/auth/envidicy/callback", params={
        "state": params["state"][0], "code": "id-code", "sub": "separate-test-subject",
    })
    redirect = parse_qs(urlsplit(callback.headers["location"]).query)
    assert redirect == {"oauth_error": ["envidicy_pilot_only"], "next": ["/portal/reports?period=month#totals"]}
    assert api.auth.validate_session(old.token).valid
    assert api.browser.cookies.get(api.main.settings.auth_cookie_name) == old.token
    assert not api.authority_calls
    with runtime_conn(api.path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == before
        assert conn.execute("SELECT COUNT(*) AS n FROM envidicy_id_principals").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM envidicy_login_transactions").fetchone()["n"] == 0


def test_pilot_accepts_only_verified_allowed_subject_and_revokes_cohort_on_next_request(api):
    api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", json.dumps([api.identity.subject]))
    public = api.browser.get("/auth/envidicy/config")
    assert public.json()["enabled"] is True and api.identity.subject not in public.text
    login(api)
    assert api.browser.get("/auth/me").json()["session"]["authority"]["access_state"] == "ready"
    calls = len(api.authority_calls)
    api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", '["another-test-subject"]')
    me = api.browser.get("/auth/me").json()
    assert me["session"]["authority"]["access_state"] == "not_granted"
    assert me["session"]["authority"]["redirect_to_my"] is False
    assert me["session"]["accessible_client_ids"] == []
    assert len(api.authority_calls) == calls
    denied = api.browser.get("/clients")
    assert denied.status_code == 403
    assert len(api.authority_calls) == calls


@pytest.mark.parametrize("value", ["", "[]", "null", "not-json", '["duplicate","duplicate"]'])
def test_invalid_pilot_config_disables_id_but_preserves_legacy_policy(api, value):
    api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", value)
    public = api.browser.get("/auth/envidicy/config").json()
    assert public["enabled"] is False and public["local_auth_enabled"] is True
    rejected = api.browser.get("/auth/envidicy/start")
    assert "envidicy_auth_not_configured" in rejected.headers["location"]
    assert not api.exchange_calls
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    assert api.browser.get("/auth/me", headers={"Authorization": "Bearer " + old.token}).status_code == 200


def test_pilot_config_changed_after_start_fails_before_exchange(api):
    params, _ = start(api)
    api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", "[]")
    response = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    assert "oauth_error=" in response.headers["location"]
    assert not api.exchange_calls
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)


@pytest.mark.parametrize("problem", ["expired", "duplicate_state", "duplicate_code", "provider_error", "exchange_failure"])
def test_invalid_callbacks_issue_no_session(api, problem):
    params, _browser = start(api)
    values = [("state", params["state"][0]), ("code", "id-code")]
    if problem == "expired":
        with runtime_conn(api.path) as conn:
            conn.execute("UPDATE envidicy_login_transactions SET expires_at=?", ((utcnow() - timedelta(minutes=1)).isoformat(),))
    elif problem == "duplicate_state":
        values.append(("state", params["state"][0]))
    elif problem == "duplicate_code":
        values.append(("code", "another-code"))
    elif problem == "provider_error":
        values.append(("error", "access_denied"))
    else:
        async def rejected(*_args, **_kwargs):
            raise oidc.EnvidicyOidcError("safe rejection")
        api.monkeypatch.setattr(oidc, "exchange_code", rejected)
    result = api.browser.get("/auth/envidicy/callback", params=values)
    assert "oauth_error=" in result.headers["location"]
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)
    assert api.browser.get("/auth/me").status_code == 401


@pytest.mark.parametrize("next_path", ["//evil.example", "/\\evil.example", "/%2f%2fevil.example", "/%252f%252fevil.example",
                                       "/%250a/evil", "/auth/envidicy/start", "/api/backend/auth/envidicy/start", "/login",
                                       "/api", "/auth", "/register", "/register/", "/register/complete?next=/portal",
                                       "/%72egister", "/%2561uth", "/portal/../register", "/portal/%2e%2e/auth",
                                       "/portal/%252e%252e/api", "/./login", "/x/../auth/envidicy"])
def test_login_next_cannot_escape_origin_or_loop_into_auth(api, next_path):
    params, _browser = start(api, next_path)
    result = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    assert_completed_return(result, "/portal")


def test_feature_off_keeps_legacy_login_me_and_refresh_working_without_my(api):
    api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", "false")
    api.monkeypatch.setenv("ENVIDICY_ID_CLIENT_SECRET_FILE", "unreadable-while-disabled")
    public = api.browser.get("/auth/envidicy/config").json()
    assert public["enabled"] is False
    assert "secret" not in str(public)
    assert "envidicy_auth_disabled" in api.browser.get("/auth/envidicy/start").headers["location"]
    legacy = api.browser.post("/auth/password/login", json={"email": api.legacy.email, "password": "test-local-password-123!"})
    assert legacy.status_code == 200
    me = api.browser.get("/auth/me").json()
    assert me["user"]["id"] == str(api.legacy.id) and me["session"]["global_access"] is True
    assert "auth_source" not in me["session"] and "authority" not in me["session"]
    assert api.browser.post("/auth/session/refresh", headers=csrf(api)).status_code == 200
    assert not api.authority_calls


@pytest.mark.parametrize("destination", ["/budgets?view=history#entries", "/reports/../portal/reports?view=history#entries"])
def test_id_callback_preserves_safe_query_and_fragment(api, destination):
    response, _params, _browser = login(api, destination)
    assert_completed_return(response, destination)


@pytest.mark.parametrize("access,expected_status", [("ready", 200), ("not_granted", 403), ("project_unlinked", 403), ("context_unavailable", 503)])
def test_me_exposes_access_states_but_data_requires_live_ready_scope(api, access, expected_status):
    login(api)
    if access == "project_unlinked":
        api.monkeypatch.setattr(api.store, "binding", lambda *_args: None)
    else:
        api.reply["access_state"] = access
    me = api.browser.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["session"]["authority"]["access_state"] == access
    assert me.json()["session"]["global_access"] is False
    result = api.browser.get("/clients")
    assert result.status_code == expected_status
    if access == "ready":
        assert str(api.mapped.id) in result.text and str(api.other.id) not in result.text
    else:
        assert me.json()["session"]["accessible_client_ids"] == []


def test_grant_revocation_is_effective_on_next_data_request(api):
    login(api)
    assert api.browser.get("/clients").status_code == 200
    api.reply.update(access_state="not_granted", permissions=[])
    assert api.browser.get("/clients").status_code == 403
    assert api.browser.get("/auth/me").json()["session"]["authority"]["access_state"] == "not_granted"
    assert len(api.authority_calls) >= 3


def test_disabling_id_does_not_turn_existing_id_session_into_legacy_access(api):
    login(api)
    before = len(api.authority_calls)
    api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", "false")
    me = api.browser.get("/auth/me").json()
    assert me["session"]["auth_source"] == "envidicy_id"
    assert me["session"]["authority"]["access_state"] == "context_unavailable"
    assert me["session"]["accessible_client_ids"] == []
    assert api.browser.get("/clients").status_code == 503
    assert len(api.authority_calls) == before


def test_read_permission_cannot_write_or_read_another_tenant(api):
    login(api)
    assert api.browser.get(f"/clients/{api.other.id}").status_code == 403
    rejected = api.browser.post("/budgets", json=plan(api), headers=csrf(api))
    assert rejected.status_code == 403 and "envidicy_operation_not_available" in rejected.text
    assert api.budgets.list() == []


def test_manage_is_budget_only_tenant_scoped_and_does_not_accept_supplied_actors(api):
    api.reply["permissions"].append(PRODUCT + ".manage")
    login(api)
    user_id = api.browser.get("/auth/me").json()["user"]["id"]
    created = api.browser.post("/budgets", json=plan(api, created_by=str(api.legacy.id)), headers=csrf(api))
    assert created.status_code == 200, created.text
    assert created.json()["created_by"] == user_id
    budget_id = created.json()["id"]
    changed = api.browser.patch(f"/budgets/{budget_id}", json={"amount": "150.00", "changed_by": str(api.legacy.id)}, headers=csrf(api))
    assert changed.status_code == 200, changed.text
    history = api.browser.get(f"/budgets/{budget_id}/history").json()
    assert all(row["changed_by"] == user_id for row in history)
    assert api.browser.post("/budgets", json=plan(api, api.other.id), headers=csrf(api)).status_code == 403
    assert api.browser.post("/clients", json={"name": "Forbidden provisioning"}, headers=csrf(api)).status_code == 403
    assert api.browser.post("/auth/internal/users", json={"name": "Privilege escalation", "role": "admin"}, headers=csrf(api)).status_code == 403
    assert api.browser.get("/auth/internal/users").status_code == 403


def test_manage_does_not_allow_accepting_legacy_agency_invites(api):
    api.reply["permissions"].append(PRODUCT + ".manage")
    login(api)
    result = api.browser.post("/auth/invites/accept", json={"token": "not-a-real-invite", "name": "ID person"}, headers=csrf(api))
    assert result.status_code == 403 and "envidicy_operation_not_available" in result.text


def test_id_session_cannot_start_linking_a_legacy_facebook_identity(api):
    login(api)
    api.reply.update(access_state="not_granted", permissions=[])
    calls = []
    class Adapter:
        def build_authorize_url(self, _config, state):
            calls.append(state)
            return "https://facebook.example.test/authorize?state=" + state
    api.main.app.state.oauth_adapters["facebook"] = Adapter()
    api.monkeypatch.setattr(api.main, "_oauth_provider_config_or_400", lambda *_args, **_kwargs: SimpleNamespace(config_id="", client_id="test-only"))
    result = api.browser.get("/auth/facebook/start", params={"intent": "link"})
    assert result.status_code == 403
    assert not calls


def test_stale_legacy_link_callback_is_rejected_before_facebook_exchange(api):
    login(api)
    user_id = UUID(api.browser.get("/auth/me").json()["user"]["id"])
    calls = []
    class Adapter:
        def fetch_identity(self, *_args):
            calls.append(True)
            raise ValueError("test stops before any provider side effect")
    api.main.app.state.oauth_adapters["facebook"] = Adapter()
    api.monkeypatch.setattr(api.main, "_oauth_provider_config_or_400", lambda *_args, **_kwargs: SimpleNamespace(config_id="", client_id="test-only"))
    next_path = api.main._with_oauth_connect_options("/portal", intent="link", connect_mode="add", connection_key=None,
                                                    agency_id=None, client_id=None, meta_config_id=None)
    nonce = "test-only-old-oauth-nonce"
    state = api.main._oauth_state_store().create_state(provider="facebook", next_path=next_path, nonce=nonce,
                                                       ttl_minutes=5, initiator_user_id=user_id)
    api.browser.cookies.set(api.main.settings.oauth_nonce_cookie_name, nonce, domain="dash.envidicy.kz", path="/")
    result = api.browser.get("/auth/facebook/callback", params={"state": state.state, "code": "fake-code"})
    assert result.status_code == 302 and "oauth_error=" in result.headers["location"]
    assert not calls
    assert api.auth.list_identities(user_id=user_id) == []


def test_id_session_cannot_be_renewed_by_legacy_refresh(api):
    login(api)
    token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    before = api.auth.validate_session(token).expires_at
    response = api.browser.post("/auth/session/refresh", headers=csrf(api))
    assert response.status_code == 401 and "envidicy_reauthentication_required" in response.text
    assert api.auth.validate_session(token).expires_at == before


def test_logout_requires_csrf_revokes_local_session_and_has_bound_one_time_callback(api):
    login(api)
    token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    assert api.browser.post("/auth/envidicy/logout").status_code == 403
    assert api.auth.validate_session(token).valid
    response = api.browser.post("/auth/envidicy/logout", headers=csrf(api))
    assert response.status_code == 200
    assert not api.auth.validate_session(token).valid
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)
    query = parse_qs(urlsplit(response.json()["logout_url"]).query)
    state = query["state"][0]
    browser = api.browser.cookies.get("ops_envidicy_tx")
    assert query["post_logout_redirect_uri"] == ["https://dash.envidicy.kz/api/backend/auth/envidicy/logout/callback"]
    accepted = api.browser.get("/auth/envidicy/logout/callback", params={"state": state})
    assert accepted.headers["location"] == "/login?logged_out=1"
    api.browser.cookies.set("ops_envidicy_tx", browser, domain="dash.envidicy.kz", path="/")
    replay = api.browser.get("/auth/envidicy/logout/callback", params={"state": state})
    assert "oauth_error=" in replay.headers["location"]


def test_logout_still_revokes_cookie_session_when_id_configuration_breaks(api):
    login(api)
    token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    api.monkeypatch.setenv("ENVIDICY_ID_CLIENT_SECRET", "bad")
    response = api.browser.post("/auth/envidicy/logout", headers=csrf(api))
    assert response.status_code == 200 and response.json()["logout_url"] == "/login?logged_out=1"
    assert not api.auth.validate_session(token).valid
    assert api.browser.get("/auth/me").status_code == 401


def test_failed_exchange_keeps_safe_return_target_for_the_next_id_attempt(api):
    next_path = "/portal?report=existing"
    params, _browser = start(api, next_path)
    async def rejected(*_args, **_kwargs):
        raise oidc.EnvidicyOidcError("ID unavailable")
    api.monkeypatch.setattr(oidc, "exchange_code", rejected)
    response = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["next"] == [next_path] and query["oauth_error"] == ["envidicy_auth_failed"]


@pytest.mark.parametrize("header", ["Authorization", "X-Session-Token"])
def test_id_logout_revokes_explicit_token_instead_of_different_cookie_session(api, header):
    login(api)
    cookie_token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    user_id = api.auth.validate_session(cookie_token).user_id
    other = api.auth.issue_envidicy_session(user_id)
    value = "Bearer " + other.token if header == "Authorization" else other.token
    response = api.browser.post("/auth/envidicy/logout", headers={header: value})
    assert response.status_code == 200
    assert not api.auth.validate_session(other.token).valid
    assert api.auth.validate_session(cookie_token).valid


def test_id_logout_rejects_malformed_authorization_without_revoking_cookie(api):
    login(api)
    token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
    response = api.browser.post("/auth/envidicy/logout", headers={"Authorization": "Basic invalid", **csrf(api)})
    assert response.status_code == 401 and api.auth.validate_session(token).valid


def test_generic_session_api_cannot_request_id_provenance(api):
    response = api.browser.post("/auth/internal/sessions/issue", json={
        "user_id": str(api.legacy.id), "ttl_minutes": 10080, "metadata": {"auth_method": "envidicy_id"},
    })
    assert response.status_code == 400
    assert not api.browser.cookies.get(api.main.settings.auth_cookie_name)


def test_linked_old_password_session_stays_legacy_and_refreshes_while_id_is_off(api):
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60,
                                                    metadata={"auth_method": "password"}))
    with runtime_conn(api.path) as conn:
        conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)",
                     (str(api.legacy.id), oidc.ISSUER, api.identity.subject, utcnow().isoformat()))
    api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", "false")
    api.browser.cookies.set(api.main.settings.auth_cookie_name, old.token, domain="dash.envidicy.kz", path="/")
    me = api.browser.get("/auth/me", params={"auth_method": "envidicy_id", "auth_source": "envidicy_id"})
    assert me.status_code == 200
    assert me.json()["session"]["global_access"] is True
    assert "auth_source" not in me.json()["session"]
    csrf_response = api.browser.get("/auth/csrf")
    assert csrf_response.status_code == 200
    refreshed = api.browser.post("/auth/session/refresh", headers=csrf(api))
    assert refreshed.status_code == 200
    assert not api.authority_calls


def test_id_callback_reuses_explicit_legacy_binding_without_elevating_old_role(api):
    with runtime_conn(api.path) as conn:
        conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)",
                     (str(api.legacy.id), oidc.ISSUER, api.identity.subject, utcnow().isoformat()))
    login(api)
    me = api.browser.get("/auth/me").json()
    assert me["user"]["id"] == str(api.legacy.id) and me["user"]["role"] == "client"
    assert me["session"]["auth_source"] == "envidicy_id" and not me["session"]["global_access"]
    assert api.auth.get_user(api.legacy.id).role == "admin"
    assert api.browser.get("/auth/internal/users").status_code == 403


def test_id_session_losing_identity_binding_cannot_regain_legacy_grants(api):
    with runtime_conn(api.path) as conn:
        conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)",
                     (str(api.legacy.id), oidc.ISSUER, api.identity.subject, utcnow().isoformat()))
    login(api)
    with runtime_conn(api.path) as conn:
        conn.execute("DELETE FROM envidicy_id_principals WHERE user_id=?", (str(api.legacy.id),))
    assert api.browser.get("/auth/me").status_code == 401
    assert api.browser.get("/clients").status_code == 401


def test_id_only_config_failure_does_not_reenable_local_login(api):
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "true")
    api.monkeypatch.setenv("ENVIDICY_ID_CLIENT_SECRET", "misconfigured")
    response = api.browser.get("/auth/envidicy/config")
    assert response.status_code == 200
    assert response.json()["enabled"] is False and response.json()["local_auth_enabled"] is False


@pytest.mark.parametrize("enabled,auto_flag,id_only,expected_auto", [
    (True, None, False, False),
    (True, False, False, False),
    (True, True, False, True),
    (True, False, True, True),
    (True, True, True, True),
    (False, None, False, False),
    (False, True, False, False),
    (False, True, True, False),
])
def test_auto_login_config_is_opt_in_and_never_changes_legacy_policy(api, enabled, auto_flag, id_only, expected_auto):
    api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", str(enabled).lower())
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", str(id_only).lower())
    if auto_flag is not None:
        api.monkeypatch.setenv("ENVIDICY_ID_AUTO_LOGIN_ENABLED", str(auto_flag).lower())
    response = api.browser.get("/auth/envidicy/config", params={"auto_login": "true", "legacy": "true"})
    assert response.status_code == 200
    assert response.json()["enabled"] is enabled
    assert response.json()["auto_login"] is expected_auto
    assert response.json()["server_entry_ready"] is True
    assert response.json()["session_cookie_name"] == api.main.settings.auth_cookie_name
    assert response.json()["local_auth_enabled"] is (not id_only)
    assert response.headers["cache-control"] == "no-store"
    assert not api.authority_calls


@pytest.mark.parametrize("value", ["", "treu", "2"])
def test_invalid_auto_login_policy_is_503_without_reopening_id_only_login(api, value):
    api.monkeypatch.setenv("ENVIDICY_ID_ONLY_ENABLED", "true")
    api.monkeypatch.setenv("ENVIDICY_ID_AUTO_LOGIN_ENABLED", value)
    response = api.browser.get("/auth/envidicy/config")
    assert response.status_code == 503
    assert "envidicy_cutover_configuration_invalid" in response.text
    assert api.browser.post("/auth/password/login", json={
        "email": api.legacy.email, "password": "test-local-password-123!",
    }).status_code == 303


def test_callback_confirmed_my_denial_hands_off_to_fixed_my_url_with_short_id_session(api):
    api.reply.update(access_state="not_granted", permissions=[], redirect_to_my=True)
    params, _ = start(api, "/portal/reports?period=month#totals")
    response = api.browser.get("/auth/envidicy/callback", params={
        "state": params["state"][0], "code": "id-code", "my_url": "https://untrusted.example",
    })
    assert response.status_code == 303 and response.headers["location"] == MY_URL
    assert len(api.authority_calls) == 1
    checked = api.auth.validate_session(api.browser.cookies.get(api.main.settings.auth_cookie_name))
    assert checked.valid and checked.auth_method == "envidicy_id"
    assert checked.expires_at == checked.issued_at + timedelta(minutes=15)
    assert api.browser.cookies.get(api.main.settings.csrf_cookie_name)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert api.browser.get("/clients").status_code == 403


@pytest.mark.parametrize("state", ["ready", "not_granted", "project_unlinked", "context_unavailable"])
def test_callback_without_confirmed_my_denial_stays_in_dash_for_existing_access_gate(api, state):
    if state == "project_unlinked":
        api.monkeypatch.setattr(api.store, "binding", lambda *_args: None)
    else:
        api.reply.update(access_state=state)
    response, _, _ = login(api, "/portal/reports?view=weekly#chart")
    assert len(api.authority_calls) == 1
    assert api.auth.validate_session(api.browser.cookies.get(api.main.settings.auth_cookie_name)).valid
    me = api.browser.get("/auth/me").json()
    assert me["session"]["authority"]["access_state"] == state
    assert me["session"]["authority"]["redirect_to_my"] is False


def test_callback_resolves_my_once_off_the_async_event_loop(api):
    exchange_threads, authority_threads = [], []
    async def exchange(*_args, **_kwargs):
        exchange_threads.append(threading.get_ident())
        return api.identity
    original = api.main.app.state.envidicy_bridge.authority.resolve
    def resolve(*args):
        authority_threads.append(threading.get_ident())
        return original(*args)
    api.monkeypatch.setattr(oidc, "exchange_code", exchange)
    api.monkeypatch.setattr(api.main.app.state.envidicy_bridge.authority, "resolve", resolve)
    login(api)
    assert len(exchange_threads) == len(authority_threads) == 1
    assert exchange_threads[0] != authority_threads[0]


@pytest.mark.parametrize("problem", [401, 403, 429, 503, "timeout", "malformed"])
def test_callback_my_transport_failure_is_a_retryable_local_gate_not_a_denial(api, problem):
    calls = []
    def handler(request):
        calls.append(request)
        if problem == "timeout":
            raise httpx.ReadTimeout("Private transport diagnostic", request=request)
        return httpx.Response(200 if problem == "malformed" else problem, json={})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        api.monkeypatch.setattr(api.main.app.state.envidicy_bridge, "authority", MyAuthorityClient(client=client, token="test-only"))
        response, _, _ = login(api, "/portal/reports?view=weekly#chart")
        assert len(calls) == 1
        token = api.browser.cookies.get(api.main.settings.auth_cookie_name)
        assert api.auth.validate_session(token).valid
        me = api.browser.get("/auth/me").json()
        assert me["session"]["authority"]["access_state"] == "context_unavailable"
        assert me["session"]["authority"]["redirect_to_my"] is False
        assert api.browser.get("/clients").status_code == 503
        assert "Private transport diagnostic" not in response.text and "Private transport diagnostic" not in str(me)


@pytest.mark.parametrize("failure", ["invalid", "wrong_user", "legacy_origin", "exception", "pilot_removed", "pilot_malformed", "disabled"])
def test_callback_unusable_new_session_is_revoked_and_preserves_previous_cookie(api, failure):
    old = api.auth.issue_session(SessionIssueRequest(user_id=api.legacy.id, ttl_minutes=60))
    api.browser.cookies.set(api.main.settings.auth_cookie_name, old.token, domain="dash.envidicy.kz", path="/")
    original = api.main.app.state.auth_facade.get_session_context
    issued_tokens = []
    def resolve(token):
        issued_tokens.append(token)
        if failure == "exception":
            raise RuntimeError("Untrusted private dependency diagnostic")
        context = original(token)
        if failure == "invalid":
            return SessionContextResponse(valid=False, reason="revoked")
        if failure == "wrong_user":
            return context.model_copy(update={"user_id": api.legacy.id})
        if failure == "legacy_origin":
            return context.model_copy(update={"auth_method": None})
        if failure == "pilot_removed":
            api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", '["another-subject"]')
        elif failure == "pilot_malformed":
            api.monkeypatch.setenv("ENVIDICY_ID_PILOT_SUBJECTS", "[]")
        else:
            api.monkeypatch.setenv("ENVIDICY_ID_ENABLED", "false")
        return context
    api.monkeypatch.setattr(api.main.app.state.auth_facade, "get_session_context", resolve)
    params, _ = start(api, "/portal/reports?view=weekly#chart")
    response = api.browser.get("/auth/envidicy/callback", params={"state": params["state"][0], "code": "id-code"})
    redirect = parse_qs(urlsplit(response.headers["location"]).query)
    expected_error = "envidicy_pilot_only" if failure == "pilot_removed" else "envidicy_auth_failed"
    assert redirect == {"oauth_error": [expected_error], "next": ["/portal/reports?view=weekly#chart"]}
    assert len(issued_tokens) == 1 and not api.auth.validate_session(issued_tokens[0]).valid
    assert api.auth.validate_session(old.token).valid
    assert api.browser.cookies.get(api.main.settings.auth_cookie_name) == old.token
    assert "Untrusted" not in response.text and "Untrusted" not in response.headers["location"]
