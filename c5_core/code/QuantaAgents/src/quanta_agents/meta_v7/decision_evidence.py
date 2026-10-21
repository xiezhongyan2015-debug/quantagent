"""Role-aware views over saved factor evidence; no numerical factor computation.

All means are copied from the report. Shared support records remove repeated
denominators, never observations. View status describes estimability, not alpha
significance or role admission. The source report remains the detailed record.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

VERSION = "v8_role_evidence_view_v1"
ROLES = {"return", "risk", "condition", "interaction"}
STATUSES = {"d": "descriptive", "one": "single_observed_day", "na": "not_estimable",
            "rd": "rank_deficient", "prd": "partly_rank_deficient",
            "u": "support_unknown", "bad": "inconsistent_saved_support"}


def _tables(view, all_ids):
    """Column-labelled, reversible encoding of the view's repeated row fields."""
    positions = {key: index for index, key in enumerate(all_ids)}
    codes = {value: key for key, value in STATUSES.items()}
    review = view["detail"] == "review"

    def value(row, annual=True):
        result = [row["mean_ic"], row["support"], codes[row.get("status", "descriptive")]]
        if annual:
            result.append(row.get("annual_summary"))
        if review:
            result += [row.get("daily_ic_std"), row.get("annual") if annual else None]
        return result

    coverage_columns = ["fraction", "pool_cells", "observed_cells", "missing_cells"]
    for row in view["factors"].values():
        row["coverage"] = [row["coverage"].get(key) for key in coverage_columns]
        row["ic"] = [[h, *value(metric)] for h, metric in row["ic"].items()]
        row["risk"] = [[h, name, *value(metric)] for h, metrics in row["risk"].items()
                       for name, metric in metrics.items()]
    view["correlations"] = [[positions[row["left"]], positions[row["right"]], *value(row, False)]
                            for row in view["correlations"]]
    view["conditions"] = [[positions[row["left"]], positions[row["right"]], row["horizon"],
                           row["condition"], row["condition_uses_future"], *value(row)]
                          for row in view["conditions"]]
    for row in view["interactions"]:
        row["left"], row["right"] = positions[row["left"]], positions[row["right"]]
        row["ic"] = [[h, name, *value(metric)] for h, metrics in row["ic"].items()
                     for name, metric in metrics.items()]
    support_columns = ["observed_days", "calendar_days", "missing_days", "paired_cells",
                       "rank_deficient_days", "min_observed_days_per_known_year", "coverage"]
    if review:
        support_columns.append("annual_days")
    for identity, row in view["evidence_support"].items():
        if row.get("coverage") is not None:
            row["coverage"] = [row["coverage"].get(key) for key in coverage_columns]
        view["evidence_support"][identity] = [row.get(key) for key in support_columns]
    extra = ["daily_ic_std", "annual"] if review else []
    metric_columns = ["mean_ic", "support", "status", "annual_summary", *extra]
    view["table_schema"] = {"factor_ids": all_ids, "pair_members_are_factor_indices": True,
        "status_codes": dict(STATUSES), "coverage": coverage_columns,
        "ic": ["horizon", *metric_columns], "risk": ["horizon", "metric", *metric_columns],
        "interaction_ic": ["horizon", "metric", *metric_columns],
        "correlations": ["left", "right", "mean_ic", "support", "status", *extra],
        "conditions": ["left", "right", "horizon", "condition", "condition_uses_future", *metric_columns],
        "evidence_support": support_columns}
    return view


def _finite_or_none(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("saved IC must be finite numeric or explicitly unknown")
    return value


def _annual_summary(rows):
    values = [_finite_or_none(row.get("mean_ic")) for row in rows]
    observed = [value for value in values if value is not None]
    return [min(observed) if observed else None, max(observed) if observed else None,
            sum(value > 0 for value in observed), sum(value < 0 for value in observed),
            sum(value == 0 for value in observed), len(values) - len(observed)]


def compact_factors_v8(report, *, detail="selection", ids=None, roles=None):
    """Return all evidence by default, or an explicitly scoped role/id view.

    ``selection`` keeps annual extrema/sign counts and minimum annual sample
    size. ``review`` additionally returns each saved annual mean and denominator.
    Filtering selects matching factor rows and every pair touching those rows;
    the other pair member is named but is not silently treated as tested alone.
    No factor is filtered by IC, coverage, or availability. A reference in
    ``support`` resolves through ``evidence_support`` in this same view.
    """
    if detail not in {"selection", "review"}:
        raise ValueError("detail must be selection or review")
    if not isinstance(report, dict) or report.get("status") != "completed":
        raise ValueError("completed saved factor report required")
    source = report.get("factors")
    if not isinstance(source, dict):
        raise ValueError("saved factor mapping required")
    if ids is not None and (not isinstance(ids, (list, tuple)) or not ids
            or any(not isinstance(value, str) for value in ids) or set(ids) - set(source)):
        raise ValueError("ids must name known factors in this report")
    if roles is not None and (not isinstance(roles, (list, tuple)) or not roles
            or any(not isinstance(value, str) for value in roles) or set(roles) - ROLES):
        raise ValueError("roles must use return/risk/condition/interaction")
    selected = {key for key, value in source.items()
                if (ids is None or key in ids)
                and (roles is None or set(value.get("roles", [])) & set(roles))}
    support_rows, support_keys = {}, {}

    def support(row, coverage=None, *, annual=True):
        value = {key: row.get(key) for key in ("observed_days", "calendar_days", "missing_days", "paired_cells")}
        if "rank_deficient_days" in row:
            value["rank_deficient_days"] = row["rank_deficient_days"]
        years = row.get("annual", []) if annual else []
        if years:
            if detail == "review":
                value["annual_days"] = [[r.get("year"), r.get("observed_days"), r.get("calendar_days")] for r in years]
            else:
                observed = [r.get("observed_days") for r in years if r.get("mean_ic") is not None]
                value["min_observed_days_per_known_year"] = min(observed) if observed and None not in observed else None
        coverage = row.get("coverage", coverage)
        if coverage is not None:
            value["coverage"] = {key: coverage.get(key) for key in
                                 ("fraction", "pool_cells", "observed_cells", "missing_cells")}
        key = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if key not in support_keys:
            identity = "s" + str(len(support_keys) + 1)
            support_keys[key] = identity
            support_rows[identity] = value
        return support_keys[key]

    def metric(row, coverage=None, *, annual=True):
        value = _finite_or_none(row.get("mean_ic"))
        n, deficient = row.get("observed_days"), row.get("rank_deficient_days", 0)
        if n is not None and (type(n) is not int or n < 0):
            raise ValueError("saved observed_days must be nonnegative integer or unknown")
        if type(deficient) is not int or deficient < 0:
            raise ValueError("saved rank_deficient_days must be nonnegative integer")
        if value is None:
            state = "rank_deficient" if deficient else "not_estimable"
        elif n is None:
            state = "support_unknown"
        elif n == 0:
            state = "inconsistent_saved_support"
        elif n == 1:
            state = "single_observed_day"
        else:
            state = "partly_rank_deficient" if deficient else "descriptive"
        result = {"mean_ic": value, "support": support(row, coverage, annual=annual)}
        if state != "descriptive":
            result["status"] = state
        if detail == "review" and "daily_ic_std" in row:
            result["daily_ic_std"] = _finite_or_none(row["daily_ic_std"])
        if annual:
            result["annual_summary"] = _annual_summary(row.get("annual", []))
            if detail == "review":
                result["annual"] = [[r.get("year"), r.get("mean_ic")] for r in row.get("annual", [])]
        return result

    factors = {}
    for key, row in source.items():
        if key not in selected:
            continue
        factors[key] = {"roles": deepcopy(row.get("roles", [])),
            "coverage": {k: row.get("coverage", {}).get(k) for k in
                         ("fraction", "pool_cells", "observed_cells", "missing_cells")},
            "ic": {h: metric(value) for h, value in row.get("horizons", {}).items()},
            "risk": {h: {name: metric(value) for name, value in metrics.items()}
                     for h, metrics in row.get("risk", {}).items()}}

    def included(row):
        return row.get("left") in selected or row.get("right") in selected

    correlations = [{"left": row["left"], "right": row["right"], **metric(row, annual=False)}
                    for row in report.get("correlations", []) if included(row)]
    conditions = [{"left": row["left"], "right": row["right"], "horizon": row["horizon"],
                   "condition": row["condition"], **metric(row),
                   "condition_uses_future": row.get("condition_uses_future")}
                  for row in report.get("conditions", []) if included(row)]
    interactions = []
    for row in report.get("interactions", []):
        if not included(row):
            continue
        interactions.append({"left": row["left"], "right": row["right"], "status": row["status"],
            "ic": {h: {name: metric(value[name], value.get("coverage"))
                       for name in ("raw_ic", "partial_ic")}
                   for h, value in row.get("horizons", {}).items()}})
    return _tables({"view_version": VERSION, "detail": detail, "scope": deepcopy(report.get("scope", {})),
            "selection": {"ids": None if ids is None else list(ids),
                          "roles": None if roles is None else list(roles),
                          "total_factors": len(source), "returned_factors": len(factors),
                          "filtered_by_efficacy": False},
            "factors": factors, "correlations": correlations, "conditions": conditions,
            "interactions": interactions, "evidence_support": support_rows,
            "interpretation": {"status_is_statistical_significance": False,
                "automatic_admission": False, "means_recomputed": False,
                "default_metric_status": "descriptive; no significance or admission claim",
                "annual_summary_columns": ["min_ic", "max_ic", "positive_years", "negative_years", "zero_years", "unknown_years"],
                "annual_days_columns": ["year", "observed_days", "calendar_days"],
                "annual_review_columns": ["year", "mean_ic"],
                "coverage_is_not_effective_independent_n": True,
                "conditions": report.get("semantics", {}).get("conditions"),
                "full_detail_pointers": ["/factors", "/correlations", "/conditions", "/interactions"]},
            "limitations": deepcopy(report.get("limitations", []))}, list(source))
