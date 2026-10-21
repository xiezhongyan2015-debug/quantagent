# -*- coding: utf-8 -*-
"""用 DuckDB 回测九月至十二月可由现有两张日表计算的候选因子。"""

from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import duckdb_factor_store as factor_store
import run_january_minute_backtest as january
import run_july_august_backtest as july_august


BASE_DIR = Path(__file__).resolve().parent
CATALOG_PATH = BASE_DIR / "september_december_factor_catalog.json"
DEFAULT_OUTPUT_DIR = BASE_DIR / "september_december_factor_results" / "final"


# 只列入 base_daily 和 minute_extra_daily 已经能够给出数值的因子。
# strict 表示不改图片公式；trial 表示图片缺参数，代码固定了一个试算口径。
FACTOR_SPECS = {
    "J09_D04_EMOTION_SPILLOVER": ("daily", "trial", "试算固定历史20日、代码相邻10只股票。"),
    "J09_D05_ABSRET_LAG_AMOUNT_CORR": ("monthly", "trial", "试算固定跨日均值窗口为10个交易日。"),
    "J09_D10_INTRADAY_TREND_RATIO": ("daily", "strict", "直接读取48段5分钟严格统计值。"),
    "J09_D14_SMART_MONEY_VWAP_RATIO": ("daily", "strict", "汇总最近10个有效交易日的分钟记录，按聪明度排序并取累计成交量前20%。"),
    "J09_D17_REALIZED_BIPOWER_VARIATION": ("daily", "strict", "直接读取48段5分钟严格统计值。"),
    "J09_D18_MAX_ABNORMAL_VOLUME": ("monthly", "trial", "试算把一年固定为250个交易日，基准不含当天。"),
    "J09_D22_MORNING_AFTERNOON_VOLUME_RATIO": ("monthly", "trial", "缓存只有每日比值；试算按月内交易日等权平均。"),
    "J09_D23_VOLUME_PEAK_MINUTE_RETURN": ("daily", "strict", "按前20个有效交易日同一分钟成交量识别孤立量峰，再累计最近20日量峰分钟收益。"),
    "J09_D25_ABNORMAL_NEG_INTRADAY_REV_FREQ": ("monthly", "strict", "当月频率除以前12个完整月份频率均值。"),
    "J09_D26_INTRADAY_AMPLITUDE_SPLIT": ("daily", "trial", "缓存先在单日内分组；试算再取10日均值和标准差并横截面标准化。"),
    "J09_D29_WEIGHTED_PROFIT_FREQUENCY": ("daily", "trial", "试算用本地股票等权收益作市场基准，时间半衰期固定20日。"),
    "J10_D07_CONFIDENCE_SPILLOVER": ("daily", "trial", "图片给出参数范围；试算固定历史20日、代码相邻10只股票。"),
    "J10_D04_LAG_ABSRET_AMOUNT_CORR": ("monthly", "trial", "图片给出5至15日范围；试算固定月末近10个交易日均值。"),
    "J10_D14_REALIZED_TRIPOWER_VAR": ("daily", "strict", "直接读取48段5分钟严格统计值。"),
    "J10_D16_INTRADAY_MAX_DRAWDOWN": ("daily", "strict", "直接读取48段5分钟严格统计值。"),
    "J10_D19_ABNORMAL_POS_INTRADAY_REV": ("monthly", "strict", "当月频率除以前12个完整月份频率均值。"),
    "J10_D22_VALLEY_REL_PRICE": ("daily", "strict", "按前20个有效交易日同一分钟阈值识别量谷，再取20日相对成交量加权价格均值。"),
    "J10_D27_WEIGHTED_UPPER_SHADOW_FREQ": ("daily", "trial", "图片没有给lambda数值；试算固定时间半衰期为20日。"),
    "J10_D28_VOLUME_PEAK_COUNT": ("daily", "strict", "沿用base_daily的峰值识别结果，统计近20日峰值分钟总数。"),
    "J10_D29_ABNORMAL_VOLUME_AVG": ("monthly", "trial", "试算把一年固定为250个交易日，基准不含当天。"),
    "J11_D01_VOLUME_PEAK_COUNT": ("daily", "strict", "单日用240根分钟线均值加总体标准差识别高峰，相邻高峰只保留首个，再取20日均值。"),
    "J11_D03_DECAY_WEIGHTED_MONTHLY_VOLUME": ("monthly", "trial", "原图权重不完整；试算使用从1到11的线性权重，越近权重越大。"),
    "J11_D07_TOP1PCT_MINUTE_RETURN_PRODUCT": ("daily", "strict", "按原图直接计算t日内涨幅最高1%分钟的收益连乘。"),
    "J11_D10_VOLUME_SHARE_STD": ("daily", "trial", "图片允许多种区间；试算固定60分钟成交量占比版本。"),
    "J11_D11_NORMAL_CDF_ACTIVE_SHARE": ("daily", "strict", "按图片常数0.1和1.96直接读取严格统计值。"),
    "J11_D14_VOLUME_TIDE_PRICE_RATE": ("daily", "strict", "按9分钟居中成交量确定涨潮、顶峰和退潮时刻，再取20日价格变化速度均值。"),
    "J11_D15_REALIZED_JUMP_VOLATILITY": ("daily", "strict", "直接读取以三幂变差常数计算的5分钟已实现跳跃波动率。"),
    "J11_D16_CAPITAL_GAIN_OVERHANG": ("daily", "trial", "图片允许40日或60日；试算固定60日，并以收盘价作为每日成交参考价。"),
    "J11_D17_OVERNIGHT_RETURN": ("daily", "strict", "采用图片的单日隔夜收益版本。"),
    "J11_D22_RIDGE_RELATIVE_VWAP": ("daily", "strict", "按前20个有效交易日同一分钟阈值识别连续量岭，再取20日相对成交量加权价格均值。"),
    "J11_D27_WEIGHTED_LOWER_SHADOW_FREQ": ("daily", "trial", "图片没有给衰减系数；试算固定时间半衰期为20日。"),
    "J11_D28_RIDGE_MINUTE_COUNT": ("daily", "strict", "累计最近20个有效交易日的连续量岭分钟数。"),
    "J12_D01_VOLUME_SHARE_SKEW": ("daily", "strict", "直接读取10分钟成交量占比偏度。"),
    "J12_D02_VOLUME_CV": ("monthly", "strict", "由每日分钟数、成交额均值和样本标准差还原月内全部分钟的变异系数。"),
    "J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR": ("monthly", "trial", "图片只给月末5至15日范围；试算固定使用区间中点10个有效交易日。"),
    "J12_D05_HF_VOLUME_VOL_CV": ("daily", "strict", "日内成交量标准差的20日变异系数。"),
    "J12_D07_DAZZLING_RETURN": ("monthly", "trial", "缓存未单独剔除收盘分钟；按缓存日值完成横截面调整和20日合成。"),
    "J12_D10_UNIFORM_ACTIVE_SHARE": ("daily", "strict", "按[-0.1,0.1]线性映射直接读取严格统计值。"),
    "J12_D13_FUZZY_AMOUNT_CORR": ("monthly", "strict", "逐日计算分钟波动率模糊性与成交额相关系数，月末等权合成20日均值和总体标准差。"),
    "J12_D14_UP_JUMP_VOL": ("daily", "strict", "由完整48段5分钟收益与三幂变差直接计算，不使用旧缓存的极端收益截断。"),
    "J12_D16_INTRADAY_OR_OVERNIGHT_RETURN": ("daily", "trial", "原图定义冲突；试算只采用标题公式close/open-1，不声称是严格公式。"),
    "J12_D20_PEAK_GAP_KURT": ("daily", "strict", "合并最近20个有效交易日内相邻孤立量峰间隔，计算Pearson总体峰度。"),
    "J12_D22_VALLEY_PRICE_PERCENTILE": ("monthly", "strict", "量谷成交量加权价格相对昨收与日内高低区间取分位，20日均值再剔除20日反转影响。"),
    "J12_D24_AVG_TURNOVER": ("monthly", "strict", "月内日换手率等权平均。"),
    "J12_D28_TIME_AVG_REL_PRICE": ("daily", "trial", "原图未指定日内区间；试算采用完整交易日。"),
}


def _load_catalog_meta() -> dict[str, dict[str, object]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows = {row["code"]: row for row in catalog["factors"]}
    missing = sorted(set(FACTOR_SPECS) - set(rows))
    if missing:
        raise KeyError(f"因子目录缺少代码：{', '.join(missing)}")
    result: dict[str, dict[str, object]] = {}
    for code, (frequency, formula_mode, execution_note) in FACTOR_SPECS.items():
        row = rows[code]
        result[code] = {
            "name": row["name"],
            "calendar_month": int(row["calendar_date"][5:7]),
            "calendar_day": int(row["calendar_date"][8:10]),
            "source_frequency": row["source_frequency"],
            "signal_frequency": frequency,
            "expected_direction": row["expected_direction"],
            "strict_status": f"{formula_mode}_formula",
            "formula_mode": formula_mode,
            "catalog_status": row["strict_status"],
            "note": f"{row['note']} {execution_note}",
        }
    return result


FACTOR_META = _load_catalog_meta()

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
    "overnight_return",
    "peak_count",
]

MINUTE_EXTRA_COLUMNS = [
    "minute_count",
    "absret_lag_amount_corr",
    "lag_absret_amount_corr",
    "morning_afternoon_volume_ratio",
    "amplitude_split_daily",
    "top1pct_minute_return",
    "normal_active_share",
    "uniform_active_share",
    "amount_cv_1m",
    "volume_std_1m",
    "dazzling_return_base",
    "time_avg_relative_price",
    "intraday_trend_ratio_5m",
    "realized_bipower_var_5m",
    "realized_tripower_var_5m",
    "intraday_max_drawdown_5m",
    "realized_jump_volatility_5m",
    "up_jump_volatility_5m",
    "volume_share_skew_10m",
    "volume_share_std_60m",
    "smart_money_vwap_ratio_10d",
    "volume_peak_return_daily",
    "valley_relative_vwap_daily",
    "current_day_volume_peak_count",
    "volume_tide_price_rate_daily",
    "ridge_relative_vwap_daily",
    "ridge_minute_count_daily",
    "adj_lag_absret_amount_corr_daily",
    "fuzzy_amount_corr_daily",
    "peak_gap_count",
    "peak_gap_sum1",
    "peak_gap_sum2",
    "peak_gap_sum3",
    "peak_gap_sum4",
    "valley_price_percentile_daily",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--duckdb-temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument(
        "--factor-codes",
        nargs="+",
        help="只回测给出的因子代码；不传时仍回测本入口全部因子。",
    )
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def _compact_panel(panel: pd.DataFrame) -> pd.DataFrame:
    for column in panel.select_dtypes(include=["number"]).columns:
        panel[column] = panel[column].astype(np.float32)
    panel["code"] = panel["code"].astype("string")
    return panel


def load_panel_with_duckdb(args: argparse.Namespace) -> tuple[pd.DataFrame, float]:
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
    elapsed = time.perf_counter() - started
    if len(panel) != base_rows:
        raise ValueError(
            "分钟日缓存与基础日表覆盖不一致："
            f"base_daily={base_rows:,}，minute_extra_daily={minute_rows:,}，"
            f"合并结果={len(panel):,}"
        )
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("DuckDB合并后出现重复的日期和股票代码")
    return _compact_panel(panel), elapsed


def _rolling_mean_std(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    mean, std = july_august._rolling_mean_std_available(values, window)
    return mean.astype(np.float32), std.astype(np.float32)


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling_mean_std(values, window)[0]


def _rolling_valid_stats(
    values: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """逐只股票按最近若干个有效交易日计算和、均值与总体标准差。"""

    source = np.asarray(values, dtype=np.float64)
    sums = np.full(source.shape, np.nan, dtype=np.float32)
    means = np.full(source.shape, np.nan, dtype=np.float32)
    stds = np.full(source.shape, np.nan, dtype=np.float32)
    for column in range(source.shape[1]):
        positions = np.flatnonzero(np.isfinite(source[:, column]))
        if len(positions) < window:
            continue
        observations = source[positions, column]
        cumulative = np.r_[0.0, np.cumsum(observations, dtype=np.float64)]
        square_cumulative = np.r_[
            0.0, np.cumsum(observations * observations, dtype=np.float64)
        ]
        rolling_sum = cumulative[window:] - cumulative[:-window]
        rolling_square_sum = (
            square_cumulative[window:] - square_cumulative[:-window]
        )
        rolling_mean = rolling_sum / window
        variance = np.maximum(
            rolling_square_sum / window - rolling_mean * rolling_mean,
            0.0,
        )
        targets = positions[window - 1 :]
        sums[targets, column] = rolling_sum.astype(np.float32)
        means[targets, column] = rolling_mean.astype(np.float32)
        stds[targets, column] = np.sqrt(variance).astype(np.float32)
    return sums, means, stds


def _rolling_valid_sum(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling_valid_stats(values, window)[0]


def _rolling_valid_mean(values: np.ndarray, window: int) -> np.ndarray:
    return _rolling_valid_stats(values, window)[1]


def _rolling_valid_mean_std(
    values: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray]:
    _, mean, std = _rolling_valid_stats(values, window)
    return mean, std


def _rolling_valid_compound_return(
    values: np.ndarray, window: int
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    logarithm = np.full(source.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(source) & (source > -1.0)
    logarithm[valid] = np.log1p(source[valid])
    log_sum = _rolling_valid_sum(logarithm, window)
    result = np.expm1(log_sum.astype(np.float64))
    result[~np.isfinite(log_sum)] = np.nan
    return result.astype(np.float32)


def compute_peak_gap_kurtosis(
    count: np.ndarray,
    sum1: np.ndarray,
    sum2: np.ndarray,
    sum3: np.ndarray,
    sum4: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    """合并最近若干有效日的间隔，用原始矩计算Pearson总体峰度。"""

    sources = [
        np.asarray(values, dtype=np.float64)
        for values in (count, sum1, sum2, sum3, sum4)
    ]
    result = np.full(sources[0].shape, np.nan, dtype=np.float32)
    for column in range(sources[0].shape[1]):
        valid = np.logical_and.reduce(
            [np.isfinite(values[:, column]) for values in sources]
        )
        positions = np.flatnonzero(valid)
        if len(positions) < window:
            continue
        rolling: list[np.ndarray] = []
        for values in sources:
            observations = values[positions, column]
            cumulative = np.r_[0.0, np.cumsum(observations, dtype=np.float64)]
            rolling.append(cumulative[window:] - cumulative[:-window])
        sample_count, raw1, raw2, raw3, raw4 = rolling
        denominator = np.maximum(sample_count, 1.0)
        mean = raw1 / denominator
        second = raw2 / denominator - mean * mean
        fourth = (
            raw4 / denominator
            - 4.0 * mean * raw3 / denominator
            + 6.0 * mean * mean * raw2 / denominator
            - 3.0 * mean**4
        )
        kurtosis = np.full(sample_count.shape, np.nan, dtype=np.float64)
        good = (sample_count >= 4) & np.isfinite(second) & (second > 1e-15)
        np.divide(
            fourth,
            second * second,
            out=kurtosis,
            where=good,
        )
        result[positions[window - 1 :], column] = kurtosis.astype(np.float32)
    return result


def _lagged_rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    mean = _rolling_mean(values, window)
    result = np.full(values.shape, np.nan, dtype=np.float32)
    result[1:] = mean[:-1]
    return result


def _month_to_date_sum(values: np.ndarray, dates: pd.DatetimeIndex) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    for start, end in zip(starts, ends):
        block = source[start:end]
        valid = np.isfinite(block)
        sums = np.cumsum(np.where(valid, block, 0.0), axis=0)
        counts = np.cumsum(valid, axis=0)
        sums[counts == 0] = np.nan
        result[start:end] = sums.astype(np.float32)
    return result


def _abnormal_monthly_frequency(
    condition: np.ndarray,
    valid: np.ndarray,
    dates: pd.DatetimeIndex,
    history_months: int = 12,
) -> np.ndarray:
    current = july_august._month_to_date_frequency(condition, valid, dates)
    result = np.full(current.shape, np.nan, dtype=np.float32)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    completed = [current[end - 1].astype(np.float64) for end in ends]
    for month_index, (start, end) in enumerate(zip(starts, ends)):
        if month_index < history_months:
            continue
        history = np.stack(completed[month_index - history_months : month_index])
        good = np.isfinite(history).all(axis=0)
        benchmark = np.full(history.shape[1], np.nan, dtype=np.float64)
        benchmark[good] = np.mean(history[:, good], axis=0)
        block = np.full(current[start:end].shape, np.nan, dtype=np.float64)
        np.divide(
            current[start:end],
            benchmark,
            out=block,
            where=np.isfinite(benchmark) & (benchmark != 0),
        )
        result[start:end] = block.astype(np.float32)
    return result


def _weighted_frequency(
    event: np.ndarray,
    valid: np.ndarray,
    window: int = 40,
    half_life: float = 20.0,
) -> np.ndarray:
    source = np.asarray(event, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    weights = 0.5 ** (np.arange(window - 1, -1, -1) / half_life)
    for column in range(source.shape[1]):
        positions = np.flatnonzero(valid[:, column])
        if len(positions) < window:
            continue
        observations = source[positions, column]
        blocks = np.lib.stride_tricks.sliding_window_view(observations, window)
        weighted = blocks @ weights / window
        result[positions[window - 1 :], column] = weighted.astype(np.float32)
    return result


def _neighbor_means(values: np.ndarray, neighbor_count: int = 10) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    left_count = neighbor_count // 2
    right_count = neighbor_count - left_count
    for row in range(source.shape[0]):
        active = np.flatnonzero(np.isfinite(source[row]))
        if len(active) < 2:
            continue
        values_row = source[row, active]
        positions = np.arange(len(active))
        starts = np.maximum(positions - left_count, 0)
        ends = np.minimum(positions + right_count + 1, len(active))
        prefix = np.r_[0.0, np.cumsum(values_row)]
        totals = prefix[ends] - prefix[starts] - values_row
        counts = ends - starts - 1
        good = counts > 0
        result[row, active[good]] = (totals[good] / counts[good]).astype(np.float32)
    return result


def previous_valid_values(values: np.ndarray) -> np.ndarray:
    """逐只股票返回当前日期之前最近一次有效值。"""

    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    latest = np.full(source.shape[1], np.nan, dtype=np.float64)
    for row in range(source.shape[0]):
        result[row] = latest
        current = source[row]
        good = np.isfinite(current)
        latest[good] = current[good]
    return result


def returns_from_previous_valid(close: np.ndarray) -> np.ndarray:
    """用上一有效收盘价计算收益，停牌空档不改变参考价。"""

    previous = previous_valid_values(close)
    result = np.full(np.shape(close), np.nan, dtype=np.float32)
    valid = np.isfinite(close) & np.isfinite(previous) & (previous != 0)
    np.divide(close, previous, out=result, where=valid)
    result[valid] -= 1.0
    return result


def compute_neighbor_spillover(
    own_values: np.ndarray,
    membership: np.ndarray,
    neighbor_count: int = 10,
    min_stocks: int = 50,
) -> np.ndarray:
    neighbors = _neighbor_means(own_values, neighbor_count).astype(np.float64)
    own = np.asarray(own_values, dtype=np.float64)
    valid = membership & np.isfinite(own) & np.isfinite(neighbors)
    count = valid.sum(axis=1).astype(np.float64)
    x = np.where(valid, own, 0.0)
    y = np.where(valid, neighbors, 0.0)
    sum_x = x.sum(axis=1)
    sum_y = y.sum(axis=1)
    sum_xx = (x * x).sum(axis=1)
    sum_xy = (x * y).sum(axis=1)
    denominator = count * sum_xx - sum_x * sum_x
    slope = np.full(len(own), np.nan, dtype=np.float64)
    good = (count >= min_stocks) & np.isfinite(denominator) & (denominator > 0)
    slope[good] = (
        count[good] * sum_xy[good] - sum_x[good] * sum_y[good]
    ) / denominator[good]
    intercept = np.full(len(own), np.nan, dtype=np.float64)
    intercept[good] = (sum_y[good] - slope[good] * sum_x[good]) / count[good]
    result = neighbors - intercept[:, None] - slope[:, None] * own
    result[~valid] = np.nan
    result[~good] = np.nan
    return result.astype(np.float32)


def _pooled_monthly_cv(
    daily_total: np.ndarray,
    daily_count: np.ndarray,
    daily_cv: np.ndarray,
    dates: pd.DatetimeIndex,
) -> np.ndarray:
    total = np.asarray(daily_total, dtype=np.float64)
    count = np.asarray(daily_count, dtype=np.float64)
    cv = np.asarray(daily_cv, dtype=np.float64)
    mean = np.full(total.shape, np.nan, dtype=np.float64)
    np.divide(total, count, out=mean, where=np.isfinite(count) & (count > 0))
    std = cv * mean
    sum_square = (count - 1.0) * std * std + count * mean * mean
    valid = (
        np.isfinite(total)
        & np.isfinite(count)
        & (count > 1)
        & np.isfinite(sum_square)
    )
    result = np.full(total.shape, np.nan, dtype=np.float32)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    for start, end in zip(starts, ends):
        block_valid = valid[start:end]
        cumulative_n = np.cumsum(np.where(block_valid, count[start:end], 0.0), axis=0)
        cumulative_sum = np.cumsum(np.where(block_valid, total[start:end], 0.0), axis=0)
        cumulative_square = np.cumsum(
            np.where(block_valid, sum_square[start:end], 0.0), axis=0
        )
        variance = np.full(cumulative_sum.shape, np.nan, dtype=np.float64)
        good = cumulative_n > 1
        variance[good] = (
            cumulative_square[good]
            - cumulative_sum[good] * cumulative_sum[good] / cumulative_n[good]
        ) / (cumulative_n[good] - 1.0)
        variance = np.maximum(variance, 0.0)
        pooled_mean = np.full(cumulative_sum.shape, np.nan, dtype=np.float64)
        np.divide(cumulative_sum, cumulative_n, out=pooled_mean, where=cumulative_n > 0)
        block_result = np.full(cumulative_sum.shape, np.nan, dtype=np.float64)
        np.divide(
            np.sqrt(variance),
            pooled_mean,
            out=block_result,
            where=np.isfinite(pooled_mean) & (pooled_mean != 0),
        )
        result[start:end] = block_result.astype(np.float32)
    return result


def _linear_weighted_monthly_volume(
    volume: np.ndarray,
    dates: pd.DatetimeIndex,
    months: int = 11,
) -> np.ndarray:
    month_sum = _month_to_date_sum(volume, dates).astype(np.float64)
    result = np.full(month_sum.shape, np.nan, dtype=np.float32)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    finals = [month_sum[end - 1] for end in ends]
    for month_index, (start, end) in enumerate(zip(starts, ends)):
        if month_index < months - 1:
            continue
        history = np.stack(finals[month_index - months + 1 : month_index])
        weights = np.arange(1.0, months, dtype=np.float64)[:, None]
        complete = np.isfinite(history).all(axis=0)
        history_part = np.sum(history * weights, axis=0)
        denominator = float(np.arange(1, months + 1).sum())
        block = (history_part[None, :] + months * month_sum[start:end]) / denominator
        block[:, ~complete] = np.nan
        result[start:end] = block.astype(np.float32)
    return result


def _rolling_product_return(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    log_values = np.full(source.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(source) & (source > -1.0)
    log_values[valid] = np.log1p(source[valid])
    sums = july_august._rolling_sum(log_values, window)
    counts = july_august._rolling_count(log_values, window)
    result = np.expm1(sums)
    result[counts < window] = np.nan
    return result.astype(np.float32)


def compute_daily_turnover(
    volume: np.ndarray,
    float_shares: np.ndarray,
) -> np.ndarray:
    """日换手率等于日成交量除以流通股本。"""

    result = np.full(np.shape(volume), np.nan, dtype=np.float32)
    valid = np.isfinite(volume) & np.isfinite(float_shares) & (float_shares > 0)
    np.divide(volume, float_shares, out=result, where=valid)
    return result


def compute_capital_gain_overhang(
    close: np.ndarray,
    turnover: np.ndarray,
    window: int = 60,
) -> np.ndarray:
    """用换手率生存权重计算参考成本，再除以参考成本。"""

    prices = np.asarray(close, dtype=np.float64)
    rates = np.clip(np.asarray(turnover, dtype=np.float64), 0.0, 1.0)
    weighted_price = np.zeros_like(prices, dtype=np.float64)
    weight_sum = np.zeros_like(prices, dtype=np.float64)
    survival = np.ones_like(prices, dtype=np.float64)
    for lag in range(1, window + 1):
        target = slice(lag, None)
        source = slice(None, -lag)
        rate = rates[source]
        price = prices[source]
        valid = np.isfinite(rate) & np.isfinite(price)
        weight = np.where(valid, rate * survival[target], 0.0)
        weight_sum[target] += weight
        weighted_price[target] += weight * np.where(valid, price, 0.0)
        survival[target] *= np.where(np.isfinite(rate), 1.0 - rate, 1.0)

    reference = np.full_like(prices, np.nan, dtype=np.float64)
    np.divide(
        weighted_price,
        weight_sum,
        out=reference,
        where=np.isfinite(weight_sum) & (weight_sum > 1e-15),
    )
    result = np.full_like(prices, np.nan, dtype=np.float64)
    valid = np.isfinite(prices) & np.isfinite(reference) & (reference != 0)
    np.divide(prices - reference, reference, out=result, where=valid)
    result[:window] = np.nan
    return result.astype(np.float32)


def _cross_sectional_abs_deviation(
    values: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for index in range(len(values)):
        valid = membership[index] & np.isfinite(values[index])
        if valid.sum() < min_stocks:
            continue
        result[index, valid] = np.abs(
            values[index, valid] - np.mean(values[index, valid])
        ).astype(np.float32)
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
    def matrix(column: str, dtype=np.float32) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=dtype
        )

    arrays = {
        "open": matrix("open"),
        "high": matrix("high"),
        "low": matrix("low"),
        "close": matrix("close"),
        "volume": matrix("volume"),
        "amount": matrix("amount"),
        "float_shares": matrix("float_shares"),
        "qfq_ratio": matrix("qfq_ratio"),
    }
    arrays["raw_close"] = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    valid_ratio = np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] > 0)
    np.divide(arrays["close"], arrays["qfq_ratio"], out=arrays["raw_close"], where=valid_ratio)
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = january.build_exit_price_matrices(
        arrays["open"], arrays["close"]
    )

    def cache(column: str, amount_related: bool = False) -> np.ndarray:
        values = matrix(column)
        if amount_related:
            values = january.mask_bad_days(values, arrays["price_amount_bad"])
        return values

    factors: dict[str, np.ndarray] = {}
    daily_return = returns_from_previous_valid(arrays["close"])
    turnover = compute_daily_turnover(arrays["volume"], arrays["float_shares"])

    turnover20 = _rolling_mean(turnover, 20)
    factors["J09_D04_EMOTION_SPILLOVER"] = compute_neighbor_spillover(
        turnover20, membership, neighbor_count=10, min_stocks=settings.min_stocks
    )
    factors["J09_D05_ABSRET_LAG_AMOUNT_CORR"] = _rolling_mean(
        cache("absret_lag_amount_corr", amount_related=True), 10
    )
    factors["J09_D10_INTRADAY_TREND_RATIO"] = cache("intraday_trend_ratio_5m")
    factors["J09_D14_SMART_MONEY_VWAP_RATIO"] = cache(
        "smart_money_vwap_ratio_10d", amount_related=True
    )
    factors["J09_D17_REALIZED_BIPOWER_VARIATION"] = cache("realized_bipower_var_5m")

    volume_baseline = _lagged_rolling_mean(arrays["volume"], 250)
    abnormal_volume = np.full(arrays["volume"].shape, np.nan, dtype=np.float32)
    np.divide(
        arrays["volume"],
        volume_baseline,
        out=abnormal_volume,
        where=np.isfinite(volume_baseline) & (volume_baseline > 0),
    )
    factors["J09_D18_MAX_ABNORMAL_VOLUME"] = july_august._month_to_date_stat(
        abnormal_volume, dates, "max"
    )
    factors["J09_D22_MORNING_AFTERNOON_VOLUME_RATIO"] = july_august._month_to_date_stat(
        cache("morning_afternoon_volume_ratio"), dates, "mean"
    )
    factors["J09_D23_VOLUME_PEAK_MINUTE_RETURN"] = _rolling_valid_sum(
        cache("volume_peak_return_daily"), 20
    )

    overnight = cache("overnight_return")
    intraday = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    np.divide(
        arrays["close"],
        arrays["open"],
        out=intraday,
        where=np.isfinite(arrays["open"]) & (arrays["open"] > 0),
    )
    intraday -= 1.0
    valid_reversal = np.isfinite(overnight) & np.isfinite(intraday)
    factors["J09_D25_ABNORMAL_NEG_INTRADAY_REV_FREQ"] = _abnormal_monthly_frequency(
        (overnight > 0) & (intraday < 0), valid_reversal, dates
    )

    amplitude = cache("amplitude_split_daily")
    amplitude_mean, amplitude_std = _rolling_mean_std(amplitude, 10)
    factors["J09_D26_INTRADAY_AMPLITUDE_SPLIT"] = (
        0.5 * july_august.cross_sectional_zscore(
            amplitude_mean, membership, settings.min_stocks
        )
        + 0.5 * july_august.cross_sectional_zscore(
            amplitude_std, membership, settings.min_stocks
        )
    ).astype(np.float32)

    market_count = np.isfinite(daily_return).sum(axis=1)
    market_return = np.full(len(dates), np.nan, dtype=np.float64)
    np.divide(
        np.nansum(daily_return, axis=1),
        market_count,
        out=market_return,
        where=market_count > 0,
    )
    excess = daily_return - market_return[:, None]
    factors["J09_D29_WEIGHTED_PROFIT_FREQUENCY"] = _weighted_frequency(
        excess > 0.02, np.isfinite(excess), window=40, half_life=20.0
    )

    return20 = _rolling_mean(daily_return, 20)
    factors["J10_D07_CONFIDENCE_SPILLOVER"] = compute_neighbor_spillover(
        return20, membership, neighbor_count=10, min_stocks=settings.min_stocks
    )
    factors["J10_D04_LAG_ABSRET_AMOUNT_CORR"] = _rolling_mean(
        cache("lag_absret_amount_corr", amount_related=True), 10
    )
    factors["J10_D14_REALIZED_TRIPOWER_VAR"] = cache("realized_tripower_var_5m")
    factors["J10_D16_INTRADAY_MAX_DRAWDOWN"] = cache("intraday_max_drawdown_5m")
    factors["J10_D19_ABNORMAL_POS_INTRADAY_REV"] = _abnormal_monthly_frequency(
        (overnight < 0) & (intraday > 0), valid_reversal, dates
    )
    factors["J10_D22_VALLEY_REL_PRICE"] = _rolling_valid_mean(
        cache("valley_relative_vwap_daily", amount_related=True), 20
    )

    previous_close = previous_valid_values(arrays["close"])
    upper_shadow = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    lower_shadow = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    good_previous = np.isfinite(previous_close) & (previous_close > 0)
    np.divide(
        arrays["high"] - np.maximum(arrays["open"], arrays["close"]),
        previous_close,
        out=upper_shadow,
        where=good_previous,
    )
    np.divide(
        np.minimum(arrays["open"], arrays["close"]) - arrays["low"],
        previous_close,
        out=lower_shadow,
        where=good_previous,
    )
    factors["J10_D27_WEIGHTED_UPPER_SHADOW_FREQ"] = _weighted_frequency(
        upper_shadow > 0.01, np.isfinite(upper_shadow), 40, 20.0
    )
    factors["J10_D28_VOLUME_PEAK_COUNT"] = july_august._rolling_sum(
        cache("peak_count"), 20
    ).astype(np.float32)
    factors["J10_D29_ABNORMAL_VOLUME_AVG"] = july_august._month_to_date_stat(
        abnormal_volume, dates, "mean"
    )

    factors["J11_D01_VOLUME_PEAK_COUNT"] = _rolling_valid_mean(
        cache("current_day_volume_peak_count"), 20
    )
    factors["J11_D03_DECAY_WEIGHTED_MONTHLY_VOLUME"] = _linear_weighted_monthly_volume(
        arrays["volume"], dates, months=11
    )
    factors["J11_D07_TOP1PCT_MINUTE_RETURN_PRODUCT"] = cache(
        "top1pct_minute_return"
    )
    factors["J11_D10_VOLUME_SHARE_STD"] = cache("volume_share_std_60m")
    factors["J11_D11_NORMAL_CDF_ACTIVE_SHARE"] = cache(
        "normal_active_share", amount_related=True
    )
    factors["J11_D14_VOLUME_TIDE_PRICE_RATE"] = _rolling_valid_mean(
        cache("volume_tide_price_rate_daily"), 20
    )
    factors["J11_D15_REALIZED_JUMP_VOLATILITY"] = cache(
        "realized_jump_volatility_5m"
    )
    factors["J11_D16_CAPITAL_GAIN_OVERHANG"] = compute_capital_gain_overhang(
        arrays["close"], turnover, window=60
    )
    factors["J11_D17_OVERNIGHT_RETURN"] = overnight.astype(np.float32)
    factors["J11_D22_RIDGE_RELATIVE_VWAP"] = _rolling_valid_mean(
        cache("ridge_relative_vwap_daily", amount_related=True), 20
    )
    factors["J11_D27_WEIGHTED_LOWER_SHADOW_FREQ"] = _weighted_frequency(
        lower_shadow > 0.01, np.isfinite(lower_shadow), 40, 20.0
    )
    factors["J11_D28_RIDGE_MINUTE_COUNT"] = _rolling_valid_sum(
        cache("ridge_minute_count_daily"), 20
    )

    factors["J12_D01_VOLUME_SHARE_SKEW"] = cache("volume_share_skew_10m")
    factors["J12_D02_VOLUME_CV"] = _pooled_monthly_cv(
        arrays["amount"],
        cache("minute_count"),
        cache("amount_cv_1m", amount_related=True),
        dates,
    )
    factors["J12_D04_ADJ_LAG_ABSRET_AMOUNT_CORR"] = _rolling_valid_mean(
        cache("adj_lag_absret_amount_corr_daily", amount_related=True), 10
    )
    volume_std_mean, volume_std_std = _rolling_mean_std(cache("volume_std_1m"), 20)
    volume_vol_cv = np.full(volume_std_mean.shape, np.nan, dtype=np.float32)
    np.divide(
        volume_std_std,
        volume_std_mean,
        out=volume_vol_cv,
        where=np.isfinite(volume_std_mean) & (volume_std_mean != 0),
    )
    factors["J12_D05_HF_VOLUME_VOL_CV"] = volume_vol_cv

    dazzling = _cross_sectional_abs_deviation(
        cache("dazzling_return_base"), membership, settings.min_stocks
    )
    dazzling_mean, dazzling_std = _rolling_mean_std(dazzling, 20)
    factors["J12_D07_DAZZLING_RETURN"] = (
        0.5 * dazzling_mean + 0.5 * dazzling_std
    ).astype(np.float32)
    factors["J12_D10_UNIFORM_ACTIVE_SHARE"] = cache(
        "uniform_active_share", amount_related=True
    )
    fuzzy_mean, fuzzy_std = _rolling_valid_mean_std(
        cache("fuzzy_amount_corr_daily", amount_related=True), 20
    )
    factors["J12_D13_FUZZY_AMOUNT_CORR"] = (
        0.5 * fuzzy_mean + 0.5 * fuzzy_std
    ).astype(np.float32)
    factors["J12_D14_UP_JUMP_VOL"] = cache("up_jump_volatility_5m")
    factors["J12_D16_INTRADAY_OR_OVERNIGHT_RETURN"] = intraday.astype(np.float32)
    factors["J12_D20_PEAK_GAP_KURT"] = compute_peak_gap_kurtosis(
        cache("peak_gap_count"),
        cache("peak_gap_sum1"),
        cache("peak_gap_sum2"),
        cache("peak_gap_sum3"),
        cache("peak_gap_sum4"),
        window=20,
    )
    valley_percentile = _rolling_valid_mean(
        cache("valley_price_percentile_daily", amount_related=True), 20
    )
    reversal20 = _rolling_valid_compound_return(daily_return, 20)
    factors["J12_D22_VALLEY_PRICE_PERCENTILE"] = january.neutralize_one_factor(
        valley_percentile,
        reversal20,
        membership,
        settings.min_stocks,
    ).astype(np.float32)
    factors["J12_D24_AVG_TURNOVER"] = july_august._month_to_date_stat(
        turnover, dates, "mean"
    )
    factors["J12_D28_TIME_AVG_REL_PRICE"] = cache("time_avg_relative_price")

    if set(factors) != set(FACTOR_META):
        missing = sorted(set(FACTOR_META) - set(factors))
        extra = sorted(set(factors) - set(FACTOR_META))
        raise AssertionError(f"因子实现与元数据不一致；缺少={missing}，多出={extra}")
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
    return pd.DataFrame(rows).sort_values(["calendar_month", "calendar_day", "factor"])


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

    print("[1/6] 用DuckDB读取并合并两张日表...", flush=True)
    panel, read_seconds = load_panel_with_duckdb(args)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(
        settings.qlib_dir, settings.start, settings.end
    )
    membership, missing_members = january.build_membership(dates, stocks, membership_rows)
    january.atomic_write_csv(
        missing_members, settings.output_dir / "qlib_members_without_cache.csv"
    )

    print(f"[2/6] 计算{len(FACTOR_META)}个九至十二月候选因子...", flush=True)
    factors, arrays = build_factor_matrices(
        panel, dates, stocks, date_map, stock_map, membership, settings
    )
    factors = january.select_factor_codes(factors, args.factor_codes)
    del panel
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
            "read_and_join_seconds": read_seconds,
            "copied_source_data": False,
        },
        "factor_meta": FACTOR_META,
        "formula_counts": {
            "strict": sum(x[1] == "strict" for x in FACTOR_SPECS.values()),
            "trial": sum(x[1] == "trial" for x in FACTOR_SPECS.values()),
        },
        "loaded_sources": {
            "base_daily": BASE_COLUMNS,
            "minute_extra_daily": MINUTE_EXTRA_COLUMNS,
        },
        "backtest_conventions": {
            "universe": "历史中证800与中证1000成分并集",
            "daily_trade_frequency": "日度因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "liquidity": "股价不低于3元，20日平均成交额不低于500万元",
            "trial_rule": "参数不完整的公式只标为trial_formula，具体固定值写入note",
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

    print("[3/6] 生成价格、成交额和历史成分筛选...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )

    print("[4/6] 计算原频率IC、统一五日IC和分组收益...", flush=True)
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
            f"  {position}/{len(factors)} {factor_name}: 统一5日IC={mean_ic:+.4f}",
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
    original_frequency_summary = january.summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = january.summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        january.summarize_original_frequency_backtest(
            original_rebalance, original_frequency_summary, settings.split_date
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
