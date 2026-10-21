# -*- coding: utf-8 -*-
"""独立回测九个参数固定的分钟试算因子。"""

from __future__ import annotations

import argparse
import gc
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import build_trial_minute_features as trial_cache
import duckdb_factor_store as factor_store
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "trial_minute_factor_results" / "final"

FACTOR_META = {
    code: {
        "name": spec["name"],
        "source_frequency": "分钟输入",
        "signal_frequency": spec["signal_frequency"],
        "expected_direction": "unknown",
        "strict_status": "trial_formula",
        "formula_mode": "trial",
        "formula_label": "试算",
        "note": spec["trial_reason"],
    }
    for code, spec in trial_cache.TRIAL_PARAMETERS.items()
}

BASE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "float_shares",
    "qfq_ratio",
    "vwap_outside_range_1pct",
]

TRIAL_COLUMNS = [
    "m15_vsa_low_close_gap_trial",
    "a22_intraday_snr_trial",
    "may13_intraday_abnormal_volume_trial",
    "j06_d02_jump_indicator_trial",
    "j06_d07_high_frequency_resiliency_trial",
    "j06_d20_rm_min_trial",
    "j06_d20_std_min_trial",
    "j09_d07_brave_peak_daily_trial",
    "j09_d11_naive_active_share_trial",
    "j09_d19_skewed_momentum_daily_trial",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument(
        "--trial-cache-dir", type=Path, default=trial_cache.DEFAULT_OUTPUT_DIR
    )
    parser.add_argument("--database", type=Path, default=factor_store.DEFAULT_DATABASE)
    parser.add_argument(
        "--duckdb-temp-dir", type=Path, default=factor_store.DEFAULT_TEMP_DIR
    )
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--min-stocks", type=int, default=50)
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def _compact_panel(panel: pd.DataFrame) -> pd.DataFrame:
    for column in panel.select_dtypes(include=["number"]).columns:
        panel[column] = panel[column].astype(np.float32)
    panel["code"] = panel["code"].astype("string")
    return panel


def load_panel_with_duckdb(args: argparse.Namespace) -> tuple[pd.DataFrame, float]:
    base_glob = Path(args.base_cache_glob)
    trial_glob = Path(args.trial_cache_dir) / "*.parquet"
    if not list(base_glob.parent.glob(base_glob.name)):
        raise FileNotFoundError(f"没有基础日值文件：{base_glob}")
    if not list(trial_glob.parent.glob(trial_glob.name)):
        raise FileNotFoundError(f"没有试算日值文件：{trial_glob}")
    base_projection = ", ".join(f'b."{column}"' for column in BASE_COLUMNS)
    trial_projection = ", ".join(f't."{column}"' for column in TRIAL_COLUMNS)
    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.duckdb_temp_dir),
        threads=args.duckdb_threads,
        memory_limit=args.duckdb_memory_limit,
    )
    started = time.perf_counter()
    with factor_store.connect(settings) as connection:
        panel = connection.execute(
            f"""
            SELECT
                b.date,
                lower(b.code) AS code,
                {base_projection},
                {trial_projection}
            FROM read_parquet(
                '{trial_cache._sql_path(base_glob)}', union_by_name = true
            ) AS b
            INNER JOIN read_parquet(
                '{trial_cache._sql_path(trial_glob)}', union_by_name = true
            ) AS t
              ON b.date = t.date
             AND lower(b.code) = lower(t.code)
            WHERE b.date BETWEEN ? AND ?
            ORDER BY b.date, lower(b.code)
            """,
            [pd.Timestamp(args.start), pd.Timestamp(args.end)],
        ).fetchdf()
    if panel.empty:
        raise ValueError("基础日值与试算日值没有可合并记录。")
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("合并后出现重复的日期和股票代码。")
    return _compact_panel(panel), time.perf_counter() - started


def _rolling_mean_std(
    values: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(source)
    filled = np.where(valid, source, 0.0)
    cumulative_sum = np.cumsum(filled, axis=0, dtype=np.float64)
    cumulative_square = np.cumsum(filled * filled, axis=0, dtype=np.float64)
    cumulative_count = np.cumsum(valid.astype(np.int32), axis=0, dtype=np.int32)
    sums = cumulative_sum.copy()
    squares = cumulative_square.copy()
    counts = cumulative_count.copy()
    if window < len(source):
        sums[window:] = cumulative_sum[window:] - cumulative_sum[:-window]
        squares[window:] = cumulative_square[window:] - cumulative_square[:-window]
        counts[window:] = cumulative_count[window:] - cumulative_count[:-window]
    mean = np.full(source.shape, np.nan, dtype=np.float64)
    variance = np.full(source.shape, np.nan, dtype=np.float64)
    np.divide(sums, counts, out=mean, where=counts > 0)
    np.divide(squares, counts, out=variance, where=counts > 0)
    variance = np.maximum(variance - mean * mean, 0.0)
    standard = np.sqrt(variance)
    incomplete = counts < window
    mean[incomplete] = np.nan
    standard[incomplete] = np.nan
    if window > 1:
        mean[: window - 1] = np.nan
        standard[: window - 1] = np.nan
    return mean, standard


def cross_sectional_reverse_percentile(
    values: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    """每日按降序计算平均并列名次百分位，最大值接近0、最小值为1。"""

    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    for day in range(len(source)):
        valid = membership[day] & np.isfinite(source[day])
        if int(valid.sum()) < min_stocks:
            continue
        ranks = pd.Series(source[day, valid]).rank(
            method="average", ascending=False, pct=True
        )
        result[day, valid] = ranks.to_numpy(dtype=np.float32)
    return result


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    settings: january.Settings,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    names = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "amount": "amount",
        "float_shares": "float_shares",
        "qfq_ratio": "qfq_ratio",
    }
    arrays = {
        key: january.panel_to_matrix(
            panel, source, dates, stocks, date_map, stock_map
        )
        for key, source in names.items()
    }
    arrays["raw_close"] = np.full(arrays["close"].shape, np.nan, dtype=np.float64)
    np.divide(
        arrays["close"],
        arrays["qfq_ratio"],
        out=arrays["raw_close"],
        where=np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] != 0),
    )
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    def cache(column: str) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map
        )

    factors: dict[str, np.ndarray] = {
        "M15_VSA_LOW_CLOSE_GAP": cache("m15_vsa_low_close_gap_trial"),
        "A22_INTRADAY_SNR": cache("a22_intraday_snr_trial"),
        "MAY13_INTRADAY_ABNORMAL_VOLUME": cache(
            "may13_intraday_abnormal_volume_trial"
        ),
        "J06_D07_HIGH_FREQUENCY_RESILIENCY": cache(
            "j06_d07_high_frequency_resiliency_trial"
        ),
        "J09_D11_NAIVE_ACTIVE_SHARE": cache(
            "j09_d11_naive_active_share_trial"
        ),
    }
    jump_mean, _ = _rolling_mean_std(cache("j06_d02_jump_indicator_trial"), 20)
    factors["J06_D02_JUMP_INTENSITY"] = jump_mean

    rm_min = cache("j06_d20_rm_min_trial")
    std_min = cache("j06_d20_std_min_trial")
    reverse_rank = cross_sectional_reverse_percentile(
        rm_min, membership, settings.min_stocks
    )
    co_min = np.full(std_min.shape, np.nan, dtype=np.float64)
    np.divide(reverse_rank, std_min, out=co_min, where=np.isfinite(std_min) & (std_min > 0))
    factors["J06_D20_CO_MIN"] = co_min

    brave_mean, brave_std = _rolling_mean_std(
        cache("j09_d07_brave_peak_daily_trial"), 20
    )
    factors["J09_D07_BRAVE_PEAK"] = 0.5 * brave_mean + 0.5 * brave_std

    _, skewed_std = _rolling_mean_std(
        cache("j09_d19_skewed_momentum_daily_trial"), 20
    )
    factors["J09_D19_SKEWED_MOMENTUM_RANGE"] = skewed_std
    return {
        code: np.asarray(values, dtype=np.float32) for code, values in factors.items()
    }, arrays


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        values = factors.get(code)
        rows.append(
            {
                "factor": code,
                **meta,
                "calculated": values is not None,
                "finite_values": int(np.isfinite(values).sum()) if values is not None else 0,
            }
        )
    return pd.DataFrame(rows)


def _mark_trial(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["formula_mode"] = "trial"
    result["formula_label"] = "试算"
    return result


def save_trial_factor_values(
    factors: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
    stocks: list[str],
    output_dir: Path,
    directory_name: str,
) -> None:
    destination = output_dir / directory_name
    destination.mkdir(parents=True, exist_ok=True)
    stock_array = np.asarray(stocks, dtype=object)
    for name, values in factors.items():
        rows, columns = np.nonzero(np.isfinite(values))
        frame = pd.DataFrame(
            {
                "date": dates[rows],
                "code": stock_array[columns],
                "value": values[rows, columns],
                "formula_mode": "trial",
                "formula_label": "试算",
            }
        )
        path = destination / f"{name}.parquet"
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, compression="zstd", index=False)
        temporary.replace(path)


def build_methodology(
    args: argparse.Namespace,
    settings: january.Settings,
    read_seconds: float,
) -> dict[str, object]:
    return {
        "formula_mode": "trial",
        "formula_label": "试算",
        "all_outputs_are_trial": True,
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        },
        "duckdb": {
            "database": str(args.database),
            "temp_dir": str(args.duckdb_temp_dir),
            "threads": args.duckdb_threads,
            "memory_limit": args.duckdb_memory_limit,
            "read_and_join_seconds": read_seconds,
            "copied_source_data": False,
        },
        "factor_meta": FACTOR_META,
        "factor_parameters": trial_cache.TRIAL_PARAMETERS,
        "loaded_sources": {
            "base_daily": BASE_COLUMNS,
            "trial_daily": TRIAL_COLUMNS,
        },
        "backtest_conventions": {
            "universe": "历史中证800与中证1000成分并集",
            "daily_trade_frequency": "日度因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "liquidity": "股价不低于3元，20日平均成交额不低于500万元",
            "trial_rule": "九项全部为参数固定试算，不作为图片原式严格结果",
        },
        "created_at": pd.Timestamp.now().isoformat(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = january.Settings(
        cache_dir=Path(args.trial_cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
        min_stocks=args.min_stocks,
    )
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    january.FACTOR_META = FACTOR_META
    started = time.time()

    print("[1/6] 读取基础日值和独立试算日值...", flush=True)
    panel, read_seconds = load_panel_with_duckdb(args)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(
        settings.qlib_dir, settings.start, settings.end
    )
    membership, missing_members = january.build_membership(dates, stocks, membership_rows)
    january.atomic_write_csv(
        _mark_trial(missing_members),
        settings.output_dir / "qlib_members_without_cache.csv",
    )

    print("[2/6] 生成9个试算因子矩阵...", flush=True)
    factors, arrays = build_factor_matrices(
        panel, dates, stocks, date_map, stock_map, membership, settings
    )
    del panel
    gc.collect()
    original_signals = january.build_original_signals(
        factors, dates, settings.backtest_start
    )

    january.atomic_write_csv(
        build_status_frame(factors), settings.output_dir / "factor_status.csv"
    )
    january.atomic_write_json(
        build_methodology(args, settings, read_seconds),
        settings.output_dir / "methodology.json",
    )
    if args.save_factor_values:
        save_trial_factor_values(
            factors, dates, stocks, settings.output_dir, "trial_factor_values"
        )
        save_trial_factor_values(
            original_signals,
            dates,
            stocks,
            settings.output_dir,
            "trial_original_signals",
        )
    if args.skip_backtest:
        print(f"只生成试算因子，耗时{time.time() - started:.1f}秒。", flush=True)
        return 0

    print("[3/6] 生成历史股票范围和流动性筛选...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )

    print("[4/6] 计算试算IC和分组收益...", flush=True)
    daily_frames = []
    original_frequency_frames = []
    rebalance_frames = []
    original_rebalance_frames = []
    for position, (factor_name, factor) in enumerate(factors.items(), start=1):
        daily = january.calculate_daily_ic(
            factor_name, factor, future_return, eligible, dates, settings
        )
        original_frequency = january.calculate_original_frequency_ic(
            factor_name,
            original_signals[factor_name],
            future_return,
            arrays,
            eligible,
            dates,
            settings,
        )
        daily_frames.append(daily)
        original_frequency_frames.append(original_frequency)
        rebalance_frames.append(
            january.run_five_day_backtest(
                factor_name, factor, future_return, eligible, arrays, dates, settings
            )
        )
        original_rebalance_frames.append(
            january.run_original_frequency_backtest(
                factor_name,
                original_signals[factor_name],
                future_return,
                eligible,
                arrays,
                dates,
                settings,
            )
        )
        mean_ic = daily["ic"].mean() if not daily.empty else np.nan
        print(
            f"  {position}/9 {factor_name}：统一5日IC={mean_ic:+.4f}（试算）",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True)
    original_frequency_ic = pd.concat(original_frequency_frames, ignore_index=True)
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = pd.concat(rebalance_frames, ignore_index=True)
    original_rebalance = pd.concat(original_rebalance_frames, ignore_index=True)
    daily_summary = january.summarize_ic(
        daily_ic, "date", "unified_5d", settings.split_date
    )
    original_summary = january.summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = january.summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        january.summarize_original_frequency_backtest(
            original_rebalance, original_summary, settings.split_date
        )
    )

    print("[5/6] 保存全部带试算标记的结果...", flush=True)
    outputs = {
        "daily_ic.csv": daily_ic,
        "monthly_ic.csv": monthly_ic,
        "original_frequency_ic.csv": original_frequency_ic,
        "original_frequency_ic_summary.csv": original_summary,
        "ic_summary.csv": pd.concat(
            [daily_summary, original_summary], ignore_index=True
        ),
        "rebalance_returns.csv": rebalance,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
        "original_frequency_rebalance_returns.csv": original_rebalance,
        "original_frequency_backtest_summary.csv": original_backtest_summary,
        "original_frequency_yearly_backtest.csv": original_yearly_backtest,
    }
    for name, frame in outputs.items():
        january.atomic_write_csv(_mark_trial(frame), settings.output_dir / name)

    print("[6/6] 九项试算完成。", flush=True)
    print(f"总耗时{time.time() - started:.1f}秒。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
