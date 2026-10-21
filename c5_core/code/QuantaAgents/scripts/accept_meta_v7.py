"""Bounded V7 acceptance preparation; numerical/model phases are explicit.

Every acceptance owns a new directory. Preparing metadata never reads market
arrays, runs an account, or calls a model. Old research artifacts are read-only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OLD_ROOT = ROOT / "output/research/meta_v6_factor_calendar_20260909"
OLD_MODEL_ACCEPTANCE = ROOT / "output/research/research_kernel_acceptance_20260909/model_acceptance.json"
VERSION = "meta_v7_acceptance_v1"
NUMERIC_RANGE = ["2015-01-01", "2024-12-31"]
TRAINING = ["2016-01-01", "2020-12-31"]
DIAGNOSTIC = ["2021-01-01", "2024-12-31"]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def verify(proofs):
    for item in proofs:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("frozen input or source changed: " + item["path"])


def source_proofs():
    files = [Path(__file__)]
    files += sorted((ROOT / "src/quanta_agents/meta_v7").glob("*.py"))
    files += [ROOT / "src/quanta_agents" / name for name in (
        "research_kernel/assets.py", "research_kernel/compiler.py", "research_kernel/execution.py",
        "research_kernel/store.py", "meta_v6/data.py", "meta_v6/factors.py", "meta/factor_algebra.py",
        "meta_v6/portfolio.py", "meta_v6/gateway.py", "meta_v6/jobs.py", "meta_v3/windows_job.py")]
    return [proof(path) for path in files]


def source_declaration(old_root):
    """Read saved provenance and calendar metadata, never numerical Parquet arrays."""
    from quanta_agents.meta_v6.data import historical_universe
    old_root = Path(old_root).resolve()
    data_path = old_root / "preparation/data_selection.json"
    inventory_path = old_root / "preparation/source_inventory.json"
    data, inventory = read(data_path), read(inventory_path)
    if data.get("authorized_numeric_range") != NUMERIC_RANGE:
        raise ValueError("old authorization differs from the fixed 2015-2024 development scope")
    if inventory["request"]["start"] != NUMERIC_RANGE[0] or inventory["request"]["end"] != NUMERIC_RANGE[1]:
        raise ValueError("old saved inventory scope differs")
    bound = [proof(data_path), proof(inventory_path)]
    for key in ("calendar", "membership"):
        item = proof(data[key + "_path"])
        if item["sha256"] != data[key + "_sha256"]:
            raise ValueError("original " + key + " metadata changed")
        bound.append(item)
    manifest = Path(data["data_root"]) / "manifest.json"
    item = proof(manifest)
    if item["sha256"] != data["source_manifest_sha256"]:
        raise ValueError("original source manifest changed")
    bound.append(item)
    days = [line.strip() for line in Path(data["calendar_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    if days != sorted(set(days)):
        raise ValueError("calendar must be unique and ordered")
    universe = historical_universe(data["membership_path"], start=NUMERIC_RANGE[0], end=NUMERIC_RANGE[1])
    if universe != data["symbols"] or len(universe) != data["symbol_count"]:
        raise ValueError("old complete historical universe differs from current membership metadata")
    ranges = {}
    for name, (first, last) in {"numeric": NUMERIC_RANGE, "training": TRAINING, "diagnostic": DIAGNOSTIC}.items():
        selected = [day for day in days if first <= day <= last]
        if not selected:
            raise ValueError("empty declared " + name + " calendar")
        ranges[name] = {"requested_start": first, "requested_end": last,
                        "first_session": selected[0], "last_session": selected[-1], "sessions": len(selected)}
    inventory_files = inventory["request"]["source_files"]
    if [row["symbol"] for row in inventory_files] != universe:
        raise ValueError("saved source inventory does not cover the full fixed universe")
    source_rows = []
    for row in inventory_files:
        path = Path(row["path"])
        current_status = "present" if path.is_file() else "missing"
        if current_status != row["status"] or (current_status == "present" and path.stat().st_size != row["bytes"]):
            raise ValueError("saved source availability or size changed: " + str(path))
        source_rows.append({**row, "prepare_check": "path/status/size plus bound saved inventory; full payload digest rechecked by numeric worker"})
    return {"input_proofs": bound, "data_root": data["data_root"], "calendar_path": data["calendar_path"],
        "membership_path": data["membership_path"], "ranges": ranges, "symbols": universe,
        "symbol_count": len(universe), "source_files": source_rows,
        "original_exposure_note": data["previous_exposure_note"],
        "universe": "complete CSI300 historical interval union over 2015-2024; signal-date eligibility retained",
        "training_history": "2015 data are causal warmup; model factor evidence ends at the frozen 2020 cutoff",
        "diagnostic_role": "previously exposed temporal diagnostic; neither independent holdout nor input for model reselection",
        "numeric_2025_read": False, "globally_unseen": False,
        "range_isolation": "date predicates before returned arrays; no 2025 arrays/cache/statistics; page-level physical decoding isolation not claimed",
        "historical_available_at_verified": False, "historical_adjustment_publication_verified": False,
        "adjustment": data["signal_adjustment"], "missing_policy": data["missing_policy"]}


def comparison_baseline(old_model_acceptance):
    """Preserve the previous operational outcome without recycling it as a paired benchmark."""
    path = Path(old_model_acceptance).resolve()
    previous = read(path)
    return {"proof": proof(path), "model_calls": previous["study_status"]["model_calls"],
        "completed_accounts": previous["study_status"]["runs"].get("completed", 0),
        "usage": previous["all_model_usage_totals"],
        "model_identities": [{key: row[key] for key in ("id", "model", "effort", "verified_local_model")}
                             for row in previous["model_calls"]],
        "interpretation": "Historical different-task observation only; not a controlled V7 capability or performance comparison"}


def prepare(output, *, old_root=OLD_ROOT, old_model_acceptance=OLD_MODEL_ACCEPTANCE,
            catalog=None, pilot_protocol=None, calendar_root=None):
    """Create one immutable preparation. All numerical and paid execution counts are zero."""
    from quanta_agents.meta_v6.portfolio import AccountPolicy
    from quanta_agents.meta_v7.factor_lab import semantics
    from quanta_agents.meta_v7.assets import AssetRegistry
    output, old_root = Path(output).resolve(), Path(old_root).resolve()
    if output.exists():
        raise ValueError("acceptance output must be new; preserve all old attempts")
    # Resolve and validate sources before creating the new acceptance directory.
    declared = source_declaration(old_root)
    previous = comparison_baseline(old_model_acceptance)
    pins = source_proofs()
    plan = read(pilot_protocol) if pilot_protocol is not None else None
    output.mkdir(parents=True)
    intent = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "real-data V7 software and workflow acceptance; no profitability or upper-bound claim",
        "source_proofs": pins, "data": declared, "previous_model_acceptance": previous,
        "factor_lab_semantics": semantics((5, 20)), "account_policy": asdict(AccountPolicy()),
        "cost_multipliers": [1.], "cost_mode": "adjusted-unit development approximation with observed fees/slippage",
        "pilot_protocol": plan, "pilot_protocol_proof": proof(pilot_protocol) if pilot_protocol is not None else None,
        "pilot_admission": "explicit phase only after benchmark API/protocol source freeze; preparation never invokes gateway",
        "catalog_proof": proof(catalog) if catalog is not None else None,
        "calendar_source_root": str(Path(calendar_root).resolve()) if calendar_root is not None else None,
        "phase_policy": {"new_phase_directory": True, "maximum_implicit_retries": 0,
                         "training_before_diagnostics": True, "diagnostic_results_must_not_reselect_model_winner": True},
        "counters": {"numeric_arrays_read": 0, "factor_evaluations": 0, "account_executions": 0, "model_calls": 0},
        "financial_success": False, "independent_validation": False, "upper_bound_demonstrated": False}
    write_once(output / "acceptance_intent.json", intent)
    config = research_config(output, declared)
    write_once(output / "research_config.json", config)
    registry = AssetRegistry(Path(config["project_root"]) / "assets")
    try:
        imported = registry.import_v6(old_root)
        catalog_result = registry.import_metadata(Path(catalog), "factor_calendar") if catalog is not None else None
        calendar_result = registry.ingest_calendar(Path(calendar_root)) if calendar_root is not None else None
        verify(pins + declared["input_proofs"] + [previous["proof"]])
        report = {"status": "prepared", "intent_sha256": sha(output / "acceptance_intent.json"),
            "imported_v6": imported, "imported_catalog": catalog_result, "ingested_calendar": calendar_result,
            "registry_root": str(Path(config["project_root"]) / "assets"), "source_hashes_reverified": True,
            "research_config": proof(output / "research_config.json"), "study_initialized": False,
            "numeric_arrays_read": 0, "factor_evaluations": 0, "account_executions": 0, "model_calls": 0,
            "numeric_2025_read": False, "financial_success": False,
            "next_step": "freeze and explicitly execute a bounded numeric or pilot phase after adapter API confirmation"}
        write_once(output / "preparation_receipt.json", report)
        return report
    except BaseException as exc:
        write_once(output / "preparation_failure.json", {"status": "failed", "type": type(exc).__name__,
            "message": str(exc), "partial_outputs_retained": True, "implicit_retry": False,
            "model_calls": 0, "account_executions": 0})
        raise


def verify_preparation(output):
    output = Path(output).resolve()
    receipt, intent = read(output / "preparation_receipt.json"), read(output / "acceptance_intent.json")
    if receipt.get("status") != "prepared" or receipt["intent_sha256"] != sha(output / "acceptance_intent.json"):
        raise ValueError("preparation intent binding failed")
    proofs = intent["source_proofs"] + intent["data"]["input_proofs"] + [intent["previous_model_acceptance"]["proof"]]
    proofs += [intent[key] for key in ("pilot_protocol_proof", "catalog_proof") if intent.get(key)]
    verify(proofs)
    return output, intent, receipt


def research_config(output, declared):
    """A reviewable configuration only; writing it does not initialize research."""
    output = Path(output).resolve()
    return {"study_id": "v7_real_training_2016_2020", "project_root": str(output / "project"),
        "split_plan": {"train_start": TRAINING[0], "train_end": TRAINING[1],
            "validation_start": DIAGNOSTIC[0], "validation_end": DIAGNOSTIC[1],
            "embargo_sessions": 0, "max_label_horizon": 20},
        "data": {"data_root": declared["data_root"], "start": NUMERIC_RANGE[0], "end": NUMERIC_RANGE[1],
            "authorized_start": NUMERIC_RANGE[0], "authorized_end": NUMERIC_RANGE[1],
            "calendar_path": declared["calendar_path"], "membership_path": declared["membership_path"],
            "symbols": declared["symbols"], "cache_dir": str(output / "panel_cache")},
        "budget": {"max_attempts": 32, "max_executions": 16, "max_batch_size": 8, "max_model_calls": 8,
            "max_context_bytes": 24000, "max_response_bytes": 8000, "job_timeout_seconds": 900},
        "research_limits": {"max_factor_jobs": 8, "max_factor_assets": 32, "max_pair_tests": 32,
            "max_validation_jobs": 2, "closing_model_calls": 1, "max_rounds": 4},
        "validation_mode": "exposed_temporal", "account_policy": {},
        "objective": "Use frozen 2016-2020 training factor evidence to develop and falsify combinations. "
            "Freeze a candidate before 2021-2024 previously exposed diagnostics; never reselect using diagnostics. "
            "Retain failed/duplicate/weak factors, role distinctions, costs and limitations; no 2025 numerical values.",
        "prior_exposures": [{"start": NUMERIC_RANGE[0], "end": NUMERIC_RANGE[1],
            "reason": declared["original_exposure_note"]}]}


PILOT_VERSION = "v7_representation_pilot_v1"
PILOT_SEEDS = (20260909, 20260910)
PILOT_MODES = ("soft_raw", "V7_compact")


def pilot_sources():
    from quanta_agents.meta_v6.gateway import base
    names = ("meta_v7/benchmarks.py", "meta_v7/validation.py", "meta_v7/temporal.py",
             "meta_v6/data.py", "meta_v6/gateway.py", "meta_v3/codex_session_gateway.py", "meta_v3/kernel.py")
    return [proof(Path(__file__)), proof(base.__file__),
            *[proof(ROOT / "src/quanta_agents" / name) for name in names]]


def pilot_schema(task_ids, candidates):
    return {"type": "object", "additionalProperties": False, "required": ["tasks", "limitations"],
        "properties": {"tasks": {"type": "array", "minItems": 3, "maxItems": 3,
            "items": {"type": "object", "additionalProperties": False,
                "required": ["task_id", "discoveries", "reason"], "properties": {
                    "task_id": {"type": "string", "enum": task_ids},
                    "discoveries": {"type": "array", "maxItems": 1, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["candidate_id", "direction"], "properties": {
                            "candidate_id": {"type": "string", "enum": candidates},
                            "direction": {"type": "integer", "enum": [-1, 1]}}}},
                    "reason": {"type": "string", "maxLength": 900}}}},
            "limitations": {"type": "string", "maxLength": 1000}}}


def _daily_values(score, labels):
    import numpy as np
    valid = np.isfinite(score) & np.isfinite(labels)
    a, b = score.where(valid).rank(axis=1), labels.where(valid).rank(axis=1)
    series = a.corrwith(b, axis=1).where(valid.sum(axis=1) >= 8)
    return [round(float(value), 5) if np.isfinite(value) else None for value in series]


def _aggregate_values(values):
    import numpy as np
    a = np.array([np.nan if value is None else value for value in values], dtype=float)
    valid = a[np.isfinite(a)]
    def mean(part):
        finite = part[np.isfinite(part)]
        return round(float(finite.mean()), 6) if len(finite) else None
    return {"mean": mean(a), "std_ddof1": round(float(valid.std(ddof=1)), 6) if len(valid) > 1 else None,
        "observed_dates": len(valid), "missing_dates": len(a)-len(valid),
        "positive_fraction": round(float((valid > 0).mean()), 6) if len(valid) else None,
        "first_half_mean": mean(a[:len(a)//2]), "second_half_mean": mean(a[len(a)//2:])}


def _training_evidence(task):
    """Transform only training slices. The rounded table is the common evidence."""
    from itertools import combinations
    labels = task["train_labels"]
    ranks = {key: value.loc[labels.index].rank(axis=1, pct=True) for key, value in task["frames"].items()}
    candidates = {"factor:"+key: _daily_values(ranks[key], labels) for key in sorted(ranks)}
    correlations = {}
    for left, right in combinations(sorted(ranks), 2):
        key = "interaction:" + left + ":" + right
        candidates[key] = _daily_values((ranks[left]-.5)*(ranks[right]-.5), labels)
        correlations[left+":"+right] = _daily_values(ranks[left], ranks[right])
    return {"task_id": task["task_id"], "dates": [str(day.date()) for day in labels.index],
            "candidate_daily_ic": candidates, "factor_pair_daily_rank_correlation": correlations}


def _pilot_prompt(evidence, mode):
    tasks = evidence if mode == "soft_raw" else [{"task_id": row["task_id"], "dates": row["dates"],
        "candidate_ic": {key: _aggregate_values(value) for key, value in row["candidate_daily_ic"].items()},
        "factor_pair_rank_correlation": {key: _aggregate_values(value)
            for key, value in row["factor_pair_daily_rank_correlation"].items()}} for row in evidence]
    body = {"tasks": tasks, "presentation": "daily tables" if mode == "soft_raw" else "descriptive aggregation"}
    intro = ("Assess three opaque synthetic tasks using ONLY the supplied training evidence. "
        "For each task freeze at most one discovery, or an empty discoveries list, and give a concise reason. "
        "All tasks expose exactly the same menu: four individual percentile-ranked factors and six pair interactions. "
        "Interaction A:B = (same-day percentile rank(A)-0.5)*(same-day percentile rank(B)-0.5); "
        "daily IC is cross-sectional Spearman against next-open entry D+1 to exit D+2 returns. "
        "Signal dates are retained, terminal two unknown label dates are null. "
        "Both available presentations derive from the identical daily numbers rounded to five decimals; "
        "summaries, where present, are descriptive means, sample standard deviations, positive fractions and fixed half means. "
        "Correlations refer to factors on the same training date, not to future labels. "
        "Choose a candidate ID exactly as supplied and direction +1 or -1 explicitly. "
        "Weak marginal IC does not logically preclude an interaction; high marginal correlation is not independent evidence. "
        "Do not assume any number or type of true discoveries. Null/noisy results and abstention are valid. "
        "No validation labels, hidden task construction or truth are available. Do not infer missing values or claim "
        "profitability, multiplicity correction or independent market validation. Use no tools. "
        "Return only the required JSON, at most 8000 UTF-8 bytes.\n")
    return intro + json.dumps(body, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _check_pilot_response(response, task_ids, candidate_ids):
    if set(response) != {"tasks", "limitations"} or len(response["tasks"]) != 3:
        raise ValueError("pilot response requires exactly three tasks")
    ids = [row["task_id"] for row in response["tasks"]]
    if set(ids) != set(task_ids) or len(set(ids)) != 3:
        raise ValueError("pilot task missing, duplicate or unknown")
    for row in response["tasks"]:
        if set(row) != {"task_id", "discoveries", "reason"} or len(row["discoveries"]) > 1:
            raise ValueError("pilot discovery schema mismatch")
        for item in row["discoveries"]:
            if set(item) != {"candidate_id", "direction"} or item["candidate_id"] not in candidate_ids:
                raise ValueError("unknown discovery candidate")
            if type(item["direction"]) is not int or item["direction"] not in (-1, 1):
                raise ValueError("explicit direction must be +1 or -1")


def pilot(output):
    """Exactly four authorized genuine model calls, with no model retry/resume."""
    import numpy as np
    from quanta_agents.meta_v7.benchmarks import KINDS, make_synthetic_task, score_discovery
    from quanta_agents.meta_v6.gateway import CodexGateway, verify_saved_completion, capture_saved_session
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("pilot requires a new directory; existing paid artifacts must never be regenerated")
    output.mkdir(parents=True)
    pins = pilot_sources()
    protocol = {"version": PILOT_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "seeds": list(PILOT_SEEDS), "modes": list(PILOT_MODES), "task_kinds_hidden_from_model": list(KINDS),
        "task_parameters": {"train_sessions": 40, "validation_sessions": 80, "stocks": 32},
        "candidate_menu": "4 singles + all 6 centered percentile-rank products; identical in both modes",
        "common_training_evidence": "40 dated IC rows per candidate and 6 factor correlation rows; rounded5 before either view",
        "compact_aggregation": "mean, sample std, finite/missing date counts, positive fraction, fixed first/second halves",
        "model": "gpt-6-astra", "effort": "xhigh", "total_call_cap": 4, "calls_per_cell": 1,
        "max_context_bytes": 24000, "max_response_bytes": 8000, "timeout_per_call_seconds": 900,
        "output_limit_enforcement": "post-generation admission, not provider token cap",
        "automatic_model_retries": 0, "counterbalance": [[20260909, "soft_raw"], [20260909, "V7_compact"],
            [20260910, "V7_compact"], [20260910, "soft_raw"]],
        "all_contexts_frozen_before_first_call": True, "all_decisions_frozen_before_any_validation_score": True,
        "source_proofs": pins, "interpretation": "small representation pilot; neither full V6/V7 ability comparison nor causal upper-bound proof",
        "financial_accounts": 0, "real_market_arrays": 0, "numeric_2025_read": False,
        "truth_isolation": "not in prompts; tool surfaces disabled by gateway, not OS-level isolation certification"}
    write_once(output / "pilot_protocol.json", protocol)
    tasks, evidences, hidden = {}, {}, []
    for seed in PILOT_SEEDS:
        group = [make_synthetic_task(seed, kind, **protocol["task_parameters"]) for kind in KINDS]
        group = [group[i] for i in np.random.default_rng(seed+73).permutation(len(group))]
        evidences[seed] = [_training_evidence(task) for task in group]
        for task in group:
            tasks[task["task_id"]] = task
            hidden.append({"seed": seed, "generation_config": task["generation_config"], "truth": task["truth"]})
        write_once(output / ("common_training_"+str(seed)+".json"), evidences[seed])
    write_once(output / "hidden_truth.json", hidden)
    cells = []
    for i, (seed, mode) in enumerate(protocol["counterbalance"]):
        folder = output / "cells" / f"{i+1:02d}_{seed}_{mode}"
        folder.mkdir(parents=True)
        evidence = evidences[seed]
        prompt = _pilot_prompt(evidence, mode)
        if len(prompt.encode("utf-8")) > protocol["max_context_bytes"]:
            raise ValueError("pilot input budget exceeded before any model call")
        with (folder / "frozen_prompt.txt").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(prompt)
        candidates = list(evidence[0]["candidate_daily_ic"])
        schema = pilot_schema([row["task_id"] for row in evidence], candidates)
        write_once(folder / "frozen_schema.json", schema)
        cells.append({"cell": folder.name, "seed": seed, "mode": mode, "folder": str(folder),
            "prompt": proof(folder / "frozen_prompt.txt"), "schema": proof(folder / "frozen_schema.json"),
            "common_evidence": proof(output / ("common_training_"+str(seed)+".json")),
            "task_ids": [row["task_id"] for row in evidence], "candidate_ids": candidates})
    write_once(output / "pilot_intent.json", {"protocol": proof(output / "pilot_protocol.json"),
        "hidden_truth": proof(output / "hidden_truth.json"), "cells": cells, "source_proofs": pins})
    bound = [proof(output / name) for name in ("pilot_protocol.json", "pilot_intent.json", "hidden_truth.json")]
    outcomes = []
    for cell in cells:
        folder = Path(cell["folder"])
        verify(pins + bound + [cell["prompt"], cell["schema"], cell["common_evidence"]])
        if (output / "cancel.request").exists():
            outcomes.append({"cell": cell["cell"], "status": "cancelled_before_call"})
            continue
        write_once(folder / "call_started.json", {"started_at": datetime.now(timezone.utc).isoformat(),
            "attempt": 1, "model_call_allowance_consumed": 1, "intent_sha256": sha(output / "pilot_intent.json")})
        print(json.dumps({"cell": cell["cell"], "status": "model_started"}), flush=True)
        started = time.perf_counter()
        try:
            try:
                receipt = CodexGateway(timeout_seconds=900).run(
                    prompt=(folder / "frozen_prompt.txt").read_text(encoding="utf-8"),
                    schema=read(folder / "frozen_schema.json"), workdir=folder / "call",
                    on_event=lambda event: None, cancelled=lambda: (output / "cancel.request").exists())
            except Exception as exc:
                # A completed paid response can be captured/verified offline, never regenerated.
                write_once(folder / "gateway_failure.json", {"error": str(exc), "type": type(exc).__name__,
                    "usage": getattr(exc, "usage", {}), "model_retry": False})
                receipt = verify_saved_completion(folder / "call")
                if not receipt.get("runtime_identity", {}).get("verified"):
                    capture_saved_session(folder / "call", receipt)
                    receipt = verify_saved_completion(folder / "call")
            if receipt.get("model") != "gpt-6-astra" or receipt.get("effort") != "xhigh" or not receipt.get("runtime_identity", {}).get("verified"):
                raise ValueError("genuine local model/effort identity is not verified")
            write_once(folder / "verified_receipt.json", receipt)
            if (folder / "call/prompt.txt").read_text(encoding="utf-8") != (folder / "frozen_prompt.txt").read_text(encoding="utf-8"):
                raise ValueError("gateway logical prompt differs from frozen context")
            if (folder / "call/response.json").stat().st_size > 8000:
                raise ValueError("saved model response exceeds output admission limit")
            _check_pilot_response(receipt["response"], cell["task_ids"], cell["candidate_ids"])
            verify(pins + bound + [cell["prompt"], cell["schema"], cell["common_evidence"]])
            write_once(folder / "frozen_decision.json", {"response": receipt["response"],
                "receipt": proof(folder / "verified_receipt.json"), "all_original_text_preserved": True,
                "validation_values_read_for_admission": False})
            outcome = {"cell": cell["cell"], "status": "admitted", "usage": receipt.get("usage", {}),
                "duration_seconds": time.perf_counter()-started, "local_model_verified": True,
                "decision": proof(folder / "frozen_decision.json")}
        except Exception as exc:
            outcome = {"cell": cell["cell"], "status": "failed", "error": str(exc),
                "duration_seconds": time.perf_counter()-started, "model_retry": False,
                "usage": read(folder / "verified_receipt.json").get("usage", {}) if (folder / "verified_receipt.json").exists() else {}}
        write_once(folder / "cell_result.json", outcome)
        outcomes.append(outcome)
        print(json.dumps({"cell": cell["cell"], "status": outcome["status"], "duration_seconds": outcome["duration_seconds"]}), flush=True)
    write_once(output / "all_decisions_terminal.json", {"cells": outcomes, "validation_scoring_started": False})
    # Hidden truth and validation numerical arrays are consumed only after ALL decisions are terminal.
    scored = []
    for cell, outcome in zip(cells, outcomes):
        if outcome["status"] != "admitted":
            scored.append({**outcome, "evaluations": [], "seed": cell["seed"], "mode": cell["mode"]})
            continue
        folder = Path(cell["folder"])
        decision = read(folder / "frozen_decision.json")
        verify([outcome["decision"], decision["receipt"]])
        evaluations = []
        for row in decision["response"]["tasks"]:
            task = tasks[row["task_id"]]
            predictions = [item["candidate_id"] for item in row["discoveries"]]
            validation = None
            if row["discoveries"]:
                item = row["discoveries"][0]
                parts = item["candidate_id"].split(":")
                labels = task["validation_labels"]
                ranks = [task["frames"][key].loc[labels.index].rank(axis=1, pct=True) for key in parts[1:]]
                score = ranks[0] if parts[0] == "factor" else (ranks[0]-.5)*(ranks[1]-.5)
                validation = _aggregate_values(_daily_values(score*item["direction"], labels))
            evaluations.append({"task_id": task["task_id"], "hidden_kind": task["truth"]["kind"],
                "discoveries": row["discoveries"], "structural_score": score_discovery(predictions, task["truth"]),
                "validation_oriented_rank_ic": validation, "validation_used_for_selection": False})
        item = {**outcome, "seed": cell["seed"], "mode": cell["mode"], "evaluations": evaluations}
        write_once(folder / "offline_score.json", item)
        scored.append(item)
    verify(pins + bound)
    report = {"version": PILOT_VERSION, "status": "completed" if all(row["status"] == "admitted" for row in outcomes) else "completed_with_failures",
        "protocol": proof(output / "pilot_protocol.json"), "intent": proof(output / "pilot_intent.json"),
        "hidden_truth": proof(output / "hidden_truth.json"), "cells": scored,
        "model_calls_started": sum((Path(cell["folder"]) / "call_started.json").exists() for cell in cells),
        "model_calls_admitted": sum(row["status"] == "admitted" for row in outcomes), "model_retries": 0,
        "accounts": 0, "real_market_arrays": 0, "representation_only": True,
        "small_sample": True, "upper_bound_proven": False, "financial_success": False,
        "limitations": ["Same synthetic evidence with different presentation; not full V6/V7 research ability",
            "Two seeds do not establish a statistically reliable model effect or independent market evidence",
            "Output byte limit is admission only; billed usage is actual gateway usage, not a hard token cap"]}
    write_once(output / "pilot_report.json", report)
    return {"status": report["status"], "model_calls_started": report["model_calls_started"],
            "model_calls_admitted": report["model_calls_admitted"], "report": proof(output / "pilot_report.json")}


def numeric_sources():
    names = ("meta_v7/temporal.py", "meta_v7/execution.py", "meta_v7/factor_lab.py",
        "research_kernel/assets.py", "research_kernel/compiler.py", "research_kernel/execution.py",
        "research_kernel/store.py", "meta_v6/data.py", "meta_v6/factors.py", "meta/factor_algebra.py",
        "meta_v6/portfolio.py", "meta_v6/portfolio_study.py", "meta_v6/jobs.py", "meta_v3/windows_job.py")
    return [proof(Path(__file__)), proof(ROOT / "scripts/accept_research_kernel_numeric.py"),
            *[proof(ROOT / "src/quanta_agents" / name) for name in names]]


def numeric_worker(args):
    import pandas as pd
    from pandas.testing import assert_frame_equal
    from accept_research_kernel_numeric import frame_hash, save_targets
    from quanta_agents.meta_v6.data import load_market_panel
    from quanta_agents.meta_v6.portfolio import AccountPolicy, DailyAccount, PortfolioSpec, target_weights
    from quanta_agents.meta_v6.portfolio_study import save_account
    from quanta_agents.research_kernel.assets import AssetRegistry
    from quanta_agents.meta_v7.execution import execute_strategy
    from quanta_agents.meta_v7.factor_lab import evaluate_factors
    out = args.output.resolve()
    intent_path = out / "numeric_intent.json"
    if sha(intent_path) != args.intent_sha256:
        raise ValueError("numeric worker intent hash mismatch")
    intent = read(intent_path)
    pins = intent["source_proofs"] + intent["data"]["input_proofs"] + intent["declaration"]["input_proofs"]
    verify(pins)
    started = time.perf_counter()
    counters = {"model_calls": 0, "accounts_started": 0, "accounts_completed": 0, "factor_lab_jobs": 0}
    def progress(stage):
        row = {"stage": stage, "elapsed_seconds": time.perf_counter() - started, **counters}
        with (out / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n"); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps(row), flush=True)
    def cancelled():
        return time.perf_counter() - started >= args.timeout_seconds - 5 or (out / "cancel.request").exists()
    try:
        declared = intent["declaration"]
        data = intent["data"]
        progress("loading_authorized_panel")
        panel = load_market_panel(data["data_root"], start=NUMERIC_RANGE[0], end=NUMERIC_RANGE[1],
            authorized_start=NUMERIC_RANGE[0], authorized_end=NUMERIC_RANGE[1],
            calendar_path=data["calendar_path"], membership_path=data["membership_path"],
            symbols=data["symbols"], cache_dir=out / "panel_cache")
        if panel.dates.min().year < 2015 or panel.dates.max().year > 2024:
            raise ValueError("numeric range escaped the declaration")
        observed_files = panel.provenance["request"]["source_files"]
        expected_files = [{k: v for k, v in row.items() if k != "prepare_check"} for row in data["source_files"]]
        if observed_files != expected_files:
            raise ValueError("raw payload inventory differs from original frozen source hashes")
        write_once(out / "panel_receipt.json", {"shape": list(panel.eligible.shape),
            "fingerprint": panel.fingerprint(), "provenance": panel.provenance, "load_metrics": panel.load_metrics,
            "numeric_2025_read": False})
        progress("three_fixed_assets")
        registry = AssetRegistry(out / "assets")
        definitions = [row for row in declared["asset_definitions"] if row["id"] in {"F1", "F2", "X4"}]
        for definition in definitions:
            registry.register(definition)
        scores, cache = registry.resolve([row["id"] for row in definitions], panel)
        if not cache["complete"] or set(scores) != {"F1", "F2", "X4"}:
            raise ValueError("fixed numeric acceptance assets unavailable")
        write_once(out / "asset_resolution.json", cache)
        policy = AccountPolicy(**intent["account_policy"])
        old_spec = PortfolioSpec(**declared["original_portfolio"])
        old_scores = {declared["original_names"][key]: scores[key] for key in ("F1", "F2")}
        comparisons = []
        for name, (first, last) in (("training", TRAINING), ("diagnostic", DIAGNOSTIC)):
            old_folder, new_folder = out / (name + "_old"), out / (name + "_v7")
            progress(name + "_old")
            targets = target_weights(panel, old_scores, old_spec, start=first, end=last)
            counters["accounts_started"] += 1
            old = DailyAccount(panel, policy).run(targets, start=first, end=last, cancelled=cancelled)
            counters["accounts_completed"] += 1
            save_targets(old_folder, targets); save_account(old_folder, old)
            progress(name + "_v7")
            counters["accounts_started"] += 1
            new = execute_strategy(panel, {key: scores[key] for key in ("F1", "F2")}, declared["baseline_strategy"],
                start=first, end=last, policy=policy, cancelled=cancelled)
            counters["accounts_completed"] += 1
            save_targets(new_folder, new["targets"]); save_account(new_folder, new)
            write_once(new_folder / "temporal_scope.json", new["temporal_scope"])
            tables = []
            for key, a, b in [(key, old[key], new[key]) for key in ("daily", "trades", "annual")] + [("targets", targets, new["targets"])]:
                left, right = a.copy(deep=False), b.copy(deep=False)
                left.attrs, right.attrs = {}, {}
                assert_frame_equal(left, right, check_exact=True, check_dtype=True)
                tables.append({"table": key, "exact": True, "shape": list(a.shape),
                               "old_content_sha256": frame_hash(a), "v7_content_sha256": frame_hash(b)})
            plans_exact = targets.attrs.get("selection_plans") == new["targets"].attrs.get("selection_plans")
            summary_exact = all(old["summary"][key] == new["summary"][key] for key in old["summary"] if key != "duration_seconds")
            if not plans_exact or not summary_exact or old["policy"] != new["policy"]:
                raise ValueError("account metadata, economic plans or summary parity failure")
            comparison = {"scope": name, "requested_dates": [first, last], "tables": tables,
                "plans_exact": plans_exact, "summary_exact_except_elapsed": summary_exact,
                "policy_exact": True, "role": "training_development" if name == "training" else "previously_exposed_temporal_diagnostic"}
            comparisons.append(comparison)
            write_once(out / (name + "_comparison.json"), comparison)
        progress("training_factor_lab_only")
        counters["factor_lab_jobs"] += 1
        lab = evaluate_factors(panel, scores, start=TRAINING[0], end=TRAINING[1], horizons=(5, 20),
            pairs=[{"left": "F1", "right": "F2"}], controls={"roles": {"X4": ["risk"]}})
        write_once(out / "training_factor_lab.json", lab)
        verify(pins)
        report = {"version": VERSION, "status": "passed", "intent_sha256": args.intent_sha256,
            "comparisons": comparisons, **counters, "worker_wall_seconds": time.perf_counter() - started,
            "training_factor_report": proof(out / "training_factor_lab.json"),
            "source_proofs": intent["source_proofs"], "numeric_2025_read": False,
            "financial_success": False, "independent_holdout": False,
            "upper_bound_demonstrated": False, "partial_outputs_retained": True,
            "limitations": ["Fixed-strategy software parity, not strategy search or a proof of future profitability",
                "The 2021-2024 diagnostic is already exposed; none of its results enter the training factor report",
                "Adjusted-unit accounting, open-fill/capacity approximation, historical arrival unverified"]}
        write_once(out / "numeric_report.json", report)
        progress("passed")
        return 0
    except BaseException as exc:
        write_once(out / "numeric_failure.json", {"status": "failed", "type": type(exc).__name__,
            "message": str(exc), "traceback": traceback.format_exc(), **counters,
            "worker_wall_seconds": time.perf_counter() - started, "implicit_retry": False,
            "partial_outputs_retained": True, "financial_success": False})
        raise


def numeric(output, *, old_root=OLD_ROOT, timeout_seconds=300):
    from accept_research_kernel_numeric import declarations
    from quanta_agents.meta_v6.portfolio import AccountPolicy
    from quanta_agents.meta_v6.jobs import run_job
    from quanta_agents.meta_v7.factor_lab import semantics
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("numeric acceptance requires a new output directory")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
        raise ValueError("bounded integer timeout required")
    data, declared = source_declaration(old_root), declarations(Path(old_root).resolve())
    output.mkdir(parents=True)
    pins = numeric_sources()
    archived = []
    for i, item in enumerate(pins):
        destination = output / "source_snapshots" / f"{i:02d}_{Path(item['path']).name}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(Path(item["path"]).read_bytes())
        archived.append(proof(destination))
    intent = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "authorized fixed-scope V7 numerical engineering parity", "data": data, "declaration": declared,
        "source_proofs": pins, "source_snapshots": archived, "account_policy": asdict(AccountPolicy()),
        "factor_lab_semantics": semantics((5, 20)), "factor_lab_ids": ["F1", "F2", "X4"],
        "factor_lab_pairs": [{"left": "F1", "right": "F2"}], "factor_lab_roles": {"X4": ["risk"]},
        "timeout_seconds": timeout_seconds, "maximum_account_executions": 4,
        "maximum_attempts": 1, "automatic_retries": 0, "model_calls": 0,
        "financial_success": False, "independent_holdout": False, "numeric_2025_read": False}
    path = output / "numeric_intent.json"
    write_once(path, intent)
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "_numeric-worker", "--output", str(output),
               "--intent-sha256", sha(path), "--timeout-seconds", str(timeout_seconds)]
    job = run_job(command, cwd=ROOT, output_dir=output / "jobs/numeric", timeout_seconds=timeout_seconds,
        max_output_bytes=64 * 1024**2, cancel_file=output / "cancel.request", env={"PYTHONIOENCODING": "utf-8"})
    success = job.get("status") == "completed" and job.get("all_owned_processes_exited") is True
    report = read(output / "numeric_report.json") if success and (output / "numeric_report.json").is_file() else None
    success = success and report is not None and report.get("status") == "passed" and report["accounts_completed"] == 4
    result = {"status": "passed" if success else "failed", "job_status": job.get("status"),
        "all_owned_processes_exited": job.get("all_owned_processes_exited"),
        "job_receipt": proof(output / "jobs/numeric/job_result.json"),
        "report": proof(output / "numeric_report.json") if report else None,
        "model_calls": 0, "financial_success": False, "automatic_retries": 0}
    write_once(output / "supervisor_receipt.json", result)
    return result


def self_check():
    """Generated-only immutable file helper check, without opening any research sources."""
    with tempfile.TemporaryDirectory(prefix="v7_acceptance_helpers_") as temporary:
        path = Path(temporary) / "intent.json"
        write_once(path, {"scope": TRAINING, "model_calls": 0})
        bound = proof(path)
        verify([bound])
        try:
            write_once(path, {"overwrite": True})
        except FileExistsError:
            pass
        else:
            raise AssertionError("immutable intent overwrite was allowed")
        path.write_text("changed", encoding="utf-8")
        try:
            verify([bound])
        except ValueError:
            pass
        else:
            raise AssertionError("changed source accepted")
    return {"status": "passed", "immutable_write_and_hash_check": True,
            "market_arrays_read": 0, "model_calls": 0, "account_executions": 0}


def pilot_self_check():
    """Generated fixture checks; never calls the gateway or reads market files."""
    import numpy as np
    from quanta_agents.meta_v7.benchmarks import KINDS, make_synthetic_task
    results = []
    for seed in PILOT_SEEDS:
        tasks = [make_synthetic_task(seed, kind, train_sessions=40, validation_sessions=80, stocks=32) for kind in KINDS]
        evidence = [_training_evidence(task) for task in tasks]
        for task, row in zip(tasks, evidence):
            assert len(row["dates"]) == 40 and len(row["candidate_daily_ic"]) == 10
            assert len(row["factor_pair_daily_rank_correlation"]) == 6
            assert all(len(values) == 40 and values[-2:] == [None, None] for values in row["candidate_daily_ic"].values())
            for values in row["candidate_daily_ic"].values():
                summary = _aggregate_values(values)
                good = [value for value in values if value is not None]
                assert summary["observed_dates"] == len(good)
                assert summary["mean"] == round(float(np.mean(good)), 6)
            # Future arrays are deliberately corrupted; the entire training report stays exact.
            task["validation_labels"].iloc[:, :] = 1e99
            for frame in task["frames"].values():
                frame.loc[frame.index > task["train_labels"].index[-1]] = np.nan
            assert _training_evidence(task) == row
        sizes = {}
        for mode in PILOT_MODES:
            prompt = _pilot_prompt(evidence, mode)
            sizes[mode] = len(prompt.encode("utf-8"))
            assert sizes[mode] <= 24000
            assert all(word not in prompt for word in ("generation_config", "discovery_groups", "hidden_kind", "validation_labels"))
        ids = [row["task_id"] for row in evidence]
        candidates = list(evidence[0]["candidate_daily_ic"])
        response = {"tasks": [{"task_id": key, "discoveries": [], "reason": "abstain"} for key in ids], "limitations": "generated"}
        _check_pilot_response(response, ids, candidates)
        response["tasks"][0]["discoveries"] = [{"candidate_id": "not_in_menu", "direction": 1}]
        try:
            _check_pilot_response(response, ids, candidates)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown candidate was admitted")
        results.append({"seed": seed, "context_bytes": sizes, "same_training_numbers": True,
            "future_perturbation_invariance": True, "invalid_discovery_rejected": True})
    return {"status": "passed", "checks": results, "model_calls": 0, "real_market_arrays": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify", "numeric", "pilot", "self-check", "pilot-self-check", "_numeric-worker"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--old-root", type=Path, default=OLD_ROOT)
    parser.add_argument("--old-model-acceptance", type=Path, default=OLD_MODEL_ACCEPTANCE)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--pilot-protocol", type=Path)
    parser.add_argument("--calendar-root", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--intent-sha256", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.phase == "self-check":
        result = self_check()
    elif args.phase == "pilot-self-check":
        result = pilot_self_check()
    else:
        if args.output is None:
            parser.error("--output is required")
        if args.phase == "_numeric-worker":
            return numeric_worker(args)
        if args.phase == "pilot":
            result = pilot(args.output)
        elif args.phase == "numeric":
            result = numeric(args.output, old_root=args.old_root, timeout_seconds=args.timeout_seconds)
        elif args.phase == "prepare":
            result = prepare(args.output, old_root=args.old_root, old_model_acceptance=args.old_model_acceptance,
                             catalog=args.catalog, pilot_protocol=args.pilot_protocol, calendar_root=args.calendar_root)
        else:
            output, intent, receipt = verify_preparation(args.output)
            result = {"status": "verified", "output": str(output), "intent_sha256": receipt["intent_sha256"],
                      "model_calls": 0, "account_executions": 0}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
