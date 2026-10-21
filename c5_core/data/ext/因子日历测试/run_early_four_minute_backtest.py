# -*- coding: utf-8 -*-
"""回测三月至五月四个新增的严格分钟因子。"""

from __future__ import annotations

import argparse
import gc
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import build_shared_market_minute_stats as shared_market
import duckdb_factor_store as factor_store
import run_january_minute_backtest as january
import run_july_august_backtest as july_august


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "early_four_minute_factor_results" / "final"
DEFAULT_MARKET_STATS = shared_market.DEFAULT_OUTPUT


FACTOR_META = {
    "M25_HIGH_VOLUME_REALIZED_SKEW": {
        "name": "高成交量已实现偏度",
        "calendar_month": 3,
        "calendar_day": 25,
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "strict_formula",
        "note": "每日取成交量最高三分之一的分钟收益，按图片分母计算已实现偏度。",
    },
    "A06_DAZZLING_VOLATILITY_20D": {
        "name": "璀璨波动率",
        "calendar_month": 4,
        "calendar_day": 6,
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "strict_formula",
        "note": "先算异常成交量时刻起未来5分钟收益样本标准差，再做每日截面调整；月末合成近20日均值与样本标准差。",
    },
    "A10_P_SHAPED_VOLUME_DISTRIBUTION": {
        "name": "P型成交量分布",
        "calendar_month": 4,
        "calendar_day": 10,
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "strict_formula",
        "note": "按相同分钟收盘价合并成交量，从最大成交量价位向两侧累计至超过50%，取日高减所选最低价。",
    },
    "MAY25_TOP_RETURN_PRICE_RESONANCE": {
        "name": "高收益时刻价格共振",
        "calendar_month": 5,
        "calendar_day": 25,
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "strict_formula",
        "note": "取个股收益最高的5个分钟时刻；全市场平均收益的截面升序名次除以全市场收益样本标准差均值。",
    },
}

BASE_COLUMNS = [
    "open",
    "close",
    "amount",
    "qfq_ratio",
    "vwap_outside_range_1pct",
]

MINUTE_EXTRA_COLUMNS = [
    "high_volume_realized_skew_daily",
    "dazzling_volatility_daily",
    "p_shaped_volume_distribution_daily",
    "top_return_time_1",
    "top_return_time_2",
    "top_return_time_3",
    "top_return_time_4",
    "top_return_time_5",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--duckdb-temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument("--market-stats", default=str(DEFAULT_MARKET_STATS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def _compact_panel(panel: pd.DataFrame) -> pd.DataFrame:
    for column in panel.select_dtypes(include=["number"]).columns:
        panel[column] = panel[column].astype(np.float32)
    panel["date"] = pd.to_datetime(panel["date"])
    panel["code"] = panel["code"].astype("string")
    return panel


def load_inputs_with_duckdb(
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    market_source = shared_market.market_read_sql(args.market_stats)
    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.duckdb_temp_dir),
        threads=args.duckdb_threads,
        memory_limit=args.duckdb_memory_limit,
    )
    started = time.perf_counter()
    with factor_store.connect(settings) as connection:
        parameters = [pd.Timestamp(args.start), pd.Timestamp(args.end)]
        base_rows = int(
            connection.execute(
                "SELECT count(*) FROM source_base_daily WHERE date BETWEEN ? AND ?",
                parameters,
            ).fetchone()[0]
        )
        minute_rows = int(
            connection.execute(
                "SELECT count(*) FROM source_minute_extra_daily WHERE date BETWEEN ? AND ?",
                parameters,
            ).fetchone()[0]
        )
        panel = factor_store.read_joined_panel(
            connection,
            {
                "base_daily": BASE_COLUMNS,
                "minute_extra_daily": MINUTE_EXTRA_COLUMNS,
            },
            args.start,
            args.end,
        )
        market = connection.execute(
            """
            SELECT
                date,
                minute_of_day,
                return_stock_count,
                market_mean_return,
                market_std_return_samp
            FROM {market_source}
            WHERE date BETWEEN ? AND ?
            ORDER BY date, minute_of_day
            """.format(market_source=market_source),
            parameters,
        ).fetchdf()
    elapsed = time.perf_counter() - started
    # 分钟缓存可以包含基础股票范围之外的额外交易日；
    # 只要基础日表中的每一行都能匹配到分钟日值，回测输入就是完整的。
    if len(panel) != base_rows:
        raise ValueError(
            "分钟日值与基础日表覆盖不一致："
            f"基础日表={base_rows:,}，分钟日值={minute_rows:,}，合并结果={len(panel):,}。"
        )
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("合并后出现重复的日期和股票代码。")
    if market.duplicated(["date", "minute_of_day"]).any():
        raise ValueError("市场分钟统计表出现重复的日期和分钟。")
    return _compact_panel(panel), market, elapsed


def market_stats_to_arrays(
    market: pd.DataFrame, dates: pd.DatetimeIndex
) -> tuple[np.ndarray, np.ndarray]:
    shape = (len(dates), 1440)
    mean_values = np.full(shape, np.nan, dtype=np.float32)
    std_values = np.full(shape, np.nan, dtype=np.float32)
    if market.empty:
        return mean_values, std_values
    normalized_dates = pd.to_datetime(market["date"]).dt.normalize()
    rows = dates.get_indexer(normalized_dates)
    minutes = market["minute_of_day"].to_numpy(dtype=np.int32)
    valid = (rows >= 0) & (minutes >= 0) & (minutes < 1440)
    rows = rows[valid]
    minutes = minutes[valid]
    mean_values[rows, minutes] = market.loc[
        valid, "market_mean_return"
    ].to_numpy(dtype=np.float32)
    std_values[rows, minutes] = market.loc[
        valid, "market_std_return_samp"
    ].to_numpy(dtype=np.float32)
    return mean_values, std_values


def cross_sectional_abs_deviation(
    values: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for index in range(len(values)):
        valid = membership[index] & np.isfinite(values[index])
        if int(valid.sum()) < min_stocks:
            continue
        current = values[index, valid].astype(np.float64)
        result[index, valid] = np.abs(current - current.mean()).astype(np.float32)
    return result


def rolling_mean_sample_std(
    values: np.ndarray, window: int, block_size: int = 256
) -> tuple[np.ndarray, np.ndarray]:
    """按固定交易日窗口计算均值和样本标准差；窗口有缺值就不计算。"""

    source = np.asarray(values)
    means = np.full(source.shape, np.nan, dtype=np.float32)
    stds = np.full(source.shape, np.nan, dtype=np.float32)
    if window < 2 or len(source) < window:
        return means, stds
    for start in range(0, source.shape[1], block_size):
        stop = min(start + block_size, source.shape[1])
        block = source[:, start:stop].astype(np.float64)
        finite = np.isfinite(block)
        filled = np.where(finite, block, 0.0)
        sums = np.vstack(
            [np.zeros((1, stop - start)), np.cumsum(filled, axis=0)]
        )
        square_sums = np.vstack(
            [np.zeros((1, stop - start)), np.cumsum(filled * filled, axis=0)]
        )
        counts = np.vstack(
            [np.zeros((1, stop - start), dtype=np.int32), np.cumsum(finite, axis=0)]
        )
        rolling_sum = sums[window:] - sums[:-window]
        rolling_square_sum = square_sums[window:] - square_sums[:-window]
        rolling_count = counts[window:] - counts[:-window]
        good = rolling_count == window
        current_mean = rolling_sum / window
        variance = np.maximum(
            (rolling_square_sum - rolling_sum * rolling_sum / window)
            / (window - 1),
            0.0,
        )
        mean_block = np.where(good, current_mean, np.nan).astype(np.float32)
        std_block = np.where(good, np.sqrt(variance), np.nan).astype(np.float32)
        means[window - 1 :, start:stop] = mean_block
        stds[window - 1 :, start:stop] = std_block
    return means, stds


def _lookup_market_values(times: np.ndarray, values: np.ndarray) -> np.ndarray:
    result = np.full(times.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(times) & (times >= 0) & (times < values.shape[1])
    minute_index = np.where(valid, times, 0).astype(np.int32)
    looked_up = values[np.arange(len(times))[:, None], minute_index]
    result[valid] = looked_up[valid]
    return result


def compute_top_return_price_resonance(
    top_time_values: Sequence[np.ndarray],
    market_mean: np.ndarray,
    market_std: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    if len(top_time_values) != 5:
        raise ValueError("高收益时刻必须恰好有5个。")
    mean_sum = np.zeros(top_time_values[0].shape, dtype=np.float32)
    std_sum = np.zeros(top_time_values[0].shape, dtype=np.float32)
    mean_count = np.zeros(top_time_values[0].shape, dtype=np.uint8)
    std_count = np.zeros(top_time_values[0].shape, dtype=np.uint8)
    for times in top_time_values:
        current_mean = _lookup_market_values(times, market_mean)
        current_std = _lookup_market_values(times, market_std)
        good_mean = np.isfinite(current_mean)
        good_std = np.isfinite(current_std)
        mean_sum[good_mean] += current_mean[good_mean]
        std_sum[good_std] += current_std[good_std]
        mean_count[good_mean] += 1
        std_count[good_std] += 1
    rm_max = np.full(mean_sum.shape, np.nan, dtype=np.float32)
    std_max = np.full(std_sum.shape, np.nan, dtype=np.float32)
    np.divide(mean_sum, 5.0, out=rm_max, where=mean_count == 5)
    np.divide(std_sum, 5.0, out=std_max, where=std_count == 5)
    ranked_rm = july_august.cross_sectional_rank(rm_max, membership, min_stocks)
    result = np.full(ranked_rm.shape, np.nan, dtype=np.float32)
    np.divide(
        ranked_rm,
        std_max,
        out=result,
        where=np.isfinite(ranked_rm) & np.isfinite(std_max) & (std_max > 0),
    )
    return result


def build_factor_matrices(
    panel: pd.DataFrame,
    market: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    settings: january.Settings,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    def matrix(column: str) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=np.float32
        )

    arrays = {
        "open": matrix("open"),
        "close": matrix("close"),
        "amount": matrix("amount"),
        "qfq_ratio": matrix("qfq_ratio"),
    }
    arrays["raw_close"] = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    valid_ratio = np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] > 0)
    np.divide(
        arrays["close"],
        arrays["qfq_ratio"],
        out=arrays["raw_close"],
        where=valid_ratio,
    )
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    factors: dict[str, np.ndarray] = {
        "M25_HIGH_VOLUME_REALIZED_SKEW": matrix(
            "high_volume_realized_skew_daily"
        ),
        "A10_P_SHAPED_VOLUME_DISTRIBUTION": matrix(
            "p_shaped_volume_distribution_daily"
        ),
    }

    dazzling_daily = matrix("dazzling_volatility_daily")
    dazzling_adjusted = cross_sectional_abs_deviation(
        dazzling_daily, membership, settings.min_stocks
    )
    dazzling_mean, dazzling_std = rolling_mean_sample_std(
        dazzling_adjusted, window=20
    )
    factors["A06_DAZZLING_VOLATILITY_20D"] = (
        0.5 * dazzling_mean + 0.5 * dazzling_std
    ).astype(np.float32)

    market_mean, market_std = market_stats_to_arrays(market, dates)
    top_times = [matrix(f"top_return_time_{number}") for number in range(1, 6)]
    factors["MAY25_TOP_RETURN_PRICE_RESONANCE"] = (
        compute_top_return_price_resonance(
            top_times,
            market_mean,
            market_std,
            membership,
            settings.min_stocks,
        )
    )
    if set(factors) != set(FACTOR_META):
        raise AssertionError("四个正式因子的实现与说明不一致。")
    return factors, arrays


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
    return pd.DataFrame(rows).sort_values(
        ["calendar_month", "calendar_day", "factor"]
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = january.Settings(
        cache_dir=Path("."),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
    )
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    january.FACTOR_META = FACTOR_META
    started = time.time()

    print("[1/6] 读取分钟日值、基础日表和市场分钟统计...", flush=True)
    panel, market, read_seconds = load_inputs_with_duckdb(args)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(
        settings.qlib_dir, settings.start, settings.end
    )
    membership, missing_members = january.build_membership(
        dates, stocks, membership_rows
    )
    january.atomic_write_csv(
        missing_members, settings.output_dir / "qlib_members_without_cache.csv"
    )

    print("[2/6] 计算四个严格分钟因子...", flush=True)
    factors, arrays = build_factor_matrices(
        panel,
        market,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )
    del panel, market
    gc.collect()
    original_signals = january.build_original_signals(
        factors, dates, settings.backtest_start
    )

    january.atomic_write_csv(
        build_status_frame(factors), settings.output_dir / "factor_status.csv"
    )
    methodology = {
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        },
        "duckdb": {
            "database": args.database,
            "temp_dir": args.duckdb_temp_dir,
            "threads": args.duckdb_threads,
            "memory_limit": args.duckdb_memory_limit,
            "read_seconds": read_seconds,
            "copied_source_data": False,
        },
        "factor_meta": FACTOR_META,
        "formula_count": {"strict": 4, "trial": 0},
        "loaded_sources": {
            "base_daily": BASE_COLUMNS,
            "minute_extra_daily": MINUTE_EXTRA_COLUMNS,
            "shared_market_minute_stats": [
                "date",
                "minute_of_day",
                "market_mean_return",
                "market_std_return_samp",
            ],
        },
        "source_pictures": {
            "M25_HIGH_VOLUME_REALIZED_SKEW": "三月因子/微信图片_20260720141925_760_3.jpg",
            "A06_DAZZLING_VOLATILITY_20D": "四月因子/微信图片_20260721121304_772_3.jpg",
            "A10_P_SHAPED_VOLUME_DISTRIBUTION": "四月因子/微信图片_20260721121311_776_3.jpg",
            "MAY25_TOP_RETURN_PRICE_RESONANCE": "五月因子/微信图片_20260721122031_821_3.jpg",
        },
        "calculation_notes": {
            "market_stats": "open、close、datetime只读一次，同时生成每分钟全市场平均收益和样本标准差。",
            "A06": "每日先减当日历史成分股平均值并取绝对值；固定最近20个交易日，均值和样本标准差各乘0.5。",
            "MAY25": "RM_MAX按历史成分股截面升序平均并列名次除以有效股票数，再除以未排名的STD_MAX。",
        },
        "backtest_conventions": {
            "universe": "历史中证800与中证1000成分并集",
            "daily_trade_frequency": "日度因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "liquidity": "股价不低于3元，20日平均成交额不低于500万元",
        },
        "created_at": pd.Timestamp.now().isoformat(),
    }
    january.atomic_write_json(methodology, settings.output_dir / "methodology.json")

    if args.save_factor_values:
        january.save_factor_values(factors, dates, stocks, settings.output_dir)
        january.save_factor_values(
            original_signals,
            dates,
            stocks,
            settings.output_dir,
            directory_name="original_signals",
        )
    if args.skip_backtest:
        print(f"只生成因子，耗时 {time.time() - started:.1f} 秒", flush=True)
        return 0

    print("[3/6] 生成交易筛选条件和未来收益...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )

    print("[4/6] 计算原频率、统一五日评价和分组收益...", flush=True)
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
            f"  {position}/4 {factor_name}: 统一5日IC={mean_ic:+.4f}",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True)
    original_frequency_ic = pd.concat(
        original_frequency_frames, ignore_index=True
    )
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = pd.concat(rebalance_frames, ignore_index=True)
    original_rebalance = pd.concat(original_rebalance_frames, ignore_index=True)
    daily_summary = january.summarize_ic(
        daily_ic, "date", "unified_5d", settings.split_date
    )
    original_frequency_summary = january.summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = january.summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        january.summarize_original_frequency_backtest(
            original_rebalance,
            original_frequency_summary,
            settings.split_date,
        )
    )

    print("[5/6] 保存回测结果...", flush=True)
    outputs = {
        "daily_ic.csv": daily_ic,
        "monthly_ic.csv": monthly_ic,
        "original_frequency_ic.csv": original_frequency_ic,
        "original_frequency_ic_summary.csv": original_frequency_summary,
        "ic_summary.csv": pd.concat(
            [daily_summary, original_frequency_summary], ignore_index=True
        ),
        "rebalance_returns.csv": rebalance,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
        "original_frequency_rebalance_returns.csv": original_rebalance,
        "original_frequency_backtest_summary.csv": original_backtest_summary,
        "original_frequency_yearly_backtest.csv": original_yearly_backtest,
    }
    for name, frame in outputs.items():
        january.atomic_write_csv(frame, settings.output_dir / name)

    print("[6/6] 完成", flush=True)
    print(f"总耗时 {time.time() - started:.1f} 秒", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
