"""Pure contracts for the one predeclared R2 comparison, never test-driven search."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

PROTOCOL_SHA256 = "e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f"
SEEDS = (11, 22, 33, 44, 55)
ARMS = {"control": 3e-4, "lower_lr": 1e-4}
FOLDS = (
    {"id": "2021", "fit": ("2019-01-01", "2021-01-01"), "inner": ("2021-01-01", "2021-07-01"), "outer": ("2021-07-01", "2022-01-01")},
    {"id": "2022", "fit": ("2019-01-01", "2022-01-01"), "inner": ("2022-01-01", "2022-07-01"), "outer": ("2022-07-01", "2023-01-01")},
    {"id": "2023", "fit": ("2019-01-01", "2023-01-01"), "inner": ("2023-01-01", "2023-07-01"), "outer": ("2023-07-01", "2024-01-01")},
)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def load_protocol(path=None):
    path = Path(path) if path is not None else Path(__file__).with_name("protocol_v1.json")
    blob = path.read_bytes()
    require(sha(blob) == PROTOCOL_SHA256, "R2 preregistered protocol bytes differ")
    doc = json.loads(blob)
    require(doc["seeds"] == list(SEEDS), "R2 fixed five seeds changed")
    require(doc["arms"] == [{"id": k, "initial_learning_rate": v} for k, v in ARMS.items()], "R2 permits only two fixed learning rates")
    require(doc["folds"] == [{k: list(v) if k != "id" else v for k, v in f.items()} for f in FOLDS], "chronological roles changed")
    require(doc["fit_cap"] == {"diagnostic": 30, "final": 5, "total": 35, "infinite_grid_or_train_until_IC07": False}, "R2 fit cap changed")
    return doc


def plans():
    return [{"fold": fold["id"], "arm": arm, "seed": seed} for fold in FOLDS for arm in ARMS for seed in SEEDS]


def run_key(fold, arm, seed):
    require(fold in {f["id"] for f in FOLDS} and arm in ARMS and type(seed) is int and seed in SEEDS,
            "unregistered fold/arm/seed")
    return f"{fold}/{arm}/seed_{seed}"


def final_budget(inner_results, selected_arm):
    """Do not fabricate a budget when the selected arm's fifteen proofs are absent."""
    require(selected_arm in ARMS, "unknown chosen learning-rate family")
    relevant = [r for r in inner_results if r.get("arm") == selected_arm]
    wanted = {(f["id"], s) for f in FOLDS for s in SEEDS}
    identities = [(r.get("fold"), r.get("seed")) for r in relevant]
    require(len(relevant) == 15 and len(set(identities)) == 15 and set(identities) == wanted,
            "final budget requires all fifteen selected-arm inner checkpoints")
    epochs = []
    for r in relevant:
        require(r.get("complete") is True and r.get("stage") == "diagnostic"
                and r.get("protocol_sha256") == PROTOCOL_SHA256, "unbound/incomplete inner result")
        e = r.get("best_inner_epoch")
        require(type(e) is int and 1 <= e <= 40, "inner best epoch outside fixed budget")
        require(r.get("best_inner_checkpoint_sha256") and r.get("inner_records_sha256"), "missing real inner-checkpoint evidence")
        epochs.append(e)
    # There are exactly fifteen integers, so the median has no rounding ambiguity.
    median = statistics.median(epochs)
    return {"epochs": max(1, min(40, int(median))), "selected_arm": selected_arm,
            "inner_best_epochs": epochs, "derivation": "integer median of fifteen chosen-arm inner best epochs",
            "outer_or_2024_2025_2026_used_for_budget": False}


def require_completed_diagnostics(inner_results, outer_results):
    """All thirty actual executions precede final refit; statistics may be weak.

    Missing/malformed evidence is a blocked final stage. Thirty intact runs with
    too few finite ICs/dates are a completed diagnostic that retains control.
    Never skip the second arm merely because the fallback arm has fifteen fits.
    """
    expected = {run_key(**p) for p in plans()}
    def roster(rows, stage):
        require(len(rows) == 30, "all thirty " + stage + " executions required before final")
        identities = [run_key(r["fold"], r["arm"], r["seed"]) for r in rows]
        require(len(set(identities)) == 30 and set(identities) == expected, "diagnostic roster missing/duplicated")
        for r in rows:
            require(r.get("complete") is True and r.get("stage") == stage
                    and r.get("protocol_sha256") == PROTOCOL_SHA256, "diagnostic execution incomplete/unbound")
        return dict(zip(identities, rows))
    inner = roster(inner_results, "diagnostic")
    outer = roster(outer_results, "diagnostic_outer")
    population_by_fold = {}
    for k, r in outer.items():
        require(r.get("checkpoint_role") == "inner_best"
                and r.get("checkpoint_sha256") == inner[k].get("best_inner_checkpoint_sha256")
                and r.get("checkpoint_sha256"), "outer source is not actual matching inner best")
        records = r.get("records")
        require(isinstance(records, list) and records and type(r.get("eligible_sections_expected")) is int
                and len(records) == r["eligible_sections_expected"], "outer records missing")
        fold = next(f for f in FOLDS if f["id"] == r["fold"])
        population = []
        for x in records:
            require(type(x.get("signal_time_ns")) is int and type(x.get("slot")) is int
                    and x["slot"] in range(4) and isinstance(x.get("date"), str), "malformed outer timestamp identity")
            require(fold["outer"][0] <= x["date"] < fold["outer"][1], "outer record belongs to another period")
            dt = datetime.fromtimestamp(x["signal_time_ns"] / 1e9, ZoneInfo("Asia/Shanghai"))
            require(dt.date().isoformat() == x["date"] and dt.strftime("%H:%M:%S")
                    == ("10:30:00", "11:30:00", "14:00:00", "15:00:00")[x["slot"]], "outer clock identity differs")
            require(type(x.get("pool_count")) is int and type(x.get("scored_count")) is int
                    and 100 <= x["scored_count"] <= x["pool_count"], "outer population malformed")
            require(x.get("ic") is None or type(x.get("ic")) in (int, float), "outer IC representation malformed")
            population.append((x["signal_time_ns"], x["date"], x["slot"], x["pool_count"], x["scored_count"]))
        require(len({p[0] for p in population}) == len(population)
                and {p[2] for p in population} == set(range(4)), "outer duplicate timestamp or removed slot")
        population.sort()
        prior = population_by_fold.setdefault(r["fold"], population)
        require(population == prior, "outer actual population differs across arms/seeds")
    return {"inner_complete": 30, "outer_complete": 30, "final_execution_prerequisites_complete": True,
            "statistics_may_still_retain_control": True}


def select_lr(runs):
    """Stratified paired blocks. Only the 30 outer diagnostic records are accepted.

    Within each fold all ten models must share every finite timestamp. At each
    timestamp seeds have equal weight. Each resampled fold preserves all within-
    day observations, then the three fold means have equal weight. No best-seed
    selection or pooling by the unequal number of dates across folds.
    """
    import numpy as np
    fallback = {"selected_arm": "control", "selected_learning_rate": ARMS["control"],
                "protocol_sha256": PROTOCOL_SHA256, "comparisons": 1,
                "reason": "insufficient_evidence_control_retained", "outer_diagnostic_is_virgin_OOS": False,
                "2024_2025_2026_used_for_selection": False}
    try:
        require(len(runs) == 30, "all thirty outer records required")
        mapping = {}
        for r in runs:
            k = run_key(r["fold"], r["arm"], r["seed"])
            require(k not in mapping, "duplicate diagnostic identity")
            require(r.get("stage") == "diagnostic_outer" and r.get("complete") is True
                    and r.get("protocol_sha256") == PROTOCOL_SHA256, "wrong/unbound outer role")
            require(r.get("checkpoint_role") == "inner_best" and r.get("checkpoint_sha256"), "outer did not use frozen inner checkpoint")
            mapping[k] = r
        require(set(mapping) == {run_key(**p) for p in plans()}, "missing diagnostic identity")
        folded, arm_means = [], {a: [] for a in ARMS}
        for fold in FOLDS:
            maps, population = {}, None
            for arm in ARMS:
                for seed in SEEDS:
                    r = mapping[run_key(fold["id"], arm, seed)]
                    records = r["records"]
                    require(records and len(records) == r.get("eligible_sections_expected"), "missing outer sections")
                    times = [x["signal_time_ns"] for x in records]
                    require(len(times) == len(set(times)), "duplicate outer timestamp")
                    current = {}
                    for x in records:
                        require(fold["outer"][0] <= x["date"] < fold["outer"][1], "outer contains another role or2024/test records")
                        require(type(x["slot"]) is int and x["slot"] in range(4), "invalid signal slot")
                        require(type(x["signal_time_ns"]) is int and type(x["ic"]) in (int, float) and math.isfinite(x["ic"]), "nonfinite/untyped outer IC")
                        timestamp = datetime.fromtimestamp(x["signal_time_ns"] / 1e9, ZoneInfo("Asia/Shanghai"))
                        require(timestamp.date().isoformat() == x["date"] and timestamp.strftime("%H:%M:%S")
                                == ("10:30:00", "11:30:00", "14:00:00", "15:00:00")[x["slot"]], "outer timestamp/date/slot disagree")
                        require(type(x["scored_count"]) is int and type(x["pool_count"]) is int
                                and 100 <= x["scored_count"] <= x["pool_count"], "invalid complete outer population")
                        current[x["signal_time_ns"]] = x
                    identity = sorted((t, x["date"], x["slot"], x["pool_count"], x["scored_count"]) for t, x in current.items())
                    if population is None:
                        population = identity
                    require(identity == population, "outer population differs between seeds/arms")
                    require({x["slot"] for x in records} == set(range(4)), "one or more signal slots were dropped")
                    maps[(arm, seed)] = current
            times = sorted(maps[("control", SEEDS[0])])
            daily = {}
            for t in times:
                date = maps[("control", SEEDS[0])][t]["date"]
                values = {arm: sum(maps[(arm, s)][t]["ic"] for s in SEEDS) / 5 for arm in ARMS}
                daily.setdefault(date, []).append(values["lower_lr"] - values["control"])
            dates = sorted(daily)
            require(len(dates) >= 20, "fewer than twenty outer trading days")
            sums = np.array([sum(daily[d]) for d in dates], dtype=np.float64)
            counts = np.array([len(daily[d]) for d in dates], dtype=np.int64)
            folded.append((sums, counts))
            for arm in ARMS:
                arm_means[arm].append(sum(sum(maps[(arm, s)][t]["ic"] for t in times) / len(times) for s in SEEDS) / 5)
        rng = np.random.default_rng(20261002)
        estimates = np.empty(2000)
        for b in range(2000):
            fold_estimates = []
            for sums, counts in folded:
                n = len(sums)
                starts = rng.integers(0, n - 20 + 1, size=int(np.ceil(n / 20)))
                sample = np.concatenate([np.arange(s, s + 20) for s in starts])[:n]
                fold_estimates.append(float(sums[sample].sum() / counts[sample].sum()))
            estimates[b] = sum(fold_estimates) / 3
        lo, hi = [float(x) for x in np.quantile(estimates, [0.025, 0.975])]
        chosen = "lower_lr" if lo > 0 else "control"
        return {**fallback, "selected_arm": chosen, "selected_learning_rate": ARMS[chosen],
                "reason": "lower_lr_positive_paired_interval" if chosen == "lower_lr" else "paired_interval_not_strictly_positive_control_retained",
                "mean_IC_by_arm": {a: sum(v) / 3 for a, v in arm_means.items()},
                "fold_mean_IC_by_arm": arm_means, "paired_difference_ci95": [lo, hi],
                "block_days": 20, "bootstrap_repetitions": 2000, "bootstrap_seed": 20261002,
                "fold_count": 3, "seed_count_per_arm_per_fold": 5,
                "mean_difference": sum(arm_means["lower_lr"]) / 3 - sum(arm_means["control"]) / 3}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {**fallback, "missing_or_invalid_evidence": type(exc).__name__ + ":" + str(exc)}
