from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_db import migrate_postgres
from app.schemas import AdAccountCreate, AdAccountPatch, AdStatWrite, AdStatsIngestRequest, ClientCreate
from app.services.ad_account_sync import AdAccountSyncService, SqliteAdAccountSyncJobStore
from app.services.ad_accounts import SqliteAdAccountStore
from app.services.ad_stats import SqliteAdStatsStore
from app.services.clients import SqliteClientStore
from app.services.provider_metrics import ProviderPayloadValidationError
from app.services.providers import google_ads, meta


@pytest.fixture(params=["sqlite", "postgresql"])
def sync_stores(request, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_BACKEND", request.param)
    if request.param == "postgresql":
        url = os.getenv("TEST_DATABASE_URL", "").strip()
        if not url:
            pytest.skip("TEST_DATABASE_URL is required for PostgreSQL numeric sync tests")
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_AUTO_MIGRATE", "false")
        migrate_postgres(url)
    path = str(tmp_path / "numeric-sync.sqlite")
    clients = SqliteClientStore(path)
    accounts = SqliteAdAccountStore(path, clients)
    stats = SqliteAdStatsStore(path, accounts)
    jobs = SqliteAdAccountSyncJobStore(path)
    client = clients.create(ClientCreate(name="Numeric sync regression"))
    account = accounts.create(
        AdAccountCreate(client_id=client.id, platform="google", external_account_id=str(uuid4().int), name="Google")
    )
    return accounts, stats, jobs, account


def _run(sync_stores, rows):
    accounts, stats, jobs, account = sync_stores
    service = AdAccountSyncService(
        accounts, jobs, stats,
        provider_fetchers={"google": lambda external, start, end, credentials: rows},
    )
    return service.run_sync(account_ids=[account.id], date_from=date(2026, 9, 1), date_to=date(2026, 9, 2), force=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("spend", "not-a-number"),
        ("spend", ""),
        ("spend", None),
        ("spend", "NaN"),
        ("spend", float("inf")),
        ("spend", "-0.001"),
        ("spend", "999999999999.995"),
        ("spend", "1e100"),
        ("clicks", "1.5"),
        ("impressions", 2**63),
        ("impressions", True),
        ("conversions", "Infinity"),
    ],
)
def test_bad_numeric_row_preserves_existing_batch_and_freshness(sync_stores, field, value):
    accounts, stats, jobs, account = sync_stores
    original_meta = {
        "last_sync_at": "2026-09-01T12:00:00",
        "last_sync_success_at": "2026-09-01T12:00:00",
        "latest_data_date": "2026-09-01",
        "last_data_at": "2026-09-01",
    }
    accounts.patch(account.id, AdAccountPatch(metadata=original_meta))
    stats.ingest(AdStatsIngestRequest(rows=[
        AdStatWrite(ad_account_id=account.id, date=date(2026, 9, 1), platform="google", impressions=100, clicks=10, spend="25.00")
    ]))
    before = stats.list(account_id=account.id)
    rows = [
        {"date": "2026-09-01", "impressions": "5", "clicks": "1", "spend": "1.00"},
        {"date": "2026-09-02", "impressions": "10", "clicks": "1", "spend": "2.00", field: value},
    ]
    result = _run(sync_stores, rows)
    assert result.failed == 1 and result.success == 0
    job = result.jobs[0]
    assert job.records_synced == 0
    assert job.error_code == "provider_payload_invalid"
    assert job.error_category == "validation" and job.retryable is False
    assert job.request_meta["latest_data_date"] is None
    assert stats.list(account_id=account.id) == before
    current = accounts.get(account.id)
    assert current.sync_status == "error"
    for key, value in original_meta.items():
        assert current.metadata[key] == value
    assert "history_backfill_completed_at" not in current.metadata
    assert jobs.list(account_id=account.id)[0].error_code == "provider_payload_invalid"


def test_valid_counts_remain_exact_and_money_rounds_half_up(sync_stores):
    _, stats, _, account = sync_stores
    result = _run(sync_stores, [
        {"date": "2026-09-01", "impressions": "9007199254740993", "clicks": "1.0", "spend": "1.005", "conversions": "0.005"},
        {"date": "2026-09-02", "impressions": 0, "clicks": "0", "spend": "0.00", "conversions": 0},
    ])
    assert result.success == 1 and result.failed == 0
    by_date = {row.date: row for row in stats.list(account_id=account.id)}
    first = by_date[date(2026, 9, 1)]
    assert first.impressions == 9007199254740993
    assert first.clicks == 1
    assert first.spend == Decimal("1.01") and first.conversions == Decimal("0.01")
    assert by_date[date(2026, 9, 2)].spend == Decimal("0.00")


def test_legacy_custom_fetcher_missing_metrics_still_defaults_to_zero(sync_stores):
    _, stats, _, account = sync_stores
    result = _run(sync_stores, [{"date": "2026-09-01"}])
    assert result.success == 1
    row = stats.list(account_id=account.id)[0]
    assert row.impressions == row.clicks == row.spend == 0
    assert row.conversions is None


@pytest.mark.parametrize("missing", ["spend", "impressions", "clicks"])
def test_builtin_provider_payload_requires_requested_metrics(missing):
    row = {"date": "2026-09-01", "spend": 0, "impressions": 0, "clicks": 0}
    del row[missing]
    with pytest.raises(ProviderPayloadValidationError, match=f"missing {missing}"):
        AdAccountSyncService._validated_provider_rows(
            [row], requested_from="2026-09-01", requested_to="2026-09-01", require_metrics=True
        )


@pytest.mark.parametrize("value", ["invalid", "NaN", "-1", "1e100", None])
def test_meta_selected_conversion_action_never_silently_becomes_zero(value):
    with pytest.raises(ProviderPayloadValidationError):
        meta._sum_actions_conversions([{"action_type": "purchase", "value": value}])


def test_meta_conversion_action_values_are_summed_before_cent_rounding():
    assert meta._sum_actions_conversions([
        {"action_type": "purchase", "value": "0.004"},
        {"action_type": "lead", "value": "0.004"},
    ]) == Decimal("0.008")


@pytest.mark.parametrize("field,value", [("clicks", "1.5"), ("cost_micros", "bad"), ("conversions", "-1")])
def test_google_daily_invalid_numeric_payload_does_not_try_another_query(monkeypatch, field, value):
    calls = []
    metrics = {"impressions": 100, "clicks": 10, "cost_micros": 1000000, "conversions": 1}
    metrics[field] = value

    def search(**kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(segments=SimpleNamespace(date="2026-09-01"), metrics=SimpleNamespace(**metrics))]

    service = SimpleNamespace(search=search)
    monkeypatch.setattr(google_ads, "ads_client", lambda *_args: SimpleNamespace(get_service=lambda _name: service))
    with pytest.raises(ProviderPayloadValidationError):
        google_ads.fetch_daily("1234567890", "2026-09-01", "2026-09-01")
    assert len(calls) == 1
