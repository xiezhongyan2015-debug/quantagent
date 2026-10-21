from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable, Iterator, Sequence
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


HISTORY_DAYS = 20
MINUTES_PER_DAY = 240
PART_BATCH_ROWS = 10_000
SOURCE_COLUMNS = (
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
)
# 成交量暂不参与规则，但仍检查输入中是否存在，防止误读其他 Parquet。
READ_COLUMNS = ("datetime", "open", "high", "low", "close", "amount")


def _output_fields() -> list[tuple[str, pa.DataType]]:
    fields: list[tuple[str, pa.DataType]] = [
        ("candidate_id", pa.string()),
        ("date", pa.timestamp("ns")),
        ("code", pa.string()),
        ("session_no", pa.int8()),
        ("current_position", pa.int16()),
        ("trigger_ts", pa.timestamp("ns")),
        ("predicted_ts", pa.timestamp("ns")),
        ("trigger_position", pa.int16()),
        ("predicted_position", pa.int16()),
        ("next_spike_position", pa.int16()),
        ("period", pa.int8()),
        ("gap_min", pa.int16()),
        ("gap_max", pa.int16()),
        ("past_up_count", pa.int8()),
        ("step_up_count", pa.int8()),
        ("return_stability", pa.float64()),
        ("amount_cv", pa.float64()),
        ("volume_z_mean", pa.float64()),
        ("volume_z_cv", pa.float64()),
        ("no_early_spike", pa.bool_()),
        ("pre_window_up", pa.bool_()),
        ("quiet_net_return", pa.float64()),
        ("quiet_abs_mean", pa.float64()),
        ("pulse_return_sum", pa.float64()),
        ("rise_concentration", pa.float64()),
        ("pulse_close_position_mean", pa.float64()),
        ("trigger_low_to_prev5_close_ma5_distance", pa.float64()),
        ("trigger_low_to_live_close_ma5_distance", pa.float64()),
        ("analysis_only_full_day_low_to_close_ma5_distance", pa.float64()),
        ("volume_hit", pa.bool_()),
        ("next_up", pa.bool_()),
        ("joint_minute_hit", pa.bool_()),
        ("target_minute_return", pa.float64()),
        ("forward_5m", pa.float64()),
        ("forward_10m", pa.float64()),
        ("forward_20m", pa.float64()),
    ]
    for number in range(1, 6):
        fields.extend(
            [
                (f"pulse{number}_position", pa.int16()),
                (f"pulse{number}_ts", pa.timestamp("ns")),
                (f"pulse{number}_return", pa.float64()),
                (f"pulse{number}_amount", pa.float64()),
            ]
        )
    return fields


OUTPUT_SCHEMA = pa.schema(_output_fields())
OUTPUT_COLUMNS = OUTPUT_SCHEMA.names


def _parse_optional_date(value: object | None, name: str) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} 不是有效日期：{value}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _validate_date_range(
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    start_name: str,
    end_name: str,
) -> None:
    if start is not None and end is not None and start > end:
        raise ValueError(f"{start_name} 不能晚于 {end_name}")


def _minute_timestamp(date: pd.Timestamp, position: int) -> pd.Timestamp:
    if position < 120:
        return date.normalize() + pd.Timedelta(hours=9, minutes=31 + position)
    return date.normalize() + pd.Timedelta(hours=13, minutes=position - 119)


def _minute_positions(datetimes: pd.Series) -> np.ndarray:
    minutes = datetimes.dt.hour.to_numpy(np.int16) * 60 + datetimes.dt.minute.to_numpy(
        np.int16
    )
    positions = np.full(len(datetimes), -1, dtype=np.int16)
    morning = (minutes >= 9 * 60 + 31) & (minutes <= 11 * 60 + 30)
    afternoon = (minutes >= 13 * 60 + 1) & (minutes <= 15 * 60)
    positions[morning] = minutes[morning] - (9 * 60 + 31)
    positions[afternoon] = 120 + minutes[afternoon] - (13 * 60 + 1)
    return positions


def _normalise_code(value: str) -> str:
    text = str(value).strip().lower()
    if text.endswith(".parquet"):
        text = text[: -len(".parquet")]
    elif text.endswith(".pq"):
        text = text[: -len(".pq")]
    if len(text) == 8 and text[:2] in {"sh", "sz", "bj"} and text[2:].isdigit():
        return text
    upper = text.upper()
    if "." in upper:
        number, suffix = upper.split(".", 1)
        exchange = {
            "SH": "sh",
            "SSE": "sh",
            "SZ": "sz",
            "SZSE": "sz",
            "BJ": "bj",
            "BSE": "bj",
        }.get(suffix)
        if exchange and len(number) == 6 and number.isdigit():
            return exchange + number
    if len(text) == 6 and text.isdigit():
        if text.startswith("6"):
            return "sh" + text
        if text.startswith(("0", "3")):
            return "sz" + text
        if text.startswith(("4", "8", "9")):
            return "bj" + text
    return text


def _split_codes(values: Sequence[str] | str | None) -> set[str] | None:
    if values is None:
        return None
    raw_values = [values] if isinstance(values, str) else list(values)
    codes = {
        _normalise_code(piece)
        for raw in raw_values
        for piece in str(raw).split(",")
        if piece.strip()
    }
    return codes or None


def _select_files(
    minute_dir: Path,
    output: Path,
    codes: set[str] | None,
    max_files: int | None,
) -> list[Path]:
    if not minute_dir.is_dir():
        raise FileNotFoundError(f"一分钟数据目录不存在：{minute_dir}")
    if max_files is not None and max_files <= 0:
        raise ValueError("max_files 必须大于 0")

    output_resolved = output.resolve()
    files = sorted(
        {
            *minute_dir.glob("*.parquet"),
            *minute_dir.glob("*.pq"),
        },
        key=lambda path: path.name.lower(),
    )
    files = [path for path in files if path.resolve() != output_resolved]
    if codes is not None:
        files = [path for path in files if _normalise_code(path.stem) in codes]
    if max_files is not None:
        files = files[:max_files]
    if not files:
        raise FileNotFoundError("没有找到符合条件的股票 Parquet 文件")
    return files


def _normalise_dates(values: Iterable[object]) -> pd.DatetimeIndex:
    parsed = pd.to_datetime(list(values), errors="coerce")
    dates = pd.DatetimeIndex(parsed).dropna()
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return pd.DatetimeIndex(dates.normalize().unique()).sort_values()


def _read_calendar_file(path: Path) -> pd.DatetimeIndex:
    if not path.is_file():
        raise FileNotFoundError(f"交易日文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
        if frame.empty:
            return pd.DatetimeIndex([])
        column = next(
            (
                name
                for name in ("date", "trade_date", "datetime", "calendar_date")
                if name in frame.columns
            ),
            frame.columns[0],
        )
        values = frame[column].tolist()
    elif suffix in {".csv", ".tsv"}:
        separator = "\t" if suffix == ".tsv" else ","
        frame = pd.read_csv(path, sep=separator)
        if frame.empty:
            return pd.DatetimeIndex([])
        column = next(
            (
                name
                for name in ("date", "trade_date", "datetime", "calendar_date")
                if name in frame.columns
            ),
            frame.columns[0],
        )
        values = frame[column].tolist()
    else:
        values = [
            line.strip().split(",", 1)[0].split("\t", 1)[0]
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    dates = _normalise_dates(values)
    if dates.empty:
        raise ValueError(f"交易日文件中没有有效日期：{path}")
    return dates


def _read_parquet_dates(
    path: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DatetimeIndex:
    filters: list[tuple[str, str, object]] = []
    if start is not None:
        filters.append(("datetime", ">=", start.to_pydatetime()))
    if end is not None:
        filters.append(("datetime", "<", (end + pd.Timedelta(days=1)).to_pydatetime()))
    table = pq.read_table(path, columns=["datetime"], filters=filters or None)
    datetimes = pd.Series(table.column("datetime").to_pandas())
    return _normalise_dates(datetimes.dt.normalize().unique())


def _build_trading_calendar(
    *,
    minute_dir: Path,
    selected_files: Sequence[Path],
    calendar_file: Path | None,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DatetimeIndex:
    if calendar_file is not None:
        calendar = _read_calendar_file(calendar_file)
    else:
        all_files = {
            _normalise_code(path.stem): path
            for path in (*minute_dir.glob("*.parquet"), *minute_dir.glob("*.pq"))
        }
        references = [
            all_files[code]
            for code in ("sh600000", "sz000001")
            if code in all_files
        ]
        if not references:
            references = list(selected_files[:2])
        date_sets = [_read_parquet_dates(path, start, end) for path in references]
        calendar = _normalise_dates(
            date for date_set in date_sets for date in date_set.to_pydatetime()
        )

    if start is not None:
        calendar = calendar[calendar >= start]
    if end is not None:
        calendar = calendar[calendar <= end]
    if calendar.empty:
        raise ValueError("指定范围内没有可用交易日")
    return calendar


def _read_stock_minutes(
    path: Path,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> pd.DataFrame:
    parquet_file = pq.ParquetFile(path)
    missing = sorted(set(SOURCE_COLUMNS) - set(parquet_file.schema_arrow.names))
    if missing:
        raise ValueError(f"{path.name} 缺少字段：{', '.join(missing)}")

    filters: list[tuple[str, str, object]] = []
    if start is not None:
        filters.append(("datetime", ">=", start.to_pydatetime()))
    if end is not None:
        filters.append(("datetime", "<", (end + pd.Timedelta(days=1)).to_pydatetime()))
    table = pq.read_table(
        path,
        columns=list(READ_COLUMNS),
        filters=filters or None,
    )
    frame = table.to_pandas()
    if frame.empty:
        return frame

    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    if frame["datetime"].dt.tz is not None:
        frame["datetime"] = frame["datetime"].dt.tz_localize(None)
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime", kind="stable")
    frame = frame.drop_duplicates(subset=["datetime"], keep="last")
    frame["_position"] = _minute_positions(frame["datetime"])
    frame = frame.loc[frame["_position"].between(0, MINUTES_PER_DAY - 1)].copy()
    frame["_date"] = frame["datetime"].dt.normalize()
    for column in ("open", "high", "low", "close", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _day_arrays(day: pd.DataFrame) -> dict[str, np.ndarray]:
    day = day.drop_duplicates(subset=["_position"], keep="last")
    positions = day["_position"].to_numpy(dtype=np.int16)
    arrays: dict[str, np.ndarray] = {}
    for column in ("open", "high", "low", "close", "amount"):
        values = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
        values[positions] = day[column].to_numpy(dtype=np.float64)
        arrays[column] = values
    return arrays


def _empty_day_arrays() -> dict[str, np.ndarray]:
    return {
        column: np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
        for column in ("open", "high", "low", "close", "amount")
    }


def _safe_nanmean(values: np.ndarray) -> float:
    valid = values[np.isfinite(values)]
    return float(valid.mean()) if len(valid) else math.nan


def _safe_nanstd(values: np.ndarray) -> float:
    valid = values[np.isfinite(values)]
    return float(valid.std(ddof=0)) if len(valid) else math.nan


def _positive_price_mean(values: Sequence[float], required_count: int) -> float:
    prices = np.asarray(values, dtype=np.float64)
    if (
        len(prices) != required_count
        or not np.all(np.isfinite(prices))
        or not np.all(prices > 0)
    ):
        return math.nan
    return float(prices.mean())


def _price_distance(price: float, reference: float) -> float:
    if (
        not np.isfinite(price)
        or price <= 0
        or not np.isfinite(reference)
        or reference <= 0
    ):
        return math.nan
    return float(price / reference - 1.0)


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


def _valid_five_pulse_windows(
    spikes: np.ndarray,
    upper: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """一次找出一个交易时段内全部合格的五批放量窗口。"""

    if len(spikes) < 5:
        return (
            np.empty((0, 5), dtype=np.int16),
            np.empty((0, 4), dtype=np.int16),
            np.empty(0, dtype=np.int8),
            np.empty(0, dtype=np.int16),
        )

    windows = np.lib.stride_tricks.sliding_window_view(spikes, 5)
    gaps = np.diff(windows, axis=1).astype(np.int16, copy=False)
    ordered_gaps = np.sort(gaps, axis=1)
    median_gaps = (
        ordered_gaps[:, 1].astype(np.float64)
        + ordered_gaps[:, 2].astype(np.float64)
    ) / 2.0

    # 四个间隔里至少三个相同，排序后必然是前3个或后3个相同。
    repeated_three = (ordered_gaps[:, 0] == ordered_gaps[:, 2]) | (
        ordered_gaps[:, 1] == ordered_gaps[:, 3]
    )
    periods = np.rint(median_gaps).astype(np.int16)
    current = windows[:, -1].astype(np.int16, copy=False)
    predicted = current + periods
    trigger = predicted - 2
    valid = (
        repeated_three
        & (median_gaps >= 2)
        & (median_gaps <= 10)
        & (trigger >= current)
        & (predicted + 1 < upper)
        & (trigger + 1 < upper)
    )

    next_spikes = np.full(len(windows), -1, dtype=np.int16)
    if len(windows) > 1:
        next_spikes[:-1] = spikes[5:]
    return (
        windows[valid].astype(np.int16, copy=False),
        gaps[valid],
        periods[valid].astype(np.int8, copy=False),
        next_spikes[valid],
    )


def _day_event_records(
    *,
    date: pd.Timestamp,
    code: str,
    arrays: dict[str, np.ndarray],
    history_sum: np.ndarray,
    history_square_sum: np.ndarray,
    history_count: np.ndarray,
    previous_daily_closes: Sequence[float],
    min_minute_amount: float,
    spike_z: float,
) -> Iterator[dict[str, object]]:
    amount = arrays["amount"]
    minute_open = arrays["open"]
    minute_high = arrays["high"]
    minute_low = arrays["low"]
    minute_close = arrays["close"]

    previous_closes = list(previous_daily_closes)
    previous_five_close_ma = _positive_price_mean(previous_closes, 5)
    previous_four_closes = previous_closes[-4:]
    valid_minute_lows = np.isfinite(minute_low) & (minute_low > 0)
    running_low = np.minimum.accumulate(
        np.where(valid_minute_lows, minute_low, np.inf)
    )
    full_day_low = float(running_low[-1])
    full_day_close_ma = _positive_price_mean(
        [*previous_four_closes, float(minute_close[MINUTES_PER_DAY - 1])],
        5,
    )
    full_day_low_distance = _price_distance(full_day_low, full_day_close_ma)

    valid_history = history_count == HISTORY_DAYS
    history_mean = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
    history_std = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
    history_mean[valid_history] = history_sum[valid_history] / HISTORY_DAYS
    variance = np.zeros(MINUTES_PER_DAY, dtype=np.float64)
    variance[valid_history] = (
        history_square_sum[valid_history] / HISTORY_DAYS
        - history_mean[valid_history] * history_mean[valid_history]
    )
    history_std[valid_history] = np.sqrt(np.maximum(variance[valid_history], 0.0))

    volume_z = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
    usable_std = valid_history & np.isfinite(history_std) & (history_std > 0)
    volume_z[usable_std] = (
        amount[usable_std] - history_mean[usable_std]
    ) / history_std[usable_std]
    is_spike = (
        np.isfinite(volume_z)
        & (volume_z > spike_z)
        & np.isfinite(amount)
        & (amount >= min_minute_amount)
    )

    minute_return = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
    valid_price = np.isfinite(minute_open) & np.isfinite(minute_close) & (minute_open > 0)
    minute_return[valid_price] = minute_close[valid_price] / minute_open[valid_price] - 1.0
    close_position = np.full(MINUTES_PER_DAY, np.nan, dtype=np.float64)
    spread = minute_high - minute_low
    valid_spread = (
        np.isfinite(minute_close)
        & np.isfinite(minute_high)
        & np.isfinite(minute_low)
        & (spread > 0)
    )
    close_position[valid_spread] = (
        minute_close[valid_spread] - minute_low[valid_spread]
    ) / spread[valid_spread]
    flat_bar = (
        np.isfinite(minute_close)
        & np.isfinite(minute_high)
        & np.isfinite(minute_low)
        & (minute_high == minute_low)
    )
    close_position[flat_bar] = 0.5

    for lower, upper in ((0, 120), (120, 240)):
        spikes = np.flatnonzero(is_spike[lower:upper]) + lower
        windows, gap_windows, periods, next_spikes = _valid_five_pulse_windows(
            spikes.astype(np.int16, copy=False), upper
        )
        for past, gaps, period_value, next_spike_value in zip(
            windows, gap_windows, periods, next_spikes
        ):
            period = int(period_value)
            current = int(past[-1])
            predicted = current + period
            trigger = predicted - 2
            next_spike = int(next_spike_value)
            volume_hit = next_spike >= 0 and abs(next_spike - predicted) <= 1
            next_up = bool(
                next_spike >= 0
                and np.isfinite(minute_return[next_spike])
                and minute_return[next_spike] > 0
            )
            no_early_spike = next_spike < 0 or next_spike > trigger
            pre_window_up = bool(
                np.isfinite(minute_return[trigger]) and minute_return[trigger] > 0
            )

            pulse_returns = minute_return[past]
            pulse_amounts = amount[past]
            pulse_z = volume_z[past]
            pulse_closes = minute_close[past]
            pulse_close_positions = close_position[past]
            quiet_positions = np.setdiff1d(
                np.arange(int(past[0]), current + 1), past, assume_unique=True
            )
            quiet_returns = minute_return[quiet_positions]
            pulse_abs_mean = _safe_nanmean(np.abs(pulse_returns))
            return_stability = (
                _safe_nanstd(pulse_returns) / max(pulse_abs_mean, 1e-8)
                if np.isfinite(pulse_abs_mean)
                else math.nan
            )
            amount_mean = _safe_nanmean(pulse_amounts)
            amount_cv = (
                _safe_nanstd(pulse_amounts) / max(amount_mean, 1.0)
                if np.isfinite(amount_mean)
                else math.nan
            )
            z_mean = _safe_nanmean(pulse_z)
            z_cv = (
                _safe_nanstd(pulse_z) / max(abs(z_mean), 1e-8)
                if np.isfinite(z_mean)
                else math.nan
            )
            quiet_net = float(np.nansum(quiet_returns))
            quiet_abs_mean = (
                _safe_nanmean(np.abs(quiet_returns)) if len(quiet_positions) else 0.0
            )
            pulse_return_sum = float(np.nansum(pulse_returns))
            total_return_sum = pulse_return_sum + quiet_net
            rise_concentration = (
                pulse_return_sum / total_return_sum
                if total_return_sum > 1e-8
                else math.nan
            )
            target_position = next_spike if volume_hit else predicted
            target_return = (
                float(minute_return[target_position])
                if 0 <= target_position < upper
                and np.isfinite(minute_return[target_position])
                else math.nan
            )
            trigger_low = float(running_low[trigger])
            trigger_live_close_ma = _positive_price_mean(
                [*previous_four_closes, float(minute_close[trigger])],
                5,
            )

            record: dict[str, object] = {
                "candidate_id": f"{date:%Y%m%d}_{code}_{current:03d}",
                "date": date.normalize(),
                "code": code,
                "session_no": 0 if current < 120 else 1,
                "current_position": current,
                "trigger_ts": _minute_timestamp(date, trigger),
                "predicted_ts": _minute_timestamp(date, predicted),
                "trigger_position": trigger,
                "predicted_position": predicted,
                "next_spike_position": next_spike,
                "period": period,
                "gap_min": int(gaps.min()),
                "gap_max": int(gaps.max()),
                "past_up_count": int(np.sum(pulse_returns > 0)),
                "step_up_count": int(np.sum(np.diff(pulse_closes) > 0)),
                "return_stability": return_stability,
                "amount_cv": amount_cv,
                "volume_z_mean": z_mean,
                "volume_z_cv": z_cv,
                "no_early_spike": no_early_spike,
                "pre_window_up": pre_window_up,
                "quiet_net_return": quiet_net,
                "quiet_abs_mean": quiet_abs_mean,
                "pulse_return_sum": pulse_return_sum,
                "rise_concentration": rise_concentration,
                "pulse_close_position_mean": _safe_nanmean(pulse_close_positions),
                "trigger_low_to_prev5_close_ma5_distance": _price_distance(
                    trigger_low, previous_five_close_ma
                ),
                "trigger_low_to_live_close_ma5_distance": _price_distance(
                    trigger_low, trigger_live_close_ma
                ),
                "analysis_only_full_day_low_to_close_ma5_distance": (
                    full_day_low_distance
                ),
                "volume_hit": volume_hit,
                "next_up": next_up,
                "joint_minute_hit": bool(volume_hit and next_up),
                "target_minute_return": target_return,
                "forward_5m": _future_return(
                    minute_open, minute_close, trigger, 5, upper
                ),
                "forward_10m": _future_return(
                    minute_open, minute_close, trigger, 10, upper
                ),
                "forward_20m": _future_return(
                    minute_open, minute_close, trigger, 20, upper
                ),
            }
            for number, position in enumerate(past, start=1):
                record[f"pulse{number}_position"] = int(position)
                record[f"pulse{number}_ts"] = _minute_timestamp(date, int(position))
                record[f"pulse{number}_return"] = float(minute_return[position])
                record[f"pulse{number}_amount"] = float(amount[position])
            yield record


def _iter_stock_records(
    path: Path,
    *,
    trading_dates: pd.DatetimeIndex,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    event_start: pd.Timestamp | None,
    event_end: pd.Timestamp | None,
    min_minute_amount: float,
    spike_z: float,
) -> Iterator[dict[str, object]]:
    read_end = end
    if event_end is not None and (read_end is None or event_end < read_end):
        read_end = event_end
    frame = _read_stock_minutes(path, start, read_end)
    if frame.empty:
        return

    code = _normalise_code(path.stem)
    history: deque[np.ndarray] = deque()
    history_sum = np.zeros(MINUTES_PER_DAY, dtype=np.float64)
    history_square_sum = np.zeros(MINUTES_PER_DAY, dtype=np.float64)
    history_count = np.zeros(MINUTES_PER_DAY, dtype=np.int16)
    daily_close_history: deque[float] = deque(maxlen=5)

    stock_dates = _normalise_dates(frame["_date"].unique())
    dates = _normalise_dates(
        [*trading_dates.to_pydatetime(), *stock_dates.to_pydatetime()]
    )
    grouped_indices = frame.groupby("_date", sort=False).indices

    for date in dates:
        indices = grouped_indices.get(pd.Timestamp(date))
        arrays = (
            _day_arrays(frame.iloc[indices])
            if indices is not None
            else _empty_day_arrays()
        )
        in_event_range = (event_start is None or date >= event_start) and (
            event_end is None or date <= event_end
        )
        if in_event_range and len(history) == HISTORY_DAYS:
            yield from _day_event_records(
                date=date,
                code=code,
                arrays=arrays,
                history_sum=history_sum,
                history_square_sum=history_square_sum,
                history_count=history_count,
                previous_daily_closes=tuple(daily_close_history),
                min_minute_amount=min_minute_amount,
                spike_z=spike_z,
            )

        daily_close = float(arrays["close"][MINUTES_PER_DAY - 1])
        daily_close_history.append(
            daily_close
            if np.isfinite(daily_close) and daily_close > 0
            else math.nan
        )

        stored = arrays["amount"].copy()
        valid = np.isfinite(stored)
        clean = np.where(valid, stored, 0.0)
        history.append(stored)
        history_sum += clean
        history_square_sum += clean * clean
        history_count += valid.astype(np.int16)
        if len(history) > HISTORY_DAYS:
            old = history.popleft()
            old_valid = np.isfinite(old)
            old_clean = np.where(old_valid, old, 0.0)
            history_sum -= old_clean
            history_square_sum -= old_clean * old_clean
            history_count -= old_valid.astype(np.int16)


def _records_to_table(records: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(records, schema=OUTPUT_SCHEMA)


def _empty_table() -> pa.Table:
    return pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in OUTPUT_SCHEMA],
        schema=OUTPUT_SCHEMA,
    )


def _process_stock_worker(task: dict[str, object]) -> dict[str, object]:
    index = int(task["index"])
    source = Path(str(task["source"]))
    part_path = Path(str(task["part_path"]))
    start = _parse_optional_date(task.get("start"), "start")
    end = _parse_optional_date(task.get("end"), "end")
    event_start = _parse_optional_date(task.get("event_start"), "event_start")
    event_end = _parse_optional_date(task.get("event_end"), "event_end")
    calendar_values = np.load(str(task["calendar_path"]), allow_pickle=False)
    trading_dates = pd.DatetimeIndex(calendar_values.astype("datetime64[ns]"))
    count = 0
    buffer: list[dict[str, object]] = []
    writer: pq.ParquetWriter | None = None
    try:
        for record in _iter_stock_records(
            source,
            trading_dates=trading_dates,
            start=start,
            end=end,
            event_start=event_start,
            event_end=event_end,
            min_minute_amount=float(task["min_minute_amount"]),
            spike_z=float(task["spike_z"]),
        ):
            buffer.append(record)
            if len(buffer) >= PART_BATCH_ROWS:
                writer = writer or pq.ParquetWriter(
                    part_path, OUTPUT_SCHEMA, compression="zstd"
                )
                writer.write_table(_records_to_table(buffer))
                count += len(buffer)
                buffer.clear()
        if buffer:
            writer = writer or pq.ParquetWriter(
                part_path, OUTPUT_SCHEMA, compression="zstd"
            )
            writer.write_table(_records_to_table(buffer))
            count += len(buffer)
    finally:
        if writer is not None:
            writer.close()
    return {
        "index": index,
        "code": _normalise_code(source.stem),
        "part_path": str(part_path) if count else "",
        "event_count": count,
    }


def _merge_parts(results: Iterable[dict[str, object]], target: Path) -> int:
    ordered = sorted(results, key=lambda item: int(item["index"]))
    writer: pq.ParquetWriter | None = None
    total = 0
    try:
        for item in ordered:
            raw_path = str(item["part_path"])
            if not raw_path:
                continue
            part_path = Path(raw_path)
            parquet_file = pq.ParquetFile(part_path)
            writer = writer or pq.ParquetWriter(
                target, OUTPUT_SCHEMA, compression="zstd"
            )
            for batch in parquet_file.iter_batches(batch_size=65_536):
                writer.write_batch(batch)
                total += batch.num_rows
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        pq.write_table(_empty_table(), target, compression="zstd")
    return total


def prepare_events(
    *,
    minute_dir: str | os.PathLike[str],
    output: str | os.PathLike[str],
    start: object | None = None,
    end: object | None = None,
    event_start: object | None = None,
    event_end: object | None = None,
    workers: int = 4,
    min_minute_amount: float = 100_000.0,
    spike_z: float = 1.0,
    codes: Sequence[str] | str | None = None,
    max_files: int | None = None,
    calendar_file: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """逐只股票生成规律放量候选事件，并合并成一个 Parquet。"""

    if workers <= 0:
        raise ValueError("workers 必须大于 0")
    if min_minute_amount < 0:
        raise ValueError("min_minute_amount 不能小于 0")
    if not np.isfinite(spike_z):
        raise ValueError("spike_z 必须是有限数值")

    start_date = _parse_optional_date(start, "start")
    end_date = _parse_optional_date(end, "end")
    event_start_date = _parse_optional_date(event_start, "event_start")
    event_end_date = _parse_optional_date(event_end, "event_end")
    _validate_date_range(start_date, end_date, "start", "end")
    _validate_date_range(
        event_start_date, event_end_date, "event_start", "event_end"
    )
    if (
        start_date is not None
        and event_start_date is not None
        and start_date >= event_start_date
    ):
        raise ValueError("start 必须早于 event_start，并至少留出20个交易日")

    minute_root = Path(minute_dir).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_codes = _split_codes(codes)
    files = _select_files(minute_root, output_path, selected_codes, max_files)

    resolved_calendar_file = (
        Path(calendar_file).expanduser().resolve() if calendar_file is not None else None
    )
    read_end = end_date
    if event_end_date is not None and (read_end is None or event_end_date < read_end):
        read_end = event_end_date
    trading_calendar = _build_trading_calendar(
        minute_dir=minute_root,
        selected_files=files,
        calendar_file=resolved_calendar_file,
        start=start_date,
        end=read_end,
    )

    effective_start = start_date
    if event_start_date is not None:
        prior_dates = trading_calendar[trading_calendar < event_start_date]
        if len(prior_dates) < HISTORY_DAYS:
            if start_date is None:
                raise ValueError("event_start 之前不足20个交易日，无法计算历史成交额")
            raise ValueError("start 到 event_start 之间不足20个交易日，请把 start 提前")
        if start_date is None:
            effective_start = pd.Timestamp(prior_dates[-HISTORY_DAYS]).normalize()

    if effective_start is not None:
        trading_calendar = trading_calendar[trading_calendar >= effective_start]

    temp_dir = output_path.parent / f".{output_path.stem}.parts-{uuid4().hex}"
    temp_dir.mkdir(parents=False, exist_ok=False)
    merged_path = temp_dir / "merged.parquet"
    calendar_path = temp_dir / "trading_dates.npy"
    np.save(
        calendar_path,
        trading_calendar.to_numpy(dtype="datetime64[D]"),
        allow_pickle=False,
    )
    tasks: list[dict[str, object]] = []
    for index, source in enumerate(files):
        tasks.append(
            {
                "index": index,
                "source": str(source),
                "part_path": str(temp_dir / f"part-{index:05d}.parquet"),
                "calendar_path": str(calendar_path),
                "start": (
                    effective_start.date().isoformat()
                    if effective_start is not None
                    else None
                ),
                "end": end_date.date().isoformat() if end_date is not None else None,
                "event_start": (
                    event_start_date.date().isoformat()
                    if event_start_date is not None
                    else None
                ),
                "event_end": (
                    event_end_date.date().isoformat()
                    if event_end_date is not None
                    else None
                ),
                "min_minute_amount": float(min_minute_amount),
                "spike_z": float(spike_z),
            }
        )

    results: list[dict[str, object]] = []
    try:
        actual_workers = min(int(workers), len(tasks))
        if actual_workers == 1:
            for task in tasks:
                results.append(_process_stock_worker(task))
        else:
            # 动态载入脚本的测试环境无法按模块名启动新进程，此时改用线程；
            # 命令行正常运行时仍使用独立进程处理每只股票。
            executor_type = (
                ProcessPoolExecutor
                if sys.modules.get(__name__) is not None
                else ThreadPoolExecutor
            )
            with executor_type(max_workers=actual_workers) as executor:
                future_map = {
                    executor.submit(_process_stock_worker, task): task for task in tasks
                }
                completed = 0
                for future in as_completed(future_map):
                    results.append(future.result())
                    completed += 1
                    if completed % 25 == 0 or completed == len(tasks):
                        print(f"已完成 {completed}/{len(tasks)} 只股票", flush=True)

        events_written = _merge_parts(results, merged_path)
        os.replace(merged_path, output_path)
    finally:
        # 临时目录名称带随机编号，且明确位于输出文件的同级目录。
        if temp_dir.is_dir() and temp_dir.parent == output_path.parent:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return {
        "minute_dir": str(minute_root),
        "output": str(output_path),
        "files_processed": len(files),
        "events_written": int(events_written),
        "workers": min(int(workers), len(tasks)),
        "calendar_dates": len(trading_calendar),
        "calendar_file": (
            str(resolved_calendar_file)
            if resolved_calendar_file is not None
            else "sh600000与sz000001日期并集"
        ),
        "effective_start": (
            effective_start.date().isoformat() if effective_start is not None else None
        ),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从多年逐股一分钟 Parquet 中生成规律放量候选事件。"
    )
    parser.add_argument("--minute-dir", type=Path, required=True, help="逐股 Parquet 目录。")
    parser.add_argument("--output", type=Path, required=True, help="结果 Parquet 路径。")
    parser.add_argument("--start", default=None, help="读取数据的开始日期。")
    parser.add_argument("--end", default=None, help="读取数据的结束日期。")
    parser.add_argument("--event-start", default=None, help="候选事件的开始日期。")
    parser.add_argument("--event-end", default=None, help="候选事件的结束日期。")
    parser.add_argument(
        "--calendar-file",
        type=Path,
        default=None,
        help="可选交易日文件；支持文本、CSV、TSV 和 Parquet。",
    )
    parser.add_argument("--workers", type=int, default=4, help="同时处理的股票数。")
    parser.add_argument(
        "--min-minute-amount",
        type=float,
        default=100_000.0,
        help="异常放量分钟的最低成交额。",
    )
    parser.add_argument(
        "--spike-z", type=float, default=1.0, help="成交额超过历史均值的标准差倍数。"
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        default=None,
        help="只处理指定代码，可用空格或逗号分隔。",
    )
    parser.add_argument(
        "--max-files", type=int, default=None, help="按文件名排序后最多处理多少只股票。"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    summary = prepare_events(
        minute_dir=args.minute_dir,
        output=args.output,
        start=args.start,
        end=args.end,
        event_start=args.event_start,
        event_end=args.event_end,
        calendar_file=args.calendar_file,
        workers=args.workers,
        min_minute_amount=args.min_minute_amount,
        spike_z=args.spike_z,
        codes=args.codes,
        max_files=args.max_files,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
