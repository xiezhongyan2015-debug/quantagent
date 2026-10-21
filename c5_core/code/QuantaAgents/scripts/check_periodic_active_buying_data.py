from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINANCE_ROOT = PROJECT_ROOT.parents[1]
LEGACY_ROOT = FINANCE_ROOT / "因子日历测试"
DEFAULT_RAW_MINUTE_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data")
DEFAULT_MINUTE_EVENTS = (
    PROJECT_ROOT
    / "data_cache"
    / "periodic_active_buying"
    / "minute_events_2022_2025.parquet"
)
DEFAULT_LEVEL2_ROOT = Path(
    r"F:\2026(QQ群1097616051后续增量更新)\l2_a_share_parquet"
)
DEFAULT_LEVEL2_EVENTS = (
    LEGACY_ROOT
    / "outputs"
    / "periodic_active_buying_20260804"
    / "level2_candidate_features.parquet"
)
DEFAULT_MINUTE_CANDIDATES = (
    LEGACY_ROOT
    / "outputs"
    / "periodic_active_buying_20260804"
    / "minute_candidates.parquet"
)


def _env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    if isinstance(raw_value, str) and raw_value.strip():
        return Path(raw_value.strip()).expanduser()
    return default


def _parquet_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
    }
    if not path.is_file():
        return summary

    connection = duckdb.connect(":memory:")
    try:
        escaped = path.resolve().as_posix().replace("'", "''")
        columns = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{escaped}')"
        ).fetchall()
        column_names = [str(row[0]) for row in columns]
        summary["size_mb"] = round(path.stat().st_size / 1024 / 1024, 2)
        summary["column_count"] = len(column_names)
        summary["columns_preview"] = column_names[:40]
        summary["rows"] = int(
            connection.execute(
                f"SELECT count(*) FROM read_parquet('{escaped}')"
            ).fetchone()[0]
        )
        date_column = next(
            (
                name
                for name in ("date", "trade_date", "trigger_ts", "datetime")
                if name in column_names
            ),
            None,
        )
        if date_column is not None:
            date_range = connection.execute(
                f"SELECT min({date_column}), max({date_column}) "
                f"FROM read_parquet('{escaped}')"
            ).fetchone()
            summary["date_start"] = str(date_range[0])
            summary["date_end"] = str(date_range[1])
    finally:
        connection.close()
    return summary


def _raw_minute_summary(path: Path) -> dict[str, Any]:
    sample = path / "sh600000.parquet"
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_dir(),
        "sample": str(sample),
        "sample_exists": sample.is_file(),
    }
    if not path.is_dir() or not sample.is_file():
        return summary
    summary["file_count"] = sum(1 for _ in path.glob("*.parquet"))
    sample_summary = _parquet_summary(sample)
    summary["sample_rows"] = sample_summary.get("rows")
    summary["sample_date_start"] = sample_summary.get("date_start")
    summary["sample_date_end"] = sample_summary.get("date_end")
    summary["sample_column_count"] = sample_summary.get("column_count", 0)
    summary["sample_columns_preview"] = sample_summary.get("columns_preview", [])
    return summary


def _level2_summary(root: Path) -> dict[str, Any]:
    catalog = root / "l2_catalog.duckdb"
    summary: dict[str, Any] = {
        "path": str(root),
        "exists": root.is_dir(),
        "catalog": str(catalog),
        "catalog_exists": catalog.is_file(),
    }
    run_summary = root / "latest_run_summary.json"
    if run_summary.is_file():
        try:
            summary["latest_run"] = json.loads(run_summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary["latest_run"] = "无法读取"
    if catalog.is_file():
        connection = duckdb.connect(str(catalog), read_only=True)
        try:
            summary["tables"] = [
                str(row[0])
                for row in connection.execute("SHOW TABLES").fetchall()
            ]
        finally:
            connection.close()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查分钟量能策略所需的本机数据")
    parser.add_argument("--profile", choices=("all", "minute", "level2"), default="all")
    parser.add_argument(
        "--raw-minute-dir",
        type=Path,
        default=_env_path("QUANTA_RAW_MINUTE_DATA_DIR", DEFAULT_RAW_MINUTE_DIR),
    )
    parser.add_argument(
        "--minute-events",
        type=Path,
        default=_env_path("QUANTA_MINUTE_EVENT_FEATURES_PATH", DEFAULT_MINUTE_EVENTS),
    )
    parser.add_argument(
        "--level2-root",
        type=Path,
        default=_env_path("QUANTA_LEVEL2_ROOT", DEFAULT_LEVEL2_ROOT),
    )
    parser.add_argument(
        "--level2-events",
        type=Path,
        default=_env_path("QUANTA_LEVEL2_EVENT_FEATURES_PATH", DEFAULT_LEVEL2_EVENTS),
    )
    parser.add_argument(
        "--minute-candidates",
        type=Path,
        default=_env_path("QUANTA_MINUTE_CANDIDATES_PATH", DEFAULT_MINUTE_CANDIDATES),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {"profile": args.profile}
    errors: list[str] = []

    if args.profile in {"all", "minute"}:
        report["raw_minute"] = _raw_minute_summary(args.raw_minute_dir)
        report["minute_events"] = _parquet_summary(args.minute_events)
        if not report["raw_minute"].get("sample_exists"):
            errors.append("多年1分钟资料缺少 sh600000.parquet 样例")
        if not report["minute_events"].get("exists"):
            errors.append("多年分钟候选文件尚未生成")

    if args.profile in {"all", "level2"}:
        report["level2"] = _level2_summary(args.level2_root)
        report["level2_events"] = _parquet_summary(args.level2_events)
        report["minute_candidates_2026"] = _parquet_summary(args.minute_candidates)
        if not report["level2"].get("catalog_exists"):
            errors.append("Level 2 资料目录缺少 l2_catalog.duckdb")
        if not report["level2_events"].get("exists"):
            errors.append("Level 2 候选特征文件不存在")

    report["ok"] = not errors
    report["errors"] = errors
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
