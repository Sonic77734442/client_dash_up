import os
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from google.ads.googleads import client as google_ads_client_module
from google.ads.googleads.client import GoogleAdsClient


def normalize_customer_id(customer_id: str) -> str:
    return "".join(ch for ch in str(customer_id or "") if ch.isdigit())


def _api_version() -> str:
    default_version = str(google_ads_client_module._DEFAULT_VERSION)
    supported_versions = {str(version) for version in google_ads_client_module._VALID_API_VERSIONS}
    raw = str(os.getenv("GOOGLE_ADS_API_VERSION", default_version) or default_version).strip().strip("'\"").rstrip(".")
    m = re.match(r"^v(\d+)$", raw.lower())
    version = f"v{m.group(1)}" if m else ""
    if version not in supported_versions:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "google_ads_api_version_unsupported",
                "message": "Configured Google Ads API version is not supported by the installed client",
                "details": {"configured": raw, "supported": sorted(supported_versions)},
            },
        )
    return version


def valid_customer_id_or_none(customer_id: object) -> Optional[str]:
    normalized = normalize_customer_id(str(customer_id or ""))
    if len(normalized) != 10:
        return None
    return normalized


def ads_client(config_override: Optional[Dict[str, Any]] = None) -> GoogleAdsClient:
    cfg = config_override or {}
    scoped = config_override is not None
    # The developer token identifies the application and may be shared globally;
    # OAuth client/refresh credentials below must remain tenant-scoped.
    developer_token = str(cfg.get("developer_token") or os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
    client_id = str(cfg.get("client_id") or ("" if scoped else os.getenv("GOOGLE_ADS_CLIENT_ID")) or "").strip()
    client_secret = str(
        cfg.get("client_secret") or ("" if scoped else os.getenv("GOOGLE_ADS_CLIENT_SECRET")) or ""
    ).strip()
    refresh_token = str(
        cfg.get("refresh_token") or ("" if scoped else os.getenv("GOOGLE_ADS_REFRESH_TOKEN")) or ""
    ).strip()
    # For explicit per-tenant credentials, do not fall back to global env login_customer_id.
    if scoped:
        login_customer_id = str(cfg.get("login_customer_id") or "").strip() or None
    else:
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
    return GoogleAdsClient.load_from_dict(config, version=_api_version())


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
    strict_mode = config_override is not None
    cfg = config_override or {}
    if strict_mode:
        login_customer_id = valid_customer_id_or_none(cfg.get("login_customer_id") or "")
    else:
        login_customer_id = valid_customer_id_or_none(cfg.get("login_customer_id") or os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "")
    try:
        client = ads_client(config_override)
        customer_service = client.get_service("CustomerService")
        ga_service = client.get_service("GoogleAdsService")
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

        verification_errors = 0

        # 1) Add root accessible accounts.
        for customer_id in root_ids:
            if login_customer_id and customer_id == login_customer_id:
                # Do not include configured MCC manager account as leaf sync target.
                continue
            name = f"Google {customer_id}"
            currency = "USD"
            is_manager: Optional[bool] = None
            try:
                rows = ga_service.search(
                    customer_id=customer_id,
                    query="SELECT customer.descriptive_name, customer.currency_code, customer.manager FROM customer LIMIT 1",
                )
                for row in rows:
                    if row.customer.descriptive_name:
                        name = str(row.customer.descriptive_name)
                    if row.customer.currency_code:
                        currency = str(row.customer.currency_code)
                    is_manager = bool(getattr(row.customer, "manager", False))
                    break
            except Exception:
                verification_errors += 1
                continue
            if is_manager is None:
                verification_errors += 1
                continue
            if is_manager:
                continue
            add_row(customer_id, name, currency, "api_root")

        # 2) Traverse MCC hierarchy and include leaf/client accounts.
        hierarchy_query = """
            SELECT
              customer_client.id,
              customer_client.descriptive_name,
              customer_client.currency_code,
              customer_client.manager,
              customer_client.level,
              customer_client.status
            FROM customer_client
            WHERE customer_client.level <= 10
        """
        for manager_id in root_ids:
            try:
                rows = ga_service.search(customer_id=manager_id, query=hierarchy_query)
            except Exception:
                verification_errors += 1
                continue
            for row in rows:
                child_id = normalize_customer_id(str(row.customer_client.id or ""))
                if not child_id:
                    continue
                if bool(row.customer_client.manager):
                    # Keep only non-manager clients in final list for registry.
                    continue
                status_name = str(getattr(row.customer_client, "status", "") or "").upper()
                if "ENABLED" not in status_name:
                    # Skip non-active client accounts to reduce predictable sync failures.
                    continue
                add_row(
                    child_id,
                    str(row.customer_client.descriptive_name or f"Google {child_id}"),
                    str(row.customer_client.currency_code or "USD"),
                    "api_mcc",
                )

        if out:
            return out
        if strict_mode and verification_errors:
            raise HTTPException(
                status_code=502,
                detail="Google account discovery could not verify accessible customer accounts",
            )
    except HTTPException:
        if strict_mode:
            raise
    except Exception as exc:
        if strict_mode:
            raise HTTPException(status_code=502, detail=f"Google account discovery failed: {exc}")
    return [] if strict_mode else _fallback_accounts()


def detect_login_customer_id(config_override: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Pick a manager (MCC) customer id for tenant-scoped credentials when possible."""
    if config_override is None:
        return None
    try:
        client = ads_client(config_override)
        customer_service = client.get_service("CustomerService")
        ga_service = client.get_service("GoogleAdsService")
        response = customer_service.list_accessible_customers()
        root_ids: List[str] = []
        seen: set[str] = set()
        for resource_name in list(response.resource_names or []):
            cid = normalize_customer_id(str(resource_name).split("/")[-1])
            if cid and cid not in seen:
                seen.add(cid)
                root_ids.append(cid)
        if not root_ids:
            return None

        managers: List[str] = []
        for customer_id in root_ids:
            try:
                rows = ga_service.search(
                    customer_id=customer_id,
                    query="SELECT customer.manager FROM customer LIMIT 1",
                )
                is_manager = False
                for row in rows:
                    is_manager = bool(getattr(row.customer, "manager", False))
                    break
                if is_manager:
                    managers.append(customer_id)
            except Exception:
                continue
        if managers:
            return managers[0]
        return root_ids[0]
    except Exception:
        return None


def fetch_insights(
    customer_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, object]], Optional[str]]:
    client = ads_client(config_override)
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


def fetch_daily(
    customer_id: str,
    date_from: str,
    date_to: str,
    config_override: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, object]]:
    client = ads_client(config_override)
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
              metrics.cost_micros,
              metrics.conversions
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
              metrics.cost_micros,
              metrics.conversions
            FROM campaign
            WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'
        """,
    ]
    last_error = None
    for query in queries:
        try:
            rows = ga_service.search(customer_id=customer_id, query=query)
            by_date: Dict[str, Dict[str, object]] = {}
            for row in rows:
                metrics = row.metrics
                day = str(row.segments.date)
                bucket = by_date.setdefault(
                    day,
                    {
                        "date": day,
                        "impressions": 0,
                        "clicks": 0,
                        "spend": 0.0,
                        "conversions": 0.0,
                    },
                )
                bucket["impressions"] = int(bucket["impressions"]) + int(metrics.impressions or 0)
                bucket["clicks"] = int(bucket["clicks"]) + int(metrics.clicks or 0)
                bucket["spend"] = float(bucket["spend"]) + float(metrics.cost_micros or 0) / 1_000_000
                bucket["conversions"] = float(bucket["conversions"]) + float(metrics.conversions or 0)

            daily: List[Dict[str, object]] = []
            for day in sorted(by_date):
                bucket = by_date[day]
                impressions = int(bucket["impressions"])
                clicks = int(bucket["clicks"])
                spend = float(bucket["spend"])
                conversions = float(bucket["conversions"])
                daily.append(
                    {
                        **bucket,
                        # Derived rates must be recomputed from the aggregated
                        # totals. Averaging per-campaign rates produces incorrect
                        # account-level daily metrics.
                        "ctr": clicks / impressions if impressions else 0.0,
                        "cpc": spend / clicks if clicks else 0.0,
                        "cpm": spend * 1_000 / impressions if impressions else 0.0,
                        "cpa": spend / conversions if conversions else 0.0,
                    }
                )
            return daily
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return []
