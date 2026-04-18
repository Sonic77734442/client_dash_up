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
    session_token = body["session"]["token"]

    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(session_token))
    assert allowed.status_code == 200

    reused = client.post("/auth/invites/accept", json={"token": token})
    assert reused.status_code == 409


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
