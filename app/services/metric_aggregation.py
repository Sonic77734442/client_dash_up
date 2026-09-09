"""Shared aggregation for persistent/in-memory stats and agency summaries.

Money is additive only within one currency. Ratios retain their own precision;
rounding a CTR fraction to cents would turn a real 0.25% into 0%.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping


MONEY_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.00000001")


def _decimal(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def sum_metric_rows(rows: Iterable[Mapping[str, object]], *, empty_currency: str | None = None) -> dict:
    rows = list(rows)
    currencies = {row.get("currency") for row in rows}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    if not rows:
        currency = empty_currency
    comparable_money = not rows or (currency is not None and all(row.get("spend") is not None for row in rows))
    spend = sum((_decimal(row.get("spend")) for row in rows), Decimal(0)) if comparable_money else None
    impressions = sum(int(row.get("impressions") or 0) for row in rows)
    clicks = sum(int(row.get("clicks") or 0) for row in rows)
    conversions = sum((_decimal(row.get("conversions")) for row in rows), Decimal(0))

    def money_ratio(denominator: int, multiplier: int = 1) -> Decimal | None:
        if spend is None:
            return None
        return ((spend * multiplier / denominator) if denominator else Decimal(0)).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)

    return {
        "currency": currency,
        "spend": spend.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP) if spend is not None else None,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP),
        "ctr": (Decimal(clicks) / impressions if impressions else Decimal(0)).quantize(RATE_PLACES, rounding=ROUND_HALF_UP),
        "cpc": money_ratio(clicks),
        "cpm": money_ratio(impressions, 1000),
    }


def public_metrics(metrics: Mapping[str, object]) -> dict:
    return {key: float(value) if isinstance(value, Decimal) else value for key, value in metrics.items()}


def aggregate_metric_rows(rows: Iterable[Mapping[str, object]], *, empty_currency: str | None = None) -> dict:
    """Accept daily rows or account totals; recalculate weighted rates from counts."""
    rows = list(rows)

    def grouped(field: str, identity_fields: tuple[str, ...] = ()) -> list[dict]:
        groups: dict[object, list] = defaultdict(list)
        for row in rows:
            groups[row.get(field)].append(row)
        result = []
        for key in sorted(groups, key=lambda value: str(value or "")):
            group = groups[key]
            result.append({
                field: key,
                **{name: group[0].get(name) for name in identity_fields},
                **public_metrics(sum_metric_rows(group)),
            })
        return result

    return {
        "totals": sum_metric_rows(rows, empty_currency=empty_currency),
        "totals_by_currency": grouped("currency"),
        "per_platform": grouped("platform"),
        "per_client": grouped("client_id"),
        "per_account": grouped("account_id", ("client_id", "name", "platform")),
    }
