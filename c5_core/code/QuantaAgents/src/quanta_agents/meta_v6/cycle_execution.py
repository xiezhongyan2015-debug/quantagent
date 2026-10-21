"""Worker entry points for frozen evidence-led combination cycles.

The CLI supplies an owned job deadline. This module never calls a model, edits
an earlier cycle, changes a selected winner, or retries an unfinished account.
Fit outputs and later continuous-account diagnostics remain separate evidence.
"""
from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import pandas as pd

from .cycles import CYCLE
from .portfolio import AccountPolicy, DailyAccount, PortfolioSpec, target_weights
from .portfolio_study import file_hash, load_panel, read, save_account
from .research import PROTOCOL, save_once
from .temporal import _account_proofs, _proof, _records, _require, _verify, temporal_segment

VERSION = "v6_frozen_combination_cycle_execution_v2_attrs_sidecar"
START, FIT_END, END = "2016-01-01", "2020-12-31", "2024-12-31"
STRESSES = ("base", "slippage_x2", "capacity_half")
DESIGN_CALL = "03_evidence_led_combination_design"


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _claim(path, value):
    """One exclusive durable claim even if two workers enter concurrently."""
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _policies():
    kwargs = {item.name: PROTOCOL["account"][item.name] for item in fields(AccountPolicy)}
    base = AccountPolicy(**kwargs)
    _require(base.capital == 1_000_000., "cycle must retain original one-million account")
    return {"base": base, "slippage_x2": replace(base, slippage=base.slippage * 2),
            "capacity_half": replace(base, max_prior_day_amount_fraction=base.max_prior_day_amount_fraction / 2)}


def _compile_specs(proposals, fit):
    """Independently check the exact recorded transcription; never call design."""
    _require(isinstance(proposals, list) and len(proposals) <= 2, "cycle permits at most two declared portfolios")
    rows = [row for row in fit["factors"] if row["status"] == "evaluated"]
    mapping = {row["factor_key"]: row for row in rows}
    _require(len(mapping) == len(rows), "factor keys must be unique")
    specs = []
    for i, proposal in enumerate(proposals):
        top, buffer = proposal["top_n"], proposal["membership_buffer"]
        _require(type(top) is int and top in {20, 40, 60} and type(buffer) is int and buffer in {0, 2 * top},
                 "cycle portfolio application choices changed")
        components = proposal["components"]
        _require(bool(components) and len({c["factor_key"] for c in components}) == len(components),
                 "unique nonempty registered components required")
        weights = {}
        for component in components:
            key, weight = component["factor_key"], component["weight"]
            _require(key in mapping and key not in {"F3", "F4"} and _finite(weight) and 0 < weight <= 1,
                     "component is unregistered, rejected, or reverses its original direction")
            factor = mapping[key]
            _require(type(factor["direction"]) in (int, float) and factor["direction"] in (-1, 1),
                     "original factor direction must be explicit")
            _require(factor["name"] not in weights, "two factor keys alias one factor name")
            weights[factor["name"]] = weight * factor["direction"]
        gate = proposal["crowding_gate_factor_key"]
        _require(gate in {"", "F6"} and (not gate or gate in mapping), "unregistered crowding gate")
        specs.append(PortfolioSpec(name="R" + str(i + 1), factor_weights=weights, top_n=top,
            max_stock_weight=.05, weighting=proposal["weighting"], market_filter=proposal["market_filter"],
            rebalance_schedule="weekly_last_session", membership_buffer=buffer,
            crowding_gate_factor=mapping[gate]["name"] if gate else "",
            metadata={"model_name": proposal["name"], "hypothesis": proposal["hypothesis"],
                      "falsification": proposal["falsification"], "cycle_id": CYCLE, "source_call": DESIGN_CALL}))
    return specs


def _prepare(root):
    root = Path(root).resolve()
    folder = root / "cycles" / CYCLE
    protocol_path, declaration_path = folder / "protocol.json", folder / "combination_declaration.json"
    protocol, declaration = read(protocol_path), read(declaration_path)
    _require(read(root / "protocol.json") == PROTOCOL, "original research protocol changed")
    _require(protocol["cycle_id"] == CYCLE and protocol["capital"] == 1_000_000
             and protocol["new_portfolio_proposals_max"] == 2 and protocol["new_factor_directions_allowed"] is False
             and protocol["gross_exposure"] == 1. and protocol["max_stock_weight"] == .05,
             "cycle scope, capital or allocation changed")
    prior_result_path = root / "account_stage_result.json"
    rejection_path = root / "model_calls/02_combination_admission/admitted_receipt.json"
    rejection = read(rejection_path)
    decisions = rejection["response"]["decisions"]
    _require(read(prior_result_path).get("status") == "no_admitted_combination"
             and rejection.get("runtime_identity", {}).get("verified") is True
             and len(decisions) == 4 and {d["candidate"] for d in decisions} == {"A", "B", "C", "D"}
             and all(d["decision"] == "reject" for d in decisions), "prior rejected A-D history must remain intact")
    _require(file_hash(prior_result_path) == protocol["prior_stage"]["result_sha256"]
             and file_hash(rejection_path) == protocol["prior_stage"]["model_rejection_sha256"]
             and protocol["prior_stage"]["account_candidates_executed"] == 0,
             "cycle prior-stage binding changed")
    receipt_path = root / "model_calls" / DESIGN_CALL / "admitted_receipt.json"
    receipt = read(receipt_path)
    _require(receipt.get("runtime_identity", {}).get("verified") is True, "cycle design lacks verified model identity")
    _require(declaration["protocol_sha256"] == file_hash(protocol_path)
             and declaration["source_receipt_sha256"] == file_hash(receipt_path)
             and declaration["implementation_sha256"] == file_hash(Path(__file__).with_name("portfolio.py")),
             "cycle declaration, researcher receipt or account implementation changed")
    index_path, fit_path = root / "factor_index.json", root / "fit_factor_view.json"
    index, fit = read(index_path), read(fit_path)
    _require(fit.get("2021_2024_result_values_included") is False, "cycle design evidence contains later-period results")
    _require(index["fit_factor_view_sha256"] == file_hash(fit_path) == protocol["fit_view_sha256"]
             and index["library_snapshot_id"] == protocol["factor_library_snapshot_id"]
             and index["factor_declaration_sha256"] == file_hash(root / "factor_declaration.json"),
             "original factor evidence binding changed")
    specs = _compile_specs(receipt["response"]["portfolios"], fit)
    _require([asdict(spec) for spec in specs] == declaration["specs"]
             and declaration["new_proposals"] == len(specs) and declaration["prior_proposals_retained"] == 4
             and declaration["cumulative_proposals"] == 4 + len(specs)
             and declaration["no_account_results_used_for_this_design"] is True,
             "cycle executable portfolios differ from the recorded model proposals")
    proofs = [_proof(path) for path in [root / "protocol.json", protocol_path, declaration_path,
        prior_result_path, rejection_path, receipt_path, index_path, fit_path, root / "factor_declaration.json"]]
    proofs += [_proof(Path(__file__).with_name(name)) for name in
               ("cycle_execution.py", "cycles.py", "portfolio.py", "portfolio_study.py", "data.py", "research.py", "temporal.py")]
    policies = _policies()
    if not specs:
        return folder, specs, None, {}, policies, proofs
    panel = load_panel(root)
    _require(panel.dates.min() >= pd.Timestamp(PROTOCOL["data"]["start"]) and panel.dates.max() <= pd.Timestamp(END),
             "cycle panel contains out-of-scope or 2025 numeric dates")
    _require(panel.fingerprint() == index["scope"]["panel_fingerprint"], "cycle panel differs from original factor evidence")
    required = {name for spec in specs for name, weight in spec.factor_weights.items() if weight != 0}
    required |= {spec.crowding_gate_factor for spec in specs if spec.crowding_gate_factor}
    scores = {}
    for name in sorted(required):
        entries = [entry for entry in index["factors"] if entry["name"] == name and entry["status"] == "evaluated"]
        _require(len(entries) == 1, "cycle requires one evaluated original factor: " + name)
        entry = entries[0]
        path = Path(entry["scores_path"])
        path = (path if path.is_absolute() else root / path).resolve()
        _require(path.is_relative_to((root / "factors").resolve()), "cycle score artifact escaped the original factor directory")
        matches = [proof for proof in entry["artifacts"] if Path(proof["path"]).resolve() == path]
        _require(len(matches) == 1 and file_hash(path) == matches[0]["sha256"]
                 and entry["data_fingerprint"] == index["scope"]["data_fingerprint"], "cycle factor score source changed")
        scores[name] = pd.read_parquet(path)
        _require(scores[name].index.equals(panel.eligible.index) and scores[name].columns.equals(panel.eligible.columns),
                 "cycle factor axes differ from the complete fixed panel")
        proofs.append(_proof(path))
    _verify(proofs)
    return folder, specs, panel, scores, policies, proofs


def _summary(native, policy):
    summary = dict(native)
    _require(_finite(summary["return"]) and type(summary["sessions"]) is int and summary["sessions"] > 0
             and _finite(summary["terminal_liquidation_cost_estimate"]) and summary["terminal_liquidation_cost_estimate"] >= 0,
             "native return or liquidation-cost observation is invalid")
    summary["net_excess_over_rf_growth"] = 1 + summary["return"] - (1 + policy.risk_free_rate) ** (summary["sessions"] / 252)
    summary["net_excess_after_estimated_liquidation"] = (summary["net_excess_over_rf_growth"]
                                                       - summary["terminal_liquidation_cost_estimate"] / policy.capital)
    return summary


def _validate_native(native, panel, policy, end):
    expected = panel.dates[(panel.dates >= START) & (panel.dates <= end)]
    _require(native["daily"].index.equals(expected), "account omitted or added fixed calendar sessions")
    _require(native["policy"] == asdict(policy), "account returned a different capital or policy")
    _require(native["summary"].get("formal_target_success") is False, "native account claimed formal financial success")


def _feasible(summary, annual):
    records = _records(annual)
    return (len(records) == 5 and [r["year"] for r in records] == list(range(2016, 2021))
        and all(r.get("full_calendar_year") is True and r.get("calendar_complete") is True
                and _finite(r.get("sharpe")) for r in records)
        and summary.get("all_full_year_sharpes_available") is True
        and _finite(summary.get("mean_full_year_sharpe")) and _finite(summary.get("turnover"))
        and summary["net_excess_after_estimated_liquidation"] > 0)


def _winner(observations):
    eligible = [row for row in observations if row["stress_feasible"]]
    return max(eligible, key=lambda row: (row["results"]["base"]["mean_full_year_sharpe"],
                                         -row["results"]["base"]["turnover"])) if eligible else None


def _cancelled(root, folder):
    return (root / "cancel.request").exists() or (folder / "cancel.request").exists()


def _targets(folder, targets):
    path = folder / "targets.parquet"
    _require(not path.exists(), "refusing to overwrite frozen target observations")
    # Arrow extracts every column; pandas would deep-copy the full buffer plan
    # into each Series. Keep that plan on the live target and its JSON sidecar.
    export = targets.copy(deep=False)
    export.attrs = {}
    export.to_parquet(path)
    save_once(folder / "target_attributes.json", targets.attrs)
    return [_proof(path), _proof(folder / "target_attributes.json")]


def evaluate_cycle_accounts(root):
    root = Path(root).resolve()
    folder, specs, panel, scores, policies, proofs = _prepare(root)
    intent_path, result_path = folder / "account_execution_intent.json", folder / "account_stage_result.json"
    intent = {"version": VERSION, "cycle_id": CYCLE, "scope": [START, FIT_END],
        "input_artifacts": proofs, "specs": [asdict(spec) for spec in specs],
        "policies": {key: asdict(policy) for key, policy in policies.items()},
        "maximum_new_accounts": len(specs) * len(STRESSES), "model_calls": 0,
        "later_account_results_authorized": False, "automatic_retry": False}
    if result_path.exists():
        _require(read(intent_path) == intent, "saved cycle accounts belong to different frozen inputs")
        saved = read(result_path)
        _require(saved == read(folder / "selection.json") and saved["intent_sha256"] == file_hash(intent_path),
                 "saved cycle selection binding changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished cycle account stage retained; explicit recovery required before rerun")
    _claim(intent_path, intent)
    observations, artifacts, completed = [], [], []
    started_accounts = 0
    try:
        for spec in specs:
            _require(not _cancelled(root, folder), "cycle account cancellation requested before dispatch")
            candidate_folder = folder / "accounts/fit" / spec.name
            candidate_folder.mkdir(parents=True, exist_ok=True)
            targets = target_weights(panel, scores, spec, start=START, end=FIT_END)
            artifacts += _targets(candidate_folder, targets)
            summaries, feasibility = {}, {}
            for stress in STRESSES:
                _require(not _cancelled(root, folder), "cycle account cancellation requested; completed outputs retained")
                _verify(proofs)
                policy = policies[stress]
                account = DailyAccount(panel, policy)
                started_accounts += 1
                native = account.run(targets, start=START, end=FIT_END, cancelled=lambda: _cancelled(root, folder))
                destination = candidate_folder / stress
                save_account(destination, native)
                _validate_native(native, panel, policy, FIT_END)
                summaries[stress] = _summary(native["summary"], policy)
                feasibility[stress] = _feasible(summaries[stress], native["annual"])
                save_once(destination / "derived_summary.json", summaries[stress])
                artifacts += [_proof(path) for path in sorted(destination.iterdir()) if path.is_file()]
                completed.append({"candidate": spec.name, "stress": stress})
                _verify(proofs)
            observation = {"candidate": spec.name, "portfolio_id": spec.portfolio_id,
                "stress_feasible": all(feasibility.values()), "stress_feasibility": feasibility, "results": summaries}
            observations.append(observation)
            save_once(candidate_folder / "result.json", observation)
            artifacts.append(_proof(candidate_folder / "result.json"))
        winner = _winner(observations)
        selection = {"version": VERSION, "cycle_id": CYCLE, "status": "completed",
            "selected": winner["candidate"] if winner else None, "training_observations": observations,
            "intent_sha256": file_hash(intent_path), "artifacts": artifacts,
            "based_only_on": "2016-2020 accounts and original registered factor evidence",
            "frozen_before_temporal_results": True, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "new_account_executions": started_accounts, "new_portfolio_proposals": len(specs),
            "prior_portfolio_proposals_retained": 4, "cumulative_portfolio_proposals": 4 + len(specs),
            "model_calls": 0, "formal_target_success": False, "financial_success": False,
            "exposure": "previously_exposed_development; no 2025 numeric data", "independent_holdout": False}
        _verify(proofs)
        save_once(folder / "selection.json", selection)
        save_once(result_path, selection)
        return selection
    except Exception as exc:
        save_once(folder / "account_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "intent_sha256": file_hash(intent_path), "started_accounts": started_accounts,
            "completed_accounts": completed, "original_outputs_retained": True,
            "financial_success": False, "automatic_retry": False})
        raise


def _selected_fit(folder, specs, panel, policies, proofs):
    selection = read(folder / "selection.json")
    _require(selection == read(folder / "account_stage_result.json")
             and selection.get("frozen_before_temporal_results") is True
             and selection["intent_sha256"] == file_hash(folder / "account_execution_intent.json"),
             "cycle selection was not bound before temporal results")
    fit_intent = read(folder / "account_execution_intent.json")
    _require(fit_intent["input_artifacts"] == proofs and fit_intent["scope"] == [START, FIT_END]
             and fit_intent["specs"] == [asdict(spec) for spec in specs]
             and fit_intent["policies"] == {key: asdict(policy) for key, policy in policies.items()},
             "cycle fitting inputs, code or policies changed")
    _verify(selection["artifacts"])
    observations = selection["training_observations"]
    _require([row["candidate"] for row in observations] == [spec.name for spec in specs],
             "cycle fitting observations omit or rename declared candidates")
    winner = _winner(observations)
    _require(winner is not None and selection["selected"] == winner["candidate"], "no unique frozen feasible cycle winner")
    selected = next(spec for spec in specs if spec.name == winner["candidate"])
    _require(selected.portfolio_id == winner["portfolio_id"], "selected cycle portfolio identity changed")
    candidate_folder = folder / "accounts/fit" / selected.name
    _require(read(candidate_folder / "result.json") == winner, "winner differs from its original fit result")
    fit = {}
    for stress in STRESSES:
        source = candidate_folder / stress
        _account_proofs(source)
        policy = policies[stress]
        _require(read(source / "policy.json") == asdict(policy), "original fit stress policy changed")
        derived = _summary(read(source / "summary.json"), policy)
        _require(derived == read(source / "derived_summary.json") == winner["results"][stress],
                 "original fit stress summary differs from frozen winner")
        annual = pd.read_parquet(source / "annual.parquet")
        _require(_feasible(derived, annual), "frozen winner did not pass each original fit stress")
        fit[stress] = pd.read_parquet(source / "daily.parquet")
        expected = panel.dates[(panel.dates >= START) & (panel.dates <= FIT_END)]
        _require(fit[stress].index.equals(expected), "original fit daily calendar is incomplete")
    return selection, selected, fit


def evaluate_cycle_temporal(root):
    root = Path(root).resolve()
    folder, specs, panel, scores, policies, common_proofs = _prepare(root)
    selection, spec, fit = _selected_fit(folder, specs, panel, policies, common_proofs)
    proofs = common_proofs + [_proof(folder / name) for name in
        ("selection.json", "account_stage_result.json", "account_execution_intent.json")] + selection["artifacts"]
    intent_path, result_path = folder / "temporal_intent.json", folder / "temporal_stage_result.json"
    intent = {"version": VERSION, "cycle_id": CYCLE, "selected": spec.name,
        "portfolio_id": spec.portfolio_id, "selection_sha256": file_hash(folder / "selection.json"),
        "scope": [START, END], "fit_comparison": [START, FIT_END], "temporal_segment": ["2021-01-01", END],
        "policies": {key: asdict(policy) for key, policy in policies.items()}, "input_artifacts": proofs,
        "maximum_new_accounts": 3, "model_calls": 0, "winner_reselected": False, "annual_account_reset": False,
        "financial_success": False, "automatic_retry": False}
    if result_path.exists():
        _require(read(intent_path) == intent, "saved cycle temporal stage belongs to different frozen inputs")
        saved = read(result_path)
        _require(saved["intent_sha256"] == file_hash(intent_path), "saved cycle temporal intent changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished cycle temporal stage retained; explicit recovery required before rerun")
    _claim(intent_path, intent)
    observations, artifacts = {}, []
    started_accounts = 0
    try:
        _require(not _cancelled(root, folder), "cycle temporal cancellation requested before dispatch")
        targets = target_weights(panel, scores, spec, start=START, end=END)
        selected_folder = folder / "accounts/temporal" / spec.name
        selected_folder.mkdir(parents=True, exist_ok=True)
        artifacts += _targets(selected_folder, targets)
        for stress in STRESSES:
            _require(not _cancelled(root, folder), "cycle temporal cancellation requested; completed outputs retained")
            _verify(proofs)
            policy = policies[stress]
            account = DailyAccount(panel, policy)
            started_accounts += 1
            native = account.run(targets, start=START, end=END, cancelled=lambda: _cancelled(root, folder))
            destination = selected_folder / stress
            save_account(destination, native)
            _validate_native(native, panel, policy, END)
            prefix = native["daily"].loc[(native["daily"].index >= START) & (native["daily"].index <= FIT_END)]
            try:
                pd.testing.assert_frame_equal(prefix, fit[stress], check_exact=True, check_like=False)
            except AssertionError as exc:
                save_once(destination / "prefix_check.json", {"passed": False, "error": str(exc)[:4000],
                    "all_fields_exact": False, "financial_validation_claimed": False})
                raise ValueError("cycle continuous account differs from its saved fitting prefix: " + stress) from exc
            prefix_check = {"passed": True, "sessions": len(prefix), "columns": list(prefix.columns),
                "all_fields_exact": True, "index_order_exact": True,
                "comparison": "pandas assert_frame_equal check_exact=True",
                "saved_fit_daily_sha256": file_hash(folder / "accounts/fit" / spec.name / stress / "daily.parquet")}
            save_once(destination / "prefix_check.json", prefix_check)
            segment = temporal_segment(native["daily"], policy, panel.dates)
            save_once(destination / "temporal_segment.json", segment)
            observations[stress] = {"full_continuous_account": _summary(native["summary"], policy),
                "annual": _records(native["annual"]), "fit_prefix_check": prefix_check,
                "temporal_2021_2024": segment}
            artifacts += [_proof(path) for path in sorted(destination.iterdir()) if path.is_file()]
            _verify(proofs)
        result = {"version": VERSION, "cycle_id": CYCLE, "status": "completed", "selected": spec.name,
            "portfolio_id": spec.portfolio_id, "intent_sha256": file_hash(intent_path),
            "selection_sha256": intent["selection_sha256"], "observations": observations, "artifacts": artifacts,
            "new_account_executions": started_accounts, "model_calls": 0,
            "winner_reselected": False, "annual_account_reset": False, "formal_target_success": False,
            "financial_success": False, "exposure": "previously_exposed_development; no 2025 numeric data",
            "independent_holdout": False}
        save_once(result_path, result)
        return result
    except Exception as exc:
        save_once(folder / "temporal_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "selected": spec.name, "intent_sha256": file_hash(intent_path), "started_accounts": started_accounts,
            "completed_stresses": list(observations), "original_outputs_retained": True,
            "financial_success": False, "automatic_retry": False})
        raise
