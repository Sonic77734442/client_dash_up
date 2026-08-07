import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import sqlite_conn
from app.main import app
from app.schemas import BudgetCreate, BudgetPatch, BudgetTransferRequest
from app.services.budgets import SqliteBudgetStore


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


def sqlite_budget_payload(client_id, *, scope="client", account_id=None, amount="100.00"):
    return BudgetCreate(
        client_id=client_id,
        scope=scope,
        account_id=account_id,
        amount=Decimal(amount),
        currency="USD",
        period_type="monthly",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
        note="concurrency test",
    )


def seed_sqlite_client_and_accounts(store, client_id, *account_ids):
    now = "2026-04-01T00:00:00"
    with sqlite_conn(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO clients
            (id, name, legal_name, status, default_currency, timezone, notes, created_at, updated_at)
            VALUES (?, 'Concurrency client', NULL, 'active', 'USD', 'UTC', NULL, ?, ?)
            """,
            (str(client_id), now, now),
        )
        for index, account_id in enumerate(account_ids, start=1):
            conn.execute(
                """
                INSERT INTO ad_accounts
                (id, client_id, platform, external_account_id, name, currency, timezone, status, metadata, created_at, updated_at)
                VALUES (?, ?, 'meta', ?, ?, 'USD', 'UTC', 'active', NULL, ?, ?)
                """,
                (str(account_id), str(client_id), f"concurrent-{index}", f"Account {index}", now, now),
            )
        conn.commit()


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


def test_budget_transfer_rejects_archived_target_account():
    reset_state()
    tenant = mk_client("Archived transfer target")
    source_account = mk_account(tenant["id"], external="source-active")
    target_account = mk_account(tenant["id"], external="target-archived")
    client.post("/budgets", json=budget_payload(tenant["id"], scope="client", amount="1000"))
    source_budget = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="account", account_id=source_account["id"], amount="500"),
    ).json()
    assert client.delete(f"/ad-accounts/{target_account['id']}").status_code == 200

    transfer = client.post(
        f"/budgets/{source_budget['id']}/transfer",
        json={"target_account_id": target_account["id"], "amount": "100.00"},
    )

    assert transfer.status_code == 409
    assert transfer.json()["error"]["code"] == "account_not_active"


def test_sqlite_concurrent_partial_patches_merge_under_lock(tmp_path):
    store = SqliteBudgetStore(str(tmp_path / "patch-concurrency.db"))
    created = store.create(sqlite_budget_payload(uuid4()))

    # This hook makes the previous implementation deterministic: both workers
    # completed its pre-lock get() before either could write. The fixed
    # implementation reads only after BEGIN IMMEDIATE, so it does not use this
    # hook and serializes the two merges correctly.
    original_get = store.get
    stale_read_barrier = Barrier(2)

    def synchronized_get(budget_id):
        row = original_get(budget_id)
        stale_read_barrier.wait(timeout=5)
        return row

    store.get = synchronized_get
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(store.patch, created.id, BudgetPatch(amount=Decimal("125.00"))),
                pool.submit(store.patch, created.id, BudgetPatch(note="second writer")),
            ]
            updated = [future.result(timeout=10) for future in futures]
    finally:
        store.get = original_get

    final = store.get(created.id)
    assert final is not None
    assert final.amount == Decimal("125.00")
    assert final.note == "second writer"
    assert final.version == 3
    assert sorted(row.version for row in updated) == [2, 3]

    history = store.history(created.id)
    assert len(history) == 2
    assert {int(row.new_values["version"]) for row in history} == {2, 3}


def test_sqlite_concurrent_transfers_cannot_overdraw_source(tmp_path):
    store = SqliteBudgetStore(str(tmp_path / "transfer-concurrency.db"))
    client_id = uuid4()
    source_account_id = uuid4()
    target_account_id = uuid4()
    seed_sqlite_client_and_accounts(store, client_id, source_account_id, target_account_id)
    source = store.create(
        sqlite_budget_payload(
            client_id,
            scope="account",
            account_id=source_account_id,
            amount="100.00",
        )
    )

    # As above, this barrier deterministically exposes the former pre-lock
    # balance check without weakening the fixed implementation.
    original_get = store.get
    stale_read_barrier = Barrier(2)

    def synchronized_get(budget_id):
        row = original_get(budget_id)
        stale_read_barrier.wait(timeout=5)
        return row

    store.get = synchronized_get

    def move_eighty():
        try:
            return (
                "ok",
                store.transfer(
                    source.id,
                    BudgetTransferRequest(target_account_id=target_account_id, amount=Decimal("80.00")),
                ),
            )
        except HTTPException as exc:
            return ("error", exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result(timeout=10) for future in [pool.submit(move_eighty), pool.submit(move_eighty)]]
    finally:
        store.get = original_get

    assert sorted(kind for kind, _ in outcomes) == ["error", "ok"]
    rejected = next(value for kind, value in outcomes if kind == "error")
    assert rejected.status_code == 400
    assert "exceeds source budget amount" in str(rejected.detail).lower()

    source_after = store.get(source.id)
    target_after = store.list(account_id=target_account_id)
    assert source_after is not None
    assert source_after.amount == Decimal("20.00")
    assert source_after.version == 2
    assert len(target_after) == 1
    assert target_after[0].amount == Decimal("80.00")
    assert len(store.list_transfers(source.id)) == 1


def test_sqlite_budget_amount_invariant_blocks_direct_negative_update(tmp_path):
    store = SqliteBudgetStore(str(tmp_path / "nonnegative.db"))
    created = store.create(sqlite_budget_payload(uuid4()))

    with pytest.raises(sqlite3.IntegrityError, match="budget_amount_negative|CHECK constraint failed"):
        with sqlite_conn(store.db_path) as conn:
            conn.execute("UPDATE budgets SET amount='-0.01' WHERE id=?", (str(created.id),))
            conn.commit()

    current = store.get(created.id)
    assert current is not None
    assert current.amount == Decimal("100.00")


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


def test_default_budget_list_excludes_active_budget_under_archived_client():
    reset_state()
    tenant = mk_client("Archived budget owner")
    budget = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="client", amount="500"),
    ).json()

    archived = client.delete(f"/clients/{tenant['id']}")
    assert archived.status_code == 200

    default_rows = client.get("/budgets")
    historical_rows = client.get("/budgets?status=all")
    explicitly_scoped = client.get(f"/budgets?client_id={tenant['id']}")

    assert default_rows.status_code == 200
    assert default_rows.json()["count"] == 0
    assert {row["id"] for row in historical_rows.json()["items"]} == {budget["id"]}
    assert {row["id"] for row in explicitly_scoped.json()["items"]} == {budget["id"]}

    blocked_update = client.patch(f"/budgets/{budget['id']}", json={"amount": "600.00"})
    assert blocked_update.status_code == 409
    assert blocked_update.json()["error"]["code"] == "client_not_active"


def test_active_account_budget_cannot_be_created_for_archived_account():
    reset_state()
    tenant = mk_client("Archived account budget")
    account = mk_account(tenant["id"], external="archived-budget-account")
    assert client.delete(f"/ad-accounts/{account['id']}").status_code == 200

    created = client.post(
        "/budgets",
        json=budget_payload(tenant["id"], scope="account", account_id=account["id"], amount="100"),
    )

    assert created.status_code == 409
    assert created.json()["error"]["code"] == "account_not_active"


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

    assert set(body.keys()) == {"range", "scope", "data_quality", "spend_summary", "budget_summary", "breakdowns"}
    assert body["scope"]["client_id"] == c["id"]
    assert body["scope"]["account_id"] == a["id"]
    assert body["spend_summary"]["spend"] == 700.0
    assert body["budget_summary"]["budget"] == 1000.0  # account overrides client
    assert body["budget_summary"]["budget_source"] == "account"
    assert body["budget_summary"]["pace_status"] in {"on_track", "overspending", "underspending"}
    assert "pace_delta" in body["budget_summary"]
    assert "pace_delta_percent" in body["budget_summary"]
    assert body["data_quality"]["rows_present"] is True
    assert body["data_quality"]["latest_data_date"] == "2026-04-01"
    assert body["data_quality"]["stale_days"] == 14


def test_unified_overview_exposes_insufficient_data_for_empty_period():
    reset_state()
    c = mk_client("No data")
    a = mk_account(c["id"], external="empty", platform="meta")

    res = client.get(
        f"/insights/overview?client_id={c['id']}&account_id={a['id']}&date_from=2026-08-01&date_to=2026-08-06&as_of_date=2026-08-06"
    )

    assert res.status_code == 200
    quality = res.json()["data_quality"]
    assert quality["status"] == "insufficient_data"
    assert quality["rows_present"] is False
    assert quality["latest_data_date"] is None
    assert quality["stale_days"] is None
    assert quality["active_accounts_count"] == 1
    assert quality["accounts_without_data_count"] == 1


def test_unified_overview_freshness_uses_every_active_account(monkeypatch):
    monkeypatch.setenv("AD_STATS_STALE_AFTER_DAYS", "3")
    reset_state()
    c = mk_client("Mixed freshness")
    fresh_account = mk_account(c["id"], external="fresh", platform="meta")
    stale_account = mk_account(c["id"], external="stale", platform="google")
    ingest_row(fresh_account["id"], "2026-08-06", platform="meta")
    ingest_row(stale_account["id"], "2026-08-01", platform="google")

    res = client.get(
        f"/insights/overview?client_id={c['id']}&date_from=2026-08-01&date_to=2026-08-06&as_of_date=2026-08-06"
    )

    assert res.status_code == 200
    quality = res.json()["data_quality"]
    assert quality["status"] == "stale"
    assert quality["latest_data_date"] == "2026-08-01"
    assert quality["stale_days"] == 5
    assert quality["stale_accounts_count"] == 1
    assert quality["coverage_percent"] == 100.0


def test_unified_overview_is_partial_when_active_account_has_no_rows(monkeypatch):
    monkeypatch.setenv("AD_STATS_STALE_AFTER_DAYS", "3")
    reset_state()
    c = mk_client("Partial coverage")
    covered_account = mk_account(c["id"], external="covered", platform="meta")
    mk_account(c["id"], external="missing", platform="google")
    ingest_row(covered_account["id"], "2026-08-06", platform="meta")

    res = client.get(
        f"/insights/overview?client_id={c['id']}&date_from=2026-08-01&date_to=2026-08-06&as_of_date=2026-08-06"
    )

    assert res.status_code == 200
    quality = res.json()["data_quality"]
    assert quality["status"] == "partial"
    assert quality["accounts_without_data_count"] == 1
    assert quality["stale_accounts_count"] == 0
    assert quality["coverage_percent"] == 50.0


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
