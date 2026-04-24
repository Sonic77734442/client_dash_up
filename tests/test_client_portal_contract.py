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


def test_multitenant_contract_agency_and_client_data_isolation():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200

    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin-multi@test.local", "name": "Admin", "role": "admin", "status": "active"},
    ).json()
    admin_token = client.post("/auth/internal/sessions/issue", json={"user_id": admin["id"], "ttl_minutes": 60}).json()["token"]
    admin_headers = auth_header(admin_token)

    c1 = client.post(
        "/clients",
        json={"name": "Tenant Alpha", "status": "active", "default_currency": "USD"},
        headers=admin_headers,
    ).json()
    c2 = client.post(
        "/clients",
        json={"name": "Tenant Beta", "status": "active", "default_currency": "USD"},
        headers=admin_headers,
    ).json()
    c3 = client.post(
        "/clients",
        json={"name": "Tenant Hidden", "status": "active", "default_currency": "USD"},
        headers=admin_headers,
    ).json()

    a1 = client.post(
        "/ad-accounts",
        json={
            "client_id": c1["id"],
            "platform": "meta",
            "external_account_id": "shared-ext-001",
            "name": "Alpha Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    ).json()
    a2 = client.post(
        "/ad-accounts",
        json={
            "client_id": c2["id"],
            "platform": "meta",
            "external_account_id": "shared-ext-001",
            "name": "Beta Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    ).json()
    a3 = client.post(
        "/ad-accounts",
        json={
            "client_id": c3["id"],
            "platform": "meta",
            "external_account_id": "hidden-ext-001",
            "name": "Hidden Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    ).json()

    assert client.post(
        "/budgets",
        json={
            "client_id": c1["id"],
            "scope": "account",
            "account_id": a1["id"],
            "amount": "1000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        },
        headers=admin_headers,
    ).status_code == 200
    assert client.post(
        "/budgets",
        json={
            "client_id": c2["id"],
            "scope": "account",
            "account_id": a2["id"],
            "amount": "2000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        },
        headers=admin_headers,
    ).status_code == 200

    assert client.post(
        "/ad-stats/ingest",
        json={
            "rows": [
                {
                    "ad_account_id": a1["id"],
                    "date": "2026-04-01",
                    "platform": "meta",
                    "impressions": 1000,
                    "clicks": 100,
                    "spend": "100.00",
                    "conversions": "5.00",
                },
                {
                    "ad_account_id": a2["id"],
                    "date": "2026-04-01",
                    "platform": "meta",
                    "impressions": 2000,
                    "clicks": 200,
                    "spend": "200.00",
                    "conversions": "10.00",
                },
                {
                    "ad_account_id": a3["id"],
                    "date": "2026-04-01",
                    "platform": "meta",
                    "impressions": 3000,
                    "clicks": 300,
                    "spend": "300.00",
                    "conversions": "15.00",
                },
            ]
        },
        headers=admin_headers,
    ).status_code == 200

    agency_user = client.post(
        "/auth/internal/users",
        json={"email": "agency-multi@test.local", "name": "Agency User", "role": "agency", "status": "active"},
    ).json()
    assert client.post(
        "/auth/internal/access",
        json={"user_id": agency_user["id"], "client_id": c1["id"], "role": "agency"},
    ).status_code == 200
    assert client.post(
        "/auth/internal/access",
        json={"user_id": agency_user["id"], "client_id": c2["id"], "role": "agency"},
    ).status_code == 200
    agency_token = client.post(
        "/auth/internal/sessions/issue",
        json={"user_id": agency_user["id"], "ttl_minutes": 60},
    ).json()["token"]

    agency_accounts = client.get("/ad-accounts?status=active", headers=auth_header(agency_token))
    assert agency_accounts.status_code == 200
    agency_account_client_ids = {x["client_id"] for x in agency_accounts.json()["items"]}
    assert agency_account_client_ids == {c1["id"], c2["id"]}

    agency_budgets = client.get("/budgets?status=active", headers=auth_header(agency_token))
    assert agency_budgets.status_code == 200
    agency_budget_client_ids = {x["client_id"] for x in agency_budgets.json()["items"]}
    assert agency_budget_client_ids == {c1["id"], c2["id"]}

    missing_scope = client.get(
        "/insights/overview?date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert missing_scope.status_code == 403

    agency_c1 = client.get(
        f"/insights/overview?client_id={c1['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert agency_c1.status_code == 200
    assert agency_c1.json()["spend_summary"]["spend"] == 100.0

    agency_c3 = client.get(
        f"/insights/overview?client_id={c3['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert agency_c3.status_code == 403

    c1_user = client.post(
        "/auth/internal/users",
        json={"email": "alpha-client@test.local", "name": "Alpha Client", "role": "client", "status": "active"},
    ).json()
    c2_user = client.post(
        "/auth/internal/users",
        json={"email": "beta-client@test.local", "name": "Beta Client", "role": "client", "status": "active"},
    ).json()
    assert client.post(
        "/auth/internal/access",
        json={"user_id": c1_user["id"], "client_id": c1["id"], "role": "client"},
    ).status_code == 200
    assert client.post(
        "/auth/internal/access",
        json={"user_id": c2_user["id"], "client_id": c2["id"], "role": "client"},
    ).status_code == 200
    c1_token = client.post("/auth/internal/sessions/issue", json={"user_id": c1_user["id"], "ttl_minutes": 60}).json()["token"]
    c2_token = client.post("/auth/internal/sessions/issue", json={"user_id": c2_user["id"], "ttl_minutes": 60}).json()["token"]

    c1_overview = client.get(
        f"/insights/overview?client_id={c1['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(c1_token),
    )
    assert c1_overview.status_code == 200
    assert c1_overview.json()["spend_summary"]["spend"] == 100.0

    c1_cross = client.get(
        f"/insights/overview?client_id={c2['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(c1_token),
    )
    assert c1_cross.status_code == 403

    c2_overview = client.get(
        f"/insights/overview?client_id={c2['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(c2_token),
    )
    assert c2_overview.status_code == 200
    assert c2_overview.json()["spend_summary"]["spend"] == 200.0
