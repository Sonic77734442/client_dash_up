import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from app.services.meta_version import meta_graph_api_version

META_INSIGHTS_MAX_PAGES = 100
META_AUTH_ERROR_CODES = {102, 190}
META_PERMISSION_ERROR_CODES = {10, 200}
META_RATE_LIMIT_ERROR_CODES = {4, 17, 32, 613}
DEFAULT_CONVERSION_ACTION_TYPES = {
    "purchase",
    "lead",
    "complete_registration",
    "subscribe",
    "start_trial",
    "submit_application",
    "contact",
    "add_payment_info",
    "initiate_checkout",
}


def _fallback_accounts() -> List[Dict[str, object]]:
    raw = os.getenv("META_ACCOUNT_IDS", "")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    return [{"external_account_id": account_id, "name": f"Meta {account_id}", "currency": "USD", "source": "env"} for account_id in ids]


def _access_token(config_override: Optional[Dict[str, Any]] = None) -> str:
    cfg = config_override or {}
    if config_override is not None:
        return str(cfg.get("access_token") or "").strip()
    return str(os.getenv("META_ACCESS_TOKEN") or "").strip()


def _business_ids(config_override: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = config_override or {}
    raw_value = cfg.get("business_ids")
    if isinstance(raw_value, list):
        return [str(x).strip() for x in raw_value if str(x).strip()]
    raw = str(raw_value or ("" if config_override is not None else os.getenv("META_BUSINESS_IDS")) or "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _extract_account_row(row: Dict[str, object], source: str) -> Dict[str, object]:
    account_id = str(row.get("account_id") or row.get("id") or "").strip().replace("act_", "")
    return {
        "external_account_id": account_id,
        "name": str(row.get("name") or f"Meta {account_id}"),
        "currency": str(row.get("currency") or "USD"),
        "source": source,
    }


def _conversion_action_types() -> set[str]:
    raw = str(os.getenv("META_CONVERSION_ACTION_TYPES", "") or "").strip()
    if not raw:
        return set(DEFAULT_CONVERSION_ACTION_TYPES)
    parsed = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return parsed or set(DEFAULT_CONVERSION_ACTION_TYPES)


def _meta_error_code(response: object) -> Optional[int]:
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    try:
        return int(error.get("code"))
    except (TypeError, ValueError, OverflowError):
        return None


def _raise_meta_api_error(response: object) -> None:
    """Map an untrusted Meta error to a stable, non-sensitive API error."""
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        status_code = 0
    meta_code = _meta_error_code(response)
    if status_code in {401, 403} or meta_code in META_AUTH_ERROR_CODES.union(META_PERMISSION_ERROR_CODES):
        raise HTTPException(
            status_code=401 if meta_code in META_AUTH_ERROR_CODES or status_code == 401 else 403,
            detail={
                "code": "meta_auth_failed",
                "message": "Meta authorization or permissions are invalid. Reconnect Meta Ads.",
            },
        )
    if status_code == 429 or meta_code in META_RATE_LIMIT_ERROR_CODES:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "meta_rate_limited",
                "message": "Meta is temporarily rate-limiting requests. Retry later.",
            },
        )
    if status_code >= 500 or status_code <= 0:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "meta_provider_unavailable",
                "message": "Meta API is temporarily unavailable. Retry later.",
            },
        )
    raise HTTPException(
        status_code=400,
        detail={
            "code": "meta_request_invalid",
            "message": "Meta rejected the request. Check the ad account and connection settings.",
        },
    )


def _raise_meta_response_invalid() -> None:
    raise HTTPException(
        status_code=502,
        detail={
            "code": "meta_response_invalid",
            "message": "Meta returned an invalid response. Retry later or reconnect Meta Ads.",
        },
    )


def _raise_meta_pagination_invalid() -> None:
    raise HTTPException(
        status_code=502,
        detail={"code": "meta_pagination_invalid", "message": "Meta returned an invalid pagination link."},
    )


def _validated_paging_url(value: object) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        _raise_meta_pagination_invalid()
    raw = value.strip()
    if not raw:
        return None
    if len(raw) > 8192:
        _raise_meta_pagination_invalid()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        parsed = None
        port = None
    if (
        parsed is None
        or parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "graph.facebook.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        _raise_meta_pagination_invalid()
    return raw


def _validated_meta_page(response: object) -> tuple[List[Dict[str, object]], Optional[str]]:
    """Validate a successful Graph response before consuming any rows."""
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception:
        _raise_meta_response_invalid()

    if not isinstance(payload, dict):
        _raise_meta_response_invalid()
    if "error" in payload:
        # Meta can return a Graph error in a HTTP 200 response. Treat it exactly
        # like a non-2xx provider error and never consume sibling data.
        _raise_meta_api_error(response)

    if "data" not in payload or not isinstance(payload["data"], list):
        _raise_meta_response_invalid()
    page_rows = payload["data"]
    if any(not isinstance(row, dict) for row in page_rows):
        _raise_meta_response_invalid()

    if "paging" not in payload:
        return page_rows, None
    paging = payload["paging"]
    if not isinstance(paging, dict):
        _raise_meta_pagination_invalid()
    return page_rows, _validated_paging_url(paging.get("next"))


def _sum_actions_conversions(actions: object) -> Optional[float]:
    if not isinstance(actions, list):
        return None
    wanted = _conversion_action_types()
    total = 0.0
    matched = False
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip().lower()
        if not action_type:
            continue
        base_type = action_type.split(".")[-1]
        normalized = base_type
        if normalized.startswith("fb_pixel_"):
            normalized = normalized[len("fb_pixel_") :]
        if action_type not in wanted and base_type not in wanted and normalized not in wanted:
            continue
        try:
            value = float(str(item.get("value") or 0))
        except Exception:
            value = 0.0
        total += value
        matched = True
    if not matched:
        return None
    return total


def _fetch_paginated_insights(url: str, params: Dict[str, object]) -> List[Dict[str, object]]:
    """Fetch every Meta insights page without silently returning partial data."""
    rows: List[Dict[str, object]] = []
    next_url: Optional[str] = url
    next_params: Optional[Dict[str, object]] = params

    for _page_number in range(META_INSIGHTS_MAX_PAGES):
        if not next_url:
            return rows
        resp = httpx.get(next_url, params=next_params, timeout=20)
        if resp.status_code != 200:
            _raise_meta_api_error(resp)

        page_rows, next_url = _validated_meta_page(resp)
        rows.extend(page_rows)
        # Meta's paging.next is already a complete URL (including its cursor and
        # access token). Reapplying the original query parameters can reset the
        # cursor and repeatedly fetch page one.
        next_params = None

    if next_url:
        raise HTTPException(
            status_code=502,
            detail=f"Meta API pagination exceeded the safe limit of {META_INSIGHTS_MAX_PAGES} pages",
        )
    return rows


def list_accounts(config_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, object]]:
    strict_mode = config_override is not None
    token = _access_token(config_override)
    if not token:
        if strict_mode:
            raise HTTPException(status_code=500, detail="Tenant-scoped Meta access token is not set")
        return _fallback_accounts()

    out: List[Dict[str, object]] = []
    seen: set[str] = set()

    def pull(url: str, params: Dict[str, object], source: str) -> None:
        next_url = url
        next_params = params
        while next_url:
            resp = httpx.get(next_url, params=next_params, timeout=20)
            if resp.status_code != 200:
                _raise_meta_api_error(resp)
            page_rows, next_url = _validated_meta_page(resp)
            for row in page_rows:
                parsed = _extract_account_row(row, source)
                account_id = str(parsed["external_account_id"]).strip()
                if not account_id or account_id in seen:
                    continue
                seen.add(account_id)
                out.append(parsed)
            next_params = None

    try:
        # Direct user-accessible ad accounts.
        pull(
            f"https://graph.facebook.com/{meta_graph_api_version()}/me/adaccounts",
            {
                "access_token": token,
                "fields": "id,account_id,name,currency,account_status",
                "limit": 200,
            },
            "api_me",
        )

        # Business Manager accounts (owned + client/shared).
        for business_id in _business_ids(config_override):
            base = f"https://graph.facebook.com/{meta_graph_api_version()}/{business_id}"
            common = {"access_token": token, "fields": "id,account_id,name,currency,account_status", "limit": 200}
            pull(f"{base}/owned_ad_accounts", common, "api_bm_owned")
            pull(f"{base}/client_ad_accounts", common, "api_bm_client")
    except HTTPException:
        if strict_mode:
            raise
        fallback = _fallback_accounts()
        if fallback:
            return fallback
        raise
    except Exception:
        if strict_mode:
            raise HTTPException(status_code=502, detail="Meta account discovery failed")
        fallback = _fallback_accounts()
        if fallback:
            return fallback
        raise HTTPException(status_code=502, detail="Meta account discovery failed")

    if out or strict_mode:
        return out
    return _fallback_accounts()


def fetch_insights(
    account_external_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    token = _access_token(config_override)
    if not token:
        raise HTTPException(status_code=500, detail="META_ACCESS_TOKEN is not set")

    url = f"https://graph.facebook.com/{meta_graph_api_version()}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "campaign",
        "fields": "campaign_id,campaign_name,account_id,account_currency,spend,ctr,cpc,cpm,reach,impressions,clicks,actions",
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    rows = _fetch_paginated_insights(url, params)
    for row in rows:
        conversions = _sum_actions_conversions(row.get("actions"))
        if conversions is not None:
            row["conversions"] = conversions
    return rows


def fetch_daily(
    account_external_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    token = _access_token(config_override)
    if not token:
        raise HTTPException(status_code=500, detail="META_ACCESS_TOKEN is not set")

    url = f"https://graph.facebook.com/{meta_graph_api_version()}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "account",
        "fields": "spend,impressions,clicks,actions",
        "time_increment": 1,
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    rows = _fetch_paginated_insights(url, params)
    for row in rows:
        conversions = _sum_actions_conversions(row.get("actions"))
        if conversions is not None:
            row["conversions"] = conversions
    return rows
