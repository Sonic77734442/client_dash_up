from typing import Dict, List, Optional

from app.schemas import AccountConfig
from app.services.date_utils import date_chunks, meta_safe_date_from
from app.services.providers import google_ads, meta, tiktok


def _to_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _sum_metrics(rows: List[Dict[str, object]]) -> Dict[str, float]:
    spend = sum(_to_float(r.get("spend")) for r in rows)
    impressions = sum(_to_float(r.get("impressions")) for r in rows)
    clicks = sum(_to_float(r.get("clicks")) for r in rows)
    ctr = (clicks / impressions) if impressions else 0.0
    cpc = (spend / clicks) if clicks else 0.0
    cpm = (spend / impressions * 1000) if impressions else 0.0
    return {
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "cpc": cpc,
        "cpm": cpm,
    }


def _platform_accounts(accounts: List[AccountConfig], platform: str, account_id: Optional[str]) -> List[AccountConfig]:
    filtered = [a for a in accounts if a.platform == platform]
    if not account_id:
        return filtered
    return [a for a in filtered if a.id == account_id or a.external_id == account_id]


def get_meta_insights(accounts: List[AccountConfig], date_from: str, date_to: str, account_id: Optional[str] = None) -> Dict[str, object]:
    active = _platform_accounts(accounts, "meta", account_id)
    if not active:
        return {"summary": _sum_metrics([]) | {"reach": 0.0, "currency": "USD"}, "campaigns": [], "status": "No Meta accounts configured."}

    campaigns: List[Dict[str, object]] = []
    total_reach = 0.0
    currency = None
    errors = []

    safe_from = meta_safe_date_from(date_from)
    for acc in active:
        try:
            rows = meta.fetch_insights(acc.external_id, safe_from, date_to)
        except Exception as exc:
            errors.append(f"{acc.name or acc.external_id}: {exc}")
            continue

        for row in rows:
            spend = _to_float(row.get("spend"))
            impressions = _to_float(row.get("impressions"))
            clicks = _to_float(row.get("clicks"))
            reach = _to_float(row.get("reach"))
            total_reach += reach
            currency = currency or row.get("account_currency")
            raw_ctr = _to_float(row.get("ctr"))
            ctr = raw_ctr / 100 if raw_ctr > 1 else raw_ctr
            campaigns.append(
                {
                    "campaign_id": row.get("campaign_id"),
                    "campaign_name": row.get("campaign_name"),
                    "account_id": row.get("account_id"),
                    "account_currency": row.get("account_currency"),
                    "spend": spend,
                    "ctr": ctr,
                    "cpc": _to_float(row.get("cpc")),
                    "cpm": _to_float(row.get("cpm")),
                    "reach": reach,
                    "impressions": impressions,
                    "clicks": clicks,
                }
            )

    summary = _sum_metrics(campaigns)
    summary["reach"] = total_reach
    summary["currency"] = currency or "USD"

    status = "Data refreshed."
    if errors and not campaigns:
        status = "Meta token expired or Meta API is unavailable."
    elif errors:
        status = f"Some Meta accounts failed: {len(errors)}"

    return {"summary": summary, "campaigns": campaigns, "status": status}


def get_google_insights(accounts: List[AccountConfig], date_from: str, date_to: str, account_id: Optional[str] = None) -> Dict[str, object]:
    active = _platform_accounts(accounts, "google", account_id)
    if not active:
        return {"summary": _sum_metrics([]) | {"conversions": 0.0, "currency": "USD"}, "campaigns": [], "status": "No Google accounts configured."}

    campaigns: List[Dict[str, object]] = []
    currency = None
    total_conversions = 0.0
    errors = []

    for acc in active:
        customer_id = google_ads.valid_customer_id_or_none(acc.external_id)
        if not customer_id:
            continue
        try:
            rows, acc_currency = google_ads.fetch_insights(customer_id, date_from, date_to)
        except Exception as exc:
            errors.append(f"{acc.name or acc.external_id}: {exc}")
            continue
        currency = currency or acc_currency
        campaigns.extend(rows)
        total_conversions += sum(_to_float(r.get("conversions")) for r in rows)

    summary = _sum_metrics(campaigns)
    summary["conversions"] = total_conversions
    summary["currency"] = currency or "USD"

    status = "Data refreshed."
    if errors and not campaigns:
        status = "Google token expired or Google Ads API is unavailable."
    elif errors:
        status = f"Some Google accounts failed: {len(errors)}"

    return {"summary": summary, "campaigns": campaigns, "status": status}


def get_tiktok_insights(accounts: List[AccountConfig], date_from: str, date_to: str, account_id: Optional[str] = None) -> Dict[str, object]:
    active = _platform_accounts(accounts, "tiktok", account_id)
    if not active:
        return {
            "summary": _sum_metrics([]) | {"currency": "USD"},
            "campaigns": [],
            "adgroups": [],
            "ads": [],
            "status": "No TikTok accounts configured.",
        }

    campaigns: List[Dict[str, object]] = []
    adgroups: List[Dict[str, object]] = []
    ads: List[Dict[str, object]] = []
    errors = []

    metrics = ["spend", "impressions", "clicks", "ctr", "cpc", "cpm"]
    summary_currency = None

    for acc in active:
        summary_currency = summary_currency or acc.currency
        advertiser_id = tiktok.normalize_advertiser_id(acc.external_id)
        try:
            campaign_rows = tiktok.fetch_report(
                advertiser_id, date_from, date_to, "AUCTION_CAMPAIGN", ["campaign_id"], metrics
            )
            adgroup_rows = tiktok.fetch_report(
                advertiser_id, date_from, date_to, "AUCTION_ADGROUP", ["adgroup_id"], metrics
            )
            ad_rows = tiktok.fetch_report(
                advertiser_id, date_from, date_to, "AUCTION_AD", ["ad_id"], metrics
            )
        except Exception as exc:
            errors.append(f"{acc.name or acc.external_id}: {exc}")
            continue

        for row in campaign_rows:
            campaigns.append(
                {
                    "campaign_id": row.get("campaign_id"),
                    "campaign_name": row.get("campaign_name"),
                    "spend": _to_float(row.get("spend")),
                    "impressions": _to_float(row.get("impressions")),
                    "clicks": _to_float(row.get("clicks")),
                    "ctr": _to_float(row.get("ctr")),
                    "cpc": _to_float(row.get("cpc")),
                    "cpm": _to_float(row.get("cpm")),
                }
            )
        for row in adgroup_rows:
            adgroups.append(
                {
                    "adgroup_id": row.get("adgroup_id"),
                    "adgroup_name": row.get("adgroup_name"),
                    "campaign_id": row.get("campaign_id"),
                    "campaign_name": row.get("campaign_name"),
                    "spend": _to_float(row.get("spend")),
                    "impressions": _to_float(row.get("impressions")),
                    "clicks": _to_float(row.get("clicks")),
                    "ctr": _to_float(row.get("ctr")),
                    "cpc": _to_float(row.get("cpc")),
                    "cpm": _to_float(row.get("cpm")),
                }
            )
        for row in ad_rows:
            ads.append(
                {
                    "ad_id": row.get("ad_id"),
                    "ad_name": row.get("ad_name"),
                    "adgroup_id": row.get("adgroup_id"),
                    "adgroup_name": row.get("adgroup_name"),
                    "campaign_id": row.get("campaign_id"),
                    "campaign_name": row.get("campaign_name"),
                    "spend": _to_float(row.get("spend")),
                    "impressions": _to_float(row.get("impressions")),
                    "clicks": _to_float(row.get("clicks")),
                    "ctr": _to_float(row.get("ctr")),
                    "cpc": _to_float(row.get("cpc")),
                    "cpm": _to_float(row.get("cpm")),
                }
            )

    summary = _sum_metrics(campaigns)
    summary["currency"] = summary_currency or "USD"

    status = "Data refreshed."
    if errors and not campaigns:
        status = "TikTok token expired or TikTok API is unavailable."
    elif errors:
        status = f"Some TikTok accounts failed: {len(errors)}"

    return {
        "summary": summary,
        "campaigns": campaigns,
        "adgroups": adgroups,
        "ads": ads,
        "status": status,
    }


def get_overview(
    accounts: List[AccountConfig],
    date_from: str,
    date_to: str,
    *,
    client_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Dict[str, object]:
    totals = {
        "meta": {"spend": 0.0, "impressions": 0.0, "clicks": 0.0},
        "google": {"spend": 0.0, "impressions": 0.0, "clicks": 0.0},
        "tiktok": {"spend": 0.0, "impressions": 0.0, "clicks": 0.0},
    }
    daily_maps: Dict[str, Dict[str, Dict[str, float]]] = {"meta": {}, "google": {}, "tiktok": {}}
    account_totals: Dict[str, Dict[str, object]] = {}

    for acc in [a for a in accounts if a.platform == "meta"]:
        try:
            rows = meta.fetch_daily(acc.external_id, meta_safe_date_from(date_from), date_to)
        except Exception:
            continue
        acc_bucket = account_totals.setdefault(
            acc.id,
            {"account_id": acc.id, "client_id": acc.client_id, "platform": acc.platform, "spend": 0.0, "impressions": 0.0, "clicks": 0.0},
        )
        for row in rows:
            key = str(row.get("date_start") or "")
            if not key:
                continue
            bucket = daily_maps["meta"].setdefault(key, {"date": key, "spend": 0.0, "impressions": 0.0, "clicks": 0.0})
            spend = _to_float(row.get("spend"))
            impressions = _to_float(row.get("impressions"))
            clicks = _to_float(row.get("clicks"))
            bucket["spend"] += spend
            bucket["impressions"] += impressions
            bucket["clicks"] += clicks
            acc_bucket["spend"] += spend
            acc_bucket["impressions"] += impressions
            acc_bucket["clicks"] += clicks

    for acc in [a for a in accounts if a.platform == "google"]:
        customer_id = google_ads.valid_customer_id_or_none(acc.external_id)
        if not customer_id:
            continue
        try:
            rows = google_ads.fetch_daily(customer_id, date_from, date_to)
        except Exception:
            continue
        acc_bucket = account_totals.setdefault(
            acc.id,
            {"account_id": acc.id, "client_id": acc.client_id, "platform": acc.platform, "spend": 0.0, "impressions": 0.0, "clicks": 0.0},
        )
        for row in rows:
            key = str(row.get("date") or "")
            if not key:
                continue
            bucket = daily_maps["google"].setdefault(key, {"date": key, "spend": 0.0, "impressions": 0.0, "clicks": 0.0})
            spend = _to_float(row.get("spend"))
            impressions = _to_float(row.get("impressions"))
            clicks = _to_float(row.get("clicks"))
            bucket["spend"] += spend
            bucket["impressions"] += impressions
            bucket["clicks"] += clicks
            acc_bucket["spend"] += spend
            acc_bucket["impressions"] += impressions
            acc_bucket["clicks"] += clicks

    for acc in [a for a in accounts if a.platform == "tiktok"]:
        advertiser_id = tiktok.normalize_advertiser_id(acc.external_id)
        try:
            for chunk_from, chunk_to in date_chunks(date_from, date_to, 30):
                rows = tiktok.fetch_daily(advertiser_id, chunk_from, chunk_to)
                acc_bucket = account_totals.setdefault(
                    acc.id,
                    {
                        "account_id": acc.id,
                        "client_id": acc.client_id,
                        "platform": acc.platform,
                        "spend": 0.0,
                        "impressions": 0.0,
                        "clicks": 0.0,
                    },
                )
                for row in rows:
                    key = str(row.get("date") or "")
                    if not key:
                        continue
                    bucket = daily_maps["tiktok"].setdefault(
                        key,
                        {"date": key, "spend": 0.0, "impressions": 0.0, "clicks": 0.0},
                    )
                    spend = _to_float(row.get("spend"))
                    impressions = _to_float(row.get("impressions"))
                    clicks = _to_float(row.get("clicks"))
                    bucket["spend"] += spend
                    bucket["impressions"] += impressions
                    bucket["clicks"] += clicks
                    acc_bucket["spend"] += spend
                    acc_bucket["impressions"] += impressions
                    acc_bucket["clicks"] += clicks
        except Exception:
            continue

    daily = {platform: [daily_maps[platform][k] for k in sorted(daily_maps[platform].keys())] for platform in daily_maps}

    for platform in ("meta", "google", "tiktok"):
        totals[platform]["spend"] = sum(r["spend"] for r in daily[platform])
        totals[platform]["impressions"] = sum(r["impressions"] for r in daily[platform])
        totals[platform]["clicks"] = sum(r["clicks"] for r in daily[platform])

    overall = {
        "spend": sum(totals[p]["spend"] for p in totals),
        "impressions": sum(totals[p]["impressions"] for p in totals),
        "clicks": sum(totals[p]["clicks"] for p in totals),
    }
    overall["ctr"] = (overall["clicks"] / overall["impressions"]) if overall["impressions"] else 0.0
    overall["cpc"] = (overall["spend"] / overall["clicks"]) if overall["clicks"] else 0.0
    overall["cpm"] = (overall["spend"] / overall["impressions"] * 1000) if overall["impressions"] else 0.0

    scoped_spend = overall["spend"]
    scope = "global"
    scope_account = None
    scope_client = None
    if account_id:
        scoped_spend = _to_float((account_totals.get(account_id) or {}).get("spend"))
        scope = "account"
        scope_account = account_id
        scope_client = (account_totals.get(account_id) or {}).get("client_id")
    elif client_id:
        scoped_spend = sum(
            _to_float(v.get("spend"))
            for v in account_totals.values()
            if str(v.get("client_id") or "") == str(client_id)
        )
        scope = "client"
        scope_client = client_id

    return {
        "date_from": date_from,
        "date_to": date_to,
        "totals": totals,
        "daily": daily,
        "overall": overall,
        "account_totals": list(account_totals.values()),
        "spend_scope": {
            "scope": scope,
            "client_id": scope_client,
            "account_id": scope_account,
            "spend": scoped_spend,
        },
    }
