# -*- coding: utf-8 -*-
"""用 DuckDB 读取每日 Parquet，分批回测新补的历史因子。"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

import historical_backtest_core as core
import historical_cached_factors as cached
import historical_factor_data as factor_data
import historical_risk_factors as risk
import historical_standard_factors as standard


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_AUDIT = (
    BASE_DIR / "historical_calendar_backtest_results" / "formula_support_audit.csv"
)
DEFAULT_OUTPUT = BASE_DIR / "historical_calendar_backtest_results" / "runs" / "new_factors"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
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
        default=Path(r"E:\codex_duckdb_temp\factor_calendar"),
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["standard", "risk", "cached", "additional", "missing_minute"],
    )
    parser.add_argument("--unit-ids", nargs="+")
    return parser.parse_args()


def _optional_additional():
    try:
        return importlib.import_module("historical_additional_factors")
    except ModuleNotFoundError:
        return None


def _optional_missing_minute():
    try:
        return importlib.import_module("historical_missing_minute_factors")
    except ModuleNotFoundError:
        return None


def _metadata(method: str, name: str) -> dict[str, object]:
    if method == "standard":
        return standard.get_factor_metadata(name)
    if method == "risk":
        spec = risk.get_factor_spec(name)
        return {
            "name": spec.name,
            "required_columns": spec.required,
            "note": spec.note,
            "status": spec.status,
        }
    if method == "cached":
        return cached.get_factor_metadata(name)
    if method == "additional":
        module = _optional_additional()
        if module is None:
            raise KeyError(name)
        metadata = module.get_factor_metadata(name)
        if "required" in metadata and "required_columns" not in metadata:
            metadata = {**metadata, "required_columns": metadata["required"]}
        return metadata
    if method == "missing_minute":
        module = _optional_missing_minute()
        if module is None:
            raise KeyError(name)
        metadata = module.get_factor_metadata(name)
        if "required" in metadata and "required_columns" not in metadata:
            metadata = {**metadata, "required_columns": metadata["required"]}
        return metadata
    raise KeyError(method)


def _required_columns(rows: pd.DataFrame) -> set[str]:
    columns = set(factor_data.CORE_COLUMNS)
    for row in rows.itertuples(index=False):
        try:
            metadata = _metadata(row.calculation_method, row.registered_name)
        except (KeyError, ValueError):
            continue
        required = metadata.get("required_fields", metadata.get("required_columns", ()))
        for item in required:
            text = str(item)
            if text in {
                "returns或close",
                "market_return或membership",
                "market_cap",
                "return_20d",
                "turnover_20d",
                "volatility_20d",
            }:
                continue
            if "或" not in text and text.isascii():
                columns.add(text)
    # 风险和补充模块会按需读取这些已存在字段；不存在的列由读取报告列出。
    columns.update(
        {
            "pv_corr_daily",
            "vwap_qfq",
            "overnight_return",
            "realized_skew_1m",
            "realized_skew_5m",
            "rvjn_5m",
            "large_up_jump_5m",
            "small_down_jump_vol_5m",
            "up_jump_volatility_5m",
            "morning_afternoon_volume_ratio",
            "time_avg_relative_price",
            "absret_lag_amount_corr",
            "lag_absret_amount_corr",
            "adj_lag_absret_amount_corr_daily",
            "volume_std_1m",
            "j09_d11_naive_active_share_trial",
            "consistent_trading_share_daily_trial",
            "consistent_buying_share_daily_trial",
            "consistent_selling_share_daily_trial",
            "a22_intraday_snr_trial",
        }
    )
    return columns


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    return core.january.rolling_mean(values, window, require_full=True).astype(np.float32)


def _rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    return core.january.rolling_mean_std(values, window)[1].astype(np.float32)


def _add_derived(arrays: dict[str, np.ndarray]) -> None:
    close = arrays["close"]
    return20 = np.full(close.shape, np.nan, dtype=np.float32)
    np.divide(
        close[20:],
        close[:-20],
        out=return20[20:],
        where=np.isfinite(close[20:]) & np.isfinite(close[:-20]) & (close[:-20] != 0),
    )
    return20[20:] -= 1.0
    arrays["return_20d"] = return20
    arrays["turnover_20d"] = _rolling_mean(arrays["turnover"], 20)
    arrays["volatility_20d"] = _rolling_std(arrays["returns"], 20)


def _calculate(
    method: str,
    name: str,
    arrays: dict[str, np.ndarray],
    membership: np.ndarray,
    dates: pd.DatetimeIndex,
) -> np.ndarray:
    if method == "standard":
        return standard.calculate_factor(name, arrays)
    if method == "risk":
        return risk.compute_factor(name, arrays, membership=membership)
    if method == "cached":
        return cached.compute_factor(name, arrays)
    if method == "additional":
        module = _optional_additional()
        if module is None:
            raise ModuleNotFoundError("historical_additional_factors.py尚未生成")
        return module.compute_factor(name, arrays, membership=membership, dates=dates)
    if method == "missing_minute":
        module = _optional_missing_minute()
        if module is None:
            raise ModuleNotFoundError("historical_missing_minute_factors.py尚未生成")
        return module.compute_factor(name, arrays, membership=membership, dates=dates)
    raise KeyError(method)


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
    audit = pd.read_csv(args.audit, encoding="utf-8-sig")
    selected = audit[audit["calculation_method"].isin(args.methods)].copy()
    if args.unit_ids:
        selected = selected[selected["unit_id"].isin(args.unit_ids)].copy()
    if selected.empty:
        raise ValueError("没有符合条件的计算单元")
    settings = core.BacktestSettings(
        qlib_dir=args.qlib_dir,
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
        report_start=args.report_start,
        report_end=args.report_end,
    )
    data_settings = factor_data.DataSettings(
        start=args.start,
        end=args.end,
        threads=args.duckdb_threads,
        memory_limit=args.duckdb_memory_limit,
        temp_dir=args.duckdb_temp_dir,
    )
    print(f"[1/3] DuckDB读取{len(_required_columns(selected))}个每日字段...", flush=True)
    arrays, dates, stocks, membership, eligible, missing_columns = (
        factor_data.load_factor_arrays(
            _required_columns(selected), settings, data_settings
        )
    )
    _add_derived(arrays)
    print(
        f"  {len(dates)}个交易日，{len(stocks)}只股票；缺少请求字段{len(missing_columns)}个。",
        flush=True,
    )

    print(f"[2/3] 逐个计算并回测{len(selected)}个公式频率单元...", flush=True)
    status_rows: list[dict[str, object]] = []
    rebalances: list[pd.DataFrame] = []
    summaries: list[pd.DataFrame] = []
    yearlies: list[pd.DataFrame] = []
    # 同一公式不同回测频率共享因子值，避免重复计算。
    for _, group in selected.groupby(
        ["formula_key", "calculation_method", "registered_name"], sort=False
    ):
        first = group.iloc[0]
        factor = None
        error = ""
        try:
            factor = _calculate(
                str(first["calculation_method"]),
                str(first["registered_name"]),
                arrays,
                membership,
                dates,
            )
        except Exception as exc:  # 单个因子失败不能中断整批。
            error = f"{type(exc).__name__}: {exc}"
        finite_values = int(np.isfinite(factor).sum()) if factor is not None else 0
        for row in group.itertuples(index=False):
            frame = pd.DataFrame()
            unit_error = error
            if factor is not None and finite_values:
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
                    unit_error = f"{type(exc).__name__}: {exc}"
            status = "completed" if not frame.empty else "failed"
            status_rows.append(
                {
                    "unit_id": row.unit_id,
                    "formula_key": row.formula_key,
                    "name": row.representative_name,
                    "frequency": row.normalized_frequency,
                    "calculation_method": row.calculation_method,
                    "registered_name": row.registered_name,
                    "status": status,
                    "finite_values": finite_values,
                    "periods_2022_2024": len(frame),
                    "error": unit_error,
                }
            )
            if frame.empty:
                continue
            frame["formula_key"] = row.formula_key
            frame["calculation_method"] = row.calculation_method
            summary, yearly = core.summarize_unit(frame)
            rebalances.append(frame)
            summaries.append(summary)
            yearlies.append(yearly)
        del factor
        gc.collect()

    print("[3/3] 写出结果...", flush=True)
    output = args.output_dir
    status = pd.DataFrame(status_rows)
    rebalance = pd.concat(rebalances, ignore_index=True) if rebalances else pd.DataFrame()
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    yearly = pd.concat(yearlies, ignore_index=True) if yearlies else pd.DataFrame()
    _atomic_csv(status, output / "unit_status.csv")
    _atomic_csv(summary, output / "unit_backtest_summary.csv")
    _atomic_csv(yearly, output / "unit_yearly_returns.csv")
    _atomic_parquet(rebalance, output / "unit_rebalance_returns.parquet")
    methodology = {
        "settings": {key: str(value) for key, value in asdict(settings).items()},
        "duckdb": {
            "threads": args.duckdb_threads,
            "memory_limit": args.duckdb_memory_limit,
            "temp_dir": str(args.duckdb_temp_dir),
            "parquet_sources": {key: str(value) for key, value in factor_data.SOURCE_GLOBS.items()},
        },
        "selected_methods": args.methods,
        "selected_units": len(selected),
        "completed_units": int((status["status"] == "completed").sum()),
        "failed_units": int((status["status"] != "completed").sum()),
        "missing_requested_columns": missing_columns,
        "elapsed_seconds": time.time() - started,
    }
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "methodology.json.tmp"
    temporary.write_text(json.dumps(methodology, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output / "methodology.json")
    print(
        f"完成{methodology['completed_units']}/{len(selected)}个单元，耗时{methodology['elapsed_seconds']:.1f}秒。",
        flush=True,
    )
    return 0 if methodology["failed_units"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
