from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AgencyClientAccessCreate, AgencyCreate, AgencyMemberCreate


client = TestClient(app)


def reset_state():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    client.headers.pop("Authorization", None)
    client.cookies.clear()


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


def mk_user(email: str, role: str):
    response = client.post(
        "/auth/internal/users",
        json={"email": email, "name": role.title(), "role": role, "status": "active"},
    )
    assert response.status_code == 200
    return response.json()


def issue_token(user_id: str) -> str:
    response = client.post("/auth/internal/sessions/issue", json={"user_id": user_id, "ttl_minutes": 60})
    assert response.status_code == 200
    return response.json()["token"]


def assign_access(user_id: str, client_id: str, role: str):
    if role == "agency":
        agency = app.state.platform_admin_store.create_agency(
            AgencyCreate(name=f"Write boundary agency {uuid4()}", status="active")
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
    response = client.post(
        "/auth/internal/access",
        json={"user_id": user_id, "client_id": client_id, "role": role},
    )
    assert response.status_code == 200


def mk_client(name: str, token: str):
    response = client.post(
        "/clients",
        json={"name": name, "status": "active", "default_currency": "USD"},
        headers=auth_header(token),
    )
    assert response.status_code == 200
    return response.json()


def mk_account(client_id: str, external_id: str, token: str, platform: str = "meta"):
    response = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external_id,
            "name": f"{platform}-{external_id}",
            "currency": "USD",
            "status": "active",
        },
        headers=auth_header(token),
    )
    assert response.status_code == 200
    return response.json()


def budget_payload(client_id: str, account_id: str, amount: str = "500.00"):
    return {
        "client_id": client_id,
        "scope": "account",
        "account_id": account_id,
        "amount": amount,
        "currency": "USD",
        "period_type": "monthly",
        "start_date": "2026-06-01",
        "end_date": "2026-06-30",
        "note": "write-boundary test",
    }


def action_payload(scope: str, scope_id: str, **extra):
    payload = {
        "action": "review",
        "scope": scope,
        "scope_id": scope_id,
        "title": "Review performance",
        "reason": "Automated recommendation",
        "metrics": {},
    }
    payload.update(extra)
    return payload


def assert_forbidden(response):
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_client_role_is_read_only_for_all_tenant_mutations():
    reset_state()
    admin = mk_user("admin-read-only@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client("Read-only tenant", admin_token)
    source_account = mk_account(tenant["id"], "source", admin_token)
    target_account = mk_account(tenant["id"], "target", admin_token)
    budget = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], source_account["id"]),
        headers=auth_header(admin_token),
    ).json()

    user = mk_user("client-read-only@test.local", "client")
    user_token = issue_token(user["id"])
    assign_access(user["id"], tenant["id"], "client")
    headers = auth_header(user_token)

    attempts = [
        client.post(
            "/clients",
            json={"name": "Forbidden tenant", "status": "active", "default_currency": "USD"},
            headers=headers,
        ),
        client.patch(f"/clients/{tenant['id']}", json={"name": "Changed"}, headers=headers),
        client.delete(f"/clients/{tenant['id']}", headers=headers),
        client.post(
            f"/clients/{tenant['id']}/invites",
            json={"email": "forbidden-invite@test.local", "expires_in_days": 7},
            headers=headers,
        ),
        client.post(
            "/ad-accounts",
            json={
                "client_id": tenant["id"],
                "platform": "google",
                "external_account_id": "forbidden",
                "name": "Forbidden",
                "currency": "USD",
                "status": "active",
            },
            headers=headers,
        ),
        client.patch(f"/ad-accounts/{source_account['id']}", json={"name": "Changed"}, headers=headers),
        client.delete(f"/ad-accounts/{source_account['id']}", headers=headers),
        client.post(
            "/ad-stats/ingest",
            json={
                "rows": [
                    {
                        "ad_account_id": source_account["id"],
                        "date": "2026-06-01",
                        "platform": "meta",
                        "impressions": 100,
                        "clicks": 10,
                        "spend": "25.00",
                        "conversions": "1.00",
                    }
                ]
            },
            headers=headers,
        ),
        client.post("/budgets", json=budget_payload(tenant["id"], target_account["id"]), headers=headers),
        client.patch(f"/budgets/{budget['id']}", json={"amount": "450.00"}, headers=headers),
        client.delete(f"/budgets/{budget['id']}", headers=headers),
        client.post(
            f"/budgets/{budget['id']}/transfer",
            json={"target_account_id": target_account["id"], "amount": "50.00"},
            headers=headers,
        ),
        client.post(
            "/insights/operational/actions",
            json=action_payload("account", source_account["id"]),
            headers=headers,
        ),
    ]

    for response in attempts:
        assert_forbidden(response)

    unchanged_client = client.get(f"/clients/{tenant['id']}", headers=auth_header(admin_token)).json()
    unchanged_account = client.get(
        f"/ad-accounts/{source_account['id']}",
        headers=auth_header(admin_token),
    ).json()
    unchanged_budget = client.get(f"/budgets/{budget['id']}", headers=auth_header(admin_token)).json()
    stats = client.get(
        f"/ad-stats?account_id={source_account['id']}",
        headers=auth_header(admin_token),
    ).json()
    actions = client.get(
        f"/insights/operational/actions?client_id={tenant['id']}",
        headers=auth_header(admin_token),
    ).json()
    assert unchanged_client["name"] == "Read-only tenant"
    assert unchanged_account["name"] == "meta-source"
    assert unchanged_budget["amount"] == "500.00"
    assert stats["count"] == 0
    assert actions == []


def test_agency_can_write_an_assigned_tenant():
    reset_state()
    admin = mk_user("admin-agency-write@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client("Agency tenant", admin_token)
    agency = mk_user("agency-write@test.local", "agency")
    agency_token = issue_token(agency["id"])
    assign_access(agency["id"], tenant["id"], "agency")
    headers = auth_header(agency_token)

    patched = client.patch(f"/clients/{tenant['id']}", json={"name": "Agency managed"}, headers=headers)
    assert patched.status_code == 200

    account = client.post(
        "/ad-accounts",
        json={
            "client_id": tenant["id"],
            "platform": "meta",
            "external_account_id": "agency-created",
            "name": "Agency created",
            "currency": "USD",
            "status": "active",
        },
        headers=headers,
    )
    assert account.status_code == 200

    ingested = client.post(
        "/ad-stats/ingest",
        json={
            "rows": [
                {
                    "ad_account_id": account.json()["id"],
                    "date": "2026-06-01",
                    "platform": "meta",
                    "impressions": 100,
                    "clicks": 10,
                    "spend": "25.00",
                    "conversions": "1.00",
                }
            ]
        },
        headers=headers,
    )
    assert ingested.status_code == 200

    budget = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], account.json()["id"]),
        headers=headers,
    )
    assert budget.status_code == 200

    action = client.post(
        "/insights/operational/actions",
        json=action_payload("client", tenant["id"]),
        headers=headers,
    )
    assert action.status_code == 200
    assert action.json()["client_id"] == tenant["id"]


def test_operational_action_scope_id_is_authoritative_and_validated():
    reset_state()
    admin = mk_user("admin-action-scope@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant_a = mk_client("Action tenant A", admin_token)
    tenant_b = mk_client("Action tenant B", admin_token)
    account_a = mk_account(tenant_a["id"], "action-a", admin_token)
    account_b = mk_account(tenant_b["id"], "action-b", admin_token)
    headers = auth_header(admin_token)

    client_action = client.post(
        "/insights/operational/actions",
        json=action_payload("client", tenant_a["id"]),
        headers=headers,
    )
    assert client_action.status_code == 200
    assert client_action.json()["client_id"] == tenant_a["id"]
    assert client_action.json()["account_id"] is None

    account_action = client.post(
        "/insights/operational/actions",
        json=action_payload("account", account_a["id"]),
        headers=headers,
    )
    assert account_action.status_code == 200
    assert account_action.json()["client_id"] == tenant_a["id"]
    assert account_action.json()["account_id"] == account_a["id"]

    mismatched_account = client.post(
        "/insights/operational/actions",
        json=action_payload("account", account_a["id"], account_id=account_b["id"]),
        headers=headers,
    )
    assert mismatched_account.status_code == 400

    mismatched_client = client.post(
        "/insights/operational/actions",
        json=action_payload("client", tenant_a["id"], client_id=tenant_b["id"]),
        headers=headers,
    )
    assert mismatched_client.status_code == 400

    invalid_scope_id = client.post(
        "/insights/operational/actions",
        json=action_payload("account", "not-a-uuid", account_id=account_a["id"]),
        headers=headers,
    )
    assert invalid_scope_id.status_code == 400

    missing_client = client.post(
        "/insights/operational/actions",
        json=action_payload("client", str(uuid4())),
        headers=headers,
    )
    assert missing_client.status_code == 404

    agency = client.post(
        "/platform/agencies",
        json={"name": "Action Agency", "status": "active", "plan": "starter"},
        headers=headers,
    )
    assert agency.status_code == 200
    agency_action = client.post(
        "/insights/operational/actions",
        json=action_payload("agency", agency.json()["id"]),
        headers=headers,
    )
    assert agency_action.status_code == 200
    assert agency_action.json()["scope_id"] == agency.json()["id"]
    assert agency_action.json()["client_id"] is None
    assert agency_action.json()["account_id"] is None


def test_unbound_agency_cannot_execute_client_or_agency_actions():
    reset_state()
    admin = mk_user("admin-unbound-agency@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client("Private tenant", admin_token)
    platform_agency = client.post(
        "/platform/agencies",
        json={"name": "Private Agency", "status": "active", "plan": "starter"},
        headers=auth_header(admin_token),
    ).json()
    agency_user = mk_user("unbound-agency@test.local", "agency")
    agency_token = issue_token(agency_user["id"])
    headers = auth_header(agency_token)

    assert_forbidden(
        client.post(
            "/insights/operational/actions",
            json=action_payload("client", tenant["id"]),
            headers=headers,
        )
    )
    assert_forbidden(
        client.post(
            "/insights/operational/actions",
            json=action_payload("agency", platform_agency["id"]),
            headers=headers,
        )
    )
    all_scope = client.post(
        "/insights/operational/actions",
        json=action_payload("agency", "all"),
        headers=headers,
    )
    assert all_scope.status_code == 403
    assert all_scope.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("impressions", -1),
        ("clicks", -1),
        ("spend", "-0.01"),
        ("conversions", "-0.01"),
    ],
)
def test_ad_stats_reject_negative_metrics(field, value):
    reset_state()
    admin = mk_user(f"admin-negative-{field}@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client(f"Negative {field}", admin_token)
    account = mk_account(tenant["id"], f"negative-{field}", admin_token)
    row = {
        "ad_account_id": account["id"],
        "date": "2026-06-01",
        "platform": "meta",
        "impressions": 100,
        "clicks": 10,
        "spend": "25.00",
        "conversions": "1.00",
    }
    row[field] = value

    response = client.post(
        "/ad-stats/ingest",
        json={"rows": [row]},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 422


def test_ad_stats_platform_must_match_account_and_is_normalized():
    reset_state()
    admin = mk_user("admin-platform-match@test.local", "admin")
    admin_token = issue_token(admin["id"])
    tenant = mk_client("Platform match", admin_token)
    account = mk_account(tenant["id"], "platform-match", admin_token, platform="meta")
    base_row = {
        "ad_account_id": account["id"],
        "date": "2026-06-01",
        "impressions": 100,
        "clicks": 10,
        "spend": "25.00",
        "conversions": "1.00",
    }

    mismatch = client.post(
        "/ad-stats/ingest",
        json={"rows": [{**base_row, "platform": "google"}]},
        headers=auth_header(admin_token),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "platform_mismatch"

    normalized = client.post(
        "/ad-stats/ingest",
        json={"rows": [{**base_row, "platform": " META "}]},
        headers=auth_header(admin_token),
    )
    assert normalized.status_code == 200
    listed = client.get(
        f"/ad-stats?account_id={account['id']}",
        headers=auth_header(admin_token),
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["platform"] == "meta"


def test_currency_codes_are_normalized_and_cross_resource_mismatches_are_rejected():
    reset_state()
    admin = mk_user("admin-currency@test.local", "admin")
    admin_token = issue_token(admin["id"])
    headers = auth_header(admin_token)

    usd_client_response = client.post(
        "/clients",
        json={"name": "USD tenant", "status": "active", "default_currency": " usd "},
        headers=headers,
    )
    assert usd_client_response.status_code == 200
    usd_client = usd_client_response.json()
    assert usd_client["default_currency"] == "USD"

    normalized_account = client.post(
        "/ad-accounts",
        json={
            "client_id": usd_client["id"],
            "platform": "meta",
            "external_account_id": "currency-normalized",
            "name": "Currency normalized",
            "currency": "usd",
            "status": "active",
        },
        headers=headers,
    )
    assert normalized_account.status_code == 200
    assert normalized_account.json()["currency"] == "USD"

    mismatch_account = client.post(
        "/ad-accounts",
        json={
            "client_id": usd_client["id"],
            "platform": "google",
            "external_account_id": "currency-mismatch",
            "name": "Currency mismatch",
            "currency": "EUR",
            "status": "active",
        },
        headers=headers,
    )
    assert mismatch_account.status_code == 400
    assert mismatch_account.json()["error"]["code"] == "currency_mismatch"

    invalid_currency = client.post(
        "/clients",
        json={"name": "Invalid currency", "status": "active", "default_currency": "US"},
        headers=headers,
    )
    assert invalid_currency.status_code == 422

    mismatched_budget_payload = budget_payload(usd_client["id"], normalized_account.json()["id"])
    mismatched_budget_payload["currency"] = "EUR"
    mismatch_budget = client.post("/budgets", json=mismatched_budget_payload, headers=headers)
    assert mismatch_budget.status_code == 400
    assert mismatch_budget.json()["error"]["code"] == "currency_mismatch"


def test_currency_changes_cannot_break_existing_account_and_budget_relationships():
    reset_state()
    admin = mk_user("admin-currency-change@test.local", "admin")
    admin_token = issue_token(admin["id"])
    headers = auth_header(admin_token)
    usd_client = mk_client("USD tenant", admin_token)
    eur_client_response = client.post(
        "/clients",
        json={"name": "EUR tenant", "status": "active", "default_currency": "EUR"},
        headers=headers,
    )
    assert eur_client_response.status_code == 200
    eur_client = eur_client_response.json()
    account = mk_account(usd_client["id"], "currency-change", admin_token)
    budget = client.post(
        "/budgets",
        json=budget_payload(usd_client["id"], account["id"]),
        headers=headers,
    )
    assert budget.status_code == 200

    client_currency_change = client.patch(
        f"/clients/{usd_client['id']}",
        json={"default_currency": "EUR"},
        headers=headers,
    )
    assert client_currency_change.status_code == 409
    assert client_currency_change.json()["error"]["code"] == "currency_conflict"

    account_move = client.patch(
        f"/ad-accounts/{account['id']}",
        json={"client_id": eur_client["id"]},
        headers=headers,
    )
    assert account_move.status_code == 400
    assert account_move.json()["error"]["code"] == "currency_mismatch"

    budget_currency_change = client.patch(
        f"/budgets/{budget.json()['id']}",
        json={"currency": "EUR"},
        headers=headers,
    )
    assert budget_currency_change.status_code == 400
    assert budget_currency_change.json()["error"]["code"] == "currency_mismatch"
