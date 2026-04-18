import os
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from google.ads.googleads.client import GoogleAdsClient


def normalize_customer_id(customer_id: str) -> str:
    return "".join(ch for ch in str(customer_id or "") if ch.isdigit())


def valid_customer_id_or_none(customer_id: object) -> Optional[str]:
    normalized = normalize_customer_id(str(customer_id or ""))
    if len(normalized) != 10:
        return None
    return normalized


def ads_client() -> GoogleAdsClient:
    developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
    client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
    login_customer_id = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None

    if not developer_token or not client_id or not client_secret or not refresh_token:
        raise HTTPException(status_code=500, detail="Google Ads API credentials are not set")

    config = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "use_proto_plus": True,
    }
    if login_customer_id:
        config["login_customer_id"] = login_customer_id
    return GoogleAdsClient.load_from_dict(config)


def fetch_insights(customer_id: str, date_from: str, date_to: str) -> Tuple[List[Dict[str, object]], Optional[str]]:
    client = ads_client()
    ga_service = client.get_service("GoogleAdsService")

    currency = None
    for row in ga_service.search(customer_id=customer_id, query="SELECT customer.currency_code FROM customer LIMIT 1"):
        currency = row.customer.currency_code
        break

    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          metrics.impressions,
          metrics.clicks,
          metrics.ctr,
          metrics.average_cpc,
          metrics.average_cpm,
          metrics.cost_micros,
          metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
    """

    rows = ga_service.search(customer_id=customer_id, query=query)
    campaigns: List[Dict[str, object]] = []
    for row in rows:
        metrics = row.metrics
        campaigns.append(
            {
                "campaign_id": row.campaign.id,
                "campaign_name": row.campaign.name,
                "impressions": int(metrics.impressions or 0),
                "clicks": int(metrics.clicks or 0),
                "ctr": float(metrics.ctr or 0),
                "cpc": float(metrics.average_cpc or 0) / 1_000_000 if metrics.average_cpc else 0,
                "cpm": float(metrics.average_cpm or 0) / 1_000_000 if metrics.average_cpm else 0,
                "spend": float(metrics.cost_micros or 0) / 1_000_000,
                "conversions": float(metrics.conversions or 0),
            }
        )
    return campaigns, currency


def fetch_daily(customer_id: str, date_from: str, date_to: str) -> List[Dict[str, object]]:
    client = ads_client()
    ga_service = client.get_service("GoogleAdsService")
    queries = [
        f"""
            SELECT
              segments.date,
              metrics.impressions,
              metrics.clicks,
              metrics.ctr,
              metrics.average_cpc,
              metrics.average_cpm,
              metrics.cost_micros
            FROM customer
            WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
        """,
        f"""
            SELECT
              segments.date,
              metrics.impressions,
              metrics.clicks,
              metrics.ctr,
              metrics.average_cpc,
              metrics.average_cpm,
              metrics.cost_micros
            FROM campaign
            WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
        """,
    ]
    last_error = None
    for query in queries:
        try:
            rows = ga_service.search(customer_id=customer_id, query=query)
            daily: List[Dict[str, object]] = []
            for row in rows:
                metrics = row.metrics
                daily.append(
                    {
                        "date": str(row.segments.date),
                        "impressions": int(metrics.impressions or 0),
                        "clicks": int(metrics.clicks or 0),
                        "ctr": float(metrics.ctr or 0),
                        "cpc": float(metrics.average_cpc or 0) / 1_000_000 if metrics.average_cpc else 0,
                        "cpm": float(metrics.average_cpm or 0) / 1_000_000 if metrics.average_cpm else 0,
                        "spend": float(metrics.cost_micros or 0) / 1_000_000,
                    }
                )
            return daily
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return []
