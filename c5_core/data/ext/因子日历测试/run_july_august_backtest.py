# -*- coding: utf-8 -*-
"""用 DuckDB 读取现有每日文件，并回测七月、八月可直接计算的因子。"""

from __future__ import annotations

import argparse
import gc
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from build_july_august_minute_features import TRIAL_PARAMETERS
import duckdb_factor_store as factor_store
import run_january_minute_backtest as january
import run_june_backtest as june


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "july_august_factor_results" / "final"


FACTOR_META = {
    "F02_D12_LONE_GOOSE": {
        "name": "孤雁出群",
        "calendar_month": 2,
        "calendar_day": 12,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_pairwise_market_amount_correlation",
        "note": "不分化时刻内，逐股计算与其余股票成交额相关系数绝对值的均值；月末合成近20日均值和标准差。",
    },
    "J07_D01_FUZZY_QTY_RATIO": {
        "name": "模糊数量比",
        "calendar_month": 7,
        "calendar_day": 1,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "两层5分钟总体标准差识别起雾时刻；日值为起雾时刻平均成交量与日内平均成交量之比，月末合成20日均值和标准差。",
    },
    "J07_D03_MAX_ABRET": {
        "name": "最大异常日收益率",
        "calendar_month": 7,
        "calendar_day": 3,
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_local_market_proxy",
        "note": "月内取|个股日收益-本地可用股票等权日收益|的最大值。",
    },
    "J07_D05_SIG_JUMP_ASYM": {
        "name": "跳跃显著程度加权的上下行跳跃波动不对称性",
        "calendar_month": 7,
        "calendar_day": 5,
        "source_frequency": "滚动20日，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "formula_mode": "trial",
        "strict_status": "trial_fixed_parameters",
        "note": "试算：alpha=1%，D=20；SRJV=RVJP-RVJN，权重=max(T_SwV/Phi_0.99,0)。",
    },
    "J07_D07_EXTREME_REVERSAL": {
        "name": "极端收益反转",
        "calendar_month": 7,
        "calendar_day": 7,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "分别取过去20日最极端分钟收益和其前一分钟收益的均值，逐日横向排名后相加。",
    },
    "J07_D08_AMOUNT_AUTOCORR": {
        "name": "分钟成交额自相关性",
        "calendar_month": 7,
        "calendar_day": 8,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "formula_mode": "trial",
        "strict_status": "trial_fixed_parameters",
        "note": "试算：固定滞后1分钟，每日计算corr(Amount_t-1,Amount_t)，不再跨日合成。",
    },
    "J07_D10_CLOSE_AMOUNT_SHARE": {
        "name": "尾盘成交额占比",
        "calendar_month": 7,
        "calendar_day": 10,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "日值为最后30分钟成交额占全天成交额之比；因子为近20日均值。",
    },
    "J07_D14_UPSIDE_RV": {
        "name": "上行已实现波动率（5分钟）",
        "calendar_month": 7,
        "calendar_day": 14,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_cache_identity",
        "note": "用已实现波动率×上行波动占比还原Σr²I(r≥0)，与原式相同。",
    },
    "J07_D19_NEG_INTRADAY_REV_FREQ": {
        "name": "反向日内逆转的频率",
        "calendar_month": 7,
        "calendar_day": 19,
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "runnable",
        "note": "月内统计隔夜收益>0且日内收益<0的交易日占比。",
    },
    "J07_D23_TURNOVER_IV": {
        "name": "特质换手波动率（20日）",
        "calendar_month": 7,
        "calendar_day": 23,
        "source_frequency": "滚动20日",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable_natural_daily_signal",
        "note": "图片未写调仓频率；本次按每个交易日可得的滚动值生成日信号。",
    },
    "J07_D25_AMT_MAX": {
        "name": "个股收益最高时的领先成交量异动",
        "calendar_month": 7,
        "calendar_day": 25,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_market_minute_total",
        "note": "收益最高30分钟之前一分钟的全市场成交额占比之和，除以该股全天成交额占比均值。",
    },
    "J07_D27_TREND_CLARITY": {
        "name": "趋势清晰度（240至20日）",
        "calendar_month": 7,
        "calendar_day": 27,
        "source_frequency": "滚动日频",
        "signal_frequency": "daily",
        "expected_direction": "conditional",
        "strict_status": "runnable_natural_daily_signal",
        "note": "对T-240至T-20收盘价与时间回归取R²，至少200个有效交易日。",
    },
    "J08_D01_ADJ_FUZZY_SPREAD": {
        "name": "修正模糊价差",
        "calendar_month": 8,
        "calendar_day": 1,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_reusing_june_amount_ratio",
        "note": "日模糊金额比减日模糊数量比；用10日标准差修正负值并保持横向负值总量，再合成20日均值和标准差。",
    },
    "J08_D03_AVG_ABRET": {
        "name": "平均异常日收益率",
        "calendar_month": 8,
        "calendar_day": 3,
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_local_market_proxy",
        "note": "月内取(个股日收益-本地可用股票等权日收益)²的平均值。",
    },
    "J08_D05_DISASTER_REBUILD": {
        "name": "日内收益波动比（灾后重建）",
        "calendar_month": 8,
        "calendar_day": 5,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "日值为收益波动比与最近5分钟20个OHLC价格点更优波动率的协方差；月末合成近20日均值和标准差。",
    },
    "J08_D08_ABSRET_AMOUNT_CORR": {
        "name": "绝对收益与成交额相关性",
        "calendar_month": 8,
        "calendar_day": 8,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "formula_mode": "trial",
        "strict_status": "trial_fixed_parameters",
        "note": "试算：每日计算1分钟对数收益绝对值与成交额的相关系数，月末取近10日均值。",
    },
    "J08_D10_AMOUNT_VARIANCE": {
        "name": "分钟成交额方差",
        "calendar_month": 8,
        "calendar_day": 10,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "按原式使用1/N定义的分钟成交额总体方差。",
    },
    "J08_D14_RSJ": {
        "name": "上下行波动率不对称性（1分钟）",
        "calendar_month": 8,
        "calendar_day": 14,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_cache_identity",
        "note": "由上行波动占比严格换算：RSJ=2×上行占比-1。",
    },
    "J08_D16_DECAY_FEAR": {
        "name": "注意力衰减-惊恐因子",
        "calendar_month": 8,
        "calendar_day": 16,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "runnable_with_index_return_fallback",
        "note": "优先使用中证全指000985日收益；相邻收盘缺失时改用中证800指数000906日收益。",
    },
    "J08_D17_LARGE_VOL_PV_CORR": {
        "name": "大成交量价量相关性",
        "calendar_month": 8,
        "calendar_day": 17,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable_natural_daily_signal",
        "note": "每日成交量排名前1/3分钟内，计算分钟成交量与收盘价的相关系数。",
    },
    "J08_D19_POS_INTRADAY_REV_FREQ": {
        "name": "正向日内逆转的频率",
        "calendar_month": 8,
        "calendar_day": 19,
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "月内统计隔夜收益<0且日内收益>0的交易日占比。",
    },
    "J08_D22_HIGH_LOW_SPREAD": {
        "name": "基于高低价的买卖价差",
        "calendar_month": 8,
        "calendar_day": 22,
        "source_frequency": "日频，两日高低价",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "formula_shifted_without_lookahead",
        "note": "原图写t与t+1；为避免使用未来价格，本次用t-1与t并把结果记在t日。",
    },
    "J08_D25_AMT_MIN": {
        "name": "个股收益最低时的领先成交量异动",
        "calendar_month": 8,
        "calendar_day": 25,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "formula_mode": "trial",
        "strict_status": "trial_fixed_parameters",
        "note": "试算：图片注释与标题冲突，按标题选择收益最低30分钟；其余计算与AMT_MAX相同。",
    },
    "J08_D26_IDEAL_AMPLITUDE": {
        "name": "分钟理想振幅",
        "calendar_month": 8,
        "calendar_day": 26,
        "source_frequency": "滚动10日，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_natural_daily_signal",
        "note": "最近10个市场交易日的分钟中，收盘价最高25%与最低25%的平均振幅之差。",
    },
    "J08_D27_TREND_CLARITY_MOM": {
        "name": "趋势清晰度动量",
        "calendar_month": 8,
        "calendar_day": 27,
        "source_frequency": "滚动日频",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "runnable_natural_daily_signal",
        "note": "动量与趋势清晰度在历史成分股内逐日标准化，TM=-|MOM'-TC'|。",
    },
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
    "overnight_return",
    "upside_vol_share_1m",
    "upside_vol_share_5m",
]
MAY_COLUMNS = ["realized_volatility_5m"]
JUNE_COLUMNS = ["fuzzy_amount_ratio_daily"]
JULY_AUGUST_COLUMNS = [
    "fuzzy_quantity_ratio_daily",
    "extreme_return_daily",
    "pre_extreme_return_daily",
    "late_amount_share_30m",
    "amt_max_30_daily",
    "disaster_rebuild_daily",
    "minute_amount_variance_daily",
    "large_volume_price_corr_daily",
    "ideal_amplitude_10d",
    "amount_autocorr_lag1_daily_trial",
    "absret_amount_corr_daily_trial",
    "amt_min_30_daily_trial",
    "sig_jump_asym_20d_trial",
]
LONE_GOOSE_COLUMNS = ["lone_goose_daily"]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--duckdb-temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument(
        "--may-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["may_daily"],
    )
    parser.add_argument(
        "--june-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["june_daily"],
    )
    parser.add_argument(
        "--july-august-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["july_august_daily"],
    )
    parser.add_argument(
        "--lone-goose-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["lone_goose_daily"],
    )
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
        factor_store.register_sources(
            connection,
            {
                "base_daily": args.base_cache_glob,
                "may_daily": args.may_cache_glob,
                "june_daily": args.june_cache_glob,
                "july_august_daily": args.july_august_cache_glob,
                "lone_goose_daily": args.lone_goose_cache_glob,
            },
        )
        panel = factor_store.read_joined_panel(
            connection,
            {"base_daily": BASE_COLUMNS, "may_daily": MAY_COLUMNS},
            args.start,
            args.end,
        )
        additions = [
            factor_store.read_source(
                connection, "june_daily", JUNE_COLUMNS, args.start, args.end
            ),
            factor_store.read_source(
                connection,
                "july_august_daily",
                JULY_AUGUST_COLUMNS,
                args.start,
                args.end,
            ),
            factor_store.read_source(
                connection,
                "lone_goose_daily",
                LONE_GOOSE_COLUMNS,
                args.start,
                args.end,
            ),
        ]
        for addition in additions:
            if addition.duplicated(["date", "code"]).any():
                raise ValueError("新增分钟日值中出现重复日期股票记录。")
            panel = panel.merge(
                addition,
                how="left",
                on=["date", "code"],
                validate="one_to_one",
            )
    elapsed = time.perf_counter() - started
    if panel.duplicated(["date", "code"]).any():
        examples = panel.loc[
            panel.duplicated(["date", "code"], keep=False), ["date", "code"]
        ].head()
        raise ValueError(f"DuckDB合并后出现重复日期股票记录：\n{examples}")
    panel = _compact_panel(panel)
    return panel, elapsed


def _month_to_date_stat(
    values: np.ndarray, dates: pd.DatetimeIndex, operation: str
) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    for start, end in zip(starts, ends):
        block = source[start:end]
        valid = np.isfinite(block)
        if operation == "mean":
            cumulative = np.cumsum(np.where(valid, block, 0.0), axis=0)
            counts = np.cumsum(valid, axis=0)
            current = np.full(block.shape, np.nan, dtype=np.float64)
            np.divide(cumulative, counts, out=current, where=counts > 0)
        elif operation == "max":
            counts = np.cumsum(valid, axis=0)
            current = np.maximum.accumulate(
                np.where(valid, block, -np.inf), axis=0
            )
            current[counts == 0] = np.nan
        else:
            raise ValueError(f"不支持的月内统计：{operation}")
        result[start:end] = current.astype(np.float32)
    return result


def _month_to_date_frequency(
    condition: np.ndarray, valid: np.ndarray, dates: pd.DatetimeIndex
) -> np.ndarray:
    values = np.where(valid, condition.astype(np.float64), np.nan)
    return _month_to_date_stat(values, dates, "mean")


def _rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(source)
    result = np.cumsum(np.where(valid, source, 0.0), axis=0, dtype=np.float64)
    if window < len(source):
        result[window:] -= result[:-window].copy()
    result[: window - 1] = np.nan
    return result


def _rolling_count(values: np.ndarray, window: int) -> np.ndarray:
    valid = np.isfinite(values).astype(np.int16)
    result = np.cumsum(valid, axis=0, dtype=np.int32)
    if window < len(values):
        result[window:] -= result[:-window].copy()
    result[: window - 1] = 0
    return result


def _rolling_mean_std_available(
    values: np.ndarray, window: int
) -> tuple[np.ndarray, np.ndarray]:
    """在固定日历窗口内忽略空值，计算总体均值和总体标准差。"""

    source = np.asarray(values, dtype=np.float64)
    counts = _rolling_count(source, window)
    sums = _rolling_sum(source, window)
    square_sums = _rolling_sum(source * source, window)
    mean = np.full(source.shape, np.nan, dtype=np.float64)
    variance = np.full(source.shape, np.nan, dtype=np.float64)
    np.divide(sums, counts, out=mean, where=counts > 0)
    np.divide(square_sums, counts, out=variance, where=counts > 0)
    variance -= mean * mean
    variance = np.maximum(variance, 0.0)
    std = np.sqrt(variance)
    mean[: window - 1] = np.nan
    std[: window - 1] = np.nan
    return mean, std


def cross_sectional_rank(
    values: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    """在每日历史成分股内计算平均并列名次的百分位。"""

    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float32)
    for index in range(len(source)):
        valid = membership[index] & np.isfinite(source[index])
        if int(valid.sum()) < min_stocks:
            continue
        result[index, valid] = (
            pd.Series(source[index, valid]).rank(method="average", pct=True).to_numpy()
        ).astype(np.float32)
    return result


def compute_adjusted_fuzzy_spread(
    fuzzy_spread: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
    window: int = 10,
) -> np.ndarray:
    """按图片步骤修正模糊价差的负值，并保持每日负值总和不变。"""

    source = np.asarray(fuzzy_spread, dtype=np.float64)
    counts = _rolling_count(source, window)
    _, rolling_std = _rolling_mean_std_available(source, window)
    rolling_std[counts < window] = np.nan
    result = np.full(source.shape, np.nan, dtype=np.float32)
    for index in range(len(source)):
        available = membership[index] & np.isfinite(source[index])
        if int(available.sum()) < min_stocks:
            continue
        positive = available & (source[index] >= 0)
        negative = (
            available
            & (source[index] < 0)
            & np.isfinite(rolling_std[index])
            & (rolling_std[index] > 0)
        )
        if not negative.any():
            continue
        s1 = float(np.sum(source[index, negative]))
        scaled = source[index, negative] / rolling_std[index, negative]
        s2 = float(np.sum(scaled))
        if not np.isfinite(s1) or not np.isfinite(s2) or s2 == 0:
            continue
        result[index, positive] = source[index, positive].astype(np.float32)
        result[index, negative] = (scaled / s2 * s1).astype(np.float32)
    return result


def compute_trend_clarity(
    close: np.ndarray,
    lookback: int = 240,
    skip_recent: int = 20,
    minimum: int = 200,
) -> np.ndarray:
    """计算T-240至T-20价格对时间回归的R²。"""

    prices = np.asarray(close, dtype=np.float64)
    window = lookback - skip_recent + 1
    time_index = np.arange(len(prices), dtype=np.float64)[:, None]
    valid = np.isfinite(prices)
    count = _rolling_count(prices, window)
    sum_y = _rolling_sum(prices, window)
    sum_y2 = _rolling_sum(prices * prices, window)
    sum_x = _rolling_sum(np.where(valid, time_index, np.nan), window)
    sum_x2 = _rolling_sum(np.where(valid, time_index * time_index, np.nan), window)
    sum_xy = _rolling_sum(np.where(valid, time_index * prices, np.nan), window)
    n = count.astype(np.float64)
    covariance = n * sum_xy - sum_x * sum_y
    variance_x = n * sum_x2 - sum_x * sum_x
    variance_y = n * sum_y2 - sum_y * sum_y
    denominator = variance_x * variance_y
    rolling_r2 = np.full(prices.shape, np.nan, dtype=np.float64)
    good = (count >= minimum) & np.isfinite(denominator) & (denominator > 0)
    rolling_r2[good] = np.clip(
        covariance[good] * covariance[good] / denominator[good], 0.0, 1.0
    )
    result = np.full(prices.shape, np.nan, dtype=np.float32)
    result[skip_recent:] = rolling_r2[:-skip_recent].astype(np.float32)
    return result


def compute_window_momentum(
    close: np.ndarray,
    lookback: int = 240,
    skip_recent: int = 20,
    minimum: int = 200,
) -> np.ndarray:
    daily_return = january.daily_returns(close)
    window = lookback - skip_recent + 1
    rolling = _rolling_sum(daily_return, window)
    count = _rolling_count(daily_return, window)
    rolling[count < minimum] = np.nan
    result = np.full(close.shape, np.nan, dtype=np.float32)
    result[skip_recent:] = rolling[:-skip_recent].astype(np.float32)
    return result


def cross_sectional_zscore(
    values: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for index in range(len(values)):
        valid = membership[index] & np.isfinite(values[index])
        if valid.sum() < min_stocks:
            continue
        current = values[index, valid].astype(np.float64)
        standard = float(np.std(current, ddof=0))
        if standard <= 0:
            continue
        result[index, valid] = ((current - np.mean(current)) / standard).astype(
            np.float32
        )
    return result


def compute_turnover_iv(
    turnover: np.ndarray, min_stocks: int, window: int = 20
) -> np.ndarray:
    """按图片规则计算20日换手率PCA残差标准差。"""

    values = np.asarray(turnover, dtype=np.float64)
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for end in range(window - 1, len(values)):
        block = values[end - window + 1 : end + 1]
        complete = np.isfinite(block).all(axis=0)
        indices = np.flatnonzero(complete)
        if len(indices) < min_stocks:
            continue
        current = block[:, indices]
        centered = current - np.mean(current, axis=0, keepdims=True)
        u, singular, _ = np.linalg.svd(centered, full_matrices=False)
        variance = singular * singular
        total = float(np.sum(variance))
        if total <= 0:
            continue
        component_count = 1 if variance[0] / total >= 0.20 else 2
        scores = u[:, :component_count] * singular[:component_count]
        design = np.column_stack([np.ones(window), scores])
        coefficients, _, _, _ = np.linalg.lstsq(design, current, rcond=None)
        residual = current - design @ coefficients
        result[end, indices] = np.std(residual, axis=0, ddof=0).astype(np.float32)
        if (end + 1) % 250 == 0:
            print(f"  特质换手波动率已计算 {end + 1}/{len(values)} 日", flush=True)
    return result


def compute_high_low_illiq(
    high: np.ndarray,
    low: np.ndarray,
    amount: np.ndarray,
    qfq_ratio: np.ndarray,
) -> np.ndarray:
    """用相邻两日高低价估算价差，并除以当日成交额。"""

    raw_high = np.full(high.shape, np.nan, dtype=np.float64)
    raw_low = np.full(low.shape, np.nan, dtype=np.float64)
    good_ratio = np.isfinite(qfq_ratio) & (qfq_ratio > 0)
    np.divide(high, qfq_ratio, out=raw_high, where=good_ratio)
    np.divide(low, qfq_ratio, out=raw_low, where=good_ratio)
    result = np.full(high.shape, np.nan, dtype=np.float32)
    valid = (
        np.isfinite(raw_high[:-1])
        & np.isfinite(raw_low[:-1])
        & np.isfinite(raw_high[1:])
        & np.isfinite(raw_low[1:])
        & (raw_high[:-1] > 0)
        & (raw_low[:-1] > 0)
        & (raw_high[1:] > 0)
        & (raw_low[1:] > 0)
        & np.isfinite(amount[:-1])
        & (amount[:-1] > 0)
    )
    day_range_previous = np.log(raw_high[:-1] / raw_low[:-1]) ** 2
    day_range_current = np.log(raw_high[1:] / raw_low[1:]) ** 2
    beta = (day_range_previous + day_range_current) / 2.0
    pair_high = np.maximum(raw_high[:-1], raw_high[1:])
    pair_low = np.minimum(raw_low[:-1], raw_low[1:])
    gamma = np.log(pair_high / pair_low) ** 2
    denominator = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = (
        (np.sqrt(2.0) * np.sqrt(beta) - np.sqrt(beta)) / denominator
        - np.sqrt(gamma / denominator)
    )
    spread = 2.0 * np.expm1(alpha) / (1.0 + np.exp(alpha))
    current = np.full(spread.shape, np.nan, dtype=np.float64)
    np.divide(spread, amount[:-1], out=current, where=valid)
    result[1:] = current.astype(np.float32)
    return result


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    settings: january.Settings,
    requested_codes: Sequence[str] | None = None,
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

    daily_return = january.daily_returns(arrays["close"])
    valid_return = np.isfinite(daily_return)
    market_count = np.sum(valid_return, axis=1)
    market_return = np.full(len(dates), np.nan, dtype=np.float64)
    np.divide(
        np.nansum(daily_return, axis=1),
        market_count,
        out=market_return,
        where=market_count > 0,
    )
    abnormal = np.abs(daily_return - market_return[:, None])
    abnormal_squared = (daily_return - market_return[:, None]) ** 2

    factors: dict[str, np.ndarray] = {}

    lone_goose = matrix("lone_goose_daily")
    mean, std = _rolling_mean_std_available(lone_goose, 20)
    factors["F02_D12_LONE_GOOSE"] = (0.5 * mean + 0.5 * std).astype(
        np.float32
    )
    del lone_goose, mean, std

    fuzzy_quantity = matrix("fuzzy_quantity_ratio_daily")
    mean, std = _rolling_mean_std_available(fuzzy_quantity, 20)
    factors["J07_D01_FUZZY_QTY_RATIO"] = (
        0.5 * mean + 0.5 * std
    ).astype(np.float32)
    del mean, std

    extreme_return = matrix("extreme_return_daily")
    pre_extreme_return = matrix("pre_extreme_return_daily")
    extreme_mean, _ = _rolling_mean_std_available(extreme_return, 20)
    pre_extreme_mean, _ = _rolling_mean_std_available(pre_extreme_return, 20)
    factors["J07_D07_EXTREME_REVERSAL"] = (
        cross_sectional_rank(extreme_mean, membership, settings.min_stocks)
        + cross_sectional_rank(
            pre_extreme_mean, membership, settings.min_stocks
        )
    ).astype(np.float32)
    del extreme_return, pre_extreme_return, extreme_mean, pre_extreme_mean

    late_amount_share = matrix("late_amount_share_30m")
    late_mean, _ = _rolling_mean_std_available(late_amount_share, 20)
    factors["J07_D10_CLOSE_AMOUNT_SHARE"] = late_mean.astype(np.float32)
    del late_amount_share, late_mean

    factors["J07_D25_AMT_MAX"] = matrix("amt_max_30_daily")

    fuzzy_amount = matrix("fuzzy_amount_ratio_daily")
    adjusted_fuzzy = compute_adjusted_fuzzy_spread(
        fuzzy_amount - fuzzy_quantity,
        membership,
        settings.min_stocks,
        window=10,
    )
    mean, std = _rolling_mean_std_available(adjusted_fuzzy, 20)
    factors["J08_D01_ADJ_FUZZY_SPREAD"] = (
        0.5 * mean + 0.5 * std
    ).astype(np.float32)
    del fuzzy_amount, fuzzy_quantity, adjusted_fuzzy, mean, std

    disaster_rebuild = matrix("disaster_rebuild_daily")
    mean, std = _rolling_mean_std_available(disaster_rebuild, 20)
    factors["J08_D05_DISASTER_REBUILD"] = (
        0.5 * mean + 0.5 * std
    ).astype(np.float32)
    del disaster_rebuild, mean, std

    factors["J08_D10_AMOUNT_VARIANCE"] = matrix(
        "minute_amount_variance_daily"
    )
    factors["J08_D17_LARGE_VOL_PV_CORR"] = matrix(
        "large_volume_price_corr_daily"
    )
    factors["J08_D26_IDEAL_AMPLITUDE"] = matrix("ideal_amplitude_10d")

    factors["J07_D05_SIG_JUMP_ASYM"] = matrix(
        "sig_jump_asym_20d_trial"
    )
    factors["J07_D08_AMOUNT_AUTOCORR"] = matrix(
        "amount_autocorr_lag1_daily_trial"
    )
    absret_amount_corr = matrix("absret_amount_corr_daily_trial")
    absret_mean, _ = _rolling_mean_std_available(absret_amount_corr, 10)
    factors["J08_D08_ABSRET_AMOUNT_CORR"] = absret_mean.astype(np.float32)
    del absret_amount_corr, absret_mean
    factors["J08_D25_AMT_MIN"] = matrix("amt_min_30_daily_trial")

    requested = set(requested_codes or ())
    if requested and requested.issubset(factors):
        return january.select_factor_codes(factors, requested_codes), arrays

    factors["J07_D03_MAX_ABRET"] = _month_to_date_stat(
        abnormal, dates, "max"
    )
    factors["J08_D03_AVG_ABRET"] = _month_to_date_stat(
        abnormal_squared, dates, "mean"
    )
    del abnormal, abnormal_squared

    upside_share = matrix("upside_vol_share_5m")
    realized_volatility = matrix("realized_volatility_5m")
    factors["J07_D14_UPSIDE_RV"] = (
        upside_share * realized_volatility
    ).astype(np.float32)
    upside_share_1m = matrix("upside_vol_share_1m")
    factors["J08_D14_RSJ"] = (2.0 * upside_share_1m - 1.0).astype(np.float32)
    del realized_volatility, upside_share_1m

    overnight_return = matrix("overnight_return")
    intraday_return = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
    valid_intraday = (
        np.isfinite(arrays["open"])
        & (arrays["open"] > 0)
        & np.isfinite(arrays["close"])
    )
    np.divide(
        arrays["close"],
        arrays["open"],
        out=intraday_return,
        where=valid_intraday,
    )
    intraday_return -= 1.0
    valid_reversal = np.isfinite(overnight_return) & np.isfinite(intraday_return)
    factors["J07_D19_NEG_INTRADAY_REV_FREQ"] = (
        _month_to_date_frequency(
            (overnight_return > 0) & (intraday_return < 0),
            valid_reversal,
            dates,
        )
    )
    factors["J08_D19_POS_INTRADAY_REV_FREQ"] = (
        _month_to_date_frequency(
            (overnight_return < 0) & (intraday_return > 0),
            valid_reversal,
            dates,
        )
    )
    del overnight_return, intraday_return, valid_reversal

    turnover = np.full(arrays["volume"].shape, np.nan, dtype=np.float32)
    valid_shares = np.isfinite(arrays["float_shares"]) & (arrays["float_shares"] > 0)
    np.divide(
        arrays["volume"],
        arrays["float_shares"],
        out=turnover,
        where=valid_shares,
    )
    factors["J07_D23_TURNOVER_IV"] = compute_turnover_iv(
        turnover, settings.min_stocks
    )
    del turnover

    trend_clarity = compute_trend_clarity(arrays["close"])
    factors["J07_D27_TREND_CLARITY"] = trend_clarity
    momentum = compute_window_momentum(arrays["close"])
    standardized_tc = cross_sectional_zscore(
        trend_clarity, membership, settings.min_stocks
    )
    standardized_momentum = cross_sectional_zscore(
        momentum, membership, settings.min_stocks
    )
    factors["J08_D27_TREND_CLARITY_MOM"] = (
        -np.abs(standardized_momentum - standardized_tc)
    ).astype(np.float32)
    del momentum, standardized_tc, standardized_momentum

    market_index_return = june._read_qlib_close_return(
        settings.qlib_dir, "sh000985", dates, fallback_instrument="sh000906"
    )
    fear = np.abs(daily_return - market_index_return[:, None]) / (
        np.abs(daily_return) + np.abs(market_index_return[:, None]) + 0.1
    )
    decay_fear = np.full(fear.shape, np.nan, dtype=np.float64)
    decay_fear[2:] = fear[2:] - (fear[1:-1] + fear[:-2]) / 2.0
    decay_fear[decay_fear < 0] = np.nan
    adjusted_return = decay_fear * daily_return
    mean, std = _rolling_mean_std_available(adjusted_return, 20)
    factors["J08_D16_DECAY_FEAR"] = (0.5 * mean + 0.5 * std).astype(
        np.float32
    )
    del fear, decay_fear, adjusted_return, mean, std, daily_return

    factors["J08_D22_HIGH_LOW_SPREAD"] = compute_high_low_illiq(
        arrays["high"],
        arrays["low"],
        arrays["amount"],
        arrays["qfq_ratio"],
    )
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
    return pd.DataFrame(rows).sort_values(["calendar_month", "calendar_day"])


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

    print("[1/6] DuckDB读取并合并基础每日数据...", flush=True)
    panel, read_seconds = load_panel_with_duckdb(args)
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
    print(
        f"  {len(panel):,}行，{len(dates)}个交易日，{len(stocks)}只股票；"
        f"DuckDB读取合并{read_seconds:.2f}秒",
        flush=True,
    )

    print(f"[2/6] 生成{len(FACTOR_META)}个可直接计算因子...", flush=True)
    factors, arrays = build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
        requested_codes=args.factor_codes,
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
        "trial_parameters": TRIAL_PARAMETERS,
        "loaded_sources": {
            "base_daily": BASE_COLUMNS,
            "may_daily": MAY_COLUMNS,
            "june_daily": JUNE_COLUMNS,
            "july_august_daily": JULY_AUGUST_COLUMNS,
            "lone_goose_daily": LONE_GOOSE_COLUMNS,
        },
        "backtest_conventions": {
            "universe": "历史中证800与中证1000成分并集",
            "training_rule": "未来收益退出日不晚于2020-12-31",
            "later_period_rule": "入场日晚于2020-12-31",
            "daily_trade_frequency": "日频因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "liquidity": "股价不低于3元，20日平均成交额不低于500万元",
            "abnormal_market_return": "本地每日文件中有数据股票的等权平均收益",
            "high_low_pair": "原图t与t+1改记为t-1与t，避免未来数据",
            "lone_goose": "相关系数先逐对取绝对值，再对其余股票求均值；分块计算但不近似",
            "fuzzy_standard_deviation": "日内两层5分钟标准差均使用总体定义，与六月模糊金额比保持一致",
            "disaster_standard_deviation": "20个OHLC价格点使用样本标准差，日内协方差使用样本定义",
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
        print(f"仅生成因子，耗时 {time.time() - started:.1f} 秒", flush=True)
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
        factor_started = time.time()
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
            f"  {position}/{len(factors)} {factor_name}: 统一5日IC="
            f"{mean_ic:+.4f}，耗时{time.time() - factor_started:.1f}秒",
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
