# -*- coding: utf-8 -*-
"""把一月因子中适合的高频思想改成日线版本并统一回测。"""

import gc
import os
import time

import numpy as np
import pandas as pd
from scipy import stats

import backtest_chip_cost_center as base


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "daily_extension_results")

FACTOR_INFO = {
    "D_AMIHUD_ILLIQ_20D": {
        "name": "日线非流动性",
        "source_day": 10,
        "formula": "20日平均(|日收益|/成交额)",
        "relation": "由回测确定",
        "note": "用日收益和日成交额构造的Amihud日线版本。",
    },
    "D_WEIGHTED_CLOSE_RATIO_20D": {
        "name": "日线加权收盘价比",
        "source_day": 12,
        "formula": "20日成交量加权收盘价/20日收盘价简单平均",
        "relation": "原文预期负向",
        "note": "把原式的日内分钟段替换为过去20个交易日。",
    },
    "D_LOG_VOLUME_KURT_20D": {
        "name": "日线对数成交量峰度",
        "source_day": 14,
        "formula": "过去20日log(1+成交量)的超额峰度",
        "relation": "原文预期负向",
        "note": "原图片允许使用对数成交量变体；把日内分钟分布替换为跨日分布。",
    },
    "D_RETURN_SKEW_20D": {
        "name": "日线收益偏度",
        "source_day": 19,
        "formula": "过去20日日收益率偏度",
        "relation": "原文预期负向",
        "note": "把分钟收益偏度改为日收益偏度。",
    },
    "D_PRICE_ELASTICITY_TREND": {
        "name": "日线价格弹性趋势",
        "source_day": 22,
        "formula": "[20日均值-120日均值]/120日标准差，基础指标=(最高-最低)/收盘价/成交额",
        "relation": "原文预期正向",
        "note": "用日内振幅比例和日成交额替代tick序列。",
    },
    "D_VOLUME_PEAK_PRICE_POS_20D": {
        "name": "日线量峰加权价格分位点",
        "source_day": 26,
        "formula": "识别成交量高于前20日均值+1倍标准差的交易日，对价格分位点做成交量加权20日均值",
        "relation": "原文预期正向",
        "note": "把分钟量峰替换为日成交量峰值。",
    },
    "D_TGD_20D": {
        "name": "日线涨跌时间重心偏离",
        "source_day": 28,
        "formula": "过去20日涨跌幅时间重心横截面回归残差，再取20日均值",
        "relation": "原文预期正向",
        "note": "把分钟序列替换为交易日序列，保留涨跌时间重心和横截面回归。",
    },
    "D_VOLUME_RIDGE_GAP_SKEW_120D": {
        "name": "日线量峰间隔偏度",
        "source_day": 30,
        "formula": "识别日成交量峰值形成的连续量岭，计算近120日量岭起点间隔的偏度",
        "relation": "原文预期负向",
        "note": "把分钟量岭替换为连续放量交易日。",
    },
    "D_DOWNSIDE_VOL_20D": {
        "name": "日线下行波动率",
        "source_day": 6,
        "formula": "sqrt[20日平均(min(日收益,0)^2)]",
        "relation": "原文预期正向风险补偿",
        "note": "日线无法识别日内跳跃，因此改名为下行波动率。",
    },
    "D_CGO_PROXY_20D": {
        "name": "资本利得突出量（日线近似20日）",
        "source_day": 1,
        "formula": "(收盘价-20日成交量加权均价)/收盘价",
        "relation": "原文预期负向",
        "note": "缺换手率生存权重，使用成交量加权成本近似。",
    },
    "D_CGO_PROXY_60D": {
        "name": "资本利得突出量（日线近似60日）",
        "source_day": 1,
        "formula": "(收盘价-60日成交量加权均价)/收盘价",
        "relation": "原文预期负向",
        "note": "缺换手率生存权重，使用成交量加权成本近似。",
    },
    "D_CGO_PROXY_120D": {
        "name": "资本利得突出量（日线近似120日）",
        "source_day": 1,
        "formula": "(收盘价-120日成交量加权均价)/收盘价",
        "relation": "原文预期负向",
        "note": "缺换手率生存权重，使用成交量加权成本近似。",
    },
    "D_VOLUME_CV_20D": {
        "name": "日线成交量均匀度",
        "source_day": 4,
        "formula": "过去20日日成交量标准差/平均值",
        "relation": "原文预期负向",
        "note": "把每日分钟换手率的离散程度改为跨日成交量离散程度。",
    },
    "D_INTRADAY_RETURN": {
        "name": "日线收盘/开盘收益",
        "source_day": 20,
        "formula": "收盘价/开盘价-1",
        "relation": "由回测确定",
        "note": "只能代表全天日内收益，不能代表尾盘半小时收益。",
    },
}


def safe_divide(numerator, denominator):
    result = np.full_like(numerator, np.nan, dtype=np.float64)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (np.abs(denominator) > 1e-12)
    result[valid] = numerator[valid] / denominator[valid]
    return result


def rolling_sum_count(values, window):
    valid = np.isfinite(values)
    sums = np.cumsum(np.where(valid, values, 0.0), axis=0)
    counts = np.cumsum(valid.astype(np.int32), axis=0)
    sums = base.rolling_from_cumsum(sums, window)
    counts = base.rolling_from_cumsum(counts, window)
    return sums, counts


def rolling_mean_std(values, window, min_count):
    sums, counts = rolling_sum_count(values, window)
    square_sums, _ = rolling_sum_count(values * values, window)
    mean = sums / np.maximum(counts, 1)
    variance = square_sums / np.maximum(counts, 1) - mean * mean
    variance = np.maximum(variance, 0.0)
    std = np.sqrt(variance)
    invalid = counts < min_count
    mean[invalid] = np.nan
    std[invalid] = np.nan
    mean[: window - 1] = np.nan
    std[: window - 1] = np.nan
    return mean, std, counts


def rolling_skew(values, window, min_count):
    s1, counts = rolling_sum_count(values, window)
    s2, _ = rolling_sum_count(values * values, window)
    s3, _ = rolling_sum_count(values * values * values, window)
    n = np.maximum(counts, 1)
    mean = s1 / n
    second = s2 / n
    third = s3 / n
    variance = np.maximum(second - mean * mean, 0.0)
    central3 = third - 3.0 * mean * second + 2.0 * mean**3
    result = central3 / np.maximum(variance, 1e-24) ** 1.5
    result[(counts < min_count) | (variance <= 1e-18)] = np.nan
    result[: window - 1] = np.nan
    return result


def rolling_kurtosis(values, window, min_count):
    s1, counts = rolling_sum_count(values, window)
    s2, _ = rolling_sum_count(values * values, window)
    s3, _ = rolling_sum_count(values * values * values, window)
    s4, _ = rolling_sum_count(values**4, window)
    n = np.maximum(counts, 1)
    mean = s1 / n
    second = s2 / n
    third = s3 / n
    fourth = s4 / n
    variance = np.maximum(second - mean * mean, 0.0)
    central4 = fourth - 4.0 * mean * third + 6.0 * mean * mean * second - 3.0 * mean**4
    result = central4 / np.maximum(variance, 1e-24) ** 2 - 3.0
    result[(counts < min_count) | (variance <= 1e-18)] = np.nan
    result[: window - 1] = np.nan
    return result


def daily_returns(close):
    result = np.full_like(close, np.nan)
    result[1:] = close[1:] / close[:-1] - 1.0
    return result


def compute_amihud(arrays):
    returns = daily_returns(arrays["close"])
    amount = arrays["amount"]
    raw = safe_divide(np.abs(returns), amount)
    factor = base.rolling_mean_2d(raw, 20, min_count=16)
    del returns, raw
    gc.collect()
    return factor


def compute_weighted_close_ratio(arrays):
    close = arrays["close"]
    volume = arrays["volume"]
    valid = np.isfinite(close) & np.isfinite(volume) & (volume > 0)
    weighted = np.where(valid, close * volume, 0.0)
    weights = np.where(valid, volume, 0.0)
    weighted_sum = base.rolling_from_cumsum(np.cumsum(weighted, axis=0), 20)
    weight_sum = base.rolling_from_cumsum(np.cumsum(weights, axis=0), 20)
    count = base.rolling_from_cumsum(np.cumsum(valid.astype(np.int32), axis=0), 20)
    vw_close = weighted_sum / np.maximum(weight_sum, 1e-12)
    simple_close = base.rolling_mean_2d(close, 20, min_count=16)
    factor = safe_divide(vw_close, simple_close)
    factor[(count < 16) | (weight_sum <= 0)] = np.nan
    factor[:19] = np.nan
    del valid, weighted, weights, weighted_sum, weight_sum, count, vw_close, simple_close
    gc.collect()
    return factor


def compute_log_volume_kurtosis(arrays):
    values = np.log1p(np.where(arrays["volume"] > 0, arrays["volume"], np.nan))
    factor = rolling_kurtosis(values, 20, min_count=16)
    del values
    gc.collect()
    return factor


def compute_return_skew(arrays):
    returns = daily_returns(arrays["close"])
    factor = rolling_skew(returns, 20, min_count=16)
    del returns
    gc.collect()
    return factor


def compute_price_elasticity_trend(arrays):
    relative_range = safe_divide(arrays["high"] - arrays["low"], arrays["close"])
    raw = safe_divide(relative_range, arrays["amount"])
    mean20 = base.rolling_mean_2d(raw, 20, min_count=16)
    mean120, std120, _ = rolling_mean_std(raw, 120, min_count=96)
    factor = safe_divide(mean20 - mean120, std120)
    del relative_range, raw, mean20, mean120, std120
    gc.collect()
    return factor


def compute_volume_peak_price_position(arrays):
    volume = arrays["volume"]
    mean20, std20, _ = rolling_mean_std(volume, 20, min_count=16)
    previous_mean = np.full_like(mean20, np.nan)
    previous_std = np.full_like(std20, np.nan)
    previous_mean[1:] = mean20[:-1]
    previous_std[1:] = std20[:-1]
    spike = np.isfinite(volume) & (volume > previous_mean + previous_std)

    previous_close = np.full_like(arrays["close"], np.nan)
    previous_close[1:] = arrays["close"][:-1]
    lower = np.fmin(arrays["low"], previous_close)
    upper = np.fmax(arrays["high"], previous_close)
    position = safe_divide(arrays["close"] - lower, upper - lower)
    position = np.clip(position, 0.0, 1.0)

    weighted = np.where(spike & np.isfinite(position), volume * position, 0.0)
    spike_volume = np.where(spike & np.isfinite(position), volume, 0.0)
    spike_count = np.where(spike & np.isfinite(position), 1, 0).astype(np.int32)
    weighted_sum = base.rolling_from_cumsum(np.cumsum(weighted, axis=0), 20)
    volume_sum = base.rolling_from_cumsum(np.cumsum(spike_volume, axis=0), 20)
    count_sum = base.rolling_from_cumsum(np.cumsum(spike_count, axis=0), 20)
    factor = weighted_sum / np.maximum(volume_sum, 1e-12)
    factor[(count_sum < 3) | (volume_sum <= 0)] = np.nan
    factor[:19] = np.nan
    del mean20, std20, previous_mean, previous_std, spike, previous_close, lower, upper
    del position, weighted, spike_volume, spike_count, weighted_sum, volume_sum, count_sum
    gc.collect()
    return factor


def compute_tgd(arrays):
    returns = daily_returns(arrays["close"])
    window = 20
    time_weight = np.arange(1, window + 1, dtype=np.float64)[:, None]
    residual = np.full_like(returns, np.nan)

    for end in range(window, len(returns)):
        sample = returns[end - window + 1 : end + 1]
        positive = np.where(sample > 0, sample, 0.0)
        negative = np.where(sample < 0, -sample, 0.0)
        positive_count = (positive > 0).sum(axis=0)
        negative_count = (negative > 0).sum(axis=0)
        gu = (positive * time_weight).sum(axis=0) / np.maximum(positive.sum(axis=0), 1e-12)
        gd = (negative * time_weight).sum(axis=0) / np.maximum(negative.sum(axis=0), 1e-12)
        valid = (
            np.isfinite(sample).sum(axis=0) >= 16
        ) & (positive_count >= 3) & (negative_count >= 3) & np.isfinite(gu) & np.isfinite(gd)
        if valid.sum() < base.CONFIG["min_universe_stocks"]:
            continue
        x = gu[valid]
        y = gd[valid]
        x_centered = x - x.mean()
        denominator = np.dot(x_centered, x_centered)
        if denominator <= 1e-12:
            continue
        beta = np.dot(x_centered, y - y.mean()) / denominator
        fitted = y.mean() + beta * x_centered
        residual[end, np.flatnonzero(valid)] = y - fitted

    factor = base.rolling_mean_2d(residual, 20, min_count=12)
    del returns, residual
    gc.collect()
    return factor


def compute_volume_ridge_gap_skew(arrays):
    volume = arrays["volume"]
    mean20, std20, _ = rolling_mean_std(volume, 20, min_count=16)
    previous_mean = np.full_like(mean20, np.nan)
    previous_std = np.full_like(std20, np.nan)
    previous_mean[1:] = mean20[:-1]
    previous_std[1:] = std20[:-1]
    spike = np.isfinite(volume) & (volume > previous_mean + previous_std)
    event = spike.copy()
    event[1:] &= ~spike[:-1]
    event[0] = False

    gaps = np.full_like(volume, np.nan)
    last_event = np.full(volume.shape[1], -1, dtype=np.int32)
    for index in range(len(volume)):
        current = np.flatnonzero(event[index])
        if len(current) == 0:
            continue
        seen = last_event[current] >= 0
        seen_stocks = current[seen]
        gaps[index, seen_stocks] = index - last_event[seen_stocks]
        last_event[current] = index

    factor = rolling_skew(gaps, 120, min_count=4)
    del mean20, std20, previous_mean, previous_std, spike, event, gaps, last_event
    gc.collect()
    return factor


def compute_downside_vol(arrays):
    returns = daily_returns(arrays["close"])
    downside_square = np.where(np.isfinite(returns), np.minimum(returns, 0.0) ** 2, np.nan)
    mean_square = base.rolling_mean_2d(downside_square, 20, min_count=16)
    factor = np.sqrt(np.maximum(mean_square, 0.0))
    del returns, downside_square, mean_square
    gc.collect()
    return factor


def compute_cgo_proxy(arrays, window):
    close = arrays["close"]
    vwap = arrays["vwap"].copy()
    fallback = (arrays["high"] + arrays["low"] + close) / 3.0
    vwap[~np.isfinite(vwap)] = fallback[~np.isfinite(vwap)]
    volume = arrays["volume"]
    valid = np.isfinite(vwap) & np.isfinite(volume) & (volume > 0)
    weighted = np.where(valid, vwap * volume, 0.0)
    weights = np.where(valid, volume, 0.0)
    weighted_sum = base.rolling_from_cumsum(np.cumsum(weighted, axis=0), window)
    weight_sum = base.rolling_from_cumsum(np.cumsum(weights, axis=0), window)
    count = base.rolling_from_cumsum(np.cumsum(valid.astype(np.int32), axis=0), window)
    reference_price = weighted_sum / np.maximum(weight_sum, 1e-12)
    factor = safe_divide(close - reference_price, close)
    factor[(count < int(window * 0.8)) | (weight_sum <= 0)] = np.nan
    factor[: window - 1] = np.nan
    del vwap, fallback, valid, weighted, weights, weighted_sum, weight_sum, count, reference_price
    gc.collect()
    return factor


def compute_volume_cv(arrays):
    mean, std, _ = rolling_mean_std(arrays["volume"], 20, min_count=16)
    factor = safe_divide(std, mean)
    del mean, std
    gc.collect()
    return factor


def compute_intraday_return(arrays):
    return safe_divide(arrays["close"], arrays["open"]) - 1.0


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


def build_report(ic_summary, backtest_summary, monotonicity):
    full_ic = ic_summary[ic_summary["period"] == "full"]
    later_bt = backtest_summary[backtest_summary["period"] == "2021_2025"]
    table = full_ic.merge(later_bt, on="factor", suffixes=("_ic", "_bt"))
    table = table.merge(monotonicity, on="factor", how="left")
    table["abs_icir"] = table["icir"].abs()
    table = table.sort_values(["abs_icir", "annual_excess_return"], ascending=False)

    lines = [
        "# 一月因子日线改版回测",
        "",
        "## 排名",
        "",
        "| 因子 | 全期IC | ICIR | 2021—2025 IC | 后段扣费年化 | 后段年化相差 | 最大回撤 | 分组单调程度 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in table.iterrows():
        later_ic = ic_summary[(ic_summary["factor"] == row["factor"]) & (ic_summary["period"] == "2021_2025")].iloc[0]
        lines.append(
            f"| {row['factor']} | {row['ic_mean']:+.4f} | {row['icir']:+.3f} | {later_ic['ic_mean']:+.4f} | "
            f"{row['annual_return']:.2%} | {row['annual_excess_return']:+.2%} | {row['max_drawdown']:.2%} | "
            f"{row['group_return_spearman']:+.3f} |"
        )
    lines.extend(
        [
            "",
            "## 方法",
            "",
            "- 股票范围、过滤条件、未来5日收益、费用和样本划分与原因子库相同。",
            "- 所有版本都是日线改版，不能替代图片中的分钟或逐笔原式。",
            "- 买入高组还是低组只由2016—2020年的IC正负决定。",
        ]
    )
    report_path = os.path.join(OUTPUT_DIR, "日线改版回测总结.md")
    with open(report_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return report_path


def main():
    started = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base.CONFIG["output_dir"] = OUTPUT_DIR
    dates, stocks, arrays = base.load_data()
    future_return, current_eligible, weight_cumsum, weighted_price_cumsum, valid_cumsum = (
        base.prepare_common_arrays(arrays)
    )
    del weight_cumsum, weighted_price_cumsum, valid_cumsum
    gc.collect()

    builders = {
        "D_AMIHUD_ILLIQ_20D": lambda: compute_amihud(arrays),
        "D_WEIGHTED_CLOSE_RATIO_20D": lambda: compute_weighted_close_ratio(arrays),
        "D_LOG_VOLUME_KURT_20D": lambda: compute_log_volume_kurtosis(arrays),
        "D_RETURN_SKEW_20D": lambda: compute_return_skew(arrays),
        "D_PRICE_ELASTICITY_TREND": lambda: compute_price_elasticity_trend(arrays),
        "D_VOLUME_PEAK_PRICE_POS_20D": lambda: compute_volume_peak_price_position(arrays),
        "D_TGD_20D": lambda: compute_tgd(arrays),
        "D_VOLUME_RIDGE_GAP_SKEW_120D": lambda: compute_volume_ridge_gap_skew(arrays),
        "D_DOWNSIDE_VOL_20D": lambda: compute_downside_vol(arrays),
        "D_CGO_PROXY_20D": lambda: compute_cgo_proxy(arrays, 20),
        "D_CGO_PROXY_60D": lambda: compute_cgo_proxy(arrays, 60),
        "D_CGO_PROXY_120D": lambda: compute_cgo_proxy(arrays, 120),
        "D_VOLUME_CV_20D": lambda: compute_volume_cv(arrays),
        "D_INTRADAY_RETURN": lambda: compute_intraday_return(arrays),
    }

    all_ic = []
    all_ic_daily = []
    all_quantile_daily = []
    all_rebalance = []
    all_backtest_summary = []

    for factor_name, builder in builders.items():
        factor_started = time.time()
        print(f"正在计算 {factor_name}...", flush=True)
        factor = builder()
        finite_count = np.isfinite(factor).sum(axis=1)
        print(
            f"  有效股票中位数={int(np.median(finite_count[finite_count > 0])) if (finite_count > 0).any() else 0}",
            flush=True,
        )

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
            f"  全期IC={full_row['ic_mean']:+.4f}，ICIR={full_row['icir']:+.3f}，"
            f"后段IC={later_row['ic_mean']:+.4f}，耗时={time.time() - factor_started:.1f}秒",
            flush=True,
        )
        del factor, finite_count, ic_daily, quantile_daily, rebalance_data
        gc.collect()

    ic_summary = pd.DataFrame(all_ic)
    daily_ic = pd.concat(all_ic_daily, ignore_index=True)
    yearly_ic = build_yearly_ic(daily_ic)
    quantile_daily = pd.concat(all_quantile_daily, ignore_index=True)
    quantile_summary = (
        quantile_daily.groupby(["factor", "group"])
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
    rebalance = pd.concat(all_rebalance, ignore_index=True)
    backtest_summary = pd.DataFrame(all_backtest_summary)
    yearly_backtest = base.build_yearly_backtest(rebalance)
    definitions = pd.DataFrame(
        [{"factor": code, **info} for code, info in FACTOR_INFO.items()]
    )

    outputs = {
        "factor_definitions.csv": definitions,
        "ic_summary.csv": ic_summary,
        "daily_ic.csv": daily_ic,
        "yearly_ic.csv": yearly_ic,
        "quantile_summary.csv": quantile_summary,
        "quantile_monotonicity.csv": monotonicity,
        "rebalance_returns.csv": rebalance,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
    }
    for file_name, frame in outputs.items():
        frame.to_csv(os.path.join(OUTPUT_DIR, file_name), index=False, encoding="utf-8-sig")

    report_path = build_report(ic_summary, backtest_summary, monotonicity)
    print(f"完成：{report_path}", flush=True)
    print(f"总耗时={time.time() - started:.1f}秒", flush=True)


if __name__ == "__main__":
    main()
