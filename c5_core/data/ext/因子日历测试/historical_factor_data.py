# -*- coding: utf-8 -*-
"""用 DuckDB 从现有每日 Parquet 中按需读取因子字段。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import duckdb
import numpy as np
import pandas as pd

import duckdb_factor_store as factor_store
import historical_backtest_core as core
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent


SOURCE_GLOBS: dict[str, Path] = {
    "base_daily": Path(factor_store.DEFAULT_SOURCES["base_daily"]),
    "minute_extra_daily": Path(factor_store.DEFAULT_SOURCES["minute_extra_daily"]),
    "april_daily": Path(factor_store.DEFAULT_SOURCES["april_daily"]),
    "may_daily": Path(factor_store.DEFAULT_SOURCES["may_daily"]),
    "june_daily": Path(factor_store.DEFAULT_SOURCES["june_daily"]),
    "july_august_daily": BASE_DIR
    / "minute_completion_runs"
    / "july_august"
    / "daily_by_stock"
    / "*.parquet",
    "early_trial_daily": BASE_DIR
    / "minute_completion_runs"
    / "early9_trial"
    / "daily_by_stock"
    / "*.parquet",
    "octdec_trial_daily": BASE_DIR
    / "minute_completion_runs"
    / "october_december_trial"
    / "daily_by_stock"
    / "*.parquet",
    "missing_minute_daily": BASE_DIR
    / "historical_calendar_backtest_results"
    / "cache"
    / "missing_minute_by_stock"
    / "*.parquet",
}


SOURCE_PRIORITY = tuple(SOURCE_GLOBS)
CORE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "float_shares",
    "total_shares",
    "qfq_ratio",
)


@dataclass(frozen=True)
class DataSettings:
    start: str = "2015-01-01"
    end: str = "2024-12-31"
    threads: int = 2
    memory_limit: str = "3GB"
    temp_dir: Path = Path(r"E:\codex_duckdb_temp\factor_calendar")
    column_chunk: int = 8


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _relation(path: Path) -> str:
    return f"read_parquet('{_sql_path(path)}', union_by_name=true)"


def _connect(settings: DataSettings) -> duckdb.DuckDBPyConnection:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET threads={max(1, settings.threads)}")
    connection.execute(f"SET memory_limit='{settings.memory_limit}'")
    connection.execute(f"SET temp_directory='{_sql_path(settings.temp_dir)}'")
    connection.execute("SET preserve_insertion_order=false")
    return connection


def source_columns(
    connection: duckdb.DuckDBPyConnection,
    sources: Mapping[str, Path] = SOURCE_GLOBS,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for name, path in sources.items():
        files = list(path.parent.glob(path.name))
        if not files:
            continue
        # 每组分股文件的字段一致；只读一个文件即可，避免每次检查都扫几千个文件的页脚。
        rows = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(files[0])}')"
        ).fetchall()
        result[name] = tuple(str(row[0]) for row in rows)
    return result


def locate_columns(
    requested: Iterable[str], available: Mapping[str, tuple[str, ...]]
) -> tuple[dict[str, list[str]], list[str]]:
    by_source: dict[str, list[str]] = {}
    missing: list[str] = []
    for column in dict.fromkeys(requested):
        if column in CORE_COLUMNS or column in {
            "raw_close",
            "returns",
            "turnover",
            "market_cap",
            "total_market_cap",
            "open_exit_mark",
            "close_exit_mark",
        }:
            continue
        source = next(
            (name for name in SOURCE_PRIORITY if column in available.get(name, ())),
            None,
        )
        if source is None:
            missing.append(column)
        else:
            by_source.setdefault(source, []).append(column)
    return by_source, missing


def _read_panel(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    columns: Iterable[str],
    settings: DataSettings,
) -> pd.DataFrame:
    clean = list(dict.fromkeys(columns))
    projection = ", ".join(["date", "lower(code) AS code", *[f'"{x}"' for x in clean]])
    frame = connection.execute(
        f"""
        SELECT {projection}
        FROM {_relation(path)}
        WHERE date BETWEEN ? AND ?
        """,
        [pd.Timestamp(settings.start), pd.Timestamp(settings.end)],
    ).fetchdf()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["code"] = frame["code"].astype("string").str.lower()
    if frame.duplicated(["date", "code"]).any():
        raise ValueError(f"{path}存在重复date、code")
    return frame


def _add_arrays(
    arrays: dict[str, np.ndarray],
    frame: pd.DataFrame,
    columns: Iterable[str],
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
) -> None:
    for column in columns:
        arrays[column] = january.panel_to_matrix(
            frame,
            column,
            dates,
            stocks,
            date_map,
            stock_map,
            dtype=np.float32,
        )


def load_factor_arrays(
    requested_columns: Iterable[str],
    backtest_settings: core.BacktestSettings,
    data_settings: DataSettings | None = None,
) -> tuple[
    dict[str, np.ndarray],
    pd.DatetimeIndex,
    list[str],
    np.ndarray,
    np.ndarray,
    list[str],
]:
    """读取核心字段和给定补充字段，返回矩阵、日期、股票和筛选结果。"""

    settings = data_settings or DataSettings(
        start=backtest_settings.start, end=backtest_settings.end
    )
    connection = _connect(settings)
    try:
        available = source_columns(connection)
        by_source, missing = locate_columns(requested_columns, available)
        base_panel = _read_panel(
            connection, SOURCE_GLOBS["base_daily"], CORE_COLUMNS, settings
        )
        dates, stocks, date_map, stock_map, membership = core.make_context(
            base_panel, backtest_settings
        )
        arrays = core.panel_arrays(
            base_panel, dates, stocks, date_map, stock_map, CORE_COLUMNS
        )
        del base_panel
        for source_name, columns in by_source.items():
            path = SOURCE_GLOBS[source_name]
            for start in range(0, len(columns), settings.column_chunk):
                chunk = columns[start : start + settings.column_chunk]
                frame = _read_panel(connection, path, chunk, settings)
                _add_arrays(
                    arrays, frame, chunk, dates, stocks, date_map, stock_map
                )
                del frame
    finally:
        connection.close()
    eligible = core.build_eligible(arrays, membership, backtest_settings)
    return arrays, dates, stocks, membership, eligible, missing
