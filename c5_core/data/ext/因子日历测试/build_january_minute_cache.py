# -*- coding: utf-8 -*-
"""把每股分钟文件汇总为一月因子所需的每日轻量文件。"""

from __future__ import annotations

import argparse
import math
import os
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data")
DEFAULT_OUTPUT_DIR = (
    BASE_DIR / "january_original_minute_results" / "cache" / "daily_by_stock"
)

QFQ_PRICE_COLUMNS = ["qfq_open", "qfq_high", "qfq_low", "qfq_close"]
PLAIN_PRICE_COLUMNS = ["open", "high", "low", "close"]
COMMON_INPUT_COLUMNS = [
    "datetime",
    "volume",
    "amount",
    "float_shares",
    "total_shares",
    "qfq_ratio",
    "source_id",
]
INPUT_COLUMNS = ["datetime", *QFQ_PRICE_COLUMNS, *COMMON_INPUT_COLUMNS[1:]]

OUTPUT_COLUMNS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "float_shares",
    "total_shares",
    "qfq_ratio",
    "vwap_qfq",
    "prev_close",
    "minute_count",
    "vwap_outside_range_1pct",
    "turnvol_daily",
    "rvjn_5m",
    "illiq_shortcut_5m_daily",
    "weighted_close_ratio_5m",
    "volume_share_kurt_10m",
    "realized_skew_1m",
    "realized_skew_5m",
    "last_30min_return",
    "price_resiliency_1m_daily",
    "volume_peak_price_position_daily",
    "gu_1m",
    "gd_1m",
    "ridge_gap_count",
    "ridge_gap_sum1",
    "ridge_gap_sum2",
    "ridge_gap_sum3",
    "peak_count",
    "rvjp_5m",
    "large_up_jump_5m",
    "golden_split_return_daily",
    "intraday_jumpiness_all_daily",
    "intraday_jumpiness_ex_open_close_daily",
    "realized_kurtosis_1m",
    "valley_ridge_vwap_ratio_daily",
    "peak_amount_daily",
    "ridge_amount_daily",
    "rbar_up17",
    "rbar_down17",
    "r_0931_1000",
    "r_1001_1030",
    "overnight_return",
    "amount_share_entropy_5m",
    "upside_vol_share_1m",
    "upside_vol_share_5m",
    "upside_vol_share_10m",
    "weighted_skew_5m",
    "morning_fog_daily",
    "burst_follow_amount_daily",
    "burst_amount_daily",
]

MORNING_MINUTES = np.arange(9 * 60 + 31, 11 * 60 + 31, dtype=np.int16)
AFTERNOON_MINUTES = np.arange(13 * 60 + 1, 15 * 60 + 1, dtype=np.int16)
TRADING_MINUTES = np.concatenate([MORNING_MINUTES, AFTERNOON_MINUTES])
MINUTE_TO_POSITION = {int(value): index for index, value in enumerate(TRADING_MINUTES)}
MINUTES_PER_DAY = 240
FIVE_MINUTE_BLOCKS = 48
TEN_MINUTE_BLOCKS = 24
VOLUME_HISTORY_DAYS = 20


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--codes-file", type=Path)
    return parser.parse_args(argv)


def normalize_code(value: str) -> str:
    return Path(value.strip().lstrip("\ufeff")).stem.lower()


def load_selected_codes(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        normalize_code(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    }


def list_input_files(data_dir: Path, selected_codes: set[str]) -> list[Path]:
    files = sorted(data_dir.glob("*.parquet"), key=lambda path: path.stem.lower())
    if selected_codes:
        files = [path for path in files if path.stem.lower() in selected_codes]
    return files


def read_one_stock(path: Path, start: str, end: str) -> pd.DataFrame:
    """只调用一次 Arrow 读取，并在读取时限制日期。"""
    lower = pd.Timestamp(start).normalize()
    upper = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)
    schema_names = set(pq.read_schema(path).names)
    missing_common = [name for name in COMMON_INPUT_COLUMNS if name not in schema_names]
    if missing_common:
        raise ValueError(f"分钟文件缺少字段：{missing_common}")
    if set(QFQ_PRICE_COLUMNS).issubset(schema_names):
        price_columns = QFQ_PRICE_COLUMNS
    elif set(PLAIN_PRICE_COLUMNS).issubset(schema_names):
        price_columns = PLAIN_PRICE_COLUMNS
    else:
        raise ValueError("分钟文件必须包含 qfq_open/qfq_high/qfq_low/qfq_close 或 open/high/low/close")
    read_columns = ["datetime", *price_columns, *COMMON_INPUT_COLUMNS[1:]]
    table = pq.read_table(
        path,
        columns=read_columns,
        filters=[
            ("datetime", ">=", lower.to_pydatetime()),
            ("datetime", "<", upper.to_pydatetime()),
        ],
        use_threads=False,
    )
    frame = table.to_pandas(use_threads=False)
    if price_columns == PLAIN_PRICE_COLUMNS:
        frame = frame.rename(columns=dict(zip(PLAIN_PRICE_COLUMNS, QFQ_PRICE_COLUMNS)))
    return frame.loc[:, INPUT_COLUMNS]


def _last_finite(values: np.ndarray) -> np.ndarray:
    valid = np.isfinite(values)
    reverse_position = np.argmax(valid[:, ::-1], axis=1)
    positions = values.shape[1] - 1 - reverse_position
    result = values[np.arange(len(values)), positions].astype(np.float64, copy=True)
    result[~valid.any(axis=1)] = np.nan
    return result


def _safe_row_max(values: np.ndarray) -> np.ndarray:
    replaced = np.where(np.isfinite(values), values, -np.inf)
    result = replaced.max(axis=1)
    result[result == -np.inf] = np.nan
    return result


def _safe_row_min(values: np.ndarray) -> np.ndarray:
    replaced = np.where(np.isfinite(values), values, np.inf)
    result = replaced.min(axis=1)
    result[result == np.inf] = np.nan
    return result


def keep_complete_days(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """仅留下两个交易时段内恰好包含全部240个分钟的日期。"""
    if frame.empty:
        return frame.copy(), pd.DatetimeIndex([])

    result = frame.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    datetimes = result["datetime"]
    exact_minute = datetimes.eq(datetimes.dt.floor("min"))
    minute_of_day = datetimes.dt.hour * 60 + datetimes.dt.minute
    result["_position"] = minute_of_day.map(MINUTE_TO_POSITION)
    result["_date"] = datetimes.dt.normalize()
    result = result[exact_minute & result["_position"].notna()].copy()
    if result.empty:
        return result, pd.DatetimeIndex([])

    result["_position"] = result["_position"].astype(np.int16)
    counts = result.groupby("_date", sort=True)["_position"].agg(["size", "nunique"])
    complete_dates = counts.index[
        (counts["size"] == MINUTES_PER_DAY)
        & (counts["nunique"] == MINUTES_PER_DAY)
    ]
    result = result[result["_date"].isin(complete_dates)]
    result = result.sort_values(["_date", "_position"], kind="stable").reset_index(drop=True)
    return result, pd.DatetimeIndex(complete_dates)


def previous_valid_closes(
    frame: pd.DataFrame, dates: pd.DatetimeIndex
) -> np.ndarray:
    """从完整日筛选前的数据中，为每个日期寻找最近一次有效收盘价。"""
    result = np.full(len(dates), np.nan, dtype=np.float64)
    if frame.empty or not len(dates):
        return result
    source = frame.loc[:, ["datetime", "qfq_close"]].copy()
    source["datetime"] = pd.to_datetime(source["datetime"], errors="coerce")
    source["qfq_close"] = pd.to_numeric(source["qfq_close"], errors="coerce")
    source = source[source["datetime"].notna() & np.isfinite(source["qfq_close"])]
    if source.empty:
        return result
    source["_date"] = source["datetime"].dt.normalize()
    source = source.sort_values("datetime", kind="stable")
    daily_last = source.groupby("_date", sort=True)["qfq_close"].last()
    history_dates = daily_last.index.to_numpy(dtype="datetime64[ns]")
    history_closes = daily_last.to_numpy(dtype=np.float64)
    target_dates = dates.to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(history_dates, target_dates, side="left") - 1
    valid = positions >= 0
    result[valid] = history_closes[positions[valid]]
    return result


def intraday_log_returns(close: np.ndarray, day_open: float) -> np.ndarray:
    """计算不含隔夜变化的日内对数收益率。"""
    close = np.asarray(close, dtype=np.float64)
    result = np.full(close.shape, np.nan, dtype=np.float64)
    if not len(close):
        return result
    previous = np.empty_like(close)
    previous[0] = day_open
    previous[1:] = close[:-1]
    valid = np.isfinite(close) & np.isfinite(previous) & (close > 0) & (previous > 0)
    result[valid] = np.log(close[valid] / previous[valid])
    return result


def intraday_log_return_matrix(close: np.ndarray, day_open: np.ndarray) -> np.ndarray:
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
    result[valid] = np.log(close[valid] / previous[valid])
    return result


def realized_skew(returns: np.ndarray) -> np.ndarray:
    """严格按卡片公式计算已实现偏度。"""
    returns = np.asarray(returns, dtype=np.float64)
    if returns.ndim == 1:
        returns = returns[None, :]
    all_valid = np.isfinite(returns).all(axis=1)
    means = np.mean(returns, axis=1)
    centered = returns - means[:, None]
    realized_variance = np.sum(centered * centered, axis=1)
    denominator = realized_variance**1.5
    result = np.full(len(returns), np.nan, dtype=np.float64)
    valid = all_valid & (denominator > 0)
    result[valid] = (
        math.sqrt(returns.shape[1])
        * np.sum(centered[valid] ** 3, axis=1)
        / denominator[valid]
    )
    return result


def rvjn_from_returns(returns: np.ndarray) -> float:
    """按5分钟负收益和三幂变差计算单日下行跳跃波动率。"""
    values = np.asarray(returns, dtype=np.float64).copy()
    if values.ndim != 1 or len(values) < 3 or not np.isfinite(values).all():
        return math.nan
    values[np.abs(values) > 0.15] = 0.0
    downside = np.sum(values[values < 0] ** 2)
    power = np.abs(values) ** (2.0 / 3.0)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0)
        * math.gamma(5.0 / 6.0)
        / math.sqrt(math.pi)
    )
    integrated_variance = (
        mu_two_thirds ** -3
        * np.sum(power[2:] * power[1:-1] * power[:-2])
    )
    return float(max(downside - 0.5 * integrated_variance, 0.0))


def classify_volume_bursts(burst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把孤立喷发分钟记为峰，把相邻喷发分钟记为岭。"""
    burst = np.asarray(burst, dtype=bool)
    if burst.shape[-1] != MINUTES_PER_DAY:
        raise ValueError("喷发标记必须正好包含240个交易分钟")
    original_shape = burst.shape
    rows = burst.reshape(-1, MINUTES_PER_DAY)
    previous = np.zeros_like(rows)
    following = np.zeros_like(rows)
    for left, right in ((0, 120), (120, 240)):
        previous[:, left + 1 : right] = rows[:, left : right - 1]
        following[:, left : right - 1] = rows[:, left + 1 : right]
    peak = rows & ~previous & ~following
    ridge = rows & (previous | following)
    return peak.reshape(original_shape), ridge.reshape(original_shape)


def historical_volume_bursts(
    volume: np.ndarray, history_days: int = VOLUME_HISTORY_DAYS
) -> np.ndarray:
    """每个阈值只使用当前日期之前的完整日期。"""
    volume = np.asarray(volume, dtype=np.float64)
    result = np.zeros(volume.shape, dtype=bool)
    for day in range(history_days, len(volume)):
        history = volume[day - history_days : day]
        valid = np.isfinite(history).all(axis=0) & np.isfinite(volume[day])
        threshold = np.mean(history, axis=0) + np.std(history, axis=0, ddof=0)
        result[day, valid] = volume[day, valid] > threshold[valid]
    return result


def ridge_gap_moments(ridge: np.ndarray) -> tuple[np.ndarray, ...]:
    ridge = np.asarray(ridge, dtype=bool)
    counts = np.zeros(len(ridge), dtype=np.float64)
    sum1 = np.zeros(len(ridge), dtype=np.float64)
    sum2 = np.zeros(len(ridge), dtype=np.float64)
    sum3 = np.zeros(len(ridge), dtype=np.float64)
    for day, flags in enumerate(ridge):
        positions = np.flatnonzero(flags) + 1
        gaps = np.diff(positions).astype(np.float64)
        counts[day] = len(gaps)
        if len(gaps):
            sum1[day] = np.sum(gaps)
            sum2[day] = np.sum(gaps**2)
            sum3[day] = np.sum(gaps**3)
    return counts, sum1, sum2, sum3


def _reshape_blocks(values: np.ndarray, width: int) -> np.ndarray:
    blocks_per_session = 120 // width
    return values.reshape(len(values), 2, blocks_per_session, width).reshape(
        len(values), 2 * blocks_per_session, width
    )


def _daily_time_centers(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    positions = np.arange(1, MINUTES_PER_DAY + 1, dtype=np.float64)
    positive = np.where(returns > 0, np.abs(returns), 0.0)
    negative = np.where(returns < 0, np.abs(returns), 0.0)
    positive_sum = np.sum(positive, axis=1)
    negative_sum = np.sum(negative, axis=1)
    gu = np.full(len(returns), np.nan, dtype=np.float64)
    gd = np.full(len(returns), np.nan, dtype=np.float64)
    np.divide(positive @ positions, positive_sum, out=gu, where=positive_sum > 0)
    np.divide(negative @ positions, negative_sum, out=gd, where=negative_sum > 0)
    return gu, gd


def _pearson_population_kurtosis(values: np.ndarray) -> np.ndarray:
    means = np.mean(values, axis=1)
    centered = values - means[:, None]
    second = np.mean(centered**2, axis=1)
    fourth = np.mean(centered**4, axis=1)
    result = np.full(len(values), np.nan, dtype=np.float64)
    np.divide(fourth, second**2, out=result, where=second > 0)
    return result


def _realized_kurtosis(returns: np.ndarray) -> np.ndarray:
    """按卡片公式计算单日高频已实现峰度。"""
    values = np.asarray(returns, dtype=np.float64)
    means = np.mean(values, axis=1)
    centered = values - means[:, None]
    second_sum = np.sum(centered**2, axis=1)
    fourth_sum = np.sum(centered**4, axis=1)
    result = np.full(len(values), np.nan, dtype=np.float64)
    valid = np.isfinite(values).all(axis=1) & (second_sum > 0)
    result[valid] = values.shape[1] * fourth_sum[valid] / second_sum[valid] ** 2
    return result


def _jump_variations(returns: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回上行跳跃、下行跳跃和大上行跳跃波动率。"""
    values = np.asarray(returns, dtype=np.float64).copy()
    all_valid = np.isfinite(values).all(axis=1)
    values[np.abs(values) > 0.15] = 0.0
    power = np.abs(values) ** (2.0 / 3.0)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0)
        * math.gamma(5.0 / 6.0)
        / math.sqrt(math.pi)
    )
    integrated_variance = mu_two_thirds ** -3 * np.sum(
        power[:, 2:] * power[:, 1:-1] * power[:, :-2], axis=1
    )
    positive = np.sum(np.where(values > 0, values * values, 0.0), axis=1)
    negative = np.sum(np.where(values < 0, values * values, 0.0), axis=1)
    rvjp = np.maximum(positive - 0.5 * integrated_variance, 0.0)
    rvjn = np.maximum(negative - 0.5 * integrated_variance, 0.0)
    gamma = 4.0 * (1.0 / values.shape[1]) ** 0.49 * np.sqrt(
        np.maximum(integrated_variance, 0.0)
    )
    large_positive = np.sum(
        np.where(values > gamma[:, None], values * values, 0.0), axis=1
    )
    large_up = np.minimum(rvjp, large_positive)
    for result in (rvjp, rvjn, large_up):
        result[~all_valid] = np.nan
    return rvjp, rvjn, large_up


def _upside_volatility_share(returns: np.ndarray) -> np.ndarray:
    values = np.asarray(returns, dtype=np.float64)
    total = np.sum(values * values, axis=1)
    upside = np.sum(np.where(values > 0, values * values, 0.0), axis=1)
    result = np.full(len(values), np.nan, dtype=np.float64)
    valid = np.isfinite(values).all(axis=1) & (total > 0)
    np.divide(upside, total, out=result, where=valid)
    return result


def _weighted_skew(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    means = np.mean(close, axis=1)
    centered = close - means[:, None]
    standard = np.std(close, axis=1, ddof=0)
    total_volume = np.sum(volume, axis=1)
    weights = np.zeros_like(volume, dtype=np.float64)
    np.divide(
        volume,
        total_volume[:, None],
        out=weights,
        where=total_volume[:, None] > 0,
    )
    numerator = np.sum(weights * centered**3, axis=1)
    result = np.full(len(close), np.nan, dtype=np.float64)
    valid = (
        np.isfinite(close).all(axis=1)
        & np.isfinite(volume).all(axis=1)
        & (standard > 0)
        & (total_volume > 0)
    )
    result[valid] = numerator[valid] / standard[valid] ** 3
    return result


def _morning_fog_tvalue_dispersion(
    returns: np.ndarray, volume: np.ndarray, chunk_size: int = 256
) -> np.ndarray:
    """逐日回归1分钟收益与当期及五阶滞后成交量，取五个滞后项t值标准差。"""
    values = np.asarray(returns, dtype=np.float64)
    minute_volume = np.asarray(volume, dtype=np.float64)
    result = np.full(len(values), np.nan, dtype=np.float64)
    for left in range(0, len(values), chunk_size):
        right = min(left + chunk_size, len(values))
        y = values[left:right, 5:]
        v = minute_volume[left:right]
        scale = np.mean(v, axis=1)
        scaled = np.full_like(v, np.nan, dtype=np.float64)
        np.divide(v, scale[:, None], out=scaled, where=scale[:, None] > 0)
        lagged = np.stack(
            [scaled[:, 5 - lag : MINUTES_PER_DAY - lag] for lag in range(6)],
            axis=2,
        )
        x = np.concatenate(
            [np.ones((*lagged.shape[:2], 1), dtype=np.float64), lagged], axis=2
        )
        valid = np.isfinite(y).all(axis=1) & np.isfinite(x).all(axis=(1, 2))
        if not valid.any():
            continue
        xv = x[valid]
        yv = y[valid]
        xtx = np.einsum("dni,dnj->dij", xv, xv, optimize=True)
        inverse = np.linalg.pinv(xtx, hermitian=True)
        xty = np.einsum("dni,dn->di", xv, yv, optimize=True)
        beta = np.einsum("dij,dj->di", inverse, xty, optimize=True)
        residual = yv - np.einsum("dni,di->dn", xv, beta, optimize=True)
        degrees = yv.shape[1] - xv.shape[2]
        sigma2 = np.sum(residual * residual, axis=1) / degrees
        standard_error = np.sqrt(
            np.maximum(np.diagonal(inverse, axis1=1, axis2=2) * sigma2[:, None], 0.0)
        )
        t_values = np.full_like(beta, np.nan)
        np.divide(beta, standard_error, out=t_values, where=standard_error > 0)
        local = np.full(right - left, np.nan, dtype=np.float64)
        local[np.flatnonzero(valid)] = np.std(t_values[:, 2:7], axis=1, ddof=0)
        result[left:right] = local
    return result


def _top_return_means(returns: np.ndarray, count: int = 17) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(returns, dtype=np.float64)
    positive = np.where(values > 0, values, -np.inf)
    negative = np.where(values < 0, -values, -np.inf)
    positive_top = np.partition(positive, -count, axis=1)[:, -count:]
    negative_top = np.partition(negative, -count, axis=1)[:, -count:]
    up = np.mean(positive_top, axis=1)
    down = np.mean(negative_top, axis=1)
    up[~np.isfinite(positive_top).all(axis=1)] = np.nan
    down[~np.isfinite(negative_top).all(axis=1)] = np.nan
    return up, down


def build_daily_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    complete, dates = keep_complete_days(frame)
    if complete.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    prev_close = previous_valid_closes(frame, dates)

    numeric_columns = [column for column in INPUT_COLUMNS if column != "datetime"]
    for column in numeric_columns:
        complete[column] = pd.to_numeric(complete[column], errors="coerce")

    day_count = len(dates)

    def matrix(column: str) -> np.ndarray:
        return complete[column].to_numpy(dtype=np.float64).reshape(day_count, MINUTES_PER_DAY)

    qfq_open = matrix("qfq_open")
    qfq_high = matrix("qfq_high")
    qfq_low = matrix("qfq_low")
    qfq_close = matrix("qfq_close")
    volume = matrix("volume")
    amount = matrix("amount")
    float_shares_minute = matrix("float_shares")
    total_shares_minute = matrix("total_shares")
    ratio = matrix("qfq_ratio")

    raw_open = np.full(qfq_open.shape, np.nan, dtype=np.float64)
    raw_high = np.full(qfq_high.shape, np.nan, dtype=np.float64)
    raw_low = np.full(qfq_low.shape, np.nan, dtype=np.float64)
    raw_close = np.full(qfq_close.shape, np.nan, dtype=np.float64)
    valid_ratio = np.isfinite(ratio) & (ratio != 0)
    np.divide(qfq_open, ratio, out=raw_open, where=valid_ratio)
    np.divide(qfq_high, ratio, out=raw_high, where=valid_ratio)
    np.divide(qfq_low, ratio, out=raw_low, where=valid_ratio)
    np.divide(qfq_close, ratio, out=raw_close, where=valid_ratio)

    daily_open = qfq_open[:, 0]
    daily_high = _safe_row_max(qfq_high)
    daily_low = _safe_row_min(qfq_low)
    daily_close = qfq_close[:, -1]
    daily_volume = np.sum(volume, axis=1)
    daily_amount = np.sum(amount, axis=1)
    daily_float_shares = _last_finite(float_shares_minute)
    daily_total_shares = _last_finite(total_shares_minute)
    daily_ratio = _last_finite(ratio)

    vwap_numerator = np.sum(amount * ratio, axis=1)
    vwap_qfq = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(vwap_numerator, daily_volume, out=vwap_qfq, where=daily_volume > 0)
    minute_vwap_qfq = np.full(amount.shape, np.nan, dtype=np.float64)
    np.divide(
        amount * ratio,
        volume,
        out=minute_vwap_qfq,
        where=np.isfinite(volume) & (volume > 0),
    )
    minute_vwap_bad = (
        (
            (minute_vwap_qfq < qfq_low * 0.99)
            | (minute_vwap_qfq > qfq_high * 1.01)
        )
        & np.isfinite(minute_vwap_qfq)
        & np.isfinite(qfq_low)
        & np.isfinite(qfq_high)
    )
    vwap_outside = minute_vwap_bad.any(axis=1)

    minute_turnover = np.full(volume.shape, np.nan, dtype=np.float64)
    np.divide(
        volume,
        daily_float_shares[:, None],
        out=minute_turnover,
        where=np.isfinite(daily_float_shares[:, None]) & (daily_float_shares[:, None] > 0),
    )
    turnvol_daily = np.std(minute_turnover, axis=1, ddof=0)

    five_qfq_close = _reshape_blocks(qfq_close, 5)[:, :, -1]
    five_raw_open = _reshape_blocks(raw_open, 5)[:, :, 0]
    five_raw_high = np.max(_reshape_blocks(raw_high, 5), axis=2)
    five_raw_low = np.min(_reshape_blocks(raw_low, 5), axis=2)
    five_raw_close = _reshape_blocks(raw_close, 5)[:, :, -1]
    five_volume = np.sum(_reshape_blocks(volume, 5), axis=2)
    five_amount = np.sum(_reshape_blocks(amount, 5), axis=2)
    five_returns = intraday_log_return_matrix(five_qfq_close, daily_open)

    rvjp_5m, rvjn_5m, large_up_jump_5m = _jump_variations(five_returns)

    shortcut = 2.0 * (five_raw_high - five_raw_low) - np.abs(
        five_raw_close - five_raw_open
    )
    shortcut_div_amount = np.zeros(shortcut.shape, dtype=np.float64)
    np.divide(shortcut, five_amount, out=shortcut_div_amount, where=five_amount > 0)
    illiq_daily = np.sum(shortcut_div_amount, axis=1)
    illiq_daily[daily_amount <= 0] = np.nan

    five_volume_share = np.full(five_volume.shape, np.nan, dtype=np.float64)
    np.divide(
        five_volume,
        daily_volume[:, None],
        out=five_volume_share,
        where=daily_volume[:, None] > 0,
    )
    weighted_five_close = np.sum(five_volume_share * five_qfq_close, axis=1)
    mean_five_close = np.mean(five_qfq_close, axis=1)
    weighted_close_ratio = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        weighted_five_close,
        mean_five_close,
        out=weighted_close_ratio,
        where=mean_five_close != 0,
    )

    ten_volume = np.sum(_reshape_blocks(volume, 10), axis=2)
    ten_qfq_close = _reshape_blocks(qfq_close, 10)[:, :, -1]
    ten_returns = intraday_log_return_matrix(ten_qfq_close, daily_open)
    ten_volume_share = np.full(ten_volume.shape, np.nan, dtype=np.float64)
    np.divide(
        ten_volume,
        daily_volume[:, None],
        out=ten_volume_share,
        where=daily_volume[:, None] > 0,
    )
    volume_share_kurt = _pearson_population_kurtosis(ten_volume_share)

    one_minute_returns = intraday_log_return_matrix(qfq_close, daily_open)
    realized_skew_1m = realized_skew(one_minute_returns)
    realized_skew_5m = realized_skew(five_returns)
    realized_kurtosis_1m = _realized_kurtosis(one_minute_returns)

    simple_returns = np.expm1(one_minute_returns)
    minute_jumpiness = 2.0 * (simple_returns - one_minute_returns) - one_minute_returns**2
    intraday_jumpiness_all = np.mean(minute_jumpiness, axis=1)
    intraday_jumpiness_ex_open_close = np.mean(minute_jumpiness[:, 1:-1], axis=1)

    position_1000 = MINUTE_TO_POSITION[10 * 60]
    position_1030 = MINUTE_TO_POSITION[10 * 60 + 30]
    golden_split_return = np.full(day_count, np.nan, dtype=np.float64)
    valid_golden = (
        np.isfinite(daily_close)
        & np.isfinite(qfq_close[:, position_1000])
        & (daily_close > 0)
        & (qfq_close[:, position_1000] > 0)
    )
    golden_split_return[valid_golden] = np.log(
        daily_close[valid_golden] / qfq_close[valid_golden, position_1000]
    )

    r_0931_1000 = np.full(day_count, np.nan, dtype=np.float64)
    r_1001_1030 = np.full(day_count, np.nan, dtype=np.float64)
    overnight_return = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        qfq_close[:, position_1000],
        daily_open,
        out=r_0931_1000,
        where=np.isfinite(daily_open) & (daily_open != 0),
    )
    r_0931_1000 -= 1.0
    np.divide(
        qfq_close[:, position_1030],
        qfq_close[:, position_1000],
        out=r_1001_1030,
        where=np.isfinite(qfq_close[:, position_1000]) & (qfq_close[:, position_1000] != 0),
    )
    r_1001_1030 -= 1.0
    np.divide(
        daily_open,
        prev_close,
        out=overnight_return,
        where=np.isfinite(prev_close) & (prev_close != 0),
    )
    overnight_return -= 1.0
    rbar_up17, rbar_down17 = _top_return_means(one_minute_returns, 17)

    five_amount_share = np.zeros_like(five_amount, dtype=np.float64)
    np.divide(
        five_amount,
        daily_amount[:, None],
        out=five_amount_share,
        where=daily_amount[:, None] > 0,
    )
    entropy_terms = np.zeros_like(five_amount_share, dtype=np.float64)
    positive_share = five_amount_share > 0
    entropy_terms[positive_share] = (
        -five_amount_share[positive_share] * np.log(five_amount_share[positive_share])
    )
    amount_share_entropy_5m = np.sum(entropy_terms, axis=1)
    amount_share_entropy_5m[daily_amount <= 0] = np.nan

    upside_vol_share_1m = _upside_volatility_share(one_minute_returns)
    upside_vol_share_5m = _upside_volatility_share(five_returns)
    upside_vol_share_10m = _upside_volatility_share(ten_returns)
    weighted_skew_5m = _weighted_skew(five_qfq_close, five_volume)
    morning_fog_daily = _morning_fog_tvalue_dispersion(one_minute_returns, volume)

    position_1430 = MINUTE_TO_POSITION[14 * 60 + 30]
    last_30min_return = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        qfq_close[:, -1],
        qfq_close[:, position_1430],
        out=last_30min_return,
        where=qfq_close[:, position_1430] != 0,
    )
    last_30min_return -= 1.0

    cumulative_high = np.fmax.accumulate(raw_high, axis=1)
    cumulative_low = np.fmin.accumulate(raw_low, axis=1)
    cumulative_amount = np.cumsum(amount, axis=1)
    minute_resiliency = np.full(raw_high.shape, np.nan, dtype=np.float64)
    np.divide(
        cumulative_high - cumulative_low,
        cumulative_amount,
        out=minute_resiliency,
        where=cumulative_amount > 0,
    )
    valid_resiliency_count = np.isfinite(minute_resiliency).sum(axis=1)
    price_resiliency = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        np.nansum(minute_resiliency, axis=1),
        valid_resiliency_count,
        out=price_resiliency,
        where=valid_resiliency_count > 0,
    )

    bursts = historical_volume_bursts(volume)
    peaks, ridges = classify_volume_bursts(bursts)
    peak_count = np.sum(peaks, axis=1).astype(np.int32)
    peak_amount_daily = np.sum(np.where(peaks, amount, 0.0), axis=1)
    ridge_amount_daily = np.sum(np.where(ridges, amount, 0.0), axis=1)
    peak_volume = np.sum(np.where(peaks, volume, 0.0), axis=1)
    peak_price_numerator = np.sum(np.where(peaks, amount * ratio, 0.0), axis=1)
    peak_price = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        peak_price_numerator,
        peak_volume,
        out=peak_price,
        where=peak_volume > 0,
    )
    price_lower = np.minimum(daily_low, prev_close)
    price_upper = np.maximum(daily_high, prev_close)
    peak_price_position = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        peak_price - price_lower,
        price_upper - price_lower,
        out=peak_price_position,
        where=(price_upper > price_lower) & np.isfinite(peak_price),
    )

    valleys = ~bursts
    valley_volume = np.sum(np.where(valleys, volume, 0.0), axis=1)
    ridge_volume = np.sum(np.where(ridges, volume, 0.0), axis=1)
    valley_price_numerator = np.sum(np.where(valleys, amount * ratio, 0.0), axis=1)
    ridge_price_numerator = np.sum(np.where(ridges, amount * ratio, 0.0), axis=1)
    valley_price = np.full(day_count, np.nan, dtype=np.float64)
    ridge_price = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        valley_price_numerator,
        valley_volume,
        out=valley_price,
        where=valley_volume > 0,
    )
    np.divide(
        ridge_price_numerator,
        ridge_volume,
        out=ridge_price,
        where=ridge_volume > 0,
    )
    valley_ridge_vwap_ratio = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(
        valley_price,
        ridge_price,
        out=valley_ridge_vwap_ratio,
        where=np.isfinite(ridge_price) & (ridge_price != 0),
    )

    next_amount = np.full_like(amount, np.nan, dtype=np.float64)
    next_amount[:, :-1] = amount[:, 1:]
    burst_with_follow = bursts.copy()
    burst_with_follow[:, -1] = False
    burst_follow_amount_daily = np.sum(
        np.where(burst_with_follow, next_amount, 0.0), axis=1
    )
    burst_amount_daily = np.sum(np.where(burst_with_follow, amount, 0.0), axis=1)

    gu_1m, gd_1m = _daily_time_centers(one_minute_returns)
    ridge_count, ridge_sum1, ridge_sum2, ridge_sum3 = ridge_gap_moments(ridges)
    unavailable_days = min(VOLUME_HISTORY_DAYS, day_count)
    for values in (ridge_count, ridge_sum1, ridge_sum2, ridge_sum3):
        values[:unavailable_days] = np.nan
    for values in (
        valley_ridge_vwap_ratio,
        peak_amount_daily,
        ridge_amount_daily,
        burst_follow_amount_daily,
        burst_amount_daily,
    ):
        values[:unavailable_days] = np.nan

    result = pd.DataFrame(
        {
            "date": dates.to_numpy(dtype="datetime64[ns]"),
            "code": code.lower(),
            "open": daily_open,
            "high": daily_high,
            "low": daily_low,
            "close": daily_close,
            "volume": daily_volume,
            "amount": daily_amount,
            "float_shares": daily_float_shares,
            "total_shares": daily_total_shares,
            "qfq_ratio": daily_ratio,
            "vwap_qfq": vwap_qfq,
            "prev_close": prev_close,
            "minute_count": np.full(day_count, MINUTES_PER_DAY, dtype=np.int16),
            "vwap_outside_range_1pct": vwap_outside,
            "turnvol_daily": turnvol_daily,
            "rvjn_5m": rvjn_5m,
            "illiq_shortcut_5m_daily": illiq_daily,
            "weighted_close_ratio_5m": weighted_close_ratio,
            "volume_share_kurt_10m": volume_share_kurt,
            "realized_skew_1m": realized_skew_1m,
            "realized_skew_5m": realized_skew_5m,
            "last_30min_return": last_30min_return,
            "price_resiliency_1m_daily": price_resiliency,
            "volume_peak_price_position_daily": peak_price_position,
            "gu_1m": gu_1m,
            "gd_1m": gd_1m,
            "ridge_gap_count": ridge_count,
            "ridge_gap_sum1": ridge_sum1,
            "ridge_gap_sum2": ridge_sum2,
            "ridge_gap_sum3": ridge_sum3,
            "peak_count": peak_count,
            "rvjp_5m": rvjp_5m,
            "large_up_jump_5m": large_up_jump_5m,
            "golden_split_return_daily": golden_split_return,
            "intraday_jumpiness_all_daily": intraday_jumpiness_all,
            "intraday_jumpiness_ex_open_close_daily": intraday_jumpiness_ex_open_close,
            "realized_kurtosis_1m": realized_kurtosis_1m,
            "valley_ridge_vwap_ratio_daily": valley_ridge_vwap_ratio,
            "peak_amount_daily": peak_amount_daily,
            "ridge_amount_daily": ridge_amount_daily,
            "rbar_up17": rbar_up17,
            "rbar_down17": rbar_down17,
            "r_0931_1000": r_0931_1000,
            "r_1001_1030": r_1001_1030,
            "overnight_return": overnight_return,
            "amount_share_entropy_5m": amount_share_entropy_5m,
            "upside_vol_share_1m": upside_vol_share_1m,
            "upside_vol_share_5m": upside_vol_share_5m,
            "upside_vol_share_10m": upside_vol_share_10m,
            "weighted_skew_5m": weighted_skew_5m,
            "morning_fog_daily": morning_fog_daily,
            "burst_follow_amount_daily": burst_follow_amount_daily,
            "burst_amount_daily": burst_amount_daily,
        },
        columns=OUTPUT_COLUMNS,
    )
    return result


def atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """先写同目录临时文件，写完后一次替换正式文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            use_dictionary=["code"] if "code" in frame.columns else False,
            write_statistics=True,
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def process_one_stock(
    input_path: Path, output_dir: Path, start: str, end: str
) -> dict[str, object]:
    code = input_path.stem.lower()
    output_path = output_dir / f"{code}.parquet"
    if output_path.exists():
        return {"code": code, "status": "skipped", "days": None}
    frame = read_one_stock(input_path, start, end)
    daily = build_daily_frame(frame, code)
    atomic_write_parquet(daily, output_path)
    return {"code": code, "status": "ok", "days": len(daily)}


def _safe_process(task: tuple[str, str, str, str]) -> dict[str, object]:
    input_path, output_dir, start, end = task
    try:
        return process_one_stock(Path(input_path), Path(output_dir), start, end)
    except Exception as exc:
        return {
            "code": Path(input_path).stem.lower(),
            "status": "error",
            "days": None,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("--workers 必须至少为1")
    if args.limit is not None and args.limit < 0:
        raise ValueError("--limit 不能小于0")
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    if end < start:
        raise ValueError("--end 不能早于 --start")

    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"分钟数据目录不存在：{data_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = load_selected_codes(args.codes_file)
    files = list_input_files(data_dir, selected)
    if args.limit is not None:
        files = files[: args.limit]
    tasks = [
        (str(path), str(output_dir), start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        for path in files
        if not (output_dir / f"{path.stem.lower()}.parquet").exists()
    ]
    skipped = len(files) - len(tasks)
    print(f"选中 {len(files)} 只股票，已有结果跳过 {skipped} 只，待处理 {len(tasks)} 只。")
    if not tasks:
        return 0

    errors = 0
    completed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for item in executor.map(_safe_process, tasks, chunksize=1):
            completed += 1
            if item["status"] == "error":
                errors += 1
                print(f"[{completed}/{len(tasks)}] {item['code']} 失败：{item['error']}")
            else:
                print(
                    f"[{completed}/{len(tasks)}] {item['code']} 完成，"
                    f"保留 {item['days']} 个完整日。"
                )
    if errors:
        raise RuntimeError(f"有 {errors} 只股票处理失败，请查看上面的错误信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
