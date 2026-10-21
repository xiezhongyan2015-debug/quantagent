# -*- coding: utf-8 -*-
"""历史因子日历回测的公共计算函数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

import run_january_minute_backtest as january


@dataclass(frozen=True)
class BacktestSettings:
    qlib_dir: Path = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    start: str = "2015-01-01"
    backtest_start: str = "2016-01-04"
    end: str = "2024-12-31"
    split_date: str = "2020-12-31"
    report_start: str = "2022-01-01"
    report_end: str = "2024-12-31"
    hold_days: int = 5
    groups: int = 10
    min_stocks: int = 50
    min_price: float = 3.0
    min_avg_amount: float = 5_000_000.0
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013


def normalize_frequency(value: object) -> str:
    """把目录中的中英文频率统一为四种回测频率。"""

    text = str(value or "").strip().lower().replace(" ", "")
    if text in {"daily", "每日", "日频", "每日一次"} or "日" in text:
        if "5" in text and ("每" in text or "五" in text):
            return "every_5_days"
        return "daily"
    if text in {"monthly", "月度", "月频", "月末"} or "月" in text:
        return "monthly"
    if text in {"weekly_tuesday", "每周二", "周频（周二）", "周二"} or "周" in text:
        return "weekly_tuesday"
    if text in {"every_5_days", "每5个交易日", "每五个交易日"}:
        return "every_5_days"
    raise ValueError(f"不支持的回测频率：{value!r}")


def make_context(
    panel: pd.DataFrame,
    settings: BacktestSettings,
    *,
    dtype: np.dtype = np.float32,
) -> tuple[pd.DatetimeIndex, list[str], dict, dict, np.ndarray]:
    """由日度面板生成日期、股票坐标和历史成分范围。"""

    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    rows = january.read_membership_rows(settings.qlib_dir, settings.start, settings.end)
    membership, _ = january.build_membership(dates, stocks, rows)
    return dates, stocks, date_map, stock_map, membership


def panel_arrays(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    columns: tuple[str, ...] = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "float_shares",
        "total_shares",
        "qfq_ratio",
    ),
) -> dict[str, np.ndarray]:
    """把需要的面板字段转为节省内存的二维矩阵。"""

    arrays: dict[str, np.ndarray] = {}
    for column in columns:
        if column not in panel.columns:
            continue
        arrays[column] = january.panel_to_matrix(
            panel,
            column,
            dates,
            stocks,
            date_map,
            stock_map,
            dtype=np.float32,
        )
    if {"close", "qfq_ratio"}.issubset(arrays):
        raw = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
        np.divide(
            arrays["close"],
            arrays["qfq_ratio"],
            out=raw,
            where=np.isfinite(arrays["qfq_ratio"]) & (arrays["qfq_ratio"] != 0),
        )
        arrays["raw_close"] = raw
    if {"volume", "float_shares"}.issubset(arrays):
        turnover = np.full(arrays["volume"].shape, np.nan, dtype=np.float32)
        np.divide(
            arrays["volume"],
            arrays["float_shares"],
            out=turnover,
            where=np.isfinite(arrays["float_shares"]) & (arrays["float_shares"] > 0),
        )
        arrays["turnover"] = turnover
    if {"raw_close", "total_shares"}.issubset(arrays):
        arrays["market_cap"] = (
            arrays["raw_close"] * arrays["total_shares"]
        ).astype(np.float32)
        arrays["total_market_cap"] = arrays["market_cap"]
    if "close" in arrays:
        returns = np.full(arrays["close"].shape, np.nan, dtype=np.float32)
        np.divide(
            arrays["close"][1:],
            arrays["close"][:-1],
            out=returns[1:],
            where=np.isfinite(arrays["close"][1:])
            & np.isfinite(arrays["close"][:-1])
            & (arrays["close"][:-1] != 0),
        )
        returns[1:] -= 1.0
        arrays["returns"] = returns
    if {"open", "close"}.issubset(arrays):
        open_exit, close_exit = january.build_exit_price_matrices(
            arrays["open"], arrays["close"]
        )
        arrays["open_exit_mark"] = open_exit.astype(np.float32)
        arrays["close_exit_mark"] = close_exit.astype(np.float32)
    return arrays


def build_eligible(
    arrays: Mapping[str, np.ndarray],
    membership: np.ndarray,
    settings: BacktestSettings,
) -> np.ndarray:
    """执行价格、成交额和历史成分筛选。"""

    average_amount = january.rolling_mean(
        np.asarray(arrays["amount"], dtype=np.float64), 20, require_full=False
    )
    raw_close = np.asarray(arrays.get("raw_close", arrays["close"]))
    return (
        membership
        & np.isfinite(raw_close)
        & (raw_close >= settings.min_price)
        & np.isfinite(average_amount)
        & (average_amount >= settings.min_avg_amount)
    )


def _periods(
    dates: pd.DatetimeIndex,
    frequency: str,
    settings: BacktestSettings,
) -> list[tuple[int, int, int]]:
    frequency = normalize_frequency(frequency)
    if frequency == "monthly":
        return january.month_pairs(dates, settings.backtest_start)
    start = dates.searchsorted(pd.Timestamp(settings.backtest_start))
    stop = len(dates) - settings.hold_days - 1
    if frequency in {"daily", "every_5_days"}:
        signals = np.arange(start, stop, settings.hold_days, dtype=int)
    elif frequency == "weekly_tuesday":
        signals = np.flatnonzero(
            (dates.weekday == 1) & (dates >= pd.Timestamp(settings.backtest_start))
        )
    else:
        raise ValueError(frequency)
    periods = [
        (int(index), int(index + 1), int(index + settings.hold_days + 1))
        for index in signals
        if index < stop
    ]
    if frequency != "weekly_tuesday":
        return periods
    result: list[tuple[int, int, int]] = []
    previous_exit = -1
    for period in periods:
        if period[1] < previous_exit:
            continue
        result.append(period)
        previous_exit = period[2]
    return result


def _transition_cost(
    previous: set[int], current: set[int], settings: BacktestSettings
) -> tuple[float, float]:
    if not current:
        return 0.0, 0.0
    old_weight = 1.0 / len(previous) if previous else 0.0
    new_weight = 1.0 / len(current)
    buy = sum(max(new_weight - (old_weight if item in previous else 0.0), 0.0) for item in current)
    sell = sum(max(old_weight - (new_weight if item in current else 0.0), 0.0) for item in previous)
    return buy * settings.buy_cost + sell * settings.sell_cost, 0.5 * (buy + sell)


def backtest_factor(
    unit_id: str,
    factor: np.ndarray,
    frequency: str,
    arrays: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: BacktestSettings,
) -> pd.DataFrame:
    """按十分位分组回测一个因子，并只用分界日前的IC确定方向。"""

    values = np.asarray(factor, dtype=np.float32)
    if values.shape != eligible.shape:
        raise ValueError(f"{unit_id}形状错误：{values.shape}，应为{eligible.shape}")
    frequency = normalize_frequency(frequency)
    rows: list[dict[str, object]] = []
    previous_low: set[int] = set()
    previous_high: set[int] = set()
    previous_exit: int | None = None
    for signal, entry, exit_index in _periods(dates, frequency, settings):
        entry_price = np.asarray(arrays["open"])[entry]
        exit_price = np.asarray(
            arrays["close_exit_mark"] if frequency == "monthly" else arrays["open_exit_mark"]
        )[exit_index]
        tradable = np.isfinite(entry_price) & (entry_price > 0)
        valuable = np.isfinite(exit_price) & (exit_price > 0)
        period_return = np.zeros(entry_price.shape, dtype=np.float64)
        valid_price = tradable & valuable
        period_return[valid_price] = exit_price[valid_price] / entry_price[valid_price] - 1.0
        mask = eligible[signal] & tradable & np.isfinite(values[signal])
        indices = np.flatnonzero(mask)
        if len(indices) < settings.min_stocks:
            continue
        current_values = values[signal, indices]
        future_values = period_return[indices]
        ic, _ = january.rank_ic(current_values, future_values, settings.min_stocks)
        order = indices[np.argsort(current_values, kind="stable")]
        group_count = max(len(order) // settings.groups, 1)
        low = set(order[:group_count].tolist())
        high = set(order[-group_count:].tolist())
        low_return = float(np.mean(period_return[np.fromiter(low, dtype=int)]))
        high_return = float(np.mean(period_return[np.fromiter(high, dtype=int)]))
        benchmark = float(np.mean(period_return[indices]))
        if frequency == "monthly":
            low_cost = high_cost = settings.buy_cost + settings.sell_cost
            low_turnover = high_turnover = 1.0
        elif previous_exit is not None and previous_exit != entry:
            if rows:
                rows[-1]["low_net_return"] = float(rows[-1]["low_net_return"]) - settings.sell_cost
                rows[-1]["high_net_return"] = float(rows[-1]["high_net_return"]) - settings.sell_cost
                rows[-1]["low_turnover"] = float(rows[-1]["low_turnover"]) + 0.5
                rows[-1]["high_turnover"] = float(rows[-1]["high_turnover"]) + 0.5
            low_cost = high_cost = settings.buy_cost
            low_turnover = high_turnover = 0.5
        else:
            low_cost, low_turnover = _transition_cost(previous_low, low, settings)
            high_cost, high_turnover = _transition_cost(previous_high, high, settings)
        rows.append(
            {
                "unit_id": unit_id,
                "signal_frequency": frequency,
                "signal_date": dates[signal],
                "entry_date": dates[entry],
                "exit_date": dates[exit_index],
                "ic": ic,
                "low_raw_return": low_return,
                "low_net_return": low_return - low_cost,
                "low_turnover": low_turnover,
                "high_raw_return": high_return,
                "high_net_return": high_return - high_cost,
                "high_turnover": high_turnover,
                "benchmark_return": benchmark,
                "eligible_stocks": len(indices),
                "group_stocks": group_count,
            }
        )
        previous_low, previous_high, previous_exit = low, high, exit_index
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if frequency != "monthly":
        frame.loc[frame.index[-1], "low_net_return"] -= settings.sell_cost
        frame.loc[frame.index[-1], "high_net_return"] -= settings.sell_cost
        frame.loc[frame.index[-1], "low_turnover"] += 0.5
        frame.loc[frame.index[-1], "high_turnover"] += 0.5
    split = pd.Timestamp(settings.split_date)
    training = frame.loc[frame["exit_date"] <= split, "ic"].dropna()
    direction_source = "2016_2020_ic"
    if training.empty:
        training = frame.loc[frame["entry_date"] < pd.Timestamp(settings.report_start), "ic"].dropna()
        direction_source = "pre_report_ic"
    mean_ic = float(training.mean()) if not training.empty else np.nan
    direction = "low" if np.isfinite(mean_ic) and mean_ic < 0 else "high"
    frame["direction"] = direction
    frame["direction_source"] = direction_source if np.isfinite(mean_ic) else "default_high"
    frame["training_ic"] = mean_ic
    frame["selected_net_return"] = frame[f"{direction}_net_return"]
    frame["selected_turnover"] = frame[f"{direction}_turnover"]
    report = frame[
        (frame["entry_date"] >= pd.Timestamp(settings.report_start))
        & (frame["entry_date"] <= pd.Timestamp(settings.report_end))
    ].copy()
    return report


def _return_metrics(frame: pd.DataFrame) -> dict[str, float | int | str]:
    if frame.empty:
        return {}
    values = frame["selected_net_return"].to_numpy(dtype=float)
    benchmark = frame["benchmark_return"].to_numpy(dtype=float)
    valid = np.isfinite(values) & np.isfinite(benchmark)
    values, benchmark = values[valid], benchmark[valid]
    if len(values) == 0:
        return {}
    elapsed = max(
        (pd.Timestamp(frame["exit_date"].max()) - pd.Timestamp(frame["entry_date"].min())).days
        / 365.25,
        1.0 / 12.0,
    )
    periods_per_year = len(values) / elapsed
    nav = np.r_[1.0, np.cumprod(1.0 + values)]
    benchmark_nav = np.r_[1.0, np.cumprod(1.0 + benchmark)]
    annual_return = nav[-1] ** (1.0 / elapsed) - 1.0 if nav[-1] > 0 else -1.0
    benchmark_annual = (
        benchmark_nav[-1] ** (1.0 / elapsed) - 1.0 if benchmark_nav[-1] > 0 else -1.0
    )
    std = values.std(ddof=1) if len(values) > 1 else np.nan
    excess = values - benchmark
    excess_std = excess.std(ddof=1) if len(excess) > 1 else np.nan
    return {
        "periods": len(values),
        "cumulative_return": nav[-1] - 1.0,
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_annual,
        "annual_excess_return": annual_return - benchmark_annual,
        "annual_volatility": std * math.sqrt(periods_per_year) if np.isfinite(std) else np.nan,
        "sharpe": values.mean() / std * math.sqrt(periods_per_year) if np.isfinite(std) and std > 0 else np.nan,
        "max_drawdown": float(np.min(nav / np.maximum.accumulate(nav) - 1.0)),
        "excess_ir": excess.mean() / excess_std * math.sqrt(periods_per_year)
        if np.isfinite(excess_std) and excess_std > 0
        else np.nan,
        "win_rate_vs_benchmark": float(np.mean(values > benchmark)),
        "mean_ic": float(frame["ic"].mean()),
        "training_ic": float(frame["training_ic"].iloc[0]),
        "direction": str(frame["direction"].iloc[0]),
        "direction_source": str(frame["direction_source"].iloc[0]),
        "avg_turnover": float(frame["selected_turnover"].mean()),
        "avg_group_stocks": float(frame["group_stocks"].mean()),
    }


def summarize_unit(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """生成一个计算单元的三年总体和逐年结果。"""

    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()
    metrics = _return_metrics(frame)
    summary = pd.DataFrame([{"unit_id": frame["unit_id"].iloc[0], **metrics}])
    yearly_rows = []
    copy = frame.copy()
    copy["year"] = copy["entry_date"].dt.year
    for year, group in copy.groupby("year"):
        strategy = float(np.prod(1.0 + group["selected_net_return"]) - 1.0)
        benchmark = float(np.prod(1.0 + group["benchmark_return"]) - 1.0)
        yearly_rows.append(
            {
                "unit_id": frame["unit_id"].iloc[0],
                "year": int(year),
                "strategy_return": strategy,
                "benchmark_return": benchmark,
                "excess_return": strategy - benchmark,
                "periods": len(group),
                "mean_ic": float(group["ic"].mean()),
            }
        )
    return summary, pd.DataFrame(yearly_rows)
