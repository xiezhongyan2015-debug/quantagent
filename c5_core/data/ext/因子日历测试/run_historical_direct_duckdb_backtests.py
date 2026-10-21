# -*- coding: utf-8 -*-
"""按组启动五个现有 DuckDB 回测入口，并把结果整理成统一格式。

默认只打印将要执行的命令。只有显式传入 ``--execute`` 才会启动回测；
``--normalize-only`` 只读取已有结果，不启动回测。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

import historical_backtest_core as core
import historical_existing_duckdb_factors as existing


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_UNITS = BASE_DIR / "historical_calendar_backtest_results" / "formula_units.csv"
DEFAULT_RAW_ROOT = (
    BASE_DIR / "historical_calendar_backtest_results" / "runs" / "existing_direct_raw"
)
DEFAULT_OUTPUT = (
    BASE_DIR / "historical_calendar_backtest_results" / "runs" / "existing_direct"
)

DIRECT_GROUPS = (
    "early_four",
    "early_trial",
    "july_august",
    "september_december",
    "october_december_trial",
)
RAW_GROUP_DIR_ALIASES = {"early_trial": "early9_trial"}
FALLBACK_RAW_DIRS = {
    "july_august": BASE_DIR / "july_august_factor_results" / "final",
    "september_december": BASE_DIR / "september_december_factor_results" / "final",
}
GLOBAL_FALLBACK_RAW_DIR = BASE_DIR / "minute_completion_results" / "final"
NATIVE_CODE_FILTER_GROUPS = frozenset({"july_august", "september_december"})

STATUS_COLUMNS = [
    "unit_id",
    "formula_key",
    "name",
    "frequency",
    "calculation_method",
    "registered_name",
    "status",
    "finite_values",
    "periods_2022_2024",
    "error",
]
SUMMARY_COLUMNS = [
    "unit_id",
    "periods",
    "cumulative_return",
    "annual_return",
    "benchmark_annual_return",
    "annual_excess_return",
    "annual_volatility",
    "sharpe",
    "max_drawdown",
    "excess_ir",
    "win_rate_vs_benchmark",
    "mean_ic",
    "training_ic",
    "direction",
    "direction_source",
    "avg_turnover",
    "avg_group_stocks",
]
YEARLY_COLUMNS = [
    "unit_id",
    "year",
    "strategy_return",
    "benchmark_return",
    "excess_return",
    "periods",
    "mean_ic",
]


@dataclass(frozen=True)
class CoverageRow:
    group_name: str
    module_name: str
    expected_codes: tuple[str, ...]
    supported_codes: tuple[str, ...]
    missing_codes: tuple[str, ...]
    extra_codes: tuple[str, ...]
    accepts_factor_codes: bool


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--execute",
        action="store_true",
        help="实际逐组启动回测，完成后整理结果。",
    )
    action.add_argument(
        "--normalize-only",
        action="store_true",
        help="只整理已有结果，不启动回测。",
    )
    parser.add_argument("--groups", nargs="+", choices=DIRECT_GROUPS)
    parser.add_argument("--factor-codes", nargs="+")
    parser.add_argument("--units", type=Path, default=DEFAULT_UNITS)
    parser.add_argument("--raw-output-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--database",
        type=Path,
        default=BASE_DIR / "duckdb_factor_store" / "factor_store.duckdb",
    )
    parser.add_argument(
        "--duckdb-temp-dir",
        type=Path,
        default=Path(r"E:\codex_duckdb_temp\factor_calendar_existing_direct"),
    )
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--duckdb-memory-limit", default="3GB")
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--report-start", default="2022-01-01")
    parser.add_argument("--report-end", default="2024-12-31")
    parser.add_argument("--min-stocks", type=int, default=50)
    parser.add_argument(
        "--allow-missing-network",
        action="store_true",
        help="仅传给十至十二月试算入口。",
    )
    return parser.parse_args(argv)


def selected_groups(groups: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(groups) if groups else DIRECT_GROUPS


def read_direct_units(
    units_path: Path,
    groups: Sequence[str] | None = None,
    factor_codes: Sequence[str] | None = None,
) -> pd.DataFrame:
    units = pd.read_csv(units_path, encoding="utf-8-sig")
    units["result_code"] = units["existing_ref"].fillna("").map(
        lambda value: existing.resolve_result_code(str(value)) if value else ""
    )
    units["group_name"] = units["result_code"].map(existing.CODE_TO_GROUP)
    units = units[units["group_name"].isin(selected_groups(groups))].copy()
    if factor_codes:
        requested = list(dict.fromkeys(str(code) for code in factor_codes))
        unknown = sorted(set(requested) - set(units["result_code"]))
        if unknown:
            raise ValueError(f"所选分组不包含这些代码：{', '.join(unknown)}")
        units = units[units["result_code"].isin(requested)].copy()
    if units.empty:
        raise ValueError("没有符合条件的直接 DuckDB 回测单元")
    return units


def inspect_coverage(groups: Sequence[str] | None = None) -> list[CoverageRow]:
    rows: list[CoverageRow] = []
    for group_name in selected_groups(groups):
        spec = existing.GROUPS[group_name]
        module = importlib.import_module(spec.module_name)
        supported = tuple(sorted(str(code) for code in module.FACTOR_META))
        expected = tuple(sorted(spec.result_codes))
        rows.append(
            CoverageRow(
                group_name=group_name,
                module_name=spec.module_name,
                expected_codes=expected,
                supported_codes=supported,
                missing_codes=tuple(sorted(set(expected) - set(supported))),
                extra_codes=tuple(sorted(set(supported) - set(expected))),
                accepts_factor_codes=group_name in NATIVE_CODE_FILTER_GROUPS,
            )
        )
    return rows


def build_group_command(
    group_name: str,
    codes: Sequence[str],
    args: argparse.Namespace,
) -> list[str]:
    spec = existing.GROUPS[group_name]
    output_dir = Path(args.raw_output_root) / group_name
    command = [
        str(args.python),
        str(BASE_DIR / f"{spec.module_name}.py"),
        "--database",
        str(args.database),
        "--duckdb-temp-dir",
        str(Path(args.duckdb_temp_dir) / group_name),
        "--duckdb-threads",
        str(args.duckdb_threads),
        "--duckdb-memory-limit",
        str(args.duckdb_memory_limit),
        "--output-dir",
        str(output_dir),
        "--qlib-dir",
        str(args.qlib_dir),
        "--start",
        str(args.start),
        "--backtest-start",
        str(args.backtest_start),
        "--end",
        str(args.end),
        "--split-date",
        str(args.split_date),
    ]
    if group_name in {"early_trial", "october_december_trial"}:
        command.extend(["--min-stocks", str(args.min_stocks)])
    if group_name == "october_december_trial" and args.allow_missing_network:
        command.append("--allow-missing-network")
    if group_name == "july_august":
        command.extend(
            [
                "--july-august-cache-glob",
                str(BASE_DIR / "minute_completion_runs" / "july_august" / "daily_by_stock" / "*.parquet"),
                "--lone-goose-cache-glob",
                str(BASE_DIR / "minute_completion_runs" / "july_august" / "lone_goose_by_year" / "*.parquet"),
            ]
        )
    if group_name in NATIVE_CODE_FILTER_GROUPS:
        command.extend(["--factor-codes", *sorted(set(codes))])
    return command


def build_commands(
    units: pd.DataFrame, args: argparse.Namespace
) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    for group_name in selected_groups(args.groups):
        group_units = units[units["group_name"] == group_name]
        if group_units.empty:
            continue
        commands[group_name] = build_group_command(
            group_name,
            group_units["result_code"].astype(str).tolist(),
            args,
        )
    return commands


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _finite_values(status: pd.DataFrame, code: str) -> int:
    if status.empty or "factor" not in status or "finite_values" not in status:
        return 0
    matched = status.loc[status["factor"].astype(str) == code, "finite_values"]
    if matched.empty or pd.isna(matched.iloc[0]):
        return 0
    return int(matched.iloc[0])


def _normalise_factor_frame(
    code: str,
    unit_id: str,
    frequency: str,
    raw_dir: Path,
    report_start: str,
    report_end: str,
) -> tuple[pd.DataFrame, str]:
    returns = _read_csv(raw_dir / "original_frequency_rebalance_returns.csv")
    if returns.empty or "factor" not in returns:
        return pd.DataFrame(), "缺少 original_frequency_rebalance_returns.csv"
    frame = returns[returns["factor"].astype(str) == code].copy()
    if frame.empty:
        return frame, "现有结果没有该代码"
    raw_frequencies = set(frame["signal_frequency"].dropna().astype(str))
    if raw_frequencies and raw_frequencies != {frequency}:
        return pd.DataFrame(), (
            f"结果频率{sorted(raw_frequencies)}与计算单元频率{frequency}不一致"
        )

    for column in ("signal_date", "entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column])
    ic = _read_csv(raw_dir / "original_frequency_ic.csv")
    if not ic.empty and {"factor", "signal_date", "ic"}.issubset(ic.columns):
        ic = ic[ic["factor"].astype(str) == code].copy()
        ic["signal_date"] = pd.to_datetime(ic["signal_date"])
        frame = frame.merge(
            ic[["signal_date", "ic"]].drop_duplicates("signal_date"),
            on="signal_date",
            how="left",
        )
    elif "ic" not in frame:
        frame["ic"] = np.nan

    summary = _read_csv(raw_dir / "original_frequency_backtest_summary.csv")
    training_ic = np.nan
    if not summary.empty and {"factor", "in_sample_ic"}.issubset(summary.columns):
        matched = summary[summary["factor"].astype(str) == code]
        if "period" in matched:
            full = matched[matched["period"].astype(str) == "full"]
            if not full.empty:
                matched = full
        if not matched.empty:
            training_ic = float(matched["in_sample_ic"].iloc[0])
    if not np.isfinite(training_ic):
        training = frame.loc[frame["exit_date"] <= pd.Timestamp("2020-12-31"), "ic"]
        training_ic = float(training.mean()) if not training.dropna().empty else np.nan

    frame = frame[
        (frame["entry_date"] >= pd.Timestamp(report_start))
        & (frame["exit_date"] <= pd.Timestamp(report_end))
    ].copy()
    if frame.empty:
        return frame, "报告区间内没有回测期"
    frame["unit_id"] = unit_id
    frame["training_ic"] = training_ic
    if "selected_direction" in frame:
        frame["direction"] = frame["selected_direction"].astype(str)
    else:
        frame["direction"] = "low" if training_ic < 0 else "high"
    frame["direction_source"] = "existing_program_in_sample_ic"
    if "selected_turnover" not in frame:
        frame["selected_turnover"] = np.nan
    if "group_stocks" not in frame:
        frame["group_stocks"] = np.nan
    return frame, ""


def normalize_results(
    units: pd.DataFrame,
    raw_root: Path,
    output_dir: Path,
    report_start: str = "2022-01-01",
    report_end: str = "2024-12-31",
    coverage: Sequence[CoverageRow] | None = None,
) -> dict[str, int]:
    status_rows: list[dict[str, object]] = []
    summary_frames: list[pd.DataFrame] = []
    yearly_frames: list[pd.DataFrame] = []
    rebalance_frames: list[pd.DataFrame] = []

    for row in units.itertuples(index=False):
        raw_dir = Path(raw_root) / str(row.group_name)
        if not raw_dir.exists() and str(row.group_name) in RAW_GROUP_DIR_ALIASES:
            raw_dir = Path(raw_root) / RAW_GROUP_DIR_ALIASES[str(row.group_name)]
        raw_status = _read_csv(raw_dir / "factor_status.csv")
        frame, error = _normalise_factor_frame(
            str(row.result_code),
            str(row.unit_id),
            str(row.normalized_frequency),
            raw_dir,
            report_start,
            report_end,
        )
        fallback_dir = FALLBACK_RAW_DIRS.get(str(row.group_name))
        if frame.empty and fallback_dir is not None and fallback_dir.exists():
            fallback_frame, fallback_error = _normalise_factor_frame(
                str(row.result_code),
                str(row.unit_id),
                str(row.normalized_frequency),
                fallback_dir,
                report_start,
                report_end,
            )
            if not fallback_frame.empty:
                raw_dir = fallback_dir
                raw_status = _read_csv(raw_dir / "factor_status.csv")
                frame, error = fallback_frame, fallback_error
        if frame.empty and GLOBAL_FALLBACK_RAW_DIR.exists():
            fallback_frame, fallback_error = _normalise_factor_frame(
                str(row.result_code),
                str(row.unit_id),
                str(row.normalized_frequency),
                GLOBAL_FALLBACK_RAW_DIR,
                report_start,
                report_end,
            )
            if not fallback_frame.empty:
                raw_dir = GLOBAL_FALLBACK_RAW_DIR
                raw_status = _read_csv(raw_dir / "factor_status.csv")
                frame, error = fallback_frame, fallback_error
        completed = not frame.empty
        status_rows.append(
            {
                "unit_id": row.unit_id,
                "formula_key": row.formula_key,
                "name": row.representative_name,
                "frequency": row.normalized_frequency,
                "calculation_method": "existing_duckdb_direct",
                "registered_name": row.result_code,
                "status": "completed" if completed else "failed",
                "finite_values": _finite_values(raw_status, str(row.result_code)),
                "periods_2022_2024": len(frame),
                "error": error,
            }
        )
        if not completed:
            continue
        summary, yearly = core.summarize_unit(frame)
        summary_frames.append(summary)
        yearly_frames.append(yearly)
        frame["formula_key"] = row.formula_key
        frame["calculation_method"] = "existing_duckdb_direct"
        rebalance_frames.append(frame)

    status = pd.DataFrame(status_rows, columns=STATUS_COLUMNS)
    summary = (
        pd.concat(summary_frames, ignore_index=True)
        if summary_frames
        else pd.DataFrame(columns=SUMMARY_COLUMNS)
    )
    yearly = (
        pd.concat(yearly_frames, ignore_index=True)
        if yearly_frames
        else pd.DataFrame(columns=YEARLY_COLUMNS)
    )
    rebalance = (
        pd.concat(rebalance_frames, ignore_index=True)
        if rebalance_frames
        else pd.DataFrame()
    )
    _atomic_csv(status, output_dir / "unit_status.csv")
    _atomic_csv(summary, output_dir / "unit_backtest_summary.csv")
    _atomic_csv(yearly, output_dir / "unit_yearly_returns.csv")
    _atomic_parquet(rebalance, output_dir / "unit_rebalance_returns.parquet")

    coverage = list(coverage or inspect_coverage(sorted(units["group_name"].unique())))
    report = {
        "groups": sorted(units["group_name"].unique()),
        "selected_units": len(units),
        "completed_units": int((status["status"] == "completed").sum()),
        "failed_units": int((status["status"] != "completed").sum()),
        "report_start": report_start,
        "report_end": report_end,
        "coverage": [row.__dict__ for row in coverage],
        "unresolved_mapping_refs": existing.unresolved_mapping_refs(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "methodology.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output_dir / "methodology.json")
    return {
        "selected": len(status),
        "completed": report["completed_units"],
        "failed": report["failed_units"],
    }


def _print_coverage(rows: Iterable[CoverageRow]) -> None:
    for row in rows:
        selector = "支持按代码筛选" if row.accepts_factor_codes else "只能整组运行"
        print(
            f"[{row.group_name}] 目标{len(row.expected_codes)}个，"
            f"程序可算{len(row.supported_codes)}个，缺少{len(row.missing_codes)}个；{selector}"
        )
        if row.missing_codes:
            print("  缺少：" + ", ".join(row.missing_codes))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    groups = selected_groups(args.groups)
    coverage = inspect_coverage(groups)
    _print_coverage(coverage)
    units = read_direct_units(args.units, groups, args.factor_codes)
    commands = build_commands(units, args)

    if not args.normalize_only:
        print("\n将要执行的命令：")
        for group_name, command in commands.items():
            print(f"[{group_name}] {subprocess.list2cmdline(command)}")
    if not args.execute and not args.normalize_only:
        print("\n当前仅显示命令；加 --execute 才会启动回测。")
        return 0

    return_codes: dict[str, int] = {}
    if args.execute:
        for group_name, command in commands.items():
            print(f"\n[{group_name}] 开始运行", flush=True)
            completed = subprocess.run(command, cwd=BASE_DIR, check=False)
            return_codes[group_name] = int(completed.returncode)
            print(f"[{group_name}] 返回值 {completed.returncode}", flush=True)

    counts = normalize_results(
        units,
        args.raw_output_root,
        args.output_dir,
        args.report_start,
        args.report_end,
        coverage,
    )
    print(
        f"整理完成：{counts['completed']}/{counts['selected']}个单元有结果，"
        f"{counts['failed']}个没有结果。"
    )
    if return_codes and any(code != 0 for code in return_codes.values()):
        return 2
    return 0 if counts["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
