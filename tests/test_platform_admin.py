from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)


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


def test_platform_agencies_admin_only():
    reset_state()
    admin = mk_user("admin@platform.local", "admin")
    agency_user = mk_user("agency@platform.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    created = client.post(
        "/platform/agencies",
        json={"name": "North Star Agency", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    )
    assert created.status_code == 200

    denied = client.post(
        "/platform/agencies",
        json={"name": "Denied", "status": "active", "plan": "starter"},
        headers=auth_header(agency_token),
    )
    assert denied.status_code == 403


def test_agency_assignment_grants_client_access_to_active_members():
    reset_state()
    admin = mk_user("admin@platform.local", "admin")
    agency_user = mk_user("agency@platform.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("Acme Tenant", admin_token)

    agency = client.post(
        "/platform/agencies",
        json={"name": "Blue Horizon", "status": "active", "plan": "growth"},
        headers=auth_header(admin_token),
    ).json()

    m = client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )
    assert m.status_code == 200

    bind = client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )
    assert bind.status_code == 200

    # access is materialized to user_client_access, so agency user can read this tenant.
    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert allowed.status_code == 200


def test_new_active_member_gets_backfilled_access_from_existing_agency_bindings():
    reset_state()
    admin = mk_user("admin@platform.local", "admin")
    agency_user = mk_user("agency2@platform.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    tenant = mk_client("Nova Tenant", admin_token)

    agency = client.post(
        "/platform/agencies",
        json={"name": "Orbit Agency", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()

    bind = client.post(
        f"/platform/agencies/{agency['id']}/clients",
        json={"client_id": tenant["id"]},
        headers=auth_header(admin_token),
    )
    assert bind.status_code == 200

    # member added after binding should receive access backfill.
    m = client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "member", "status": "active"},
        headers=auth_header(admin_token),
    )
    assert m.status_code == 200

    allowed = client.get(f"/clients/{tenant['id']}", headers=auth_header(agency_token))
    assert allowed.status_code == 200


def test_agency_user_can_list_own_agencies_and_members():
    reset_state()
    admin = mk_user("admin3@platform.local", "admin")
    agency_user = mk_user("agency3@platform.local", "agency")
    admin_token = issue_token(admin["id"])
    agency_token = issue_token(agency_user["id"])

    agency = client.post(
        "/platform/agencies",
        json={"name": "Nebula Agency", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    member = client.post(
        f"/platform/agencies/{agency['id']}/members",
        json={"user_id": agency_user["id"], "role": "owner", "status": "active"},
        headers=auth_header(admin_token),
    )
    assert member.status_code == 200

    listed = client.get("/platform/agencies?status=all", headers=auth_header(agency_token))
    assert listed.status_code == 200
    assert any(x["id"] == agency["id"] for x in listed.json()["items"])

    members = client.get(f"/platform/agencies/{agency['id']}/members", headers=auth_header(agency_token))
    assert members.status_code == 200
    assert any(x["user_id"] == agency_user["id"] for x in members.json())
