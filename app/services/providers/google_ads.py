import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from google.ads.googleads.client import GoogleAdsClient


def normalize_customer_id(customer_id: str) -> str:
    return "".join(ch for ch in str(customer_id or "") if ch.isdigit())


def _api_version() -> str:
    # Keep explicit API version to avoid accidental calls to sunset versions
    # on older client libs/deploy images.
    return (os.getenv("GOOGLE_ADS_API_VERSION", "v18") or "v18").strip()


def valid_customer_id_or_none(customer_id: object) -> Optional[str]:
    normalized = normalize_customer_id(str(customer_id or ""))
    if len(normalized) != 10:
        return None
    return normalized


def ads_client(config_override: Optional[Dict[str, Any]] = None) -> GoogleAdsClient:
    cfg = config_override or {}
    developer_token = str(cfg.get("developer_token") or os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
    client_id = str(cfg.get("client_id") or os.getenv("GOOGLE_ADS_CLIENT_ID") or "").strip()
    client_secret = str(cfg.get("client_secret") or os.getenv("GOOGLE_ADS_CLIENT_SECRET") or "").strip()
    refresh_token = str(cfg.get("refresh_token") or os.getenv("GOOGLE_ADS_REFRESH_TOKEN") or "").strip()
    login_customer_id = str(cfg.get("login_customer_id") or os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").strip() or None

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


def _fallback_accounts() -> List[Dict[str, object]]:
    raw = os.getenv("GOOGLE_CUSTOMER_IDS", "")
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    return [
        {
            "external_account_id": normalize_customer_id(customer_id),
            "name": f"Google {normalize_customer_id(customer_id)}",
            "currency": "USD",
            "source": "env",
        }
        for customer_id in ids
        if normalize_customer_id(customer_id)
    ]


def list_accounts(config_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, object]]:
    try:
        client = ads_client(config_override)
        version = _api_version()
        customer_service = client.get_service("CustomerService", version=version)
        ga_service = client.get_service("GoogleAdsService", version=version)
        response = customer_service.list_accessible_customers()
        out: List[Dict[str, object]] = []
        seen: set[str] = set()

        def add_row(customer_id: str, name: str, currency: str, source: str) -> None:
            if not customer_id or customer_id in seen:
                return
            seen.add(customer_id)
            out.append(
                {
                    "external_account_id": customer_id,
                    "name": name or f"Google {customer_id}",
                    "currency": currency or "USD",
                    "source": source,
                }
            )

        root_ids: List[str] = []
        for resource_name in list(response.resource_names or []):
            cid = normalize_customer_id(str(resource_name).split("/")[-1])
            if cid:
                root_ids.append(cid)

        # 1) Add root accessible accounts.
        for customer_id in root_ids:
            name = f"Google {customer_id}"
            currency = "USD"
            try:
                rows = ga_service.search(
                    customer_id=customer_id,
                    query="SELECT customer.descriptive_name, customer.currency_code FROM customer LIMIT 1",
                )
                for row in rows:
                    if row.customer.descriptive_name:
                        name = str(row.customer.descriptive_name)
                    if row.customer.currency_code:
                        currency = str(row.customer.currency_code)
                    break
            except Exception:
                pass
            add_row(customer_id, name, currency, "api_root")

        # 2) Traverse MCC hierarchy and include leaf/client accounts.
        hierarchy_query = """
            SELECT
              customer_client.id,
              customer_client.descriptive_name,
              customer_client.currency_code,
              customer_client.manager,
              customer_client.level
            FROM customer_client
            WHERE customer_client.level <= 10
        """
        for manager_id in root_ids:
            try:
                rows = ga_service.search(customer_id=manager_id, query=hierarchy_query)
            except Exception:
                continue
            for row in rows:
                child_id = normalize_customer_id(str(row.customer_client.id or ""))
                if not child_id:
                    continue
                if bool(row.customer_client.manager):
                    # Keep only non-manager clients in final list for registry.
                    continue
                add_row(
                    child_id,
                    str(row.customer_client.descriptive_name or f"Google {child_id}"),
                    str(row.customer_client.currency_code or "USD"),
                    "api_mcc",
                )

        if out:
            return out
    except Exception:
        pass
    return _fallback_accounts()


def fetch_insights(
    customer_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, object]], Optional[str]]:
    client = ads_client(config_override)
    ga_service = client.get_service("GoogleAdsService", version=_api_version())

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


def fetch_daily(
    customer_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    client = ads_client(config_override)
    ga_service = client.get_service("GoogleAdsService", version=_api_version())
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
