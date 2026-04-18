from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


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
    r = client.post(
        "/clients",
        json={
            "name": name,
            "status": "active",
            "default_currency": "USD",
        },
    )
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


def budget_payload(client_id, scope="client", account_id=None, amount="1000.00", start="2026-04-01", end="2026-04-30"):
    p = {
        "client_id": client_id,
        "scope": scope,
        "amount": amount,
        "currency": "USD",
        "period_type": "monthly",
        "start_date": start,
        "end_date": end,
        "note": "test",
    }
    if account_id:
        p["account_id"] = account_id
    return p


def test_budget_scope_overlap_and_cross_scope_behavior():
    reset_state()
    c = mk_client()
    a = mk_account(c["id"])

    r1 = client.post("/budgets", json=budget_payload(c["id"], scope="client", start="2026-04-01", end="2026-04-30"))
    assert r1.status_code == 200

    r2 = client.post("/budgets", json=budget_payload(c["id"], scope="client", start="2026-04-15", end="2026-05-15"))
    assert r2.status_code == 409

    r3 = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a["id"], start="2026-04-10", end="2026-04-20"),
    )
    assert r3.status_code == 200  # cross-scope overlap allowed

    r4 = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a["id"], start="2026-04-18", end="2026-04-25"),
    )
    assert r4.status_code == 409


def test_account_budgets_cannot_exceed_client_budget_cap():
    reset_state()
    c = mk_client()
    a1 = mk_account(c["id"], external="acc-1", platform="meta")
    a2 = mk_account(c["id"], external="acc-2", platform="google")

    client_budget = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="client", amount="1000.00", start="2026-04-01", end="2026-04-30"),
    )
    assert client_budget.status_code == 200

    first_account = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a1["id"], amount="600.00", start="2026-04-01", end="2026-04-30"),
    )
    assert first_account.status_code == 200

    second_account = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a2["id"], amount="500.00", start="2026-04-01", end="2026-04-30"),
    )
    assert second_account.status_code == 409
    assert "exceeds client budget cap" in second_account.json()["error"]["message"].lower()


def test_client_budget_cannot_be_reduced_below_allocated_accounts():
    reset_state()
    c = mk_client()
    a1 = mk_account(c["id"], external="acc-1", platform="meta")
    a2 = mk_account(c["id"], external="acc-2", platform="google")

    created_client_budget = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="client", amount="1200.00", start="2026-04-01", end="2026-04-30"),
    ).json()
    client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a1["id"], amount="500.00", start="2026-04-01", end="2026-04-30"),
    )
    client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a2["id"], amount="400.00", start="2026-04-01", end="2026-04-30"),
    )

    too_low = client.patch(f"/budgets/{created_client_budget['id']}", json={"amount": "800.00"})
    assert too_low.status_code == 409
    assert "lower than allocated active account budgets" in too_low.json()["error"]["message"].lower()


def test_budget_transfer_between_accounts_updates_both_budgets_atomically():
    reset_state()
    c = mk_client()
    a1 = mk_account(c["id"], external="acc-1", platform="meta")
    a2 = mk_account(c["id"], external="acc-2", platform="google")

    client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="client", amount="1000.00", start="2026-04-01", end="2026-04-30"),
    )
    src = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a1["id"], amount="700.00", start="2026-04-01", end="2026-04-30"),
    ).json()
    tgt = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a2["id"], amount="100.00", start="2026-04-01", end="2026-04-30"),
    ).json()

    moved = client.post(
        f"/budgets/{src['id']}/transfer",
        json={"target_account_id": a2["id"], "amount": "150.00"},
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["transferred_amount"] == "150.00"
    assert body["source_budget"]["amount"] == "550.00"
    assert body["target_budget"]["amount"] == "250.00"
    assert body["source_budget"]["id"] == src["id"]
    assert body["target_budget"]["id"] == tgt["id"]

    src_log = client.get(f"/budgets/{src['id']}/transfers")
    assert src_log.status_code == 200
    src_items = src_log.json()
    assert len(src_items) == 1
    assert src_items[0]["source_budget_id"] == src["id"]
    assert src_items[0]["target_budget_id"] == tgt["id"]
    assert src_items[0]["amount"] == "150.00"

    outgoing = client.get(f"/budgets/{src['id']}/transfers?direction=outgoing&limit=1")
    assert outgoing.status_code == 200
    out_items = outgoing.json()
    assert len(out_items) == 1
    assert out_items[0]["source_budget_id"] == src["id"]

    incoming = client.get(f"/budgets/{tgt['id']}/transfers?direction=incoming&limit=1")
    assert incoming.status_code == 200
    in_items = incoming.json()
    assert len(in_items) == 1
    assert in_items[0]["target_budget_id"] == tgt["id"]


def test_budget_transfer_cannot_exceed_source_amount():
    reset_state()
    c = mk_client()
    a1 = mk_account(c["id"], external="acc-1", platform="meta")
    a2 = mk_account(c["id"], external="acc-2", platform="google")

    client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="client", amount="1000.00", start="2026-04-01", end="2026-04-30"),
    )
    src = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a1["id"], amount="100.00", start="2026-04-01", end="2026-04-30"),
    ).json()

    too_much = client.post(
        f"/budgets/{src['id']}/transfer",
        json={"target_account_id": a2["id"], "amount": "101.00"},
    )
    assert too_much.status_code == 400
    assert "exceeds source budget amount" in too_much.json()["error"]["message"].lower()


def test_budget_patch_audit_business_changes_only_and_noop():
    reset_state()
    c = mk_client()
    created = client.post("/budgets", json=budget_payload(c["id"], scope="client", amount="100.00")).json()
    bid = created["id"]

    # no-op patch: no version bump, no history
    p1 = client.patch(f"/budgets/{bid}", json={"changed_by": str(uuid4())})
    assert p1.status_code == 200
    assert p1.json()["version"] == 1
    h1 = client.get(f"/budgets/{bid}/history")
    assert h1.status_code == 200
    assert h1.json() == []

    # business-field patch: version/history updated
    p2 = client.patch(f"/budgets/{bid}", json={"amount": "150.00", "changed_by": str(uuid4())})
    assert p2.status_code == 200
    assert p2.json()["version"] == 2
    h2 = client.get(f"/budgets/{bid}/history").json()
    assert len(h2) == 1
    assert h2[0]["previous_values"]["amount"] == "100.00"
    assert h2[0]["new_values"]["amount"] == "150.00"


def test_budget_list_status_filters_active_archived_all():
    reset_state()
    c = mk_client()
    a = mk_account(c["id"], external="acc-b2", platform="meta")
    b1 = client.post("/budgets", json=budget_payload(c["id"], scope="client", amount="300")).json()
    b2 = client.post(
        "/budgets",
        json=budget_payload(c["id"], scope="account", account_id=a["id"], amount="200"),
    ).json()

    client.delete(f"/budgets/{b1['id']}")

    active = client.get("/budgets").json()
    archived = client.get("/budgets?status=archived").json()
    all_rows = client.get("/budgets?status=all").json()

    active_ids = {x["id"] for x in active["items"]}
    archived_ids = {x["id"] for x in archived["items"]}
    all_ids = {x["id"] for x in all_rows["items"]}

    assert b2["id"] in active_ids
    assert b1["id"] in archived_ids
    assert {b1["id"], b2["id"]}.issubset(all_ids)


def test_clients_module_crud():
    reset_state()
    c = mk_client("Client A")

    listed = client.get("/clients").json()
    assert listed["count"] == 1

    got = client.get(f"/clients/{c['id']}")
    assert got.status_code == 200
    assert got.json()["name"] == "Client A"

    patched = client.patch(f"/clients/{c['id']}", json={"status": "inactive", "notes": "hold"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "inactive"

    archived = client.delete(f"/clients/{c['id']}")
    assert archived.status_code == 200
    assert archived.json()["client"]["status"] == "archived"


def test_ad_accounts_module_and_client_fk_enforced():
    reset_state()
    c = mk_client()

    bad = client.post(
        "/ad-accounts",
        json={
            "client_id": str(uuid4()),
            "platform": "meta",
            "external_account_id": "x1",
            "name": "Bad",
            "currency": "USD",
            "status": "active",
        },
    )
    assert bad.status_code == 400

    a = mk_account(c["id"], external="x1", platform="meta")

    listed = client.get(f"/ad-accounts?client_id={c['id']}").json()
    assert listed["count"] == 1

    patched = client.patch(f"/ad-accounts/{a['id']}", json={"name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"

    archived = client.delete(f"/ad-accounts/{a['id']}")
    assert archived.status_code == 200
    assert archived.json()["ad_account"]["status"] == "archived"


def test_ad_stats_ingestion_and_aggregation_filters():
    reset_state()
    c1 = mk_client("C1")
    c2 = mk_client("C2")
    a1 = mk_account(c1["id"], external="m1", platform="meta")
    a2 = mk_account(c2["id"], external="g1", platform="google")

    ingest_row(a1["id"], "2026-04-01", platform="meta", spend="100.00", impressions=1000, clicks=100, conversions="5")
    ingest_row(a2["id"], "2026-04-01", platform="google", spend="200.00", impressions=2000, clicks=200, conversions="10")

    # list filter by client
    rows = client.get(f"/ad-stats?client_id={c1['id']}&date_from=2026-04-01&date_to=2026-04-30").json()
    assert rows["count"] == 1
    assert rows["items"][0]["platform"] == "meta"


def test_ad_stats_ingest_idempotency_key_replay_and_conflict():
    reset_state()
    c = mk_client("C1")
    a = mk_account(c["id"], external="m1", platform="meta")
    payload = {
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
    }
    key = "ingest-001"
    first = client.post("/ad-stats/ingest", json=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 200
    assert first.json()["idempotency"]["replayed"] is False

    replay = client.post("/ad-stats/ingest", json=payload, headers={"Idempotency-Key": key})
    assert replay.status_code == 200
    assert replay.json()["idempotency"]["replayed"] is True

    changed = {
        "rows": [
            {
                "ad_account_id": a["id"],
                "date": "2026-04-01",
                "platform": "meta",
                "impressions": 999,
                "clicks": 99,
                "spend": "99.00",
                "conversions": "4.00",
            }
        ]
    }
    conflict = client.post("/ad-stats/ingest", json=changed, headers={"Idempotency-Key": key})
    assert conflict.status_code == 409


def test_unified_overview_contract_and_budget_priority_and_pace_fields():
    reset_state()
    c = mk_client("C1")
    a = mk_account(c["id"], external="m1", platform="meta")

    ingest_row(a["id"], "2026-04-01", platform="meta", spend="700.00", impressions=7000, clicks=700, conversions="35")

    client.post("/budgets", json=budget_payload(c["id"], scope="client", amount="2000.00"))
    client.post("/budgets", json=budget_payload(c["id"], scope="account", account_id=a["id"], amount="1000.00"))

    res = client.get(
        f"/insights/overview?client_id={c['id']}&account_id={a['id']}&date_from=2026-04-01&date_to=2026-04-30&as_of_date=2026-04-15"
    )
    assert res.status_code == 200
    body = res.json()

    assert set(body.keys()) == {"range", "scope", "spend_summary", "budget_summary", "breakdowns"}
    assert body["scope"]["client_id"] == c["id"]
    assert body["scope"]["account_id"] == a["id"]
    assert body["spend_summary"]["spend"] == 700.0
    assert body["budget_summary"]["budget"] == 1000.0  # account overrides client
    assert body["budget_summary"]["budget_source"] == "account"
    assert body["budget_summary"]["pace_status"] in {"on_track", "overspending", "underspending"}
    assert "pace_delta" in body["budget_summary"]
    assert "pace_delta_percent" in body["budget_summary"]


def test_pace_delta_percent_expected_zero_safe_null():
    reset_state()
    c = mk_client("C1")
    a = mk_account(c["id"], external="m1", platform="meta")
    ingest_row(a["id"], "2026-04-01", platform="meta", spend="10.00", impressions=100, clicks=10, conversions="1")
    client.post("/budgets", json=budget_payload(c["id"], scope="account", account_id=a["id"], amount="0.00"))

    res = client.get(
        f"/insights/overview?client_id={c['id']}&account_id={a['id']}&date_from=2026-04-01&date_to=2026-04-30&as_of_date=2026-04-15"
    )
    b = res.json()["budget_summary"]
    assert b["expected_spend_to_date"] == 0.0
    assert b["pace_delta_percent"] is None


def test_agency_aggregation_support():
    reset_state()
    c1 = mk_client("C1")
    c2 = mk_client("C2")
    a1 = mk_account(c1["id"], external="m1", platform="meta")
    a2 = mk_account(c2["id"], external="g1", platform="google")

    ingest_row(a1["id"], "2026-04-01", platform="meta", spend="100.00", impressions=1000, clicks=100, conversions="5")
    ingest_row(a2["id"], "2026-04-01", platform="google", spend="200.00", impressions=2000, clicks=200, conversions="10")

    res = client.get("/agency/overview?date_from=2026-04-01&date_to=2026-04-30")
    assert res.status_code == 200
    body = res.json()

    assert body["totals"]["spend"] == 300.0
    assert len(body["per_platform"]) == 2
    assert len(body["per_client"]) == 2
    assert len(body["per_account"]) == 2
