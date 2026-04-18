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


def test_execute_and_list_operational_actions():
    reset_state()
    c = mk_client("C1")
    a = mk_account(c["id"], external="m1", platform="meta")

    ingest_row(a["id"], "2026-04-01", platform="meta", spend="250.00", impressions=5000, clicks=150, conversions="7")

    created = client.post(
        "/insights/operational/actions",
        json={
            "action": "scale",
            "scope": "account",
            "scope_id": a["id"],
            "title": "Scale META on account",
            "reason": "Good CTR and efficient CPC",
            "metrics": {"platform": "meta", "ctr": 0.03},
            "client_id": c["id"],
            "account_id": a["id"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["action"] == "scale"
    assert body["status"] == "queued"
    assert body["client_id"] == c["id"]
    assert body["account_id"] == a["id"]

    listed = client.get(f"/insights/operational/actions?client_id={c['id']}")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert items[0]["id"] == body["id"]


def test_execute_action_validates_account_client_relation():
    reset_state()
    c1 = mk_client("C1")
    c2 = mk_client("C2")
    a2 = mk_account(c2["id"], external="g1", platform="google")

    bad = client.post(
        "/insights/operational/actions",
        json={
            "action": "cap",
            "scope": "account",
            "scope_id": a2["id"],
            "title": "Cap spend",
            "reason": "High CPC",
            "metrics": {},
            "client_id": c1["id"],
            "account_id": a2["id"],
        },
    )
    assert bad.status_code == 400


def test_execute_account_action_autofills_client_id_from_account():
    reset_state()
    c = mk_client("C1")
    a = mk_account(c["id"], external="m1", platform="meta")

    created = client.post(
        "/insights/operational/actions",
        json={
            "action": "review",
            "scope": "account",
            "scope_id": a["id"],
            "title": "Review account",
            "reason": "No explicit client_id in payload",
            "metrics": {},
            "account_id": a["id"],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["client_id"] == c["id"]
    assert body["account_id"] == a["id"]
