from fastapi.testclient import TestClient

from app.main import app
from app.services import auth_arch as auth_arch_module


client = TestClient(app)


def reset_state():
    r = client.post("/_testing/use-inmemory-stores")
    assert r.status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name="Acme"):
    r = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert r.status_code == 200
    return r.json()


def mk_user(email="u@example.com", role="agency"):
    r = client.post("/auth/internal/users", json={"email": email, "name": "User", "role": role, "status": "active"})
    assert r.status_code == 200
    return r.json()


def test_auth_access_model_endpoint():
    reset_state()
    r = client.get("/auth/access-model")
    assert r.status_code == 200
    body = r.json()
    assert set(body["roles"].keys()) == {"admin", "agency", "client"}
    assert "tenant_isolation_enforced_by_backend" in body["security_assumptions"]


def test_provider_identity_maps_to_single_internal_user():
    reset_state()
    u1 = mk_user("u1@example.com", role="agency")
    u2 = mk_user("u2@example.com", role="agency")

    linked = client.post(
        "/auth/internal/identities/link",
        json={"user_id": u1["id"], "provider": "google", "provider_user_id": "google-123", "email": "u1@example.com"},
    )
    assert linked.status_code == 200

    conflict = client.post(
        "/auth/internal/identities/link",
        json={"user_id": u2["id"], "provider": "google", "provider_user_id": "google-123", "email": "u2@example.com"},
    )
    assert conflict.status_code == 409


def test_issue_validate_revoke_backend_session():
    reset_state()
    u = mk_user("s@example.com", role="client")

    issued = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    token = issued.json()["token"]

    valid = client.post("/auth/internal/sessions/validate", json={"token": token})
    assert valid.status_code == 200
    assert valid.json()["valid"] is True
    assert valid.json()["user_id"] == u["id"]

    revoked = client.post("/auth/internal/sessions/revoke", json={"token": token})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    invalid = client.post("/auth/internal/sessions/validate", json={"token": token})
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert invalid.json()["reason"] == "revoked"


def test_provider_config_upsert_and_list():
    reset_state()
    c1 = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "gid",
            "client_secret": "gsecret",
            "redirect_uri": "http://localhost:8000/auth/google/callback",
            "enabled": True,
        },
    )
    assert c1.status_code == 200

    c2 = client.post(
        "/auth/provider-configs",
        json={
            "provider": "google",
            "client_id": "gid2",
            "client_secret": "gsecret2",
            "redirect_uri": "http://localhost:8000/auth/google/callback",
            "enabled": False,
        },
    )
    assert c2.status_code == 200
    assert c2.json()["enabled"] is False
    assert c2.json()["client_id"] == "gid2"

    listed = client.get("/auth/provider-configs")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


def test_user_client_access_assignment():
    reset_state()
    u = mk_user("access@example.com", role="agency")
    c = mk_client("Tenant")

    assigned = client.post(
        "/auth/internal/access",
        json={"user_id": u["id"], "client_id": c["id"], "role": "agency"},
    )
    assert assigned.status_code == 200

    listed = client.get(f"/auth/internal/access?user_id={u['id']}")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    reassigned = client.post(
        "/auth/internal/access",
        json={"user_id": u["id"], "client_id": c["id"], "role": "client"},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["role"] == "client"

    listed2 = client.get(f"/auth/internal/access?user_id={u['id']}").json()
    assert listed2["count"] == 1
    assert listed2["items"][0]["role"] == "client"


def test_delete_user_allows_email_recreate():
    reset_state()
    u = mk_user("recreate@example.com", role="agency")

    deleted = client.delete(f"/auth/internal/users/{u['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    recreated = client.post(
        "/auth/internal/users",
        json={"email": "recreate@example.com", "name": "Recreated", "role": "client", "status": "active"},
    )
    assert recreated.status_code == 200


def test_auth_facade_resolve_or_create_and_identity_upsert():
    reset_state()
    # First resolve should create internal user + link identity + session
    first = client.post(
        "/auth/internal/facade/external/resolve",
        json={
            "provider": "google",
            "provider_user_id": "g-777",
            "email": "newuser@example.com",
            "email_verified": True,
            "name": "New User",
            "raw_profile": {"locale": "en"},
            "default_role": "client",
            "issue_session": True,
            "session_ttl_minutes": 30,
        },
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1["user"]["email"] == "newuser@example.com"
    assert body1["identity"]["provider"] == "google"
    assert body1["session"] is not None

    # Second resolve with same provider identity should upsert identity and reuse same user
    second = client.post(
        "/auth/internal/facade/external/resolve",
        json={
            "provider": "google",
            "provider_user_id": "g-777",
            "email": "newuser@example.com",
            "email_verified": False,
            "name": "New User Updated",
            "raw_profile": {"locale": "de"},
            "default_role": "client",
            "issue_session": False,
        },
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2["user"]["id"] == body1["user"]["id"]
    assert body2["identity"]["email_verified"] is False
    assert body2["identity"]["raw_profile"]["locale"] == "de"
    assert body2["session"] is None


def test_auth_facade_resolve_conflict_policy_for_email_merge():
    reset_state()
    u = mk_user("merge@example.com", role="client")

    # New provider identity with existing email should conflict by default.
    conflict = client.post(
        "/auth/internal/facade/external/resolve",
        json={
            "provider": "facebook",
            "provider_user_id": "fb-1",
            "email": "merge@example.com",
            "name": "Merge Candidate",
            "default_role": "client",
            "issue_session": False,
        },
    )
    assert conflict.status_code == 409

    # Explicit allow_email_merge should resolve to existing user.
    merged = client.post(
        "/auth/internal/facade/external/resolve",
        json={
            "provider": "facebook",
            "provider_user_id": "fb-1",
            "email": "merge@example.com",
            "name": "Merge Candidate",
            "allow_email_merge": True,
            "default_role": "client",
            "issue_session": False,
        },
    )
    assert merged.status_code == 200
    assert merged.json()["user"]["id"] == u["id"]


def test_auth_facade_session_context_role_and_tenant_resolution():
    reset_state()
    c1 = mk_client("Tenant-1")
    c2 = mk_client("Tenant-2")

    admin = mk_user("admin@example.com", role="admin")
    agency = mk_user("agency@example.com", role="agency")

    client.post(
        "/auth/internal/access",
        json={"user_id": agency["id"], "client_id": c1["id"], "role": "agency"},
    )

    # Admin context: global access (all clients)
    admin_session = client.post("/auth/internal/sessions/issue", json={"user_id": admin["id"], "ttl_minutes": 60}).json()
    admin_ctx = client.post("/auth/internal/facade/sessions/context", json={"token": admin_session["token"]})
    assert admin_ctx.status_code == 200
    admin_body = admin_ctx.json()
    assert admin_body["valid"] is True
    assert admin_body["role"] == "admin"
    assert admin_body["access_scope"] == "all"
    assert admin_body["global_access"] is True
    assert admin_body["accessible_client_ids"] == []

    # Agency context: assigned access only
    agency_session = client.post("/auth/internal/sessions/issue", json={"user_id": agency["id"], "ttl_minutes": 60}).json()
    agency_ctx = client.post("/auth/internal/facade/sessions/context", json={"token": agency_session["token"]})
    assert agency_ctx.status_code == 200
    agency_body = agency_ctx.json()
    assert agency_body["valid"] is True
    assert agency_body["role"] == "agency"
    assert agency_body["access_scope"] == "assigned"
    assert agency_body["global_access"] is False
    assert agency_body["accessible_client_ids"] == [c1["id"]]


def test_session_validation_expired_and_inactive_user():
    reset_state()
    u = mk_user("expire@example.com", role="client")
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 1})
    assert issued.status_code == 200
    token = issued.json()["token"]

    # Expired
    original_datetime = auth_arch_module.datetime
    class FutureDateTime:
        @staticmethod
        def utcnow():
            return original_datetime.utcnow() + auth_arch_module.timedelta(minutes=2)

        @staticmethod
        def fromisoformat(value):
            return original_datetime.fromisoformat(value)

    try:
        auth_arch_module.datetime = FutureDateTime  # monkeypatch-like for module usage
        expired = client.post("/auth/internal/sessions/validate", json={"token": token})
    finally:
        auth_arch_module.datetime = original_datetime
    assert expired.status_code == 200
    assert expired.json()["valid"] is False
    assert expired.json()["reason"] == "expired"

    # Inactive user
    issued2 = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 60})
    token2 = issued2.json()["token"]
    store = app.state.auth_store
    from uuid import UUID as _UUID
    user_obj = store.users[_UUID(u["id"])]
    store.users[_UUID(u["id"])] = user_obj.model_copy(update={"status": "inactive"})
    inactive = client.post("/auth/internal/sessions/validate", json={"token": token2})
    assert inactive.status_code == 200
    assert inactive.json()["valid"] is False
    assert inactive.json()["reason"] == "user_inactive"


def test_auth_me_and_logout_flow():
    reset_state()
    u = mk_user("me@example.com", role="agency")
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    token = issued.json()["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["id"] == u["id"]
    assert body["session"]["valid"] is True
    assert body["session"]["role"] == "agency"

    logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 200
    assert logout.json()["status"] == "ok"

    me_after = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_after.status_code == 401


def test_auth_session_refresh_extends_expiry():
    reset_state()
    u = mk_user("refresh@example.com", role="agency")
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 1})
    assert issued.status_code == 200
    token = issued.json()["token"]

    me_before = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_before.status_code == 200
    before_exp = me_before.json()["session"]["expires_at"]

    refreshed = client.post("/auth/session/refresh", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "ok"
    after_exp = refreshed.json()["expires_at"]
    assert after_exp
    assert after_exp > before_exp
