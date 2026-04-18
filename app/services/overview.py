from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Optional, Set
from uuid import UUID

from app.services.ad_accounts import AdAccountStore
from app.services.ad_stats import AdStatsStore
from app.services.budgets import BudgetStore, calculate_financial_metrics, utc_today_date


@dataclass
class OverviewService:
    ad_stats_store: AdStatsStore
    ad_account_store: AdAccountStore
    budget_store: BudgetStore

    def dashboard_overview(
        self,
        *,
        date_from: date,
        date_to: date,
        client_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, object]:
        effective_client_id = client_id
        if account_id and not effective_client_id:
            acc = self.ad_account_store.get(account_id)
            effective_client_id = acc.client_id if acc else None

        aggr = self.ad_stats_store.aggregate(
            client_id=effective_client_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )
        totals = aggr["totals"]
        spend = Decimal(str(totals["spend"]))

        budget = self.budget_store.resolve_effective(
            client_id=effective_client_id,
            account_id=account_id,
            period_start=date_from,
            period_end=date_to,
        )

        metric = calculate_financial_metrics(
            spend=spend,
            budget=Decimal(str(budget.amount)) if budget else None,
            period_start=budget.start_date if budget else date_from,
            period_end=budget.end_date if budget else date_to,
            as_of_date=as_of_date or utc_today_date(),
        )

        return {
            "range": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "as_of_date": (as_of_date or utc_today_date()).isoformat(),
                "timezone_policy": metric.date_policy,
            },
            "scope": {
                "client_id": str(effective_client_id) if effective_client_id else None,
                "account_id": str(account_id) if account_id else None,
            },
            "spend_summary": {
                "spend": float(totals["spend"]),
                "impressions": int(totals["impressions"]),
                "clicks": int(totals["clicks"]),
                "conversions": float(totals["conversions"]),
                "ctr": float(totals["ctr"]),
                "cpc": float(totals["cpc"]),
                "cpm": float(totals["cpm"]),
            },
            "budget_summary": {
                "budget": float(metric.budget) if metric.budget is not None else None,
                "spend": float(metric.spend),
                "remaining": float(metric.remaining) if metric.remaining is not None else None,
                "usage_percent": float(metric.usage_percent) if metric.usage_percent is not None else None,
                "expected_spend_to_date": float(metric.expected_spend_to_date) if metric.expected_spend_to_date is not None else None,
                "forecast_spend": float(metric.forecast_spend) if metric.forecast_spend is not None else None,
                "pace_status": metric.pace_status,
                "pace_delta": float(metric.pace_delta) if metric.pace_delta is not None else None,
                "pace_delta_percent": float(metric.pace_delta_percent) if metric.pace_delta_percent is not None else None,
                "budget_source": "account" if budget and budget.account_id else ("client" if budget else None),
                "budget_id": str(budget.id) if budget else None,
            },
            "breakdowns": {
                "platforms": aggr["per_platform"],
                "accounts": aggr["per_account"],
            },
        }

    def agency_overview(
        self,
        *,
        date_from: date,
        date_to: date,
        allowed_client_ids: Optional[Set[UUID]] = None,
    ) -> Dict[str, object]:
        aggr = self.ad_stats_store.aggregate(date_from=date_from, date_to=date_to)
        if allowed_client_ids is not None:
            allowed = {str(x) for x in allowed_client_ids}
            accounts = [x for x in aggr["per_account"] if x.get("client_id") in allowed]
            by_platform: Dict[str, Dict[str, object]] = {}
            by_client: Dict[str, Dict[str, object]] = {}
            totals = {
                "spend": Decimal("0"),
                "impressions": 0,
                "clicks": 0,
                "conversions": Decimal("0"),
            }

            for row in accounts:
                spend = Decimal(str(row["spend"]))
                impressions = int(row["impressions"])
                clicks = int(row["clicks"])
                conversions = Decimal(str(row["conversions"]))
                platform = str(row["platform"])
                client_id = str(row["client_id"])
                totals["spend"] += spend
                totals["impressions"] += impressions
                totals["clicks"] += clicks
                totals["conversions"] += conversions

                if platform not in by_platform:
                    by_platform[platform] = {
                        "platform": platform,
                        "spend": 0.0,
                        "impressions": 0,
                        "clicks": 0,
                        "conversions": 0.0,
                    }
                by_platform[platform]["spend"] += float(spend)
                by_platform[platform]["impressions"] += impressions
                by_platform[platform]["clicks"] += clicks
                by_platform[platform]["conversions"] += float(conversions)

                if client_id not in by_client:
                    by_client[client_id] = {
                        "client_id": client_id,
                        "spend": 0.0,
                        "impressions": 0,
                        "clicks": 0,
                        "conversions": 0.0,
                    }
                by_client[client_id]["spend"] += float(spend)
                by_client[client_id]["impressions"] += impressions
                by_client[client_id]["clicks"] += clicks
                by_client[client_id]["conversions"] += float(conversions)

            def _with_rates(bucket: Dict[str, object]) -> Dict[str, object]:
                spend = Decimal(str(bucket["spend"]))
                impressions = Decimal(str(bucket["impressions"]))
                clicks = Decimal(str(bucket["clicks"]))
                return {
                    **bucket,
                    "ctr": float((clicks / impressions) if impressions > 0 else Decimal("0")),
                    "cpc": float((spend / clicks) if clicks > 0 else Decimal("0")),
                    "cpm": float(((spend * Decimal("1000")) / impressions) if impressions > 0 else Decimal("0")),
                }

            totals["ctr"] = (Decimal(str(totals["clicks"])) / Decimal(str(totals["impressions"]))) if totals["impressions"] > 0 else Decimal("0")
            totals["cpc"] = (Decimal(str(totals["spend"])) / Decimal(str(totals["clicks"]))) if totals["clicks"] > 0 else Decimal("0")
            totals["cpm"] = (
                (Decimal(str(totals["spend"])) * Decimal("1000")) / Decimal(str(totals["impressions"]))
            ) if totals["impressions"] > 0 else Decimal("0")

            per_platform = [_with_rates(x) for x in by_platform.values()]
            per_client = [_with_rates(x) for x in by_client.values()]
            per_account = accounts
        else:
            totals = aggr["totals"]
            per_platform = aggr["per_platform"]
            per_client = aggr["per_client"]
            per_account = aggr["per_account"]

        return {
            "range": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "as_of_date": utc_today_date().isoformat(),
                "timezone_policy": "UTC calendar dates, inclusive period day-count (start/end included).",
            },
            "totals": {
                "spend": float(totals["spend"]),
                "impressions": int(totals["impressions"]),
                "clicks": int(totals["clicks"]),
                "conversions": float(totals["conversions"]),
                "ctr": float(totals["ctr"]),
                "cpc": float(totals["cpc"]),
                "cpm": float(totals["cpm"]),
            },
            "per_platform": per_platform,
            "per_client": per_client,
            "per_account": per_account,
        }
