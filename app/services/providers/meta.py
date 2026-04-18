import json
import os
from typing import Dict, List

import httpx
from fastapi import HTTPException


API_VERSION = "v20.0"


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
