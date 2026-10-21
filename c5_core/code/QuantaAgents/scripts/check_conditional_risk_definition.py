"""One-shot exact generated-input checks for the unfrozen sixth-case definition.

No input paths, market reads, strategy execution, network, randomness or search.
The sole filesystem operation is exclusive creation of the fixed result JSON.
Correlation (sign, square) is an exact, unique encoding; decimals are display only.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction as F
from pathlib import Path


OUTPUT = Path(__file__).resolve().parents[1] / "validation" / "v4_conditional_risk_definition_003.json"


def avg(values):
    return sum(values, F(0)) / len(values)


def dot(left, right):
    return sum((x * y for x, y in zip(left, right)), F(0))


def ols_residuals(values, market):
    x = [v - avg(market) for v in market]
    y = [v - avg(values) for v in values]
    denominator = dot(x, x)
    if denominator == 0:
        raise ValueError("zero_market_variance")
    beta = dot(x, y) / denominator
    residual = [v - beta * m for v, m in zip(y, x)]
    assert sum(residual) == 0 and dot(residual, market) == 0
    return residual


def downside_indices(market, columns):
    if len(market) != 60 or any(len(v) != 60 for v in columns.values()):
        raise ValueError("incomplete_common_60_day_calendar")
    if any(type(v) is not F for v in market):
        raise ValueError("missing_or_non_rational_input")
    if any(type(v) is not F for c in columns.values() for v in c):
        raise ValueError("missing_or_non_rational_input")
    indices = [k for k, m in enumerate(market) if m < 0]
    if len(indices) < 20:
        raise ValueError("insufficient_downside_sample")
    return indices


def residuals_003(market, columns):
    indices = downside_indices(market, columns)
    selected_market = [market[k] for k in indices]
    result = {}
    for code, values in columns.items():
        residual = ols_residuals([values[k] for k in indices], selected_market)
        if dot(residual, residual) == 0:
            raise ValueError("zero_residual_variance:" + code)
        result[code] = residual
    return result


def residuals_002(market, columns):
    indices = downside_indices(market, columns)
    result = {}
    for code, values in columns.items():
        all_residuals = ols_residuals(values, market)
        selected = [all_residuals[k] for k in indices]
        centered = [v - avg(selected) for v in selected]
        if dot(centered, centered) == 0:
            raise ValueError("zero_residual_variance:" + code)
        result[code] = centered
    return result


def correlation_signature(left, right):
    numerator = dot(left, right)
    denominator = dot(left, left) * dot(right, right)
    if denominator == 0:
        raise ValueError("zero_residual_variance")
    sign = (numerator > 0) - (numerator < 0)
    square = numerator * numerator / denominator
    assert 0 <= square <= 1
    return {"sign": sign, "square_exact": str(square), "correlation_display": sign * math.sqrt(float(square))}


def matrix_signature(residuals):
    return {
        i: {j: correlation_signature(residuals[i], residuals[j]) for j in residuals}
        for i in residuals
    }


def scaled(values, denominator, repeats):
    return [F(v, denominator) for v in values] * repeats


def transform(market, columns, params):
    return {
        code: [a + b * m + c * r if m < 0 else r for m, r in zip(market, values)]
        for code, values in columns.items()
        for a, b, c in [params[code]]
    }


def expect_rejection(market, columns, expected):
    try:
        residuals_003(market, columns)
    except ValueError as exc:
        assert str(exc).startswith(expected), (expected, str(exc))
        return {"rejected": True, "reason": str(exc)}
    raise AssertionError("Expected rejection: " + expected)


def main():
    # Independent reviewer's fixed counterexample; 60 observations, 30 downside.
    market = scaled([-3, -2, -1, 1, 2, 3], 100, 10)
    original = {
        "A": scaled([1, -2, 1, 0, 0, 0], 1000, 10),
        "B": scaled([-1, 0, 1, 1, 0, -1], 1000, 10),
    }
    shifted = transform(market, original, {"A": (F(14, 1000), F(0), F(1)), "B": (F(0), F(0), F(1))})
    before_002 = matrix_signature(residuals_002(market, original))
    after_002 = matrix_signature(residuals_002(market, shifted))
    assert before_002["A"]["B"]["square_exact"] == "0"
    assert after_002["A"]["B"]["sign"] == 1
    assert after_002["A"]["B"]["square_exact"] == "3/4"
    # B is exactly affine in m on D. The corrected definition must reject it;
    # an invented correlation here would conceal the zero-residual boundary.
    before_003 = expect_rejection(market, original, "zero_residual_variance:B")
    after_003 = expect_rejection(market, shifted, "zero_residual_variance:B")

    # Each market value is paired; within-pair reorder preserves univariate
    # distributions, means, scale, market loading and cumulative gross return.
    pair_market = scaled([-3, -3, -2, -2, -1, -1, 1, 2, 3, 4], 100, 6)
    paired = {
        "A": scaled([1, -1, 1, -1, 1, -1, 0, 0, 0, 0], 1000, 6),
        "B": scaled([1, -1, -1, 1, 1, -1, 0, 0, 0, 0], 1000, 6),
        "C": scaled([-1, 1, 1, -1, 1, -1, 0, 0, 0, 0], 1000, 6),
    }
    baseline_residuals = residuals_003(pair_market, paired)
    baseline_matrix = matrix_signature(baseline_residuals)
    constants = {"A": F(14, 1000), "B": F(-7, 1000), "C": F(11, 1000)}
    loadings = {"A": F(-7, 10), "B": F(2, 5), "C": F(3, 10)}
    scales = {"A": F(3, 2), "B": F(2), "C": F(1, 2)}
    transformations = {}
    for kind in ("constant_only", "market_loading_only", "positive_scale_only", "combined"):
        params = {
            code: (
                constants[code] if kind in ("constant_only", "combined") else F(0),
                loadings[code] if kind in ("market_loading_only", "combined") else F(0),
                scales[code] if kind in ("positive_scale_only", "combined") else F(1),
            ) for code in paired
        }
        changed = transform(pair_market, paired, params)
        changed_residuals = residuals_003(pair_market, changed)
        for code in paired:
            assert changed_residuals[code] == [params[code][2] * v for v in baseline_residuals[code]]
        assert matrix_signature(changed_residuals) == baseline_matrix
        transformations[kind] = {
            "parameters_a_b_c": {k: [str(v) for v in values] for k, values in params.items()},
            "raw_returns_transformed_only_on_D": True,
            "residuals_equal_c_times_original_exactly": True,
            "entire_correlation_matrix_equal_exactly": True,
        }

    re_paired = dict(paired)
    re_paired["B"] = scaled([1, -1, -1, 1, -1, 1, 0, 0, 0, 0], 1000, 6)
    for code in paired:
        assert sorted(paired[code]) == sorted(re_paired[code])
        assert dot(paired[code], pair_market) == dot(re_paired[code], pair_market) == 0
    changed_pair_matrix = matrix_signature(residuals_003(pair_market, re_paired))
    assert baseline_matrix["A"]["B"]["sign"] == 1
    assert changed_pair_matrix["A"]["B"]["sign"] == -1
    assert baseline_matrix["A"]["B"]["square_exact"] == changed_pair_matrix["A"]["B"]["square_exact"] == "1/9"

    zero_column = {"LINEAR": [F(7, 1000) + F(3, 2) * m for m in pair_market]}
    missing = {"A": list(paired["A"])}
    missing["A"][0] = None
    rejection_cases = {
        "pure_market_linear_column": expect_rejection(pair_market, zero_column, "zero_residual_variance"),
        "affine_transform_of_zero_residual": expect_rejection(pair_market, transform(pair_market, zero_column, {"LINEAR": (F(5), F(3), F(2))}), "zero_residual_variance"),
        "missing_raw_input": expect_rejection(pair_market, missing, "missing_or_non_rational_input"),
        "insufficient_D": expect_rejection([F(-1, 100)] * 19 + [F(1, 100)] * 41, paired, "insufficient_downside_sample"),
        "zero_conditional_market_variance": expect_rejection([F(-1, 100)] * 30 + [F(1, 100)] * 30, paired, "zero_market_variance"),
    }
    result = {
        "kind": "conditional_risk_definition_generated_exact_check_003",
        "status": "all_assertions_passed",
        "scope": "Fixed generated raw returns only; no strategy or financial evaluation.",
        "arithmetic": "fractions.Fraction; correlation sign and square uniquely encode exact correlation; decimal is display only",
        "counterexample_002": {
            "days": 60, "downside_days": 30, "D_only_shift_A": "7/500",
            "before": before_002, "after": after_002,
            "002_invariant": False,
            "003_before": before_003, "003_after": after_003,
            "003_same_fixture_matrix": "not_evaluable_B_is_market_linear_on_D",
        },
        "003_raw_input_affine_invariance": {"days": 60, "downside_days": 36, "baseline_matrix": baseline_matrix, "checks": transformations},
        "pair_reordering": {
            "each_column_marginal_multiset_unchanged": True,
            "each_column_mean_variance_gross_product_unchanged_by_multiset": True,
            "each_column_market_loading_unchanged_exactly": True,
            "A_B_before": baseline_matrix["A"]["B"], "A_B_after": changed_pair_matrix["A"]["B"],
            "correlation_changes": True,
            "not_a_proof_of_independence_from_entire_T02_research_space": True,
        },
        "rejections": rejection_cases,
        "limitations": ["The proof covers the feature matrix, not unchanged real-account risk or economic attribution.", "Sample estimator verification is not a provenance check, case admission, source availability or financial success.", "Floating-point production behavior and selection/execution are not implemented or tested."],
        "actions": {"market_files_read": 0, "strategy_return_files_read": 0, "sealed_values_read": 0, "network_requests": 0, "strategy_executions": 0, "research_model_calls": 0, "task_registry_mutations": 0, "formal_case_admissions": 0, "formal_target_success": False},
    }
    # Exclusive write: this fixed audit record cannot be replaced by a rerun.
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(OUTPUT), "formal_target_success": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
