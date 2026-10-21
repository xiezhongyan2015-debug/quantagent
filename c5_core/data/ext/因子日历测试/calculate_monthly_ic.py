# -*- coding: utf-8 -*-
"""计算现有19个参数版本的标准月IC。"""

import gc
import os
import time

import numpy as np
import pandas as pd

import backtest_chip_cost_center as base
import backtest_daily_extensions as daily
import backtest_january_factors as january


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "monthly_ic_results")
START_DATE = pd.Timestamp("2016-01-01")
SPLIT_DATE = pd.Timestamp("2020-12-31")


def build_month_pairs(dates):
    """生成月末信号日、下月首日和下月末日。"""
    periods = dates.to_period("M")
    month_end_indices = np.flatnonzero(
        np.r_[periods[:-1].to_numpy() != periods[1:].to_numpy(), True]
    )
    pairs = []
    for position in range(len(month_end_indices) - 1):
        signal_index = int(month_end_indices[position])
        exit_index = int(month_end_indices[position + 1])
        entry_index = signal_index + 1
        signal_date = dates[signal_index]
        if signal_date < START_DATE:
            continue
        if entry_index > exit_index:
            continue
        pairs.append((signal_index, entry_index, exit_index, signal_date))
    return pairs


def calculate_factor_monthly_ic(factor, arrays, eligible, dates, month_pairs, factor_name):
    """月末取因子，下月首日开盘买入，下月末收盘计算收益。"""
    rows = []
    for signal_index, entry_index, exit_index, signal_date in month_pairs:
        future_return = arrays["close"][exit_index] / arrays["open"][entry_index] - 1.0
        mask = (
            eligible[signal_index]
            & np.isfinite(factor[signal_index])
            & np.isfinite(future_return)
            & (arrays["open"][entry_index] > 0)
        )
        stock_count = int(mask.sum())
        if stock_count < base.CONFIG["min_universe_stocks"]:
            continue
        ic = base.rank_correlation(factor[signal_index, mask], future_return[mask])
        if np.isfinite(ic):
            rows.append(
                {
                    "factor": factor_name,
                    "signal_date": signal_date,
                    "entry_date": dates[entry_index],
                    "exit_date": dates[exit_index],
                    "monthly_ic": ic,
                    "stock_count": stock_count,
                }
            )
    return pd.DataFrame(rows)


def summarize_monthly_ic(monthly_ic, factor_name):
    rows = []
    periods = {
        "full": monthly_ic,
        "2016_2020": monthly_ic[monthly_ic["signal_date"] <= SPLIT_DATE],
        "2021_2025": monthly_ic[monthly_ic["signal_date"] > SPLIT_DATE],
    }
    for period_name, subset in periods.items():
        values = subset["monthly_ic"].dropna()
        mean = values.mean()
        std = values.std(ddof=1)
        rows.append(
            {
                "factor": factor_name,
                "period": period_name,
                "monthly_ic_mean": mean,
                "monthly_ic_std": std,
                "monthly_icir": mean / std if std > 0 else np.nan,
                "positive_month_ratio": (values > 0).mean(),
                "t_stat": mean / (std / np.sqrt(len(values))) if std > 0 else np.nan,
                "months": len(values),
                "avg_stock_count": subset["stock_count"].mean(),
            }
        )
    return rows


def build_yearly_summary(monthly_ic):
    data = monthly_ic.copy()
    data["year"] = data["signal_date"].dt.year
    return (
        data.groupby(["factor", "year"])
        .agg(
            monthly_ic_mean=("monthly_ic", "mean"),
            monthly_ic_std=("monthly_ic", "std"),
            positive_month_ratio=("monthly_ic", lambda values: (values > 0).mean()),
            months=("monthly_ic", "size"),
            avg_stock_count=("stock_count", "mean"),
        )
        .reset_index()
    )


def load_daily_ic_summary():
    files = [
        os.path.join(BASE_DIR, "results", "ic_summary.csv"),
        os.path.join(BASE_DIR, "january_factor_results", "ic_summary.csv"),
        os.path.join(BASE_DIR, "daily_extension_results", "ic_summary.csv"),
    ]
    frames = [pd.read_csv(file) for file in files]
    return pd.concat(frames, ignore_index=True)


def write_report(summary):
    daily_summary = load_daily_ic_summary()
    monthly_full = summary[summary["period"] == "full"].copy()
    daily_full = daily_summary[daily_summary["period"] == "full"][["factor", "ic_mean", "icir"]]
    comparison = monthly_full.merge(daily_full, on="factor", how="left")
    comparison = comparison.sort_values("monthly_ic_mean", key=lambda values: values.abs(), ascending=False)

    lines = [
        "# 现有因子月IC结果",
        "",
        "计算方法：每月最后一个交易日收盘后取因子值，以次月第一个交易日开盘到次月最后一个交易日收盘的收益计算横截面等级相关。",
        "股票筛选与日IC测试一致；月IC与日IC的未来收益期限不同，不能直接按倍数比较。",
        "",
        "| 因子 | 月IC | 月ICIR | 正IC月份占比 | 月数 | 日IC | 日ICIR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in comparison.iterrows():
        lines.append(
            f"| {row['factor']} | {row['monthly_ic_mean']:+.4f} | {row['monthly_icir']:+.3f} | "
            f"{row['positive_month_ratio']:.1%} | {int(row['months'])} | {row['ic_mean']:+.4f} | {row['icir']:+.3f} |"
        )
    report_path = os.path.join(OUTPUT_DIR, "月IC总结.md")
    with open(report_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return report_path


def main():
    started = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    dates, stocks, arrays = base.load_data()
    daily_future_return, eligible, weight_cumsum, weighted_price_cumsum, valid_cumsum = (
        base.prepare_common_arrays(arrays)
    )
    del daily_future_return
    gc.collect()
    month_pairs = build_month_pairs(dates)
    print(f"月度观测：{len(month_pairs)}个，{month_pairs[0][3].date()}至{month_pairs[-1][3].date()}", flush=True)

    all_monthly = []
    all_summary = []

    def test_factor(factor_name, factor):
        factor_started = time.time()
        monthly_ic = calculate_factor_monthly_ic(
            factor, arrays, eligible, dates, month_pairs, factor_name
        )
        all_monthly.append(monthly_ic)
        rows = summarize_monthly_ic(monthly_ic, factor_name)
        all_summary.extend(rows)
        full = next(row for row in rows if row["period"] == "full")
        later = next(row for row in rows if row["period"] == "2021_2025")
        print(
            f"{factor_name}: 月IC={full['monthly_ic_mean']:+.4f}, "
            f"月ICIR={full['monthly_icir']:+.3f}, 后段={later['monthly_ic_mean']:+.4f}, "
            f"耗时={time.time() - factor_started:.1f}秒",
            flush=True,
        )

    print("计算筹码成本重心月IC...", flush=True)
    for window in base.CONFIG["windows"]:
        code = f"CKDP_{window}D"
        factor = base.compute_factor(
            arrays, window, weight_cumsum, weighted_price_cumsum, valid_cumsum
        )
        test_factor(code, factor)
        del factor
        gc.collect()

    del weight_cumsum, weighted_price_cumsum, valid_cumsum
    gc.collect()

    print("计算日线原式因子月IC...", flush=True)
    factor = january.compute_scc_20d(arrays["close"], window=20)
    test_factor("SCC_20D", factor)
    del factor
    gc.collect()
    factor = january.compute_ideal_amplitude_20d(
        arrays["close"], arrays["high"], arrays["low"], window=20, fraction=0.25
    )
    test_factor("IDEAL_AMPLITUDE_20D", factor)
    del factor
    gc.collect()

    print("计算14个日线改版月IC...", flush=True)
    builders = {
        "D_AMIHUD_ILLIQ_20D": lambda: daily.compute_amihud(arrays),
        "D_WEIGHTED_CLOSE_RATIO_20D": lambda: daily.compute_weighted_close_ratio(arrays),
        "D_LOG_VOLUME_KURT_20D": lambda: daily.compute_log_volume_kurtosis(arrays),
        "D_RETURN_SKEW_20D": lambda: daily.compute_return_skew(arrays),
        "D_PRICE_ELASTICITY_TREND": lambda: daily.compute_price_elasticity_trend(arrays),
        "D_VOLUME_PEAK_PRICE_POS_20D": lambda: daily.compute_volume_peak_price_position(arrays),
        "D_TGD_20D": lambda: daily.compute_tgd(arrays),
        "D_VOLUME_RIDGE_GAP_SKEW_120D": lambda: daily.compute_volume_ridge_gap_skew(arrays),
        "D_DOWNSIDE_VOL_20D": lambda: daily.compute_downside_vol(arrays),
        "D_CGO_PROXY_20D": lambda: daily.compute_cgo_proxy(arrays, 20),
        "D_CGO_PROXY_60D": lambda: daily.compute_cgo_proxy(arrays, 60),
        "D_CGO_PROXY_120D": lambda: daily.compute_cgo_proxy(arrays, 120),
        "D_VOLUME_CV_20D": lambda: daily.compute_volume_cv(arrays),
        "D_INTRADAY_RETURN": lambda: daily.compute_intraday_return(arrays),
    }
    for factor_name, builder in builders.items():
        factor = builder()
        test_factor(factor_name, factor)
        del factor
        gc.collect()

    monthly_ic = pd.concat(all_monthly, ignore_index=True)
    summary = pd.DataFrame(all_summary)
    yearly = build_yearly_summary(monthly_ic)
    monthly_ic.to_csv(
        os.path.join(OUTPUT_DIR, "monthly_ic.csv"), index=False, encoding="utf-8-sig"
    )
    summary.to_csv(
        os.path.join(OUTPUT_DIR, "monthly_ic_summary.csv"), index=False, encoding="utf-8-sig"
    )
    yearly.to_csv(
        os.path.join(OUTPUT_DIR, "yearly_monthly_ic.csv"), index=False, encoding="utf-8-sig"
    )
    report_path = write_report(summary)
    print(f"完成：{report_path}", flush=True)
    print(f"总耗时={time.time() - started:.1f}秒", flush=True)


if __name__ == "__main__":
    main()
