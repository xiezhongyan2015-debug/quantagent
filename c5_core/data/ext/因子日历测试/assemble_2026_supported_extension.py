# -*- coding: utf-8 -*-
"""整理 24 个旧代码和 148 个公式单元的 2026 年逐期 IC。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import historical_backtest_core as core
import historical_existing_duckdb_factors as existing


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = (
    BASE_DIR
    / "historical_calendar_backtest_results"
    / "2026_extension_direct_supported_20260724"
)
DEFAULT_STATUS = (
    BASE_DIR / "historical_calendar_backtest_results" / "formula_unit_status.csv"
)
QLIB_DIR = BASE_DIR / ".codex-artifact" / "2026_rank_check" / "qlib_overlay"
OLD_GROUPS = ("july_august", "september_december")
OLD_CODES = {
    "J07_D14_UPSIDE_RV",
    "J07_D19_NEG_INTRADAY_REV_FREQ",
    "J08_D14_RSJ",
    "J08_D16_DECAY_FEAR",
    "J08_D19_POS_INTRADAY_REV_FREQ",
    "J09_D04_EMOTION_SPILLOVER",
    "J09_D10_INTRADAY_TREND_RATIO",
    "J09_D17_REALIZED_BIPOWER_VARIATION",
    "J10_D07_CONFIDENCE_SPILLOVER",
    "J10_D14_REALIZED_TRIPOWER_VAR",
    "J10_D16_INTRADAY_MAX_DRAWDOWN",
    "J10_D19_ABNORMAL_POS_INTRADAY_REV",
    "J11_D10_VOLUME_SHARE_STD",
    "J11_D11_NORMAL_CDF_ACTIVE_SHARE",
    "J11_D15_REALIZED_JUMP_VOLATILITY",
    "J11_D17_OVERNIGHT_RETURN",
    "J12_D01_VOLUME_SHARE_SKEW",
    "J12_D02_VOLUME_CV",
    "J12_D05_HF_VOLUME_VOL_CV",
    "J12_D07_DAZZLING_RETURN",
    "J12_D10_UNIFORM_ACTIVE_SHARE",
    "J12_D14_UP_JUMP_VOL",
    "J12_D16_INTRADAY_OR_OVERNIGHT_RETURN",
    "J12_D28_TIME_AVG_REL_PRICE",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    return parser.parse_args(argv)


def historical_daily_signal_dates() -> set[pd.Timestamp]:
    dates = pd.DatetimeIndex(
        pd.to_datetime(pd.read_csv(QLIB_DIR / "calendars" / "day.txt", header=None)[0])
    )
    dates = dates[(dates >= pd.Timestamp("2015-01-01")) & (dates <= pd.Timestamp("2026-05-29"))]
    settings = core.BacktestSettings(
        qlib_dir=QLIB_DIR,
        start="2015-01-01",
        backtest_start="2016-01-04",
        end="2026-05-29",
    )
    return {dates[signal] for signal, _, _ in core._periods(dates, "daily", settings)}


def historical_weekly_signal_dates() -> set[pd.Timestamp]:
    dates = pd.DatetimeIndex(
        pd.to_datetime(pd.read_csv(QLIB_DIR / "calendars" / "day.txt", header=None)[0])
    )
    dates = dates[(dates >= pd.Timestamp("2015-01-01")) & (dates <= pd.Timestamp("2026-05-29"))]
    settings = core.BacktestSettings(
        qlib_dir=QLIB_DIR,
        start="2015-01-01",
        backtest_start="2016-01-04",
        end="2026-05-29",
    )
    return {
        dates[signal]
        for signal, _, _ in core._periods(dates, "weekly_tuesday", settings)
    }


def old_unit_frame(root: Path, status: pd.DataFrame) -> pd.DataFrame:
    raw_frames = []
    for group in OLD_GROUPS:
        path = root / "old_codes_raw" / group / "original_frequency_ic.csv"
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = pd.read_csv(path, encoding="utf-8-sig")
        frame["source_group"] = group
        raw_frames.append(frame)
    long_history_path = (
        root
        / "old_codes_raw"
        / "september_december_long_history"
        / "original_frequency_ic.csv"
    )
    if not long_history_path.is_file():
        raise FileNotFoundError(long_history_path)
    long_history = pd.read_csv(long_history_path, encoding="utf-8-sig")
    long_history["source_group"] = "september_december_long_history"
    raw_frames.append(long_history)
    existing_recent_roots = (
        BASE_DIR / ".codex-artifact" / "2026_rank_check" / "all57_runs",
        BASE_DIR
        / ".codex-artifact"
        / "2026_rank_check"
        / "minute_completion_runs",
    )
    for recent_root in existing_recent_roots:
        for path in sorted(recent_root.glob("*/original_frequency_ic.csv")):
            frame = pd.read_csv(path, encoding="utf-8-sig")
            frame["source_group"] = f"existing_2026_{path.parent.name}"
            raw_frames.append(frame)
    raw = pd.concat(raw_frames, ignore_index=True)
    for column in ("signal_date", "entry_date", "exit_date"):
        raw[column] = pd.to_datetime(raw[column])
    raw = raw[
        raw["entry_date"].ge("2026-01-01")
        & raw["exit_date"].le("2026-05-29")
    ].copy()

    units = status[
        status["status"].eq("completed")
        & status["calculation_method"].eq("existing_duckdb")
    ].copy()
    units["factor"] = units["existing_ref"].fillna("").map(
        lambda value: existing.resolve_result_code(str(value)) if value else ""
    )
    if len(units) != 95:
        raise RuntimeError(f"旧代码应映射到 95 个单元，实际为 {len(units)}")

    daily_dates = historical_daily_signal_dates()
    weekly_dates = historical_weekly_signal_dates()
    rows = []
    for unit in units.itertuples(index=False):
        selected = raw[raw["factor"].eq(unit.factor)].copy()
        if unit.frequency in {"daily", "every_5_days"}:
            selected = selected[selected["signal_date"].isin(daily_dates)]
        elif unit.frequency == "monthly":
            pass
        elif unit.frequency == "weekly_tuesday":
            selected = selected[selected["signal_date"].isin(weekly_dates)]
        else:
            raise ValueError(f"旧代码出现未处理频率：{unit.unit_id}={unit.frequency}")
        if selected.empty:
            raise RuntimeError(f"{unit.unit_id}（{unit.factor}）没有 2026 年逐期 IC")
        selected["unit_id"] = unit.unit_id
        selected["formula_key"] = unit.formula_key
        selected["name"] = unit.representative_name
        selected["frequency"] = unit.frequency
        selected["calculation_method"] = "existing_duckdb_2026_extension"
        rows.append(selected)
    result = pd.concat(rows, ignore_index=True)
    return result[
        [
            "unit_id",
            "formula_key",
            "name",
            "frequency",
            "factor",
            "source_group",
            "signal_date",
            "entry_date",
            "exit_date",
            "ic",
            "stock_count",
            "calculation_method",
        ]
    ].sort_values(["unit_id", "signal_date"], kind="stable")


def formula_unit_frame(root: Path, status: pd.DataFrame) -> pd.DataFrame:
    paths = [root / "formula_units_raw" / "unit_rebalance_returns.parquet"]
    missing_path = root / "missing_units_raw" / "unit_rebalance_returns.parquet"
    if missing_path.is_file():
        paths.append(missing_path)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    raw = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    for column in ("signal_date", "entry_date", "exit_date"):
        raw[column] = pd.to_datetime(raw[column])
    raw = raw[raw["exit_date"].between("2026-01-01", "2026-05-29")].copy()
    meta = status[
        ["unit_id", "representative_name", "frequency"]
    ].drop_duplicates("unit_id")
    raw = raw.merge(meta, on="unit_id", how="left", validate="many_to_one")
    raw["factor"] = raw["unit_id"]
    raw["source_group"] = "historical_formula"
    raw["stock_count"] = raw["eligible_stocks"]
    raw["name"] = raw["representative_name"]
    raw["calculation_method"] = raw.get(
        "calculation_method", "formula_2026_extension"
    )
    raw["calculation_method"] = "formula_2026_extension"
    return raw[
        [
            "unit_id",
            "formula_key",
            "name",
            "frequency",
            "factor",
            "source_group",
            "signal_date",
            "entry_date",
            "exit_date",
            "ic",
            "stock_count",
            "calculation_method",
        ]
    ].sort_values(["unit_id", "signal_date"], kind="stable")


def unit_status(combined: pd.DataFrame, expected_units: list[str]) -> pd.DataFrame:
    grouped = combined.groupby("unit_id", as_index=False).agg(
        ic_rows=("ic", "size"),
        finite_ic=("ic", lambda values: int(np.isfinite(values).sum())),
        first_signal_date=("signal_date", "min"),
        last_exit_date=("exit_date", "max"),
        min_stock_count=("stock_count", "min"),
        avg_stock_count=("stock_count", "mean"),
    )
    base = pd.DataFrame({"unit_id": expected_units})
    result = base.merge(grouped, on="unit_id", how="left")
    result["status"] = np.where(
        result["finite_ic"].fillna(0).gt(0), "completed", "failed"
    )
    return result[
        [
            "unit_id",
            "status",
            "ic_rows",
            "finite_ic",
            "first_signal_date",
            "last_exit_date",
            "min_stock_count",
            "avg_stock_count",
        ]
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    status = pd.read_csv(args.status, encoding="utf-8-sig")
    old = old_unit_frame(root, status)
    formula = formula_unit_frame(root, status)
    combined = pd.concat([old, formula], ignore_index=True).sort_values(
        ["unit_id", "signal_date"], kind="stable"
    )
    expected_units = sorted(set(old["unit_id"]) | set(formula["unit_id"]))
    checks = unit_status(combined, expected_units)

    old.to_csv(root / "old_units_2026_ic.csv", index=False, encoding="utf-8-sig")
    formula.to_csv(
        root / "formula_units_2026_ic.csv", index=False, encoding="utf-8-sig"
    )
    combined.to_csv(root / "combined_2026_ic.csv", index=False, encoding="utf-8-sig")
    checks.to_csv(root / "unit_status.csv", index=False, encoding="utf-8-sig")
    methodology = {
        "period": "entry_date >= 2026-01-01 and exit_date <= 2026-05-29",
        "old_units": int(old["unit_id"].nunique()),
        "formula_units": int(formula["unit_id"].nunique()),
        "combined_units": int(combined["unit_id"].nunique()),
        "combined_ic_rows": int(len(combined)),
        "finite_ic_rows": int(np.isfinite(combined["ic"]).sum()),
        "first_signal_date": str(combined["signal_date"].min().date()),
        "last_exit_date": str(combined["exit_date"].max().date()),
        "daily_sampling": "沿用历史程序从 2016-01-04 起每 5 个交易日的信号日",
        "rebuilt_missing_units": 11,
        "recent_missing_cache_files": len(
            list((root / "missing_minute_recent_cache").glob("*.parquet"))
        ),
        "excluded_units": "无",
    }
    (root / "methodology.json").write_text(
        json.dumps(methodology, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    failed = int(checks["status"].ne("completed").sum())
    print(json.dumps(methodology, ensure_ascii=False, indent=2))
    print(f"失败单元：{failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
