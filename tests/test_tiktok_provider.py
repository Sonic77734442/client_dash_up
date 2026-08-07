from fastapi import HTTPException
import pytest

from app.services.providers import tiktok


class _Response:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"code": 0, "data": {"list": []}}


def test_scoped_tiktok_credentials_never_fall_back_to_global_token_or_account_ids(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "global-token")
    monkeypatch.setenv("TIKTOK_ADVERTISER_IDS", "998877")

    with pytest.raises(HTTPException) as exc_info:
        tiktok.list_accounts({"advertiser_ids": ["tenant-hint"]})

    assert exc_info.value.status_code == 500
    assert "access_token" in str(exc_info.value.detail).lower()


def test_empty_scoped_tiktok_record_is_fail_closed(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "global-token")
    monkeypatch.setenv("TIKTOK_ADVERTISER_IDS", "998877")

    with pytest.raises(HTTPException):
        tiktok.list_accounts({})


def test_scoped_tiktok_zero_accounts_stays_empty_instead_of_using_env_fallback(monkeypatch):
    monkeypatch.setenv("TIKTOK_ADVERTISER_IDS", "998877")
    monkeypatch.setattr(tiktok.httpx, "get", lambda *_args, **_kwargs: _Response())

    assert tiktok.list_accounts({"access_token": "tenant-token"}) == []
