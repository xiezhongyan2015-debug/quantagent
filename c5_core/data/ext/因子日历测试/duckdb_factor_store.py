# -*- coding: utf-8 -*-
"""用 DuckDB 统一读取本地 Parquet 因子数据。"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import duckdb
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "duckdb_factor_store" / "factor_store.duckdb"
DEFAULT_TEMP_DIR = BASE_DIR / "duckdb_factor_store" / "tmp"
DEFAULT_MINUTE_DIR = Path(
    r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data"
)

DEFAULT_SOURCES = {
    "base_daily": BASE_DIR
    / "february_march_results"
    / "cache"
    / "daily_by_stock"
    / "*.parquet",
    "january_daily": BASE_DIR
    / "january_original_minute_results"
    / "cache"
    / "daily_by_stock_v2"
    / "*.parquet",
    "april_daily": BASE_DIR
    / "april_factor_results"
    / "cache"
    / "april_daily_by_stock"
    / "*.parquet",
    "may_daily": BASE_DIR
    / "may_factor_results"
    / "cache"
    / "may_daily_by_stock"
    / "*.parquet",
    "june_daily": BASE_DIR
    / "june_factor_results"
    / "cache"
    / "june_daily_by_stock"
    / "*.parquet",
    "minute_extra_daily": BASE_DIR
    / "duckdb_factor_store"
    / "minute_daily_extra_by_stock_v3"
    / "*.parquet",
    "july_august_daily": BASE_DIR
    / "duckdb_factor_store"
    / "july_august_minute_daily_by_stock"
    / "*.parquet",
    "lone_goose_daily": BASE_DIR
    / "duckdb_factor_store"
    / "lone_goose_daily_by_year"
    / "*.parquet",
    "minute": DEFAULT_MINUTE_DIR / "*.parquet",
}

_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DuckDBSettings:
    """DuckDB 的本地运行设置。"""

    database_path: Path = DEFAULT_DATABASE
    temp_dir: Path = DEFAULT_TEMP_DIR
    threads: int = max(1, min(8, os.cpu_count() or 4))
    memory_limit: str = "8GB"


def _check_name(value: str, label: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label}只能包含英文字母、数字和下划线：{value!r}")
    return value


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _view_name(source_name: str) -> str:
    return f"source_{_check_name(source_name, '数据源名称')}"


def connect(settings: DuckDBSettings | None = None) -> duckdb.DuckDBPyConnection:
    """打开本地数据库并应用统一的内存、线程和临时目录设置。"""

    settings = settings or DuckDBSettings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(settings.database_path))
    connection.execute(f"SET threads = {int(settings.threads)}")
    connection.execute(f"SET memory_limit = '{settings.memory_limit}'")
    connection.execute(
        f"SET temp_directory = '{_sql_path(settings.temp_dir)}'"
    )
    connection.execute("SET preserve_insertion_order = false")
    return connection


def register_sources(
    connection: duckdb.DuckDBPyConnection,
    sources: Mapping[str, Path] | None = None,
) -> pd.DataFrame:
    """登记 Parquet 文件位置，并创建不会复制原数据的虚拟表。"""

    sources = sources or DEFAULT_SOURCES
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_sources (
            source_name VARCHAR PRIMARY KEY,
            parquet_glob VARCHAR NOT NULL,
            file_count BIGINT NOT NULL,
            columns_json VARCHAR NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    rows: list[dict[str, object]] = []
    for source_name, raw_glob in sources.items():
        _check_name(source_name, "数据源名称")
        parquet_glob = Path(raw_glob)
        files = sorted(parquet_glob.parent.glob(parquet_glob.name))
        if not files:
            raise FileNotFoundError(f"没有找到 Parquet 文件：{parquet_glob}")
        sql_glob = _sql_path(parquet_glob)
        view_name = _view_name(source_name)
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW \"{view_name}\" AS
            SELECT *
            FROM read_parquet('{sql_glob}', union_by_name = true)
            """
        )
        description = connection.execute(
            f'DESCRIBE SELECT * FROM "{view_name}"'
        ).fetchdf()
        columns = description["column_name"].astype(str).tolist()
        if source_name != "minute" and not {"date", "code"}.issubset(columns):
            raise ValueError(f"{source_name} 缺少 date 或 code 字段")
        connection.execute(
            """
            INSERT OR REPLACE INTO factor_sources
            VALUES (?, ?, ?, ?, current_timestamp)
            """,
            [
                source_name,
                str(parquet_glob.resolve()),
                len(files),
                json.dumps(columns, ensure_ascii=False),
            ],
        )
        rows.append(
            {
                "source_name": source_name,
                "file_count": len(files),
                "column_count": len(columns),
                "parquet_glob": str(parquet_glob.resolve()),
            }
        )
    return pd.DataFrame(rows)


def list_sources(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """列出已经登记的数据源。"""

    return connection.execute(
        """
        SELECT source_name, file_count, parquet_glob, updated_at
        FROM factor_sources
        ORDER BY source_name
        """
    ).fetchdf()


def source_columns(
    connection: duckdb.DuckDBPyConnection, source_name: str
) -> list[str]:
    """返回一个数据源的字段名。"""

    view_name = _view_name(source_name)
    description = connection.execute(
        f'DESCRIBE SELECT * FROM "{view_name}"'
    ).fetchdf()
    return description["column_name"].astype(str).tolist()


def read_source(
    connection: duckdb.DuckDBPyConnection,
    source_name: str,
    columns: Sequence[str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """只读取指定日期和字段，并按日期、股票代码排序。"""

    requested = ["date", "code", *columns]
    available = set(source_columns(connection, source_name))
    missing = sorted(set(requested) - available)
    if missing:
        raise KeyError(f"{source_name} 缺少字段：{', '.join(missing)}")
    quoted = ", ".join(f'\"{_check_name(column, "字段名")}\"' for column in requested)
    view_name = _view_name(source_name)
    return connection.execute(
        f"""
        SELECT {quoted}
        FROM \"{view_name}\"
        WHERE date BETWEEN ? AND ?
        ORDER BY date, code
        """,
        [pd.Timestamp(start), pd.Timestamp(end)],
    ).fetchdf()


def read_joined_panel(
    connection: duckdb.DuckDBPyConnection,
    requested_sources: Mapping[str, Sequence[str]],
    start: str,
    end: str,
) -> pd.DataFrame:
    """在 DuckDB 内按 date、code 合并多个每日数据源后再交给 Pandas。"""

    if not requested_sources:
        raise ValueError("至少要指定一个数据源")
    ctes: list[str] = []
    output_columns = ["date", "code"]
    seen = set(output_columns)
    parameters: list[pd.Timestamp] = []
    aliases: list[str] = []

    for index, (source_name, columns) in enumerate(requested_sources.items()):
        alias = f"s{index}"
        aliases.append(alias)
        view_name = _view_name(source_name)
        available = set(source_columns(connection, source_name))
        missing = sorted({"date", "code", *columns} - available)
        if missing:
            raise KeyError(f"{source_name} 缺少字段：{', '.join(missing)}")
        duplicate = sorted(set(columns) & seen)
        if duplicate:
            raise ValueError(f"不同数据源包含同名字段：{', '.join(duplicate)}")
        clean_columns = [_check_name(column, "字段名") for column in columns]
        projection = ", ".join(
            ["date", "lower(code) AS code", *[f'\"{column}\"' for column in clean_columns]]
        )
        ctes.append(
            f"""
            {alias} AS (
                SELECT {projection}
                FROM \"{view_name}\"
                WHERE date BETWEEN ? AND ?
            )
            """
        )
        parameters.extend([pd.Timestamp(start), pd.Timestamp(end)])
        output_columns.extend(clean_columns)
        seen.update(clean_columns)

    selected = [f'{aliases[0]}.date', f'{aliases[0]}.code']
    for alias, columns in zip(aliases, requested_sources.values()):
        selected.extend(
            f'{alias}.\"{_check_name(column, "字段名")}\"' for column in columns
        )
    joins = [aliases[0]]
    joins.extend(f"INNER JOIN {alias} USING (date, code)" for alias in aliases[1:])
    sql = f"""
        WITH {', '.join(ctes)}
        SELECT {', '.join(selected)}
        FROM {' '.join(joins)}
        ORDER BY date, code
    """
    return connection.execute(sql, parameters).fetchdf()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "list", "benchmark"])
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--temp-dir", default=str(DEFAULT_TEMP_DIR))
    parser.add_argument("--threads", type=int, default=max(1, min(8, os.cpu_count() or 4)))
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--source", default="base_daily")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2020-12-31")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.temp_dir),
        threads=args.threads,
        memory_limit=args.memory_limit,
    )
    with connect(settings) as connection:
        if args.command == "init":
            print(register_sources(connection).to_string(index=False))
        elif args.command == "list":
            print(list_sources(connection).to_string(index=False))
        else:
            started = time.perf_counter()
            view_name = _view_name(args.source)
            row_count = connection.execute(
                f"SELECT count(*) FROM \"{view_name}\" WHERE date BETWEEN ? AND ?",
                [pd.Timestamp(args.start), pd.Timestamp(args.end)],
            ).fetchone()[0]
            elapsed = time.perf_counter() - started
            print(
                f"{args.source}: {row_count:,} 行，{elapsed:.3f} 秒，"
                f"threads={settings.threads}，memory_limit={settings.memory_limit}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
