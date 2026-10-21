#!/usr/bin/env python3
"""把多个分钟压缩包直接合并为按股票保存的前复权 Parquet 文件。"""

from __future__ import annotations

import argparse
import atexit
import json
import math
import msvcrt
import os
import shutil
import time
import traceback
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path, PurePosixPath
from typing import Iterator

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


BASE_ARCHIVE_NAME = "1分钟.zip"
SHSZ_ARCHIVE_NAME = "沪深2026分钟数据（截止20260529）.zip"
Q1_ARCHIVE_NAME = "1min.7z"
ROLLING_ARCHIVE_RELATIVE = Path("最新更新") / "2026.03.23-2026.04.17" / "A股分钟线.zip"
DATA_END_DATE = 20260529

# 这些日期已经回原压缩包和日线逐项核对，分钟值无法可靠恢复。
KNOWN_CORRUPT_DAYS: dict[str, set[int]] = {
    "sh600103": {20000829},
    "sh600185": {20000829},
    "sh600221": {20000829},
    "sz000008": {20060109},
    "sz000088": {20061215},
    "sz000089": {20060109},
    "sz000488": {20060109},
    "sz000543": {20061215},
    "sz000783": {20000829},
    "sz000786": {20060109},
}
KNOWN_CORRUPT_TIMESTAMPS: dict[str, set[str]] = {
    "sz000800": {"2006-12-15 15:00:00"},
}

OUTPUT_SCHEMA = pa.schema(
    [
        pa.field("datetime", pa.timestamp("s"), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("amount", pa.float64(), nullable=False),
        pa.field("float_shares", pa.int64(), nullable=True),
        pa.field("total_shares", pa.int64(), nullable=True),
        pa.field("qfq_ratio", pa.float64(), nullable=False),
        pa.field("source_id", pa.uint8(), nullable=False),
    ],
    metadata={
        b"price": b"qfq, raw_price * daily_adj_factor / adj_factor_at_20260529",
        b"volume_unit": b"share",
        b"amount_unit": b"CNY",
        b"source_1": b"1min.zip, through 2025-12-31, 09:30 merged into 09:31",
        b"source_2": b"SH/SZ 2026 archive, timestamps shifted +1 minute",
        b"source_3": b"Q1 2026 archive, used for BJ and SH/SZ missing from source_2",
        b"source_4": b"rolling archive, only used when source_2 is absent, 09:30 merged into 09:31",
    },
)


WORKER_ARCHIVES: dict[str, zipfile.ZipFile] = {}
WORKER_OUTPUT_DIR: Path | None = None
WORKER_DAILY_DIR: Path | None = None
WORKER_FACTOR_DIR: Path | None = None
WORKER_ROW_GROUP_SIZE = 500_000
WORKER_COMPRESSION_LEVEL = 15
WORKER_MIN_FREE_BYTES = 10 * 1024**3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--daily-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--row-group-size", type=int, default=500_000)
    parser.add_argument("--compression-level", type=int, default=15)
    parser.add_argument("--min-free-gib", type=float, default=10.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--codes", nargs="*")
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def code_from_member(name: str) -> str | None:
    filename = PurePosixPath(name.replace("\\", "/")).name.lower()
    if not filename.endswith(".csv"):
        return None
    stem = filename[:-4]
    if stem.endswith("_1m"):
        stem = stem[:-3]
    if stem.startswith(("sh", "sz", "bj")) and stem[2:].isdigit():
        return stem
    parts = stem.split(".")
    if len(parts) == 2 and parts[0].isdigit() and parts[1] in {"sh", "sz", "bj"}:
        return parts[1] + parts[0]
    return None


def member_map(archive_path: Path, markets: set[str] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            code = code_from_member(item.filename)
            if code and (markets is None or code[:2] in markets):
                result[code] = item.filename
    return result


def archive_file_sizes(archive_path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            code = code_from_member(item.filename)
            if code:
                result[code] = item.file_size
    return result


def make_tasks(data_dir: Path) -> tuple[list[dict[str, object]], int]:
    base_path = data_dir / BASE_ARCHIVE_NAME
    shsz_path = data_dir / SHSZ_ARCHIVE_NAME
    q1_path = data_dir / Q1_ARCHIVE_NAME
    rolling_path = data_dir / ROLLING_ARCHIVE_RELATIVE
    base = member_map(base_path)
    shsz = member_map(shsz_path, {"sh", "sz"})
    q1 = member_map(q1_path)
    rolling = member_map(rolling_path)
    base_sizes = archive_file_sizes(base_path)
    shsz_sizes = archive_file_sizes(shsz_path)
    q1_sizes = archive_file_sizes(q1_path)
    rolling_sizes = archive_file_sizes(rolling_path)
    codes = sorted(set(base) | set(shsz) | set(q1) | set(rolling))
    tasks: list[dict[str, object]] = []
    for code in codes:
        q1_member = q1.get(code)
        rolling_member = rolling.get(code) if code not in shsz else None
        source_bytes = (
            base_sizes.get(code, 0)
            + shsz_sizes.get(code, 0)
            + (q1_sizes.get(code, 0) if q1_member else 0)
            + (rolling_sizes.get(code, 0) if rolling_member else 0)
        )
        tasks.append(
            {
                "code": code,
                "base_member": base.get(code),
                "shsz_member": shsz.get(code),
                "q1_member": q1_member,
                "rolling_member": rolling_member,
                "source_bytes": source_bytes,
            }
        )
    tasks.sort(key=lambda item: (-int(item["source_bytes"]), str(item["code"])))
    return tasks, sum(int(item["source_bytes"]) for item in tasks)


def init_worker(
    base_path: str,
    shsz_path: str,
    q1_path: str,
    rolling_path: str,
    output_dir: str,
    daily_dir: str,
    factor_dir: str,
    row_group_size: int,
    compression_level: int,
    min_free_bytes: int,
) -> None:
    global WORKER_ARCHIVES
    global WORKER_OUTPUT_DIR, WORKER_DAILY_DIR, WORKER_FACTOR_DIR
    global WORKER_ROW_GROUP_SIZE, WORKER_COMPRESSION_LEVEL, WORKER_MIN_FREE_BYTES
    WORKER_ARCHIVES = {
        "base": zipfile.ZipFile(base_path),
        "shsz": zipfile.ZipFile(shsz_path),
        "q1": zipfile.ZipFile(q1_path),
        "rolling": zipfile.ZipFile(rolling_path),
    }
    WORKER_OUTPUT_DIR = Path(output_dir)
    WORKER_DAILY_DIR = Path(daily_dir)
    WORKER_FACTOR_DIR = Path(factor_dir)
    WORKER_ROW_GROUP_SIZE = row_group_size
    WORKER_COMPRESSION_LEVEL = compression_level
    WORKER_MIN_FREE_BYTES = min_free_bytes


def load_daily_reference(code: str) -> pd.DataFrame:
    assert WORKER_DAILY_DIR is not None
    path = WORKER_DAILY_DIR / f"{code}.csv"
    columns = [
        "交易日期",
        "开盘价",
        "最高价",
        "最低价",
        "收盘价",
        "成交量",
        "成交额",
        "流通市值",
        "总市值",
    ]
    if not path.exists():
        return pd.DataFrame(index=pd.Index([], name="date_int"))
    frame = pd.read_csv(
        path,
        skiprows=1,
        usecols=columns,
        low_memory=False,
        encoding="gb18030",
    )
    dates = pd.to_datetime(frame["交易日期"], errors="coerce")
    frame["date_int"] = (
        dates.dt.year * 10000 + dates.dt.month * 100 + dates.dt.day
    ).astype("Int64")
    rename = {
        "开盘价": "daily_open",
        "最高价": "daily_high",
        "最低价": "daily_low",
        "收盘价": "daily_close",
        "成交量": "daily_volume",
        "成交额": "daily_amount",
    }
    frame = frame.rename(columns=rename)
    for column in rename.values():
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close = frame["daily_close"]
    frame["daily_float_shares"] = pd.to_numeric(frame["流通市值"], errors="coerce") / close
    frame["daily_total_shares"] = pd.to_numeric(frame["总市值"], errors="coerce") / close
    frame = frame.dropna(subset=["date_int"]).drop_duplicates("date_int", keep="last")
    return frame.set_index(frame["date_int"].astype("int32")).sort_index()


def load_factors(code: str) -> tuple[np.ndarray, np.ndarray, float]:
    assert WORKER_FACTOR_DIR is not None
    path = WORKER_FACTOR_DIR / f"{code}.npz"
    if not path.exists():
        raise FileNotFoundError(f"缺少复权系数：{path.name}")
    with np.load(path) as data:
        dates = data["dates"].astype(np.int32, copy=True)
        factors = data["factors"].astype(np.float64, copy=True)
    if not len(dates) or len(dates) != len(factors):
        raise ValueError("复权系数为空或长度不一致")
    if np.any(np.diff(dates) <= 0):
        raise ValueError("复权系数日期没有严格递增")
    if not np.all(np.isfinite(factors)) or np.any(factors <= 0):
        raise ValueError("复权系数含无效值或非正数")
    cutoff_position = np.searchsorted(dates, DATA_END_DATE, side="right") - 1
    if cutoff_position < 0:
        raise ValueError("截止日前没有复权系数")
    return dates, factors, float(factors[cutoff_position])


def date_int_from_datetime(values: pd.Series) -> np.ndarray:
    return (
        values.dt.year.to_numpy(dtype=np.int32) * 10000
        + values.dt.month.to_numpy(dtype=np.int32) * 100
        + values.dt.day.to_numpy(dtype=np.int32)
    )


def normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["日期", "开盘", "最高", "最低", "收盘", "成交量(股)", "成交额(元)"]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"历史文件缺字段：{missing}")
    result = pd.DataFrame()
    result["datetime"] = pd.to_datetime(frame["日期"], errors="coerce")
    result["raw_open"] = pd.to_numeric(frame["开盘"], errors="coerce")
    result["raw_high"] = pd.to_numeric(frame["最高"], errors="coerce")
    result["raw_low"] = pd.to_numeric(frame["最低"], errors="coerce")
    result["raw_close"] = pd.to_numeric(frame["收盘"], errors="coerce")
    result["volume"] = pd.to_numeric(frame["成交量(股)"], errors="coerce")
    result["amount"] = pd.to_numeric(frame["成交额(元)"], errors="coerce")
    for source, target in (("流通股本(股)", "float_shares"), ("总股本(股)", "total_shares")):
        result[target] = pd.to_numeric(frame[source], errors="coerce") if source in frame else np.nan
    result["source_id"] = np.uint8(1)
    return result


def normalize_shsz(frame: pd.DataFrame) -> pd.DataFrame:
    time_column = frame.columns[0]
    result = pd.DataFrame()
    parsed = pd.to_datetime(frame[time_column].astype("string"), format="%Y%m%d%H%M%S", errors="coerce")
    result["datetime"] = parsed + pd.Timedelta(minutes=1)
    for column in ("open", "high", "low", "close"):
        result[f"raw_{column}"] = pd.to_numeric(frame[column], errors="coerce")
    result["volume"] = pd.to_numeric(frame["volume"], errors="coerce") * 100
    result["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    result["float_shares"] = np.nan
    result["total_shares"] = np.nan
    result["source_id"] = np.uint8(2)
    return result


def normalize_q1(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame()
    combined = frame["日期"].astype("string") + " " + frame["时间"].astype("string")
    result["datetime"] = pd.to_datetime(combined, format="%Y-%m-%d %H:%M", errors="coerce")
    mapping = {"开盘": "open", "最高": "high", "最低": "low", "收盘": "close"}
    for source, target in mapping.items():
        result[f"raw_{target}"] = pd.to_numeric(frame[source], errors="coerce")
    result["volume"] = pd.to_numeric(frame["成交量"], errors="coerce") * 100
    result["amount"] = pd.to_numeric(frame["成交额"], errors="coerce")
    result["float_shares"] = np.nan
    result["total_shares"] = np.nan
    result["source_id"] = np.uint8(3)
    return result


def normalize_rolling(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame()
    result["datetime"] = pd.to_datetime(
        frame["time"].astype("string"),
        format="%Y%m%d%H%M%S",
        errors="coerce",
    )
    for column in ("open", "high", "low", "close"):
        result[f"raw_{column}"] = pd.to_numeric(frame[column], errors="coerce")
    result["volume"] = pd.to_numeric(frame["volume"], errors="coerce") * 100
    result["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    result["float_shares"] = np.nan
    result["total_shares"] = np.nan
    result["source_id"] = np.uint8(4)
    return result


def iter_source_chunks(kind: str, member: str) -> Iterator[pd.DataFrame]:
    archive = WORKER_ARCHIVES[kind]
    with archive.open(member) as handle:
        for frame in pd.read_csv(handle, chunksize=WORKER_ROW_GROUP_SIZE, low_memory=False):
            if kind == "base":
                yield normalize_history(frame)
            elif kind == "shsz":
                yield normalize_shsz(frame)
            elif kind == "rolling":
                yield normalize_rolling(frame)
            else:
                yield normalize_q1(frame)


def merge_opening_auction(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """把旧格式单列的 09:30 集合竞价并入 09:31。"""
    times = frame["datetime"]
    source_mask = frame["source_id"].isin([1, 4])
    auction_mask = source_mask & (times.dt.hour == 9) & (times.dt.minute == 30)
    if not auction_mask.any():
        return frame, 0

    work = frame.copy()
    work["_day"] = work["datetime"].dt.normalize()
    remove_indices: list[int] = []
    auction_rows = work.loc[auction_mask]
    next_mask = (
        source_mask
        & (times.dt.hour == 9)
        & (times.dt.minute == 31)
    )
    target_lookup: dict[tuple[int, pd.Timestamp], int] = {}
    for index, row in work.loc[next_mask, ["source_id", "_day"]].iterrows():
        target_lookup.setdefault((int(row["source_id"]), pd.Timestamp(row["_day"])), int(index))
    for (source_id, day), auction in auction_rows.groupby(["source_id", "_day"], sort=False):
        target = target_lookup.get((int(source_id), pd.Timestamp(day)))
        price_columns = ["raw_open", "raw_high", "raw_low", "raw_close"]
        auction_prices = auction[price_columns].to_numpy(dtype=np.float64)
        valid_auction = auction.loc[
            np.isfinite(auction_prices).all(axis=1) & (auction_prices > 0).all(axis=1)
        ]
        if target is not None:
            if len(valid_auction):
                first_auction = valid_auction.sort_values("datetime").iloc[0]
                work.at[target, "raw_open"] = first_auction["raw_open"]
                work.at[target, "raw_high"] = max(
                    float(work.at[target, "raw_high"]),
                    float(valid_auction["raw_high"].max()),
                )
                work.at[target, "raw_low"] = min(
                    float(work.at[target, "raw_low"]),
                    float(valid_auction["raw_low"].min()),
                )
                work.at[target, "volume"] = float(work.at[target, "volume"]) + float(valid_auction["volume"].sum())
                work.at[target, "amount"] = float(work.at[target, "amount"]) + float(valid_auction["amount"].sum())
            remove_indices.extend(int(value) for value in auction.index)
        elif not len(valid_auction):
            remove_indices.extend(int(value) for value in auction.index)
        else:
            ordered = valid_auction.sort_values("datetime")
            target = int(ordered.index[-1])
            work.at[target, "datetime"] = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=31)
            work.at[target, "raw_open"] = ordered.iloc[0]["raw_open"]
            work.at[target, "raw_high"] = valid_auction["raw_high"].max()
            work.at[target, "raw_low"] = valid_auction["raw_low"].min()
            work.at[target, "raw_close"] = ordered.iloc[-1]["raw_close"]
            work.at[target, "volume"] = valid_auction["volume"].sum()
            work.at[target, "amount"] = valid_auction["amount"].sum()
            remove_indices.extend(int(value) for value in auction.index if int(value) != target)
    work = work.drop(index=remove_indices).drop(columns="_day")
    return work, len(remove_indices)


def load_task_frame(task: dict[str, object]) -> tuple[pd.DataFrame, dict[str, object]]:
    frames: list[pd.DataFrame] = []
    q1_overlap_rows_ignored = 0
    rolling_overlap_rows_ignored = 0
    rows_before_auction_merge = 0
    auction_rows_merged = 0
    premerge_duplicate_rows_removed = 0
    premerge_conflicting_rows = 0
    premerge_conflicting_examples: list[str] = []
    value_columns = [
        "datetime",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "volume",
        "amount",
        "float_shares",
        "total_shares",
    ]
    sources = [
        ("base", task.get("base_member")),
        ("shsz", task.get("shsz_member")),
        ("q1", task.get("q1_member")),
        ("rolling", task.get("rolling_member")),
    ]
    for kind, member_value in sources:
        if not member_value:
            continue
        source_parts = list(iter_source_chunks(kind, str(member_value)))
        if not source_parts:
            continue
        source_frame = pd.concat(source_parts, ignore_index=True)
        rows_before_auction_merge += len(source_frame)
        source_duplicate_rows = source_frame.duplicated("datetime", keep="last")
        source_exact_duplicates = source_frame.duplicated(value_columns, keep="last")
        source_conflicts = source_duplicate_rows & ~source_exact_duplicates
        premerge_duplicate_rows_removed += int(source_duplicate_rows.sum())
        premerge_conflicting_rows += int(source_conflicts.sum())
        if source_conflicts.any() and len(premerge_conflicting_examples) < 12:
            premerge_conflicting_examples.extend(
                value.isoformat() if hasattr(value, "isoformat") else str(value)
                for value in source_frame.loc[source_conflicts, "datetime"].head(
                    12 - len(premerge_conflicting_examples)
                )
            )
        source_frame = source_frame.loc[~source_duplicate_rows].copy()
        if kind in {"base", "rolling"}:
            source_frame, merged = merge_opening_auction(source_frame)
            auction_rows_merged += merged
        if kind == "q1" and frames:
            preferred_frames = [
                item for item in frames if len(item) and int(item["source_id"].iloc[0]) == 2
            ]
            if preferred_frames:
                preferred_times = pd.Index(
                    np.concatenate([item["datetime"].to_numpy() for item in preferred_frames])
                )
                overlap = source_frame["datetime"].isin(preferred_times)
                q1_overlap_rows_ignored += int(overlap.sum())
                source_frame = source_frame.loc[~overlap]
        if kind == "rolling" and frames:
            existing_times = pd.Index(
                np.concatenate([item["datetime"].to_numpy() for item in frames])
            )
            overlap = source_frame["datetime"].isin(existing_times)
            rolling_overlap_rows_ignored += int(overlap.sum())
            source_frame = source_frame.loc[~overlap]
        if len(source_frame):
            frames.append(source_frame)
    if not frames:
        raise ValueError("没有可读取的分钟数据")

    combined = pd.concat(frames, ignore_index=True)
    duplicate_rows = combined.duplicated("datetime", keep="last")
    exact_duplicate_rows = combined.duplicated(value_columns, keep="last")
    conflicting_rows = duplicate_rows & ~exact_duplicate_rows
    examples = premerge_conflicting_examples + [
        value.isoformat() if hasattr(value, "isoformat") else str(value)
        for value in combined.loc[conflicting_rows, "datetime"].head(
            max(0, 12 - len(premerge_conflicting_examples))
        )
    ]
    if premerge_conflicting_rows or conflicting_rows.any():
        raise ValueError(
            "同一分钟存在数值不同的重复记录："
            f"同一来源 {premerge_conflicting_rows} 行，来源之间 {int(conflicting_rows.sum())} 行；"
            f"例如 {examples}"
        )
    stats: dict[str, object] = {
        "input_rows_before_dedup": len(combined),
        "input_rows_before_auction_merge": rows_before_auction_merge,
        "opening_auction_rows_merged": auction_rows_merged,
        "q1_overlap_rows_ignored": q1_overlap_rows_ignored,
        "rolling_overlap_rows_ignored": rolling_overlap_rows_ignored,
        "premerge_duplicate_rows_removed": premerge_duplicate_rows_removed,
        "premerge_conflicting_duplicate_rows": premerge_conflicting_rows,
        "duplicate_input_rows_removed": premerge_duplicate_rows_removed + int(duplicate_rows.sum()),
        "conflicting_duplicate_input_rows": premerge_conflicting_rows + int(conflicting_rows.sum()),
        "conflicting_duplicate_examples": examples,
    }
    combined = combined.loc[~duplicate_rows].sort_values("datetime", kind="mergesort")
    valid_times = combined["datetime"].dropna()
    minute_number = valid_times.dt.hour * 60 + valid_times.dt.minute
    standard_mask = (
        minute_number.between(9 * 60 + 31, 11 * 60 + 30)
        | minute_number.between(13 * 60 + 1, 15 * 60)
    )
    standard = pd.DataFrame(
        {
            "day": valid_times.dt.normalize(),
            "minute": minute_number,
        },
        index=valid_times.index,
    ).loc[standard_mask]
    minute_counts = standard.groupby("day")["minute"].nunique()
    missing_by_day = (240 - minute_counts).clip(lower=0)
    missing_days = missing_by_day[missing_by_day > 0]
    stats["days_with_missing_standard_minutes"] = int(len(missing_days))
    stats["missing_standard_minute_rows"] = int(missing_days.sum())
    stats["missing_standard_minute_examples"] = [
        {"date": day.strftime("%Y-%m-%d"), "missing": int(count)}
        for day, count in missing_days.head(12).items()
    ]
    return combined.reset_index(drop=True), stats


def remove_unusable_rows(
    frame: pd.DataFrame,
    daily: pd.DataFrame,
    code: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """删除可严格证明不是有效分钟行情的日期或单行。"""
    date_values = date_int_from_datetime(frame["datetime"])
    day_totals = pd.DataFrame(
        {
            "date_int": date_values,
            "volume_abs": frame["volume"].abs().to_numpy(dtype=np.float64),
            "amount_abs": frame["amount"].abs().to_numpy(dtype=np.float64),
        }
    ).groupby("date_int", sort=False).agg(
        volume_abs=("volume_abs", "sum"),
        amount_abs=("amount_abs", "sum"),
        rows=("date_int", "size"),
    )
    daily_dates = {int(value) for value in daily.index}
    empty_nontrading_days = {
        int(day)
        for day, row in day_totals.iterrows()
        if float(row["volume_abs"]) == 0.0
        and float(row["amount_abs"]) == 0.0
        and int(day) not in daily_dates
    }
    known_days = set(KNOWN_CORRUPT_DAYS.get(code, set())) & {
        int(value) for value in day_totals.index
    }
    remove_days = empty_nontrading_days | known_days
    day_mask = np.isin(date_values, np.fromiter(remove_days, dtype=np.int32)) if remove_days else np.zeros(len(frame), dtype=bool)

    known_timestamps = {
        pd.Timestamp(value) for value in KNOWN_CORRUPT_TIMESTAMPS.get(code, set())
    }
    timestamp_mask = frame["datetime"].isin(known_timestamps).to_numpy() if known_timestamps else np.zeros(len(frame), dtype=bool)
    raw_prices = frame[["raw_open", "raw_high", "raw_low", "raw_close"]].to_numpy(
        dtype=np.float64
    )
    zero_no_trade_mask = (
        (raw_prices <= 0).any(axis=1)
        & (frame["volume"].to_numpy(dtype=np.float64) == 0)
        & (frame["amount"].to_numpy(dtype=np.float64) == 0)
    )
    zero_no_trade_only_mask = zero_no_trade_mask & ~day_mask & ~timestamp_mask
    remove_mask = day_mask | timestamp_mask | zero_no_trade_mask
    examples: list[dict[str, object]] = []
    for day in sorted(empty_nontrading_days)[:12]:
        examples.append({"date": day, "reason": "整日成交量和成交额均为0，且本地日线没有该日"})
    for day in sorted(known_days):
        if len(examples) >= 12:
            break
        examples.append({"date": day, "reason": "已人工核实的原始坏数据"})
    for value in sorted(known_timestamps):
        if len(examples) >= 12:
            break
        examples.append({"datetime": value.isoformat(), "reason": "已人工核实的原始坏行"})

    for value in frame.loc[zero_no_trade_only_mask, "datetime"].head(max(0, 12 - len(examples))):
        examples.append({"datetime": value.isoformat(), "reason": "无成交且价格不是有效正数"})

    result = frame.loc[~remove_mask].reset_index(drop=True)
    if result.empty:
        raise ValueError("删除无效记录后没有剩余分钟数据")
    return result, {
        "empty_nontrading_days_removed": len(empty_nontrading_days),
        "empty_nontrading_rows_removed": int(np.count_nonzero(day_mask & np.isin(date_values, np.fromiter(empty_nontrading_days, dtype=np.int32)))) if empty_nontrading_days else 0,
        "known_corrupt_days_removed": len(known_days),
        "known_corrupt_day_rows_removed": int(np.count_nonzero(np.isin(date_values, np.fromiter(known_days, dtype=np.int32)))) if known_days else 0,
        "known_corrupt_timestamp_rows_removed": int(timestamp_mask.sum()),
        "zero_no_trade_rows_removed": int(zero_no_trade_only_mask.sum()),
        "unusable_rows_removed": int(remove_mask.sum()),
        "removed_unusable_examples": examples,
    }


def merge_daily_aggregate(target: dict[int, dict[str, float]], frame: pd.DataFrame) -> None:
    work = frame.assign(date_int=date_int_from_datetime(frame["datetime"]))
    grouped = work.groupby("date_int", sort=False).agg(
        raw_open=("raw_open", "first"),
        raw_high=("raw_high", "max"),
        raw_low=("raw_low", "min"),
        raw_close=("raw_close", "last"),
        volume=("volume", "sum"),
        amount=("amount", "sum"),
        rows=("datetime", "size"),
    )
    for day, row in grouped.iterrows():
        key = int(day)
        if key not in target:
            target[key] = {name: float(row[name]) for name in grouped.columns}
            continue
        current = target[key]
        current["raw_high"] = max(current["raw_high"], float(row["raw_high"]))
        current["raw_low"] = min(current["raw_low"], float(row["raw_low"]))
        current["raw_close"] = float(row["raw_close"])
        current["volume"] += float(row["volume"])
        current["amount"] += float(row["amount"])
        current["rows"] += float(row["rows"])


def compare_daily(
    aggregates: dict[int, dict[str, float]],
    daily: pd.DataFrame,
) -> dict[str, object]:
    result: dict[str, object] = {
        "daily_common_days": 0,
        "daily_price_mismatch_days": 0,
        "daily_volume_mismatch_days": 0,
        "daily_amount_mismatch_days": 0,
        "minute_days_missing_daily_reference": 0,
        "daily_days_missing_minutes": 0,
        "daily_mismatch_examples": [],
    }
    if not aggregates or daily.empty:
        result["minute_days_missing_daily_reference"] = len(aggregates)
        return result
    minute_days = set(aggregates)
    daily_days = {int(value) for value in daily.index}
    start, end = min(minute_days), max(minute_days)
    daily_in_range = {day for day in daily_days if start <= day <= end}
    result["minute_days_missing_daily_reference"] = len(minute_days - daily_days)
    result["daily_days_missing_minutes"] = len(daily_in_range - minute_days)
    examples: list[dict[str, object]] = []
    price_errors: list[float] = []
    volume_relative_errors: list[float] = []
    amount_relative_errors: list[float] = []
    for day in sorted(minute_days & daily_days):
        minute = aggregates[day]
        reference = daily.loc[day]
        result["daily_common_days"] = int(result["daily_common_days"]) + 1
        price_error = max(
            abs(minute[f"raw_{name}"] - float(reference[f"daily_{name}"]))
            for name in ("open", "high", "low", "close")
        )
        reference_price = max(
            abs(float(reference[f"daily_{name}"])) for name in ("open", "high", "low", "close")
        )
        volume_ref = float(reference["daily_volume"])
        amount_ref = float(reference["daily_amount"])
        volume_error = abs(minute["volume"] - volume_ref)
        amount_error = abs(minute["amount"] - amount_ref)
        volume_relative = volume_error / max(abs(volume_ref), 1.0)
        amount_relative = amount_error / max(abs(amount_ref), 1.0)
        price_errors.append(price_error)
        volume_relative_errors.append(volume_relative)
        amount_relative_errors.append(amount_relative)
        price_bad = price_error > max(0.021, reference_price * 0.002)
        volume_bad = volume_error > max(1000.0, abs(volume_ref) * 0.01)
        amount_bad = amount_error > max(1000.0, abs(amount_ref) * 0.01)
        if price_bad:
            result["daily_price_mismatch_days"] = int(result["daily_price_mismatch_days"]) + 1
        if volume_bad:
            result["daily_volume_mismatch_days"] = int(result["daily_volume_mismatch_days"]) + 1
        if amount_bad:
            result["daily_amount_mismatch_days"] = int(result["daily_amount_mismatch_days"]) + 1
        if (price_bad or volume_bad or amount_bad) and len(examples) < 12:
            examples.append(
                {
                    "date": day,
                    "price": price_bad,
                    "volume": volume_bad,
                    "amount": amount_bad,
                }
            )
    result["daily_mismatch_examples"] = examples
    if price_errors:
        result["daily_price_error_p99"] = float(np.quantile(price_errors, 0.99))
        result["daily_price_error_max"] = float(max(price_errors))
        result["daily_volume_relative_error_p99"] = float(np.quantile(volume_relative_errors, 0.99))
        result["daily_volume_relative_error_max"] = float(max(volume_relative_errors))
        result["daily_amount_relative_error_p99"] = float(np.quantile(amount_relative_errors, 0.99))
        result["daily_amount_relative_error_max"] = float(max(amount_relative_errors))
    return result


def arrow_table(frame: pd.DataFrame) -> pa.Table:
    arrays = [
        pa.array(frame["datetime"], type=pa.timestamp("s"), from_pandas=True),
        pa.array(frame["open"], type=pa.float64(), from_pandas=True),
        pa.array(frame["high"], type=pa.float64(), from_pandas=True),
        pa.array(frame["low"], type=pa.float64(), from_pandas=True),
        pa.array(frame["close"], type=pa.float64(), from_pandas=True),
        pa.array(frame["volume"], type=pa.int64(), from_pandas=True),
        pa.array(frame["amount"], type=pa.float64(), from_pandas=True),
        pa.array(frame["float_shares"], type=pa.int64(), from_pandas=True),
        pa.array(frame["total_shares"], type=pa.int64(), from_pandas=True),
        pa.array(frame["qfq_ratio"], type=pa.float64(), from_pandas=True),
        pa.array(frame["source_id"], type=pa.uint8(), from_pandas=True),
    ]
    return pa.Table.from_arrays(arrays, schema=OUTPUT_SCHEMA)


def convert_one(task: dict[str, object]) -> dict[str, object]:
    code = str(task["code"])
    started = time.monotonic()
    assert WORKER_OUTPUT_DIR is not None
    output_path = WORKER_OUTPUT_DIR / "data" / f"{code}.parquet"
    temporary_path = output_path.with_suffix(".part.parquet")
    temporary_path.unlink(missing_ok=True)
    factors_dates, factors, base_factor = load_factors(code)
    daily = load_daily_reference(code)
    aggregates: dict[int, dict[str, float]] = {}
    counters: dict[str, int] = {
        "rows": 0,
        "null_core_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_volume_or_amount_rows": 0,
        "off_session_rows": 0,
        "extreme_price_rows": 0,
        "non_increasing_timestamps": 0,
        "duplicate_timestamps": 0,
        "large_intraday_jump_rows": 0,
        "missing_float_shares_rows": 0,
        "missing_total_shares_rows": 0,
        "source_1_rows": 0,
        "source_2_rows": 0,
        "source_3_rows": 0,
        "source_4_rows": 0,
    }
    minimum_time: pd.Timestamp | None = None
    maximum_time: pd.Timestamp | None = None
    previous_time: pd.Timestamp | None = None
    previous_close: float | None = None
    previous_day: int | None = None
    writer: pq.ParquetWriter | None = None
    daily_comparison: dict[str, object] = {}
    duplicate_stats: dict[str, object] = {}

    try:
        combined, duplicate_stats = load_task_frame(task)
        combined, removal_stats = remove_unusable_rows(combined, daily, code)
        duplicate_stats.update(removal_stats)
        for start in range(0, len(combined), WORKER_ROW_GROUP_SIZE):
                frame = combined.iloc[start : start + WORKER_ROW_GROUP_SIZE].copy()
                core = ["datetime", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]
                null_rows = frame[core].isna().any(axis=1)
                counters["null_core_rows"] += int(null_rows.sum())
                if null_rows.any():
                    raise ValueError(f"核心字段有空值：{int(null_rows.sum())} 行")
                numeric_core = frame[["raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]]
                if not np.isfinite(numeric_core.to_numpy(dtype=np.float64)).all():
                    raise ValueError("核心字段含无穷大或无效数字")
                date_int = date_int_from_datetime(frame["datetime"])
                positions = np.searchsorted(factors_dates, date_int)
                valid_positions = positions < len(factors_dates)
                exact = np.zeros(len(frame), dtype=bool)
                exact[valid_positions] = factors_dates[positions[valid_positions]] == date_int[valid_positions]
                if not np.all(exact):
                    missing = sorted({int(value) for value in date_int[~exact]})[:12]
                    raise ValueError(f"分钟日期缺复权系数：{missing}")

                reference = daily.reindex(date_int) if not daily.empty else None
                if reference is not None:
                    for column, daily_column in (
                        ("float_shares", "daily_float_shares"),
                        ("total_shares", "daily_total_shares"),
                    ):
                        fill_values = reference[daily_column].to_numpy(dtype=np.float64)
                        current = frame[column].to_numpy(dtype=np.float64)
                        missing_mask = ~np.isfinite(current)
                        current[missing_mask] = fill_values[missing_mask]
                        frame[column] = current

                prices = frame[["raw_open", "raw_high", "raw_low", "raw_close"]]
                invalid = (
                    (frame["raw_high"] < prices.max(axis=1))
                    | (frame["raw_low"] > prices.min(axis=1))
                    | (frame["raw_low"] > frame["raw_high"])
                )
                counters["invalid_ohlc_rows"] += int(invalid.sum())
                negative = (frame["volume"] < 0) | (frame["amount"] < 0)
                counters["negative_volume_or_amount_rows"] += int(negative.sum())
                extreme = (prices <= 0).any(axis=1) | (prices > 10000).any(axis=1)
                counters["extreme_price_rows"] += int(extreme.sum())
                minutes = frame["datetime"].dt.hour * 60 + frame["datetime"].dt.minute
                off_session = ~(
                    minutes.between(9 * 60 + 31, 11 * 60 + 30)
                    | minutes.between(13 * 60 + 1, 15 * 60)
                )
                counters["off_session_rows"] += int(off_session.sum())

                times = frame["datetime"]
                if previous_time is not None and len(times):
                    counters["non_increasing_timestamps"] += int(times.iloc[0] <= previous_time)
                    counters["duplicate_timestamps"] += int(times.iloc[0] == previous_time)
                differences = times.diff()
                counters["non_increasing_timestamps"] += int((differences.iloc[1:] <= pd.Timedelta(0)).sum())
                counters["duplicate_timestamps"] += int((differences.iloc[1:] == pd.Timedelta(0)).sum())

                closes = frame["raw_close"].to_numpy(dtype=np.float64)
                jump_previous = np.empty(len(frame), dtype=np.float64)
                jump_previous[0] = previous_close if previous_close is not None else np.nan
                if len(frame) > 1:
                    jump_previous[1:] = closes[:-1]
                prior_days = np.empty(len(frame), dtype=np.int32)
                prior_days[0] = previous_day if previous_day is not None else -1
                if len(frame) > 1:
                    prior_days[1:] = date_int[:-1]
                same_day = prior_days == date_int
                valid_previous = np.isfinite(jump_previous) & (jump_previous != 0)
                close_ratio = np.full(len(frame), np.nan, dtype=np.float64)
                np.divide(closes, jump_previous, out=close_ratio, where=valid_previous)
                jumps = same_day & valid_previous & (np.abs(close_ratio - 1) > 0.40)
                counters["large_intraday_jump_rows"] += int(jumps.sum())

                merge_daily_aggregate(aggregates, frame)
                ratio = factors[positions] / base_factor
                for name in ("open", "high", "low", "close"):
                    frame[name] = frame[f"raw_{name}"].to_numpy(dtype=np.float64) * ratio
                frame["qfq_ratio"] = ratio
                frame["volume"] = np.rint(frame["volume"]).astype("int64")
                frame["amount"] = frame["amount"].astype("float64")
                for column in ("float_shares", "total_shares"):
                    counters[f"missing_{column}_rows"] += int(frame[column].isna().sum())
                    frame[column] = pd.array(np.rint(frame[column]), dtype="Int64")

                for source_id, source_rows in frame["source_id"].value_counts().items():
                    counters[f"source_{int(source_id)}_rows"] += int(source_rows)
                counters["rows"] += len(frame)
                current_min = times.iloc[0]
                current_max = times.iloc[-1]
                minimum_time = current_min if minimum_time is None else min(minimum_time, current_min)
                maximum_time = current_max if maximum_time is None else max(maximum_time, current_max)
                previous_time = current_max
                previous_close = float(closes[-1])
                previous_day = int(date_int[-1])

                table = arrow_table(frame)
                if writer is None:
                    writer = pq.ParquetWriter(
                        temporary_path,
                        OUTPUT_SCHEMA,
                        compression="zstd",
                        compression_level=WORKER_COMPRESSION_LEVEL,
                        use_dictionary=True,
                        write_statistics=True,
                    )
                if shutil.disk_usage(WORKER_OUTPUT_DIR).free < WORKER_MIN_FREE_BYTES:
                    raise RuntimeError("磁盘剩余空间低于设定值，已停止当前股票")
                writer.write_table(table, row_group_size=WORKER_ROW_GROUP_SIZE)
        if writer is None:
            raise ValueError("没有可写入的分钟数据")
        daily_comparison = compare_daily(aggregates, daily)
        common_days = int(daily_comparison.get("daily_common_days") or 0)
        if common_days >= 20:
            systematic_fields = (
                "daily_price_mismatch_days",
                "daily_volume_mismatch_days",
                "daily_amount_mismatch_days",
            )
            if any(int(daily_comparison.get(field) or 0) / common_days > 0.90 for field in systematic_fields):
                raise ValueError("分钟汇总与日线在九成以上日期严重不一致，疑似单位或来源错误")
        writer.close()
        writer = None
        os.replace(temporary_path, output_path)
    except Exception:
        try:
            if writer is not None:
                writer.close()
        except Exception:
            pass
        try:
            temporary_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    result: dict[str, object] = {
        "code": code,
        "status": "ok",
        "source_bytes": int(task["source_bytes"]),
        "output_bytes": output_path.stat().st_size,
        "first_time": minimum_time.isoformat() if minimum_time is not None else None,
        "last_time": maximum_time.isoformat() if maximum_time is not None else None,
        "days": len(aggregates),
        "seconds": round(time.monotonic() - started, 3),
        **duplicate_stats,
        **counters,
        **daily_comparison,
    }
    return result


def safe_convert(task: dict[str, object]) -> dict[str, object]:
    try:
        return convert_one(task)
    except Exception as exc:
        return {
            "code": str(task["code"]),
            "status": "error",
            "source_bytes": int(task["source_bytes"]),
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }


def load_completed(manifest: Path, data_dir: Path) -> set[str]:
    latest: dict[str, dict[str, object]] = {}
    if not manifest.exists():
        return set()
    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = str(item.get("code") or "")
            if code:
                latest[code] = item
    completed: set[str] = set()
    for code, item in latest.items():
        path = data_dir / f"{code}.parquet"
        if item.get("status") != "ok" or not path.exists():
            continue
        expected_bytes = int(item.get("output_bytes") or 0)
        expected_rows = int(item.get("rows") or 0)
        if expected_bytes and path.stat().st_size != expected_bytes:
            continue
        try:
            actual_rows = pq.ParquetFile(path).metadata.num_rows
        except Exception:
            continue
        if expected_rows and actual_rows != expected_rows:
            continue
        completed.add(code)
    return completed


def append_manifest(handle, item: dict[str, object]) -> None:
    handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    handle.flush()


def acquire_conversion_lock(path: Path):
    handle = path.open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        handle.close()
        raise RuntimeError("这个输出目录已经有转换程序在运行") from exc
    atexit.register(handle.close)
    return handle


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    output_dir = args.output_dir.resolve()
    daily_dir = args.daily_dir.resolve()
    parquet_dir = output_dir / "data"
    metadata_dir = output_dir / "_metadata"
    factor_dir = metadata_dir / "adj_factors"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    lock_handle = acquire_conversion_lock(metadata_dir / "conversion.lock")
    _ = lock_handle
    for leftover in parquet_dir.glob("*.part.parquet"):
        leftover.unlink(missing_ok=True)
    initial_free_gib = shutil.disk_usage(output_dir).free / (1024**3)
    if initial_free_gib < args.min_free_gib:
        raise RuntimeError(
            f"磁盘只剩 {initial_free_gib:.3f} GiB，低于设定的 {args.min_free_gib:.3f} GiB"
        )
    manifest = metadata_dir / "conversion_manifest.jsonl"

    tasks, all_source_bytes = make_tasks(data_dir)
    selected = {code.lower() for code in (args.codes or [])}
    if args.codes_file:
        selected.update(
            line.strip().lower()
            for line in args.codes_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if selected:
        tasks = [task for task in tasks if str(task["code"]) in selected]
    if args.limit is not None:
        tasks = tasks[: args.limit]
    completed = set() if args.force else load_completed(manifest, parquet_dir)
    tasks = [task for task in tasks if str(task["code"]) not in completed]
    missing_factors = [
        str(task["code"]) for task in tasks if not (factor_dir / f"{task['code']}.npz").exists()
    ]
    if missing_factors:
        raise RuntimeError(f"还有 {len(missing_factors)} 只股票缺复权系数，例如：{missing_factors[:10]}")

    print(
        f"待处理：{len(tasks)} 只；已完成：{len(completed)} 只；"
        f"输入 CSV 合计：{sum(int(item['source_bytes']) for item in tasks) / 1e9:.3f} GB",
        flush=True,
    )
    if not tasks:
        return

    initializer_args = (
        str(data_dir / BASE_ARCHIVE_NAME),
        str(data_dir / SHSZ_ARCHIVE_NAME),
        str(data_dir / Q1_ARCHIVE_NAME),
        str(data_dir / ROLLING_ARCHIVE_RELATIVE),
        str(output_dir),
        str(daily_dir),
        str(factor_dir),
        args.row_group_size,
        args.compression_level,
        int(args.min_free_gib * 1024**3),
    )
    task_iterator = iter(tasks)
    processed_source = 0
    successful_source = 0
    written_output = 0
    finished = 0
    errors = 0
    stop_submitting = False
    stop_reason: str | None = None

    with manifest.open("a", encoding="utf-8", newline="\n") as manifest_handle:
        with ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=init_worker,
            initargs=initializer_args,
        ) as executor:
            futures = {}
            for _ in range(min(args.workers, len(tasks))):
                task = next(task_iterator, None)
                if task is not None:
                    futures[executor.submit(safe_convert, task)] = task
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task = futures.pop(future)
                    try:
                        item = future.result()
                    except Exception as exc:
                        item = {
                            "code": str(task["code"]),
                            "status": "error",
                            "source_bytes": int(task["source_bytes"]),
                            "error": f"工作进程意外退出：{exc}",
                        }
                        stop_submitting = True
                        stop_reason = str(item["error"])
                    append_manifest(manifest_handle, item)
                    finished += 1
                    processed_source += int(item.get("source_bytes") or 0)
                    if item.get("status") == "ok":
                        successful_source += int(item.get("source_bytes") or 0)
                        written_output += int(item.get("output_bytes") or 0)
                    else:
                        errors += 1
                    free_gib = shutil.disk_usage(output_dir).free / (1024**3)
                    if free_gib < args.min_free_gib:
                        stop_submitting = True
                        stop_reason = (
                            f"磁盘剩余 {free_gib:.3f} GiB，低于设定的 {args.min_free_gib:.3f} GiB"
                        )
                    if finished % 10 == 0 or item.get("status") != "ok":
                        estimate = (
                            all_source_bytes * written_output / successful_source
                            if successful_source and written_output
                            else math.nan
                        )
                        print(
                            f"[{finished}/{len(tasks)}] {item['code']} {item['status']}；"
                            f"输出 {written_output / (1024**3):.3f} GiB；"
                            f"全量估算 {estimate / (1024**3):.3f} GiB；"
                            f"剩余 {free_gib:.3f} GiB；错误 {errors}",
                            flush=True,
                        )
                    if not stop_submitting:
                        next_task = next(task_iterator, None)
                        if next_task is not None:
                            futures[executor.submit(safe_convert, next_task)] = next_task

    if stop_submitting:
        raise RuntimeError(stop_reason or "转换已暂停提交新股票")
    print(f"转换结束：成功 {finished - errors}，错误 {errors}", flush=True)
    if errors:
        raise RuntimeError(f"有 {errors} 只股票处理失败，请查看 conversion_manifest.jsonl")


if __name__ == "__main__":
    main()
