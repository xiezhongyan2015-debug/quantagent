# -*- coding: utf-8 -*-
"""生成九个参数固定试算因子的轻量日值，支持按股票中断后继续。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
import psutil
import pyarrow.parquet as pq
from scipy import sparse
from scipy.interpolate import CubicSpline
from scipy.signal import find_peaks
from scipy.sparse.linalg import factorized
from scipy.special import gamma
from scipy.stats import norm, t as student_t

import build_shared_market_minute_stats as shared_market
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_ROOT = BASE_DIR / "trial_minute_factor_results" / "cache"
DEFAULT_OUTPUT_DIR = DEFAULT_CACHE_ROOT / "daily_by_stock"
DEFAULT_MARKET_FILE = shared_market.DEFAULT_OUTPUT
DEFAULT_MARKET_META = DEFAULT_MARKET_FILE.with_suffix(".meta.json")
DEFAULT_MANIFEST = DEFAULT_CACHE_ROOT / "manifest.json"
DEFAULT_CACHE_METHODOLOGY = DEFAULT_CACHE_ROOT / "methodology.json"

CACHE_VERSION = 2
MARKET_VERSION = 2
MINUTES_PER_DAY = 240
FIVE_MINUTE_BLOCKS = 48
DEFAULT_MIN_MARKET_STOCKS = 500

MORNING_MINUTES = np.arange(9 * 60 + 31, 11 * 60 + 31, dtype=np.int16)
AFTERNOON_MINUTES = np.arange(13 * 60 + 1, 15 * 60 + 1, dtype=np.int16)
TRADING_MINUTES = np.concatenate([MORNING_MINUTES, AFTERNOON_MINUTES])
MINUTE_TO_POSITION = {int(value): index for index, value in enumerate(TRADING_MINUTES)}

OUTPUT_COLUMNS = [
    "date",
    "code",
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
    "formula_mode",
    "formula_label",
    "minute_count",
    "trial_min_market_stocks",
    "trial_cache_version",
]

MARKET_COLUMNS = [
    "date",
    "datetime",
    "market_mean_return",
    "market_std_return_pop",
    "positive_return_mean",
    "negative_return_mean",
    "return_stock_count",
    "deviation_skew",
]

TRIAL_PARAMETERS = {
    "M15_VSA_LOW_CLOSE_GAP": {
        "name": "成交量支撑区域下限与收盘价差异",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "三月因子/微信图片_20260720141905_750_3.jpg",
        "parameters": {
            "price_round_decimals": 8,
            "support_threshold": "累计成交量严格超过全日成交量的50%",
            "vsp_tie_break": "先选离当日收盘价近者，再选较低价格",
            "expansion_order": "离VSP价差由近到远；同价差成交量由大到小；再按价格升序",
            "daily_formula": "(当日收盘价-VSA_Low)/当日收盘价",
        },
        "trial_reason": "图片把结果称为差异又命名为ratio，未明确是否归一化及并列顺序。",
    },
    "A22_INTRADAY_SNR": {
        "name": "日内信噪比",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "四月因子/微信图片_20260721121330_788_3.jpg",
        "parameters": {
            "emd_layers": 2,
            "envelope": "镜像端点三次样条",
            "sifting_stop_sd": 0.2,
            "max_siftings_per_layer": 10,
            "daily_formula": "log(std(二层EMD残余信号)/std(两层IMF之和))",
        },
        "trial_reason": "图片只建议二至三层并把EMD细节指向文献，未给停止条件。",
    },
    "MAY13_INTRADAY_ABNORMAL_VOLUME": {
        "name": "日内异常交易量",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "五月因子/微信图片_20260721122012_809_3.jpg",
        "parameters": {
            "frequency": "48个5分钟段",
            "expected_volume": "上一有效交易日同一5分钟段成交量",
            "daily_formula": "48段ATV中分母大于0部分的算术平均",
        },
        "trial_reason": "图片允许不同回看期，也没有规定如何把分钟ATV合成日值。",
    },
    "J06_D02_JUMP_INTENSITY": {
        "name": "跳跃强度",
        "formula_mode": "trial",
        "signal_frequency": "monthly",
        "source_image": "六月因子/微信图片_20260721122945_829_3.jpg",
        "parameters": {
            "frequency": "5分钟",
            "segments_per_day": 48,
            "test": "Jiang-Oomen SwV单侧检验",
            "alpha": 0.01,
            "monthly_formula": "最近20个交易日跳跃标记的平均值",
        },
        "trial_reason": "图片没有指定分钟频率和显著性水平。",
    },
    "J06_D07_HIGH_FREQUENCY_RESILIENCY": {
        "name": "高频弹性因子",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "六月因子/微信图片_20260721123038_834_3.jpg",
        "parameters": {
            "frequency": "5分钟",
            "window_trading_days": 20,
            "samples_per_window": 960,
            "hp_lambda": 129600.0,
            "fft_formula": "mean(2*abs(FFT(HP暂时价格)/N)*(k/N)), k=1..floor(N/2)",
        },
        "trial_reason": "图片没有指定窗口、分钟频率和HP平滑参数。",
    },
    "J06_D20_CO_MIN": {
        "name": "个股收益最低时的价变共振CO_MIN",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "六月因子/微信图片_20260721123057_847_3.jpg",
        "parameters": {
            "selected_minutes": "个股当日1分钟简单收益最低的5个分钟",
            "market_return": "同一分钟全市场股票等权简单收益",
            "market_std": "同一分钟全市场简单收益总体标准差",
            "rank": "RM_MIN每日横截面降序百分位名次",
            "daily_formula": "降序百分位名次/STD_MIN",
        },
        "trial_reason": "图片正文与变量说明对最低或最高分钟有冲突，名次归一化算法未给。",
    },
    "J09_D07_BRAVE_PEAK": {
        "name": "勇敢者高峰",
        "formula_mode": "trial",
        "signal_frequency": "monthly",
        "source_image": "九月因子/微信图片_20260722111012_926_3.jpg",
        "parameters": {
            "price_observations": "当前及前4分钟OHLC，共20个价格",
            "better_volatility": "20个价格的总体标准差/均值平方",
            "abnormal_threshold": "当日均值+1倍总体标准差，含等号",
            "daily_formula": "异常分钟内(1分钟简单收益/更优波动率, 更优波动率)的总体协方差",
            "monthly_formula": "0.5*最近20日日值均值+0.5*最近20日日值总体标准差",
        },
        "trial_reason": "图片没有给价格观察个数n；试算按图中五分钟OHLC固定为20。",
    },
    "J09_D11_NAIVE_ACTIVE_SHARE": {
        "name": "朴素主动占比",
        "formula_mode": "trial",
        "signal_frequency": "daily",
        "source_image": "九月因子/微信图片_20260722111015_930_3.jpg",
        "parameters": {
            "frequency": "1分钟",
            "student_t_df": 5,
            "price_change_scale": "当日1分钟收盘价差的总体标准差",
            "daily_formula": "sum(amount*t_cdf(delta_close/sigma,df=5))/sum(amount)",
        },
        "trial_reason": "图片没有给t分布自由度，也允许1分钟或5分钟。",
    },
    "J09_D19_SKEWED_MOMENTUM_RANGE": {
        "name": "偏斜动量区间",
        "formula_mode": "trial",
        "signal_frequency": "monthly",
        "source_image": "九月因子/微信图片_20260722111020_938_3.jpg",
        "parameters": {
            "market_benchmark": "每分钟上涨股票与下跌股票分别等权平均，缺值忽略",
            "min_market_stocks": DEFAULT_MIN_MARKET_STOCKS,
            "momentum_minute": "全市场同方向收益偏离度的样本偏度大于0",
            "daily_formula": "动量分钟sum(log(1+个股同方向收益偏离度))",
            "monthly_formula": "最近20个交易日日值的总体标准差",
        },
        "trial_reason": "图片未指定最少股票数和缺值处理。",
    },
}


_WORKER_CONNECTION: duckdb.DuckDBPyConnection | None = None


def _sql_path(value: str | Path) -> str:
    return str(Path(value).resolve()).replace("\\", "/").replace("'", "''")


def _assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须位于当前工作区内。")
    return resolved


def _date_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    lower = pd.Timestamp(start).normalize()
    upper = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)
    if upper <= lower:
        raise ValueError("结束日期必须不早于开始日期。")
    return lower, upper


def _read_parquet_sql(paths: Sequence[str | Path], filename: bool = False) -> str:
    if not paths:
        raise ValueError("至少需要一个分钟文件。")
    quoted = ", ".join(f"'{_sql_path(path)}'" for path in paths)
    extra = ", filename = true" if filename else ""
    return f"read_parquet([{quoted}], union_by_name = true{extra})"


def build_market_query(
    minute_paths: Sequence[str | Path], start: str, end: str
) -> str:
    """保留原入口，但生成统一的共用市场分钟表。"""

    return shared_market.build_query(minute_paths, start, end)


def build_stock_query(
    minute_path: str | Path,
    market_path: str | Path,
    start: str,
    end: str,
    source_relation: str | None = None,
) -> str:
    """只读取一只股票需要的原始字段，并接入共用市场分钟表。"""

    lower, upper = _date_bounds(start, end)
    source = source_relation or _read_parquet_sql([minute_path])
    market_source = shared_market.market_read_sql(market_path)
    return f"""
        SELECT
            CAST(s.datetime AS DATE) AS date,
            s.datetime,
            CAST(s.open AS DOUBLE) AS minute_open,
            CAST(s.high AS DOUBLE) AS minute_high,
            CAST(s.low AS DOUBLE) AS minute_low,
            CAST(s.close AS DOUBLE) AS minute_close,
            CAST(s.volume AS DOUBLE) AS minute_volume,
            CAST(s.amount AS DOUBLE) AS minute_amount,
            m.market_mean_return,
            m.market_std_return_pop,
            m.positive_return_mean,
            m.negative_return_mean,
            m.return_stock_count,
            m.deviation_skew
        FROM {source} AS s
        INNER JOIN {market_source} AS m USING (datetime)
        WHERE s.datetime >= TIMESTAMP '{lower:%Y-%m-%d 00:00:00}'
          AND s.datetime < TIMESTAMP '{upper:%Y-%m-%d 00:00:00}'
        ORDER BY s.datetime
    """


def _prepare_complete_days(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    if frame.empty:
        return frame.copy(), pd.DatetimeIndex([])
    result = frame.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    minute_of_day = result["datetime"].dt.hour * 60 + result["datetime"].dt.minute
    result["_position"] = minute_of_day.map(MINUTE_TO_POSITION)
    result["_date"] = result["datetime"].dt.normalize()
    exact = result["datetime"].eq(result["datetime"].dt.floor("min"))
    result = result[exact & result["_position"].notna()].copy()
    if result.empty:
        return result, pd.DatetimeIndex([])
    result["_position"] = result["_position"].astype(np.int16)
    counts = result.groupby("_date", sort=True)["_position"].agg(["size", "nunique"])
    dates = counts.index[
        (counts["size"] == MINUTES_PER_DAY)
        & (counts["nunique"] == MINUTES_PER_DAY)
    ]
    result = result[result["_date"].isin(dates)]
    result = result.sort_values(["_date", "_position"], kind="stable").reset_index(
        drop=True
    )
    return result, pd.DatetimeIndex(dates)


def _reshape_five_minute(values: np.ndarray) -> np.ndarray:
    return values.reshape(len(values), 2, 24, 5).reshape(len(values), 48, 5)


def _intraday_returns(
    close: np.ndarray, day_open: np.ndarray, logarithmic: bool
) -> np.ndarray:
    previous = np.empty_like(close, dtype=np.float64)
    previous[:, 0] = day_open
    previous[:, 1:] = close[:, :-1]
    result = np.full(close.shape, np.nan, dtype=np.float64)
    valid = (
        np.isfinite(close)
        & np.isfinite(previous)
        & (close > 0)
        & (previous > 0)
    )
    ratio = np.full(close.shape, np.nan, dtype=np.float64)
    np.divide(close, previous, out=ratio, where=valid)
    if logarithmic:
        result[valid] = np.log(ratio[valid])
    else:
        result[valid] = ratio[valid] - 1.0
    return result


def compute_vsa_low_close_ratio(
    close: np.ndarray, volume: np.ndarray, price_decimals: int = 8
) -> np.ndarray:
    """按固定并列次序构造成交量支撑区，并计算归一化收盘价差。"""

    prices = np.asarray(close, dtype=np.float64)
    quantities = np.asarray(volume, dtype=np.float64)
    result = np.full(len(prices), np.nan, dtype=np.float64)
    for day in range(len(prices)):
        valid = (
            np.isfinite(prices[day])
            & np.isfinite(quantities[day])
            & (prices[day] > 0)
            & (quantities[day] >= 0)
        )
        if not valid.any():
            continue
        day_close = prices[day, np.flatnonzero(valid)[-1]]
        rounded = np.round(prices[day, valid], price_decimals)
        day_volume = quantities[day, valid]
        unique_price, inverse = np.unique(rounded, return_inverse=True)
        grouped_volume = np.bincount(
            inverse, weights=day_volume, minlength=len(unique_price)
        ).astype(np.float64)
        total = grouped_volume.sum()
        if total <= 0 or day_close <= 0:
            continue
        vsp = min(
            range(len(unique_price)),
            key=lambda index: (
                -grouped_volume[index],
                abs(unique_price[index] - day_close),
                unique_price[index],
            ),
        )
        order = [vsp] + sorted(
            (index for index in range(len(unique_price)) if index != vsp),
            key=lambda index: (
                abs(unique_price[index] - unique_price[vsp]),
                -grouped_volume[index],
                unique_price[index],
            ),
        )
        selected: list[int] = []
        cumulative = 0.0
        for index in order:
            selected.append(index)
            cumulative += grouped_volume[index]
            if cumulative > 0.5 * total:
                break
        vsa_low = float(np.min(unique_price[selected]))
        result[day] = (day_close - vsa_low) / day_close
    return result


def _mirrored_envelope(values: np.ndarray, extrema: np.ndarray) -> np.ndarray:
    length = len(values)
    take = min(3, len(extrema))
    left_index = -extrema[:take]
    right_source = extrema[-take:]
    right_index = 2 * (length - 1) - right_source
    x = np.concatenate([left_index, extrema, right_index]).astype(np.float64)
    y = np.concatenate(
        [values[extrema[:take]], values[extrema], values[right_source]]
    ).astype(np.float64)
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    unique_x, unique_positions = np.unique(x, return_index=True)
    unique_y = y[unique_positions]
    target = np.arange(length, dtype=np.float64)
    if len(unique_x) < 4:
        return np.interp(target, unique_x, unique_y)
    return CubicSpline(unique_x, unique_y, bc_type="natural")(target)


def emd_signal_noise(
    values: np.ndarray,
    layers: int = 2,
    stop_sd: float = 0.2,
    max_siftings: int = 10,
) -> tuple[np.ndarray, np.ndarray]:
    """用镜像端点三次样条提取指定层数IMF，返回残余信号和噪声。"""

    source = np.asarray(values, dtype=np.float64)
    if source.ndim != 1 or len(source) < 8 or not np.isfinite(source).all():
        return np.full_like(source, np.nan), np.full_like(source, np.nan)
    residual = source.copy()
    extracted = 0
    for _ in range(layers):
        candidate = residual.copy()
        usable = False
        for _ in range(max_siftings):
            maxima = find_peaks(candidate, plateau_size=True)[0]
            minima = find_peaks(-candidate, plateau_size=True)[0]
            if len(maxima) < 2 or len(minima) < 2:
                break
            upper = _mirrored_envelope(candidate, maxima)
            lower = _mirrored_envelope(candidate, minima)
            updated = candidate - 0.5 * (upper + lower)
            denominator = float(np.sum(candidate * candidate))
            sd = float(np.sum((candidate - updated) ** 2)) / max(
                denominator, np.finfo(np.float64).eps
            )
            candidate = updated
            usable = True
            if sd <= stop_sd:
                break
        if not usable:
            break
        residual = residual - candidate
        extracted += 1
    if extracted != layers:
        return np.full_like(source, np.nan), np.full_like(source, np.nan)
    return residual, source - residual


def compute_intraday_snr(close: np.ndarray) -> np.ndarray:
    prices = np.asarray(close, dtype=np.float64)
    result = np.full(len(prices), np.nan, dtype=np.float64)
    for day, values in enumerate(prices):
        signal, noise = emd_signal_noise(values)
        signal_std = np.std(signal, ddof=0)
        noise_std = np.std(noise, ddof=0)
        if np.isfinite(signal_std) and np.isfinite(noise_std) and signal_std > 0 and noise_std > 0:
            result[day] = math.log(signal_std / noise_std)
    return result


def compute_intraday_abnormal_volume(volume_5m: np.ndarray) -> np.ndarray:
    values = np.asarray(volume_5m, dtype=np.float64)
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < 2:
        return result
    expected = values[:-1]
    actual = values[1:]
    ratios = np.full(actual.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(actual) & np.isfinite(expected) & (expected > 0)
    np.divide(actual, expected, out=ratios, where=valid)
    counts = np.sum(np.isfinite(ratios), axis=1)
    sums = np.nansum(ratios, axis=1)
    np.divide(sums, counts, out=result[1:], where=counts > 0)
    return result


def compute_swap_variance_jump_indicator(
    close_5m: np.ndarray,
    day_open: np.ndarray,
    alpha: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """按卡片中的SwV统计量计算每日单侧跳跃标记。"""

    simple = _intraday_returns(close_5m, day_open, logarithmic=False)
    log_return = _intraday_returns(close_5m, day_open, logarithmic=True)
    count = log_return.shape[1]
    rv = np.sum(log_return * log_return, axis=1)
    swap_variance = 2.0 * np.sum(simple - log_return, axis=1)
    mu_one = math.sqrt(2.0 / math.pi)
    bipower = (
        mu_one ** -2
        * count
        / (count - 1.0)
        * np.sum(np.abs(log_return[:, 1:]) * np.abs(log_return[:, :-1]), axis=1)
    )
    mu_six = 15.0
    mu_three_halves = 2.0 ** 0.75 * gamma(1.25) / math.sqrt(math.pi)
    powered = np.abs(log_return) ** 1.5
    quartic_product = (
        powered[:, :-3]
        * powered[:, 1:-2]
        * powered[:, 2:-1]
        * powered[:, 3:]
    )
    omega = (
        (mu_six / 9.0)
        * (count**3 / (count - 3.0))
        * mu_three_halves ** -4
        * np.sum(quartic_product, axis=1)
    )
    statistic = np.full(len(log_return), np.nan, dtype=np.float64)
    valid = (
        np.isfinite(log_return).all(axis=1)
        & np.isfinite(simple).all(axis=1)
        & (swap_variance > 0)
        & (omega > 0)
    )
    statistic[valid] = (
        bipower[valid]
        / np.sqrt(omega[valid] / count)
        * (1.0 - rv[valid] / swap_variance[valid])
    )
    indicator = np.full(len(log_return), np.nan, dtype=np.float64)
    indicator[valid] = (statistic[valid] > norm.ppf(1.0 - alpha)).astype(np.float64)
    return indicator, statistic


@lru_cache(maxsize=4)
def _hp_trend_solver(length: int, lambda_value: float):
    second_difference = sparse.diags(
        [np.ones(length - 2), -2.0 * np.ones(length - 2), np.ones(length - 2)],
        [0, 1, 2],
        shape=(length - 2, length),
        format="csc",
    )
    system = sparse.eye(length, format="csc") + lambda_value * (
        second_difference.T @ second_difference
    )
    return factorized(system)


def compute_high_frequency_resiliency(
    close_5m: np.ndarray,
    window_days: int = 20,
    hp_lambda: float = 129600.0,
    chunk_days: int = 128,
) -> np.ndarray:
    """在20日连续5分钟价格上做HP分解和频域恢复速度计算。"""

    values = np.asarray(close_5m, dtype=np.float64)
    result = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < window_days:
        return result
    samples = window_days * values.shape[1]
    flattened = values.reshape(-1)
    windows = np.lib.stride_tricks.sliding_window_view(flattened, samples)[:: values.shape[1]]
    solver = _hp_trend_solver(samples, float(hp_lambda))
    frequency = np.arange(1, samples // 2 + 1, dtype=np.float64) / samples
    for left in range(0, len(windows), chunk_days):
        right = min(left + chunk_days, len(windows))
        block = windows[left:right]
        valid = np.isfinite(block).all(axis=1) & (block > 0).all(axis=1)
        if not valid.any():
            continue
        log_price = np.log(block[valid]).T
        trend = np.asarray(solver(log_price))
        if trend.ndim == 1:
            trend = trend[:, None]
        temporary = log_price - trend
        spectrum = np.fft.rfft(temporary, axis=0)[1 : samples // 2 + 1]
        normalized_amplitude = np.abs(spectrum) / samples
        local = np.mean(2.0 * normalized_amplitude * frequency[:, None], axis=0)
        positions = np.flatnonzero(valid) + (window_days - 1 + left)
        result[positions] = local
    return result


def compute_co_min_inputs(
    simple_return: np.ndarray,
    market_mean: np.ndarray,
    market_std: np.ndarray,
    selected_count: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    result_mean = np.full(len(simple_return), np.nan, dtype=np.float64)
    result_std = np.full(len(simple_return), np.nan, dtype=np.float64)
    for day in range(len(simple_return)):
        valid = (
            np.isfinite(simple_return[day])
            & np.isfinite(market_mean[day])
            & np.isfinite(market_std[day])
        )
        positions = np.flatnonzero(valid)
        if len(positions) < selected_count:
            continue
        order = positions[
            np.argsort(simple_return[day, positions], kind="stable")[:selected_count]
        ]
        result_mean[day] = np.mean(market_mean[day, order])
        result_std[day] = np.mean(market_std[day, order])
    return result_mean, result_std


def compute_brave_peak(
    minute_open: np.ndarray,
    minute_high: np.ndarray,
    minute_low: np.ndarray,
    minute_close: np.ndarray,
    simple_return: np.ndarray,
) -> np.ndarray:
    day_count, minute_count = minute_close.shape
    better = np.full((day_count, minute_count), np.nan, dtype=np.float64)
    for minute in range(4, minute_count):
        block = np.concatenate(
            [
                minute_open[:, minute - 4 : minute + 1],
                minute_high[:, minute - 4 : minute + 1],
                minute_low[:, minute - 4 : minute + 1],
                minute_close[:, minute - 4 : minute + 1],
            ],
            axis=1,
        )
        mean = np.mean(block, axis=1)
        standard = np.std(block, axis=1, ddof=0)
        np.divide(standard, mean * mean, out=better[:, minute], where=mean != 0)
    ratio = np.full_like(better, np.nan)
    np.divide(simple_return, better, out=ratio, where=better > 0)
    result = np.full(day_count, np.nan, dtype=np.float64)
    for day in range(day_count):
        valid = np.isfinite(better[day]) & np.isfinite(ratio[day])
        if valid.sum() < 2:
            continue
        daily_mean = np.mean(better[day, valid])
        daily_std = np.std(better[day, valid], ddof=0)
        abnormal = valid & (better[day] >= daily_mean + daily_std)
        if abnormal.sum() < 2:
            continue
        left = ratio[day, abnormal]
        right = better[day, abnormal]
        result[day] = np.mean((left - left.mean()) * (right - right.mean()))
    return result


def compute_naive_active_share(
    minute_close: np.ndarray,
    day_open: np.ndarray,
    minute_amount: np.ndarray,
    degrees_of_freedom: int = 5,
) -> np.ndarray:
    previous = np.empty_like(minute_close, dtype=np.float64)
    previous[:, 0] = day_open
    previous[:, 1:] = minute_close[:, :-1]
    change = minute_close - previous
    scale = np.std(change, axis=1, ddof=0)
    standardized = np.zeros_like(change, dtype=np.float64)
    np.divide(change, scale[:, None], out=standardized, where=scale[:, None] > 0)
    weight = student_t.cdf(standardized, df=degrees_of_freedom)
    total = np.sum(minute_amount, axis=1)
    result = np.full(len(total), np.nan, dtype=np.float64)
    numerator = np.sum(minute_amount * weight, axis=1)
    np.divide(numerator, total, out=result, where=total > 0)
    return result


def compute_skewed_momentum_daily(
    simple_return: np.ndarray,
    positive_mean: np.ndarray,
    negative_mean: np.ndarray,
    deviation_skew: np.ndarray,
    market_count: np.ndarray,
    min_market_stocks: int = DEFAULT_MIN_MARKET_STOCKS,
) -> np.ndarray:
    deviation = np.zeros_like(simple_return, dtype=np.float64)
    positive = simple_return > 0
    negative = simple_return < 0
    deviation[positive] = simple_return[positive] - positive_mean[positive]
    deviation[negative] = simple_return[negative] - negative_mean[negative]
    market_valid = (
        np.isfinite(deviation_skew)
        & np.isfinite(market_count)
        & (market_count >= min_market_stocks)
    )
    momentum = market_valid & (deviation_skew > 0)
    valid_log = momentum & np.isfinite(deviation) & (deviation > -1.0)
    result = np.full(len(simple_return), np.nan, dtype=np.float64)
    usable_day = market_valid.any(axis=1)
    result[usable_day] = np.sum(
        np.where(valid_log[usable_day], np.log1p(deviation[usable_day]), 0.0), axis=1
    )
    return result


def build_daily_frame(
    frame: pd.DataFrame,
    code: str,
    min_market_stocks: int = DEFAULT_MIN_MARKET_STOCKS,
) -> pd.DataFrame:
    complete, dates = _prepare_complete_days(frame)
    if complete.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    numeric_columns = [
        column
        for column in complete.columns
        if column not in {"date", "datetime", "_date", "_position"}
    ]
    for column in numeric_columns:
        complete[column] = pd.to_numeric(complete[column], errors="coerce")
    day_count = len(dates)

    def matrix(column: str) -> np.ndarray:
        return complete[column].to_numpy(dtype=np.float64).reshape(
            day_count, MINUTES_PER_DAY
        )

    minute_open = matrix("minute_open")
    minute_high = matrix("minute_high")
    minute_low = matrix("minute_low")
    minute_close = matrix("minute_close")
    minute_volume = matrix("minute_volume")
    minute_amount = matrix("minute_amount")
    market_mean = matrix("market_mean_return")
    market_std = matrix("market_std_return_pop")
    positive_mean = matrix("positive_return_mean")
    negative_mean = matrix("negative_return_mean")
    market_count = matrix("return_stock_count")
    deviation_skew = matrix("deviation_skew")

    day_open = minute_open[:, 0]
    simple_return = _intraday_returns(minute_close, day_open, logarithmic=False)
    close_5m = _reshape_five_minute(minute_close)[:, :, -1]
    volume_5m = np.sum(_reshape_five_minute(minute_volume), axis=2)

    vsa = compute_vsa_low_close_ratio(minute_close, minute_volume)
    snr = compute_intraday_snr(minute_close)
    atv = compute_intraday_abnormal_volume(volume_5m)
    jump, _ = compute_swap_variance_jump_indicator(close_5m, day_open)
    resiliency = compute_high_frequency_resiliency(close_5m)
    rm_min, std_min = compute_co_min_inputs(simple_return, market_mean, market_std)
    brave_peak = compute_brave_peak(
        minute_open, minute_high, minute_low, minute_close, simple_return
    )
    naive_share = compute_naive_active_share(minute_close, day_open, minute_amount)
    skewed_momentum = compute_skewed_momentum_daily(
        simple_return,
        positive_mean,
        negative_mean,
        deviation_skew,
        market_count,
        min_market_stocks,
    )

    return pd.DataFrame(
        {
            "date": dates,
            "code": code.lower(),
            "m15_vsa_low_close_gap_trial": vsa,
            "a22_intraday_snr_trial": snr,
            "may13_intraday_abnormal_volume_trial": atv,
            "j06_d02_jump_indicator_trial": jump,
            "j06_d07_high_frequency_resiliency_trial": resiliency,
            "j06_d20_rm_min_trial": rm_min,
            "j06_d20_std_min_trial": std_min,
            "j09_d07_brave_peak_daily_trial": brave_peak,
            "j09_d11_naive_active_share_trial": naive_share,
            "j09_d19_skewed_momentum_daily_trial": skewed_momentum,
            "formula_mode": "trial",
            "formula_label": "试算",
            "minute_count": np.full(day_count, MINUTES_PER_DAY, dtype=np.int16),
            "trial_min_market_stocks": np.full(
                day_count, min_market_stocks, dtype=np.int16
            ),
            "trial_cache_version": np.full(day_count, CACHE_VERSION, dtype=np.int8),
        },
        columns=OUTPUT_COLUMNS,
    )


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
    os.replace(temporary, path)


def _source_fingerprint(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        stat = path.stat()
        digest.update(path.name.lower().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
    return digest.hexdigest()


def _market_meta_matches(
    meta_path: Path,
    files: Sequence[Path],
    start: str,
    end: str,
) -> bool:
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        meta.get("market_version") == MARKET_VERSION
        and meta.get("start") == start
        and meta.get("end") == end
        and meta.get("source_fingerprint") == _source_fingerprint(files)
        and meta.get("source_file_count") == len(files)
    )


def _shared_market_file_is_usable(path: Path) -> bool:
    try:
        files = shared_market.list_market_stat_files(path)
        return all(
            pq.ParquetFile(file).metadata.num_rows > 0
            and set(shared_market.OUTPUT_COLUMNS).issubset(
                pq.ParquetFile(file).schema_arrow.names
            )
            for file in files
        )
    except Exception:
        return False


def build_market_file(
    connection: duckdb.DuckDBPyConnection,
    minute_paths: Sequence[Path],
    output_path: Path,
    meta_path: Path,
    start: str,
    end: str,
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.stem}.tmp.parquet")
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    query = build_market_query(minute_paths, start, end)
    connection.execute(
        f"""
        COPY ({query})
        TO '{_sql_path(temporary)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    rows = int(
        connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(temporary)]
        ).fetchone()[0]
    )
    if rows == 0:
        temporary.unlink(missing_ok=True)
        raise ValueError("共用市场分钟表没有生成任何记录。")
    temporary.replace(output_path)
    meta = {
        "market_version": MARKET_VERSION,
        "shared_table_version": shared_market.TABLE_VERSION,
        "start": start,
        "end": end,
        "source_file_count": len(minute_paths),
        "source_fingerprint": _source_fingerprint(minute_paths),
        "rows": rows,
        "columns": list(shared_market.OUTPUT_COLUMNS),
        "size_bytes": output_path.stat().st_size,
        "elapsed_seconds": time.perf_counter() - started,
        "copied_raw_minute_data": False,
    }
    _atomic_write_json(meta, meta_path)
    return meta


def output_is_current(path: Path, min_market_stocks: int) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != OUTPUT_COLUMNS or parquet.metadata.num_rows == 0:
            return False
        marker = parquet.read_row_group(
            0, columns=["trial_cache_version", "trial_min_market_stocks"]
        ).to_pandas()
        return bool(
            marker["trial_cache_version"].eq(CACHE_VERSION).all()
            and marker["trial_min_market_stocks"].eq(min_market_stocks).all()
        )
    except Exception:
        return False


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION
    worker_temp = Path(temp_dir) / f"trial_minute_worker_{os.getpid()}"
    worker_temp.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(f"SET temp_directory = '{_sql_path(worker_temp)}'")
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def _build_one_stock(
    minute_path: str,
    market_path: str,
    output_path: str,
    start: str,
    end: str,
    min_market_stocks: int,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程没有初始化。")
    source = Path(minute_path)
    output = Path(output_path)
    temporary = output.with_name(f"{output.stem}.tmp.parquet")
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    frame = _WORKER_CONNECTION.execute(
        build_stock_query(source, market_path, start, end)
    ).fetchdf()
    daily = build_daily_frame(frame, source.stem.lower(), min_market_stocks)
    if daily.empty:
        return {
            "code": source.stem.lower(),
            "status": "empty",
            "rows": 0,
            "size_bytes": 0,
            "seconds": time.perf_counter() - started,
        }
    daily.to_parquet(temporary, compression="zstd", index=False)
    temporary.replace(output)
    return {
        "code": source.stem.lower(),
        "status": "completed",
        "rows": len(daily),
        "size_bytes": output.stat().st_size,
        "seconds": time.perf_counter() - started,
    }


def _memory_sample() -> tuple[float, float]:
    parent = psutil.Process(os.getpid())
    children = [child for child in parent.children(recursive=True) if child.is_running()]
    memory = []
    for child in children:
        try:
            memory.append(child.memory_info().rss / 1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sum(memory), max(memory, default=0.0)


def _selected_codes(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        Path(line.strip().lstrip("\ufeff")).stem.lower()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    }


def list_input_files(
    data_dir: Path,
    codes_file: Path | None = None,
    limit: int | None = None,
) -> list[Path]:
    selected = _selected_codes(codes_file)
    files = sorted(data_dir.glob("*.parquet"), key=lambda path: path.stem.lower())
    if selected:
        files = [path for path in files if path.stem.lower() in selected]
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"没有找到分钟文件：{data_dir}")
    return files


def list_base_universe_minute_files(data_dir: Path) -> list[Path]:
    """只列出统一回测基础日表里存在的股票分钟文件。"""

    base_pattern = factor_store.DEFAULT_SOURCES["base_daily"]
    codes = sorted(
        path.stem.lower() for path in base_pattern.parent.glob(base_pattern.name)
    )
    files = [data_dir / f"{code}.parquet" for code in codes]
    files = [path for path in files if path.exists()]
    if not files:
        raise FileNotFoundError("基础日表股票范围内没有找到分钟文件。")
    return files


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=factor_store.DEFAULT_MINUTE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--market-file", type=Path, default=DEFAULT_MARKET_FILE)
    parser.add_argument("--market-meta", type=Path, default=DEFAULT_MARKET_META)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--cache-methodology", type=Path, default=DEFAULT_CACHE_METHODOLOGY
    )
    parser.add_argument("--temp-dir", type=Path, default=factor_store.DEFAULT_TEMP_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--worker-memory-limit", default="2GB")
    parser.add_argument("--min-market-stocks", type=int, default=DEFAULT_MIN_MARKET_STOCKS)
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force-market", action="store_true")
    parser.add_argument("--force-stocks", action="store_true")
    parser.add_argument("--market-only", action="store_true")
    parser.add_argument("--stocks-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("workers必须至少为1。")
    if args.min_market_stocks < 2:
        raise ValueError("min-market-stocks必须至少为2。")
    if args.market_only and args.stocks_only:
        raise ValueError("market-only与stocks-only不能同时使用。")

    output_dir = _assert_workspace_path(args.output_dir)
    market_file = _assert_workspace_path(args.market_file)
    market_meta = _assert_workspace_path(args.market_meta)
    manifest_path = _assert_workspace_path(args.manifest)
    methodology_path = _assert_workspace_path(args.cache_methodology)
    temp_dir = _assert_workspace_path(args.temp_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    market_file.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    if args.codes_file is not None:
        stock_files = list_input_files(args.data_dir, args.codes_file, args.limit)
        market_files = stock_files
    else:
        stock_files = list_base_universe_minute_files(args.data_dir)
        if args.limit is not None:
            stock_files = stock_files[: args.limit]
            market_files = stock_files
        else:
            market_files = list_input_files(args.data_dir)
    market_status: dict[str, object]
    if not args.stocks_only:
        current = _shared_market_file_is_usable(market_file)
        if current and not args.force_market:
            if market_meta.exists():
                try:
                    market_status = json.loads(
                        market_meta.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    market_status = {"path": str(market_file)}
            else:
                market_status = {"path": str(market_file)}
            market_status["status"] = "reused"
        else:
            if market_file.exists() and not args.force_market and not current:
                raise ValueError(
                    "现有文件不是完整的共用市场分钟表；"
                    "请先运行 build_shared_market_minute_stats.py，"
                    "或明确加--force-market重新生成。"
                )
            settings = factor_store.DuckDBSettings(
                database_path=factor_store.DEFAULT_DATABASE,
                temp_dir=temp_dir,
                threads=max(1, min(args.workers, 4)),
                memory_limit="4GB",
            )
            with factor_store.connect(settings) as connection:
                market_status = build_market_file(
                    connection,
                    market_files,
                    market_file,
                    market_meta,
                    args.start,
                    args.end,
                )
                market_status["status"] = "completed"
    else:
        if not _shared_market_file_is_usable(market_file):
            raise FileNotFoundError(
                f"缺少完整的共用市场分钟表：{market_file}。"
                "请先运行 build_shared_market_minute_stats.py。"
            )
        market_status = {"status": "existing", "path": str(market_file)}

    _atomic_write_json(
        {
            "formula_mode": "trial",
            "formula_label": "试算",
            "cache_version": CACHE_VERSION,
            "market_version": MARKET_VERSION,
            "factors": TRIAL_PARAMETERS,
            "shared_market_table": {
                "path": str(market_file),
                "columns": MARKET_COLUMNS,
                "source_columns": list(shared_market.OUTPUT_COLUMNS),
                "purpose": "CO_MIN与偏斜动量复用统一的全市场分钟统计表",
            },
        },
        methodology_path,
    )
    if args.market_only:
        print(f"共用市场分钟表已准备：{market_file}", flush=True)
        return 0

    jobs = []
    skipped = 0
    for source in stock_files:
        output = output_dir / f"{source.stem.lower()}.parquet"
        if output_is_current(output, args.min_market_stocks) and not args.force_stocks:
            skipped += 1
            continue
        jobs.append((source, output))

    started = time.perf_counter()
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    peak_total_mib = 0.0
    peak_single_mib = 0.0
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(str(temp_dir), args.worker_memory_limit),
    ) as executor:
        future_map = {
            executor.submit(
                _build_one_stock,
                str(source),
                str(market_file),
                str(output),
                args.start,
                args.end,
                args.min_market_stocks,
            ): source.stem.lower()
            for source, output in jobs
        }
        pending = set(future_map)
        completed = 0
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            total_mib, single_mib = _memory_sample()
            peak_total_mib = max(peak_total_mib, total_mib)
            peak_single_mib = max(peak_single_mib, single_mib)
            for future in done:
                code = future_map[future]
                completed += 1
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"已处理 {completed}/{len(jobs)}；失败 {len(failures)}；"
                        f"子进程内存最高合计 {peak_total_mib:.0f} MiB",
                        flush=True,
                    )

    elapsed = time.perf_counter() - started
    manifest = {
        "formula_mode": "trial",
        "formula_label": "试算",
        "cache_version": CACHE_VERSION,
        "start": args.start,
        "end": args.end,
        "stock_source_file_count": len(stock_files),
        "stock_source_fingerprint": _source_fingerprint(stock_files),
        "market_source_file_count": len(market_files),
        "market_source_fingerprint": _source_fingerprint(market_files),
        "output_dir": str(output_dir),
        "market_file": str(market_file),
        "market_status": market_status,
        "jobs_run": len(jobs),
        "skipped_current": skipped,
        "completed": sum(item["status"] == "completed" for item in results),
        "empty": sum(item["status"] == "empty" for item in results),
        "failures": failures,
        "rows_written": int(sum(int(item["rows"]) for item in results)),
        "bytes_written": int(sum(int(item["size_bytes"]) for item in results)),
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "worker_memory_limit": args.worker_memory_limit,
        "peak_total_child_mib": peak_total_mib,
        "peak_single_child_mib": peak_single_mib,
        "min_market_stocks": args.min_market_stocks,
        "output_columns": OUTPUT_COLUMNS,
        "copied_raw_minute_data": False,
        "created_at": pd.Timestamp.now().isoformat(),
    }
    _atomic_write_json(manifest, manifest_path)
    print(
        f"试算日值完成：新算{manifest['completed']}只，跳过{skipped}只，"
        f"失败{len(failures)}只，耗时{elapsed:.1f}秒。",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
