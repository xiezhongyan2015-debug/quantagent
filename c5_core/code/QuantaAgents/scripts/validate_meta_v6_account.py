"""One supervised real development account benchmark; no model or search.

Only the frozen original three seeds and 2016-2020 account scope are executed.
The 2015 price anchor/warmup and complete 648-symbol historical union are kept.
An exit-zero process is separate from the saved mechanical account checks.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / "output/research/meta_v6_factor_calendar_20260909/preparation"
DEFAULT_OUTPUT = ROOT / "output/validation/meta_v6_calendar_account_20260909"
START, END = "2016-01-01", "2020-12-31"
PIN_PATHS = [
    "scripts/validate_meta_v6_account.py", "src/quanta_agents/meta_v6/data.py",
    "src/quanta_agents/meta_v6/factors.py", "src/quanta_agents/meta_v6/portfolio.py",
    "src/quanta_agents/meta_v6/jobs.py", "src/quanta_agents/meta/factor_algebra.py",
    "src/quanta_agents/meta_v3/windows_job.py",
]


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def save_once(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str)


def verify_pins(declaration):
    for name, expected in declaration["source_sha256"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("frozen source changed: " + name)
    for name, proof in declaration["preparation_bindings"].items():
        if sha(proof["path"]) != proof["sha256"]:
            raise ValueError("frozen preparation changed: " + name)


def freeze(output):
    research = ROOT / "src/quanta_agents/meta_v6/research.py"
    tree = ast.parse(research.read_text(encoding="utf-8"))
    seeds = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "SEEDS" for target in node.targets))
    receipt = json.loads((PREP / "data_load_receipt.json").read_text(encoding="utf-8"))
    cache = PREP / "market_panel_cache" / receipt["first_load"]["cache_key"]
    bindings = {name: {"path": str(path), "sha256": sha(path)} for name, path in {
        "selection": PREP / "data_selection.json", "source_inventory": PREP / "source_inventory.json",
        "load_receipt": PREP / "data_load_receipt.json", "cache_manifest": cache / "manifest.json",
        "cache_payload": cache / "panel.npz"}.items()}
    declaration = {"version": "v6_real_calendar_account_engineering_1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "real 648-symbol continuous-account throughput and existing-prior development reference; no new strategy/model search",
        "evaluation": {"start": START, "end": END, "warmup_start": "2015-01-01",
            "account_results_2021_2024_generated": False, "annual_capital_reset": False},
        "source_sha256": {path: sha(ROOT / path) for path in PIN_PATHS},
        "preparation_bindings": bindings, "base_panel_fingerprint": receipt["panel_fingerprint"],
        "expected_shape": receipt["shape"], "seeds": seeds,
        "seed_source": {"path": str(research), "sha256_at_freeze": sha(research),
            "binding": "frozen SEEDS literal embedded above; no model or research controller imported"},
        "portfolio": {"name": "original_calendar_three_seed_equal_rank_top20_5session",
            "factor_weights": {s["name"]: s["direction"] for s in seeds}, "top_n": 20,
            "rebalance_sessions": 5, "gross_exposure": 1., "max_stock_weight": .05,
            "weighting": "equal", "market_filter": "none", "rebalance_schedule": "sessions",
            "membership_buffer": 0, "crowding_gate_factor": ""},
        "account_policy": {"capital": 1000000., "risk_free_rate": .02,
            "buy_commission": .0003, "sell_commission": .0003,
            "sell_levy_before_2023_08_28": .001, "sell_levy_from_2023_08_28": .0005,
            "slippage": .001, "min_commission": 5., "lot_size": 100,
            "max_prior_day_amount_fraction": .01},
        "budget": {"timeout_seconds": 120, "max_output_bytes": 256 * 1024**2,
            "account_runs": 1, "model_calls": 0, "implicit_retry": False},
        "limitations": ["all supplied dates previously exposed development",
            "adjusted-unit accounting approximation, no separate raw dividend/withholding ledger",
            "opening fills and prior-day capacity are approximations",
            "a passing process or profitable exposed reference does not establish financial target success"]}
    output.mkdir(parents=True, exist_ok=True)
    save_once(output / "declaration.json", declaration)
    return declaration


def worker(output):
    import numpy as np
    import pandas as pd
    from quanta_agents.meta_v6.data import MarketPanel, _MemorySample, load_market_panel
    from quanta_agents.meta_v6.factors import FactorEngine, FactorSpec
    from quanta_agents.meta_v6.portfolio import AccountPolicy, DailyAccount, PortfolioSpec, target_weights

    declaration = json.loads((output / "declaration.json").read_text(encoding="utf-8"))
    verify_pins(declaration)
    phases = {}
    begun = datetime.now(timezone.utc).isoformat()
    with _MemorySample() as meter:
        before = time.perf_counter()
        complete = load_market_panel("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025",
            start="2015-01-01", end="2024-12-31", authorized_start="2015-01-01", authorized_end="2024-12-31",
            calendar_path="/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt",
            membership_path="/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt", cache_dir=PREP / "market_panel_cache")
        assert complete.fingerprint() == declaration["base_panel_fingerprint"], "base panel identity changed"
        assert list(complete.eligible.shape) == declaration["expected_shape"] == [2431, 648]
        assert complete.load_metrics["cache_hit"], "exact prebuilt cache was not reused"
        chosen = complete.dates <= pd.Timestamp(END)
        provenance = dict(complete.provenance)
        provenance["validation_view"] = {"end": END, "base_panel_fingerprint": complete.fingerprint(),
                                         "original_starting_anchors_preserved": True}
        panel = MarketPanel({k: v.loc[chosen].copy() for k, v in complete.fields.items()},
                            complete.eligible.loc[chosen].copy(), provenance)
        load_metrics = complete.load_metrics.copy()
        del complete
        phases["load_and_scoped_view_seconds"] = time.perf_counter() - before
        expected_dates = panel.dates[(panel.dates >= START) & (panel.dates <= END)]
        save_once(output / "worker_scope.json", {"started_at_utc": begun, "base_load": load_metrics,
            "view_shape": list(panel.eligible.shape), "evaluation_sessions": len(expected_dates),
            "evaluation_first_session": str(expected_dates.min().date()),
            "evaluation_last_session": str(expected_dates.max().date()),
            "symbols": panel.symbols, "eligible_stock_days": int(panel.eligible.loc[expected_dates].to_numpy().sum()),
            "evaluation_dates_2021_or_later": 0, "source_provenance": panel.provenance})

        # Save precise engineering failure evidence; never mask or drop mismatches.
        opening, raw, factor = [panel.fields[k] for k in ("open", "raw_open", "adjustment_factor")]
        known = opening.gt(0) & raw.gt(0) & factor.gt(0)
        mismatch = known & ~np.isclose(opening, raw * factor, rtol=1e-6, atol=1e-6)
        if mismatch.to_numpy().any():
            rows, cols = np.nonzero(mismatch.to_numpy())
            pd.DataFrame({"date": panel.dates[rows], "code": np.asarray(panel.symbols)[cols],
                "signal_open": opening.to_numpy()[rows, cols], "raw_open": raw.to_numpy()[rows, cols],
                "adjustment_factor": factor.to_numpy()[rows, cols]}).to_parquet(output / "open_unit_mismatches.parquet", index=False)
            raise ValueError("open/raw conversion mismatch retained; no rows dropped and no account run")
        before = time.perf_counter()
        policy, spec = AccountPolicy(**declaration["account_policy"]), PortfolioSpec(**declaration["portfolio"])
        account = DailyAccount(panel, policy)
        phases["account_initialization_seconds"] = time.perf_counter() - before
        before = time.perf_counter()
        engine = FactorEngine(panel)
        scores = {}
        for seed in declaration["seeds"]:
            factor_spec = FactorSpec(seed["name"], seed["expression"], metadata={"prior_seed": True, "direction": seed["direction"]})
            scores[seed["name"]] = engine.compute(factor_spec)
        assert engine.cache_info["label_evaluations"] == 0
        phases["three_factor_scores_seconds"] = time.perf_counter() - before
        before = time.perf_counter()
        targets = target_weights(panel, scores, spec, start=START, end=END)
        assert targets.columns.tolist() == panel.symbols and targets.index.max() <= pd.Timestamp(END)
        targets.to_parquet(output / "targets.parquet")
        phases["target_construction_seconds"] = time.perf_counter() - before
        save_once(output / "worker_preaccount.json", {"portfolio": asdict(spec), "policy": asdict(policy),
            "target_rows": len(targets), "target_columns": len(targets.columns),
            "target_sha256": sha(output / "targets.parquet"), "factor_engine_cache": engine.cache_info,
            "source_pins_verified": True, "phases": phases, "account_executions_started": 0})
        verify_pins(declaration)
        result = account.run(targets, start=START, end=END,
                             cancelled=lambda: (output / "cancel.request").exists())
        phases["account_execution_seconds"] = result["summary"]["duration_seconds"]
        daily, trades, annual = result["daily"], result["trades"], result["annual"]
        daily.to_parquet(output / "daily.parquet")
        trades.to_parquet(output / "trades.parquet", index=False)
        annual.to_parquet(output / "annual.parquet", index=False)
        save_once(output / "annual.json", json.loads(annual.to_json(orient="records")))
        save_once(output / "summary.json", result["summary"])
        previous = np.r_[policy.capital, daily.nav.to_numpy()[:-1]]
        checks = {
            "all_evaluation_dates_once": daily.index.equals(expected_dates),
            "complete_five_years": annual.year.tolist() == [2016, 2017, 2018, 2019, 2020] and bool(annual.full_calendar_year.all()),
            "continuous_daily_returns": bool(np.allclose(daily["return"], daily.nav / previous - 1, rtol=0, atol=1e-12)),
            "continuous_annual_returns": bool(np.allclose(annual["return"],
                np.array([g.nav.iloc[-1] for _, g in daily.groupby(daily.index.year)]) /
                np.r_[policy.capital, [g.nav.iloc[-1] for _, g in list(daily.groupby(daily.index.year))[:-1]]] - 1, rtol=0, atol=1e-10)),
            "cash_not_borrowed": bool(daily.cash.ge(-1e-7).all()),
            "fees_match_saved_trades": bool(np.isclose(daily.fees.sum(), trades.fees.sum() if "fees" in trades else 0., rtol=0, atol=1e-6)),
            "slippage_matches_saved_trades": bool(np.isclose(daily.slippage.sum(), trades.slippage.sum() if "slippage" in trades else 0., rtol=0, atol=1e-6)),
            "target_full_universe_648": len(targets.columns) == 648,
            "no_account_rows_after_2020": bool(daily.index.max() <= pd.Timestamp(END) and (trades.empty or trades.date.max() <= pd.Timestamp(END))),
            "formal_financial_success_not_claimed": result["summary"]["formal_target_success"] is False,
        }
        save_once(output / "mechanical_checks.json", checks)
        if not all(checks.values()):
            raise ValueError("saved account failed one or more mechanical checks")
        verify_pins(declaration)
    artifacts = {p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
                 for p in output.iterdir() if p.is_file() and p.name in {
                     "declaration.json", "worker_scope.json", "worker_preaccount.json", "targets.parquet",
                     "daily.parquet", "trades.parquet", "annual.parquet", "annual.json", "summary.json", "mechanical_checks.json"}}
    save_once(output / "account_validation_receipt.json", {"started_at_utc": begun,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(), "phases": phases,
        "resource_measurements": meter.metrics, "artifacts": artifacts, "checks": checks,
        "annual": json.loads(annual.to_json(orient="records")), "summary": result["summary"],
        "account_executions": 1, "model_calls": 0, "search_candidates": 0,
        "process_result_separate": "job_result.json", "financial_target_completed": False})
    print(json.dumps({"checks": checks, "phases": phases, "annual": json.loads(annual.to_json(orient="records")),
                      "summary": result["summary"]}, ensure_ascii=False, default=str), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((ROOT / "output/validation").resolve())
    if not output.name.startswith("meta_v6_calendar_account_"):
        raise ValueError("dedicated V6 account validation output directory required")
    if args.worker:
        try:
            worker(output)
        except BaseException as exc:
            save_once(output / "worker_failure.json", {"type": type(exc).__name__, "message": str(exc),
                "traceback": traceback.format_exc(), "financial_result_complete": False,
                "automatic_retry": False, "model_calls": 0})
            raise
    else:
        from quanta_agents.meta_v6.jobs import run_job
        declaration = freeze(output)
        result = run_job([sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(output)],
            cwd=ROOT, output_dir=output, timeout_seconds=declaration["budget"]["timeout_seconds"],
            max_output_bytes=declaration["budget"]["max_output_bytes"],
            env={"PYTHONPATH": str(ROOT / "src")})
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        if result["status"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
