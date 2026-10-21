# -*- coding: utf-8 -*-
"""回测“高潜在止损密集区破位后站回买入、区间高位卖出”的简单策略。

规则在运行前固定：

1. 每只股票只用之前若干交易日的密集度分数确定高分界；
2. 破位后两条连续行情重新站上支撑，下一条行情才尝试买入；
3. 买入使用十档卖盘，卖出使用十档买盘，并限制为各档可见数量的一部分；
4. 遵守 A 股 T+1，当天买入的股票最早下一交易日卖出；
5. 下一交易日起，先到破位前区间高位则卖出，跌到止损位则卖出，
   三个交易日仍未触发则在最后一天临近收盘卖出；
6. 计入佣金最低收费和卖出印花税。

本程序研究的是历史行情下的可能成交结果，不代表真实止损单或未来收益。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Callable, Iterable, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESEARCH_DIR = PROJECT_ROOT / "data_cache" / "stop_cluster_risk_trial"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data_cache" / "stop_cluster_accumulation_backtest"
RAW_FILE_PATTERN = re.compile(
    r"part-\d+-(\d{6})_(SZ|SH)-(\d{6})_(SZ|SH)\.parquet$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BacktestSettings:
    """首版回测的固定参数。"""

    score_quantile: float = 0.80
    require_high_density: bool = True
    score_lookback_days: int = 20
    min_score_events: int = 20
    reclaim_seconds: int = 60
    entry_quote_delay_seconds: int = 6
    entry_cutoff: str = "14:45:00"
    exit_cutoff: str = "14:50:00"
    tick_size: float = 0.01
    max_entry_range_share: float = 0.25
    minimum_entry_ratio: float = 0.0005
    min_near_support_net_buy_ratio: float = 1.0
    min_target_net_margin: float = 0.002
    stop_range_share: float = 0.25
    minimum_stop_ratio: float = 0.001
    max_hold_days: int = 3
    initial_cash: float = 1_000_000.0
    position_cash: float = 200_000.0
    max_positions: int = 5
    lot_size: int = 100
    book_participation: float = 0.10
    execution_penalty_rate: float = 0.00025
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005


def settings_for_variant(name: str) -> BacktestSettings:
    """返回预先固定的严格版或简单版规则。"""

    if name == "strict":
        return BacktestSettings()
    if name in {"simple", "all_reclaims"}:
        return BacktestSettings(
            require_high_density=name == "simple",
            max_entry_range_share=0.50,
            min_near_support_net_buy_ratio=0.0,
            min_target_net_margin=0.0,
        )
    raise ValueError(f"不支持的规则版本：{name}")


def discover_level2_root() -> Path:
    """在 F 盘寻找唯一的 Level 2 资料目录。"""

    candidates = sorted(Path("F:/").glob("2026*/l2_a_share_parquet"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"预期在 F 盘找到一个 Level 2 目录，实际找到 {len(candidates)} 个"
        )
    return candidates[0].resolve()


def files_for_codes(root: Path, date: str, codes: Iterable[str]) -> list[Path]:
    """根据文件名中的代码范围，只选可能包含目标股票的行情文件。"""

    numbers = sorted({int(str(code)[-6:]) for code in codes})
    directory = root / "quotes" / f"trade_date={date}"
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
        raise FileNotFoundError(f"{date} 没有找到行情文件")
    return selected


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _parquet_source(paths: Sequence[Path]) -> str:
    values = ",".join(f"'{_sql_path(path)}'" for path in paths)
    return f"read_parquet([{values}])"


def _code_filter(codes: Sequence[str]) -> str:
    return ",".join("'" + code.replace("'", "''") + "'" for code in codes)


def quote_cache_key(root: Path, codes: Sequence[str]) -> str:
    payload = json.dumps(
        {"root": str(root.resolve()), "codes": sorted(codes), "version": 1},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def load_quote_day(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    date: str,
    codes: Sequence[str],
) -> pd.DataFrame:
    """读取指定股票一天的十档行情，保留买一或卖一为零的时刻。"""

    files = files_for_codes(root, date, codes)
    fields = [
        "code",
        "event_ts",
        "source_row_no",
        "last_price_x1e4 / 10000.0 AS last_price",
        "prev_close_x1e4 / 10000.0 AS prev_close",
    ]
    for side in ("bid", "ask"):
        for level in range(1, 11):
            fields.append(
                f"{side}_price_{level}_x1e4 / 10000.0 AS {side}_price_{level}"
            )
            fields.append(f"{side}_volume_{level}")
    result = connection.execute(
        f"""
        SELECT {', '.join(fields)}
        FROM {_parquet_source(files)}
        WHERE code IN ({_code_filter(codes)})
          AND (
            (CAST(event_ts AS TIME) >= TIME '09:30:00'
             AND CAST(event_ts AS TIME) < TIME '11:30:00')
            OR
            (CAST(event_ts AS TIME) >= TIME '13:00:00'
             AND CAST(event_ts AS TIME) < TIME '15:01:00')
          )
        ORDER BY code, event_ts, source_row_no
        """
    ).fetchdf()
    result["event_ts"] = pd.to_datetime(result["event_ts"])
    return result


class QuoteDayReader:
    """按日读取行情，并把精简结果保存在研究目录。"""

    def __init__(
        self,
        *,
        root: Path,
        codes: Sequence[str],
        cache_dir: Path,
        threads: int,
        memory_limit: str,
        overwrite: bool,
    ) -> None:
        self.root = root.resolve()
        self.codes = list(codes)
        self.cache_dir = cache_dir / quote_cache_key(self.root, self.codes)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.overwrite = overwrite
        self.connection = duckdb.connect()
        self.connection.execute(f"SET threads={max(int(threads), 1)}")
        self.connection.execute(f"SET memory_limit='{memory_limit}'")

    def close(self) -> None:
        self.connection.close()

    def __call__(self, date: str) -> pd.DataFrame:
        target = self.cache_dir / f"{date}.parquet"
        if target.is_file() and not self.overwrite:
            frame = pd.read_parquet(target)
            frame["event_ts"] = pd.to_datetime(frame["event_ts"])
            return frame
        frame = load_quote_day(self.connection, self.root, date, self.codes)
        frame.to_parquet(target, index=False)
        return frame


def _number(row: Mapping[str, object] | pd.Series, name: str) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def first_book_price(row: Mapping[str, object] | pd.Series, side: str) -> float:
    """返回买盘或卖盘中第一档有效价格。"""

    for level in range(1, 11):
        price = _number(row, f"{side}_price_{level}")
        volume = _number(row, f"{side}_volume_{level}")
        if price > 0 and volume > 0:
            return price
    return math.nan


def executable_book_vwap(
    row: Mapping[str, object] | pd.Series,
    *,
    side: str,
    shares: int,
    participation: float,
) -> float | None:
    """按十档可见数量的一部分计算整笔订单可能成交的均价。"""

    if side not in {"ask", "bid"}:
        raise ValueError("side 必须是 ask 或 bid")
    if shares <= 0 or not 0 < participation <= 1:
        return None
    remaining = int(shares)
    amount = 0.0
    for level in range(1, 11):
        price = _number(row, f"{side}_price_{level}")
        volume = _number(row, f"{side}_volume_{level}")
        if not (price > 0 and volume > 0):
            continue
        available = int(math.floor(volume * participation))
        if available <= 0:
            continue
        take = min(remaining, available)
        amount += take * price
        remaining -= take
        if remaining == 0:
            return amount / shares
    return None


def commission(amount: float, settings: BacktestSettings) -> float:
    return max(settings.minimum_commission, amount * settings.commission_rate)


def penalized_price(price: float, *, side: str, settings: BacktestSettings) -> float:
    """给三秒行情增加一小段不利价格，并按最小价位取整。"""

    if side == "buy":
        raw = price * (1.0 + settings.execution_penalty_rate)
        return math.ceil(raw / settings.tick_size - 1e-9) * settings.tick_size
    if side == "sell":
        raw = price * (1.0 - settings.execution_penalty_rate)
        return math.floor(raw / settings.tick_size + 1e-9) * settings.tick_size
    raise ValueError("side 必须是 buy 或 sell")


def affordable_entry(
    row: Mapping[str, object] | pd.Series,
    *,
    cash_budget: float,
    settings: BacktestSettings,
) -> tuple[int, float, float] | None:
    """计算资金和可见卖盘都允许的买入数量、均价和佣金。"""

    best_ask = first_book_price(row, "ask")
    if not best_ask > 0 or cash_budget <= settings.minimum_commission:
        return None
    desired = int(cash_budget / best_ask / settings.lot_size) * settings.lot_size
    for shares in range(desired, settings.lot_size - 1, -settings.lot_size):
        price = executable_book_vwap(
            row,
            side="ask",
            shares=shares,
            participation=settings.book_participation,
        )
        if price is None:
            continue
        price = penalized_price(price, side="buy", settings=settings)
        amount = price * shares
        fee = commission(amount, settings)
        if amount + fee <= cash_budget + 1e-9:
            return shares, price, fee
    return None


def prepare_signals(
    candidates: pd.DataFrame,
    outcomes: pd.DataFrame,
    trading_dates: Sequence[str],
    settings: BacktestSettings,
) -> pd.DataFrame:
    """只用每个事件之前的日期确定高密集分界。"""

    required_candidates = {
        "candidate_id",
        "date",
        "code",
        "breach_ts",
        "support_price",
        "range_high",
        "range_width",
        "near_support_net_buy_ratio",
        "stop_density_score",
    }
    missing = sorted(required_candidates - set(candidates.columns))
    if missing:
        raise ValueError(f"候选事件缺少字段：{', '.join(missing)}")
    if not {"candidate_id", "reclaim_ts"}.issubset(outcomes.columns):
        raise ValueError("结果资料必须包含 candidate_id 和 reclaim_ts")

    frame = candidates.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["breach_ts"] = pd.to_datetime(frame["breach_ts"])
    reclaim = outcomes[["candidate_id", "reclaim_ts"]].copy()
    reclaim["reclaim_ts"] = pd.to_datetime(reclaim["reclaim_ts"])
    frame = frame.merge(reclaim, on="candidate_id", how="left", validate="one_to_one")

    dates = [pd.Timestamp(value).normalize() for value in trading_dates]
    date_position = {value: index for index, value in enumerate(dates)}
    frame["date_position"] = frame["date"].map(date_position)
    frame["score_cut"] = np.nan
    frame["signal"] = False
    frame["skip_reason"] = ""
    cutoff_time = pd.Timestamp(settings.entry_cutoff).time()

    for index, row in frame.sort_values(["date", "breach_ts"]).iterrows():
        position_value = row["date_position"]
        if pd.isna(position_value):
            frame.at[index, "skip_reason"] = "日期不在研究范围"
            continue
        position = int(position_value)
        if position < settings.score_lookback_days:
            frame.at[index, "skip_reason"] = "历史日期不足"
            continue
        history_dates = set(dates[position - settings.score_lookback_days : position])
        history = frame.loc[
            frame["code"].eq(row["code"])
            & frame["date"].isin(history_dates),
            "stop_density_score",
        ].dropna()
        if len(history) < settings.min_score_events:
            frame.at[index, "skip_reason"] = "历史事件不足"
            continue
        score_cut = float(history.quantile(settings.score_quantile))
        frame.at[index, "score_cut"] = score_cut
        if (
            settings.require_high_density
            and float(row["stop_density_score"]) < score_cut - 1e-9
        ):
            frame.at[index, "skip_reason"] = "密集度未达高分界"
            continue
        if (
            float(row["near_support_net_buy_ratio"])
            < settings.min_near_support_net_buy_ratio - 1e-9
        ):
            frame.at[index, "skip_reason"] = "支撑附近主动净买入不足"
            continue
        reclaim_ts = row["reclaim_ts"]
        if pd.isna(reclaim_ts):
            frame.at[index, "skip_reason"] = "60秒内没有重新站回"
            continue
        reclaim_ts = pd.Timestamp(reclaim_ts)
        breach_ts = pd.Timestamp(row["breach_ts"])
        if not breach_ts < reclaim_ts <= breach_ts + pd.Timedelta(
            seconds=settings.reclaim_seconds
        ):
            frame.at[index, "skip_reason"] = "重新站回时间无效"
            continue
        if reclaim_ts.time() > cutoff_time:
            frame.at[index, "skip_reason"] = "重新站回时间过晚"
            continue
        frame.at[index, "signal"] = True

    return frame.sort_values(["breach_ts", "candidate_id"]).reset_index(drop=True)


def _quotes_for_code(quotes: pd.DataFrame, code: str) -> pd.DataFrame:
    return quotes.loc[quotes["code"].eq(code)].sort_values(
        ["event_ts", "source_row_no"]
    )


def find_entry_quote(
    code_quotes: pd.DataFrame,
    reclaim_ts: pd.Timestamp,
    settings: BacktestSettings,
) -> pd.Series | None:
    """信号确认后，取下一条且没有明显延迟的行情。"""

    cutoff = pd.Timestamp.combine(reclaim_ts.date(), pd.Timestamp(settings.entry_cutoff).time())
    choices = code_quotes.loc[
        code_quotes["event_ts"].gt(reclaim_ts)
        & code_quotes["event_ts"].le(
            reclaim_ts + pd.Timedelta(seconds=settings.entry_quote_delay_seconds)
        )
        & code_quotes["event_ts"].le(cutoff)
    ]
    return None if choices.empty else choices.iloc[0]


def find_reclaim_entry_quote(
    code_quotes: pd.DataFrame,
    *,
    breach_ts: pd.Timestamp,
    support_price: float,
    settings: BacktestSettings,
) -> tuple[pd.Timestamp, pd.Series] | None:
    """寻找两条买一站上支撑的行情，再取下一条行情尝试买入。"""

    cutoff = pd.Timestamp.combine(breach_ts.date(), pd.Timestamp(settings.entry_cutoff).time())
    future = code_quotes.loc[
        code_quotes["event_ts"].gt(breach_ts)
        & code_quotes["event_ts"].le(
            breach_ts + pd.Timedelta(seconds=settings.reclaim_seconds)
        )
        & code_quotes["event_ts"].le(cutoff)
    ].sort_values(["event_ts", "source_row_no"])
    if len(future) < 3:
        return None
    times = pd.to_datetime(future["event_ts"]).tolist()
    bids = [first_book_price(row, "bid") for _, row in future.iterrows()]
    for index in range(1, len(future)):
        if not (
            bids[index - 1] >= support_price - 1e-9
            and bids[index] >= support_price - 1e-9
        ):
            continue
        if times[index] - times[index - 1] > pd.Timedelta(seconds=6):
            continue
        confirmation_ts = pd.Timestamp(times[index])
        entry = find_entry_quote(code_quotes, confirmation_ts, settings)
        if entry is not None:
            return confirmation_ts, entry
    return None


def find_exit_for_day(
    position: dict[str, object],
    code_quotes: pd.DataFrame,
    *,
    date_position: int,
    settings: BacktestSettings,
) -> dict[str, object] | None:
    """从买入后的交易日寻找目标、止损或到期卖出行情。"""

    entry_position = int(position["entry_date_position"])
    if date_position <= entry_position or code_quotes.empty:
        return None
    shares = int(position["shares"])
    due_position = entry_position + settings.max_hold_days
    rows = code_quotes.sort_values(["event_ts", "source_row_no"]).reset_index(drop=True)
    if rows.empty:
        return None

    def fill_at(index: int, reason: str) -> dict[str, object] | None:
        row = rows.iloc[index]
        price = executable_book_vwap(
            row,
            side="bid",
            shares=shares,
            participation=settings.book_participation,
        )
        if price is None:
            return None
        return {
            "exit_ts": pd.Timestamp(row["event_ts"]),
            "exit_price": penalized_price(price, side="sell", settings=settings),
            "exit_reason": reason,
        }

    def fill_after(index: int, reason: str) -> dict[str, object] | None:
        for later in range(index + 1, len(rows)):
            fill = fill_at(later, reason)
            if fill is not None:
                return fill
        return None

    pending_reason = position.get("pending_exit_reason")
    if pending_reason:
        for index in range(len(rows)):
            fill = fill_at(index, str(pending_reason))
            if fill is not None:
                return fill
        return None

    if date_position > due_position:
        for index in range(len(rows)):
            fill = fill_at(index, "到期后首个可卖时刻")
            if fill is not None:
                return fill
        return None

    cutoff_time = pd.Timestamp(settings.exit_cutoff).time()
    decision_indexes = [
        index
        for index, value in enumerate(pd.to_datetime(rows["event_ts"]).dt.time)
        if value <= cutoff_time
    ]
    for index in decision_indexes:
        row = rows.iloc[index]
        best_bid = first_book_price(row, "bid")
        last_price = _number(row, "last_price")
        reason = ""
        if best_bid >= float(position["target_price"]) - 1e-9:
            reason = "区间高位"
        elif (
            best_bid > 0 and best_bid <= float(position["stop_price"]) + 1e-9
        ) or (
            not best_bid > 0
            and last_price > 0
            and last_price <= float(position["stop_price"]) + 1e-9
        ):
            reason = "止损"
        if reason:
            fill = fill_after(index, reason)
            if fill is not None:
                return fill
            position["pending_exit_reason"] = reason
            position["pending_exit_ts"] = pd.Timestamp(row["event_ts"])
            return None

    if date_position >= due_position:
        time_indexes = [
            index
            for index, value in enumerate(pd.to_datetime(rows["event_ts"]).dt.time)
            if value >= cutoff_time
        ]
        if time_indexes:
            trigger_index = time_indexes[0]
            fill = fill_after(trigger_index, "持有到期")
            if fill is not None:
                return fill
            position["pending_exit_reason"] = "持有到期"
            position["pending_exit_ts"] = pd.Timestamp(
                rows.iloc[trigger_index]["event_ts"]
            )
    return None


def _last_mark(code_quotes: pd.DataFrame) -> float | None:
    if code_quotes.empty:
        return None
    rows = code_quotes.loc[code_quotes["last_price"].gt(0)]
    if rows.empty:
        return None
    return float(rows.iloc[-1]["last_price"])


def terminal_liquidation(
    position: Mapping[str, object],
    code_quotes: pd.DataFrame,
    settings: BacktestSettings,
) -> dict[str, object]:
    """按最后仍能成交的买盘估算未卖仓位；没有买盘时按零计。"""

    shares = int(position["shares"])
    rows = code_quotes.sort_values(["event_ts", "source_row_no"]).copy()
    pending_ts = position.get("pending_exit_ts")
    if pending_ts is not None:
        rows = rows.loc[rows["event_ts"].gt(pd.Timestamp(pending_ts))]
    elif not rows.empty:
        rows = rows.tail(1)
    for _, row in rows.iloc[::-1].iterrows():
        price = executable_book_vwap(
            row,
            side="bid",
            shares=shares,
            participation=settings.book_participation,
        )
        if price is None:
            continue
        price = penalized_price(price, side="sell", settings=settings)
        amount = price * shares
        fee = commission(amount, settings) + amount * settings.stamp_duty_rate
        return {
            "liquidation_ts": pd.Timestamp(row["event_ts"]),
            "liquidation_price": price,
            "liquidation_fee": fee,
            "liquidation_net_value": amount - fee,
        }
    return {
        "liquidation_ts": pd.NaT,
        "liquidation_price": math.nan,
        "liquidation_fee": 0.0,
        "liquidation_net_value": 0.0,
    }


def _performance_summary(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    settings: BacktestSettings,
    signal_count: int,
    skipped: Mapping[str, int],
    open_positions: pd.DataFrame,
) -> dict[str, object]:
    if daily.empty:
        return {
            "initial_cash": settings.initial_cash,
            "final_equity": settings.initial_cash,
            "total_return": 0.0,
            "trade_count": 0,
        }
    equity = daily["equity"].astype(float)
    daily_return = equity.pct_change().fillna(0.0)
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    std = float(daily_return.std(ddof=1)) if len(daily_return) > 1 else 0.0
    sharpe = (
        float(daily_return.mean() / std * math.sqrt(252.0)) if std > 0 else None
    )
    total_return = float(equity.iloc[-1] / settings.initial_cash - 1.0)
    if trades.empty:
        win_rate = None
        average_trade_return = None
        median_trade_return = None
        total_fees = 0.0
        reason_counts: dict[str, int] = {}
    else:
        win_rate = float(trades["net_pnl"].gt(0).mean())
        average_trade_return = float(trades["net_return"].mean())
        median_trade_return = float(trades["net_return"].median())
        total_fees = float(trades["buy_fee"].sum() + trades["sell_fee"].sum())
        reason_counts = {
            str(key): int(value)
            for key, value in trades["exit_reason"].value_counts().items()
        }
    if not trades.empty and "price_reference_warning" in trades.columns:
        trusted = trades.loc[~trades["price_reference_warning"].astype(bool)]
    else:
        trusted = trades
    closed_pnl = trades["net_pnl"].tolist() if not trades.empty else []
    open_pnl = (
        open_positions["estimated_net_pnl"].tolist()
        if not open_positions.empty
        else []
    )
    all_pnl = closed_pnl + open_pnl
    return {
        "initial_cash": settings.initial_cash,
        "final_equity": float(equity.iloc[-1]),
        "total_return": total_return,
        "max_drawdown": float(drawdown.min()),
        "daily_sharpe": sharpe,
        "signal_count": int(signal_count),
        "trade_count": int(len(trades)),
        "open_position_count": int(len(open_positions)),
        "evaluated_position_count": int(len(all_pnl)),
        "win_rate": win_rate,
        "win_rate_including_open": (
            float(np.mean(np.asarray(all_pnl, dtype=float) > 0)) if all_pnl else None
        ),
        "open_position_estimated_pnl": float(sum(open_pnl)),
        "average_trade_return": average_trade_return,
        "median_trade_return": median_trade_return,
        "total_fees": total_fees,
        "trusted_trade_count": int(len(trusted)),
        "trusted_trade_net_pnl": float(trusted["net_pnl"].sum()) if not trusted.empty else 0.0,
        "trusted_trade_win_rate": (
            float(trusted["net_pnl"].gt(0).mean()) if not trusted.empty else None
        ),
        "exit_reason_counts": reason_counts,
        "skipped_after_signal": {str(key): int(value) for key, value in skipped.items()},
        "average_invested_ratio": float(daily["invested_value"].mean() / settings.initial_cash),
    }


def run_backtest(
    candidates: pd.DataFrame,
    outcomes: pd.DataFrame,
    trading_dates: Sequence[str],
    quote_reader: Callable[[str], pd.DataFrame],
    settings: BacktestSettings,
) -> tuple[
    dict[str, object],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    """逐日处理卖出与买入，并返回汇总、交易、每日资产和信号检查表。"""

    if len(trading_dates) <= settings.score_lookback_days + settings.max_hold_days:
        raise ValueError("交易日不足以同时留出评分历史和卖出观察期")
    signals = prepare_signals(candidates, outcomes, trading_dates, settings)
    last_signal_position = len(trading_dates) - settings.max_hold_days - 1
    signals["inside_signal_period"] = signals["date_position"].le(
        last_signal_position
    )
    signals["selected_for_backtest"] = (
        signals["signal"] & signals["inside_signal_period"]
    )
    selected = signals.loc[signals["selected_for_backtest"]].copy()
    selected_by_date = {
        str(pd.Timestamp(date).date()): group.copy()
        for date, group in selected.groupby("date", sort=False)
    }
    cash = float(settings.initial_cash)
    positions: dict[str, dict[str, object]] = {}
    completed: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []
    skipped: dict[str, int] = {}
    price_reference_warning_count = 0

    def count_skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for date_position, date in enumerate(trading_dates):
        date_key = str(pd.Timestamp(date).date())
        quotes = quote_reader(date_key)
        code_frames = {
            str(code): group.sort_values(["event_ts", "source_row_no"]).reset_index(drop=True)
            for code, group in quotes.groupby("code", sort=False)
        }

        day_events: list[dict[str, object]] = []
        for code, position in list(positions.items()):
            code_quotes = code_frames.get(code, pd.DataFrame())
            previous_close = position.get("last_close_price")
            reference_values = (
                code_quotes.loc[code_quotes["prev_close"].gt(0), "prev_close"]
                if not code_quotes.empty and "prev_close" in code_quotes.columns
                else pd.Series(dtype=float)
            )
            if previous_close is not None and not reference_values.empty:
                ratio = float(reference_values.iloc[0]) / float(previous_close)
                if abs(ratio - 1.0) > 0.005:
                    position["price_reference_warning"] = True
                    price_reference_warning_count += 1
            exit_event = find_exit_for_day(
                position,
                code_quotes,
                date_position=date_position,
                settings=settings,
            )
            if exit_event is not None:
                day_events.append({"kind": "exit", "code": code, **exit_event})

        day_signals = selected_by_date.get(date_key, pd.DataFrame())
        for _, signal in day_signals.iterrows():
            code = str(signal["code"])
            reclaim_entry = find_reclaim_entry_quote(
                code_frames.get(code, pd.DataFrame()),
                breach_ts=pd.Timestamp(signal["breach_ts"]),
                support_price=float(signal["support_price"]),
                settings=settings,
            )
            if reclaim_entry is None:
                count_skip("买一没有连续站回或确认后无及时行情")
                continue
            actual_reclaim_ts, entry_quote = reclaim_entry
            day_events.append(
                {
                    "kind": "entry",
                    "code": code,
                    "event_ts": pd.Timestamp(entry_quote["event_ts"]),
                    "entry_quote": entry_quote,
                    "actual_reclaim_ts": actual_reclaim_ts,
                    "signal": signal,
                }
            )

        day_events.sort(
            key=lambda item: (
                pd.Timestamp(item.get("event_ts", item.get("exit_ts"))),
                0 if item["kind"] == "exit" else 1,
                -float(item.get("signal", {}).get("stop_density_score", 0.0)),
            )
        )
        for event in day_events:
            code = str(event["code"])
            if event["kind"] == "exit":
                position = positions.get(code)
                if position is None:
                    continue
                exit_price = float(event["exit_price"])
                shares = int(position["shares"])
                exit_amount = exit_price * shares
                sell_fee = commission(exit_amount, settings) + (
                    exit_amount * settings.stamp_duty_rate
                )
                cash += exit_amount - sell_fee
                entry_total = float(position["entry_amount"]) + float(position["buy_fee"])
                net_pnl = exit_amount - sell_fee - entry_total
                completed.append(
                    {
                        **position,
                        "exit_ts": pd.Timestamp(event["exit_ts"]),
                        "exit_price": exit_price,
                        "exit_amount": exit_amount,
                        "sell_fee": sell_fee,
                        "exit_reason": str(event["exit_reason"]),
                        "holding_days": date_position
                        - int(position["entry_date_position"]),
                        "net_pnl": net_pnl,
                        "net_return": net_pnl / entry_total,
                    }
                )
                del positions[code]
                continue

            if code in positions:
                count_skip("同一股票已有持仓")
                continue
            if len(positions) >= settings.max_positions:
                count_skip("同时持仓已满")
                continue
            signal = event["signal"]
            budget = min(settings.position_cash, cash)
            fill = affordable_entry(
                event["entry_quote"], cash_budget=budget, settings=settings
            )
            if fill is None:
                count_skip("资金或卖盘不足")
                continue
            shares, entry_price, buy_fee = fill
            support = float(signal["support_price"])
            range_width = float(signal["range_width"])
            target = float(signal["range_high"])
            maximum_entry = support + max(
                settings.max_entry_range_share * range_width,
                settings.minimum_entry_ratio * support,
            )
            if entry_price > maximum_entry + 1e-9:
                count_skip("站回后价格已经离支撑过远")
                continue
            if target <= entry_price + 1e-9:
                count_skip("买入价已经达到区间高位")
                continue
            entry_amount = entry_price * shares
            estimated_exit_price = penalized_price(
                target, side="sell", settings=settings
            )
            estimated_exit_amount = estimated_exit_price * shares
            estimated_sell_fee = commission(estimated_exit_amount, settings) + (
                estimated_exit_amount * settings.stamp_duty_rate
            )
            estimated_net_return = (
                (estimated_exit_amount - estimated_sell_fee)
                / (entry_amount + buy_fee)
                - 1.0
            )
            if estimated_net_return < settings.min_target_net_margin - 1e-9:
                count_skip("目标空间不足以覆盖费用")
                continue
            required_cash = entry_amount + buy_fee
            if required_cash > cash + 1e-9:
                count_skip("可用资金不足")
                continue
            cash -= required_cash
            positions[code] = {
                "candidate_id": str(signal["candidate_id"]),
                "code": code,
                "breach_ts": pd.Timestamp(signal["breach_ts"]),
                "reclaim_ts": pd.Timestamp(event["actual_reclaim_ts"]),
                "entry_ts": pd.Timestamp(event["event_ts"]),
                "entry_date_position": date_position,
                "entry_price": entry_price,
                "shares": shares,
                "entry_amount": entry_amount,
                "buy_fee": buy_fee,
                "support_price": support,
                "target_price": target,
                "stop_price": support
                - max(
                    settings.stop_range_share * range_width,
                    settings.minimum_stop_ratio * support,
                ),
                "range_width": range_width,
                "stop_density_score": float(signal["stop_density_score"]),
                "score_cut": float(signal["score_cut"]),
                "last_mark_price": entry_price,
                "price_reference_warning": False,
            }

        invested_value = 0.0
        for code, position in positions.items():
            mark = _last_mark(code_frames.get(code, pd.DataFrame()))
            if mark is not None:
                position["last_mark_price"] = mark
                position["last_close_price"] = mark
            invested_value += int(position["shares"]) * float(position["last_mark_price"])
        daily_rows.append(
            {
                "date": pd.Timestamp(date_key),
                "cash": cash,
                "invested_value": invested_value,
                "equity": cash + invested_value,
                "position_count": len(positions),
                "completed_trade_count": len(completed),
            }
        )
        print(
            f"策略回测 {date_position + 1}/{len(trading_dates)} {date_key}："
            f"持仓 {len(positions)}，已完成 {len(completed)} 笔",
            flush=True,
        )

    open_rows: list[dict[str, object]] = []
    final_code_frames = code_frames if trading_dates else {}
    for code, position in positions.items():
        liquidation = terminal_liquidation(
            position,
            final_code_frames.get(code, pd.DataFrame()),
            settings,
        )
        entry_total = float(position["entry_amount"]) + float(position["buy_fee"])
        net_value = float(liquidation["liquidation_net_value"])
        open_rows.append(
            {
                **position,
                **liquidation,
                "estimated_net_pnl": net_value - entry_total,
                "estimated_net_return": net_value / entry_total - 1.0,
            }
        )
    open_frame = pd.DataFrame(open_rows)
    if daily_rows and open_rows:
        liquidation_value = float(open_frame["liquidation_net_value"].sum())
        daily_rows[-1]["invested_value"] = liquidation_value
        daily_rows[-1]["equity"] = cash + liquidation_value

    trades = pd.DataFrame(completed)
    daily = pd.DataFrame(daily_rows)
    summary = _performance_summary(
        daily,
        trades,
        settings=settings,
        signal_count=len(selected),
        skipped=skipped,
        open_positions=open_frame,
    )
    summary["settings"] = asdict(settings)
    summary["trading_dates"] = list(trading_dates)
    summary["signal_period"] = {
        "start": str(pd.Timestamp(trading_dates[settings.score_lookback_days]).date()),
        "end": str(pd.Timestamp(trading_dates[last_signal_position]).date()),
    }
    summary["exit_buffer_dates"] = list(trading_dates[last_signal_position + 1 :])
    summary["all_rule_signal_count"] = int(signals["signal"].sum())
    summary["price_reference_warning_count"] = int(price_reference_warning_count)
    summary["important_limit"] = (
        "密集度是潜在止损区的估计，不是真实止损单；三秒行情也不能证明排队订单一定成交。"
    )
    return summary, trades, daily, signals, open_frame


def run_from_files(
    *,
    research_dir: Path,
    output_dir: Path,
    level2_root: Path,
    threads: int,
    memory_limit: str,
    overwrite_quote_cache: bool,
    settings: BacktestSettings | None = None,
    variant_name: str = "custom",
) -> dict[str, object]:
    settings = settings or BacktestSettings()
    candidate_path = research_dir / "stop_cluster_breach_candidates.parquet"
    outcome_path = research_dir / "stop_cluster_breach_outcomes.parquet"
    research_summary_path = research_dir / "summary.json"
    candidates = pd.read_parquet(candidate_path)
    outcomes = pd.read_parquet(outcome_path)
    research_summary = json.loads(research_summary_path.read_text(encoding="utf-8"))
    trading_dates = [str(value) for value in research_summary["research_scope"]["dates"]]
    codes = sorted(candidates["code"].astype(str).unique())
    output_dir.mkdir(parents=True, exist_ok=True)

    reader = QuoteDayReader(
        root=level2_root,
        codes=codes,
        cache_dir=output_dir / "quote_days",
        threads=threads,
        memory_limit=memory_limit,
        overwrite=overwrite_quote_cache,
    )
    try:
        summary, trades, daily, signals, open_positions = run_backtest(
            candidates,
            outcomes,
            trading_dates,
            reader,
            settings,
        )
    finally:
        reader.close()

    trade_path = output_dir / "trades.parquet"
    daily_path = output_dir / "daily_equity.parquet"
    signal_path = output_dir / "signal_audit.parquet"
    open_position_path = output_dir / "open_positions.parquet"
    trades.to_parquet(trade_path, index=False)
    daily.to_parquet(daily_path, index=False)
    signals.to_parquet(signal_path, index=False)
    open_positions.to_parquet(open_position_path, index=False)
    summary.update(
        {
            "variant_name": variant_name,
            "trade_path": str(trade_path.resolve()),
            "daily_path": str(daily_path.resolve()),
            "signal_path": str(signal_path.resolve()),
            "open_position_path": str(open_position_path.resolve()),
            "candidate_path": str(candidate_path.resolve()),
            "outcome_path": str(outcome_path.resolve()),
        }
    )
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_path"] = str(summary_path.resolve())
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--level2-root", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--overwrite-quote-cache", action="store_true")
    parser.add_argument(
        "--variant",
        choices=("simple", "strict", "all_reclaims"),
        default="simple",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_from_files(
        research_dir=args.research_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        level2_root=(args.level2_root or discover_level2_root()).resolve(),
        threads=args.threads,
        memory_limit=args.memory_limit,
        overwrite_quote_cache=args.overwrite_quote_cache,
        settings=settings_for_variant(args.variant),
        variant_name=args.variant,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
