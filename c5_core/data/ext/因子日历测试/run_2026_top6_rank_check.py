# -*- coding: utf-8 -*-
"""重算2026年1—5月前六个因子，并比较短期与长期内部排名。"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_february_march_backtest as feb_mar
import run_january_minute_backtest as january
import run_june_backtest as june
import run_may_backtest as may


BASE_DIR = Path(__file__).resolve().parent

FACTOR_ORDER = [
    "J06_D10_VOLATILITY_PANIC_20D",
    "MAY14_PANIC_20D",
    "M27_INTRADAY_JUMPINESS",
    "J06_D08_WAITING_RESCUE_20D",
    "J06_D04_FUZZY_AMOUNT_RATIO_20D",
    "F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D",
]

FACTOR_NAMES = {
    "J06_D10_VOLATILITY_PANIC_20D": "波动率加剧—惊恐因子",
    "MAY14_PANIC_20D": "惊恐因子",
    "M27_INTRADAY_JUMPINESS": "日内跳跃度",
    "J06_D08_WAITING_RESCUE_20D": "待著而救因子",
    "J06_D04_FUZZY_AMOUNT_RATIO_20D": "模糊金额比",
    "F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D": "谷岭加权价格比",
}

SCRIPT_TO_CANONICAL = {
    "M27_INTRADAY_JUMPINESS_20D": "M27_INTRADAY_JUMPINESS",
}
CANONICAL_TO_SCRIPT = {value: key for key, value in SCRIPT_TO_CANONICAL.items()}

SOURCE_FILES = {
    "J06_D10_VOLATILITY_PANIC_20D": "june_factor_results/final/original_frequency_ic.csv",
    "MAY14_PANIC_20D": "may_factor_results/final/original_frequency_ic.csv",
    "M27_INTRADAY_JUMPINESS": "february_march_results/final/original_frequency_ic.csv",
    "J06_D08_WAITING_RESCUE_20D": "june_factor_results/final/original_frequency_ic.csv",
    "J06_D04_FUZZY_AMOUNT_RATIO_20D": "june_factor_results/final/original_frequency_ic.csv",
    "F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D": "february_march_results/final/original_frequency_ic.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-cache-dir",
        type=Path,
        default=BASE_DIR / ".codex-artifact/2026_rank_check/cache/base",
    )
    parser.add_argument(
        "--june-cache-dir",
        type=Path,
        default=BASE_DIR / ".codex-artifact/2026_rank_check/cache/june",
    )
    parser.add_argument(
        "--qlib-dir",
        type=Path,
        default=BASE_DIR / ".codex-artifact/2026_rank_check/qlib_overlay",
    )
    parser.add_argument(
        "--clean-rows",
        type=Path,
        default=BASE_DIR / ".codex-artifact/2026_rank_check/current_clean_rows.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BASE_DIR / ".codex-artifact/2026_rank_check/results",
    )
    parser.add_argument("--start", default="2025-06-02")
    parser.add_argument("--backtest-start", default="2025-12-01")
    parser.add_argument("--end", default="2026-05-29")
    return parser.parse_args()


def _canonical(code: str) -> str:
    return SCRIPT_TO_CANONICAL.get(code, code)


def _icir(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) < 2:
        return np.nan
    standard = values.std(ddof=1)
    if not np.isfinite(standard) or standard == 0:
        return np.nan
    return float(values.mean() / standard)


def _rank_by_abs(values: pd.Series) -> pd.Series:
    return values.abs().rank(method="min", ascending=False).astype("Int64")


def _load_published(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    header = payload["header"]
    frame = pd.DataFrame(payload["rows"], columns=header)
    frame = frame[frame["因子代码"].isin(FACTOR_ORDER)].copy()
    frame["旧57库名次"] = pd.to_numeric(frame["|IC|名次"], errors="raise").astype(int)
    frame["旧IC"] = pd.to_numeric(frame["全期IC"], errors="raise")
    frame["旧ICIR"] = pd.to_numeric(frame["全期ICIR"], errors="raise")
    frame["旧观测数"] = pd.to_numeric(frame["观测期数"], errors="raise").astype(int)
    frame = frame.rename(columns={"因子代码": "factor", "因子名称": "factor_name"})
    frame["旧六因子内部名次"] = _rank_by_abs(frame["旧IC"])
    return frame[
        [
            "factor",
            "factor_name",
            "旧57库名次",
            "旧六因子内部名次",
            "旧观测数",
            "旧IC",
            "旧ICIR",
        ]
    ].sort_values("旧57库名次")


def _load_old_raw() -> pd.DataFrame:
    frames = []
    for relative in sorted(set(SOURCE_FILES.values())):
        path = BASE_DIR / relative
        frame = pd.read_csv(path)
        frame["source_file"] = relative.replace("/", "\\")
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    combined["factor"] = combined["factor"].map(_canonical)
    combined = combined[combined["factor"].isin(FACTOR_ORDER)].copy()
    for column in ["signal_date", "entry_date", "exit_date"]:
        combined[column] = pd.to_datetime(combined[column], errors="raise")
    combined["sample"] = "2016—2025原记录"
    combined["return_month"] = combined["exit_date"].dt.to_period("M").astype(str)
    return combined


def _summarize_raw(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    rows = []
    for factor, group in frame.groupby("factor", sort=False):
        rows.append(
            {
                "factor": factor,
                f"{prefix}观测数": int(group["ic"].notna().sum()),
                f"{prefix}IC": float(group["ic"].mean()),
                f"{prefix}ICIR": _icir(group["ic"]),
                f"{prefix}最少股票数": int(group["stock_count"].min()),
                f"{prefix}平均股票数": float(group["stock_count"].mean()),
            }
        )
    result = pd.DataFrame(rows)
    result[f"{prefix}内部名次"] = _rank_by_abs(result[f"{prefix}IC"])
    return result


def _monthly_summary(monthly: pd.DataFrame, prefix: str) -> pd.DataFrame:
    rows = []
    for factor, group in monthly.groupby("factor", sort=False):
        rows.append(
            {
                "factor": factor,
                f"{prefix}月份数": int(group["monthly_ic"].notna().sum()),
                f"{prefix}IC": float(group["monthly_ic"].mean()),
                f"{prefix}IC标准差": float(group["monthly_ic"].std(ddof=1)),
                f"{prefix}ICIR": _icir(group["monthly_ic"]),
                f"{prefix}正IC月数": int((group["monthly_ic"] > 0).sum()),
            }
        )
    result = pd.DataFrame(rows)
    result[f"{prefix}内部名次"] = _rank_by_abs(result[f"{prefix}IC"])
    return result


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = january.Settings(
        cache_dir=args.base_cache_dir,
        output_dir=args.output_dir,
        qlib_dir=args.qlib_dir,
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
    )

    print("[1/5] 读取2025年中至2026年5月的基础数据...", flush=True)
    base_columns = [
        "date",
        "code",
        "open",
        "close",
        "amount",
        "qfq_ratio",
        "vwap_outside_range_1pct",
        "intraday_jumpiness_ex_open_close_daily",
        "valley_ridge_vwap_ratio_daily",
    ]
    june_columns = [
        "date",
        "code",
        "fuzzy_amount_ratio_daily",
        "wait_rescue_follow_daily",
        "intraday_volatility_1m",
    ]
    base_files = january.list_cache_files(args.base_cache_dir)
    june_files = january.list_cache_files(args.june_cache_dir)
    base_panel = feb_mar.load_panel(base_files, args.start, args.end, base_columns)
    june_panel = feb_mar.load_panel(june_files, args.start, args.end, june_columns)
    panel = base_panel.merge(
        june_panel,
        on=["date", "code"],
        how="inner",
        validate="one_to_one",
    )
    del base_panel, june_panel
    gc.collect()

    dates = january.read_calendar(args.qlib_dir, args.start, args.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(args.qlib_dir, args.start, args.end)
    membership, missing_members = january.build_membership(dates, stocks, membership_rows)
    missing_members.to_csv(args.output_dir / "qlib_members_without_cache.csv", index=False)

    def matrix(column: str) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=np.float32
        )

    arrays = {
        "open": matrix("open"),
        "close": matrix("close"),
        "amount": matrix("amount"),
        "qfq_ratio": matrix("qfq_ratio"),
    }
    arrays["raw_close"] = arrays["close"] / arrays["qfq_ratio"]
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    print("[2/5] 按原公式生成前六个因子...", flush=True)
    factors: dict[str, np.ndarray] = {}

    jumpiness = matrix("intraday_jumpiness_ex_open_close_daily")
    jumpiness_mean, jumpiness_std = january.rolling_mean_std(jumpiness, 20)
    factors["M27_INTRADAY_JUMPINESS"] = np.asarray(
        0.5 * jumpiness_mean + 0.5 * jumpiness_std, dtype=np.float32
    )
    del jumpiness, jumpiness_mean, jumpiness_std

    valley_ridge = matrix("valley_ridge_vwap_ratio_daily")
    valley_ridge = january.mask_bad_days(valley_ridge, arrays["price_amount_bad"])
    factors["F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D"] = np.asarray(
        january.rolling_mean(valley_ridge, 20), dtype=np.float32
    )
    del valley_ridge

    market_close = may.read_qlib_feature(args.qlib_dir, "sh000985", "close", dates)
    factors["MAY14_PANIC_20D"] = np.asarray(
        may.compute_panic_factor(arrays["close"], market_close, 20), dtype=np.float32
    )

    factors["J06_D04_FUZZY_AMOUNT_RATIO_20D"] = june._mean_std_composite(
        matrix("fuzzy_amount_ratio_daily"), 20
    )
    factors["J06_D08_WAITING_RESCUE_20D"] = june._mean_std_composite(
        matrix("wait_rescue_follow_daily"), 20
    )
    market_return = june._read_qlib_close_return(args.qlib_dir, "sh000985", dates)
    factors["J06_D10_VOLATILITY_PANIC_20D"] = june._compute_volatility_panic(
        january.daily_returns(arrays["close"]),
        market_return,
        matrix("intraday_volatility_1m"),
        20,
    )
    del panel
    gc.collect()

    print("[3/5] 计算2026年1—5月IC...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )
    raw_frames = []
    daily_code = "F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D"
    for code in FACTOR_ORDER:
        if code == daily_code:
            current = january.calculate_daily_ic(
                code, factors[code], future_return, eligible, dates, settings
            ).rename(columns={"date": "signal_date"})
            current["signal_frequency"] = "daily"
            current["return_horizon"] = "next_5_trading_days"
        else:
            current = january.calculate_monthly_ic(
                code, factors[code], arrays, eligible, dates, settings
            )
            current["signal_frequency"] = "monthly"
            current["return_horizon"] = "next_month"
        raw_frames.append(current)
    raw_2026 = pd.concat(raw_frames, ignore_index=True)
    for column in ["signal_date", "entry_date", "exit_date"]:
        raw_2026[column] = pd.to_datetime(raw_2026[column], errors="raise")
    raw_2026 = raw_2026[
        raw_2026["exit_date"].between("2026-01-01", "2026-05-31")
    ].copy()
    raw_2026["factor_name"] = raw_2026["factor"].map(FACTOR_NAMES)
    raw_2026["sample"] = "2026年1—5月"
    raw_2026["return_month"] = raw_2026["exit_date"].dt.to_period("M").astype(str)
    raw_2026 = raw_2026[
        [
            "sample",
            "factor",
            "factor_name",
            "signal_frequency",
            "return_horizon",
            "signal_date",
            "entry_date",
            "exit_date",
            "return_month",
            "ic",
            "stock_count",
        ]
    ].sort_values(["factor", "exit_date", "signal_date"])

    monthly_2026 = (
        raw_2026.groupby(["factor", "factor_name", "return_month"], as_index=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "count"),
            min_stock_count=("stock_count", "min"),
            avg_stock_count=("stock_count", "mean"),
        )
        .sort_values(["factor", "return_month"])
    )

    expected_months = [f"2026-{month:02d}" for month in range(1, 6)]
    month_counts = monthly_2026.groupby("factor")["return_month"].nunique()
    if month_counts.reindex(FACTOR_ORDER).ne(5).any():
        raise RuntimeError(f"2026月份数量不完整：{month_counts.to_dict()}")
    if sorted(monthly_2026["return_month"].unique()) != expected_months:
        raise RuntimeError("2026收益月份不是完整的1—5月")
    if (raw_2026["stock_count"] < settings.min_stocks).any():
        raise RuntimeError("存在股票数量低于50的IC记录")

    print("[4/5] 比较旧排名、共同月份排名和加入2026后的排名...", flush=True)
    published = _load_published(args.clean_rows)
    old_raw = _load_old_raw()
    old_raw_summary = _summarize_raw(old_raw, "旧原频率")
    check = published.merge(old_raw_summary, on="factor", validate="one_to_one")
    if not np.allclose(check["旧IC"], check["旧原频率IC"], rtol=0, atol=1e-12):
        mismatch = check.loc[
            ~np.isclose(check["旧IC"], check["旧原频率IC"], rtol=0, atol=1e-12),
            ["factor", "旧IC", "旧原频率IC"],
        ]
        raise RuntimeError(f"旧总表与原始IC记录不一致：\n{mismatch}")

    old_monthly = (
        old_raw.groupby(["factor", "return_month"], as_index=False)
        .agg(
            monthly_ic=("ic", "mean"),
            raw_observations=("ic", "count"),
            min_stock_count=("stock_count", "min"),
            avg_stock_count=("stock_count", "mean"),
        )
        .sort_values(["factor", "return_month"])
    )
    common_months = (
        old_monthly.groupby("return_month")["factor"]
        .nunique()
        .loc[lambda values: values == len(FACTOR_ORDER)]
        .index
    )
    old_common = old_monthly[old_monthly["return_month"].isin(common_months)].copy()
    old_common_summary = _monthly_summary(old_common, "旧共同月份")
    recent_summary = _monthly_summary(monthly_2026, "2026月均")
    raw_2026_summary = _summarize_raw(raw_2026, "2026原频率")

    combined_raw = pd.concat(
        [
            old_raw[
                [
                    "sample",
                    "factor",
                    "signal_frequency",
                    "return_horizon",
                    "signal_date",
                    "entry_date",
                    "exit_date",
                    "return_month",
                    "ic",
                    "stock_count",
                ]
            ],
            raw_2026.drop(columns="factor_name"),
        ],
        ignore_index=True,
    )
    updated_summary = _summarize_raw(combined_raw, "加入2026后")

    comparison = published.merge(
        old_common_summary, on="factor", validate="one_to_one"
    ).merge(recent_summary, on="factor", validate="one_to_one")
    comparison = comparison.merge(
        raw_2026_summary, on="factor", validate="one_to_one"
    ).merge(updated_summary, on="factor", validate="one_to_one")
    comparison["2026名次变化"] = (
        comparison["旧六因子内部名次"].astype(int)
        - comparison["2026月均内部名次"].astype(int)
    )
    comparison["长期名次变化"] = (
        comparison["旧六因子内部名次"].astype(int)
        - comparison["加入2026后内部名次"].astype(int)
    )
    comparison["历史方向"] = np.where(comparison["旧IC"] >= 0, "正向", "负向")
    comparison["2026方向"] = np.where(comparison["2026月均IC"] >= 0, "正向", "负向")
    comparison["方向是否保持"] = np.where(
        np.sign(comparison["旧IC"]) == np.sign(comparison["2026月均IC"]),
        "是",
        "否",
    )
    comparison["方向后2026IC"] = np.sign(comparison["旧IC"]) * comparison["2026月均IC"]
    comparison = comparison.sort_values("2026月均内部名次")

    print("[5/5] 保存结果和检查记录...", flush=True)
    raw_2026.to_csv(args.output_dir / "top6_2026_ic_raw.csv", index=False, encoding="utf-8-sig")
    monthly_2026.to_csv(
        args.output_dir / "top6_2026_monthly_ic.csv", index=False, encoding="utf-8-sig"
    )
    old_common.to_csv(
        args.output_dir / "top6_old_common_monthly_ic.csv",
        index=False,
        encoding="utf-8-sig",
    )
    comparison.to_csv(
        args.output_dir / "top6_ranking_comparison.csv", index=False, encoding="utf-8-sig"
    )
    combined_raw.to_csv(
        args.output_dir / "top6_combined_raw_ic.csv", index=False, encoding="utf-8-sig"
    )

    manifest = {
        "period": "2026-01-01至2026-05-29，按收益实现月统计",
        "trading_days_2026": int((dates.year == 2026).sum()),
        "cache_start": args.start,
        "calendar_end": str(dates[-1].date()),
        "stock_files_base": len(base_files),
        "stock_files_june": len(june_files),
        "matrix_stocks": len(stocks),
        "median_membership": int(np.median(membership.sum(axis=1))),
        "old_common_months": int(len(common_months)),
        "old_common_start": str(min(common_months)),
        "old_common_end": str(max(common_months)),
        "factor_count": len(FACTOR_ORDER),
        "monthly_ic_rows_2026": int(len(monthly_2026)),
        "raw_ic_rows_2026": int(len(raw_2026)),
        "rules": {
            "universe": "中证800与中证1000并集；2026年使用Tushare月末真实成分快照",
            "min_raw_price": settings.min_price,
            "min_20d_avg_amount": settings.min_avg_amount,
            "min_stocks": settings.min_stocks,
            "ic": "股票截面Spearman相关系数",
            "monthly_return": "月末信号；次月首个交易日开盘到月末收盘",
            "daily_return": "当日信号；次日开盘到第6个交易日开盘",
            "recent_comparison": "先按收益实现月汇总，再以5个月IC均值的绝对值排名",
            "icir": "IC均值除以IC样本标准差，标准差ddof=1",
            "normalization": "不做额外归一化；Spearman IC只使用排序",
        },
        "sources": {
            "minute_cache_base": str(args.base_cache_dir.resolve()),
            "minute_cache_june": str(args.june_cache_dir.resolve()),
            "qlib_overlay": str(args.qlib_dir.resolve()),
            "old_clean_table_export": str(args.clean_rows.resolve()),
            "old_ic_files": sorted(set(SOURCE_FILES.values())),
            "index": "Tushare index_daily：000985.CSI",
            "membership": "Tushare index_weight：000906.SH、000852.SH，按2026年月末快照",
        },
        "limitations": [
            "2026年只有5个月，月频ICIR样本很短，只适合作近期观察。",
            "本轮只更新六个因子，因此2026名次和加入2026后的名次都是六因子内部排名，不代表57因子全库新排名。",
            "分钟数据截至2026-05-29，未计算2026年6月。",
        ],
    }
    (args.output_dir / "methodology.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(comparison[["factor_name", "旧六因子内部名次", "2026月均内部名次", "加入2026后内部名次", "2026月均IC", "2026月均ICIR", "方向是否保持"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
