import pytest

from app.settings import get_settings


def _production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("ENABLE_TEST_ENDPOINTS", "false")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://client-dash-up.vercel.app")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://client-dash-up.vercel.app")
    monkeypatch.setenv("METRICS_BEARER_TOKEN", "test-metrics-token-at-least-24-characters")


def test_production_cors_uses_only_explicit_origins(monkeypatch):
    _production_env(monkeypatch)

    settings = get_settings()

    assert settings.allowed_origins == ["https://client-dash-up.vercel.app"]
    assert all("localhost" not in origin and "127.0.0.1" not in origin for origin in settings.allowed_origins)


def test_production_rejects_wildcard_cors(monkeypatch):
    _production_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(ValueError, match="explicit allowlist"):
        get_settings()


def test_production_rejects_test_endpoints(monkeypatch):
    _production_env(monkeypatch)
    monkeypatch.setenv("ENABLE_TEST_ENDPOINTS", "true")

    with pytest.raises(ValueError, match="ENABLE_TEST_ENDPOINTS"):
        get_settings()


def test_production_requires_secret_for_protected_metrics(monkeypatch):
    _production_env(monkeypatch)
    monkeypatch.delenv("METRICS_BEARER_TOKEN", raising=False)

    with pytest.raises(ValueError, match="METRICS_BEARER_TOKEN"):
        get_settings()
