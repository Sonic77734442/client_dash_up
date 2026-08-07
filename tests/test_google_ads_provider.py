from fastapi import HTTPException
import pytest
from types import SimpleNamespace

from app.services.providers import google_ads


def _credentials():
    return {
        "developer_token": "developer-token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
    }


def test_ads_client_passes_validated_api_version_as_client_argument(monkeypatch):
    captured = {}

    def fake_load(cls, config, version=None):
        captured["config"] = config
        captured["version"] = version
        return object()

    monkeypatch.setenv("GOOGLE_ADS_API_VERSION", "v25")
    monkeypatch.setattr(google_ads.GoogleAdsClient, "load_from_dict", classmethod(fake_load))

    google_ads.ads_client(_credentials())

    assert captured["version"] == "v25"
    assert "version" not in captured["config"]


def test_ads_client_rejects_version_not_supported_by_installed_library(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_API_VERSION", "v19")

    with pytest.raises(HTTPException) as exc_info:
        google_ads.ads_client(_credentials())

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "google_ads_api_version_unsupported"
    assert "v25" in exc_info.value.detail["details"]["supported"]


def test_scoped_google_credentials_do_not_mix_with_global_oauth_secrets(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "global-developer-token")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "global-client-id")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "global-client-secret")
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", "global-refresh-token")

    with pytest.raises(HTTPException) as exc_info:
        google_ads.ads_client({"refresh_token": "tenant-refresh-token"})

    assert exc_info.value.status_code == 500
    assert "credentials are not set" in str(exc_info.value.detail).lower()


def test_empty_scoped_google_record_is_fail_closed(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "global-developer-token")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "global-client-id")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "global-client-secret")
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", "global-refresh-token")

    with pytest.raises(HTTPException):
        google_ads.ads_client({})


def test_scoped_google_zero_accounts_stays_empty_instead_of_using_env_fallback(monkeypatch):
    class CustomerService:
        @staticmethod
        def list_accessible_customers():
            return type("Response", (), {"resource_names": []})()

    class FakeClient:
        @staticmethod
        def get_service(name):
            if name == "CustomerService":
                return CustomerService()
            return object()

    monkeypatch.setenv("GOOGLE_CUSTOMER_IDS", "111-222-3333")
    monkeypatch.setattr(google_ads, "ads_client", lambda _credentials=None: FakeClient())

    assert google_ads.list_accounts(_credentials()) == []


def test_google_discovery_does_not_import_unverified_root_as_leaf_account(monkeypatch):
    class CustomerService:
        @staticmethod
        def list_accessible_customers():
            return type("Response", (), {"resource_names": ["customers/1234567890"]})()

    class GoogleAdsService:
        @staticmethod
        def search(**_kwargs):
            raise RuntimeError("permission denied while reading customer metadata")

    class FakeClient:
        @staticmethod
        def get_service(name):
            return CustomerService() if name == "CustomerService" else GoogleAdsService()

    monkeypatch.setenv("GOOGLE_CUSTOMER_IDS", "999-888-7777")
    monkeypatch.setattr(google_ads, "ads_client", lambda _credentials=None: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        google_ads.list_accounts(_credentials())

    assert exc_info.value.status_code == 502
    assert "could not verify" in str(exc_info.value.detail).lower()


def test_google_daily_campaign_fallback_aggregates_campaign_rows_by_date(monkeypatch):
    def campaign_row(*, impressions, clicks, cost_micros, conversions):
        return SimpleNamespace(
            segments=SimpleNamespace(date="2026-08-01"),
            metrics=SimpleNamespace(
                impressions=impressions,
                clicks=clicks,
                cost_micros=cost_micros,
                conversions=conversions,
            ),
        )

    class GoogleAdsService:
        @staticmethod
        def search(*, customer_id, query):
            assert customer_id == "1234567890"
            if "FROM customer" in query:
                raise RuntimeError("customer resource does not support segmented metrics")
            assert "FROM campaign" in query
            return [
                campaign_row(impressions=100, clicks=10, cost_micros=20_000_000, conversions=2),
                campaign_row(impressions=300, clicks=30, cost_micros=60_000_000, conversions=6),
            ]

    class FakeClient:
        @staticmethod
        def get_service(name):
            assert name == "GoogleAdsService"
            return GoogleAdsService()

    monkeypatch.setattr(google_ads, "ads_client", lambda _credentials=None: FakeClient())

    rows = google_ads.fetch_daily("1234567890", "2026-08-01", "2026-08-01", _credentials())

    assert len(rows) == 1
    assert rows[0] == {
        "date": "2026-08-01",
        "impressions": 400,
        "clicks": 40,
        "spend": 80.0,
        "conversions": 8.0,
        "ctr": pytest.approx(0.1),
        "cpc": pytest.approx(2.0),
        "cpm": pytest.approx(200.0),
        "cpa": pytest.approx(10.0),
    }
