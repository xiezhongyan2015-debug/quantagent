"""Bounded, explicitly invoked real-data engineering calibration; no study counts."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time

import numpy as np
import psutil

from quanta_agents.factor_campaign.campaign import Campaign, read, sha, spec_of
from quanta_agents.factor_campaign.combination import (
    CombinationEvaluator, CombinationSpec, replay_predictions,
)
from quanta_agents.factor_campaign.resources import CostLedger, ResourceGuard
from quanta_agents.research_kernel.store import write_json


@contextmanager
def measured(phases, name):
    done = threading.Event()
    rows = []
    def sample():
        while not done.is_set():
            memory = psutil.Process().memory_info()
            rows.append((getattr(memory, "private", memory.rss), memory.rss,
                         psutil.virtual_memory().available))
            done.wait(.1)
    worker = threading.Thread(target=sample, daemon=True)
    start, cpu = time.perf_counter(), time.process_time()
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join()
        phases.append({"phase": name, "wall_seconds": time.perf_counter()-start,
            "cpu_seconds": time.process_time()-cpu,
            "sampled_peak_private_bytes": max(r[0] for r in rows),
            "sampled_peak_rss_bytes": max(r[1] for r in rows),
            "minimum_available_memory_bytes": min(r[2] for r in rows)})


def run(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("calibration output already exists; preserve it and choose a new run directory")
    admission = ResourceGuard(root).check()
    if not admission["allowed"]:
        raise RuntimeError("resource admission blocked: " + json.dumps(admission))
    output.mkdir(parents=True)
    rows = read(root / "reference_catalog.json")
    factors = [spec_of(row) for row in rows]
    if len(factors) != 46:
        raise ValueError("calibration requires the complete registered 46-library reference")
    first4 = tuple(item.factor_id for item in factors[:4])
    all46 = tuple(item.factor_id for item in factors)
    declarations = [CombinationSpec("calibration_first4", first4, target=target, update_rule=rule)
        for target in ("raw_return_demeaned", "rank_return_demeaned")
        for rule in ("fixed", "quarterly_rolling3y", "quarterly_expanding")]
    declarations += [CombinationSpec("calibration_first4_equal", first4, method="equal_direction"),
        CombinationSpec("calibration_dense46", all46)]
    write_json(output / "preregistered.json", {"study_counted": False,
        "selection": "first_four_in_frozen_reference_inventory_without_performance_selection",
        "combinations": [spec.to_dict() for spec in declarations],
        "pair": {"base_ids": list(first4), "additional_id": factors[4].factor_id,
            "folds": [["2016-01-01", "2016-12-31", "2017-01-01", "2017-12-31"],
                      ["2016-01-01", "2017-12-31", "2018-01-01", "2018-12-31"]]},
        "source_hashes": {str(p): sha(p) for p in Path(__file__).resolve().parents[1].joinpath(
            "src/quanta_agents/factor_campaign").glob("*.py")}})
    phases, results = [], []
    ledger = CostLedger(root)
    campaign = Campaign(root)
    error = None
    try:
        with measured(phases, "context_load"):
            ctx = campaign.context().register(factors)
        evaluator = CombinationEvaluator(ctx, cache_dir=root / "cache/combination")
        for spec in declarations:
            if not ResourceGuard(root).check()["allowed"]:
                raise RuntimeError("resource floor reached before next calibration phase")
            with measured(phases, spec.combination_id):
                folder = output / spec.combination_id
                folder.mkdir()
                write_json(folder / "preregistered_spec.json", spec.to_dict())
                report, predictions = evaluator.evaluate(spec, start="2019-01-01", end="2024-12-31")
                np.savez_compressed(folder / "scores.npz", scores=predictions.to_numpy(dtype=float),
                    dates=np.asarray(predictions.index.strftime("%Y-%m-%d"), dtype="U10"),
                    columns=np.asarray(predictions.columns, dtype=str))
                write_json(folder / "report.json", report)
                write_json(folder / "package.json", {"entity_type": "combination",
                    "entity_id": spec.combination_id, "spec": spec.to_dict(),
                    "models_by_interval": report["models_by_interval"],
                    "factors": [f.to_dict() for f in factors], "scores_file": "scores.npz",
                    "scores_sha256": sha(folder / "scores.npz"),
                    "registration_file": "preregistered_spec.json",
                    "registration_sha256": sha(folder / "preregistered_spec.json"),
                    "lineage": {"supervised": spec.method == "ridge",
                        "aggregation_of_registered_factors": list(spec.feature_ids)}})
                np.testing.assert_allclose(predictions.to_numpy(),
                    replay_predictions(ctx, report["models_by_interval"]).to_numpy(),
                    rtol=1e-12, atol=1e-12, equal_nan=True)
                results.append({"combination_id": spec.combination_id, "status": report["status"],
                    "member_count": len(spec.feature_ids), "target": spec.target,
                    "update_rule": spec.update_rule, "method": spec.method,
                    "replay_passed": True, "annual": report["annual"],
                    "statistic_day_computations": evaluator.statistic_day_computations})
                print(json.dumps({"completed": len(results), "id": spec.combination_id,
                                  "status": report["status"]}), flush=True)
        with measured(phases, "warm_cache_parity"):
            first = declarations[0]
            warm, models = evaluator._predict(first, start="2019-01-01", end="2024-12-31")
            with np.load(output / first.combination_id / "scores.npz", allow_pickle=False) as archive:
                np.testing.assert_allclose(warm.to_numpy(), archive["scores"],
                                           rtol=1e-12, atol=1e-12, equal_nan=True)
        with measured(phases, "paired_inner_folds"):
            base = CombinationSpec("fixed_structural_four", first4)
            augmented = CombinationSpec("fixed_structural_four_plus_fifth", first4+(factors[4].factor_id,))
            paired = [evaluator.paired_increment(base, augmented, start=f"{year+1}-01-01",
                end=f"{year+1}-12-31", fit_start="2016-01-01", fit_end=f"{year}-12-31")
                for year in (2016, 2017)]
            write_json(output / "paired_inner_folds.json", paired)
            for report in paired:
                for left, right in zip(report["models"]["baseline_common"], report["models"]["augmented"]):
                    if left["status"] == right["status"] == "fitted":
                        assert left["sample_mask_id"] == right["sample_mask_id"]
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        for phase in phases:
            ledger.record(output.name+":"+phase["phase"], kind="combination_calibration",
                wall_seconds=phase["wall_seconds"], cpu_seconds=phase["cpu_seconds"],
                peak_memory_bytes=phase["sampled_peak_private_bytes"])
        write_json(output / "summary.json", {"status": "complete" if error is None else "failed",
            "error": error, "study_counted": False, "numeric_2025_loaded": False,
            "all_declared_fits_available": len(results) == len(declarations) and all(
                r["status"] == "evaluated" for r in results),
            "results": results, "phases": phases,
            "memory_semantics": "process private/RSS sampled every100ms; not OS lifetime maximum",
            "total_wall_seconds": sum(p["wall_seconds"] for p in phases),
            "total_cpu_seconds": sum(p["cpu_seconds"] for p in phases)})
    return read(output / "summary.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output), ensure_ascii=False), flush=True)
