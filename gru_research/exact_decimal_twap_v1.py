"""Prepared-only exact five-open arithmetic; no replay/provider integration.

Callers must supply five independently source-bound, chronological raw opens.
An aggregated float TWAP cannot be used to recover those inputs. This module
does not infer prices, tick-round a mean, clip a limit, or certify execution.
The sealed V4 data and executor remain unchanged.
"""
from decimal import Decimal, InvalidOperation, localcontext
import json


def _positive_decimal(value):
    # A float is already a binary approximation, so reject it at this interface.
    if type(value) not in (str, Decimal, int):
        raise ValueError("exact source decimal/string/integer required")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid exact source price") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("finite positive exact source price required")
    return number


def exact_five_open_twap(raw_opens):
    """Return the exact arithmetic mean, retaining fractional-cent precision.

    Source byte identity, five timestamps, completeness, observed volume and
    arrival/availability remain separate caller obligations. This function
    deliberately accepts no old aggregated window price or inferred opens.
    """
    if type(raw_opens) not in (list, tuple) or len(raw_opens) != 5:
        raise ValueError("exactly five actual individual raw opens required")
    values = [_positive_decimal(value) for value in raw_opens]
    # Sum and division by five terminate in base ten. Allow sufficient digits
    # for the full input exponent range, the addition carry, and one extra
    # decimal place introduced by /5, independent of the ambient context.
    minimum_exponent = min(value.as_tuple().exponent for value in values)
    maximum_adjusted = max(value.adjusted() for value in values)
    with localcontext() as context:
        context.prec = max(28, maximum_adjusted - minimum_exponent + 4)
        price = sum(values, Decimal(0)) / Decimal(5)
    return price


def require_verified_bounds(raw_price, floor, ceiling):
    """Mirror the existing strict bounds without epsilon or price repair."""
    price = _positive_decimal(raw_price)
    lower = _positive_decimal(floor)
    upper = _positive_decimal(ceiling)
    if lower > upper:
        raise ValueError("invalid verified price bounds")
    if not lower <= price <= upper:
        raise ValueError("raw open outside verified bounds")
    return price


def contract():
    return {
        "status": "PREPARED_EXACT_INDIVIDUAL_FIVE_OPEN_ARITHMETIC_ONLY",
        "input": "five actual source-bound raw price strings/Decimal/integers",
        "requires_independent_source_rows_and_timestamps": True,
        "aggregate_float_price_is_not_an_input": True,
        "output": "exact Decimal mean; fractional cents retained",
        "replay_or_provider_integration_performed": False,
        "original_sealed_sources_modified": False,
        "limit_epsilon_or_tick_rounding_or_clipping": False,
        "execution_or_economic_admission": False,
        "complete_actual_36_replays": False,
        "full_PLAN_complete": False,
    }


if __name__ == "__main__":
    print(json.dumps(contract(), sort_keys=True))
