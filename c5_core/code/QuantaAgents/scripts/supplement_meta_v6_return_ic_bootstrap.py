"""Post-hoc, saved-fit-only descriptive return IC uncertainty supplement.

This script never loads a market panel, factor scores, accounts or a model.
Run `prepare` first, then `run`. Both require a completed owned factor job.
Existing protocols/results are exclusive: this does not silently retry work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd
import pyarrow

from quanta_agents.meta_v6 import risk_diagnostics

VERSION = "v6_saved_fit_return_ic_date_block_supplement_v1"
CYCLE = "03_factor_information_expansion"
START, END = "2016-01-01", "2020-12-02"
COUNT = 1197
HORIZONS = [1, 5, 20]
KEYS = ["HF0219", "HF0017", "HF0091", "F1", "F2", "F3", "F4", "F5", "F6", "HF0280", "F7", "F8"]
RISK_SHA = "7c655af33283261421465974ad260c58700ba9f0aa93907e5c1c4cbb4c60cb3d"


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode("utf-8")).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_once(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(serial(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size}


def verify(row):
    require(Path(row["path"]).is_file() and file_hash(row["path"]) == row["sha256"], "input hash changed: " + row["path"])


def locations(root):
    root = Path(root).resolve()
    folder = root / "cycles" / CYCLE
    job = folder / "jobs/01_factor_execution"
    return root, folder, job


def completed(root):
    root, folder, job = locations(root)
    job_result, job_intent = read(job / "job_result.json"), read(job / "job_intent.json")
    require(job_result["status"] == "completed" and job_result["primary_exit_code"] == 0
            and job_result["all_owned_processes_exited"] is True
            and job_result["owned_active_processes_at_last_observation"] == 0, "owned factor job must be completed with no remaining processes")
    require(job_result["intent_hash"] == digest(job_intent), "job result/intent mismatch")
    require("_expansion_factors_worker" in job_intent["command"], "wrong factor job command")
    old_index, index = read(root / "factor_index.json"), read(folder / "factor_index.json")
    require(old_index["status"] == index["status"] == "completed", "factor indices incomplete")
    require(index["cycle_id"] == CYCLE and index["factor_count"] == 3, "wrong expansion cycle or count")
    require(index["intent_sha256"] == file_hash(folder / "factor_execution_intent.json"), "factor intent mismatch")
    require(index["fit_factor_view_sha256"] == file_hash(folder / "fit_factor_view.json"), "new fit view mismatch")
    require(old_index["fit_factor_view_sha256"] == file_hash(root / "fit_factor_view.json"), "old fit view mismatch")
    require(index["prior_factors"] == old_index["factors"], "prior factor lineage changed")
    return old_index, index


def prepare(root):
    root, folder, job = locations(root)
    old_index, index = completed(root)
    output = folder / "return_ic_bootstrap_protocol.json"
    require(not output.exists(), "protocol exists; do not replace it")
    selection = read(root / "preparation/data_selection.json")
    calendar = Path(selection["calendar_path"])
    require(file_hash(calendar) == selection["calendar_sha256"], "original calendar changed")
    dates = pd.DatetimeIndex(pd.to_datetime(calendar.read_text(encoding="utf-8").splitlines()))
    require(dates.is_unique and dates.is_monotonic_increasing and not dates.hasnans, "invalid original session calendar")
    fit_candidates = dates[(dates >= pd.Timestamp(START)) & (dates <= pd.Timestamp("2020-12-31"))]
    fit_dates = fit_candidates[:-21]
    require(len(fit_dates) == COUNT and str(fit_dates[-1].date()) == END, "fit calendar/purge mismatch")
    paths = [Path(__file__), Path(risk_diagnostics.__file__), root / "factor_index.json", root / "fit_factor_view.json",
             root / "factor_panel_identity.json", root / "preparation/data_selection.json", calendar,
             folder / "factor_index.json", folder / "fit_factor_view.json", folder / "factor_execution_intent.json",
             folder / "factor_declaration.json", folder / "protocol.json", job / "job_result.json", job / "job_intent.json"]
    require(file_hash(risk_diagnostics.__file__) == RISK_SHA, "tested bootstrap helper changed")
    require((risk_diagnostics.BLOCK_LENGTH, risk_diagnostics.BOOTSTRAP_REPETITIONS, risk_diagnostics.BOOTSTRAP_SEED)
            == (20, 1000, 20260909), "bootstrap defaults changed")
    factors = []
    for entry in [*old_index["factors"], *index["factors"]]:
        # F7 also has risk_archive/daily_ic and risk_fit/daily_ic. Only the
        # return table next to the saved score belongs to this supplement.
        expected_daily = Path(entry["scores_path"]).with_name("daily_ic.parquet") if entry.get("scores_path") else None
        candidates = [row for row in entry["artifacts"] if expected_daily is not None and Path(row["path"]) == expected_daily]
        require(len(candidates) <= 1, "ambiguous daily IC artifact")
        daily = candidates[0] if candidates else None
        if daily is not None and Path(daily["path"]).exists():
            verify(daily)
            paths.append(Path(daily["path"]))
            available = True
        else:
            available = False
        factors.append({"factor_key": entry["factor_key"], "factor_id": entry["factor_id"], "name": entry["name"],
            "direction": entry["direction"], "role": entry.get("role", "return_prediction"),
            "source_factor_status": entry["status"], "primary_horizon": entry["primary_horizon"],
            "daily_ic_expected_proof": daily, "daily_ic_available_at_protocol": available})
    require([row["factor_key"] for row in factors] == KEYS, "expected all original nine plus all three expansion factors")
    value = {"version": VERSION, "status": "frozen_supplement_protocol", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root), "cycle_id": CYCLE, "source_proofs": [proof(path) for path in dict.fromkeys(paths)],
        "upstream_price_source_lineage": {"prior_index_source_proofs": old_index["source_proofs"],
            "expansion_index_source_proofs": index["source_proofs"],
            "raw_price_sources_reloaded": False, "raw_price_sources_rehashed_by_supplement": False},
        "proposal_timing": {"post_hoc_supplement": True, "proposed_after_root_saw_hf0280_fit_mean": True,
            "all_source_hashes_frozen_before_saved_daily_ic_numeric_read": True, "pre_results_preregistration_claimed": False,
            "reason": "Call 04 requested date-block or HAC treatment of overlapping forward labels; saved fit return IC had means without these intervals."},
        "scope": {"date_range": [START, END], "fit_signal_sessions": COUNT, "fit_calendar_dates": [str(x.date()) for x in fit_dates],
            "fit_calendar_sha256": digest([str(x.date()) for x in fit_dates]), "training_end": "2020-12-31", "purged_sessions": 21,
            "exposure": "previously_exposed_development", "new_2021_2024_values_read": False, "2025_values_read": False},
        "method": {"helper": "quanta_agents.meta_v6.risk_diagnostics.date_block_bootstrap", "helper_sha256": RISK_SHA,
            "block_length": 20, "repetitions": 1000, "seed": 20260909, "confidence": .95,
            "scheme": "noncircular moving contiguous date blocks; concatenate ceil(T/20) blocks and trim to T",
            "unit": "daily cross-sectional statistic, all stocks together", "stock_day_iid": False,
            "value": "saved raw rank_ic times original declared direction, applied exactly once",
            "horizons": HORIZONS, "calendar": "Reindex each factor/horizon to the same full 1197 original exchange sessions; retain NaN positions",
            "mean_check": "Compare original-direction mean and observed-date count against existing fit view at same horizon; atol=1e-12, rtol=0",
            "parameter_searches": 0, "multiple_testing_corrected": False, "independent_validation": False,
            "residual_ic_ci": "not_evaluable: daily residual IC series were not saved; do not refit or substitute aggregate residual IC"},
        "io": {"parquet_columns": ["date", "horizon", "rank_ic"], "predicate": f"date >= {START} and date <= {END}",
            "physical_decode_limit": "Parquet row groups may internally decode rows outside the predicate; returned, cached and statistical rows outside fit scope are rejected."},
        "factors": factors, "model_calls": 0, "factor_recomputations": 0, "account_executions": 0,
        "financial_success": False, "limitations": ["Descriptive percentile intervals, not calibrated tests or multiple-testing correction",
            "All 12 factors retained, including null intervals or local failures; no winner selection",
            "F7 is a risk-information hypothesis; return IC intervals alone cannot admit or reject its risk role",
            "No residual IC confidence intervals because its daily series is unavailable"]}
    write_once(output, value)
    return {"protocol": str(output), "sha256": file_hash(output), "factors": len(factors), "fit_sessions": COUNT,
            "saved_daily_ic_values_read": False}


def run(root):
    started = time.perf_counter()
    root, folder, _ = locations(root)
    completed(root)
    protocol_path = folder / "return_ic_bootstrap_protocol.json"
    protocol = read(protocol_path)
    require(protocol["version"] == VERSION and protocol["root"] == str(root), "wrong supplement protocol")
    require(protocol["method"]["horizons"] == HORIZONS and protocol["scope"]["date_range"] == [START, END], "changed supplement scope")
    for row in protocol["source_proofs"]:
        verify(row)
    write_once(folder / "return_ic_bootstrap_intent.json", {"created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": file_hash(protocol_path), "script_sha256": file_hash(__file__), "automatic_retry": False})
    dates = pd.DatetimeIndex(protocol["scope"]["fit_calendar_dates"])
    require(len(dates) == COUNT and digest([str(x.date()) for x in dates]) == protocol["scope"]["fit_calendar_sha256"], "fit calendar changed")
    view = read(folder / "fit_factor_view.json")
    require(view["fit_signal_sessions"] == COUNT and view["scope"]["last_allowed_signal_date"] == END, "fit view scope mismatch")
    fit_by_key = {row["factor_key"]: row for row in view["factors"]}
    require(list(fit_by_key) == KEYS, "fit view factor list changed")
    results = []
    for factor in protocol["factors"]:
        step = time.perf_counter()
        key = factor["factor_key"]
        output = {**factor, "horizons": [], "residual_ic_ci_status": "not_evaluable_saved_daily_series_missing"}
        try:
            require(factor["source_factor_status"] == "evaluated", "source factor was not evaluated")
            require(factor["daily_ic_available_at_protocol"], "saved daily IC unavailable when protocol frozen")
            source = factor["daily_ic_expected_proof"]
            verify(source)
            daily = pd.read_parquet(source["path"], columns=protocol["io"]["parquet_columns"],
                filters=[("date", ">=", pd.Timestamp(START)), ("date", "<=", pd.Timestamp(END))])
            verify(source)
            require(not daily.date.isna().any() and daily.date.isin(dates).all(), "returned daily values escape frozen fit calendar")
            require(daily.horizon.isin(HORIZONS).all() and not daily.duplicated(["date", "horizon"]).any(), "invalid daily horizon/date keys")
            require(not np.isinf(daily.rank_ic.to_numpy()).any(), "infinite saved IC")
            fit = fit_by_key[key]
            require(fit["direction"] == factor["direction"] and fit["factor_id"] == factor["factor_id"], "factor direction/identity mismatch")
            wide = daily.pivot(index="date", columns="horizon", values="rank_ic").reindex(index=dates, columns=HORIZONS)
            wide.columns = [str(h) for h in HORIZONS]
            wide = wide * factor["direction"]
            statistics = risk_diagnostics.date_block_bootstrap(wide, block_length=20, repetitions=1000, seed=20260909, confidence=.95)
            for row in statistics.to_dict("records"):
                horizon = int(row.pop("statistic"))
                original = next(item for item in fit["by_horizon_and_year"] if item["horizon"] == horizon and item["year"] is None)
                expected = original["mean_rank_ic_oriented"]
                match = (row["mean"] is None and expected is None) or (row["mean"] is not None and expected is not None
                    and bool(np.isclose(row["mean"], expected, rtol=0, atol=1e-12)))
                row.update({"horizon": horizon, "mean_fit_view": expected, "mean_matches_fit_view": bool(match),
                    "observed_dates_fit_view": original["ic_days"],
                    "observed_dates_match_fit_view": row["observed_dates"] == original["ic_days"],
                    "abs_mean_difference": abs(row["mean"]-expected) if row["mean"] is not None and expected is not None else None})
                output["horizons"].append(row)
            require(all(row["mean_matches_fit_view"] and row["observed_dates_match_fit_view"] for row in output["horizons"]), "saved daily IC disagrees with original fit view")
            output.update(status="completed", predicate_rows_returned=len(daily), mean_checks_passed=True)
        except Exception as exc:
            output.update(status="failed", error_type=type(exc).__name__, error=str(exc), mean_checks_passed=False)
        output["wall_seconds"] = time.perf_counter() - step
        results.append(output)
    for row in protocol["source_proofs"]:
        verify(row)
    require(file_hash(protocol_path) == read(folder / "return_ic_bootstrap_intent.json")["protocol_sha256"], "protocol changed while running")
    failed = sum(row["status"] != "completed" for row in results)
    result = {"version": VERSION, "status": "completed" if failed == 0 else "completed_with_local_failures",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(), "protocol_sha256": file_hash(protocol_path),
        "cycle_id": CYCLE, "factor_index_sha256": file_hash(folder / "factor_index.json"),
        "fit_factor_view_sha256": file_hash(folder / "fit_factor_view.json"),
        "factor_job_result_sha256": file_hash(folder / "jobs/01_factor_execution/job_result.json"),
        "source_proofs": protocol["source_proofs"], "scope": protocol["scope"], "method": protocol["method"],
        "proposal_timing": protocol["proposal_timing"], "limitations": protocol["limitations"],
        "factors": results, "factor_count": len(results), "completed_factors": len(results)-failed, "failed_factors": failed,
        "all_mean_checks_passed": failed == 0, "runtime": {"wall_seconds": time.perf_counter()-started,
            "python": sys.version, "pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pyarrow.__version__},
        "model_calls": 0, "factor_recomputations": 0, "account_executions": 0, "financial_success": False}
    write_once(folder / "return_ic_bootstrap.json", result)
    return {"status": result["status"], "factor_count": len(results), "failed_factors": failed,
        "all_mean_checks_passed": result["all_mean_checks_passed"], "wall_seconds": result["runtime"]["wall_seconds"],
        "output": str(folder / "return_ic_bootstrap.json"), "sha256": file_hash(folder / "return_ic_bootstrap.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(serial(prepare(args.root) if args.action == "prepare" else run(args.root)))
