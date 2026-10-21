"""Read-only contracts for *existing* next-stage outputs, never model selection.

Pure numerical checks support synthetic fixtures without importing Torch, panel
arrays, labels or scoring. CPU checkpoint loading belongs to the collector.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from .evaluate import breakdown
from .experiment_acceptance import audited_epoch_count

SEEDS = [11, 22, 33, 44, 55]
VARIANTS = ("pv", "pv_factors", "pv_fund")
FROZEN_SOURCE_NAMES = {
    "gru_research/" + name for name in (
        "minute_data.py", "slow_features.py", "assemble.py", "model.py", "train.py",
        "evaluate.py", "config.json", "tests/test_minute_data.py", "tests/test_slow_features.py",
        "tests/test_assembly.py", "tests/test_training.py", "experiment_acceptance.py",
        "prospective.py", "postprocess.sbatch", "score_export_audit.py",
        "tests/test_score_export_audit.py", "postprocess.py", "tests/test_postprocess.py",
        "prospective_features.py")
}


class PendingEvidence(RuntimeError):
    """Required existing output has not yet been produced; no completion proof."""


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def near(a, b, tolerance=1e-10):
    require(isinstance(a, (int, float)) and math.isfinite(a), "nonfinite numeric evidence")
    require(isinstance(b, (int, float)) and math.isfinite(b), "nonfinite expected value")
    require(math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance), "numeric evidence mismatch")


def matching(left, right):
    if isinstance(right, dict):
        require(isinstance(left, dict) and set(left) == set(right), "summary keys mismatch")
        for key in right:
            matching(left[key], right[key])
    elif isinstance(right, (float, np.floating)):
        near(left, float(right))
    else:
        require(left == right, "summary evidence mismatch")


def ordered_events(blob, event, field, total, seed):
    require(blob.endswith(b"\n"), "completed trace has partial tail")
    rows = [json.loads(line) for line in blob.splitlines() if line.strip()]
    require(len(rows) == total, "completed trace count mismatch")
    require(all(r.get("event") == event and r.get("seed") == seed for r in rows), "trace event/seed mismatch")
    require([r.get(field) for r in rows] == list(range(1, total + 1)), "duplicate, missing or unordered trace")
    return rows


def signature(result, expected, real_data=False):
    require(result.get("complete") is True, "existing output is not complete")
    require(result.get("signature") == expected, "signature/mode/config/dataset mismatch")
    require(result.get("seed") == expected["seed"], "outer seed mismatch")
    if real_data:
        require(result.get("real_data") is True, "result has no real-data assertion")
        require(result.get("label") == expected["label"] and result.get("variant") == expected["variant"], "outer label/variant mismatch")


def simplex(value):
    a = np.asarray(value, dtype=np.float64)
    require(a.shape == (4,) and np.isfinite(a).all() and np.all(a >= 0), "four finite nonnegative weights required")
    near(float(a.sum()), 1., 1e-6)
    return a


def blo_summary(result, seed, config, fingerprint):
    signature(result, {"dataset_fingerprint": fingerprint, "config": config, "seed": seed, "mode": "blo"})
    require(config["blo_warmup_epochs"] == 3 and config["blo_steps"] == 2000 and config["blo_average_tail"] == 200, "frozen BLO budget changed")
    require((result["warmup_epochs"], result["steps"], result["tail_steps"]) == (3, 2000, 200), "BLO reported budget mismatch")
    return simplex(result["mean_tail_weights"])


def validate_blo_trace(result, warm_blob, steps_blob, seed, config, fingerprint):
    blo_summary(result, seed, config, fingerprint)
    warm = ordered_events(warm_blob, "blo_warmup", "epoch", 3, seed)
    require(all(math.isfinite(r["loss"]) for r in warm), "nonfinite warmup loss")
    steps = ordered_events(steps_blob, "blo_step", "step", 2000, seed)
    nonzero, skipped = 0, 0
    for row in steps:
        simplex(row["weights"])
        grad = np.asarray(row["predictive_hypergradient"], dtype=np.float64)
        require(grad.shape == (4,) and np.isfinite(grad).all(), "predictive gradient is malformed")
        nonzero += int(np.linalg.norm(grad) > 0)
        require(all(math.isfinite(row[k]) for k in ("inner_loss", "outer_y4_loss", "entropy", "hypergradient_norm", "elapsed_seconds")), "nonfinite BLO diagnostics")
        require(row["hypergradient_norm"] >= 0 and row["elapsed_seconds"] > 0, "invalid BLO diagnostic magnitude")
        require(-1e-6 <= row["entropy"] <= math.log(4) + 1e-6, "invalid categorical entropy")
        require(type(row["skipped_sections"]) is int and row["skipped_sections"] >= 0, "invalid skipped count")
        skipped += row["skipped_sections"]
    require(nonzero > 0, "entropy alone is not predictive hypergradient evidence")
    tail = np.asarray([r["weights"] for r in steps[-200:]], dtype=np.float64)
    require(np.allclose(tail.mean(axis=0), result["mean_tail_weights"], rtol=0, atol=1e-10), "result last200 average disagrees with actual steps")
    return {"actual_warm_epochs": 3, "actual_steps": 2000, "tail_steps": 200,
            "mean_tail_weights": tail.mean(axis=0).tolist(), "predictive_nonzero_steps": nonzero,
            "skipped_sections_total": skipped, "warm_events": warm, "step_events": steps,
            "inner_outer_step_sample_times_recorded": False,
            "temporal_scope": "Frozen execution path and dataset_audit boundaries only; existing step logs have no individual sampled timestamps."}


def validate_blo_checkpoint(state, trace, expected_signature, groups, n_industries):
    require(state["signature"] == expected_signature, "selector checkpoint signature/mode mismatch")
    require(state["groups"] == groups and state["n_industries"] == n_industries, "checkpoint feature identities mismatch")
    require(state["warm_epoch"] == 3 and state["step"] == 2000, "selector checkpoint budget is incomplete")
    tail = np.asarray(state["tail"], dtype=np.float64)
    actual = np.asarray([r["weights"] for r in trace["step_events"][-200:]], dtype=np.float64)
    require(tail.shape == (200, 4) and np.isfinite(tail).all(), "checkpoint tail malformed")
    require(np.allclose(tail, actual, rtol=0, atol=1e-12), "checkpoint tail disagrees with actual steps")
    logits = np.asarray(state["logits"], dtype=np.float64)
    require(logits.shape == (4,) and np.isfinite(logits).all(), "checkpoint logits malformed")
    softmax = np.exp(logits - logits.max()); softmax /= softmax.sum()
    require(np.allclose(softmax, actual[-1], rtol=0, atol=1e-6), "checkpoint logits disagree with final actual weights")
    require(state["lambda_optimizer_steps"] == [2000], "lambda Adam checkpoint step mismatch")
    return {"warm_epoch": 3, "step": 2000, "checkpoint_tail_exact_actual_steps": True,
            "final_logits_match_actual_last_step_weights": True, "lambda_adam_step": 2000}


def validate_weights(weights, results, config, fingerprint):
    require(set(results) == set(SEEDS) and config["seeds"] == SEEDS, "five exact selector seeds required")
    declared = simplex(weights["mean_weights"])
    means = np.mean([blo_summary(results[s], s, config, fingerprint) for s in SEEDS], axis=0)
    require(weights["seeds"] == SEEDS and weights["per_seed_tail_steps"] == 200, "BLO aggregation seed/tail identities changed")
    require(np.allclose(means, declared, rtol=0, atol=1e-10), "existing five-seed aggregation mismatch")
    require(weights["selected_label"] == int(np.argmax(means) + 1), "existing BLO label is not frozen-rule argmax")
    return {"existing_label": weights["selected_label"], "five_seed_result_aggregation_matches": True,
            "new_selection_written": False, "selector_traces_loaded_this_aggregation_check": False}


def validate_selection(selection, weights, candidates, config, fingerprint, formal):
    methods = ["fixed_1", "fixed_2", "fixed_3", "fixed_4", "blo"]
    require(set(candidates) == set(methods) and set(selection["alternatives"]) == set(methods), "25 candidate source roster mismatch")
    means = {}
    for method in methods:
        require(set(candidates[method]) == set(SEEDS), "candidate seed roster mismatch")
        label = weights["selected_label"] if method == "blo" else int(method[-1])
        actual_rows = selection["alternatives"][method]["seeds"]
        require([r["seed"] for r in actual_rows] == SEEDS, "selection seed lineage mismatch")
        values = []
        for row, seed in zip(actual_rows, SEEDS):
            source = candidates[method][seed]
            signature(source, {"dataset_fingerprint": fingerprint, "config": config, "seed": seed, "label": label, "variant": "full"}, True)
            near(row["ic"], source["best_validation_ic"])
            require(row["epoch"] == source["best_epoch"], "selection best-epoch lineage mismatch")
            relative = Path("blo_final" if method == "blo" else "fixed") / f"label_{label}" / f"seed_{seed}"
            require(Path(row["path"]).resolve() == (formal / relative).resolve(), "selection path provenance mismatch")
            values.append(source["best_validation_ic"])
        means[method] = float(np.mean(values))
        near(selection["alternatives"][method]["mean_ic"], means[method])
        near(selection["alternatives"][method]["std_ic"], float(np.std(values, ddof=1)))
    best = max(means, key=means.get)
    gain = means[best] - means["fixed_4"]
    existing_rule_choice = "fixed_4" if gain < config["label_selection_min_gain"] else best
    require(config["label_selection_min_gain"] == .001, "selection threshold changed")
    require(selection["validation_year"] == 2024 and selection["blo_label"] == weights["selected_label"], "selection target/year lineage mismatch")
    near(selection["minimum_gain"], .001); near(selection["best_gain_vs_direct_y4"], gain)
    require(selection["selected_method"] == existing_rule_choice, "existing selection violates frozen2024 rule")
    expected_label = weights["selected_label"] if existing_rule_choice == "blo" else int(existing_rule_choice[-1])
    require(selection["selected_label"] == expected_label, "selected ablation label lineage mismatch")
    return {"existing_label": expected_label, "existing_method": selection["selected_method"],
            "all25_result_lineage_checked": True, "new_selection_written": False,
            "test_or_forward_results_used": False, "candidate_checkpoint_bytes_loaded": False}


def validate_fixed_trace(result, blob, seed, label, variant, config, fingerprint, train_sha):
    signature(result, {"dataset_fingerprint": fingerprint, "config": config, "seed": seed, "label": label, "variant": variant}, True)
    require(blob.endswith(b"\n"), "completed epoch log partial tail")
    rows = [json.loads(l) for l in blob.splitlines() if l.strip()]
    require(rows, "missing completed epochs")
    ordered_events(blob, "epoch", "epoch", len(rows), seed)
    require(all(r["label"] == label and r["variant"] == variant for r in rows), "epoch label/variant mismatch")
    counts = audited_epoch_count(result["epochs_completed"], {r["epoch"]: r for r in rows}, config, train_sha)
    best, epoch, bad = float("-inf"), None, 0
    for row in rows:
        require(all(math.isfinite(row[k]) for k in ("train_loss", "validation_y4_ic", "validation_y4_rank_ic", "best_ic", "learning_rate", "elapsed_seconds")), "nonfinite epoch diagnostics")
        require(row["elapsed_seconds"] > 0 and config["min_learning_rate"] <= row["learning_rate"] <= config["learning_rate"], "invalid epoch duration/LR")
        require(type(row["skipped_sections"]) is int and row["skipped_sections"] >= 0, "invalid epoch skipped count")
        if row["validation_y4_ic"] > best:
            best, epoch, bad = row["validation_y4_ic"], row["epoch"], 0
        else:
            bad += 1
        near(row["best_ic"], best); require(row["bad_epochs"] == bad, "early-stop sequence mismatch")
    near(result["best_validation_ic"], best)
    require(result["best_epoch"] == epoch and (len(rows) == config["epochs"] or bad >= config["patience"]), "best epoch or stopping reason mismatch")
    return {**counts, "best_epoch": epoch, "validation_y4_ic": best, "epoch_events": rows}


def validation_records(sections, stored, best_ic, expected_population, expected_scored_population):
    rows = sections["records"]
    require(len(rows) == len({r["signal_time_ns"] for r in rows}), "duplicate validation timestamp")
    for r in rows:
        t = datetime.fromtimestamp(r["signal_time_ns"] / 1e9, ZoneInfo("Asia/Shanghai"))
        require(t.date().isoformat() == r["date"] and r["date"].startswith("2024-"), "validation timestamp/date/year mismatch")
        require(type(r["slot"]) is int and 0 <= r["slot"] < 4, "invalid validation slot")
        require(t.strftime("%H:%M:%S") == ["10:30:00", "11:30:00", "14:00:00", "15:00:00"][r["slot"]], "validation slot/time mismatch")
        require(type(r["pool_count"]) is int and type(r["scored_count"]) is int and 100 <= r["scored_count"] <= r["pool_count"], "invalid validation coverage")
    population = sorted([[r["signal_time_ns"], r["date"], r["slot"], r["pool_count"]] for r in rows])
    digest = hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(digest == expected_population, "validation population differs from accepted baseline")
    scored = sorted([[r["signal_time_ns"], r["date"], r["slot"], r["pool_count"], r["scored_count"]] for r in rows])
    scored_digest = hashlib.sha256(json.dumps(scored, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(scored_digest == expected_scored_population, "validation scored population differs from accepted baseline")
    summary = breakdown(rows); matching(stored, summary); near(summary["overall"]["ic"], best_ic)
    return {"cross_sections": len(rows), "population_sha256": digest, "scored_population_sha256": scored_digest,
            "all_year_quarter_slot_ddof1_metrics_match": True, "summary": summary}
