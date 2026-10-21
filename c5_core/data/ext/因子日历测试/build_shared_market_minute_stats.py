# -*- coding: utf-8 -*-
"""一次读取全部分钟文件，生成可被多组因子共用的市场分钟统计表。"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import glob
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR
    / "duckdb_factor_store"
    / "shared_market_minute_stats_2015_2026.parquet"
)
DEFAULT_TEMP_DIR = BASE_DIR / "duckdb_factor_store" / "tmp" / "shared_market"
TABLE_VERSION = 1

TRADING_MINUTES = np.concatenate(
    [
        np.arange(9 * 60 + 31, 11 * 60 + 31, dtype=np.int16),
        np.arange(13 * 60 + 1, 15 * 60 + 1, dtype=np.int16),
    ]
)

PARTIAL_COLUMNS = (
    "slot",
    "market_amount_total",
    "amount_stock_count",
    "return_stock_count",
    "return_sum",
    "return_square_sum",
    "positive_count",
    "positive_sum",
    "positive_square_sum",
    "positive_cube_sum",
    "negative_count",
    "negative_sum",
    "negative_square_sum",
    "negative_cube_sum",
)

OUTPUT_COLUMNS = (
    "date",
    "datetime",
    "minute_of_day",
    "market_day_index",
    "market_amount_total",
    "market_mean_return",
    "market_std_return_samp",
    "market_std_return_pop",
    "daily_market_std_samp_mean",
    "daily_market_std_pop_mean",
    "positive_return_mean",
    "negative_return_mean",
    "return_stock_count",
    "amount_stock_count",
    "deviation_skew",
    "is_non_differentiation_samp",
    "is_non_differentiation_pop",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-glob",
        default=str(factor_store.DEFAULT_MINUTE_DIR / "*.parquet"),
        help="分钟 Parquet 文件匹配表达式",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--yearly-dir",
        type=Path,
        help="按自然年分别输出到此目录；设置后不写单一输出文件",
    )
    parser.add_argument(
        "--yearly-manifest",
        type=Path,
        help="年度分片说明文件；默认写在年度目录内",
    )
    parser.add_argument(
        "--meta",
        type=Path,
        help="说明文件位置；不填时与输出文件同名并使用 .meta.json 后缀",
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-05-29")
    parser.add_argument(
        "--threads",
        type=int,
        default=max(1, min(8, os.cpu_count() or 4)),
    )
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--row-group-size", type=int, default=100_000)
    parser.add_argument(
        "--yearly-scan-mode",
        choices=("streaming", "repeated"),
        default="streaming",
        help=(
            "年度文件首次生成时的读取方式。streaming让每个原始分钟文件只读一次；"
            "repeated保留原来逐年读取的方式。"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使现有文件检查正确也重新生成",
    )
    return parser.parse_args(argv)


def _date_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    lower = pd.Timestamp(start).normalize()
    upper_inclusive = pd.Timestamp(end).normalize()
    if upper_inclusive < lower:
        raise ValueError("结束日期必须不早于开始日期。")
    return lower, upper_inclusive + pd.Timedelta(days=1)


def _sql_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").replace("'", "''")


def _read_parquet_sql(paths: Sequence[str | Path]) -> str:
    if not paths:
        raise ValueError("至少需要一个分钟文件。")
    quoted = ", ".join(f"'{_sql_path(path)}'" for path in paths)
    return (
        f"read_parquet([{quoted}], union_by_name = true, filename = true)"
    )


def build_query(
    minute_paths: Sequence[str | Path],
    start: str,
    end: str,
    market_day_offset: int = 0,
) -> str:
    """生成单次读取全部分钟文件的市场统计查询。"""

    lower, upper = _date_bounds(start, end)
    source = _read_parquet_sql(minute_paths)
    return f"""
        WITH raw AS (
            SELECT
                filename,
                CAST(datetime AS DATE) AS date,
                datetime,
                CAST(open AS DOUBLE) AS minute_open,
                CAST(close AS DOUBLE) AS minute_close,
                CAST(amount AS DOUBLE) AS minute_amount
            FROM {source}
            WHERE datetime >= TIMESTAMP '{lower:%Y-%m-%d 00:00:00}'
              AND datetime < TIMESTAMP '{upper:%Y-%m-%d 00:00:00}'
        ),
        ordered AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY filename, date ORDER BY datetime
                ) AS minute_number,
                lag(minute_close) OVER (
                    PARTITION BY filename, date ORDER BY datetime
                ) AS previous_close
            FROM raw
        ),
        minute_returns AS (
            SELECT
                date,
                datetime,
                minute_amount,
                CASE
                    WHEN minute_number = 1
                    THEN minute_close / nullif(minute_open, 0) - 1.0
                    ELSE minute_close / nullif(previous_close, 0) - 1.0
                END AS return_1m
            FROM ordered
        ),
        direction_means AS (
            SELECT
                *,
                avg(return_1m) FILTER (WHERE return_1m > 0) OVER minute_group
                    AS positive_return_mean,
                avg(return_1m) FILTER (WHERE return_1m < 0) OVER minute_group
                    AS negative_return_mean
            FROM minute_returns
            WINDOW minute_group AS (PARTITION BY date, datetime)
        ),
        market_raw AS (
            SELECT
                date,
                datetime,
                CAST(
                    extract(hour FROM datetime) * 60
                        + extract(minute FROM datetime)
                    AS SMALLINT
                ) AS minute_of_day,
                sum(minute_amount) AS market_amount_total,
                avg(return_1m) AS market_mean_return,
                stddev_samp(return_1m) AS market_std_return_samp,
                stddev_pop(return_1m) AS market_std_return_pop,
                max(positive_return_mean) AS positive_return_mean,
                max(negative_return_mean) AS negative_return_mean,
                count(return_1m) AS return_stock_count,
                count(minute_amount) AS amount_stock_count,
                skewness(
                    CASE
                        WHEN return_1m IS NULL THEN NULL
                        WHEN return_1m > 0
                        THEN return_1m - positive_return_mean
                        WHEN return_1m < 0
                        THEN return_1m - negative_return_mean
                        ELSE 0.0
                    END
                ) AS deviation_skew
            FROM direction_means
            GROUP BY date, datetime
        ),
        with_daily_values AS (
            SELECT
                *,
                dense_rank() OVER (ORDER BY date) - 1
                    + {int(market_day_offset)} AS market_day_index,
                avg(market_std_return_samp) OVER (PARTITION BY date)
                    AS daily_market_std_samp_mean,
                avg(market_std_return_pop) OVER (PARTITION BY date)
                    AS daily_market_std_pop_mean
            FROM market_raw
        )
        SELECT
            date,
            datetime,
            minute_of_day,
            CAST(market_day_index AS INTEGER) AS market_day_index,
            market_amount_total,
            market_mean_return,
            market_std_return_samp,
            market_std_return_pop,
            daily_market_std_samp_mean,
            daily_market_std_pop_mean,
            positive_return_mean,
            negative_return_mean,
            CAST(return_stock_count AS INTEGER) AS return_stock_count,
            CAST(amount_stock_count AS INTEGER) AS amount_stock_count,
            deviation_skew,
            market_std_return_samp < daily_market_std_samp_mean
                AS is_non_differentiation_samp,
            market_std_return_pop < daily_market_std_pop_mean
                AS is_non_differentiation_pop
        FROM with_daily_values
        ORDER BY datetime
    """


def _normalise_glob(value: str) -> str:
    return os.path.abspath(os.path.expanduser(value)).replace("\\", "/")


def list_source_files(source_glob: str) -> list[Path]:
    files = sorted(
        {
            Path(value).resolve()
            for value in glob.glob(
                os.path.expanduser(source_glob), recursive=True
            )
            if Path(value).is_file()
            and Path(value).suffix.lower() == ".parquet"
        },
        key=lambda path: str(path).lower(),
    )
    if not files:
        raise FileNotFoundError(f"没有找到分钟 Parquet 文件：{source_glob}")
    return files


def list_market_stat_files(value: str | Path) -> list[Path]:
    raw = os.path.expanduser(str(value))
    candidate = Path(raw)
    if candidate.is_dir():
        pattern = str(candidate / "*.parquet")
    elif any(character in raw for character in "*?["):
        pattern = raw
    elif candidate.is_file():
        pattern = raw
    else:
        raise FileNotFoundError(f"没有找到共用市场分钟表：{value}")
    files = sorted(
        {
            Path(item).resolve()
            for item in glob.glob(pattern)
            if Path(item).is_file()
            and Path(item).suffix.lower() == ".parquet"
        },
        key=lambda path: str(path).lower(),
    )
    if not files:
        raise FileNotFoundError(f"没有找到共用市场分钟表：{value}")
    return files


def market_read_sql(value: str | Path) -> str:
    files = list_market_stat_files(value)
    quoted = ", ".join(f"'{_sql_path(path)}'" for path in files)
    return f"read_parquet([{quoted}], union_by_name = true)"


def source_fingerprint(files: Sequence[Path]) -> dict[str, object]:
    digest = hashlib.sha256()
    total_bytes = 0
    newest_mtime_ns = 0
    for path in files:
        stat = path.stat()
        total_bytes += stat.st_size
        newest_mtime_ns = max(newest_mtime_ns, stat.st_mtime_ns)
        identity = (
            f"{path.resolve().as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
        )
        digest.update(identity.encode("utf-8"))
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "newest_mtime_ns": newest_mtime_ns,
        "sha256_path_size_mtime": digest.hexdigest(),
        "first_file": str(files[0]),
        "last_file": str(files[-1]),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expected_signature(
    source_glob: str,
    source_info: dict[str, object],
    start: str,
    end: str,
    market_day_offset: int = 0,
) -> dict[str, object]:
    lower, upper = _date_bounds(start, end)
    return {
        "table_version": TABLE_VERSION,
        "source_glob": _normalise_glob(source_glob),
        "source": source_info,
        "start": lower.strftime("%Y-%m-%d"),
        "end": (upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "market_day_offset": int(market_day_offset),
        "columns": list(OUTPUT_COLUMNS),
    }


def _parquet_shape(path: Path) -> tuple[list[str], int]:
    parquet = pq.ParquetFile(path)
    return parquet.schema_arrow.names, parquet.metadata.num_rows


def can_reuse(
    output_path: Path,
    meta_path: Path,
    expected_signature: dict[str, object],
) -> tuple[bool, str]:
    if not output_path.is_file():
        return False, "输出文件不存在"
    if not meta_path.is_file():
        return False, "说明文件不存在"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"说明文件无法读取：{exc}"
    if meta.get("status") != "completed":
        return False, "说明文件没有完成标记"
    if meta.get("signature") != expected_signature:
        return False, "日期、来源或版本已经变化"
    recorded_output = meta.get("output")
    if not isinstance(recorded_output, dict):
        return False, "说明文件缺少输出记录"
    if recorded_output.get("path") != str(output_path.resolve()):
        return False, "说明文件记录的输出位置不同"
    try:
        columns, rows = _parquet_shape(output_path)
    except Exception as exc:
        return False, f"输出文件无法读取：{exc}"
    if columns != list(OUTPUT_COLUMNS):
        return False, "输出字段不完整或顺序不同"
    if rows <= 0 or rows != recorded_output.get("rows"):
        return False, "输出行数不正确"
    if output_path.stat().st_size != recorded_output.get("bytes"):
        return False, "输出文件大小已经变化"
    if _sha256(output_path) != recorded_output.get("sha256"):
        return False, "输出文件内容已经变化"
    return True, "现有文件检查正确"


def _configure_connection(
    connection: duckdb.DuckDBPyConnection,
    threads: int,
    memory_limit: str,
    temp_dir: Path,
) -> None:
    if threads < 1:
        raise ValueError("线程数必须至少为1。")
    if not memory_limit.strip():
        raise ValueError("内存上限不能为空。")
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_memory = memory_limit.replace("'", "''")
    connection.execute(f"SET threads = {threads}")
    connection.execute(f"SET memory_limit = '{safe_memory}'")
    connection.execute(f"SET temp_directory = '{_sql_path(temp_dir.resolve())}'")
    connection.execute("SET preserve_insertion_order = false")


def _validate_built_file(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    market_day_offset: int = 0,
) -> dict[str, object]:
    columns, parquet_rows = _parquet_shape(path)
    if columns != list(OUTPUT_COLUMNS):
        raise RuntimeError(
            f"生成文件字段错误：实际={columns}；预期={list(OUTPUT_COLUMNS)}"
        )
    summary = connection.execute(
        """
        SELECT
            count(*) AS rows,
            count(DISTINCT datetime) AS unique_datetimes,
            count(DISTINCT date) AS trading_days,
            min(date) AS first_date,
            max(date) AS last_date,
            min(datetime) AS first_datetime,
            max(datetime) AS last_datetime,
            min(market_day_index) AS min_day_index,
            max(market_day_index) AS max_day_index,
            count(*) FILTER (
                WHERE date <> CAST(datetime AS DATE)
            ) AS date_mismatches,
            count(*) FILTER (
                WHERE minute_of_day <>
                    extract(hour FROM datetime) * 60
                    + extract(minute FROM datetime)
            ) AS minute_mismatches,
            count(*) FILTER (
                WHERE return_stock_count < 0 OR amount_stock_count < 0
            ) AS negative_counts,
            count(*) FILTER (
                WHERE market_std_return_samp < 0
                   OR market_std_return_pop < 0
            ) AS negative_standard_deviations
        FROM read_parquet(?)
        """,
        [str(path)],
    ).fetchdf().iloc[0]
    rows = int(summary["rows"])
    trading_days = int(summary["trading_days"])
    if rows <= 0 or parquet_rows != rows:
        raise RuntimeError("指定日期内没有可用分钟记录，或文件行数检查失败。")
    if int(summary["unique_datetimes"]) != rows:
        raise RuntimeError("生成文件包含重复的市场分钟时间。")
    if int(summary["date_mismatches"]) or int(summary["minute_mismatches"]):
        raise RuntimeError("生成文件中的日期或分钟编号不正确。")
    if int(summary["negative_counts"]) or int(
        summary["negative_standard_deviations"]
    ):
        raise RuntimeError("生成文件包含不合理的计数或标准差。")
    if int(summary["min_day_index"]) != market_day_offset:
        raise RuntimeError("market_day_index 的起始编号不正确。")
    if int(summary["max_day_index"]) != market_day_offset + trading_days - 1:
        raise RuntimeError("market_day_index 与交易日数量不一致。")
    return {
        "rows": rows,
        "unique_datetimes": int(summary["unique_datetimes"]),
        "trading_days": trading_days,
        "market_day_offset": int(market_day_offset),
        "first_date": str(summary["first_date"]),
        "last_date": str(summary["last_date"]),
        "first_datetime": str(summary["first_datetime"]),
        "last_datetime": str(summary["last_datetime"]),
    }


def _atomic_write_json(value: object, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _output_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    output_path = Path(args.output).resolve()
    if output_path.suffix.lower() != ".parquet":
        raise ValueError("输出文件必须使用 .parquet 后缀。")
    if args.meta is None:
        meta_path = output_path.with_suffix(".meta.json")
    else:
        meta_path = Path(args.meta).resolve()
    if meta_path.suffix.lower() != ".json":
        raise ValueError("说明文件必须使用 .json 后缀。")
    if output_path == meta_path:
        raise ValueError("数据文件和说明文件不能是同一个文件。")
    return output_path, meta_path


def _build_one_output(
    args: argparse.Namespace,
    source_files: Sequence[Path],
    output_path: Path,
    meta_path: Path,
    signature: dict[str, object],
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.force:
        reusable, reason = can_reuse(output_path, meta_path, signature)
        if reusable:
            print(f"{reason}，跳过生成：{output_path}", flush=True)
            result = json.loads(meta_path.read_text(encoding="utf-8"))
            result["run_status"] = "reused"
            return result
        if output_path.exists() or meta_path.exists():
            print(f"现有文件不能直接使用：{reason}。", flush=True)

    temporary = output_path.with_name(
        f".{output_path.stem}.{os.getpid()}.{time.time_ns()}.tmp.parquet"
    )
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    print(
        f"开始读取{len(source_files)}个分钟文件，日期{signature['start']}至"
        f"{signature['end']}。",
        flush=True,
    )
    try:
        with duckdb.connect() as connection:
            _configure_connection(
                connection,
                args.threads,
                args.memory_limit,
                Path(args.temp_dir),
            )
            query = build_query(
                source_files,
                str(signature["start"]),
                str(signature["end"]),
                int(signature["market_day_offset"]),
            )
            connection.execute(
                f"""
                COPY ({query})
                TO '{_sql_path(temporary)}'
                (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE {int(args.row_group_size)}
                )
                """
            )
            summary = _validate_built_file(
                connection,
                temporary,
                int(signature["market_day_offset"]),
            )

        output_sha256 = _sha256(temporary)
        output_bytes = temporary.stat().st_size
        os.replace(temporary, output_path)
        elapsed = time.perf_counter() - started
        methodology = {
            "status": "completed",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "signature": signature,
            "intended_factor_groups": [
                "F02",
                "J07-J08",
                "MAY25",
                "早期9项试算",
            ],
            "formulas": {
                "first_minute_return": "close/open-1",
                "later_minute_return": "close/previous_minute_close-1",
                "market_mean_return": "同一分钟有效股票简单收益的算术平均",
                "market_std_return_samp": "同一分钟简单收益的样本标准差，分母n-1",
                "market_std_return_pop": "同一分钟简单收益的总体标准差，分母n",
                "daily_standard_deviation_means": "当日各分钟市场标准差的算术平均，空值不参与",
                "direction_means": "上涨和下跌股票分别计算同一分钟平均收益，零收益不归入两组",
                "deviation_skew": "同方向收益减去同方向平均收益后的调整样本偏度；零收益偏离记0",
                "non_differentiation": "分钟市场标准差严格小于当日对应标准差均值",
                "market_day_index": "所选日期内按交易日递增，从0开始",
            },
            "settings": {
                "threads": args.threads,
                "memory_limit": args.memory_limit,
                "temp_dir": str(Path(args.temp_dir).resolve()),
                "row_group_size": args.row_group_size,
                "compression": "ZSTD",
                "parquet_date_filter": (
                    f"datetime >= {signature['start']} and "
                    f"datetime < next_day({signature['end']})"
                ),
            },
            "output": {
                "path": str(output_path),
                "bytes": output_bytes,
                "sha256": output_sha256,
                **summary,
            },
            "elapsed_seconds": elapsed,
        }
        _atomic_write_json(methodology, meta_path)
    finally:
        temporary.unlink(missing_ok=True)

    print(
        f"生成完成：{summary['rows']}行、{summary['trading_days']}个交易日，"
        f"{output_bytes / 1024**2:.1f} MiB，耗时{elapsed:.1f}秒。",
        flush=True,
    )
    print(f"说明文件：{meta_path}", flush=True)
    methodology["run_status"] = "completed"
    return methodology


def _yearly_paths(directory: Path, year: int) -> tuple[Path, Path]:
    output = directory / f"shared_market_minute_stats_{year}.parquet"
    return output, output.with_suffix(".meta.json")


def _empty_partial_arrays(size: int) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    count_columns = {
        "amount_stock_count",
        "return_stock_count",
        "positive_count",
        "negative_count",
    }
    for column in PARTIAL_COLUMNS[1:]:
        dtype = np.int64 if column in count_columns else np.float64
        arrays[column] = np.zeros(size, dtype=dtype)
    return arrays


def _minute_positions(minutes: np.ndarray) -> np.ndarray:
    positions = np.full(len(minutes), -1, dtype=np.int16)
    morning = (minutes >= 571) & (minutes <= 690)
    afternoon = (minutes >= 781) & (minutes <= 900)
    positions[morning] = (minutes[morning] - 571).astype(np.int16)
    positions[afternoon] = (minutes[afternoon] - 781 + 120).astype(np.int16)
    return positions


def _add_at_unique(
    destination: np.ndarray,
    positions: np.ndarray,
    values: np.ndarray | int,
    assume_unique: bool = False,
) -> None:
    if len(positions) == 0:
        return
    if assume_unique or len(positions) == len(np.unique(positions)):
        destination[positions] += values
    else:
        np.add.at(destination, positions, values)


def _streaming_partial_worker(
    task: tuple[list[str], str, str, str],
) -> dict[str, object]:
    paths, start, end, output = task
    lower = np.datetime64(start, "D")
    upper = np.datetime64(end, "D") + np.timedelta64(1, "D")
    day_count = int((upper - lower).astype(np.int64))
    size = day_count * len(TRADING_MINUTES)
    arrays = _empty_partial_arrays(size)
    rows_read = 0
    files_read = 0
    for raw_path in paths:
        frame = pq.read_table(
            raw_path,
            columns=["datetime", "open", "close", "amount"],
        ).to_pandas()
        if frame.empty:
            files_read += 1
            continue
        datetimes = frame["datetime"].to_numpy(dtype="datetime64[ns]")
        order = None
        if len(datetimes) > 1 and np.any(datetimes[1:] < datetimes[:-1]):
            order = np.argsort(datetimes, kind="stable")
            datetimes = datetimes[order]
        days = datetimes.astype("datetime64[D]")
        selected = (days >= lower) & (days < upper)
        if not selected.any():
            files_read += 1
            continue
        datetimes = datetimes[selected]
        days = days[selected]
        source_positions = np.flatnonzero(selected)
        if order is not None:
            source_positions = order[source_positions]
        minute_open = frame["open"].to_numpy(dtype=np.float64)[source_positions]
        minute_close = frame["close"].to_numpy(dtype=np.float64)[source_positions]
        minute_amount = frame["amount"].to_numpy(dtype=np.float64)[source_positions]
        minutes = (datetimes - days).astype("timedelta64[m]").astype(np.int32)
        minute_positions = _minute_positions(minutes)
        valid_time = minute_positions >= 0
        if not valid_time.all():
            datetimes = datetimes[valid_time]
            days = days[valid_time]
            minute_positions = minute_positions[valid_time]
            minute_open = minute_open[valid_time]
            minute_close = minute_close[valid_time]
            minute_amount = minute_amount[valid_time]
        if not len(datetimes):
            files_read += 1
            continue
        day_offsets = (days - lower).astype(np.int32)
        slots = day_offsets * len(TRADING_MINUTES) + minute_positions
        slots_are_unique = bool(
            len(slots) < 2 or np.all(slots[1:] != slots[:-1])
        )
        first = np.r_[True, days[1:] != days[:-1]]
        denominator = np.empty(len(minute_close), dtype=np.float64)
        denominator[first] = minute_open[first]
        denominator[~first] = minute_close[:-1][~first[1:]]
        returns = np.full(len(minute_close), np.nan, dtype=np.float64)
        valid_return = (
            np.isfinite(minute_close)
            & np.isfinite(denominator)
            & (denominator != 0)
        )
        np.divide(
            minute_close,
            denominator,
            out=returns,
            where=valid_return,
        )
        returns[valid_return] -= 1.0
        valid_return &= np.isfinite(returns)
        return_slots = slots[valid_return]
        return_values = returns[valid_return]
        _add_at_unique(
            arrays["return_stock_count"], return_slots, 1, slots_are_unique
        )
        _add_at_unique(
            arrays["return_sum"], return_slots, return_values, slots_are_unique
        )
        _add_at_unique(
            arrays["return_square_sum"],
            return_slots,
            return_values**2,
            slots_are_unique,
        )
        positive = return_values > 0
        positive_slots = return_slots[positive]
        positive_values = return_values[positive]
        _add_at_unique(
            arrays["positive_count"], positive_slots, 1, slots_are_unique
        )
        _add_at_unique(
            arrays["positive_sum"],
            positive_slots,
            positive_values,
            slots_are_unique,
        )
        _add_at_unique(
            arrays["positive_square_sum"],
            positive_slots,
            positive_values**2,
            slots_are_unique,
        )
        _add_at_unique(
            arrays["positive_cube_sum"],
            positive_slots,
            positive_values**3,
            slots_are_unique,
        )
        negative = return_values < 0
        negative_slots = return_slots[negative]
        negative_values = return_values[negative]
        _add_at_unique(
            arrays["negative_count"], negative_slots, 1, slots_are_unique
        )
        _add_at_unique(
            arrays["negative_sum"],
            negative_slots,
            negative_values,
            slots_are_unique,
        )
        _add_at_unique(
            arrays["negative_square_sum"],
            negative_slots,
            negative_values**2,
            slots_are_unique,
        )
        _add_at_unique(
            arrays["negative_cube_sum"],
            negative_slots,
            negative_values**3,
            slots_are_unique,
        )
        valid_amount = np.isfinite(minute_amount)
        amount_slots = slots[valid_amount]
        _add_at_unique(
            arrays["amount_stock_count"], amount_slots, 1, slots_are_unique
        )
        _add_at_unique(
            arrays["market_amount_total"],
            amount_slots,
            minute_amount[valid_amount],
            slots_are_unique,
        )
        rows_read += len(datetimes)
        files_read += 1

    used = (arrays["return_stock_count"] > 0) | (
        arrays["amount_stock_count"] > 0
    )
    frame = pd.DataFrame({"slot": np.flatnonzero(used).astype(np.int32)})
    for column in PARTIAL_COLUMNS[1:]:
        frame[column] = arrays[column][used]
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, compression="zstd", index=False)
    return {
        "output": output,
        "files_read": files_read,
        "rows_read": rows_read,
        "partial_rows": len(frame),
    }


def _merge_streaming_partials(
    partial_paths: Sequence[Path], start: str, end: str
) -> pd.DataFrame:
    lower = np.datetime64(start, "D")
    upper = np.datetime64(end, "D") + np.timedelta64(1, "D")
    day_count = int((upper - lower).astype(np.int64))
    size = day_count * len(TRADING_MINUTES)
    arrays = _empty_partial_arrays(size)
    for path in partial_paths:
        part = pd.read_parquet(path, columns=list(PARTIAL_COLUMNS))
        slots = part["slot"].to_numpy(dtype=np.int32)
        for column in PARTIAL_COLUMNS[1:]:
            arrays[column][slots] += part[column].to_numpy(
                dtype=arrays[column].dtype
            )
    used = (arrays["return_stock_count"] > 0) | (
        arrays["amount_stock_count"] > 0
    )
    slots = np.flatnonzero(used)
    day_offsets = slots // len(TRADING_MINUTES)
    minute_positions = slots % len(TRADING_MINUTES)
    dates = lower + day_offsets.astype("timedelta64[D]")
    datetimes = dates.astype("datetime64[m]") + TRADING_MINUTES[
        minute_positions
    ].astype("timedelta64[m]")
    trading_day_offsets = np.unique(day_offsets)
    market_day_index = np.searchsorted(trading_day_offsets, day_offsets)

    count = arrays["return_stock_count"][used].astype(np.float64)
    total = arrays["return_sum"][used]
    square = arrays["return_square_sum"][used]
    market_mean = np.full(len(slots), np.nan, dtype=np.float64)
    np.divide(total, count, out=market_mean, where=count > 0)
    centered_square = square - total * total / np.where(count > 0, count, 1)
    centered_square = np.maximum(centered_square, 0.0)
    std_pop = np.full(len(slots), np.nan, dtype=np.float64)
    std_samp = np.full(len(slots), np.nan, dtype=np.float64)
    np.sqrt(
        centered_square / np.where(count > 0, count, 1),
        out=std_pop,
        where=count > 0,
    )
    np.sqrt(
        centered_square / np.where(count > 1, count - 1, 1),
        out=std_samp,
        where=count > 1,
    )

    positive_count = arrays["positive_count"][used].astype(np.float64)
    negative_count = arrays["negative_count"][used].astype(np.float64)
    positive_sum = arrays["positive_sum"][used]
    negative_sum = arrays["negative_sum"][used]
    positive_mean = np.full(len(slots), np.nan, dtype=np.float64)
    negative_mean = np.full(len(slots), np.nan, dtype=np.float64)
    np.divide(
        positive_sum,
        positive_count,
        out=positive_mean,
        where=positive_count > 0,
    )
    np.divide(
        negative_sum,
        negative_count,
        out=negative_mean,
        where=negative_count > 0,
    )

    def shifted_moments(
        group_count: np.ndarray,
        group_sum: np.ndarray,
        group_square: np.ndarray,
        group_cube: np.ndarray,
        group_mean: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        mean = np.where(group_count > 0, group_mean, 0.0)
        moment1 = group_sum - group_count * mean
        moment2 = (
            group_square
            - 2.0 * mean * group_sum
            + group_count * mean**2
        )
        moment3 = (
            group_cube
            - 3.0 * mean * group_square
            + 3.0 * mean**2 * group_sum
            - group_count * mean**3
        )
        return moment1, moment2, moment3

    p1, p2, p3 = shifted_moments(
        positive_count,
        positive_sum,
        arrays["positive_square_sum"][used],
        arrays["positive_cube_sum"][used],
        positive_mean,
    )
    n1, n2, n3 = shifted_moments(
        negative_count,
        negative_sum,
        arrays["negative_square_sum"][used],
        arrays["negative_cube_sum"][used],
        negative_mean,
    )
    adjusted_sum = p1 + n1
    adjusted_square = p2 + n2
    adjusted_cube = p3 + n3
    adjusted_mean = np.divide(
        adjusted_sum,
        count,
        out=np.zeros(len(slots), dtype=np.float64),
        where=count > 0,
    )
    central_square = (
        adjusted_square - adjusted_sum * adjusted_sum / np.where(count > 0, count, 1)
    )
    central_square = np.maximum(central_square, 0.0)
    central_cube = (
        adjusted_cube
        - 3.0 * adjusted_mean * adjusted_square
        + 3.0 * adjusted_mean**2 * adjusted_sum
        - count * adjusted_mean**3
    )
    deviation_skew = np.full(len(slots), np.nan, dtype=np.float64)
    skew_good = (count > 2) & (central_square > 0)
    deviation_skew[skew_good] = (
        count[skew_good]
        * np.sqrt(count[skew_good] - 1.0)
        / (count[skew_good] - 2.0)
        * central_cube[skew_good]
        / central_square[skew_good] ** 1.5
    )

    result = pd.DataFrame(
        {
            "date": dates.astype("datetime64[ns]"),
            "datetime": datetimes.astype("datetime64[ns]"),
            "minute_of_day": TRADING_MINUTES[minute_positions],
            "market_day_index": market_day_index.astype(np.int32),
            "market_amount_total": arrays["market_amount_total"][used],
            "market_mean_return": market_mean,
            "market_std_return_samp": std_samp,
            "market_std_return_pop": std_pop,
            "positive_return_mean": positive_mean,
            "negative_return_mean": negative_mean,
            "return_stock_count": arrays["return_stock_count"][used].astype(
                np.int32
            ),
            "amount_stock_count": arrays["amount_stock_count"][used].astype(
                np.int32
            ),
            "deviation_skew": deviation_skew,
        }
    )
    result["daily_market_std_samp_mean"] = result.groupby("date", sort=False)[
        "market_std_return_samp"
    ].transform("mean")
    result["daily_market_std_pop_mean"] = result.groupby("date", sort=False)[
        "market_std_return_pop"
    ].transform("mean")
    result["is_non_differentiation_samp"] = (
        result["market_std_return_samp"]
        < result["daily_market_std_samp_mean"]
    )
    result["is_non_differentiation_pop"] = (
        result["market_std_return_pop"]
        < result["daily_market_std_pop_mean"]
    )
    return result.loc[:, OUTPUT_COLUMNS]


def _run_yearly_repeated(
    args: argparse.Namespace,
    source_files: Sequence[Path],
    source_info: dict[str, object],
) -> int:
    lower, upper = _date_bounds(args.start, args.end)
    directory = Path(args.yearly_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        Path(args.yearly_manifest).resolve()
        if args.yearly_manifest is not None
        else directory / "shared_market_minute_stats.yearly.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    expected_years = list(range(lower.year, (upper - pd.Timedelta(days=1)).year + 1))
    manifest: dict[str, object] = {
        "status": "running",
        "table_version": TABLE_VERSION,
        "start": lower.strftime("%Y-%m-%d"),
        "end": (upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "expected_years": expected_years,
        "source_glob": _normalise_glob(args.source_glob),
        "source": source_info,
        "parquet_glob": str(
            (directory / "shared_market_minute_stats_*.parquet").resolve()
        ),
        "parts": [],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    day_offset = 0
    for year in expected_years:
        part_lower = max(lower, pd.Timestamp(year=year, month=1, day=1))
        part_upper = min(upper, pd.Timestamp(year=year + 1, month=1, day=1))
        part_start = part_lower.strftime("%Y-%m-%d")
        part_end = (part_upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        output_path, meta_path = _yearly_paths(directory, year)
        signature = _expected_signature(
            args.source_glob,
            source_info,
            part_start,
            part_end,
            day_offset,
        )
        try:
            result = _build_one_output(
                args,
                source_files,
                output_path,
                meta_path,
                signature,
            )
        except Exception as exc:
            manifest.update(
                {
                    "status": "failed",
                    "failed_year": year,
                    "error": repr(exc),
                    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            _atomic_write_json(manifest, manifest_path)
            raise
        output = result["output"]
        part_record = {
            "year": year,
            "start": part_start,
            "end": part_end,
            "market_day_offset": day_offset,
            "trading_days": int(output["trading_days"]),
            "rows": int(output["rows"]),
            "path": str(output_path),
            "meta_path": str(meta_path),
            "run_status": result["run_status"],
        }
        manifest["parts"].append(part_record)
        day_offset += int(output["trading_days"])
        manifest.update(
            {
                "completed_years": len(manifest["parts"]),
                "total_trading_days": day_offset,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        _atomic_write_json(manifest, manifest_path)

    manifest.update(
        {
            "status": "completed",
            "completed_years": len(expected_years),
            "total_rows": sum(int(part["rows"]) for part in manifest["parts"]),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    _atomic_write_json(manifest, manifest_path)
    print(
        f"年度分片完成：{len(expected_years)}年，累计{manifest['total_rows']}行；"
        f"可读取 {manifest['parquet_glob']}。",
        flush=True,
    )
    return 0


def _run_yearly_streaming(
    args: argparse.Namespace,
    source_files: Sequence[Path],
    source_info: dict[str, object],
) -> int:
    lower, upper = _date_bounds(args.start, args.end)
    directory = Path(args.yearly_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = (
        Path(args.yearly_manifest).resolve()
        if args.yearly_manifest is not None
        else directory / "shared_market_minute_stats.yearly.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    expected_years = list(range(lower.year, (upper - pd.Timedelta(days=1)).year + 1))
    worker_count = max(1, min(int(args.threads), len(source_files)))
    assignments: list[list[Path]] = [[] for _ in range(worker_count)]
    assignment_bytes = [0] * worker_count
    for path in sorted(source_files, key=lambda item: item.stat().st_size, reverse=True):
        worker = int(np.argmin(assignment_bytes))
        assignments[worker].append(path)
        assignment_bytes[worker] += path.stat().st_size

    temp_root = Path(args.temp_dir).resolve() / (
        f"streaming_{os.getpid()}_{time.time_ns()}"
    )
    temp_root.mkdir(parents=True, exist_ok=False)
    partial_paths = [
        temp_root / f"partial_{worker:02d}.parquet"
        for worker in range(worker_count)
    ]
    manifest: dict[str, object] = {
        "status": "running",
        "table_version": TABLE_VERSION,
        "start": lower.strftime("%Y-%m-%d"),
        "end": (upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "expected_years": expected_years,
        "source_glob": _normalise_glob(args.source_glob),
        "source": source_info,
        "parquet_glob": str(
            (directory / "shared_market_minute_stats_*.parquet").resolve()
        ),
        "scan_mode": "one_read_per_source_file",
        "workers": worker_count,
        "parts": [],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(manifest, manifest_path)
    started = time.perf_counter()
    try:
        tasks = [
            (
                [str(path) for path in assignments[worker]],
                lower.strftime("%Y-%m-%d"),
                (upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                str(partial_paths[worker]),
            )
            for worker in range(worker_count)
        ]
        worker_results: list[dict[str, object]] = []
        if worker_count == 1:
            worker_results.append(_streaming_partial_worker(tasks[0]))
        else:
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_streaming_partial_worker, task): number
                    for number, task in enumerate(tasks, start=1)
                }
                for future in as_completed(futures):
                    result = future.result()
                    worker_results.append(result)
                    print(
                        f"分钟文件读取 {len(worker_results)}/{worker_count}："
                        f"{result['files_read']}个文件，{result['rows_read']:,}行",
                        flush=True,
                    )
        combined = _merge_streaming_partials(
            partial_paths,
            lower.strftime("%Y-%m-%d"),
            (upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        day_offset = 0
        with duckdb.connect() as connection:
            for year in expected_years:
                part_lower = max(lower, pd.Timestamp(year=year, month=1, day=1))
                part_upper = min(upper, pd.Timestamp(year=year + 1, month=1, day=1))
                part_start = part_lower.strftime("%Y-%m-%d")
                part_end = (part_upper - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                output_path, meta_path = _yearly_paths(directory, year)
                signature = _expected_signature(
                    args.source_glob,
                    source_info,
                    part_start,
                    part_end,
                    day_offset,
                )
                part = combined.loc[
                    combined["date"].between(
                        part_lower,
                        part_upper,
                        inclusive="left",
                    )
                ]
                temporary = output_path.with_name(
                    f".{output_path.stem}.{os.getpid()}.tmp.parquet"
                )
                temporary.unlink(missing_ok=True)
                part.to_parquet(
                    temporary,
                    compression="zstd",
                    index=False,
                    row_group_size=int(args.row_group_size),
                )
                summary = _validate_built_file(connection, temporary, day_offset)
                output_bytes = temporary.stat().st_size
                output_sha256 = _sha256(temporary)
                os.replace(temporary, output_path)
                metadata = {
                    "status": "completed",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "signature": signature,
                    "build_mode": "one_read_per_source_file_then_year_split",
                    "settings": {
                        "workers": worker_count,
                        "compression": "ZSTD",
                        "row_group_size": args.row_group_size,
                    },
                    "output": {
                        "path": str(output_path),
                        "bytes": output_bytes,
                        "sha256": output_sha256,
                        **summary,
                    },
                    "elapsed_seconds": time.perf_counter() - started,
                }
                _atomic_write_json(metadata, meta_path)
                part_record = {
                    "year": year,
                    "start": part_start,
                    "end": part_end,
                    "market_day_offset": day_offset,
                    "trading_days": int(summary["trading_days"]),
                    "rows": int(summary["rows"]),
                    "path": str(output_path),
                    "meta_path": str(meta_path),
                    "run_status": "completed",
                }
                manifest["parts"].append(part_record)
                day_offset += int(summary["trading_days"])
                manifest.update(
                    {
                        "completed_years": len(manifest["parts"]),
                        "total_trading_days": day_offset,
                        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
                _atomic_write_json(manifest, manifest_path)

        elapsed = time.perf_counter() - started
        manifest.update(
            {
                "status": "completed",
                "completed_years": len(expected_years),
                "total_rows": sum(
                    int(part["rows"]) for part in manifest["parts"]
                ),
                "source_files_read": sum(
                    int(result["files_read"]) for result in worker_results
                ),
                "source_rows_read": sum(
                    int(result["rows_read"]) for result in worker_results
                ),
                "elapsed_seconds": elapsed,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        _atomic_write_json(manifest, manifest_path)
        print(
            f"年度市场分钟表生成完成：原始文件各读取一次，"
            f"共{manifest['total_rows']:,}行，耗时{elapsed:.1f}秒。",
            flush=True,
        )
        return 0
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "error": repr(exc),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        _atomic_write_json(manifest, manifest_path)
        raise
    finally:
        for path in partial_paths:
            path.unlink(missing_ok=True)
        try:
            temp_root.rmdir()
        except OSError:
            pass


def _run_yearly(
    args: argparse.Namespace,
    source_files: Sequence[Path],
    source_info: dict[str, object],
) -> int:
    lower, upper = _date_bounds(args.start, args.end)
    directory = Path(args.yearly_dir).resolve()
    expected_years = list(range(lower.year, (upper - pd.Timedelta(days=1)).year + 1))
    has_existing_part = any(
        path.exists()
        for year in expected_years
        for path in _yearly_paths(directory, year)
    )
    if args.yearly_scan_mode == "streaming" and (
        args.force or not has_existing_part
    ):
        return _run_yearly_streaming(args, source_files, source_info)
    return _run_yearly_repeated(args, source_files, source_info)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.row_group_size < 1:
        raise ValueError("row-group-size 必须至少为1。")
    if args.yearly_manifest is not None and args.yearly_dir is None:
        raise ValueError("设置yearly-manifest时必须同时设置yearly-dir。")
    source_files = list_source_files(args.source_glob)
    source_info = source_fingerprint(source_files)
    if args.yearly_dir is not None:
        return _run_yearly(args, source_files, source_info)

    output_path, meta_path = _output_paths(args)
    signature = _expected_signature(
        args.source_glob,
        source_info,
        args.start,
        args.end,
    )
    _build_one_output(
        args,
        source_files,
        output_path,
        meta_path,
        signature,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
