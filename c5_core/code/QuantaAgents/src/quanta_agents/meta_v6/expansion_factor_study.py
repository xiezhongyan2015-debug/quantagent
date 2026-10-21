"""Frozen information-expansion factor worker; no model or account execution.

The caller supplies the owned-job deadline. An exclusive durable intent makes
unfinished work visible and prevents implicit replay. Auxiliary-data/HF0280
failures remain local; independently declared OHLC factors can still be tested.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import traceback

import numpy as np
import pandas as pd

from .auxiliary import HF0280_FIELDS, load_auxiliary_panel
from .cross_sectional import compute_hf0280
from .data import BASE_FIELDS, MarketPanel
from .expansion import CALL, CYCLE, HF0280_FIELD, HF0280_SEED, PRIOR_CYCLE
from .factors import FactorEngine, FactorSpec, _parse, _rank_ic
from .library import FactorLibrary
from .portfolio_study import file_hash, load_panel, read
from .research import PROTOCOL, ROOT, save_once
from .risk_diagnostics import evaluate_risk_information, risk_information_semantics
from .study import TABLES, _fit_view, _json, _mean, _proof

VERSION = "v6_information_expansion_factor_study_v1"
START, END = "2015-01-01", "2024-12-31"
FIT_START, FIT_END = "2016-01-01", "2020-12-31"
PURGE = 21
LOG_AMOUNT_CONTROL = "diagnostic_log_amount"
RISK_TABLES = ("daily_ic", "quantile_risk", "coverage", "annual", "common_sample", "bootstrap")
HF_SOURCE_LIMITATIONS = [
    "Current generator semantics use completed same-day and earlier values, but historical cache generation/version/minute-source binding is missing.",
    "rbar_up17/rbar_down17 are same-day extreme seventeen minute-return means, not seventeen-day rolling returns.",
    "overnight_return uses the most recent strictly earlier valid minute price, not necessarily yesterday's official 15:00 close.",
    "Finite auxiliary statistics do not prove all 240 minute prices were valid; historical publication and execution remain uncertified.",
]


class ExpansionIntegrityError(ValueError):
    """A frozen dependency changed; never downgrade this to factor failure."""


def _require(value, message):
    if not value:
        raise ExpansionIntegrityError(message)


def _verify(proofs):
    for proof in proofs:
        path = Path(proof["path"])
        _require(path.is_file() and file_hash(path) == proof["sha256"], "frozen source/artifact changed: " + str(path))


def _claim(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _frame(path, value):
    _require(not path.exists(), "refusing to overwrite expansion evidence: " + str(path))
    exported = value.copy(deep=False)
    exported.attrs = {}
    exported.to_parquet(path)
    if value.attrs:
        save_once(path.with_name(path.stem + "_attributes.json"), value.attrs)


def _cancelled(root, folder):
    if (root / "cancel.request").exists() or (folder / "cancel.request").exists():
        raise InterruptedError("expansion cancelled; saved evidence retained, no implicit retry")


def _admitted(root):
    folder = root / "model_calls" / CALL
    path = folder / "admitted_receipt.json"
    receipt = read(path)
    identity = receipt.get("runtime_identity", {})
    _require(identity.get("verified") is True and identity.get("model") == "gpt-6-astra"
             and identity.get("effort") == "xhigh" and receipt.get("model") == "gpt-6-astra"
             and receipt.get("effort") == "xhigh", "expansion researcher local identity is not admitted")
    bindings = receipt.get("artifact_sha256", {})
    _require({"response.json", "runtime_session.jsonl", "prompt.txt"} <= set(bindings), "incomplete model artifact bindings")
    proofs = [_proof(path)]
    for name, expected in sorted(bindings.items()):
        item = (folder / name).resolve()
        _require(item.parent == folder.resolve() and item.is_file() and file_hash(item) == expected,
                 "expansion model artifact changed: " + name)
        proofs.append(_proof(item))
    _require(read(folder / "response.json") == receipt.get("response"), "admitted response differs from saved original")
    return receipt, proofs


def _prepare(root, library_path):
    folder = root / "cycles" / CYCLE
    protocol, declaration = read(folder / "protocol.json"), read(folder / "factor_declaration.json")
    receipt, proofs = _admitted(root)
    prior_index, prior_fit = read(root / "factor_index.json"), read(root / "fit_factor_view.json")
    registry_path = root / "preparation/hf0280_source_registration.json"
    registry = read(registry_path)
    expected = {"cycle_id": CYCLE, "prior_factors_evaluated": 9, "new_original_seed": HF0280_SEED,
        "new_model_factor_hypotheses_max": 3, "factor_evaluations_max": 4,
        "new_portfolio_proposals_before_factor_results": 0, "new_factor_windows_or_direction_sweeps": False,
        "numeric_scope": [START, END], "model_factor_selection_view": [FIT_START, FIT_END],
        "purge_signal_sessions_at_fit_end": PURGE, "factor_screen": PROTOCOL["factor_screen"],
        "new_2025_numeric_data_allowed": False, "independent_holdout": False,
        "automatic_retry": False, "financial_success": False}
    _require(read(root / "protocol.json") == PROTOCOL and all(protocol.get(k) == v for k, v in expected.items()),
             "expansion protocol changed its fixed factor/data/evaluation scope")
    _require(protocol["prior_factor_index_sha256"] == file_hash(root / "factor_index.json")
             and protocol["prior_fit_view_sha256"] == file_hash(root / "fit_factor_view.json")
             and prior_index["fit_factor_view_sha256"] == protocol["prior_fit_view_sha256"]
             and protocol["prior_library_snapshot_id"] == prior_index["library_snapshot_id"], "prior factor lineage changed")
    _require(prior_index["factor_declaration_sha256"] == file_hash(root / "factor_declaration.json"), "prior declarations changed")
    _require(protocol["auxiliary_registration_id"] == registry["registration_id"]
             and protocol["auxiliary_registration_sha256"] == file_hash(registry_path)
             and registry["market_value_arrays_read"] is False, "auxiliary registry differs from pre-value selection")
    _require(protocol["hf0280_operator_sha256"] == file_hash(Path(__file__).with_name("cross_sectional.py"))
             and protocol["seed_formula_sha256"] == file_hash(ROOT / "experiments/factor_calendar_daily_hf0280_research_sharpe15.yaml"),
             "original HF0280 operator/formula changed")
    generation_names = ("expansion.py", "factors.py", "research.py", "gateway.py")
    _require(protocol.get("generation_source_sha256") == {
        name: file_hash(Path(__file__).with_name(name)) for name in generation_names},
        "generation source changed after hypothesis registration")
    archive_manifest = root / "preparation/source_archive_before_target_serialization_fix/manifest.json"
    _require(protocol.get("prior_source_archive_sha256") == file_hash(archive_manifest), "prior execution source archive changed")
    for archived in read(archive_manifest)["source_files"]:
        _require(file_hash(archived["archive_path"]) == archived["sha256"], "archived prior execution source changed")
    previous = root / "cycles" / PRIOR_CYCLE
    _require(protocol["prior_observations"]["selection_sha256"] == file_hash(previous / "selection.json")
             and protocol["prior_observations"]["temporal_result_sha256"] == file_hash(previous / "temporal_stage_result.json"),
             "declared earlier exposure changed")
    factors = receipt["response"].get("factors")
    _require(isinstance(factors, list) and len(factors) <= 3, "new model hypotheses exceed the frozen allocation")
    _require(declaration.get("cycle_id") == CYCLE and declaration.get("fixed_seeds") == [HF0280_SEED]
             and declaration.get("model_factors") == factors
             and declaration["protocol_sha256"] == file_hash(folder / "protocol.json")
             and declaration["source_receipt_sha256"] == proofs[0]["sha256"]
             and declaration.get("new_model_factor_hypotheses") == len(factors)
             and declaration.get("cumulative_model_factor_hypotheses") == 6 + len(factors)
             and declaration.get("prior_model_factor_hypotheses_retained") == 6
             and declaration.get("new_portfolio_proposals") == 0
             and declaration.get("new_evaluation_results_seen_before_declaration") is False,
             "declaration is not the exact frozen researcher transcription")
    for item in [HF0280_SEED, *factors]:
        _require(isinstance(item.get("name"), str) and bool(item["name"])
                 and isinstance(item.get("expression"), str) and bool(item["expression"])
                 and type(item.get("direction")) is int and item["direction"] in {-1, 1}
                 and type(item.get("primary_horizon")) is int and item["primary_horizon"] in {1, 5, 20}
                 and item.get("role") in {"return_prediction", "risk_information", "conditional_gate"}, "invalid frozen factor declaration")
    _require(len(prior_index["factors"]) == 9 and len(prior_fit["factors"]) == 9
             and prior_fit.get("2021_2024_result_values_included") is False, "original nine factor identities/fit scope missing")
    old_keys = [entry["factor_key"] for entry in prior_index["factors"]]
    _require(len(set(old_keys)) == 9 and set(old_keys) == {entry["factor_key"] for entry in prior_fit["factors"]},
             "prior factor keys are ambiguous")
    old_ids, new_ids, expected_trials = {}, {}, []
    for entry in prior_fit["factors"]:
        spec = FactorSpec(entry["name"], entry["expression"])
        _require(entry["factor_id"] == spec.factor_id, "prior expression identity changed")
        old_ids.setdefault(spec.factor_id, []).append(entry["factor_key"])
    for i, item in enumerate(factors):
        factor_id = FactorSpec(item["name"], item["expression"]).factor_id
        prior_duplicates, new_duplicates = old_ids.get(factor_id, []), list(new_ids.get(factor_id, []))
        expected_trials.append({"declaration_index": i, "factor_id": factor_id,
            "duplicate_of_prior_factor_keys": prior_duplicates, "duplicate_of_new_indices": new_duplicates,
            "status": "duplicate" if prior_duplicates or new_duplicates else "new_expression",
            "new_evaluation_results_seen_before_declaration": False,
            "prior_expression_evidence_already_seen": bool(prior_duplicates), "counts_as_hypothesis_attempt": True})
        new_ids.setdefault(factor_id, []).append(i)
    _require(declaration.get("trial_registration") == expected_trials, "registered trial or prior-exposure identity changed")
    prior_proofs = []
    for entry in prior_index["factors"]:
        _require(entry.get("status") == "evaluated", "original nine evaluated factors must remain visible")
        path = Path(entry["scores_path"]).resolve()
        _require(path.is_relative_to((root / "factors").resolve()), "prior score escaped its frozen source directory")
        bound = [p for p in entry["artifacts"] if Path(p["path"]).resolve() == path]
        _require(len(bound) == 1 and entry["data_fingerprint"] == prior_index["scope"]["data_fingerprint"], "prior score source identity missing")
        # Missing prior scores are a disclosed comparison limitation. A changed
        # existing file is an integrity error, not an optional missing control.
        if path.is_file():
            _require(file_hash(path) == bound[0]["sha256"], "prior score hash mismatch")
            prior_proofs.append(_proof(path))
    paths = [root / "protocol.json", root / "factor_index.json", root / "fit_factor_view.json",
        root / "factor_declaration.json", folder / "protocol.json", folder / "factor_declaration.json",
        folder / "model_exposure.json", folder / "prior_account_observations.json", registry_path,
        previous / "selection.json", previous / "temporal_stage_result.json", archive_manifest,
        ROOT / "validation/meta_v6_hf0280_provenance_20260909/source_trace.md",
        ROOT / "validation/meta_v6_hf0280_provenance_20260909/source_pins.json",
        ROOT / "experiments/factor_calendar_daily_hf0280_research_sharpe15.yaml"]
    paths += [Path(__file__).with_name(name) for name in ("expansion_factor_study.py", "expansion.py", "study.py",
        "factors.py", "library.py", "data.py", "auxiliary.py", "cross_sectional.py", "portfolio_study.py", "research.py", "gateway.py", "risk_diagnostics.py")]
    paths += [Path(row["archive_path"]) for row in read(archive_manifest)["source_files"]]
    paths.append(Path(__file__).parent.parent / "meta/factor_algebra.py")
    proofs += [_proof(path) for path in paths] + prior_proofs
    _verify(proofs)
    declarations = [{"factor_key": "HF0280", "origin": "original_seed", "original": HF0280_SEED},
        *[{"factor_key": f"F{i+7}", "origin": "admitted_model_hypothesis", "original": item} for i, item in enumerate(factors)]]
    fresh_keys = ["HF0280", *[f"F{i+7}" for i, trial in enumerate(expected_trials) if trial["status"] == "new_expression"]]
    risk_semantics = {}
    for entry in declarations:
        item, key = entry["original"], entry["factor_key"]
        if item["role"] != "risk_information":
            continue
        try:
            semantics = risk_information_semantics(direction=item["direction"], horizon=item["primary_horizon"],
                minimum=PROTOCOL["factor_screen"]["minimum_cross_section"], quantiles=PROTOCOL["factor_screen"]["quantiles"],
                control_names=[*old_keys, *[k for k in fresh_keys if k != key], LOG_AMOUNT_CONTROL],
                observed_price_mask_supplied=True)
            risk_semantics[key] = {"status": "declared", "semantics": semantics}
        except ValueError as exc:
            risk_semantics[key] = {"status": "unsupported_declared_risk_horizon", "error": str(exc)}
    identity = {"version": VERSION, "cycle_id": CYCLE, "source_proofs": proofs,
        "library_path": str(library_path), "input_library_snapshot_id": prior_index["library_snapshot_id"],
        "declarations": declarations, "trial_registration": expected_trials,
        "risk_information_predeclaration": risk_semantics,
        "risk_control_policy": "Use available predeclared prior/sibling controls and same-day log(amount); disclose every missing control as partial, never claim complete incremental evidence.",
        "risk_model_view": "Separately computed purged-fit summary, annual, fixed bootstrap, metric/quantile arithmetic means and common-sample coverage; no full-range aggregate reuse.",
        "risk_labels_as_alpha_operands": False, "hf0280_source_limitations": HF_SOURCE_LIMITATIONS,
        "numeric_scope": [START, END], "fit_scope": [FIT_START, FIT_END], "purge_signal_sessions": PURGE,
        "new_model_calls": 0, "account_executions": 0, "automatic_retry": False}
    identity["stage_id"] = hashlib.sha256(_json(identity).encode()).hexdigest()
    return folder, protocol, receipt, registry, prior_index, prior_fit, identity


def _existing(root, panel, prior_index, prior_fit):
    previous, directions, unavailable = {}, {}, []
    fit_by_key = {item["factor_key"]: item for item in prior_fit["factors"]}
    for entry in prior_index["factors"]:
        key, path = entry["factor_key"], Path(entry["scores_path"])
        if not path.is_file():
            unavailable.append({"factor_key": key, "status": "missing_saved_score", "path": str(path)})
            continue
        bound = [proof for proof in entry["artifacts"] if Path(proof["path"]).resolve() == path.resolve()]
        _require(len(bound) == 1 and file_hash(path) == bound[0]["sha256"], "prior score changed before read: " + key)
        values = pd.read_parquet(path)
        _require(file_hash(path) == bound[0]["sha256"], "prior score changed during read: " + key)
        _require(values.index.equals(panel.dates) and values.columns.equals(panel.eligible.columns), "prior score axes changed")
        previous[key], directions[key] = values, fit_by_key[key]["direction"]
    return previous, directions, unavailable


def _comparison(expected, available, previous_keys):
    missing = [key for key in expected if key not in available]
    return {"expected_control_keys": list(expected), "available_control_keys": list(available), "missing_control_keys": missing,
        "expected_previous_factor_count": len(previous_keys),
        "available_previous_factor_count": sum(key in available for key in previous_keys),
        "missing_previous_factor_keys": [key for key in previous_keys if key not in available],
        "comparison_complete": not missing,
        "incremental_status": "not_evaluable" if not available else "partial_controls" if missing else "complete_controls"}


def _fit_diagnostics(evaluation, engine, controls, directions, direction, fit_dates, primary_horizon, comparison):
    # Reuse only the stable primary/quantile/coverage aggregation. Compute the
    # control diagnostics here so paired candidate and residual IC use exactly
    # the same stock/date/label observations and the residual is fitted once.
    result = _fit_view(evaluation, engine, {}, {}, direction, fit_dates, PROTOCOL, primary_horizon)
    scores = evaluation.scores.loc[fit_dates]
    previous = {key: value.loc[fit_dates] for key, value in controls.items()}
    minimum = PROTOCOL["factor_screen"]["minimum_cross_section"]
    correlations = []
    for key, values in previous.items():
        ic, counts = _rank_ic(scores, values, minimum)
        correlations.append({"factor_key": key, "mean_rank_correlation_raw": _mean(ic),
            "mean_rank_correlation_oriented": _mean(ic * direction * directions[key]),
            "days": int(ic.count()), "paired_observations": int(counts.sum())})
    incremental = []
    if previous:
        residual = engine._residual_scores(scores, previous)
        for horizon in PROTOCOL["factor_screen"]["horizons"]:
            labels = engine.labels(horizon).loc[fit_dates]
            residual_ic, common_n = _rank_ic(residual, labels, minimum)
            candidate_ic, candidate_n = _rank_ic(scores.where(residual.notna()), labels, minimum)
            _require(common_n.equals(candidate_n), "candidate/residual incremental samples differ")
            _, candidate_all_n = _rank_ic(scores, labels, minimum)
            denominator = int(candidate_all_n.sum())
            incremental.append({"horizon": horizon, "candidate_mean_rank_ic_common": _mean(candidate_ic),
                "candidate_mean_rank_ic_common_oriented": _mean(candidate_ic * direction),
                "residual_mean_rank_ic_oriented": _mean(residual_ic * direction),
                "candidate_ic_days_common": int(candidate_ic.count()), "residual_ic_days": int(residual_ic.count()),
                "paired_observations": int(common_n.sum()), "candidate_all_paired_observations": denominator,
                "common_sample_coverage": float(common_n.sum() / denominator) if denominator else None,
                "same_stock_date_label_pairs": True, "incremental_status": comparison["incremental_status"],
                "method": "fit_only_daily_rank_ols_residual_descriptive_not_independent_evidence"})
    result.update(correlations=correlations, incremental=incremental, **comparison)
    return result


def _records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _risk_diagnostics(output, spec, scores, panel, fit_dates, controls, comparison, predeclaration):
    if predeclaration["status"] != "declared":
        raise ValueError(predeclaration.get("error", "risk diagnostic semantics are unsupported"))
    _require("open_observed" in panel.fields, "predeclared observed-price risk sensitivity lacks its source field")
    risk_controls = {**controls, LOG_AMOUNT_CONTROL: np.log(panel.fields["amount"].where(panel.fields["amount"] > 0))}
    original = spec.metadata
    settings = dict(direction=original["original_direction"], horizon=original["primary_horizon"],
        minimum=PROTOCOL["factor_screen"]["minimum_cross_section"], quantiles=PROTOCOL["factor_screen"]["quantiles"])
    expected_semantics = risk_information_semantics(**settings, control_names=risk_controls, observed_price_mask_supplied=True)
    declared_semantics = predeclaration["semantics"]
    _require({k: v for k, v in expected_semantics.items() if k != "controls"} ==
             {k: v for k, v in declared_semantics.items() if k != "controls"}
             and set(risk_controls) <= set(declared_semantics["controls"]), "risk semantics differ from the before-load declaration")
    fit_view = None
    for name, dates in (("archive", panel.dates), ("fit", fit_dates)):
        result = evaluate_risk_information(scores, panel.fields["open"], panel.eligible, dates,
            **settings, controls=risk_controls, observed_price_mask=panel.fields["open_observed"].eq(1))
        _require(result["summary"]["semantics"] == expected_semantics
                 and result["summary"].get("risk_labels_registered_as_signal_fields") is False, "risk operator changed semantics or exposed labels as signals")
        destination = output / ("risk_" + name)
        destination.mkdir()
        for table_name in RISK_TABLES:
            frame = result[table_name]
            if "date" in frame:
                _require(pd.to_datetime(frame["date"]).isin(dates).all(), "risk table escaped its authorized signal dates")
            if "year" in frame:
                _require(set(frame["year"]) <= set(dates.year), "risk annual table escaped its authorized signal years")
            _frame(destination / (table_name + ".parquet"), frame)
        for label in result["labels"].values():
            _require(label.index.equals(dates) and label.columns.equals(panel.eligible.columns), "diagnostic labels changed axes")
        save_once(destination / "summary.json", {**result["summary"], "comparison": comparison,
            "signal_scope": [str(dates[0].date()), str(dates[-1].date())], "alpha_operand_registration": False})
        save_once(destination / "label_lineage.json", {"computed_transiently_not_registered_as_signal_fields": True,
            "labels": {key: {"shape": list(label.shape), "sha256": hashlib.sha256(pd.util.hash_pandas_object(label, index=True).values.tobytes()).hexdigest()}
                       for key, label in result["labels"].items()}, "semantics": expected_semantics})
        if name == "fit":
            quantiles = result["quantile_risk"].groupby(["metric", "quantile"], sort=True).agg(
                mean_risk=("risk_mean", "mean"), mean_common_risk=("common_risk_mean", "mean"),
                mean_coverage=("coverage", "mean"), labeled_stock_days=("labeled_members", "sum")).reset_index()
            common = result["common_sample"].groupby("metric", sort=True).agg(
                paired_stock_days=("paired_n", "sum"), common_stock_days=("common_n", "sum"),
                mean_common_oriented_ic=("common_oriented_rank_ic", "mean"),
                mean_partial_oriented_ic=("partial_oriented_rank_ic", "mean"),
                rank_deficient_dates=("control_rank_deficient", "sum")).reset_index()
            fit_view = {"status": "evaluated", "summary": result["summary"], "annual": _records(result["annual"]),
                "bootstrap": _records(result["bootstrap"]), "quantiles": _records(quantiles), "common_sample": _records(common),
                "comparison": comparison, "diagnostic_control_keys": list(risk_controls),
                "purged_signal_sessions": PURGE, "return_ic_is_not_risk_admission_gate": True,
                "full_range_aggregates_reused": False, "risk_labels_registered_as_signal_fields": False}
    return fit_view


def _auxiliary(root, folder, panel, registry, protocol, base_fingerprint):
    admission = {"registration_id": registry["registration_id"], "base_panel_fingerprint": base_fingerprint,
        "authorized_start": START, "authorized_end": END, "fields": list(HF0280_FIELDS),
        "purpose": "Evaluate the original fixed HF0280 factor after its separately frozen source selection",
        "authorization_reference": "user active V6 factor-calendar goal; expansion protocol SHA256 " + file_hash(folder / "protocol.json")}
    save_once(folder / "auxiliary_admission.json", admission)
    started = time.perf_counter()
    try:
        auxiliary = load_auxiliary_panel(panel, registry, admission=admission,
                                        cache_dir=root / "preparation/auxiliary_panel_cache")
        _require(auxiliary.eligible.equals(panel.eligible), "auxiliary attachment changed eligibility")
        for name in set(panel.fields) - set(HF0280_FIELDS):
            _require(auxiliary.fields[name].equals(panel.fields[name]), "auxiliary attachment changed main field: " + name)
        save_once(folder / "auxiliary_load_receipt.json", {"status": "loaded", "metrics": auxiliary.load_metrics,
            "provenance": auxiliary.provenance.get("auxiliary"), "scope": [START, END],
            "shape": list(auxiliary.eligible.shape), "eligibility_unchanged": True, "source_units_unchanged": True,
            "historical_available_at_verified": False, "execution_certified": False})
        result = compute_hf0280(auxiliary.fields, eligible=auxiliary.eligible, calendar=panel.dates)
        output = folder / "hf0280_operator"
        output.mkdir()
        for name in ("scores", "daily_residual", "diagnostics"):
            _frame(output / (name + ".parquet"), getattr(result, name))
        for name, regression in result.regressions.items():
            for kind in ("residuals", "coefficients", "diagnostics"):
                _frame(output / (name + "_" + kind + ".parquet"), getattr(regression, kind))
        save_once(output / "semantics.json", result.semantics)
        _require(result.scores.index.equals(panel.dates) and result.scores.columns.equals(panel.eligible.columns), "HF0280 operator changed axes")
        if not result.scores.notna().any().any():
            raise ValueError("HF0280 has no complete twenty-session score; operator diagnostics retained")
        save_once(folder / "hf0280_receipt.json", {"status": "computed", "seconds": time.perf_counter()-started,
            "nonmissing_stock_days": int(result.scores.notna().to_numpy().sum()), "scope": [START, END],
            "field": HF0280_FIELD, "direction": 1, "artifacts": [_proof(p) for p in sorted(output.iterdir())]})
        return result.scores, None
    except ExpansionIntegrityError:
        raise
    except Exception as exc:
        failure = {"status": "failed", "type": type(exc).__name__, "error": str(exc),
            "seconds": time.perf_counter()-started, "dependent_factor_keys": ["HF0280"],
            "independent_ohlc_factors_may_continue": True, "missing_stocks_or_dates_removed": False}
        save_once(folder / "hf0280_failure.json", failure)
        return pd.DataFrame(np.nan, index=panel.dates, columns=panel.eligible.columns), failure


def evaluate_expansion_factors(root, *, library_path=None):
    """Execute one frozen expansion factor stage, retaining local failures.

    Only this cycle's fit_factor_view.json is the next model's numeric factor
    evidence. Full-range outputs are archive evidence, never account results.
    The original nine score artifacts are read, not recomputed or rewritten.
    """
    root = Path(root).resolve()
    library_path = Path(library_path or ROOT / "output/factor_library_v6.sqlite").resolve()
    folder, protocol, receipt, registry, prior_index, prior_fit, intent = _prepare(root, library_path)
    intent_path, index_path = folder / "factor_execution_intent.json", folder / "factor_index.json"
    if index_path.exists():
        _require(read(intent_path) == intent, "completed expansion belongs to different frozen inputs")
        saved = read(index_path)
        _require(saved["intent_sha256"] == file_hash(intent_path), "saved expansion intent binding changed")
        _verify(saved["artifacts"])
        return saved
    _require(not intent_path.exists(), "unfinished expansion retained; explicit recovery required, never automatic rerun")
    _claim(intent_path, intent)
    started, results, fit_results = time.perf_counter(), [], []
    try:
        _cancelled(root, folder)
        _verify(intent["source_proofs"])
        panel = load_panel(root)
        base_fingerprint = panel.fingerprint()
        _require(panel.dates.min() >= pd.Timestamp(START) and panel.dates.max() <= pd.Timestamp(END)
                 and base_fingerprint == prior_index["scope"]["panel_fingerprint"], "loaded expansion panel differs from original fixed scope")
        previous, previous_directions, unavailable = _existing(root, panel, prior_index, prior_fit)
        save_once(folder / "prior_score_reuse.json", {"recomputed": 0, "loaded": list(previous), "unavailable": unavailable,
            "prior_index_sha256": file_hash(root / "factor_index.json"), "all_original_nine_retained": True})
        fit_candidates = panel.dates[(panel.dates >= FIT_START) & (panel.dates <= FIT_END)]
        _require(len(fit_candidates) > PURGE and PURGE >= max(PROTOCOL["factor_screen"]["horizons"]) + 1, "insufficient fixed training purge")
        fit_dates = fit_candidates[:-PURGE]
        scope = {"stage_id": intent["stage_id"], "cycle_id": CYCLE, "panel_fingerprint": base_fingerprint,
            "date_range": [START, END], "role": "previously_exposed_development", "independent_holdout": False}
        fit_scope = {**scope, "date_range": [FIT_START, FIT_END], "purpose": "next_model_fit_only",
            "last_allowed_signal_date": str(fit_dates[-1].date()), "purged_signal_sessions": PURGE,
            "last_label_exit_no_later_than": FIT_END}
        with FactorLibrary(library_path) as library:
            library.load_snapshot(prior_index["library_snapshot_id"])
            _require(library.verify_integrity(), "factor library integrity failure")
            snapshot = library.create_snapshot(scope={**scope, "purpose": "pre_expansion_all_trial_and_exposure_history"})
            save_once(folder / "input_library_snapshot.json", library.load_snapshot(snapshot))
            records, aliases, errors = [], {}, {}
            for entry in prior_fit["factors"]:
                if entry.get("factor_id"):
                    aliases.setdefault(entry["factor_id"], entry["factor_key"])
            for declaration in intent["declarations"]:
                original, key = declaration["original"], declaration["factor_key"]
                spec = None
                try:
                    spec = FactorSpec(original["name"], original["expression"], metadata={
                        "cycle_id": CYCLE, "factor_key": key, "origin": declaration["origin"],
                        "original_direction": original["direction"], "primary_horizon": original["primary_horizon"],
                        "role": original["role"], "hypothesis": original["hypothesis"]})
                except Exception as exc:
                    errors[key] = {"type": type(exc).__name__, "error": str(exc)}
                factor_id = spec.factor_id if spec else hashlib.sha256(original["expression"].encode()).hexdigest()
                output = folder / "factors" / (key + "_" + factor_id[:16])
                output.mkdir(parents=True)
                common = dict(scope=scope, seed={"origin": declaration["origin"], "key": key},
                    model=receipt["runtime_identity"] if key != "HF0280" else None,
                    run_id=intent["stage_id"] + ":" + key, library_snapshot_id=snapshot,
                    source={"declaration_sha256": file_hash(folder / "factor_declaration.json"),
                            "original": original, "source_proofs": intent["source_proofs"]},
                    exposure={"data": protocol["all_data_exposure"], "next_model_scope": fit_scope},
                    cost={"origin_model_call_ref": intent["source_proofs"][0]["sha256"] if key != "HF0280" else None,
                          "no_new_model_call": True, "allocated_model_money_usd": None if key != "HF0280" else 0.})
                planned = library.record_attempt(spec, expression=original["expression"],
                    status="planned" if spec else "invalid", error=errors.get(key, {}).get("error"), **common)
                duplicate = aliases.get(factor_id)
                if spec and duplicate is None:
                    aliases[factor_id] = key
                save_once(output / "spec.json", {**declaration, "spec": spec.to_dict() if spec else None,
                    "planned_attempt_id": planned, "duplicate_of": duplicate})
                records.append({"key": key, "original": original, "spec": spec, "factor_id": factor_id,
                    "folder": output, "common": common, "planned": planned, "duplicate": duplicate})
            _cancelled(root, folder)
            hf_scores, hf_failure = _auxiliary(root, folder, panel, registry, protocol, base_fingerprint)
            if hf_failure:
                errors["HF0280"] = hf_failure
            _require(HF0280_FIELD not in panel.fields, "derived HF0280 field would overwrite an existing field")
            provenance = deepcopy(panel.provenance)
            provenance["derived_fields"] = {HF0280_FIELD: {"read_only": True, "formula": "three daily raw OLS then complete trailing20",
                "operator_sha256": protocol["hf0280_operator_sha256"], "registration_id": registry["registration_id"],
                "admission_sha256": file_hash(folder / "auxiliary_admission.json"), "available": hf_failure is None,
                "historical_publication_verified": False}}
            provenance["causal_fields"] = [*provenance.get("causal_fields", []), HF0280_FIELD]
            extended = MarketPanel({**panel.fields, HF0280_FIELD: hf_scores}, panel.eligible.copy(deep=True), provenance)
            engine = FactorEngine(extended)
            scope["data_fingerprint"] = engine.data_fingerprint
            fit_scope["data_fingerprint"] = engine.data_fingerprint
            save_once(folder / "factor_panel_identity.json", {"scope": scope, "fit_scope": fit_scope,
                "shape": list(panel.eligible.shape), "original_prices_eligibility_unchanged": True,
                "derived_field": HF0280_FIELD, "auxiliary_available": hf_failure is None,
                "load_metrics": panel.load_metrics})
            new_scores, new_directions = {}, {}
            for record in records:
                key, spec = record["key"], record["spec"]
                if key in errors or record["duplicate"] is not None:
                    continue
                _cancelled(root, folder)
                try:
                    _parse(spec.expression, {HF0280_FIELD} if key == "HF0280" else set(BASE_FIELDS))
                    values = engine.compute(spec)
                    _require(values.index.equals(panel.dates) and values.columns.equals(panel.eligible.columns), "factor computation changed axes")
                    _frame(record["folder"] / "scores.parquet", values)
                    new_scores[key], new_directions[key] = values, record["original"]["direction"]
                except ExpansionIntegrityError:
                    raise
                except Exception as exc:
                    errors[key] = {"type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}
            for record in records:
                _cancelled(root, folder)
                _verify(intent["source_proofs"])
                key, spec, output, original = record["key"], record["spec"], record["folder"], record["original"]
                step = time.perf_counter()
                metadata = {"transition_of": record["planned"], "duplicate_of_declared_key": record["duplicate"]}
                common = record["common"]
                try:
                    if key in errors:
                        raise ValueError(errors[key]["error"])
                    if record["duplicate"] is not None:
                        terminal = library.record_attempt(spec, status="duplicate", metadata=metadata, **common)
                        fit = {"factor_key": key, "name": original["name"], "factor_id": record["factor_id"],
                            "expression": original["expression"], "direction": original["direction"], "status": "duplicate",
                            "duplicate_of": record["duplicate"], "new_discovery": False, "scope": fit_scope}
                        result = {**fit, "planned_attempt_id": record["planned"], "attempt_id": terminal, "scores_path": None}
                    else:
                        controls = {**previous, **{k: v for k, v in new_scores.items() if k != key}}
                        directions = {**previous_directions, **{k: v for k, v in new_directions.items() if k != key}}
                        prior_keys = [entry["factor_key"] for entry in prior_index["factors"]]
                        expected_controls = [*prior_keys, *[row["key"] for row in records
                            if row["key"] != key and row["duplicate"] is None]]
                        comparison = _comparison(expected_controls, controls, prior_keys)
                        evaluation = engine.evaluate(spec, horizons=tuple(PROTOCOL["factor_screen"]["horizons"]),
                            quantiles=PROTOCOL["factor_screen"]["quantiles"], existing_factors=controls,
                            min_cross_section=PROTOCOL["factor_screen"]["minimum_cross_section"])
                        pd.testing.assert_frame_equal(evaluation.scores, new_scores[key], check_exact=True)
                        fit = {"factor_key": key, "name": original["name"], "factor_id": spec.factor_id,
                            "expression": spec.expression, "direction": original["direction"], "role": original["role"],
                            "origin": "original_seed" if key == "HF0280" else "admitted_model_hypothesis",
                            "status": "evaluated", "hypothesis": original["hypothesis"], "scope": fit_scope,
                            "comparison_factor_keys": list(controls), "unavailable_prior_scores": unavailable,
                            **_fit_diagnostics(evaluation, engine, controls, directions, original["direction"], fit_dates,
                                               original["primary_horizon"], comparison)}
                        for name in TABLES:
                            _frame(output / (name + ".parquet"), getattr(evaluation, name))
                        save_once(output / "summary.json", {**evaluation.summary, "locked_to_next_model": True,
                            "original_direction": original["direction"], "role": original["role"], "comparison": comparison})
                        if original["role"] == "risk_information":
                            fit["risk_information"] = _risk_diagnostics(output, spec, evaluation.scores, panel,
                                fit_dates, controls, comparison, intent["risk_information_predeclaration"][key])
                            fit["role_admission_basis"] = "purged future-risk diagnostics; return IC alone cannot admit or reject this role"
                        else:
                            fit["role_admission_basis"] = "purged return-information diagnostics; portfolio application remains undeclared"
                        terminal = library.record_attempt(spec, status="evaluated", metadata=metadata, **common)
                        result = {"factor_key": key, "factor_id": spec.factor_id, "name": spec.name,
                            "direction": original["direction"], "role": original["role"], "status": "evaluated",
                            "primary_horizon": original["primary_horizon"], "scores_path": str(output / "scores.parquet"),
                            "data_fingerprint": engine.data_fingerprint, "planned_attempt_id": record["planned"], "attempt_id": terminal,
                            "comparison_complete": comparison["comparison_complete"], "incremental_status": comparison["incremental_status"]}
                except ExpansionIntegrityError:
                    raise
                except Exception as exc:
                    failure = {"type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(),
                        "local_dependency_failure": errors.get(key), "independent_siblings_continue": True}
                    save_once(output / "failure.json", failure)
                    terminal = library.record_attempt(spec, expression=original["expression"], status="failed",
                        error=str(exc), metadata=metadata, **common)
                    fit = {"factor_key": key, "factor_id": record["factor_id"], "name": original["name"],
                        "expression": original["expression"], "direction": original["direction"], "role": original["role"],
                        "status": "failed", "failure_type": type(exc).__name__, "failure": str(exc), "scope": fit_scope}
                    result = {**fit, "scores_path": str(output / "scores.parquet") if (output / "scores.parquet").exists() else None,
                              "planned_attempt_id": record["planned"], "attempt_id": terminal}
                save_once(output / "fit_summary.json", fit)
                proofs = [_proof(p) for p in sorted(output.rglob("*")) if p.is_file()]
                evidence = library.record_evidence(terminal, scope=scope,
                    status="observed" if result["status"] == "evaluated" else "failed" if result["status"] == "failed" else "inconclusive",
                    artifacts=proofs, cost={"compute_wall_seconds": time.perf_counter()-step, "model_calls": 0},
                    metadata={"full_results_archived_not_next_model_input": True, "comparison_scope": "old cached factors and declared siblings"})
                library.record_exposure(attempt_id=terminal, scope=scope, purpose="archived_expansion_development_diagnostics",
                    results_revealed=True, used_for_selection=False, metadata={"next_model_numeric_scope": [FIT_START, FIT_END], "purge": PURGE})
                result.update(artifacts=proofs, evidence_id=evidence, compute_wall_seconds=time.perf_counter()-step)
                save_once(output / "receipt.json", result)
                results.append(result)
                fit_results.append(fit)
                print(_json({"factor_key": key, "status": result["status"], "completed": len(results)}), flush=True)
            _require(panel.fingerprint() == base_fingerprint, "factor work mutated the base panel")
            _verify(intent["source_proofs"])
            library.record_exposure(library_snapshot_id=snapshot, scope=fit_scope,
                purpose="prepared_expansion_fit_view_not_yet_delivered", results_revealed=False, used_for_selection=False,
                metadata={"all_previous_trials_retained": True, "new_factor_keys": [r["factor_key"] for r in results]})
            final_snapshot = library.create_snapshot(scope={**fit_scope, "purpose": "completed_expansion_all_trial_and_exposure_history"})
            save_once(folder / "output_library_snapshot.json", library.load_snapshot(final_snapshot))
            comparisons_complete = bool([r for r in results if r["status"] == "evaluated"]) and all(
                r["comparison_complete"] for r in results if r["status"] == "evaluated")
            comparison_coverage = {"comparison_complete": comparisons_complete,
                "expected_previous_factor_count": 9, "available_previous_factor_count": len(previous),
                "unavailable_prior_scores": unavailable,
                "interpretation": "Complete means all declared control score artifacts available, not every stock/day observed; paired coverage remains explicit per statistic."}
            view = {"version": VERSION, "cycle_id": CYCLE, "stage_id": intent["stage_id"], "scope": fit_scope,
                **comparison_coverage,
                "fit_signal_sessions": len(fit_dates), "library_snapshot_id": final_snapshot,
                "library_snapshot_hash": library.load_snapshot(final_snapshot)["snapshot_hash"],
                "factor_declaration_sha256": file_hash(folder / "factor_declaration.json"),
                "factors": [*deepcopy(prior_fit["factors"]), *fit_results], "prior_factor_keys": [r["factor_key"] for r in prior_fit["factors"]],
                "new_factor_keys": [r["factor_key"] for r in results], "source_proofs": intent["source_proofs"],
                "2021_2024_result_values_included": False, "next_model_may_read_full_range_artifacts": False,
                "model_calls_by_factor_stage": 0, "not_independent_holdout": True, "financial_success": False,
                "risk_information_predeclaration": intent["risk_information_predeclaration"],
                "hf0280_source_limitations": HF_SOURCE_LIMITATIONS,
                "limitations": ["all years previously exposed development", "source publication vintage not certified",
                    "descriptive factor tests, not account returns", "prior factor evidence copied unchanged; new sibling diagnostics are same-sample"]}
            save_once(folder / "fit_factor_view.json", view)
            artifacts = [_proof(p) for p in sorted(folder.rglob("*")) if p.is_file()
                         and not p.is_relative_to(folder / "jobs") and p != index_path]
            index = {"version": VERSION, "cycle_id": CYCLE, "stage_id": intent["stage_id"], "status": "completed",
                **comparison_coverage,
                "intent_sha256": file_hash(intent_path), "scope": scope, "shape": list(panel.eligible.shape),
                "factors": results, "prior_factors": prior_index["factors"], "prior_scores_recomputed": 0,
                "factor_count": len(results), "evaluated": sum(r["status"] == "evaluated" for r in results),
                "failed": sum(r["status"] == "failed" for r in results), "duplicate": sum(r["status"] == "duplicate" for r in results),
                "factor_declaration_sha256": file_hash(folder / "factor_declaration.json"),
                "fit_factor_view_sha256": file_hash(folder / "fit_factor_view.json"), "library_path": str(library_path),
                "input_library_snapshot_id": snapshot, "library_snapshot_id": final_snapshot,
                "artifacts": artifacts, "source_proofs": intent["source_proofs"], "cache": engine.cache_info,
                "duration_seconds": time.perf_counter()-started, "model_calls": 0, "account_executions": 0, "financial_success": False}
            save_once(index_path, index)
            return index
    except Exception as exc:
        save_once(folder / "factor_stage_failure.json", {"type": type(exc).__name__, "error": str(exc),
            "traceback": traceback.format_exc(), "completed_factor_keys": [r["factor_key"] for r in results],
            "intent_sha256": file_hash(intent_path), "original_outputs_retained": True,
            "automatic_retry": False, "financial_success": False})
        raise
