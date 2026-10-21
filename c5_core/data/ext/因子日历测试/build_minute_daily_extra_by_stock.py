# -*- coding: utf-8 -*-
"""按股票分批生成分钟因子的轻量日频结果，支持中断后继续。"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Sequence

import duckdb
import psutil

import duckdb_factor_store as factor_store
from build_minute_daily_extra import BASE_DIR, _sql_path, build_query


DEFAULT_CACHE_DIR = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_by_stock_v3"
)
DEFAULT_MANIFEST = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_manifest_v3.json"
)
_WORKER_CONNECTION: duckdb.DuckDBPyConnection | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument(
        "--minute-data-dir", type=Path, default=factor_store.DEFAULT_MINUTE_DIR
    )
    parser.add_argument(
        "--base-cache-glob", type=Path, default=factor_store.DEFAULT_SOURCES["base_daily"]
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--worker-memory-limit", default="2GB")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须位于当前工作区内。")
    return resolved


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION
    worker_temp = Path(temp_dir) / f"minute_worker_{os.getpid()}"
    worker_temp.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(
        f"SET temp_directory = '{_sql_path(worker_temp)}'"
    )
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def _build_one(
    minute_path: str,
    output_path: str,
    start: str,
    end: str,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程没有初始化。")
    source = Path(minute_path)
    output = Path(output_path)
    temp = output.with_name(f"{output.stem}.tmp.parquet")
    if temp.exists():
        temp.unlink()
    started = time.perf_counter()
    query = build_query(str(source), start, end)
    _WORKER_CONNECTION.execute(
        f"""
        COPY ({query})
        TO '{_sql_path(temp)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )
    rows = int(
        _WORKER_CONNECTION.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(temp)]
        ).fetchone()[0]
    )
    if rows == 0:
        temp.unlink(missing_ok=True)
        return {
            "code": source.stem.lower(),
            "rows": 0,
            "seconds": time.perf_counter() - started,
            "size_bytes": 0,
            "status": "empty",
        }
    temp.replace(output)
    return {
        "code": source.stem.lower(),
        "rows": rows,
        "seconds": time.perf_counter() - started,
        "size_bytes": output.stat().st_size,
        "status": "completed",
    }


def _source_files(
    base_pattern: Path, minute_data_dir: Path
) -> tuple[list[Path], list[str]]:
    codes = sorted(path.stem.lower() for path in base_pattern.parent.glob(base_pattern.name))
    minute_files: list[Path] = []
    missing: list[str] = []
    for code in codes:
        path = minute_data_dir / f"{code}.parquet"
        if path.exists():
            minute_files.append(path)
        else:
            missing.append(code)
    return minute_files, missing


def _memory_sample() -> tuple[float, float]:
    parent = psutil.Process(os.getpid())
    children = [child for child in parent.children(recursive=True) if child.is_running()]
    child_mib = []
    for child in children:
        try:
            child_mib.append(child.memory_info().rss / 1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sum(child_mib), max(child_mib, default=0.0)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("workers 必须至少为1。")
    cache_dir = _assert_workspace_path(Path(args.cache_dir))
    manifest_path = _assert_workspace_path(Path(args.manifest))
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    base_pattern = Path(args.base_cache_glob).resolve()
    minute_data_dir = Path(args.minute_data_dir).resolve()
    files, missing_codes = _source_files(base_pattern, minute_data_dir)
    if args.limit:
        files = files[: args.limit]
    jobs: list[tuple[Path, Path]] = []
    skipped = 0
    for source in files:
        output = cache_dir / f"{source.stem.lower()}.parquet"
        if output.exists() and output.stat().st_size > 0 and not args.force:
            skipped += 1
            continue
        jobs.append((source, output))

    started = time.perf_counter()
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    peak_total_child_mib = 0.0
    peak_single_child_mib = 0.0
    temp_dir = str(_assert_workspace_path(Path(args.temp_dir)))
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(temp_dir, args.worker_memory_limit),
    ) as executor:
        future_map = {
            executor.submit(
                _build_one,
                str(source),
                str(output),
                args.start,
                args.end,
            ): source.stem.lower()
            for source, output in jobs
        }
        pending = set(future_map)
        completed = 0
        while pending:
            done, pending = wait(
                pending,
                timeout=1.0,
                return_when=FIRST_COMPLETED,
            )
            total_mib, single_mib = _memory_sample()
            peak_total_child_mib = max(peak_total_child_mib, total_mib)
            peak_single_child_mib = max(peak_single_child_mib, single_mib)
            for future in done:
                code = future_map[future]
                completed += 1
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"已处理 {completed}/{len(jobs)}；失败 {len(failures)}；"
                        f"子进程内存最高合计 {peak_total_child_mib:.0f} MiB",
                        flush=True,
                    )

    completed_files = sorted(cache_dir.glob("*.parquet"))
    if completed_files and args.limit is None:
        settings = factor_store.DuckDBSettings(
            database_path=Path(args.database),
            temp_dir=Path(args.temp_dir),
            threads=max(1, min(args.workers, 4)),
            memory_limit="4GB",
        )
        with factor_store.connect(settings) as connection:
            factor_store.register_sources(
                connection,
                {
                    "base_daily": base_pattern,
                    "minute_extra_daily": cache_dir / "*.parquet",
                },
            )

    elapsed = time.perf_counter() - started
    manifest = {
        "cache_version": 3,
        "formal_factors_added": [
            "M25_HIGH_VOLUME_REALIZED_SKEW",
            "A06_DAZZLING_VOLATILITY_20D",
            "A10_P_SHAPED_VOLUME_DISTRIBUTION",
            "MAY25_TOP_RETURN_PRICE_RESONANCE",
        ],
        "definition": (
            "在v2基础上新增聪明钱、量峰量谷、量岭、成交量潮、"
            "调整成交额相关、模糊关联度、量峰间隔四阶矩和量谷价格分位；"
            "本次再加入高成交量已实现偏度、璀璨波动率日值、P型成交量分布"
            "以及每日最高5个收益分钟时刻"
        ),
        "cache_dir": str(cache_dir),
        "base_cache_glob": str(base_pattern),
        "minute_data_dir": str(minute_data_dir),
        "start": args.start,
        "end": args.end,
        "requested_files": len(files),
        "jobs_run": len(jobs),
        "skipped_existing": skipped,
        "completed_outputs": len(completed_files),
        "missing_minute_files": missing_codes,
        "failures": failures,
        "rows_written": int(sum(int(item["rows"]) for item in results)),
        "size_bytes_written_this_run": int(
            sum(int(item["size_bytes"]) for item in results)
        ),
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "worker_memory_limit": args.worker_memory_limit,
        "peak_total_child_mib": round(peak_total_child_mib, 1),
        "peak_single_child_mib": round(peak_single_child_mib, 1),
    }
    temp_manifest = manifest_path.with_suffix(".tmp.json")
    temp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        "utf-8",
    )
    temp_manifest.replace(manifest_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    if failures:
        raise RuntimeError(f"有 {len(failures)} 只股票处理失败。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
