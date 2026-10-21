# -*- coding: utf-8 -*-
"""用少量真实分钟文件比较孤雁出群逐年查询与全期一次查询。"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Sequence

import pandas as pd
import pyarrow.parquet as pq

import build_july_august_minute_features as target
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR
    / ".codex-artifact"
    / "minute_completion"
    / "lone_goose_parallel_benchmark.json"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stocks", type=int, default=120)
    parser.add_argument("--start-year", type=int, default=2024)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="768MB")
    parser.add_argument("--block-size", type=int, default=256)
    return parser.parse_args(argv)


def _real_files(limit: int) -> list[Path]:
    files = sorted(
        Path(factor_store.DEFAULT_MINUTE_DIR).glob("*.parquet"),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    if len(files) < limit:
        raise FileNotFoundError(f"真实分钟文件不足{limit}个。")
    return files[:limit]


def _write_market_sample(
    source: Path, output: Path, start: str, end: str
) -> int:
    datetimes = pq.read_table(source, columns=["datetime"]).to_pandas()
    datetimes["datetime"] = pd.to_datetime(datetimes["datetime"])
    selected = datetimes["datetime"].between(
        pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1), inclusive="left"
    )
    frame = datetimes.loc[selected].copy()
    minute_of_day = frame["datetime"].dt.hour * 60 + frame["datetime"].dt.minute
    frame["date"] = frame["datetime"].dt.normalize()
    frame["is_non_differentiation_pop"] = minute_of_day.mod(2).eq(0)
    frame.to_parquet(output, compression="zstd", index=False)
    return int(frame["date"].nunique())


def _read_years(directory: Path, years: range) -> dict[int, pd.DataFrame]:
    return {
        year: pd.read_parquet(directory / f"lone_goose_{year}.parquet")
        .sort_values(["date", "code"])
        .reset_index(drop=True)
        for year in years
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.stocks < 2:
        raise ValueError("至少需要两只股票。")
    if args.end_year < args.start_year:
        raise ValueError("结束年份不能早于开始年份。")
    files = _real_files(args.stocks)
    start = f"{args.start_year}-01-01"
    end = f"{args.end_year}-12-31"
    years = range(args.start_year, args.end_year + 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BASE_DIR / "tmp") as raw_root:
        root = Path(raw_root)
        market = root / "market.parquet"
        trading_days = _write_market_sample(files[0], market, start, end)
        sequential_dir = root / "sequential"
        full_query_dir = root / "full_query"
        sequential_dir.mkdir()
        full_query_dir.mkdir()

        started = time.perf_counter()
        sequential_summary = target._build_lone_goose_files(
            files,
            market,
            sequential_dir,
            start,
            end,
            root / "temp_sequential",
            threads=args.threads,
            memory_limit=args.memory_limit,
            block_size=args.block_size,
            force=True,
        )
        sequential_seconds = time.perf_counter() - started

        started = time.perf_counter()
        with target._connection(
            root / "temp_full_query", args.threads, args.memory_limit
        ) as connection:
            query = target.build_lone_goose_query(files, market, start, end)
            reader = connection.execute(query).to_arrow_reader(4096)
            frames = (batch.to_pandas() for batch in reader)
            full_daily, full_diagnostics = target._process_lone_goose_rows(
                frames, args.block_size
            )
        full_daily["date"] = pd.to_datetime(full_daily["date"])
        full_daily["code"] = full_daily["code"].astype("string")
        for year in years:
            full_daily.loc[full_daily["date"].dt.year.eq(year)].to_parquet(
                full_query_dir / f"lone_goose_{year}.parquet",
                compression="zstd",
                index=False,
            )
        full_query_seconds = time.perf_counter() - started
        sequential = _read_years(sequential_dir, years)
        full_query = _read_years(full_query_dir, years)
        rows = 0
        for year in years:
            pd.testing.assert_frame_equal(sequential[year], full_query[year])
            rows += len(full_query[year])

        ratio = sequential_seconds / full_query_seconds
        result = {
            "real_minute_files": len(files),
            "compressed_source_gib": sum(path.stat().st_size for path in files)
            / 2**30,
            "start": start,
            "end": end,
            "trading_days_from_reference_stock": trading_days,
            "output_rows": rows,
            "sequential_seconds": sequential_seconds,
            "full_query_seconds": full_query_seconds,
            "speed_ratio_sequential_over_full_query": ratio,
            "full_query_time_reduction_fraction": 1.0 - 1.0 / ratio,
            "value_comparison": "exact",
            "sequential_summary": sequential_summary,
            "full_query_diagnostic_days": len(full_diagnostics),
            "estimated_full_hours_from_old_1_5h": 1.5 / ratio,
            "estimated_full_hours_from_old_2_0h": 2.0 / ratio,
            "note": "只读取少量真实分钟文件，未触碰正式缓存。",
            "created_at": pd.Timestamp.now().isoformat(),
        }
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
