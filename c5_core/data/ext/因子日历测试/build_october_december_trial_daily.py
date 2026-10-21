# -*- coding: utf-8 -*-
"""生成十月至十二月B类试算所需的独立分钟日值。"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
import psutil
import pyarrow.parquet as pq
from scipy.special import gamma
from scipy.stats import norm, t as student_t

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE_DIR / "october_december_trial_results" / "cache"
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "daily_by_stock"
DEFAULT_MANIFEST = DEFAULT_ROOT / "daily_manifest.json"
DEFAULT_METHODOLOGY = DEFAULT_ROOT / "daily_methodology.json"
DEFAULT_TEMP_DIR = factor_store.DEFAULT_TEMP_DIR

CACHE_VERSION = 1
MINUTES_PER_DAY = 240
FIVE_MINUTE_BLOCKS = 48
JS_ALPHA = 0.01
CONSISTENT_ALPHA = 0.10
T_DEGREES_OF_FREEDOM = 5
AMOUNT_HISTORY_DAYS = 20

MORNING_MINUTES = np.arange(9 * 60 + 31, 11 * 60 + 31, dtype=np.int16)
AFTERNOON_MINUTES = np.arange(13 * 60 + 1, 15 * 60 + 1, dtype=np.int16)
TRADING_MINUTES = np.concatenate([MORNING_MINUTES, AFTERNOON_MINUTES])
MINUTE_TO_POSITION = {int(value): index for index, value in enumerate(TRADING_MINUTES)}

OUTPUT_COLUMNS = [
    "date",
    "code",
    "j10_d10_t_active_share_daily_trial",
    "consistent_trading_share_daily_trial",
    "consistent_buying_share_daily_trial",
    "consistent_selling_share_daily_trial",
    "j11_d04_adj_absret_lag_amount_corr_daily_trial",
    "jiang_oomen_js_daily_trial",
    "jiang_oomen_jump_direction_daily_trial",
    "positive_jump_return_daily_trial",
    "negative_jump_return_daily_trial",
    "nonjump_return_daily_trial",
    "intraday_log_return_daily_trial",
    "formula_mode",
    "formula_label",
    "minute_count",
    "trial_cache_version",
]

DAILY_PARAMETERS = {
    "t_active_share": {
        "frequency": "1分钟",
        "return": "日内1分钟对数收益，第一分钟使用开盘价",
        "sigma": "当日1分钟对数收益样本标准差",
        "student_t_df": T_DEGREES_OF_FREEDOM,
        "weight": "分钟成交额",
    },
    "consistent_volume": {
        "frequency": "5分钟，每日48段",
        "alpha": CONSISTENT_ALPHA,
        "condition": "abs(close-open)<=alpha*(high-low)",
        "buy": "同时close>open",
        "sell": "同时close<open",
    },
    "jiang_oomen": {
        "frequency": "5分钟，每日48段",
        "test": "Jiang-Oomen SwV可行比率检验，Omega采用p=6多幂变差",
        "one_sided_alpha_each_direction": JS_ALPHA,
        "positive_jump": "JS>正态分布99%分位",
        "negative_jump": "JS<-正态分布99%分位",
        "minute_localization": "日检验不提供时点；试算取显著方向上绝对值最大的5分钟对数收益",
        "nonjump_return": "当日全部5分钟对数收益减去试算定位的跳跃收益",
    },
    "adjusted_amount_corr": {
        "amount_history": "前20个完整交易日同一分钟",
        "standard_deviation": "总体标准差",
        "return": "1分钟对数收益",
        "daily_formula": "corr(abs(ret_t), adjusted_amount_(t-1))，剔除ret_t=0",
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


def build_stock_query(
    minute_path: str | Path,
    start: str,
    end: str,
    source_relation: str | None = None,
) -> str:
    lower, upper = _date_bounds(start, end)
    source = source_relation or (
        f"read_parquet('{_sql_path(minute_path)}', union_by_name = true)"
    )
    return f"""
        SELECT
            datetime,
            CAST(open AS DOUBLE) AS minute_open,
            CAST(high AS DOUBLE) AS minute_high,
            CAST(low AS DOUBLE) AS minute_low,
            CAST(close AS DOUBLE) AS minute_close,
            CAST(volume AS DOUBLE) AS minute_volume,
            CAST(amount AS DOUBLE) AS minute_amount
        FROM {source}
        WHERE datetime >= TIMESTAMP '{lower:%Y-%m-%d 00:00:00}'
          AND datetime < TIMESTAMP '{upper:%Y-%m-%d 00:00:00}'
        ORDER BY datetime
    """


def prepare_complete_days(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    if frame.empty:
        return frame.copy(), pd.DatetimeIndex([])
    result = frame.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    minute_of_day = result["datetime"].dt.hour * 60 + result["datetime"].dt.minute
    result["_position"] = minute_of_day.map(MINUTE_TO_POSITION)
    result["_date"] = result["datetime"].dt.normalize()
    exact_minute = result["datetime"].eq(result["datetime"].dt.floor("min"))
    result = result[exact_minute & result["_position"].notna()].copy()
    if result.empty:
        return result, pd.DatetimeIndex([])
    result["_position"] = result["_position"].astype(np.int16)
    counts = result.groupby("_date", sort=True)["_position"].agg(["size", "nunique"])
    dates = counts.index[
        (counts["size"] == MINUTES_PER_DAY)
        & (counts["nunique"] == MINUTES_PER_DAY)
    ]
    result = result[result["_date"].isin(dates)]
    result = result.sort_values(["_date", "_position"], kind="stable")
    return result.reset_index(drop=True), pd.DatetimeIndex(dates)


def intraday_returns(
    close: np.ndarray, day_open: np.ndarray, logarithmic: bool
) -> np.ndarray:
    previous = np.empty_like(close, dtype=np.float64)
    previous[:, 0] = day_open
    previous[:, 1:] = close[:, :-1]
    valid = (
        np.isfinite(close)
        & np.isfinite(previous)
        & (close > 0)
        & (previous > 0)
    )
    ratio = np.full(close.shape, np.nan, dtype=np.float64)
    np.divide(close, previous, out=ratio, where=valid)
    result = np.full(close.shape, np.nan, dtype=np.float64)
    if logarithmic:
        result[valid] = np.log(ratio[valid])
    else:
        result[valid] = ratio[valid] - 1.0
    return result


def compute_t_active_share(
    log_return: np.ndarray,
    minute_amount: np.ndarray,
    degrees_of_freedom: int = T_DEGREES_OF_FREEDOM,
) -> np.ndarray:
    returns = np.asarray(log_return, dtype=np.float64)
    amount = np.asarray(minute_amount, dtype=np.float64)
    scale = np.std(returns, axis=1, ddof=1)
    standardized = np.full(returns.shape, np.nan, dtype=np.float64)
    np.divide(
        returns,
        scale[:, None],
        out=standardized,
        where=np.isfinite(returns) & (scale[:, None] > 0),
    )
    probability = student_t.cdf(standardized, df=degrees_of_freedom)
    valid = np.isfinite(probability) & np.isfinite(amount) & (amount >= 0)
    numerator = np.sum(np.where(valid, probability * amount, 0.0), axis=1)
    denominator = np.sum(np.where(valid, amount, 0.0), axis=1)
    result = np.full(len(returns), np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    return result


def build_five_minute_bars(
    minute_open: np.ndarray,
    minute_high: np.ndarray,
    minute_low: np.ndarray,
    minute_close: np.ndarray,
    minute_volume: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    day_count = len(minute_close)

    def grouped(values: np.ndarray) -> np.ndarray:
        return values.reshape(day_count, 2, 24, 5).reshape(
            day_count, FIVE_MINUTE_BLOCKS, 5
        )

    open_group = grouped(minute_open)
    high_group = grouped(minute_high)
    low_group = grouped(minute_low)
    close_group = grouped(minute_close)
    volume_group = grouped(minute_volume)
    return (
        open_group[:, :, 0],
        np.max(high_group, axis=2),
        np.min(low_group, axis=2),
        close_group[:, :, -1],
        np.sum(volume_group, axis=2),
    )


def compute_consistent_volume_shares(
    open_5m: np.ndarray,
    high_5m: np.ndarray,
    low_5m: np.ndarray,
    close_5m: np.ndarray,
    volume_5m: np.ndarray,
    alpha: float = CONSISTENT_ALPHA,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    complete = np.logical_and.reduce(
        [
            np.isfinite(open_5m),
            np.isfinite(high_5m),
            np.isfinite(low_5m),
            np.isfinite(close_5m),
            np.isfinite(volume_5m),
            volume_5m >= 0,
        ]
    )
    consistent = complete & (
        np.abs(close_5m - open_5m) <= alpha * np.abs(high_5m - low_5m)
    )
    total = np.sum(np.where(complete, volume_5m, 0.0), axis=1)

    def share(mask: np.ndarray) -> np.ndarray:
        numerator = np.sum(np.where(mask, volume_5m, 0.0), axis=1)
        result = np.full(len(total), np.nan, dtype=np.float64)
        np.divide(numerator, total, out=result, where=total > 0)
        return result

    return (
        share(consistent),
        share(consistent & (close_5m > open_5m)),
        share(consistent & (close_5m < open_5m)),
    )


def compute_jiang_oomen_daily(
    close_5m: np.ndarray,
    day_open: np.ndarray,
    alpha: float = JS_ALPHA,
) -> dict[str, np.ndarray]:
    """按照片展开式计算JS，并固定一个可复现的5分钟收益定位办法。"""

    simple = intraday_returns(close_5m, day_open, logarithmic=False)
    log_return = intraday_returns(close_5m, day_open, logarithmic=True)
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
    six_products = np.ones((len(log_return), count - 5), dtype=np.float64)
    absolute = np.abs(log_return)
    for offset in range(6):
        six_products *= absolute[:, offset : offset + count - 5]
    mu_six = 2.0**3 * gamma(3.5) / math.sqrt(math.pi)
    omega = (
        mu_six
        / 9.0
        * count**3
        * mu_one ** -6
        / (count - 5.0)
        * np.sum(six_products, axis=1)
    )
    statistic = np.full(len(log_return), np.nan, dtype=np.float64)
    valid = (
        np.isfinite(simple).all(axis=1)
        & np.isfinite(log_return).all(axis=1)
        & np.isfinite(bipower)
        & np.isfinite(omega)
        & (swap_variance > 0)
        & (omega > 0)
    )
    statistic[valid] = (
        count
        * bipower[valid]
        / np.sqrt(omega[valid])
        * (1.0 - rv[valid] / swap_variance[valid])
    )
    critical = norm.ppf(1.0 - alpha)
    direction = np.full(len(log_return), np.nan, dtype=np.float64)
    direction[valid] = 0.0
    direction[valid & (statistic > critical)] = 1.0
    direction[valid & (statistic < -critical)] = -1.0

    positive_jump = np.full(len(log_return), np.nan, dtype=np.float64)
    negative_jump = np.full(len(log_return), np.nan, dtype=np.float64)
    positive_jump[valid] = 0.0
    negative_jump[valid] = 0.0
    positive_days = np.flatnonzero(direction == 1)
    negative_days = np.flatnonzero(direction == -1)
    if len(positive_days):
        positive_jump[positive_days] = np.maximum(
            np.max(log_return[positive_days], axis=1), 0.0
        )
    if len(negative_days):
        negative_jump[negative_days] = np.minimum(
            np.min(log_return[negative_days], axis=1), 0.0
        )
    intraday = np.full(len(log_return), np.nan, dtype=np.float64)
    intraday[valid] = np.sum(log_return[valid], axis=1)
    nonjump = intraday - positive_jump - negative_jump
    return {
        "statistic": statistic,
        "direction": direction,
        "positive_jump_return": positive_jump,
        "negative_jump_return": negative_jump,
        "nonjump_return": nonjump,
        "intraday_log_return": intraday,
    }


def adjusted_amount_zscore(
    minute_amount: np.ndarray, history_days: int = AMOUNT_HISTORY_DAYS
) -> np.ndarray:
    amount = np.asarray(minute_amount, dtype=np.float64)
    result = np.full(amount.shape, np.nan, dtype=np.float64)
    if len(amount) <= history_days:
        return result
    finite = np.isfinite(amount)
    filled = np.where(finite, amount, 0.0)
    sums = np.vstack([np.zeros((1, amount.shape[1])), np.cumsum(filled, axis=0)])
    squares = np.vstack(
        [np.zeros((1, amount.shape[1])), np.cumsum(filled * filled, axis=0)]
    )
    counts = np.vstack(
        [
            np.zeros((1, amount.shape[1]), dtype=np.int32),
            np.cumsum(finite, axis=0, dtype=np.int32),
        ]
    )
    history_sum = sums[history_days:-1] - sums[:-history_days - 1]
    history_square = squares[history_days:-1] - squares[:-history_days - 1]
    history_count = counts[history_days:-1] - counts[:-history_days - 1]
    mean = history_sum / history_days
    variance = np.maximum(history_square / history_days - mean * mean, 0.0)
    standard = np.sqrt(variance)
    current = amount[history_days:]
    good = (
        (history_count == history_days)
        & np.isfinite(current)
        & (standard > 0)
    )
    standardized = np.full(current.shape, np.nan, dtype=np.float64)
    np.divide(current - mean, standard, out=standardized, where=good)
    result[history_days:] = standardized
    return result


def compute_adj_absret_lag_amount_corr(
    log_return: np.ndarray, adjusted_amount: np.ndarray
) -> np.ndarray:
    lagged_amount = np.full(adjusted_amount.shape, np.nan, dtype=np.float64)
    lagged_amount[:, 1:] = adjusted_amount[:, :-1]
    result = np.full(len(log_return), np.nan, dtype=np.float64)
    for day in range(len(log_return)):
        valid = (
            np.isfinite(log_return[day])
            & np.isfinite(lagged_amount[day])
            & (log_return[day] != 0)
        )
        if int(valid.sum()) < 2:
            continue
        left = np.abs(log_return[day, valid])
        right = lagged_amount[day, valid]
        if np.std(left) == 0 or np.std(right) == 0:
            continue
        result[day] = np.corrcoef(left, right)[0, 1]
    return result


def build_daily_frame(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    complete, dates = prepare_complete_days(frame)
    if complete.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    numeric_columns = [
        "minute_open",
        "minute_high",
        "minute_low",
        "minute_close",
        "minute_volume",
        "minute_amount",
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
    day_open = minute_open[:, 0]
    log_return = intraday_returns(minute_close, day_open, logarithmic=True)
    t_active = compute_t_active_share(log_return, minute_amount)

    open_5m, high_5m, low_5m, close_5m, volume_5m = build_five_minute_bars(
        minute_open,
        minute_high,
        minute_low,
        minute_close,
        minute_volume,
    )
    consistent, buying, selling = compute_consistent_volume_shares(
        open_5m, high_5m, low_5m, close_5m, volume_5m
    )
    jump = compute_jiang_oomen_daily(close_5m, day_open)
    adjusted_amount = adjusted_amount_zscore(minute_amount)
    adjusted_corr = compute_adj_absret_lag_amount_corr(
        log_return, adjusted_amount
    )
    return pd.DataFrame(
        {
            "date": dates,
            "code": code.lower(),
            "j10_d10_t_active_share_daily_trial": t_active,
            "consistent_trading_share_daily_trial": consistent,
            "consistent_buying_share_daily_trial": buying,
            "consistent_selling_share_daily_trial": selling,
            "j11_d04_adj_absret_lag_amount_corr_daily_trial": adjusted_corr,
            "jiang_oomen_js_daily_trial": jump["statistic"],
            "jiang_oomen_jump_direction_daily_trial": jump["direction"],
            "positive_jump_return_daily_trial": jump["positive_jump_return"],
            "negative_jump_return_daily_trial": jump["negative_jump_return"],
            "nonjump_return_daily_trial": jump["nonjump_return"],
            "intraday_log_return_daily_trial": jump["intraday_log_return"],
            "formula_mode": "trial",
            "formula_label": "试算",
            "minute_count": np.full(day_count, MINUTES_PER_DAY, dtype=np.int16),
            "trial_cache_version": np.full(day_count, CACHE_VERSION, dtype=np.int8),
        },
        columns=OUTPUT_COLUMNS,
    )


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def output_is_current(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != OUTPUT_COLUMNS or parquet.metadata.num_rows == 0:
            return False
        marker = parquet.read_row_group(
            0, columns=["trial_cache_version"]
        ).to_pandas()
        return bool(marker["trial_cache_version"].eq(CACHE_VERSION).all())
    except Exception:
        return False


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION
    worker_temp = Path(temp_dir) / f"oct_dec_trial_worker_{os.getpid()}"
    worker_temp.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(f"SET temp_directory = '{_sql_path(worker_temp)}'")
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def _build_one_stock(
    minute_path: str,
    output_path: str,
    start: str,
    end: str,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程没有初始化。")
    source = Path(minute_path)
    output = Path(output_path)
    temporary = output.with_name(f"{output.stem}.tmp.parquet")
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    frame = _WORKER_CONNECTION.execute(
        build_stock_query(source, start, end)
    ).fetchdf()
    daily = build_daily_frame(frame, source.stem.lower())
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
    memory = []
    for child in parent.children(recursive=True):
        try:
            if child.is_running():
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
    if selected:
        files = [data_dir / f"{code}.parquet" for code in sorted(selected)]
        files = [path for path in files if path.exists()]
    else:
        base_pattern = factor_store.DEFAULT_SOURCES["base_daily"]
        codes = sorted(
            path.stem.lower()
            for path in base_pattern.parent.glob(base_pattern.name)
        )
        files = [data_dir / f"{code}.parquet" for code in codes]
        files = [path for path in files if path.exists()]
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"没有找到分钟文件：{data_dir}")
    return files


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=factor_store.DEFAULT_MINUTE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--worker-memory-limit", default="2GB")
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("workers必须至少为1。")
    output_dir = _assert_workspace_path(args.output_dir)
    manifest_path = _assert_workspace_path(args.manifest)
    methodology_path = _assert_workspace_path(args.methodology)
    temp_dir = _assert_workspace_path(args.temp_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    files = list_input_files(args.data_dir, args.codes_file, args.limit)
    jobs: list[tuple[Path, Path]] = []
    skipped = 0
    for source in files:
        output = output_dir / f"{source.stem.lower()}.parquet"
        if output_is_current(output) and not args.force:
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
        futures = {
            executor.submit(
                _build_one_stock,
                str(source),
                str(output),
                args.start,
                args.end,
            ): source.stem.lower()
            for source, output in jobs
        }
        pending = set(futures)
        completed = 0
        while pending:
            done, pending = wait(
                pending, timeout=1.0, return_when=FIRST_COMPLETED
            )
            total_mib, single_mib = _memory_sample()
            peak_total_mib = max(peak_total_mib, total_mib)
            peak_single_mib = max(peak_single_mib, single_mib)
            for future in done:
                completed += 1
                code = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"已处理 {completed}/{len(jobs)}；失败 {len(failures)}；"
                        f"工作进程内存最高合计 {peak_total_mib:.0f} MiB",
                        flush=True,
                    )

    elapsed = time.perf_counter() - started
    manifest = {
        "cache_version": CACHE_VERSION,
        "formula_mode": "trial",
        "formula_label": "试算",
        "start": args.start,
        "end": args.end,
        "requested_files": len(files),
        "jobs_run": len(jobs),
        "skipped_existing": skipped,
        "completed_outputs": len(list(output_dir.glob("*.parquet"))),
        "failures": failures,
        "rows_written": int(sum(int(row["rows"]) for row in results)),
        "size_bytes_written": int(
            sum(int(row["size_bytes"]) for row in results)
        ),
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "worker_memory_limit": args.worker_memory_limit,
        "peak_total_child_mib": round(peak_total_mib, 1),
        "peak_single_child_mib": round(peak_single_mib, 1),
        "columns": OUTPUT_COLUMNS,
    }
    methodology = {
        "formula_mode": "trial",
        "formula_label": "试算",
        "parameters": DAILY_PARAMETERS,
        "source_fields": [
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
        ],
        "raw_minute_data_copied": False,
        "cache_purpose": "一次读取每只股票所需分钟字段，同时生成所有十至十二月B类日值。",
        "created_at": pd.Timestamp.now().isoformat(),
    }
    _atomic_write_json(manifest, manifest_path)
    _atomic_write_json(methodology, methodology_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    if failures:
        raise RuntimeError(f"有 {len(failures)} 只股票处理失败。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
