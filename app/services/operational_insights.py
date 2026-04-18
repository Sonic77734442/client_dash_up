from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Dict, List, Optional


@dataclass
class OperationalInsightsService:
    rules: Dict[str, float]

    @staticmethod
    def _to_float(v) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    def _priority(self, score: float) -> str:
        if score >= self._to_float(self.rules.get("high_priority_score_threshold", 1.0)):
            return "high"
        if score >= self._to_float(self.rules.get("medium_priority_score_threshold", 0.6)):
            return "medium"
        return "low"

    def generate(
        self,
        *,
        date_from: date,
        date_to: date,
        scope_client_id: Optional[str],
        scope_account_id: Optional[str],
        breakdown_accounts: List[Dict[str, object]],
        budget_summary: Dict[str, object],
    ) -> Dict[str, object]:
        rows = [dict(x) for x in breakdown_accounts]
        if scope_account_id:
            rows = [x for x in rows if str(x.get("account_id")) == str(scope_account_id)]

        total_spend = sum(self._to_float(x.get("spend")) for x in rows) or 1.0
        ctr_values = [self._to_float(x.get("ctr")) for x in rows if self._to_float(x.get("ctr")) > 0]
        cpc_values = [self._to_float(x.get("cpc")) for x in rows if self._to_float(x.get("cpc")) > 0]

        ctr_mid = median(ctr_values) if ctr_values else 0.0
        cpc_mid = median(cpc_values) if cpc_values else 0.0

        min_spend_share = self._to_float(self.rules.get("min_spend_share_for_action", 0.15))
        high_cpc_mul = self._to_float(self.rules.get("high_cpc_multiplier", 1.25))
        low_cpc_mul = self._to_float(self.rules.get("low_cpc_multiplier", 0.9))
        high_ctr_mul = self._to_float(self.rules.get("high_ctr_multiplier", 1.15))
        low_ctr_mul = self._to_float(self.rules.get("low_ctr_multiplier", 0.85))
        pace_review_pct = self._to_float(self.rules.get("pace_delta_abs_percent_for_review", 15.0))

        pace_status = str(budget_summary.get("pace_status") or "on_track")
        pace_delta_percent = budget_summary.get("pace_delta_percent")
        pace_delta_percent = self._to_float(pace_delta_percent) if pace_delta_percent is not None else None

        items: List[Dict[str, object]] = []
        for r in rows:
            account_id = str(r.get("account_id") or "")
            name = str(r.get("name") or account_id[:8])
            platform = str(r.get("platform") or "unknown")
            spend = self._to_float(r.get("spend"))
            cpc = self._to_float(r.get("cpc"))
            ctr = self._to_float(r.get("ctr"))
            conversions = self._to_float(r.get("conversions"))
            spend_share = spend / total_spend

            if cpc_mid > 0 and cpc >= cpc_mid * high_cpc_mul and spend_share >= min_spend_share:
                score = min(2.0, (cpc / cpc_mid) * spend_share)
                items.append(
                    {
                        "scope": "account",
                        "scope_id": account_id,
                        "title": f"Cap {platform.upper()} spend for {name}",
                        "reason": f"CPC {cpc:.2f} is above cohort median {cpc_mid:.2f} with {spend_share * 100:.1f}% spend share.",
                        "action": "cap",
                        "priority": self._priority(score),
                        "score": round(score, 3),
                        "metrics": {
                            "platform": platform,
                            "spend": spend,
                            "cpc": cpc,
                            "ctr": ctr,
                            "spend_share": round(spend_share, 4),
                        },
                    }
                )

            if ctr_mid > 0 and cpc_mid > 0 and ctr >= ctr_mid * high_ctr_mul and cpc <= cpc_mid * low_cpc_mul and conversions > 0:
                score = min(2.0, (ctr / ctr_mid) * (cpc_mid / max(cpc, 0.0001)))
                if pace_status in {"underspending", "on_track"}:
                    items.append(
                        {
                            "scope": "account",
                            "scope_id": account_id,
                            "title": f"Scale {platform.upper()} on {name}",
                            "reason": f"CTR {ctr * 100:.2f}% is above cohort median and CPC {cpc:.2f} is efficient.",
                            "action": "scale",
                            "priority": self._priority(score),
                            "score": round(score, 3),
                            "metrics": {
                                "platform": platform,
                                "spend": spend,
                                "cpc": cpc,
                                "ctr": ctr,
                                "conversions": conversions,
                            },
                        }
                    )

            if ctr_mid > 0 and ctr <= ctr_mid * low_ctr_mul and spend_share >= min_spend_share:
                score = min(2.0, (ctr_mid / max(ctr, 0.0001)) * spend_share)
                items.append(
                    {
                        "scope": "account",
                        "scope_id": account_id,
                        "title": f"Review creatives for {name}",
                        "reason": f"CTR {ctr * 100:.2f}% is below cohort median {ctr_mid * 100:.2f}%.",
                        "action": "review",
                        "priority": self._priority(score),
                        "score": round(score, 3),
                        "metrics": {
                            "platform": platform,
                            "spend": spend,
                            "ctr": ctr,
                            "ctr_median": ctr_mid,
                            "spend_share": round(spend_share, 4),
                        },
                    }
                )

        if pace_delta_percent is not None and abs(pace_delta_percent) >= pace_review_pct:
            score = min(2.0, abs(pace_delta_percent) / 20.0)
            items.append(
                {
                    "scope": "client" if scope_client_id else "agency",
                    "scope_id": scope_client_id or "all",
                    "title": "Review pacing against budget trajectory",
                    "reason": f"Pace delta is {pace_delta_percent:.2f}% vs expected spend-to-date.",
                    "action": "review",
                    "priority": self._priority(score),
                    "score": round(score, 3),
                    "metrics": {
                        "pace_status": pace_status,
                        "pace_delta_percent": pace_delta_percent,
                        "forecast_spend": budget_summary.get("forecast_spend"),
                        "budget": budget_summary.get("budget"),
                    },
                }
            )

        # De-duplicate by (action, scope_id) and keep strongest score.
        dedup: Dict[tuple, Dict[str, object]] = {}
        for item in items:
            key = (item["action"], item["scope_id"])
            prev = dedup.get(key)
            if not prev or self._to_float(item.get("score")) > self._to_float(prev.get("score")):
                dedup[key] = item

        ranked = sorted(dedup.values(), key=lambda x: self._to_float(x.get("score")), reverse=True)
        max_items = int(self._to_float(self.rules.get("max_items", 6)))
        if not ranked:
            # Frontend-friendly fallback: keep list non-empty with an explicit
            # monitoring recommendation when no action thresholds were triggered.
            ranked = [
                {
                    "scope": "client" if scope_client_id else "agency",
                    "scope_id": scope_client_id or "all",
                    "title": "Monitoring: no immediate action required",
                    "reason": "Current metrics are within configured thresholds for this scope. Continue monitoring.",
                    "action": "review",
                    "priority": "low",
                    "score": 0.0,
                    "metrics": {
                        "fallback": True,
                        "pace_status": pace_status,
                        "rows_evaluated": len(rows),
                    },
                }
            ]

        return {
            "range": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            },
            "scope": {
                "client_id": scope_client_id,
                "account_id": scope_account_id,
            },
            "items": ranked[:max_items],
        }
