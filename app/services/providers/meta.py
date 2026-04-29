import json
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException


API_VERSION = "v20.0"
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
    return str(cfg.get("access_token") or os.getenv("META_ACCESS_TOKEN") or "").strip()


def _business_ids(config_override: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = config_override or {}
    raw_value = cfg.get("business_ids")
    if isinstance(raw_value, list):
        return [str(x).strip() for x in raw_value if str(x).strip()]
    raw = str(raw_value or os.getenv("META_BUSINESS_IDS") or "")
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


def list_accounts(config_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, object]]:
    strict_mode = config_override is not None
    token = _access_token(config_override)
    if not token:
        return _fallback_accounts()

    out: List[Dict[str, object]] = []
    seen: set[str] = set()

    def pull(url: str, params: Dict[str, object], source: str) -> None:
        next_url = url
        next_params = params
        while next_url:
            resp = httpx.get(next_url, params=next_params, timeout=20)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
            payload = resp.json()
            for row in payload.get("data", []):
                parsed = _extract_account_row(row, source)
                account_id = str(parsed["external_account_id"]).strip()
                if not account_id or account_id in seen:
                    continue
                seen.add(account_id)
                out.append(parsed)
            paging = payload.get("paging") or {}
            next_url = paging.get("next")
            next_params = None

    try:
        # Direct user-accessible ad accounts.
        pull(
            f"https://graph.facebook.com/{API_VERSION}/me/adaccounts",
            {
                "access_token": token,
                "fields": "id,account_id,name,currency,account_status",
                "limit": 200,
            },
            "api_me",
        )

        # Business Manager accounts (owned + client/shared).
        for business_id in _business_ids(config_override):
            base = f"https://graph.facebook.com/{API_VERSION}/{business_id}"
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

    if out:
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

    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "campaign",
        "fields": "campaign_id,campaign_name,account_id,account_currency,spend,ctr,cpc,cpm,reach,impressions,clicks,actions",
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    resp = httpx.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
    payload = resp.json()
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if isinstance(row, dict):
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

    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "account",
        "fields": "spend,impressions,clicks,actions",
        "time_increment": 1,
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    resp = httpx.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
    payload = resp.json()
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if isinstance(row, dict):
            conversions = _sum_actions_conversions(row.get("actions"))
            if conversions is not None:
                row["conversions"] = conversions
    return rows
