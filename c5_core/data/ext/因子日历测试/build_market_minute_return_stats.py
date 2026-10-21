# -*- coding: utf-8 -*-
"""一次读取分钟价格，生成全市场每分钟收益统计小表。"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

import pandas as pd

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR
    / "duckdb_factor_store"
    / "market_minute_return_stats_2015_2026.parquet"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-glob",
        default=str(factor_store.DEFAULT_MINUTE_DIR / "*.parquet"),
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-05-29")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="8GB")
    return parser.parse_args(argv)


def _sql_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").replace("'", "''")


def build_query(source_glob: str, start: str, end: str) -> str:
    start_time = pd.Timestamp(start)
    end_exclusive = pd.Timestamp(end) + pd.Timedelta(days=1)
    source = _sql_path(source_glob)
    return f"""
        WITH prices AS (
            SELECT
                filename,
                CAST(datetime AS DATE) AS date,
                datetime,
                CAST(open AS DOUBLE) AS open,
                CAST(close AS DOUBLE) AS close
            FROM read_parquet(
                '{source}',
                union_by_name = true,
                filename = true
            )
            WHERE datetime >= TIMESTAMP '{start_time:%Y-%m-%d 00:00:00}'
              AND datetime < TIMESTAMP '{end_exclusive:%Y-%m-%d 00:00:00}'
        ),
        returns AS (
            SELECT
                date,
                datetime,
                close / nullif(
                    coalesce(
                        lag(close) OVER (
                            PARTITION BY filename, date ORDER BY datetime
                        ),
                        open
                    ),
                    0
                ) - 1.0 AS simple_return
            FROM prices
        )
        SELECT
            date,
            datetime,
            CAST(
                extract(hour FROM datetime) * 60
                    + extract(minute FROM datetime)
                AS SMALLINT
            ) AS minute_of_day,
            count(simple_return) AS market_stock_count,
            avg(simple_return) AS market_mean_return,
            stddev_samp(simple_return) AS market_std_return
        FROM returns
        GROUP BY date, datetime
        ORDER BY datetime
    """


def _assert_safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出文件必须位于当前工作区内。")
    if resolved.suffix.lower() != ".parquet":
        raise ValueError("输出文件必须使用 .parquet 后缀。")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output_path = _assert_safe_output(Path(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.stem}.tmp.parquet")
    temp_path.unlink(missing_ok=True)

    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.temp_dir),
        threads=args.threads,
        memory_limit=args.memory_limit,
    )
    query = build_query(args.source_glob, args.start, args.end)
    started = time.perf_counter()
    with factor_store.connect(settings) as connection:
        connection.execute(
            f"""
            COPY ({query})
            TO '{_sql_path(temp_path)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
        temp_path.replace(output_path)
        summary = connection.execute(
            """
            SELECT
                count(*) AS rows,
                min(date) AS first_date,
                max(date) AS last_date,
                max(market_stock_count) AS max_stocks
            FROM read_parquet(?)
            """,
            [str(output_path)],
        ).fetchdf()

    elapsed = time.perf_counter() - started
    print(summary.to_string(index=False))
    print(
        f"已生成 {output_path}，{output_path.stat().st_size / 1024**2:.1f} MiB，"
        f"耗时 {elapsed:.1f} 秒。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
