# -*- coding: utf-8 -*-
"""用已经算好的 Level2 因子长表完成 2026 年短样本回测。"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import historical_backtest_core as historical
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent
REQUIRED_FACTOR_COLUMNS = (
    "factor_id",
    "date",
    "code",
    "value",
    "frequency",
    "expected_direction",
    "direction_source",
    "implementation_quality",
)
EXCLUDED_SIGNAL_DATES = frozenset(
    pd.to_datetime(
        [
            "2026-03-12",
            "2026-04-14",
            "2026-05-12",
            "2026-07-06",
            "2026-07-09",
            "2026-07-10",
            "2026-07-13",
            "2026-07-15",
        ]
    )
)
SIDES = ("low", "high")
RANK_IC_COLUMNS = [
    "factor_id",
    "signal_date",
    "entry_date",
    "exit_date",
    "return_horizon",
    "rank_ic",
    "stock_count",
]
REBALANCE_COLUMNS = [
    "factor_id",
    "signal_date",
    "entry_date",
    "exit_date",
    "trade_frequency",
    "return_horizon",
    "side",
    "raw_return",
    "transaction_cost",
    "net_return",
    "turnover",
    "benchmark_return",
    "period_rank_ic",
    "eligible_stocks",
    "group_stocks",
    "exit_price_fallback_stocks",
    "exit_price_missing_stocks",
]


@dataclass(frozen=True)
class ShortBacktestSettings:
    """短样本回测的固定规则和路径。"""

    base_cache_dir: Path = BASE_DIR / ".codex-artifact/2026_rank_check/cache/base"
    qlib_dir: Path = BASE_DIR / ".codex-artifact/2026_rank_check/qlib_overlay"
    output_dir: Path = BASE_DIR / ".codex-artifact/level2_short_backtests"
    start: str = "2026-01-05"
    end: str = "2026-07-16"
    lookback_calendar_days: int = 90
    hold_days: int = 5
    groups: int = 10
    min_stocks: int = 50
    min_price: float = 3.0
    min_avg_amount: float = 5_000_000.0
    buy_cost: float = 0.0003
    sell_cost: float = 0.0013


def normalize_frequency(value: object) -> str:
    """把输入频率统一为日频或月频。"""

    if pd.isna(value):
        raise ValueError("frequency 不能为空")
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        raise ValueError("frequency 不能为空")
    if "日" in text and "月" in text:
        raise ValueError(f"frequency 仍有多种可能，需先明确为日频或月频：{value!r}")
    daily = {
        "daily",
        "d",
        "日频",
        "日度",
        "每日",
        "每日一次",
        "every_5_days",
        "每5个交易日",
        "每五个交易日",
    }
    monthly = {"monthly", "m", "月频", "月度", "每月", "月末"}
    if text in daily:
        return "daily"
    if text in monthly:
        return "monthly"
    try:
        normalized = historical.normalize_frequency(value)
    except ValueError as error:
        raise ValueError(f"只支持日频和月频：{value!r}") from error
    if normalized in {"daily", "every_5_days"}:
        return "daily"
    if normalized == "monthly":
        return "monthly"
    raise ValueError(f"只支持日频和月频：{value!r}")


def normalize_code(value: object) -> str:
    """把常见股票代码写法统一为 sh600000 这类形式。"""

    if pd.isna(value):
        raise ValueError("code 不能为空")
    text = str(value).strip().lower().replace(" ", "")
    if not text:
        raise ValueError("code 不能为空")
    prefix = re.fullmatch(r"(sh|sz|bj)[._-]?(\d{6})", text)
    suffix = re.fullmatch(r"(\d{6})[._-]?(sh|sz|bj)", text)
    bare = re.fullmatch(r"\d{6}", text)
    if prefix:
        result = prefix.group(1) + prefix.group(2)
    elif suffix:
        result = suffix.group(2) + suffix.group(1)
    elif bare:
        exchange = "sh" if text[0] in {"5", "6", "9"} else "bj" if text[0] in {"4", "8"} else "sz"
        result = exchange + text
    else:
        raise ValueError(f"无法识别股票代码：{value!r}")
    return january.qlib_code(result).lower()


def _read_factor_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path, columns=list(REQUIRED_FACTOR_COLUMNS))
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(
            path,
            usecols=list(REQUIRED_FACTOR_COLUMNS),
            dtype={"factor_id": "string", "code": "string"},
        )
    raise ValueError(f"不支持的因子文件：{path}")


def _discover_factor_files(source: str | Path) -> list[Path]:
    text = str(source)
    if any(mark in text for mark in "*?["):
        files = [Path(item) for item in glob.glob(text)]
    else:
        path = Path(source)
        if path.is_dir():
            files = [
                item
                for item in sorted(path.rglob("*"))
                if item.suffix.lower() in {".parquet", ".pq", ".csv", ".txt"}
            ]
        else:
            files = [path]
    files = sorted({item.resolve() for item in files if item.is_file()})
    if not files:
        raise FileNotFoundError(f"没有找到因子长表：{source}")
    return files


def load_factor_values(source: str | Path) -> pd.DataFrame:
    """读取单个文件、文件夹或通配写法指定的因子长表。"""

    files = _discover_factor_files(source)
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for path in files:
        try:
            frame = _read_factor_file(path)
        except (KeyError, ValueError) as error:
            if len(files) == 1:
                raise ValueError(
                    f"因子长表必须含这些列：{', '.join(REQUIRED_FACTOR_COLUMNS)}"
                ) from error
            skipped.append(str(path))
            continue
        frames.append(frame)
    if not frames:
        raise ValueError("找到的文件都不是合格的因子长表")
    result = pd.concat(frames, ignore_index=True)
    result.attrs["source_files"] = [str(path) for path in files if str(path) not in skipped]
    result.attrs["skipped_files"] = skipped
    return result


def _single_text(values: pd.Series, factor_id: str, column: str) -> str:
    cleaned = [str(value).strip() for value in values if pd.notna(value) and str(value).strip()]
    unique = list(dict.fromkeys(cleaned))
    if len(unique) > 1:
        raise ValueError(f"{factor_id} 的 {column} 不一致：{unique}")
    return unique[0] if unique else ""


def prepare_factor_values(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """检查长表，统一日期、代码和频率，并取出每个因子的说明。"""

    missing = [column for column in REQUIRED_FACTOR_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"因子长表缺少列：{', '.join(missing)}")
    result = frame.loc[:, REQUIRED_FACTOR_COLUMNS].copy()
    if result["factor_id"].isna().any():
        raise ValueError("factor_id 不能为空")
    result["factor_id"] = result["factor_id"].astype(str).str.strip()
    if result["factor_id"].eq("").any():
        raise ValueError("factor_id 不能为空")
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
    if result["date"].isna().any():
        raise ValueError("date 中有无法识别的值")
    result["code"] = result["code"].map(normalize_code)
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["frequency"] = result["frequency"].map(normalize_frequency)

    duplicate = result.duplicated(["factor_id", "date", "code"], keep=False)
    if duplicate.any():
        sample = result.loc[duplicate, ["factor_id", "date", "code"]].head(5)
        raise ValueError(f"factor_id、date、code 必须唯一，例如：\n{sample.to_string(index=False)}")

    metadata_rows: list[dict[str, object]] = []
    for factor_id, group in result.groupby("factor_id", sort=True):
        frequencies = group["frequency"].drop_duplicates().tolist()
        if len(frequencies) != 1:
            raise ValueError(f"{factor_id} 的 frequency 不一致：{frequencies}")
        metadata_rows.append(
            {
                "factor_id": factor_id,
                "frequency": frequencies[0],
                "expected_direction": _single_text(group["expected_direction"], factor_id, "expected_direction"),
                "direction_source": _single_text(group["direction_source"], factor_id, "direction_source"),
                "implementation_quality": _single_text(
                    group["implementation_quality"], factor_id, "implementation_quality"
                ),
            }
        )
    metadata = pd.DataFrame(metadata_rows)
    return result.sort_values(["factor_id", "date", "code"]), metadata


def _parquet_columns(path: Path) -> list[str]:
    return list(pq.ParquetFile(path).schema.names)


def load_base_panel(
    cache_dir: Path,
    codes: Iterable[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """只读取因子长表中出现股票的基础日线。"""

    cache_dir = Path(cache_dir)
    file_map: dict[str, Path] = {}
    for path in sorted(cache_dir.glob("*.parquet")):
        normalized = normalize_code(path.stem)
        current = file_map.get(normalized)
        # 更名股票的新代码文件优先；只有旧代码文件时也能正常读取。
        if current is None or path.stem.lower() == normalized:
            file_map[normalized] = path
    normalized_codes = sorted({normalize_code(code) for code in codes})
    frames: list[pd.DataFrame] = []
    missing_codes: list[str] = []
    wanted = ["date", "code", "open", "close", "amount", "qfq_ratio", "raw_close"]
    lower, upper = pd.Timestamp(start), pd.Timestamp(end)
    loaded_files: list[str] = []
    for code in normalized_codes:
        path = file_map.get(code)
        if path is None:
            missing_codes.append(code)
            continue
        columns = _parquet_columns(path)
        selected = [column for column in wanted if column in columns]
        frame = pd.read_parquet(path, columns=selected)
        required = {"date", "open", "close", "amount"}
        absent = sorted(required - set(frame.columns))
        if absent:
            raise ValueError(f"基础日线 {path} 缺少列：{', '.join(absent)}")
        if "code" not in frame.columns:
            frame["code"] = code
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame = frame[frame["date"].between(lower, upper)].copy()
        if frame.empty:
            continue
        frame["code"] = frame["code"].map(normalize_code)
        frames.append(frame)
        loaded_files.append(str(path.resolve()))
    if not frames:
        raise ValueError("没有读到与因子股票相匹配的基础日线")
    panel = pd.concat(frames, ignore_index=True, sort=False)
    panel = panel.sort_values(["date", "code"]).drop_duplicates(
        ["date", "code"], keep="last"
    )
    panel.attrs["loaded_files"] = loaded_files
    panel.attrs["missing_codes"] = missing_codes
    return panel


def _validate_settings(settings: ShortBacktestSettings) -> None:
    if pd.Timestamp(settings.start) > pd.Timestamp(settings.end):
        raise ValueError("start 不能晚于 end")
    if settings.hold_days <= 0 or settings.groups <= 1 or settings.min_stocks <= 0:
        raise ValueError("hold_days、groups 和 min_stocks 必须为正，groups 至少为2")


def _require_requested_end(
    dates: pd.DatetimeIndex,
    base_panel: pd.DataFrame,
    settings: ShortBacktestSettings,
) -> None:
    requested = pd.Timestamp(settings.end).normalize()
    actual_calendar = pd.Timestamp(dates[-1]).normalize() if len(dates) else pd.NaT
    actual_base = pd.to_datetime(base_panel["date"], errors="coerce").max()
    actual_base = pd.Timestamp(actual_base).normalize() if pd.notna(actual_base) else pd.NaT
    if actual_calendar != requested:
        raise ValueError(f"交易日历截止 {actual_calendar}，没有覆盖请求的 {requested:%Y-%m-%d}")
    if actual_base != requested:
        raise ValueError(f"基础日线截止 {actual_base}，没有覆盖请求的 {requested:%Y-%m-%d}")


def _monthly_periods(
    dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp
) -> list[tuple[int, int, int]]:
    periods = dates.to_period("M")
    next_business_day = dates[-1] + pd.offsets.BDay(1)
    last_is_complete_month = next_business_day.to_period("M") != dates[-1].to_period("M")
    month_ends = np.flatnonzero(
        np.r_[periods[:-1].to_numpy() != periods[1:].to_numpy(), last_is_complete_month]
    )
    result: list[tuple[int, int, int]] = []
    for position in range(len(month_ends) - 1):
        signal = int(month_ends[position])
        entry = signal + 1
        exit_index = int(month_ends[position + 1])
        if start <= dates[signal] <= end and dates[exit_index] <= end and entry <= exit_index:
            result.append((signal, entry, exit_index))
    return result


def evaluation_periods(
    dates: pd.DatetimeIndex,
    frequency: str,
    settings: ShortBacktestSettings,
    *,
    rebalance: bool,
) -> list[tuple[int, int, int]]:
    """给出信号、次日进入和退出日期的位置。"""

    start, end = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    if frequency == "monthly":
        periods = _monthly_periods(dates, start, end)
    else:
        first = int(dates.searchsorted(start))
        last = len(dates) - settings.hold_days - 1
        step = settings.hold_days if rebalance else 1
        periods = [
            (signal, signal + 1, signal + settings.hold_days + 1)
            for signal in range(first, last, step)
            if dates[signal] <= end and dates[signal + settings.hold_days + 1] <= end
        ]
    return [period for period in periods if dates[period[0]].normalize() not in EXCLUDED_SIGNAL_DATES]


def _daily_trade_frequency(hold_days: int) -> str:
    """按实际持有天数生成日频调仓说明。"""

    return "every_trading_day" if hold_days == 1 else f"every_{hold_days}_trading_days"


def _daily_return_horizon(hold_days: int) -> str:
    """按实际持有天数生成收益区间说明。"""

    unit = "day" if hold_days == 1 else "days"
    return f"next_{hold_days}_trading_{unit}"


def _transition_cost(
    previous: set[int], current: set[int], settings: ShortBacktestSettings
) -> tuple[float, float]:
    if not current:
        return 0.0, 0.0
    old_weight = 1.0 / len(previous) if previous else 0.0
    new_weight = 1.0 / len(current)
    buy = 0.0
    sell = 0.0
    for stock in previous | current:
        before = old_weight if stock in previous else 0.0
        after = new_weight if stock in current else 0.0
        buy += max(after - before, 0.0)
        sell += max(before - after, 0.0)
    cost = buy * settings.buy_cost + sell * settings.sell_cost
    return cost, 0.5 * (buy + sell)


def _future_return(
    arrays: Mapping[str, np.ndarray],
    entry: int,
    exit_index: int,
    frequency: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    entry_price = np.asarray(arrays["open"])[entry]
    if frequency == "monthly":
        original_exit = np.asarray(arrays["close"])[exit_index]
        exit_price = np.asarray(arrays["close_exit_mark"])[exit_index]
    else:
        original_exit = np.asarray(arrays["open"])[exit_index]
        exit_price = np.asarray(arrays["open_exit_mark"])[exit_index]
    tradable = np.isfinite(entry_price) & (entry_price > 0)
    valuable = np.isfinite(exit_price) & (exit_price > 0)
    result = np.full(entry_price.shape, np.nan, dtype=np.float64)
    valid = tradable & valuable
    result[valid] = exit_price[valid] / entry_price[valid] - 1.0
    fallback = ~np.isfinite(original_exit) & valuable
    return result, tradable, valuable, fallback


def calculate_rank_ic(
    factor_id: str,
    factor: np.ndarray,
    frequency: str,
    arrays: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: ShortBacktestSettings,
) -> pd.DataFrame:
    """日频逐日算秩相关，月频逐月算秩相关。"""

    rows: list[dict[str, object]] = []
    for signal, entry, exit_index in evaluation_periods(
        dates, frequency, settings, rebalance=False
    ):
        future, tradable, _, _ = _future_return(arrays, entry, exit_index, frequency)
        mask = eligible[signal] & tradable & np.isfinite(factor[signal]) & np.isfinite(future)
        count = int(mask.sum())
        if count < settings.min_stocks:
            continue
        rank_ic, _ = january.rank_ic(
            factor[signal, mask], future[mask], settings.min_stocks
        )
        rows.append(
            {
                "factor_id": factor_id,
                "signal_date": dates[signal],
                "entry_date": dates[entry],
                "exit_date": dates[exit_index],
                "return_horizon": (
                    "next_month"
                    if frequency == "monthly"
                    else _daily_return_horizon(settings.hold_days)
                ),
                "rank_ic": rank_ic,
                "stock_count": count,
            }
        )
    return pd.DataFrame(rows)


def backtest_both_sides(
    factor_id: str,
    factor: np.ndarray,
    frequency: str,
    arrays: Mapping[str, np.ndarray],
    eligible: np.ndarray,
    dates: pd.DatetimeIndex,
    settings: ShortBacktestSettings,
) -> pd.DataFrame:
    """按十分位分别保存低组和高组，不从短样本挑选其中一组。"""

    rows: list[dict[str, object]] = []
    previous: dict[str, set[int]] = {side: set() for side in SIDES}
    previous_exit: int | None = None
    last_row: dict[str, int | None] = {side: None for side in SIDES}
    for signal, entry, exit_index in evaluation_periods(
        dates, frequency, settings, rebalance=True
    ):
        future, tradable, valuable, fallback = _future_return(
            arrays, entry, exit_index, frequency
        )
        mask = eligible[signal] & tradable & np.isfinite(factor[signal])
        indices = np.flatnonzero(mask)
        if len(indices) < settings.min_stocks:
            continue
        current_values = factor[signal, indices]
        valid_ic = np.isfinite(future[indices])
        period_ic, _ = january.rank_ic(
            current_values[valid_ic], future[indices][valid_ic], settings.min_stocks
        )
        order = indices[np.argsort(current_values, kind="stable")]
        group_count = max(len(order) // settings.groups, 1)
        holdings = {
            "low": set(order[:group_count].tolist()),
            "high": set(order[-group_count:].tolist()),
        }
        valued_return = np.where(np.isfinite(future), future, 0.0)
        benchmark_return = float(np.mean(valued_return[indices]))

        gap = frequency != "monthly" and previous_exit is not None and previous_exit != entry
        if gap:
            # 中间有空仓日时，上一期补记卖出，本期只记重新买入。
            for side in SIDES:
                row_index = last_row[side]
                if row_index is not None:
                    rows[row_index]["transaction_cost"] = (
                        float(rows[row_index]["transaction_cost"]) + settings.sell_cost
                    )
                    rows[row_index]["net_return"] = (
                        float(rows[row_index]["net_return"]) - settings.sell_cost
                    )
                    rows[row_index]["turnover"] = float(rows[row_index]["turnover"]) + 0.5

        for side in SIDES:
            members = holdings[side]
            raw_return = float(np.mean(valued_return[np.fromiter(members, dtype=int)]))
            if frequency == "monthly":
                transaction_cost = settings.buy_cost + settings.sell_cost
                turnover = 1.0
            elif gap:
                transaction_cost = settings.buy_cost
                turnover = 0.5
            else:
                transaction_cost, turnover = _transition_cost(
                    previous[side], members, settings
                )
            rows.append(
                {
                    "factor_id": factor_id,
                    "signal_date": dates[signal],
                    "entry_date": dates[entry],
                    "exit_date": dates[exit_index],
                    "trade_frequency": (
                        "monthly"
                        if frequency == "monthly"
                        else _daily_trade_frequency(settings.hold_days)
                    ),
                    "return_horizon": (
                        "next_month"
                        if frequency == "monthly"
                        else _daily_return_horizon(settings.hold_days)
                    ),
                    "side": side,
                    "raw_return": raw_return,
                    "transaction_cost": transaction_cost,
                    "net_return": raw_return - transaction_cost,
                    "turnover": turnover,
                    "benchmark_return": benchmark_return,
                    "period_rank_ic": period_ic,
                    "eligible_stocks": len(indices),
                    "group_stocks": group_count,
                    "exit_price_fallback_stocks": int(fallback[indices].sum()),
                    "exit_price_missing_stocks": int((~valuable[indices]).sum()),
                }
            )
            last_row[side] = len(rows) - 1
        previous = holdings
        previous_exit = exit_index

    if rows and frequency != "monthly":
        # 最后一期结束后卖出全部股票。
        for side in SIDES:
            row_index = last_row[side]
            if row_index is not None:
                rows[row_index]["transaction_cost"] = (
                    float(rows[row_index]["transaction_cost"]) + settings.sell_cost
                )
                rows[row_index]["net_return"] = (
                    float(rows[row_index]["net_return"]) - settings.sell_cost
                )
                rows[row_index]["turnover"] = float(rows[row_index]["turnover"]) + 0.5
    return pd.DataFrame(rows)


def _performance_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    if frame.empty:
        return {}
    strategy = pd.to_numeric(frame["net_return"], errors="coerce").to_numpy(dtype=float)
    benchmark = pd.to_numeric(frame["benchmark_return"], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(strategy) & np.isfinite(benchmark)
    strategy, benchmark = strategy[valid], benchmark[valid]
    if len(strategy) == 0:
        return {}
    elapsed = max(
        (pd.Timestamp(frame["exit_date"].max()) - pd.Timestamp(frame["entry_date"].min())).days
        / 365.25,
        1.0 / 12.0,
    )
    periods_per_year = len(strategy) / elapsed
    nav = np.r_[1.0, np.cumprod(1.0 + strategy)]
    benchmark_nav = np.r_[1.0, np.cumprod(1.0 + benchmark)]
    annual_return = nav[-1] ** (1.0 / elapsed) - 1.0 if nav[-1] > 0 else -1.0
    benchmark_annual = (
        benchmark_nav[-1] ** (1.0 / elapsed) - 1.0
        if benchmark_nav[-1] > 0
        else -1.0
    )
    standard = strategy.std(ddof=1) if len(strategy) > 1 else np.nan
    excess = strategy - benchmark
    excess_standard = excess.std(ddof=1) if len(excess) > 1 else np.nan
    return {
        "periods": len(strategy),
        "periods_per_year": periods_per_year,
        "cumulative_return": nav[-1] - 1.0,
        "benchmark_cumulative_return": benchmark_nav[-1] - 1.0,
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_annual,
        "annual_excess_return": annual_return - benchmark_annual,
        "annual_volatility": standard * math.sqrt(periods_per_year)
        if np.isfinite(standard)
        else np.nan,
        "sharpe": strategy.mean() / standard * math.sqrt(periods_per_year)
        if np.isfinite(standard) and standard > 0
        else np.nan,
        "max_drawdown": float(np.min(nav / np.maximum.accumulate(nav) - 1.0)),
        "excess_ir": excess.mean() / excess_standard * math.sqrt(periods_per_year)
        if np.isfinite(excess_standard) and excess_standard > 0
        else np.nan,
        "win_rate_vs_benchmark": float(np.mean(strategy > benchmark)),
        "avg_turnover": float(frame.loc[valid, "turnover"].mean()),
        "avg_group_stocks": float(frame.loc[valid, "group_stocks"].mean()),
    }


def _ic_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    values = pd.to_numeric(frame.get("rank_ic", pd.Series(dtype=float)), errors="coerce").dropna()
    standard = float(values.std(ddof=1)) if len(values) > 1 else np.nan
    return {
        "rank_ic_observations": int(len(values)),
        "mean_rank_ic": float(values.mean()) if len(values) else np.nan,
        "rank_ic_std": standard,
        "rank_ic_ir": float(values.mean() / standard)
        if np.isfinite(standard) and standard > 0
        else np.nan,
    }


def summarize_results(
    metadata: pd.DataFrame,
    rank_ic: pd.DataFrame,
    rebalance: pd.DataFrame,
    report_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """为每个因子的高组、低组分别生成总体和 2026 年结果。"""

    summary_rows: list[dict[str, object]] = []
    yearly_rows: list[dict[str, object]] = []
    for meta in metadata.itertuples(index=False):
        factor_ic = rank_ic[rank_ic["factor_id"] == meta.factor_id] if not rank_ic.empty else rank_ic
        ic_metrics = _ic_metrics(factor_ic)
        yearly_ic = factor_ic[
            pd.to_datetime(factor_ic["exit_date"]).dt.year == report_year
        ] if not factor_ic.empty else factor_ic
        yearly_ic_metrics = _ic_metrics(yearly_ic)
        for side in SIDES:
            trades = rebalance[
                (rebalance["factor_id"] == meta.factor_id) & (rebalance["side"] == side)
            ] if not rebalance.empty else rebalance
            metrics = _performance_metrics(trades)
            summary_rows.append(
                {
                    "factor_id": meta.factor_id,
                    "frequency": meta.frequency,
                    "expected_direction": meta.expected_direction,
                    "direction_source": meta.direction_source,
                    "implementation_quality": meta.implementation_quality,
                    "side": side,
                    "status": "ok" if metrics else "no_valid_period",
                    "direction_used_for_selection": False,
                    **ic_metrics,
                    **metrics,
                }
            )
            current_year = trades[
                pd.to_datetime(trades["entry_date"]).dt.year == report_year
            ] if not trades.empty else trades
            if current_year.empty:
                strategy_return = benchmark_return = np.nan
                periods = 0
                average_turnover = np.nan
            else:
                strategy_return = float(np.prod(1.0 + current_year["net_return"]) - 1.0)
                benchmark_return = float(
                    np.prod(1.0 + current_year["benchmark_return"]) - 1.0
                )
                periods = len(current_year)
                average_turnover = float(current_year["turnover"].mean())
            yearly_rows.append(
                {
                    "factor_id": meta.factor_id,
                    "frequency": meta.frequency,
                    "expected_direction": meta.expected_direction,
                    "direction_source": meta.direction_source,
                    "implementation_quality": meta.implementation_quality,
                    "side": side,
                    "year": report_year,
                    "strategy_return": strategy_return,
                    "benchmark_return": benchmark_return,
                    "excess_return": strategy_return - benchmark_return
                    if np.isfinite(strategy_return) and np.isfinite(benchmark_return)
                    else np.nan,
                    "periods": periods,
                    "mean_rank_ic": yearly_ic_metrics["mean_rank_ic"],
                    "rank_ic_observations": yearly_ic_metrics["rank_ic_observations"],
                    "avg_turnover": average_turnover,
                    "direction_used_for_selection": False,
                }
            )
    summary = pd.DataFrame(summary_rows).sort_values(["factor_id", "side"])
    yearly = pd.DataFrame(yearly_rows).sort_values(["factor_id", "side", "year"])
    return summary.reset_index(drop=True), yearly.reset_index(drop=True)


def _attach_metadata(frame: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        first = [
            "factor_id",
            "frequency",
            "expected_direction",
            "direction_source",
            "implementation_quality",
        ]
        return pd.DataFrame(
            columns=first + [column for column in frame.columns if column not in first]
        )
    result = frame.merge(metadata, on="factor_id", how="left", validate="many_to_one")
    first = [
        "factor_id",
        "frequency",
        "expected_direction",
        "direction_source",
        "implementation_quality",
    ]
    return result[first + [column for column in result.columns if column not in first]]


def _build_methodology(
    settings: ShortBacktestSettings,
    metadata: pd.DataFrame,
    usable_factor_rows: int,
    base_panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    membership: np.ndarray,
    outputs: Mapping[str, pd.DataFrame],
    *,
    input_rows: int,
    excluded_rows: int,
) -> dict[str, object]:
    report_year = int(pd.Timestamp(settings.start).year)
    return {
        "period": {
            "signal_start": settings.start,
            "signal_end": settings.end,
            "yearly_report_year": report_year,
            "excluded_signal_dates": sorted(
                timestamp.strftime("%Y-%m-%d") for timestamp in EXCLUDED_SIGNAL_DATES
            ),
        },
        "counts": {
            "factor_count": int(len(metadata)),
            "input_factor_rows": int(input_rows),
            "usable_factor_rows": int(usable_factor_rows),
            "excluded_signal_rows": int(excluded_rows),
            "base_daily_rows": int(len(base_panel)),
            "calendar_days": int(len(dates)),
            "matrix_stocks": int(membership.shape[1]),
            "median_membership_stocks": int(np.median(membership.sum(axis=1))),
            "rank_ic_rows": int(len(outputs["rank_ic"])),
            "rebalance_rows": int(len(outputs["rebalance_returns"])),
            "summary_rows": int(len(outputs["summary"])),
            "yearly_rows": int(len(outputs["yearly"])),
        },
        "rules": {
            "universe": "每个信号日的历史中证800与中证1000成分并集",
            "minimum_unadjusted_close": settings.min_price,
            "minimum_20_trading_day_average_amount": settings.min_avg_amount,
            "average_amount_minimum_observations": 10,
            "groups": settings.groups,
            "minimum_stocks": settings.min_stocks,
            "buy_cost": settings.buy_cost,
            "sell_cost": settings.sell_cost,
            "daily_rank_ic": (
                f"每日信号；次日开盘进入，持有{settings.hold_days}个交易日后在开盘退出；"
                "股票截面 Spearman 秩相关"
            ),
            "daily_rebalance": (
                f"从开始日锚定，每{settings.hold_days}个交易日调仓；"
                f"次日开盘进入，持有{settings.hold_days}个交易日后在开盘退出"
            ),
            "monthly_rank_ic_and_rebalance": "月末信号；次月首个交易日开盘进入，到次月最后交易日收盘退出",
            "exit_when_suspended": "退出价缺失时使用此前最近收盘价估值",
            "direction_handling": "expected_direction 和 direction_source 只登记；高组、低组都输出，不用本短样本选择方向",
            "annualization": "总体结果按实际首个进入日到最后退出日折算；逐年表只列开始年份",
        },
        "sources": {
            "factor_input": "由调用方提供的 Parquet 或 CSV 长表",
            "base_daily": str(Path(settings.base_cache_dir).resolve()),
            "qlib_membership": str(Path(settings.qlib_dir).resolve()),
            "loaded_base_files": list(base_panel.attrs.get("loaded_files", [])),
            "missing_base_codes": list(base_panel.attrs.get("missing_codes", [])),
        },
        "limitations": [
            "2026年1月5日至7月16日是短样本，只能补充近期表现，不能替代多年结果。",
            "输入方向没有经过本短样本重新确定，也没有据此删除另一组。",
        ],
    }


def _prepare_market_context(
    base_panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    membership: np.ndarray,
    settings: ShortBacktestSettings,
) -> dict[str, object]:
    """基础日线只转一次矩阵，多个因子重复使用。"""

    normalized_dates = (
        pd.DatetimeIndex(pd.to_datetime(dates)).normalize().drop_duplicates().sort_values()
    )
    if normalized_dates.empty:
        raise ValueError("交易日历为空")
    panel_attrs = dict(base_panel.attrs)
    panel = base_panel.copy()
    required_base = {"date", "code", "open", "close", "amount"}
    missing_base = sorted(required_base - set(panel.columns))
    if missing_base:
        raise ValueError(f"基础日线缺少列：{', '.join(missing_base)}")
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    panel["code"] = panel["code"].map(normalize_code)
    panel = panel[panel["date"].isin(normalized_dates)].copy()
    panel = panel.sort_values(["date", "code"]).drop_duplicates(
        ["date", "code"], keep="last"
    )
    panel.attrs.update(panel_attrs)
    stocks, date_map, stock_map = january.make_axes(panel, normalized_dates)
    if membership.shape != (len(normalized_dates), len(stocks)):
        raise ValueError(
            f"membership 形状错误：{membership.shape}，应为 {(len(normalized_dates), len(stocks))}"
        )
    arrays = historical.panel_arrays(
        panel,
        normalized_dates,
        stocks,
        date_map,
        stock_map,
        columns=("open", "close", "amount", "qfq_ratio", "raw_close"),
    )
    required_arrays = {"open", "close", "amount", "open_exit_mark", "close_exit_mark"}
    missing_arrays = sorted(required_arrays - set(arrays))
    if missing_arrays:
        raise ValueError(f"基础日线无法生成这些字段：{', '.join(missing_arrays)}")
    core_settings = historical.BacktestSettings(
        qlib_dir=Path(settings.qlib_dir),
        start=str(normalized_dates[0].date()),
        backtest_start=settings.start,
        end=settings.end,
        report_start=settings.start,
        report_end=settings.end,
        hold_days=settings.hold_days,
        groups=settings.groups,
        min_stocks=settings.min_stocks,
        min_price=settings.min_price,
        min_avg_amount=settings.min_avg_amount,
        buy_cost=settings.buy_cost,
        sell_cost=settings.sell_cost,
    )
    eligible = historical.build_eligible(arrays, membership.astype(bool), core_settings)
    return {
        "panel": panel,
        "dates": normalized_dates,
        "stocks": stocks,
        "date_map": date_map,
        "stock_map": stock_map,
        "arrays": arrays,
        "eligible": eligible,
    }


def _calculate_prepared_factors(
    values: pd.DataFrame,
    metadata: pd.DataFrame,
    market: Mapping[str, object],
    settings: ShortBacktestSettings,
) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    dates = market["dates"]
    stocks = market["stocks"]
    date_map = market["date_map"]
    stock_map = market["stock_map"]
    arrays = market["arrays"]
    eligible = market["eligible"]
    assert isinstance(dates, pd.DatetimeIndex)
    assert isinstance(stocks, list)
    assert isinstance(date_map, dict)
    assert isinstance(stock_map, dict)
    assert isinstance(arrays, dict)
    assert isinstance(eligible, np.ndarray)
    ic_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    grouped_values = values.groupby("factor_id", sort=False)
    empty_values = values.iloc[:0]
    for meta in metadata.itertuples(index=False):
        current = (
            grouped_values.get_group(meta.factor_id)
            if meta.factor_id in grouped_values.indices
            else empty_values
        )
        factor = january.panel_to_matrix(
            current,
            "value",
            dates,
            stocks,
            date_map,
            stock_map,
            dtype=np.float32,
        )
        ic_frame = calculate_rank_ic(
            meta.factor_id,
            factor,
            meta.frequency,
            arrays,
            eligible,
            dates,
            settings,
        )
        if not ic_frame.empty:
            ic_frames.append(ic_frame)
        trade_frame = backtest_both_sides(
            meta.factor_id,
            factor,
            meta.frequency,
            arrays,
            eligible,
            dates,
            settings,
        )
        if not trade_frame.empty:
            trade_frames.append(trade_frame)
    return ic_frames, trade_frames


def _assemble_output_frames(
    metadata: pd.DataFrame,
    ic_frames: list[pd.DataFrame],
    trade_frames: list[pd.DataFrame],
    settings: ShortBacktestSettings,
) -> dict[str, pd.DataFrame]:
    rank_ic = (
        pd.concat(ic_frames, ignore_index=True)
        if ic_frames
        else pd.DataFrame(columns=RANK_IC_COLUMNS)
    )
    rebalance = (
        pd.concat(trade_frames, ignore_index=True)
        if trade_frames
        else pd.DataFrame(columns=REBALANCE_COLUMNS)
    )
    rank_ic = _attach_metadata(rank_ic, metadata)
    rebalance = _attach_metadata(rebalance, metadata)
    if not rank_ic.empty:
        rank_ic = rank_ic.sort_values(["factor_id", "signal_date"]).reset_index(drop=True)
    if not rebalance.empty:
        rebalance = rebalance.sort_values(
            ["factor_id", "signal_date", "side"]
        ).reset_index(drop=True)
    summary, yearly = summarize_results(
        metadata,
        rank_ic,
        rebalance,
        report_year=int(pd.Timestamp(settings.start).year),
    )
    return {
        "rank_ic": rank_ic,
        "rebalance_returns": rebalance,
        "summary": summary,
        "yearly": yearly,
    }


def run_backtests(
    factor_values: pd.DataFrame,
    base_panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    membership: np.ndarray,
    settings: ShortBacktestSettings,
) -> dict[str, object]:
    """对内存中的合成长表或真实长表执行完整计算。"""

    _validate_settings(settings)
    input_rows = len(factor_values)
    values, metadata = prepare_factor_values(factor_values)
    lower, upper = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    in_period = values["date"].between(lower, upper)
    excluded = values["date"].isin(EXCLUDED_SIGNAL_DATES)
    excluded_rows = int((in_period & excluded).sum())
    values = values[in_period & ~excluded].copy()
    market = _prepare_market_context(base_panel, dates, membership, settings)
    ic_frames, trade_frames = _calculate_prepared_factors(
        values, metadata, market, settings
    )
    outputs: dict[str, object] = _assemble_output_frames(
        metadata, ic_frames, trade_frames, settings
    )
    panel = market["panel"]
    normalized_dates = market["dates"]
    assert isinstance(panel, pd.DataFrame)
    assert isinstance(normalized_dates, pd.DatetimeIndex)
    outputs["methodology"] = _build_methodology(
        settings,
        metadata,
        len(values),
        panel,
        normalized_dates,
        membership,
        outputs,  # type: ignore[arg-type]
        input_rows=input_rows,
        excluded_rows=excluded_rows,
    )
    return outputs


def _stat_text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _factor_row_groups(path: Path) -> dict[str, list[int]] | None:
    """识别上游按因子逐组写出的 Parquet；不能确认时返回 None。"""

    parquet = pq.ParquetFile(path)
    names = parquet.schema_arrow.names
    if any(column not in names for column in REQUIRED_FACTOR_COLUMNS):
        return None
    factor_position = names.index("factor_id")
    groups: dict[str, list[int]] = {}
    for row_group in range(parquet.num_row_groups):
        statistics = parquet.metadata.row_group(row_group).column(factor_position).statistics
        if statistics is None or not statistics.has_min_max:
            return None
        minimum = _stat_text(statistics.min)
        maximum = _stat_text(statistics.max)
        if not minimum or minimum != maximum:
            return None
        groups.setdefault(minimum, []).append(row_group)
    return groups or None


def _find_streamable_parquet(
    factor_input: str | Path,
) -> tuple[Path, dict[str, list[int]]] | None:
    candidates: list[tuple[Path, dict[str, list[int]]]] = []
    for path in _discover_factor_files(factor_input):
        if path.suffix.lower() not in {".parquet", ".pq"}:
            continue
        try:
            groups = _factor_row_groups(path)
        except Exception:
            groups = None
        if groups is not None:
            candidates.append((path, groups))
    return candidates[0] if len(candidates) == 1 else None


def _read_one_factor_from_parquet(
    path: Path,
    factor_id: str,
    row_groups: list[int],
    settings: ShortBacktestSettings,
) -> pd.DataFrame:
    filters = [
        ("factor_id", "==", factor_id),
        ("date", ">=", pd.Timestamp(settings.start)),
        ("date", "<=", pd.Timestamp(settings.end)),
    ]
    try:
        frame = pd.read_parquet(
            path,
            columns=list(REQUIRED_FACTOR_COLUMNS),
            filters=filters,
        )
    except (TypeError, ValueError):
        parquet = pq.ParquetFile(path)
        frame = parquet.read_row_groups(
            row_groups, columns=list(REQUIRED_FACTOR_COLUMNS)
        ).to_pandas()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame[
            frame["factor_id"].astype(str).eq(factor_id)
            & frame["date"].between(settings.start, settings.end)
        ].copy()
    if not frame.empty:
        return frame
    # 即使区间内没有值，也保留该因子的说明，让 summary/yearly 仍有高低两组。
    parquet = pq.ParquetFile(path)
    batch = next(
        parquet.iter_batches(
            batch_size=1,
            row_groups=[row_groups[0]],
            columns=list(REQUIRED_FACTOR_COLUMNS),
        )
    )
    return batch.to_pandas()


def _run_streamable_parquet(
    factor_path: Path,
    factor_groups: dict[str, list[int]],
    settings: ShortBacktestSettings,
) -> dict[str, object]:
    """一次只读一个因子，避免真实长表同时出现多份内存副本。"""

    _validate_settings(settings)
    history_start = pd.Timestamp(settings.start) - pd.Timedelta(
        days=settings.lookback_calendar_days
    )
    dates = january.read_calendar(
        Path(settings.qlib_dir), str(history_start.date()), settings.end
    )
    if dates.empty:
        raise ValueError("指定范围内没有交易日")
    all_base_codes = [path.stem for path in Path(settings.base_cache_dir).glob("*.parquet")]
    base_panel = load_base_panel(
        Path(settings.base_cache_dir), all_base_codes, dates[0], dates[-1]
    )
    _require_requested_end(dates, base_panel, settings)
    stocks, _, _ = january.make_axes(base_panel, dates)
    membership_rows = january.read_membership_rows(
        Path(settings.qlib_dir), str(dates[0].date()), str(dates[-1].date())
    )
    membership, _ = january.build_membership(dates, stocks, membership_rows)
    market = _prepare_market_context(base_panel, dates, membership, settings)

    metadata_frames: list[pd.DataFrame] = []
    ic_frames: list[pd.DataFrame] = []
    trade_frames: list[pd.DataFrame] = []
    usable_rows = 0
    excluded_rows = 0
    parquet = pq.ParquetFile(factor_path)
    input_rows = sum(
        parquet.metadata.row_group(row_group).num_rows
        for row_groups in factor_groups.values()
        for row_group in row_groups
    )
    lower, upper = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    for factor_id, row_groups in factor_groups.items():
        piece = _read_one_factor_from_parquet(
            factor_path, factor_id, row_groups, settings
        )
        values, metadata = prepare_factor_values(piece)
        if len(metadata) != 1 or str(metadata.iloc[0]["factor_id"]) != factor_id:
            raise ValueError(f"Parquet 分组与 factor_id 不一致：{factor_id}")
        in_period = values["date"].between(lower, upper)
        excluded = values["date"].isin(EXCLUDED_SIGNAL_DATES)
        excluded_rows += int((in_period & excluded).sum())
        values = values[in_period & ~excluded].copy()
        usable_rows += len(values)
        metadata_frames.append(metadata)
        current_ic, current_trades = _calculate_prepared_factors(
            values, metadata, market, settings
        )
        ic_frames.extend(current_ic)
        trade_frames.extend(current_trades)

    metadata = pd.concat(metadata_frames, ignore_index=True).sort_values("factor_id")
    outputs: dict[str, object] = _assemble_output_frames(
        metadata, ic_frames, trade_frames, settings
    )
    panel = market["panel"]
    normalized_dates = market["dates"]
    assert isinstance(panel, pd.DataFrame)
    assert isinstance(normalized_dates, pd.DatetimeIndex)
    outputs["methodology"] = _build_methodology(
        settings,
        metadata,
        usable_rows,
        panel,
        normalized_dates,
        membership,
        outputs,  # type: ignore[arg-type]
        input_rows=input_rows,
        excluded_rows=excluded_rows,
    )
    methodology = outputs["methodology"]
    assert isinstance(methodology, dict)
    methodology["sources"]["factor_input"] = [str(factor_path.resolve())]  # type: ignore[index]
    methodology["sources"]["factor_reading"] = "按 factor_id 分批读取"  # type: ignore[index]
    return outputs


def run_from_files(
    factor_input: str | Path, settings: ShortBacktestSettings
) -> dict[str, object]:
    """读取因子、基础日线和历史成分，再执行回测。"""

    streamable = _find_streamable_parquet(factor_input)
    if streamable is not None:
        return _run_streamable_parquet(streamable[0], streamable[1], settings)

    factor_values = load_factor_values(factor_input)
    history_start = pd.Timestamp(settings.start) - pd.Timedelta(
        days=settings.lookback_calendar_days
    )
    dates = january.read_calendar(
        Path(settings.qlib_dir), str(history_start.date()), settings.end
    )
    if dates.empty:
        raise ValueError("指定范围内没有交易日")
    factor_codes = factor_values["code"].dropna().map(normalize_code).unique()
    base_panel = load_base_panel(
        Path(settings.base_cache_dir),
        factor_codes,
        dates[0],
        dates[-1],
    )
    _require_requested_end(dates, base_panel, settings)
    stocks, _, _ = january.make_axes(base_panel, dates)
    membership_rows = january.read_membership_rows(
        Path(settings.qlib_dir), str(dates[0].date()), str(dates[-1].date())
    )
    membership, _ = january.build_membership(dates, stocks, membership_rows)
    outputs = run_backtests(factor_values, base_panel, dates, membership, settings)
    methodology = outputs["methodology"]
    assert isinstance(methodology, dict)
    source_files = factor_values.attrs.get("source_files", [str(Path(factor_input).resolve())])
    methodology["sources"]["factor_input"] = source_files  # type: ignore[index]
    methodology["sources"]["skipped_input_files"] = factor_values.attrs.get(  # type: ignore[index]
        "skipped_files", []
    )
    return outputs


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def _atomic_write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
    os.replace(temporary, path)


def write_outputs(outputs: Mapping[str, object], output_dir: Path) -> None:
    """保存五个约定结果文件。"""

    output_dir = Path(output_dir)
    for key, filename in {
        "rank_ic": "rank_ic.csv",
        "rebalance_returns": "rebalance_returns.csv",
        "summary": "summary.csv",
        "yearly": "yearly.csv",
    }.items():
        frame = outputs[key]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{key} 不是表格")
        _atomic_write_csv(frame, output_dir / filename)
    _atomic_write_json(outputs["methodology"], output_dir / "methodology.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-input", type=Path, required=True)
    parser.add_argument(
        "--base-cache-dir",
        type=Path,
        default=ShortBacktestSettings.base_cache_dir,
    )
    parser.add_argument(
        "--qlib-dir", type=Path, default=ShortBacktestSettings.qlib_dir
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ShortBacktestSettings.output_dir
    )
    parser.add_argument("--start", default=ShortBacktestSettings.start)
    parser.add_argument("--end", default=ShortBacktestSettings.end)
    parser.add_argument("--lookback-calendar-days", type=int, default=90)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--groups", type=int, default=10)
    parser.add_argument("--min-stocks", type=int, default=50)
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--min-avg-amount", type=float, default=5_000_000.0)
    parser.add_argument("--buy-cost", type=float, default=0.0003)
    parser.add_argument("--sell-cost", type=float, default=0.0013)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = ShortBacktestSettings(
        base_cache_dir=args.base_cache_dir,
        qlib_dir=args.qlib_dir,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        lookback_calendar_days=args.lookback_calendar_days,
        hold_days=args.hold_days,
        groups=args.groups,
        min_stocks=args.min_stocks,
        min_price=args.min_price,
        min_avg_amount=args.min_avg_amount,
        buy_cost=args.buy_cost,
        sell_cost=args.sell_cost,
    )
    outputs = run_from_files(args.factor_input, settings)
    write_outputs(outputs, settings.output_dir)
    summary = outputs["summary"]
    assert isinstance(summary, pd.DataFrame)
    print(
        f"已完成 {summary['factor_id'].nunique()} 个因子，高组和低组共 {len(summary)} 行；"
        f"结果在 {settings.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
