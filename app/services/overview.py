from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Optional, Set
from uuid import UUID

from app.services.ad_accounts import AdAccountStore
from app.services.ad_stats import AdStatsStore
from app.services.budgets import BudgetStore, calculate_financial_metrics, utc_today_date
from app.services.metric_aggregation import aggregate_metric_rows, public_metrics


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
        effective_as_of_date = as_of_date or utc_today_date()
        effective_client_id = client_id
        if account_id and not effective_client_id:
            acc = self.ad_account_store.get(account_id)
            effective_client_id = acc.client_id if acc else None

        aggr = self.ad_stats_store.aggregate(
            client_id=effective_client_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            as_of_date=effective_as_of_date,
        )
        totals = aggr["totals"]
        comparable_money = totals["spend"] is not None
        spend = Decimal(str(totals["spend"])) if comparable_money else Decimal(0)

        budget = self.budget_store.resolve_effective(
            client_id=effective_client_id,
            account_id=account_id,
            period_start=date_from,
            period_end=date_to,
        )
        budget_unavailable_reason = None
        if not comparable_money:
            budget_unavailable_reason = "mixed_currencies"
        elif budget and totals["currency"] and budget.currency != totals["currency"]:
            budget_unavailable_reason = "currency_mismatch"
        if budget_unavailable_reason:
            # Mixed or legacy mismatched currencies cannot be compared to a
            # scalar budget even when only one account currently has stats.
            budget = None

        metric = calculate_financial_metrics(
            spend=spend,
            budget=Decimal(str(budget.amount)) if budget else None,
            period_start=budget.start_date if budget else date_from,
            period_end=budget.end_date if budget else date_to,
            as_of_date=effective_as_of_date,
        )

        return {
            "range": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "as_of_date": effective_as_of_date.isoformat(),
                "timezone_policy": metric.date_policy,
            },
            "scope": {
                "client_id": str(effective_client_id) if effective_client_id else None,
                "account_id": str(account_id) if account_id else None,
            },
            "data_quality": aggr["data_quality"],
            "spend_summary": public_metrics(totals),
            "totals_by_currency": aggr["totals_by_currency"],
            "budget_summary": {
                "currency": totals["currency"],
                **({"unavailable_reason": budget_unavailable_reason} if budget_unavailable_reason else {}),
                "budget": float(metric.budget) if metric.budget is not None else None,
                "spend": float(metric.spend) if comparable_money else None,
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
        accounts = aggr["per_account"]
        if allowed_client_ids is not None:
            allowed = {str(x) for x in allowed_client_ids}
            accounts = [row for row in accounts if row.get("client_id") in allowed]
        # Reaggregate the filtered account rows, including currency groups, so
        # other clients cannot affect either totals or the currency breakdown.
        scoped = aggregate_metric_rows(
            accounts,
            empty_currency=aggr["totals"]["currency"] if allowed_client_ids is None else None,
        )
        return {
            "range": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "as_of_date": utc_today_date().isoformat(),
                "timezone_policy": "UTC calendar dates, inclusive period day-count (start/end included).",
            },
            **scoped,
            "totals": public_metrics(scoped["totals"]),
        }
