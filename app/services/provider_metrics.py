from __future__ import annotations

from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext


class ProviderPayloadValidationError(ValueError):
    """Provider metrics cannot be stored without fabricating or losing data."""


MAX_COUNT = 2**63 - 1
MAX_MONEY = Decimal("999999999999.99")


def provider_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ProviderPayloadValidationError(f"Provider {field} must be numeric")
    try:
        parsed = Decimal(str(value).strip())
    except (DecimalException, ValueError) as exc:
        raise ProviderPayloadValidationError(f"Provider {field} must be numeric") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ProviderPayloadValidationError(f"Provider {field} must be finite and nonnegative")
    return parsed


def provider_count(value: object, *, field: str) -> int:
    parsed = provider_decimal(value, field=field)
    if parsed > MAX_COUNT or parsed != parsed.to_integral_value():
        raise ProviderPayloadValidationError(f"Provider {field} must be a nonnegative 64-bit integer")
    return int(parsed)


def provider_money(value: object, *, field: str) -> Decimal:
    parsed = provider_decimal(value, field=field)
    if parsed >= Decimal("1000000000000"):
        raise ProviderPayloadValidationError(f"Provider {field} exceeds supported numeric(14,2) range")
    try:
        with localcontext() as context:
            context.prec = 32
            rounded = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ProviderPayloadValidationError(f"Provider {field} cannot be represented as numeric(14,2)") from exc
    if rounded > MAX_MONEY:
        raise ProviderPayloadValidationError(f"Provider {field} exceeds supported numeric(14,2) range")
    return rounded
