from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AgencyClientAccessCreate, AgencyCreate, AgencyMemberCreate


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


def mk_client(name: str, token: str):
    res = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
        headers=auth_header(token),
    )
    assert res.status_code == 200
    return res.json()


def mk_account(client_id: str, external: str, token: str):
    res = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": "meta",
            "external_account_id": external,
            "name": f"meta-{external}",
            "currency": "USD",
            "status": "active",
        },
        headers=auth_header(token),
    )
    assert res.status_code == 200
    return res.json()


def assign_access(user_id: str, client_id: str, role: str):
    if role == "agency":
        agency = app.state.platform_admin_store.create_agency(
            AgencyCreate(name=f"ACL agency {uuid4()}", status="active")
        )
        app.state.platform_admin_store.upsert_member(
            agency.id,
            AgencyMemberCreate(user_id=UUID(user_id), role="member", status="active"),
        )
        app.state.platform_admin_store.assign_client(
            agency.id,
            AgencyClientAccessCreate(client_id=UUID(client_id)),
        )
        return
    res = client.post("/auth/internal/access", json={"user_id": user_id, "client_id": client_id, "role": role})
    assert res.status_code == 200


def test_admin_has_full_access():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    c2 = mk_client("Tenant 2", admin_token)

    listed = client.get("/clients?status=all", headers=auth_header(admin_token))
    assert listed.status_code == 200
    ids = {x["id"] for x in listed.json()["items"]}
    assert {c1["id"], c2["id"]}.issubset(ids)

    one = client.get(f"/clients/{c2['id']}", headers=auth_header(admin_token))
    assert one.status_code == 200


def test_agency_access_restricted_to_assigned_clients():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    c2 = mk_client("Tenant 2", admin_token)

    agency = mk_user("agency@acl.local", "agency")
    agency_token = issue_token(agency["id"])
    assign_access(agency["id"], c1["id"], "agency")

    listed = client.get("/clients?status=all", headers=auth_header(agency_token))
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()["items"]] == [c1["id"]]

    denied = client.get(f"/clients/{c2['id']}", headers=auth_header(agency_token))
    assert denied.status_code == 403


def test_client_role_access_restricted_to_assigned_clients():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    c2 = mk_client("Tenant 2", admin_token)
    a1 = mk_account(c1["id"], "acc-1", admin_token)
    mk_account(c2["id"], "acc-2", admin_token)

    user = mk_user("client@acl.local", "client")
    user_token = issue_token(user["id"])
    assign_access(user["id"], c1["id"], "client")

    listed = client.get("/ad-accounts?status=all", headers=auth_header(user_token))
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()["items"]] == [a1["id"]]


def test_forbidden_cross_tenant_access():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    c2 = mk_client("Tenant 2", admin_token)
    a1 = mk_account(c1["id"], "acc-1", admin_token)
    a2 = mk_account(c2["id"], "acc-2", admin_token)

    ingest_payload = {
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
        ]
    }
    assert client.post("/ad-stats/ingest", json=ingest_payload, headers=auth_header(admin_token)).status_code == 200

    agency = mk_user("agency@acl.local", "agency")
    agency_token = issue_token(agency["id"])
    assign_access(agency["id"], c1["id"], "agency")

    denied = client.get(
        f"/insights/overview?client_id={c2['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert denied.status_code == 403

    allowed = client.get(
        f"/insights/overview?client_id={c1['id']}&date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert allowed.status_code == 200
    assert allowed.json()["scope"]["client_id"] == c1["id"]


def test_account_under_disallowed_client_returns_403():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    c2 = mk_client("Tenant 2", admin_token)
    mk_account(c1["id"], "acc-1", admin_token)
    a2 = mk_account(c2["id"], "acc-2", admin_token)

    agency = mk_user("agency@acl.local", "agency")
    agency_token = issue_token(agency["id"])
    assign_access(agency["id"], c1["id"], "agency")

    denied = client.get(f"/ad-accounts/{a2['id']}", headers=auth_header(agency_token))
    assert denied.status_code == 403


def test_provider_insights_endpoints_are_admin_only():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    agency = mk_user("agency@acl.local", "agency")
    agency_token = issue_token(agency["id"])

    admin_ok = client.get(
        "/meta/insights?date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(admin_token),
    )
    assert admin_ok.status_code in (200, 500)

    denied = client.get(
        "/meta/insights?date_from=2026-04-01&date_to=2026-04-30",
        headers=auth_header(agency_token),
    )
    assert denied.status_code == 403


def test_discovery_and_sync_are_forbidden_for_client_role():
    reset_state()
    admin = mk_user("admin@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    c1 = mk_client("Tenant 1", admin_token)
    a1 = mk_account(c1["id"], "acc-1", admin_token)

    user = mk_user("client-sync@acl.local", "client")
    user_token = issue_token(user["id"])
    assign_access(user["id"], c1["id"], "client")

    discover = client.post(
        "/ad-accounts/discover",
        json={"provider": "meta", "client_id": c1["id"]},
        headers=auth_header(user_token),
    )
    assert discover.status_code == 403

    sync = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [a1["id"]]},
        headers=auth_header(user_token),
    )
    assert sync.status_code == 403


def test_agency_broad_sync_is_limited_to_assigned_clients_and_explicit_cross_tenant_is_rejected():
    reset_state()
    admin = mk_user("admin-sync-scope@acl.local", "admin")
    admin_token = issue_token(admin["id"])
    allowed_client = mk_client("Allowed sync tenant", admin_token)
    hidden_client = mk_client("Hidden sync tenant", admin_token)
    allowed_account = mk_account(allowed_client["id"], "allowed-sync", admin_token)
    hidden_account = mk_account(hidden_client["id"], "hidden-sync", admin_token)

    app.state.ad_account_sync_service.provider_fetchers = {
        "meta": lambda external, date_from, date_to: [{"id": external, "date": date_from}],
    }

    agency = mk_user("agency-sync-scope@acl.local", "agency")
    agency_token = issue_token(agency["id"])
    assign_access(agency["id"], allowed_client["id"], "agency")

    broad = client.post("/ad-accounts/sync/run", json={}, headers=auth_header(agency_token))
    assert broad.status_code == 200, broad.text
    assert broad.json()["processed"] == 1
    assert broad.json()["jobs"][0]["ad_account_id"] == allowed_account["id"]

    explicit_hidden = client.post(
        "/ad-accounts/sync/run",
        json={"account_ids": [hidden_account["id"]]},
        headers=auth_header(agency_token),
    )
    assert explicit_hidden.status_code == 403
    assert explicit_hidden.json()["error"]["code"] == "forbidden"
