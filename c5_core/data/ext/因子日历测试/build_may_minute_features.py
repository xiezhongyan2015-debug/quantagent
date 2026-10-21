# -*- coding: utf-8 -*-
"""从每股分钟文件生成五月量价因子所需的每日数据。"""

from __future__ import annotations

import argparse
import math
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import build_january_minute_cache as january


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = january.DEFAULT_DATA_DIR
DEFAULT_OUTPUT_DIR = BASE_DIR / "may_factor_results" / "cache" / "may_daily_by_stock"

INPUT_COLUMNS = january.INPUT_COLUMNS
MINUTES_PER_DAY = january.MINUTES_PER_DAY
HISTORY_DAYS = 20
CURRENT_FEATURE_VERSION = 2

OUTPUT_COLUMNS = [
    "date",
    "code",
    "may_feature_version",
    "b_volume_distribution_gap",
    "large_jump_asymmetry_5m",
    "single_amount_share_entropy_5m",
    "realized_volatility_5m",
    "night_sleep_t_intercept",
    "same_time_peak_ridge_corr_20d",
]


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


def _large_jump_asymmetry(
    returns: np.ndarray, alpha: float = 4.0
) -> np.ndarray:
    """按前面跳跃卡片的定义计算大上行减大下行跳跃波动。"""
    values = np.asarray(returns, dtype=np.float64).copy()
    if values.ndim == 1:
        values = values[None, :]
    if values.ndim != 2 or values.shape[1] < 3:
        raise ValueError("收益数据必须是二维，且每日至少有3个时点")
    if alpha <= 0:
        raise ValueError("alpha 必须大于0")

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
    positive_variance = np.sum(
        np.where(values > 0, values * values, 0.0), axis=1
    )
    negative_variance = np.sum(
        np.where(values < 0, values * values, 0.0), axis=1
    )
    rvjp = np.maximum(positive_variance - 0.5 * integrated_variance, 0.0)
    rvjn = np.maximum(negative_variance - 0.5 * integrated_variance, 0.0)
    threshold = alpha * (1.0 / values.shape[1]) ** 0.49 * np.sqrt(
        np.maximum(integrated_variance, 0.0)
    )
    large_positive = np.sum(
        np.where(values > threshold[:, None], values * values, 0.0), axis=1
    )
    large_negative = np.sum(
        np.where(values < -threshold[:, None], values * values, 0.0), axis=1
    )
    result = np.minimum(rvjp, large_positive) - np.minimum(rvjn, large_negative)
    result[~all_valid] = np.nan
    return result


def _b_volume_distribution_gap(
    close: np.ndarray, volume: np.ndarray, daily_low: np.ndarray
) -> np.ndarray:
    """按价格距离优先、同距离成交量优先的顺序解释b型成交量分布。"""
    prices = np.asarray(close, dtype=np.float64)
    quantities = np.asarray(volume, dtype=np.float64)
    lows = np.asarray(daily_low, dtype=np.float64).reshape(-1)
    if prices.shape != quantities.shape or prices.ndim != 2:
        raise ValueError("价格和成交量必须是形状相同的二维数据")
    if len(lows) != len(prices):
        raise ValueError("每日最低价数量必须和交易日数量相同")

    result = np.full(len(prices), np.nan, dtype=np.float64)
    for day in range(len(prices)):
        valid = (
            np.isfinite(prices[day])
            & np.isfinite(quantities[day])
            & (quantities[day] >= 0)
        )
        if not np.isfinite(lows[day]) or valid.sum() == 0:
            continue
        day_prices = prices[day, valid]
        day_volume = quantities[day, valid]
        total_volume = float(day_volume.sum())
        if total_volume <= 0:
            continue
        unique_prices, inverse = np.unique(day_prices, return_inverse=True)
        price_volume = np.bincount(
            inverse, weights=day_volume, minlength=len(unique_prices)
        )
        # np.unique按价格升序，成交量相同时选择较低的VSP。
        vsp_index = int(np.argmax(price_volume))
        vsp = unique_prices[vsp_index]
        order = np.lexsort(
            (unique_prices, -price_volume, np.abs(unique_prices - vsp))
        )
        cumulative = 0.0
        selected = []
        for index in order:
            selected.append(int(index))
            cumulative += float(price_volume[index])
            if cumulative / total_volume > 0.5:
                break
        if selected:
            vsa_high = float(np.max(unique_prices[selected]))
            result[day] = vsa_high - lows[day]
    return result


def _single_amount_share_entropy(
    close: np.ndarray, volume: np.ndarray
) -> np.ndarray:
    """严格按卡片的两个占比乘积计算熵，不再次归一化。"""
    prices = np.asarray(close, dtype=np.float64)
    quantities = np.asarray(volume, dtype=np.float64)
    if prices.shape != quantities.shape or prices.ndim != 2:
        raise ValueError("价格和成交量必须是形状相同的二维数据")
    volume_sum = np.sum(quantities, axis=1)
    close_sum = np.sum(prices, axis=1)
    probability = np.full(prices.shape, np.nan, dtype=np.float64)
    valid_day = (
        np.isfinite(prices).all(axis=1)
        & np.isfinite(quantities).all(axis=1)
        & (volume_sum > 0)
        & (close_sum > 0)
    )
    probability[valid_day] = (
        quantities[valid_day] / volume_sum[valid_day, None]
    ) * (prices[valid_day] / close_sum[valid_day, None])
    terms = np.zeros_like(probability)
    positive = probability > 0
    terms[positive] = -probability[positive] * np.log(probability[positive])
    result = np.sum(terms, axis=1)
    result[~valid_day] = np.nan
    return result


def _realized_volatility(returns: np.ndarray) -> np.ndarray:
    """计算日内收益平方和。"""
    values = np.asarray(returns, dtype=np.float64)
    if values.ndim == 1:
        values = values[None, :]
    result = np.sum(values * values, axis=1)
    result[~np.isfinite(values).all(axis=1)] = np.nan
    return result


def _night_sleep_t_intercept(
    simple_returns: np.ndarray, volume: np.ndarray
) -> np.ndarray:
    """每日用第6至240分钟回归，并返回截距项的t值。"""
    returns = np.asarray(simple_returns, dtype=np.float64)
    minute_volume = np.asarray(volume, dtype=np.float64)
    if returns.shape != minute_volume.shape or returns.ndim != 2:
        raise ValueError("收益和成交量必须是形状相同的二维数据")
    if returns.shape[1] != MINUTES_PER_DAY:
        raise ValueError("每个交易日必须正好包含240个交易分钟")

    volume_difference = np.empty_like(minute_volume)
    volume_difference[:, 0] = minute_volume[:, 0]
    volume_difference[:, 1:] = np.diff(minute_volume, axis=1)
    regressors = np.stack(
        [
            volume_difference[:, 5 - lag : MINUTES_PER_DAY - lag]
            for lag in range(6)
        ],
        axis=2,
    )
    dependent = returns[:, 5:]
    result = np.full(len(returns), np.nan, dtype=np.float64)

    for day in range(len(returns)):
        x = regressors[day]
        y = dependent[day]
        valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
        x = x[valid]
        y = y[valid]
        parameter_count = 7
        if len(y) <= parameter_count:
            continue
        scales = np.std(x, axis=0, ddof=0)
        if not np.isfinite(scales).all() or np.any(scales <= 0):
            continue
        design = np.column_stack([np.ones(len(y)), x / scales])
        if np.linalg.matrix_rank(design) < parameter_count:
            continue
        beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ beta
        degrees_of_freedom = len(y) - parameter_count
        sigma2 = float(residual @ residual) / degrees_of_freedom
        inverse = np.linalg.inv(design.T @ design)
        intercept_variance = sigma2 * inverse[0, 0]
        if np.isfinite(intercept_variance) and intercept_variance > 0:
            result[day] = float(beta[0]) / math.sqrt(intercept_variance)
    return result


def _same_time_peak_ridge_correlation(
    volume: np.ndarray, history_days: int = HISTORY_DAYS
) -> np.ndarray:
    """计算过去20日、240个同时点的量峰数和量岭数相关系数。"""
    minute_volume = np.asarray(volume, dtype=np.float64)
    if minute_volume.ndim != 2 or minute_volume.shape[1] != MINUTES_PER_DAY:
        raise ValueError("成交量必须是每日240分钟的二维数据")
    if history_days < 2:
        raise ValueError("历史日数必须至少为2")

    result = np.full(len(minute_volume), np.nan, dtype=np.float64)
    if len(minute_volume) < history_days:
        return result

    # 每个窗口都只使用同一组最近20日：先在窗口内定同分钟标准，
    # 再对这20日识别量峰、量岭并统计240个同时点的样本数。
    windows = np.lib.stride_tricks.sliding_window_view(
        minute_volume, window_shape=history_days, axis=0
    )
    threshold = np.mean(windows, axis=2) + np.std(windows, axis=2, ddof=0)
    bursts = np.moveaxis(windows > threshold[:, :, None], 2, 1)
    previous = np.zeros_like(bursts)
    following = np.zeros_like(bursts)
    for left, right in ((0, 120), (120, 240)):
        previous[:, :, left + 1 : right] = bursts[:, :, left : right - 1]
        following[:, :, left : right - 1] = bursts[:, :, left + 1 : right]
    peaks = bursts & ~previous & ~following
    ridges = bursts & (previous | following)
    peak_count = np.sum(peaks, axis=1, dtype=np.float64)
    ridge_count = np.sum(ridges, axis=1, dtype=np.float64)
    peak_centered = peak_count - peak_count.mean(axis=1, keepdims=True)
    ridge_centered = ridge_count - ridge_count.mean(axis=1, keepdims=True)
    numerator = np.sum(peak_centered * ridge_centered, axis=1)
    denominator = np.sqrt(
        np.sum(peak_centered * peak_centered, axis=1)
        * np.sum(ridge_centered * ridge_centered, axis=1)
    )
    correlations = np.full(len(windows), np.nan, dtype=np.float64)
    valid = np.isfinite(denominator) & (denominator > 0)
    np.divide(numerator, denominator, out=correlations, where=valid)
    result[history_days - 1 :] = correlations
    return result


def build_daily_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """把一只股票的分钟数据整理成每日五月因子输入。"""
    complete, dates = january.keep_complete_days(frame)
    if complete.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    for column in INPUT_COLUMNS:
        if column != "datetime":
            complete[column] = pd.to_numeric(complete[column], errors="coerce")
    day_count = len(dates)

    def matrix(column: str) -> np.ndarray:
        return complete[column].to_numpy(dtype=np.float64).reshape(
            day_count, MINUTES_PER_DAY
        )

    qfq_open = matrix("qfq_open")
    qfq_low = matrix("qfq_low")
    qfq_close = matrix("qfq_close")
    volume = matrix("volume")
    five_close = january._reshape_blocks(qfq_close, 5)[:, :, -1]
    five_volume = np.sum(january._reshape_blocks(volume, 5), axis=2)
    five_returns = january.intraday_log_return_matrix(five_close, qfq_open[:, 0])

    simple_returns = np.full(qfq_close.shape, np.nan, dtype=np.float64)
    previous_close = qfq_close[:, :-1]
    np.divide(
        qfq_close[:, 1:],
        previous_close,
        out=simple_returns[:, 1:],
        where=np.isfinite(previous_close) & (previous_close != 0),
    )
    simple_returns[:, 1:] -= 1.0

    return pd.DataFrame(
        {
            "date": dates.to_numpy(dtype="datetime64[ns]"),
            "code": code.lower(),
            "may_feature_version": np.full(
                day_count, CURRENT_FEATURE_VERSION, dtype=np.int8
            ),
            "b_volume_distribution_gap": _b_volume_distribution_gap(
                qfq_close, volume, np.min(qfq_low, axis=1)
            ),
            "large_jump_asymmetry_5m": _large_jump_asymmetry(
                five_returns, alpha=4.0
            ),
            "single_amount_share_entropy_5m": _single_amount_share_entropy(
                five_close, five_volume
            ),
            "realized_volatility_5m": _realized_volatility(five_returns),
            "night_sleep_t_intercept": _night_sleep_t_intercept(
                simple_returns, volume
            ),
            "same_time_peak_ridge_corr_20d": _same_time_peak_ridge_correlation(
                volume, HISTORY_DAYS
            ),
        },
        columns=OUTPUT_COLUMNS,
    )


def output_is_current(path: Path) -> bool:
    """已有结果只有在字段完整且计算版本一致时才跳过。"""
    if not path.exists():
        return False
    try:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != OUTPUT_COLUMNS or parquet.metadata.num_rows == 0:
            return False
        version = parquet.read_row_group(
            0, columns=["may_feature_version"]
        ).column(0)[0].as_py()
        return version == CURRENT_FEATURE_VERSION
    except Exception:
        return False


def process_one_stock(
    input_path: Path, output_dir: Path, start: str, end: str
) -> dict[str, object]:
    code = input_path.stem.lower()
    output_path = output_dir / f"{code}.parquet"
    if output_is_current(output_path):
        return {"code": code, "status": "skipped", "days": None}
    frame = january.read_one_stock(input_path, start, end)
    daily = build_daily_frame(frame, code)
    january.atomic_write_parquet(daily, output_path)
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

    selected_codes = january.load_selected_codes(args.codes_file)
    files = january.list_input_files(data_dir, selected_codes)
    if args.limit is not None:
        files = files[: args.limit]
    tasks = [
        (
            str(path),
            str(output_dir),
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
        for path in files
        if not output_is_current(output_dir / f"{path.stem.lower()}.parquet")
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
            elif completed % 50 == 0 or completed == len(tasks):
                print(f"[{completed}/{len(tasks)}] 已完成")
    if errors:
        raise RuntimeError(f"有 {errors} 只股票处理失败，请查看上面的错误信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
