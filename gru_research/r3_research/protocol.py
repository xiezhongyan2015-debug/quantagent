"""Registered R3 contracts. No array, model, or data access at import time."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import statistics
from zoneinfo import ZoneInfo

PROTOCOL_SHA256 = "6c3008c45c73d38f7dddc67fbc4ed156546c38e4fbe1c0c51b9eb760516c6d83"
BASELINE_SHA256 = "3ae2bf43c0e2734348ad67f9a968c6548582aeb6b7f402bdf791ea9da4da5ada"
REGISTRATION_SHA256 = "6e841c1760cb68fa82cf655dda384341993af8a4bce11f53e260f52d724ebdb6"
SEEDS = (11, 22, 33, 44, 55)
LR = 3e-4
FOLDS = (
    {"id": "2021", "fit": ("2019-01-01", "2021-01-01"), "inner": ("2021-01-01", "2021-07-01"), "outer": ("2021-07-01", "2022-01-01")},
    {"id": "2022", "fit": ("2019-01-01", "2022-01-01"), "inner": ("2022-01-01", "2022-07-01"), "outer": ("2022-07-01", "2023-01-01")},
    {"id": "2023", "fit": ("2019-01-01", "2023-01-01"), "inner": ("2023-01-01", "2023-07-01"), "outer": ("2023-07-01", "2024-01-01")},
)
R2_BINDING = {
    "protocol_sha256": "e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f",
    "new_source_manifest_sha256": "8a7c855374b7237044639b1b0610b25a0843f73b6393ec3555270dfa1d9eb809",
    "causal_receipt_sha256": "95370519b5974c6724cef641506eeb113b4d33fc76fdb575182f7783fe2ca3cc",
    "causal_transport_manifest_sha256": "685c131667613b4a6d7fc2ff49713d889faadd3590ebaf986e624bd1b718dc9c",
    "dataset_fingerprint": "6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def plans():
    return [{"fold": f["id"], "seed": s} for f in FOLDS for s in SEEDS]


def load_protocol(blob):
    require(sha(blob) == PROTOCOL_SHA256, "registered R3 protocol bytes differ")
    doc = json.loads(blob)
    require(doc["candidate"] == {"id": "pv_label4", "variant": "pv", "training_label": 4,
            "zero_based_loss_index": 3, "evaluation_label": 4, "initial_learning_rate": LR}, "unregistered candidate")
    require(doc["seeds"] == list(SEEDS) and doc["folds"] == [
        {k: v if k == "id" else list(v) for k, v in f.items()} for f in FOLDS], "registered roster changed")
    require(doc["fit_cap"] == {"new_development": 15, "new_baseline": 0, "new_final_maximum": 5,
        "new_total_maximum": 20, "new_final_if_candidate_not_selected": 0,
        "infinite_grid_or_train_until_IC07": False}, "registered fit cap changed")
    return doc


def population(records, fold):
    require(isinstance(records, list) and records, "missing saved outer records")
    result = []
    for x in records:
        require(type(x.get("signal_time_ns")) is int and type(x.get("slot")) is int
                and x["slot"] in range(4) and isinstance(x.get("date"), str), "malformed outer identity")
        require(fold["outer"][0] <= x["date"] < fold["outer"][1], "wrong chronological outer role")
        dt = datetime.fromtimestamp(x["signal_time_ns"] / 1e9, ZoneInfo("Asia/Shanghai"))
        require(dt.date().isoformat() == x["date"] and dt.strftime("%H:%M:%S")
                == ("10:30:00", "11:30:00", "14:00:00", "15:00:00")[x["slot"]], "clock/date/slot mismatch")
        require(type(x.get("scored_count")) is int and type(x.get("pool_count")) is int
                and 100 <= x["scored_count"] <= x["pool_count"], "malformed shared population")
        require(x.get("ic") is None or type(x["ic"]) in (int, float), "malformed saved IC")
        result.append((x["signal_time_ns"], x["date"], x["slot"], x["pool_count"], x["scored_count"]))
    require(len({x[0] for x in result}) == len(result) and {x[2] for x in result} == set(range(4)),
            "repeated timestamp or deleted signal slot")
    return sorted(result)


def verify_pair(documents, binding, fold, seed, label, config):
    """Check actual inner/once-outer metadata, never train or load a checkpoint."""
    inner, outer = documents["result.json"], documents["outer_result.json"]
    expected = {**binding, "config": config, "seed": seed, "label": label, "variant": "pv",
        "stage": "diagnostic", "arm": "control", "fold": fold["id"],
        "fit_period": list(fold["fit"]), "inner_period": list(fold["inner"])}
    require(inner.get("complete") is True and inner.get("signature") == expected
            and documents["identity.json"] == expected
            and all(inner.get(k) == v for k, v in expected.items()), "actual inner identity differs")
    require(outer.get("complete") is True and outer.get("stage") == "diagnostic_outer"
            and all(outer.get(k) == v for k, v in binding.items()) and outer.get("arm") == "control"
            and outer.get("fold") == fold["id"] and outer.get("seed") == seed
            and outer.get("used_for_training_or_checkpoint") is False,
            "actual outer identity differs")
    hashes = documents["_hashes"]
    require(outer.get("checkpoint_role") == "inner_best"
            and outer.get("checkpoint_sha256") == inner.get("best_inner_checkpoint_sha256")
            and outer.get("inner_result_sha256") == hashes["result.json"]
            and outer.get("outer_attempt_sha256") == hashes["outer_attempt.json"], "once-outer source differs")
    attempt = documents["outer_attempt.json"]
    require(all(attempt.get(k) == v for k, v in {**binding, "fold": fold["id"], "arm": "control",
        "seed": seed, "checkpoint_role": "inner_best", "checkpoint_sha256": outer["checkpoint_sha256"],
        "inner_result_sha256": hashes["result.json"]}.items()), "once-outer marker differs")
    require(inner["epoch_log_sha256"] == hashes["epochs.jsonl"]
            and inner["inner_records_sha256"] == hashes["best_inner_sections.json"]
            and inner["inner_summary_sha256"] == hashes["best_inner_summary.json"], "inner evidence bytes changed")
    epochs = documents["epochs.jsonl"]
    require([r["epoch"] for r in epochs] == list(range(1, len(epochs) + 1))
            and inner["epochs_completed"] == len(epochs) and 1 <= len(epochs) <= config["epochs"], "epoch gap/count")
    best, best_epoch, bad = -math.inf, 0, 0
    for r in epochs:
        require(r.get("phase") == ("r3_diagnostic" if label == 4 else "r2_diagnostic")
                and r.get("arm") == "control" and r.get("fold") == fold["id"] and r.get("seed") == seed
                and type(r.get("inner_y4_IC")) in (int, float) and math.isfinite(r["inner_y4_IC"]), "epoch role/IC")
        if r["inner_y4_IC"] > best:
            best, best_epoch, bad = r["inner_y4_IC"], r["epoch"], 0
        else:
            bad += 1
        require(r["best_inner_IC"] == best and r["best_inner_epoch"] == best_epoch
                and r["bad_epochs"] == bad, "inner early-stop/checkpoint trace")
    require(inner["best_inner_epoch"] == best_epoch and inner["best_inner_IC"] == best
            and (len(epochs) == config["epochs"] or bad == config["patience"]), "inner completion boundary")
    require(documents["best_inner_summary.json"]["overall"]["ic"] == best, "best inner summary differs")
    records = outer.get("records")
    require(isinstance(records, list) and type(outer.get("eligible_sections_expected")) is int
            and len(records) == outer["eligible_sections_expected"], "outer section count differs")
    return population(records, fold)


def decide(candidate, baseline):
    """Exactly one prespecified comparison; no incomplete-roster fallback publish."""
    import numpy as np
    wanted = {(f["id"], s) for f in FOLDS for s in SEEDS}
    require(set(candidate) == set(baseline) == wanted, "all fifteen matched pairs required")
    folded, gains, means = [], [], {"candidate": [], "baseline": []}
    for fold in FOLDS:
        maps, prior = {}, None
        for family, runs in (("candidate", candidate), ("baseline", baseline)):
            for seed in SEEDS:
                rows = runs[(fold["id"], seed)]["records"]
                current = population(rows, fold)
                prior = current if prior is None else prior
                require(current == prior, "actual candidate/baseline/seed population differs")
                maps[family, seed] = {x["signal_time_ns"]: x for x in rows}
        times = [x[0] for x in prior]
        # Completed evidence with weak statistics retains R2; nothing is filtered.
        if any(type(maps[k][t]["ic"]) not in (int, float) or not math.isfinite(maps[k][t]["ic"])
               for k in maps for t in times):
            return {"candidate_selected": False, "reason": "complete_evidence_nonfinite_IC_R2_retained",
                    "statistical_comparison_valid": False, "new_final_fits": 0, "dropped_records": 0}
        daily = {}
        for t in times:
            values = {a: sum(maps[a, s][t]["ic"] for s in SEEDS) / 5 for a in means}
            daily.setdefault(maps["baseline", SEEDS[0]][t]["date"], []).append(values["candidate"] - values["baseline"])
        dates = sorted(daily)
        if len(dates) < 20:
            return {"candidate_selected": False, "reason": "complete_evidence_below20days_R2_retained",
                    "statistical_comparison_valid": False, "new_final_fits": 0, "dropped_records": 0}
        for family in means:
            means[family].append(sum(sum(maps[family, s][t]["ic"] for t in times) / len(times) for s in SEEDS) / 5)
        gains.append(means["candidate"][-1] - means["baseline"][-1])
        folded.append((np.array([sum(daily[d]) for d in dates], np.float64),
                       np.array([len(daily[d]) for d in dates], np.int64)))
    rng, estimates = np.random.default_rng(20261002), np.empty(2000)
    for repetition in range(2000):
        fold_estimates = []
        for sums, counts in folded:
            n = len(sums)
            starts = rng.integers(0, n - 19, size=(n + 19) // 20)
            sample = np.concatenate([np.arange(s, s + 20) for s in starts])[:n]
            fold_estimates.append(float(sums[sample].sum() / counts[sample].sum()))
        estimates[repetition] = sum(fold_estimates) / 3
    lower, upper = map(float, np.quantile(estimates, [.005, .995]))
    overall = sum(means["candidate"]) / 3 - sum(means["baseline"]) / 3
    conditions = {"overall_gain_at_least_001": overall >= .001,
                  "three_fold_gains_strictly_positive": all(x > 0 for x in gains),
                  "paired_99pct_CI_lower_strictly_positive": lower > 0}
    chosen = all(conditions.values())
    return {"candidate_selected": chosen, "reason": "all_registered_conditions_passed" if chosen
            else "registered_condition_failed_R2_retained", "statistical_comparison_valid": True,
            "mean_IC_by_family": {a: sum(v) / 3 for a, v in means.items()}, "fold_mean_IC_by_family": means,
            "fold_gains": gains, "overall_gain": overall, "paired_difference_ci99": [lower, upper],
            "conditions": conditions, "block_days": 20, "bootstrap_repetitions": 2000,
            "bootstrap_seed": 20261002, "fold_strata": 3, "equal_seeds": 5,
            "dropped_records": 0, "new_final_fits": 5 if chosen else 0,
            "2024_2025_2026_used": False, "virgin_OOS": False, "comparisons": 1}


def final_budget(inner):
    require(set(inner) == {(f["id"], s) for f in FOLDS for s in SEEDS}, "fifteen candidate budgets required")
    epochs = []
    for key in plans():
        result = inner[key["fold"], key["seed"]]
        value = result.get("best_inner_epoch")
        require(result.get("complete") is True and result.get("label") == 4
                and result.get("protocol_sha256") == PROTOCOL_SHA256
                and type(value) is int and 1 <= value <= 40, "unbound candidate inner epoch")
        epochs.append(value)
    return {"epochs": max(1, min(40, int(statistics.median(epochs)))), "inner_best_epochs": epochs,
            "best_inner_epoch_is_one_based": True, "added_one_to_result_epoch": False,
            "derivation": "integer median of fifteen candidate one-based best-inner epochs; clamp1..40",
            "outer_or_2024_2025_2026_used_for_budget": False}
