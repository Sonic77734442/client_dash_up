from fastapi.testclient import TestClient

from app.main import app


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


def test_member_deactivate_and_remove():
    reset_state()
    admin = mk_user("admin@lifecycle.local", "admin")
    agency_user = mk_user("agency@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("Tenant A", admin_token)
    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )
    client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )

    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert allowed.status_code == 200

    members = client.get(f"/platform/agencies/{agency['id']}/members", headers=auth_header(admin_token)).json()
    member_id = members[0]["id"]

    deactivated = client.post(
        f"/platform/agencies/{agency['id']}/members/{member_id}/deactivate",
        headers=auth_header(admin_token),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"

    denied = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert denied.status_code == 403

    removed = client.delete(
        f"/platform/agencies/{agency['id']}/members/{member_id}",
        headers=auth_header(admin_token),
    )
    assert removed.status_code == 200



def test_revoke_client_access_binding_removes_tenant_access():
    reset_state()
    admin = mk_user("admin2@lifecycle.local", "admin")
    agency_user = mk_user("agency2@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("Tenant B", admin_token)
    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency B", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )
    client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )

    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert allowed.status_code == 200

    bindings = client.get(f"/platform/agencies/{agency['id']}/clients", headers=auth_header(admin_token)).json()
    access_id = bindings[0]["id"]
    revoked = client.delete(
        f"/platform/agencies/{agency['id']}/clients/{access_id}",
        headers=auth_header(admin_token),
    )
    assert revoked.status_code == 200

    denied = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert denied.status_code == 403



def test_invite_revoke_and_resend_flow():
    reset_state()
    admin = mk_user("admin3@lifecycle.local", "admin")
    admin_token = issue_token(admin["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency C", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()

    issued = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "invitee@agency.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(admin_token),
    )
    assert issued.status_code == 200
    invite_id = issued.json()["invite"]["id"]

    revoked = client.post(
        f"/platform/agencies/{agency['id']}/invites/{invite_id}/revoke",
        headers=auth_header(admin_token),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    resent = client.post(
        f"/platform/agencies/{agency['id']}/invites/{invite_id}/resend",
        json={"expires_in_days": 5},
        headers=auth_header(admin_token),
    )
    assert resent.status_code == 200
    assert resent.json()["invite"]["status"] == "pending"
    assert resent.json()["invite"]["id"] != invite_id


def test_delete_agency_detaches_members_and_keeps_users():
    reset_state()
    admin = mk_user("admin4@lifecycle.local", "admin")
    agency_user = mk_user("agency4@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("Tenant C", admin_token)
    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency D", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )
    client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )

    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert allowed.status_code == 200

    deleted = client.delete(
        f"/platform/agencies/{agency['id']}",
        headers=auth_header(admin_token),
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    denied = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert denied.status_code == 403

    users = client.get("/auth/internal/users", headers=auth_header(admin_token))
    assert users.status_code == 200
    detached = next((u for u in users.json()["items"] if u["id"] == agency_user["id"]), None)
    assert detached is not None
    assert detached["role"] == "client"


def test_agency_owner_can_remove_member_but_member_cannot_manage():
    reset_state()
    admin = mk_user("admin5@lifecycle.local", "admin")
    owner_user = mk_user("owner@lifecycle.local", "agency")
    member_user = mk_user("member@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    owner_token = issue_token(owner_user["id"])
    member_token = issue_token(member_user["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency E", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": owner_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )
    add_member = client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": member_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )
    assert add_member.status_code == 200
    member_id = add_member.json()["id"]

    denied = client.delete(
        f"/platform/agencies/{agency['id']}/members/{member_id}",
        headers=auth_header(member_token),
    )
    assert denied.status_code == 403

    removed = client.delete(
        f"/platform/agencies/{agency['id']}/members/{member_id}",
        headers=auth_header(owner_token),
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"


def test_agency_owner_can_manage_invites_but_member_cannot():
    reset_state()
    admin = mk_user("admin6@lifecycle.local", "admin")
    owner_user = mk_user("owner2@lifecycle.local", "agency")
    member_user = mk_user("member2@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    owner_token = issue_token(owner_user["id"])
    member_token = issue_token(member_user["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency F", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": owner_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": member_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )

    denied_issue = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "x@agency.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(member_token),
    )
    assert denied_issue.status_code == 403

    issued = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "x@agency.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(owner_token),
    )
    assert issued.status_code == 200
    invite_id = issued.json()["invite"]["id"]

    denied_revoke = client.post(
        f"/platform/agencies/{agency['id']}/invites/{invite_id}/revoke",
        headers=auth_header(member_token),
    )
    assert denied_revoke.status_code == 403

    revoked = client.post(
        f"/platform/agencies/{agency['id']}/invites/{invite_id}/revoke",
        headers=auth_header(owner_token),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


def test_resend_accepted_invite_creates_new_pending_invite():
    reset_state()
    admin = mk_user("admin7@lifecycle.local", "admin")
    owner_user = mk_user("owner3@lifecycle.local", "agency")
    admin_token = issue_token(admin["id"])
    owner_token = issue_token(owner_user["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Lifecycle Agency G", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": owner_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )

    issued = client.post(
        f"/platform/agencies/{agency['id']}/invites",
        json={"email": "accepted@agency.local", "member_role": "member", "expires_in_days": 7},
        headers=auth_header(owner_token),
    )
    assert issued.status_code == 200
    invite_id = issued.json()["invite"]["id"]
    token = issued.json()["invite_token"]

    accepted = client.post(
        "/auth/invites/accept",
        json={"token": token, "name": "Accepted User", "password": "acceptedPwd123"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["invite"]["status"] == "accepted"

    resent = client.post(
        f"/platform/agencies/{agency['id']}/invites/{invite_id}/resend",
        json={"expires_in_days": 7},
        headers=auth_header(owner_token),
    )
    assert resent.status_code == 200
    assert resent.json()["invite"]["status"] == "pending"
    assert resent.json()["invite"]["id"] != invite_id
