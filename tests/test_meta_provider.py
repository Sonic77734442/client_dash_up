from fastapi import HTTPException
import pytest

from app.services.providers import meta


class _Response:
    def __init__(self, payload=None, *, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {"data": []}

    def json(self):
        return self._payload


def test_meta_provider_uses_configured_graph_api_version(monkeypatch):
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return _Response()

    monkeypatch.setenv("META_GRAPH_API_VERSION", "25")
    monkeypatch.setattr(meta.httpx, "get", fake_get)

    meta.fetch_daily("123456", "2026-08-01", "2026-08-02", {"access_token": "token"})

    assert requested == ["https://graph.facebook.com/v25.0/act_123456/insights"]


def test_meta_provider_rejects_invalid_graph_api_version(monkeypatch):
    monkeypatch.setenv("META_GRAPH_API_VERSION", "latest")

    with pytest.raises(HTTPException) as exc_info:
        meta.fetch_daily("123456", "2026-08-01", "2026-08-02", {"access_token": "token"})

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "meta_graph_api_version_invalid"


def test_scoped_meta_credentials_never_fall_back_to_global_token_or_account_ids(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "global-token")
    monkeypatch.setenv("META_ACCOUNT_IDS", "global-account")

    with pytest.raises(HTTPException) as exc_info:
        meta.list_accounts({"business_ids": []})

    assert exc_info.value.status_code == 500
    assert "tenant-scoped" in str(exc_info.value.detail).lower()


def test_empty_scoped_meta_record_is_fail_closed(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "global-token")
    monkeypatch.setenv("META_ACCOUNT_IDS", "global-account")

    with pytest.raises(HTTPException):
        meta.list_accounts({})


def test_scoped_meta_zero_accounts_stays_empty_instead_of_using_env_fallback(monkeypatch):
    monkeypatch.setenv("META_ACCOUNT_IDS", "global-account")
    monkeypatch.setattr(meta.httpx, "get", lambda *_args, **_kwargs: _Response())

    assert meta.list_accounts({"access_token": "tenant-token", "business_ids": []}) == []


@pytest.mark.parametrize("fetcher", [meta.fetch_daily, meta.fetch_insights])
def test_meta_insights_fetches_all_pages_without_reapplying_first_page_params(monkeypatch, fetcher):
    requests = []
    second_page_url = "https://graph.facebook.com/v25.0/page-two?after=cursor&access_token=token"
    responses = iter(
        [
            _Response(
                {
                    "data": [{"campaign_id": "one", "actions": [{"action_type": "lead", "value": "2"}]}],
                    "paging": {"next": second_page_url},
                }
            ),
            _Response({"data": [{"campaign_id": "two"}]}),
        ]
    )

    def fake_get(url, **kwargs):
        requests.append((url, kwargs.get("params")))
        return next(responses)

    monkeypatch.setattr(meta.httpx, "get", fake_get)

    rows = fetcher("123456", "2026-08-01", "2026-08-02", {"access_token": "token"})

    assert [row["campaign_id"] for row in rows] == ["one", "two"]
    assert rows[0]["conversions"] == 2.0
    assert requests[0][1] is not None
    assert requests[1] == (second_page_url, None)


@pytest.mark.parametrize("fetcher", [meta.fetch_daily, meta.fetch_insights])
def test_meta_insights_surfaces_error_from_later_page(monkeypatch, fetcher):
    responses = iter(
        [
            _Response({"data": [{"campaign_id": "partial"}], "paging": {"next": "https://graph.facebook.com/page-two"}}),
            _Response(status_code=500, text="second page failed"),
        ]
    )
    monkeypatch.setattr(meta.httpx, "get", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(HTTPException) as exc_info:
        fetcher("123456", "2026-08-01", "2026-08-02", {"access_token": "token"})

    assert exc_info.value.status_code == 502
    assert "second page failed" in str(exc_info.value.detail)


def test_meta_insights_fails_instead_of_silently_truncating_at_page_limit(monkeypatch):
    monkeypatch.setattr(meta, "META_INSIGHTS_MAX_PAGES", 2)
    monkeypatch.setattr(
        meta.httpx,
        "get",
        lambda *_args, **_kwargs: _Response(
            {"data": [{"campaign_id": "row"}], "paging": {"next": "https://graph.facebook.com/next"}}
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        meta.fetch_insights("123456", "2026-08-01", "2026-08-02", {"access_token": "token"})

    assert exc_info.value.status_code == 502
    assert "safe limit of 2 pages" in str(exc_info.value.detail)
