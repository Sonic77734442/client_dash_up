import json
import os
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException


def normalize_advertiser_id(advertiser_id: object) -> str:
    raw = str(advertiser_id or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or raw


def access_token(config_override: Optional[Dict[str, Any]] = None) -> str:
    cfg = config_override or {}
    token = str(cfg.get("access_token") or os.getenv("TIKTOK_ACCESS_TOKEN") or "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="TIKTOK_ACCESS_TOKEN is not set")
    return token


def _fallback_accounts() -> List[Dict[str, object]]:
    raw = os.getenv("TIKTOK_ADVERTISER_IDS", "")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    return [
        {
            "external_account_id": normalize_advertiser_id(advertiser_id),
            "name": f"TikTok {normalize_advertiser_id(advertiser_id)}",
            "currency": "USD",
            "source": "env",
        }
        for advertiser_id in ids
        if normalize_advertiser_id(advertiser_id)
    ]


def list_accounts(config_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, object]]:
    url = "https://business-api.tiktok.com/open_api/v1.3/oauth2/advertiser/get/"
    headers = {"Access-Token": access_token(config_override)}
    try:
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            fallback = _fallback_accounts()
            if fallback:
                return fallback
            raise HTTPException(status_code=502, detail=f"TikTok API error: {resp.text}")
        payload = resp.json()
        if payload.get("code") not in (0, None):
            fallback = _fallback_accounts()
            if fallback:
                return fallback
            raise HTTPException(status_code=502, detail=f"TikTok API error: {payload}")
        data = payload.get("data") or {}
        rows = data.get("list") or data.get("advertisers") or []
        out: List[Dict[str, object]] = []
        for row in rows:
            advertiser_id = normalize_advertiser_id(
                row.get("advertiser_id") or row.get("id") or row.get("advertiserId")
            )
            if not advertiser_id:
                continue
            out.append(
                {
                    "external_account_id": advertiser_id,
                    "name": str(
                        row.get("advertiser_name")
                        or row.get("name")
                        or row.get("advertiserName")
                        or f"TikTok {advertiser_id}"
                    ),
                    "currency": str(row.get("currency") or "USD"),
                    "source": "api",
                }
            )
        if out:
            return out
    except HTTPException:
        raise
    except Exception:
        pass
    return _fallback_accounts()


def fetch_report(
    advertiser_id: str,
    date_from: str,
    date_to: str,
    data_level: str,
    dimensions: List[str],
    metrics: List[str],
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    url = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"

    def _sanitize_dimensions(values: List[str]) -> List[str]:
        blocked = {"campaign_name", "adgroup_name", "ad_name"}
        cleaned = [v for v in values if v not in blocked]
        return cleaned or [v for v in values if v]

    def _request_with_dimensions(current_dimensions: List[str]) -> Dict[str, object]:
        params = {
            "advertiser_id": advertiser_id,
            "report_type": "BASIC",
            "data_level": data_level,
            "dimensions": json.dumps(current_dimensions),
            "metrics": json.dumps(metrics),
            "start_date": date_from,
            "end_date": date_to,
            "page_size": 1000,
        }
        headers = {"Access-Token": access_token(config_override)}
        resp = httpx.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"TikTok API error: {resp.text}")
        return resp.json()

    payload = _request_with_dimensions(dimensions)
    if payload.get("code") not in (0, None):
        message = str(payload.get("message") or "")
        if int(payload.get("code") or 0) == 40002 and "dimensions" in message.lower():
            sanitized = _sanitize_dimensions(dimensions)
            if sanitized != dimensions:
                payload = _request_with_dimensions(sanitized)
        if payload.get("code") not in (0, None):
            raise HTTPException(status_code=502, detail=f"TikTok API error: {payload}")

    data = payload.get("data") or {}
    rows = data.get("list") or []
    results: List[Dict[str, object]] = []
    for row in rows:
        merged = {}
        merged.update(row.get("dimensions") or {})
        merged.update(row.get("metrics") or {})
        results.append(merged)
    return results


def fetch_daily(
    advertiser_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    rows = fetch_report(
        advertiser_id,
        date_from,
        date_to,
        "AUCTION_ADVERTISER",
        ["stat_time_day"],
        ["spend", "impressions", "clicks", "ctr", "cpc", "cpm"],
        config_override=config_override,
    )
    return [
        {
            "date": row.get("stat_time_day"),
            "spend": row.get("spend"),
            "impressions": row.get("impressions"),
            "clicks": row.get("clicks"),
            "ctr": row.get("ctr"),
            "cpc": row.get("cpc"),
            "cpm": row.get("cpm"),
        }
        for row in rows
    ]
