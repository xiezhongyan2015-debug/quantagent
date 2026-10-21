"""Warning-only review of preregistered scalar conjunctions.

This pure function consumes declarations, never outcomes, programs, or prose
meaning. Decimal endpoints are compared exactly, without arithmetic or rounding.
It neither admits experiments nor changes reservations, scans, or budgets.
"""
from collections import Counter
from decimal import Decimal
from itertools import combinations

from .experiment_contrast import METRICS, validate
from .ledger import digest


VERSION = "scalar_conjunction_design_review_v1"
UNBOUNDED = (None, False, None, False)
LIMITATIONS = (
    "Only declared scalar checks are evaluated; prose, mechanism meaning, falsifiability, "
    "success/failure coverage and simultaneous structural changes remain unassessed.",
    "Checks belonging to one hypothesis are interpreted as a conjunction; different metrics "
    "are independent real coordinates here, without account identities, discreteness or feasible-range constraints.",
    "A different or disjoint numeric prediction does not establish causal identification, "
    "control adequacy, effect size, statistical power, executable value or future profitability.",
    "Missing checks are unassessed, not false or equivalent to another unassessed hypothesis.",
    "Redundancy evidence is relative to the original declaration; do not remove all flagged checks simultaneously.",
    "This review issues warnings only; it does not accept/reject a design or change frozen scope, scans or budget.",
)


def _decimal_text(value):
    if value is None:
        return None
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _interval(check):
    threshold = Decimal(check["threshold"])
    op = check["operator"]
    if op == "eq":
        return threshold, True, threshold, True
    if op in ("gt", "gte"):
        return threshold, op == "gte", None, False
    return None, False, threshold, op == "lte"


def _intersect(left, right):
    lower, closed_lower, upper, closed_upper = left
    other_lower, other_closed_lower, other_upper, other_closed_upper = right
    if other_lower is not None:
        if lower is None or other_lower > lower:
            lower, closed_lower = other_lower, other_closed_lower
        elif other_lower == lower:
            closed_lower = closed_lower and other_closed_lower
    if other_upper is not None:
        if upper is None or other_upper < upper:
            upper, closed_upper = other_upper, other_closed_upper
        elif other_upper == upper:
            closed_upper = closed_upper and other_closed_upper
    return lower, closed_lower, upper, closed_upper


def _empty(interval):
    lower, closed_lower, upper, closed_upper = interval
    return lower is not None and upper is not None and (
        lower > upper or (lower == upper and not (closed_lower and closed_upper)))


def _conjoin(checks):
    interval = UNBOUNDED
    for check in checks:
        interval = _intersect(interval, _interval(check))
    return interval


def _public_interval(interval):
    lower, closed_lower, upper, closed_upper = interval
    return {"lower": _decimal_text(lower), "lower_inclusive": closed_lower,
            "upper": _decimal_text(upper), "upper_inclusive": closed_upper,
            "empty": _empty(interval)}


def _smallest_witness(checks, predicate):
    # Existing schema permits at most six checks, so exhaustive witnesses are bounded.
    for size in range(1, len(checks) + 1):
        for subset in combinations(checks, size):
            if predicate(_conjoin(subset)):
                return [check["id"] for check in subset]
    return []


def review_experiment_design(declaration):
    """Review a registration's ``declaration``; return JSON-compatible evidence.

    A single hypothesis is legitimate. Missing or empty checks are unassessed.
    Invalid nonempty checks use the existing contrast schema's ValueError; the
    caller should invoke this only after normal registration validation. Valid
    but contradictory/redundant/equivalent predictions are never rejected here.
    Hypothesis prose is neither interpreted nor rewritten.
    """
    hypotheses = declaration["mechanism_hypotheses"]
    if type(hypotheses) is not list:
        raise ValueError("mechanism_hypotheses must be a list")
    checks = declaration.get("contrast_checks", [])
    if checks != []:
        validate(checks, len(hypotheses))
    elif type(checks) is not list:
        raise ValueError("contrast_checks must be a list")
    issues, rows, normalized = [], [], []
    for index in range(len(hypotheses)):
        own = [check for check in checks if check["hypothesis_index"] == index]
        metrics = {}
        for check in own:
            metrics.setdefault(check["metric"], []).append(check)
        intervals = {metric: _conjoin(group) for metric, group in sorted(metrics.items())}
        normalized.append(intervals)
        contradictory = any(_empty(value) for value in intervals.values())
        rows.append({"hypothesis_index": index, "check_ids": [check["id"] for check in own],
            "status": "not_evaluated" if not own else "contradictory" if contradictory else "satisfiable",
            "metric_intervals": [{"metric": metric, "unit": METRICS[metric],
                                  **_public_interval(value)} for metric, value in intervals.items()]})
        if not own:
            issues.append({"code": "numeric_prediction_not_evaluated", "severity": "info",
                           "hypothesis_indices": [index], "check_ids": []})
        for metric, group in sorted(metrics.items()):
            interval = intervals[metric]
            if _empty(interval):
                issues.append({"code": "contradictory_numeric_conditions", "severity": "warning",
                    "hypothesis_indices": [index], "metric": metric,
                    "check_ids": [check["id"] for check in group],
                    "witness_check_ids": _smallest_witness(group, _empty),
                    "intersection": _public_interval(interval)})
            # Duplicate classes use normalized intervals, so 0, -0.0 and 0.00 agree.
            classes = {}
            for check in group:
                classes.setdefault(_interval(check), []).append(check)
            for single, equivalent_checks in classes.items():
                ids = [check["id"] for check in equivalent_checks]
                if len(ids) > 1:
                    issues.append({"code": "duplicate_numeric_conditions", "severity": "warning",
                        "hypothesis_indices": [index], "metric": metric, "check_ids": ids,
                        "normalized_condition": _public_interval(single)})
                # Empty premises imply everything vacuously; never label that useful redundancy.
                others = [check for check in group if check["id"] not in ids]
                if not _empty(interval) and others and _conjoin(others) == interval:
                    def implies(target, condition=single):
                        return not _empty(target) and _intersect(target, condition) == target
                    issues.append({"code": "redundant_numeric_condition", "severity": "warning",
                        "hypothesis_indices": [index], "metric": metric, "check_ids": ids,
                        "implied_by_check_ids": _smallest_witness(others, implies),
                        "intersection_without_condition": _public_interval(_conjoin(others)),
                        "intersection_with_condition": _public_interval(interval)})
    pairs = []
    for left, right in combinations(range(len(hypotheses)), 2):
        a, b = normalized[left], normalized[right]
        pair = {"hypothesis_indices": [left, right], "relation": "not_evaluated"}
        if not a or not b:
            pair["reason"] = "one_or_both_hypotheses_have_no_numeric_checks"
        else:
            empty_a = rows[left]["status"] == "contradictory"
            empty_b = rows[right]["status"] == "contradictory"
            if empty_a or empty_b:
                # All inconsistent conjunctions denote the same empty set, even on different metrics.
                pair["relation"] = "equivalent_empty" if empty_a and empty_b else "not_evaluated"
                pair["reason"] = "both_prediction_sets_empty" if empty_a and empty_b else "one_prediction_set_empty"
            elif a == b:
                pair["relation"] = "equivalent"
            else:
                separating = [metric for metric in sorted(set(a) | set(b))
                    if _empty(_intersect(a.get(metric, UNBOUNDED), b.get(metric, UNBOUNDED)))]
                pair["relation"] = "disjoint" if separating else "overlapping_non_equivalent"
                pair["disjoint_metrics"] = separating
            if pair["relation"] in ("equivalent", "equivalent_empty"):
                issues.append({"code": "equivalent_numeric_predictions", "severity": "warning",
                    "hypothesis_indices": [left, right],
                    "check_ids": rows[left]["check_ids"] + rows[right]["check_ids"],
                    "relation": pair["relation"], "empty_prediction_set": empty_a and empty_b})
        pairs.append(pair)
    counts = Counter(issue["severity"] for issue in issues)
    return {"kind": VERSION, "checks_hash": digest(checks),
        "status": "not_evaluated" if not checks else "evaluated_with_warnings" if counts["warning"] else "evaluated",
        "hypothesis_count": len(hypotheses), "declared_check_count": len(checks),
        "comparison": "revision minus baseline; conjunction within each hypothesis",
        "numeric_domain": "independent_real_scalar_coordinates", "hypotheses": rows,
        "pair_comparisons": pairs, "issues": issues,
        "issue_counts": {key: counts[key] for key in ("warning", "info")},
        "warning_only": True, "design_acceptance_decision": "not_made",
        "causal_mechanism_identified": False, "formal_target_success": False,
        "limitations": list(LIMITATIONS)}
