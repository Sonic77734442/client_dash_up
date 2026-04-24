from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def _bootstrap_admin():
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin.connections@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    token = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60}).json()["token"]
    return admin.json(), token


def test_me_integration_connections_agency_scope_visibility_and_disconnect():
    reset_state()
    _, admin_token = _bootstrap_admin()

    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency.connections@test.local", "name": "Agency", "role": "agency", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_user.status_code == 200

    agency_1 = client.post(
        "/platform/agencies",
        json={"name": "Agency 1", "status": "active", "plan": "starter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_1.status_code == 200
    agency_2 = client.post(
        "/platform/agencies",
        json={"name": "Agency 2", "status": "active", "plan": "starter"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert agency_2.status_code == 200

    member = client.post(
        f"/platform/agencies/{agency_1.json()['id']}/members",
        json={"user_id": agency_user.json()["id"], "role": "owner", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert member.status_code == 200

    c1 = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "agency",
            "scope_id": agency_1.json()["id"],
            "connection_key": "google:mcc-agency-1",
            "credentials": {"refresh_token": "rt-a1"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert c1.status_code == 200
    c2 = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "agency",
            "scope_id": agency_2.json()["id"],
            "connection_key": "google:mcc-agency-2",
            "credentials": {"refresh_token": "rt-a2"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert c2.status_code == 200

    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": agency_user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]

    listed = client.get(
        "/me/integration-connections?status=all",
        headers={"Authorization": f"Bearer {agency_token}"},
    )
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["connection_key"] == "google:mcc-agency-1"

    archived = client.delete(
        f"/me/integration-connections/{items[0]['id']}",
        headers={"Authorization": f"Bearer {agency_token}"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


def test_me_integration_connections_client_forbidden():
    reset_state()
    _, admin_token = _bootstrap_admin()

    user = client.post(
        "/auth/internal/users",
        json={"email": "client.connections@test.local", "name": "Client", "role": "client", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert user.status_code == 200
    token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": user.json()["id"], "ttl_minutes": 60},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()["token"]

    listed = client.get("/me/integration-connections?status=all", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 403


def test_me_integration_connections_admin_sees_all():
    reset_state()
    _, admin_token = _bootstrap_admin()

    g = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "google",
            "scope_type": "global",
            "connection_key": "google:global-1",
            "credentials": {"refresh_token": "rt-global"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert g.status_code == 200
    m = client.post(
        "/platform/integration-credentials",
        json={
            "provider": "meta",
            "scope_type": "global",
            "connection_key": "meta:global-1",
            "credentials": {"access_token": "at-global"},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert m.status_code == 200

    listed = client.get("/me/integration-connections?status=all", headers={"Authorization": f"Bearer {admin_token}"})
    assert listed.status_code == 200
    assert listed.json()["count"] >= 2
