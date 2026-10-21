# -*- coding: utf-8 -*-
"""用现有 Qlib 日线数据测试筹码分布成本重心因子。"""

import gc
import os
import time
import warnings

import numpy as np
import pandas as pd
from scipy import stats

import qlib
from qlib.data import D

warnings.filterwarnings("ignore")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = {
    "qlib_uri": r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin",
    "data_start": "2015-01-01",
    "backtest_start": "2016-01-04",
    "backtest_end": "2025-12-31",
    "split_date": "2020-12-31",
    "windows": [20, 60, 120],
    "hold_days": 5,
    "groups": 10,
    "min_universe_stocks": 50,
    "min_avg_amount": 5_000_000,
    "min_price": 3.0,
    "buy_cost": 0.0003,
    "sell_cost": 0.0013,
    "output_dir": os.path.join(BASE_DIR, "results"),
}


def rolling_from_cumsum(cumsum, window):
    """根据累计值计算固定天数内的合计值。"""
    result = cumsum.copy()
    if window < len(cumsum):
        result[window:] = cumsum[window:] - cumsum[:-window]
    return result


def rolling_mean_2d(values, window, min_count=None):
    """按日期方向计算平均值。"""
    if min_count is None:
        min_count = max(window // 2, 1)
    valid = np.isfinite(values)
    sums = np.cumsum(np.where(valid, values, 0.0), axis=0)
    counts = np.cumsum(valid.astype(np.int32), axis=0)
    sums = rolling_from_cumsum(sums, window)
    counts = rolling_from_cumsum(counts, window)
    result = sums / np.maximum(counts, 1)
    result[counts < min_count] = np.nan
    result[: window - 1] = np.nan
    return result


def rolling_extreme(values, window, kind, min_count):
    """计算固定天数内的最高值或最低值。"""
    try:
        import bottleneck as bn

        if kind == "min":
            return bn.move_min(values, window=window, min_count=min_count, axis=0)
        return bn.move_max(values, window=window, min_count=min_count, axis=0)
    except ImportError:
        frame = pd.DataFrame(values)
        roller = frame.rolling(window=window, min_periods=min_count)
        result = roller.min() if kind == "min" else roller.max()
        return result.to_numpy(dtype=np.float64)


def load_data():
    """读取股票范围和日线数据，并转为日期乘股票的二维数组。"""
    print("[1/5] 初始化 Qlib 并读取数据...")
    qlib.init(provider_uri=CONFIG["qlib_uri"], region="cn", kernels=1)

    csi800 = D.instruments("csi800")
    csi1000 = D.instruments("csi1000")
    stocks = list(D.list_instruments(csi800, as_list=True))
    for stock in D.list_instruments(csi1000, as_list=True):
        if stock not in stocks:
            stocks.append(stock)

    fields = ["$close", "$open", "$high", "$low", "$volume", "$vwap", "$amount"]
    data = D.features(
        instruments=stocks,
        fields=fields,
        start_time=CONFIG["data_start"],
        end_time=CONFIG["backtest_end"],
        freq="day",
    )
    data.columns = ["close", "open", "high", "low", "volume", "vwap", "amount"]
    data = data.reset_index().rename(columns={"datetime": "date"})

    dates = pd.DatetimeIndex(sorted(data["date"].unique()))
    actual_stocks = sorted(data["instrument"].unique())
    date_map = {date: i for i, date in enumerate(dates)}
    stock_map = {stock: i for i, stock in enumerate(actual_stocks)}
    date_index = data["date"].map(date_map).to_numpy()
    stock_index = data["instrument"].map(stock_map).to_numpy()

    shape = (len(dates), len(actual_stocks))
    arrays = {}
    for column in ["close", "open", "high", "low", "volume", "vwap", "amount"]:
        array = np.full(shape, np.nan, dtype=np.float64)
        array[date_index, stock_index] = data[column].to_numpy(dtype=np.float64)
        arrays[column] = array

    print(
        f"      {dates[0].date()} 至 {dates[-1].date()}，"
        f"{len(dates)} 个交易日，{len(actual_stocks)} 只股票，{len(data):,} 行"
    )
    del data, date_index, stock_index
    gc.collect()
    return dates, actual_stocks, arrays


def prepare_common_arrays(arrays):
    """准备未来收益、成交额筛选值和成交量累计值。"""
    close = arrays["close"]
    open_price = arrays["open"]
    hold_days = CONFIG["hold_days"]

    future_return = np.full_like(close, np.nan)
    future_return[: -hold_days - 1] = (
        open_price[hold_days + 1 :] / open_price[1:-hold_days] - 1
    )

    # 这份 Qlib 数据的 amount 以千元记录，换算为元后再做筛选。
    amount_yuan = arrays["amount"] * 1000.0
    avg_amount = rolling_mean_2d(amount_yuan, 20, min_count=10)

    vwap = arrays["vwap"].copy()
    fallback_price = (arrays["high"] + arrays["low"] + close) / 3
    vwap[~np.isfinite(vwap)] = fallback_price[~np.isfinite(vwap)]

    valid_weight = np.isfinite(vwap) & np.isfinite(arrays["volume"]) & (arrays["volume"] > 0)
    weights = np.where(valid_weight, arrays["volume"], 0.0)
    weighted_price = np.where(valid_weight, vwap * arrays["volume"], 0.0)
    weight_cumsum = np.cumsum(weights, axis=0)
    weighted_price_cumsum = np.cumsum(weighted_price, axis=0)
    valid_cumsum = np.cumsum(valid_weight.astype(np.int32), axis=0)

    current_eligible = (
        np.isfinite(close)
        & (close >= CONFIG["min_price"])
        & np.isfinite(avg_amount)
        & (avg_amount >= CONFIG["min_avg_amount"])
    )

    eligible_count = current_eligible.sum(axis=1)
    print(
        f"      每日可选股票：中位数 {int(np.median(eligible_count))} 只，"
        f"最多 {int(eligible_count.max())} 只"
    )

    del vwap, fallback_price, valid_weight, weights, weighted_price, amount_yuan, avg_amount
    gc.collect()
    return future_return, current_eligible, weight_cumsum, weighted_price_cumsum, valid_cumsum


def compute_factor(arrays, window, weight_cumsum, weighted_price_cumsum, valid_cumsum):
    """计算一个观察期的日线近似成本重心。"""
    min_count = max(int(window * 0.8), 5)
    rolling_weight = rolling_from_cumsum(weight_cumsum, window)
    rolling_weighted_price = rolling_from_cumsum(weighted_price_cumsum, window)
    rolling_count = rolling_from_cumsum(valid_cumsum, window)

    mean_cost = rolling_weighted_price / np.maximum(rolling_weight, 1e-12)
    mean_cost[(rolling_count < min_count) | (rolling_weight <= 0)] = np.nan
    mean_cost[: window - 1] = np.nan

    lowest = rolling_extreme(arrays["low"], window, "min", min_count)
    highest = rolling_extreme(arrays["high"], window, "max", min_count)
    width = highest - lowest
    factor = (mean_cost - lowest) / width
    factor[(width <= 1e-12) | ~np.isfinite(factor)] = np.nan
    factor = np.clip(factor, 0.0, 1.0)

    del rolling_weight, rolling_weighted_price, rolling_count, mean_cost, lowest, highest, width
    gc.collect()
    return factor


def rank_correlation(values, returns):
    """计算一组股票的等级相关系数。"""
    valid = np.isfinite(values) & np.isfinite(returns)
    if valid.sum() < 50:
        return np.nan
    value_rank = stats.rankdata(values[valid], method="average")
    return_rank = stats.rankdata(returns[valid], method="average")
    return np.corrcoef(value_rank, return_rank)[0, 1]


def calculate_daily_ic(factor, future_return, current_eligible, dates):
    """逐日计算因子与未来五日收益的等级相关系数。"""
    start = dates.searchsorted(pd.Timestamp(CONFIG["backtest_start"]))
    records = []
    for index in range(start, len(dates) - CONFIG["hold_days"] - 1):
        mask = current_eligible[index] & np.isfinite(future_return[index])
        ic = rank_correlation(factor[index, mask], future_return[index, mask])
        if np.isfinite(ic):
            records.append({"date": dates[index], "ic": ic, "stock_count": int(mask.sum())})
    return pd.DataFrame(records)


def summarize_ic(ic_data, factor_name):
    """汇总全期、前半段和后半段的因子表现。"""
    if ic_data.empty:
        raise ValueError(f"{factor_name} 没有可计算的每日 IC，请检查因子和筛选条件")
    split_date = pd.Timestamp(CONFIG["split_date"])
    periods = {
        "full": np.ones(len(ic_data), dtype=bool),
        "2016_2020": ic_data["date"] <= split_date,
        "2021_2025": ic_data["date"] > split_date,
    }
    rows = []
    for period, mask in periods.items():
        values = ic_data.loc[mask, "ic"].dropna()
        if len(values) == 0:
            continue
        mean = values.mean()
        std = values.std(ddof=1)
        rows.append(
            {
                "factor": factor_name,
                "period": period,
                "ic_mean": mean,
                "ic_std": std,
                "icir": mean / std if std > 0 else np.nan,
                "ic_positive_ratio": (values > 0).mean(),
                "t_stat": mean / (std / np.sqrt(len(values))) if std > 0 else np.nan,
                "days": len(values),
                "avg_stock_count": ic_data.loc[mask, "stock_count"].mean(),
            }
        )
    return rows


def calculate_quantile_returns(factor, future_return, current_eligible, dates, factor_name):
    """把股票按因子从低到高分成十组。"""
    start = dates.searchsorted(pd.Timestamp(CONFIG["backtest_start"]))
    records = []
    group_count = CONFIG["groups"]
    for index in range(start, len(dates) - CONFIG["hold_days"] - 1):
        mask = current_eligible[index] & np.isfinite(factor[index]) & np.isfinite(future_return[index])
        stock_indices = np.flatnonzero(mask)
        if len(stock_indices) < CONFIG["min_universe_stocks"]:
            continue
        order = stock_indices[np.argsort(factor[index, stock_indices], kind="mergesort")]
        for group_number, group_indices in enumerate(np.array_split(order, group_count), start=1):
            group_return = np.nanmean(future_return[index, group_indices])
            records.append(
                {
                    "date": dates[index],
                    "factor": factor_name,
                    "group": group_number,
                    "future_5d_return": group_return,
                    "stock_count": len(group_indices),
                }
            )
    return pd.DataFrame(records)


def transition_cost(previous, current):
    """按前后股票名单变化估算本次买卖费用。"""
    previous = set(previous)
    current = set(current)
    if not current:
        return 0.0, 0.0
    previous_weight = 1.0 / len(previous) if previous else 0.0
    current_weight = 1.0 / len(current)
    buys = 0.0
    sells = 0.0
    for stock in previous | current:
        old_weight = previous_weight if stock in previous else 0.0
        new_weight = current_weight if stock in current else 0.0
        if new_weight > old_weight:
            buys += new_weight - old_weight
        else:
            sells += old_weight - new_weight
    cost = buys * CONFIG["buy_cost"] + sells * CONFIG["sell_cost"]
    return cost, (buys + sells) / 2


def group_return(future_returns, stock_indices):
    """计算一个股票组的等权收益。"""
    values = future_returns[stock_indices]
    values = values[np.isfinite(values)]
    return values.mean() if len(values) >= 10 else np.nan


def run_rebalance_test(factor, future_return, current_eligible, arrays, dates, factor_name):
    """每五个交易日换一次股票，分别测试最低组和最高组。"""
    start = dates.searchsorted(pd.Timestamp(CONFIG["backtest_start"]))
    last_signal = len(dates) - CONFIG["hold_days"] - 1
    previous_low = set()
    previous_high = set()
    records = []

    for index in range(start, last_signal, CONFIG["hold_days"]):
        execution_possible = np.isfinite(arrays["open"][index + 1])
        mask = current_eligible[index] & np.isfinite(factor[index]) & execution_possible
        stock_indices = np.flatnonzero(mask)
        if len(stock_indices) < CONFIG["min_universe_stocks"]:
            continue

        order = stock_indices[np.argsort(factor[index, stock_indices], kind="mergesort")]
        count = max(len(order) // CONFIG["groups"], 1)
        low_stocks = set(order[:count].tolist())
        high_stocks = set(order[-count:].tolist())

        low_raw = group_return(future_return[index], np.fromiter(low_stocks, dtype=int))
        high_raw = group_return(future_return[index], np.fromiter(high_stocks, dtype=int))
        benchmark_raw = group_return(future_return[index], stock_indices)
        if not np.isfinite(low_raw) or not np.isfinite(high_raw) or not np.isfinite(benchmark_raw):
            continue

        low_cost, low_turnover = transition_cost(previous_low, low_stocks)
        high_cost, high_turnover = transition_cost(previous_high, high_stocks)
        records.append(
            {
                "date": dates[index],
                "factor": factor_name,
                "low_raw_return": low_raw,
                "low_net_return": low_raw - low_cost,
                "low_turnover": low_turnover,
                "high_raw_return": high_raw,
                "high_net_return": high_raw - high_cost,
                "high_turnover": high_turnover,
                "benchmark_return": benchmark_raw,
                "eligible_stocks": len(stock_indices),
                "group_stocks": count,
            }
        )
        previous_low = low_stocks
        previous_high = high_stocks

    return pd.DataFrame(records)


def return_metrics(returns, benchmark):
    """计算收益、波动和回撤。"""
    returns = np.asarray(returns, dtype=float)
    benchmark = np.asarray(benchmark, dtype=float)
    valid = np.isfinite(returns) & np.isfinite(benchmark)
    returns = returns[valid]
    benchmark = benchmark[valid]
    if len(returns) < 10:
        return {}

    periods_per_year = 252 / CONFIG["hold_days"]
    nav = np.cumprod(1 + returns)
    benchmark_nav = np.cumprod(1 + benchmark)
    years = len(returns) / periods_per_year
    annual_return = nav[-1] ** (1 / years) - 1
    benchmark_annual = benchmark_nav[-1] ** (1 / years) - 1
    annual_volatility = returns.std(ddof=1) * np.sqrt(periods_per_year)
    sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year)
    drawdown = nav / np.maximum.accumulate(nav) - 1
    excess = returns - benchmark
    excess_ir = (
        excess.mean() / excess.std(ddof=1) * np.sqrt(periods_per_year)
        if excess.std(ddof=1) > 0
        else np.nan
    )
    return {
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_annual,
        "annual_excess_return": annual_return - benchmark_annual,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": drawdown.min(),
        "excess_ir": excess_ir,
        "win_rate_vs_benchmark": (returns > benchmark).mean(),
        "periods": len(returns),
    }


def summarize_rebalance(rebalance_data, ic_summary, factor_name):
    """用前半段的相关方向决定买最低组还是最高组。"""
    ic_rows = pd.DataFrame(ic_summary)
    in_sample_ic = ic_rows.loc[
        (ic_rows["factor"] == factor_name) & (ic_rows["period"] == "2016_2020"), "ic_mean"
    ].iloc[0]
    direction = "low" if in_sample_ic < 0 else "high"
    return_column = f"{direction}_net_return"
    turnover_column = f"{direction}_turnover"
    split_date = pd.Timestamp(CONFIG["split_date"])
    periods = {
        "full": np.ones(len(rebalance_data), dtype=bool),
        "2016_2020": rebalance_data["date"] <= split_date,
        "2021_2025": rebalance_data["date"] > split_date,
    }
    rows = []
    for period, mask in periods.items():
        subset = rebalance_data.loc[mask]
        metrics = return_metrics(subset[return_column], subset["benchmark_return"])
        if not metrics:
            continue
        metrics.update(
            {
                "factor": factor_name,
                "period": period,
                "direction": direction,
                "is_ic": in_sample_ic,
                "avg_turnover": subset[turnover_column].mean(),
                "avg_group_stocks": subset["group_stocks"].mean(),
            }
        )
        rows.append(metrics)
    rebalance_data["selected_direction"] = direction
    rebalance_data["selected_net_return"] = rebalance_data[return_column]
    rebalance_data["selected_turnover"] = rebalance_data[turnover_column]
    return rows


def make_plot(ic_summary, quantile_summary, rebalance_all):
    """生成一张便于快速查看的结果图。"""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ic_frame = pd.DataFrame(ic_summary)
    order = [f"CKDP_{window}D" for window in CONFIG["windows"]]
    x = np.arange(len(order))
    width = 0.25
    for offset, period in zip([-width, 0, width], ["full", "2016_2020", "2021_2025"]):
        values = (
            ic_frame[ic_frame["period"] == period]
            .set_index("factor")
            .reindex(order)["ic_mean"]
            .to_numpy()
        )
        axes[0].bar(x + offset, values, width=width, label=period)
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_xticks(x, order)
    axes[0].set_title("Mean daily rank IC")
    axes[0].legend()

    for factor_name in order:
        subset = quantile_summary[quantile_summary["factor"] == factor_name]
        axes[1].plot(subset["group"], subset["mean_5d_return"], marker="o", label=factor_name)
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xticks(range(1, CONFIG["groups"] + 1))
    axes[1].set_xlabel("Factor group: low to high")
    axes[1].set_title("Average next 5-day return")
    axes[1].legend()

    split_date = pd.Timestamp(CONFIG["split_date"])
    benchmark_drawn = False
    for factor_name in order:
        subset = rebalance_all[
            (rebalance_all["factor"] == factor_name) & (rebalance_all["date"] > split_date)
        ].copy()
        subset["nav"] = (1 + subset["selected_net_return"]).cumprod()
        axes[2].plot(subset["date"], subset["nav"], label=factor_name)
        if not benchmark_drawn:
            subset["benchmark_nav"] = (1 + subset["benchmark_return"]).cumprod()
            axes[2].plot(
                subset["date"], subset["benchmark_nav"], color="black", linestyle="--", label="benchmark"
            )
            benchmark_drawn = True
    axes[2].set_title("2021-2025 net value (direction fixed by 2016-2020)")
    axes[2].legend()
    fig.tight_layout()
    output_file = os.path.join(CONFIG["output_dir"], "chip_cost_center_factor_test.png")
    fig.savefig(output_file, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output_file


def percent(value, digits=2):
    return f"{value:.{digits}%}" if np.isfinite(value) else "NA"


def build_yearly_backtest(rebalance_all):
    """汇总每年的扣费收益和同范围等权收益。"""
    rows = []
    data = rebalance_all.copy()
    data["year"] = data["date"].dt.year
    for (factor_name, year), subset in data.groupby(["factor", "year"]):
        strategy_return = (1 + subset["selected_net_return"]).prod() - 1
        benchmark_return = (1 + subset["benchmark_return"]).prod() - 1
        rows.append(
            {
                "factor": factor_name,
                "year": int(year),
                "strategy_return": strategy_return,
                "benchmark_return": benchmark_return,
                "excess_return": strategy_return - benchmark_return,
                "periods": len(subset),
            }
        )
    return pd.DataFrame(rows)


def write_report(
    dates,
    stocks,
    ic_summary,
    quantile_summary,
    quantile_monotonicity,
    backtest_summary,
    yearly_backtest,
):
    """把关键结果写入简短报告。"""
    ic_frame = pd.DataFrame(ic_summary)
    backtest_frame = pd.DataFrame(backtest_summary)
    later_backtest = backtest_frame[backtest_frame["period"] == "2021_2025"].copy()
    best_row = later_backtest.sort_values("annual_excess_return", ascending=False).iloc[0]
    best_factor = best_row["factor"]
    best_years = yearly_backtest[
        (yearly_backtest["factor"] == best_factor) & (yearly_backtest["year"] >= 2021)
    ]
    positive_excess_years = int((best_years["excess_return"] > 0).sum())
    best_monotonicity = quantile_monotonicity.loc[
        quantile_monotonicity["factor"] == best_factor, "group_return_spearman"
    ].iloc[0]

    lines = [
        "# 筹码分布成本重心因子测试",
        "",
        "## 结论",
        "",
        "- **总判断：日线近似版有一定预测关系，但不适合单独使用。** "
        "三个观察期的 IC 都为正，意味着成本重心越靠近筹码区间上沿，未来 5 日收益整体越高。",
        f"- **{best_factor} 是三者中交易结果较好的版本。** 2021—2025 年扣费后年化 "
        f"{percent(best_row['annual_return'])}，同期等权股票范围 {percent(best_row['benchmark_annual_return'])}，"
        f"年化多 {percent(best_row['annual_excess_return'])}；5 年中有 {positive_excess_years} 年跑赢，"
        f"十组收益递增程度为 {best_monotonicity:.3f}。",
        "- 图片末句提到的“成本带宽与未来收益负相关”是另一个指标；本次测试的是公式中的成本重心 CKDP，"
        "两者方向不能直接混用。",
        "",
    ]

    for factor_name in [f"CKDP_{window}D" for window in CONFIG["windows"]]:
        full_ic = ic_frame[(ic_frame["factor"] == factor_name) & (ic_frame["period"] == "full")].iloc[0]
        first_ic = ic_frame[(ic_frame["factor"] == factor_name) & (ic_frame["period"] == "2016_2020")].iloc[0]
        later_ic = ic_frame[(ic_frame["factor"] == factor_name) & (ic_frame["period"] == "2021_2025")].iloc[0]
        later_bt = backtest_frame[
            (backtest_frame["factor"] == factor_name) & (backtest_frame["period"] == "2021_2025")
        ].iloc[0]
        full_bt = backtest_frame[
            (backtest_frame["factor"] == factor_name) & (backtest_frame["period"] == "full")
        ].iloc[0]
        direction_text = "最低组" if later_bt["direction"] == "low" else "最高组"
        lines.append(
            f"- **{factor_name}**：全期 IC {full_ic['ic_mean']:+.4f}，"
            f"2016—2020 年 {first_ic['ic_mean']:+.4f}，2021—2025 年 {later_ic['ic_mean']:+.4f}；"
            f"按前半段确定买入{direction_text}，全期扣费后年化 {percent(full_bt['annual_return'])}，"
            f"后半段扣费后年化 {percent(later_bt['annual_return'])}，"
            f"同期等权股票范围 {percent(later_bt['benchmark_annual_return'])}，"
            f"最大回撤 {percent(later_bt['max_drawdown'])}。"
        )

    lines.extend(
        [
            "",
            "## 计算方法",
            "",
            "图片中的公式为：`CKDP = (平均成本 - 筹码最低价) / (筹码最高价 - 筹码最低价)`。",
            "",
            "现有 Qlib 数据没有逐价位筹码分布和每日换手率，因此本次采用日线近似：",
            "",
            "- 平均成本：近 20、60、120 个交易日的成交量加权均价；",
            "- 筹码最低价、最高价：同期最低价和最高价；",
            "- 股票范围：沪深 800 与中证 1000 的合并范围；",
            "- 因子在收盘后计算，下一交易日开盘执行，持有 5 个交易日；",
            f"- 剔除股价低于 {CONFIG['min_price']:.0f} 元、20 日平均成交额低于 "
            f"{CONFIG['min_avg_amount']/1e6:.0f} 百万元的股票；Qlib 的成交额按千元保存，计算时已换算为元；",
            f"- 买入费用 {CONFIG['buy_cost']:.2%}，卖出费用 {CONFIG['sell_cost']:.2%}。",
            "",
            "## 数据",
            "",
            f"- Qlib 数据目录：`{CONFIG['qlib_uri']}`",
            f"- 实际日期：{dates[0].date()} 至 {dates[-1].date()}",
            f"- 股票数量：{len(stocks)}",
            "- 前半段：2016—2020 年；后半段：2021—2025 年。买入方向只由前半段 IC 正负决定。",
            "",
            "## 十组平均未来 5 日收益",
            "",
            "组号 1 代表因子最低，组号 10 代表因子最高。",
            "",
        ]
    )

    quantile_pivot = quantile_summary.pivot(index="group", columns="factor", values="mean_5d_return")
    lines.append("| 组 | " + " | ".join(quantile_pivot.columns) + " |")
    lines.append("|---:" + "|---:" * len(quantile_pivot.columns) + "|")
    for group, row in quantile_pivot.iterrows():
        lines.append(
            f"| {group} | " + " | ".join(percent(row[column], 3) for column in quantile_pivot.columns) + " |"
        )

    lines.extend(
        [
            "",
            f"## {best_factor} 分年扣费收益",
            "",
            "| 年份 | 因子最高组 | 同范围等权 | 相差 |",
            "|---:|---:|---:|---:|",
        ]
    )
    for _, row in yearly_backtest[yearly_backtest["factor"] == best_factor].iterrows():
        lines.append(
            f"| {int(row['year'])} | {percent(row['strategy_return'])} | "
            f"{percent(row['benchmark_return'])} | {percent(row['excess_return'])} |"
        )

    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 这是日线近似测试，不是原报告分钟成交价分布模型的严格复现；",
            "- Qlib 股票范围沿用已有项目的写法，没有逐日限制为当时的指数成分股；",
            "- 停牌时的成交处理做了简化，本结果更适合判断因子是否有预测关系，不等同于可直接交易的收益；",
            "- 三个观察期都完整列出，没有只保留表现最好的一个。",
            "",
            "详细数字见同目录 CSV 文件，图见 `chip_cost_center_factor_test.png`。",
        ]
    )
    report_file = os.path.join(CONFIG["output_dir"], "report.md")
    with open(report_file, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    return report_file


def main():
    start_time = time.time()
    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    dates, stocks, arrays = load_data()
    print("[2/5] 准备未来收益和筛选条件...")
    future_return, current_eligible, weight_cumsum, weighted_price_cumsum, valid_cumsum = (
        prepare_common_arrays(arrays)
    )

    all_ic = []
    all_ic_daily = []
    all_yearly_ic = []
    all_quantile_daily = []
    all_rebalance = []
    all_backtest_summary = []

    print("[3/5] 计算并测试三个观察期...")
    for window in CONFIG["windows"]:
        factor_name = f"CKDP_{window}D"
        factor_start = time.time()
        print(f"      {factor_name}...")
        factor = compute_factor(
            arrays, window, weight_cumsum, weighted_price_cumsum, valid_cumsum
        )

        ic_daily = calculate_daily_ic(factor, future_return, current_eligible, dates)
        ic_daily["factor"] = factor_name
        ic_rows = summarize_ic(ic_daily, factor_name)
        all_ic.extend(ic_rows)
        all_ic_daily.append(ic_daily)
        for year, subset in ic_daily.groupby(ic_daily["date"].dt.year):
            all_yearly_ic.append(
                {
                    "factor": factor_name,
                    "year": int(year),
                    "ic_mean": subset["ic"].mean(),
                    "ic_std": subset["ic"].std(ddof=1),
                    "days": len(subset),
                }
            )

        quantile_daily = calculate_quantile_returns(
            factor, future_return, current_eligible, dates, factor_name
        )
        all_quantile_daily.append(quantile_daily)

        rebalance_data = run_rebalance_test(
            factor, future_return, current_eligible, arrays, dates, factor_name
        )
        backtest_rows = summarize_rebalance(rebalance_data, all_ic, factor_name)
        all_backtest_summary.extend(backtest_rows)
        all_rebalance.append(rebalance_data)

        full_row = next(row for row in ic_rows if row["period"] == "full")
        later_row = next(row for row in ic_rows if row["period"] == "2021_2025")
        print(
            f"        全期 IC={full_row['ic_mean']:+.4f}，"
            f"后半段 IC={later_row['ic_mean']:+.4f}，耗时 {time.time()-factor_start:.1f} 秒"
        )
        del factor, ic_daily, quantile_daily, rebalance_data
        gc.collect()

    print("[4/5] 保存结果和图片...")
    ic_summary = pd.DataFrame(all_ic)
    ic_daily_all = pd.concat(all_ic_daily, ignore_index=True)
    yearly_ic = pd.DataFrame(all_yearly_ic)
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
    rebalance_all = pd.concat(all_rebalance, ignore_index=True)
    backtest_summary = pd.DataFrame(all_backtest_summary)
    monotonicity_rows = []
    for factor_name, subset in quantile_summary.groupby("factor"):
        monotonicity_rows.append(
            {
                "factor": factor_name,
                "group_return_spearman": stats.spearmanr(
                    subset["group"], subset["mean_5d_return"]
                ).statistic,
            }
        )
    quantile_monotonicity = pd.DataFrame(monotonicity_rows)
    yearly_backtest = build_yearly_backtest(rebalance_all)

    files = {
        "ic_summary.csv": ic_summary,
        "daily_ic.csv": ic_daily_all,
        "yearly_ic.csv": yearly_ic,
        "quantile_summary.csv": quantile_summary,
        "quantile_monotonicity.csv": quantile_monotonicity,
        "quantile_daily_returns.csv": quantile_daily_all,
        "rebalance_returns.csv": rebalance_all,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
    }
    for name, frame in files.items():
        frame.to_csv(os.path.join(CONFIG["output_dir"], name), index=False, encoding="utf-8-sig")

    plot_file = make_plot(ic_summary, quantile_summary, rebalance_all)
    report_file = write_report(
        dates,
        stocks,
        ic_summary,
        quantile_summary,
        quantile_monotonicity,
        backtest_summary,
        yearly_backtest,
    )

    print("[5/5] 完成")
    print(f"      报告: {report_file}")
    print(f"      图片: {plot_file}")
    print(f"      总耗时: {time.time()-start_time:.1f} 秒")


if __name__ == "__main__":
    main()
