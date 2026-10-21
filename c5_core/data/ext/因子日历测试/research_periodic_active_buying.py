# -*- coding: utf-8 -*-
"""研究规律放量上涨，并用逐笔成交检查是否像持续分批买入。

程序分成四步：

1. 从逐笔行情快照生成一分钟数据；
2. 用过去二十个交易日判断异常放量，寻找连续五批的规律；
3. 只读取候选股票对应的逐笔成交，计算主动买入和订单分布；
4. 前半段选条件，后半段独立检查，并输出结果。

所有判断只使用当时及此前能够看到的数据。原始 F 盘数据只读。
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Iterable, Sequence
import warnings

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
warnings.filterwarnings("ignore", message="All-NaN slice encountered")
warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0 for slice")


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "outputs" / "periodic_active_buying_20260804"
BAD_DATES = {
    "2026-03-12",
    "2026-04-14",
    "2026-05-12",
    "2026-07-06",
    "2026-07-09",
    "2026-07-10",
    "2026-07-13",
    "2026-07-15",
}
HISTORY_DAYS = 20
MIN_HISTORY_DAYS = 20
RETURN_STABILITY_LIMIT = 0.927460


@dataclass(frozen=True)
class Settings:
    catalog: Path
    output_dir: Path = DEFAULT_OUTPUT
    start: str = "2026-01-05"
    end: str = "2026-07-22"
    threads: int = 6
    memory_limit: str = "8GB"
    min_minute_amount: float = 100_000.0
    spike_z: float = 1.0
    overwrite: bool = False

    @property
    def minute_dir(self) -> Path:
        return self.output_dir / "minute_cache"

    @property
    def candidate_path(self) -> Path:
        return self.output_dir / "minute_candidates.parquet"

    @property
    def level2_path(self) -> Path:
        return self.output_dir / "level2_candidate_features.parquet"


def discover_catalog() -> Path:
    candidates = sorted(Path("F:/").glob("2026*/l2_a_share_parquet/l2_catalog.duckdb"))
    if len(candidates) != 1:
        raise FileNotFoundError(f"预期找到一个 Level2 目录，实际找到 {len(candidates)} 个")
    return candidates[0]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("minutes", "candidates", "level2", "evaluate", "all"),
        default="all",
    )
    parser.add_argument("--catalog", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start", default="2026-01-05")
    parser.add_argument("--end", default="2026-07-22")
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--min-minute-amount", type=float, default=100_000.0)
    parser.add_argument("--spike-z", type=float, default=1.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def make_settings(args: argparse.Namespace) -> Settings:
    return Settings(
        catalog=(args.catalog or discover_catalog()).resolve(),
        output_dir=args.output_dir.resolve(),
        start=args.start,
        end=args.end,
        threads=max(int(args.threads), 1),
        memory_limit=args.memory_limit,
        min_minute_amount=float(args.min_minute_amount),
        spike_z=float(args.spike_z),
        overwrite=bool(args.overwrite),
    )


def sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def available_dates(settings: Settings) -> list[str]:
    quote_root = settings.catalog.parent / "quotes"
    start = pd.Timestamp(settings.start)
    end = pd.Timestamp(settings.end)
    dates = []
    for path in quote_root.glob("trade_date=*"):
        if not path.is_dir() or "=" not in path.name:
            continue
        value = path.name.split("=", 1)[1]
        try:
            date = pd.Timestamp(value)
        except ValueError:
            continue
        if start <= date <= end and value not in BAD_DATES:
            dates.append(value)
    dates.sort()
    if not dates:
        raise RuntimeError("没有找到可用日期")
    return dates


def minute_cache_query(date: str) -> str:
    return f"""
        WITH minute_snapshot AS (
            SELECT
                code,
                CASE
                    WHEN CAST(event_ts AS TIME) < TIME '12:00:00'
                        THEN datediff('minute', TIMESTAMP '{date} 09:30:00', date_trunc('minute', event_ts))
                    ELSE 120 + datediff('minute', TIMESTAMP '{date} 13:00:00', date_trunc('minute', event_ts))
                END::SMALLINT AS minute_no,
                arg_min(last_price_x1e4, event_ts) / 10000.0 AS minute_open,
                max(last_price_x1e4) / 10000.0 AS minute_high,
                min(last_price_x1e4) / 10000.0 AS minute_low,
                arg_max(last_price_x1e4, event_ts) / 10000.0 AS minute_close,
                arg_max(cum_amount_valid, event_ts) AS cum_amount
            FROM l2_quotes
            WHERE trade_date = DATE '{date}'
              AND last_price_x1e4 > 0
              AND (
                    (CAST(event_ts AS TIME) >= TIME '09:30:00' AND CAST(event_ts AS TIME) < TIME '11:30:00')
                 OR (CAST(event_ts AS TIME) >= TIME '13:00:00' AND CAST(event_ts AS TIME) < TIME '15:00:00')
              )
            GROUP BY code, minute_no
        ), delta AS (
            SELECT
                *,
                cum_amount - coalesce(lag(cum_amount) OVER (
                    PARTITION BY code ORDER BY minute_no
                ), 0) AS minute_amount
            FROM minute_snapshot
        )
        SELECT
            code,
            minute_no,
            minute_open,
            minute_high,
            minute_low,
            minute_close,
            greatest(minute_amount, 0)::DOUBLE AS minute_amount
        FROM delta
        WHERE minute_no BETWEEN 0 AND 239
    """


def build_minute_cache(settings: Settings) -> list[Path]:
    settings.minute_dir.mkdir(parents=True, exist_ok=True)
    dates = available_dates(settings)
    connection = duckdb.connect(str(settings.catalog), read_only=True)
    connection.execute(f"SET threads={settings.threads}")
    connection.execute(f"SET memory_limit='{settings.memory_limit}'")
    outputs: list[Path] = []
    started = time.time()
    try:
        for index, date in enumerate(dates, start=1):
            target = settings.minute_dir / f"{date}.parquet"
            outputs.append(target)
            if target.is_file() and not settings.overwrite:
                continue
            temporary = target.with_suffix(".parquet.tmp")
            if temporary.exists():
                temporary.unlink()
            query = minute_cache_query(date)
            connection.execute(
                f"COPY ({query}) TO '{sql_path(temporary)}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
            )
            os.replace(temporary, target)
            elapsed = time.time() - started
            print(
                f"一分钟缓存 {index}/{len(dates)} {date}，累计 {elapsed:.1f} 秒",
                flush=True,
            )
    finally:
        connection.close()
    return outputs


def collect_codes(paths: Sequence[Path]) -> list[str]:
    if not paths:
        return []
    connection = duckdb.connect()
    source = sql_path(paths[0].parent / "*.parquet")
    try:
        rows = connection.execute(
            f"SELECT DISTINCT code FROM read_parquet('{source}') ORDER BY code"
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def empty_matrix(rows: int, columns: int = 240) -> np.ndarray:
    return np.full((rows, columns), np.nan, dtype=np.float64)


def load_minute_arrays(path: Path, codes: Sequence[str]) -> dict[str, np.ndarray]:
    columns = [
        "code",
        "minute_no",
        "minute_open",
        "minute_high",
        "minute_low",
        "minute_close",
        "minute_amount",
    ]
    frame = pq.read_table(path, columns=columns).to_pandas()
    code_index = pd.Categorical(frame["code"], categories=codes).codes
    minute_index = frame["minute_no"].to_numpy(dtype=np.int16)
    valid = (code_index >= 0) & (minute_index >= 0) & (minute_index < 240)
    result: dict[str, np.ndarray] = {}
    for source, name in (
        ("minute_open", "open"),
        ("minute_high", "high"),
        ("minute_low", "low"),
        ("minute_close", "close"),
        ("minute_amount", "amount"),
    ):
        values = empty_matrix(len(codes))
        values[code_index[valid], minute_index[valid]] = pd.to_numeric(
            frame.loc[valid, source], errors="coerce"
        ).to_numpy(dtype=np.float64)
        result[name] = values
    return result


def minute_timestamp(date: pd.Timestamp, minute_no: int) -> pd.Timestamp:
    if minute_no < 120:
        return date.normalize() + pd.Timedelta(hours=9, minutes=30 + minute_no)
    return date.normalize() + pd.Timedelta(hours=13, minutes=minute_no - 120)


def safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full_like(numerator, np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=result, where=np.isfinite(denominator) & (denominator != 0))
    return result


class CandidateBuilder:
    def __init__(self, codes: Sequence[str], settings: Settings):
        self.codes = list(codes)
        self.settings = settings
        shape = (len(codes), 240)
        self.history: deque[np.ndarray] = deque()
        self.history_sum = np.zeros(shape, dtype=np.float64)
        self.history_square_sum = np.zeros(shape, dtype=np.float64)
        self.history_count = np.zeros(shape, dtype=np.int16)

    def history_stats(self) -> tuple[np.ndarray, np.ndarray]:
        count = self.history_count.astype(np.float64)
        mean = safe_ratio(self.history_sum, count)
        variance = safe_ratio(self.history_square_sum, count) - mean * mean
        standard_deviation = np.sqrt(np.maximum(variance, 0.0))
        mean[count < MIN_HISTORY_DAYS] = np.nan
        standard_deviation[count < MIN_HISTORY_DAYS] = np.nan
        return mean, standard_deviation

    def add_history(self, amount: np.ndarray) -> None:
        stored = amount.astype(np.float64, copy=True)
        valid = np.isfinite(stored)
        clean = np.where(valid, stored, 0.0)
        self.history.append(stored)
        self.history_sum += clean
        self.history_square_sum += clean * clean
        self.history_count += valid.astype(np.int16)
        if len(self.history) > HISTORY_DAYS:
            old = self.history.popleft()
            old_valid = np.isfinite(old)
            old_clean = np.where(old_valid, old, 0.0)
            self.history_sum -= old_clean
            self.history_square_sum -= old_clean * old_clean
            self.history_count -= old_valid.astype(np.int16)

    @staticmethod
    def _recent_median(amount: np.ndarray) -> np.ndarray:
        result = np.full_like(amount, np.nan, dtype=np.float64)
        for lower, upper in ((0, 120), (120, 240)):
            block = amount[:, lower:upper]
            padded = np.pad(block, ((0, 0), (20, 0)), constant_values=np.nan)
            windows = np.lib.stride_tricks.sliding_window_view(padded, 20, axis=1)
            with np.errstate(all="ignore"):
                result[:, lower:upper] = np.nanmedian(windows[:, : block.shape[1], :], axis=2)
        return result

    @staticmethod
    def _future_return(
        minute_open: np.ndarray,
        minute_close: np.ndarray,
        trigger: int,
        holding: int,
        upper: int,
    ) -> float:
        entry = trigger + 1
        exit_position = trigger + holding
        if entry >= upper or exit_position >= upper:
            return math.nan
        entry_price = minute_open[entry]
        exit_price = minute_close[exit_position]
        if not np.isfinite(entry_price) or not np.isfinite(exit_price) or entry_price <= 0:
            return math.nan
        return float(exit_price / entry_price - 1.0)

    def process_day(self, date: pd.Timestamp, arrays: dict[str, np.ndarray]) -> list[dict[str, object]]:
        amount = arrays["amount"]
        minute_open = arrays["open"]
        minute_high = arrays["high"]
        minute_low = arrays["low"]
        minute_close = arrays["close"]
        history_mean, history_std = self.history_stats()
        with np.errstate(divide="ignore", invalid="ignore"):
            volume_z = (amount - history_mean) / history_std
            minute_return = minute_close / minute_open - 1.0
            close_position = (minute_close - minute_low) / (minute_high - minute_low)
        flat_bar = np.isfinite(minute_close) & np.isfinite(minute_high) & (minute_high == minute_low)
        close_position[flat_bar] = 0.5
        recent_median = self._recent_median(amount)
        recent_ratio = safe_ratio(amount, recent_median)
        clock_ratio = safe_ratio(amount, history_mean)

        prior_amount = np.cumsum(np.nan_to_num(amount, nan=0.0), axis=1) - np.nan_to_num(amount, nan=0.0)
        prior_expected = np.cumsum(np.nan_to_num(history_mean, nan=0.0), axis=1) - np.nan_to_num(history_mean, nan=0.0)
        typical = np.nanmedian(history_mean, axis=1, keepdims=True)
        typical[~np.isfinite(typical)] = 0.0
        smoothing = 10.0 * typical
        day_scale = safe_ratio(prior_amount + smoothing, prior_expected + smoothing)
        day_scale = np.clip(day_scale, 0.5, 2.5)
        scaled_expected = history_mean * day_scale
        scaled_clock_ratio = safe_ratio(amount, scaled_expected)

        is_spike = (
            np.isfinite(volume_z)
            & (volume_z > self.settings.spike_z)
            & np.isfinite(amount)
            & (amount >= self.settings.min_minute_amount)
        )
        market_return = np.nanmean(minute_return, axis=0)
        market_up_share = np.nanmean(minute_return > 0, axis=0)
        market_spike_share = np.nanmean(is_spike, axis=0)
        records: list[dict[str, object]] = []

        for code_index, code in enumerate(self.codes):
            returns = minute_return[code_index]
            closes = minute_close[code_index]
            opens = minute_open[code_index]
            if np.isfinite(closes).sum() < 200:
                continue
            for lower, upper in ((0, 120), (120, 240)):
                spikes = np.flatnonzero(is_spike[code_index, lower:upper]) + lower
                if len(spikes) < 5:
                    continue
                for spike_index in range(4, len(spikes)):
                    past = spikes[spike_index - 4 : spike_index + 1].astype(int)
                    gaps = np.diff(past)
                    median_gap = float(np.median(gaps))
                    if not (2 <= median_gap <= 10):
                        continue
                    if float(np.median(np.abs(gaps - median_gap))) > 1e-12:
                        continue
                    period = int(np.rint(median_gap))
                    current = int(past[-1])
                    predicted = current + period
                    trigger = predicted - 2
                    if trigger < current or predicted + 1 >= upper or trigger + 1 >= upper:
                        continue

                    future_spikes = spikes[spikes > current]
                    next_spike = int(future_spikes[0]) if len(future_spikes) else -999
                    volume_hit = abs(next_spike - predicted) <= 1
                    next_up = bool(next_spike >= 0 and np.isfinite(returns[next_spike]) and returns[next_spike] > 0)
                    no_early_spike = not bool(
                        np.any(is_spike[code_index, current + 1 : trigger + 1])
                    )
                    pre_window_up = bool(np.isfinite(returns[trigger]) and returns[trigger] > 0)
                    pulse_returns = returns[past]
                    pulse_amounts = amount[code_index, past]
                    pulse_z = volume_z[code_index, past]
                    pulse_closes = closes[past]
                    pulse_close_positions = close_position[code_index, past]
                    quiet_positions = np.setdiff1d(
                        np.arange(int(past[0]), current + 1), past, assume_unique=True
                    )
                    quiet_returns = returns[quiet_positions]
                    pulse_abs_mean = float(np.nanmean(np.abs(pulse_returns)))
                    return_stability = float(
                        np.nanstd(pulse_returns) / max(pulse_abs_mean, 1e-8)
                    )
                    amount_mean = float(np.nanmean(pulse_amounts))
                    amount_cv = float(np.nanstd(pulse_amounts) / max(amount_mean, 1.0))
                    z_mean = float(np.nanmean(pulse_z))
                    z_cv = float(np.nanstd(pulse_z) / max(abs(z_mean), 1e-8))
                    quiet_net = float(np.nansum(quiet_returns))
                    quiet_abs_mean = float(np.nanmean(np.abs(quiet_returns))) if len(quiet_positions) else 0.0
                    pulse_return_sum = float(np.nansum(pulse_returns))
                    total_return_sum = pulse_return_sum + quiet_net
                    rise_concentration = (
                        pulse_return_sum / total_return_sum
                        if total_return_sum > 1e-8
                        else math.nan
                    )
                    next_position = next_spike if volume_hit else predicted
                    target_ret = (
                        float(returns[next_position])
                        if 0 <= next_position < upper and np.isfinite(returns[next_position])
                        else math.nan
                    )
                    record: dict[str, object] = {
                        "candidate_id": f"{date:%Y%m%d}_{code}_{current:03d}",
                        "date": date.normalize(),
                        "code": code,
                        "session_no": 0 if current < 120 else 1,
                        "period": period,
                        "gap_min": int(gaps.min()),
                        "gap_max": int(gaps.max()),
                        "current_position": current,
                        "trigger_position": trigger,
                        "predicted_position": predicted,
                        "next_spike_position": next_spike,
                        "trigger_ts": minute_timestamp(date, trigger),
                        "predicted_ts": minute_timestamp(date, predicted),
                        "volume_hit": bool(volume_hit),
                        "next_up": bool(next_up),
                        "joint_minute_hit": bool(volume_hit and next_up),
                        "target_minute_return": target_ret,
                        "no_early_spike": bool(no_early_spike),
                        "pre_window_up": bool(pre_window_up),
                        "past_up_count": int(np.sum(pulse_returns > 0)),
                        "step_up_count": int(np.sum(np.diff(pulse_closes) > 0)),
                        "return_stability": return_stability,
                        "amount_cv": amount_cv,
                        "volume_z_mean": z_mean,
                        "volume_z_cv": z_cv,
                        "recent_ratio_median": float(np.nanmedian(recent_ratio[code_index, past])),
                        "clock_ratio_median": float(np.nanmedian(clock_ratio[code_index, past])),
                        "scaled_clock_ratio_median": float(np.nanmedian(scaled_clock_ratio[code_index, past])),
                        "pulse_close_position_mean": float(np.nanmean(pulse_close_positions)),
                        "pulse_return_sum": pulse_return_sum,
                        "quiet_net_return": quiet_net,
                        "quiet_abs_mean": quiet_abs_mean,
                        "rise_concentration": rise_concentration,
                        "market_up_pulses": int(np.sum(market_return[past] > 0)),
                        "pulse_excess_mean": float(np.nanmean(pulse_returns - market_return[past])),
                        "market_return_at_trigger": float(market_return[trigger]),
                        "market_up_share_at_trigger": float(market_up_share[trigger]),
                        "market_spike_share_at_trigger": float(market_spike_share[trigger]),
                        "forward_5m": self._future_return(opens, closes, trigger, 5, upper),
                        "forward_10m": self._future_return(opens, closes, trigger, 10, upper),
                        "forward_20m": self._future_return(opens, closes, trigger, 20, upper),
                    }
                    for number, position in enumerate(past, start=1):
                        record[f"pulse{number}_position"] = int(position)
                        record[f"pulse{number}_ts"] = minute_timestamp(date, int(position))
                        record[f"pulse{number}_return"] = float(returns[position])
                        record[f"pulse{number}_amount"] = float(amount[code_index, position])
                    records.append(record)

        self.add_history(amount)
        return records


def build_minute_candidates(settings: Settings) -> pd.DataFrame:
    paths = sorted(settings.minute_dir.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"没有一分钟缓存：{settings.minute_dir}")
    codes = collect_codes(paths)
    builder = CandidateBuilder(codes, settings)
    daily_dir = settings.output_dir / "minute_candidate_daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    started = time.time()
    for index, path in enumerate(paths, start=1):
        date = pd.Timestamp(path.stem)
        arrays = load_minute_arrays(path, codes)
        records = builder.process_day(date, arrays)
        daily = pd.DataFrame(records)
        daily_path = daily_dir / path.name
        if daily.empty:
            pd.DataFrame(columns=["candidate_id", "date", "code"]).to_parquet(
                daily_path, index=False
            )
        else:
            daily.to_parquet(daily_path, index=False)
            frames.append(daily)
        print(
            f"分钟候选 {index}/{len(paths)} {path.stem}：{len(daily)} 次，累计 {time.time()-started:.1f} 秒",
            flush=True,
        )
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result.to_parquet(settings.candidate_path, index=False)
    return result


def select_level2_pool(candidates: pd.DataFrame) -> pd.DataFrame:
    """分钟规则先固定，再比较不同逐笔条件。"""

    if candidates.empty:
        return candidates.copy()
    mask = (
        candidates["past_up_count"].eq(5)
        & candidates["return_stability"].le(RETURN_STABILITY_LIMIT)
        & candidates["no_early_spike"].fillna(False)
        & candidates["pre_window_up"].fillna(False)
        & candidates["gap_max"].le(candidates["period"] * 3)
    )
    return candidates.loc[mask].copy().sort_values(["date", "code", "trigger_ts"])


def make_window_id(date: pd.Timestamp, code: str, position: int) -> str:
    return f"{date:%Y%m%d}_{code}_{int(position):03d}"


def add_window_ids(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    dates = pd.to_datetime(result["date"])
    for number in range(1, 6):
        positions = result[f"pulse{number}_position"].astype(int)
        result[f"pulse{number}_window_id"] = [
            make_window_id(date, code, position)
            for date, code, position in zip(dates, result["code"], positions)
        ]
    target_positions = np.where(
        result["volume_hit"].to_numpy(bool),
        result["next_spike_position"].to_numpy(int),
        result["predicted_position"].to_numpy(int),
    )
    result["target_position"] = target_positions
    result["target_window_id"] = [
        make_window_id(date, code, int(position))
        for date, code, position in zip(dates, result["code"], target_positions)
    ]
    result["trigger_window_id"] = [
        make_window_id(date, code, int(position))
        for date, code, position in zip(
            dates, result["code"], result["trigger_position"].astype(int)
        )
    ]
    return result


def candidate_windows(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        date = pd.Timestamp(row.date)
        for number in range(1, 6):
            position = int(getattr(row, f"pulse{number}_position"))
            start = minute_timestamp(date, position)
            rows.append(
                {
                    "window_id": make_window_id(date, row.code, position),
                    "code": row.code,
                    "start_ts": start,
                    "end_ts": start + pd.Timedelta(minutes=1),
                }
            )
        for position in (int(row.target_position), int(row.trigger_position)):
            start = minute_timestamp(date, position)
            rows.append(
                {
                    "window_id": make_window_id(date, row.code, position),
                    "code": row.code,
                    "start_ts": start,
                    "end_ts": start + pd.Timedelta(minutes=1),
                }
            )
    return pd.DataFrame(rows).drop_duplicates("window_id").reset_index(drop=True)


TRADE_WINDOW_SQL = """
    WITH trade_summary AS (
        SELECT
            window_id,
            sum(trade_amount) AS trade_amount,
            sum(trade_amount) FILTER (WHERE bs_flag='B') AS active_buy_amount,
            sum(trade_amount) FILTER (WHERE bs_flag='S') AS active_sell_amount,
            count(*) AS trade_count,
            count(*) FILTER (WHERE bs_flag='B') AS active_buy_trade_count,
            arg_min(trade_price, event_ts) AS first_trade_price,
            arg_max(trade_price, event_ts) AS last_trade_price
        FROM matched_trades
        GROUP BY window_id
    ), per_buy_order AS MATERIALIZED (
        SELECT
            window_id,
            bid_order_id AS order_id,
            sum(trade_amount) AS order_amount,
            sum(trade_quantity)::DOUBLE AS order_quantity,
            count(*) AS fill_count,
            min(event_ts) AS first_ts,
            max(event_ts) AS last_ts,
            datediff('millisecond', min(event_ts), max(event_ts)) / 1000.0 AS duration_seconds,
            arg_min(trade_price, event_ts) AS first_price,
            arg_max(trade_price, event_ts) AS last_price
        FROM matched_trades
        WHERE bs_flag='B' AND bid_order_id > 0
        GROUP BY window_id, bid_order_id
    ), ranked AS (
        SELECT *, row_number() OVER (
            PARTITION BY window_id ORDER BY order_amount DESC, order_id
        ) AS amount_rank
        FROM per_buy_order
    ), order_stats AS (
        SELECT
            window_id,
            count(*) AS buy_order_count,
            sum(order_amount) AS grouped_buy_amount,
            quantile_cont(order_amount, 0.25) AS buy_order_q25,
            quantile_cont(order_amount, 0.50) AS buy_order_median,
            quantile_cont(order_amount, 0.75) AS buy_order_q75,
            max(order_amount) AS largest_buy_order,
            sum(order_amount) FILTER (WHERE amount_rank <= 3) AS top3_buy_order_amount,
            max(order_amount) FILTER (WHERE amount_rank = 1) AS rank1_buy_order_amount,
            max(order_amount) FILTER (WHERE amount_rank = 2) AS rank2_buy_order_amount,
            max(order_amount) FILTER (WHERE amount_rank = 3) AS rank3_buy_order_amount,
            max(order_amount) FILTER (WHERE amount_rank = 4) AS rank4_buy_order_amount,
            max(order_amount) FILTER (WHERE amount_rank = 5) AS rank5_buy_order_amount,
            max(fill_count) FILTER (WHERE amount_rank = 1) AS largest_buy_order_fill_count,
            sum(order_amount * order_amount) / nullif(pow(sum(order_amount), 2), 0) AS buy_order_hhi,
            stddev_pop(ln(greatest(order_amount, 0.01))) AS buy_order_log_std,
            quantile_cont(fill_count, 0.50) AS buy_order_fill_median,
            quantile_cont(duration_seconds, 0.50) AS buy_order_duration_median,
            arg_min(first_price, first_ts) AS first_active_buy_price,
            arg_max(last_price, last_ts) AS last_active_buy_price
        FROM ranked
        GROUP BY window_id
    ), order_gaps AS (
        SELECT
            window_id,
            epoch_ms(first_ts) - lag(epoch_ms(first_ts)) OVER (
                PARTITION BY window_id ORDER BY first_ts, order_id
            ) AS gap_ms
        FROM per_buy_order
    ), gap_median AS (
        SELECT window_id, quantile_cont(gap_ms, 0.50) FILTER (WHERE gap_ms > 0) AS median_gap_ms
        FROM order_gaps
        GROUP BY window_id
    ), gap_stats AS (
        SELECT
            g.window_id,
            avg(g.gap_ms) FILTER (WHERE g.gap_ms > 0) AS mean_gap_ms,
            stddev_pop(g.gap_ms) FILTER (WHERE g.gap_ms > 0) AS std_gap_ms,
            avg(CASE
                WHEN g.gap_ms > 0 AND m.median_gap_ms > 0
                 AND g.gap_ms BETWEEN 0.5*m.median_gap_ms AND 1.5*m.median_gap_ms
                THEN 1.0
                WHEN g.gap_ms > 0 THEN 0.0
                ELSE NULL
            END) AS regular_gap_share
        FROM order_gaps g
        JOIN gap_median m USING(window_id)
        GROUP BY g.window_id
    ), quantity_counts AS (
        SELECT
            window_id,
            order_quantity,
            count(*) AS order_count,
            sum(order_amount) AS amount
        FROM per_buy_order
        GROUP BY window_id, order_quantity
    ), quantity_stats AS (
        SELECT
            window_id,
            max(order_count)::DOUBLE / nullif(sum(order_count), 0) AS repeated_quantity_count_share,
            max(amount) / nullif(sum(amount), 0) AS repeated_quantity_amount_share
        FROM quantity_counts
        GROUP BY window_id
    )
    SELECT
        w.window_id,
        coalesce(t.trade_amount, 0) AS trade_amount,
        coalesce(t.active_buy_amount, 0) AS active_buy_amount,
        coalesce(t.active_sell_amount, 0) AS active_sell_amount,
        t.active_buy_amount / nullif(t.trade_amount, 0) AS active_buy_share,
        (t.active_buy_amount - t.active_sell_amount)
            / nullif(t.active_buy_amount + t.active_sell_amount, 0) AS active_net_buy_share,
        t.trade_count,
        t.active_buy_trade_count,
        o.buy_order_count,
        o.buy_order_q25,
        o.buy_order_median,
        o.buy_order_q75,
        o.largest_buy_order,
        o.largest_buy_order / nullif(o.grouped_buy_amount, 0) AS largest_buy_order_share,
        o.top3_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS top3_buy_order_share,
        o.rank1_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS buy_order_share1,
        o.rank2_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS buy_order_share2,
        o.rank3_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS buy_order_share3,
        o.rank4_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS buy_order_share4,
        o.rank5_buy_order_amount / nullif(o.grouped_buy_amount, 0) AS buy_order_share5,
        o.largest_buy_order_fill_count,
        o.buy_order_hhi,
        1.0 / nullif(o.buy_order_hhi, 0) AS effective_buy_order_count,
        o.buy_order_log_std,
        o.buy_order_fill_median,
        o.buy_order_duration_median,
        o.last_active_buy_price / nullif(o.first_active_buy_price, 0) - 1 AS active_buy_price_move,
        g.std_gap_ms / nullif(g.mean_gap_ms, 0) AS buy_order_gap_cv,
        g.regular_gap_share AS buy_order_gap_regular_share,
        q.repeated_quantity_count_share,
        q.repeated_quantity_amount_share,
        p.peak_second_ts,
        p.peak_second_buy_amount,
        p.peak_second_buy_amount / nullif(t.active_buy_amount, 0) AS peak_second_buy_share,
        p.peak_second_buy_order_count,
        p.peak_second_largest_order,
        p.peak_second_largest_order_share,
        p.peak_second_top3_order_share,
        p.peak_second_effective_order_count,
        p.peak_second_largest_order_fill_count,
        p.peak_second_order_share1,
        p.peak_second_order_share2,
        p.peak_second_order_share3,
        p.peak_second_order_share4,
        p.peak_second_order_share5,
        t.last_trade_price / nullif(t.first_trade_price, 0) - 1 AS trade_price_move
    FROM window_input w
    LEFT JOIN trade_summary t USING(window_id)
    LEFT JOIN order_stats o USING(window_id)
    LEFT JOIN gap_stats g USING(window_id)
    LEFT JOIN quantity_stats q USING(window_id)
    LEFT JOIN peak_second_stats p USING(window_id)
"""


PEAK_SECOND_SQL = """
    CREATE OR REPLACE TEMP TABLE peak_second_stats AS
    WITH per_order_second AS (
        SELECT
            window_id,
            date_trunc('second', event_ts) AS second_ts,
            bid_order_id AS order_id,
            sum(trade_amount) AS order_amount,
            count(*) AS fill_count
        FROM matched_trades
        WHERE bs_flag='B' AND bid_order_id > 0
        GROUP BY window_id, second_ts, bid_order_id
    ), ranked_orders AS (
        SELECT *, row_number() OVER (
            PARTITION BY window_id, second_ts ORDER BY order_amount DESC, order_id
        ) AS amount_rank
        FROM per_order_second
    ), per_second AS (
        SELECT
            window_id,
            second_ts,
            sum(order_amount) AS second_buy_amount,
            count(*) AS second_buy_order_count,
            max(order_amount) AS largest_order,
            sum(order_amount) FILTER (WHERE amount_rank <= 3) AS top3_order_amount,
            max(fill_count) FILTER (WHERE amount_rank = 1) AS largest_order_fill_count,
            sum(order_amount * order_amount) / nullif(pow(sum(order_amount), 2), 0) AS order_hhi,
            max(order_amount) FILTER (WHERE amount_rank = 1) AS rank1_amount,
            max(order_amount) FILTER (WHERE amount_rank = 2) AS rank2_amount,
            max(order_amount) FILTER (WHERE amount_rank = 3) AS rank3_amount,
            max(order_amount) FILTER (WHERE amount_rank = 4) AS rank4_amount,
            max(order_amount) FILTER (WHERE amount_rank = 5) AS rank5_amount
        FROM ranked_orders
        GROUP BY window_id, second_ts
    ), ranked_seconds AS (
        SELECT *, row_number() OVER (
            PARTITION BY window_id ORDER BY largest_order DESC, second_buy_amount DESC, second_ts
        ) AS second_rank
        FROM per_second
    )
    SELECT
        window_id,
        second_ts AS peak_second_ts,
        second_buy_amount AS peak_second_buy_amount,
        second_buy_order_count AS peak_second_buy_order_count,
        largest_order AS peak_second_largest_order,
        largest_order / nullif(second_buy_amount, 0) AS peak_second_largest_order_share,
        top3_order_amount / nullif(second_buy_amount, 0) AS peak_second_top3_order_share,
        1.0 / nullif(order_hhi, 0) AS peak_second_effective_order_count,
        largest_order_fill_count AS peak_second_largest_order_fill_count,
        rank1_amount / nullif(second_buy_amount, 0) AS peak_second_order_share1,
        rank2_amount / nullif(second_buy_amount, 0) AS peak_second_order_share2,
        rank3_amount / nullif(second_buy_amount, 0) AS peak_second_order_share3,
        rank4_amount / nullif(second_buy_amount, 0) AS peak_second_order_share4,
        rank5_amount / nullif(second_buy_amount, 0) AS peak_second_order_share5
    FROM ranked_seconds
    WHERE second_rank=1
"""


QUOTE_WINDOW_SQL = """
    WITH valid_quotes AS (
        SELECT
            w.window_id,
            w.start_ts,
            w.end_ts,
            q.event_ts,
            (q.ask_price_1 + q.bid_price_1) / 2.0 AS mid_price,
            q.ask_price_1,
            q.ask_volume_1,
            q.bid_volume_1
        FROM day_quotes q
        JOIN window_input w
          ON q.code=w.code
         AND q.event_ts >= w.start_ts - INTERVAL '6 seconds'
         AND q.event_ts < w.end_ts
        WHERE q.ask_price_1 > 0 AND q.bid_price_1 > 0
    ), summary AS (
        SELECT
            window_id,
            arg_max(mid_price, event_ts) FILTER (WHERE event_ts < start_ts) AS mid_before,
            arg_min(mid_price, abs(epoch_ms(event_ts)-epoch_ms(start_ts+INTERVAL '30 seconds'))) AS mid_30s,
            arg_max(mid_price, event_ts) FILTER (WHERE event_ts >= start_ts) AS mid_end,
            arg_max(ask_price_1, event_ts) FILTER (WHERE event_ts < start_ts) AS ask_price_before,
            max(ask_price_1) FILTER (WHERE event_ts >= start_ts) AS max_ask_price,
            arg_max(ask_volume_1, event_ts) FILTER (WHERE event_ts < start_ts) AS ask_volume_before,
            min(ask_volume_1) FILTER (WHERE event_ts >= start_ts) AS min_ask_volume,
            arg_max(bid_volume_1, event_ts) FILTER (WHERE event_ts < start_ts) AS bid_volume_before
        FROM valid_quotes
        GROUP BY window_id
    )
    SELECT
        window_id,
        mid_30s / nullif(mid_before, 0) - 1 AS mid_lift_30s,
        mid_end / nullif(mid_before, 0) - 1 AS mid_lift_end,
        max_ask_price / nullif(ask_price_before, 0) - 1 AS ask_price_lift,
        CASE
            WHEN max_ask_price > ask_price_before THEN 1.0
            ELSE 1.0 - min_ask_volume / nullif(ask_volume_before, 0)
        END AS ask_depth_consumed,
        bid_volume_before / nullif(ask_volume_before, 0) AS book_bid_ask_ratio
    FROM summary
"""


PEAK_QUOTE_SQL = """
    WITH snapshots AS (
        SELECT
            p.window_id,
            p.peak_second_ts,
            q.event_ts,
            (q.ask_price_1 + q.bid_price_1) / 2.0 AS mid_price,
            q.ask_price_1,
            q.ask_volume_1
        FROM peak_second_stats p
        JOIN window_input w USING(window_id)
        JOIN day_quotes q
          ON q.code=w.code
         AND q.event_ts >= p.peak_second_ts - INTERVAL '6 seconds'
         AND q.event_ts <= p.peak_second_ts + INTERVAL '6 seconds'
        WHERE q.ask_price_1 > 0 AND q.bid_price_1 > 0
    ), summary AS (
        SELECT
            window_id,
            arg_max(mid_price, event_ts) FILTER (WHERE event_ts < peak_second_ts) AS mid_before,
            arg_min(mid_price, abs(epoch_ms(event_ts)-epoch_ms(peak_second_ts+INTERVAL '3 seconds'))) AS mid_after_3s,
            arg_max(ask_price_1, event_ts) FILTER (WHERE event_ts < peak_second_ts) AS ask_price_before,
            max(ask_price_1) FILTER (
                WHERE event_ts >= peak_second_ts AND event_ts <= peak_second_ts+INTERVAL '3 seconds'
            ) AS max_ask_price_3s,
            arg_max(ask_volume_1, event_ts) FILTER (WHERE event_ts < peak_second_ts) AS ask_volume_before,
            min(ask_volume_1) FILTER (
                WHERE event_ts >= peak_second_ts AND event_ts <= peak_second_ts+INTERVAL '3 seconds'
            ) AS min_ask_volume_3s
        FROM snapshots
        GROUP BY window_id
    )
    SELECT
        window_id,
        mid_after_3s / nullif(mid_before, 0) - 1 AS peak_mid_lift_3s,
        max_ask_price_3s / nullif(ask_price_before, 0) - 1 AS peak_ask_price_lift_3s,
        CASE
            WHEN max_ask_price_3s > ask_price_before THEN 1.0
            ELSE 1.0 - min_ask_volume_3s / nullif(ask_volume_before, 0)
        END AS peak_ask_depth_consumed_3s
    FROM summary
"""


RAW_FILE_PATTERN = re.compile(
    r"part-\d+-(\d{6})_(SZ|SH)-(\d{6})_(SZ|SH)\.parquet$",
    re.IGNORECASE,
)


def files_for_codes(root: Path, kind: str, date: str, codes: Iterable[str]) -> list[Path]:
    numbers = sorted({int(str(code)[-6:]) for code in codes})
    directory = root / kind / f"trade_date={date}"
    selected: list[Path] = []
    for path in sorted(directory.glob("*.parquet")):
        match = RAW_FILE_PATTERN.match(path.name)
        if match is None:
            selected.append(path)
            continue
        lower = int(match.group(1))
        upper = int(match.group(3))
        if any(lower <= number <= upper for number in numbers):
            selected.append(path)
    if not selected:
        raise FileNotFoundError(f"{date} 没有找到候选股票对应的 {kind} 文件")
    return selected


def parquet_source(paths: Sequence[Path]) -> str:
    quoted = ",".join(f"'{sql_path(path)}'" for path in paths)
    return f"read_parquet([{quoted}])"


def query_window_features(
    connection: duckdb.DuckDBPyConnection,
    date: str,
    windows: pd.DataFrame,
    l2_root: Path,
) -> pd.DataFrame:
    if windows.empty:
        return pd.DataFrame(columns=["window_id"])
    connection.register("window_frame", windows)
    connection.execute("CREATE OR REPLACE TEMP TABLE window_input AS SELECT * FROM window_frame")
    connection.unregister("window_frame")
    codes = windows["code"].drop_duplicates().astype(str).tolist()
    code_filter = ",".join("'" + code.replace("'", "''") + "'" for code in codes)
    earliest = pd.Timestamp(windows["start_ts"].min()) - pd.Timedelta(seconds=6)
    latest = pd.Timestamp(windows["end_ts"].max())
    time_filter = (
        f"event_ts >= TIMESTAMP '{earliest:%Y-%m-%d %H:%M:%S}' "
        f"AND event_ts < TIMESTAMP '{latest:%Y-%m-%d %H:%M:%S}'"
    )
    trade_files = files_for_codes(l2_root, "trades", date, codes)
    quote_files = files_for_codes(l2_root, "quotes", date, codes)
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW day_trades AS
        SELECT
            code,
            event_ts,
            bs_flag,
            trade_price_x1e4 / 10000.0 AS trade_price,
            trade_quantity,
            trade_price_x1e4::DOUBLE * trade_quantity / 10000.0 AS trade_amount,
            bid_order_id,
            ask_order_id
        FROM {parquet_source(trade_files)}
        WHERE code IN ({code_filter}) AND {time_filter}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW day_quotes AS
        SELECT
            code,
            event_ts,
            ask_price_1_x1e4 / 10000.0 AS ask_price_1,
            bid_price_1_x1e4 / 10000.0 AS bid_price_1,
            ask_volume_1,
            bid_volume_1
        FROM {parquet_source(quote_files)}
        WHERE code IN ({code_filter}) AND {time_filter}
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE TEMP TABLE matched_trades AS
        SELECT
            w.window_id,
            t.event_ts,
            t.bs_flag,
            t.trade_amount,
            t.trade_quantity,
            t.trade_price,
            t.bid_order_id,
            t.ask_order_id
        FROM day_trades t
        JOIN window_input w
          ON t.code=w.code
         AND t.event_ts >= w.start_ts
         AND t.event_ts < w.end_ts
        WHERE t.trade_price > 0
          AND t.trade_quantity > 0
        """
    )
    connection.execute(PEAK_SECOND_SQL)
    trades = connection.execute(TRADE_WINDOW_SQL).fetchdf()
    quotes = connection.execute(QUOTE_WINDOW_SQL).fetchdf()
    peak_quotes = connection.execute(PEAK_QUOTE_SQL).fetchdf()
    result = trades.merge(quotes, on="window_id", how="left").merge(
        peak_quotes, on="window_id", how="left"
    )
    return result


WINDOW_FEATURES = [
    "trade_amount",
    "active_buy_amount",
    "active_sell_amount",
    "active_buy_share",
    "active_net_buy_share",
    "trade_count",
    "active_buy_trade_count",
    "buy_order_count",
    "buy_order_q25",
    "buy_order_median",
    "buy_order_q75",
    "largest_buy_order",
    "largest_buy_order_share",
    "top3_buy_order_share",
    "buy_order_share1",
    "buy_order_share2",
    "buy_order_share3",
    "buy_order_share4",
    "buy_order_share5",
    "largest_buy_order_fill_count",
    "buy_order_hhi",
    "effective_buy_order_count",
    "buy_order_log_std",
    "buy_order_fill_median",
    "buy_order_duration_median",
    "active_buy_price_move",
    "buy_order_gap_cv",
    "buy_order_gap_regular_share",
    "repeated_quantity_count_share",
    "repeated_quantity_amount_share",
    "peak_second_buy_amount",
    "peak_second_buy_share",
    "peak_second_buy_order_count",
    "peak_second_largest_order",
    "peak_second_largest_order_share",
    "peak_second_top3_order_share",
    "peak_second_effective_order_count",
    "peak_second_largest_order_fill_count",
    "peak_second_order_share1",
    "peak_second_order_share2",
    "peak_second_order_share3",
    "peak_second_order_share4",
    "peak_second_order_share5",
    "trade_price_move",
    "mid_lift_30s",
    "mid_lift_end",
    "ask_price_lift",
    "ask_depth_consumed",
    "book_bid_ask_ratio",
    "peak_mid_lift_3s",
    "peak_ask_price_lift_3s",
    "peak_ask_depth_consumed_3s",
]


def coefficient_of_variation(values: np.ndarray) -> np.ndarray:
    mean = np.nanmean(values, axis=1)
    deviation = np.nanstd(values, axis=1)
    result = np.full(len(mean), np.nan)
    np.divide(deviation, np.abs(mean), out=result, where=np.isfinite(mean) & (np.abs(mean) > 1e-12))
    return result


def add_level2_aggregates(
    candidates: pd.DataFrame,
    window_features: pd.DataFrame,
) -> pd.DataFrame:
    result = candidates.copy()
    lookup = window_features.drop_duplicates("window_id").set_index("window_id")
    phases = [f"pulse{number}" for number in range(1, 6)] + ["target", "trigger"]
    mapped_columns: dict[str, pd.Series] = {}
    for phase in phases:
        ids = result[f"{phase}_window_id"]
        for feature in WINDOW_FEATURES:
            if feature not in lookup.columns:
                mapped_columns[f"{phase}_{feature}"] = pd.Series(np.nan, index=result.index)
            else:
                mapped_columns[f"{phase}_{feature}"] = ids.map(lookup[feature])
    result = pd.concat([result, pd.DataFrame(mapped_columns, index=result.index)], axis=1).copy()

    def pulse_values(feature: str) -> np.ndarray:
        return result[[f"pulse{i}_{feature}" for i in range(1, 6)]].to_numpy(float)

    pulse_trade = pulse_values("trade_amount")
    pulse_buy = pulse_values("active_buy_amount")
    pulse_sell = pulse_values("active_sell_amount")
    total_trade = np.nansum(pulse_trade, axis=1)
    total_buy = np.nansum(pulse_buy, axis=1)
    total_sell = np.nansum(pulse_sell, axis=1)
    result["pulse_combined_buy_share"] = np.divide(
        total_buy,
        total_trade,
        out=np.full(len(result), np.nan),
        where=total_trade > 0,
    )
    result["pulse_combined_net_buy_share"] = np.divide(
        total_buy - total_sell,
        total_buy + total_sell,
        out=np.full(len(result), np.nan),
        where=(total_buy + total_sell) > 0,
    )
    buy_shares = pulse_values("active_buy_share")
    result["pulse_buy_share_min"] = np.nanmin(buy_shares, axis=1)
    result["pulse_buy_share_median"] = np.nanmedian(buy_shares, axis=1)
    result["pulse_buy_share_ge60_count"] = np.sum(buy_shares >= 0.60, axis=1)
    result["pulse_buy_share_ge70_count"] = np.sum(buy_shares >= 0.70, axis=1)
    result["pulse_active_buy_amount_cv"] = coefficient_of_variation(pulse_buy)
    pulse_buy_min = np.nanmin(pulse_buy, axis=1)
    pulse_buy_max = np.nanmax(pulse_buy, axis=1)
    result["pulse_active_buy_amount_max_min"] = np.divide(
        pulse_buy_max,
        pulse_buy_min,
        out=np.full(len(result), np.nan),
        where=pulse_buy_min > 0,
    )
    order_medians = pulse_values("buy_order_median")
    result["pulse_order_median_cv"] = coefficient_of_variation(order_medians)
    quantiles = np.stack(
        [
            pulse_values("buy_order_q25"),
            pulse_values("buy_order_median"),
            pulse_values("buy_order_q75"),
        ],
        axis=2,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        result["pulse_order_distribution_log_spread"] = np.nanmean(
            np.nanstd(np.log(np.where(quantiles > 0, quantiles, np.nan)), axis=1),
            axis=1,
        )
    result["pulse_top1_share_cv"] = coefficient_of_variation(
        pulse_values("largest_buy_order_share")
    )
    dominant_amounts = pulse_values("largest_buy_order")
    result["pulse_dominant_order_amount_cv"] = coefficient_of_variation(dominant_amounts)
    dominant_min = np.nanmin(dominant_amounts, axis=1)
    dominant_max = np.nanmax(dominant_amounts, axis=1)
    result["pulse_dominant_order_amount_max_min"] = np.divide(
        dominant_max,
        dominant_min,
        out=np.full(len(result), np.nan),
        where=dominant_min > 0,
    )
    result["pulse_largest_order_min"] = dominant_min
    result["pulse_largest_order_share_min"] = np.nanmin(
        pulse_values("largest_buy_order_share"), axis=1
    )
    result["pulse_top3_order_share_min"] = np.nanmin(
        pulse_values("top3_buy_order_share"), axis=1
    )
    result["pulse_largest_order_fill_min"] = np.nanmin(
        pulse_values("largest_buy_order_fill_count"), axis=1
    )
    result["pulse_effective_order_count_median"] = np.nanmedian(
        pulse_values("effective_buy_order_count"), axis=1
    )
    order_shapes = np.stack(
        [pulse_values(f"buy_order_share{rank}") for rank in range(1, 6)], axis=2
    )
    pair_cosines: list[np.ndarray] = []
    for left in range(5):
        for right in range(left + 1, 5):
            a = order_shapes[:, left, :]
            b = order_shapes[:, right, :]
            numerator = np.nansum(a * b, axis=1)
            denominator = np.sqrt(np.nansum(a * a, axis=1) * np.nansum(b * b, axis=1))
            pair_cosines.append(
                np.divide(
                    numerator,
                    denominator,
                    out=np.full(len(result), np.nan),
                    where=denominator > 0,
                )
            )
    result["pulse_order_shape_similarity"] = np.nanmean(
        np.stack(pair_cosines, axis=1), axis=1
    )
    peak_amounts = pulse_values("peak_second_buy_amount")
    peak_min = np.nanmin(peak_amounts, axis=1)
    peak_max = np.nanmax(peak_amounts, axis=1)
    result["pulse_peak_second_amount_max_min"] = np.divide(
        peak_max,
        peak_min,
        out=np.full(len(result), np.nan),
        where=peak_min > 0,
    )
    peak_dominant = pulse_values("peak_second_largest_order")
    peak_dominant_min = np.nanmin(peak_dominant, axis=1)
    peak_dominant_max = np.nanmax(peak_dominant, axis=1)
    result["pulse_peak_dominant_amount_max_min"] = np.divide(
        peak_dominant_max,
        peak_dominant_min,
        out=np.full(len(result), np.nan),
        where=peak_dominant_min > 0,
    )
    result["pulse_peak_largest_order_min"] = peak_dominant_min
    result["pulse_peak_largest_order_share_min"] = np.nanmin(
        pulse_values("peak_second_largest_order_share"), axis=1
    )
    result["pulse_peak_top3_order_share_min"] = np.nanmin(
        pulse_values("peak_second_top3_order_share"), axis=1
    )
    result["pulse_peak_effective_order_count_max"] = np.nanmax(
        pulse_values("peak_second_effective_order_count"), axis=1
    )
    result["pulse_peak_largest_order_fill_min"] = np.nanmin(
        pulse_values("peak_second_largest_order_fill_count"), axis=1
    )
    peak_shapes = np.stack(
        [pulse_values(f"peak_second_order_share{rank}") for rank in range(1, 6)], axis=2
    )
    peak_pair_cosines: list[np.ndarray] = []
    for left in range(5):
        for right in range(left + 1, 5):
            a = peak_shapes[:, left, :]
            b = peak_shapes[:, right, :]
            numerator = np.nansum(a * b, axis=1)
            denominator = np.sqrt(np.nansum(a * a, axis=1) * np.nansum(b * b, axis=1))
            peak_pair_cosines.append(
                np.divide(
                    numerator,
                    denominator,
                    out=np.full(len(result), np.nan),
                    where=denominator > 0,
                )
            )
    result["pulse_peak_order_shape_similarity"] = np.nanmean(
        np.stack(peak_pair_cosines, axis=1), axis=1
    )
    result["pulse_peak_mid_lift_count"] = np.sum(
        pulse_values("peak_mid_lift_3s") > 0, axis=1
    )
    result["pulse_peak_ask_lift_count"] = np.sum(
        pulse_values("peak_ask_price_lift_3s") > 0, axis=1
    )
    result["pulse_peak_ask_depth_consumed_median"] = np.nanmedian(
        pulse_values("peak_ask_depth_consumed_3s"), axis=1
    )
    result["pulse_repeat_quantity_share_median"] = np.nanmedian(
        pulse_values("repeated_quantity_count_share"), axis=1
    )
    result["pulse_order_gap_cv_median"] = np.nanmedian(
        pulse_values("buy_order_gap_cv"), axis=1
    )
    result["pulse_order_gap_regular_median"] = np.nanmedian(
        pulse_values("buy_order_gap_regular_share"), axis=1
    )
    result["pulse_mid_lift_positive_count"] = np.sum(
        pulse_values("mid_lift_end") > 0, axis=1
    )
    result["pulse_mid_lift_median"] = np.nanmedian(
        pulse_values("mid_lift_end"), axis=1
    )
    result["pulse_ask_lift_count"] = np.sum(
        pulse_values("ask_price_lift") > 0, axis=1
    )
    result["pulse_ask_depth_consumed_median"] = np.nanmedian(
        pulse_values("ask_depth_consumed"), axis=1
    )
    result["pulse_trade_coverage"] = np.sum(np.isfinite(pulse_trade), axis=1)
    target_share = result["target_active_buy_share"].fillna(0.0)
    result["target_buy_up_60"] = (
        result["volume_hit"].fillna(False)
        & result["next_up"].fillna(False)
        & target_share.ge(0.60)
    )
    result["target_buy_up_70"] = (
        result["volume_hit"].fillna(False)
        & result["next_up"].fillna(False)
        & target_share.ge(0.70)
    )
    result["target_buy_up_75"] = (
        result["volume_hit"].fillna(False)
        & result["next_up"].fillna(False)
        & target_share.ge(0.75)
    )
    return result


def enrich_level2_candidates(settings: Settings) -> pd.DataFrame:
    if not settings.candidate_path.is_file():
        raise FileNotFoundError(settings.candidate_path)
    candidates = pd.read_parquet(settings.candidate_path)
    candidates["date"] = pd.to_datetime(candidates["date"])
    pool = add_window_ids(select_level2_pool(candidates))
    daily_dir = settings.output_dir / "level2_candidate_daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute(f"SET threads={settings.threads}")
    connection.execute(f"SET memory_limit='{settings.memory_limit}'")
    temp_dir = settings.output_dir / "duckdb_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    connection.execute(f"SET temp_directory='{sql_path(temp_dir)}'")
    frames: list[pd.DataFrame] = []
    started = time.time()
    try:
        grouped = list(pool.groupby(pool["date"].dt.strftime("%Y-%m-%d"), sort=True))
        for index, (date, daily_candidates) in enumerate(grouped, start=1):
            target = daily_dir / f"{date}.parquet"
            if target.is_file() and not settings.overwrite:
                daily = pd.read_parquet(target)
            else:
                windows = candidate_windows(daily_candidates)
                features = query_window_features(connection, date, windows, settings.catalog.parent)
                daily = add_level2_aggregates(daily_candidates, features)
                temporary = target.with_suffix(".parquet.tmp")
                daily.to_parquet(temporary, index=False)
                os.replace(temporary, target)
            frames.append(daily)
            print(
                f"逐笔候选 {index}/{len(grouped)} {date}：{len(daily)} 次，累计 {time.time()-started:.1f} 秒",
                flush=True,
            )
    finally:
        connection.close()
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    result.to_parquet(settings.level2_path, index=False)
    return result


def apply_cooldown(frame: pd.DataFrame, minutes: int = 20) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(["date", "code", "trigger_position"])
    keep: list[int] = []
    for (_, _), group in ordered.groupby(["date", "code"], sort=False):
        last_position = -10_000
        for index, row in group.iterrows():
            position = int(row["trigger_position"])
            if position - last_position >= minutes:
                keep.append(index)
                last_position = position
    return ordered.loc[keep].copy()


def trimmed_mean(values: pd.Series, fraction: float = 0.01) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna().sort_values()
    if clean.empty:
        return math.nan
    cut = int(math.floor(len(clean) * fraction))
    if cut > 0 and len(clean) > 2 * cut:
        clean = clean.iloc[cut:-cut]
    return float(clean.mean())


def wilson_interval(successes: int, count: int, z: float = 1.959964) -> tuple[float, float]:
    if count <= 0:
        return math.nan, math.nan
    proportion = successes / count
    denominator = 1.0 + z * z / count
    centre = proportion + z * z / (2.0 * count)
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count)
    )
    return (centre - margin) / denominator, (centre + margin) / denominator


def summarize_events(
    frame: pd.DataFrame,
    rule: str,
    period_name: str,
) -> dict[str, object]:
    sample = apply_cooldown(frame)
    count = len(sample)
    result: dict[str, object] = {
        "rule": rule,
        "period": period_name,
        "count": count,
        "dates": int(sample["date"].nunique()) if count else 0,
        "stocks": int(sample["code"].nunique()) if count else 0,
        "top_stock_share": (
            float(sample["code"].value_counts(normalize=True).iloc[0]) if count else math.nan
        ),
    }
    for column in (
        "volume_hit",
        "joint_minute_hit",
        "target_buy_up_60",
        "target_buy_up_70",
        "target_buy_up_75",
    ):
        if column not in sample.columns or count == 0:
            result[column] = math.nan
            result[f"{column}_wilson_low"] = math.nan
            result[f"{column}_wilson_high"] = math.nan
            continue
        values = sample[column].fillna(False).astype(bool)
        successes = int(values.sum())
        low, high = wilson_interval(successes, count)
        result[column] = float(values.mean())
        result[f"{column}_wilson_low"] = low
        result[f"{column}_wilson_high"] = high
    for holding in (5, 10, 20):
        column = f"forward_{holding}m"
        values = pd.to_numeric(sample.get(column), errors="coerce").dropna()
        result[f"return_{holding}m_count"] = len(values)
        result[f"return_{holding}m_mean"] = float(values.mean()) if len(values) else math.nan
        result[f"return_{holding}m_median"] = float(values.median()) if len(values) else math.nan
        result[f"return_{holding}m_trimmed"] = trimmed_mean(values)
        result[f"return_{holding}m_up"] = float((values > 0).mean()) if len(values) else math.nan
    return result


def split_frames(frame: pd.DataFrame, split_date: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(frame["date"])
    return frame.loc[dates < split_date].copy(), frame.loc[dates >= split_date].copy()


def fixed_minute_masks(frame: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    all_rows = pd.Series(True, index=frame.index)
    up_four = frame["past_up_count"].ge(4)
    stable_return = frame["return_stability"].le(RETURN_STABILITY_LIMIT)
    pre_confirm = frame["no_early_spike"].fillna(False) & frame["pre_window_up"].fillna(False)
    all_five_up = frame["past_up_count"].eq(5)
    return [
        ("五批且间隔规律", all_rows),
        ("加至少四批上涨", up_four),
        ("再加每批涨幅接近", up_four & stable_return),
        ("再加预计窗口前确认", up_four & stable_return & pre_confirm),
        ("固定规则且周期为2分钟", up_four & stable_return & pre_confirm & frame["period"].eq(2)),
        ("固定规则且周期至少3分钟", up_four & stable_return & pre_confirm & frame["period"].ge(3)),
        ("五批全涨的更严格版本", all_five_up & stable_return & pre_confirm),
    ]


def fixed_level2_masks(frame: pd.DataFrame) -> list[tuple[str, pd.Series]]:
    base = (
        frame["past_up_count"].ge(4)
        & frame["return_stability"].le(RETURN_STABILITY_LIMIT)
        & frame["no_early_spike"].fillna(False)
        & frame["pre_window_up"].fillna(False)
    )
    buy60 = frame["pulse_combined_buy_share"].ge(0.60)
    buy70 = frame["pulse_combined_buy_share"].ge(0.70)
    buy75 = frame["pulse_combined_buy_share"].ge(0.75)
    four_buy60 = frame["pulse_buy_share_ge60_count"].ge(4)
    four_buy70 = frame["pulse_buy_share_ge70_count"].ge(4)
    net30 = frame["pulse_combined_net_buy_share"].ge(0.30)
    net50 = frame["pulse_combined_net_buy_share"].ge(0.50)
    amount_similar = frame["pulse_active_buy_amount_cv"].le(0.75)
    amount_very_similar = frame["pulse_active_buy_amount_cv"].le(0.50)
    amount_ratio_18 = frame["pulse_active_buy_amount_max_min"].le(1.80)
    peak_amount_ratio_18 = frame["pulse_peak_second_amount_max_min"].le(1.80)
    order_similar = frame["pulse_order_distribution_log_spread"].le(0.50)
    concentrated_orders = (
        frame["pulse_peak_largest_order_min"].ge(200_000)
        & frame["pulse_peak_largest_order_share_min"].ge(0.50)
        & frame["pulse_peak_top3_order_share_min"].ge(0.80)
        & frame["pulse_peak_effective_order_count_max"].le(4.0)
        & frame["pulse_peak_largest_order_fill_min"].ge(5)
        & frame["pulse_peak_order_shape_similarity"].ge(0.90)
    )
    price_response = frame["pulse_mid_lift_positive_count"].ge(4)
    ask_lift = frame["pulse_ask_lift_count"].ge(3)
    not_market_wide = frame["market_up_share_at_trigger"].le(0.70)
    return [
        ("分钟固定规则", base),
        ("分钟固定规则且周期为2分钟", base & frame["period"].eq(2)),
        ("分钟固定规则且周期至少3分钟", base & frame["period"].ge(3)),
        ("主动买入合计至少60%", base & buy60),
        ("主动买入合计至少70%", base & buy70),
        ("主动买入合计至少75%", base & buy75),
        ("至少四批主动买入超过60%", base & four_buy60),
        ("至少四批主动买入超过70%", base & four_buy70),
        ("净主动买入至少30%", base & net30),
        ("净主动买入至少50%", base & net50),
        ("各批主动买入额较接近", base & amount_similar),
        ("各批订单分布较接近", base & order_similar),
        ("至少四批价格即时抬高", base & price_response),
        ("五批金额比不超过1.8且主动买入75%", base & amount_ratio_18 & buy75),
        ("最强一秒金额比不超过1.8且主动买入75%", base & peak_amount_ratio_18 & buy75),
        ("每批由少数大买单主导", base & concentrated_orders),
        (
            "逐笔较严规则",
            base
            & buy70
            & four_buy60
            & net30
            & amount_similar
            & order_similar
            & price_response
            & not_market_wide,
        ),
        (
            "逐笔极严规则",
            base
            & buy75
            & four_buy70
            & net50
            & amount_very_similar
            & order_similar
            & price_response
            & ask_lift
            & concentrated_orders
            & not_market_wide,
        ),
    ]


@dataclass(frozen=True)
class SearchCondition:
    feature: str
    direction: str
    threshold: float

    def apply(self, frame: pd.DataFrame) -> pd.Series:
        values = pd.to_numeric(frame[self.feature], errors="coerce")
        if self.direction == "high":
            return values.ge(self.threshold)
        return values.le(self.threshold)

    @property
    def label(self) -> str:
        sign = "≥" if self.direction == "high" else "≤"
        return f"{self.feature}{sign}{self.threshold:.6g}"


SEARCH_FEATURES = {
    "pulse_combined_buy_share": "high",
    "pulse_combined_net_buy_share": "high",
    "pulse_buy_share_min": "high",
    "pulse_buy_share_ge60_count": "high",
    "pulse_active_buy_amount_cv": "low",
    "pulse_active_buy_amount_max_min": "low",
    "pulse_peak_second_amount_max_min": "low",
    "pulse_peak_dominant_amount_max_min": "low",
    "pulse_peak_largest_order_min": "high",
    "pulse_peak_largest_order_share_min": "high",
    "pulse_peak_top3_order_share_min": "high",
    "pulse_peak_effective_order_count_max": "low",
    "pulse_peak_largest_order_fill_min": "high",
    "pulse_peak_order_shape_similarity": "high",
    "pulse_peak_mid_lift_count": "high",
    "pulse_peak_ask_lift_count": "high",
    "pulse_peak_ask_depth_consumed_median": "high",
    "pulse_order_median_cv": "low",
    "pulse_order_distribution_log_spread": "low",
    "pulse_top1_share_cv": "low",
    "pulse_repeat_quantity_share_median": "high",
    "pulse_dominant_order_amount_cv": "low",
    "pulse_dominant_order_amount_max_min": "low",
    "pulse_largest_order_min": "high",
    "pulse_largest_order_share_min": "high",
    "pulse_top3_order_share_min": "high",
    "pulse_largest_order_fill_min": "high",
    "pulse_effective_order_count_median": "low",
    "pulse_order_shape_similarity": "high",
    "pulse_order_gap_cv_median": "low",
    "pulse_order_gap_regular_median": "high",
    "pulse_mid_lift_positive_count": "high",
    "pulse_mid_lift_median": "high",
    "pulse_ask_lift_count": "high",
    "pulse_ask_depth_consumed_median": "high",
    "pulse_close_position_mean": "high",
    "rise_concentration": "high",
    "market_up_share_at_trigger": "low",
    "market_spike_share_at_trigger": "low",
}


def base_level2_mask(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["past_up_count"].ge(4)
        & frame["return_stability"].le(RETURN_STABILITY_LIMIT)
        & frame["no_early_spike"].fillna(False)
        & frame["pre_window_up"].fillna(False)
    )


def one_condition_search(train: pd.DataFrame) -> pd.DataFrame:
    base = train.loc[base_level2_mask(train)].copy()
    rows: list[dict[str, object]] = []
    for feature, direction in SEARCH_FEATURES.items():
        values = pd.to_numeric(base.get(feature), errors="coerce").dropna()
        if len(values) < 30 or values.nunique() < 2:
            continue
        quantiles = sorted(set(float(value) for value in values.quantile([0.30, 0.50, 0.70]).dropna()))
        for threshold in quantiles:
            condition = SearchCondition(feature, direction, threshold)
            sample = apply_cooldown(base.loc[condition.apply(base)])
            if len(sample) < 20:
                continue
            successes = int(sample["target_buy_up_70"].fillna(False).sum())
            low, high = wilson_interval(successes, len(sample))
            rows.append(
                {
                    "feature": feature,
                    "direction": direction,
                    "threshold": threshold,
                    "count": len(sample),
                    "target_buy_up_70": successes / len(sample),
                    "wilson_low": low,
                    "wilson_high": high,
                    "return_10m_mean": pd.to_numeric(sample["forward_10m"], errors="coerce").mean(),
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["wilson_low", "target_buy_up_70", "count"], ascending=[False, False, False]
    ) if rows else pd.DataFrame()


def choose_early_conditions(train: pd.DataFrame, maximum: int = 4) -> list[SearchCondition]:
    base = train.loc[base_level2_mask(train)].copy()
    if base.empty:
        return []
    current = pd.Series(True, index=base.index)
    chosen: list[SearchCondition] = []
    used: set[str] = set()
    minimum_count = max(20, min(50, len(apply_cooldown(base)) // 4))

    for _ in range(maximum):
        best: tuple[float, float, int, SearchCondition, pd.Series] | None = None
        for feature, direction in SEARCH_FEATURES.items():
            if feature in used:
                continue
            available = pd.to_numeric(base.loc[current, feature], errors="coerce").dropna()
            if len(available) < minimum_count or available.nunique() < 2:
                continue
            for quantile in (0.30, 0.50, 0.70):
                threshold = float(available.quantile(quantile))
                condition = SearchCondition(feature, direction, threshold)
                next_mask = current & condition.apply(base)
                sample = apply_cooldown(base.loc[next_mask])
                if len(sample) < minimum_count:
                    continue
                successes = int(sample["target_buy_up_70"].fillna(False).sum())
                low, _ = wilson_interval(successes, len(sample))
                rate = successes / len(sample)
                candidate = (low, rate, len(sample), condition, next_mask)
                if best is None or candidate[:3] > best[:3]:
                    best = candidate
        if best is None:
            break
        chosen.append(best[3])
        used.add(best[3].feature)
        current = best[4]
    return chosen


def apply_conditions(frame: pd.DataFrame, conditions: Sequence[SearchCondition]) -> pd.Series:
    mask = base_level2_mask(frame)
    for condition in conditions:
        mask &= condition.apply(frame)
    return mask


def format_percent(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(number):
        return "—"
    return f"{number * 100:.2f}%"


def markdown_table(frame: pd.DataFrame, columns: Sequence[tuple[str, str]]) -> str:
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    rows = [header, divider]
    for row in frame.itertuples(index=False):
        values: list[str] = []
        row_dict = row._asdict()
        for column, _ in columns:
            value = row_dict.get(column)
            if column in {
                "volume_hit",
                "joint_minute_hit",
                "target_buy_up_70",
                "return_10m_mean",
                "return_10m_up",
            }:
                values.append(format_percent(value))
            elif column == "count":
                values.append(str(int(value)))
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def evaluate_results(settings: Settings) -> dict[str, object]:
    if not settings.candidate_path.is_file():
        raise FileNotFoundError(settings.candidate_path)
    minute = pd.read_parquet(settings.candidate_path)
    minute["date"] = pd.to_datetime(minute["date"])
    all_dates = sorted(pd.Timestamp(path.stem) for path in settings.minute_dir.glob("*.parquet"))
    if not all_dates:
        raise RuntimeError("没有日期可用于划分前后两段")
    split_date = all_dates[len(all_dates) // 2]
    minute_early, minute_late = split_frames(minute, split_date)
    minute_rows: list[dict[str, object]] = []
    for rule, mask in fixed_minute_masks(minute):
        early_mask = mask.loc[minute_early.index]
        late_mask = mask.loc[minute_late.index]
        minute_rows.append(summarize_events(minute_early.loc[early_mask], rule, "较早期"))
        minute_rows.append(summarize_events(minute_late.loc[late_mask], rule, "较晚期"))
    minute_results = pd.DataFrame(minute_rows)
    minute_results.to_csv(settings.output_dir / "minute_rule_results.csv", index=False, encoding="utf-8-sig")

    level2_results = pd.DataFrame()
    search_results = pd.DataFrame()
    selected_events = pd.DataFrame()
    chosen: list[SearchCondition] = []
    if settings.level2_path.is_file():
        level2 = pd.read_parquet(settings.level2_path)
        level2["date"] = pd.to_datetime(level2["date"])
        early, late = split_frames(level2, split_date)
        level2_rows: list[dict[str, object]] = []
        for rule, mask in fixed_level2_masks(level2):
            level2_rows.append(
                summarize_events(early.loc[mask.loc[early.index]], rule, "较早期")
            )
            level2_rows.append(
                summarize_events(late.loc[mask.loc[late.index]], rule, "较晚期")
            )
        search_results = one_condition_search(early)
        chosen = choose_early_conditions(early)
        chosen_name = "较早期选出的组合：" + "；".join(condition.label for condition in chosen)
        for period_name, part in (("较早期", early), ("较晚期", late)):
            mask = apply_conditions(part, chosen)
            level2_rows.append(summarize_events(part.loc[mask], chosen_name, period_name))
        level2_results = pd.DataFrame(level2_rows)
        level2_results.to_csv(
            settings.output_dir / "level2_rule_results.csv", index=False, encoding="utf-8-sig"
        )
        search_results.to_csv(
            settings.output_dir / "level2_one_condition_search.csv", index=False, encoding="utf-8-sig"
        )
        late_selected = apply_cooldown(late.loc[apply_conditions(late, chosen)])
        late_selected = late_selected.copy()
        late_selected.insert(0, "selected_rule", chosen_name)
        selected_events = late_selected
        selected_events.to_csv(
            settings.output_dir / "selected_events_late.csv", index=False, encoding="utf-8-sig"
        )

    minute_late_table = minute_results[minute_results["period"].eq("较晚期")]
    report: list[str] = [
        "# 规律放量上涨与疑似分批主动买入检查",
        "",
        f"- 数据日期：{settings.start} 至 {settings.end}，已排除 {len(BAD_DATES)} 个原始金额检查未通过的日期。",
        f"- 前后两段分界：{split_date:%Y-%m-%d}。所有可调条件只允许在较早期选择，较晚期只检查一次。",
        "- 一分钟成功：预计时间前后一分钟内再次异常放量，而且该分钟上涨。",
        "- 逐笔成功：同时满足一分钟成功，并且目标分钟主动买入占比至少70%。",
        "- 收益从预计窗口前确认完成后的下一分钟开盘开始计算，均未扣交易费用。",
        "",
        "## 一分钟规则在较晚期的结果",
        "",
        markdown_table(
            minute_late_table,
            [
                ("rule", "条件"),
                ("count", "次数"),
                ("volume_hit", "下一批放量"),
                ("joint_minute_hit", "放量且上涨"),
                ("return_10m_mean", "10分钟平均"),
                ("return_10m_up", "10分钟上涨比例"),
            ],
        ),
    ]
    if not level2_results.empty:
        report.extend(
            [
                "",
                "## 逐笔规则在较晚期的结果",
                "",
                markdown_table(
                    level2_results[level2_results["period"].eq("较晚期")],
                    [
                        ("rule", "条件"),
                        ("count", "次数"),
                        ("joint_minute_hit", "放量且上涨"),
                        ("target_buy_up_70", "主动买入放量上涨"),
                        ("return_10m_mean", "10分钟平均"),
                        ("return_10m_up", "10分钟上涨比例"),
                    ],
                ),
                "",
                "## 较早期选出的条件",
                "",
                *(f"- {condition.label}" for condition in chosen),
                "",
                "公开逐笔数据没有账户、席位或母订单编号，因此本结果只能称为“疑似分批主动买入”，不能确认是同一家机构。",
            ]
        )
    report_path = settings.output_dir / "report.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "settings": {key: str(value) if isinstance(value, Path) else value for key, value in asdict(settings).items()},
        "bad_dates": sorted(BAD_DATES),
        "split_date": split_date.strftime("%Y-%m-%d"),
        "minute_candidate_count": len(minute),
        "level2_candidate_count": int(len(pd.read_parquet(settings.level2_path))) if settings.level2_path.is_file() else 0,
        "chosen_early_conditions": [asdict(condition) for condition in chosen],
        "selected_late_count": len(selected_events),
    }
    (settings.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = make_settings(args)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    stages = (
        ("minutes", build_minute_cache),
        ("candidates", build_minute_candidates),
        ("level2", enrich_level2_candidates),
        ("evaluate", evaluate_results),
    )
    for name, function in stages:
        if args.stage in {name, "all"}:
            print(f"开始：{name}", flush=True)
            function(settings)
    print(f"完成，结果目录：{settings.output_dir}", flush=True)


if __name__ == "__main__":
    main()
