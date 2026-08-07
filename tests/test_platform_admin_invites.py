from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import _revoke_client_invite, app, settings
from app.services.auth_arch import SqliteAuthStore
from app.services.auth_facade import AuthFacadeService
from app.services.clients import SqliteClientStore
from app.services.platform_admin import SqlitePlatformAdminStore


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def mk_user(email: str, role: str):
    res = client.post("/auth/internal/users", json={"email": email, "name": role, "role": role, "status": "active"})
    assert res.status_code == 200
    return res.json()


def issue_token(user_id: str) -> str:
    res = client.post("/auth/internal/sessions/issue", json={"user_id": user_id, "ttl_minutes": 60})
    assert res.status_code == 200
    return res.json()["token"]


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def mk_client(name: str, admin_token: str):
    res = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
        headers=auth_header(admin_token),
    )
    assert res.status_code == 200
    return res.json()


@pytest.fixture
def sqlite_app_state(tmp_path):
    db_path = str(tmp_path / "platform-invites.db")
    old_state = {
        "client_store": app.state.client_store,
        "auth_store": app.state.auth_store,
        "platform_admin_store": app.state.platform_admin_store,
        "auth_facade": app.state.auth_facade,
    }
    old_db_path = settings.budgets_db_path
    auth_store = SqliteAuthStore(db_path)
    object.__setattr__(settings, "budgets_db_path", db_path)
    app.state.client_store = SqliteClientStore(db_path)
    app.state.auth_store = auth_store
    app.state.platform_admin_store = SqlitePlatformAdminStore(db_path, auth_store)
    app.state.auth_facade = AuthFacadeService(auth_store=auth_store)
    client.headers.pop("Authorization", None)
    client.cookies.clear()

    try:
        yield auth_store
    finally:
        app.state.client_store = old_state["client_store"]
        app.state.auth_store = old_state["auth_store"]
        app.state.platform_admin_store = old_state["platform_admin_store"]
        app.state.auth_facade = old_state["auth_facade"]
        object.__setattr__(settings, "budgets_db_path", old_db_path)
        client.headers.pop("Authorization", None)
        client.cookies.clear()


def test_issue_and_accept_agency_invite_grants_access():
    reset_state()
    admin = mk_user("admin@invite.local", "admin")
    admin_token = issue_token(admin["id"])

    tenant = mk_client("Invite Tenant", admin_token)
    agency = client.post(
        "/platform/agencies",
        json={"name": "Invite Ops", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()

    bind = client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )
    assert bind.status_code == 200

    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "member@agency.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    token = issue.json()["invite_token"]

    accepted = client.post("/auth/invites/accept", json={"token": token, "name": "Agency Member"})
    assert accepted.status_code == 200
    assert "ops_session=" in accepted.headers.get("set-cookie", "")
    body = accepted.json()
    assert body["invite"]["status"] == "accepted"
    assert body["user"]["role"] == "agency"
    assert "token" not in body["session"]

    allowed = client.get(f"/clients/{tenant['id']}")
    assert allowed.status_code == 200

    reused = client.post("/auth/invites/accept", json={"token": token})
    assert reused.status_code == 409


def test_manager_cannot_invite_resend_or_revoke_owner_but_owner_and_admin_can():
    reset_state()
    admin = mk_user("invite-role-admin@invite.local", "admin")
    owner = mk_user("invite-role-owner@invite.local", "agency")
    manager = mk_user("invite-role-manager@invite.local", "agency")
    admin_token = issue_token(admin["id"])
    owner_token = issue_token(owner["id"])
    manager_token = issue_token(manager["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Invite role guard", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    for user, role in ((owner, "owner"), (manager, "manager")):
        added = client.post(
            f"/platform/agencies/{agency['id']}/members",
            json={"user_id": user["id"], "role": role, "status": "active"},
            headers=auth_header(admin_token),
        )
        assert added.status_code == 200

    denied = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "manager-created-owner@invite.local", "member_role": "owner", "expires_in_days": 7},
        headers=auth_header(manager_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "agency_owner_protected"

    manager_invite = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "manager-created-manager@invite.local", "member_role": "manager", "expires_in_days": 7},
        headers=auth_header(manager_token),
    )
    assert manager_invite.status_code == 200

    owner_invite = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "owner-created-owner@invite.local", "member_role": "owner", "expires_in_days": 7},
        headers=auth_header(owner_token),
    )
    assert owner_invite.status_code == 200

    denied_resend = client.post(
        f"/platform/agencies/{agency['id']}/invites/{owner_invite.json()['invite']['id']}/resend",
        json={"expires_in_days": 7},
        headers=auth_header(manager_token),
    )
    assert denied_resend.status_code == 403
    assert denied_resend.json()["error"]["code"] == "agency_owner_protected"

    denied_revoke = client.post(
        f"/platform/agencies/{agency['id']}/invites/{owner_invite.json()['invite']['id']}/revoke",
        headers=auth_header(manager_token),
    )
    assert denied_revoke.status_code == 403
    assert denied_revoke.json()["error"]["code"] == "agency_owner_protected"

    admin_invite = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "admin-created-owner@invite.local", "member_role": "owner", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert admin_invite.status_code == 200


def test_new_user_can_accept_invite_with_stale_session_cookie():
    reset_state()
    admin = mk_user("stale-cookie-admin@invite.local", "admin")
    admin_token = issue_token(admin["id"])
    agency = client.post(
        "/platform/agencies",
        json={"name": "Stale Cookie Invite", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "new-with-stale-cookie@invite.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    client.cookies.set(settings.auth_cookie_name, "expired-or-invalid-session")

    accepted = client.post(
        "/auth/invites/accept",
        json={"token": issue.json()["invite_token"], "name": "New Agency User"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["email"] == "new-with-stale-cookie@invite.local"
    assert accepted.json()["user"]["role"] == "agency"


def test_invite_accept_conflicts_with_existing_client_role_email():
    reset_state()
    admin = mk_user("admin2@invite.local", "admin")
    mk_user("existing@invite.local", "client")
    admin_token = issue_token(admin["id"])

    agency_res = client.post(
        "/platform/agencies",
        json={"name": "Conflict Invite", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    )
    assert agency_res.status_code == 200
    agency = agency_res.json()

    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "existing@invite.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200

    denied = client.post("/auth/invites/accept", json={"token": issue.json()["invite_token"]})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "user_role_conflict"


def test_agency_invite_cannot_take_over_existing_admin_account():
    reset_state()
    admin = mk_user("admin-takeover@invite.local", "admin")
    admin_token = issue_token(admin["id"])
    original_password = "OriginalAdminPassword1"
    attacker_password = "AttackerPassword123"
    admin_id = UUID(admin["id"])
    app.state.auth_store.set_password(admin_id, original_password)

    agency = client.post(
        "/platform/agencies",
        json={"name": "Admin Takeover Guard", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": admin["email"], "member_role": "owner", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    payload = {
        "token": issue.json()["invite_token"],
        "name": "Taken Over Admin",
        "password": attacker_password,
    }

    denied_public = client.post("/auth/invites/accept", json=payload)
    assert denied_public.status_code == 409
    assert denied_public.json()["error"]["code"] == "user_role_conflict"
    denied_as_admin = client.post("/auth/invites/accept", json=payload, headers=auth_header(admin_token))
    assert denied_as_admin.status_code == 409
    assert denied_as_admin.json()["error"]["code"] == "user_role_conflict"

    unchanged = app.state.auth_store.get_user(admin_id)
    assert unchanged.name == "admin"
    assert unchanged.role == "admin"
    assert unchanged.status == "active"
    assert app.state.auth_store.authenticate_password(admin["email"], original_password) is not None
    assert app.state.auth_store.authenticate_password(admin["email"], attacker_password) is None
    invites = client.get(
        f"/platform/agencies/{agency['id']}/invites?status=all",
        headers=auth_header(admin_token),
    )
    assert invites.status_code == 200
    assert invites.json()[0]["status"] == "pending"


def test_existing_agency_user_must_authenticate_and_profile_is_immutable(sqlite_app_state):
    auth_store = sqlite_app_state
    admin = mk_user("agency-own-auth-admin@invite.local", "admin")
    agency_user = mk_user("existing-agency@invite.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])
    original_password = "OriginalAgencyPassword1"
    replacement_password = "ReplacementPassword1"
    auth_store.set_password(agency_user["id"], original_password)

    agency = client.post(
        "/platform/agencies",
        json={"name": "Existing Agency Auth", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": agency_user["email"], "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    payload = {
        "token": issue.json()["invite_token"],
        "name": "Changed Agency Name",
        "password": replacement_password,
    }

    client.cookies.set(settings.auth_cookie_name, "expired-or-invalid-session")
    denied = client.post("/auth/invites/accept", json=payload)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "existing_user_auth_required"

    accepted = client.post("/auth/invites/accept", json=payload, headers=auth_header(agency_token))
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"
    unchanged = auth_store.get_user(agency_user["id"])
    assert unchanged.name == "agency"
    assert unchanged.role == "agency"
    assert unchanged.status == "active"
    assert auth_store.authenticate_password(agency_user["email"], original_password) is not None
    assert auth_store.authenticate_password(agency_user["email"], replacement_password) is None


def test_client_invite_cannot_take_over_existing_admin_account(sqlite_app_state):
    auth_store = sqlite_app_state
    admin = mk_user("client-admin-takeover@invite.local", "admin")
    admin_token = issue_token(admin["id"])
    original_password = "OriginalClientAdmin1"
    attacker_password = "AttackerClientAdmin1"
    auth_store.set_password(admin["id"], original_password)
    tenant = mk_client("Client Admin Takeover Guard", admin_token)
    issue = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": admin["email"], "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    payload = {
        "token": issue.json()["invite_token"],
        "name": "Taken Over Client Admin",
        "password": attacker_password,
    }

    denied_public = client.post("/auth/invites/accept", json=payload)
    assert denied_public.status_code == 409
    assert denied_public.json()["error"]["code"] == "user_role_conflict"
    denied_as_admin = client.post("/auth/invites/accept", json=payload, headers=auth_header(admin_token))
    assert denied_as_admin.status_code == 409
    assert denied_as_admin.json()["error"]["code"] == "user_role_conflict"

    unchanged = auth_store.get_user(admin["id"])
    assert unchanged.name == "admin"
    assert unchanged.role == "admin"
    assert unchanged.status == "active"
    assert auth_store.authenticate_password(admin["email"], original_password) is not None
    assert auth_store.authenticate_password(admin["email"], attacker_password) is None
    invites = client.get(f"/clients/{tenant['id']}/invites?status=all", headers=auth_header(admin_token))
    assert invites.status_code == 200
    assert invites.json()[0]["status"] == "pending"


def test_existing_client_user_must_authenticate_and_profile_is_immutable(sqlite_app_state):
    auth_store = sqlite_app_state
    admin = mk_user("client-own-auth-admin@invite.local", "admin")
    client_user = mk_user("existing-client@invite.local", "client")
    admin_token = issue_token(admin["id"])
    client_token = issue_token(client_user["id"])
    original_password = "OriginalClientPassword1"
    replacement_password = "ReplacementClientPassword1"
    auth_store.set_password(client_user["id"], original_password)
    tenant = mk_client("Existing Client Auth", admin_token)
    issue = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": client_user["email"], "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issue.status_code == 200
    payload = {
        "token": issue.json()["invite_token"],
        "name": "Changed Client Name",
        "password": replacement_password,
    }

    denied = client.post("/auth/invites/accept", json=payload)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "existing_user_auth_required"

    accepted = client.post("/auth/invites/accept", json=payload, headers=auth_header(client_token))
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"
    unchanged = auth_store.get_user(client_user["id"])
    assert unchanged.name == "client"
    assert unchanged.role == "client"
    assert unchanged.status == "active"
    assert auth_store.authenticate_password(client_user["email"], original_password) is not None
    assert auth_store.authenticate_password(client_user["email"], replacement_password) is None
    assert client.get(f"/clients/{tenant['id']}", headers=auth_header(client_token)).status_code == 200


def test_concurrent_revoke_wins_before_agency_invite_provisioning(sqlite_app_state, monkeypatch):
    auth_store = sqlite_app_state
    admin = mk_user("agency-race-admin@invite.local", "admin")
    invited = mk_user("agency-race-user@invite.local", "agency")
    admin_token = issue_token(admin["id"])
    invited_token = issue_token(invited["id"])
    agency = client.post(
        "/platform/agencies",
        json={"name": "Agency Revoke Race", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": invited["email"], "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    ).json()

    original_find = auth_store.find_user_by_email
    fired = False

    def revoke_then_find(email):
        nonlocal fired
        if not fired:
            fired = True
            app.state.platform_admin_store.revoke_invite(UUID(agency["id"]), UUID(issue["invite"]["id"]))
        return original_find(email)

    monkeypatch.setattr(auth_store, "find_user_by_email", revoke_then_find)
    accepted = client.post(
        "/auth/invites/accept",
        json={"token": issue["invite_token"]},
        headers=auth_header(invited_token),
    )

    assert accepted.status_code == 409
    assert app.state.platform_admin_store.list_members(UUID(agency["id"])) == []
    assert auth_store.list_client_access(user_id=UUID(invited["id"])) == []


def test_concurrent_revoke_wins_before_client_invite_access(sqlite_app_state, monkeypatch):
    auth_store = sqlite_app_state
    admin = mk_user("client-race-admin@invite.local", "admin")
    invited = mk_user("client-race-user@invite.local", "client")
    admin_token = issue_token(admin["id"])
    invited_token = issue_token(invited["id"])
    tenant = mk_client("Client Revoke Race", admin_token)
    issue = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": invited["email"], "expires_in_days": 7},
        headers=auth_header(admin_token),
    ).json()

    original_find = auth_store.find_user_by_email
    fired = False

    def revoke_then_find(email):
        nonlocal fired
        if not fired:
            fired = True
            _revoke_client_invite(client_id=UUID(tenant["id"]), invite_id=UUID(issue["invite"]["id"]))
        return original_find(email)

    monkeypatch.setattr(auth_store, "find_user_by_email", revoke_then_find)
    accepted = client.post(
        "/auth/invites/accept",
        json={"token": issue["invite_token"]},
        headers=auth_header(invited_token),
    )

    assert accepted.status_code == 409
    assert auth_store.list_client_access(user_id=UUID(invited["id"])) == []


def test_concurrent_revoke_does_not_leave_new_client_user_orphan(sqlite_app_state, monkeypatch):
    auth_store = sqlite_app_state
    admin = mk_user("new-client-race-admin@invite.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client("New Client User Revoke Race", admin_token)
    invited_email = "new-client-race-user@invite.local"
    issue = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": invited_email, "expires_in_days": 7},
        headers=auth_header(admin_token),
    ).json()

    original_find = auth_store.find_user_by_email
    fired = False

    def revoke_then_find(email):
        nonlocal fired
        if not fired:
            fired = True
            _revoke_client_invite(client_id=UUID(tenant["id"]), invite_id=UUID(issue["invite"]["id"]))
        return original_find(email)

    monkeypatch.setattr(auth_store, "find_user_by_email", revoke_then_find)
    accepted = client.post(
        "/auth/invites/accept",
        json={"token": issue["invite_token"], "password": "SafePassword123"},
    )

    assert accepted.status_code == 409
    assert original_find(invited_email) is None


def test_client_invite_provisioning_failure_rolls_back_claim_and_access(sqlite_app_state, monkeypatch):
    auth_store = sqlite_app_state
    admin = mk_user("provision-race-admin@invite.local", "admin")
    invited = mk_user("provision-race-user@invite.local", "client")
    admin_token = issue_token(admin["id"])
    invited_token = issue_token(invited["id"])
    tenant = mk_client("Provisioning Rollback", admin_token)
    issue = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": invited["email"], "expires_in_days": 7},
        headers=auth_header(admin_token),
    ).json()

    monkeypatch.setattr(auth_store, "issue_session", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    accepted = client.post(
        "/auth/invites/accept",
        json={"token": issue["invite_token"]},
        headers=auth_header(invited_token),
    )

    assert accepted.status_code == 503
    assert accepted.json()["error"]["code"] == "invite_provisioning_failed"
    assert auth_store.list_client_access(user_id=UUID(invited["id"])) == []
    invites = client.get(
        f"/clients/{tenant['id']}/invites?status=all",
        headers=auth_header(admin_token),
    )
    assert invites.status_code == 200
    assert invites.json()[0]["status"] == "pending"


def test_agency_client_invites_can_be_disabled_per_agency():
    reset_state()
    admin = mk_user("admin3@invite.local", "admin")
    agency_user = mk_user("agency-client-invites@invite.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("No Portal Tenant", admin_token)
    agency_res = client.post(
        "/platform/agencies",
        json={
            "name": "No Portal Agency",
            "status": "active",
            "plan": "starter",
            "allow_client_invites": False,
        },
        headers=auth_header(admin_token),
    )
    assert agency_res.status_code == 200
    agency = agency_res.json()
    assert agency["allow_client_invites"] is False

    member = client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )
    assert member.status_code == 200
    bind = client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )
    assert bind.status_code == 200

    denied = client.post(
        f"/clients/{tenant['id']}/invites",
        json={"email": "client@tenant.local", "expires_in_days": 7},
        headers=auth_header(agency_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "client_invites_disabled"

    enabled = client.patch(
        f"/platform/agencies/{agency['id']}",
        json={"allow_client_invites": True},
        headers=auth_header(admin_token),
    )
    assert enabled.status_code == 200
    assert enabled.json()["allow_client_invites"] is True


def test_archiving_client_revokes_pending_invites_and_suspends_client_session_scope(tmp_path):
    db_path = str(tmp_path / "client-invite-lifecycle.db")
    old_state = {
        "client_store": app.state.client_store,
        "auth_store": app.state.auth_store,
        "platform_admin_store": app.state.platform_admin_store,
        "auth_facade": app.state.auth_facade,
    }
    old_db_path = settings.budgets_db_path
    auth_store = SqliteAuthStore(db_path)
    object.__setattr__(settings, "budgets_db_path", db_path)
    app.state.client_store = SqliteClientStore(db_path)
    app.state.auth_store = auth_store
    app.state.platform_admin_store = SqlitePlatformAdminStore(db_path, auth_store)
    app.state.auth_facade = AuthFacadeService(auth_store=auth_store)
    client.headers.pop("Authorization", None)
    client.cookies.clear()

    try:
        admin = mk_user("archive-admin@invite.local", "admin")
        admin_token = issue_token(admin["id"])
        tenant = mk_client("Archived Invite Tenant", admin_token)

        accepted_issue = client.post(
            f"/clients/{tenant['id']}/invites",
            json={"email": "accepted-client@invite.local", "expires_in_days": 7},
            headers=auth_header(admin_token),
        )
        assert accepted_issue.status_code == 200
        accepted = client.post(
            "/auth/invites/accept",
            json={"token": accepted_issue.json()["invite_token"], "name": "Accepted Client"},
        )
        assert accepted.status_code == 200
        assert "token" not in accepted.json()["session"]
        assert client.get(f"/clients/{tenant['id']}").status_code == 200

        pending_issue = client.post(
            f"/clients/{tenant['id']}/invites",
            json={"email": "pending-client@invite.local", "expires_in_days": 7},
            headers=auth_header(admin_token),
        )
        assert pending_issue.status_code == 200

        archived = client.delete(f"/clients/{tenant['id']}", headers=auth_header(admin_token))
        assert archived.status_code == 200

        revoked_accept = client.post(
            "/auth/invites/accept",
            json={"token": pending_issue.json()["invite_token"], "name": "Must Not Join"},
        )
        assert revoked_accept.status_code == 400
        assert revoked_accept.json()["error"]["code"] == "invite_revoked"

        suspended_access = client.get(f"/clients/{tenant['id']}")
        assert suspended_access.status_code == 403

        new_invite = client.post(
            f"/clients/{tenant['id']}/invites",
            json={"email": "new-client@invite.local", "expires_in_days": 7},
            headers=auth_header(admin_token),
        )
        assert new_invite.status_code == 409
        assert new_invite.json()["error"]["code"] == "client_not_active"
    finally:
        app.state.client_store = old_state["client_store"]
        app.state.auth_store = old_state["auth_store"]
        app.state.platform_admin_store = old_state["platform_admin_store"]
        app.state.auth_facade = old_state["auth_facade"]
        object.__setattr__(settings, "budgets_db_path", old_db_path)
        client.cookies.clear()
