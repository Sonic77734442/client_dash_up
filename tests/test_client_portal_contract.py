from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_client_session_context_and_scoped_overview_flow():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200

    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-portal@test.local", "name": "Admin", "role": "admin", "status": "active"},
    ).json()
    admin_token = client.post("/auth/internal/sessions/issue", json={"user_id": admin["id"], "ttl_minutes": 60}).json()["token"]

    c1 = client.post(
        "/clients",
        json={"name": "Tenant One", "status": "active", "default_currency": "USD"},
        headers=auth_header(admin_token),
    ).json()
    c2 = client.post(
        "/clients",
        json={"name": "Tenant Two", "status": "active", "default_currency": "USD"},
        headers=auth_header(admin_token),
    ).json()

    a1 = client.post(
        "/ad-accounts",
        json={
            "client_id": c1["id"],
            "platform": "meta",
            "external_account_id": "portal-meta-1",
            "name": "Portal Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=auth_header(admin_token),
    ).json()

    ingest = {
        "rows": [
            {
                "ad_account_id": a1["id"],
                "date": "2026-04-01",
                "platform": "meta",
                "impressions": 1000,
                "clicks": 100,
                "spend": "100.00",
                "conversions": "5.00",
            }
        ]
    }
    assert client.post("/ad-stats/ingest", json=ingest, headers=auth_header(admin_token)).status_code == 200

    user = client.post(
        "/auth/internal/users",
        json={"email": "client-portal@test.local", "name": "Client User", "role": "client", "status": "active"},
    ).json()
    assert client.post(
        "/auth/internal/access",
        json={"user_id": user["id"], "client_id": c1["id"], "role": "client"},
    ).status_code == 200

    client_token = client.post("/auth/internal/sessions/issue", json={"user_id": user["id"], "ttl_minutes": 60}).json()["token"]

    ctx = client.post("/auth/internal/facade/sessions/context", json={"token": client_token})
    assert ctx.status_code == 200
    body = ctx.json()
    assert body["valid"] is True
    assert body["role"] == "client"
    assert body["global_access"] is False
    assert body["accessible_client_ids"] == [c1["id"]]

    own = client.get(
        f"/insights/overview?client_id={c1['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(client_token),
    )
    assert own.status_code == 200

    cross = client.get(
        f"/insights/overview?client_id={c2['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(client_token),
    )
    assert cross.status_code == 403
