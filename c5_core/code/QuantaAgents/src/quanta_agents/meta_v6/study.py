"""Admitted, auditable factor evaluation with a purged model-facing fit view.

No model is called here. Full development diagnostics are archived separately;
only the explicitly constructed fit view is eligible for the next model prompt.
This is an information-flow contract, not an OS permission boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import traceback
from uuid import uuid4

import numpy as np
import pandas as pd

from .data import load_market_panel
from .factors import FactorEngine, FactorSpec, _rank_ic
from .library import FactorLibrary
from .research import DEFAULT_RUN, PROTOCOL, ROOT, SEEDS, initialize_run, save_once

VERSION = "v6_factor_stage_1"
TABLES = ("daily_ic", "annual", "quantile_returns", "turnover", "coverage", "correlations", "incremental")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _hash(path), "bytes": path.stat().st_size}


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _admitted(root):
    folder = root / "model_calls/01_hypotheses"
    path = folder / "admitted_receipt.json"
    if not path.is_file():
        raise ValueError("admitted hypothesis receipt is required before consuming model factors")
    receipt = _load(path)
    identity = receipt.get("runtime_identity", {})
    if (identity.get("verified") is not True or identity.get("model") != "gpt-6-astra"
            or identity.get("effort") != "xhigh" or receipt.get("model") != "gpt-6-astra"
            or receipt.get("effort") != "xhigh"):
        raise ValueError("hypothesis local runtime identity is not admitted as gpt-6-astra/xhigh")
    artifacts = receipt.get("artifact_sha256", {})
    if not {"response.json", "runtime_session.jsonl", "prompt.txt"} <= set(artifacts):
        raise ValueError("admission is missing response/runtime/prompt artifact bindings")
    proofs = [_proof(path)]
    for name, expected in sorted(artifacts.items()):
        item = (folder / name).resolve()
        if item.parent != folder.resolve() or not item.is_file() or _hash(item) != expected:
            raise ValueError("admitted artifact binding mismatch: " + str(name))
        proofs.append(_proof(item))
    if _json(_load(folder / "response.json")) != _json(receipt.get("response")):
        raise ValueError("admitted response and original JSON differ")
    response = receipt["response"]
    factors = response.get("factors") if isinstance(response, dict) else None
    if not isinstance(factors, list) or len(factors) > min(8, PROTOCOL["initial_stage_search"]["new_hypotheses_max"]):
        raise ValueError("hypothesis response exceeds the declared new-factor budget")
    for item in list(SEEDS) + factors:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or not item["name"] or not isinstance(item.get("expression"), str)
                or type(item.get("direction")) is not int or item["direction"] not in {-1, 1}):
            raise ValueError("factor declaration requires its original name, expression and direction")
    return receipt, proofs


def _parquet_once(path, frame):
    path = Path(path)
    if path.exists():
        # An unfinished stage can resume only from an identical checkpoint.
        pd.testing.assert_frame_equal(pd.read_parquet(path), frame)
        return
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        frame.to_parquet(temp, index=True)
        if path.exists():
            raise ValueError("artifact appeared concurrently: " + str(path))
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _mean(series):
    return _number(series.mean())


def _primary_horizon(item, origin):
    declared = re.search(r"主期限\s*(\d+)\s*日", item.get("hypothesis", ""))
    horizon = int(declared.group(1)) if declared else 5
    if horizon not in PROTOCOL["factor_screen"]["horizons"]:
        raise ValueError("declared primary horizon is outside the frozen evaluated horizons")
    source = ("admitted_response.hypothesis" if origin == "admitted_model_hypothesis"
              else "original_seed.hypothesis") if declared else "runner_descriptive_default_not_an_original_horizon_claim"
    return horizon, source


def _fit_view(evaluation, engine, previous, previous_directions, direction, fit_dates, protocol, primary_horizon):
    """Reaggregate only masked daily observations, never use full-range summaries."""
    ic = evaluation.daily_ic.loc[evaluation.daily_ic.date.isin(fit_dates)].copy()
    qr = evaluation.quantile_returns.loc[evaluation.quantile_returns.date.isin(fit_dates)].copy()
    coverage = evaluation.coverage.loc[evaluation.coverage.date.isin(fit_dates)].copy()
    turnover = evaluation.turnover.loc[evaluation.turnover.date.isin(fit_dates)].copy()
    horizons = protocol["factor_screen"]["horizons"]
    quantiles = protocol["factor_screen"]["quantiles"]
    minimum = protocol["factor_screen"]["minimum_cross_section"]
    records = []
    for h in horizons:
        for year in [None, *range(pd.Timestamp(protocol["development_partition"]["fit"][0]).year,
                                  pd.Timestamp(protocol["development_partition"]["fit"][1]).year + 1)]:
            selected_ic = ic.loc[ic.horizon.eq(h)]
            selected_qr = qr.loc[qr.horizon.eq(h)]
            selected_cov = coverage.loc[coverage.horizon.eq(h)]
            if year is not None:
                selected_ic = selected_ic.loc[selected_ic.date.dt.year.eq(year)]
                selected_qr = selected_qr.loc[selected_qr.date.dt.year.eq(year)]
                selected_cov = selected_cov.loc[selected_cov.date.dt.year.eq(year)]
            means = selected_qr.pivot(index="date", columns="quantile", values="mean_forward_return")
            spread = means.get(quantiles, pd.Series(dtype=float)) - means.get(1, pd.Series(dtype=float))
            raw = selected_ic.rank_ic
            denominator = int(selected_cov.eligible_count.sum())
            records.append({"horizon": h, "year": year, "signal_sessions": len(selected_cov),
                "ic_days": int(raw.count()), "mean_rank_ic_raw": _mean(raw),
                "mean_rank_ic_oriented": _mean(raw * direction),
                "oriented_positive_ic_days": int((raw * direction > 0).sum()),
                "oriented_negative_ic_days": int((raw * direction < 0).sum()),
                "mean_top_minus_bottom_oriented": _mean(spread * direction), "spread_days": int(spread.count()),
                "eligible_observations": denominator, "paired_observations": int(selected_cov.paired_count.sum()),
                "factor_coverage": float(selected_cov.factor_count.sum() / denominator) if denominator else None,
                "evaluation_coverage": float(selected_cov.paired_count.sum() / denominator) if denominator else None})
    scores = evaluation.scores.loc[fit_dates]
    earlier = {name: frame.loc[fit_dates] for name, frame in previous.items()}
    correlations = []
    for name, frame in earlier.items():
        corr, n = _rank_ic(scores, frame, minimum)
        correlations.append({"factor_key": name, "mean_rank_correlation_raw": _mean(corr),
            "mean_rank_correlation_oriented": _mean(corr * direction * previous_directions[name]),
            "days": int(corr.count()), "paired_observations": int(n.sum())})
    incremental = []
    if earlier:
        residual = engine._residual_scores(scores, earlier)
        for h in horizons:
            value, count = _rank_ic(residual, engine.labels(h).loc[fit_dates], minimum)
            incremental.append({"horizon": h, "residual_mean_rank_ic_oriented": _mean(value * direction),
                "ic_days": int(value.count()), "paired_observations": int(count.sum()),
                "method": "fit_only_daily_rank_ols_residual_descriptive_not_independent_evidence"})
    return {"primary_horizon_predeclared": primary_horizon,
        "primary": next(row for row in records if row["horizon"] == primary_horizon and row["year"] is None),
        "by_horizon_and_year": records, "correlations": correlations, "incremental": incremental,
        "quantile_turnover": [{"quantile": int(q), "mean_new_member_fraction": _mean(frame.turnover),
                               "observed_days": int(frame.turnover.count())} for q, frame in turnover.groupby("quantile")],
        "execution_certified": False, "independent_market_validation": False}


def _validate_result_files(result):
    for proof in result.get("artifacts", []):
        path = Path(proof["path"])
        if not path.is_file() or _hash(path) != proof["sha256"]:
            raise ValueError("factor checkpoint artifact changed: " + str(path))


def evaluate_factor_stage(root=DEFAULT_RUN, *, library_path=None):
    """Evaluate all fixed declarations once; failed factors do not abort siblings.

    The default loader exactly matches the prepared 2015-2024 historical CSI300
    union cache. This stage records no financial success and invokes no model.
    """
    root = Path(root).resolve()
    receipt, admission_proofs = _admitted(root)  # Gate before any factor consumption/load.
    initialize_run(root)
    library_path = Path(library_path or ROOT / "output/factor_library_v6.sqlite").resolve()
    source_proofs = admission_proofs + [_proof(root / "protocol.json"), _proof(root / "seed_factors.json")]
    source_proofs += [_proof(Path(__file__).with_name(name)) for name in ("study.py", "factors.py", "library.py", "data.py", "research.py")]
    for name in ("data_selection.json", "data_load_receipt.json", "source_inventory.json"):
        path = root / "preparation" / name
        if path.is_file():
            source_proofs.append(_proof(path))
    declarations = []
    for origin, items in (("original_seed", SEEDS), ("admitted_model_hypothesis", receipt["response"]["factors"])):
        for i, item in enumerate(items):
            primary, primary_source = _primary_horizon(item, origin)
            declarations.append({"declaration_index": len(declarations), "factor_key": item["name"].split("_")[0]
                if origin == "original_seed" else f"F{i+1}", "origin": origin,
                "origin_index": i, "original": item, "primary_horizon": primary,
                "primary_horizon_source": primary_source})
    identity = {"version": VERSION, "protocol": PROTOCOL, "declarations": declarations,
                "source_proofs": source_proofs, "library_path": str(library_path)}
    stage_id = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    index_path = root / "factor_index.json"
    if index_path.exists():
        completed = _load(index_path)
        if completed["stage_id"] != stage_id:
            raise ValueError("completed factor stage has different inputs; register a new stage instead")
        for result in completed["factors"]:
            _validate_result_files(result)
        if _hash(root / "fit_factor_view.json") != completed["fit_factor_view_sha256"]:
            raise ValueError("frozen fit view changed")
        return completed
    lock = root / "factor_stage.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(_json({"pid": os.getpid(), "stage_id": stage_id, "created_at": datetime.now(timezone.utc).isoformat()}))
    started = time.perf_counter()
    try:
        with FactorLibrary(library_path) as library:
            declaration_path = root / "factor_declaration.json"
            if declaration_path.exists():
                declaration = _load(declaration_path)
                if declaration["stage_id"] != stage_id:
                    raise ValueError("frozen factor declaration differs from current source identity")
                library.load_snapshot(declaration["input_library_snapshot_id"])
            else:
                snapshot = library.create_snapshot(scope={"stage_id": stage_id, "purpose": "pre_evaluation_knowledge"})
                declaration = {**identity, "stage_id": stage_id, "input_library_snapshot_id": snapshot,
                    "created_at": datetime.now(timezone.utc).isoformat(), "directions_preserved": True,
                    "primary_horizons": {e["factor_key"]: {"horizon": e["primary_horizon"],
                        "source": e["primary_horizon_source"]} for e in declarations}, "model_calls_by_factor_stage": 0,
                    "model_origin_usage": receipt.get("usage"),
                    "model_origin_cost": {"money_usd": None, "known": False,
                        "shared_call_ref": admission_proofs[0]["sha256"], "do_not_sum_once_per_factor": True},
                    "diagnostic_policy": "2021-2024 full tables archived; excluded from next model view",
                    "runtime_identity_scope": receipt["runtime_identity"]}
                save_once(declaration_path, declaration)
            data = PROTOCOL["data"]
            panel = load_market_panel(data["root"], start=data["start"], end=data["end"],
                authorized_start=data["start"], authorized_end=data["end"], calendar_path=data["calendar"],
                membership_path=data["membership"], cache_dir=root / "preparation/market_panel_cache")
            selection_path = root / "preparation/data_selection.json"
            if selection_path.is_file():
                selection = _load(selection_path)
                if panel.symbols != selection["symbols"]:
                    raise ValueError("loaded panel differs from the frozen historical universe")
            engine = FactorEngine(panel)
            scope = {"data_fingerprint": engine.data_fingerprint, "panel_fingerprint": panel.fingerprint(),
                "date_range": [data["start"], data["end"]], "role": "previously_exposed_development",
                "stage_id": stage_id}
            fit_start, fit_end = map(pd.Timestamp, PROTOCOL["development_partition"]["fit"])
            fit_candidates = panel.dates[(panel.dates >= fit_start) & (panel.dates <= fit_end)]
            purge = PROTOCOL["validation"]["label_horizon_purge_at_training_end"]
            if len(fit_candidates) <= purge or purge < max(PROTOCOL["factor_screen"]["horizons"]) + 1:
                raise ValueError("fit scope cannot provide the declared horizon purge")
            fit_dates = fit_candidates[:-purge]
            fit_scope = {**scope, "date_range": [str(fit_start.date()), str(fit_end.date())],
                "last_allowed_signal_date": str(fit_dates[-1].date()), "purged_signal_sessions": purge,
                "last_label_exit_no_later_than": str(fit_end.date()), "purpose": "next_model_fit_only"}
            save_once(root / "factor_panel_identity.json", {"scope": scope, "shape": list(panel.eligible.shape),
                "fit_scope": fit_scope, "feature_names": sorted(engine.feature_names),
                "source_provenance_hash": hashlib.sha256(_json(panel.provenance).encode()).hexdigest()})
            previous, directions, results, fit_results = {}, {}, [], []
            for entry in declarations:
                if (root / "cancel.request").exists():
                    raise InterruptedError("factor stage cancellation requested; completed checkpoints retained")
                original, key = entry["original"], entry["factor_key"]
                try:
                    spec = FactorSpec(original["name"], original["expression"], metadata={
                        "original_direction": original["direction"], "origin": entry["origin"],
                        "hypothesis": original.get("hypothesis"), "factor_key": key,
                        "primary_horizon": entry["primary_horizon"], "primary_horizon_source": entry["primary_horizon_source"]})
                    factor_id = spec.factor_id
                except Exception as exc:
                    spec = None
                    factor_id = hashlib.sha256(original["expression"].encode()).hexdigest()
                    validation_error = {"type": type(exc).__name__, "error": str(exc)}
                folder = root / "factors" / f"{factor_id[:16]}_{entry['declaration_index']:02d}"
                folder.mkdir(parents=True, exist_ok=True)
                checkpoint = folder / "receipt.json"
                if checkpoint.exists():
                    result = _load(checkpoint)
                    if result["stage_id"] != stage_id or result["data_fingerprint"] != engine.data_fingerprint:
                        raise ValueError("factor checkpoint is bound to different data/declaration")
                    _validate_result_files(result)
                    if result["status"] == "evaluated":
                        previous[key] = pd.read_parquet(folder / "scores.parquet")
                        directions[key] = original["direction"]
                        fit_results.append(_load(folder / "fit_summary.json"))
                    else:
                        fit_results.append(result["fit_failure"])
                    results.append(result)
                    continue
                source = {"origin": entry["origin"], "factor_key": key, "original": original,
                    "admitted_receipt": admission_proofs[0] if entry["origin"] == "admitted_model_hypothesis" else None,
                    "declaration_sha256": _hash(declaration_path)}
                cost = {"origin_model_call_ref": admission_proofs[0]["sha256"]
                        if entry["origin"] == "admitted_model_hypothesis" else None,
                        "allocated_model_money_usd": None if entry["origin"] == "admitted_model_hypothesis" else 0.,
                        "allocation_known": entry["origin"] == "original_seed",
                        "no_new_model_call": True}
                common = dict(scope=scope, seed={"origin": entry["origin"], "index": entry["origin_index"]},
                    model=receipt["runtime_identity"] if entry["origin"] == "admitted_model_hypothesis" else None,
                    source=source, exposure={"data": "previously_exposed_development", "next_model_scope": fit_scope},
                    cost=cost, run_id=f"{stage_id}:{entry['declaration_index']}",
                    library_snapshot_id=declaration["input_library_snapshot_id"])
                save_once(folder / "spec.json", {"factor_key": key, "original": original,
                    "spec": spec.to_dict() if spec else None, "source": source})
                planned_path = folder / "planned_attempt.json"
                if planned_path.exists():
                    planned = _load(planned_path)["attempt_id"]
                    library.get_event(planned)
                else:
                    planned = library.record_attempt(spec, status="planned" if spec else "invalid",
                        expression=original["expression"], error=None if spec else validation_error["error"],
                        metadata={"phase": "declaration", "trial_id": common["run_id"]}, **common)
                    save_once(planned_path, {"attempt_id": planned})
                step_started = time.perf_counter()
                try:
                    if spec is None:
                        raise ValueError(validation_error["error"])
                    evaluation = engine.evaluate(spec, horizons=tuple(PROTOCOL["factor_screen"]["horizons"]),
                        quantiles=PROTOCOL["factor_screen"]["quantiles"], existing_factors=previous,
                        min_cross_section=PROTOCOL["factor_screen"]["minimum_cross_section"])
                    fit = {"factor_key": key, "name": spec.name, "factor_id": factor_id, "status": "evaluated",
                        "origin": entry["origin"], "direction": original["direction"], "expression": spec.expression,
                        "primary_horizon_source": entry["primary_horizon_source"],
                        "hypothesis": original.get("hypothesis"), "scope": fit_scope,
                        **_fit_view(evaluation, engine, previous, directions, original["direction"], fit_dates,
                                    PROTOCOL, entry["primary_horizon"])}
                    _parquet_once(folder / "scores.parquet", evaluation.scores)
                    for name in TABLES:
                        _parquet_once(folder / (name + ".parquet"), getattr(evaluation, name))
                    save_once(folder / "summary.json", {**evaluation.summary,
                        "locked_to_next_model": True, "original_direction": original["direction"]})
                    save_once(folder / "fit_summary.json", fit)
                    artifacts = [_proof(folder / name) for name in ["spec.json", "scores.parquet", "summary.json",
                        "fit_summary.json", *[t + ".parquet" for t in TABLES]]]
                    elapsed = time.perf_counter() - step_started
                    complete = library.record_attempt(spec, status="evaluated", metadata={"transition_of": planned,
                        "phase": "evaluation", "trial_id": common["run_id"], "compute_wall_seconds": elapsed}, **common)
                    evidence = library.record_evidence(complete, scope=scope, status="observed", artifacts=artifacts,
                        metrics={"horizons": evaluation.summary["horizons"], "locked_to_next_model": True},
                        cost={"compute_wall_seconds": elapsed, "model_calls": 0})
                    library.record_exposure(attempt_id=complete, scope=scope, purpose="archived_development_diagnostics",
                        results_revealed=True, used_for_selection=False,
                        metadata={"2021_2024_results_in_next_model_payload": False})
                    previous[key], directions[key] = evaluation.scores, original["direction"]
                    fit_results.append(fit)
                    result = {"stage_id": stage_id, "factor_key": key, "factor_id": factor_id, "name": spec.name,
                        "direction": original["direction"], "status": "evaluated", "folder": str(folder),
                        "primary_horizon": entry["primary_horizon"],
                        "scores_path": str(folder / "scores.parquet"),
                        "data_fingerprint": engine.data_fingerprint, "planned_attempt_id": planned,
                        "attempt_id": complete, "evidence_id": evidence, "artifacts": artifacts,
                        "compute_wall_seconds": elapsed}
                except Exception as exc:
                    failure = {"type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}
                    save_once(folder / "failure.json", failure)
                    failed = library.record_attempt(spec, status="failed", expression=original["expression"],
                        error=str(exc), metadata={"transition_of": planned, "phase": "evaluation",
                            "trial_id": common["run_id"]}, **common)
                    failure_view = {"factor_key": key, "factor_id": factor_id, "name": original["name"],
                        "direction": original["direction"], "status": "failed", "failure_type": type(exc).__name__}
                    fit_results.append(failure_view)
                    result = {"stage_id": stage_id, "factor_key": key, "factor_id": factor_id, "name": original["name"],
                        "direction": original["direction"], "status": "failed", "folder": str(folder),
                        "scores_path": None,
                        "data_fingerprint": engine.data_fingerprint, "planned_attempt_id": planned,
                        "attempt_id": failed, "failure": failure, "fit_failure": failure_view,
                        "artifacts": [_proof(folder / "spec.json"), _proof(folder / "failure.json")]}
                save_once(checkpoint, result)
                results.append(result)
                print(_json({"factor_key": key, "status": result["status"], "completed": len(results),
                             "declared": len(declarations)}), flush=True)
            snapshot_id = library.create_snapshot(scope={**fit_scope, "purpose": "completed_factor_stage_frozen_library"})
            model_view = {"version": VERSION, "stage_id": stage_id, "scope": fit_scope,
                "fit_signal_sessions": len(fit_dates), "library_snapshot_id": snapshot_id,
                "library_snapshot_hash": library.load_snapshot(snapshot_id)["snapshot_hash"],
                "factor_declaration_sha256": _hash(declaration_path), "source_proofs": source_proofs,
                "factors": fit_results, "model_origin_usage": receipt.get("usage"),
                "not_independent_holdout": True, "model_calls_by_factor_stage": 0,
                "2021_2024_result_values_included": False, "next_model_may_read_full_range_artifacts": False,
                "view_policy": "Only this JSON is approved as the next model's numeric factor evidence; archive/snapshot IDs are lineage, not permission to load full evidence.",
                "limitations": ["descriptive adjusted-price factor diagnostics", "no executable portfolio PnL",
                                "previously exposed development data", "all attempts and directions retained"]}
            save_once(root / "fit_factor_view.json", model_view)
            library.record_exposure(library_snapshot_id=snapshot_id, scope=fit_scope,
                purpose="prepared_next_model_fit_view_not_yet_delivered", results_revealed=False,
                metadata={"fit_factor_view_sha256": _hash(root / "fit_factor_view.json")})
            result = {"version": VERSION, "stage_id": stage_id, "status": "completed",
                "scope": scope, "shape": list(panel.eligible.shape), "factor_count": len(results),
                "evaluated": sum(r["status"] == "evaluated" for r in results),
                "failed": sum(r["status"] == "failed" for r in results), "factors": results,
                "factor_declaration_sha256": _hash(declaration_path), "library_path": str(library_path),
                "library_snapshot_id": snapshot_id, "fit_factor_view_path": str(root / "fit_factor_view.json"),
                "fit_factor_view_sha256": _hash(root / "fit_factor_view.json"),
                "source_proofs": source_proofs, "cache": engine.cache_info,
                "duration_seconds": time.perf_counter() - started, "model_calls": 0,
                "account_executions": 0, "financial_success": False}
            save_once(index_path, result)
            return result
    finally:
        lock.unlink()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_RUN))
    options = parser.parse_args()
    result = evaluate_factor_stage(options.root)
    print(_json({key: result[key] for key in ("status", "evaluated", "failed", "duration_seconds")}), flush=True)
