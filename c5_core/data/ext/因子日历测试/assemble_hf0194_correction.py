# -*- coding: utf-8 -*-
"""合并 HF0194 修正后的四年逐期 IC，并检查月份完整性。"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
ROOT = (
    BASE_DIR
    / "historical_calendar_backtest_results"
    / "HF0194_formula_fix_20260724"
)
INPUTS = (
    ROOT / "historical_2022_2024" / "unit_rebalance_returns.parquet",
    ROOT / "ytd_2026" / "unit_rebalance_returns.parquet",
)
CALENDARS = (
    Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt"),
    BASE_DIR / ".codex-artifact" / "2026_rank_check" / "qlib_overlay" / "calendars" / "day.txt",
)
UNIT_ID = "HF0194__daily"
FACTOR_NAME = "绝对收益与调整后滞后成交量相关性"
CACHE_GLOB = (
    BASE_DIR
    / "minute_completion_runs"
    / "october_december_trial"
    / "daily_by_stock"
    / "*.parquet"
)
FIELD = "j11_d04_adj_absret_lag_amount_corr_daily_trial"


def expected_months() -> list[str]:
    historical = pd.period_range("2022-01", "2024-12", freq="M").astype(str).tolist()
    current = pd.period_range("2026-01", "2026-05", freq="M").astype(str).tolist()
    return historical + current


def cache_coverage() -> dict[str, object]:
    path = str(CACHE_GLOB.resolve()).replace("\\", "/")
    connection = duckdb.connect(":memory:")
    try:
        row = connection.execute(
            f"""
            SELECT
                min(date) AS first_date,
                max(date) AS last_date,
                count(DISTINCT date) AS date_count,
                count(DISTINCT lower(code)) AS stock_count,
                sum(CASE WHEN isfinite({FIELD}) THEN 1 ELSE 0 END) AS finite_values
            FROM read_parquet(?, union_by_name=true)
            """,
            [path],
        ).fetchone()
    finally:
        connection.close()
    result = {
        "file_count": len(list(CACHE_GLOB.parent.glob(CACHE_GLOB.name))),
        "first_date": str(pd.Timestamp(row[0]).date()),
        "last_date": str(pd.Timestamp(row[1]).date()),
        "date_count": int(row[2]),
        "stock_count": int(row[3]),
        "finite_values": int(row[4]),
    }
    if result["first_date"] > "2015-01-05" or result["last_date"] < "2026-05-29":
        raise RuntimeError(f"正确字段缓存范围不足：{result}")
    return result


def main() -> None:
    frames = []
    schedule_checks = []
    for path, calendar_path in zip(INPUTS, CALENDARS):
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = pd.read_parquet(path)
        if set(frame["unit_id"].astype(str)) != {UNIT_ID}:
            raise RuntimeError(f"{path} 含有 HF0194 以外的单元")
        frame = frame.sort_values("signal_date", kind="stable").reset_index(drop=True)
        calendar = pd.DatetimeIndex(
            pd.to_datetime(pd.read_csv(calendar_path, header=None)[0])
        )
        positions = {date: index for index, date in enumerate(calendar)}
        signal = [positions[pd.Timestamp(value)] for value in frame["signal_date"]]
        entry = [positions[pd.Timestamp(value)] for value in frame["entry_date"]]
        exit_position = [positions[pd.Timestamp(value)] for value in frame["exit_date"]]
        signal_steps = sorted(set(right - left for left, right in zip(signal, signal[1:])))
        entry_lags = sorted(set(right - left for left, right in zip(signal, entry)))
        holding_days = sorted(set(right - left for left, right in zip(entry, exit_position)))
        if signal_steps != [5] or entry_lags != [1] or holding_days != [5]:
            raise RuntimeError(
                f"取样或持有期错误：signal={signal_steps}, entry={entry_lags}, hold={holding_days}"
            )
        schedule_checks.append(
            {
                "source": str(path.relative_to(ROOT)),
                "signal_step_trading_days": signal_steps,
                "entry_lag_trading_days": entry_lags,
                "holding_trading_days": holding_days,
            }
        )
        frames.append(frame)

    raw = pd.concat(frames, ignore_index=True)
    for column in ("signal_date", "entry_date", "exit_date"):
        raw[column] = pd.to_datetime(raw[column])
    raw["return_month"] = raw["exit_date"].dt.to_period("M").astype(str)
    duplicate_columns = ["unit_id", "signal_date", "entry_date", "exit_date"]
    duplicate_rows = int(raw.duplicated(duplicate_columns).sum())
    finite_rows = int(np.isfinite(raw["ic"]).sum())
    actual_months = sorted(raw["return_month"].unique().tolist())

    if duplicate_rows:
        raise RuntimeError(f"发现 {duplicate_rows} 条重复逐期 IC")
    if finite_rows != len(raw):
        raise RuntimeError(f"IC 有限值应为 {len(raw)} 条，实际为 {finite_rows} 条")
    if actual_months != expected_months():
        raise RuntimeError(f"月份不完整：{actual_months}")
    if not raw["signal_frequency"].eq("daily").all():
        raise RuntimeError("发现非日频记录")

    output = pd.DataFrame(
        {
            "unit_id": UNIT_ID,
            "formula_key": "HF0194",
            "name": FACTOR_NAME,
            "frequency": "daily",
            "factor": "HF0194",
            "source_group": "hf0194_formula_fix_20260724",
            "signal_date": raw["signal_date"],
            "entry_date": raw["entry_date"],
            "exit_date": raw["exit_date"],
            "return_month": raw["return_month"],
            "ic": raw["ic"].astype(float),
            "stock_count": raw["eligible_stocks"].astype(int),
            "calculation_method": "cached_corrected_j11_d04",
        }
    ).sort_values(["signal_date", "exit_date"], kind="stable")

    monthly = (
        output.groupby("return_month", as_index=False)
        .agg(monthly_ic=("ic", "mean"), raw_observations=("ic", "size"))
        .sort_values("return_month", kind="stable")
    )
    monthly["year"] = monthly["return_month"].str[:4].astype(int)
    annual = (
        monthly.groupby("year", as_index=False)
        .agg(
            month_count=("monthly_ic", "size"),
            ic=("monthly_ic", "mean"),
            ic_std=("monthly_ic", lambda values: values.std(ddof=1)),
        )
        .sort_values("year", kind="stable")
    )
    annual["icir"] = annual["ic"] / annual["ic_std"]
    total_ic = float(monthly["monthly_ic"].mean())
    total_ic_std = float(monthly["monthly_ic"].std(ddof=1))
    total_icir = total_ic / total_ic_std

    output.to_csv(ROOT / "corrected_four_year_ic.csv", index=False, encoding="utf-8-sig")
    monthly.to_csv(ROOT / "corrected_monthly_ic.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(ROOT / "corrected_annual_ic_icir.csv", index=False, encoding="utf-8-sig")
    checks = {
        "unit_id": UNIT_ID,
        "field": FIELD,
        "formula": "corr(abs(ret_t), adjusted_amount_{t-1}) 的近10日均值",
        "cache_coverage": cache_coverage(),
        "frequency": "daily，每5个交易日取IC",
        "hold_days": 5,
        "schedule_checks": schedule_checks,
        "raw_ic_rows": int(len(output)),
        "finite_ic_rows": finite_rows,
        "duplicate_rows": duplicate_rows,
        "month_count": int(len(monthly)),
        "months_by_year": {
            str(year): int(count)
            for year, count in monthly.groupby("year")["return_month"].size().items()
        },
        "total_ic": total_ic,
        "total_icir": total_icir,
        "first_signal_date": str(output["signal_date"].min().date()),
        "last_exit_date": str(output["exit_date"].max().date()),
    }
    (ROOT / "correction_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
