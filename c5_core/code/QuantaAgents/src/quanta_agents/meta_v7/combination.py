"""Training-only, frozen numeric combinations using the existing strategy DSL.

Inputs must already be causal registered factor frames. This module neither
loads market data nor validates the historical availability of an input vendor.
The caller declares lambda and interactions before fitting; no validation label
is used to choose either. All model diagnostics are in-sample descriptions.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math

import numpy as np
import pandas as pd
from pandas.api.types import is_complex_dtype, is_numeric_dtype

from quanta_agents.research_kernel.compiler import (
    _axes, canonical_expression, evaluate_expression,
)


VERSION = "v9_frozen_rank_combination_v1"
TARGET = "same_day_common_sample_percentile_rank_return_demeaned"
DEFAULT_SELECTION = {
    "min_coverage": .6, "min_abs_mean_ic": 0., "min_ic_days": 60,
    "min_annual_days": 20, "min_stable_year_fraction": .6,
    "min_usable_years": 1, "max_abs_correlation": .9,
    "max_factors": 6, "min_cross_section": 5,
}


class CombinationFitError(ValueError):
    """A failed fit with a JSON-serializable audit that callers should retain."""

    def __init__(self, message, audit=None):
        super().__init__(message)
        self.audit = deepcopy(audit or {})
        self.audit.update(status="failed", failure_reason=message)


def _need(condition, message, audit=None):
    if not condition:
        raise CombinationFitError(message, audit)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _selection(value):
    _need(value is None or isinstance(value, dict), "selection must be a dictionary")
    cfg = {**DEFAULT_SELECTION, **(value or {})}
    _need(set(cfg) == set(DEFAULT_SELECTION), "unknown selection setting")
    for key in ("min_coverage", "min_abs_mean_ic", "min_stable_year_fraction", "max_abs_correlation"):
        _need(type(cfg[key]) in (int, float) and math.isfinite(cfg[key]) and 0 <= cfg[key] <= 1,
              key + " must be finite in [0,1]")
    for key in ("min_ic_days", "min_annual_days", "min_usable_years", "max_factors", "min_cross_section"):
        _need(type(cfg[key]) is int and cfg[key] >= 1, key + " must be a positive integer")
    _need(cfg["max_factors"] <= 6 and cfg["min_cross_section"] >= 3,
          "at most six base factors and at least three stocks per date required")
    _need(cfg["max_abs_correlation"] > 0, "correlation cutoff must be positive")
    return cfg


def _clean(frame):
    values = frame.astype(float, copy=True)
    return values.where(np.isfinite(values))


def _numeric_prefix(frame, dates):
    # Out-of-scope strings can upcast an otherwise valid numeric prefix to
    # object. Restore numeric dtypes only AFTER cutting to label-safe dates.
    prefix = frame.loc[dates].copy(deep=True)
    value = prefix.apply(pd.to_numeric, errors="raise")
    value.attrs = deepcopy(prefix.attrs)
    return value


def _daily_ic(left, right, eligible, minimum):
    common = eligible & left.notna() & right.notna()
    a = left.where(common).rank(axis=1, method="average")
    b = right.where(common).rank(axis=1, method="average")
    counts = common.sum(axis=1)
    daily = a.corrwith(b, axis=1).where(
        (counts >= minimum) & a.nunique(axis=1).gt(1) & b.nunique(axis=1).gt(1))
    return daily, counts


def _finite_or_none(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _factor_audit(name, frame, labels, eligible, cfg):
    daily, counts = _daily_ic(frame, labels, eligible, cfg["min_cross_section"])
    denominator = int(eligible.to_numpy().sum())
    coverage = int((eligible & frame.notna()).to_numpy().sum()) / denominator if denominator else 0.
    mean = _finite_or_none(daily.mean())
    direction = 1 if mean is not None and mean > 0 else -1 if mean is not None and mean < 0 else 0
    annual = []
    for year, part in daily.groupby(daily.index.year):
        value = _finite_or_none(part.mean())
        observed = int(part.notna().sum())
        usable = observed >= cfg["min_annual_days"] and value is not None
        annual.append({"year": int(year), "mean_ic": value, "observed_days": observed,
                       "calendar_days": len(part), "usable": usable,
                       "same_training_direction": bool(usable and direction and direction * value > 0)})
    usable = sum(row["usable"] for row in annual)
    stable = sum(row["same_training_direction"] for row in annual)
    fraction = stable / usable if usable else None
    support_reasons = []
    if coverage < cfg["min_coverage"]:
        support_reasons.append("insufficient_factor_coverage")
    if int(daily.notna().sum()) < cfg["min_ic_days"]:
        support_reasons.append("insufficient_ic_days")
    reasons = list(support_reasons)
    if not direction:
        reasons.append("unknown_or_zero_training_direction")
    if mean is not None and abs(mean) < cfg["min_abs_mean_ic"]:
        reasons.append("below_declared_return_ic_threshold")
    if usable < cfg["min_usable_years"]:
        reasons.append("insufficient_usable_years")
    if fraction is None or fraction < cfg["min_stable_year_fraction"]:
        reasons.append("unstable_annual_direction")
    return {"factor_id": name, "coverage": coverage, "pool_cells": denominator,
            "paired_cells": int(counts.sum()), "observed_ic_days": int(daily.notna().sum()),
            "calendar_days": len(daily), "mean_ic": mean, "direction": direction,
            "annual": annual, "usable_years": usable, "stable_year_fraction": fraction,
            "support_passed": not support_reasons, "support_reasons": support_reasons,
            "return_screen_passed": not reasons, "selected": False, "reasons": reasons}


def _rank_expr(name):
    return {"op": "subtract", "args": [
        {"op": "rank", "args": [{"op": "factor", "id": name}]},
        {"op": "constant", "value": .5}]}


def _expression(features, weights, means=None, scales=None):
    args = []
    for index, feature in enumerate(features):
        expr = deepcopy(feature["expression"])
        if means is not None:
            expr = {"op": "divide", "args": [
                {"op": "subtract", "args": [expr, {"op": "constant", "value": means[index]}]},
                {"op": "constant", "value": scales[index]}]}
        args.append(expr)
    return canonical_expression({"op": "weighted_sum", "args": args, "weights": list(weights)})


def _design(features, frames, eligible, labels, minimum, audit):
    # Rank each factor over its observed signal-day eligible universe, exactly
    # as compiler.rank does. Only regression rows require ALL feature operands
    # and the label. No forward/zero filling or future-membership mask is used.
    values = [evaluate_expression(feature["expression"], frames, eligible) for feature in features]
    common = eligible & labels.notna()
    for value in values:
        common &= value.notna()
    common &= common.sum(axis=1).ge(minimum).to_numpy()[:, None]
    counts = common.sum(axis=1)
    days, cells = int(counts.gt(0).sum()), int(counts.sum())
    required = audit["configuration"]
    _need(days >= required["min_train_days"] and cells >= required["min_train_cells"],
          "insufficient common training sample", {**audit, "common_sample": {"days": days, "cells": cells}})
    target = labels.where(common).rank(axis=1, method="average", pct=True)
    target = target.sub(target.mean(axis=1), axis=0)
    mask = common.to_numpy()
    x = np.column_stack([value.to_numpy()[mask] for value in values])
    y = target.to_numpy()[mask]
    _need(np.isfinite(x).all() and np.isfinite(y).all(), "nonfinite common training sample", audit)
    _need(float(np.std(y)) > 1e-12, "constant training target", audit)
    cell_weights = common.div(counts.replace(0, np.nan), axis=0).to_numpy()[mask] / days
    return x, y, cell_weights, common, {"days": days, "cells": cells,
                         "weighting": "each_valid_date_equal_mass_then_equal_common_stock_cells",
                         "excluded_pool_cells": int(eligible.to_numpy().sum()) - cells}


def _diagnostics(prediction, labels, common, target, weights, minimum):
    daily, counts = _daily_ic(prediction, labels, common, minimum)
    residual = prediction.to_numpy()[common.to_numpy()] - target
    return {"scope": "in_sample_fit_not_validation", "mean_daily_rank_ic": _finite_or_none(daily.mean()),
            "rank_ic_days": int(daily.notna().sum()), "paired_cells": int(counts.sum()),
            "target_mse": float(np.sum(weights * residual ** 2)),
            "weighting": "each_valid_date_equal_mass_then_equal_common_stock_cells"}


def _fit_model(features, frames, eligible, labels, cfg, audit, *, ridge_lambda=None, directions=None):
    x, y, cell_weights, common, support = _design(features, frames, eligible, labels, cfg["min_cross_section"], audit)
    if ridge_lambda is None:
        weights = [float(value) / len(features) for value in directions]
        expression = _expression(features, weights)
        model = {"method": "training_direction_equal_rank", "coefficients": weights,
                 "feature_means": None, "feature_scales": None}
    else:
        means = cell_weights @ x
        scales = np.sqrt(cell_weights @ ((x - means) ** 2))
        _need(np.all(scales > 1e-12), "constant or degenerate ridge feature", audit)
        z = (x - means) / scales
        gram = z.T @ (z * cell_weights[:, None])
        try:
            beta = np.linalg.solve(gram + ridge_lambda * np.eye(len(features)), z.T @ (cell_weights * y))
        except np.linalg.LinAlgError as exc:
            raise CombinationFitError("ridge solve failed", audit) from exc
        _need(np.isfinite(beta).all(), "nonfinite fitted coefficient", audit)
        weights = beta.tolist()
        expression = _expression(features, weights, means.tolist(), scales.tolist())
        model = {"method": "standardized_ridge", "ridge_lambda": ridge_lambda,
                 "objective": "equal_date_weighted_mean_squared_error + lambda * sum(beta_squared)",
                 "coefficients": weights, "feature_means": means.tolist(), "feature_scales": scales.tolist(),
                 "intercept": 0., "scale_ddof": 0}
    prediction = evaluate_expression(expression, frames, eligible)
    return {**model, "features": deepcopy(features), "expression": expression,
            "common_sample": support, "target": TARGET,
            "train_fit_diagnostics": _diagnostics(prediction, labels, common, y, cell_weights, cfg["min_cross_section"])}


def _attempt_model(audit, name, features, frames, eligible, labels, cfg, **kwargs):
    """Keep numerical unavailability local to the affected declared method."""
    try:
        fitted = _fit_model(features, frames, eligible, labels, cfg, audit, **kwargs)
    except CombinationFitError as exc:
        status = {"status": "unavailable", "reason": str(exc)}
        if "common_sample" in exc.audit:
            status["common_sample"] = exc.audit["common_sample"]
        audit["model_status"][name] = status
        return
    audit["models"][name] = fitted
    audit["model_status"][name] = {"status": "fitted", "common_sample": fitted["common_sample"]}


def fit_combinations(frames, eligible, forward_returns, *, train_start, train_end,
                     horizon_sessions, ridge_lambda, selection=None, interaction_pairs=None,
                     return_candidate_ids=None, min_train_days=60, min_train_cells=300):
    """Fit base equal-rank/ridge and optional predeclared rank-product ridge.

    Axes must match exactly. Date rows represent the complete market-session
    calendar, not compressed observed days. The caller's labels must mean
    ``open[t+1+h]/open[t+1]-1``. An h+1-session purge means only signal dates
    whose terminal observation is at/before train_end enter selection or fit.
    Dates after that safe signal endpoint are sliced before numeric access.

    Return-factor screening applies only to base models. Declared interaction
    endpoints need coverage/sample support, not positive marginal return IC or
    stable annual direction. Their main effects are included as controls even
    when absent from the six-factor base selection.
    """
    cfg = _selection(selection)
    _need(type(horizon_sessions) is int and 1 <= horizon_sessions <= 2520, "positive bounded horizon_sessions required")
    _need(type(ridge_lambda) in (int, float) and math.isfinite(ridge_lambda) and ridge_lambda > 0,
          "caller must declare a finite positive ridge_lambda")
    _need(type(min_train_days) is int and min_train_days >= 1 and type(min_train_cells) is int and min_train_cells >= 1,
          "positive common-sample limits required")
    _need(isinstance(eligible, pd.DataFrame) and isinstance(eligible.index, pd.DatetimeIndex), "dated eligible DataFrame required")
    _need(eligible.index.is_unique and eligible.index.is_monotonic_increasing and not eligible.index.hasnans
          and eligible.index.tz is None, "unique sorted timezone-naive market sessions required")
    start, end = pd.Timestamp(train_start), pd.Timestamp(train_end)
    _need(not pd.isna(start) and not pd.isna(end) and start.tz is None and end.tz is None and start <= end,
          "valid timezone-naive training boundaries required")
    _need(isinstance(frames, Mapping) and bool(frames), "nonempty factor mapping required")
    candidates = sorted(frames) if return_candidate_ids is None else return_candidate_ids
    _need(isinstance(candidates, (list, tuple)) and all(isinstance(name, str) and name in frames for name in candidates)
          and len(set(candidates)) == len(candidates), "return_candidate_ids must be unique supplied factor IDs")
    candidates = sorted(candidates)
    _need(isinstance(forward_returns, pd.DataFrame) and forward_returns.index.equals(eligible.index)
          and forward_returns.columns.equals(eligible.columns), "label axes must exactly equal eligible")
    for name, frame in frames.items():
        _need(isinstance(frame, pd.DataFrame) and frame.index.equals(eligible.index)
              and frame.columns.equals(eligible.columns), "factor axes must exactly equal eligible: " + str(name))
    last = int(eligible.index.searchsorted(end, side="right")) - 1
    safe = last - horizon_sessions - 1
    _need(safe >= 0, "no label-safe training dates after horizon purge")
    fit_dates = eligible.index[:safe + 1]
    fit_dates = fit_dates[fit_dates >= start]
    _need(len(fit_dates) >= min_train_days, "insufficient label-safe training dates")
    scoped = {name: _numeric_prefix(frame, fit_dates) for name, frame in frames.items()}
    pool = eligible.loc[fit_dates].copy()
    _need(all(type(value) in (bool, np.bool_) for value in pool.to_numpy().ravel()),
          "label-safe eligibility must contain only complete booleans")
    pool = pool.astype(bool)
    _axes(scoped, pool)
    labels = _numeric_prefix(forward_returns, fit_dates)
    _need(all(is_numeric_dtype(dtype) and not is_complex_dtype(dtype) for dtype in labels.dtypes), "real numeric forward returns required")
    labels = _clean(labels)
    pairs = [] if interaction_pairs is None else interaction_pairs
    _need(isinstance(pairs, (list, tuple)) and len(pairs) <= 6, "at most six predeclared interaction pairs required")
    normalized_pairs = []
    for pair in pairs:
        _need(isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(v, str) and v in frames for v in pair)
              and pair[0] != pair[1], "interaction pair must name two distinct supplied factors")
        normalized = tuple(sorted(pair))
        _need(normalized not in normalized_pairs, "duplicate predeclared interaction pair")
        normalized_pairs.append(normalized)
    normalized_pairs.sort()
    audit = {"version": VERSION, "status": "fitted", "configuration": {
        "train_start": start.isoformat(), "train_end": end.isoformat(), "horizon_sessions": horizon_sessions,
        "label_entry_lag_sessions": 1, "ridge_lambda": float(ridge_lambda), "target": TARGET,
        "selection": cfg, "interaction_pairs": [list(p) for p in normalized_pairs],
        "return_candidate_ids": candidates,
        "min_train_days": min_train_days, "min_train_cells": min_train_cells},
        "fit_scope": {"first_signal_date": fit_dates[0].isoformat(), "last_signal_date": fit_dates[-1].isoformat(),
                      "last_allowed_label_endpoint": eligible.index[last].isoformat(), "calendar_days": len(fit_dates),
                      "purged_terminal_sessions": horizon_sessions + 1,
                      "calendar_completeness": "caller_declared_complete_market_session_axis"},
        "factor_selection": [], "correlation_audit": [], "interaction_audit": [],
        "limitations": ["training_selection_and_fit_not_independent_validation", "no_lambda_search",
                        "factor_input_point_in_time_provenance_is_callers_responsibility",
                        "return_screen_does_not_gate_declared_interaction_endpoints",
                        "annual_sign_stability_is_descriptive_not_multiple_testing_correction"],
        "models": {}, "model_status": {}}
    fingerprint = hashlib.sha256()
    fingerprint.update(_json({"stock_columns": [str(value) for value in pool.columns]}).encode("utf-8"))
    fingerprint.update(pd.util.hash_pandas_object(pool, index=True).to_numpy().tobytes())
    fingerprint.update(pd.util.hash_pandas_object(labels, index=True).to_numpy().tobytes())
    for name in sorted(scoped):
        value = _clean(scoped[name])
        fingerprint.update(name.encode("utf-8"))
        fingerprint.update(pd.util.hash_pandas_object(value, index=True).to_numpy().tobytes())
        row = _factor_audit(name, value, labels, pool, cfg)
        row["declared_return_candidate"] = name in candidates
        if name not in candidates:
            row["return_screen_passed"] = False
            row["reasons"].append("not_declared_return_candidate")
        audit["factor_selection"].append(row)
    audit["fit_input_sha256"] = fingerprint.hexdigest()
    by_id = {row["factor_id"]: row for row in audit["factor_selection"]}
    ordered = sorted((row for row in audit["factor_selection"] if row["return_screen_passed"]),
                     key=lambda row: (-abs(row["mean_ic"]), row["factor_id"]))
    selected, selected_frames = [], {}
    for row in ordered:
        name = row["factor_id"]
        if len(selected) >= cfg["max_factors"]:
            row["reasons"].append("base_factor_limit")
            continue
        value = _clean(scoped[name])
        redundant = []
        for previous in selected:
            daily, counts = _daily_ic(value, selected_frames[previous], pool, cfg["min_cross_section"])
            corr = _finite_or_none(daily.mean())
            supported = int(daily.notna().sum()) >= cfg["min_ic_days"]
            record = {"candidate": name, "selected_factor": previous, "mean_daily_rank_correlation": corr,
                      "observed_days": int(daily.notna().sum()), "paired_cells": int(counts.sum()),
                      "sufficient_support": supported,
                      "redundant": bool(supported and corr is not None and abs(corr) >= cfg["max_abs_correlation"])}
            audit["correlation_audit"].append(record)
            if record["redundant"]:
                redundant.append(previous)
        if redundant:
            row["reasons"].append("absolute_rank_correlation_redundancy")
            row["redundant_with"] = redundant
            continue
        row["selected"] = True
        selected.append(name)
        selected_frames[name] = value
    audit["selected_factors"] = list(selected)
    base = [{"id": name, "kind": "main_effect", "factor_ids": [name], "expression": _rank_expr(name)} for name in selected]
    active = dict(selected_frames)
    if base:
        _attempt_model(audit, "equal_rank", base, active, pool, labels, cfg,
                       directions=[by_id[name]["direction"] for name in selected])
        _attempt_model(audit, "ridge", base, active, pool, labels, cfg, ridge_lambda=float(ridge_lambda))
    else:
        for name in ("equal_rank", "ridge"):
            audit["model_status"][name] = {"status": "unavailable", "reason": "no eligible base return factor"}
    accepted_pairs = []
    for left, right in normalized_pairs:
        unsupported = [name for name in (left, right) if not by_id[name]["support_passed"]]
        audit["interaction_audit"].append({"pair": [left, right], "accepted": not unsupported,
                                            "unsupported_endpoints": unsupported,
                                            "policy": "support_only_not_marginal_return_screen"})
        if not unsupported:
            accepted_pairs.append((left, right))
    augmented = sorted({name for pair in accepted_pairs for name in pair} - set(selected))
    audit["augmented_for_declared_pairs"] = augmented
    if accepted_pairs:
        features = deepcopy(base)
        for name in augmented:
            active[name] = _clean(scoped[name])
            features.append({"id": name, "kind": "main_effect_control", "factor_ids": [name], "expression": _rank_expr(name)})
        _attempt_model(audit, "ridge_augmented", features, active, pool, labels, cfg,
                       ridge_lambda=float(ridge_lambda))
        for left, right in accepted_pairs:
            features.append({"id": "interaction:" + left + ":" + right, "kind": "centered_rank_product",
                             "factor_ids": [left, right], "expression": {
                                 "op": "multiply", "args": [_rank_expr(left), _rank_expr(right)]}})
        _attempt_model(audit, "ridge_interactions", features, active, pool, labels, cfg,
                       ridge_lambda=float(ridge_lambda))
    else:
        for name in ("ridge_augmented", "ridge_interactions"):
            audit["model_status"][name] = {"status": "unavailable", "reason":
                "no supported predeclared interaction pair" if normalized_pairs else "no predeclared interaction pairs"}
    if not audit["models"]:
        first = audit["model_status"]["equal_rank"]
        if "common_sample" in first:
            audit["common_sample"] = first["common_sample"]
        raise CombinationFitError("no combination model fitted: " + first["reason"], audit)
    audit["artifact_sha256"] = hashlib.sha256(_json(audit).encode("utf-8")).hexdigest()
    _json(audit)
    return audit


def compile_to_strategy_expr(artifact, *, model="ridge"):
    """Return the fitted causal JSON score; coefficients never refit here."""
    _need(isinstance(artifact, dict) and artifact.get("version") == VERSION and artifact.get("status") == "fitted",
          "a successful supported frozen artifact is required")
    unhashed = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    expected = hashlib.sha256(_json(unhashed).encode("utf-8")).hexdigest()
    _need(artifact.get("artifact_sha256") == expected, "frozen combination artifact hash differs")
    _need(model in artifact["models"], "unknown fitted combination model")
    return canonical_expression(artifact["models"][model]["expression"])


def predict(artifact, frames, eligible, *, model="ridge"):
    """Score only supplied causal factors, preserving missing values and axes."""
    return evaluate_expression(compile_to_strategy_expr(artifact, model=model), frames, eligible)
