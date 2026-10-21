# -*- coding: utf-8 -*-
"""用现有 Qlib 日线数据回测一月因子中可直接计算的因子。"""

import gc
import os
import time

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy import stats

import backtest_chip_cost_center as base


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "january_factor_results")
FACTOR_NAMES = ["SCC_20D", "IDEAL_AMPLITUDE_20D"]


def compute_scc_20d(close, window=20):
    """计算过去 20 日平均相关系数对应的空间网络相对中心度。"""
    daily_return = np.full_like(close, np.nan)
    daily_return[1:] = close[1:] / close[:-1] - 1.0
    result = np.full_like(close, np.nan)

    for end in range(window, len(close)):
        values = daily_return[end - window + 1 : end + 1]
        valid = np.isfinite(values).all(axis=0)
        valid_indices = np.flatnonzero(valid)
        if len(valid_indices) < base.CONFIG["min_universe_stocks"]:
            continue

        sample = values[:, valid_indices]
        mean = sample.mean(axis=0)
        std = sample.std(axis=0, ddof=0)
        non_constant = std > 1e-12
        valid_indices = valid_indices[non_constant]
        if len(valid_indices) < base.CONFIG["min_universe_stocks"]:
            continue

        sample = sample[:, non_constant]
        mean = mean[non_constant]
        std = std[non_constant]
        zscore = (sample - mean) / std
        market_sum = zscore.sum(axis=1)

        # 用等价公式求每只股票与其余股票的平均相关系数，避免生成巨大的相关系数矩阵。
        correlation_sum = zscore.T @ market_sum / window
        average_correlation = (correlation_sum - 1.0) / (len(valid_indices) - 1)
        average_correlation = np.clip(average_correlation, -0.999999, 0.999999)
        result[end, valid_indices] = 1.0 / (2.0 * (1.0 - average_correlation))

    return result


def compute_ideal_amplitude_20d(close, high, low, window=20, fraction=0.25, chunk=64):
    """计算高价日振幅均值减低价日振幅均值。"""
    amplitude = high / low - 1.0
    result = np.full_like(close, np.nan)
    close_windows = sliding_window_view(close, window_shape=window, axis=0)
    amplitude_windows = sliding_window_view(amplitude, window_shape=window, axis=0)
    count = max(int(round(window * fraction)), 1)

    for start in range(0, len(close_windows), chunk):
        stop = min(start + chunk, len(close_windows))
        close_part = close_windows[start:stop]
        amplitude_part = amplitude_windows[start:stop]
        valid = np.isfinite(close_part).all(axis=2) & np.isfinite(amplitude_part).all(axis=2)
        order = np.argsort(close_part, axis=2, kind="stable")
        sorted_amplitude = np.take_along_axis(amplitude_part, order, axis=2)
        low_mean = sorted_amplitude[:, :, :count].mean(axis=2)
        high_mean = sorted_amplitude[:, :, -count:].mean(axis=2)
        factor_part = high_mean - low_mean
        factor_part[~valid] = np.nan
        result[start + window - 1 : stop + window - 1] = factor_part

    return result


def build_yearly_ic(ic_daily):
    rows = []
    for (factor_name, year), subset in ic_daily.groupby(["factor", ic_daily["date"].dt.year]):
        values = subset["ic"].dropna()
        rows.append(
            {
                "factor": factor_name,
                "year": int(year),
                "ic_mean": values.mean(),
                "ic_std": values.std(ddof=1),
                "icir": values.mean() / values.std(ddof=1) if values.std(ddof=1) > 0 else np.nan,
                "days": len(values),
            }
        )
    return pd.DataFrame(rows)


def write_report(ic_summary, backtest_summary, monotonicity, yearly_backtest):
    full_ic = ic_summary[ic_summary["period"] == "full"].copy()
    later = backtest_summary[backtest_summary["period"] == "2021_2025"].copy()
    combined = full_ic.merge(later, on="factor", suffixes=("_ic", "_bt"))
    combined = combined.merge(monotonicity, on="factor", how="left")

    lines = [
        "# 一月因子可回测部分总结",
        "",
        "## 结论",
        "",
    ]
    for _, row in combined.sort_values("icir", ascending=False).iterrows():
        direction = "低因子组" if row["direction"] == "low" else "高因子组"
        lines.append(
            f"- **{row['factor']}**：全期 IC {row['ic_mean']:+.4f}，ICIR {row['icir']:+.3f}；"
            f"按 2016—2020 年方向选择{direction}后，2021—2025 年扣费年化收益 "
            f"{row['annual_return']:.2%}，同范围等权收益 {row['benchmark_annual_return']:.2%}，"
            f"年化相差 {row['annual_excess_return']:+.2%}，最大回撤 {row['max_drawdown']:.2%}。"
        )

    lines.extend(
        [
            "",
            "## 统一方法",
            "",
            "- 股票范围：沪深 800 与中证 1000 的合并范围。",
            "- 日期：2016-01-04 至 2025-12-31；2016—2020 年用于确定买入高组还是低组，2021—2025 年单独查看。",
            "- 因子在收盘后计算，下一个交易日开盘买入，持有 5 个交易日。",
            "- 未来收益：下一个交易日开盘至第六个交易日开盘。",
            "- 排除股价低于 3 元、20 日平均成交额低于 500 万元的股票。",
            "- 买入费用 0.03%，卖出费用 0.13%。",
            "- IC 为每日股票间等级相关系数；ICIR 为每日 IC 均值除以每日 IC 标准差。",
            "",
            "## 注意",
            "",
            "- 股票范围使用数据中可取得的指数股票合集，没有按每个历史日期重新限定成分股。",
            "- 空间网络相对中心度要求股票在 20 日窗口内收益完整；相关系数按窗口内有效股票计算。",
            "- 结果用于判断因子是否有稳定关系，不代表可以直接交易。",
            "",
            "## 分年结果",
            "",
            "| 因子 | 年份 | 扣费收益 | 同范围等权收益 | 相差 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for _, row in yearly_backtest.iterrows():
        lines.append(
            f"| {row['factor']} | {int(row['year'])} | {row['strategy_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['excess_return']:+.2%} |"
        )

    report_path = os.path.join(OUTPUT_DIR, "一月因子可回测总结.md")
    with open(report_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return report_path


def main():
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base.CONFIG["output_dir"] = OUTPUT_DIR

    dates, stocks, arrays = base.load_data()
    future_return, current_eligible, weight_cumsum, weighted_price_cumsum, valid_cumsum = (
        base.prepare_common_arrays(arrays)
    )
    del weight_cumsum, weighted_price_cumsum, valid_cumsum
    gc.collect()

    factor_builders = {
        "SCC_20D": lambda: compute_scc_20d(arrays["close"], window=20),
        "IDEAL_AMPLITUDE_20D": lambda: compute_ideal_amplitude_20d(
            arrays["close"], arrays["high"], arrays["low"], window=20, fraction=0.25
        ),
    }

    all_ic = []
    all_ic_daily = []
    all_quantile_daily = []
    all_rebalance = []
    all_backtest_summary = []

    for factor_name, builder in factor_builders.items():
        factor_start = time.time()
        print(f"正在计算 {factor_name}...")
        factor = builder()

        ic_daily = base.calculate_daily_ic(factor, future_return, current_eligible, dates)
        ic_daily["factor"] = factor_name
        ic_rows = base.summarize_ic(ic_daily, factor_name)
        all_ic.extend(ic_rows)
        all_ic_daily.append(ic_daily)

        quantile_daily = base.calculate_quantile_returns(
            factor, future_return, current_eligible, dates, factor_name
        )
        all_quantile_daily.append(quantile_daily)

        rebalance_data = base.run_rebalance_test(
            factor, future_return, current_eligible, arrays, dates, factor_name
        )
        backtest_rows = base.summarize_rebalance(rebalance_data, all_ic, factor_name)
        all_backtest_summary.extend(backtest_rows)
        all_rebalance.append(rebalance_data)

        full_row = next(row for row in ic_rows if row["period"] == "full")
        later_row = next(row for row in ic_rows if row["period"] == "2021_2025")
        print(
            f"  全期 IC={full_row['ic_mean']:+.4f}，后半段 IC={later_row['ic_mean']:+.4f}，"
            f"耗时 {time.time() - factor_start:.1f} 秒"
        )
        del factor, ic_daily, quantile_daily, rebalance_data
        gc.collect()

    ic_summary = pd.DataFrame(all_ic)
    ic_daily_all = pd.concat(all_ic_daily, ignore_index=True)
    yearly_ic = build_yearly_ic(ic_daily_all)
    quantile_daily_all = pd.concat(all_quantile_daily, ignore_index=True)
    quantile_summary = (
        quantile_daily_all.groupby(["factor", "group"])
        .agg(
            mean_5d_return=("future_5d_return", "mean"),
            median_5d_return=("future_5d_return", "median"),
            positive_ratio=("future_5d_return", lambda value: (value > 0).mean()),
            days=("future_5d_return", "size"),
            avg_stock_count=("stock_count", "mean"),
        )
        .reset_index()
    )
    monotonicity = pd.DataFrame(
        [
            {
                "factor": factor_name,
                "group_return_spearman": stats.spearmanr(
                    subset["group"], subset["mean_5d_return"]
                ).statistic,
            }
            for factor_name, subset in quantile_summary.groupby("factor")
        ]
    )
    rebalance_all = pd.concat(all_rebalance, ignore_index=True)
    backtest_summary = pd.DataFrame(all_backtest_summary)
    yearly_backtest = base.build_yearly_backtest(rebalance_all)

    files = {
        "ic_summary.csv": ic_summary,
        "daily_ic.csv": ic_daily_all,
        "yearly_ic.csv": yearly_ic,
        "quantile_summary.csv": quantile_summary,
        "quantile_monotonicity.csv": monotonicity,
        "rebalance_returns.csv": rebalance_all,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
    }
    for name, frame in files.items():
        frame.to_csv(os.path.join(OUTPUT_DIR, name), index=False, encoding="utf-8-sig")

    report_path = write_report(ic_summary, backtest_summary, monotonicity, yearly_backtest)
    print(f"完成：{report_path}")
    print(f"总耗时 {time.time() - start_time:.1f} 秒")


if __name__ == "__main__":
    main()
