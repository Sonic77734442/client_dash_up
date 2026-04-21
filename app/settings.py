import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from app.schemas import AccountConfig


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str
    host: str
    port: int
    allowed_origins: List[str]
    account_config_path: str
    budgets_db_path: str
    frontend_base_url: str
    auth_cookie_name: str
    auth_cookie_secure: bool
    auth_cookie_samesite: str
    oauth_nonce_cookie_name: str
    oauth_state_ttl_minutes: int
    oauth_session_ttl_minutes: int
    oauth_session_refresh_ttl_minutes: int
    csrf_cookie_name: str
    csrf_header_name: str
    csrf_enforce_cookie_auth: bool
    auth_rate_limit_enabled: bool
    auth_rate_limit_window_seconds: int
    auth_rate_limit_auth_max_requests: int
    auth_rate_limit_invite_max_requests: int
    auth_rate_limit_admin_invite_max_requests: int
    request_log_enabled: bool
    metrics_enabled: bool
    observability_public: bool
    enable_test_endpoints: bool
    api_docs_enabled: bool
    operational_insights_rules: Dict[str, Any]


def _default_operational_insights_rules() -> Dict[str, Any]:
    return {
        "max_items": 6,
        "min_spend_share_for_action": 0.15,
        "high_cpc_multiplier": 1.25,
        "low_cpc_multiplier": 0.9,
        "high_ctr_multiplier": 1.15,
        "low_ctr_multiplier": 0.85,
        "high_priority_score_threshold": 1.0,
        "medium_priority_score_threshold": 0.6,
        "pace_delta_abs_percent_for_review": 15.0,
    }


def _operational_insights_rules_from_env() -> Dict[str, Any]:
    raw = os.getenv("OPERATIONAL_INSIGHTS_RULES_JSON", "").strip()
    if not raw:
        return _default_operational_insights_rules()
    try:
        parsed = json.loads(raw)
    except Exception:
        return _default_operational_insights_rules()
    if not isinstance(parsed, dict):
        return _default_operational_insights_rules()
    merged = _default_operational_insights_rules()
    merged.update(parsed)
    return merged


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
    origins = [x.strip() for x in origins_raw.split(",") if x.strip()]
    app_env = os.getenv("APP_ENV", "development")
    is_prod = app_env.lower() in {"prod", "production"}
    settings = Settings(
        app_env=app_env,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        allowed_origins=[o for o in origins if o != "*"] or ["http://localhost:3000", "http://127.0.0.1:3000"],
        account_config_path=os.getenv("ACCOUNT_CONFIG_PATH", "./accounts.json"),
        budgets_db_path=os.getenv("BUDGETS_DB_PATH", "./storage/budgets.db"),
        frontend_base_url=os.getenv("FRONTEND_BASE_URL", "http://localhost:3000"),
        auth_cookie_name=os.getenv("AUTH_COOKIE_NAME", "ops_session"),
        auth_cookie_secure=_bool_from_env("AUTH_COOKIE_SECURE", False),
        auth_cookie_samesite=os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower() or "lax",
        oauth_nonce_cookie_name=os.getenv("OAUTH_NONCE_COOKIE_NAME", "ops_oauth_nonce"),
        oauth_state_ttl_minutes=max(1, int(os.getenv("OAUTH_STATE_TTL_MINUTES", "10"))),
        oauth_session_ttl_minutes=max(1, int(os.getenv("OAUTH_SESSION_TTL_MINUTES", "1440"))),
        oauth_session_refresh_ttl_minutes=max(1, int(os.getenv("OAUTH_SESSION_REFRESH_TTL_MINUTES", "1440"))),
        csrf_cookie_name=os.getenv("CSRF_COOKIE_NAME", "ops_csrf"),
        csrf_header_name=os.getenv("CSRF_HEADER_NAME", "X-CSRF-Token"),
        csrf_enforce_cookie_auth=_bool_from_env("CSRF_ENFORCE_COOKIE_AUTH", True),
        auth_rate_limit_enabled=_bool_from_env("AUTH_RATE_LIMIT_ENABLED", True),
        auth_rate_limit_window_seconds=max(1, int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60"))),
        auth_rate_limit_auth_max_requests=max(1, int(os.getenv("AUTH_RATE_LIMIT_AUTH_MAX_REQUESTS", "60"))),
        auth_rate_limit_invite_max_requests=max(1, int(os.getenv("AUTH_RATE_LIMIT_INVITE_MAX_REQUESTS", "30"))),
        auth_rate_limit_admin_invite_max_requests=max(
            1, int(os.getenv("AUTH_RATE_LIMIT_ADMIN_INVITE_MAX_REQUESTS", "30"))
        ),
        request_log_enabled=_bool_from_env("REQUEST_LOG_ENABLED", True),
        metrics_enabled=_bool_from_env("METRICS_ENABLED", True),
        observability_public=_bool_from_env("OBSERVABILITY_PUBLIC", not is_prod),
        enable_test_endpoints=_bool_from_env("ENABLE_TEST_ENDPOINTS", False),
        api_docs_enabled=_bool_from_env("API_DOCS_ENABLED", not is_prod),
        operational_insights_rules=_operational_insights_rules_from_env(),
    )
    if settings.auth_cookie_samesite == "none" and not settings.auth_cookie_secure:
        raise ValueError("AUTH_COOKIE_SECURE must be true when AUTH_COOKIE_SAMESITE=none")
    if settings.app_env.lower() in {"prod", "production"} and not settings.auth_cookie_secure:
        raise ValueError("AUTH_COOKIE_SECURE must be true in production")
    return settings


def _accounts_from_env() -> List[AccountConfig]:
    accounts: List[AccountConfig] = []

    def _append(platform: str, env_name: str) -> None:
        raw = os.getenv(env_name, "")
        ids = [x.strip() for x in raw.split(",") if x.strip()]
        for value in ids:
            accounts.append(
                AccountConfig(
                    id=f"{platform}:{value}",
                    platform=platform,
                    external_id=value,
                    name=f"{platform.upper()} {value}",
                )
            )

    _append("meta", "META_ACCOUNT_IDS")
    _append("google", "GOOGLE_CUSTOMER_IDS")
    _append("tiktok", "TIKTOK_ADVERTISER_IDS")
    return accounts


def _accounts_from_file(path: str) -> List[AccountConfig]:
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    accounts: List[AccountConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            acc = AccountConfig(**item)
        except Exception:
            continue
        accounts.append(acc)
    return accounts


def load_accounts(settings: Settings) -> List[AccountConfig]:
    file_accounts = _accounts_from_file(settings.account_config_path)
    env_accounts = _accounts_from_env()

    merged = file_accounts + env_accounts
    unique = {}
    for acc in merged:
        unique[acc.id] = acc
    return list(unique.values())
