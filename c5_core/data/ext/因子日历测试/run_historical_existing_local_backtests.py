# -*- coding: utf-8 -*-
"""用 DuckDB 重跑一月至六月旧程序中命中的历史因子。"""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

import historical_backtest_core as core
import historical_existing_duckdb_factors as existing


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_UNITS = BASE_DIR / "historical_calendar_backtest_results" / "formula_units.csv"
DEFAULT_OUTPUT = BASE_DIR / "historical_calendar_backtest_results" / "runs" / "existing_local"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=Path, default=DEFAULT_UNITS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--report-start", default="2022-01-01")
    parser.add_argument("--report-end", default="2024-12-31")
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--duckdb-memory-limit", default="3GB")
    parser.add_argument(
        "--duckdb-temp-dir",
        type=Path,
        default=Path(r"E:\codex_duckdb_temp\factor_calendar_existing"),
    )
    parser.add_argument("--groups", nargs="+", choices=sorted(existing.GROUPS))
    parser.add_argument("--unit-ids", nargs="+", help="只回测指定计算单元")
    return parser.parse_args()


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    started = time.time()
    settings = core.BacktestSettings(
        qlib_dir=args.qlib_dir,
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
        report_start=args.report_start,
        report_end=args.report_end,
    )
    units = pd.read_csv(args.units, encoding="utf-8-sig")
    units["result_code"] = units["existing_ref"].fillna("").map(
        lambda value: existing.resolve_result_code(str(value)) if value else ""
    )
    units["group_name"] = units["result_code"].map(existing.CODE_TO_GROUP)
    units = units[units["group_name"].notna()].copy()
    local_names = {
        name for name, spec in existing.GROUPS.items() if spec.local_adapter
    }
    units = units[units["group_name"].isin(local_names)].copy()
    if args.groups:
        units = units[units["group_name"].isin(args.groups)].copy()
    if args.unit_ids:
        units = units[units["unit_id"].isin(args.unit_ids)].copy()
    if units.empty:
        raise ValueError("没有需要本入口重跑的计算单元")

    status_rows: list[dict[str, object]] = []
    rebalances: list[pd.DataFrame] = []
    summaries: list[pd.DataFrame] = []
    yearlies: list[pd.DataFrame] = []
    for group_name, group_units in units.groupby("group_name", sort=False):
        codes = sorted(group_units["result_code"].unique())
        print(f"[{group_name}] DuckDB读取日度Parquet，准备{len(codes)}个因子...", flush=True)
        panel = existing.load_group_panel(
            group_name,
            args.start,
            args.end,
            threads=args.duckdb_threads,
            memory_limit=args.duckdb_memory_limit,
            temp_dir=args.duckdb_temp_dir / group_name,
        )
        dates, stocks, date_map, stock_map, membership = core.make_context(
            panel, settings
        )
        factors, arrays = existing.build_group_factor_matrices(
            group_name,
            panel,
            args.start,
            args.end,
            qlib_dir=args.qlib_dir,
            min_stocks=settings.min_stocks,
            requested_codes=codes,
        )
        del panel
        if "raw_close" not in arrays and {"close", "qfq_ratio"}.issubset(arrays):
            raw = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
            np.divide(
                arrays["close"],
                arrays["qfq_ratio"],
                out=raw,
                where=np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] != 0),
            )
            arrays["raw_close"] = raw
        if "open_exit_mark" not in arrays:
            open_exit, close_exit = core.january.build_exit_price_matrices(
                arrays["open"], arrays["close"]
            )
            arrays["open_exit_mark"] = open_exit.astype(np.float32)
            arrays["close_exit_mark"] = close_exit.astype(np.float32)
        eligible = core.build_eligible(arrays, membership, settings)
        for row in group_units.itertuples(index=False):
            factor = factors.get(row.result_code)
            frame = pd.DataFrame()
            error = ""
            if factor is None:
                error = "原计算函数没有返回该代码"
            else:
                try:
                    frame = core.backtest_factor(
                        row.unit_id,
                        factor,
                        row.normalized_frequency,
                        arrays,
                        eligible,
                        dates,
                        settings,
                    )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            status_rows.append(
                {
                    "unit_id": row.unit_id,
                    "formula_key": row.formula_key,
                    "name": row.representative_name,
                    "frequency": row.normalized_frequency,
                    "result_code": row.result_code,
                    "group_name": group_name,
                    "status": "completed" if not frame.empty else "failed",
                    "finite_values": int(np.isfinite(factor).sum()) if factor is not None else 0,
                    "periods_2022_2024": len(frame),
                    "error": error,
                }
            )
            if frame.empty:
                continue
            frame["formula_key"] = row.formula_key
            frame["calculation_method"] = "existing_duckdb"
            summary, yearly = core.summarize_unit(frame)
            rebalances.append(frame)
            summaries.append(summary)
            yearlies.append(yearly)
        del factors, arrays, membership, eligible
        gc.collect()

    output = args.output_dir
    status = pd.DataFrame(status_rows)
    rebalance = pd.concat(rebalances, ignore_index=True) if rebalances else pd.DataFrame()
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    yearly = pd.concat(yearlies, ignore_index=True) if yearlies else pd.DataFrame()
    _atomic_csv(status, output / "unit_status.csv")
    _atomic_csv(summary, output / "unit_backtest_summary.csv")
    _atomic_csv(yearly, output / "unit_yearly_returns.csv")
    _atomic_parquet(rebalance, output / "unit_rebalance_returns.parquet")
    method = {
        "settings": {key: str(value) for key, value in asdict(settings).items()},
        "duckdb": {
            "threads": args.duckdb_threads,
            "memory_limit": args.duckdb_memory_limit,
            "temp_dir": str(args.duckdb_temp_dir),
        },
        "groups": sorted(units["group_name"].unique()),
        "selected_units": len(units),
        "completed_units": int((status["status"] == "completed").sum()),
        "failed_units": int((status["status"] != "completed").sum()),
        "elapsed_seconds": time.time() - started,
    }
    output.mkdir(parents=True, exist_ok=True)
    temp = output / "methodology.json.tmp"
    temp.write_text(json.dumps(method, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, output / "methodology.json")
    print(
        f"完成{method['completed_units']}/{method['selected_units']}个旧因子单元，耗时{method['elapsed_seconds']:.1f}秒。",
        flush=True,
    )
    return 0 if method["failed_units"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
