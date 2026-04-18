from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def shape_of(value):
    if isinstance(value, dict):
        return {k: shape_of(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [shape_of(v) for v in value]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def reset_and_seed_minimal():
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    ).json()
    token = client.post("/auth/internal/sessions/issue", json={"user_id": admin["id"], "ttl_minutes": 60}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    c = client.post("/clients", json={"name": "Acme", "status": "active", "default_currency": "USD"}).json()
    a = client.post(
        "/ad-accounts",
        json={
            "client_id": c["id"],
            "platform": "meta",
            "external_account_id": "snap-meta-1",
            "name": "Meta",
            "currency": "USD",
            "status": "active",
        },
    ).json()

    client.post(
        "/budgets",
        json={
            "client_id": c["id"],
            "scope": "account",
            "account_id": a["id"],
            "amount": "1000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "note": "snapshot",
        },
    )

    client.post(
        "/ad-stats/ingest",
        json={
            "rows": [
                {
                    "ad_account_id": a["id"],
                    "date": "2026-04-01",
                    "platform": "meta",
                    "impressions": 1000,
                    "clicks": 100,
                    "spend": "100.00",
                    "conversions": "5.00",
                }
            ]
        },
        headers={"Idempotency-Key": "snapshot-ingest-1"},
    )

    u = client.post("/auth/internal/users", json={"email": "ctx@example.com", "name": "Ctx", "role": "agency", "status": "active"}).json()
    client.post("/auth/internal/access", json={"user_id": u["id"], "client_id": c["id"], "role": "agency"})
    token = client.post("/auth/internal/sessions/issue", json={"user_id": u["id"], "ttl_minutes": 60}).json()["token"]

    return c, a, token


def test_snapshot_insights_overview_shape():
    c, a, _ = reset_and_seed_minimal()
    res = client.get(
        f"/insights/overview?client_id={c['id']}&account_id={a['id']}&date_from=2026-04-01&date_to=2026-04-30&as_of_date=2026-04-15"
    )
    assert res.status_code == 200
    snap = shape_of(res.json())
    assert snap == {
        "breakdowns": {
            "accounts": [
                {
                    "account_id": "str",
                    "client_id": "str",
                    "clicks": "int",
                    "conversions": "float",
                    "cpc": "float",
                    "cpm": "float",
                    "ctr": "float",
                    "impressions": "int",
                    "name": "str",
                    "platform": "str",
                    "spend": "float",
                }
            ],
            "platforms": [
                {
                    "clicks": "int",
                    "conversions": "float",
                    "cpc": "float",
                    "cpm": "float",
                    "ctr": "float",
                    "impressions": "int",
                    "platform": "str",
                    "spend": "float",
                }
            ],
        },
        "budget_summary": {
            "budget": "float",
            "budget_id": "str",
            "budget_source": "str",
            "expected_spend_to_date": "float",
            "forecast_spend": "float",
            "pace_delta": "float",
            "pace_delta_percent": "float",
            "pace_status": "str",
            "remaining": "float",
            "spend": "float",
            "usage_percent": "float",
        },
        "range": {
            "as_of_date": "str",
            "date_from": "str",
            "date_to": "str",
            "timezone_policy": "str",
        },
        "scope": {"account_id": "str", "client_id": "str"},
        "spend_summary": {
            "clicks": "int",
            "conversions": "float",
            "cpc": "float",
            "cpm": "float",
            "ctr": "float",
            "impressions": "int",
            "spend": "float",
        },
    }


def test_snapshot_agency_overview_shape():
    reset_and_seed_minimal()
    res = client.get("/agency/overview?date_from=2026-04-01&date_to=2026-04-30")
    assert res.status_code == 200
    snap = shape_of(res.json())
    assert snap == {
        "per_account": [
            {
                "account_id": "str",
                "client_id": "str",
                "clicks": "int",
                "conversions": "float",
                "cpc": "float",
                "cpm": "float",
                "ctr": "float",
                "impressions": "int",
                "name": "str",
                "platform": "str",
                "spend": "float",
            }
        ],
        "per_client": [
            {
                "clicks": "int",
                "client_id": "str",
                "conversions": "float",
                "cpc": "float",
                "cpm": "float",
                "ctr": "float",
                "impressions": "int",
                "spend": "float",
            }
        ],
        "per_platform": [
            {
                "clicks": "int",
                "conversions": "float",
                "cpc": "float",
                "cpm": "float",
                "ctr": "float",
                "impressions": "int",
                "platform": "str",
                "spend": "float",
            }
        ],
        "range": {
            "as_of_date": "str",
            "date_from": "str",
            "date_to": "str",
            "timezone_policy": "str",
        },
        "totals": {
            "clicks": "int",
            "conversions": "float",
            "cpc": "float",
            "cpm": "float",
            "ctr": "float",
            "impressions": "int",
            "spend": "float",
        },
    }


def test_snapshot_auth_session_context_shape():
    _, _, token = reset_and_seed_minimal()
    res = client.post("/auth/internal/facade/sessions/context", json={"token": token})
    assert res.status_code == 200
    snap = shape_of(res.json())
    assert snap == {
        "access_scope": "str",
        "accessible_client_ids": ["str"],
        "expires_at": "str",
        "global_access": "bool",
        "reason": "null",
        "role": "str",
        "session_id": "str",
        "user_id": "str",
        "valid": "bool",
    }


def test_snapshot_budgets_list_shape():
    c, _, _ = reset_and_seed_minimal()
    res = client.get(f"/budgets?status=active&client_id={c['id']}")
    assert res.status_code == 200
    snap = shape_of(res.json())
    assert snap == {
        "count": "int",
        "items": [
            {
                "account_id": "str",
                "amount": "str",
                "client_id": "str",
                "created_at": "str",
                "created_by": "null",
                "currency": "str",
                "end_date": "str",
                "id": "str",
                "note": "str",
                "period_type": "str",
                "scope": "str",
                "start_date": "str",
                "status": "str",
                "updated_at": "str",
                "version": "int",
            }
        ],
    }


def test_snapshot_operational_actions_shape():
    c, a, _ = reset_and_seed_minimal()
    create = client.post(
        "/insights/operational/actions",
        json={
            "action": "review",
            "scope": "account",
            "scope_id": a["id"],
            "title": "Review account",
            "reason": "Snapshot action",
            "metrics": {"platform": "meta"},
            "client_id": c["id"],
            "account_id": a["id"],
        },
    )
    assert create.status_code == 200
    res = client.get("/insights/operational/actions")
    assert res.status_code == 200
    snap = shape_of(res.json())
    assert snap == [
        {
            "account_id": "str",
            "action": "str",
            "client_id": "str",
            "created_at": "str",
            "created_by": "str",
            "id": "str",
            "metrics": {"platform": "str"},
            "reason": "str",
            "scope": "str",
            "scope_id": "str",
            "status": "str",
            "title": "str",
        }
    ]
