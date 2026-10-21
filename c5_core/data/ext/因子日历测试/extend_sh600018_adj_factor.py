#!/usr/bin/env python3
"""用本地日线向前补齐 sh600018 的价格连续复权系数。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-file", required=True, type=Path)
    parser.add_argument("--factor-file", required=True, type=Path)
    parser.add_argument("--report-file", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    daily = pd.read_csv(
        args.daily_file,
        skiprows=1,
        usecols=["交易日期", "收盘价", "前收盘价"],
        encoding="gb18030",
        low_memory=False,
    )
    daily["交易日期"] = pd.to_datetime(daily["交易日期"], errors="raise")
    daily["收盘价"] = pd.to_numeric(daily["收盘价"], errors="raise")
    daily["前收盘价"] = pd.to_numeric(daily["前收盘价"], errors="raise")
    daily = daily.sort_values("交易日期", kind="mergesort").reset_index(drop=True)
    if daily["交易日期"].duplicated().any():
        raise ValueError("日线存在重复日期")
    if (daily[["收盘价", "前收盘价"]] <= 0).any().any():
        raise ValueError("日线收盘价或前收盘价不是正数")

    with np.load(args.factor_file) as source:
        official_dates = source["dates"].astype(np.int32, copy=True)
        official_factors = source["factors"].astype(np.float64, copy=True)
    if not len(official_dates) or np.any(np.diff(official_dates) <= 0):
        raise ValueError("原复权系数为空或日期顺序有误")
    if not np.all(np.isfinite(official_factors)) or np.any(official_factors <= 0):
        raise ValueError("原复权系数含无效值")

    daily_dates = (
        daily["交易日期"].dt.year * 10000
        + daily["交易日期"].dt.month * 100
        + daily["交易日期"].dt.day
    ).to_numpy(dtype=np.int32)
    first_official = int(official_dates[0])
    positions = np.flatnonzero(daily_dates == first_official)
    if len(positions) != 1:
        raise ValueError(f"日线中找不到唯一的首个官方系数日期：{first_official}")

    # A(前一日) = A(当日) × 当日前收盘价 ÷ 前一日收盘价。
    first_position = int(positions[0])
    derived_dates: list[int] = []
    derived_factors: list[float] = []
    current_factor = float(official_factors[0])
    for index in range(first_position, 0, -1):
        previous_close = float(daily.at[index - 1, "收盘价"])
        current_preclose = float(daily.at[index, "前收盘价"])
        current_factor = current_factor * current_preclose / previous_close
        if not np.isfinite(current_factor) or current_factor <= 0:
            raise ValueError(f"倒推到 {int(daily_dates[index - 1])} 时得到无效系数")
        derived_dates.append(int(daily_dates[index - 1]))
        derived_factors.append(current_factor)

    derived_dates_array = np.asarray(derived_dates[::-1], dtype=np.int32)
    derived_factors_array = np.asarray(derived_factors[::-1], dtype=np.float64)
    if len(derived_dates_array) and int(derived_dates_array[-1]) >= first_official:
        raise ValueError("补出的日期与原系数重叠")
    dates = np.concatenate([derived_dates_array, official_dates])
    factors = np.concatenate([derived_factors_array, official_factors])
    if np.any(np.diff(dates) <= 0):
        raise ValueError("合并后的系数日期没有严格递增")

    backup = args.factor_file.with_name(args.factor_file.stem + ".tushare_original.npz")
    if not backup.exists():
        shutil.copy2(args.factor_file, backup)
    temporary = args.factor_file.with_name(args.factor_file.stem + ".part.npz")
    np.savez_compressed(temporary, dates=dates, factors=factors)
    os.replace(temporary, args.factor_file)

    boundary_previous = first_position - 1
    report = {
        "code": "sh600018",
        "method": "价格连续：由同一份日线的前收盘价和前一日收盘价向前倒推",
        "formula": "A_previous = A_current * preclose_current / close_previous",
        "daily_file": str(args.daily_file.resolve()),
        "factor_file": str(args.factor_file.resolve()),
        "original_backup": str(backup.resolve()),
        "original_first_date": first_official,
        "original_first_factor": float(official_factors[0]),
        "extended_first_date": int(dates[0]),
        "derived_date_count": int(len(derived_dates_array)),
        "boundary_previous_date": int(daily_dates[boundary_previous]),
        "boundary_previous_factor": float(derived_factors_array[-1]),
        "boundary_previous_close": float(daily.at[boundary_previous, "收盘价"]),
        "boundary_current_preclose": float(daily.at[first_position, "前收盘价"]),
        "special_note": "2006年换股时按价格连续法计算；这不等同于按1:4.5换股比例计算真实持股收益。",
    }
    args.report_file.parent.mkdir(parents=True, exist_ok=True)
    args.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
