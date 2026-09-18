import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.runtime_db import migrate_postgres, runtime_conn
from app.schemas import ClientCreate, ClientPatch, SessionContextResponse, SessionIssueRequest, UserCreate, UserPatch
from app.services import envidicy_bridge as bridge_module
from app.services import auth_arch as auth_module
from app.services.auth_arch import InMemoryAuthStore, SqliteAuthStore
from app.services.auth_facade import AuthFacadeService
from fastapi import HTTPException
from app.services.clients import InMemoryClientStore, SqliteClientStore
from app.services.envidicy_bridge import (
    EnvidicyBridge, ISSUER, MemoryEnvidicyStore, MyAuthorityClient, PRODUCT,
    SqlEnvidicyStore, utcnow, validate_authority,
)


def decision(*, subject="id-subject", organization_id=None, project_id=None, permissions=None, request_id=None):
    now = utcnow()
    evaluated = now.isoformat()
    until = (now + timedelta(seconds=110)).isoformat()
    start = (now - timedelta(days=1)).isoformat()
    return {
        "contract_version": "envidicy.authority.v1", "request_id": request_id or str(uuid4()),
        "principal": {"iss": ISSUER, "sub": subject}, "decision": "allow", "evaluated_at": evaluated,
        "valid_until": until, "authority_revision": "core:2",
        "membership": {"status": "active", "kind": "direct", "organization_id": organization_id or str(uuid4()),
                       "project_id": project_id or str(uuid4()), "organization_name": "Organization", "project_name": "Project",
                       "brand_id": None, "brand_name": None, "timezone": "UTC", "roles": ["billing", "editor"],
                       "version": 2, "valid_from": start, "valid_until": until},
        "entitlements": [{"code": PRODUCT, "status": "active", "version": 2, "valid_from": start, "valid_until": until}],
        "permissions": permissions or [PRODUCT, PRODUCT + ".read"],
        "revocation": {"membership_generation": 2, "entitlement_generation": 2, "checked_at": evaluated},
    }


@pytest.fixture(params=["memory", "sqlite", "postgresql"])
def stores(request, monkeypatch, tmp_path):
    backend = request.param
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite" if backend == "memory" else backend)
    if backend == "postgresql":
        url = os.getenv("TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("TEST_DATABASE_URL required")
        monkeypatch.setenv("DATABASE_URL", url)
        migrate_postgres(url)
    if backend == "memory":
        auth = InMemoryAuthStore()
        return auth, InMemoryClientStore(), MemoryEnvidicyStore(auth)
    path = str(tmp_path / "id.sqlite")
    clients = SqliteClientStore(path)
    auth = SqliteAuthStore(path)
    return auth, clients, SqlEnvidicyStore(auth)


def test_id_projection_is_separate_no_email_or_grants(stores):
    auth, _, store = stores
    old = auth.create_user(UserCreate(email=f"old-{uuid4()}@example.test", name="Legacy", role="admin"))
    subject = str(uuid4())
    projected = store.resolve_identity(ISSUER, subject, "ID person")
    assert projected.id != old.id and projected.role == "client" and projected.email is None
    assert store.resolve_identity(ISSUER, subject).id == projected.id
    assert auth.list_client_access(user_id=projected.id) == []
    auth.patch_user(projected.id, UserPatch(status="inactive"))
    with pytest.raises(Exception):
        store.resolve_identity(ISSUER, subject)
    assert auth.get_user(old.id).role == "admin"


def test_transaction_is_browser_bound_expiring_and_single_use(stores):
    _, _, store = stores
    state = str(uuid4())
    store.save_transaction(state=state, browser="browser", nonce="nonce", verifier="verifier", next_path="/portal")
    assert store.consume_transaction(state, "attacker") is None
    assert store.consume_transaction(state, "browser", kind="logout") is None
    result = store.consume_transaction(state, "browser")
    assert result["nonce"] == "nonce" and result["code_verifier"] == "verifier"
    assert store.consume_transaction(state, "browser") is None


def bind(store, organization, project, client):
    if isinstance(store, MemoryEnvidicyStore):
        store.bindings[(organization, project)] = client.id
    else:
        assert store.bind_project(organization, project, str(client.id), operator_ref="test", apply=False)["status"] == "checked"
        assert store.binding(organization, project) is None
        store.bind_project(organization, project, str(client.id), operator_ref="test", apply=True)


def test_my_scope_replaces_all_local_grants_and_rechecks_every_request(stores):
    auth, clients, store = stores
    subject = str(uuid4())
    user = store.resolve_identity(ISSUER, subject)
    c1 = clients.create(ClientCreate(name="Mapped"))
    c2 = clients.create(ClientCreate(name="Other"))
    payload = decision(subject=subject)
    org, project = payload["membership"]["organization_id"], payload["membership"]["project_id"]
    bind(store, org, project, c1)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["product"] == PRODUCT and body["principal"] == {"iss": ISSUER, "sub": subject}
        assert request.url.host == "my.envidicy.com"
        reply = copy.deepcopy(payload)
        reply["request_id"] = body["request_id"]
        return httpx.Response(200, json=reply)

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    bridge = EnvidicyBridge(store, clients, MyAuthorityClient(client=transport, token="test-service-token"), enabled=lambda: True)
    local = SessionContextResponse(valid=True, user_id=user.id, role="admin", global_access=True,
                                   accessible_client_ids=[c2.id], auth_method="envidicy_id")
    projected = bridge.project_session(local)
    assert projected.accessible_client_ids == [c1.id] and not projected.global_access and projected.role == "client"
    assert projected.auth_source == "envidicy_id" and projected.authority["access_state"] == "ready"
    payload.clear()
    payload.update({"contract_version": "envidicy.authority.v1", "request_id": "filled-by-handler", "principal": {"iss": ISSUER, "sub": subject},
                    "decision": "deny", "reason_code": "permission_denied", "evaluated_at": utcnow().isoformat()})
    revoked = bridge.project_session(local)
    assert revoked.accessible_client_ids == [] and revoked.authority["access_state"] == "not_granted"
    assert len(calls) == 2


def test_no_my_or_unlinked_project_never_uses_legacy_scope(stores):
    _, clients, store = stores
    user = store.resolve_identity(ISSUER, str(uuid4()))
    local = SessionContextResponse(valid=True, user_id=user.id, role="admin", global_access=True, auth_method="envidicy_id")
    bridge = EnvidicyBridge(store, clients, enabled=lambda: False)
    result = bridge.project_session(local)
    assert not result.global_access and result.accessible_client_ids == [] and result.authority["access_state"] == "context_unavailable"
    legacy = local.model_copy(update={"user_id": uuid4(), "auth_method": None})
    assert bridge.project_session(legacy) is legacy


def test_legacy_context_wire_contract_unchanged():
    body = SessionContextResponse(valid=False, reason="expired").model_dump()
    assert "auth_source" not in body and "authority" not in body
    assert "auth_method" not in body and "issued_at" not in body


def link_existing_profile(store, user_id, subject):
    """Isolated fixture for a separately reviewed legacy-identity migration."""
    if isinstance(store, MemoryEnvidicyStore):
        store.principals[user_id] = {"issuer": ISSUER, "subject": subject}
    else:
        with runtime_conn(store.db_path) as conn:
            conn.execute("INSERT INTO envidicy_id_principals(user_id,issuer,subject,created_at) VALUES (?,?,?,?)",
                         (str(user_id), ISSUER, subject, utcnow().isoformat()))


@pytest.mark.parametrize("enabled", [False, True])
def test_linked_profile_keeps_old_sessions_legacy_and_scopes_only_true_id_sessions(stores, enabled):
    auth, clients, store = stores
    user = auth.create_user(UserCreate(email=None, name="Migrated profile", role="admin"))
    legacy = auth.issue_session(SessionIssueRequest(user_id=user.id, ttl_minutes=120,
                                                    metadata={"auth_method": "password"}))
    subject = str(uuid4())
    link_existing_profile(store, user.id, subject)
    assert store.resolve_identity(ISSUER, subject).id == user.id
    issued_id = auth.issue_envidicy_session(user.id)
    calls = []
    class Authority:
        def resolve(self, issuer, sub):
            calls.append((issuer, sub))
            return {"access_state": "not_granted", "permissions": []}
    facade = AuthFacadeService(auth, context_resolver=EnvidicyBridge(store, clients, Authority(), enabled=lambda: enabled).project_session)
    old = facade.get_session_context(legacy.token)
    assert old.valid and old.role == "admin" and old.global_access and old.auth_source is None
    assert not calls
    assert "auth_method" not in old.model_dump() and "issued_at" not in old.model_dump()
    identified = facade.get_session_context(issued_id.token)
    assert identified.valid and identified.user_id == user.id and identified.role == "client"
    assert identified.auth_source == "envidicy_id" and identified.auth_method == "envidicy_id"
    assert not identified.global_access and identified.accessible_client_ids == []
    assert len(calls) == int(enabled)
    assert auth.refresh_session(legacy.token, ttl_minutes=120).valid
    refused = auth.refresh_session(issued_id.token, ttl_minutes=120)
    assert not refused.valid and refused.reason == "envidicy_reauthentication_required"
    assert auth.get_user(user.id).role == "admin"


def test_true_id_session_without_identity_mapping_never_falls_back_to_admin(stores):
    auth, clients, store = stores
    user = auth.create_user(UserCreate(email=None, name="Unmapped profile", role="admin"))
    issued = auth.issue_envidicy_session(user.id)
    facade = AuthFacadeService(auth, context_resolver=EnvidicyBridge(store, clients, enabled=lambda: True).project_session)
    result = facade.get_session_context(issued.token)
    assert not result.valid and result.reason == "envidicy_identity_unlinked"
    assert result.auth_source == "envidicy_id" and not result.global_access and result.accessible_client_ids == []


def test_generic_session_issuer_cannot_forge_id_provenance(stores):
    auth, _, _ = stores
    user = auth.create_user(UserCreate(email=None, name="Caller", role="client"))
    with pytest.raises(HTTPException) as rejected:
        auth.issue_session(SessionIssueRequest(user_id=user.id, ttl_minutes=10080,
                                               metadata={"auth_method": "envidicy_id"}))
    assert rejected.value.status_code == 400
    ordinary = AuthFacadeService(auth).issue_session(user.id, ttl_minutes=120)
    checked = auth.validate_session(ordinary.token)
    assert checked.valid and checked.auth_method is None and checked.issued_at is not None
    assert "issued_at" not in checked.model_dump() and "auth_method" not in checked.model_dump()


def test_id_expiry_is_hard_bounded_by_original_issuance_even_if_db_expiry_is_extended(stores, monkeypatch):
    auth, _, store = stores
    user = auth.create_user(UserCreate(email=None, name="Short ID session", role="client"))
    issued = auth.issue_envidicy_session(user.id)
    original = auth.validate_session(issued.token)
    assert original.auth_method == "envidicy_id" and original.issued_at is not None
    assert original.expires_at == original.issued_at + timedelta(minutes=15)
    extended = original.issued_at + timedelta(days=7)
    if isinstance(store, MemoryEnvidicyStore):
        auth.sessions[auth_module._token_hash(issued.token)]["expires_at"] = extended
    else:
        with runtime_conn(store.db_path) as conn:
            conn.execute("UPDATE sessions SET expires_at=? WHERE id=?", (extended.isoformat(), str(issued.session_id)))
    assert auth.validate_session(issued.token).expires_at == original.expires_at
    assert not auth.refresh_session(issued.token, ttl_minutes=10080).valid
    monkeypatch.setattr(auth_module, "_utcnow", lambda: original.expires_at)
    expired = auth.validate_session(issued.token)
    assert not expired.valid and expired.reason == "expired"


def test_id_session_with_future_issuance_is_rejected(stores):
    auth, _, store = stores
    user = auth.create_user(UserCreate(email=None, name="Clock rejection", role="client"))
    issued = auth.issue_envidicy_session(user.id)
    future = auth_module._utcnow() + timedelta(minutes=10)
    if isinstance(store, MemoryEnvidicyStore):
        auth.sessions[auth_module._token_hash(issued.token)]["created_at"] = future
    else:
        with runtime_conn(store.db_path) as conn:
            conn.execute("UPDATE sessions SET created_at=? WHERE id=?", (future.isoformat(), str(issued.session_id)))
    assert not auth.validate_session(issued.token).valid


@pytest.mark.parametrize("mutate", [
    lambda p: p["principal"].update(sub="wrong"),
    lambda p: p["principal"].update(iss="https://wrong.test"),
    lambda p: p["entitlements"][0].update(code="creative.lab"),
    lambda p: p["membership"].update(kind="inherited"),
    lambda p: p["membership"].update(project_id="fake"),
    lambda p: p["membership"].update(status="inactive"),
    lambda p: p["revocation"].update(membership_generation=999),
    lambda p: p.update(permissions=[PRODUCT, PRODUCT + ".read", "platform.admin"]),
    lambda p: p.update(valid_until=(utcnow() - timedelta(seconds=1)).isoformat()),
    lambda p: p.update(extra="not allowed"),
])
def test_authority_rejects_malformed_or_cross_tenant_decisions(mutate):
    payload = decision()
    request_id = payload["request_id"]
    mutate(payload)
    with pytest.raises(ValueError):
        validate_authority(payload, issuer=ISSUER, subject="id-subject", request_id=request_id)


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500, 503])
def test_my_errors_are_not_allowed_decisions(status):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})))
    with pytest.raises(ValueError):
        MyAuthorityClient(client=client, token="fake").resolve(ISSUER, "subject")


def test_transaction_expiry_and_concurrent_consume(stores, monkeypatch):
    _, _, store = stores
    state = str(uuid4())
    store.save_transaction(state=state, browser="browser", nonce="nonce", verifier="verifier", next_path="/portal")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: store.consume_transaction(state, "browser"), range(2)))
    assert sum(result is not None for result in results) == 1
    state = str(uuid4())
    store.save_transaction(state=state, browser="browser", nonce="nonce", verifier="verifier", next_path="/portal")
    future = utcnow() + timedelta(minutes=6)
    monkeypatch.setattr(bridge_module, "utcnow", lambda: future)
    assert store.consume_transaction(state, "browser") is None


def test_binding_never_reassigns_existing_client_or_project(stores):
    _, clients, store = stores
    if isinstance(store, MemoryEnvidicyStore):
        assert not hasattr(store, "bind_project")
        return  # The operator CLI intentionally supports only persistent stores.
    first = clients.create(ClientCreate(name="First"))
    second = clients.create(ClientCreate(name="Second"))
    org, project = str(uuid4()), str(uuid4())
    bind(store, org, project, first)
    assert store.bind_project(org, project, str(first.id), operator_ref="retry", apply=True)["status"] == "unchanged"
    for other_org, other_project, other_client in [
        (str(uuid4()), project, str(first.id)),
        (org, project, str(second.id)),
        (org, str(uuid4()), str(first.id)),
    ]:
        with pytest.raises(ValueError, match="reassignment"):
            store.bind_project(other_org, other_project, other_client, operator_ref="test", apply=True)
    assert store.binding(org, project) == first.id
    clients.patch(second.id, ClientPatch(status="archived"))
    with pytest.raises(ValueError, match="active Dash client"):
        store.bind_project(org, str(uuid4()), str(second.id), operator_ref="test", apply=True)


@pytest.mark.parametrize("body", [
    b'{"decision":"allow","decision":"deny"}',
    b"x" * 32769,
    b"not-json",
], ids=["duplicate", "oversized", "invalid-json"])
def test_my_rejects_duplicate_oversized_and_invalid_json(body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body, headers={"content-type": "application/json"}))) as client:
        with pytest.raises(ValueError):
            MyAuthorityClient(client=client, token="fake").resolve(ISSUER, "subject")


def test_operator_dry_run_does_not_create_missing_sqlite_database(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from scripts import envidicy_bind_project as cli

    path = tmp_path / "missing.sqlite"
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(budgets_db_path=str(path)))
    monkeypatch.setattr("sys.argv", ["envidicy_bind_project", "--organization-id", str(uuid4()), "--project-id", str(uuid4()),
                                   "--client-id", str(uuid4()), "--operator-ref", "test"])
    assert cli.main() == 2
    assert not path.exists()
    assert "no file was created" in capsys.readouterr().err
