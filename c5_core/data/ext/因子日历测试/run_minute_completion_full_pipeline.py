# -*- coding: utf-8 -*-
"""可续跑地执行分钟因子补算、历史回测和最终汇总。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd
import pyarrow.parquet as pq

import assemble_minute_completion_results as assembler
import build_combined_minute_trial_features_by_stock as combined_trial_builder
import build_july_august_minute_features as july_builder
import build_minute_daily_extra_by_stock as v3_builder
import build_october_december_trial_daily as octdec_daily
import build_october_december_trial_network as octdec_network
import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as early_trial
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RUN_ROOT = BASE_DIR / "minute_completion_runs"
DEFAULT_FINAL_OUTPUT = BASE_DIR / "minute_completion_results" / "final"
EXPECTED_BASE_STOCK_COUNT = 3240

STAGE_NAMES = (
    "v3_cache",
    "shared_market_yearly",
    "julaug_stock_daily",
    "lone_goose_yearly",
    "early9_trial_daily",
    "octdec_trial_daily",
    "network_252",
    "backtest_early4",
    "backtest_julaug",
    "backtest_sepdec",
    "backtest_early9_trial",
    "backtest_octdec_trial",
    "assemble",
)


@dataclass(frozen=True)
class CheckResult:
    complete: bool
    reason: str
    details: dict[str, object]


@dataclass(frozen=True)
class StageSpec:
    name: str
    description: str
    command: tuple[str, ...]
    check: Callable[[], CheckResult]


@dataclass(frozen=True)
class PipelinePaths:
    run_root: Path
    logs: Path
    statuses: Path
    temp: Path
    shared_yearly: Path
    shared_manifest: Path
    julaug_stock: Path
    lone_goose: Path
    early_trial_daily: Path
    octdec_daily: Path
    octdec_network: Path
    backtest_early4: Path
    backtest_julaug: Path
    backtest_sepdec: Path
    backtest_early_trial: Path
    backtest_octdec_trial: Path
    final_output: Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--final-output", type=Path, default=DEFAULT_FINAL_OUTPUT)
    parser.add_argument(
        "--recent-2026-dir",
        type=Path,
        help="可选；传入2026近期检查目录后，把四个近期结果一并写入最终汇总。",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--minute-data-dir", type=Path, default=factor_store.DEFAULT_MINUTE_DIR
    )
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["base_daily"],
    )
    parser.add_argument(
        "--may-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["may_daily"],
    )
    parser.add_argument(
        "--june-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["june_daily"],
    )
    parser.add_argument("--v3-cache-dir", type=Path, default=v3_builder.DEFAULT_CACHE_DIR)
    parser.add_argument("--v3-manifest", type=Path, default=v3_builder.DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=factor_store.DEFAULT_DATABASE)
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--cache-start", default="2015-01-01")
    parser.add_argument("--cache-end", default="2026-05-29")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--backtest-end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--worker-memory-limit", default="2GB")
    parser.add_argument("--lone-goose-stock-batch-size", type=int, default=40)
    parser.add_argument("--min-market-stocks", type=int, default=500)
    parser.add_argument("--from-stage", choices=STAGE_NAMES, default=STAGE_NAMES[0])
    parser.add_argument("--to-stage", choices=STAGE_NAMES, default=STAGE_NAMES[-1])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def make_paths(args: argparse.Namespace) -> PipelinePaths:
    root = Path(args.run_root).resolve()
    return PipelinePaths(
        run_root=root,
        logs=root / "logs",
        statuses=root / "status",
        temp=root / "temp",
        shared_yearly=root / "shared_market" / "yearly",
        shared_manifest=root / "shared_market" / "shared_market_minute_stats.yearly.json",
        julaug_stock=root / "july_august" / "daily_by_stock",
        lone_goose=root / "july_august" / "lone_goose_by_year",
        early_trial_daily=root / "early9_trial" / "daily_by_stock",
        octdec_daily=root / "october_december_trial" / "daily_by_stock",
        octdec_network=root / "october_december_trial" / "network",
        backtest_early4=root / "backtests" / "early_four",
        backtest_julaug=root / "backtests" / "july_august",
        backtest_sepdec=root / "backtests" / "september_december",
        backtest_early_trial=root / "backtests" / "early9_trial",
        backtest_octdec_trial=root / "backtests" / "october_december_trial",
        final_output=Path(args.final_output).resolve(),
    )


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, default=str)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _script(name: str) -> str:
    return str((BASE_DIR / name).resolve())


def _command_text(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _load_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


@lru_cache(maxsize=8)
def _base_codes(pattern_text: str) -> tuple[str, ...]:
    pattern = Path(pattern_text)
    return tuple(
        sorted(path.stem.lower() for path in pattern.parent.glob(pattern.name))
    )


def _expected_base_codes(args: argparse.Namespace) -> tuple[str, ...]:
    return _base_codes(str(Path(args.base_cache_glob).resolve()))


def _check_code_parquets(
    directory: Path,
    expected_codes: Sequence[str],
    required_columns: set[str],
    exact_columns: Sequence[str] | None = None,
    marker: tuple[str, object] | None = None,
) -> CheckResult:
    expected = set(expected_codes)
    files = sorted(directory.glob("*.parquet")) if directory.is_dir() else []
    found = {path.stem.lower() for path in files}
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    details: dict[str, object] = {
        "directory": str(directory),
        "expected_files": len(expected),
        "found_files": len(files),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "missing_examples": missing[:20],
        "extra_examples": extra[:20],
    }
    if missing or extra:
        return CheckResult(False, "股票文件集合不完整", details)
    errors = []
    for path in files:
        try:
            parquet = pq.ParquetFile(path)
            columns = parquet.schema_arrow.names
            if parquet.metadata.num_rows <= 0:
                errors.append(f"{path.name}:没有记录")
            if required_columns - set(columns):
                errors.append(
                    f"{path.name}:缺字段{sorted(required_columns - set(columns))}"
                )
            if exact_columns is not None and columns != list(exact_columns):
                errors.append(f"{path.name}:字段或顺序不同")
            if marker is not None and marker[0] in columns:
                values = parquet.read_row_group(0, columns=[marker[0]]).column(0)
                if not all(value.as_py() == marker[1] for value in values):
                    errors.append(f"{path.name}:{marker[0]}值不正确")
            elif marker is not None:
                errors.append(f"{path.name}:缺字段{marker[0]}")
        except Exception as exc:
            errors.append(f"{path.name}:{exc}")
        if len(errors) >= 20:
            break
    details["file_errors"] = errors
    if errors:
        return CheckResult(False, "股票文件检查失败", details)
    return CheckResult(True, "股票文件完整", details)


def _check_v3(args: argparse.Namespace) -> CheckResult:
    codes = _expected_base_codes(args)
    if len(codes) != EXPECTED_BASE_STOCK_COUNT:
        return CheckResult(
            False,
            "基础日表股票数不是3240",
            {"base_stock_count": len(codes)},
        )
    manifest = _load_json(Path(args.v3_manifest))
    if manifest is None:
        return CheckResult(False, "v3说明文件不存在或无法读取", {})
    required = {
        "date",
        "code",
        "high_volume_realized_skew_daily",
        "dazzling_volatility_daily",
        "p_shaped_volume_distribution_daily",
        "top_return_time_1",
        "top_return_time_2",
        "top_return_time_3",
        "top_return_time_4",
        "top_return_time_5",
        "adj_lag_absret_amount_corr_daily",
    }
    files = _check_code_parquets(Path(args.v3_cache_dir), codes, required)
    details = {"manifest": manifest, "files": files.details}
    valid_manifest = (
        manifest.get("cache_version") == 3
        and manifest.get("start") == args.cache_start
        and manifest.get("end") == args.cache_end
        and Path(str(manifest.get("cache_dir", ""))).resolve()
        == Path(args.v3_cache_dir).resolve()
        and Path(str(manifest.get("base_cache_glob", ""))).resolve()
        == Path(args.base_cache_glob).resolve()
        and Path(str(manifest.get("minute_data_dir", ""))).resolve()
        == Path(args.minute_data_dir).resolve()
        and manifest.get("requested_files") == EXPECTED_BASE_STOCK_COUNT
        and manifest.get("completed_outputs") == EXPECTED_BASE_STOCK_COUNT
        and not manifest.get("failures")
    )
    if not valid_manifest:
        return CheckResult(False, "v3说明文件显示尚未完整", details)
    if not files.complete:
        return CheckResult(False, files.reason, details)
    return CheckResult(True, "v3主缓存完整", details)


def _check_shared_yearly(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    manifest = _load_json(paths.shared_manifest)
    if manifest is None:
        return CheckResult(False, "年度市场表说明文件不存在", {})
    lower = pd.Timestamp(args.cache_start)
    upper = pd.Timestamp(args.cache_end)
    expected_years = list(range(lower.year, upper.year + 1))
    parts = manifest.get("parts")
    details: dict[str, object] = {
        "manifest": str(paths.shared_manifest),
        "expected_years": expected_years,
    }
    expected_source_glob = shared_market._normalise_glob(
        str(Path(args.minute_data_dir).resolve() / "*.parquet")
    )
    if (
        manifest.get("status") != "completed"
        or manifest.get("table_version") != shared_market.TABLE_VERSION
        or manifest.get("start") != args.cache_start
        or manifest.get("end") != args.cache_end
        or manifest.get("expected_years") != expected_years
        or manifest.get("source_glob") != expected_source_glob
        or not isinstance(parts, list)
        or len(parts) != len(expected_years)
    ):
        return CheckResult(False, "年度市场表说明文件不完整", details)
    errors = []
    offset = 0
    for year, part in zip(expected_years, parts):
        output, meta = shared_market._yearly_paths(paths.shared_yearly, year)
        if (
            not isinstance(part, dict)
            or part.get("year") != year
            or Path(str(part.get("path", ""))).resolve() != output.resolve()
            or Path(str(part.get("meta_path", ""))).resolve() != meta.resolve()
        ):
            errors.append(f"{year}:年度记录路径不同")
            continue
        part_meta = _load_json(meta)
        if part_meta is None:
            errors.append(f"{year}:缺说明文件")
            continue
        signature = part_meta.get("signature")
        if not isinstance(signature, dict):
            errors.append(f"{year}:缺检查资料")
            continue
        expected_start = max(lower, pd.Timestamp(year=year, month=1, day=1))
        expected_upper = min(
            upper + pd.Timedelta(days=1),
            pd.Timestamp(year=year + 1, month=1, day=1),
        )
        expected_end = expected_upper - pd.Timedelta(days=1)
        if (
            signature.get("start") != expected_start.strftime("%Y-%m-%d")
            or signature.get("end") != expected_end.strftime("%Y-%m-%d")
            or signature.get("source") != manifest.get("source")
            or signature.get("source_glob") != manifest.get("source_glob")
        ):
            errors.append(f"{year}:日期或分钟来源不同")
            continue
        reusable, reason = shared_market.can_reuse(output, meta, signature)
        if not reusable:
            errors.append(f"{year}:{reason}")
            continue
        if int(signature.get("market_day_offset", -1)) != offset:
            errors.append(f"{year}:交易日编号不连续")
        output_info = part_meta.get("output", {})
        offset += int(output_info.get("trading_days", 0))
    details["errors"] = errors
    details["total_trading_days"] = offset
    if errors:
        return CheckResult(False, "年度市场表分片检查失败", details)
    return CheckResult(True, "年度市场表完整", details)


def _check_julaug_stock(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    codes = _expected_base_codes(args)
    if len(codes) != EXPECTED_BASE_STOCK_COUNT:
        return CheckResult(False, "基础日表股票数不是3240", {"count": len(codes)})
    manifest = _load_json(paths.run_root / "july_august" / "stock_manifest.json")
    files = _check_code_parquets(
        paths.julaug_stock,
        codes,
        set(july_builder.STOCK_OUTPUT_COLUMNS),
        exact_columns=july_builder.STOCK_OUTPUT_COLUMNS,
    )
    nested = manifest.get("stock") if manifest else None
    valid_manifest = (
        isinstance(manifest, dict)
        and manifest.get("start") == args.cache_start
        and manifest.get("end") == args.cache_end
        and Path(str(manifest.get("base_cache_glob", ""))).resolve()
        == Path(args.base_cache_glob).resolve()
        and Path(str(manifest.get("minute_data_dir", ""))).resolve()
        == Path(args.minute_data_dir).resolve()
        and Path(str(manifest.get("shared_market_file", ""))).resolve()
        == paths.shared_yearly.resolve()
        and manifest.get("source_files") == EXPECTED_BASE_STOCK_COUNT
        and isinstance(nested, dict)
        and nested.get("status") == "completed"
        and nested.get("requested_files") == EXPECTED_BASE_STOCK_COUNT
        and not nested.get("failures")
    )
    if not valid_manifest:
        return CheckResult(False, "七八月股票日值说明文件不完整", {"files": files.details})
    return files


def _check_lone_goose(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    lower = pd.Timestamp(args.cache_start)
    upper = pd.Timestamp(args.cache_end)
    years = list(range(lower.year, upper.year + 1))
    expected_names = {f"lone_goose_{year}" for year in years}
    files = sorted(paths.lone_goose.glob("*.parquet")) if paths.lone_goose.is_dir() else []
    found_names = {path.stem for path in files}
    manifest = _load_json(paths.run_root / "july_august" / "lone_goose_manifest.json")
    nested = manifest.get("lone_goose") if manifest else None
    errors = []
    if found_names != expected_names:
        errors.append("年度文件集合不同")
    for path in files:
        try:
            parquet = pq.ParquetFile(path)
            if parquet.metadata.num_rows <= 0:
                errors.append(f"{path.name}:没有记录")
            if not {"date", "code", "lone_goose_daily"}.issubset(
                parquet.schema_arrow.names
            ):
                errors.append(f"{path.name}:字段不完整")
        except Exception as exc:
            errors.append(f"{path.name}:{exc}")
    if not (
        isinstance(manifest, dict)
        and manifest.get("start") == args.cache_start
        and manifest.get("end") == args.cache_end
        and Path(str(manifest.get("base_cache_glob", ""))).resolve()
        == Path(args.base_cache_glob).resolve()
        and Path(str(manifest.get("minute_data_dir", ""))).resolve()
        == Path(args.minute_data_dir).resolve()
        and isinstance(nested, dict)
        and nested.get("status") == "completed"
    ):
        errors.append("说明文件不完整")
    details = {
        "expected_years": years,
        "found_files": len(files),
        "errors": errors[:20],
    }
    return CheckResult(not errors, "孤雁年度文件完整" if not errors else "孤雁年度文件不完整", details)


def _check_early_trial(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    codes = _expected_base_codes(args)
    manifest = _load_json(paths.run_root / "early9_trial" / "daily_manifest.json")
    files = _check_code_parquets(
        paths.early_trial_daily,
        codes,
        set(early_trial.OUTPUT_COLUMNS),
        exact_columns=early_trial.OUTPUT_COLUMNS,
        marker=("trial_cache_version", early_trial.CACHE_VERSION),
    )
    valid_manifest = (
        isinstance(manifest, dict)
        and manifest.get("start") == args.cache_start
        and manifest.get("end") == args.cache_end
        and manifest.get("stock_source_file_count") == EXPECTED_BASE_STOCK_COUNT
        and not manifest.get("failures")
        and manifest.get("completed", 0) + manifest.get("skipped_current", 0)
        == EXPECTED_BASE_STOCK_COUNT
    )
    if not valid_manifest:
        return CheckResult(False, "早期9项试算说明文件不完整", {"files": files.details})
    return files


def _check_octdec_daily(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    codes = _expected_base_codes(args)
    manifest = _load_json(paths.run_root / "october_december_trial" / "daily_manifest.json")
    files = _check_code_parquets(
        paths.octdec_daily,
        codes,
        set(octdec_daily.OUTPUT_COLUMNS),
        exact_columns=octdec_daily.OUTPUT_COLUMNS,
        marker=("trial_cache_version", octdec_daily.CACHE_VERSION),
    )
    valid_manifest = (
        isinstance(manifest, dict)
        and manifest.get("start") == args.cache_start
        and manifest.get("end") == args.cache_end
        and manifest.get("requested_files") == EXPECTED_BASE_STOCK_COUNT
        and manifest.get("completed_outputs") == EXPECTED_BASE_STOCK_COUNT
        and not manifest.get("failures")
    )
    if not valid_manifest:
        return CheckResult(False, "十至十二月试算说明文件不完整", {"files": files.details})
    return files


def _check_combined_stock_features(
    args: argparse.Namespace, paths: PipelinePaths
) -> CheckResult:
    checks = {
        "july_august": _check_julaug_stock(args, paths),
        "early9_trial": _check_early_trial(args, paths),
        "october_december_trial": _check_octdec_daily(args, paths),
    }
    errors = [
        f"{name}:{result.reason}"
        for name, result in checks.items()
        if not result.complete
    ]
    return CheckResult(
        not errors,
        "三组逐股日值完整" if not errors else "三组逐股日值不完整",
        {
            name: {
                "complete": result.complete,
                "reason": result.reason,
                "details": result.details,
            }
            for name, result in checks.items()
        },
    )


def _check_network(args: argparse.Namespace, paths: PipelinePaths) -> CheckResult:
    manifest_path = paths.octdec_network / "network_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest is None:
        return CheckResult(False, "252日网络说明文件不存在", {})
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, list):
        return CheckResult(False, "252日网络没有快照记录", {})
    expected_stems = {
        str(row.get("snapshot_date", "")).replace("-", "") for row in snapshots
    }
    nodes = sorted((paths.octdec_network / "nodes").glob("*.parquet"))
    edges = sorted((paths.octdec_network / "edges").glob("*.parquet"))
    errors = []
    if {path.stem for path in nodes} != expected_stems:
        errors.append("节点文件集合不同")
    if {path.stem for path in edges} != expected_stems:
        errors.append("边文件集合不同")
    if manifest.get("start") != args.cache_start or manifest.get("end") != args.cache_end:
        errors.append("日期范围不同")
    if manifest.get("window") != 252:
        errors.append("历史日数不是252")
    if manifest.get("source_file_count") != EXPECTED_BASE_STOCK_COUNT:
        errors.append("来源股票数不是3240")
    if manifest.get("snapshot_count") != len(snapshots) or not snapshots:
        errors.append("快照数量不正确")
    if not (paths.octdec_network / "network_methodology.json").is_file():
        errors.append("缺少计算说明")
    for path, required in [
        *[(path, set(octdec_network.NODE_COLUMNS)) for path in nodes],
        *[(path, set(octdec_network.EDGE_COLUMNS)) for path in edges],
    ]:
        try:
            if not required.issubset(pq.ParquetFile(path).schema_arrow.names):
                errors.append(f"{path.name}:字段不完整")
        except Exception as exc:
            errors.append(f"{path.name}:{exc}")
        if len(errors) >= 20:
            break
    return CheckResult(
        not errors,
        "252日网络完整" if not errors else "252日网络不完整",
        {"snapshots": len(snapshots), "errors": errors[:20]},
    )


def _check_network_compact(
    args: argparse.Namespace, paths: PipelinePaths
) -> CheckResult:
    manifest_path = paths.octdec_network / "network_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest is None:
        return CheckResult(False, "网络计算说明不存在", {})
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        return CheckResult(False, "网络月份记录为空", {})
    expected_stems = {
        str(row.get("snapshot_date", "")).replace("-", "")
        for row in snapshots
    }
    nodes = sorted((paths.octdec_network / "nodes").glob("*.parquet"))
    daily_files = sorted(paths.octdec_daily.glob("*.parquet"))
    base_pattern = Path(args.base_cache_glob)
    base_files = sorted(base_pattern.parent.glob(base_pattern.name))
    daily_fingerprint = octdec_network._source_fingerprint(daily_files)
    base_fingerprint = octdec_network._source_fingerprint(base_files)
    errors: list[str] = []
    if {path.stem for path in nodes} != expected_stems:
        errors.append("月份节点文件集合不同")
    if manifest.get("output_mode") != "compact":
        errors.append("网络结果不是紧凑格式")
    if manifest.get("edge_files_written") != 0:
        errors.append("紧凑格式不应写逐条关系文件")
    if manifest.get("network_version") != octdec_network.NETWORK_VERSION:
        errors.append("网络结果版本不同")
    if (
        manifest.get("start") != args.cache_start
        or manifest.get("end") != args.cache_end
    ):
        errors.append("日期范围不同")
    if manifest.get("window") != 252:
        errors.append("历史天数不是252")
    if manifest.get("source_file_count") != EXPECTED_BASE_STOCK_COUNT:
        errors.append("试算日值股票数不正确")
    if manifest.get("base_source_file_count") != EXPECTED_BASE_STOCK_COUNT:
        errors.append("基础日值股票数不正确")
    if manifest.get("source_fingerprint") != daily_fingerprint:
        errors.append("试算日值来源已经变化")
    if manifest.get("base_source_fingerprint") != base_fingerprint:
        errors.append("基础日值来源已经变化")
    if manifest.get("snapshot_count") != len(snapshots):
        errors.append("月份数量不正确")
    if manifest.get("node_rows", 0) <= 0:
        errors.append("节点记录为空")
    if not (paths.octdec_network / "network_methodology.json").is_file():
        errors.append("缺少计算说明")
    required = set(
        octdec_network.COMPACT_NODE_COLUMNS + octdec_network.COMPACT_RESUME_COLUMNS
    )
    for path in nodes:
        try:
            parquet = pq.ParquetFile(path)
            if parquet.metadata.num_rows <= 0:
                errors.append(f"{path.name}:没有记录")
            if not required.issubset(parquet.schema_arrow.names):
                errors.append(f"{path.name}:字段不完整")
            else:
                markers = parquet.read_row_group(
                    0, columns=octdec_network.COMPACT_RESUME_COLUMNS
                ).to_pandas()
                if (
                    markers.empty
                    or not markers["daily_source_fingerprint"]
                    .eq(daily_fingerprint)
                    .all()
                    or not markers["base_source_fingerprint"]
                    .eq(base_fingerprint)
                    .all()
                ):
                    errors.append(f"{path.name}:分钟来源标记不同")
        except Exception as exc:
            errors.append(f"{path.name}:{exc}")
        if len(errors) >= 20:
            break
    return CheckResult(
        not errors,
        "252日网络紧凑结果完整" if not errors else "252日网络紧凑结果不完整",
        {"snapshots": len(snapshots), "errors": errors[:20]},
    )


def check_backtest_result(
    directory: Path,
    expected_codes: Sequence[str],
    expected_settings: dict[str, str] | None = None,
) -> CheckResult:
    expected = set(expected_codes)
    errors = []
    counts = {}
    for filename in assembler.REQUIRED_CSV_FILES:
        path = directory / filename
        if not path.is_file():
            errors.append(f"缺文件:{filename}")
            continue
        try:
            factors = pd.read_csv(path, usecols=["factor"])["factor"].dropna().astype(str)
            present = set(factors)
            missing = sorted(expected - present)
            if missing:
                errors.append(f"{filename}:缺代码{','.join(missing)}")
            counts[filename] = {"rows": len(factors), "factor_codes": len(present)}
            if filename == "factor_status.csv":
                selected = factors[factors.isin(expected)]
                if len(selected) != len(expected) or selected.nunique() != len(expected):
                    errors.append("factor_status代码重复或行数不正确")
                status = pd.read_csv(path)
                if "calculated" not in status.columns:
                    errors.append("factor_status缺少calculated字段")
                else:
                    status["factor"] = status["factor"].astype(str)
                    selected_status = status[status["factor"].isin(expected)]
                    calculated = (
                        selected_status["calculated"]
                        .astype(str)
                        .str.strip()
                        .str.lower()
                    )
                    failed = sorted(
                        selected_status.loc[
                            ~calculated.isin({"true", "1"}), "factor"
                        ].astype(str)
                    )
                    if failed:
                        errors.append(
                            "factor_status显示未完成:" + ",".join(failed)
                        )
        except Exception as exc:
            errors.append(f"{filename}:{exc}")
    methodology_path = directory / "methodology.json"
    if not methodology_path.is_file():
        errors.append("缺文件:methodology.json")
    elif expected_settings is not None:
        methodology = _load_json(methodology_path)
        settings = methodology.get("settings") if methodology else None
        if not isinstance(settings, dict):
            errors.append("methodology.json缺少回测日期设置")
        else:
            for key, expected_value in expected_settings.items():
                if str(settings.get(key)) != expected_value:
                    errors.append(
                        f"methodology.json:{key}={settings.get(key)}，"
                        f"预期={expected_value}"
                    )
    return CheckResult(
        not errors,
        "历史回测结果完整" if not errors else "历史回测结果不完整",
        {"directory": str(directory), "counts": counts, "errors": errors[:30]},
    )


def _group_codes(*indices: int) -> tuple[str, ...]:
    return tuple(code for index in indices for code in assembler.GROUP_SPECS[index].codes)


def _register_primary_backtest_sources(args: argparse.Namespace) -> pd.DataFrame:
    """把正式基础日值和v3分钟日值明确登记到本次使用的数据库。"""

    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.run_root).resolve() / "temp" / "backtests",
        threads=max(1, min(int(args.threads), 4)),
        memory_limit=str(args.memory_limit),
    )
    sources = {
        "base_daily": Path(args.base_cache_glob).resolve(),
        "minute_extra_daily": Path(args.v3_cache_dir).resolve() / "*.parquet",
    }
    with factor_store.connect(settings) as connection:
        return factor_store.register_sources(connection, sources)


def _check_assembled(
    paths: PipelinePaths, recent_2026_dir: Path | None = None
) -> CheckResult:
    errors = []
    for filename in assembler.REQUIRED_CSV_FILES:
        path = paths.final_output / filename
        if not path.is_file():
            errors.append(f"缺文件:{filename}")
            continue
        try:
            frame = pd.read_csv(path)
            if set(frame["factor"].dropna().astype(str)) != {
                code for group in assembler.GROUP_SPECS for code in group.codes
            }:
                errors.append(f"{filename}:代码集合不等于51个")
        except Exception as exc:
            errors.append(f"{filename}:{exc}")
    try:
        status = pd.read_csv(paths.final_output / "factor_status.csv")
        if len(status) != 51 or status["factor"].nunique() != 51:
            errors.append("factor_status不是51个唯一代码")
        if status["formula_mode"].value_counts().to_dict() != {
            "trial": 27,
            "strict": 24,
        }:
            errors.append("严格和试算数量不正确")
    except Exception:
        pass
    unavailable = paths.final_output / "currently_unavailable_factors.csv"
    if not unavailable.is_file():
        errors.append("缺少当前不能计算清单")
    else:
        try:
            frame = pd.read_csv(unavailable)
            if len(frame) != 27 or frame["factor"].nunique() != 27:
                errors.append("当前不能计算清单不是27个")
        except Exception as exc:
            errors.append(f"不能计算清单:{exc}")
    methodology_path = paths.final_output / "methodology.json"
    if not methodology_path.is_file():
        errors.append("缺少最终计算说明")
    if recent_2026_dir is not None:
        try:
            assembler.validate_recent_2026_outputs(paths.final_output)
            methodology = _load_json(methodology_path)
            recent = methodology.get("recent_2026") if methodology else None
            if not isinstance(recent, dict) or recent.get("included") is not True:
                errors.append("最终计算说明没有记录2026近期结果")
        except Exception as exc:
            errors.append(f"2026近期结果:{exc}")
    return CheckResult(
        not errors,
        "最终汇总完整" if not errors else "最终汇总不完整",
        {"output": str(paths.final_output), "errors": errors},
    )


def build_stage_specs(args: argparse.Namespace, paths: PipelinePaths) -> list[StageSpec]:
    python = str(args.python)
    base_glob = str(Path(args.base_cache_glob).resolve())
    v3_glob = str((Path(args.v3_cache_dir).resolve() / "*.parquet"))
    minute_glob = str(Path(args.minute_data_dir).resolve() / "*.parquet")
    common_backtest = [
        "--database",
        str(Path(args.database).resolve()),
        "--duckdb-temp-dir",
        str(paths.temp / "backtests"),
        "--duckdb-threads",
        str(args.threads),
        "--duckdb-memory-limit",
        args.memory_limit,
        "--qlib-dir",
        str(Path(args.qlib_dir).resolve()),
        "--start",
        args.cache_start,
        "--backtest-start",
        args.backtest_start,
        "--end",
        args.backtest_end,
        "--split-date",
        args.split_date,
    ]
    expected_backtest_settings = {
        "start": args.cache_start,
        "backtest_start": args.backtest_start,
        "end": args.backtest_end,
        "split_date": args.split_date,
    }
    recent_assemble_arguments: tuple[object, ...] = (
        ()
        if args.recent_2026_dir is None
        else ("--recent-2026-dir", Path(args.recent_2026_dir).resolve())
    )

    def command(script: str, *values: object) -> tuple[str, ...]:
        return (python, _script(script), *(str(value) for value in values))

    stages = [
        StageSpec(
            "v3_cache",
            "检查或生成v3主缓存",
            command(
                "build_minute_daily_extra_by_stock.py",
                "--cache-dir",
                Path(args.v3_cache_dir).resolve(),
                "--manifest",
                Path(args.v3_manifest).resolve(),
                "--database",
                Path(args.database).resolve(),
                "--minute-data-dir",
                Path(args.minute_data_dir).resolve(),
                "--base-cache-glob",
                Path(args.base_cache_glob).resolve(),
                "--temp-dir",
                paths.temp / "v3",
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--workers",
                args.workers,
                "--worker-memory-limit",
                args.worker_memory_limit,
            ),
            lambda: _check_v3(args),
        ),
        StageSpec(
            "shared_market_yearly",
            "按自然年生成共用市场分钟表",
            command(
                "build_shared_market_minute_stats.py",
                "--source-glob",
                minute_glob,
                "--yearly-dir",
                paths.shared_yearly,
                "--yearly-manifest",
                paths.shared_manifest,
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--threads",
                args.threads,
                "--memory-limit",
                args.memory_limit,
                "--temp-dir",
                paths.temp / "shared_market",
            ),
            lambda: _check_shared_yearly(args, paths),
        ),
        StageSpec(
            "julaug_stock_daily",
            "生成七八月股票日值",
            command(
                "build_combined_minute_trial_features_by_stock.py",
                "--universe",
                "base",
                "--minute-data-dir",
                Path(args.minute_data_dir).resolve(),
                "--base-cache-glob",
                Path(args.base_cache_glob).resolve(),
                "--market-file",
                paths.shared_yearly,
                "--july-output-dir",
                paths.julaug_stock,
                "--early-output-dir",
                paths.early_trial_daily,
                "--octdec-output-dir",
                paths.octdec_daily,
                "--combined-manifest",
                paths.run_root / "combined_minute_trial_features" / "manifest.json",
                "--july-manifest",
                paths.run_root / "july_august" / "stock_manifest.json",
                "--early-manifest",
                paths.run_root / "early9_trial" / "daily_manifest.json",
                "--octdec-manifest",
                paths.run_root / "october_december_trial" / "daily_manifest.json",
                "--early-methodology",
                paths.run_root / "early9_trial" / "daily_methodology.json",
                "--octdec-methodology",
                paths.run_root / "october_december_trial" / "daily_methodology.json",
                "--temp-dir",
                paths.temp / "combined_minute_trial_features",
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--workers",
                args.workers,
                "--worker-memory-limit",
                args.worker_memory_limit,
                "--min-market-stocks",
                args.min_market_stocks,
            ),
            lambda: _check_combined_stock_features(args, paths),
        ),
        StageSpec(
            "lone_goose_yearly",
            "生成孤雁年度文件",
            command(
                "build_july_august_minute_features.py",
                "--stage",
                "lone-goose",
                "--universe",
                "base",
                "--minute-data-dir",
                Path(args.minute_data_dir).resolve(),
                "--base-cache-glob",
                Path(args.base_cache_glob).resolve(),
                "--market-file",
                paths.shared_yearly,
                "--stock-dir",
                paths.julaug_stock,
                "--lone-goose-dir",
                paths.lone_goose,
                "--manifest",
                paths.run_root / "july_august" / "lone_goose_manifest.json",
                "--temp-dir",
                paths.temp / "lone_goose",
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--threads",
                args.threads,
                "--memory-limit",
                args.memory_limit,
                "--lone-goose-stock-batch-size",
                args.lone_goose_stock_batch_size,
            ),
            lambda: _check_lone_goose(args, paths),
        ),
        StageSpec(
            "early9_trial_daily",
            "生成早期9项试算日值",
            command(
                "build_trial_minute_features.py",
                "--data-dir",
                Path(args.minute_data_dir).resolve(),
                "--output-dir",
                paths.early_trial_daily,
                "--market-file",
                paths.shared_yearly,
                "--manifest",
                paths.run_root / "early9_trial" / "daily_manifest.json",
                "--cache-methodology",
                paths.run_root / "early9_trial" / "daily_methodology.json",
                "--temp-dir",
                paths.temp / "early9_trial",
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--workers",
                args.workers,
                "--worker-memory-limit",
                args.worker_memory_limit,
                "--min-market-stocks",
                args.min_market_stocks,
                "--stocks-only",
            ),
            lambda: _check_early_trial(args, paths),
        ),
        StageSpec(
            "octdec_trial_daily",
            "生成十至十二月试算日值",
            command(
                "build_october_december_trial_daily.py",
                "--data-dir",
                Path(args.minute_data_dir).resolve(),
                "--output-dir",
                paths.octdec_daily,
                "--manifest",
                paths.run_root / "october_december_trial" / "daily_manifest.json",
                "--methodology",
                paths.run_root / "october_december_trial" / "daily_methodology.json",
                "--temp-dir",
                paths.temp / "octdec_daily",
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--workers",
                args.workers,
                "--worker-memory-limit",
                args.worker_memory_limit,
            ),
            lambda: _check_octdec_daily(args, paths),
        ),
        StageSpec(
            "network_252",
            "生成252日共同跳跃网络",
            command(
                "build_october_december_trial_network.py",
                "--daily-dir",
                paths.octdec_daily,
                "--base-cache-glob",
                Path(args.base_cache_glob).resolve(),
                "--output-root",
                paths.octdec_network,
                "--output-mode",
                "compact",
                "--temp-dir",
                paths.temp / "network_252",
                "--memory-limit",
                args.memory_limit,
                "--threads",
                args.threads,
                "--start",
                args.cache_start,
                "--end",
                args.cache_end,
                "--window",
                252,
            ),
            lambda: _check_network_compact(args, paths),
        ),
        StageSpec(
            "backtest_early4",
            "回测早期4个严格因子",
            command(
                "run_early_four_minute_backtest.py",
                *common_backtest,
                "--market-stats",
                paths.shared_yearly,
                "--output-dir",
                paths.backtest_early4,
            ),
            lambda: check_backtest_result(
                paths.backtest_early4,
                _group_codes(0),
                expected_backtest_settings,
            ),
        ),
        StageSpec(
            "backtest_julaug",
            "回测七八月严格与试算因子",
            command(
                "run_july_august_backtest.py",
                *common_backtest,
                "--base-cache-glob",
                base_glob,
                "--may-cache-glob",
                Path(args.may_cache_glob).resolve(),
                "--june-cache-glob",
                Path(args.june_cache_glob).resolve(),
                "--july-august-cache-glob",
                paths.julaug_stock / "*.parquet",
                "--lone-goose-cache-glob",
                paths.lone_goose / "*.parquet",
                "--factor-codes",
                *_group_codes(1, 4),
                "--output-dir",
                paths.backtest_julaug,
            ),
            lambda: check_backtest_result(
                paths.backtest_julaug,
                _group_codes(1, 4),
                expected_backtest_settings,
            ),
        ),
        StageSpec(
            "backtest_sepdec",
            "回测九至十二月严格因子",
            command(
                "run_september_december_backtest.py",
                *common_backtest,
                "--factor-codes",
                *_group_codes(2),
                "--output-dir",
                paths.backtest_sepdec,
            ),
            lambda: check_backtest_result(
                paths.backtest_sepdec,
                _group_codes(2),
                expected_backtest_settings,
            ),
        ),
        StageSpec(
            "backtest_early9_trial",
            "回测早期9项试算因子",
            command(
                "run_trial_minute_backtest.py",
                "--base-cache-glob",
                base_glob,
                "--trial-cache-dir",
                paths.early_trial_daily,
                *common_backtest,
                "--output-dir",
                paths.backtest_early_trial,
            ),
            lambda: check_backtest_result(
                paths.backtest_early_trial,
                _group_codes(3),
                expected_backtest_settings,
            ),
        ),
        StageSpec(
            "backtest_octdec_trial",
            "回测十至十二月14项试算因子",
            command(
                "run_october_december_trial_backtest.py",
                "--base-cache-glob",
                base_glob,
                "--trial-cache-dir",
                paths.octdec_daily,
                "--minute-v3-glob",
                v3_glob,
                "--network-root",
                paths.octdec_network,
                *common_backtest,
                "--output-dir",
                paths.backtest_octdec_trial,
            ),
            lambda: check_backtest_result(
                paths.backtest_octdec_trial,
                _group_codes(5),
                expected_backtest_settings,
            ),
        ),
        StageSpec(
            "assemble",
            "汇总51个新增因子结果",
            command(
                "assemble_minute_completion_results.py",
                "--early-strict-dir",
                paths.backtest_early4,
                "--julaug-strict-dir",
                paths.backtest_julaug,
                "--sepdec-strict-dir",
                paths.backtest_sepdec,
                "--early-sep-trial-dir",
                paths.backtest_early_trial,
                "--julaug-trial-dir",
                paths.backtest_julaug,
                "--octdec-trial-dir",
                paths.backtest_octdec_trial,
                *recent_assemble_arguments,
                "--output-dir",
                paths.final_output,
            ),
            lambda: _check_assembled(paths, args.recent_2026_dir),
        ),
    ]
    assert tuple(stage.name for stage in stages) == STAGE_NAMES
    return stages


_V3_BUILD_SCRIPT_NAMES = (
    "build_minute_daily_extra_by_stock.py",
    "build_minute_daily_extra_incremental_by_stock.py",
)


def _is_v3_build_command(command: str) -> bool:
    lowered = command.lower()
    return any(name in lowered for name in _V3_BUILD_SCRIPT_NAMES)


def _v3_external_process_running() -> bool:
    try:
        import psutil

        for process in psutil.process_iter(["pid", "cmdline"]):
            if process.info["pid"] == os.getpid():
                continue
            command = " ".join(process.info.get("cmdline") or []).lower()
            if _is_v3_build_command(command):
                return True
    except Exception:
        return False
    return False


def _status_paths(paths: PipelinePaths, index: int, stage: StageSpec) -> tuple[Path, Path]:
    prefix = f"{index + 1:02d}_{stage.name}"
    return paths.logs / f"{prefix}.log", paths.statuses / f"{prefix}.json"


def _write_short_log(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _execute_stage(
    stage: StageSpec,
    index: int,
    paths: PipelinePaths,
    dry_run: bool,
) -> tuple[bool, dict[str, object]]:
    log_path, status_path = _status_paths(paths, index, stage)
    before = stage.check()
    base_status: dict[str, object] = {
        "stage": stage.name,
        "stage_number": index + 1,
        "description": stage.description,
        "command": list(stage.command),
        "command_text": _command_text(stage.command),
        "log_path": str(log_path),
        "status_path": str(status_path),
        "check_before": {
            "complete": before.complete,
            "reason": before.reason,
            "details": before.details,
        },
        "updated_at_utc": _now(),
    }
    if dry_run:
        base_status.update(
            {
                "status": "dry_run",
                "would_skip": before.complete,
                "exit_code": None,
            }
        )
        _write_short_log(
            log_path,
            ["仅显示命令，未执行。", base_status["command_text"]],
        )
        _atomic_write_json(base_status, status_path)
        return True, base_status
    if before.complete:
        base_status.update({"status": "skipped", "exit_code": 0})
        _write_short_log(log_path, [f"已有完整产物，跳过：{before.reason}"])
        _atomic_write_json(base_status, status_path)
        return True, base_status
    if stage.name == "v3_cache" and _v3_external_process_running():
        base_status.update(
            {
                "status": "blocked",
                "exit_code": None,
                "error": "检测到v3主缓存正在由其他进程生成，已停止以免重复运行。",
            }
        )
        _write_short_log(log_path, [base_status["error"]])
        _atomic_write_json(base_status, status_path)
        return False, base_status

    base_status.update({"status": "running", "started_at_utc": _now()})
    _atomic_write_json(base_status, status_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"命令：{base_status['command_text']}\n")
        log.flush()
        try:
            process = subprocess.Popen(
                list(stage.command),
                cwd=str(BASE_DIR),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            exit_code = int(process.wait())
        except Exception as exc:
            exit_code = -1
            log.write(f"\n无法启动：{exc!r}\n")
            base_status["launch_error"] = repr(exc)

    base_status.update(
        {
            "exit_code": exit_code,
            "finished_at_utc": _now(),
            "updated_at_utc": _now(),
        }
    )
    if exit_code != 0:
        base_status.update(
            {
                "status": "failed",
                "error": f"命令退出码为{exit_code}，后续阶段未执行。",
            }
        )
        _atomic_write_json(base_status, status_path)
        return False, base_status
    after = stage.check()
    base_status["check_after"] = {
        "complete": after.complete,
        "reason": after.reason,
        "details": after.details,
    }
    if not after.complete:
        base_status.update(
            {
                "status": "failed",
                "error": "命令返回成功，但产物检查没有通过，后续阶段未执行。",
            }
        )
        _atomic_write_json(base_status, status_path)
        return False, base_status
    base_status["status"] = "completed"
    _atomic_write_json(base_status, status_path)
    return True, base_status


def run_pipeline(args: argparse.Namespace) -> int:
    if args.workers < 1 or args.threads < 1:
        raise ValueError("进程数和线程数必须至少为1。")
    if args.lone_goose_stock_batch_size < 0:
        raise ValueError("孤雁股票批量不能小于0。")
    if pd.Timestamp(args.cache_end) < pd.Timestamp(args.cache_start):
        raise ValueError("缓存结束日期不能早于开始日期。")
    if pd.Timestamp(args.backtest_end) < pd.Timestamp(args.backtest_start):
        raise ValueError("回测结束日期不能早于开始日期。")
    paths = make_paths(args)
    for directory in [paths.run_root, paths.logs, paths.statuses, paths.temp]:
        directory.mkdir(parents=True, exist_ok=True)
    stages = build_stage_specs(args, paths)
    start_index = STAGE_NAMES.index(args.from_stage)
    end_index = STAGE_NAMES.index(args.to_stage)
    if start_index > end_index:
        raise ValueError("from-stage不能排在to-stage之后。")

    if not args.dry_run:
        for index in range(start_index):
            previous = stages[index].check()
            if not previous.complete:
                summary = {
                    "status": "failed",
                    "reason": f"不能从{args.from_stage}开始：前置阶段{stages[index].name}不完整。",
                    "failed_stage": stages[index].name,
                    "check": previous.details,
                    "updated_at_utc": _now(),
                }
                _atomic_write_json(summary, paths.run_root / "pipeline_status.json")
                print(summary["reason"], flush=True)
                return 2

    records = []
    for index in range(start_index, end_index + 1):
        stage = stages[index]
        print(f"[{index + 1}/{len(stages)}] {stage.description}", flush=True)
        if args.dry_run:
            print(_command_text(stage.command), flush=True)
        elif stage.name in {"backtest_early4", "backtest_sepdec"}:
            try:
                registered = _register_primary_backtest_sources(args)
                print(
                    "已确认基础日值和v3分钟日值使用正式目录："
                    f"{', '.join(registered['source_name'].astype(str))}",
                    flush=True,
                )
            except Exception as exc:
                summary = {
                    "status": "failed",
                    "reason": f"正式回测数据目录登记失败：{exc}",
                    "failed_stage": stage.name,
                    "updated_at_utc": _now(),
                }
                _atomic_write_json(summary, paths.run_root / "pipeline_status.json")
                print(summary["reason"], flush=True)
                return 2
        success, record = _execute_stage(stage, index, paths, args.dry_run)
        records.append(record)
        pipeline_status = {
            "status": "dry_run" if args.dry_run else ("running" if success else "failed"),
            "from_stage": args.from_stage,
            "to_stage": args.to_stage,
            "current_stage": stage.name,
            "records": records,
            "updated_at_utc": _now(),
        }
        _atomic_write_json(pipeline_status, paths.run_root / "pipeline_status.json")
        if not success:
            print(f"阶段失败，已停止：{stage.name}", flush=True)
            return int(record.get("exit_code") or 1)

    final_status = {
        "status": "dry_run" if args.dry_run else "completed",
        "from_stage": args.from_stage,
        "to_stage": args.to_stage,
        "records": records,
        "final_output": str(paths.final_output),
        "updated_at_utc": _now(),
    }
    _atomic_write_json(final_status, paths.run_root / "pipeline_status.json")
    print("命令检查完成。" if args.dry_run else "全部指定阶段已完成。", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_pipeline(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
