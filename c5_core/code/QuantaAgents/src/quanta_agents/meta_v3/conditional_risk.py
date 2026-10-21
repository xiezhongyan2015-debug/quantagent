"""Bounded conditional residual selector; numerical evidence, never admission.

The common sixty-row window must be constructed and authenticated by the caller.
Missing common data means a cash *target*, not an assertion about actual holdings.
Fixed quantized score comparisons avoid non-transitive epsilon tie breaking.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from itertools import combinations
import math
import re
from types import MappingProxyType

import numpy as np


_FIXED_POLICY = MappingProxyType({
    "version": "conditional_downside_residual_v1",
    "window": 60,
    "min_downside": 20,
    "max_symbols": 16,
    "selection_count": 10,
    "zero_relative": "1e-12",
    "score_tick": "1e-12",
    "single_name_weight": "0.08",
    "rebalance_sessions": 5,
    "max_absolute_input": "1e150",
    "cap_decimal_places": 12,
})
POLICY = dict(_FIXED_POLICY)


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if number == 0 and value != 0:
        return None
    return number if math.isfinite(number) and abs(number) <= 1e150 else None


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _center(values):
    mean = math.fsum(values) / len(values)
    return [v - mean for v in values]


def _tick(value):
    return int((Decimal(str(value)) / Decimal("1e-12")).to_integral_value(rounding=ROUND_HALF_EVEN))


def evaluate_window(market, returns, *, eligible, single_name_cap="0.08", policy=None, industries=None):
    """Evaluate one already-aligned window, with at most sixteen original stocks.

    Shape/policy violations raise ValueError before numerical work. Common unknown
    observations/eligibility return a recorded cash decision without substitution.
    Only known ineligibility and numerically unresolved residuals exclude a stock.
    """
    if policy is not None and policy != _FIXED_POLICY:
        raise ValueError("unsupported conditional risk policy")
    if not isinstance(returns, dict) or not 1 <= len(returns) <= 16:
        raise ValueError("original symbol count must be between 1 and 16")
    if any(not isinstance(s, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,24}", s) is None for s in returns):
        raise ValueError("invalid bounded symbol identifier")
    if not isinstance(market, list) or len(market) != 60:
        raise ValueError("exact common sixty-observation market window required")
    if any(not isinstance(v, list) or len(v) != 60 for v in returns.values()):
        raise ValueError("every original stock needs the same sixty observations")
    try:
        cap_text = str(single_name_cap)
        if len(cap_text) > 24:
            raise ValueError("single name cap textual bound exceeded")
        cap = Decimal(cap_text)
        if not cap.is_finite() or not Decimal(0) <= cap <= Decimal(1):
            raise ValueError("single name cap outside [0,1]")
        if cap.as_tuple().exponent < -12:
            raise ValueError("single name cap exceeds twelve decimal places")
    except InvalidOperation as exc:
        raise ValueError("invalid single name cap") from exc
    symbols = sorted(returns)
    result = {
        "version": _FIXED_POLICY["version"], "policy": dict(_FIXED_POLICY),
        "status": "target_cash", "reason": None,
        "symbols": symbols, "estimable_symbols": [], "excluded": [], "matrix": {},
        "selected": [], "target_weights": {}, "target_gross_weight": "0", "risk": None,
        "downside_count": 0,
        "work_units": {"input_cells": 60 * (1 + len(symbols)), "projection_cells": 0,
                       "correlation_multiply_adds": 0, "selection_terms": 0,
                       "output_matrix_cells": 0, "eigen_dimension": 0},
        "source_authenticated": False, "execution_valid": False, "formal_target_success": False,
    }

    def cash(reason):
        result["reason"] = reason
        return result

    # Validate all original columns before considering known ineligibility.
    m = [_finite(v) for v in market]
    r = {s: [_finite(v) for v in returns[s]] for s in symbols}
    if any(v is None for v in m) or any(v is None for s in symbols for v in r[s]):
        return cash("common_window_missing_or_nonfinite")
    if not isinstance(eligible, dict) or set(eligible) != set(symbols) or any(type(v) is not bool for v in eligible.values()):
        return cash("common_eligibility_unknown")
    downside = [i for i, value in enumerate(m) if value < 0]
    result["downside_count"] = len(downside)
    if len(downside) < 20:
        return cash("insufficient_common_downside_rows")
    md = [m[i] for i in downside]
    mscale = max(map(abs, md))
    scaled_m = [value / mscale for value in md]
    x = _center(scaled_m)
    xx = _dot(x, x)
    if math.sqrt(xx) <= 1e-12 * math.sqrt(_dot(scaled_m, scaled_m)):
        return cash("conditional_market_variance_numerically_zero")

    units, stds = {}, {}
    for symbol in symbols:
        if not eligible[symbol]:
            result["excluded"].append({"symbol": symbol, "reason": "known_ineligible"})
            continue
        yd = [r[symbol][i] for i in downside]
        scale = max(map(abs, yd))
        scaled_y = [v / scale for v in yd] if scale else [0.0] * len(yd)
        y = _center(scaled_y)
        beta = _dot(y, x) / xx
        u = [a - beta * b for a, b in zip(y, x)]
        # One reorthogonalization reduces cancellation while retaining the same
        # D-only intercept/slope projection. It does not recover lost input bits.
        u = _center(u)
        correction = _dot(u, x) / xx
        u = [a - correction * b for a, b in zip(u, x)]
        norm = math.sqrt(_dot(u, u))
        result["work_units"]["projection_cells"] += len(downside)
        if norm <= 1e-12 * math.sqrt(_dot(scaled_y, scaled_y)):
            result["excluded"].append({"symbol": symbol, "reason": "residual_variance_numerically_zero"})
            continue
        units[symbol] = [v / norm for v in u]
        stds[symbol] = scale * norm / math.sqrt(len(downside) - 1)
    valid = sorted(units)
    result["estimable_symbols"] = valid
    matrix = {s: {s: 1.0} for s in valid}
    for left, right in combinations(valid, 2):
        value = max(-1.0, min(1.0, _dot(units[left], units[right])))
        matrix[left][right] = matrix[right][left] = value
        result["work_units"]["correlation_multiply_adds"] += len(downside)
    result["matrix"] = matrix
    result["work_units"]["output_matrix_cells"] = len(valid) ** 2
    if len(valid) < 10:
        return cash("fewer_than_ten_estimable_original_stocks")

    pairs = list(combinations(valid, 2))
    selected = list(min(pairs, key=lambda pair: (_tick(matrix[pair[0]][pair[1]]), pair)))
    result["work_units"]["selection_terms"] += len(pairs)
    while len(selected) < 10:
        remaining = [s for s in valid if s not in selected]
        scores = {s: math.fsum(matrix[s][t] for t in selected) for s in remaining}
        result["work_units"]["selection_terms"] += len(remaining) * len(selected)
        selected.append(min(remaining, key=lambda s: (_tick(scores[s]), s)))

    weight = min(cap, Decimal("0.08"))
    w = float(weight)
    if w and any((w * stds[s]) ** 2 == 0 for s in selected):
        return cash("target_residual_variance_numeric_underflow")
    average = math.fsum(matrix[a][b] for a, b in combinations(selected, 2)) / 45
    diagonal = math.fsum((w * stds[s]) ** 2 for s in selected)
    off_diagonal = 2 * math.fsum(w * stds[a] * w * stds[b] * matrix[a][b] for a, b in combinations(selected, 2))
    # Sum the weighted residual vectors for a nonnegative variance, retaining the
    # independent diagonal/off-diagonal accounting identity as a diagnostic.
    portfolio = [math.fsum(w * stds[s] * units[s][i] for s in selected) for i in range(len(downside))]
    total = _dot(portfolio, portfolio)
    if total == 0 and any(portfolio):
        return cash("portfolio_residual_variance_numeric_underflow")
    selected_matrix = np.array([[matrix[a][b] for b in selected] for a in selected], dtype=float)
    concentration = float(np.linalg.eigvalsh(selected_matrix)[-1]) / 10
    result["work_units"]["eigen_dimension"] = 10
    industry_report = {"status": "unknown", "weights": None, "max_weight": None, "hhi": None}
    if (isinstance(industries, dict) and all(isinstance(industries.get(s), str) and
            1 <= len(industries[s]) <= 100 for s in selected)):
        grouped = {}
        for s in selected:
            group = industries[s]
            grouped[group] = grouped.get(group, Decimal(0)) + weight
        gross = weight * 10
        industry_report = {"status": "caller_supplied_unverified", "weights": {k: str(v) for k, v in sorted(grouped.items())},
                           "max_weight": str(max(grouped.values())),
                           "hhi": float(sum((v / gross) ** 2 for v in grouped.values())) if gross else None}
    result.update(status="ready", reason="conditional_residual_selection_complete", selected=selected,
                  target_weights={s: str(weight) for s in selected}, target_gross_weight=str(weight * 10),
                  risk={"mean_pair_correlation": average, "standardized_equal_variance": 0.1 + 0.9 * average,
                        "target_residual_variance": total, "diagonal_residual_variance": diagonal,
                        "off_diagonal_residual_variance": off_diagonal,
                        "largest_correlation_share": concentration, "industry": industry_report})
    return result
