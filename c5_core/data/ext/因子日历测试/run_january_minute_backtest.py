# -*- coding: utf-8 -*-
"""汇总一月分钟因子，并按现有 Qlib 股票范围完成统一评价。"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    output_dir: Path
    qlib_dir: Path
    start: str = "2015-01-01"
    backtest_start: str = "2016-01-04"
    end: str = "2025-12-31"
    split_date: str = "2020-12-31"
    hold_days: int = 5
    groups: int = 10
    min_stocks: int = 50
    min_price: float = 3.0
    min_avg_amount: float = 5_000_000.0
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013


FACTOR_META = {
    "CGO": {
        "name": "资本利得突出量",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "T=100，月末生成信号。",
    },
    "UTD_20D_NEUTRAL": {
        "name": "换手率分布均匀度",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "月末回看20日；先逐日横截面标准化，再对log总市值回归并取残差。",
    },
    "CKDP": {
        "name": "筹码分布成本重心",
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_data_definition",
        "note": "分钟库没有逐价位筹码分布，原图也没有给筹码生成方法。",
    },
    "RVJN_5M": {
        "name": "下行跳跃波动率",
        "source_frequency": "周频（周二）",
        "signal_frequency": "weekly_tuesday",
        "expected_direction": "positive",
        "strict_status": "formula_typo_corrected",
        "note": "5分钟收益按r<0统计，绝对收益超过15%时置0；日值取5日均值，周二生成信号。",
    },
    "ILLIQ_SHORTCUT_5M_5D": {
        "name": "K线最短路径非流动性",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "按卡片式(9)采用5分钟K线、d=5；取t-1至t-5，不含当天，月末生成信号。",
    },
    "WEIGHTED_CLOSE_RATIO_5M": {
        "name": "加权收盘价比",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_example_frequency",
        "note": "采用卡片示例的5分钟、每日48段。",
    },
    "VOLUME_SHARE_KURT_10M": {
        "name": "成交量占比峰度",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_example_frequency",
        "note": "采用卡片示例的10分钟分段与Pearson总体峰度。",
    },
    "SCC_20D": {
        "name": "空间网络相对中心度",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "按正文的Pearson相关系数计算，不采用公式中误写的cov。",
    },
    "IDEAL_AMPLITUDE_20D": {
        "name": "理想振幅",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "20日窗口，高低收盘价各取25%。",
    },
    "REALIZED_SKEW_1M_20D": {
        "name": "高频已实现偏度",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "采用1分钟收益，取过去20日均值，月末生成信号。",
    },
    "REALIZED_SKEW_5M_20D": {
        "name": "高频已实现偏度（5分钟）",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "采用5分钟收益，取过去20日均值，月末生成信号。",
    },
    "LAST_30MIN_RETURN": {
        "name": "尾盘半小时收益率",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "卡片单日版：15:00收盘价/14:30分钟收盘价-1。",
    },
    "LAST_30MIN_RETURN_MA5": {
        "name": "尾盘半小时收益率（5日均值）",
        "source_frequency": "每5个交易日",
        "signal_frequency": "every_5_days",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "原报告主版：单日尾盘半小时收益率取5日均值，每5个交易日生成信号。",
    },
    "SIMILAR_REVERSE": {
        "name": "相似反转",
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_parameters",
        "note": "原图没有给HoldingTime、半衰期H和超额收益基准。",
    },
    "PRICE_RESILIENCY_1M_TREND": {
        "name": "价格弹性",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "approximation_only",
        "note": "原图要求tick；这里只提供1分钟K线近似，并按(20日指数移动平均-120日简单移动平均)/120日标准差计算趋势。",
    },
    "VOLUME_PEAK_PRICE_POS_20D_NEUTRAL": {
        "name": "量峰加权价格分位点",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "text_interpreted",
        "note": "日值取20日均值，去除20日反转影响，月末生成信号。",
    },
    "TGD_20D": {
        "name": "跌幅时间重心偏离",
        "source_frequency": "日频",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "formula_typo_corrected",
        "note": "跌幅重心按正文定义使用D，不采用卡片公式中误写的U。",
    },
    "VOLUME_RIDGE_GAP_SKEW_20D": {
        "name": "量岭间隔偏度",
        "source_frequency": "月频",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "合并过去20日同日相邻量岭间隔，计算Pearson总体偏度，月末生成信号。",
    },
}


PRICE_AMOUNT_BAD_COLUMNS = (
    "vwap_outside_range_1pct",
    "amount_price_inconsistent",
    "price_amount_bad",
)


ALIASES = {"sz302132": "SZ300114"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default=str(BASE_DIR / "january_original_minute_results" / "cache" / "daily_by_stock"),
    )
    parser.add_argument(
        "--output-dir", default=str(BASE_DIR / "january_original_minute_results")
    )
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--cgo-window", type=int, default=100)
    parser.add_argument("--illiq-window", type=int, default=5)
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args()


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
    os.replace(temporary, path)


def read_calendar(qlib_dir: Path, start: str, end: str) -> pd.DatetimeIndex:
    path = qlib_dir / "calendars" / "day.txt"
    dates = pd.to_datetime(pd.read_csv(path, header=None)[0], errors="coerce")
    dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    return pd.DatetimeIndex(dates.dropna().drop_duplicates().sort_values())


def qlib_code(code: str) -> str:
    lower = code.lower()
    return ALIASES.get(lower, lower.upper())


def read_membership_rows(qlib_dir: Path, start: str, end: str) -> pd.DataFrame:
    frames = []
    for name in ["csi800", "csi1000"]:
        path = qlib_dir / "instruments" / f"{name}.txt"
        frame = pd.read_csv(path, sep="\t", header=None, names=["instrument", "start", "end"])
        frame["start"] = pd.to_datetime(frame["start"])
        frame["end"] = pd.to_datetime(frame["end"])
        frame = frame[(frame["end"] >= pd.Timestamp(start)) & (frame["start"] <= pd.Timestamp(end))]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def list_cache_files(cache_dir: Path) -> list[Path]:
    files = sorted(cache_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"没有找到每日轻量文件：{cache_dir}")
    return files


def load_panel(files: list[Path], start: str, end: str) -> pd.DataFrame:
    frames = []
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end)
    for index, path in enumerate(files, start=1):
        frame = pd.read_parquet(path)
        if "date" not in frame.columns:
            raise ValueError(f"每日轻量文件缺少date列：{path}")
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame[(frame["date"] >= lower) & (frame["date"] <= upper)].copy()
        if "code" not in frame.columns:
            frame["code"] = path.stem.lower()
        frames.append(frame)
        if index % 500 == 0:
            print(f"  已读取 {index}/{len(files)} 只股票", flush=True)
    panel = pd.concat(frames, ignore_index=True)
    panel["code"] = panel["code"].str.lower()
    panel = panel.sort_values(["date", "code"]).drop_duplicates(["date", "code"], keep="last")
    return panel


def make_axes(panel: pd.DataFrame, calendar: pd.DatetimeIndex) -> tuple[list[str], dict, dict]:
    stocks = sorted(panel["code"].unique())
    date_map = {date: index for index, date in enumerate(calendar)}
    stock_map = {stock: index for index, stock in enumerate(stocks)}
    return stocks, date_map, stock_map


def _panel_matrix_coordinates(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """一次算好整张表的行列位置，后续各数值列直接复用。"""

    cache_key = (id(date_map), id(stock_map), len(dates), len(stocks), len(panel))
    cached = panel.attrs.get("_panel_matrix_coordinates")
    if isinstance(cached, tuple) and len(cached) == 4 and cached[0] == cache_key:
        return cached[1], cached[2], cached[3]

    rows = panel["date"].map(date_map).fillna(-1).to_numpy(dtype=np.int32)
    columns = panel["code"].map(stock_map).fillna(-1).to_numpy(dtype=np.int32)
    valid = (rows >= 0) & (columns >= 0)
    panel.attrs["_panel_matrix_coordinates"] = (cache_key, rows, columns, valid)
    return rows, columns, valid


def panel_to_matrix(
    panel: pd.DataFrame,
    column: str,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    dtype=np.float64,
) -> np.ndarray:
    if column not in panel.columns:
        raise KeyError(f"每日轻量文件缺少列：{column}")
    result = np.full((len(dates), len(stocks)), np.nan, dtype=dtype)
    rows, columns, in_axes = _panel_matrix_coordinates(
        panel, dates, stocks, date_map, stock_map
    )
    valid = in_axes & panel[column].notna().to_numpy()
    result[rows[valid], columns[valid]] = panel.loc[valid, column].to_numpy(dtype=dtype)
    return result


def panel_flag_to_matrix(
    panel: pd.DataFrame,
    column: str,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
) -> np.ndarray:
    """把每日异常标记列转成布尔矩阵；缺值按没有异常处理。"""
    result = np.zeros((len(dates), len(stocks)), dtype=bool)
    rows, columns, in_axes = _panel_matrix_coordinates(
        panel, dates, stocks, date_map, stock_map
    )
    valid = in_axes & panel[column].notna().to_numpy()
    subset = panel.loc[valid, column]
    if subset.empty:
        return result
    values = subset
    if pd.api.types.is_bool_dtype(values.dtype):
        flags = values.to_numpy(dtype=bool)
    elif pd.api.types.is_numeric_dtype(values.dtype):
        flags = values.to_numpy(dtype=np.float64) != 0.0
    else:
        flags = (
            values.astype("string")
            .str.strip()
            .str.lower()
            .isin(["1", "true", "yes", "y", "bad"])
            .to_numpy(dtype=bool)
        )
    result[rows[valid], columns[valid]] = flags
    return result


def build_price_amount_bad_matrix(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
) -> np.ndarray:
    """合并当前和旧版缓存里的价格、成交额异常标记。"""
    result = np.zeros((len(dates), len(stocks)), dtype=bool)
    for column in PRICE_AMOUNT_BAD_COLUMNS:
        if column in panel.columns:
            result |= panel_flag_to_matrix(panel, column, dates, stocks, date_map, stock_map)
    return result


def mask_bad_days(values: np.ndarray, bad_days: np.ndarray) -> np.ndarray:
    result = values.copy()
    result[bad_days] = np.nan
    return result


def build_membership(
    dates: pd.DatetimeIndex,
    stocks: list[str],
    rows: pd.DataFrame,
) -> tuple[np.ndarray, pd.DataFrame]:
    stock_map = {}
    for index, stock in enumerate(stocks):
        stock_map[stock.upper()] = index
        old_code = ALIASES.get(stock.lower())
        if old_code is not None:
            stock_map[old_code] = index
    membership = np.zeros((len(dates), len(stocks)), dtype=bool)
    missing = []
    for row in rows.itertuples(index=False):
        code = str(row.instrument).upper()
        stock_index = stock_map.get(code)
        if stock_index is None:
            missing.append(code)
            continue
        left = dates.searchsorted(max(pd.Timestamp(row.start), dates[0]), side="left")
        right = dates.searchsorted(min(pd.Timestamp(row.end), dates[-1]), side="right")
        if left < right:
            membership[left:right, stock_index] = True
    missing_frame = pd.DataFrame({"qlib_instrument_without_cache": sorted(set(missing))})
    return membership, missing_frame


def rolling_sum_count(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(values)
    filled = np.where(valid, values, 0.0)
    sums = np.cumsum(filled, axis=0, dtype=np.float64)
    counts = np.cumsum(valid.astype(np.int32), axis=0, dtype=np.int32)
    result_sum = sums.copy()
    result_count = counts.copy()
    if window < len(values):
        result_sum[window:] = sums[window:] - sums[:-window]
        result_count[window:] = counts[window:] - counts[:-window]
    result_sum[: window - 1] = np.nan
    result_count[: window - 1] = 0
    return result_sum, result_count


def rolling_mean(values: np.ndarray, window: int, require_full: bool = True) -> np.ndarray:
    sums, counts = rolling_sum_count(values, window)
    result = sums / np.maximum(counts, 1)
    minimum = window if require_full else max(window // 2, 1)
    result[counts < minimum] = np.nan
    return result


def rolling_mean_std(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    sums, counts = rolling_sum_count(values, window)
    square_sums, _ = rolling_sum_count(values * values, window)
    mean = sums / np.maximum(counts, 1)
    variance = square_sums / np.maximum(counts, 1) - mean * mean
    std = np.sqrt(np.maximum(variance, 0.0))
    mean[counts < window] = np.nan
    std[counts < window] = np.nan
    return mean, std


def exponential_moving_average(values: np.ndarray, span: int) -> np.ndarray:
    """逐股计算指数移动平均；当前日缺值时不生成因子值。"""
    alpha = 2.0 / (span + 1.0)
    result = np.full_like(values, np.nan, dtype=np.float64)
    running = np.full(values.shape[1], np.nan, dtype=np.float64)
    counts = np.zeros(values.shape[1], dtype=np.int32)
    for index in range(len(values)):
        current = values[index]
        valid = np.isfinite(current)
        first = valid & ~np.isfinite(running)
        continuing = valid & np.isfinite(running)
        running[first] = current[first]
        running[continuing] = (
            alpha * current[continuing] + (1.0 - alpha) * running[continuing]
        )
        counts[valid] += 1
        ready = valid & (counts >= span)
        result[index, ready] = running[ready]
    return result


def build_exit_price_matrices(
    open_price: np.ndarray, close_price: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """退出日停牌时使用此前最近收盘价估值，不提前剔除该股票。"""
    open_exit = np.full_like(open_price, np.nan, dtype=np.float64)
    close_exit = np.full_like(close_price, np.nan, dtype=np.float64)
    last_close = np.full(close_price.shape[1], np.nan, dtype=np.float64)
    for index in range(len(close_price)):
        current_open = open_price[index]
        open_exit[index] = np.where(np.isfinite(current_open), current_open, last_close)
        current_close = close_price[index]
        valid_close = np.isfinite(current_close)
        last_close[valid_close] = current_close[valid_close]
        close_exit[index] = last_close
    return open_exit, close_exit


def daily_returns(close: np.ndarray) -> np.ndarray:
    result = np.full_like(close, np.nan)
    result[1:] = close[1:] / close[:-1] - 1.0
    return result


def neutralize_one_factor(
    factor: np.ndarray,
    explanatory: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    result = np.full_like(factor, np.nan)
    for index in range(len(factor)):
        valid = membership[index] & np.isfinite(factor[index]) & np.isfinite(explanatory[index])
        if valid.sum() < min_stocks:
            continue
        y = factor[index, valid]
        x = explanatory[index, valid]
        x_centered = x - x.mean()
        denominator = np.dot(x_centered, x_centered)
        if denominator <= 1e-20:
            continue
        beta = np.dot(x_centered, y - y.mean()) / denominator
        result[index, np.flatnonzero(valid)] = y - (y.mean() + beta * x_centered)
    return result


def cross_sectional_standardize(
    factor: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    result = np.full_like(factor, np.nan, dtype=np.float64)
    for index in range(len(factor)):
        valid = membership[index] & np.isfinite(factor[index])
        if valid.sum() < min_stocks:
            continue
        values = factor[index, valid]
        standard = values.std(ddof=0)
        if standard <= 1e-15:
            continue
        result[index, np.flatnonzero(valid)] = (values - values.mean()) / standard
    return result


def compute_scc(close: np.ndarray, membership: np.ndarray, window: int, min_stocks: int) -> np.ndarray:
    returns = daily_returns(close)
    result = np.full_like(close, np.nan)
    for end in range(window, len(close)):
        sample = returns[end - window + 1 : end + 1]
        valid = membership[end] & np.isfinite(sample).all(axis=0)
        indices = np.flatnonzero(valid)
        if len(indices) < min_stocks:
            continue
        part = sample[:, indices]
        means = part.mean(axis=0)
        stds = part.std(axis=0, ddof=0)
        nonconstant = stds > 1e-12
        indices = indices[nonconstant]
        if len(indices) < min_stocks:
            continue
        z = (part[:, nonconstant] - means[nonconstant]) / stds[nonconstant]
        market_sum = z.sum(axis=1)
        correlation_sum = z.T @ market_sum / window
        average_correlation = (correlation_sum - 1.0) / (len(indices) - 1)
        average_correlation = np.clip(average_correlation, -0.999999, 0.999999)
        result[end, indices] = 1.0 / (2.0 * (1.0 - average_correlation))
    return result


def compute_ideal_amplitude(
    close: np.ndarray, high: np.ndarray, low: np.ndarray, window: int = 20, fraction: float = 0.25
) -> np.ndarray:
    amplitude = high / low - 1.0
    result = np.full_like(close, np.nan)
    count = max(int(round(window * fraction)), 1)
    for end in range(window - 1, len(close)):
        close_part = close[end - window + 1 : end + 1]
        amplitude_part = amplitude[end - window + 1 : end + 1]
        valid = np.isfinite(close_part).all(axis=0) & np.isfinite(amplitude_part).all(axis=0)
        if not valid.any():
            continue
        indices = np.flatnonzero(valid)
        order = np.argsort(close_part[:, indices], axis=0, kind="stable")
        sorted_amplitude = np.take_along_axis(amplitude_part[:, indices], order, axis=0)
        result[end, indices] = sorted_amplitude[-count:].mean(axis=0) - sorted_amplitude[:count].mean(axis=0)
    return result


def compute_cgo(
    close: np.ndarray,
    vwap: np.ndarray,
    turnover: np.ndarray,
    window: int,
) -> np.ndarray:
    """按卡片的换手率存续权重计算固定T的参考价格。"""
    rates = np.clip(turnover, 0.0, 1.0)
    weighted_price = np.zeros_like(close, dtype=np.float64)
    weight_sum = np.zeros_like(close, dtype=np.float64)
    survival = np.ones_like(close, dtype=np.float64)

    # 同一历史距离的全部股票和日期一起计算，避免逐格重复循环。
    for lag in range(window):
        target = slice(lag, None)
        source = slice(None) if lag == 0 else slice(None, -lag)
        rate = rates[source]
        weight = rate * survival[target]
        weight_sum[target] += weight
        weight *= vwap[source]
        weighted_price[target] += weight
        survival[target] *= 1.0 - rate

    reference = np.full_like(close, np.nan, dtype=np.float64)
    np.divide(
        weighted_price,
        weight_sum,
        out=reference,
        where=np.isfinite(weight_sum) & (weight_sum > 1e-15),
    )
    result = np.full_like(close, np.nan, dtype=np.float64)
    valid = np.isfinite(close) & np.isfinite(reference) & (close != 0)
    np.divide(close - reference, close, out=result, where=valid)
    result[: window - 1] = np.nan
    return result


def compute_gap_skew(
    count: np.ndarray,
    sum1: np.ndarray,
    sum2: np.ndarray,
    sum3: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    roll_count, valid_days = rolling_sum_count(count, window)
    roll1, _ = rolling_sum_count(sum1, window)
    roll2, _ = rolling_sum_count(sum2, window)
    roll3, _ = rolling_sum_count(sum3, window)
    mean = roll1 / np.maximum(roll_count, 1)
    second = roll2 / np.maximum(roll_count, 1) - mean * mean
    third = roll3 / np.maximum(roll_count, 1) - 3.0 * mean * (roll2 / np.maximum(roll_count, 1)) + 2.0 * mean**3
    result = np.full_like(third, np.nan)
    np.divide(
        third,
        np.maximum(second, 0.0) ** 1.5,
        out=result,
        where=second > 1e-15,
    )
    result[(valid_days < window) | (roll_count < 3) | (second <= 1e-15)] = np.nan
    return result


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    cgo_window: int,
    illiq_window: int,
    min_stocks: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    print("[2/6] 生成价格、成交和筛选矩阵...", flush=True)
    names = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "amount": "amount",
        "volume": "volume",
        "float_shares": "float_shares",
        "total_shares": "total_shares",
        "qfq_ratio": "qfq_ratio",
        "vwap_qfq": "vwap_qfq",
    }
    arrays = {
        key: panel_to_matrix(panel, source, dates, stocks, date_map, stock_map)
        for key, source in names.items()
    }
    arrays["raw_close"] = arrays["close"] / arrays["qfq_ratio"]
    arrays["turnover"] = arrays["volume"] / arrays["float_shares"]
    arrays["total_market_cap"] = arrays["raw_close"] * arrays["total_shares"]
    arrays["price_amount_bad"] = build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = build_exit_price_matrices(
        arrays["open"], arrays["close"]
    )

    def cache(name: str) -> np.ndarray:
        return panel_to_matrix(panel, name, dates, stocks, date_map, stock_map)

    print("[3/6] 计算16个因子的可执行部分...", flush=True)
    factors: dict[str, np.ndarray] = {}
    if cgo_window > 0:
        factors["CGO"] = compute_cgo(
            arrays["close"],
            mask_bad_days(arrays["vwap_qfq"], arrays["price_amount_bad"]),
            arrays["turnover"],
            cgo_window,
        )
        FACTOR_META["CGO"]["note"] = f"T={cgo_window}，月末生成信号。"

    turnvol = cache("turnvol_daily")
    turnvol_mean, turnvol_std = rolling_mean_std(turnvol, 20)
    utd_raw = turnvol_std / turnvol_mean
    utd_standardized = cross_sectional_standardize(utd_raw, membership, min_stocks)
    log_market_cap = np.log(np.where(arrays["total_market_cap"] > 0, arrays["total_market_cap"], np.nan))
    factors["UTD_20D_NEUTRAL"] = neutralize_one_factor(
        utd_standardized, log_market_cap, membership, min_stocks
    )

    factors["RVJN_5M"] = rolling_mean(cache("rvjn_5m"), 5)
    illiq_daily = mask_bad_days(
        cache("illiq_shortcut_5m_daily"), arrays["price_amount_bad"]
    )
    illiq_lagged = np.roll(illiq_daily, 1, axis=0)
    illiq_lagged[0] = np.nan
    factors["ILLIQ_SHORTCUT_5M_5D"] = rolling_mean(illiq_lagged, illiq_window)
    factors["WEIGHTED_CLOSE_RATIO_5M"] = cache("weighted_close_ratio_5m")
    factors["VOLUME_SHARE_KURT_10M"] = cache("volume_share_kurt_10m")
    factors["SCC_20D"] = compute_scc(arrays["close"], membership, 20, min_stocks)
    factors["IDEAL_AMPLITUDE_20D"] = compute_ideal_amplitude(
        arrays["close"], arrays["high"], arrays["low"]
    )
    factors["REALIZED_SKEW_1M_20D"] = rolling_mean(cache("realized_skew_1m"), 20)
    factors["REALIZED_SKEW_5M_20D"] = rolling_mean(cache("realized_skew_5m"), 20)
    last_30min_return = cache("last_30min_return")
    factors["LAST_30MIN_RETURN"] = last_30min_return
    factors["LAST_30MIN_RETURN_MA5"] = rolling_mean(last_30min_return, 5)

    elasticity = mask_bad_days(
        cache("price_resiliency_1m_daily"), arrays["price_amount_bad"]
    )
    elasticity_mean20 = exponential_moving_average(elasticity, 20)
    elasticity_mean120, elasticity_std120 = rolling_mean_std(elasticity, 120)
    factors["PRICE_RESILIENCY_1M_TREND"] = (
        elasticity_mean20 - elasticity_mean120
    ) / elasticity_std120

    peak_position_daily = mask_bad_days(
        cache("volume_peak_price_position_daily"), arrays["price_amount_bad"]
    )
    peak_position = rolling_mean(peak_position_daily, 20)
    reversal20 = arrays["close"] / np.roll(arrays["close"], 20, axis=0) - 1.0
    reversal20[:20] = np.nan
    factors["VOLUME_PEAK_PRICE_POS_20D_NEUTRAL"] = neutralize_one_factor(
        peak_position, reversal20, membership, min_stocks
    )

    gu = cache("gu_1m")
    gd = cache("gd_1m")
    daily_tgd_residual = neutralize_one_factor(gd, gu, membership, min_stocks)
    factors["TGD_20D"] = rolling_mean(daily_tgd_residual, 20)

    factors["VOLUME_RIDGE_GAP_SKEW_20D"] = compute_gap_skew(
        cache("ridge_gap_count"),
        cache("ridge_gap_sum1"),
        cache("ridge_gap_sum2"),
        cache("ridge_gap_sum3"),
        20,
    )
    return factors, arrays


def rolling_average_amount(amount: np.ndarray) -> np.ndarray:
    return rolling_mean(amount, 20, require_full=False)


def future_open_returns(
    open_price: np.ndarray,
    hold_days: int,
    exit_mark_price: np.ndarray | None = None,
) -> np.ndarray:
    result = np.full_like(open_price, np.nan)
    exits = open_price if exit_mark_price is None else exit_mark_price
    result[: -hold_days - 1] = exits[hold_days + 1 :] / open_price[1:-hold_days] - 1.0
    return result


def rank_ic(values: np.ndarray, returns: np.ndarray, min_stocks: int) -> tuple[float, int]:
    valid = np.isfinite(values) & np.isfinite(returns)
    count = int(valid.sum())
    if count < min_stocks:
        return np.nan, count
    value_rank = stats.rankdata(values[valid], method="average")
    return_rank = stats.rankdata(returns[valid], method="average")
    return float(np.corrcoef(value_rank, return_rank)[0, 1]), count


def build_eligible(
    arrays: dict[str, np.ndarray], membership: np.ndarray, min_price: float, min_amount: float
) -> np.ndarray:
    average_amount = rolling_average_amount(
        mask_bad_days(arrays["amount"], arrays["price_amount_bad"])
    )
    return (
        membership
        & np.isfinite(arrays["raw_close"])
        & (arrays["raw_close"] >= min_price)
        & np.isfinite(average_amount)
        & (average_amount >= min_amount)
    )


def calculate_daily_ic(
    factor_name: str,
    factor: np.ndarray,
    future_return: np.ndarray,
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    rows = []
    start = dates.searchsorted(pd.Timestamp(settings.backtest_start))
    stop = len(dates) - settings.hold_days - 1
    for index in range(start, stop):
        mask = eligible[index] & np.isfinite(factor[index]) & np.isfinite(future_return[index])
        ic, count = rank_ic(factor[index, mask], future_return[index, mask], settings.min_stocks)
        if np.isfinite(ic):
            rows.append(
                {
                    "factor": factor_name,
                    "date": dates[index],
                    "entry_date": dates[index + 1],
                    "exit_date": dates[index + settings.hold_days + 1],
                    "ic": ic,
                    "stock_count": count,
                }
            )
    return pd.DataFrame(rows)


def month_pairs(dates: pd.DatetimeIndex, backtest_start: str) -> list[tuple[int, int, int]]:
    periods = dates.to_period("M")
    ends = np.flatnonzero(np.r_[periods[:-1].to_numpy() != periods[1:].to_numpy(), True])
    pairs = []
    for position in range(len(ends) - 1):
        signal = int(ends[position])
        entry = signal + 1
        exit_index = int(ends[position + 1])
        if dates[entry] >= pd.Timestamp(backtest_start) and entry <= exit_index:
            pairs.append((signal, entry, exit_index))
    return pairs


def original_signal_mask(
    dates: pd.DatetimeIndex, frequency: str, backtest_start: str
) -> np.ndarray:
    """按原报告的日期安排选出真正生成信号的交易日。"""
    result = np.zeros(len(dates), dtype=bool)
    if frequency == "daily":
        result[:] = True
    elif frequency == "weekly_tuesday":
        result[dates.weekday == 1] = True
    elif frequency == "every_5_days":
        start = dates.searchsorted(pd.Timestamp(backtest_start))
        result[start::5] = True
    elif frequency == "monthly":
        periods = dates.to_period("M")
        ends = np.flatnonzero(np.r_[periods[:-1].to_numpy() != periods[1:].to_numpy(), True])
        result[ends] = True
    elif frequency != "unavailable":
        raise ValueError(f"不支持的信号频率：{frequency}")
    return result


def build_original_signals(
    factors: dict[str, np.ndarray], dates: pd.DatetimeIndex, backtest_start: str
) -> dict[str, np.ndarray]:
    signals = {}
    for factor_name, values in factors.items():
        frequency = FACTOR_META[factor_name]["signal_frequency"]
        keep = original_signal_mask(dates, frequency, backtest_start)
        signal = np.full_like(values, np.nan)
        signal[keep] = values[keep]
        signals[factor_name] = signal
    return signals


def select_factor_codes(
    factors: dict[str, np.ndarray], requested_codes: Sequence[str] | None
) -> dict[str, np.ndarray]:
    """按给定顺序只保留本次需要回测的因子；不传名单时保持原行为。"""

    if not requested_codes:
        return factors
    requested = list(requested_codes)
    if len(requested) != len(set(requested)):
        raise ValueError("--factor-codes 里出现重复因子代码。")
    missing = [code for code in requested if code not in factors]
    if missing:
        raise ValueError(f"本入口没有这些因子：{', '.join(missing)}")
    return {code: factors[code] for code in requested}


def calculate_monthly_ic(
    factor_name: str,
    factor: np.ndarray,
    arrays: dict[str, np.ndarray],
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    rows = []
    for signal, entry, exit_index in month_pairs(dates, settings.backtest_start):
        returns = arrays["close_exit_mark"][exit_index] / arrays["open"][entry] - 1.0
        mask = eligible[signal] & np.isfinite(factor[signal]) & np.isfinite(returns)
        ic, count = rank_ic(factor[signal, mask], returns[mask], settings.min_stocks)
        if np.isfinite(ic):
            rows.append(
                {
                    "factor": factor_name,
                    "signal_date": dates[signal],
                    "entry_date": dates[entry],
                    "exit_date": dates[exit_index],
                    "ic": ic,
                    "stock_count": count,
                }
            )
    return pd.DataFrame(rows)


def calculate_original_frequency_ic(
    factor_name: str,
    signal: np.ndarray,
    future_return: np.ndarray,
    arrays: dict[str, np.ndarray],
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    frequency = FACTOR_META[factor_name]["signal_frequency"]
    if frequency == "monthly":
        frame = calculate_monthly_ic(factor_name, signal, arrays, eligible, dates, settings)
        horizon = "next_month"
    else:
        frame = calculate_daily_ic(
            factor_name, signal, future_return, eligible, dates, settings
        ).rename(columns={"date": "signal_date"})
        horizon = "next_5_trading_days"
        if not frame.empty:
            positions = dates.get_indexer(frame["signal_date"])
            frame["entry_date"] = dates[positions + 1]
            frame["exit_date"] = dates[positions + settings.hold_days + 1]
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "factor",
                "signal_frequency",
                "return_horizon",
                "signal_date",
                "entry_date",
                "exit_date",
                "ic",
                "stock_count",
            ]
        )
    frame["signal_frequency"] = frequency
    frame["return_horizon"] = horizon
    return frame


def newey_west_t(values: np.ndarray, lag: int) -> float:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if len(values) < lag + 3:
        return np.nan
    centered = values - values.mean()
    variance = np.dot(centered, centered) / len(values)
    for step in range(1, lag + 1):
        covariance = np.dot(centered[step:], centered[:-step]) / len(values)
        variance += 2.0 * (1.0 - step / (lag + 1.0)) * covariance
    standard_error = math.sqrt(max(variance, 0.0) / len(values))
    return values.mean() / standard_error if standard_error > 0 else np.nan


def summarize_ic(frame: pd.DataFrame, date_column: str, frequency: str, split_date: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = []
    nominal_periods_per_year = {
        # 日频信号对应未来5日重叠收益，按约50.4个独立区间年化，避免放大ICIR。
        "unified_5d": 252 / 5,
        "daily": 252 / 5,
        "weekly_tuesday": 52,
        "every_5_days": 252 / 5,
        "monthly": 12,
    }[frequency]
    split = pd.Timestamp(split_date)
    for factor_name, factor_data in frame.groupby("factor"):
        # 训练区间只保留在分界日前已经结束的未来收益，避免把2021年的价格用于选方向。
        if {"entry_date", "exit_date"}.issubset(factor_data.columns):
            in_sample = factor_data["exit_date"] <= split
            out_of_sample = factor_data["entry_date"] > split
        else:
            in_sample = factor_data[date_column] <= split
            out_of_sample = factor_data[date_column] > split
        periods = {
            "full": np.ones(len(factor_data), dtype=bool),
            "2016_2020": in_sample,
            "2021_2025": out_of_sample,
        }
        for period, mask in periods.items():
            sample = factor_data.loc[mask].copy()
            values = sample["ic"].dropna().to_numpy(dtype=float)
            if len(values) == 0:
                continue
            time_column = "entry_date" if "entry_date" in sample.columns else date_column
            observed_dates = pd.to_datetime(sample.loc[sample["ic"].notna(), time_column])
            if len(observed_dates) > 1 and observed_dates.max() > observed_dates.min():
                elapsed_years = (observed_dates.max() - observed_dates.min()).days / 365.25
                observed_per_year = len(values) / elapsed_years
                periods_per_year = (
                    observed_per_year / 5.0
                    if frequency in {"unified_5d", "daily"}
                    else observed_per_year
                )
            else:
                periods_per_year = nominal_periods_per_year
            annualizer = math.sqrt(periods_per_year)
            standard = values.std(ddof=1) if len(values) > 1 else np.nan
            raw_icir = values.mean() / standard if standard > 0 else np.nan
            rows.append(
                {
                    "factor": factor_name,
                    "evaluation_frequency": frequency,
                    "period": period,
                    "ic_mean": values.mean(),
                    "ic_std": standard,
                    "icir_raw": raw_icir,
                    "icir_annualized": raw_icir * annualizer,
                    "periods_per_year": periods_per_year,
                    "positive_ratio": (values > 0).mean(),
                    "newey_west_t": newey_west_t(
                        values, 5 if frequency in {"unified_5d", "daily"} else 1
                    ),
                    "observations": len(values),
                    "avg_stock_count": factor_data.loc[mask, "stock_count"].mean(),
                }
            )
    return pd.DataFrame(rows)


def summarize_original_frequency_ic(frame: pd.DataFrame, split_date: str) -> pd.DataFrame:
    summaries = []
    for frequency, subset in frame.groupby("signal_frequency"):
        summaries.append(summarize_ic(subset, "signal_date", frequency, split_date))
    return pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()


def transition_cost(previous: set[int], current: set[int], settings: Settings) -> tuple[float, float]:
    if not current:
        return 0.0, 0.0
    old_weight = 1.0 / len(previous) if previous else 0.0
    new_weight = 1.0 / len(current)
    buys = 0.0
    sells = 0.0
    for stock in previous | current:
        before = old_weight if stock in previous else 0.0
        after = new_weight if stock in current else 0.0
        if after > before:
            buys += after - before
        else:
            sells += before - after
    return buys * settings.buy_cost + sells * settings.sell_cost, (buys + sells) / 2.0


def original_backtest_periods(
    dates: pd.DatetimeIndex,
    frequency: str,
    backtest_start: str,
    hold_days: int,
) -> list[tuple[int, int, int]]:
    """按因子的原信号频率给出信号、入场和退出位置。"""
    if frequency == "monthly":
        return month_pairs(dates, backtest_start)

    start = dates.searchsorted(pd.Timestamp(backtest_start))
    stop = len(dates) - hold_days - 1
    if frequency == "weekly_tuesday":
        signals = np.flatnonzero((dates.weekday == 1) & (dates >= pd.Timestamp(backtest_start)))
    elif frequency in {"every_5_days", "daily"}:
        # 日频卡片没有给持有期，交易时仍按每五个交易日生成一次信号。
        signals = np.arange(start, stop, hold_days, dtype=int)
    elif frequency == "unavailable":
        return []
    else:
        raise ValueError(f"不支持的信号频率：{frequency}")
    periods = [
        (int(signal), int(signal + 1), int(signal + hold_days + 1))
        for signal in signals
        if signal < stop
    ]
    if frequency != "weekly_tuesday":
        return periods

    # 节假日可能让相邻两个周二信号对应的5日持有期重叠；交易结果只保留不重叠的期次。
    non_overlapping = []
    previous_exit = -1
    for period in periods:
        _, entry, exit_index = period
        if entry < previous_exit:
            continue
        non_overlapping.append(period)
        previous_exit = exit_index
    return non_overlapping


def run_original_frequency_backtest(
    factor_name: str,
    signal: np.ndarray,
    future_return: np.ndarray,
    eligible: np.ndarray,
    arrays: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    """按原信号日期完成十分位分组交易。"""
    frequency = FACTOR_META[factor_name]["signal_frequency"]
    rows = []
    previous_low: set[int] = set()
    previous_high: set[int] = set()
    previous_exit_index: int | None = None
    periods = original_backtest_periods(
        dates, frequency, settings.backtest_start, settings.hold_days
    )
    for signal_index, entry_index, exit_index in periods:
        entry_price = arrays["open"][entry_index]
        exit_price = (
            arrays["close_exit_mark"][exit_index]
            if frequency == "monthly"
            else arrays["open_exit_mark"][exit_index]
        )
        entry_tradable = np.isfinite(entry_price) & (entry_price > 0)
        exit_valuable = np.isfinite(exit_price) & (exit_price > 0)
        period_return = np.zeros(entry_price.shape, dtype=np.float64)
        valued = entry_tradable & exit_valuable
        period_return[valued] = exit_price[valued] / entry_price[valued] - 1.0
        horizon = "next_month" if frequency == "monthly" else "next_5_trading_days"

        mask = (
            eligible[signal_index]
            & entry_tradable
            & np.isfinite(signal[signal_index])
        )
        indices = np.flatnonzero(mask)
        if len(indices) < settings.min_stocks:
            continue
        order = indices[np.argsort(signal[signal_index, indices], kind="stable")]
        count = max(len(order) // settings.groups, 1)
        low = set(order[:count].tolist())
        high = set(order[-count:].tolist())
        low_return = float(np.mean(period_return[np.fromiter(low, dtype=int)]))
        high_return = float(np.mean(period_return[np.fromiter(high, dtype=int)]))
        benchmark = float(np.mean(period_return[indices]))
        if frequency == "monthly":
            # 月末收盘退出、下月首日开盘重新进入，两个时点之间不计收益，因此每期完整收费。
            low_cost = high_cost = settings.buy_cost + settings.sell_cost
            low_turnover = high_turnover = 1.0
        else:
            if previous_exit_index is not None and previous_exit_index != entry_index:
                # 两期不相接时，上一期先退出并空仓，本期再完整买入。
                rows[-1]["low_net_return"] -= settings.sell_cost
                rows[-1]["high_net_return"] -= settings.sell_cost
                rows[-1]["low_turnover"] += 0.5
                rows[-1]["high_turnover"] += 0.5
                low_cost = high_cost = settings.buy_cost
                low_turnover = high_turnover = 0.5
            else:
                low_cost, low_turnover = transition_cost(previous_low, low, settings)
                high_cost, high_turnover = transition_cost(previous_high, high, settings)
        rows.append(
            {
                "factor": factor_name,
                "signal_frequency": frequency,
                "trade_frequency": "every_5_days" if frequency == "daily" else frequency,
                "return_horizon": horizon,
                "signal_date": dates[signal_index],
                "entry_date": dates[entry_index],
                "exit_date": dates[exit_index],
                "low_raw_return": low_return,
                "low_net_return": low_return - low_cost,
                "low_turnover": low_turnover,
                "high_raw_return": high_return,
                "high_net_return": high_return - high_cost,
                "high_turnover": high_turnover,
                "benchmark_return": benchmark,
                "eligible_stocks": len(indices),
                "group_stocks": count,
                "exit_price_fallback_stocks": int(
                    np.sum(~np.isfinite(arrays["close"][exit_index, indices]))
                    if frequency == "monthly"
                    else np.sum(~np.isfinite(arrays["open"][exit_index, indices]))
                ),
                "exit_price_missing_stocks": int(np.sum(~exit_valuable[indices])),
            }
        )
        previous_low = low
        previous_high = high
        previous_exit_index = exit_index
    if rows and frequency != "monthly":
        # 最后一期退出后不再换仓，补记全仓卖出费用。
        rows[-1]["low_net_return"] -= settings.sell_cost
        rows[-1]["high_net_return"] -= settings.sell_cost
        rows[-1]["low_turnover"] += 0.5
        rows[-1]["high_turnover"] += 0.5
    return pd.DataFrame(rows)


def run_five_day_backtest(
    factor_name: str,
    factor: np.ndarray,
    future_return: np.ndarray,
    eligible: np.ndarray,
    arrays: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    rows = []
    previous_low: set[int] = set()
    previous_high: set[int] = set()
    start = dates.searchsorted(pd.Timestamp(settings.backtest_start))
    stop = len(dates) - settings.hold_days - 1
    for index in range(start, stop, settings.hold_days):
        entry_price = arrays["open"][index + 1]
        exit_price = arrays["open_exit_mark"][index + settings.hold_days + 1]
        entry_tradable = np.isfinite(entry_price) & (entry_price > 0)
        exit_valuable = np.isfinite(exit_price) & (exit_price > 0)
        period_return = np.zeros(entry_price.shape, dtype=np.float64)
        valued = entry_tradable & exit_valuable
        period_return[valued] = exit_price[valued] / entry_price[valued] - 1.0
        mask = eligible[index] & entry_tradable & np.isfinite(factor[index])
        indices = np.flatnonzero(mask)
        if len(indices) < settings.min_stocks:
            continue
        order = indices[np.argsort(factor[index, indices], kind="stable")]
        count = max(len(order) // settings.groups, 1)
        low = set(order[:count].tolist())
        high = set(order[-count:].tolist())
        low_return = float(np.mean(period_return[np.fromiter(low, dtype=int)]))
        high_return = float(np.mean(period_return[np.fromiter(high, dtype=int)]))
        benchmark = float(np.mean(period_return[indices]))
        low_cost, low_turnover = transition_cost(previous_low, low, settings)
        high_cost, high_turnover = transition_cost(previous_high, high, settings)
        rows.append(
            {
                "factor": factor_name,
                "date": dates[index],
                "entry_date": dates[index + 1],
                "exit_date": dates[index + settings.hold_days + 1],
                "low_raw_return": low_return,
                "low_net_return": low_return - low_cost,
                "low_turnover": low_turnover,
                "high_raw_return": high_return,
                "high_net_return": high_return - high_cost,
                "high_turnover": high_turnover,
                "benchmark_return": benchmark,
                "eligible_stocks": len(indices),
                "group_stocks": count,
                "exit_price_fallback_stocks": int(
                    np.sum(~np.isfinite(arrays["open"][index + settings.hold_days + 1, indices]))
                ),
                "exit_price_missing_stocks": int(np.sum(~exit_valuable[indices])),
            }
        )
        previous_low = low
        previous_high = high
    if rows:
        rows[-1]["low_net_return"] -= settings.sell_cost
        rows[-1]["high_net_return"] -= settings.sell_cost
        rows[-1]["low_turnover"] += 0.5
        rows[-1]["high_turnover"] += 0.5
    return pd.DataFrame(rows)


def return_metrics(returns: pd.Series, benchmark: pd.Series, hold_days: int) -> dict:
    values = returns.to_numpy(dtype=float)
    base = benchmark.to_numpy(dtype=float)
    valid = np.isfinite(values) & np.isfinite(base)
    values = values[valid]
    base = base[valid]
    if len(values) < 10:
        return {}
    periods_per_year = 252.0 / hold_days
    nav = np.r_[1.0, np.cumprod(1.0 + values)]
    base_nav = np.cumprod(1.0 + base)
    years = len(values) / periods_per_year
    annual = nav[-1] ** (1.0 / years) - 1.0
    base_annual = base_nav[-1] ** (1.0 / years) - 1.0
    volatility = values.std(ddof=1) * math.sqrt(periods_per_year)
    sharpe = values.mean() / values.std(ddof=1) * math.sqrt(periods_per_year)
    drawdown = nav / np.maximum.accumulate(nav) - 1.0
    excess = values - base
    return {
        "annual_return": annual,
        "benchmark_annual_return": base_annual,
        "annual_excess_return": annual - base_annual,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": drawdown.min(),
        "excess_ir": excess.mean() / excess.std(ddof=1) * math.sqrt(periods_per_year)
        if excess.std(ddof=1) > 0
        else np.nan,
        "win_rate_vs_benchmark": (values > base).mean(),
        "periods": len(values),
    }


ORIGINAL_FREQUENCY_PERIODS_PER_YEAR = {
    "monthly": 12.0,
    "weekly_tuesday": 52.0,
    "every_5_days": 252.0 / 5.0,
    "daily": 252.0 / 5.0,
}


def original_frequency_return_metrics(
    returns: pd.Series,
    benchmark: pd.Series,
    frequency: str,
    entry_dates: pd.Series | None = None,
    exit_dates: pd.Series | None = None,
) -> dict:
    values = returns.to_numpy(dtype=float)
    base = benchmark.to_numpy(dtype=float)
    valid = np.isfinite(values) & np.isfinite(base)
    values = values[valid]
    base = base[valid]
    if len(values) < 10:
        return {}
    if entry_dates is not None and exit_dates is not None:
        entries = pd.to_datetime(entry_dates).to_numpy()[valid]
        exits = pd.to_datetime(exit_dates).to_numpy()[valid]
        elapsed_days = (pd.Timestamp(exits.max()) - pd.Timestamp(entries.min())).days
    else:
        elapsed_days = 0
    years = elapsed_days / 365.25 if elapsed_days > 0 else 0.0
    if years <= 0:
        periods_per_year = ORIGINAL_FREQUENCY_PERIODS_PER_YEAR[frequency]
        years = len(values) / periods_per_year
    else:
        periods_per_year = len(values) / years
    nav = np.r_[1.0, np.cumprod(1.0 + values)]
    base_nav = np.cumprod(1.0 + base)
    annual = nav[-1] ** (1.0 / years) - 1.0
    base_annual = base_nav[-1] ** (1.0 / years) - 1.0
    volatility = values.std(ddof=1) * math.sqrt(periods_per_year)
    sharpe = values.mean() / values.std(ddof=1) * math.sqrt(periods_per_year)
    drawdown = nav / np.maximum.accumulate(nav) - 1.0
    excess = values - base
    return {
        "annual_return": annual,
        "benchmark_annual_return": base_annual,
        "annual_excess_return": annual - base_annual,
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": drawdown.min(),
        "excess_ir": excess.mean() / excess.std(ddof=1) * math.sqrt(periods_per_year)
        if excess.std(ddof=1) > 0
        else np.nan,
        "win_rate_vs_benchmark": (values > base).mean(),
        "periods": len(values),
        "periods_per_year": periods_per_year,
    }


def summarize_backtest(
    rebalance: pd.DataFrame,
    daily_summary: pd.DataFrame,
    split_date: str,
    hold_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    yearly = []
    split = pd.Timestamp(split_date)
    for factor_name, data in rebalance.groupby("factor"):
        row = daily_summary[
            (daily_summary["factor"] == factor_name)
            & (daily_summary["period"] == "2016_2020")
        ]
        if row.empty:
            continue
        in_sample_ic = float(row.iloc[0]["ic_mean"])
        direction = "low" if in_sample_ic < 0 else "high"
        return_column = f"{direction}_net_return"
        turnover_column = f"{direction}_turnover"
        rebalance.loc[data.index, "selected_direction"] = direction
        rebalance.loc[data.index, "selected_net_return"] = data[return_column]
        rebalance.loc[data.index, "selected_turnover"] = data[turnover_column]
        periods = {
            "full": np.ones(len(data), dtype=bool),
            "2016_2020": data["exit_date"] <= split,
            "2021_2025": data["entry_date"] > split,
        }
        for period, mask in periods.items():
            subset = data.loc[mask]
            metrics = return_metrics(subset[return_column], subset["benchmark_return"], hold_days)
            if not metrics:
                continue
            metrics.update(
                {
                    "factor": factor_name,
                    "period": period,
                    "direction": direction,
                    "in_sample_ic": in_sample_ic,
                    "avg_turnover": subset[turnover_column].mean(),
                    "avg_group_stocks": subset["group_stocks"].mean(),
                }
            )
            summaries.append(metrics)
        copy = data.copy()
        copy["year"] = copy["entry_date"].dt.year
        for year, subset in copy.groupby("year"):
            yearly.append(
                {
                    "factor": factor_name,
                    "year": int(year),
                    "direction": direction,
                    "strategy_return": (1.0 + subset[return_column]).prod() - 1.0,
                    "benchmark_return": (1.0 + subset["benchmark_return"]).prod() - 1.0,
                    "periods": len(subset),
                }
            )
    yearly_frame = pd.DataFrame(yearly)
    if not yearly_frame.empty:
        yearly_frame["excess_return"] = yearly_frame["strategy_return"] - yearly_frame["benchmark_return"]
    return pd.DataFrame(summaries), yearly_frame


def summarize_original_frequency_backtest(
    rebalance: pd.DataFrame,
    original_frequency_summary: pd.DataFrame,
    split_date: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """只用2016—2020年原频率IC确定每个因子的交易方向。"""
    summaries = []
    yearly = []
    if rebalance.empty or original_frequency_summary.empty:
        return pd.DataFrame(), pd.DataFrame()
    split = pd.Timestamp(split_date)
    for factor_name, data in rebalance.groupby("factor"):
        frequency = str(data.iloc[0]["signal_frequency"])
        row = original_frequency_summary[
            (original_frequency_summary["factor"] == factor_name)
            & (original_frequency_summary["evaluation_frequency"] == frequency)
            & (original_frequency_summary["period"] == "2016_2020")
        ]
        if row.empty:
            continue
        in_sample_ic = float(row.iloc[0]["ic_mean"])
        if not np.isfinite(in_sample_ic):
            continue
        direction = "low" if in_sample_ic < 0 else "high"
        return_column = f"{direction}_net_return"
        turnover_column = f"{direction}_turnover"
        rebalance.loc[data.index, "selected_direction"] = direction
        rebalance.loc[data.index, "selected_net_return"] = data[return_column]
        rebalance.loc[data.index, "selected_turnover"] = data[turnover_column]
        periods = {
            "full": np.ones(len(data), dtype=bool),
            "2016_2020": data["exit_date"] <= split,
            "2021_2025": data["entry_date"] > split,
        }
        for period, mask in periods.items():
            subset = data.loc[mask]
            metrics = original_frequency_return_metrics(
                subset[return_column],
                subset["benchmark_return"],
                frequency,
                subset["entry_date"],
                subset["exit_date"],
            )
            if not metrics:
                continue
            metrics.update(
                {
                    "factor": factor_name,
                    "signal_frequency": frequency,
                    "trade_frequency": "every_5_days" if frequency == "daily" else frequency,
                    "period": period,
                    "direction": direction,
                    "in_sample_ic": in_sample_ic,
                    "avg_turnover": subset[turnover_column].mean(),
                    "avg_group_stocks": subset["group_stocks"].mean(),
                }
            )
            summaries.append(metrics)
        copy = data.copy()
        copy["year"] = copy["entry_date"].dt.year
        for year, subset in copy.groupby("year"):
            yearly.append(
                {
                    "factor": factor_name,
                    "signal_frequency": frequency,
                    "trade_frequency": "every_5_days" if frequency == "daily" else frequency,
                    "year": int(year),
                    "direction": direction,
                    "in_sample_ic": in_sample_ic,
                    "strategy_return": (1.0 + subset[return_column]).prod() - 1.0,
                    "benchmark_return": (1.0 + subset["benchmark_return"]).prod() - 1.0,
                    "periods": len(subset),
                }
            )
    yearly_frame = pd.DataFrame(yearly)
    if not yearly_frame.empty:
        yearly_frame["excess_return"] = yearly_frame["strategy_return"] - yearly_frame["benchmark_return"]
    return pd.DataFrame(summaries), yearly_frame


def save_factor_values(
    factors: dict[str, np.ndarray],
    dates: pd.DatetimeIndex,
    stocks: list[str],
    output_dir: Path,
    directory_name: str = "factor_values",
) -> None:
    destination = output_dir / directory_name
    destination.mkdir(parents=True, exist_ok=True)
    for name, values in factors.items():
        rows, columns = np.nonzero(np.isfinite(values))
        frame = pd.DataFrame(
            {"date": dates[rows], "code": np.asarray(stocks, dtype=object)[columns], "value": values[rows, columns]}
        )
        path = destination / f"{name}.parquet"
        temporary = path.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), temporary, compression="zstd")
        os.replace(temporary, path)


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        row = {"factor": code, **meta}
        row["calculated"] = code in factors
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    settings = Settings(
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
    )
    started = time.time()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    print("[1/6] 读取每日轻量文件和历史指数成分...", flush=True)
    files = list_cache_files(settings.cache_dir)
    dates = read_calendar(settings.qlib_dir, settings.start, settings.end)
    panel = load_panel(files, settings.start, settings.end)
    stocks, date_map, stock_map = make_axes(panel, dates)
    membership_rows = read_membership_rows(settings.qlib_dir, settings.start, settings.end)
    membership, missing_members = build_membership(dates, stocks, membership_rows)
    atomic_write_csv(missing_members, settings.output_dir / "qlib_members_without_cache.csv")
    print(
        f"  {len(dates)}个交易日，{len(stocks)}只缓存股票；每日成分中位数"
        f"{int(np.median(membership.sum(axis=1)))}只",
        flush=True,
    )

    factors, arrays = build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        args.cgo_window,
        args.illiq_window,
        settings.min_stocks,
    )
    original_signals = build_original_signals(factors, dates, settings.backtest_start)
    del panel
    gc.collect()

    status = build_status_frame(factors)
    atomic_write_csv(status, settings.output_dir / "factor_status.csv")
    parameters = {
        "settings": {key: str(value) if isinstance(value, Path) else value for key, value in asdict(settings).items()},
        "cgo_window": args.cgo_window,
        "illiq_window": args.illiq_window,
        "factor_meta": FACTOR_META,
        "backtest_conventions": {
            "training_rule": "未来收益退出日不晚于2020-12-31",
            "later_period_rule": "入场日晚于2020-12-31",
            "daily_icir_annualizer": "按实际有效信号数/年再除以5后开平方，因为未来5日收益相互重叠",
            "daily_trade_frequency": "原文未给持有期，交易结果每5个交易日选一次并持有5日",
            "monthly_cost": "每月开盘买入、月末收盘退出，每期完整计买入0.03%和卖出0.13%",
            "missing_exit_price": "退出日无目标价格时使用此前最近收盘价估值；若仍无价格则该股票该期收益记0",
            "execution_scope": "退出缺价采用估值，不能视为长期停牌或退市股票一定可按该价格成交",
            "max_drawdown": "仅使用每个持有期末净值计算，属于调仓期末最大回撤",
            "amount_quality": "分钟成交额推算均价超出当分钟高低价1%的日期，不进入成交额相关因子窗口和成交额筛选窗口",
        },
        "created_at": pd.Timestamp.now().isoformat(),
    }
    atomic_write_json(parameters, settings.output_dir / "methodology.json")

    if args.save_factor_values:
        save_factor_values(factors, dates, stocks, settings.output_dir)
        save_factor_values(
            original_signals,
            dates,
            stocks,
            settings.output_dir,
            directory_name="original_signals",
        )
    if args.skip_backtest:
        print(f"仅生成因子，耗时 {time.time() - started:.1f} 秒", flush=True)
        return

    print("[4/6] 计算原频率IC和统一五日评价...", flush=True)
    eligible = build_eligible(arrays, membership, settings.min_price, settings.min_avg_amount)
    future_return = future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )
    daily_frames = []
    original_frequency_frames = []
    rebalance_frames = []
    original_rebalance_frames = []
    for position, (factor_name, factor) in enumerate(factors.items(), start=1):
        factor_started = time.time()
        daily = calculate_daily_ic(
            factor_name, factor, future_return, eligible, dates, settings
        )
        original_frequency = calculate_original_frequency_ic(
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
            run_five_day_backtest(
                factor_name, factor, future_return, eligible, arrays, dates, settings
            )
        )
        original_rebalance_frames.append(
            run_original_frequency_backtest(
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
            f"  {position}/{len(factors)} {factor_name}: 统一5日IC={mean_ic:+.4f}，"
            f"耗时{time.time() - factor_started:.1f}秒",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    original_frequency_ic = (
        pd.concat(original_frequency_frames, ignore_index=True)
        if original_frequency_frames
        else pd.DataFrame()
    )
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = pd.concat(rebalance_frames, ignore_index=True) if rebalance_frames else pd.DataFrame()
    original_rebalance = (
        pd.concat(original_rebalance_frames, ignore_index=True)
        if original_rebalance_frames
        else pd.DataFrame()
    )
    daily_summary = summarize_ic(daily_ic, "date", "unified_5d", settings.split_date)
    original_frequency_summary = summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        summarize_original_frequency_backtest(
            original_rebalance, original_frequency_summary, settings.split_date
        )
    )

    print("[5/6] 保存结果...", flush=True)
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
        atomic_write_csv(frame, settings.output_dir / name)

    print("[6/6] 完成", flush=True)
    print(f"总耗时 {time.time() - started:.1f} 秒", flush=True)


if __name__ == "__main__":
    main()
