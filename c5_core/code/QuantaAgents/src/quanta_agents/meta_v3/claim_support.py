"""Bounded checks of declared claims against saved, controller-owned evidence.

This module checks structured numeric statements, not natural-language truth.
The evidence mapping must come from this task's saved results, never arguments
provided by the report author. Free prose and unregistered metrics stay open
for review. A successful check certifies neither source completeness, causality,
real execution, OOS performance nor the accuracy of the surrounding report.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re


VERSION = "claim_support_v1"
RELATIONS = ("eq", "gt", "ge", "lt", "le", "between")
KINDS = ("numeric", "descriptive", "causal", "registered_check")
# Conversion factors express one display unit in the corresponding base unit.
UNITS = {
    "fraction": ("level", Decimal(1)),
    "percent": ("level", Decimal("0.01")),
    "fraction_difference": ("difference", Decimal(1)),
    "percentage_points": ("difference", Decimal("0.01")),
    "basis_points": ("difference", Decimal("0.0001")),
    "CNY": ("money", Decimal(1)),
    "count": ("count", Decimal(1)),
    "multiple": ("multiple", Decimal(1)),
}
ACCOUNT_UNITS = {
    "initial_cash": "CNY", "final_nav": "CNY", "net_pnl": "CNY",
    "return_on_full_initial_cash": "fraction", "maximum_drawdown": "fraction",
    "fees_on_recorded_trades": "CNY", "recorded_slippage_in_prices": "CNY",
    "fixed_path_fee_drag_on_initial_cash": "fraction_difference",
    "turnover_multiple_of_initial_cash": "multiple",
    "mean_marked_share_fraction_of_gross_assets": "fraction",
    "all_cash_days": "count", "saved_cash_days": "count", "trade_count": "count",
    "unvalued_trade_count": "count", "rejection_count": "count",
}
SUMMARY_UNITS = {
    "initial_cash": "CNY", "final_net_asset_value": "CNY",
    "net_return_on_full_initial_cash": "fraction", "maximum_daily_drawdown": "fraction",
    "cash_days_including_no_position_days": "count",
}
HORIZON_UNITS = {
    "horizon": "count", "low_mean": "fraction", "high_mean": "fraction",
    "matched_pairs": "count", "matched_mean_difference": "fraction_difference",
}


def contract():
    """Compact author-facing contract; report policy is frozen by the caller."""
    return {
        "version": VERSION, "type": "array", "min_items": 1, "max_items": 16,
        "serialization": "Set arguments.claims directly to a JSON array of claim objects. Do not wrap the array in a claims object. Unique claim_id per item. This contract describes the array; do not copy its metadata into the report.",
        "common_required": ["claim_id", "kind", "evidence_id", "research_class"],
        "kind": list(KINDS), "relations": list(RELATIONS), "units": list(UNITS),
        "numeric_required": ["path", "relation", "value", "unit"],
        "registered_check_required": ["check_id", "status"],
        "registered_check_rules": "Cite both the develop_strategy revision result and its original register_experiment evidence. check_id names an original contrast check; status is matched, contradicted or unevaluable. This verifies a frozen observed check only, never a mechanism, prose success rule or strategy success.",
        "registered_contrast_metrics": "With both evidence IDs cited, [experiment,declared_check_results,counts,matched|contradicted|unevaluable] uses count. [experiment,declared_check_results,checks,index,baseline_value|revision_value|revision_minus_baseline|threshold] uses the declared metric unit; exposure/drawdown deltas and thresholds use fraction_difference, not fraction.",
        "optional": {"text": "Unverified prose; never certified by this checker",
                     "decimal_places": "numeric eq only; integer 0..8, explicit ROUND_HALF_UP"},
        "numeric_example": {"claim_id": "return_1", "kind": "numeric", "evidence_id": "saved_call_id",
            "path": ["account_summary", "net_return_on_full_initial_cash"],
            "relation": "eq", "value": "12.6474", "unit": "percent", "decimal_places": 4,
            "research_class": "real_saved_development"},
        "value": "Decimal string or finite number; between uses [lower, upper], inclusive",
        "input_coverage_metrics": "inspect_inputs path [rows, row_index, sessions|initial_cash] is registered only when that saved row has kind=whole_input_scope: sessions use count; initial_cash uses CNY. The row index alone does not establish semantics or historical completeness.",
        "batch_account_metrics": "For inspect_batch results paths [rows,index,account,metric], cite the original register_batch, execute_batch and delivered inspect_batch page. Only complete saved accounts bound to the frozen case, ordered program identities and full calendar are supported. This checks saved scalar consistency, not a new account audit or execution certification. diagnose_execution [pairs,index,comparison_minus_reference_fees] uses CNY only when both saved full accounts and original subtraction match.",
        "common_horizon_metrics": "diagnose_horizons development common-sample paths: [horizon_summary,denominators,common_sample_events] uses count; [horizon_summary,curve,index,mean_gross_forward_return] uses fraction (percent conversion allowed); [horizon_summary,curve,index,dependence,same_stock_overlapping_event_pairs] uses count. The row must match a declared horizon and the saved common-sample count/hash. Gross endpoint returns are not net portfolio returns; overlapping pairs do not estimate independent sample size.",
        "rules": "path addresses saved result.public. Cite report evidence IDs. Scope must equal frozen research_class. Unknown metrics and prose require review. Descriptive evidence cannot establish causality. Positive structured checks do not certify the report, real fills, completeness or OOS.",
    }


def _number(value):
    if type(value) not in (str, int, float, Decimal) or len(str(value)) > 100:
        raise ValueError("bounded numeric scalar required")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid numeric scalar") from exc
    if (not number.is_finite() or abs(number.as_tuple().exponent) > 100
            or (number and abs(number.adjusted()) > 100)):
        raise ValueError("finite bounded numeric scalar required")
    return number


def _common_horizon_unit(path, public, research_class):
    """Recognize only the saved common-sample development diagnostic schema."""
    if research_class not in ("real_saved_development", "synthetic_calibration"):
        return None
    summary = public.get("horizon_summary")
    if type(summary) is not dict:
        return None
    scope, policy = summary.get("scope"), summary.get("policy")
    if (type(scope) is not dict or scope.get("split") != "development"
            or scope.get("observation_unit") != "eligible_signal_stock_days"
            or any(scope.get(key) is not False for key in ("execution_valid", "formal_target_success", "promotion"))
            or type(policy) is not dict
            or policy.get("sample") != "same_event_entry_and_all_requested_exit_opens"
            or policy.get("return") != "exit_open / entry_open - 1"):
        return None
    denominators, horizons, curve = summary.get("denominators"), summary.get("horizons"), summary.get("curve")
    if (type(denominators) is not dict or type(horizons) is not list or not 1 <= len(horizons) <= 6
            or any(type(h) is not int or not 1 <= h <= 20 for h in horizons)
            or horizons != sorted(set(horizons)) or type(curve) is not list or len(curve) != len(horizons)):
        return None
    count, sample_hash = denominators.get("common_sample_events"), denominators.get("common_sample_hash")
    if (type(count) is not int or count < 0 or type(sample_hash) is not str
            or re.fullmatch(r"[a-f0-9]{64}", sample_hash) is None
            or summary.get("status") != ("computed" if count else "empty_common_sample")):
        return None
    for horizon, row in zip(horizons, curve):
        if (type(row) is not dict or type(row.get("horizon_sessions")) is not int
                or row["horizon_sessions"] != horizon or type(row.get("observations")) is not int
                or row["observations"] != count or row.get("common_sample_hash") != sample_hash):
            return None
    if path == ["horizon_summary", "denominators", "common_sample_events"]:
        return "count"
    if (len(path) in (4, 5) and path[:2] == ["horizon_summary", "curve"]
            and type(path[2]) is int and 0 <= path[2] < len(curve)):
        if path[3:] == ["mean_gross_forward_return"]:
            return "fraction"
        if (path[3:] == ["dependence", "same_stock_overlapping_event_pairs"]
                and policy.get("overlap_interval") == "[entry_open, exit_open); touching endpoints do not overlap"):
            dependence = curve[path[2]].get("dependence")
            pairs = dependence.get("same_stock_overlapping_event_pairs") if type(dependence) is dict else None
            if type(pairs) is int and 0 <= pairs <= count * (count - 1) // 2:
                return "count"
    return None


def _unit(action, path, public, research_class):
    """Register tool-owned paths only; never infer units from model prose."""
    if (action == "inspect_inputs" and len(path) == 3 and path[0] == "rows"
            and type(path[1]) is int):
        row = _lookup(public, path[:2])
        if type(row) is dict and row.get("kind") == "whole_input_scope":
            return {"sessions": "count", "initial_cash": "CNY"}.get(path[2])
    if action == "develop_strategy" and len(path) == 2:
        if path[0] == "account_summary":
            return SUMMARY_UNITS.get(path[1])
        if path[0] == "cash_summary" and path[1] in ("initial_cash", "final_cash", "fees_paid", "slippage_paid"):
            return "CNY"
    if action == "diagnose_execution" and len(path) == 3 and type(path[1]) is int:
        if path[0] == "accounts":
            return ACCOUNT_UNITS.get(path[2])
        if path[0] == "pairs" and path[2] == "comparison_minus_reference_net_pnl":
            return "CNY"
    if (action == "diagnose_horizons" and len(path) == 4
            and path[:2] == ["horizon_summary", "descriptive_conditioning"] and type(path[2]) is int):
        return HORIZON_UNITS.get(path[3])
    if action == "diagnose_horizons":
        return _common_horizon_unit(path, public, research_class)
    return None


def _lookup(data, path):
    for part in path:
        if type(data) is dict and type(part) is str:
            data = data[part]
        elif type(data) is list and type(part) is int and part >= 0:
            data = data[part]
        else:
            raise KeyError("path does not address a saved field")
    return data


def _result(claim, status, reason, **detail):
    return {"claim_id": claim.get("claim_id"), "status": status, "reason": reason, **detail}


def _review_one(claim, evidence, research_class, frozen_case=None):
    common = {"claim_id", "kind", "evidence_id", "research_class"}
    numeric = {"path", "relation", "value", "unit"}
    required = common | (numeric if claim.get("kind") == "numeric" else
                         {"check_id", "status"} if claim.get("kind") == "registered_check" else set())
    allowed = required | {"text"} | ({"decimal_places"} if claim.get("kind") == "numeric" else set())
    if not required <= set(claim) or not set(claim) <= allowed:
        return _result(claim, "invalid", "claim_fields")
    if any(type(claim[k]) is not str or not 0 < len(claim[k]) <= 200 for k in common):
        return _result(claim, "invalid", "claim_identity")
    if claim["kind"] not in KINDS:
        return _result(claim, "invalid", "unknown_claim_kind")
    if "text" in claim and (type(claim["text"]) is not str or len(claim["text"]) > 2000):
        return _result(claim, "invalid", "claim_text_bound")
    if claim["evidence_id"] not in evidence:
        return _result(claim, "unsupported", "unknown_or_uncited_evidence")
    if claim["research_class"] != research_class:
        return _result(claim, "unsupported", "research_scope_mismatch")
    saved = evidence[claim["evidence_id"]]
    if type(saved) is not dict or type(saved.get("public")) is not dict:
        return _result(claim, "needs_review", "no_registered_public_evidence")
    if claim['kind'] == 'registered_check':
        from .registered_contrast_claims import bound_results, STATES
        if (type(claim['check_id']) is not str or not 0 < len(claim['check_id']) <= 32
                or type(claim['status']) is not str or claim['status'] not in STATES):
            return _result(claim, 'invalid', 'registered_check_fields')
        try:
            contrast = bound_results(saved, evidence, research_class)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return _result(claim, 'unsupported', 'registered_contrast_binding_missing_or_invalid', detail=str(exc))
        rows = [r for r in contrast['checks'] if r['id'] == claim['check_id']]
        if len(rows) != 1:
            return _result(claim, 'unsupported', 'unknown_registered_check')
        row = rows[0]
        return _result(claim, 'supported' if row['status'] == claim['status'] else 'contradicted',
                       'frozen_registered_check_observation', observed_status=row['status'],
                       registration_hash=contrast['registration_hash'], check_id=row['id'],
                       mechanism_or_strategy_success_verified=False,
                       text_support='unverified' if claim.get('text') else 'not_supplied')
    if claim["kind"] == "causal":
        if saved.get("action") in ("develop_strategy", "diagnose_execution", "diagnose_horizons", "register_experiment", "execute_batch"):
            return _result(claim, "unsupported", "descriptive_evidence_does_not_identify_cause")
        return _result(claim, "needs_review", "causal_identification_not_implemented")
    if claim["kind"] == "descriptive":
        return _result(claim, "needs_review", "prose_entailment_not_implemented")
    path = claim["path"]
    if (type(path) is not list or not 1 <= len(path) <= 8 or
            not all((type(p) is str and 0 < len(p) <= 100) or (type(p) is int and 0 <= p <= 100000) for p in path)):
        return _result(claim, "invalid", "bounded_public_path_required")
    if type(claim["relation"]) is not str or claim["relation"] not in RELATIONS:
        return _result(claim, "invalid", "unknown_relation")
    if type(claim["unit"]) is not str or claim["unit"] not in UNITS:
        return _result(claim, "needs_review", "unregistered_display_unit")
    places = claim.get("decimal_places")
    if "decimal_places" in claim and (type(places) is not int or not 0 <= places <= 8 or claim["relation"] != "eq"):
        return _result(claim, "invalid", "rounding_only_for_eq_0_to_8_places")
    try:
        source = _lookup(saved["public"], path)
    except (KeyError, IndexError):
        return _result(claim, "unsupported", "cited_field_not_present")
    if path[:2] == ['experiment', 'declared_check_results']:
        from .registered_contrast_claims import bound_results, numeric_unit
        try:
            source_unit = numeric_unit(path, bound_results(saved, evidence, research_class))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return _result(claim, 'unsupported', 'registered_contrast_binding_missing_or_invalid', detail=str(exc))
    elif saved.get('action') == 'inspect_batch':
        from .batch_claim_support import bound_unit
        try:
            if type(frozen_case) is not dict or frozen_case.get('research_class') != research_class:
                raise ValueError('frozen batch research class mismatch')
            source_unit = bound_unit(saved, evidence, path, frozen_case)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return _result(claim, 'unsupported', 'batch_account_binding_missing_or_invalid', detail=str(exc))
    elif (saved.get('action') == 'diagnose_execution' and len(path) == 3
          and path[0] == 'pairs' and type(path[1]) is int and path[2] == 'comparison_minus_reference_fees'):
        from .batch_claim_support import bound_fee_difference_unit
        try:
            if type(frozen_case) is not dict or frozen_case.get('research_class') != research_class:
                raise ValueError('frozen diagnosis research class mismatch')
            source_unit = bound_fee_difference_unit(saved, path, frozen_case)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return _result(claim, 'unsupported', 'fee_difference_binding_missing_or_invalid', detail=str(exc))
    else:
        source_unit = _unit(saved.get("action"), path, saved["public"], research_class)
    if source_unit is None:
        return _result(claim, "needs_review", "unregistered_metric_semantics")
    if source is None:
        return _result(claim, "needs_review", "saved_value_unknown_not_zero")
    if UNITS[source_unit][0] != UNITS[claim["unit"]][0]:
        return _result(claim, "unsupported", "incompatible_units", source_unit=source_unit)
    try:
        number = _number(source)
    except ValueError:
        return _result(claim, "needs_review", "saved_value_not_finite_numeric")
    try:
        if claim["relation"] == "between":
            if type(claim["value"]) is not list or len(claim["value"]) != 2:
                raise ValueError("between requires two ordered endpoints")
            values = [_number(v) for v in claim["value"]]
            if values[0] > values[1]:
                raise ValueError("interval bounds reversed")
        else:
            values = [_number(claim["value"])]
    except ValueError as exc:
        return _result(claim, "invalid", "invalid_claim_value", detail=str(exc))
    # Independent of process decimal settings and user-supplied tolerance.
    with localcontext() as ctx:
        ctx.prec = 220
        display = number * UNITS[source_unit][1] / UNITS[claim["unit"]][1]
        compared = display.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) if places is not None else display
        if places is not None and values[0] != values[0].quantize(Decimal(1).scaleb(-places)):
            return _result(claim, "invalid", "value_exceeds_declared_decimal_places")
        relation = claim["relation"]
        matches = {"eq": lambda: compared == values[0], "gt": lambda: display > values[0],
                   "ge": lambda: display >= values[0], "lt": lambda: display < values[0],
                   "le": lambda: display <= values[0],
                   "between": lambda: values[0] <= display <= values[1]}[relation]()
    return _result(claim, "supported" if matches else "contradicted", "saved_numeric_comparison",
        source_value=str(number), source_unit=source_unit, display_value=str(display),
        compared_value=str(compared), rounding="ROUND_HALF_UP" if places is not None else "none",
        text_support="unverified" if claim.get("text") else "not_supplied")


def review_claims(claims, evidence, *, research_class, report_text="", frozen_case=None):
    """Return non-mutating, deterministic findings without rejecting legal reports.

    Callers must limit evidence to IDs cited by this report and owned by the task.
    The ``evidence_support_accepted`` flag covers declared structured claims ONLY.
    It never covers report_text, even when every numeric tuple is correct.
    """
    if type(claims) is not list or not 1 <= len(claims) <= 16:
        raise ValueError("claims must contain 1..16 items")
    if type(evidence) is not dict or type(research_class) is not str or not research_class:
        raise ValueError("controller evidence mapping and frozen research_class required")
    if type(report_text) is not str:
        raise ValueError("report_text must be text")
    results = []
    seen = set()
    for claim in claims:
        if type(claim) is not dict:
            results.append({"claim_id": None, "status": "invalid", "reason": "claim_object_required"})
            continue
        cid = claim.get("claim_id")
        if type(cid) is str and cid in seen:
            results.append(_result(claim, "invalid", "duplicate_claim_id"))
            continue
        if type(cid) is str:
            seen.add(cid)
        results.append(_review_one(claim, evidence, research_class, frozen_case))
    return {
        "version": VERSION, "claims": results,
        "evidence_support_accepted": all(row["status"] == "supported" for row in results),
        "acceptance_scope": "declared structured claims only; free text and claim-to-prose correspondence unverified",
        "report_text_support": "needs_review" if report_text else "not_supplied",
        "general_report_truth_verified": False, "causal_identification_verified": False,
        "formal_target_success": False,
    }
