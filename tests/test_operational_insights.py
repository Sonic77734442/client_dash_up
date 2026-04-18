from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.services.operational_insights import OperationalInsightsService


client = TestClient(app)


def reset_state():
    r = client.post("/_testing/use-inmemory-stores")
    assert r.status_code == 200
    admin = client.post(
        "/auth/internal/users",
        json={"email": "admin@test.local", "name": "Admin", "role": "admin", "status": "active"},
    )
    assert admin.status_code == 200
    issued = client.post("/auth/internal/sessions/issue", json={"user_id": admin.json()["id"], "ttl_minutes": 60})
    assert issued.status_code == 200
    client.headers.update({"Authorization": f"Bearer {issued.json()['token']}"})


def mk_client(name="Acme"):
    r = client.post("/clients", json={"name": name, "status": "active", "default_currency": "USD"})
    assert r.status_code == 200
    return r.json()


def mk_account(client_id, external="acc-1", platform="meta"):
    r = client.post(
        "/ad-accounts",
        json={
            "client_id": client_id,
            "platform": platform,
            "external_account_id": external,
            "name": f"{platform}-{external}",
            "currency": "USD",
            "status": "active",
        },
    )
    assert r.status_code == 200
    return r.json()


def ingest_row(account_id, dt, platform="meta", spend="100.00", impressions=1000, clicks=100, conversions="5.00"):
    payload = {
        "rows": [
            {
                "ad_account_id": account_id,
                "date": dt,
                "platform": platform,
                "impressions": impressions,
                "clicks": clicks,
                "spend": spend,
                "conversions": conversions,
            }
        ]
    }
    r = client.post("/ad-stats/ingest", json=payload)
    assert r.status_code == 200


def test_operational_insights_endpoint_returns_recommendations():
    reset_state()
    c = mk_client("C1")
    a1 = mk_account(c["id"], external="m1", platform="meta")
    a2 = mk_account(c["id"], external="g1", platform="google")

    ingest_row(a1["id"], "2026-04-01", platform="meta", spend="500.00", impressions=6000, clicks=200, conversions="8")
    ingest_row(a2["id"], "2026-04-01", platform="google", spend="150.00", impressions=4000, clicks=350, conversions="25")

    client.post(
        "/budgets",
        json={
            "client_id": c["id"],
            "scope": "client",
            "amount": "1000.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "note": "test",
        },
    )

    res = client.get(
        f"/insights/operational?client_id={c['id']}&date_from=2026-04-01&date_to=2026-04-30&as_of_date=2026-04-15"
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"range", "scope", "items"}
    assert body["scope"]["client_id"] == c["id"]
    assert isinstance(body["items"], list)
    assert len(body["items"]) > 0
    first = body["items"][0]
    assert set(first.keys()) == {"scope", "scope_id", "title", "reason", "action", "priority", "score", "metrics"}


def test_operational_insights_rules_are_configurable_not_hardcoded():
    rows = [
        {
            "account_id": "a-1",
            "name": "A",
            "platform": "meta",
            "spend": 700.0,
            "cpc": 4.0,
            "ctr": 0.03,
            "conversions": 10.0,
        },
        {
            "account_id": "a-2",
            "name": "B",
            "platform": "google",
            "spend": 300.0,
            "cpc": 1.0,
            "ctr": 0.07,
            "conversions": 20.0,
        },
    ]

    suppress_cap_rules = {
        "max_items": 10,
        "min_spend_share_for_action": 0.01,
        "high_cpc_multiplier": 10.0,
        "low_cpc_multiplier": 1.0,
        "high_ctr_multiplier": 1.0,
        "low_ctr_multiplier": 1.0,
        "high_priority_score_threshold": 1.0,
        "medium_priority_score_threshold": 0.6,
        "pace_delta_abs_percent_for_review": 100.0,
    }
    svc_suppress = OperationalInsightsService(rules=suppress_cap_rules)
    out1 = svc_suppress.generate(
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
        scope_client_id="c-1",
        scope_account_id=None,
        breakdown_accounts=rows,
        budget_summary={"pace_status": "on_track", "pace_delta_percent": 0},
    )
    assert not any(x["action"] == "cap" for x in out1["items"])

    allow_cap_rules = dict(suppress_cap_rules)
    allow_cap_rules["high_cpc_multiplier"] = 1.1
    svc_allow = OperationalInsightsService(rules=allow_cap_rules)
    out2 = svc_allow.generate(
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
        scope_client_id="c-1",
        scope_account_id=None,
        breakdown_accounts=rows,
        budget_summary={"pace_status": "on_track", "pace_delta_percent": 0},
    )
    assert any(x["action"] == "cap" for x in out2["items"])


def test_operational_insights_returns_monitoring_fallback_when_no_signals():
    rules = {
        "max_items": 6,
        "min_spend_share_for_action": 0.15,
        "high_cpc_multiplier": 10.0,
        "low_cpc_multiplier": 0.1,
        "high_ctr_multiplier": 10.0,
        "low_ctr_multiplier": 0.1,
        "high_priority_score_threshold": 1.0,
        "medium_priority_score_threshold": 0.6,
        "pace_delta_abs_percent_for_review": 100.0,
    }
    svc = OperationalInsightsService(rules=rules)
    out = svc.generate(
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
        scope_client_id="c-1",
        scope_account_id=None,
        breakdown_accounts=[
            {
                "account_id": "a-1",
                "name": "A",
                "platform": "tiktok",
                "spend": 100.0,
                "cpc": 1.0,
                "ctr": 0.05,
                "conversions": 10.0,
            }
        ],
        budget_summary={"pace_status": "on_track", "pace_delta_percent": 0},
    )
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert item["action"] == "review"
    assert item["priority"] == "low"
    assert item["metrics"]["fallback"] is True
