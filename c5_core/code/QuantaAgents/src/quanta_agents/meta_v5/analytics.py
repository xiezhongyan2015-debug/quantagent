"""Development-only heterogeneous evidence; no market reader or significance claim.

Every expected unit/year stays in the denominator. An observed subtotal is
explicitly descriptive and cannot replace a missing full-scope quantity.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from statistics import mean, stdev

VERSION = "v5_stability_profile_v1"
POLICY_VERSION = "v5_stability_policy_v1"
POLICY_KEYS = frozenset(("version", "expected_years", "annualization", "risk_free_rate",
    "min_sessions_per_year", "min_paired_cell_fraction", "min_unit_coverage_fraction",
    "min_positive_excess_year_fraction", "min_positive_excess_unit_fraction",
    "max_worst_year_excess_loss", "max_drawdown", "max_positive_pnl_concentration",
    "max_stale_fraction", "require_known_fees", "require_exposure", "require_execution_certified"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_policy(policy):
    _need(type(policy) is dict and set(policy) == POLICY_KEYS, "complete explicit V5 policy required")
    _need(policy["version"] == POLICY_VERSION, "unsupported stability policy version")
    years = policy["expected_years"]
    _need(type(years) is list and bool(years) and all(type(y) is int and 1900 <= y <= 2200 for y in years)
          and years == sorted(set(years)), "expected_years must be fixed ordered unique integers")
    _need(years == list(range(years[0], years[-1] + 1)), "expected_years cannot omit intervening calendar years")
    for key in ("annualization", "min_sessions_per_year"):
        _need(type(policy[key]) is int and policy[key] > 0, "positive integer policy: " + key)
    _need(_number(policy["risk_free_rate"]) and -1 < policy["risk_free_rate"] < 1, "invalid risk-free rate")
    for key in ("min_paired_cell_fraction", "min_unit_coverage_fraction", "min_positive_excess_year_fraction",
                "min_positive_excess_unit_fraction", "max_drawdown", "max_positive_pnl_concentration", "max_stale_fraction"):
        _need(_number(policy[key]) and 0 <= policy[key] <= 1, "fraction policy outside [0,1]: " + key)
    _need(_number(policy["max_worst_year_excess_loss"]) and policy["max_worst_year_excess_loss"] >= 0,
          "nonnegative excess-loss tolerance required")
    for key in ("require_known_fees", "require_exposure", "require_execution_certified"):
        _need(type(policy[key]) is bool, "explicit boolean policy: " + key)
    return digest(policy)


def _series(value, length, label):
    if value is None:
        return None
    _need(type(value) is dict and "nav" in value, label + " requires nav")
    out = {}
    for key in ("nav", "exposure", "fees", "stale"):
        data = value.get(key)
        if data is None:
            out[key] = [None] * length
            continue
        _need(type(data) is list and len(data) == length, label + ": invalid " + key + " length")
        for x in data:
            if x is None:
                continue
            if key == "stale":
                _need(type(x) in (int, bool) and x >= 0, label + ": invalid stale observation")
            else:
                _need(_number(x) and x >= 0 and (key != "nav" or x > 0), label + ": invalid " + key)
                if key == "exposure":
                    _need(x <= 1 + 1e-8, label + ": exposure exceeds full capital")
        out[key] = list(data)
    return out


def _empty_metrics(sessions=0):
    return {"sessions": sessions, "observed_nav_sessions": 0, "complete": False,
        "starting_nav": None, "ending_nav": None, "return": None, "sharpe": None,
        "max_drawdown": None, "net_change": None, "average_exposure": None,
        "fees": None, "stale_fraction": None, "known_fee_sessions": 0, "known_exposure_sessions": 0,
        "known_stale_sessions": 0}


def _metrics(series, indices, initial, annualization, risk_free_rate, scope_complete=True):
    if series is None or not indices:
        return _empty_metrics(len(indices))
    rows = [series["nav"][i] for i in indices]
    start = initial if indices[0] == 0 else series["nav"][indices[0] - 1]
    complete = scope_complete and start is not None and all(x is not None for x in rows)
    out = _empty_metrics(len(indices))
    out.update(observed_nav_sessions=sum(x is not None for x in rows), complete=complete,
               starting_nav=start, ending_nav=rows[-1])
    for field, metric, operation in (("exposure", "average_exposure", mean), ("fees", "fees", sum),
                                      ("stale", "stale_fraction", lambda xs: sum(x > 0 for x in xs) / len(xs))):
        values = [series[field][i] for i in indices]
        out["known_" + ("fee" if field == "fees" else field) + "_sessions"] = sum(x is not None for x in values)
        out[metric] = float(operation(values)) if all(x is not None for x in values) else None
    if complete:
        path = [start] + rows
        returns = [right / left - 1 for left, right in zip(path, path[1:])]
        sd = stdev(returns) if len(returns) > 1 else None
        rf = (1 + risk_free_rate) ** (1 / annualization) - 1
        peak = start
        worst = 0.0
        for nav in rows:
            peak = max(peak, nav); worst = max(worst, 1 - nav / peak)
        out.update(return_=rows[-1] / start - 1)
        out["return"] = out.pop("return_")
        out.update(sharpe=(mean(returns) - rf) / sd * math.sqrt(annualization) if sd and sd > 0 else None,
                   max_drawdown=worst, net_change=rows[-1] - start)
    return out


def _status(known, passed):
    return "not_evaluable" if not known else "pass" if passed else "fail"


def evaluate_bundle(bundle, policy):
    """Evaluate a frozen development bundle. No formal acceptance is possible."""
    bundle, policy = deepcopy(bundle), deepcopy(policy)
    policy_hash = validate_policy(policy)
    _need(type(bundle) is dict and bundle.get("version") == "v5_stability_bundle_v1", "invalid bundle version")
    _need(bundle.get("split") == "development", "V5 analytics cannot consume confirmation/final inputs")
    for key in ("candidate_id", "scope_id"):
        _need(type(bundle.get(key)) is str and bool(bundle[key].strip()), "missing " + key)
    _need(_hash(bundle.get("program_hash")), "program_hash must be an exact SHA256")
    units = bundle.get("expected_units")
    _need(type(units) is list and bool(units) and len(units) == len(set(units))
          and all(type(u) is str and u and ":" not in u for u in units), "fixed ordered expected_units required")
    pairs = bundle.get("pairs")
    _need(type(pairs) is list, "pairs must be a list, including missing-unit absence")
    provenance = bundle.get("provenance")
    _need(type(provenance) is dict and type(provenance.get("accounting_mode")) is str
          and provenance["accounting_mode"], "accounting_mode provenance required")
    for key in ("execution_certified", "exposed"):
        _need(type(provenance.get(key)) is bool, "explicit provenance boolean: " + key)
    for key, required in (("account_currency", "CNY"), ("fee_unit", "CNY"),
                          ("external_cash_flows", "none_after_initial")):
        _need(provenance.get(key) == required, "explicit supported provenance required: " + key + "=" + required)
    source_hashes = provenance.get("source_hashes")
    _need(type(source_hashes) is dict and bool(source_hashes) and all(type(k) is str and k and _hash(v)
          for k, v in source_hashes.items()), "source_hashes must bind observed sources")
    source_scope_identity = provenance.get("source_scope_identity")
    if source_scope_identity is None:
        # Unclassified sources are conservatively all common. Explicit scope
        # identity separates common market inputs from revision result artifacts.
        source_scope_identity = {"unclassified_source_hashes": source_hashes}
    else:
        _need(type(source_scope_identity) in (str, dict) and bool(source_scope_identity),
              "nonempty declared source_scope_identity required")
        digest(source_scope_identity)
    cost_hash = provenance.get("cost_policy_hash")
    if "cost_policy" in provenance:
        _need(type(provenance["cost_policy"]) is dict and bool(provenance["cost_policy"]), "explicit cost_policy object required")
        derived = digest(provenance["cost_policy"])
        _need(cost_hash is None or cost_hash == derived, "cost policy hash mismatch")
        cost_hash = derived
    _need(cost_hash is None or _hash(cost_hash), "invalid cost policy hash")
    bundle_hash = digest(bundle)
    for key in ("bundle_hash",):
        if key in bundle:
            _need(bundle[key] == digest({k: v for k, v in bundle.items() if k != key}), "bundle content hash drift")
    years = policy["expected_years"]
    catalog, identities = {}, []
    for pair in pairs:
        _need(type(pair) is dict and pair.get("unit_id") in units and pair["unit_id"] not in catalog,
              "unknown or duplicate unit pair")
        uid = pair["unit_id"]
        _need(pair.get("unit_kind") in ("stock", "portfolio", "index"), "unknown unit_kind")
        calendar = pair.get("calendar")
        _need(type(calendar) is list and bool(calendar) and calendar == sorted(set(calendar)), "unique ordered pair calendar required")
        for day in calendar:
            _need(type(day) is str and date.fromisoformat(day).isoformat() == day and int(day[:4]) in years,
                  "calendar outside frozen expected years")
        initial = pair.get("initial_nav")
        _need(_number(initial) and initial > 0, "positive full initial capital required")
        candidate = _series(pair.get("candidate"), len(calendar), uid + " candidate")
        benchmark = _series(pair.get("benchmark"), len(calendar), uid + " benchmark")
        for side in ("candidate", "benchmark"):
            series = pair.get(side)
            if series is not None:
                _need("calendar" not in series or series["calendar"] == calendar, "candidate/benchmark calendars differ")
                _need("initial_nav" not in series or series["initial_nav"] == initial, "candidate/benchmark initial capital differs")
        if "program_hash" in pair:
            _need(pair["program_hash"] == bundle["program_hash"], "pair program hash drift")
        if "scope_id" in pair:
            _need(pair["scope_id"] == bundle["scope_id"], "pair scope drift")
        catalog[uid] = (pair, calendar, initial, candidate, benchmark)
    cells, full_period, issues = [], [], []

    def issue(code, cell_ids, message, severity="warning"):
        body = {"severity": severity, "code": code, "cell_ids": list(cell_ids), "message": message}
        issues.append({"id": "issue_" + digest(body)[:16], **body})

    for uid in units:
        item = catalog.get(uid)
        if item is None:
            identities.append({"unit_id": uid, "missing": True})
            for year in years:
                cid = f"{uid}:{year}"
                cells.append({"cell_id": cid, "unit_id": uid, "unit_kind": None, "year": year, "sessions": 0,
                    "complete": False, "candidate_metrics": _empty_metrics(), "benchmark_metrics": _empty_metrics(),
                    "excess_return": None, "quality_flags": ["missing_unit"], "calendar_hash": None})
            full_period.append({"unit_id": uid, "unit_kind": None, "complete": False,
                "candidate_metrics": _empty_metrics(), "benchmark_metrics": _empty_metrics(), "excess_return": None,
                "positive_pnl_concentration": None, "calendar_hash": None})
            issue("missing_unit", [f"{uid}:{y}" for y in years], "Expected unit is absent; its years remain unknown.", "error")
            continue
        pair, calendar, initial, candidate, benchmark = item
        identities.append({"unit_id": uid, "unit_kind": pair["unit_kind"], "calendar": calendar,
            "calendar_hash": digest(calendar), "initial_nav": initial,
            "benchmark_hash": digest(pair.get("benchmark")), "benchmark_id": pair.get("benchmark_id"),
            "cost_policy_hash": pair.get("cost_policy_hash", cost_hash)})
        if "cost_policy_hash" in pair:
            _need(pair["cost_policy_hash"] == cost_hash, "pair cost policy identity differs")
        yearly = []
        for year in years:
            indices = [i for i, d in enumerate(calendar) if int(d[:4]) == year]
            cid = f"{uid}:{year}"
            # Only the scope's first year can start at initial capital. Missing
            # intervening years cannot turn a multi-year change into one year.
            boundary_known = bool(indices) and (year == years[0] or
                (indices[0] > 0 and int(calendar[indices[0] - 1][:4]) == year - 1))
            cm = _metrics(candidate, indices, initial, policy["annualization"], policy["risk_free_rate"], boundary_known)
            bm = _metrics(benchmark, indices, initial, policy["annualization"], policy["risk_free_rate"], boundary_known)
            if not boundary_known:
                cm["starting_nav"] = bm["starting_nav"] = None
            flags = []
            if not indices:
                flags.append("missing_year")
            if len(indices) < policy["min_sessions_per_year"]:
                flags.append("insufficient_sessions")
            if not cm["complete"]: flags.append("candidate_nav_incomplete")
            if not bm["complete"]: flags.append("benchmark_nav_incomplete")
            if any(m["fees"] is None for m in (cm, bm)): flags.append("fees_unknown")
            if any(m["average_exposure"] is None for m in (cm, bm)): flags.append("exposure_unknown")
            if any(m["stale_fraction"] is None for m in (cm, bm)): flags.append("stale_quality_unknown")
            elif any(m["stale_fraction"] > policy["max_stale_fraction"] for m in (cm, bm)): flags.append("stale_quality_exceeded")
            paired = bool(cm["complete"] and bm["complete"] and len(indices) >= policy["min_sessions_per_year"])
            excess = cm["return"] - bm["return"] if paired else None
            row = {"cell_id": cid, "unit_id": uid, "unit_kind": pair["unit_kind"], "year": year,
                "sessions": len(indices), "complete": paired, "candidate_metrics": cm, "benchmark_metrics": bm,
                "excess_return": excess, "quality_flags": flags, "calendar_hash": digest([calendar[i] for i in indices])}
            cells.append(row); yearly.append(row)
            for flag in flags:
                if flag in ("fees_unknown", "exposure_unknown", "stale_quality_unknown", "stale_quality_exceeded",
                            "missing_year", "candidate_nav_incomplete", "benchmark_nav_incomplete", "insufficient_sessions"):
                    issue(flag, [cid], "Annual evidence reports " + flag + "; no zero or reduced denominator substituted.",
                          "error" if flag in ("missing_year", "candidate_nav_incomplete", "benchmark_nav_incomplete") else "warning")
            if excess is not None and excess < -policy["max_worst_year_excess_loss"]:
                issue("annual_excess_deterioration", [cid], "Annual paired excess loss exceeds the frozen development tolerance.")
        complete_scope = all(row["complete"] for row in yearly)
        cm = _metrics(candidate, list(range(len(calendar))), initial, policy["annualization"], policy["risk_free_rate"], complete_scope)
        bm = _metrics(benchmark, list(range(len(calendar))), initial, policy["annualization"], policy["risk_free_rate"], complete_scope)
        changes = [row["candidate_metrics"]["net_change"] for row in yearly]
        concentration = None
        if all(v is not None for v in changes):
            positive = [max(v, 0.0) for v in changes]
            concentration = max(positive) / sum(positive) if sum(positive) > 0 else None
        if concentration is not None and concentration > policy["max_positive_pnl_concentration"]:
            issue("positive_pnl_concentration", [r["cell_id"] for r in yearly], "A large share of positive annual cash PnL comes from one year; descriptive concentration, not independent significance.")
        full_period.append({"unit_id": uid, "unit_kind": pair["unit_kind"], "complete": complete_scope,
            "candidate_metrics": cm, "benchmark_metrics": bm, "excess_return": cm["return"] - bm["return"] if complete_scope else None,
            "positive_pnl_concentration": concentration, "calendar_hash": digest(calendar)})

    yearly_summary = []
    for year in years:
        rows = [r for r in cells if r["year"] == year]
        vals = [r["excess_return"] for r in rows if r["excess_return"] is not None]
        yearly_summary.append({"year": year, "expected_units": len(units), "paired_units": len(vals),
            "mean_excess_return": mean(vals) if len(vals) == len(units) else None,
            "observed_mean_excess_return": mean(vals) if vals else None,
            "min_observed_excess_return": min(vals) if vals else None,
            "max_observed_excess_return": max(vals) if vals else None,
            "positive_observed_units": sum(x > 0 for x in vals), "unit_excess_returns": {r["unit_id"]: r["excess_return"] for r in rows}})
    paired_cells = sum(c["complete"] for c in cells)
    complete_units = sum(f["complete"] for f in full_period)
    year_excess = [r["mean_excess_return"] for r in yearly_summary]
    unit_excess = [r["excess_return"] for r in full_period]
    all_years = all(v is not None for v in year_excess)
    all_units = all(v is not None for v in unit_excess)
    positive_year_fraction = sum(v > 0 for v in year_excess) / len(years) if all_years else None
    positive_unit_fraction = sum(v > 0 for v in unit_excess) / len(units) if all_units else None
    risks = [c["candidate_metrics"]["max_drawdown"] for c in cells]
    full_risks = [f["candidate_metrics"]["max_drawdown"] for f in full_period]
    quality_metrics = [c[side] for c in cells for side in ("candidate_metrics", "benchmark_metrics")]
    quality_known = cost_hash is not None and all(m["stale_fraction"] is not None for m in quality_metrics)
    quality_pass = all(m["stale_fraction"] is not None and m["stale_fraction"] <= policy["max_stale_fraction"] for m in quality_metrics)
    if policy["require_known_fees"]: quality_known &= all(m["fees"] is not None for m in quality_metrics)
    if policy["require_exposure"]: quality_known &= all(m["average_exposure"] is not None for m in quality_metrics)
    if cost_hash is None:
        issue("cost_policy_unknown", [c["cell_id"] for c in cells], "No frozen cost policy identity; relative economics cannot be certified comparable.", "error")
    concentrations = [r["positive_pnl_concentration"] for r in full_period]
    concentration_known = all_units
    concentration_pass = all(v is None or v <= policy["max_positive_pnl_concentration"] for v in concentrations)
    statuses = {
        "coverage": _status(True, paired_cells / len(cells) >= policy["min_paired_cell_fraction"] and complete_units / len(units) >= policy["min_unit_coverage_fraction"]),
        "source_quality": _status(quality_known, quality_pass),
        "annual_increment": _status(all_years, all_years and positive_year_fraction >= policy["min_positive_excess_year_fraction"]),
        "cross_unit_increment": _status(all_units, all_units and positive_unit_fraction >= policy["min_positive_excess_unit_fraction"]),
        "heterogeneity": _status(all_years, all_years and all(v >= -policy["max_worst_year_excess_loss"] for v in year_excess)
            and all(c["excess_return"] is not None and c["excess_return"] >= -policy["max_worst_year_excess_loss"] for c in cells)),
        "risk": _status(all(v is not None for v in risks + full_risks), all(v is not None and v <= policy["max_drawdown"] for v in risks + full_risks)),
        "concentration": _status(concentration_known, concentration_pass),
        "execution": "pass" if provenance["execution_certified"] else "not_certified"}
    all_cell_ids = [c["cell_id"] for c in cells]
    for state, code in (("coverage", "coverage_below_policy"),
                        ("annual_increment", "annual_increment_insufficient"),
                        ("cross_unit_increment", "cross_unit_increment_insufficient"),
                        ("risk", "risk_limit_exceeded")):
        if statuses[state] == "fail":
            issue(code, all_cell_ids, "Frozen development policy failed for " + state + "; this is not a formal-test conclusion.")
    if policy["require_execution_certified"] and not provenance["execution_certified"]:
        issue("execution_not_certified", all_cell_ids, "Required execution certification is not present; a provenance declaration cannot establish it.", "error")
    required_statuses = [v for k, v in statuses.items() if k != "execution"]
    eligible = all(v == "pass" for v in required_statuses) and (not policy["require_execution_certified"] or provenance["execution_certified"])
    comparison_identity = {"scope_id": bundle["scope_id"], "split": bundle["split"], "expected_units": units,
        "expected_years": years, "units": identities, "accounting_mode": provenance["accounting_mode"],
        "account_currency": provenance["account_currency"], "fee_unit": provenance["fee_unit"],
        "external_cash_flows": provenance["external_cash_flows"],
        "cost_policy_hash": cost_hash, "source_scope_identity": source_scope_identity,
        "execution_certified": provenance["execution_certified"]}
    profile = {"version": VERSION, "candidate_id": bundle["candidate_id"], "program_hash": bundle["program_hash"],
        "scope_id": bundle["scope_id"], "split": "development", "bundle_hash": bundle_hash,
        "expected_units": units, "expected_years": years, "provenance": provenance,
        "public_contract": {"version": "v5_stability_public_contract_v1",
            "cell_order": "expected_units_then_expected_years", "expected_units": units,
            "expected_years": years, "status_keys": list(statuses), "development_policy_only": True},
        "policy": policy, "policy_hash": policy_hash, "comparison_identity": comparison_identity,
        "comparison_hash": digest(comparison_identity), "cells": cells, "full_period": full_period,
        "summary": {"expected_units": len(units), "expected_years": years, "expected_cells": len(cells),
            "paired_cells": paired_cells, "paired_cell_fraction": paired_cells / len(cells), "complete_units": complete_units,
            "unit_coverage_fraction": complete_units / len(units), "yearly_excess_distribution": yearly_summary,
            "unit_full_period_excess": {r["unit_id"]: r["excess_return"] for r in full_period},
            "positive_excess_year_fraction": positive_year_fraction, "positive_excess_unit_fraction": positive_unit_fraction,
            "statuses": statuses, "issue_counts": dict(Counter(i["code"] for i in issues)),
            "dependence_statement": "Units and years may overlap or share market shocks; no independence or significance inferred."},
        "issues": issues, "development_eligible": bool(eligible), "formal_target_success": False,
        "causal_mechanism_identified": False, "statistical_significance_established": False,
        "exposed": provenance["exposed"], "policy_scope": "Explicit development screen only; not formal validation or annual-profit guarantee.",
        "accounting_declaration_limit": "CNY NAV and fees with no cash flows after initial capital are declared input conditions, not independently certified cash-ledger evidence."}
    profile["profile_hash"] = digest(profile)
    return profile


def compare_profiles(old, new):
    """Describe a paired revision; never accept a strategy or erase regressions."""
    for p in (old, new):
        _need(type(p) is dict and p.get("version") == VERSION, "invalid profile version")
        _need(p.get("profile_hash") == digest({k: v for k, v in p.items() if k != "profile_hash"}), "profile hash drift")
        _need(p.get("policy_hash") == validate_policy(p["policy"]), "profile policy drift")
        _need(p.get("comparison_hash") == digest(p["comparison_identity"]), "comparison identity drift")
    _need(old["scope_id"] == new["scope_id"] and old["policy_hash"] == new["policy_hash"], "revision scope/policy changed")
    _need(old["comparison_identity"] == new["comparison_identity"], "revision units/calendar/benchmark/capital/cost/source identity changed")
    _need([c["cell_id"] for c in old["cells"]] == [c["cell_id"] for c in new["cells"]], "revision cell set changed")
    comparisons = []
    for left, right in zip(old["cells"], new["cells"]):
        x, y = left["excess_return"], right["excess_return"]
        delta = y - x if x is not None and y is not None else None
        ddx, ddy = left["candidate_metrics"]["max_drawdown"], right["candidate_metrics"]["max_drawdown"]
        comparisons.append({"cell_id": left["cell_id"], "unit_id": left["unit_id"], "year": left["year"],
            "old_excess_return": x, "new_excess_return": y, "excess_return_change": delta,
            "max_drawdown_change": ddy - ddx if ddx is not None and ddy is not None else None,
            "status": "unknown" if delta is None else "improved" if delta > 0 else "degraded" if delta < 0 else "unchanged"})
    degraded = [c["cell_id"] for c in comparisons if c["status"] == "degraded"]
    drawdown_degraded = [c["cell_id"] for c in comparisons if c["max_drawdown_change"] is not None and c["max_drawdown_change"] > 1e-12]
    unknown = [c["cell_id"] for c in comparisons if c["status"] == "unknown"]
    result = {"version": "v5_profile_comparison_v1", "old_profile_hash": old["profile_hash"],
        "new_profile_hash": new["profile_hash"], "old_program_hash": old["program_hash"], "new_program_hash": new["program_hash"],
        "scope_id": old["scope_id"], "policy_hash": old["policy_hash"], "comparison_hash": old["comparison_hash"],
        "paired": True, "cells": comparisons, "degraded_cell_ids": degraded,
        "drawdown_degraded_cell_ids": drawdown_degraded, "unknown_cell_ids": unknown,
        "heterogeneity_review_required": bool(degraded or drawdown_degraded or unknown), "new_development_eligible": new["development_eligible"],
        "adoption_recommended": False, "formal_target_success": False,
        "interpretation": "Every expected cell retained. Aggregate gains cannot erase annual regressions; scientific adoption requires the bound reflection."}
    result["comparison_result_hash"] = digest(result)
    return result
