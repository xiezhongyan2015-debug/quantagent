"""Prepared-first R2 frozen stock projection and strict common-cohort research.

This separate namespace never modifies the submitted R2 eight-source seal,
original CSVs, training choices, or either freeze. Execute is possible only with
a root-pinned actual final R2 freeze and all thirty diagnostics/five refits.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from itertools import groupby
import json
import math
from pathlib import Path
import time

from gru_research.r2_research.protocol import (ARMS, FOLDS, SEEDS, PROTOCOL_SHA256,
    final_budget, plans, require, require_completed_diagnostics, sha)
from gru_research.r2_research.causal_gate import source_seal

COMPARISON_PROTOCOL_SHA256 = "b87a29808a478303472809968a59a07a4d409e00dca32de9ab1a8b58ff2b254b"
RUNTIME_SEAL_SHA256 = "8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809"
BASE_COLUMNS = ["symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag"]
EXPORT_COLUMNS = BASE_COLUMNS + ["y4", "label_end_time", "label_mature"]
NEW_SOURCES = ("gru_research/r2_comparison/__init__.py", "gru_research/r2_comparison/tool.py",
               "gru_research/r2_comparison/protocol_v1.json")


def digest(path):
    h = __import__("hashlib").sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def read(path, expected=None):
    p = Path(path)
    require(p.is_file() and p.stat().st_size < 64 * 1024 * 1024, "required actual small original missing/too large: " + str(p))
    raw = p.read_bytes()
    require(expected is None or sha(raw) == expected, "actual original bytes differ: " + str(p))
    return json.loads(raw)


def publish(path, doc):
    with Path(path).open("x") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def deadline_check(deadline):
    require(time.monotonic() < deadline, "bounded comparison/projection deadline reached; preserve partial evidence, no automatic retry")


def prepared():
    return {"status": "PREPARED_ACTUAL_R2_FINAL_RESULTS_MISSING", "comparison_protocol_sha256": COMPARISON_PROTOCOL_SHA256,
            "r2_required_diagnostic_fits": 30, "r2_required_final_refits": 5,
            "r2_existing_stock_scores_saved_by_runner": False,
            "new_readonly_five_fixed_last_weights_projection_required": True,
            "new_fits": 0, "actual_comparison_executed": False, "source_or_score_files_read": False,
            "strict_group": "shared time × symbol × identical actually mature y4",
            "paired_block_days": 20, "bootstrap_repetitions": 2000, "virgin_OOS_claim": False,
            "model_selection_or_training_feedback": False, "IC07_guaranteed": False}


def gate(args):
    """No Torch, array load or score parsing precedes the complete actual gate."""
    root, run = args.root.resolve(), args.r2_run.resolve()
    require(Path(__file__).resolve() == root / "gru_research/r2_comparison/tool.py", "executed tool differs from explicitly guarded source root")
    p = read(root / "gru_research/r2_comparison/protocol_v1.json", COMPARISON_PROTOCOL_SHA256)
    new = read(args.source_manifest, args.expected_source_manifest_sha256)
    require(new.get("protocol_sha256") == COMPARISON_PROTOCOL_SHA256
            and set(new.get("files_sha256", {})) == set(NEW_SOURCES), "comparison source seal identity/set differs")
    require({n: digest(root / n) for n in NEW_SOURCES} == new["files_sha256"], "comparison source changed")
    source_seal(root, args.r2_source_manifest, RUNTIME_SEAL_SHA256)
    old_protocol = read(root / "gru_research/r2_research/protocol_v1.json", PROTOCOL_SHA256)
    require({n: digest(root / n) for n in old_protocol["original_dataset"]["original_source19_sha256"]}
            == old_protocol["original_dataset"]["original_source19_sha256"], "original frozen19 source changed")
    meta = read(args.dataset / "meta.json", old_protocol["original_dataset"]["meta_sha256"])
    require(sha(canonical(meta)) == p["r2_binding"]["dataset_fingerprint"], "original panel metadata changed")
    identity = read(run / "run_identity.json")
    require(identity == p["r2_binding"], "actual R2 run identity differs")
    frozen = read(run / "freeze.json", args.expected_r2_freeze_sha256)
    require(all(frozen.get(k) == v for k, v in identity.items()), "actual R2 freeze binding differs")
    require(frozen.get("checkpoint_role") == "fixed_last_epoch" and frozen.get("seeds") == list(SEEDS)
            and frozen.get("variant") == "pv" and frozen.get("training_label") == 3
            and isinstance(frozen.get("frozen_at_utc"), str), "R2 five fixed-last freeze required")
    core = {k: v for k, v in frozen.items() if k not in ("freeze_id", "frozen_at_utc")}
    require(frozen["freeze_id"] == sha(canonical(core))[:24], "R2 freeze content ID differs")
    selected = read(run / "selection.json", frozen["selection_sha256"])
    budget = read(run / "budget.json", frozen["budget_sha256"])
    arm = selected.get("selected_arm")
    require(arm in ARMS and selected.get("selected_learning_rate") == ARMS[arm]
            and all(selected.get(k) == v for k, v in identity.items()), "existing LR choice identity differs")
    config = read(root / "gru_research/config.json")
    measured = {"freeze.json": digest(run / "freeze.json"), "selection.json": digest(run / "selection.json"),
                "budget.json": digest(run / "budget.json"), "run_identity.json": digest(run / "run_identity.json")}
    inner, outer = [], []
    for plan in plans():
        base = run / "diagnostic" / plan["fold"] / plan["arm"] / ("seed_%s" % plan["seed"])
        key = base.relative_to(run).as_posix()
        result_key, outer_key = key + "/result.json", key + "/outer_result.json"
        result = read(base / "result.json", selected["diagnostic_evidence_sha256"].get(result_key))
        observation = read(base / "outer_result.json", selected["diagnostic_evidence_sha256"].get(outer_key))
        require(result_key in selected["diagnostic_evidence_sha256"] and outer_key in selected["diagnostic_evidence_sha256"], "selector source record missing")
        fold = next(f for f in FOLDS if f["id"] == plan["fold"])
        expected_config = {**config, "learning_rate": ARMS[plan["arm"]]}
        sig = {**identity, "config": expected_config, "seed": plan["seed"], "label": 3, "variant": "pv",
               "stage": "diagnostic", "arm": plan["arm"], "fold": plan["fold"],
               "fit_period": list(fold["fit"]), "inner_period": list(fold["inner"])}
        require(result.get("signature") == sig and all(result.get(k) == v for k, v in sig.items()), "actual diagnostic signature differs")
        require(all(observation.get(k) == v for k, v in identity.items())
                and all(observation.get(k) == v for k, v in plan.items()), "actual outer protocol/data/source/identity differs")
        require(observation["inner_result_sha256"] == digest(base / "result.json")
                and observation["checkpoint_sha256"] == result["best_inner_checkpoint_sha256"], "outer is not bound to actual inner checkpoint")
        checks = {"epochs.jsonl": result["epoch_log_sha256"], "best_inner.pt": result["best_inner_checkpoint_sha256"],
                  "best_inner_sections.json": result["inner_records_sha256"], "best_inner_summary.json": result["inner_summary_sha256"],
                  "outer_attempt.json": observation["outer_attempt_sha256"]}
        for name, expected in checks.items():
            require(digest(base / name) == expected, "actual diagnostic evidence changed")
            measured[key + "/" + name] = expected
        epochs = [json.loads(x) for x in (base / "epochs.jsonl").read_text().splitlines()]
        require([e["epoch"] for e in epochs] == list(range(1, len(epochs) + 1))
                and len(epochs) == result["epochs_completed"] and 1 <= len(epochs) <= 40
                and type(result["best_inner_epoch"]) is int and 1 <= result["best_inner_epoch"] <= len(epochs), "actual diagnostic epochs incomplete")
        require(epochs[result["best_inner_epoch"] - 1]["inner_y4_IC"] == result["best_inner_IC"], "actual best inner epoch/metric differs")
        best_ic, best_epoch, bad = -math.inf, 0, 0
        for epoch in epochs:
            require(epoch["phase"] == "r2_diagnostic" and all(epoch[k] == v for k, v in plan.items())
                    and type(epoch["inner_y4_IC"]) in (int, float) and math.isfinite(epoch["inner_y4_IC"]), "actual diagnostic epoch role/nonfinite metric differs")
            if epoch["inner_y4_IC"] > best_ic:
                best_ic, best_epoch, bad = epoch["inner_y4_IC"], epoch["epoch"], 0
            else:
                bad += 1
            require(epoch["best_inner_IC"] == best_ic and epoch["best_inner_epoch"] == best_epoch
                    and epoch["bad_epochs"] == bad, "actual inner checkpoint/early-stop trace inconsistent")
        require(result["best_inner_epoch"] == best_epoch and result["best_inner_IC"] == best_ic
                and (len(epochs) == 40 or bad == 8), "diagnostic completion lacks real max-budget/early-stop boundary")
        attempt = read(base / "outer_attempt.json")
        require(all(attempt.get(k) == observation.get(k) for k in (*identity, *plan, "checkpoint_sha256", "inner_result_sha256")), "outer once-attempt identity differs")
        inner.append(result); outer.append(observation)
        measured[result_key], measured[outer_key] = digest(base / "result.json"), digest(base / "outer_result.json")
    require(set(selected["diagnostic_evidence_sha256"]) == {f"diagnostic/{q['fold']}/{q['arm']}/seed_{q['seed']}/{n}" for q in plans() for n in ("result.json", "outer_result.json")}, "exact sixty diagnostic decision inputs required")
    require_completed_diagnostics(inner, outer)
    derived = {**identity, **final_budget(inner, arm), "selection_sha256": frozen["selection_sha256"]}
    require(budget == derived, "actual final budget not median fifteen chosen inner best epochs")
    original_run = Path(args.r2_original_run_path)
    original_paths = [str(original_run / "final" / ("seed_%s" % s) / "last.pt") for s in SEEDS]
    require(set(frozen["checkpoint_hashes"]) == set(original_paths), "original-to-native final path identity is not explicit/exact")
    final_paths = []
    for seed, original in zip(SEEDS, original_paths):
        base = run / "final" / ("seed_%s" % seed)
        sig = {**identity, "config": {**config, "learning_rate": ARMS[arm]}, "seed": seed, "label": 3, "variant": "pv",
               "stage": "final", "arm": arm, "fit_period": ["2019-01-01", "2024-01-01"],
               "fixed_epoch_budget": budget["epochs"], "checkpoint_role": "fixed_last_epoch",
               "selection_sha256": frozen["selection_sha256"], "budget_sha256": frozen["budget_sha256"]}
        result = read(base / "result.json")
        require(result.get("complete") is True and result.get("signature") == sig
                and result.get("epochs_completed") == budget["epochs"], "actual final refit incomplete/wrong identity")
        require(result["last_checkpoint_sha256"] == frozen["checkpoint_hashes"][original]
                and digest(base / "last.pt") == result["last_checkpoint_sha256"]
                and digest(base / "epochs.jsonl") == result["epoch_log_sha256"], "actual final evidence differs")
        epochs = [json.loads(x) for x in (base / "epochs.jsonl").read_text().splitlines()]
        require([e["epoch"] for e in epochs] == list(range(1, budget["epochs"] + 1))
                and all(e.get("validation_read") is False and e["learning_rate"] == ARMS[arm]
                        and e["phase"] == "r2_final" and e["seed"] == seed and e["arm"] == arm for e in epochs), "final budget/constant LR/validation isolation differs")
        for name in ("result.json", "epochs.jsonl", "last.pt"):
            measured["final/seed_%s/%s" % (seed, name)] = digest(base / name)
        final_paths.append(base / "last.pt")
    sections = {}
    for period in ("validation_2024", "historical_2025", "recent_2026"):
        base = run / "research_evaluation" / period
        summary = read(base / "summary.json")
        require(summary.get("complete") is True and summary["freeze_id"] == frozen["freeze_id"]
                and all(summary.get(k) == v for k, v in identity.items()), "actual once-evaluated research report missing/different")
        doc = read(base / "sections.json", summary["outputs_sha256"]["sections.json"])
        attempt = read(base / "attempt.json")
        require(attempt.get("freeze_id") == frozen["freeze_id"] and attempt.get("period") == period, "research evaluation once-attempt missing/different")
        for name in ("summary.json", "sections.json", "attempt.json"):
            measured["research_evaluation/%s/%s" % (period, name)] = digest(base / name)
        sections[period] = doc
    return p, frozen, final_paths, sections, measured, meta


def strict_common(left, right, labels_left, labels_right, min_stocks=100):
    """Recompute both metrics on one symbol set; separate cohort ICs cannot pair."""
    import numpy as np
    from gru_research.evaluate import correlation, ranks
    common = sorted(set(left) & set(right) & set(labels_left) & set(labels_right))
    common = [s for s in common if all(math.isfinite(float(x)) for x in
                                     (left[s], right[s], labels_left[s], labels_right[s]))]
    require(all(labels_left[s] == labels_right[s] for s in common), "shared symbols have different y4 labels")
    out = {"common_symbols": common, "common_stock_count": len(common), "valid_pair": False,
           "r1_ic": None, "r2_ic": None, "r1_rank_ic": None, "r2_rank_ic": None}
    if len(common) < min_stocks:
        return {**out, "reason": "common_group_below100"}
    a, b, y = [np.asarray([v[s] for s in common], dtype=np.float64) for v in (left, right, labels_left)]
    values = [correlation(a, y, min_stocks), correlation(b, y, min_stocks),
              correlation(ranks(a), ranks(y), min_stocks), correlation(ranks(b), ranks(y), min_stocks)]
    if not all(math.isfinite(v) for v in values):
        return {**out, "reason": "constant_or_nonfinite_common_score_or_label"}
    return {**out, "valid_pair": True, "reason": "valid_exact_common_group",
            **dict(zip(("r1_ic", "r2_ic", "r1_rank_ic", "r2_rank_ic"), values))}


def paired_ci(period_records, metric):
    """Stratify pooled by year; preserve all observed within-day pairs."""
    import numpy as np
    rng = np.random.default_rng(20261002)
    strata, total_sums, total_count = [], 0.0, 0
    for records in period_records:
        daily = {}
        for r in records:
            if r["valid_pair"]:
                daily.setdefault(r["date"], []).append(r["r2_" + metric] - r["r1_" + metric])
        dates = sorted(daily)
        if len(dates) < 20:
            return {"valid": False, "reason": "fewer than20 common trading dates in a year", "stock_group_is_identical": True}
        sums, counts = np.array([sum(daily[d]) for d in dates]), np.array([len(daily[d]) for d in dates])
        strata.append((sums, counts)); total_sums += sums.sum(); total_count += counts.sum()
    estimates = []
    for _ in range(2000):
        numerator, denominator = 0.0, 0
        for sums, counts in strata:
            n = len(sums)
            starts = rng.integers(0, n - 20 + 1, size=int(np.ceil(n / 20)))
            picked = np.concatenate([np.arange(s, s + 20) for s in starts])[:n]
            numerator += sums[picked].sum(); denominator += counts[picked].sum()
        estimates.append(numerator / denominator)
    return {"valid": True, "mean_difference_R2_minus_R1": float(total_sums / total_count),
            "ci95": [float(x) for x in np.quantile(estimates, [.025, .975])], "paired_sections": int(total_count),
            "block_days": 20, "repetitions": 2000, "seed": 20261002, "year_strata": len(strata),
            "select_winner_or_training_feedback": False}


def match_metric(actual, expected, tolerance):
    if type(actual) in (int, float) and not math.isfinite(actual):
        actual = None  # The frozen writer serialized invalid float metrics as null.
    require((actual is None and expected is None) or
            (type(actual) in (int, float) and type(expected) in (int, float)
             and math.isfinite(actual) and math.isfinite(expected) and abs(actual - expected) <= tolerance),
            "readonly R2 projection did not reproduce actual saved section metric")


def export(args, contract, frozen, paths, originals, measured, meta, deadline):
    import numpy as np
    import torch
    from gru_research.train import PanelDataset, checkpoint_load, seed_everything
    from gru_research.model import PlanGRU
    from gru_research.evaluate import breakdown, correlation, ranks
    from gru_research.r2_research.folds import period_indices
    require(Path(seed_everything.__code__.co_filename).resolve() == args.root.resolve() / "gru_research/train.py",
            "backend initializer is not the guarded original train source")
    seed_everything(11)
    backend = {"cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
               "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
               "cuda_matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
               "cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32)}
    require(backend == {"cudnn_benchmark": False, "cudnn_deterministic": True,
                        "cuda_matmul_allow_tf32": False, "cudnn_allow_tf32": False},
            "actual CPU/CUDA projection backend differs from original seeded policy")
    dataset = PanelDataset(args.dataset)
    config = read(args.root / "gru_research/config.json")
    models = []
    for seed, path in zip(SEEDS, paths):
        deadline_check(deadline)
        state = checkpoint_load(path, args.device)
        require(state["signature"] == read(path.parent / "result.json")["signature"]
                and state["epoch"] + 1 == state["signature"]["fixed_epoch_budget"], "frozen last checkpoint internal signature/epoch differs")
        require(state["groups"] == dataset.groups and state["n_industries"] == dataset.n_industries
                and len(state["model"]) == 26, "checkpoint feature/industry/tensor shape identity differs")
        require(all(x.dtype == torch.float32 and torch.isfinite(x).all().item() for x in state["model"].values()), "nonfinite/nonFP32 final model tensor")
        model = PlanGRU(state["groups"], state["n_industries"], "pv").to(args.device)
        model.load_state_dict(state["model"], strict=True)
        require(sum(v.numel() for v in model.parameters()) == 202481, "PLAN parameter count differs")
        model.eval(); models.append(model)
    exports = {}
    for period in contract["periods"]:
        expected = {r["signal_time_ns"]: r for r in originals[period]["records"]}
        require(len(expected) == len(originals[period]["records"]), "duplicate saved R2 timestamp")
        interval = read(args.root / "gru_research/r2_research/protocol_v1.json")["research_evaluation_after_new_freeze"]["periods"][period]
        ix, boundary = period_indices(dataset, *interval, config)
        require(set(expected) == set(int(dataset.times[t]) for t in ix), "saved R2 population differs from its real causal/purge rule")
        seed_maps = {str(s): {r["signal_time_ns"]: r for r in originals[period]["seed_records"][str(s)]} for s in SEEDS}
        require(all(set(v) == set(expected) and len(v) == len(originals[period]["seed_records"][s]) for s, v in seed_maps.items()), "actual five seed-section populations missing/duplicated")
        csv_path = args.output / (period + "_r2_stock_scores.csv")
        records, max_error = [], 0.0
        with csv_path.open("x", newline="") as stream:
            writer = csv.writer(stream); writer.writerow(EXPORT_COLUMNS)
            with torch.inference_mode():
                for t in ix:
                    deadline_check(deadline); t = int(t)
                    mask = dataset.eligible_mask(t, 80, False)
                    x, industry, slots, _, _ = dataset.batch([t], args.device, config, False)
                    seed_scores = []
                    for model in models:
                        s = model(x, industry, slots).cpu().numpy().astype(np.float64)
                        require(np.isfinite(s).all(), "nonfinite readonly projection score")
                        seed_scores.append((s - s.mean()) / s.std() if s.std() > 0 else np.zeros_like(s))
                    scores = np.mean(seed_scores, axis=0)
                    y = np.asarray(dataset.labels[t, mask, 3]); signal, entry, end = int(dataset.times[t]), int(dataset.earliest_execution_times[t]), int(dataset.label_end_times[t, 3])
                    mature = end > entry and end <= int(dataset.times[-1])
                    finite = np.isfinite(y) & mature
                    ic = correlation(scores[finite], y[finite]); rank_ic = correlation(ranks(scores[finite]), ranks(y[finite]))
                    current = {"signal_time_ns": signal, "date": str(np.datetime64(int(dataset.days[t]), "D")), "slot": int(dataset.slots[t]),
                               "ic": ic if math.isfinite(ic) else None, "rank_ic": rank_ic if math.isfinite(rank_ic) else None,
                               "pool_count": int(dataset.members[t].sum()), "scored_count": int(mask.sum()), "mature_label_count": int(finite.sum()), "label_mature": bool(mature)}
                    original = expected[signal]
                    require(all(current[k] == original[k] for k in ("date", "slot", "pool_count", "scored_count", "mature_label_count", "label_mature")), "readonly R2 population/maturity changed")
                    for field in ("ic", "rank_ic"):
                        match_metric(current[field], original[field], contract["reproduction_metric_absolute_tolerance"])
                        if current[field] is not None: max_error = max(max_error, abs(current[field] - original[field]))
                    for seed, s in zip(SEEDS, seed_scores):
                        ref = seed_maps[str(seed)][signal]
                        require(all(ref[k] == original[k] for k in ("pool_count", "scored_count", "mature_label_count")), "seed-section original population differs")
                        match_metric(correlation(s[finite], y[finite]), ref["ic"], contract["reproduction_metric_absolute_tolerance"])
                        match_metric(correlation(ranks(s[finite]), ranks(y[finite])), ref["rank_ic"], contract["reproduction_metric_absolute_tolerance"])
                    covered = np.flatnonzero(mask); by_symbol = {int(u): j for j, u in enumerate(covered)}
                    percentile = (ranks(scores) - .5) / len(scores)
                    window_missing = (~torch.isfinite(x)).any(dim=2).any(dim=1).cpu().numpy()
                    for u in np.flatnonzero(dataset.members[t]):
                        j = by_symbol.get(int(u)); target = float(dataset.labels[t, u, 3]); lm = mature and math.isfinite(target)
                        flags = (["feature_missing"] if j is not None and window_missing[j] else []) + (["industry_unknown"] if dataset.industry[t, u] == 0 else [])
                        if j is None: flags.append("incomplete_hour_window")
                        if not lm: flags.append("label_unmatured_or_missing")
                        writer.writerow([dataset.symbols[u], signal, entry, "" if j is None else float(scores[j]),
                            "" if j is None else float(percentile[j]), frozen["freeze_id"] + ":pv", "|".join(flags) or "ok",
                            target if lm else "", end, "true" if lm else "false"])
                    records.append(current)
                    deadline_check(deadline)
        sections = args.output / (period + "_projection_sections.json")
        publish(sections, {"records": records, "max_metric_absolute_reproduction_error": max_error,
                           "allowed_FP32_cross_device_tolerance": contract["reproduction_metric_absolute_tolerance"]})
        exports[period] = {"csv": csv_path.name, "csv_sha256": digest(csv_path), "sections": sections.name,
                           "sections_sha256": digest(sections), "source_saved_R2_sections_sha256": measured["research_evaluation/%s/sections.json" % period],
                           "original_R2_cohort_sections": len(records), "boundary": boundary, "reproduction_population_exact": True,
                           "max_metric_absolute_reproduction_error": max_error}
    return {"status": "ACTUAL_READONLY_R2_FIRST_STOCK_PROJECTION_COMPLETE", "r2_freeze_id": frozen["freeze_id"],
            "r2_freeze_sha256": args.expected_r2_freeze_sha256, "comparison_protocol_sha256": COMPARISON_PROTOCOL_SHA256,
            "exports": exports, "actual_source_final_originals": measured, "device": args.device,
            "actual_backend_settings": backend, "backend_initializer": "guarded_original_train.seed_everything(11)",
            "checkpoint_role": "fixed_last_epoch", "additional_fits": 0, "virgin_OOS_claim": False}


def csv_groups(path, columns):
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream); require(reader.fieldnames == columns, "actual score CSV interface differs")
        previous = -1
        for key, rows in groupby(reader, lambda r: int(r["signal_time"])):
            require(key > previous, "repeated/unordered score timestamp"); previous = key
            result = {}
            for row in rows:
                require(row["symbol"] not in result, "duplicate symbol in score group")
                result[row["symbol"]] = row
            yield key, result


def compare(args, contract, frozen, paths, originals, measured, meta, deadline):
    import numpy as np
    from gru_research.evaluate import breakdown
    manifest = read(args.export_directory / "manifest.json", args.expected_export_manifest_sha256)
    require(manifest.get("status") == "ACTUAL_READONLY_R2_FIRST_STOCK_PROJECTION_COMPLETE"
            and manifest["r2_freeze_id"] == frozen["freeze_id"] and manifest["r2_freeze_sha256"] == args.expected_r2_freeze_sha256
            and manifest["actual_source_final_originals"] == measured
            and manifest["comparison_protocol_sha256"] == COMPARISON_PROTOCOL_SHA256, "real R2 stock projection binding differs")
    old_freeze = read(args.r1_formal / "freeze.json", contract["r1_freeze_sha256"])
    old_manifest = read(args.r1_export_manifest, contract["r1_export_manifest_sha256"])
    require(old_freeze["freeze_id"] == contract["r1_freeze_id"] and old_manifest["freeze_id"] == old_freeze["freeze_id"]
            and old_manifest["status"] == "complete_export" and len(old_manifest["reports"]) == 18, "original frozen nine-model/18-export envelope differs")
    require({p: old_freeze["checkpoint_hashes"][p] for p in old_freeze["evaluation_models"]["pv"]}
            == contract["r1_pv_checkpoint_hashes"], "original PV five checkpoint identity differs")
    inputs = {str(args.r1_formal / "freeze.json"): contract["r1_freeze_sha256"],
              str(args.r1_export_manifest): contract["r1_export_manifest_sha256"],
              str(args.export_directory / "manifest.json"): args.expected_export_manifest_sha256}
    for name, h in contract["r1_export_receipts_sha256"].items():
        require(digest(args.r1_formal / name) == h, "original eighteen export receipts changed")
        inputs[str(args.r1_formal / name)] = h
    # Only the actual comparison label/clock/pool materials are rehashed here.
    # X and all other large fields inherit the original signed byte evidence.
    arrays = {}
    for name in ("labels", "label_end_times", "times", "earliest_execution_times", "members", "hour_complete"):
        deadline_check(deadline)
        require(digest(args.dataset / (name + ".npy")) == meta["files_sha256"][name + ".npy"], "actual same-group label/clock/pool material changed")
        arrays[name] = np.load(args.dataset / (name + ".npy"), mmap_mode="r", allow_pickle=False)
        inputs[str(args.dataset / (name + ".npy"))] = meta["files_sha256"][name + ".npy"]
    require(digest(args.dataset / "symbols.json") == meta["files_sha256"]["symbols.json"], "actual symbol identity changed")
    symbols = read(args.dataset / "symbols.json"); positions = {s: i for i, s in enumerate(symbols)}
    inputs[str(args.dataset / "symbols.json")] = meta["files_sha256"]["symbols.json"]
    times = {int(t): i for i, t in enumerate(arrays["times"])}
    incomplete = np.r_[np.zeros((1, len(symbols)), np.int32), np.cumsum(~arrays["hour_complete"], axis=0, dtype=np.int32)]
    report, all_records = {}, []
    for r2period, r1period in contract["periods"].items():
        deadline_check(deadline)
        receipt = read(args.r1_formal / "corrected_score_exports" / r1period / "pv_scores.receipt.json")
        require(old_manifest["reports"][r1period + "/pv"]["corrected_sha256"] == receipt["corrected_sha256"]
                and receipt["model_version"] == old_freeze["freeze_id"] + ":pv", "original PV model/version mapping differs")
        left_path = args.r1_formal / "corrected_score_exports" / r1period / "pv_scores.csv"
        right_spec = manifest["exports"][r2period]; right_path = args.export_directory / right_spec["csv"]
        require(Path(right_spec["csv"]).name == right_spec["csv"]
                and right_spec["csv"] == r2period + "_r2_stock_scores.csv", "R2 projection path is not the declared closed file")
        require(digest(left_path) == receipt["corrected_sha256"] and digest(right_path) == right_spec["csv_sha256"], "actual score bytes differ")
        inputs[str(left_path)], inputs[str(right_path)] = receipt["corrected_sha256"], right_spec["csv_sha256"]
        old_sections = {r["signal_time_ns"]: r for r in receipt["sections"]}
        r2_sections = {r["signal_time_ns"]: r for r in originals[r2period]["records"]}
        require(set(r2_sections) <= set(old_sections), "R2 timestamp outside original frozen comparison period")
        right_iterator = iter(csv_groups(right_path, EXPORT_COLUMNS)); pending = next(right_iterator, None)
        records, left_seen, right_seen, total_common, excluded_times = [], set(), set(), 0, {}
        totals = {"R1_full_calendar_pool_stock_hours": 0, "R1_original_scored_stock_hours": 0,
                  "R2_original_time_cohort_pool_stock_hours": 0, "R2_original_scored_stock_hours": 0,
                  "removed_R1_stock_hours_R2_time_exclusion": 0, "removed_common_time_missing_R1_score": 0,
                  "removed_common_time_missing_R2_score": 0, "removed_common_time_unmatured_or_missing_y4": 0}
        for signal, left_rows in csv_groups(left_path, BASE_COLUMNS):
            deadline_check(deadline); require(signal in old_sections and signal not in left_seen, "original PV section outside pinned export")
            left_seen.add(signal); t = times[signal]
            pool = {symbols[u] for u in np.flatnonzero(arrays["members"][t])}
            require(set(left_rows) == pool, "original full pool population differs from actual historical members")
            ref = old_sections[signal]; totals["R1_full_calendar_pool_stock_hours"] += len(pool)
            require(len(pool) == ref["pool_count"], "original receipt pool differs")
            right_rows = pending[1] if pending is not None and pending[0] == signal else {}
            require(pending is None or pending[0] >= signal, "R2 CSV contains unexpected timestamp")
            require(bool(right_rows) == (signal in r2_sections), "R2 score timestamp presence differs from saved original R2 cohort")
            if right_rows:
                require(set(right_rows) == pool, "R2 original time cohort pool differs")
                right_seen.add(signal); pending = next(right_iterator, None)
                totals["R2_original_time_cohort_pool_stock_hours"] += len(pool)
            complete = incomplete[t + 1] == incomplete[t + 1 - 80] if t >= 79 else np.zeros(len(symbols), bool)
            left_scores, right_scores, targets = {}, {}, {}
            end = int(arrays["label_end_times"][t, 3]); entry = int(arrays["earliest_execution_times"][t]); mature = end > entry and end <= int(arrays["times"][-1])
            for side, rows, version, result in (("R1", left_rows, receipt["model_version"], left_scores),
                                               ("R2", right_rows, frozen["freeze_id"] + ":pv", right_scores)):
                for s, row in rows.items():
                    require(int(row["earliest_execution_time"]) == entry and row["model_version"] == version,
                            "actual forecast clock/model version differs")
                    if row["score"]:
                        require(complete[positions[s]] and math.isfinite(float(row["score"])), "invalid finite-window score")
                        result[s] = float(row["score"])
                    if side == "R2":
                        target = float(arrays["labels"][t, positions[s], 3]); lm = mature and math.isfinite(target)
                        require(int(row["label_end_time"]) == end and row["label_mature"] == ("true" if lm else "false")
                                and (float(row["y4"]) == target if lm else row["y4"] == ""), "R2 exported y4 is not the identical actually mature label")
            require(len(left_scores) == ref["scored_count"], "original scored population differs from receipt")
            totals["R1_original_scored_stock_hours"] += len(left_scores)
            if not right_rows:
                totals["removed_R1_stock_hours_R2_time_exclusion"] += len(left_scores)
                boundary = right_spec["boundary"]
                ends = arrays["label_end_times"][t]
                if ref["date"] >= boundary["embargo_cutoff_day"]:
                    reason = "R2_two_complete_trading_day_boundary_embargo"
                elif not ((ends > entry).all() and (np.diff(ends) > 0).all()):
                    reason = "R2_invalid_actual_four_label_clock"
                elif (ends >= boundary["all_actual_label_end_times_strictly_before_ns"]).any():
                    reason = "R2_actual_four_label_end_purge"
                elif ends[3] > arrays["times"][-1]:
                    reason = "R2_not_mature_by_available_market_end"
                else:
                    valid = complete & arrays["members"][t] & np.isfinite(arrays["labels"][t]).all(axis=1)
                    if valid.sum() < 100:
                        reason = "R2_below100_full_window_four_label_eligible_members"
                    elif (np.var(np.asarray(arrays["labels"][t, valid], dtype=np.float64), axis=0) <= 1e-12).any():
                        reason = "R2_four_label_variance_floor"
                    else:
                        raise ValueError("missing R2 timestamp has no legitimate fixed protocol exclusion")
                excluded_times[reason] = excluded_times.get(reason, 0) + 1
                records.append({"signal_time_ns": signal, "date": ref["date"], "slot": ref["slot"],
                                "valid_pair": False, "common_stock_count": 0, "reason": reason})
                continue
            require(len(right_scores) == r2_sections[signal]["scored_count"], "R2 scored population differs from original saved section")
            totals["R2_original_scored_stock_hours"] += len(right_scores)
            for s in pool:
                y = float(arrays["labels"][t, positions[s], 3])
                if mature and math.isfinite(y): targets[s] = y
            # These exclusion counts are intentionally non-disjoint; joint
            # common count supplies the exact denominator and cannot be summed.
            totals["removed_common_time_missing_R1_score"] += len(pool - set(left_scores))
            totals["removed_common_time_missing_R2_score"] += len(pool - set(right_scores))
            totals["removed_common_time_unmatured_or_missing_y4"] += len(pool - set(targets))
            current = strict_common(left_scores, right_scores, targets, targets)
            common_sha = sha(canonical([[signal, s, targets[s]] for s in current.pop("common_symbols")]))
            total_common += current["common_stock_count"]
            records.append({**current, "signal_time_ns": signal, "date": ref["date"], "slot": ref["slot"],
                            "common_time_symbol_y4_identity_sha256": common_sha,
                            "original_R1_scored_count": len(left_scores), "original_R2_scored_count": len(right_scores)})
        require(pending is None and left_seen == set(old_sections) and right_seen == set(r2_sections), "actual score CSV omitted or added whole sections")
        valid = [r for r in records if r["valid_pair"]]
        def metrics_for(side):
            return breakdown([{**r, "ic": r[side + "_ic"], "rank_ic": r[side + "_rank_ic"],
                               "pool_count": r["common_stock_count"], "scored_count": r["common_stock_count"]} for r in valid])
        report[r2period] = {"R1_common": metrics_for("r1"), "R2_common": metrics_for("r2"),
            "paired_ic": paired_ci([records], "ic"), "paired_rank_ic": paired_ci([records], "rank_ic"),
            "population": {**totals, "common_mature_stock_hours": total_common, "original_R1_calendar_sections": len(old_sections),
                "original_R2_time_sections": len(r2_sections), "common_valid_sections": len(valid), "removal_counts_overlap": True},
            "excluded_original_timestamp_reasons": excluded_times,
            "records": records, "actual_R1_score_sha256": receipt["corrected_sha256"], "actual_R2_score_sha256": right_spec["csv_sha256"]}
        all_records.append(records)
    def pooled_metrics(side):
        return breakdown([{**r, "ic": r[side + "_ic"], "rank_ic": r[side + "_rank_ic"],
            "pool_count": r["common_stock_count"], "scored_count": r["common_stock_count"]}
            for records in all_records for r in records if r["valid_pair"]])
    return {"status": "ACTUAL_COMMON_COHORT_EXPOSED_HISTORY_COMPARISON_COMPLETE", "periods": report,
            "pooled_two_years": {"R1_common": pooled_metrics("r1"), "R2_common": pooled_metrics("r2"),
                "paired_ic": paired_ci(all_records, "ic"), "paired_rank_ic": paired_ci(all_records, "rank_ic")},
            "r1_freeze_id": old_freeze["freeze_id"], "r2_freeze_id": frozen["freeze_id"],
            "historical_exposure_declared": True, "virgin_OOS_claim": False, "no_training_selection_threshold_feedback": True,
            "IC07_guaranteed": False, "all_four_slots_retained_by_rule": True, "net_performance_claim": False,
            "comparison_protocol_sha256": COMPARISON_PROTOCOL_SHA256, "r2_export_manifest_sha256": args.expected_export_manifest_sha256,
            "input_files_actual_sha256_before_and_after": inputs, "X_and_full26_input_hashes_inherited_not_rehashed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--action", choices=("export", "compare"), default="export")
    for name in ("root", "dataset", "r2-run", "source-manifest", "r2-source-manifest", "output", "r1-formal", "r1-export-manifest", "export-directory"):
        parser.add_argument("--" + name, type=Path)
    for name in ("expected-source-manifest-sha256", "expected-r2-freeze-sha256", "r2-original-run-path", "expected-export-manifest-sha256"):
        parser.add_argument("--" + name)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(prepared())); return
    required = ("root", "dataset", "r2_run", "source_manifest", "r2_source_manifest", "output",
                "expected_source_manifest_sha256", "expected_r2_freeze_sha256", "r2_original_run_path")
    require(all(getattr(args, n) for n in required), "actual future native result roots and reviewed pins required")
    require(60 <= args.timeout_seconds <= 86400, "explicit bounded1minute..24hour execution required")
    deadline = time.monotonic() + args.timeout_seconds
    if args.action == "compare":
        require(all(getattr(args, n) for n in ("r1_formal", "r1_export_manifest", "export_directory", "expected_export_manifest_sha256")), "actual original PV and R2 projection artifacts required")
    contract, frozen, paths, originals, measured, meta = gate(args)
    require(args.output.resolve() != args.r2_run.resolve() and args.r2_run.resolve() not in args.output.resolve().parents
            and args.output.resolve() != args.dataset.resolve() and args.dataset.resolve() not in args.output.resolve().parents,
            "derived namespace must be separate from original R2 run and dataset")
    if args.r1_formal:
        require(args.r1_formal.resolve() != args.output.resolve() and args.r1_formal.resolve() not in args.output.resolve().parents, "original R1 outputs are read-only")
    args.output.mkdir(parents=True, exist_ok=False)
    publish(args.output / "attempt.json", {"action": args.action, "started_at_utc": now(), "deadline_seconds": args.timeout_seconds,
            "r2_freeze_sha256": args.expected_r2_freeze_sha256, "actual_native_originals_sha256": measured})
    result = export(args, contract, frozen, paths, originals, measured, meta, deadline) if args.action == "export" else compare(args, contract, frozen, paths, originals, measured, meta, deadline)
    deadline_check(deadline)
    require({n: digest(args.r2_run / n) for n in measured} == measured, "native R2 originals changed during read-only derivation")
    source_seal(args.root, args.r2_source_manifest, RUNTIME_SEAL_SHA256)
    _, after_freeze, _, _, after_measured, _ = gate(args)
    require(after_freeze == frozen and after_measured == measured, "small actual source or original metadata/identity changed")
    for path, expected in result.get("input_files_actual_sha256_before_and_after", {}).items():
        deadline_check(deadline)
        require(digest(path) == expected, "actual comparison CSV/label originals changed during derivation")
    result["finished_at_utc"] = now()
    publish(args.output / ("manifest.json" if args.action == "export" else "comparison.json"), result)
    print(json.dumps({"status": result["status"], "output": str(args.output), "new_fits": 0,
                      "receipt_sha256": digest(args.output / ("manifest.json" if args.action == "export" else "comparison.json"))}), flush=True)


if __name__ == "__main__":
    main()
