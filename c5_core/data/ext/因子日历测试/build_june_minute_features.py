# -*- coding: utf-8 -*-
"""从每股分钟文件生成六月可计算量价因子所需的每日数据。"""

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
    BASE_DIR / "june_factor_results" / "cache" / "june_daily_by_stock"
)

INPUT_COLUMNS = january.INPUT_COLUMNS
TRADING_MINUTES = january.TRADING_MINUTES
MINUTES_PER_DAY = january.MINUTES_PER_DAY
STRUCTURE_DAYS = 21
ATV_COLUMNS = [f"atv_5m_{index:02d}" for index in range(48)]
OUTPUT_COLUMNS = [
    "date",
    "code",
    "structure_reversal_21d_10m",
    "fuzzy_amount_ratio_daily",
    "wait_rescue_follow_daily",
    "downside_realized_vol_5m",
    "intraday_cvar_5pct_daily",
    "intraday_volatility_1m",
    "pv_corr_daily",
    *ATV_COLUMNS,
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--codes-file", type=Path)
    return parser.parse_args(argv)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    valid = (
        np.isfinite(values)
        & np.isfinite(weights)
        & (weights > 0)
    )
    if not valid.any():
        return math.nan
    denominator = float(np.sum(weights[valid]))
    if not np.isfinite(denominator) or denominator <= 0:
        return math.nan
    return float(np.sum(values[valid] * weights[valid]) / denominator)


def _structured_reversal_21d_10m(
    ten_minute_returns: np.ndarray,
    ten_minute_volume: np.ndarray,
    history_days: int = STRUCTURE_DAYS,
    low_volume_share: float = 0.10,
) -> np.ndarray:
    """按过去21日全部10分钟段计算结构化反转。"""
    returns = np.asarray(ten_minute_returns, dtype=np.float64)
    volume = np.asarray(ten_minute_volume, dtype=np.float64)
    if returns.shape != volume.shape or returns.ndim != 2:
        raise ValueError("10分钟收益和成交量必须是形状相同的二维矩阵")
    if history_days < 1:
        raise ValueError("历史日数必须大于0")
    if not 0 < low_volume_share < 1:
        raise ValueError("低成交量占比必须在0和1之间")

    result = np.full(len(returns), np.nan, dtype=np.float64)
    for day in range(history_days - 1, len(returns)):
        left = day - history_days + 1
        window_return = returns[left : day + 1].reshape(-1)
        window_volume = volume[left : day + 1].reshape(-1)
        valid = (
            np.isfinite(window_return)
            & np.isfinite(window_volume)
            & (window_volume > 0)
        )
        # 21日窗口必须包含完整的504个有效10分钟段，不能静默缩短窗口。
        if not valid.all():
            continue
        current_return = window_return[valid]
        current_volume = window_volume[valid]
        order = np.argsort(current_volume, kind="stable")
        low_count = int(np.floor(len(order) * low_volume_share))
        if low_count < 1 or low_count >= len(order):
            continue
        momentum = order[:low_count]
        reversal = order[low_count:]
        momentum_return = _weighted_mean(
            current_return[momentum], 1.0 / current_volume[momentum]
        )
        reversal_return = _weighted_mean(
            current_return[reversal], current_volume[reversal]
        )
        if np.isfinite(momentum_return) and np.isfinite(reversal_return):
            result[day] = reversal_return - momentum_return
    return result


def _rolling_std_rows(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    if source.ndim != 2:
        raise ValueError("滚动标准差输入必须是二维矩阵")
    result = np.full(source.shape, np.nan, dtype=np.float64)
    for end in range(window - 1, source.shape[1]):
        block = source[:, end - window + 1 : end + 1]
        valid = np.isfinite(block).all(axis=1)
        result[valid, end] = np.std(block[valid], axis=1, ddof=0)
    return result


def _daily_fuzzy_amount_ratio(
    one_minute_returns: np.ndarray,
    one_minute_amount: np.ndarray,
    window: int = 5,
) -> np.ndarray:
    """计算每日模糊金额比。"""
    returns = np.asarray(one_minute_returns, dtype=np.float64)
    amount = np.asarray(one_minute_amount, dtype=np.float64)
    if returns.shape != amount.shape or returns.ndim != 2:
        raise ValueError("分钟收益和成交额必须是形状相同的二维矩阵")
    kept_returns = returns.copy()
    kept_returns[:, 0] = np.nan
    kept_returns[:, -1] = np.nan
    volatility = _rolling_std_rows(kept_returns, window)
    fuzziness = _rolling_std_rows(volatility, window)
    result = np.full(len(returns), np.nan, dtype=np.float64)
    for day in range(len(returns)):
        valid_amount = np.isfinite(amount[day])
        valid_fuzziness = np.isfinite(fuzziness[day])
        valid_amount[[0, -1]] = False
        valid_fuzziness[[0, -1]] = False
        if not valid_amount.any() or not valid_fuzziness.any():
            continue
        daily_mean = float(np.mean(fuzziness[day, valid_fuzziness]))
        fog = valid_amount & valid_fuzziness & (fuzziness[day] > daily_mean)
        if not fog.any():
            continue
        all_mean = float(np.mean(amount[day, valid_amount]))
        if not np.isfinite(all_mean) or all_mean <= 0:
            continue
        result[day] = float(np.mean(amount[day, fog])) / all_mean
    return result


def _waiting_rescue_follow_coefficient(
    one_minute_volume: np.ndarray,
    top_count: int = 10,
    minimum_gap_minutes: int = 5,
    follow_minutes: int = 5,
    start_position: int = 15,
) -> np.ndarray:
    """按图片文字计算每日“待著而救”跟随系数。"""
    volume = np.asarray(one_minute_volume, dtype=np.float64)
    if volume.ndim != 2 or volume.shape[1] != MINUTES_PER_DAY:
        raise ValueError("分钟成交量必须是每天240个时点的二维矩阵")
    if top_count < 1 or follow_minutes < 1:
        raise ValueError("海量点数量和跟随分钟数必须大于0")

    result = np.full(len(volume), np.nan, dtype=np.float64)
    minute_labels = np.asarray(TRADING_MINUTES, dtype=np.int32)
    session_end = np.r_[np.repeat(119, 120), np.repeat(239, 120)]
    for day in range(len(volume)):
        row = volume[day]
        eligible = np.flatnonzero(
            (np.arange(MINUTES_PER_DAY) >= start_position)
            & np.isfinite(row)
            & (row > 0)
        )
        if not len(eligible):
            continue
        count = min(top_count, len(eligible))
        selected = eligible[
            np.argpartition(row[eligible], -count)[-count:]
        ]
        selected = selected[np.argsort(minute_labels[selected], kind="stable")]

        kept: list[int] = []
        for position in selected:
            if kept and (
                minute_labels[position] - minute_labels[kept[-1]]
                < minimum_gap_minutes
            ):
                # 原文说明第二个海量点更像跟随交易，因此删去后一个。
                continue
            kept.append(int(position))

        coefficients: list[float] = []
        for position in kept:
            if position + follow_minutes > session_end[position]:
                continue
            follower = row[position + 1 : position + follow_minutes + 1]
            if not np.isfinite(follower).all() or row[position] <= 0:
                continue
            coefficients.append(float(np.sum(follower) / row[position]))
        if coefficients:
            result[day] = float(np.mean(coefficients))
    return result


def _downside_realized_volatility_5m(
    five_minute_log_returns: np.ndarray,
) -> np.ndarray:
    """计算5分钟下行已实现波动率。"""
    returns = np.asarray(five_minute_log_returns, dtype=np.float64)
    if returns.ndim != 2:
        raise ValueError("5分钟收益必须是二维矩阵")
    valid = np.isfinite(returns).all(axis=1)
    result = np.sum(np.where(returns <= 0, returns * returns, 0.0), axis=1)
    result = result.astype(np.float64, copy=False)
    result[~valid] = np.nan
    return result


def _left_tail_volume_weighted_cvar(
    one_minute_returns: np.ndarray,
    one_minute_volume: np.ndarray,
    alpha: float = 0.05,
) -> np.ndarray:
    """计算每日左侧alpha分位内的成交量加权平均分钟收益。"""
    returns = np.asarray(one_minute_returns, dtype=np.float64)
    volume = np.asarray(one_minute_volume, dtype=np.float64)
    if returns.shape != volume.shape or returns.ndim != 2:
        raise ValueError("分钟收益和成交量必须是形状相同的二维矩阵")
    if not 0 < alpha < 1:
        raise ValueError("alpha 必须在0和1之间")
    result = np.full(len(returns), np.nan, dtype=np.float64)
    for day in range(len(returns)):
        valid = (
            np.isfinite(returns[day])
            & np.isfinite(volume[day])
            & (volume[day] > 0)
        )
        count = int(valid.sum())
        if count < 2:
            continue
        current_return = returns[day, valid]
        current_volume = volume[day, valid]
        tail_count = max(1, int(math.ceil(count * alpha)))
        positions = np.argsort(current_return, kind="stable")[:tail_count]
        result[day] = _weighted_mean(
            current_return[positions], current_volume[positions]
        )
    return result


def _intraday_return_std(one_minute_returns: np.ndarray) -> np.ndarray:
    returns = np.asarray(one_minute_returns, dtype=np.float64)
    if returns.ndim != 2:
        raise ValueError("分钟收益必须是二维矩阵")
    valid = np.isfinite(returns).all(axis=1)
    result = np.std(returns, axis=1, ddof=0)
    result[~valid] = np.nan
    return result


def _daily_price_volume_correlation(
    one_minute_close: np.ndarray,
    one_minute_volume: np.ndarray,
) -> np.ndarray:
    close = np.asarray(one_minute_close, dtype=np.float64)
    volume = np.asarray(one_minute_volume, dtype=np.float64)
    if close.shape != volume.shape or close.ndim != 2:
        raise ValueError("分钟收盘价和成交量必须是形状相同的二维矩阵")
    result = np.full(len(close), np.nan, dtype=np.float64)
    for day in range(len(close)):
        left = close[day]
        right = volume[day]
        valid = np.isfinite(left) & np.isfinite(right)
        if valid.sum() < 2:
            continue
        x = left[valid] - np.mean(left[valid])
        y = right[valid] - np.mean(right[valid])
        denominator = math.sqrt(float(x @ x) * float(y @ y))
        if denominator > 0 and np.isfinite(denominator):
            result[day] = float((x @ y) / denominator)
    return result


def _abnormal_trading_volume_5m(five_minute_volume: np.ndarray) -> np.ndarray:
    """用上一交易日同一5分钟成交量作为预期值。"""
    volume = np.asarray(five_minute_volume, dtype=np.float64)
    if volume.ndim != 2 or volume.shape[1] != 48:
        raise ValueError("5分钟成交量必须是每天48个时点的二维矩阵")
    result = np.full(volume.shape, np.nan, dtype=np.float64)
    previous = volume[:-1]
    current = volume[1:]
    valid = np.isfinite(current) & np.isfinite(previous) & (previous > 0)
    np.divide(current, previous, out=result[1:], where=valid)
    return result


def build_daily_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """把一只股票的分钟数据整理成每日六月因子输入。"""
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
    qfq_close = matrix("qfq_close")
    volume = matrix("volume")
    amount = matrix("amount")

    one_returns = january.intraday_log_return_matrix(qfq_close, qfq_open[:, 0])
    five_close = january._reshape_blocks(qfq_close, 5)[:, :, -1]
    five_volume = np.sum(january._reshape_blocks(volume, 5), axis=2)
    five_returns = january.intraday_log_return_matrix(five_close, qfq_open[:, 0])
    ten_close = january._reshape_blocks(qfq_close, 10)[:, :, -1]
    ten_volume = np.sum(january._reshape_blocks(volume, 10), axis=2)
    ten_returns = january.intraday_log_return_matrix(ten_close, qfq_open[:, 0])

    atv = _abnormal_trading_volume_5m(five_volume)
    data: dict[str, object] = {
        "date": dates.to_numpy(dtype="datetime64[ns]"),
        "code": np.repeat(code.lower(), day_count),
        "structure_reversal_21d_10m": _structured_reversal_21d_10m(
            ten_returns, ten_volume
        ),
        "fuzzy_amount_ratio_daily": _daily_fuzzy_amount_ratio(
            one_returns, amount
        ),
        "wait_rescue_follow_daily": _waiting_rescue_follow_coefficient(volume),
        "downside_realized_vol_5m": _downside_realized_volatility_5m(
            five_returns
        ),
        "intraday_cvar_5pct_daily": _left_tail_volume_weighted_cvar(
            one_returns, volume, 0.05
        ),
        "intraday_volatility_1m": _intraday_return_std(one_returns),
        "pv_corr_daily": _daily_price_volume_correlation(qfq_close, volume),
    }
    for index, column in enumerate(ATV_COLUMNS):
        data[column] = atv[:, index]
    daily = pd.DataFrame(data, columns=OUTPUT_COLUMNS)
    numeric_columns = OUTPUT_COLUMNS[2:]
    daily[numeric_columns] = daily[numeric_columns].astype(np.float32)
    return daily


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

    selected = january.load_selected_codes(args.codes_file)
    files = january.list_input_files(data_dir, selected)
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
    print(
        f"选中 {len(files)} 只股票，已有结果跳过 {skipped} 只，"
        f"待处理 {len(tasks)} 只。",
        flush=True,
    )
    if not tasks:
        return 0

    errors = 0
    completed = 0
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        for item in executor.map(_safe_process, tasks, chunksize=1):
            completed += 1
            if item["status"] == "error":
                errors += 1
                print(
                    f"[{completed}/{len(tasks)}] {item['code']} 失败："
                    f"{item['error']}",
                    flush=True,
                )
            else:
                print(
                    f"[{completed}/{len(tasks)}] {item['code']} 完成，"
                    f"保留 {item['days']} 个完整日。",
                    flush=True,
                )
    if errors:
        raise RuntimeError(f"有 {errors} 只股票处理失败，请查看上面的错误信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
