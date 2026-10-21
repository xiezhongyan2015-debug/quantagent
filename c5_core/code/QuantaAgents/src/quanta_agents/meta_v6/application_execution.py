"""Owned-job workers for frozen cycle-04 application accounts.

Declarations and input hashes are checked before an exclusive durable intent;
market arrays are loaded only after that intent. No model or automatic retry.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import traceback

import pandas as pd

from .cycle_execution import (_claim, _feasible, _policies, _summary, _targets,
                              _validate_native, _winner)
from .portfolio import DailyAccount, PortfolioSpec, target_weights
from .portfolio_study import file_hash, load_panel, read, save_account
from .research import PROTOCOL, save_once
from .temporal import _account_proofs, _proof, _records, _require, _verify, temporal_segment

VERSION = "v6_frozen_information_application_execution_v2_reference_recovery"
CYCLE, FACTOR_CYCLE = "04_information_application", "03_factor_information_expansion"
DESIGN_CALL = "05_information_application_design"
START, FIT_END, END = "2016-01-01", "2020-12-31", "2024-12-31"
STRESSES = ("base", "slippage_x2", "capacity_half")
FIT_JOB, TEMPORAL_JOB = "01_account_execution", "02_temporal_execution"
DAILY_COLUMNS = ("nav", "return", "cash", "position_value", "exposure", "fees", "slippage",
                 "turnover", "trade_count", "blocked_orders", "stale_value", "stale_fraction",
                 "oldest_held_mark_sessions")
SOURCE_NAMES = ("application_execution.py", "information_application.py", "cycle_execution.py",
                "portfolio.py", "portfolio_study.py", "data.py", "research.py", "temporal.py", "jobs.py")


def _declaration(root):
    receipt = Path(root) / "cycles" / CYCLE / "reference_recovery_receipt.json"
    if receipt.exists():
        from .application_reference_recovery import verify_recovered_application_declaration
        return verify_recovered_application_declaration(root)
    from .information_application import verify_application_declaration
    return verify_application_declaration(root)


def _recovery_proofs(root):
    folder = Path(root) / "cycles" / CYCLE
    receipt_path = folder / "reference_recovery_receipt.json"
    if not receipt_path.exists():
        return []
    receipt = read(receipt_path)
    intent = folder / "reference_recovery_intent.json"
    _require(receipt.get("status") == "completed" and receipt["intent_sha256"] == file_hash(intent)
             and receipt.get("economic_decisions_unchanged") is True
             and receipt.get("original_assessment_refs_restored") is True
             and receipt.get("model_calls") == 0 and receipt.get("account_executions") == 0,
             "offline application reference recovery is not complete or changed economic decisions")
    return ([_proof(receipt_path), _proof(intent), _proof(Path(__file__).with_name("application_reference_recovery.py"))]
            + receipt["input_artifacts"] + receipt["source_proofs"] + receipt["artifacts"])


def _unique_proofs(proofs):
    result = {}
    for proof in proofs:
        path = str(Path(proof["path"]).resolve())
        if path in result:
            _require(result[path]["sha256"] == proof["sha256"], "conflicting input hashes: " + path)
        result[path] = _proof(path)
        _require(result[path]["sha256"] == proof["sha256"], "input changed while preparing: " + path)
    return [result[key] for key in sorted(result)]


def _prepare(root):
    """Read declarations/metadata and hash artifacts; never load market arrays."""
    folder, protocol, declaration, fit = _declaration(root)
    folder = Path(folder).resolve()
    _require(folder == root / "cycles" / CYCLE, "application declaration points to another cycle")
    _require(read(root / "protocol.json") == PROTOCOL, "original research protocol changed")
    specs = [PortfolioSpec(**row) for row in declaration["specs"]]
    _require(len(specs) <= 3 and [s.name for s in specs] == [f"I{i+1}" for i in range(len(specs))],
             "application must retain zero to three ordered I1/I2/I3 proposals")
    _require(declaration["prior_proposals_retained"] == 6
             and declaration["new_proposals"] == len(specs)
             and declaration["cumulative_proposals"] == 6 + len(specs), "application trial counts changed")
    _require(fit.get("2021_2024_result_values_included") is False, "new design evidence contains later factor results")
    factor_folder = root / "cycles" / FACTOR_CYCLE
    index_path, fit_path = factor_folder / "factor_index.json", factor_folder / "fit_factor_view.json"
    index = read(index_path)
    admitted_fit_path = folder / "admitted_fit_factor_view.json"
    _require(index.get("status") == "completed" and index["fit_factor_view_sha256"] == file_hash(fit_path)
             == declaration["original_expansion_fit_view_sha256"]
             and file_hash(admitted_fit_path) == declaration["fit_factor_view_sha256"]
             and read(admitted_fit_path) == fit
             and index["library_snapshot_id"] == declaration["library_snapshot_id"],
             "completed expansion factor evidence differs from the frozen design")
    _require(index.get("prior_scores_recomputed") == 0, "original factors were not preserved as cached evidence")
    old_index = read(root / "factor_index.json")
    _require(index["prior_factors"] == old_index["factors"] and len(index["prior_factors"]) == 9,
             "original nine factor records changed")
    _require(index["scope"]["panel_fingerprint"] == old_index["scope"]["panel_fingerprint"],
             "expansion changed the original market panel")
    required = {name for spec in specs for name, weight in spec.factor_weights.items() if weight}
    required |= {spec.crowding_gate_factor for spec in specs if spec.crowding_gate_factor}
    entries = []
    for name in sorted(required):
        matches = [(entry, owner, base) for owner, base in
                   ((old_index, root / "factors"), (index, factor_folder / "factors"))
                   for entry in owner["factors"] if entry.get("name") == name and entry.get("status") == "evaluated"]
        _require(len(matches) == 1, "application requires one evaluated score artifact: " + name)
        entry, owner, base = matches[0]
        path = Path(entry["scores_path"]).resolve()
        _require(path.is_relative_to(base.resolve()), "application score escaped its frozen factor directory")
        bound = [proof for proof in entry["artifacts"] if Path(proof["path"]).resolve() == path]
        _require(len(bound) == 1 and path.is_file() and file_hash(path) == bound[0]["sha256"]
                 and entry["data_fingerprint"] == owner["scope"]["data_fingerprint"],
                 "application score hash or data identity changed: " + name)
        entries.append({"name": name, "path": str(path), "sha256": bound[0]["sha256"]})
    receipt_folder = root / "model_calls" / DESIGN_CALL
    receipt_path = receipt_folder / "admitted_receipt.json"
    _require(file_hash(receipt_path) == declaration["source_receipt_sha256"], "application researcher receipt changed")
    receipt = read(receipt_path)
    model_proofs = [_proof(receipt_path)]
    for name, expected in receipt.get("artifact_sha256", {}).items():
        path = (receipt_folder / name).resolve()
        _require(path.parent == receipt_folder.resolve() and path.is_file() and file_hash(path) == expected,
                 "application researcher original artifact changed")
        model_proofs.append(_proof(path))
    paths = [root / "protocol.json", root / "factor_index.json", index_path, fit_path, admitted_fit_path,
             folder / "protocol.json", folder / "combination_declaration.json",
             folder / "application_declaration.json"]
    paths += [Path(__file__).with_name(name) for name in SOURCE_NAMES]
    proofs = ([_proof(path) for path in paths] + list(declaration["input_artifacts"]) + model_proofs
              + _recovery_proofs(root))
    proofs += list(index["source_proofs"]) + list(index["artifacts"]) + entries
    # Completed factor source pins may point at preserved source archives. The
    # declaration verifier checks those historical identities; never re-pin them.
    proofs = _unique_proofs(proofs)
    _verify(proofs)
    return folder, specs, index, entries, _policies(), proofs


def _load(root, index, entries):
    panel = load_panel(root)
    _require(panel.dates.min() >= pd.Timestamp(PROTOCOL["data"]["start"])
             and panel.dates.max() <= pd.Timestamp(END)
             and panel.fingerprint() == index["scope"]["panel_fingerprint"], "application market panel changed or exceeds scope")
    scores = {}
    for entry in entries:
        path = Path(entry["path"])
        _require(file_hash(path) == entry["sha256"], "score changed before numeric read")
        values = pd.read_parquet(path)
        _require(file_hash(path) == entry["sha256"], "score changed during numeric read")
        _require(values.index.equals(panel.eligible.index) and values.columns.equals(panel.eligible.columns),
                 "application score axes differ from the fixed panel")
        scores[entry["name"]] = values
    return panel, scores


def _cancelled(root, folder):
    return (root / "cancel.request").exists() or (folder / "cancel.request").exists()


def _validate(native, panel, policy, end):
    _validate_native(native, panel, policy, end)
    _require(tuple(native["daily"].columns) == DAILY_COLUMNS, "account did not preserve all thirteen daily fields")


def _intent(specs, policies, proofs):
    return {"version": VERSION, "cycle_id": CYCLE, "scope": [START, FIT_END],
        "input_artifacts": proofs, "specs": [asdict(s) for s in specs],
        "policies": {key: asdict(value) for key, value in policies.items()},
        "maximum_new_accounts": len(specs) * 3, "model_calls": 0, "automatic_retry": False,
        "annual_account_reset": False, "later_account_results_authorized": False,
        "selection_rule": "all three original stress gates; highest mean full-year net Sharpe then lowest turnover; declaration order breaks exact ties"}


def evaluate_application_accounts(root):
    root = Path(root).resolve()
    folder, specs, index, entries, policies, proofs = _prepare(root)
    intent_path, result_path = folder / "account_execution_intent.json", folder / "account_stage_result.json"
    intent = _intent(specs, policies, proofs)
    if result_path.exists():
        _require(read(intent_path) == intent, "saved application inputs changed")
        saved = read(result_path)
        _require(saved == read(folder / "selection.json") and saved["intent_sha256"] == file_hash(intent_path),
                 "saved application selection changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished application accounts retained; explicit recovery required")
    _claim(intent_path, intent)
    observations, artifacts, completed = [], [], []
    started = 0
    try:
        _require(not _cancelled(root, folder), "application cancelled before numeric read")
        panel, scores = _load(root, index, entries) if specs else (None, {})
        for spec in specs:
            _require(not _cancelled(root, folder), "application cancelled before target generation")
            destination = folder / "accounts/fit" / spec.name
            destination.mkdir(parents=True)
            targets = target_weights(panel, scores, spec, start=START, end=FIT_END)
            artifacts += _targets(destination, targets)
            summaries, feasible = {}, {}
            for stress in STRESSES:
                _require(not _cancelled(root, folder), "application account cancellation requested")
                _verify(proofs)
                policy = policies[stress]
                started += 1
                native = DailyAccount(panel, policy).run(targets, start=START, end=FIT_END,
                                                       cancelled=lambda: _cancelled(root, folder))
                account_dir = destination / stress
                save_account(account_dir, native)
                _validate(native, panel, policy, FIT_END)
                summaries[stress] = _summary(native["summary"], policy)
                feasible[stress] = _feasible(summaries[stress], native["annual"])
                save_once(account_dir / "derived_summary.json", summaries[stress])
                artifacts += [_proof(path) for path in sorted(account_dir.iterdir()) if path.is_file()]
                completed.append({"candidate": spec.name, "stress": stress})
                _verify(proofs)
            row = {"candidate": spec.name, "portfolio_id": spec.portfolio_id,
                   "stress_feasible": all(feasible.values()), "stress_feasibility": feasible, "results": summaries}
            observations.append(row)
            save_once(destination / "result.json", row)
            artifacts.append(_proof(destination / "result.json"))
        winner = _winner(observations)
        result = {"version": VERSION, "cycle_id": CYCLE,
            "status": "no_proposals" if not specs else "completed" if winner else "no_feasible_portfolio",
            "selected": winner["candidate"] if winner else None, "training_observations": observations,
            "intent_sha256": file_hash(intent_path), "artifacts": artifacts,
            "based_only_on": "2016-2020 accounts and the frozen purged factor application declaration",
            "frozen_before_temporal_results": True, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "new_account_executions": started, "new_portfolio_proposals": len(specs),
            "prior_portfolio_proposals_retained": 6, "cumulative_portfolio_proposals": 6 + len(specs),
            "annual_account_reset": False, "model_calls": 0, "formal_target_success": False,
            "financial_success": False, "exposure": "previously_exposed_development; no 2025 numeric data",
            "independent_holdout": False}
        _verify(proofs)
        save_once(folder / "selection.json", result)
        save_once(result_path, result)
        return result
    except Exception as exc:
        save_once(folder / "account_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "intent_sha256": file_hash(intent_path),
            "started_accounts": started, "completed_accounts": completed,
            "expected_accounts": [{"candidate": s.name, "stress": k} for s in specs for k in STRESSES],
            "original_outputs_retained": True, "financial_success": False, "automatic_retry": False})
        raise


def _selected(folder, specs, policies, proofs):
    selection = read(folder / "selection.json")
    _require(selection == read(folder / "account_stage_result.json")
             and selection.get("frozen_before_temporal_results") is True
             and selection["intent_sha256"] == file_hash(folder / "account_execution_intent.json")
             and read(folder / "account_execution_intent.json") == _intent(specs, policies, proofs),
             "application selection or fitting intent changed")
    _verify(selection["artifacts"])
    observations = selection["training_observations"]
    _require([r["candidate"] for r in observations] == [s.name for s in specs], "fitting candidates changed")
    winner = _winner(observations)
    _require(winner is not None and selection["selected"] == winner["candidate"], "no frozen feasible application winner")
    spec = next(s for s in specs if s.name == winner["candidate"])
    _require(spec.portfolio_id == winner["portfolio_id"] and read(folder / "accounts/fit" / spec.name / "result.json") == winner,
             "frozen winner differs from original fitting result")
    return selection, spec, winner


def _fit_accounts(folder, spec, winner, policies, panel):
    saved = {}
    for stress in STRESSES:
        path = folder / "accounts/fit" / spec.name / stress
        _account_proofs(path)
        _require(read(path / "policy.json") == asdict(policies[stress]), "fit stress policy changed")
        summary = _summary(read(path / "summary.json"), policies[stress])
        _require(summary == read(path / "derived_summary.json") == winner["results"][stress], "fit stress summary changed")
        native = {name: pd.read_parquet(path / (name + ".parquet")) for name in ("daily", "trades", "annual")}
        _require(_feasible(summary, native["annual"]), "frozen application winner fails original stress gate")
        _require(native["daily"].index.equals(panel.dates[(panel.dates >= START) & (panel.dates <= FIT_END)]),
                 "fit daily calendar changed")
        saved[stress] = native
    return saved


def _prefix_check(native, fit, source, destination):
    try:
        prefix = native["daily"].loc[START:FIT_END]
        pd.testing.assert_frame_equal(prefix, fit["daily"], check_exact=True, check_like=False)
        trades = native["trades"]
        trade_prefix = trades.loc[pd.to_datetime(trades["date"]).between(START, FIT_END)] if len(trades) else trades
        pd.testing.assert_frame_equal(trade_prefix, fit["trades"], check_exact=True, check_like=False)
        annual = native["annual"]
        pd.testing.assert_frame_equal(annual.loc[annual["year"].between(2016, 2020)], fit["annual"],
                                      check_exact=True, check_like=False)
    except AssertionError as exc:
        save_once(destination / "prefix_check.json", {"passed": False, "error": str(exc)[:6000],
            "all_fields_exact": False, "financial_validation_claimed": False})
        raise ValueError("application continuous account differs from its saved fitting prefix") from exc
    result = {"passed": True, "sessions": len(prefix), "columns": list(prefix.columns),
        "all_fields_exact": True, "index_order_exact": True, "dtypes_exact": True,
        "trades_exact": True, "annual_exact": True,
        "saved_fit_sha256": {name: file_hash(source / (name + ".parquet")) for name in ("daily", "trades", "annual")},
        "comparison": "pandas assert_frame_equal check_exact=True; original raw outputs saved first"}
    save_once(destination / "prefix_check.json", result)
    return result


def evaluate_application_temporal(root):
    root = Path(root).resolve()
    folder, specs, index, entries, policies, common = _prepare(root)
    selection, spec, winner = _selected(folder, specs, policies, common)
    proofs = _unique_proofs(common + [_proof(folder / name) for name in
        ("selection.json", "account_stage_result.json", "account_execution_intent.json")] + selection["artifacts"])
    intent_path, result_path = folder / "temporal_intent.json", folder / "temporal_stage_result.json"
    intent = {"version": VERSION, "cycle_id": CYCLE, "selected": spec.name, "portfolio_id": spec.portfolio_id,
        "selection_sha256": file_hash(folder / "selection.json"), "scope": [START, END],
        "fit_comparison": [START, FIT_END], "temporal_segment": ["2021-01-01", END],
        "policies": {key: asdict(value) for key, value in policies.items()}, "input_artifacts": proofs,
        "maximum_new_accounts": 3, "model_calls": 0, "winner_reselected": False, "annual_account_reset": False,
        "financial_success": False, "automatic_retry": False}
    if result_path.exists():
        _require(read(intent_path) == intent, "saved application temporal inputs changed")
        saved = read(result_path)
        _require(saved["intent_sha256"] == file_hash(intent_path), "saved temporal result intent changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished application temporal retained; explicit recovery required")
    _claim(intent_path, intent)
    observations, artifacts, started = {}, [], 0
    try:
        _require(not _cancelled(root, folder), "application temporal cancelled before numeric read")
        panel, scores = _load(root, index, entries)
        fit = _fit_accounts(folder, spec, winner, policies, panel)
        targets = target_weights(panel, scores, spec, start=START, end=END)
        selected_folder = folder / "accounts/temporal" / spec.name
        selected_folder.mkdir(parents=True)
        artifacts += _targets(selected_folder, targets)
        for stress in STRESSES:
            _require(not _cancelled(root, folder), "application temporal cancellation requested")
            _verify(proofs)
            policy = policies[stress]
            started += 1
            native = DailyAccount(panel, policy).run(targets, start=START, end=END,
                                                   cancelled=lambda: _cancelled(root, folder))
            destination = selected_folder / stress
            save_account(destination, native)
            _validate(native, panel, policy, END)
            prefix = _prefix_check(native, fit[stress], folder / "accounts/fit" / spec.name / stress, destination)
            segment = temporal_segment(native["daily"], policy, panel.dates)
            save_once(destination / "temporal_segment.json", segment)
            observations[stress] = {"full_continuous_account": _summary(native["summary"], policy),
                "annual": _records(native["annual"]), "fit_prefix_check": prefix, "temporal_2021_2024": segment}
            artifacts += [_proof(path) for path in sorted(destination.iterdir()) if path.is_file()]
            _verify(proofs)
        result = {"version": VERSION, "cycle_id": CYCLE, "status": "completed", "selected": spec.name,
            "portfolio_id": spec.portfolio_id, "intent_sha256": file_hash(intent_path),
            "selection_sha256": intent["selection_sha256"], "observations": observations, "artifacts": artifacts,
            "new_account_executions": started, "model_calls": 0, "winner_reselected": False,
            "annual_account_reset": False, "formal_target_success": False, "financial_success": False,
            "exposure": "previously_exposed_development; no 2025 numeric data", "independent_holdout": False}
        save_once(result_path, result)
        return result
    except Exception as exc:
        save_once(folder / "temporal_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "selected": spec.name, "intent_sha256": file_hash(intent_path),
            "started_accounts": started, "completed_stresses": list(observations), "expected_stresses": list(STRESSES),
            "original_outputs_retained": True, "financial_success": False, "automatic_retry": False})
        raise
