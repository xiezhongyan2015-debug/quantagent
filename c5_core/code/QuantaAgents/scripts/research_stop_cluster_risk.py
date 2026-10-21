# -*- coding: utf-8 -*-
"""用 Level 2 数据研究潜在止损区与破位后的连续卖出风险。

本程序不声称能看到真实止损单，也不识别投资者身份。它只做两件事：

1. 用破位前的重复低点、反弹和附近主动净买入计算“潜在止损密集度”；
2. 用破位前的主动卖出、十档买盘和买盘减弱计算“易扫程度”，再检查
   破位后十秒内是否真的出现连续卖出。

所有评分只使用破位时刻之前的数据。破位后的字段只用于评价。
原始 Level 2 目录只读，研究结果写入 data_cache。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data_cache" / "stop_cluster_risk"
RAW_FILE_PATTERN = re.compile(
    r"part-\d+-(\d{6})_(SZ|SH)-(\d{6})_(SZ|SH)\.parquet$",
    re.IGNORECASE,
)
KNOWN_BAD_DATES = {
    "2026-03-12",
    "2026-04-14",
    "2026-05-12",
    "2026-07-06",
    "2026-07-09",
    "2026-07-10",
    "2026-07-13",
    "2026-07-15",
}
OUTCOME_COLUMNS = [
    "post_sell_amount_10s",
    "post_buy_amount_10s",
    "excess_sell_amount_10s",
    "continuation_ticks_10s",
    "cascade_10s",
    "reclaim_ts",
    "reclaimed_60s",
]
CACHE_VERSION = 2


@dataclass(frozen=True)
class ResearchSettings:
    """首版研究使用的固定参数。"""

    lookback_minutes: int = 20
    history_gap_seconds: int = 60
    min_history_quotes: int = 240
    min_range_ratio: float = 0.0025
    max_range_ratio: float = 0.015
    min_break_ticks: int = 1
    max_break_range_share: float = 0.35
    min_touch_episodes: int = 2
    min_touch_separation_seconds: int = 60
    min_rebound_ticks: int = 3
    pre_sell_seconds: int = 30
    outcome_seconds: int = 10
    reclaim_seconds: int = 60
    cascade_ticks: int = 3
    cooldown_minutes: int = 20


def discover_level2_root() -> Path:
    """优先读取环境变量，否则在 F 盘寻找唯一的 Level 2 目录。"""

    configured = os.getenv("QUANTA_LEVEL2_ROOT")
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path.resolve()
        raise FileNotFoundError(f"QUANTA_LEVEL2_ROOT 不存在：{path}")
    candidates = sorted(Path("F:/").glob("2026*/l2_a_share_parquet"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"预期在 F 盘找到一个 Level 2 目录，实际找到 {len(candidates)} 个"
        )
    return candidates[0].resolve()


def normalize_codes(raw: str | Iterable[str]) -> list[str]:
    """统一股票代码格式，并限制首版只研究普通沪深股票。"""

    values = raw.split(",") if isinstance(raw, str) else list(raw)
    result: list[str] = []
    for value in values:
        code = str(value).strip().lower()
        if not code:
            continue
        if re.fullmatch(r"\d{6}", code):
            code = ("sh" if code.startswith(("6", "9")) else "sz") + code
        if not re.fullmatch(r"(?:sh6\d{5}|sz[03]\d{5})", code):
            raise ValueError(f"首版只接受普通沪深股票代码，收到：{value}")
        if code not in result:
            result.append(code)
    if not result:
        raise ValueError("至少需要一个股票代码")
    return result


def available_dates(root: Path, start: str, end: str) -> list[str]:
    """返回行情快照和逐笔成交都存在的交易日。"""

    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    if start_date > end_date:
        raise ValueError("start 不能晚于 end")

    def dates_for(kind: str) -> set[str]:
        values: set[str] = set()
        for path in (root / kind).glob("trade_date=*"):
            if not path.is_dir() or "=" not in path.name:
                continue
            value = path.name.split("=", 1)[1]
            try:
                date = pd.Timestamp(value)
            except ValueError:
                continue
            if start_date <= date <= end_date:
                values.add(value)
        return values

    return sorted(
        (dates_for("quotes") & dates_for("trades")) - KNOWN_BAD_DATES
    )


def files_for_codes(root: Path, kind: str, date: str, codes: Iterable[str]) -> list[Path]:
    """根据文件名中的股票代码范围，只选可能包含目标股票的文件。"""

    numbers = sorted({int(str(code)[-6:]) for code in codes})
    directory = root / kind / f"trade_date={date}"
    selected: list[Path] = []
    for path in sorted(directory.glob("*.parquet")):
        match = RAW_FILE_PATTERN.match(path.name)
        if match is None:
            selected.append(path)
            continue
        lower = int(match.group(1))
        upper = int(match.group(3))
        if any(lower <= number <= upper for number in numbers):
            selected.append(path)
    if not selected:
        raise FileNotFoundError(f"{date} 没有找到 {kind} 文件")
    return selected


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _parquet_source(paths: Sequence[Path]) -> str:
    values = ",".join(f"'{_sql_path(path)}'" for path in paths)
    return f"read_parquet([{values}])"


def _code_filter(codes: Sequence[str]) -> str:
    return ",".join("'" + code.replace("'", "''") + "'" for code in codes)


def load_day_data(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    date: str,
    codes: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """只读取指定日期和股票的十档行情与正常逐笔成交。"""

    quote_files = files_for_codes(root, "quotes", date, codes)
    trade_files = files_for_codes(root, "trades", date, codes)
    code_filter = _code_filter(codes)
    session_filter = """
        (
            (CAST(event_ts AS TIME) >= TIME '09:30:00'
             AND CAST(event_ts AS TIME) < TIME '11:30:00')
            OR
            (CAST(event_ts AS TIME) >= TIME '13:00:00'
             AND CAST(event_ts AS TIME) < TIME '14:57:00')
        )
    """
    quote_fields = [
        "code",
        "event_ts",
        "source_row_no",
        "last_price_x1e4 / 10000.0 AS last_price",
    ]
    for side in ("bid", "ask"):
        for level in range(1, 11):
            quote_fields.append(
                f"{side}_price_{level}_x1e4 / 10000.0 AS {side}_price_{level}"
            )
            quote_fields.append(f"{side}_volume_{level}")
    quotes = connection.execute(
        f"""
        SELECT {', '.join(quote_fields)}
        FROM {_parquet_source(quote_files)}
        WHERE code IN ({code_filter})
          AND last_price_x1e4 > 0
          AND bid_price_1_x1e4 > 0
          AND ask_price_1_x1e4 > 0
          AND {session_filter}
        ORDER BY code, event_ts, source_row_no
        """
    ).fetchdf()
    trades = connection.execute(
        f"""
        SELECT
            code,
            event_ts,
            source_row_no,
            bs_flag,
            trade_price_x1e4 / 10000.0 AS trade_price,
            trade_quantity,
            trade_price_x1e4::DOUBLE * trade_quantity / 10000.0 AS trade_amount,
            bid_order_id,
            ask_order_id
        FROM {_parquet_source(trade_files)}
        WHERE code IN ({code_filter})
          AND bs_flag IN ('B', 'S')
          AND trade_price_x1e4 > 0
          AND trade_quantity > 0
          AND {session_filter}
        ORDER BY code, event_ts, source_row_no
        """
    ).fetchdf()
    return quotes, trades


def _session_number(values: pd.Series) -> np.ndarray:
    hours = pd.to_datetime(values).dt.hour.to_numpy()
    return np.where(hours < 12, 1, 2).astype(np.int8)


def _shifted_rolling(
    values: pd.Series,
    times: pd.DatetimeIndex,
    *,
    window_minutes: int,
    gap_seconds: int,
    min_periods: int,
    operation: str,
    quantile: float | None = None,
) -> np.ndarray:
    """计算截至当前时刻前一段空白期的滚动统计。"""

    series = pd.Series(values.to_numpy(float), index=times)
    rolling = series.rolling(f"{window_minutes}min", min_periods=min_periods)
    if operation == "quantile":
        assert quantile is not None
        result = rolling.quantile(quantile)
    elif operation == "count":
        result = rolling.count()
    else:
        raise ValueError(f"不支持的滚动统计：{operation}")
    result.index = result.index + pd.Timedelta(seconds=gap_seconds)
    aligned = result.reindex(times, method="ffill")
    return aligned.to_numpy(float)


def prepare_quote_state(
    quotes: pd.DataFrame,
    settings: ResearchSettings,
    *,
    tick_size: float = 0.01,
) -> pd.DataFrame:
    """为每条十档行情加入只依赖更早资料的箱体与盘口指标。"""

    if quotes.empty:
        return quotes.copy()
    frame = quotes.copy()
    frame["event_ts"] = pd.to_datetime(frame["event_ts"])
    frame["session_no"] = _session_number(frame["event_ts"])
    frame["mid_price"] = (frame["bid_price_1"] + frame["ask_price_1"]) / 2.0
    frame["spread_ticks"] = (
        (frame["ask_price_1"] - frame["bid_price_1"]) / tick_size
    )
    frame["bid_depth_3"] = sum(
        frame[f"bid_price_{level}"] * frame[f"bid_volume_{level}"]
        for level in range(1, 4)
    )
    frame["ask_depth_3"] = sum(
        frame[f"ask_price_{level}"] * frame[f"ask_volume_{level}"]
        for level in range(1, 4)
    )
    total_depth = frame["bid_depth_3"] + frame["ask_depth_3"]
    frame["book_imbalance_3"] = np.divide(
        frame["bid_depth_3"] - frame["ask_depth_3"],
        total_depth,
        out=np.full(len(frame), np.nan),
        where=total_depth.to_numpy(float) > 0,
    )

    groups: list[pd.DataFrame] = []
    for (_, _), group in frame.groupby(["code", "session_no"], sort=False):
        group = group.sort_values(["event_ts", "source_row_no"]).copy()
        times = pd.DatetimeIndex(group["event_ts"])
        common = {
            "window_minutes": settings.lookback_minutes,
            "gap_seconds": settings.history_gap_seconds,
            "min_periods": settings.min_history_quotes,
        }
        group["support_raw"] = _shifted_rolling(
            group["last_price"], times, operation="quantile", quantile=0.05, **common
        )
        group["range_high_raw"] = _shifted_rolling(
            group["last_price"], times, operation="quantile", quantile=0.95, **common
        )
        group["history_quote_count"] = _shifted_rolling(
            group["last_price"], times, operation="count", **common
        )
        group["support_price"] = (
            np.round(group["support_raw"] / tick_size) * tick_size
        )
        group["range_high"] = (
            np.round(group["range_high_raw"] / tick_size) * tick_size
        )
        group["range_width"] = group["range_high"] - group["support_price"]
        group["range_ratio"] = group["range_width"] / group["mid_price"]
        group["bid_depth_3_lag_6s"] = group["bid_depth_3"].shift(2)
        groups.append(group)
    return pd.concat(groups, ignore_index=True) if groups else frame.iloc[0:0].copy()


def count_touch_episodes(prices: np.ndarray, level: float, tick_size: float) -> int:
    """统计历史价格进入目标价位上下一个最小价位的独立次数。"""

    if prices.size == 0 or not np.isfinite(level):
        return 0
    near = np.abs(prices.astype(float) - float(level)) <= tick_size + 1e-9
    return int(np.sum(near & ~np.r_[False, near[:-1]]))


def support_touch_statistics(
    prices: np.ndarray,
    times: np.ndarray,
    level: float,
    tick_size: float,
    *,
    min_separation_seconds: int,
    min_rebound_ticks: int,
) -> tuple[int, float, float]:
    """统计相互分开的有效触底次数、附近停留秒数和随后反弹档数。"""

    if prices.size == 0 or times.size != prices.size or not np.isfinite(level):
        return 0, 0.0, 0.0
    price_values = prices.astype(float)
    time_values = times.astype("datetime64[ns]")
    near = np.abs(price_values - float(level)) <= tick_size + 1e-9
    if not bool(near.any()):
        return 0, 0.0, 0.0
    if len(time_values) > 1:
        gaps = np.diff(time_values).astype("timedelta64[ms]").astype(float) / 1000.0
        normal_gaps = gaps[(gaps > 0) & (gaps <= 10)]
        quote_seconds = float(np.median(normal_gaps)) if normal_gaps.size else 3.0
    else:
        quote_seconds = 3.0
    near_seconds = float(near.sum()) * quote_seconds

    starts = np.flatnonzero(near & ~np.r_[False, near[:-1]])
    accepted_times: list[np.datetime64] = []
    rebounds: list[float] = []
    separation = np.timedelta64(min_separation_seconds, "s")
    horizon = np.timedelta64(60, "s")
    for start in starts:
        touch_time = time_values[start]
        if accepted_times and touch_time - accepted_times[-1] < separation:
            continue
        future = (
            (time_values > touch_time)
            & (time_values <= touch_time + horizon)
        )
        if not bool(future.any()):
            continue
        rebound_ticks = (
            float(np.max(price_values[future])) - float(level)
        ) / tick_size
        if rebound_ticks + 1e-9 < min_rebound_ticks:
            continue
        accepted_times.append(touch_time)
        rebounds.append(max(rebound_ticks, 0.0))
    median_rebound = float(np.median(rebounds)) if rebounds else 0.0
    return len(accepted_times), near_seconds, median_rebound


def visible_depth_to_level(
    row: pd.Series,
    level: float,
) -> tuple[float, bool]:
    """计算从买一到目标价位之间可见的买盘金额。"""

    bid_prices = np.asarray([row[f"bid_price_{i}"] for i in range(1, 11)], float)
    bid_volumes = np.asarray([row[f"bid_volume_{i}"] for i in range(1, 11)], float)
    valid = (bid_prices > 0) & (bid_volumes > 0)
    if not bool(valid.any()):
        return math.nan, False
    lowest_visible = float(np.min(bid_prices[valid]))
    highest_visible = float(np.max(bid_prices[valid]))
    inside = lowest_visible - 1e-9 <= level <= highest_visible + 1e-9
    if not inside:
        return math.nan, False
    selected = valid & (bid_prices >= level - 1e-9)
    return float(np.sum(bid_prices[selected] * bid_volumes[selected])), True


def score_stop_zone(
    *,
    touch_episodes: int,
    near_support_seconds: float,
    median_rebound_ticks: float,
    near_support_net_buy_ratio: float,
) -> float:
    """计算0至100分的潜在止损密集度，仅用于排序。"""

    touch_part = min(max(touch_episodes, 0) / 4.0, 1.0)
    stay_part = min(max(float(near_support_seconds), 0.0) / 120.0, 1.0)
    rebound_part = min(max(float(median_rebound_ticks), 0.0) / 8.0, 1.0)
    net_buy_part = min(max(float(near_support_net_buy_ratio), 0.0) / 5.0, 1.0)
    return float(
        30.0 * touch_part
        + 20.0 * stay_part
        + 20.0 * rebound_part
        + 30.0 * net_buy_part
    )


def score_sweep_ease(
    *,
    recent_sell_to_depth: float,
    book_imbalance: float,
    bid_withdrawal: float,
) -> float:
    """计算0至100分的易扫程度，仅使用破位前资料。"""

    reach_part = min(max(float(recent_sell_to_depth), 0.0), 1.0)
    imbalance_part = (1.0 - min(max(float(book_imbalance), -1.0), 1.0)) / 2.0
    withdrawal_part = min(max(float(bid_withdrawal), 0.0), 1.0)
    return float(50.0 * reach_part + 30.0 * imbalance_part + 20.0 * withdrawal_part)


def _window_slice(times: np.ndarray, start: pd.Timestamp, end: pd.Timestamp) -> slice:
    start_value = np.datetime64(start.to_datetime64())
    end_value = np.datetime64(end.to_datetime64())
    left = int(np.searchsorted(times, start_value, side="left"))
    right = int(np.searchsorted(times, end_value, side="left"))
    return slice(left, right)


def _trailing_sell_distribution(
    trades: pd.DataFrame,
    event_ts: pd.Timestamp,
    *,
    lookback_minutes: int,
    bin_seconds: int,
) -> tuple[float, float]:
    """计算破位前固定秒数窗口主动卖出金额的中位数和95%分位数。"""

    start = event_ts - pd.Timedelta(minutes=lookback_minutes)
    values = trades.loc[
        trades["event_ts"].ge(start)
        & trades["event_ts"].lt(event_ts)
        & trades["bs_flag"].eq("S"),
        ["event_ts", "trade_amount"],
    ]
    bin_count = max(int(lookback_minutes * 60 / bin_seconds), 1)
    if values.empty:
        amounts = np.zeros(bin_count, dtype=float)
    else:
        seconds = (
            (pd.to_datetime(values["event_ts"]) - start).dt.total_seconds().to_numpy()
        )
        indices = np.floor(seconds / bin_seconds).astype(int)
        valid = (indices >= 0) & (indices < bin_count)
        amounts = np.bincount(
            indices[valid],
            weights=values["trade_amount"].to_numpy(float)[valid],
            minlength=bin_count,
        ).astype(float)
    return float(np.median(amounts)), float(np.quantile(amounts, 0.95))


def _first_reclaim(
    quotes: pd.DataFrame,
    event_ts: pd.Timestamp,
    support_price: float,
    seconds: int,
) -> pd.Timestamp | pd.NaT:
    future = quotes.loc[
        quotes["event_ts"].gt(event_ts)
        & quotes["event_ts"].le(event_ts + pd.Timedelta(seconds=seconds))
    ].sort_values("event_ts")
    if len(future) < 2:
        return pd.NaT
    above = future["mid_price"].to_numpy(float) >= support_price - 1e-9
    times = pd.to_datetime(future["event_ts"]).to_numpy()
    for index in range(1, len(future)):
        if above[index - 1] and above[index]:
            gap = pd.Timestamp(times[index]) - pd.Timestamp(times[index - 1])
            if gap <= pd.Timedelta(seconds=6):
                return pd.Timestamp(times[index])
    return pd.NaT


def analyze_code_day(
    quotes: pd.DataFrame,
    trades: pd.DataFrame,
    settings: ResearchSettings,
    *,
    tick_size: float = 0.01,
) -> pd.DataFrame:
    """提取单只股票一天内的破位事件，并计算事前评分和事后结果。"""

    output_columns = [
        "candidate_id",
        "date",
        "code",
        "breach_ts",
        "support_price",
        "range_high",
        "range_width",
        "touch_episodes",
        "near_support_seconds",
        "median_rebound_ticks",
        "near_support_buy_share",
        "near_support_net_buy_amount",
        "near_support_net_buy_ratio",
        "stop_density_score",
        "sweep_ease_score",
        "combined_risk_score",
        "pre_sell_amount_30s",
        "visible_bid_amount_to_support",
        "support_inside_ten_levels",
        "recent_sell_to_depth",
        "book_imbalance_3",
        "bid_withdrawal_6s",
        "spread_ticks",
        "break_ticks",
        "baseline_sell_10s_median",
        "baseline_sell_10s_p95",
        "post_sell_amount_10s",
        "post_buy_amount_10s",
        "excess_sell_amount_10s",
        "continuation_ticks_10s",
        "cascade_10s",
        "reclaim_ts",
        "reclaimed_60s",
    ]
    if quotes.empty or trades.empty:
        return pd.DataFrame(columns=output_columns)

    quote_state = prepare_quote_state(quotes, settings, tick_size=tick_size)
    quote_state = quote_state.sort_values(["event_ts", "source_row_no"]).reset_index(drop=True)
    trade_frame = trades.sort_values(["event_ts", "source_row_no"]).reset_index(drop=True).copy()
    quote_state["event_ts"] = pd.to_datetime(quote_state["event_ts"])
    trade_frame["event_ts"] = pd.to_datetime(trade_frame["event_ts"])
    trade_frame["trade_position"] = np.arange(len(trade_frame), dtype=np.int64)

    state_columns = [
        "event_ts",
        "last_price",
        "mid_price",
        "support_price",
        "range_high",
        "range_width",
        "range_ratio",
        "history_quote_count",
        "book_imbalance_3",
        "bid_depth_3",
        "bid_depth_3_lag_6s",
        "spread_ticks",
    ]
    for side in ("bid", "ask"):
        for level in range(1, 11):
            state_columns.extend([f"{side}_price_{level}", f"{side}_volume_{level}"])
    state = quote_state[state_columns].rename(columns={"event_ts": "quote_ts"})
    sells = trade_frame.loc[trade_frame["bs_flag"].eq("S")].copy()
    aligned = pd.merge_asof(
        sells.sort_values("event_ts"),
        state.sort_values("quote_ts"),
        left_on="event_ts",
        right_on="quote_ts",
        direction="backward",
        tolerance=pd.Timedelta(seconds=4),
        allow_exact_matches=False,
    )
    candidates = aligned.loc[
        aligned["support_price"].notna()
        & aligned["range_width"].gt(0)
        & aligned["range_ratio"].between(
            settings.min_range_ratio, settings.max_range_ratio, inclusive="both"
        )
        & aligned["history_quote_count"].ge(settings.min_history_quotes)
        & aligned["trade_price"].le(
            aligned["support_price"] - settings.min_break_ticks * tick_size + 1e-9
        )
        & aligned["trade_price"].ge(
            aligned["support_price"]
            - settings.max_break_range_share * aligned["range_width"]
            - 1e-9
        )
        & aligned["last_price"].ge(aligned["support_price"] - 0.25 * tick_size)
        & aligned["spread_ticks"].le(3.0)
    ].sort_values(["event_ts", "source_row_no"])

    if candidates.empty:
        return pd.DataFrame(columns=output_columns)

    all_times = trade_frame["event_ts"].to_numpy(dtype="datetime64[ns]")
    quote_times = quote_state["event_ts"].to_numpy(dtype="datetime64[ns]")
    rows: list[dict[str, object]] = []
    last_event: pd.Timestamp | None = None
    for _, candidate in candidates.iterrows():
        event_ts = pd.Timestamp(candidate["event_ts"])
        if last_event is not None and event_ts - last_event < pd.Timedelta(
            minutes=settings.cooldown_minutes
        ):
            continue
        time_value = event_ts.time()
        if not (
            pd.Timestamp("09:50:00").time() <= time_value <= pd.Timestamp("11:25:00").time()
            or pd.Timestamp("13:20:00").time() <= time_value <= pd.Timestamp("14:50:00").time()
        ):
            continue

        support = float(candidate["support_price"])
        range_width = float(candidate["range_width"])
        history_start = event_ts - pd.Timedelta(
            minutes=settings.lookback_minutes,
            seconds=settings.history_gap_seconds,
        )
        history_end = event_ts - pd.Timedelta(seconds=settings.history_gap_seconds)
        quote_slice = _window_slice(quote_times, history_start, history_end)
        history_quotes = quote_state.iloc[quote_slice]
        touch_episodes, near_support_seconds, median_rebound_ticks = (
            support_touch_statistics(
                history_quotes["last_price"].to_numpy(float),
                history_quotes["event_ts"].to_numpy(dtype="datetime64[ns]"),
                support,
                tick_size,
                min_separation_seconds=settings.min_touch_separation_seconds,
                min_rebound_ticks=settings.min_rebound_ticks,
            )
        )
        if touch_episodes < settings.min_touch_episodes:
            continue

        history_trades = trade_frame.loc[
            trade_frame["event_ts"].ge(history_start)
            & trade_frame["event_ts"].lt(history_end)
        ]
        history_buys = history_trades.loc[history_trades["bs_flag"].eq("B")]
        history_sells = history_trades.loc[history_trades["bs_flag"].eq("S")]
        total_buy = float(history_buys["trade_amount"].sum())
        near_band = 5.0 * tick_size
        near_buy = float(
            history_buys.loc[
                history_buys["trade_price"].between(
                    support - 1e-9, support + near_band + 1e-9, inclusive="both"
                ),
                "trade_amount",
            ].sum()
        )
        near_sell = float(
            history_sells.loc[
                history_sells["trade_price"].between(
                    support - 1e-9, support + near_band + 1e-9, inclusive="both"
                ),
                "trade_amount",
            ].sum()
        )
        near_buy_share = near_buy / total_buy if total_buy > 0 else 0.0
        near_net_buy_amount = max(near_buy - near_sell, 0.0)
        minute_amounts = (
            history_trades.assign(
                minute=pd.to_datetime(history_trades["event_ts"]).dt.floor("min")
            )
            .groupby("minute", observed=True)["trade_amount"]
            .sum()
        )
        typical_minute_amount = float(minute_amounts.median()) if len(minute_amounts) else 0.0
        near_net_buy_ratio = (
            near_net_buy_amount / typical_minute_amount
            if typical_minute_amount > 0
            else 0.0
        )

        trade_position = int(candidate["trade_position"])
        pre_start = event_ts - pd.Timedelta(seconds=settings.pre_sell_seconds)
        pre_left = int(np.searchsorted(all_times, np.datetime64(pre_start), side="left"))
        pre_trades = trade_frame.iloc[pre_left:trade_position]
        pre_sell_amount = float(
            pre_trades.loc[pre_trades["bs_flag"].eq("S"), "trade_amount"].sum()
        )

        visible_depth, inside_book = visible_depth_to_level(candidate, support)
        if not inside_book or not np.isfinite(visible_depth) or visible_depth <= 0:
            continue
        recent_sell_to_depth = (
            pre_sell_amount / visible_depth
            if inside_book and np.isfinite(visible_depth) and visible_depth > 0
            else math.nan
        )
        previous_bid_depth = float(candidate["bid_depth_3_lag_6s"])
        current_bid_depth = float(candidate["bid_depth_3"])
        bid_withdrawal = (
            max(0.0, 1.0 - current_bid_depth / previous_bid_depth)
            if np.isfinite(previous_bid_depth) and previous_bid_depth > 0
            else 0.0
        )
        density_score = score_stop_zone(
            touch_episodes=touch_episodes,
            near_support_seconds=near_support_seconds,
            median_rebound_ticks=median_rebound_ticks,
            near_support_net_buy_ratio=near_net_buy_ratio,
        )
        ease_score = score_sweep_ease(
            recent_sell_to_depth=(recent_sell_to_depth if np.isfinite(recent_sell_to_depth) else 0.0),
            book_imbalance=float(candidate["book_imbalance_3"]),
            bid_withdrawal=bid_withdrawal,
        )

        outcome_end = event_ts + pd.Timedelta(seconds=settings.outcome_seconds)
        post_right = int(np.searchsorted(all_times, np.datetime64(outcome_end), side="right"))
        # 触发破位的成交单独记录，连续卖出只统计它之后的逐笔成交。
        post_trades = trade_frame.iloc[trade_position + 1 : post_right]
        post_sell_amount = float(
            post_trades.loc[post_trades["bs_flag"].eq("S"), "trade_amount"].sum()
        )
        post_buy_amount = float(
            post_trades.loc[post_trades["bs_flag"].eq("B"), "trade_amount"].sum()
        )
        post_min_price = (
            float(post_trades["trade_price"].min())
            if not post_trades.empty
            else math.nan
        )
        continuation_ticks = (
            max(0.0, (support - post_min_price) / tick_size)
            if np.isfinite(post_min_price)
            else 0.0
        )
        baseline_median, baseline_p95 = _trailing_sell_distribution(
            trade_frame,
            event_ts,
            lookback_minutes=settings.lookback_minutes,
            bin_seconds=settings.outcome_seconds,
        )
        cascade = bool(
            post_sell_amount > max(baseline_p95, 0.0)
            and post_sell_amount >= 2.0 * max(baseline_median, 1e-9)
            and continuation_ticks >= settings.cascade_ticks - 1e-9
        )
        reclaim_ts = _first_reclaim(
            quote_state,
            event_ts,
            support,
            settings.reclaim_seconds,
        )
        code = str(candidate["code"])
        rows.append(
            {
                "candidate_id": f"{event_ts:%Y%m%d_%H%M%S%f}_{code}_{support:.3f}",
                "date": event_ts.normalize(),
                "code": code,
                "breach_ts": event_ts,
                "support_price": support,
                "range_high": float(candidate["range_high"]),
                "range_width": range_width,
                "touch_episodes": touch_episodes,
                "near_support_seconds": near_support_seconds,
                "median_rebound_ticks": median_rebound_ticks,
                "near_support_buy_share": near_buy_share,
                "near_support_net_buy_amount": near_net_buy_amount,
                "near_support_net_buy_ratio": near_net_buy_ratio,
                "stop_density_score": density_score,
                "sweep_ease_score": ease_score,
                "combined_risk_score": density_score * ease_score / 100.0,
                "pre_sell_amount_30s": pre_sell_amount,
                "visible_bid_amount_to_support": visible_depth,
                "support_inside_ten_levels": inside_book,
                "recent_sell_to_depth": recent_sell_to_depth,
                "book_imbalance_3": float(candidate["book_imbalance_3"]),
                "bid_withdrawal_6s": bid_withdrawal,
                "spread_ticks": float(candidate["spread_ticks"]),
                "break_ticks": (support - float(candidate["trade_price"])) / tick_size,
                "baseline_sell_10s_median": baseline_median,
                "baseline_sell_10s_p95": baseline_p95,
                "post_sell_amount_10s": post_sell_amount,
                "post_buy_amount_10s": post_buy_amount,
                "excess_sell_amount_10s": max(0.0, post_sell_amount - baseline_median),
                "continuation_ticks_10s": continuation_ticks,
                "cascade_10s": cascade,
                "reclaim_ts": reclaim_ts,
                "reclaimed_60s": bool(pd.notna(reclaim_ts)),
            }
        )
        last_event = event_ts
    return pd.DataFrame(rows, columns=output_columns)


def analyze_day(
    quotes: pd.DataFrame,
    trades: pd.DataFrame,
    settings: ResearchSettings,
) -> pd.DataFrame:
    """逐股票分析一天的数据。"""

    frames: list[pd.DataFrame] = []
    codes = sorted(set(quotes.get("code", pd.Series(dtype=str)).astype(str)))
    for code in codes:
        result = analyze_code_day(
            quotes.loc[quotes["code"].eq(code)].copy(),
            trades.loc[trades["code"].eq(code)].copy(),
            settings,
        )
        if not result.empty:
            frames.append(result)
    if not frames:
        return analyze_code_day(
            pd.DataFrame(), pd.DataFrame(), settings
        )
    return pd.concat(frames, ignore_index=True)


def _bucket_summary(frame: pd.DataFrame, column: str) -> list[dict[str, object]]:
    valid = frame.loc[frame[column].notna()].copy()
    if len(valid) < 3 or valid[column].nunique() < 2:
        return []
    bucket_count = min(3, int(valid[column].nunique()), len(valid))
    valid["bucket"] = pd.qcut(
        valid[column], q=bucket_count, duplicates="drop"
    ).astype(str)
    grouped = valid.groupby("bucket", observed=True)
    rows: list[dict[str, object]] = []
    for bucket, group in grouped:
        rows.append(
            {
                "bucket": bucket,
                "events": int(len(group)),
                "score_mean": float(group[column].mean()),
                "cascade_rate": float(group["cascade_10s"].mean()),
                "reclaim_rate": float(group["reclaimed_60s"].mean()),
                "excess_sell_amount_median": float(
                    group["excess_sell_amount_10s"].median()
                ),
                "continuation_ticks_median": float(
                    group["continuation_ticks_10s"].median()
                ),
            }
        )
    return rows


def _group_result(group: pd.DataFrame) -> dict[str, object]:
    if group.empty:
        return {
            "events": 0,
            "cascade_rate": None,
            "sell_burst_rate": None,
            "deep_break_rate": None,
            "reclaim_rate": None,
        }
    return {
        "events": int(len(group)),
        "cascade_rate": float(group["cascade_10s"].mean()),
        "sell_burst_rate": float(group["sell_burst_10s"].mean()),
        "deep_break_rate": float(group["deep_break_10s"].mean()),
        "reclaim_rate": float(group["reclaimed_60s"].mean()),
    }


def chronological_evaluation(
    events: pd.DataFrame,
    *,
    cascade_ticks: int = 3,
) -> dict[str, object]:
    """用较早日期定高低分界，只在最后四分之一日期检查区分效果。"""

    if events.empty:
        return {"available": False, "reason": "没有事件"}
    frame = events.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    dates = sorted(frame["date"].dropna().unique())
    if len(dates) < 4:
        return {"available": False, "reason": "少于4个交易日"}
    split_index = min(max(int(math.ceil(len(dates) * 0.75)), 1), len(dates) - 1)
    train_dates = dates[:split_index]
    test_dates = dates[split_index:]
    frame["sell_burst_10s"] = (
        frame["post_sell_amount_10s"].gt(frame["baseline_sell_10s_p95"])
        & frame["post_sell_amount_10s"].ge(
            2.0 * frame["baseline_sell_10s_median"].clip(lower=1e-9)
        )
    )
    frame["deep_break_10s"] = frame["continuation_ticks_10s"].ge(
        float(cascade_ticks) - 1e-9
    )
    train = frame.loc[frame["date"].isin(train_dates)].copy()
    test = frame.loc[frame["date"].isin(test_dates)].copy()

    score_results: dict[str, object] = {}
    for column in (
        "stop_density_score",
        "sweep_ease_score",
        "combined_risk_score",
    ):
        low_cut = float(train[column].quantile(0.20))
        high_cut = float(train[column].quantile(0.80))
        if not math.isfinite(low_cut) or not math.isfinite(high_cut):
            score_results[column] = {
                "available": False,
                "reason": "较早日期的分数不是有效数字",
                "low_cut_from_earlier_dates": low_cut,
                "high_cut_from_earlier_dates": high_cut,
            }
            continue
        if low_cut >= high_cut:
            score_results[column] = {
                "available": False,
                "reason": "较早日期的高低分界无法分开",
                "low_cut_from_earlier_dates": low_cut,
                "high_cut_from_earlier_dates": high_cut,
            }
            continue
        score_results[column] = {
            "available": True,
            "low_cut_from_earlier_dates": low_cut,
            "high_cut_from_earlier_dates": high_cut,
            "earlier_dates": {
                "low": _group_result(train.loc[train[column].le(low_cut)]),
                "high": _group_result(train.loc[train[column].ge(high_cut)]),
            },
            "later_dates": {
                "low": _group_result(test.loc[test[column].le(low_cut)]),
                "high": _group_result(test.loc[test[column].ge(high_cut)]),
            },
        }

    def stock_results(period: pd.DataFrame) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for code, group in period.groupby("code", sort=True):
            values = _group_result(group)
            values["code"] = str(code)
            rows.append(values)
        return rows

    return {
        "available": True,
        "earlier_dates": [str(pd.Timestamp(value).date()) for value in train_dates],
        "later_dates": [str(pd.Timestamp(value).date()) for value in test_dates],
        "earlier_event_count": int(len(train)),
        "later_event_count": int(len(test)),
        "scores": score_results,
        "by_stock_earlier_dates": stock_results(train),
        "by_stock_later_dates": stock_results(test),
    }


def summarize_events(
    events: pd.DataFrame,
    *,
    dates: Sequence[str],
    codes: Sequence[str],
    settings: ResearchSettings,
) -> dict[str, object]:
    """生成便于人工检查的小样本报告。"""

    summary: dict[str, object] = {
        "research_scope": {
            "dates": list(dates),
            "codes": list(codes),
            "settings": asdict(settings),
        },
        "important_limit": (
            "分数是潜在止损区和连续卖出风险的代理，不是真实止损单、账户或操盘意图。"
        ),
        "event_count": int(len(events)),
    }
    if events.empty:
        summary.update(
            {
                "cascade_count": 0,
                "cascade_rate": None,
                "reclaim_rate": None,
                "density_buckets": [],
                "ease_buckets": [],
                "combined_buckets": [],
                "chronological_check": chronological_evaluation(
                    events, cascade_ticks=settings.cascade_ticks
                ),
                "top_events": [],
            }
        )
        return summary
    summary.update(
        {
            "cascade_count": int(events["cascade_10s"].sum()),
            "cascade_rate": float(events["cascade_10s"].mean()),
            "reclaim_rate": float(events["reclaimed_60s"].mean()),
            "median_excess_sell_amount_10s": float(
                events["excess_sell_amount_10s"].median()
            ),
            "density_buckets": _bucket_summary(events, "stop_density_score"),
            "ease_buckets": _bucket_summary(events, "sweep_ease_score"),
            "combined_buckets": _bucket_summary(events, "combined_risk_score"),
            "chronological_check": chronological_evaluation(
                events, cascade_ticks=settings.cascade_ticks
            ),
        }
    )
    top_columns = [
        "code",
        "breach_ts",
        "support_price",
        "stop_density_score",
        "sweep_ease_score",
        "combined_risk_score",
        "cascade_10s",
        "excess_sell_amount_10s",
        "continuation_ticks_10s",
        "reclaimed_60s",
    ]
    summary["top_events"] = (
        events.sort_values("combined_risk_score", ascending=False)
        .head(20)[top_columns]
        .to_dict(orient="records")
    )
    return summary


def research_cache_key(
    *,
    root: Path,
    codes: Sequence[str],
    settings: ResearchSettings,
) -> str:
    """把资料目录、股票和参数写入缓存编号，避免误读别的试跑结果。"""

    payload = {
        "version": CACHE_VERSION,
        "root": str(root.resolve()),
        "codes": sorted(str(code) for code in codes),
        "settings": asdict(settings),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def run_research(
    *,
    root: Path,
    output_dir: Path,
    start: str,
    end: str,
    codes: Sequence[str],
    max_days: int | None,
    threads: int,
    memory_limit: str,
    overwrite: bool,
    settings: ResearchSettings | None = None,
) -> dict[str, object]:
    """按日期读取小样本并保存破位事件。"""

    settings = settings or ResearchSettings()
    dates = available_dates(root, start, end)
    if max_days is not None:
        if int(max_days) <= 0:
            raise ValueError("max_days 必须大于0")
        dates = dates[-int(max_days) :]
    if not dates:
        raise RuntimeError("指定时期没有可用交易日")
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_key = research_cache_key(root=root, codes=codes, settings=settings)
    daily_dir = output_dir / "daily" / cache_key
    daily_dir.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect()
    connection.execute(f"SET threads={max(int(threads), 1)}")
    connection.execute(f"SET memory_limit='{memory_limit}'")
    frames: list[pd.DataFrame] = []
    try:
        for index, date in enumerate(dates, start=1):
            target = daily_dir / f"{date}.parquet"
            if target.is_file() and not overwrite:
                daily = pd.read_parquet(target)
            else:
                quotes, trades = load_day_data(connection, root, date, codes)
                daily = analyze_day(quotes, trades, settings)
                daily.to_parquet(target, index=False)
            frames.append(daily)
            print(
                f"止损区研究 {index}/{len(dates)} {date}：{len(daily)} 个破位事件",
                flush=True,
            )
    finally:
        connection.close()

    events = (
        pd.concat(frames, ignore_index=True)
        if frames
        else analyze_code_day(pd.DataFrame(), pd.DataFrame(), settings)
    )
    candidate_path = output_dir / "stop_cluster_breach_candidates.parquet"
    outcome_path = output_dir / "stop_cluster_breach_outcomes.parquet"
    candidates = events.drop(columns=OUTCOME_COLUMNS, errors="ignore")
    outcome_keys = ["candidate_id", "date", "code", "breach_ts", "support_price"]
    outcomes = events[
        [column for column in outcome_keys + OUTCOME_COLUMNS if column in events.columns]
    ].copy()
    candidates.to_parquet(candidate_path, index=False)
    outcomes.to_parquet(outcome_path, index=False)
    summary = summarize_events(events, dates=dates, codes=codes, settings=settings)
    summary["cache_key"] = cache_key
    summary["candidate_path"] = str(candidate_path.resolve())
    summary["outcome_path"] = str(outcome_path.resolve())
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_path"] = str(summary_path.resolve())
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level2-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--codes",
        required=True,
        help="逗号分隔的普通股票代码，例如 sh600000,sz000001",
    )
    parser.add_argument("--max-days", type=int, default=5)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = (args.level2_root or discover_level2_root()).resolve()
    codes = normalize_codes(args.codes)
    summary = run_research(
        root=root,
        output_dir=args.output_dir.resolve(),
        start=args.start,
        end=args.end,
        codes=codes,
        max_days=args.max_days,
        threads=args.threads,
        memory_limit=args.memory_limit,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
