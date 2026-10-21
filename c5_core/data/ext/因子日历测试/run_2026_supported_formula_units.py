# -*- coding: utf-8 -*-
"""补算当前缓存可直接支持的指定行情区间历史公式单元。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

import historical_factor_data as factor_data
import run_historical_new_factor_backtests as historical_runner


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATUS = (
    BASE_DIR / "historical_calendar_backtest_results" / "formula_unit_status.csv"
)
DEFAULT_AUDIT = (
    BASE_DIR / "historical_calendar_backtest_results" / "formula_support_audit.csv"
)
DEFAULT_OUTPUT = (
    BASE_DIR
    / "historical_calendar_backtest_results"
    / "2026_extension_direct_supported_20260724"
    / "formula_units_raw"
)
DEFAULT_RECENT_MISSING = (
    BASE_DIR
    / "historical_calendar_backtest_results"
    / "2026_extension_direct_supported_20260724"
    / "missing_minute_recent_cache"
    / "*.parquet"
)
RECENT_ROOT = BASE_DIR / ".codex-artifact" / "2026_rank_check" / "cache"
RECENT_SOURCES = {
    "base_daily": RECENT_ROOT / "base" / "*.parquet",
    "april_daily": RECENT_ROOT / "april" / "*.parquet",
    "may_daily": RECENT_ROOT / "may" / "*.parquet",
    "june_daily": RECENT_ROOT / "june" / "*.parquet",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--qlib-dir",
        type=Path,
        default=BASE_DIR
        / ".codex-artifact"
        / "2026_rank_check"
        / "qlib_overlay",
    )
    parser.add_argument("--unit-ids", nargs="+")
    parser.add_argument("--include-rebuilt-missing", action="store_true")
    parser.add_argument(
        "--recent-missing-glob", type=Path, default=DEFAULT_RECENT_MISSING
    )
    parser.add_argument(
        "--recent-missing-lookback-glob",
        type=Path,
        help=(
            "可选的2025年1至2月回看重建缓存；提供后，2025年1至2月使用该缓存，"
            "2025年3月起继续使用 --recent-missing-glob。"
        ),
    )
    parser.add_argument("--duckdb-threads", type=int, default=3)
    parser.add_argument("--duckdb-memory-limit", default="5GB")
    parser.add_argument(
        "--duckdb-temp-dir",
        type=Path,
        default=Path(r"E:\codex_duckdb_temp\factor_calendar_2026_supported"),
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2026-05-29")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--report-start", default="2026-01-01")
    parser.add_argument("--report-end", default="2026-05-29")
    return parser.parse_args(argv)


def supported_unit_ids(
    status_path: Path, include_rebuilt_missing: bool = False
) -> list[str]:
    status = pd.read_csv(status_path, encoding="utf-8-sig")
    selected = status[
        status["status"].eq("completed")
        & status["calculation_method"].ne("existing_duckdb")
    ].copy()
    if not include_rebuilt_missing:
        rebuild_required = selected[
            selected["calculation_method"].eq("missing_minute")
            & selected["unit_id"].ne("HF0214__daily")
        ]
        selected = selected[~selected["unit_id"].isin(rebuild_required["unit_id"])]
    unit_ids = selected["unit_id"].astype(str).tolist()
    expected = 159 if include_rebuilt_missing else 148
    if len(unit_ids) != expected:
        raise RuntimeError(f"应有 {expected} 个目标公式单元，实际为 {len(unit_ids)}")
    return unit_ids


def install_recent_source_union(
    recent_missing_glob: Path | None = None,
    recent_missing_lookback_glob: Path | None = None,
) -> dict[str, str]:
    original_relation = factor_data._relation
    recent_sources = dict(RECENT_SOURCES)
    if recent_missing_glob is not None:
        recent_sources["missing_minute_daily"] = recent_missing_glob
    recent_by_old_path: dict[str, tuple[Path, str, Path | None]] = {}
    for source_name, recent_path in recent_sources.items():
        if not list(recent_path.parent.glob(recent_path.name)):
            raise FileNotFoundError(f"没有找到近期缓存：{recent_path}")
        old_path = factor_data.SOURCE_GLOBS[source_name]
        cutoff = "2025-01-01" if source_name == "missing_minute_daily" else "2026-01-01"
        lookback_path = (
            recent_missing_lookback_glob
            if source_name == "missing_minute_daily"
            else None
        )
        recent_by_old_path[str(old_path.resolve()).lower()] = (
            recent_path,
            cutoff,
            lookback_path,
        )

    if recent_missing_lookback_glob is not None:
        if recent_missing_glob is None:
            raise ValueError("使用回看重建缓存时必须同时提供近期缺失分钟缓存。")
        if not list(
            recent_missing_lookback_glob.parent.glob(
                recent_missing_lookback_glob.name
            )
        ):
            raise FileNotFoundError(
                f"没有找到2025年1至2月回看重建缓存：{recent_missing_lookback_glob}"
            )
        recent_sources["missing_minute_lookback_daily"] = (
            recent_missing_lookback_glob
        )

    def combined_relation(path: Path) -> str:
        old_relation = original_relation(path)
        recent_spec = recent_by_old_path.get(str(path.resolve()).lower())
        if recent_spec is None:
            return old_relation
        recent_path, cutoff, lookback_path = recent_spec
        recent_relation = original_relation(recent_path)
        if lookback_path is not None:
            lookback_relation = original_relation(lookback_path)
            return (
                "(SELECT * FROM "
                + old_relation
                + f" WHERE date < DATE '{cutoff}' UNION ALL SELECT * FROM "
                + lookback_relation
                + " WHERE date >= DATE '2025-01-01' AND date < DATE '2025-03-01' "
                + "UNION ALL SELECT * FROM "
                + recent_relation
                + " WHERE date >= DATE '2025-03-01')"
            )
        return (
            "(SELECT * FROM "
            + old_relation
            + f" WHERE date < DATE '{cutoff}' UNION ALL SELECT * FROM "
            + recent_relation
            + f" WHERE date >= DATE '{cutoff}')"
        )

    factor_data._relation = combined_relation
    return {name: str(path.resolve()) for name, path in recent_sources.items()}


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    all_supported = supported_unit_ids(args.status, args.include_rebuilt_missing)
    selected = all_supported if not args.unit_ids else list(dict.fromkeys(args.unit_ids))
    unknown = sorted(set(selected) - set(all_supported))
    if unknown:
        raise ValueError("这些单元不在可直接补算清单中：" + ", ".join(unknown))
    recent_sources = install_recent_source_union(
        args.recent_missing_glob if args.include_rebuilt_missing else None,
        args.recent_missing_lookback_glob
        if args.include_rebuilt_missing
        else None,
    )

    previous_argv = sys.argv
    sys.argv = [
        "run_historical_new_factor_backtests.py",
        "--audit",
        str(args.audit),
        "--output-dir",
        str(args.output_dir),
        "--qlib-dir",
        str(args.qlib_dir),
        "--start",
        args.start,
        "--backtest-start",
        args.backtest_start,
        "--end",
        args.end,
        "--split-date",
        args.split_date,
        "--report-start",
        args.report_start,
        "--report-end",
        args.report_end,
        "--duckdb-threads",
        str(args.duckdb_threads),
        "--duckdb-memory-limit",
        str(args.duckdb_memory_limit),
        "--duckdb-temp-dir",
        str(args.duckdb_temp_dir),
        "--unit-ids",
        *selected,
    ]
    try:
        result = historical_runner.main()
    finally:
        sys.argv = previous_argv

    methodology_path = args.output_dir / "methodology.json"
    if methodology_path.is_file():
        methodology = json.loads(methodology_path.read_text(encoding="utf-8"))
        methodology["purpose"] = (
            f"补算 {args.report_start} 至 {args.report_end} 当前缓存可直接支持的历史公式单元"
        )
        methodology["recent_sources"] = recent_sources
        methodology["excluded_rebuild_required_units"] = (
            0 if args.include_rebuilt_missing else 11
        )
        methodology_path.write_text(
            json.dumps(methodology, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
