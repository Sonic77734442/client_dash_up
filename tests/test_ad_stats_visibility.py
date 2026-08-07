from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdAccountCreate, AdStatWrite, AdStatsIngestRequest, ClientCreate
from app.services.ad_accounts import InMemoryAdAccountStore, SqliteAdAccountStore
from app.services.ad_stats import InMemoryAdStatsStore, SqliteAdStatsStore
from app.services.clients import InMemoryClientStore, SqliteClientStore


def _build_stores(kind: str, tmp_path):
    if kind == "sqlite":
        db_path = str(tmp_path / "stats-visibility.db")
        clients = SqliteClientStore(db_path)
        accounts = SqliteAdAccountStore(db_path, clients)
        stats = SqliteAdStatsStore(db_path, accounts)
        return clients, accounts, stats

    clients = InMemoryClientStore()
    accounts = InMemoryAdAccountStore(clients)
    stats = InMemoryAdStatsStore(accounts)
    return clients, accounts, stats


def _seed_stats(clients, accounts, stats):
    tenant = clients.create(ClientCreate(name="Visibility tenant"))
    retained = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="meta",
            external_account_id="retained-account",
            name="Retained account",
        )
    )
    archived = accounts.create(
        AdAccountCreate(
            client_id=tenant.id,
            platform="google",
            external_account_id="archived-account",
            name="Archived account",
        )
    )
    stats.ingest(
        AdStatsIngestRequest(
            rows=[
                AdStatWrite(
                    ad_account_id=retained.id,
                    date=date(2026, 8, 6),
                    platform="meta",
                    impressions=100,
                    clicks=10,
                    spend=Decimal("10.00"),
                    conversions=Decimal("1.00"),
                ),
                AdStatWrite(
                    ad_account_id=archived.id,
                    date=date(2026, 8, 6),
                    platform="google",
                    impressions=200,
                    clicks=20,
                    spend=Decimal("20.00"),
                    conversions=Decimal("2.00"),
                ),
            ]
        )
    )
    return tenant, retained, archived


@pytest.mark.parametrize("kind", ["sqlite", "memory"])
def test_current_stats_hide_archived_entities_but_explicit_history_remains(kind, tmp_path):
    clients, accounts, stats = _build_stores(kind, tmp_path)
    tenant, retained, archived = _seed_stats(clients, accounts, stats)
    period = {
        "date_from": date(2026, 8, 6),
        "date_to": date(2026, 8, 6),
        "as_of_date": date(2026, 8, 6),
    }

    accounts.archive(archived.id)

    global_current = stats.aggregate(**period)
    client_current = stats.aggregate(client_id=tenant.id, **period)
    assert global_current["totals"]["spend"] == Decimal("10.00")
    assert client_current["totals"]["spend"] == Decimal("10.00")
    assert global_current["data_quality"]["row_count"] == 1
    assert global_current["data_quality"]["active_accounts_count"] == 1
    assert {row.ad_account_id for row in stats.list()} == {retained.id}
    assert {row.ad_account_id for row in stats.list(client_id=tenant.id)} == {retained.id}

    archived_account_history = stats.aggregate(account_id=archived.id, **period)
    assert archived_account_history["totals"]["spend"] == Decimal("20.00")
    assert archived_account_history["data_quality"]["rows_present"] is True
    assert archived_account_history["data_quality"]["row_count"] == 1
    assert archived_account_history["data_quality"]["active_accounts_count"] == 1
    assert [row.ad_account_id for row in stats.list(account_id=archived.id)] == [archived.id]

    clients.archive(tenant.id)

    global_after_parent_archive = stats.aggregate(**period)
    assert global_after_parent_archive["totals"]["spend"] == Decimal("0.00")
    assert global_after_parent_archive["data_quality"]["rows_present"] is False
    assert global_after_parent_archive["data_quality"]["row_count"] == 0
    assert stats.list() == []

    archived_client_history = stats.aggregate(client_id=tenant.id, **period)
    assert archived_client_history["totals"]["spend"] == Decimal("30.00")
    assert archived_client_history["data_quality"]["rows_present"] is True
    assert archived_client_history["data_quality"]["row_count"] == 2
    assert archived_client_history["data_quality"]["active_accounts_count"] == 2
    assert {row.ad_account_id for row in stats.list(client_id=tenant.id)} == {retained.id, archived.id}


def test_admin_current_endpoints_exclude_archived_account_from_rows_totals_and_quality():
    http = TestClient(app)
    assert http.post("/_testing/use-inmemory-stores").status_code == 200
    admin = http.post(
        "/auth/internal/users",
        json={"email": "stats-admin@test.local", "name": "Stats admin", "role": "admin", "status": "active"},
    )
    session = http.post(
        "/auth/internal/sessions/issue",
        json={"user_id": admin.json()["id"], "ttl_minutes": 60},
    )
    http.headers.update({"Authorization": f"Bearer {session.json()['token']}"})

    tenant = http.post(
        "/clients",
        json={"name": "Admin visibility", "status": "active", "default_currency": "USD"},
    ).json()

    def create_account(platform: str, external_id: str):
        return http.post(
            "/ad-accounts",
            json={
                "client_id": tenant["id"],
                "platform": platform,
                "external_account_id": external_id,
                "name": external_id,
                "currency": "USD",
                "status": "active",
            },
        ).json()

    retained = create_account("meta", "admin-retained")
    archived = create_account("google", "admin-archived")
    ingested = http.post(
        "/ad-stats/ingest",
        json={
            "rows": [
                {
                    "ad_account_id": retained["id"],
                    "date": "2026-08-06",
                    "platform": "meta",
                    "impressions": 100,
                    "clicks": 10,
                    "spend": "10.00",
                    "conversions": "1.00",
                },
                {
                    "ad_account_id": archived["id"],
                    "date": "2026-08-06",
                    "platform": "google",
                    "impressions": 200,
                    "clicks": 20,
                    "spend": "20.00",
                    "conversions": "2.00",
                },
            ]
        },
    )
    assert ingested.status_code == 200
    assert http.delete(f"/ad-accounts/{archived['id']}").status_code == 200

    current_rows = http.get("/ad-stats?date_from=2026-08-06&date_to=2026-08-06")
    current_overview = http.get(
        "/insights/overview?date_from=2026-08-06&date_to=2026-08-06&as_of_date=2026-08-06"
    )
    active_client_overview = http.get(
        f"/insights/overview?client_id={tenant['id']}&date_from=2026-08-06&date_to=2026-08-06&as_of_date=2026-08-06"
    )

    assert current_rows.status_code == 200
    assert current_rows.json()["count"] == 1
    for response in (current_overview, active_client_overview):
        assert response.status_code == 200
        body = response.json()
        assert body["spend_summary"]["spend"] == 10.0
        assert body["data_quality"]["rows_present"] is True
        assert body["data_quality"]["row_count"] == 1
        assert body["data_quality"]["active_accounts_count"] == 1

    account_history = http.get(
        f"/insights/overview?account_id={archived['id']}&date_from=2026-08-06&date_to=2026-08-06&as_of_date=2026-08-06"
    )
    assert account_history.status_code == 200
    assert account_history.json()["spend_summary"]["spend"] == 20.0
    assert account_history.json()["data_quality"]["row_count"] == 1
