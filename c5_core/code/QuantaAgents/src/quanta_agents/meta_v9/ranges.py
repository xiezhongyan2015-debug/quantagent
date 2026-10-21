"""Bounded semantic ranges, deterministic sampling and automatic refinement.

Only this module materializes exact portfolio values. A research decision names
factor sets and parameter intervals; no LLM call is needed inside a search round.
"""
from __future__ import annotations

from itertools import product
import math
import numpy as np

from quanta_agents.research_kernel.store import digest

VERSION = "meta_v9.1.0"


def validate_range(value, available):
    required = {"name", "hypothesis", "falsifier", "factors", "top_n", "rebalance_sessions",
                "weighting", "market_filter", "buffer_multiple"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Range requires the documented semantic fields")
    for field in ("name", "hypothesis", "falsifier"):
        if not isinstance(value[field], str) or not 1 <= len(value[field]) <= 800:
            raise ValueError("A short nonempty " + field + " is required")
    if not isinstance(value["factors"], list) or not 1 <= len(value["factors"]) <= 4:
        raise ValueError("A range combines one to four library factors")
    ids = []
    for row in value["factors"]:
        if set(row) != {"id", "direction", "weight"} or row["id"] not in available:
            raise ValueError("Unknown library factor or invalid factor range")
        ids.append(row["id"])
        if type(row["direction"]) is not int or row["direction"] not in (-1, 1):
            raise ValueError("Factor orientation must be explicit")
        interval(row["weight"], 0.05, 1.)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate factors in range")
    interval(value["top_n"], 5, 100, integer=True)
    interval(value["rebalance_sessions"], 2, 120, integer=True)
    for field, allowed in (("weighting", {"equal", "inverse_volatility"}),
                           ("market_filter", {"none", "trend60", "trend120"}),
                           ("buffer_multiple", {0, 1.5, 2, 3})):
        options = value[field]
        if not isinstance(options, list) or not options or len(set(options)) != len(options) or not set(options) <= allowed:
            raise ValueError("Invalid categorical range: " + field)
    if value["top_n"][0] == value["top_n"][1] and value["rebalance_sessions"][0] == value["rebalance_sessions"][1]:
        raise ValueError("Decision must leave a nontrivial parameter range to the program")
    return value


def interval(value, low, high, integer=False):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Expected [lower, upper] interval")
    if any(type(x) not in ((int,) if integer else (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError("Finite typed interval required")
    if not low <= value[0] <= value[1] <= high:
        raise ValueError("Interval outside admitted bounds")


def materialize(space, values):
    n, hold, weighting, market_filter, buffer, *weights = values
    mass = sum(weights)
    signed = {f["id"]: round(f["direction"] * w / mass, 10) for f, w in zip(space["factors"], weights)}
    return {"factor_weights": signed, "top_n": int(n), "rebalance_sessions": int(hold),
            "weighting": weighting, "market_filter": market_filter,
            "membership_buffer": int(math.ceil(n * buffer)) if buffer else 0,
            "max_stock_weight": .2, "gross_exposure": 1., "rebalance_schedule": "sessions"}


def sample_range(space, count, seed):
    """Stratified continuous/integer samples, with reproducible boundary anchors."""
    validate_range(space, {f["id"] for f in space["factors"]})
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("Bounded positive sample count required")
    rng = np.random.default_rng(seed)
    dimensions = [space["top_n"], space["rebalance_sessions"], *[f["weight"] for f in space["factors"]]]
    strata = [(rng.permutation(count) + rng.random(count)) / count for _ in dimensions]
    categorical = list(product(space["weighting"], space["market_filter"], space["buffer_multiple"]))
    rng.shuffle(categorical)
    result = {}
    for i in range(count):
        nums = [lo + (hi - lo) * u[i] for (lo, hi), u in zip(dimensions, strata)]
        if i < 3:
            nums = [lo + (hi - lo) * (i / 2) for lo, hi in dimensions]
        n, hold = (int(round(v)) for v in nums[:2])
        row = materialize(space, [n, hold, *categorical[i % len(categorical)], *nums[2:]])
        result[digest(row)] = row
    return list(result.values())


def coarse_candidates(ids):
    """A genuine broad account sweep, including both orientations of risk assets."""
    output = []
    for factor, direction, n, hold, filt in product(sorted(ids), (-1, 1), (15, 40), (10, 40), ("none", "trend120")):
        space = {"factors": [{"id": factor, "direction": direction}]}
        output.append(materialize(space, [n, hold, "equal", filt, 0, 1.]))
    return output


def objective(row):
    """Reward net annual Sharpe, penalize weak years and excessive drawdown.

    Failed/incomplete years cannot disappear from the comparison denominator.
    The primary target itself remains the separately reported arithmetic mean.
    """
    s = row.get("summary", {})
    mean, worst = s.get("mean_full_year_sharpe"), s.get("worst_full_year_sharpe")
    if row.get("status") != "completed" or mean is None or worst is None or not s.get("all_full_year_sharpes_available"):
        return -math.inf
    return mean - .15 * max(0., -worst) - .25 * max(0., -s["max_drawdown"] - .3)


def refine_range(space, rows, elite_fraction=.25):
    """Keep a neighborhood of multiple elites; never collapse to a winning point."""
    valid = sorted((r for r in rows if math.isfinite(objective(r))), key=lambda r: (-objective(r), r["id"]))
    if len(valid) < 4:
        return space
    elite = valid[:max(4, math.ceil(len(valid) * elite_fraction))]
    refined = {**space, "factors": [dict(f) for f in space["factors"]]}
    for field, minimum_width in (("top_n", 6), ("rebalance_sessions", 6)):
        old_lo, old_hi = space[field]
        values = [r["spec"][field] for r in elite]
        pad = max(minimum_width / 2, (old_hi - old_lo) * .1)
        lo = max(old_lo, int(math.floor(np.quantile(values, .1) - pad)))
        hi = min(old_hi, int(math.ceil(np.quantile(values, .9) + pad)))
        refined[field] = [lo, hi]
    # Relative weights are normalized; preserve their original intervals so
    # shrinkage does not confuse normalized and pre-normalization coordinates.
    return refined


def range_summary(space, rows):
    good = [r for r in rows if math.isfinite(objective(r))]
    scores = [r["summary"]["mean_full_year_sharpe"] for r in good]
    return {"name": space["name"], "tested": len(rows), "completed": len(good),
            "failed_or_incomplete": len(rows) - len(good),
            "annual_sharpe_quantiles": [round(float(np.quantile(scores, q)), 5) for q in (.1, .5, .9)] if scores else [],
            "fraction_above_target": sum(s > 1 for s in scores) / len(scores) if scores else None,
            "next_range": refine_range(space, rows)}
