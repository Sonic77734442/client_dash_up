from datetime import date
from decimal import Decimal
import os
from uuid import uuid4

import pytest

from app.runtime_db import migrate_postgres
from app.schemas import AdAccountCreate, AdStatsIngestRequest, AdStatWrite, BudgetCreate, ClientCreate
from app.services.ad_accounts import InMemoryAdAccountStore, SqliteAdAccountStore
from app.services.ad_stats import InMemoryAdStatsStore, SqliteAdStatsStore
from app.services.budgets import InMemoryBudgetStore
from app.services.clients import InMemoryClientStore, SqliteClientStore
from app.services.metric_aggregation import aggregate_metric_rows
from app.services.overview import OverviewService
from app.services.operational_insights import OperationalInsightsService


DAY = date(2026, 9, 1)


@pytest.fixture(params=["memory", "sqlite", "postgresql"])
def stores(request, monkeypatch, tmp_path):
    backend = request.param
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite" if backend == "memory" else backend)
    if backend == "postgresql":
        url = os.getenv("TEST_DATABASE_URL", "").strip()
        if not url:
            pytest.skip("TEST_DATABASE_URL required for PostgreSQL regressions")
        monkeypatch.setenv("DATABASE_URL", url)
        migrate_postgres(url)
    if backend == "memory":
        clients = InMemoryClientStore()
        accounts = InMemoryAdAccountStore(clients)
        stats = InMemoryAdStatsStore(accounts)
    else:
        path = str(tmp_path / "metrics.sqlite")
        clients = SqliteClientStore(path)
        accounts = SqliteAdAccountStore(path, clients)
        stats = SqliteAdStatsStore(path, accounts)
    return clients, accounts, stats, OverviewService(stats, accounts, InMemoryBudgetStore())


def seed(stores, currency, spend="100.00", clicks=25, impressions=10000):
    clients, accounts, stats, _ = stores
    client = clients.create(ClientCreate(name=f"Metric test {currency}", default_currency=currency))
    account = accounts.create(AdAccountCreate(
        client_id=client.id, name=currency, currency=currency,
        platform="meta", external_account_id=str(uuid4().int),
    ))
    stats.ingest(AdStatsIngestRequest(rows=[AdStatWrite(
        ad_account_id=account.id, date=DAY, platform="meta", spend=Decimal(spend),
        clicks=clicks, impressions=impressions,
    )]))
    return client, account


def test_sub_percent_ctr_survives_every_aggregate_and_overview(stores):
    client, account = seed(stores, "USD")
    _, _, stats, overview = stores
    result = stats.aggregate(client_id=client.id, date_from=DAY, date_to=DAY)
    assert result["totals"]["ctr"] == Decimal("0.0025")
    assert result["totals"]["spend"] == Decimal("100.00")
    for key in ("per_account", "per_client", "per_platform", "totals_by_currency"):
        assert result[key][0]["ctr"] == 0.0025
        assert result[key][0]["currency"] == "USD"
    dashboard = overview.dashboard_overview(client_id=client.id, account_id=account.id, date_from=DAY, date_to=DAY)
    assert dashboard["spend_summary"]["ctr"] * 100 == 0.25
    assert dashboard["spend_summary"]["spend"] == 100.0
    agency = overview.agency_overview(date_from=DAY, date_to=DAY, allowed_client_ids={client.id})
    assert agency["totals"]["ctr"] == 0.0025


def test_agency_separates_currencies_and_filters_currency_totals_to_allowed_clients(stores):
    usd, _ = seed(stores, "USD")
    kzt, _ = seed(stores, "KZT", spend="50000.00", clicks=10)
    seed(stores, "EUR", spend="999.00")
    overview = stores[3]
    result = overview.agency_overview(date_from=DAY, date_to=DAY, allowed_client_ids={usd.id, kzt.id})
    assert result["totals"]["currency"] is None
    assert result["totals"]["spend"] is None
    assert result["totals"]["cpc"] is None
    assert result["totals"]["cpm"] is None
    assert result["totals"]["clicks"] == 35
    assert result["totals"]["ctr"] == 0.00175
    by_currency = {row["currency"]: row["spend"] for row in result["totals_by_currency"]}
    assert by_currency == {"USD": 100.0, "KZT": 50000.0}
    assert result["per_platform"][0]["spend"] is None
    assert all(row["currency"] in {"USD", "KZT"} and row["spend"] is not None for row in result["per_account"])

    single = overview.agency_overview(date_from=DAY, date_to=DAY, allowed_client_ids={usd.id})
    assert single["totals"]["currency"] == "USD"
    assert single["totals"]["spend"] == 100.0
    assert len(single["totals_by_currency"]) == 1
    empty = overview.agency_overview(date_from=DAY, date_to=DAY, allowed_client_ids=set())
    assert empty["totals"]["spend"] == 0.0
    assert empty["totals_by_currency"] == []


def test_weighted_ctr_recomputed_from_counts_not_rounded_row_rates():
    result = aggregate_metric_rows([
        {"currency": "USD", "spend": "0.01", "clicks": 1, "impressions": 10000, "ctr": 0},
        {"currency": "USD", "spend": "0.02", "clicks": 3, "impressions": 10000, "ctr": 0},
    ])
    assert result["totals"]["ctr"] == Decimal("0.0002")
    assert result["totals"]["spend"] == Decimal("0.03")


def test_mixed_dashboard_does_not_compare_money_to_scalar_budget(stores):
    # A scope containing legacy mixed-currency data must stay readable without
    # publishing a fabricated scalar spend or forecast.
    client, _ = seed(stores, "USD")
    _, accounts, stats, overview = stores
    legacy = accounts.create(AdAccountCreate(
        client_id=client.id, platform="meta", name="Legacy KZT", currency="KZT", external_account_id=str(uuid4().int),
    ))
    stats.ingest(AdStatsIngestRequest(rows=[AdStatWrite(
        ad_account_id=legacy.id, date=DAY, platform="meta", spend=Decimal("50000"),
    )]))
    result = overview.dashboard_overview(client_id=client.id, date_from=DAY, date_to=DAY)
    assert result["spend_summary"]["spend"] is None
    assert result["budget_summary"]["spend"] is None
    assert result["budget_summary"]["forecast_spend"] is None
    assert len(result["totals_by_currency"]) == 2


def test_recommendations_do_not_compare_nominal_cpc_across_currencies():
    rows = [
        {"account_id": "usd", "name": "USD", "currency": "USD", "platform": "meta", "spend": 100, "cpc": 1, "ctr": 0.01},
        {"account_id": "kzt", "name": "KZT", "currency": "KZT", "platform": "meta", "spend": 50000, "cpc": 500, "ctr": 0.01},
    ]
    result = OperationalInsightsService({}).generate(
        date_from=DAY, date_to=DAY, scope_client_id=None, scope_account_id=None,
        breakdown_accounts=rows, budget_summary={},
        data_quality={"status": "fresh", "rows_present": True},
    )
    assert not any(item["action"] == "cap" for item in result["items"])


def test_legacy_mismatched_budget_currency_never_compares_nominal_amounts(stores):
    client, _ = seed(stores, "USD")
    overview = stores[3]
    overview.budget_store.create(BudgetCreate(
        client_id=client.id, scope="client", amount=Decimal("50000"), currency="KZT",
        period_type="custom", start_date=DAY, end_date=date(2026, 9, 30),
    ))
    result = overview.dashboard_overview(client_id=client.id, date_from=DAY, date_to=DAY)
    assert result["spend_summary"]["spend"] == 100.0
    assert result["budget_summary"]["currency"] == "USD"
    assert result["budget_summary"]["budget"] is None
    assert result["budget_summary"]["remaining"] is None
    assert result["budget_summary"]["usage_percent"] is None
    assert result["budget_summary"]["unavailable_reason"] == "currency_mismatch"


def test_empty_client_with_no_accounts_keeps_its_currency_and_budget(stores):
    clients, _, stats, overview = stores
    client = clients.create(ClientCreate(name="New KZT client", default_currency="KZT"))
    result = stats.aggregate(client_id=client.id, date_from=DAY, date_to=DAY)
    assert result["totals"]["currency"] == "KZT"
    assert result["totals"]["spend"] == 0
    overview.budget_store.create(BudgetCreate(
        client_id=client.id, scope="client", currency="KZT", amount=Decimal("50000"),
        period_type="custom", start_date=DAY, end_date=date(2026, 9, 30),
    ))
    dashboard = overview.dashboard_overview(client_id=client.id, date_from=DAY, date_to=DAY)
    assert dashboard["spend_summary"]["currency"] == "KZT"
    assert dashboard["budget_summary"]["currency"] == "KZT"
    assert dashboard["budget_summary"]["budget"] == 50000.0
    assert dashboard["budget_summary"]["spend"] == 0.0
    assert "unavailable_reason" not in dashboard["budget_summary"]
