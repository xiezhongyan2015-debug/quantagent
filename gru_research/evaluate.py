"""Frozen y4 prediction metrics and paired trading-day block inference.

Portfolio performance must be computed with the project's unchanged cash/trade/cost
ledger. This module reports prediction metrics and never labels IC as trading return.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np


def ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    values = np.asarray(x)[order]
    cuts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1, len(values)]
    out = np.empty(len(values), dtype=np.float64)
    for start, end in zip(cuts[:-1], cuts[1:]):
        out[order[start:end]] = (start + end - 1) * 0.5 + 1
    return out


def correlation(x: np.ndarray, y: np.ndarray, min_stocks: int = 100, variance_floor: float = 1e-12) -> float:
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = np.asarray(x, dtype=np.float64)[finite], np.asarray(y, dtype=np.float64)[finite]
    if len(x) < min_stocks:
        return float("nan")
    x, y = x - x.mean(), y - y.mean()
    vx, vy = np.mean(x * x), np.mean(y * y)
    if vx <= variance_floor or vy <= variance_floor:
        return float("nan")
    return float(np.mean(x * y) / np.sqrt(vx * vy))


def metrics(values: list[dict]) -> dict:
    out = {"cross_sections": len(values), "valid_ic_sections": 0}
    for field in ("ic", "rank_ic"):
        a = np.array([v[field] for v in values], dtype=np.float64)
        a = a[np.isfinite(a)]
        std = float(a.std(ddof=1)) if len(a) > 1 else float("nan")
        out[field] = float(a.mean()) if len(a) else None
        out[field + "_std"] = std if np.isfinite(std) else None
        out[field + "_ir"] = float(a.mean() / std) if len(a) > 1 and std > 0 else None
        if field == "ic":
            out["valid_ic_sections"] = len(a)
    total = sum(v.get("pool_count", 0) for v in values)
    covered = sum(v.get("scored_count", 0) for v in values)
    out["coverage"] = covered / total if total else None
    out["pool_stock_hours"], out["scored_stock_hours"] = total, covered
    out["icir_definition"] = "mean / sample_std of per-timestamp cross-sectional IC; unannualized"
    return out


def breakdown(records: list[dict]) -> dict:
    groups = {"year": {}, "quarter": {}, "slot": {}}
    for record in records:
        date = record["date"]
        keys = {"year": date[:4], "quarter": date[:4] + "Q" + str((int(date[5:7]) - 1) // 3 + 1),
                "slot": str(record["slot"])}
        for kind, key in keys.items():
            groups[kind].setdefault(key, []).append(record)
    return {"overall": metrics(records), **{kind: {k: metrics(v) for k, v in sorted(g.items())}
                                              for kind, g in groups.items()}}


def paired_block_bootstrap(a: list[dict], b: list[dict], block_days: int = 20,
                           repetitions: int = 2000, seed: int = 20261001) -> dict:
    """Paired, non-circular moving blocks; preserve all four within-day observations.

    Each bootstrap sample consists of N trading days sampled as blocks of 20 days.
    We compute the timestamp-equal mean (not a stock-hour independent standard error).
    """
    am = {r["signal_time_ns"]: r for r in a if r["ic"] is not None and np.isfinite(r["ic"])}
    bm = {r["signal_time_ns"]: r for r in b if r["ic"] is not None and np.isfinite(r["ic"])}
    daily = {}
    for t in sorted(am.keys() & bm.keys()):
        daily.setdefault(am[t]["date"], []).append(am[t]["ic"] - bm[t]["ic"])
    dates = sorted(daily)
    if len(dates) < block_days:
        return {"valid": False, "reason": "fewer trading days than the fixed block length", "trading_days": len(dates)}
    sums = np.array([sum(daily[d]) for d in dates], dtype=np.float64)
    counts = np.array([len(daily[d]) for d in dates], dtype=np.int64)
    rng = np.random.default_rng(seed)
    estimates = np.empty(repetitions)
    n, blocks = len(dates), int(np.ceil(len(dates) / block_days))
    for i in range(repetitions):
        starts = rng.integers(0, n - block_days + 1, size=blocks)
        sample = np.concatenate([np.arange(s, s + block_days) for s in starts])[:n]
        estimates[i] = sums[sample].sum() / counts[sample].sum()
    low, high = np.quantile(estimates, [0.025, 0.975])
    return {"valid": True, "mean_ic_difference": float(sums.sum() / counts.sum()),
            "ci_95": [float(low), float(high)], "improvement_established": bool(low > 0),
            "block_trading_days": block_days, "trading_days": n, "repetitions": repetitions,
            "paired_timestamp_count": int(counts.sum()), "seed": seed}


def summarize_seed_results(paths: list[Path]) -> dict:
    rows = []
    for path in paths:
        data = json.loads(path.read_text())
        rows.append({"seed": data["seed"], "ic": data["best_validation_ic"],
                     "epoch": data["best_epoch"], "path": str(path.parent)})
    ic = np.array([r["ic"] for r in rows], dtype=np.float64)
    return {"mean_ic": float(ic.mean()), "std_ic": float(ic.std(ddof=1)) if len(ic) > 1 else None,
            "seeds": rows}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data: dict):
    def clean(v):
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, (float, np.floating)):
            return float(v) if np.isfinite(v) else None
        if isinstance(v, np.integer):
            return int(v)
        return v
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2))
    temporary.replace(path)


def evaluate_frozen(dataset, output: Path, freeze: dict, config: dict, device: str):
    """Evaluate only after freeze; write once for a dataset/model fingerprint."""
    from .train import prediction_records
    full_summary = {}
    for split in ("test_2025", "recent_2026"):
        directory = output / "frozen_evaluation" / split
        directory.mkdir(parents=True, exist_ok=True)
        result_path = directory / "summary.json"
        expected = {"dataset_fingerprint": dataset.fingerprint, "freeze_id": freeze["freeze_id"]}
        if result_path.exists():
            existing = json.loads(result_path.read_text())
            if any(existing.get(k) != v for k, v in expected.items()):
                raise ValueError("Frozen evaluation already exists for a different dataset/model; use a new output directory")
            full_summary[split] = existing
            continue
        indices = dataset.split_indices(split, config, require_labels=False)
        model_results, records = {}, {}
        for name, checkpoints in freeze["evaluation_models"].items():
            seed_records = {}
            rec = prediction_records(dataset, indices, checkpoints, config, device,
                                     scores_csv=directory / (name + "_scores.csv"), model_version=freeze["freeze_id"] + ":" + name,
                                     seed_records_out=seed_records)
            records[name] = rec
            write_json(directory / (name + "_sections.json"), {"records": rec, "seed_records": seed_records})
            per_seed = {seed: breakdown(seed_rec) for seed, seed_rec in seed_records.items()}
            per_seed_ic = np.array([r["overall"]["ic"] for r in per_seed.values()], dtype=np.float64)
            finite_ic = per_seed_ic[np.isfinite(per_seed_ic)]
            model_results[name] = {**breakdown(rec), "per_seed": per_seed,
                "five_seed_ic_distribution": {"mean": float(finite_ic.mean()) if len(finite_ic) else None,
                  "std": float(finite_ic.std(ddof=1)) if len(finite_ic) > 1 else None,
                  "min": float(finite_ic.min()) if len(finite_ic) else None,
                  "max": float(finite_ic.max()) if len(finite_ic) else None}}
        comparison = {name: paired_block_bootstrap(rec, records["selected_full"],
                        config["bootstrap_block_trading_days"], config["bootstrap_repetitions"])
                      for name, rec in records.items() if name != "selected_full"}
        result = {**expected, "split": split, "models": model_results, "ic_difference_vs_selected_full": comparison,
                  "trading_evaluation_status": "must use the existing frozen execution and cost ledger; pending",
                  "historical_test_status": "frozen evaluation for this GRU experiment; prior project exposure not excluded",
                  "unmatured_predictions": "saved to scores CSV but excluded from IC/RankIC"}
        write_json(result_path, result)
        full_summary[split] = result
    return full_summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate frozen GRU models without tuning")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    from .train import PanelDataset
    freeze = json.loads((args.output / "freeze.json").read_text())
    dataset = PanelDataset(args.dataset)
    if dataset.fingerprint != freeze["dataset_fingerprint"]:
        raise ValueError("Dataset differs from the frozen training dataset")
    for name, paths in freeze["evaluation_models"].items():
        for path in paths:
            if file_hash(Path(path)) != freeze["checkpoint_hashes"][path]:
                raise ValueError("A frozen checkpoint changed: " + path)
    evaluate_frozen(dataset, args.output, freeze, freeze["config"], args.device)


if __name__ == "__main__":
    main()
