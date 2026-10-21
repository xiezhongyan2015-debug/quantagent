# -*- coding: utf-8 -*-
"""每只股票只读一次原始分钟文件，同时生成三组逐日因子文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Sequence

import duckdb
import pandas as pd
import psutil
import pyarrow.parquet as pq

import build_july_august_minute_features as july_builder
import build_october_december_trial_daily as octdec_builder
import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as early_builder
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
CACHE_VERSION = 1
DEFAULT_ROOT = BASE_DIR / "duckdb_factor_store" / "combined_minute_trial_features"
DEFAULT_JULAUG_DIR = DEFAULT_ROOT / "july_august" / "daily_by_stock"
DEFAULT_EARLY_DIR = DEFAULT_ROOT / "early9_trial" / "daily_by_stock"
DEFAULT_OCTDEC_DIR = DEFAULT_ROOT / "october_december_trial" / "daily_by_stock"
DEFAULT_COMBINED_MANIFEST = DEFAULT_ROOT / "combined_manifest.json"

_WORKER_CONNECTION: duckdb.DuckDBPyConnection | None = None


def _sql_path(value: str | Path) -> str:
    return str(Path(value).resolve()).replace("\\", "/").replace("'", "''")


def _workspace_path(value: str | Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须放在当前工作区内。")
    return path


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _resume_manifest_matches(
    manifest: dict[str, object] | None,
    signature: dict[str, object],
) -> bool:
    return bool(
        manifest
        and all(manifest.get(key) == value for key, value in signature.items())
        and manifest.get("status") in {"running", "completed"}
    )


def _date_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    lower = pd.Timestamp(start).normalize()
    upper = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)
    if upper <= lower:
        raise ValueError("结束日期不能早于开始日期。")
    return lower, upper


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
    universe: str,
    base_cache_glob: Path,
    codes_file: Path | None = None,
    limit: int | None = None,
) -> list[Path]:
    selected = _selected_codes(codes_file)
    if selected:
        codes = sorted(selected)
    elif universe == "base":
        codes = sorted(
            path.stem.lower()
            for path in base_cache_glob.parent.glob(base_cache_glob.name)
        )
    elif universe == "all":
        codes = sorted(path.stem.lower() for path in data_dir.glob("*.parquet"))
    else:
        raise ValueError(f"不支持的股票范围：{universe}")
    files = [data_dir / f"{code}.parquet" for code in codes]
    files = [path for path in files if path.exists()]
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"没有找到分钟文件：{data_dir}")
    return files


def _source_fingerprint(files: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        stat = path.stat()
        digest.update(path.name.lower().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def _july_output_is_current(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        parquet = pq.ParquetFile(path)
        return bool(
            parquet.metadata.num_rows > 0
            and parquet.schema_arrow.names == july_builder.STOCK_OUTPUT_COLUMNS
        )
    except Exception:
        return False


def outputs_are_current(
    july_path: Path,
    early_path: Path,
    octdec_path: Path,
    min_market_stocks: int,
) -> bool:
    return bool(
        _july_output_is_current(july_path)
        and early_builder.output_is_current(early_path, min_market_stocks)
        and octdec_builder.output_is_current(octdec_path)
    )


def _require_market_columns(path: Path) -> None:
    required = {
        "datetime",
        "market_amount_total",
        "market_day_index",
        "market_mean_return",
        "market_std_return_pop",
        "positive_return_mean",
        "negative_return_mean",
        "return_stock_count",
        "deviation_skew",
    }
    files = shared_market.list_market_stat_files(path)
    for file in files:
        columns = set(pq.ParquetFile(file).schema_arrow.names)
        missing = sorted(required - columns)
        if missing:
            raise ValueError(f"共用市场分钟文件 {file.name} 缺少字段：{', '.join(missing)}")


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION
    worker_temp = Path(temp_dir) / f"combined_minute_worker_{os.getpid()}"
    worker_temp.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(
        f"SET temp_directory = '{_sql_path(worker_temp)}'"
    )
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def build_materialize_query(minute_path: str | Path, start: str, end: str) -> str:
    """生成一次性读取原始分钟文件的语句，便于单独测试。"""

    lower, upper = _date_bounds(start, end)
    source = _sql_path(minute_path)
    return f"""
        CREATE OR REPLACE TEMP TABLE combined_raw AS
        SELECT datetime, open, high, low, close, volume, amount
        FROM read_parquet('{source}', union_by_name = true)
        WHERE datetime >= TIMESTAMP '{lower:%Y-%m-%d 00:00:00}'
          AND datetime < TIMESTAMP '{upper:%Y-%m-%d 00:00:00}'
        ORDER BY datetime
    """


def _write_three_outputs(
    july: pd.DataFrame,
    early: pd.DataFrame,
    octdec: pd.DataFrame,
    july_output: Path,
    early_output: Path,
    octdec_output: Path,
) -> int:
    outputs = [july_output, early_output, octdec_output]
    frames = [july, early, octdec]
    temporary = [
        path.with_name(f"{path.stem}.combined.tmp.parquet") for path in outputs
    ]
    for path in temporary:
        path.unlink(missing_ok=True)
    try:
        for frame, path in zip(frames, temporary):
            frame.to_parquet(path, compression="zstd", index=False)
        for source, output in zip(temporary, outputs):
            os.replace(source, output)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return sum(path.stat().st_size for path in outputs)


def _build_one_stock(
    minute_path: str,
    market_path: str,
    july_output_path: str,
    early_output_path: str,
    octdec_output_path: str,
    start: str,
    end: str,
    min_market_stocks: int,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程还没有准备好。")
    source = Path(minute_path)
    code = source.stem.lower()
    july_output = Path(july_output_path)
    early_output = Path(early_output_path)
    octdec_output = Path(octdec_output_path)
    started = time.perf_counter()
    try:
        _WORKER_CONNECTION.execute(build_materialize_query(source, start, end))
        raw_rows = int(
            _WORKER_CONNECTION.execute(
                "SELECT count(*) FROM combined_raw"
            ).fetchone()[0]
        )
        if raw_rows == 0:
            return {
                "code": code,
                "status": "empty",
                "raw_rows": 0,
                "rows": 0,
                "size_bytes": 0,
                "seconds": time.perf_counter() - started,
            }

        july_query = july_builder.build_stock_daily_query(
            source,
            market_path,
            start,
            end,
            source_relation="combined_raw",
            code=code,
        )
        july_raw = _WORKER_CONNECTION.execute(july_query).fetchdf()
        if july_raw.empty:
            raise RuntimeError("七八月逐日结果为空。")
        july_daily = july_builder.finalize_stock_daily_frame(july_raw)

        early_query = early_builder.build_stock_query(
            source,
            market_path,
            start,
            end,
            source_relation="combined_raw",
        )
        early_frame = _WORKER_CONNECTION.execute(early_query).fetchdf()
        early_daily = early_builder.build_daily_frame(
            early_frame, code, min_market_stocks
        )

        octdec_query = octdec_builder.build_stock_query(
            source,
            start,
            end,
            source_relation="combined_raw",
        )
        octdec_frame = _WORKER_CONNECTION.execute(octdec_query).fetchdf()
        octdec_daily = octdec_builder.build_daily_frame(octdec_frame, code)
        if early_daily.empty or octdec_daily.empty:
            raise RuntimeError("早期9项或十至十二月逐日结果为空。")

        size_bytes = _write_three_outputs(
            july_daily,
            early_daily,
            octdec_daily,
            july_output,
            early_output,
            octdec_output,
        )
        return {
            "code": code,
            "status": "completed",
            "raw_reads": 1,
            "raw_rows": raw_rows,
            "july_rows": len(july_daily),
            "early_rows": len(early_daily),
            "octdec_rows": len(octdec_daily),
            "rows": len(july_daily) + len(early_daily) + len(octdec_daily),
            "size_bytes": size_bytes,
            "seconds": time.perf_counter() - started,
        }
    finally:
        _WORKER_CONNECTION.execute("DROP TABLE IF EXISTS combined_raw")


def _memory_sample() -> tuple[float, float]:
    parent = psutil.Process(os.getpid())
    values: list[float] = []
    for child in parent.children(recursive=True):
        try:
            if child.is_running():
                values.append(child.memory_info().rss / 1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sum(values), max(values, default=0.0)


def _run_jobs(
    jobs: Sequence[tuple[Path, Path, Path, Path]],
    market_file: Path,
    start: str,
    end: str,
    min_market_stocks: int,
    temp_dir: Path,
    workers: int,
    worker_memory_limit: str,
) -> tuple[list[dict[str, object]], list[dict[str, str]], float, float, float]:
    started = time.perf_counter()
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    peak_total = 0.0
    peak_single = 0.0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(str(temp_dir), worker_memory_limit),
    ) as executor:
        future_map = {
            executor.submit(
                _build_one_stock,
                str(source),
                str(market_file),
                str(july_output),
                str(early_output),
                str(octdec_output),
                start,
                end,
                min_market_stocks,
            ): source.stem.lower()
            for source, july_output, early_output, octdec_output in jobs
        }
        pending = set(future_map)
        completed = 0
        while pending:
            done, pending = wait(
                pending, timeout=1.0, return_when=FIRST_COMPLETED
            )
            total, single = _memory_sample()
            peak_total = max(peak_total, total)
            peak_single = max(peak_single, single)
            for future in done:
                completed += 1
                code = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"已处理 {completed}/{len(jobs)}；失败 {len(failures)}；"
                        f"工作进程内存最高合计 {peak_total:.0f} MiB",
                        flush=True,
                    )
    return (
        results,
        failures,
        time.perf_counter() - started,
        peak_total,
        peak_single,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        "--minute-data-dir",
        dest="data_dir",
        type=Path,
        default=factor_store.DEFAULT_MINUTE_DIR,
    )
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument("--universe", choices=["base", "all"], default="base")
    parser.add_argument("--market-file", type=Path, default=shared_market.DEFAULT_OUTPUT)
    parser.add_argument("--july-output-dir", type=Path, default=DEFAULT_JULAUG_DIR)
    parser.add_argument("--early-output-dir", type=Path, default=DEFAULT_EARLY_DIR)
    parser.add_argument("--octdec-output-dir", type=Path, default=DEFAULT_OCTDEC_DIR)
    parser.add_argument(
        "--combined-manifest", type=Path, default=DEFAULT_COMBINED_MANIFEST
    )
    parser.add_argument("--july-manifest", type=Path)
    parser.add_argument("--early-manifest", type=Path)
    parser.add_argument("--octdec-manifest", type=Path)
    parser.add_argument("--early-methodology", type=Path)
    parser.add_argument("--octdec-methodology", type=Path)
    parser.add_argument("--temp-dir", type=Path, default=factor_store.DEFAULT_TEMP_DIR)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-memory-limit", default="1500MB")
    parser.add_argument(
        "--min-market-stocks",
        type=int,
        default=early_builder.DEFAULT_MIN_MARKET_STOCKS,
    )
    parser.add_argument("--codes-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("进程数至少为1。")
    if args.min_market_stocks < 2:
        raise ValueError("市场股票数下限至少为2。")
    if args.limit is not None and args.limit < 1:
        raise ValueError("测试股票数至少为1。")

    data_dir = Path(args.data_dir).resolve()
    base_cache_glob = Path(args.base_cache_glob).resolve()
    market_file = Path(args.market_file).resolve()
    july_dir = _workspace_path(args.july_output_dir)
    early_dir = _workspace_path(args.early_output_dir)
    octdec_dir = _workspace_path(args.octdec_output_dir)
    temp_dir = _workspace_path(args.temp_dir)
    combined_manifest = _workspace_path(args.combined_manifest)
    july_manifest = _workspace_path(
        args.july_manifest or july_dir.parent / "stock_manifest.json"
    )
    early_manifest = _workspace_path(
        args.early_manifest or early_dir.parent / "daily_manifest.json"
    )
    octdec_manifest = _workspace_path(
        args.octdec_manifest or octdec_dir.parent / "daily_manifest.json"
    )
    early_methodology = _workspace_path(
        args.early_methodology or early_dir.parent / "daily_methodology.json"
    )
    octdec_methodology = _workspace_path(
        args.octdec_methodology or octdec_dir.parent / "daily_methodology.json"
    )
    for directory in [july_dir, early_dir, octdec_dir, temp_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    _require_market_columns(market_file)

    files = list_input_files(
        data_dir,
        args.universe,
        base_cache_glob,
        args.codes_file,
        args.limit,
    )
    source_fingerprint = _source_fingerprint(files)
    resume_signature = {
        "combined_cache_version": CACHE_VERSION,
        "start": args.start,
        "end": args.end,
        "minute_data_dir": str(data_dir),
        "base_cache_glob": str(base_cache_glob),
        "shared_market_file": str(market_file),
        "source_files": len(files),
        "source_fingerprint": source_fingerprint,
        "min_market_stocks": args.min_market_stocks,
        "july_output_dir": str(july_dir),
        "early_output_dir": str(early_dir),
        "octdec_output_dir": str(octdec_dir),
    }
    resume_allowed = _resume_manifest_matches(
        _load_json(combined_manifest), resume_signature
    )
    jobs: list[tuple[Path, Path, Path, Path]] = []
    skipped = 0
    for source in files:
        code = source.stem.lower()
        july_output = july_dir / f"{code}.parquet"
        early_output = early_dir / f"{code}.parquet"
        octdec_output = octdec_dir / f"{code}.parquet"
        if not args.force and resume_allowed and outputs_are_current(
            july_output,
            early_output,
            octdec_output,
            args.min_market_stocks,
        ):
            skipped += 1
        else:
            jobs.append((source, july_output, early_output, octdec_output))

    _atomic_write_json(
        {
            **resume_signature,
            "status": "running",
            "jobs_planned": len(jobs),
            "skipped_current": skipped,
            "started_at": pd.Timestamp.now().isoformat(),
        },
        combined_manifest,
    )

    results, failures, elapsed, peak_total, peak_single = _run_jobs(
        jobs,
        market_file,
        args.start,
        args.end,
        args.min_market_stocks,
        temp_dir,
        args.workers,
        args.worker_memory_limit,
    )
    completed = sum(item["status"] == "completed" for item in results)
    empty = sum(item["status"] == "empty" for item in results)
    current_outputs = sum(
        outputs_are_current(
            july_dir / f"{path.stem.lower()}.parquet",
            early_dir / f"{path.stem.lower()}.parquet",
            octdec_dir / f"{path.stem.lower()}.parquet",
            args.min_market_stocks,
        )
        for path in files
    )
    common = {
        "combined_cache_version": CACHE_VERSION,
        "start": args.start,
        "end": args.end,
        "minute_data_dir": str(data_dir),
        "base_cache_glob": str(base_cache_glob),
        "shared_market_file": str(market_file),
        "source_files": len(files),
        "source_fingerprint": source_fingerprint,
        "min_market_stocks": args.min_market_stocks,
        "jobs_run": len(jobs),
        "skipped_current": skipped,
        "completed": completed,
        "empty": empty,
        "current_outputs": current_outputs,
        "failures": failures,
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "worker_memory_limit": args.worker_memory_limit,
        "peak_total_child_mib": round(peak_total, 1),
        "peak_single_child_mib": round(peak_single, 1),
        "raw_parquet_reads_per_completed_stock": 1,
        "copied_raw_minute_data": False,
        "created_at": pd.Timestamp.now().isoformat(),
    }
    _atomic_write_json(
        {
            **common,
            "status": "completed" if not failures else "failed",
            "july_output_dir": str(july_dir),
            "early_output_dir": str(early_dir),
            "octdec_output_dir": str(octdec_dir),
            "output_columns": {
                "july_august": july_builder.STOCK_OUTPUT_COLUMNS,
                "early9_trial": early_builder.OUTPUT_COLUMNS,
                "october_december_trial": octdec_builder.OUTPUT_COLUMNS,
            },
        },
        combined_manifest,
    )
    _atomic_write_json(
        {
            "definition_version": 3,
            "universe": args.universe,
            "minute_data_dir": str(data_dir),
            "base_cache_glob": str(base_cache_glob),
            "source_files": len(files),
            "start": args.start,
            "end": args.end,
            "shared_market_file": str(market_file),
            "trial_parameters": july_builder.TRIAL_PARAMETERS,
            "stock": {
                "status": "completed" if not failures else "failed",
                "requested_files": len(files),
                "jobs_run": len(jobs),
                "skipped_existing": skipped,
                "completed": completed,
                "empty": empty,
                "failures": failures,
                "seconds": elapsed,
                "peak_total_child_mib": round(peak_total, 1),
                "peak_single_child_mib": round(peak_single, 1),
            },
        },
        july_manifest,
    )
    _atomic_write_json(
        {
            **common,
            "formula_mode": "trial",
            "formula_label": "试算",
            "cache_version": early_builder.CACHE_VERSION,
            "stock_source_file_count": len(files),
            "output_dir": str(early_dir),
            "market_file": str(market_file),
            "completed": completed,
            "skipped_current": skipped,
            "rows_written": int(
                sum(int(item.get("early_rows", 0)) for item in results)
            ),
            "output_columns": early_builder.OUTPUT_COLUMNS,
            "min_market_stocks": args.min_market_stocks,
        },
        early_manifest,
    )
    _atomic_write_json(
        {
            **common,
            "cache_version": octdec_builder.CACHE_VERSION,
            "formula_mode": "trial",
            "formula_label": "试算",
            "requested_files": len(files),
            "completed_outputs": current_outputs,
            "skipped_existing": skipped,
            "rows_written": int(
                sum(int(item.get("octdec_rows", 0)) for item in results)
            ),
            "columns": octdec_builder.OUTPUT_COLUMNS,
        },
        octdec_manifest,
    )
    _atomic_write_json(
        {
            "formula_mode": "trial",
            "formula_label": "试算",
            "cache_version": early_builder.CACHE_VERSION,
            "factors": early_builder.TRIAL_PARAMETERS,
            "shared_market_table": {
                "path": str(market_file),
                "columns": early_builder.MARKET_COLUMNS,
            },
            "raw_minute_data_copied": False,
        },
        early_methodology,
    )
    _atomic_write_json(
        {
            "formula_mode": "trial",
            "formula_label": "试算",
            "parameters": octdec_builder.DAILY_PARAMETERS,
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
        },
        octdec_methodology,
    )
    print(
        f"三组逐日结果完成：新算 {completed} 只，跳过 {skipped} 只，"
        f"失败 {len(failures)} 只，耗时 {elapsed:.1f} 秒。",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
