# -*- coding: utf-8 -*-
"""独立计算并汇总51个分钟因子的2026年1至5月近期IC。"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from assemble_minute_completion_results import GROUP_SPECS
import build_october_december_trial_daily as octdec_trial_daily
import build_october_december_trial_network as octdec_network
import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as early_trial_daily
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
WORK_ROOT = BASE_DIR / ".codex-artifact" / "2026_rank_check"
DEFAULT_RUNS_ROOT = WORK_ROOT / "minute_completion_runs"
DEFAULT_OUTPUT_DIR = WORK_ROOT / "minute_completion_results"
DEFAULT_DATABASE = WORK_ROOT / "minute_completion_duckdb" / "factor_store.duckdb"
DEFAULT_TEMP_DIR = WORK_ROOT / "minute_completion_duckdb" / "tmp"
FULL_PIPELINE_ROOT = BASE_DIR / "minute_completion_runs"

RUN_START = pd.Timestamp("2025-06-02")
BACKTEST_START = pd.Timestamp("2025-12-01")
RUN_END = pd.Timestamp("2026-05-29")
IC_START = pd.Timestamp("2026-01-01")
IC_END = pd.Timestamp("2026-05-29")
EXPECTED_MONTHS = tuple(f"2026-{month:02d}" for month in range(1, 6))
MIN_SOURCE_CODE_COVERAGE = 0.99

RAW_OUTPUT = "minute_completion_2026_ic_raw.csv"
MONTHLY_OUTPUT = "minute_completion_2026_monthly_ic.csv"
SUMMARY_OUTPUT = "minute_completion_2026_summary.csv"
QUALITY_OUTPUT = "minute_completion_2026_quality_checks.csv"


class CompletionCheckError(ValueError):
    """输入缓存或回测结果不完整。"""


@dataclass(frozen=True)
class EntrypointCall:
    name: str
    module: str
    output_dir: Path
    arguments: tuple[str, ...]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-cache-glob",
        type=Path,
        default=WORK_ROOT / "cache" / "base" / "*.parquet",
    )
    parser.add_argument(
        "--may-cache-glob",
        type=Path,
        default=WORK_ROOT / "cache" / "may" / "*.parquet",
    )
    parser.add_argument(
        "--june-cache-glob",
        type=Path,
        default=WORK_ROOT / "cache" / "june" / "*.parquet",
    )
    parser.add_argument(
        "--v3-cache-glob",
        type=Path,
        default=factor_store.DEFAULT_SOURCES["minute_extra_daily"],
    )
    parser.add_argument(
        "--july-august-cache-glob",
        type=Path,
        default=FULL_PIPELINE_ROOT / "july_august" / "daily_by_stock" / "*.parquet",
    )
    parser.add_argument(
        "--lone-goose-cache-glob",
        type=Path,
        default=FULL_PIPELINE_ROOT / "july_august" / "lone_goose_by_year" / "*.parquet",
    )
    parser.add_argument(
        "--early-trial-cache-dir",
        type=Path,
        default=FULL_PIPELINE_ROOT / "early9_trial" / "daily_by_stock",
    )
    parser.add_argument(
        "--octdec-trial-cache-dir",
        type=Path,
        default=FULL_PIPELINE_ROOT / "october_december_trial" / "daily_by_stock",
    )
    parser.add_argument(
        "--network-root",
        type=Path,
        default=FULL_PIPELINE_ROOT / "october_december_trial" / "network",
    )
    parser.add_argument(
        "--market-stats",
        type=Path,
        default=FULL_PIPELINE_ROOT / "shared_market" / "yearly",
    )
    parser.add_argument(
        "--qlib-dir", type=Path, default=WORK_ROOT / "qlib_overlay"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--duckdb-temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--duckdb-threads", type=int, default=8)
    parser.add_argument("--duckdb-memory-limit", default="8GB")
    parser.add_argument("--min-stocks", type=int, default=50)

    parser.add_argument(
        "--early-four-result-dir",
        type=Path,
        default=DEFAULT_RUNS_ROOT / "early_four",
    )
    parser.add_argument(
        "--july-august-result-dir",
        type=Path,
        default=DEFAULT_RUNS_ROOT / "july_august",
    )
    parser.add_argument(
        "--september-december-result-dir",
        type=Path,
        default=DEFAULT_RUNS_ROOT / "september_december",
    )
    parser.add_argument(
        "--early-trial-result-dir",
        type=Path,
        default=DEFAULT_RUNS_ROOT / "early_trial",
    )
    parser.add_argument(
        "--octdec-trial-result-dir",
        type=Path,
        default=DEFAULT_RUNS_ROOT / "octdec_trial",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-runs", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def expected_factor_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group in GROUP_SPECS:
        for code in group.codes:
            rows.append(
                {
                    "factor": code,
                    "source_group": group.display_name,
                    "group_argument": group.argument_name,
                    "formula_mode": group.formula_mode,
                    "formula_label": "严格" if group.formula_mode == "strict" else "试算",
                    "factor_order": len(rows),
                }
            )
    frame = pd.DataFrame(rows)
    strict_count = int(frame["formula_mode"].eq("strict").sum())
    trial_count = int(frame["formula_mode"].eq("trial").sum())
    if len(frame) != 51 or frame["factor"].nunique() != 51:
        raise RuntimeError("GROUP_SPECS必须恰好包含51个不重复因子代码。")
    if strict_count != 24 or trial_count != 27:
        raise RuntimeError(
            f"GROUP_SPECS必须是24个严格因子和27个试算因子；当前为{strict_count}+{trial_count}。"
        )
    return frame


def _as_glob(path: Path) -> Path:
    return path / "*.parquet" if path.is_dir() else path


def _glob_files(path: Path) -> list[Path]:
    pattern = _as_glob(Path(path))
    if pattern.is_file():
        return [pattern]
    return sorted(pattern.parent.glob(pattern.name))


def _network_output_mode(network_root: Path) -> str:
    manifest_path = Path(network_root) / "network_manifest.json"
    if not manifest_path.is_file():
        return "legacy"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CompletionCheckError(f"无法读取网络说明文件{manifest_path}：{exc}") from exc
    mode = str(manifest.get("output_mode", "legacy")).strip().lower()
    if mode not in {"compact", "legacy"}:
        raise CompletionCheckError(f"网络说明文件的output_mode不正确：{mode}")
    return mode


def _result_dirs(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "early_strict_dir": Path(args.early_four_result_dir),
        "julaug_strict_dir": Path(args.july_august_result_dir),
        "sepdec_strict_dir": Path(args.september_december_result_dir),
        "early_sep_trial_dir": Path(args.early_trial_result_dir),
        "julaug_trial_dir": Path(args.july_august_result_dir),
        "octdec_trial_dir": Path(args.octdec_trial_result_dir),
    }


def source_paths(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "base_daily": _as_glob(Path(args.base_cache_glob)),
        "may_daily": _as_glob(Path(args.may_cache_glob)),
        "june_daily": _as_glob(Path(args.june_cache_glob)),
        "minute_extra_daily": _as_glob(Path(args.v3_cache_glob)),
        "july_august_daily": _as_glob(Path(args.july_august_cache_glob)),
        "lone_goose_daily": _as_glob(Path(args.lone_goose_cache_glob)),
        "early_trial_daily": _as_glob(Path(args.early_trial_cache_dir)),
        "octdec_trial_daily": _as_glob(Path(args.octdec_trial_cache_dir)),
    }


def validate_run_inputs(args: argparse.Namespace) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    database = Path(args.database).resolve()
    if database == Path(factor_store.DEFAULT_DATABASE).resolve():
        errors.append("独立DuckDB文件不能使用主数据库位置")
    if args.duckdb_threads < 1:
        errors.append("DuckDB线程数必须大于0")
    if args.min_stocks < 1:
        errors.append("最低股票数必须大于0")

    labels = {
        "base_daily": "基础日值缓存",
        "may_daily": "五月日值缓存",
        "june_daily": "六月日值缓存",
        "minute_extra_daily": "v3分钟日值缓存",
        "july_august_daily": "七八月分钟日值缓存",
        "lone_goose_daily": "孤雁日值缓存",
        "early_trial_daily": "早期九项试算缓存",
        "octdec_trial_daily": "十至十二月试算缓存",
    }
    source_files: dict[str, list[Path]] = {}
    for source_name, path in source_paths(args).items():
        files = _glob_files(path)
        source_files[source_name] = files
        if not files:
            errors.append(f"{labels[source_name]}没有Parquet文件：{path}")

    base_codes = {path.stem.lower() for path in source_files.get("base_daily", [])}
    source_code_sets: dict[str, set[str]] = {"base_daily": base_codes}
    source_code_coverage: list[dict[str, object]] = []
    for source_name in (
        "may_daily",
        "june_daily",
        "minute_extra_daily",
        "july_august_daily",
        "early_trial_daily",
        "octdec_trial_daily",
    ):
        found_codes = {path.stem.lower() for path in source_files.get(source_name, [])}
        source_code_sets[source_name] = found_codes
        missing_codes = sorted(base_codes - found_codes)
        covered_codes = len(base_codes & found_codes)
        coverage_ratio = covered_codes / len(base_codes) if base_codes else 0.0
        source_code_coverage.append(
            {
                "source_name": source_name,
                "base_code_count": len(base_codes),
                "covered_code_count": covered_codes,
                "missing_code_count": len(missing_codes),
                "coverage_ratio": coverage_ratio,
                "missing_examples": missing_codes[:10],
            }
        )
        if missing_codes:
            message = (
                f"{labels[source_name]}缺少{len(missing_codes)}只近期股票，"
                f"例如：{', '.join(missing_codes[:10])}"
            )
            if coverage_ratio < MIN_SOURCE_CODE_COVERAGE:
                errors.append(message)
            else:
                warnings.append(
                    f"{message}；覆盖率为{coverage_ratio:.2%}，继续计算。"
                )

    compared_sets = [base_codes]
    compared_sets.extend(
        source_code_sets[name]
        for name in (
            "may_daily",
            "june_daily",
            "minute_extra_daily",
            "july_august_daily",
            "early_trial_daily",
            "octdec_trial_daily",
        )
    )
    common_codes = set.intersection(*compared_sets) if base_codes else set()
    common_ratio = len(common_codes) / len(base_codes) if base_codes else 0.0
    if base_codes and common_ratio < MIN_SOURCE_CODE_COVERAGE:
        errors.append(
            "全部逐股缓存共同覆盖率不足："
            f"{len(common_codes)}/{len(base_codes)}={common_ratio:.2%}，"
            f"最低要求为{MIN_SOURCE_CODE_COVERAGE:.0%}。"
        )

    market_path = Path(args.market_stats)
    try:
        shared_market.list_market_stat_files(market_path)
    except FileNotFoundError:
        errors.append(f"共用市场分钟表不存在：{market_path}")
    network_root = Path(args.network_root)
    network_mode = _network_output_mode(network_root)
    required_parts = ("nodes",) if network_mode == "compact" else ("nodes", "edges")
    for part in required_parts:
        if not list((network_root / part).glob("*.parquet")):
            errors.append(f"跳跃网络缺少{part}文件：{network_root / part}")
    if network_mode == "compact":
        node_files = sorted((network_root / "nodes").glob("*.parquet"))
        if node_files:
            try:
                columns = set(pd.read_parquet(node_files[0]).columns)
            except Exception as exc:
                errors.append(f"无法读取汇总网络文件{node_files[0]}：{exc}")
            else:
                missing = sorted(set(octdec_network.COMPACT_VALUE_COLUMNS) - columns)
                if missing:
                    errors.append(
                        "汇总网络文件缺少回测字段：" + ", ".join(missing)
                    )
    qlib_dir = Path(args.qlib_dir)
    for relative in (
        Path("calendars/day.txt"),
        Path("instruments/csi800.txt"),
        Path("instruments/csi1000.txt"),
        Path("features/sh000985/close.day.bin"),
    ):
        if not (qlib_dir / relative).is_file():
            errors.append(f"Qlib覆盖目录缺少文件：{qlib_dir / relative}")
    if errors:
        raise CompletionCheckError("运行输入不完整：\n- " + "\n- ".join(errors))
    for warning in warnings:
        print(f"提示：{warning}", flush=True)
    return {
        "minimum_required_ratio": MIN_SOURCE_CODE_COVERAGE,
        "base_code_count": len(base_codes),
        "common_code_count": len(common_codes),
        "common_coverage_ratio": common_ratio,
        "sources": source_code_coverage,
        "warnings": warnings,
    }


def _coverage_row(
    connection, relation_sql: str, date_column: str = "date"
) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    row = connection.execute(
        f"SELECT min({date_column}), max({date_column}), count(*) FROM {relation_sql}"
    ).fetchone()
    if row is None or int(row[2]) == 0 or row[0] is None or row[1] is None:
        raise CompletionCheckError(f"缓存没有日期记录：{relation_sql}")
    return pd.Timestamp(row[0]), pd.Timestamp(row[1]), int(row[2])


def register_and_check_sources(args: argparse.Namespace) -> pd.DataFrame:
    calendar = pd.to_datetime(
        pd.read_csv(Path(args.qlib_dir) / "calendars" / "day.txt", header=None)[0],
        errors="coerce",
    ).dropna()
    if calendar.empty or calendar.min() > RUN_START or calendar.max() < RUN_END:
        raise CompletionCheckError(
            "Qlib交易日未覆盖2025-06-02至2026-05-29："
            f"{calendar.min()}至{calendar.max()}"
        )
    required_calendar = calendar[
        calendar.between(RUN_START, RUN_END, inclusive="both")
    ]
    if required_calendar.empty:
        raise CompletionCheckError("Qlib在检查区间内没有交易日。")
    required_start = pd.Timestamp(required_calendar.min())
    required_end = pd.Timestamp(required_calendar.max())

    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.duckdb_temp_dir),
        threads=int(args.duckdb_threads),
        memory_limit=str(args.duckdb_memory_limit),
    )
    rows: list[dict[str, object]] = []
    with factor_store.connect(settings) as connection:
        registered = factor_store.register_sources(connection, source_paths(args))
        for source_name in source_paths(args):
            minimum, maximum, count = _coverage_row(
                connection, f'"source_{source_name}"'
            )
            rows.append(
                {
                    "source_name": source_name,
                    "minimum_date": minimum,
                    "maximum_date": maximum,
                    "row_count": count,
                }
            )

        market_sql = shared_market.market_read_sql(args.market_stats)
        minimum, maximum, count = _coverage_row(connection, market_sql)
        rows.append(
            {
                "source_name": "shared_market_minute_stats",
                "minimum_date": minimum,
                "maximum_date": maximum,
                "row_count": count,
            }
        )

        nodes_glob = Path(args.network_root) / "nodes" / "*.parquet"
        nodes_sql = (
            "read_parquet('"
            + str(nodes_glob.resolve()).replace("\\", "/").replace("'", "''")
            + "', union_by_name=true)"
        )
        minimum, maximum, count = _coverage_row(
            connection, nodes_sql, "snapshot_date"
        )
        if _network_output_mode(Path(args.network_root)) == "compact":
            node_columns = set(
                connection.execute(f"DESCRIBE SELECT * FROM {nodes_sql}")
                .fetchdf()["column_name"]
                .astype(str)
            )
            missing = sorted(
                set(octdec_network.COMPACT_VALUE_COLUMNS) - node_columns
            )
            if missing:
                raise CompletionCheckError(
                    "汇总网络文件缺少回测字段：" + ", ".join(missing)
                )
        rows.append(
            {
                "source_name": "octdec_network_nodes",
                "minimum_date": minimum,
                "maximum_date": maximum,
                "row_count": count,
            }
        )

    coverage = pd.DataFrame(rows)
    incomplete = coverage[
        (coverage["minimum_date"] > required_start)
        | (coverage["maximum_date"] < required_end)
    ]
    if not incomplete.empty:
        descriptions = [
            f"{row.source_name}={row.minimum_date.date()}至{row.maximum_date.date()}"
            for row in incomplete.itertuples(index=False)
        ]
        raise CompletionCheckError(
            "缓存日期未覆盖2025-06-02至2026-05-29内的交易日"
            f"（{required_start.date()}至{required_end.date()}）：\n- "
            + "\n- ".join(descriptions)
        )
    return coverage.merge(
        registered[["source_name", "file_count", "column_count"]],
        on="source_name",
        how="left",
    )


def _common_arguments(args: argparse.Namespace, output_dir: Path) -> list[str]:
    return [
        "--database",
        str(Path(args.database)),
        "--duckdb-temp-dir",
        str(Path(args.duckdb_temp_dir)),
        "--duckdb-threads",
        str(args.duckdb_threads),
        "--duckdb-memory-limit",
        str(args.duckdb_memory_limit),
        "--output-dir",
        str(output_dir),
        "--qlib-dir",
        str(Path(args.qlib_dir)),
        "--start",
        RUN_START.strftime("%Y-%m-%d"),
        "--backtest-start",
        BACKTEST_START.strftime("%Y-%m-%d"),
        "--end",
        RUN_END.strftime("%Y-%m-%d"),
        "--split-date",
        "2025-12-31",
    ]


def build_entrypoint_calls(args: argparse.Namespace) -> tuple[EntrypointCall, ...]:
    early_four = Path(args.early_four_result_dir)
    julaug = Path(args.july_august_result_dir)
    sepdec = Path(args.september_december_result_dir)
    early_trial = Path(args.early_trial_result_dir)
    octdec_trial = Path(args.octdec_trial_result_dir)
    return (
        EntrypointCall(
            "早期4项严格因子",
            "run_early_four_minute_backtest",
            early_four,
            tuple(
                _common_arguments(args, early_four)
                + ["--market-stats", str(Path(args.market_stats))]
            ),
        ),
        EntrypointCall(
            "七八月25项入口",
            "run_july_august_backtest",
            julaug,
            tuple(
                _common_arguments(args, julaug)
                + [
                    "--factor-codes",
                    *GROUP_SPECS[1].codes,
                    *GROUP_SPECS[4].codes,
                ]
            ),
        ),
        EntrypointCall(
            "九至十二月入口",
            "run_september_december_backtest",
            sepdec,
            tuple(
                _common_arguments(args, sepdec)
                + ["--factor-codes", *GROUP_SPECS[2].codes]
            ),
        ),
        EntrypointCall(
            "早期九项试算因子",
            "run_trial_minute_backtest",
            early_trial,
            tuple(
                _common_arguments(args, early_trial)
                + [
                    "--base-cache-glob",
                    str(_as_glob(Path(args.base_cache_glob))),
                    "--trial-cache-dir",
                    str(Path(args.early_trial_cache_dir)),
                    "--min-stocks",
                    str(args.min_stocks),
                ]
            ),
        ),
        EntrypointCall(
            "十至十二月十四项试算因子",
            "run_october_december_trial_backtest",
            octdec_trial,
            tuple(
                _common_arguments(args, octdec_trial)
                + [
                    "--base-cache-glob",
                    str(_as_glob(Path(args.base_cache_glob))),
                    "--trial-cache-dir",
                    str(Path(args.octdec_trial_cache_dir)),
                    "--minute-v3-glob",
                    str(_as_glob(Path(args.v3_cache_glob))),
                    "--network-root",
                    str(Path(args.network_root)),
                    "--min-stocks",
                    str(args.min_stocks),
                ]
            ),
        ),
    )


_CHILD_CODE = """
import importlib
import json
import os
from pathlib import Path
import duckdb_factor_store as factor_store

factor_store.DEFAULT_SOURCES.update(
    {key: Path(value) for key, value in json.loads(os.environ["MINUTE_CHECK_SOURCES"]).items()}
)
module = importlib.import_module(os.environ["MINUTE_CHECK_MODULE"])
raise SystemExit(module.main(json.loads(os.environ["MINUTE_CHECK_ARGUMENTS"])))
"""


def run_entrypoints(args: argparse.Namespace) -> list[dict[str, object]]:
    source_json = json.dumps(
        {key: str(value) for key, value in source_paths(args).items()},
        ensure_ascii=False,
    )
    records: list[dict[str, object]] = []
    for index, call in enumerate(build_entrypoint_calls(args), start=1):
        call.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{index}/5] {call.name}...", flush=True)
        environment = os.environ.copy()
        # 只固定子进程输出编码，不强制 site.py 用UTF-8读取用户已有的.pth文件。
        # Windows上的旧项目路径文件可能按系统编码保存。
        environment.pop("PYTHONUTF8", None)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["MINUTE_CHECK_SOURCES"] = source_json
        environment["MINUTE_CHECK_MODULE"] = call.module
        environment["MINUTE_CHECK_ARGUMENTS"] = json.dumps(
            list(call.arguments), ensure_ascii=False
        )
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-c", _CHILD_CODE],
            cwd=BASE_DIR,
            env=environment,
            check=False,
        )
        elapsed = time.perf_counter() - started
        records.append(
            {
                "name": call.name,
                "module": call.module,
                "output_dir": str(call.output_dir.resolve()),
                "return_code": int(completed.returncode),
                "elapsed_seconds": elapsed,
            }
        )
        if completed.returncode != 0:
            raise CompletionCheckError(
                f"{call.name}运行失败，返回码{completed.returncode}；后续入口未启动。"
            )
    return records


def _read_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    if not path.is_file():
        raise CompletionCheckError(f"缺少结果文件：{path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise CompletionCheckError(f"无法读取结果文件{path}：{exc}") from exc
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise CompletionCheckError(f"结果文件{path}缺少列：{', '.join(missing)}")
    return frame


def load_recent_raw_ic(args: argparse.Namespace) -> pd.DataFrame:
    expected = expected_factor_frame()
    directories = _result_dirs(args)
    pieces: list[pd.DataFrame] = []
    errors: list[str] = []
    raw_required = {
        "factor",
        "signal_date",
        "entry_date",
        "exit_date",
        "ic",
        "stock_count",
        "signal_frequency",
        "return_horizon",
    }
    for group in GROUP_SPECS:
        directory = directories[group.argument_name]
        raw_path = directory / "original_frequency_ic.csv"
        status_path = directory / "factor_status.csv"
        try:
            raw = _read_csv(raw_path, raw_required)
            status = _read_csv(status_path, {"factor"})
        except CompletionCheckError as exc:
            errors.append(f"{group.display_name}：{exc}")
            continue
        raw = raw.copy()
        raw["factor"] = raw["factor"].astype("string").str.strip()
        status = status.copy()
        status["factor"] = status["factor"].astype("string").str.strip()
        expected_codes = set(group.codes)
        raw_present = set(raw["factor"].dropna().astype(str))
        status_present = set(status["factor"].dropna().astype(str))
        raw_missing = sorted(expected_codes - raw_present)
        status_missing = sorted(expected_codes - status_present)
        if raw_missing:
            errors.append(
                f"{group.display_name}的original_frequency_ic.csv缺少代码：{', '.join(raw_missing)}"
            )
        if status_missing:
            errors.append(
                f"{group.display_name}的factor_status.csv缺少代码：{', '.join(status_missing)}"
            )
        duplicate_status = sorted(
            status.loc[
                status["factor"].isin(group.codes)
                & status["factor"].duplicated(keep=False),
                "factor",
            ]
            .dropna()
            .astype(str)
            .unique()
        )
        if duplicate_status:
            errors.append(
                f"{group.display_name}的factor_status.csv代码重复：{', '.join(duplicate_status)}"
            )
        if raw_missing or status_missing or duplicate_status:
            continue

        date_error = False
        for column in ("signal_date", "entry_date", "exit_date"):
            try:
                raw[column] = pd.to_datetime(raw[column], errors="raise")
            except Exception as exc:
                errors.append(f"{group.display_name}的{column}无法转成日期：{exc}")
                date_error = True
        if date_error:
            continue
        raw["ic"] = pd.to_numeric(raw["ic"], errors="coerce")
        raw["stock_count"] = pd.to_numeric(raw["stock_count"], errors="coerce")
        selected = raw[
            raw["factor"].isin(group.codes)
            & raw["exit_date"].between(IC_START, IC_END)
        ].copy()
        recent_present = set(selected["factor"].dropna().astype(str))
        recent_missing = sorted(expected_codes - recent_present)
        if recent_missing:
            errors.append(
                f"{group.display_name}在2026-01-01至2026-05-29没有IC的代码：{', '.join(recent_missing)}"
            )
            continue
        if selected[["ic", "stock_count"]].isna().any().any():
            errors.append(f"{group.display_name}含有无法使用的IC或股票数")
            continue

        status_selected = status[status["factor"].isin(group.codes)].set_index(
            "factor"
        )
        if "calculated" in status_selected.columns:
            calculated = status_selected["calculated"].astype(str).str.lower()
            failed = sorted(
                calculated[~calculated.isin(["true", "1"])].index.astype(str)
            )
            if failed:
                errors.append(
                    f"{group.display_name}的factor_status显示未计算：{', '.join(failed)}"
                )
                continue
        name_column = "name" if "name" in status_selected.columns else None
        selected["factor_name"] = (
            selected["factor"].map(status_selected[name_column])
            if name_column
            else selected["factor"]
        )
        selected["formula_mode"] = group.formula_mode
        selected["formula_label"] = (
            "严格" if group.formula_mode == "strict" else "试算"
        )
        selected["source_group"] = group.display_name
        selected["source_file"] = str(raw_path.resolve())
        pieces.append(selected)
    if errors:
        raise CompletionCheckError("结果不完整：\n- " + "\n- ".join(errors))

    combined = pd.concat(pieces, ignore_index=True, sort=False)
    order = expected.set_index("factor")["factor_order"]
    combined["factor_order"] = combined["factor"].map(order)
    actual_codes = set(combined["factor"].astype(str))
    wanted_codes = set(expected["factor"].astype(str))
    if actual_codes != wanted_codes:
        raise CompletionCheckError(
            "近期IC代码不等于51个目标代码；"
            f"缺少={sorted(wanted_codes - actual_codes)}；多出={sorted(actual_codes - wanted_codes)}"
        )
    duplicate_keys = ["factor", "signal_date", "entry_date", "exit_date"]
    duplicates = combined.duplicated(duplicate_keys, keep=False)
    if duplicates.any():
        examples = combined.loc[duplicates, duplicate_keys].head(10)
        raise CompletionCheckError(
            "近期IC存在重复记录：\n" + examples.to_string(index=False)
        )
    frequency_count = combined.groupby("factor")["signal_frequency"].nunique()
    if (frequency_count != 1).any():
        raise CompletionCheckError(
            "同一因子出现多种信号频率：\n"
            + frequency_count[frequency_count != 1].to_string()
        )
    combined["return_month"] = (
        combined["exit_date"].dt.to_period("M").astype(str)
    )
    columns = [
        "factor",
        "factor_name",
        "formula_mode",
        "formula_label",
        "source_group",
        "signal_frequency",
        "return_horizon",
        "signal_date",
        "entry_date",
        "exit_date",
        "return_month",
        "ic",
        "stock_count",
        "source_file",
        "factor_order",
    ]
    return combined[columns].sort_values(
        ["factor_order", "exit_date", "signal_date"], kind="stable"
    )


def _icir(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    standard = float(clean.std(ddof=1))
    if not np.isfinite(standard) or standard == 0:
        return np.nan
    return float(clean.mean() / standard)


def build_recent_outputs(
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    group_columns = [
        "factor",
        "factor_name",
        "formula_mode",
        "formula_label",
        "source_group",
        "signal_frequency",
        "factor_order",
    ]
    monthly = (
        raw.groupby(group_columns + ["return_month"], as_index=False, dropna=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "count"),
            min_stock_count=("stock_count", "min"),
            avg_stock_count=("stock_count", "mean"),
        )
        .sort_values(["factor_order", "return_month"], kind="stable")
    )
    rows: list[dict[str, object]] = []
    for factor, month_part in monthly.groupby("factor", sort=False):
        raw_part = raw[raw["factor"].eq(factor)]
        first = month_part.iloc[0]
        recent_ic = float(month_part["monthly_ic"].mean())
        rows.append(
            {
                "factor": factor,
                "factor_name": first["factor_name"],
                "formula_mode": first["formula_mode"],
                "formula_label": first["formula_label"],
                "source_group": first["source_group"],
                "signal_frequency": first["signal_frequency"],
                "recent_months": int(month_part["monthly_ic"].notna().sum()),
                "recent_ic": recent_ic,
                "recent_ic_std": float(month_part["monthly_ic"].std(ddof=1)),
                "recent_icir": _icir(month_part["monthly_ic"]),
                "positive_months": int((month_part["monthly_ic"] > 0).sum()),
                "negative_months": int((month_part["monthly_ic"] < 0).sum()),
                "raw_observations": int(raw_part["ic"].notna().sum()),
                "raw_ic_mean": float(raw_part["ic"].mean()),
                "raw_ic_std": float(raw_part["ic"].std(ddof=1)),
                "raw_icir": _icir(raw_part["ic"]),
                "min_stock_count": int(raw_part["stock_count"].min()),
                "avg_stock_count": float(raw_part["stock_count"].mean()),
                "first_exit_date": raw_part["exit_date"].min(),
                "last_exit_date": raw_part["exit_date"].max(),
                "factor_order": int(first["factor_order"]),
            }
        )
    summary = pd.DataFrame(rows)
    summary["recent_abs_ic"] = summary["recent_ic"].abs()
    summary["recent_abs_ic_rank"] = summary["recent_abs_ic"].rank(
        method="min", ascending=False
    ).astype("Int64")
    summary = summary.sort_values(
        ["recent_abs_ic_rank", "factor_order"], kind="stable"
    )

    expected = expected_factor_frame()
    actual_months = monthly.groupby("factor")["return_month"].agg(
        lambda values: tuple(sorted(values.astype(str).unique()))
    )
    duplicate_raw = int(
        raw.duplicated(
            ["factor", "signal_date", "entry_date", "exit_date"]
        ).sum()
    )
    checks = [
        ("目标因子数", 51, int(summary["factor"].nunique())),
        (
            "严格因子数",
            24,
            int(summary["formula_mode"].eq("strict").sum()),
        ),
        (
            "试算因子数",
            27,
            int(summary["formula_mode"].eq("trial").sum()),
        ),
        ("目标代码重复数", 0, int(expected["factor"].duplicated().sum())),
        ("原始IC重复记录数", 0, duplicate_raw),
        (
            "日期下限",
            f">={IC_START.date()}",
            str(raw["exit_date"].min().date()),
        ),
        (
            "日期上限",
            f"<={IC_END.date()}",
            str(raw["exit_date"].max().date()),
        ),
        ("因子月份记录数", 255, int(len(monthly))),
        (
            "完整五个月因子数",
            51,
            int((actual_months == EXPECTED_MONTHS).sum()),
        ),
        ("IC缺失数", 0, int(raw["ic"].isna().sum())),
        ("每个因子信号频率数上限", 1, int(raw.groupby("factor")["signal_frequency"].nunique().max())),
        ("最低股票数", 50, int(raw["stock_count"].min())),
    ]
    quality_rows: list[dict[str, object]] = []
    for item, expected_value, actual_value in checks:
        if item == "日期下限":
            passed = raw["exit_date"].min() >= IC_START
        elif item == "日期上限":
            passed = raw["exit_date"].max() <= IC_END
        elif item == "最低股票数":
            passed = int(actual_value) >= int(expected_value)
        else:
            passed = actual_value == expected_value
        quality_rows.append(
            {
                "check_item": item,
                "expected": expected_value,
                "actual": actual_value,
                "status": "通过" if passed else "失败",
            }
        )
    quality = pd.DataFrame(quality_rows)
    return monthly, summary, quality


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    output = frame.drop(columns=["factor_order"], errors="ignore").copy()
    for column in ("signal_date", "entry_date", "exit_date", "first_exit_date", "last_exit_date"):
        if column in output.columns:
            output[column] = pd.to_datetime(output[column]).dt.strftime("%Y-%m-%d")
    output.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    expected = expected_factor_frame()
    if args.dry_run:
        for index, call in enumerate(build_entrypoint_calls(args), start=1):
            print(
                f"[{index}/5] {call.module} "
                + subprocess.list2cmdline(list(call.arguments)),
                flush=True,
            )
        print("仅显示五次回测的参数，未读取缓存、未运行回测。", flush=True)
        return 0
    coverage = pd.DataFrame()
    stock_code_coverage: dict[str, object] = {}
    run_records: list[dict[str, object]] = []
    if not args.skip_runs:
        stock_code_coverage = validate_run_inputs(args)
        print("登记独立DuckDB并检查缓存日期...", flush=True)
        coverage = register_and_check_sources(args)
        run_records = run_entrypoints(args)
    else:
        if Path(args.database).resolve() == Path(factor_store.DEFAULT_DATABASE).resolve():
            raise CompletionCheckError("独立DuckDB文件不能使用主数据库位置。")
        print("跳过五次回测，只汇总已有结果。", flush=True)

    raw = load_recent_raw_ic(args)
    monthly, summary, quality = build_recent_outputs(raw)
    if stock_code_coverage:
        actual_coverage = float(stock_code_coverage["common_coverage_ratio"])
        coverage_quality = pd.DataFrame(
            [
                {
                    "check_item": "近期逐股缓存共同覆盖率",
                    "expected": f">={MIN_SOURCE_CODE_COVERAGE:.0%}",
                    "actual": f"{actual_coverage:.2%}",
                    "status": (
                        "通过"
                        if actual_coverage >= MIN_SOURCE_CODE_COVERAGE
                        else "失败"
                    ),
                }
            ]
        )
        quality = pd.concat([quality, coverage_quality], ignore_index=True)
    output_dir = Path(args.output_dir)
    _atomic_write_csv(raw, output_dir / RAW_OUTPUT)
    _atomic_write_csv(monthly, output_dir / MONTHLY_OUTPUT)
    _atomic_write_csv(summary, output_dir / SUMMARY_OUTPUT)
    _atomic_write_csv(quality, output_dir / QUALITY_OUTPUT)
    if not coverage.empty:
        _atomic_write_csv(coverage, output_dir / "cache_coverage.csv")
    _atomic_write_json(
        {
            "run_period": f"{RUN_START.date()}至{RUN_END.date()}",
            "backtest_start": str(BACKTEST_START.date()),
            "ic_exit_period": f"{IC_START.date()}至{IC_END.date()}",
            "factor_count": int(len(expected)),
            "strict_count": int(expected["formula_mode"].eq("strict").sum()),
            "trial_count": int(expected["formula_mode"].eq("trial").sum()),
            "group_specs_source": str(
                (BASE_DIR / "assemble_minute_completion_results.py").resolve()
            ),
            "database": str(Path(args.database).resolve()),
            "main_database_unchanged": (
                Path(args.database).resolve()
                != Path(factor_store.DEFAULT_DATABASE).resolve()
            ),
            "skip_runs": bool(args.skip_runs),
            "entrypoint_runs": run_records,
            "result_directories": {
                key: str(value.resolve()) for key, value in _result_dirs(args).items()
            },
            "cache_sources": {
                key: str(value.resolve()) for key, value in source_paths(args).items()
            },
            "stock_code_coverage": stock_code_coverage,
            "qlib_dir": str(Path(args.qlib_dir).resolve()),
            "network_root": str(Path(args.network_root).resolve()),
            "market_stats": str(Path(args.market_stats).resolve()),
            "summary_rule": "先按退出月份求IC均值，再对2026年1至5月的月度IC求均值和样本标准差。",
            "created_at": pd.Timestamp.now().isoformat(),
        },
        output_dir / "methodology.json",
    )
    failures = quality[quality["status"].ne("通过")]
    if not failures.empty:
        raise CompletionCheckError(
            "质量检查未全部通过：\n" + failures.to_string(index=False)
        )
    print(
        f"完成：{len(raw)}条原始IC、{len(monthly)}条逐月IC、{len(summary)}个因子。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
