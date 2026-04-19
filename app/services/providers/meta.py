import json
import os
from typing import Dict, List

import httpx
from fastapi import HTTPException


API_VERSION = "v20.0"


def _fallback_accounts() -> List[Dict[str, object]]:
    raw = os.getenv("META_ACCOUNT_IDS", "")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    return [{"external_account_id": account_id, "name": f"Meta {account_id}", "currency": "USD", "source": "env"} for account_id in ids]


def list_accounts() -> List[Dict[str, object]]:
    token = os.getenv("META_ACCESS_TOKEN")
    if not token:
        return _fallback_accounts()

    url = f"https://graph.facebook.com/{API_VERSION}/me/adaccounts"
    params = {
        "access_token": token,
        "fields": "id,account_id,name,currency,account_status",
        "limit": 200,
    }
    out: List[Dict[str, object]] = []
    next_url = url
    next_params = params
    while next_url:
        resp = httpx.get(next_url, params=next_params, timeout=20)
        if resp.status_code != 200:
            fallback = _fallback_accounts()
            if fallback:
                return fallback
            raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
        payload = resp.json()
        for row in payload.get("data", []):
            account_id = str(row.get("account_id") or row.get("id") or "").strip()
            account_id = account_id.replace("act_", "")
            if not account_id:
                continue
            out.append(
                {
                    "external_account_id": account_id,
                    "name": str(row.get("name") or f"Meta {account_id}"),
                    "currency": str(row.get("currency") or "USD"),
                    "source": "api",
                }
            )
        paging = payload.get("paging") or {}
        next_url = paging.get("next")
        next_params = None
    if out:
        return out
    return _fallback_accounts()


def fetch_insights(account_external_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
    token = os.getenv("META_ACCESS_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="META_ACCESS_TOKEN is not set")

    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "campaign",
        "fields": "campaign_id,campaign_name,account_id,account_currency,spend,ctr,cpc,cpm,reach,impressions,clicks",
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    resp = httpx.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
    payload = resp.json()
    return payload.get("data", [])


def fetch_daily(account_external_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
    token = os.getenv("META_ACCESS_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="META_ACCESS_TOKEN is not set")

    url = f"https://graph.facebook.com/{API_VERSION}/act_{account_external_id}/insights"
    params = {
        "access_token": token,
        "level": "account",
        "fields": "spend,impressions,clicks",
        "time_increment": 1,
        "time_range": json.dumps({"since": date_from, "until": date_to}),
    }
    resp = httpx.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Meta API error: {resp.text}")
    payload = resp.json()
    return payload.get("data", [])
