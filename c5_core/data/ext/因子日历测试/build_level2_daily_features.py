# -*- coding: utf-8 -*-
"""把 F 盘 Level2 Parquet 汇总成每日每股一行的基础特征。

程序只读取原始 Parquet，不会修改 F 盘文件。输出按交易日拆分，可中断后继续。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_L2_ROOT = Path(
    r"F:\2026(QQ群1097616051后续增量更新)\l2_a_share_parquet"
)
DEFAULT_DAILY_DIR = BASE_DIR / ".codex-artifact" / "2026_rank_check" / "cache" / "base"
DEFAULT_OUTPUT_DIR = BASE_DIR / "level2_calendar_backtest_results" / "daily_features"
DEFAULT_TEMP_DIR = BASE_DIR / "tmp" / "level2_duckdb"

# 这些日期在转换清单里没有通过成交量额核对，不能混入正式结果。
BAD_DATES = {
    "2026-03-12": "成交汇总未通过行情累计量额检查",
    "2026-04-14": "成交汇总未通过行情累计量额检查",
    "2026-05-12": "成交汇总未通过行情累计量额检查",
    "2026-07-06": "成交汇总未通过行情累计量额检查",
    "2026-07-09": "成交汇总未通过行情累计量额检查",
    "2026-07-10": "成交汇总未通过行情累计量额检查",
    "2026-07-13": "成交汇总未通过行情累计量额检查",
    "2026-07-15": "成交汇总未通过行情累计量额检查",
}


@dataclass(frozen=True)
class BuildSettings:
    l2_root: Path = DEFAULT_L2_ROOT
    daily_dir: Path = DEFAULT_DAILY_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    temp_dir: Path = DEFAULT_TEMP_DIR
    start: str = "2026-01-05"
    end: str = "2026-07-16"
    threads: int = 4
    memory_limit: str = "6GB"
    order_file_batch: int = 1
    order_workers: int = 1
    overwrite: bool = False


def _safe_div(numerator: object, denominator: object) -> object:
    """除数无效时返回空值。"""

    if isinstance(numerator, pd.Series) or isinstance(denominator, pd.Series):
        num = pd.Series(numerator, copy=False, dtype="float64")
        den = pd.Series(denominator, copy=False, dtype="float64")
        result = num / den.where(den.ne(0))
        return result.replace([np.inf, -np.inf], np.nan)
    try:
        den = float(denominator)
        if not np.isfinite(den) or den == 0:
            return np.nan
        value = float(numerator) / den
        return value if np.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _spearman(left: pd.Series, right: pd.Series) -> float:
    pair = pd.concat([left, right], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 5 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2:
        return np.nan
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman"))


def _weighted_price_impact(frame: pd.DataFrame) -> float:
    """按五分钟近似式估计上涨与下跌订单冲击差异。

    原图分母印刷有误，这里只生成试算用的 t 值；结果说明会明确标为试算。
    """

    sample = frame[["minute_return", "net_flow_share", "amount"]].replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    if len(sample) < 12:
        return np.nan
    flow = sample["net_flow_share"].to_numpy(dtype=float)
    design = np.column_stack(
        [np.ones(len(sample)), np.where(flow > 0, flow, 0.0), np.where(flow <= 0, flow, 0.0)]
    )
    weights = np.sqrt(np.maximum(sample["amount"].to_numpy(dtype=float), 0.0))
    y = sample["minute_return"].to_numpy(dtype=float)
    valid = np.isfinite(design).all(axis=1) & np.isfinite(y) & np.isfinite(weights) & (weights > 0)
    if valid.sum() < 12 or np.linalg.matrix_rank(design[valid]) < 3:
        return np.nan
    xw = design[valid] * weights[valid, None]
    yw = y[valid] * weights[valid]
    beta, *_ = np.linalg.lstsq(xw, yw, rcond=None)
    residual = yw - xw @ beta
    dof = max(len(residual) - 3, 1)
    try:
        covariance = (residual @ residual / dof) * np.linalg.inv(xw.T @ xw)
        variance = covariance[1, 1] + covariance[2, 2] - 2 * covariance[1, 2]
        scale = math.sqrt(max(float(variance), 0.0))
    except np.linalg.LinAlgError:
        return np.nan
    return _safe_div(beta[1] - beta[2], scale)


def _ar_residual(returns: pd.Series) -> pd.Series:
    """用当日一分钟收益的一阶回归取得非预期收益。"""

    result = pd.Series(np.nan, index=returns.index, dtype="float64")
    pair = pd.DataFrame({"y": returns, "x": returns.shift(1)}).dropna()
    if len(pair) < 12 or pair["x"].nunique() < 2:
        return result
    design = np.column_stack([np.ones(len(pair)), pair["x"].to_numpy(dtype=float)])
    beta, *_ = np.linalg.lstsq(design, pair["y"].to_numpy(dtype=float), rcond=None)
    result.loc[pair.index] = pair["y"].to_numpy(dtype=float) - design @ beta
    return result


def _trade_group_features(group: pd.DataFrame) -> pd.Series:
    """由一只股票一天的分钟成交汇总生成日度特征。"""

    group = group.sort_values("minute_ts").copy()
    close = group["minute_close"].astype(float)
    group["minute_return"] = close.pct_change(fill_method=None)
    group["avg_trade_amount_minute"] = _safe_div(group["amount"], group["trade_count"])
    group["avg_trade_volume_minute"] = _safe_div(group["volume"], group["trade_count"])
    group["trade_vwap"] = _safe_div(group["amount"], group["volume"])
    group["net_flow_share"] = _safe_div(
        group["buy_amount"] - group["sell_amount"], group["amount"]
    )

    amount = float(group["amount"].sum())
    volume = float(group["volume"].sum())
    trade_count = float(group["trade_count"].sum())
    buy_amount = float(group["buy_amount"].sum())
    sell_amount = float(group["sell_amount"].sum())
    buy_volume = float(group["buy_volume"].sum())
    sell_volume = float(group["sell_volume"].sum())
    buy_count = float(group["buy_count"].sum())
    sell_count = float(group["sell_count"].sum())

    avg_amount = group["avg_trade_amount_minute"].replace([np.inf, -np.inf], np.nan)
    avg_volume = group["avg_trade_volume_minute"].replace([np.inf, -np.inf], np.nan)
    returns = group["minute_return"]
    valid_avg = avg_amount.dropna()
    q10_position = np.nan
    if len(valid_avg) >= 10:
        lower = float(valid_avg.min())
        upper = float(valid_avg.max())
        q10_position = _safe_div(float(valid_avg.quantile(0.10)) - lower, upper - lower)

    standardized = (avg_volume - avg_volume.mean()) / avg_volume.std(ddof=0)
    avg_volume_diff_std = float(standardized.diff().std(ddof=0)) if standardized.notna().sum() >= 5 else np.nan

    top_avg = avg_amount >= avg_amount.quantile(0.70)
    high_volume = group["volume"].eq(group["volume"].max())
    down = returns < 0
    bottom_return = returns <= returns.quantile(0.10)
    pv_divergence = group["tick_price_volume_corr"] <= group["tick_price_volume_corr"].quantile(0.50)

    overall_avg_trade_amount = _safe_div(amount, trade_count)
    down_sell_avg = _safe_div(
        group.loc[down, "sell_amount"].sum(), group.loc[down, "sell_count"].sum()
    )
    high_sell_avg = _safe_div(
        group.loc[high_volume, "sell_amount"].sum(), group.loc[high_volume, "sell_count"].sum()
    )
    pv_sell_avg = _safe_div(
        group.loc[pv_divergence, "sell_amount"].sum(), group.loc[pv_divergence, "sell_count"].sum()
    )
    bottom_sell_avg = _safe_div(
        group.loc[bottom_return, "sell_amount"].sum(), group.loc[bottom_return, "sell_count"].sum()
    )

    residual = _ar_residual(returns)
    negative_surprise = residual < 0
    positive_surprise = residual > 0
    informed_buy = _safe_div(group.loc[negative_surprise, "buy_amount"].sum(), amount)
    informed_sell = _safe_div(group.loc[positive_surprise, "sell_amount"].sum(), amount)
    dpin_parts = []
    if negative_surprise.any():
        dpin_parts.append(
            _safe_div(group.loc[negative_surprise, "buy_count"].sum(), group.loc[negative_surprise, "trade_count"].sum())
        )
    if positive_surprise.any():
        dpin_parts.append(
            _safe_div(group.loc[positive_surprise, "sell_count"].sum(), group.loc[positive_surprise, "trade_count"].sum())
        )
    dpin = float(np.nanmean(dpin_parts)) if dpin_parts else np.nan
    volume_median = float(group["volume"].median())
    small_period = group["volume"] < volume_median
    large_period = group["volume"] >= volume_median

    def state_probability(mask: pd.Series) -> float:
        values = []
        for state, side in ((negative_surprise, "buy_count"), (positive_surprise, "sell_count")):
            selected = mask & state
            if selected.any():
                values.append(_safe_div(group.loc[selected, side].sum(), group.loc[selected, "trade_count"].sum()))
        return float(np.nanmean(values)) if values else np.nan

    return pd.Series(
        {
            "trade_amount": amount,
            "trade_volume": volume,
            "trade_count": trade_count,
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "active_buy_share": _safe_div(buy_amount, amount),
            "avg_trade_amount": overall_avg_trade_amount,
            "trade_amount_q10_position": q10_position,
            "avg_trade_volume_diff_std": avg_volume_diff_std,
            "mte_daily": _spearman(avg_amount, close),
            "mts_daily": _spearman(avg_amount, group["amount"]),
            "avg_trade_volume_return_corr": _spearman(avg_volume, returns),
            "large_order_push_return": float(np.nansum(np.log1p(returns[top_avg].clip(lower=-0.999999)))),
            "large_net_inflow_rate": _safe_div(
                (group.loc[top_avg, "buy_amount"] - group.loc[top_avg, "sell_amount"]).sum(),
                group.loc[top_avg, "amount"].sum(),
            ),
            "avg_outflow_trade_amount_ratio": _safe_div(down_sell_avg, overall_avg_trade_amount),
            "high_volume_avg_trade_amount": _safe_div(
                group.loc[high_volume, "amount"].sum(), group.loc[high_volume, "trade_count"].sum()
            ),
            "high_volume_sell_avg_trade_amount": high_sell_avg,
            "pv_divergence_avg_trade_amount": _safe_div(
                group.loc[pv_divergence, "amount"].sum(), group.loc[pv_divergence, "trade_count"].sum()
            ),
            "pv_divergence_sell_avg_trade_amount": pv_sell_avg,
            "down_avg_trade_amount": _safe_div(bottom_sell_avg, overall_avg_trade_amount),
            "price_impact_bias": _weighted_price_impact(group),
            "informed_buy_share": informed_buy,
            "informed_sell_share": informed_sell,
            "dpin": dpin,
            "small_dpin": state_probability(small_period),
            "large_dpin": state_probability(large_period),
            "raw_trade_imbalance": _safe_div(buy_count - sell_count, trade_count),
        }
    )


def aggregate_trade_minutes(minute: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拆分集合竞价与连续竞价，并返回日度成交特征和分钟明细。"""

    if minute.empty:
        return pd.DataFrame(columns=["code"]), minute
    minute = minute.copy()
    minute["minute_ts"] = pd.to_datetime(minute["minute_ts"])
    minute["clock"] = minute["minute_ts"].dt.hour * 100 + minute["minute_ts"].dt.minute
    auction = minute[minute["clock"].between(915, 929)]
    close_five = minute[minute["clock"].between(1455, 1500)]
    regular = minute[
        minute["clock"].between(930, 1130) | minute["clock"].between(1300, 1500)
    ].copy()
    daily = regular.groupby("code", sort=False, group_keys=False).apply(
        _trade_group_features, include_groups=False
    ).reset_index()
    open_volume = auction.groupby("code")["volume"].sum().rename("open_auction_volume")
    close_volume = close_five.groupby("code")["volume"].sum().rename("close_5m_volume")
    daily = daily.merge(open_volume, on="code", how="left").merge(close_volume, on="code", how="left")
    daily[["open_auction_volume", "close_5m_volume"]] = daily[
        ["open_auction_volume", "close_5m_volume"]
    ].fillna(0.0)

    regular = regular.sort_values(["code", "minute_ts"])
    regular["minute_return"] = regular.groupby("code", sort=False)["minute_close"].pct_change(
        fill_method=None
    )
    regular["trade_vwap"] = _safe_div(regular["amount"], regular["volume"])
    return daily, regular


def _simulate_ask_cost_frame(frame: pd.DataFrame, cash: pd.Series) -> pd.Series:
    """批量用五档卖盘模拟买入，五档不足时按最后一档价格成交。"""

    cash_values = cash.to_numpy(dtype=float)
    valid_cash = np.isfinite(cash_values) & (cash_values > 0)
    remaining = np.where(valid_cash, cash_values, 0.0)
    shares = np.zeros(len(frame), dtype=float)
    spent = np.zeros(len(frame), dtype=float)
    last_price = np.full(len(frame), np.nan, dtype=float)
    for level in range(1, 6):
        price = frame[f"ask_price_{level}"].to_numpy(dtype=float)
        quantity = frame[f"ask_volume_{level}"].to_numpy(dtype=float)
        valid = np.isfinite(price) & (price > 0) & np.isfinite(quantity) & (quantity > 0)
        last_price = np.where(valid, price, last_price)
        available_cash = np.where(valid, price * quantity, 0.0)
        take_cash = np.minimum(remaining, available_cash)
        shares += np.divide(take_cash, price, out=np.zeros(len(frame)), where=valid)
        spent += take_cash
        remaining -= take_cash
    extrapolate = (remaining > 0) & np.isfinite(last_price) & (last_price > 0)
    shares += np.divide(
        remaining,
        last_price,
        out=np.zeros(len(frame)),
        where=extrapolate,
    )
    spent += np.where(extrapolate, remaining, 0.0)
    result = np.divide(
        spent,
        shares,
        out=np.full(len(frame), np.nan),
        where=valid_cash & (shares > 0),
    )
    return pd.Series(result, index=frame.index)


def _quote_group_features(group: pd.DataFrame) -> pd.Series:
    group = group.sort_values("minute_ts").copy()
    for level in range(1, 6):
        for side in ("ask", "bid"):
            group[f"{side}_price_{level}"] = group[f"{side}_price_{level}_x1e4"].astype(float) / 10000.0
    ask1 = group["ask_price_1"]
    bid1 = group["bid_price_1"]
    valid = (ask1 > 0) & (bid1 > 0)
    group = group[valid].copy()
    if len(group) < 5:
        return pd.Series(dtype="float64")
    group["mid"] = (group["ask_price_1"] + group["bid_price_1"]) / 2.0
    group["spread"] = 2.0 * (group["ask_price_1"] - group["bid_price_1"]) / (
        group["ask_price_1"] + group["bid_price_1"]
    )
    group["mid_return"] = group["mid"].pct_change(fill_method=None)

    weights = np.array([0.5 ** level for level in range(5)], dtype=float)
    weights /= weights.sum()
    soir = np.zeros(len(group), dtype=float)
    price_ratio_bid = np.zeros(len(group), dtype=float)
    price_ratio_ask = np.zeros(len(group), dtype=float)
    slope = np.zeros(len(group), dtype=float)
    ask_depth = np.zeros(len(group), dtype=float)
    bid_depth = np.zeros(len(group), dtype=float)
    mofi = np.zeros(len(group), dtype=float)
    net_bid_change = np.zeros(len(group), dtype=float)
    weighted_price_count = np.zeros(len(group), dtype=float)
    previous = group.shift(1)

    for offset, level in enumerate(range(1, 6)):
        w = weights[offset]
        av = group[f"ask_volume_{level}"].astype(float)
        bv = group[f"bid_volume_{level}"].astype(float)
        ap = group[f"ask_price_{level}"].astype(float)
        bp = group[f"bid_price_{level}"].astype(float)
        denom = bv + av
        soir += w * np.divide(
            bv - av, denom, out=np.zeros(len(group), dtype=float), where=denom.to_numpy() != 0
        )
        price_ratio_bid += w * bp.to_numpy()
        price_ratio_ask += w * ap.to_numpy()
        log_volume = np.log1p(np.maximum(av.to_numpy() + bv.to_numpy(), 0.0))
        log_gap = np.log(np.maximum(ap.to_numpy(), 1e-12)) - np.log(np.maximum(bp.to_numpy(), 1e-12))
        slope += w * np.divide(log_gap, log_volume, out=np.zeros(len(group)), where=log_volume > 0)
        ask_distance = ap.to_numpy() - group["mid"].to_numpy()
        bid_distance = group["mid"].to_numpy() - bp.to_numpy()
        ask_depth += np.divide(av.to_numpy(), ask_distance, out=np.zeros(len(group)), where=ask_distance > 0)
        bid_depth += np.divide(bv.to_numpy(), bid_distance, out=np.zeros(len(group)), where=bid_distance > 0)

        pav = previous[f"ask_volume_{level}"].astype(float)
        pbv = previous[f"bid_volume_{level}"].astype(float)
        pap = previous[f"ask_price_{level}"]
        pbp = previous[f"bid_price_{level}"]
        bid_ofi = np.where(bp > pbp, bv, np.where(bp == pbp, bv - pbv, -pbv))
        ask_ofi = np.where(ap < pap, av, np.where(ap == pap, av - pav, -pav))
        level_ofi = np.nan_to_num(bid_ofi, nan=0.0) - np.nan_to_num(ask_ofi, nan=0.0)
        mofi += w * level_ofi
        net_bid_change += np.nan_to_num((bv - pbv) - (av - pav), nan=0.0)
        weighted_price_count += w

    group["five_level_soir_row"] = soir
    group["bidask_price_ratio_row"] = np.divide(
        price_ratio_bid - price_ratio_ask,
        price_ratio_bid + price_ratio_ask,
        out=np.full(len(group), np.nan),
        where=(price_ratio_bid + price_ratio_ask) != 0,
    )
    group["multilevel_order_slope_row"] = slope
    group["ask_depth_row"] = ask_depth
    group["bid_depth_row"] = bid_depth
    group["mofi_row"] = mofi
    group["net_bid_change_row"] = net_bid_change
    group["average_depth_row"] = (
        group["ask_volume_1"].astype(float) + group["bid_volume_1"].astype(float)
    ) / 2.0
    group["effective_depth_row"] = np.minimum(
        group["ask_volume_1"].astype(float), group["bid_volume_1"].astype(float)
    )

    ask_value = sum(
        group[f"ask_price_{level}"] * group[f"ask_volume_{level}"].astype(float)
        for level in range(1, 6)
    )
    bid_value = sum(
        group[f"bid_price_{level}"] * group[f"bid_volume_{level}"].astype(float)
        for level in range(1, 6)
    )
    ask_volume = sum(group[f"ask_volume_{level}"].astype(float) for level in range(1, 6))
    bid_volume = sum(group[f"bid_volume_{level}"].astype(float) for level in range(1, 6))
    ask_vwap = _safe_div(ask_value, ask_volume)
    bid_vwap = _safe_div(bid_value, bid_volume)
    group["marginal_cost_row"] = (
        _safe_div(ask_vwap - group["mid"], group["mid"])
        + _safe_div(group["mid"] - bid_vwap, group["mid"])
    ) / 2.0

    if "minute_amount" in group.columns:
        cash = group["minute_amount"].astype(float) * 0.01
        execution = _simulate_ask_cost_frame(group, cash)
        group["liq_premium_row"] = _safe_div(execution - group["mid"], group["mid"])
    else:
        group["liq_premium_row"] = np.nan

    last = group.iloc[-1]
    open_mask = group["minute_ts"].dt.hour.eq(9) & group["minute_ts"].dt.minute.between(30, 59)
    return pd.Series(
        {
            "close_spread": float(last["spread"]),
            "spread_mean": float(group["spread"].mean()),
            "midprice_change": float(group["mid_return"].mean()),
            "midprice_change_skew": float(group["mid_return"].skew()),
            "midprice_change_max": float(group["mid_return"].max()),
            "five_level_soir": float(group["five_level_soir_row"].mean()),
            "mofi": float(group["mofi_row"].mean()),
            "bidask_price_ratio": float(group["bidask_price_ratio_row"].mean()),
            "multilevel_order_slope": float(group["multilevel_order_slope_row"].mean()),
            "ask_depth": float(group["ask_depth_row"].replace([np.inf, -np.inf], np.nan).mean()),
            "bid_depth": float(group["bid_depth_row"].replace([np.inf, -np.inf], np.nan).mean()),
            "average_depth": float(group["average_depth_row"].mean()),
            "effective_depth": float(group["effective_depth_row"].mean()),
            "net_bid_change": float(group["net_bid_change_row"].mean()),
            "order_trade_corr": _spearman(
                group["net_bid_change_row"], group.get("minute_return", pd.Series(index=group.index, dtype=float))
            ),
            "open_net_bid_change": float(group.loc[open_mask, "net_bid_change_row"].sum()),
            "marginal_trading_cost": float(group["marginal_cost_row"].mean()),
            "liq_premium_equal": float(group["liq_premium_row"].mean()),
            "liq_premium_weighted": float(
                np.average(
                    group["liq_premium_row"].fillna(0.0),
                    weights=np.arange(1, len(group) + 1, dtype=float),
                )
            ),
        }
    )


def aggregate_quotes(quotes: pd.DataFrame, minute: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame(columns=["code"])
    quotes = quotes.copy()
    quotes["minute_ts"] = pd.to_datetime(quotes["minute_ts"])
    trade_columns = minute[["code", "minute_ts", "amount", "trade_vwap", "minute_return"]].rename(
        columns={"amount": "minute_amount", "trade_vwap": "minute_trade_vwap"}
    )
    quotes = quotes.merge(trade_columns, on=["code", "minute_ts"], how="left")
    quotes = quotes.sort_values(["code", "minute_ts"])
    quotes["previous_mid"] = (
        quotes["ask_price_1_x1e4"].astype(float) + quotes["bid_price_1_x1e4"].astype(float)
    ) / 20000.0
    quotes["previous_mid"] = quotes.groupby("code", sort=False)["previous_mid"].shift(1)
    current_mid = (
        quotes["ask_price_1_x1e4"].astype(float) + quotes["bid_price_1_x1e4"].astype(float)
    ) / 20000.0
    quotes["market_price_bias_row"] = quotes["minute_trade_vwap"] - (
        current_mid + quotes["previous_mid"]
    ) / 2.0
    quotes["prsi_row"] = _safe_div(
        (quotes["minute_trade_vwap"] - quotes.groupby("code")["previous_mid"].shift(-1)).abs(),
        current_mid,
    )
    # 部分停牌股票一天只有极少盘口行。逐组收集可以明确跳过这些空结果，
    # 也避免不同 pandas 版本在空 Series 上丢失 code 索引。
    rows: list[dict] = []
    for code, group in quotes.groupby("code", sort=False):
        values = _quote_group_features(group)
        if values.empty:
            continue
        row = values.to_dict()
        row["code"] = code
        rows.append(row)
    daily = pd.DataFrame(rows)
    if daily.empty:
        return pd.DataFrame(columns=["code"])
    extra = quotes.groupby("code", sort=False).agg(
        market_price_bias=("market_price_bias_row", "mean"),
        prsi=("prsi_row", "mean"),
    ).reset_index()
    return daily.merge(extra, on="code", how="left")


def _market_minute_features(minute: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """补充需要全市场同一分钟比较的三个指标。"""

    if minute.empty or daily.empty:
        return daily
    minute = minute.copy()
    market = minute.groupby("minute_ts", sort=False).agg(
        market_amount=("amount", "sum"), market_buy_amount=("buy_amount", "sum")
    ).reset_index()
    hot_minutes = set(market.nlargest(min(23, len(market)), "market_amount")["minute_ts"])
    merged = minute.merge(market, on="minute_ts", how="left")
    merged["market_buy_share"] = _safe_div(merged["market_buy_amount"], merged["market_amount"])
    merged["stock_buy_share"] = _safe_div(merged["buy_amount"], merged["amount"])
    merged["active_buy_gap"] = merged["stock_buy_share"] - merged["market_buy_share"]
    merged["hot_sell_amount"] = np.where(
        merged["minute_ts"].isin(hot_minutes), merged["sell_amount"], 0.0
    )
    market_features = merged.groupby("code", sort=False).agg(
        active_buy_singularity=("active_buy_gap", "std"),
        siphon_sell_amount=("hot_sell_amount", "sum"),
        stock_buy_share_mean=("stock_buy_share", "mean"),
    ).reset_index()
    market_features["siphon_effect"] = _safe_div(
        market_features["siphon_sell_amount"],
        market_features["siphon_sell_amount"].sum(),
    )
    daily = daily.merge(market_features, on="code", how="left")
    probability = _safe_div(daily["buy_count"], daily["trade_count"])
    market_probability = _safe_div(daily["buy_count"].sum(), daily["trade_count"].sum())
    adjustment = np.sqrt(
        np.maximum(market_probability * (1.0 - market_probability), 0.0)
        / np.maximum(daily["trade_count"].to_numpy(dtype=float), 1.0)
    )
    daily["herding"] = (probability - market_probability).abs() - adjustment
    daily["residual_noniso_imbalance"] = daily["raw_trade_imbalance"] - float(
        np.nanmean(daily["raw_trade_imbalance"])
    )
    return daily


class Level2Reader:
    """逐日读取三类 Parquet。"""

    def __init__(self, settings: BuildSettings, universe: pd.DataFrame) -> None:
        self.settings = settings
        self.universe = universe[["code"]].drop_duplicates().copy()
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect()
        self.connection.execute(f"SET threads={int(settings.threads)}")
        self.connection.execute(f"SET memory_limit='{settings.memory_limit}'")
        self.connection.execute(
            "SET temp_directory='" + settings.temp_dir.resolve().as_posix().replace("'", "''") + "'"
        )
        self.connection.register("universe", self.universe)

    def close(self) -> None:
        self.connection.close()

    def _glob(self, kind: str, date: str) -> str:
        return (self.settings.l2_root / kind / f"trade_date={date}" / "*.parquet").as_posix()

    def minute_trades(self, date: str) -> pd.DataFrame:
        source = self._glob("trades", date).replace("'", "''")
        query = f"""
            SELECT t.code,
                   date_trunc('minute', t.event_ts) AS minute_ts,
                   arg_max(t.trade_price_x1e4, t.event_ts) / 10000.0 AS minute_close,
                   sum(t.trade_quantity)::DOUBLE AS volume,
                   sum(t.trade_price_x1e4::DOUBLE * t.trade_quantity / 10000.0) AS amount,
                   count(*)::BIGINT AS trade_count,
                   sum(CASE WHEN t.bs_flag='B' THEN t.trade_quantity ELSE 0 END)::DOUBLE AS buy_volume,
                   sum(CASE WHEN t.bs_flag='S' THEN t.trade_quantity ELSE 0 END)::DOUBLE AS sell_volume,
                   sum(CASE WHEN t.bs_flag='B' THEN t.trade_price_x1e4::DOUBLE*t.trade_quantity/10000.0 ELSE 0 END) AS buy_amount,
                   sum(CASE WHEN t.bs_flag='S' THEN t.trade_price_x1e4::DOUBLE*t.trade_quantity/10000.0 ELSE 0 END) AS sell_amount,
                   count(*) FILTER (WHERE t.bs_flag='B')::BIGINT AS buy_count,
                   count(*) FILTER (WHERE t.bs_flag='S')::BIGINT AS sell_count,
                   corr(t.trade_price_x1e4::DOUBLE, t.trade_quantity::DOUBLE) AS tick_price_volume_corr
            FROM read_parquet('{source}') t
            JOIN universe u USING(code)
            WHERE t.trade_price_x1e4 > 0 AND t.trade_quantity > 0
            GROUP BY t.code, minute_ts
        """
        return self.connection.execute(query).fetchdf()

    def quote_minutes(self, date: str) -> pd.DataFrame:
        source = self._glob("quotes", date).replace("'", "''")
        fields = []
        for level in range(1, 6):
            for side in ("ask", "bid"):
                fields.append(
                    f"arg_max(q.{side}_price_{level}_x1e4, q.event_ts) AS {side}_price_{level}_x1e4"
                )
                fields.append(
                    f"arg_max(q.{side}_volume_{level}, q.event_ts) AS {side}_volume_{level}"
                )
        query = f"""
            SELECT q.code, date_trunc('minute', q.event_ts) AS minute_ts,
                   {', '.join(fields)}
            FROM read_parquet('{source}') q
            JOIN universe u USING(code)
            WHERE CAST(q.event_ts AS TIME) BETWEEN TIME '09:30:00' AND TIME '15:00:59'
            GROUP BY q.code, minute_ts
        """
        return self.connection.execute(query).fetchdf()

    def order_events(self, date: str) -> pd.DataFrame:
        source = self._glob("orders", date).replace("'", "''")
        query = f"""
            SELECT o.code,
                   count(*) FILTER (WHERE o.order_side='B' AND ((o.code LIKE 'sh%' AND o.order_type='A') OR (o.code LIKE 'sz%' AND o.order_type='0'))) AS buy_order_arrivals,
                   count(*) FILTER (WHERE o.order_side='S' AND ((o.code LIKE 'sh%' AND o.order_type='A') OR (o.code LIKE 'sz%' AND o.order_type='0'))) AS sell_order_arrivals,
                   count(*) FILTER (WHERE o.order_side='B' AND ((o.code LIKE 'sh%' AND o.order_type='D') OR (o.code LIKE 'sz%' AND o.order_type IN ('1','U')))) AS buy_order_cancels,
                   count(*) FILTER (WHERE o.order_side='S' AND ((o.code LIKE 'sh%' AND o.order_type='D') OR (o.code LIKE 'sz%' AND o.order_type IN ('1','U')))) AS sell_order_cancels
            FROM read_parquet('{source}') o
            JOIN universe u USING(code)
            GROUP BY o.code
        """
        return self.connection.execute(query).fetchdf()

    def order_trade_metrics(self, date: str, prices: pd.DataFrame) -> pd.DataFrame:
        """按成交表中的买卖单号合成订单，再按股票汇总。"""

        self.connection.register("daily_prices", prices[["code", "open", "close"]])
        queries: list[str] = []
        paths = sorted((self.settings.l2_root / "trades" / f"trade_date={date}").glob("*.parquet"))
        batch_size = max(int(self.settings.order_file_batch), 1)
        for offset in range(0, len(paths), batch_size):
            batch = paths[offset : offset + batch_size]
            sources = [path.as_posix().replace("'", "''") for path in batch]
            if len(sources) == 1:
                relation = f"read_parquet('{sources[0]}')"
            else:
                relation = "read_parquet([" + ",".join(f"'{source}'" for source in sources) + "])"
            query = f"""
                WITH normal AS NOT MATERIALIZED (
                    SELECT t.code, t.event_ts, t.bs_flag,
                           t.trade_price_x1e4 / 10000.0 AS price,
                           t.trade_quantity::DOUBLE AS qty,
                           t.trade_price_x1e4::DOUBLE*t.trade_quantity/10000.0 AS amount,
                           t.bid_order_id, t.ask_order_id, p.open AS day_open, p.close AS day_close
                    FROM {relation} t
                    JOIN universe u USING(code)
                    JOIN daily_prices p USING(code)
                    WHERE t.trade_price_x1e4 > 0 AND t.trade_quantity > 0
                ), expanded AS (
                    SELECT n.*, x.side, x.order_id
                    FROM normal n
                    CROSS JOIN LATERAL (VALUES ('B', n.bid_order_id), ('S', n.ask_order_id)) x(side, order_id)
                    WHERE x.order_id > 0
                ), per_order AS MATERIALIZED (
                    SELECT code, side, order_id, any_value(day_open) AS day_open, any_value(day_close) AS day_close,
                           sum(amount) AS order_amount, sum(qty) AS order_qty,
                           sum(CASE WHEN bs_flag=side THEN amount ELSE 0 END) AS active_amount,
                           sum(CASE WHEN bs_flag=side THEN qty ELSE 0 END) AS active_qty,
                           sum(CASE WHEN bs_flag=side AND CAST(event_ts AS TIME) BETWEEN TIME '09:30:00' AND TIME '10:00:00' THEN amount ELSE 0 END) AS open_active_amount,
                           sum(CASE WHEN side='B' AND bs_flag='B' AND price>day_close THEN qty ELSE 0 END) AS buy_loss_qty,
                           sum(CASE WHEN side='S' AND bs_flag='S' AND price<day_close THEN qty ELSE 0 END) AS sell_rebound_qty,
                           datediff('millisecond', min(event_ts), max(event_ts))/1000.0 AS duration_seconds,
                           arg_min(price, event_ts) AS first_price, arg_max(price, event_ts) AS last_price
                    FROM expanded GROUP BY code, side, order_id
                ), stats AS (
                    SELECT code,
                           quantile_cont(order_amount, 0.70) AS q70,
                           avg(ln(greatest(order_amount, 0.01))) AS log_mean,
                           stddev_pop(ln(greatest(order_amount, 0.01))) AS log_std,
                           quantile_cont(duration_seconds, 0.50) AS duration_median,
                           sum(order_amount)/2.0 AS daily_amount
                    FROM per_order
                    GROUP BY code
                )
                SELECT p.code,
                       sum(CASE WHEN side='B' THEN order_amount ELSE 0 END) AS buy_order_amount,
                       sum(CASE WHEN side='S' THEN order_amount ELSE 0 END) AS sell_order_amount,
                       sum(CASE WHEN side='B' AND order_amount<40000 THEN order_amount ELSE 0 END) AS small_buy_amount_40k,
                       sum(CASE WHEN side='S' AND order_amount<40000 THEN order_amount ELSE 0 END) AS small_sell_amount_40k,
                       sum(CASE WHEN side='B' AND order_amount<50000 THEN active_amount ELSE 0 END) AS small_active_buy_amount_50k,
                       sum(CASE WHEN side='B' AND order_amount>=q70 THEN order_amount ELSE 0 END) AS large_buy_amount,
                       sum(CASE WHEN side='S' AND order_amount>=q70 THEN order_amount ELSE 0 END) AS large_sell_amount,
                       sum(CASE WHEN side='B' AND order_amount>daily_amount*0.01 THEN order_amount ELSE 0 END) AS super_buy_amount,
                       sum(CASE WHEN side='S' AND order_amount>daily_amount*0.01 THEN order_amount ELSE 0 END) AS super_sell_amount,
                       sum(CASE WHEN side='B' AND order_amount>=q70 AND order_amount<=daily_amount*0.01 THEN order_amount ELSE 0 END) AS ordinary_large_buy_amount,
                       sum(CASE WHEN side='S' AND order_amount>=q70 AND order_amount<=daily_amount*0.01 THEN order_amount ELSE 0 END) AS ordinary_large_sell_amount,
                       sum(CASE WHEN side='B' AND order_amount>=q70 THEN active_amount ELSE 0 END) AS active_large_buy_amount,
                       sum(CASE WHEN side='S' AND order_amount>=q70 THEN active_amount ELSE 0 END) AS active_large_sell_amount,
                       sum(CASE WHEN side='B' AND order_amount>=exp(log_mean+log_std) THEN open_active_amount ELSE 0 END) AS open_large_active_buy_amount,
                       sum(CASE WHEN side='S' AND order_amount>=exp(log_mean+log_std) THEN open_active_amount ELSE 0 END) AS open_large_active_sell_amount,
                       sum(CASE WHEN side='B' THEN order_amount*order_amount ELSE 0 END)/nullif(pow(sum(CASE WHEN side='B' THEN order_amount ELSE 0 END),2),0) AS buy_order_hhi,
                       sum(CASE WHEN side='B' AND order_amount<50000 THEN active_amount ELSE 0 END)/nullif(sum(CASE WHEN side='B' THEN active_amount ELSE 0 END),0) AS active_flexible_chip,
                       sum(CASE WHEN side='B' AND order_amount<50000 THEN active_amount*(day_close/nullif(order_amount/nullif(order_qty,0),0)-1) ELSE 0 END)/nullif(sum(CASE WHEN side='B' THEN active_amount ELSE 0 END),0) AS profit_adjusted_active_flexible_chip,
                       sum(CASE WHEN side='B' AND order_amount<50000 THEN active_amount*((order_amount/nullif(order_qty,0))/nullif(day_open,0)-1) ELSE 0 END)/nullif(sum(CASE WHEN side='B' THEN active_amount ELSE 0 END),0) AS open_adjusted_active_flexible_chip,
                       sum(CASE WHEN order_amount>daily_amount*0.01 AND active_amount>0 AND first_price>0 AND last_price>0 THEN ln(last_price/first_price) ELSE 0 END) AS super_order_price_move,
                       sum(CASE WHEN order_amount>=q70 AND order_amount<=daily_amount*0.01 AND active_amount>0 AND first_price>0 AND last_price>0 THEN ln(last_price/first_price) ELSE 0 END) AS big_order_price_move_ex_super,
                       sum(CASE WHEN order_amount>=q70 AND duration_seconds>=duration_median THEN active_amount ELSE 0 END)/nullif(sum(active_amount),0) AS long_big_order_composite,
                       sum(CASE WHEN side='S' AND order_amount<40000 AND order_qty>0 AND order_amount/order_qty<day_close THEN sell_rebound_qty*(day_close-order_amount/order_qty)/nullif(day_close,0) ELSE 0 END)/nullif(sum(CASE WHEN side='S' AND order_amount<40000 THEN sell_rebound_qty ELSE 0 END),0) AS small_sell_rebound_deviation,
                       sum(buy_loss_qty)/nullif(sum(CASE WHEN side='B' THEN active_qty ELSE 0 END),0) AS buy_floating_loss_share,
                       sum(CASE WHEN side='B' AND order_qty>0 AND order_amount/order_qty>day_close THEN buy_loss_qty*((order_amount/order_qty)/nullif(day_close,0)-1) ELSE 0 END)/nullif(sum(buy_loss_qty),0) AS buy_floating_loss_bias,
                       sum(sell_rebound_qty)/nullif(sum(CASE WHEN side='S' THEN active_qty ELSE 0 END),0) AS sell_rebound_share,
                       sum(CASE WHEN side='S' AND order_qty>0 AND order_amount/order_qty<day_close THEN sell_rebound_qty*(day_close-order_amount/order_qty)/nullif(day_close,0) ELSE 0 END)/nullif(sum(sell_rebound_qty),0) AS sell_rebound_deviation
                FROM per_order p
                JOIN stats s USING(code)
                GROUP BY p.code
            """
            queries.append(query)
        def execute_one(item: tuple[int, str]) -> pd.DataFrame:
            task_index, sql = item
            connection = duckdb.connect()
            connection.execute("SET threads=1")
            connection.execute("SET memory_limit='2GB'")
            task_temp = self.settings.temp_dir / "order_parallel" / f"task_{task_index:02d}"
            task_temp.mkdir(parents=True, exist_ok=True)
            connection.execute(
                "SET temp_directory='" + task_temp.resolve().as_posix().replace("'", "''") + "'"
            )
            connection.register("universe", self.universe)
            connection.register("daily_prices", prices[["code", "open", "close"]])
            try:
                return connection.execute(sql).fetchdf()
            finally:
                connection.close()

        workers = max(int(self.settings.order_workers), 1)
        if workers == 1:
            frames = [self.connection.execute(query).fetchdf() for query in queries]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                frames = list(executor.map(execute_one, enumerate(queries)))
        self.connection.unregister("daily_prices")
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=["code"])
        result = pd.concat(frames, ignore_index=True)
        if result["code"].duplicated().any():
            examples = result.loc[result["code"].duplicated(keep=False), "code"].unique()[:5]
            raise RuntimeError(f"成交文件的股票范围发生重叠，示例：{', '.join(examples)}")
        return result


def load_daily_panel(settings: BuildSettings) -> pd.DataFrame:
    files = sorted(settings.daily_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"没有找到日线缓存：{settings.daily_dir}")
    connection = duckdb.connect()
    source = (settings.daily_dir / "*.parquet").resolve().as_posix().replace("'", "''")
    columns = "date, code, open, close, volume, amount, float_shares, total_shares, qfq_ratio, prev_close"
    frame = connection.execute(
        f"SELECT {columns} FROM read_parquet('{source}', union_by_name=true) "
        f"WHERE date BETWEEN DATE '{settings.start}' AND DATE '{settings.end}'"
    ).fetchdf()
    connection.close()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "code"]).drop_duplicates(["date", "code"], keep="last")
    if "prev_close" in frame.columns:
        frame["daily_return"] = _safe_div(frame["close"], frame["prev_close"]) - 1.0
    else:
        frame["daily_return"] = frame.groupby("code", sort=False)["close"].pct_change(
            fill_method=None
        )
    frame["market_return"] = frame.groupby("date")["daily_return"].transform("mean")
    return frame


def available_dates(settings: BuildSettings, panel: pd.DataFrame) -> list[str]:
    dates_by_kind = {
        kind: {
            path.name.split("=", 1)[1]
            for path in (settings.l2_root / kind).glob("trade_date=*")
            if path.is_dir()
        }
        for kind in ("trades", "orders", "quotes")
    }
    if not (dates_by_kind["trades"] == dates_by_kind["orders"] == dates_by_kind["quotes"]):
        raise RuntimeError("成交、委托、报价的日期范围不一致")
    l2_dates = dates_by_kind["trades"]
    panel_dates = {value.strftime("%Y-%m-%d") for value in panel["date"].drop_duplicates()}
    start = pd.Timestamp(settings.start)
    end = pd.Timestamp(settings.end)
    requested_end = end.strftime("%Y-%m-%d")
    if requested_end not in l2_dates:
        raise RuntimeError(f"Level2 数据没有覆盖截止日 {requested_end}")
    if requested_end not in panel_dates:
        raise RuntimeError(f"基础日线没有覆盖截止日 {requested_end}")
    dates = sorted(
        value
        for value in l2_dates & panel_dates
        if start <= pd.Timestamp(value) <= end and value not in BAD_DATES
    )
    return dates


def combine_daily_features(
    date: str,
    prices: pd.DataFrame,
    trades: pd.DataFrame,
    orders: pd.DataFrame,
    quotes: pd.DataFrame,
    order_events: pd.DataFrame,
) -> pd.DataFrame:
    result = prices.copy()
    for frame in (trades, orders, quotes, order_events):
        if not frame.empty:
            result = result.merge(frame, on="code", how="left")
    # 合并较多表后整理一次内存，避免后面逐列计算反复发出碎片警告。
    result = result.copy()
    result["date"] = pd.Timestamp(date)
    numeric = result.select_dtypes(include=[np.number]).columns
    result[numeric] = result[numeric].replace([np.inf, -np.inf], np.nan)

    result["open_auction_share"] = _safe_div(result.get("open_auction_volume"), result.get("trade_volume"))
    result["close_5m_share"] = _safe_div(result.get("close_5m_volume"), result.get("trade_volume"))
    result["open_net_buy_share"] = _safe_div(
        result.get("open_large_active_buy_amount") - result.get("open_large_active_sell_amount"),
        result.get("trade_amount"),
    )
    result["open_net_buy_strength"] = _safe_div(
        result.get("open_large_active_buy_amount") - result.get("open_large_active_sell_amount"),
        result.get("open_large_active_buy_amount") + result.get("open_large_active_sell_amount"),
    )
    result["open_large_net_buy_share"] = result["open_net_buy_share"]
    result["open_large_net_buy_strength"] = result["open_net_buy_strength"]
    result["retail_bsi"] = _safe_div(
        result.get("small_buy_amount_40k") - result.get("small_sell_amount_40k"),
        result.get("small_buy_amount_40k") + result.get("small_sell_amount_40k"),
    )
    result["small_flow"] = _safe_div(
        result.get("small_buy_amount_40k") - result.get("small_sell_amount_40k"),
        result.get("small_buy_amount_40k") + result.get("small_sell_amount_40k"),
    )
    result["large_flow"] = _safe_div(
        result.get("large_buy_amount") - result.get("large_sell_amount"),
        result.get("large_buy_amount") + result.get("large_sell_amount"),
    )
    result["super_flow"] = _safe_div(
        result.get("super_buy_amount") - result.get("super_sell_amount"),
        result.get("super_buy_amount") + result.get("super_sell_amount"),
    )
    result["corrected_net_inflow"] = _safe_div(
        result.get("active_large_buy_amount") - result.get("active_large_sell_amount"),
        result.get("active_large_buy_amount") + result.get("active_large_sell_amount"),
    )
    result["retail_fear"] = (
        _safe_div(
            result.get("small_buy_amount_40k") + result.get("small_sell_amount_40k"),
            result.get("trade_amount"),
        )
        * result.get("daily_return")
        * result.get("market_return").abs()
    )
    result["net_bid_change_rate"] = _safe_div(result.get("net_bid_change"), result.get("float_shares"))
    result["open_net_bid_share"] = _safe_div(
        result.get("open_net_bid_change") * result.get("close"), result.get("trade_amount")
    )
    result["open_buy_willingness_share"] = result.get("open_net_buy_share") - result.get("open_net_bid_share")
    result["open_buy_willingness_strength"] = result["open_buy_willingness_share"]
    result["order_imbalance"] = result.get("mofi")
    result["order_imbalance_rate"] = result.get("five_level_soir")
    result["game_factor"] = _safe_div(result.get("buy_volume"), result.get("sell_volume"))
    result["pin"] = _safe_div(
        (result.get("buy_order_arrivals") - result.get("sell_order_arrivals")).abs(),
        result.get("buy_order_arrivals") + result.get("sell_order_arrivals"),
    )
    result["toxic_liquidity"] = _safe_div(
        (result.get("buy_order_cancels") - result.get("sell_order_cancels")).abs(),
        result.get("buy_order_arrivals") + result.get("sell_order_arrivals"),
    )
    result["vpin"] = _safe_div(
        (result.get("buy_volume") - result.get("sell_volume")).abs(), result.get("trade_volume")
    )
    result["vwpin"] = result["vpin"] * _safe_div(result.get("buy_count") + result.get("sell_count"), result.get("trade_count"))
    result["active_large_buy_share"] = _safe_div(result.get("active_large_buy_amount"), result.get("trade_amount"))
    result["active_buy_strength"] = _safe_div(
        result.get("buy_amount"), result.get("sell_amount")
    )
    return result.sort_values("code").reset_index(drop=True)


def process_date(reader: Level2Reader, panel: pd.DataFrame, date: str) -> tuple[pd.DataFrame, dict]:
    started = time.perf_counter()
    prices = panel.loc[panel["date"].eq(pd.Timestamp(date))].copy()
    steps: dict[str, float] = {}
    step_started = time.perf_counter()
    raw_minutes = reader.minute_trades(date)
    steps["read_trade_minutes"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    order_daily = reader.order_trade_metrics(date, prices)
    steps["calculate_order_features"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    trade_daily, minute = aggregate_trade_minutes(raw_minutes)
    trade_daily = _market_minute_features(minute, trade_daily)
    steps["calculate_trade_features"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    quote_raw = reader.quote_minutes(date)
    steps["read_quote_minutes"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    quote_daily = aggregate_quotes(quote_raw, minute)
    steps["calculate_quote_features"] = time.perf_counter() - step_started
    order_events = reader.order_events(date)
    steps["read_order_events"] = time.perf_counter() - step_started
    step_started = time.perf_counter()
    daily = combine_daily_features(date, prices, trade_daily, order_daily, quote_daily, order_events)
    steps["combine"] = time.perf_counter() - step_started
    stats = {
        "date": date,
        "rows": int(len(daily)),
        "minute_rows": int(len(raw_minutes)),
        "quote_minute_rows": int(len(quote_raw)),
        "finite_trade_amount": int(daily.get("trade_amount", pd.Series(dtype=float)).notna().sum()),
        "finite_spread": int(daily.get("spread_mean", pd.Series(dtype=float)).notna().sum()),
        "finite_order_amount": int(daily.get("buy_order_amount", pd.Series(dtype=float)).notna().sum()),
        "seconds": round(time.perf_counter() - started, 3),
        "step_seconds": {key: round(value, 3) for key, value in steps.items()},
    }
    return daily, stats


def write_atomic(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    frame.to_parquet(temporary, compression="zstd", index=False)
    temporary.replace(target)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l2-root", type=Path, default=DEFAULT_L2_ROOT)
    parser.add_argument("--daily-dir", type=Path, default=DEFAULT_DAILY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--temp-dir", type=Path, default=DEFAULT_TEMP_DIR)
    parser.add_argument("--start", default="2026-01-05")
    parser.add_argument("--end", default="2026-07-16")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--order-file-batch", type=int, default=1)
    parser.add_argument("--order-workers", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit-dates", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = BuildSettings(
        l2_root=args.l2_root,
        daily_dir=args.daily_dir,
        output_dir=args.output_dir,
        temp_dir=args.temp_dir,
        start=args.start,
        end=args.end,
        threads=args.threads,
        memory_limit=args.memory_limit,
        order_file_batch=args.order_file_batch,
        order_workers=args.order_workers,
        overwrite=args.overwrite,
    )
    panel = load_daily_panel(settings)
    universe = pd.DataFrame({"code": sorted(panel["code"].dropna().astype(str).unique())})
    dates = available_dates(settings, panel)
    if args.limit_dates is not None:
        dates = dates[: max(args.limit_dates, 0)]
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    reader = Level2Reader(settings, universe)
    reports: list[dict] = []
    try:
        for index, date in enumerate(dates, start=1):
            target = settings.output_dir / f"{date}.parquet"
            if target.exists() and not settings.overwrite:
                reports.append({"date": date, "status": "existing", "rows": len(pd.read_parquet(target, columns=["code"]))})
                print(f"[{index}/{len(dates)}] {date} 已存在，跳过", flush=True)
                continue
            print(f"[{index}/{len(dates)}] {date} 开始", flush=True)
            daily, stats = process_date(reader, panel, date)
            write_atomic(daily, target)
            stats["status"] = "completed"
            reports.append(stats)
            print(
                f"[{index}/{len(dates)}] {date} 完成：{stats['rows']}只，{stats['seconds']}秒",
                flush=True,
            )
            pd.DataFrame(reports).to_csv(
                settings.output_dir / "build_progress.csv", index=False, encoding="utf-8-sig"
            )
    finally:
        reader.close()

    summary = {
        "l2_root": str(settings.l2_root),
        "daily_dir": str(settings.daily_dir),
        "start": settings.start,
        "end": settings.end,
        "bad_dates_excluded": BAD_DATES,
        "requested_dates": len(dates),
        "completed_files": len(list(settings.output_dir.glob("2026-??-??.parquet"))),
        "reports": reports,
    }
    (settings.output_dir / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
