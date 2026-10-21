# -*- coding: utf-8 -*-
"""从每股分钟文件生成四月量价因子所需的每日数据。"""

from __future__ import annotations

import argparse
import math
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import build_january_minute_cache as january


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = january.DEFAULT_DATA_DIR
DEFAULT_OUTPUT_DIR = (
    BASE_DIR / "april_factor_results" / "cache" / "april_daily_by_stock"
)

# 分钟读法和交易时点与一月脚本保持一致。
INPUT_COLUMNS = january.INPUT_COLUMNS
TRADING_MINUTES = january.TRADING_MINUTES
MINUTE_TO_POSITION = january.MINUTE_TO_POSITION
MINUTES_PER_DAY = january.MINUTES_PER_DAY
HISTORY_DAYS = 20

OUTPUT_COLUMNS = [
    "date",
    "code",
    "tail_volume_share_daily",
    "small_down_jump_vol_5m",
    "noon_ancient_tree_abs_t",
    "noon_ancient_tree_f_all",
    "burst_amount_corr_20d",
    "pv_corr_daily",
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


def _pearson_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """计算有限数值对的皮尔逊相关系数。"""
    left = np.asarray(x, dtype=np.float64).reshape(-1)
    right = np.asarray(y, dtype=np.float64).reshape(-1)
    valid = np.isfinite(left) & np.isfinite(right)
    if valid.sum() < 2:
        return math.nan
    left = left[valid]
    right = right[valid]
    left = left - np.mean(left)
    right = right - np.mean(right)
    denominator = math.sqrt(float(left @ left) * float(right @ right))
    if not np.isfinite(denominator) or denominator <= 0:
        return math.nan
    return float((left @ right) / denominator)


def _daily_pearson(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """逐日计算两个分钟序列的皮尔逊相关系数。"""
    left = np.asarray(x, dtype=np.float64)
    right = np.asarray(y, dtype=np.float64)
    if left.shape != right.shape or left.ndim != 2:
        raise ValueError("两个分钟矩阵必须形状相同且都是二维")
    result = np.full(len(left), np.nan, dtype=np.float64)
    for day in range(len(left)):
        result[day] = _pearson_correlation(left[day], right[day])
    return result


def _small_down_jump_variation(
    returns: np.ndarray, alpha: float = 4.0
) -> np.ndarray:
    """用5分钟对数收益计算小幅下行跳跃波动率。"""
    values = np.asarray(returns, dtype=np.float64).copy()
    if values.ndim == 1:
        values = values[None, :]
    if values.ndim != 2 or values.shape[1] < 3:
        raise ValueError("收益矩阵每日至少需要3个时点")
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
    negative_variance = np.sum(
        np.where(values < 0, values * values, 0.0), axis=1
    )
    rvjn = np.maximum(negative_variance - 0.5 * integrated_variance, 0.0)
    threshold = alpha * (1.0 / values.shape[1]) ** 0.49 * np.sqrt(
        np.maximum(integrated_variance, 0.0)
    )
    large_negative = np.sum(
        np.where(values < -threshold[:, None], values * values, 0.0), axis=1
    )
    # 大幅部分不能超过全部下行跳跃，剩余值因此不会小于零。
    large_negative = np.minimum(rvjn, large_negative)
    result = np.maximum(rvjn - large_negative, 0.0)
    result[~all_valid] = np.nan
    return result


def _noon_ancient_tree_statistics(
    simple_returns: np.ndarray, volume: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """回归1分钟简单收益与当期及5阶成交量差，返回截距t值和整体F值。"""
    returns = np.asarray(simple_returns, dtype=np.float64)
    minute_volume = np.asarray(volume, dtype=np.float64)
    if returns.shape != minute_volume.shape or returns.ndim != 2:
        raise ValueError("收益和成交量必须是形状相同的二维矩阵")
    if returns.shape[1] != MINUTES_PER_DAY:
        raise ValueError("每个交易日必须正好包含240个交易分钟")

    absolute_t = np.full(len(returns), np.nan, dtype=np.float64)
    all_slope_f = np.full(len(returns), np.nan, dtype=np.float64)
    volume_difference = np.diff(minute_volume, axis=1)
    # 第6个差分以后，当前值及5阶差分才全部可用。
    regressors = np.stack(
        [
            volume_difference[:, 5 - lag : volume_difference.shape[1] - lag]
            for lag in range(6)
        ],
        axis=2,
    )
    dependent = returns[:, 6:]

    for day in range(len(returns)):
        x = regressors[day]
        y = dependent[day]
        valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
        x = x[valid]
        y = y[valid]
        parameter_count = 7
        if len(y) <= parameter_count:
            continue

        # 缩小数值范围不会改变截距t值或六个斜率的整体F值。
        scales = np.std(x, axis=0, ddof=0)
        if not np.isfinite(scales).all() or np.any(scales <= 0):
            continue
        design = np.column_stack([np.ones(len(y)), x / scales])
        if np.linalg.matrix_rank(design) < parameter_count:
            continue
        beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ beta
        residual_square_sum = float(residual @ residual)
        degrees_of_freedom = len(y) - parameter_count
        sigma2 = residual_square_sum / degrees_of_freedom
        inverse = np.linalg.inv(design.T @ design)
        intercept_variance = sigma2 * inverse[0, 0]
        if np.isfinite(intercept_variance) and intercept_variance > 0:
            absolute_t[day] = abs(float(beta[0])) / math.sqrt(intercept_variance)

        restricted_residual = y - np.mean(y)
        restricted_square_sum = float(restricted_residual @ restricted_residual)
        explained = max(restricted_square_sum - residual_square_sum, 0.0)
        if residual_square_sum > 0 and np.isfinite(residual_square_sum):
            all_slope_f[day] = (
                explained / 6.0
            ) / (residual_square_sum / degrees_of_freedom)
        elif explained > 0:
            all_slope_f[day] = math.inf
    return absolute_t, all_slope_f


def _rolling_burst_amount_correlation(
    volume: np.ndarray,
    amount: np.ndarray,
    history_days: int = HISTORY_DAYS,
    valid_days: np.ndarray | None = None,
) -> np.ndarray:
    """计算过去20日喷发点成交额与下一交易分钟成交额的相关系数。"""
    minute_volume = np.asarray(volume, dtype=np.float64)
    minute_amount = np.asarray(amount, dtype=np.float64)
    if minute_volume.shape != minute_amount.shape or minute_volume.ndim != 2:
        raise ValueError("成交量和成交额必须是形状相同的二维矩阵")
    if minute_volume.shape[1] != MINUTES_PER_DAY:
        raise ValueError("每个交易日必须正好包含240个交易分钟")
    if history_days < 1:
        raise ValueError("历史日数必须大于0")
    if valid_days is None:
        good_days = np.ones(len(minute_volume), dtype=bool)
    else:
        good_days = np.asarray(valid_days, dtype=bool).reshape(-1)
        if len(good_days) != len(minute_volume):
            raise ValueError("有效日期标记必须与分钟矩阵日数一致")

    next_amount = np.full_like(minute_amount, np.nan, dtype=np.float64)
    # 11:30的下一根是13:01；15:00保持缺值，不接到次日。
    next_amount[:, :-1] = minute_amount[:, 1:]
    result = np.full(len(minute_volume), np.nan, dtype=np.float64)
    if len(minute_volume) < history_days:
        return result

    volume_windows = np.lib.stride_tricks.sliding_window_view(
        minute_volume, history_days, axis=0
    )
    amount_windows = np.lib.stride_tricks.sliding_window_view(
        minute_amount, history_days, axis=0
    )
    next_windows = np.lib.stride_tricks.sliding_window_view(
        next_amount, history_days, axis=0
    )
    good_windows = np.lib.stride_tricks.sliding_window_view(
        good_days, history_days
    ).all(axis=1)

    # 分块计算，避免一次建立全部日期的三维临时数组。
    chunk_size = 128
    for left_day in range(0, len(volume_windows), chunk_size):
        right_day = min(left_day + chunk_size, len(volume_windows))
        volume_part = volume_windows[left_day:right_day]
        amount_part = amount_windows[left_day:right_day]
        next_part = next_windows[left_day:right_day]

        volume_valid = np.isfinite(volume_part).all(axis=2)
        mean = np.mean(volume_part, axis=2)
        standard = np.std(volume_part, axis=2, ddof=0)
        threshold = mean + standard
        bursts = volume_part > threshold[:, :, None]
        valid_pair = (
            bursts
            & volume_valid[:, :, None]
            & np.isfinite(amount_part)
            & np.isfinite(next_part)
        )

        count = np.sum(valid_pair, axis=(1, 2), dtype=np.int64).astype(np.float64)
        x = np.where(valid_pair, amount_part, 0.0)
        y = np.where(valid_pair, next_part, 0.0)
        sum_x = np.sum(x, axis=(1, 2))
        sum_y = np.sum(y, axis=(1, 2))
        sum_x2 = np.sum(x * x, axis=(1, 2))
        sum_y2 = np.sum(y * y, axis=(1, 2))
        sum_xy = np.sum(x * y, axis=(1, 2))

        with np.errstate(invalid="ignore", divide="ignore"):
            covariance = sum_xy - sum_x * sum_y / count
            variance_x = sum_x2 - sum_x * sum_x / count
            variance_y = sum_y2 - sum_y * sum_y / count
            denominator = np.sqrt(
                np.maximum(variance_x, 0.0) * np.maximum(variance_y, 0.0)
            )
        valid = (
            good_windows[left_day:right_day]
            & (count >= 2)
            & np.isfinite(covariance)
            & np.isfinite(denominator)
            & (denominator > 0)
        )
        local = np.full(right_day - left_day, np.nan, dtype=np.float64)
        np.divide(covariance, denominator, out=local, where=valid)
        result[
            left_day + history_days - 1 : right_day + history_days - 1
        ] = np.clip(local, -1.0, 1.0)
    return result


def build_daily_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """把一只股票的分钟数据整理成每日四月因子输入。"""
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
    qfq_high = matrix("qfq_high")
    qfq_low = matrix("qfq_low")
    qfq_close = matrix("qfq_close")
    volume = matrix("volume")
    amount = matrix("amount")
    ratio = matrix("qfq_ratio")

    minute_vwap_qfq = np.full(amount.shape, np.nan, dtype=np.float64)
    np.divide(
        amount * ratio,
        volume,
        out=minute_vwap_qfq,
        where=np.isfinite(volume) & (volume > 0),
    )
    price_amount_bad = (
        (
            (minute_vwap_qfq < qfq_low * 0.99)
            | (minute_vwap_qfq > qfq_high * 1.01)
        )
        & np.isfinite(minute_vwap_qfq)
        & np.isfinite(qfq_low)
        & np.isfinite(qfq_high)
    ).any(axis=1)

    daily_volume = np.sum(volume, axis=1)
    tail_start = MINUTE_TO_POSITION[14 * 60 + 31]
    tail_volume = np.sum(volume[:, tail_start:], axis=1)
    tail_share = np.full(day_count, np.nan, dtype=np.float64)
    np.divide(tail_volume, daily_volume, out=tail_share, where=daily_volume > 0)

    five_close = january._reshape_blocks(qfq_close, 5)[:, :, -1]
    five_returns = january.intraday_log_return_matrix(five_close, qfq_open[:, 0])
    small_down = _small_down_jump_variation(five_returns, alpha=4.0)

    simple_returns = np.full(qfq_close.shape, np.nan, dtype=np.float64)
    previous_close = qfq_close[:, :-1]
    np.divide(
        qfq_close[:, 1:],
        previous_close,
        out=simple_returns[:, 1:],
        where=np.isfinite(previous_close) & (previous_close != 0),
    )
    simple_returns[:, 1:] -= 1.0
    ancient_t, ancient_f = _noon_ancient_tree_statistics(simple_returns, volume)
    burst_corr = _rolling_burst_amount_correlation(
        volume,
        amount,
        HISTORY_DAYS,
        valid_days=~price_amount_bad,
    )
    price_volume_corr = _daily_pearson(qfq_close, volume)

    return pd.DataFrame(
        {
            "date": dates.to_numpy(dtype="datetime64[ns]"),
            "code": code.lower(),
            "tail_volume_share_daily": tail_share,
            "small_down_jump_vol_5m": small_down,
            "noon_ancient_tree_abs_t": ancient_t,
            "noon_ancient_tree_f_all": ancient_f,
            "burst_amount_corr_20d": burst_corr,
            "pv_corr_daily": price_volume_corr,
        },
        columns=OUTPUT_COLUMNS,
    )


def process_one_stock(
    input_path: Path, output_dir: Path, start: str, end: str
) -> dict[str, object]:
    code = input_path.stem.lower()
    output_path = output_dir / f"{code}.parquet"
    if output_path.exists():
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
