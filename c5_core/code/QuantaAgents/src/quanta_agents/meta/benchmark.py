"""Deterministic, synthetic smoke benchmark; not evidence of financial alpha.

Only a small declarative strategy is interpreted. No model-authored Python is
executed. The public final split is reserved for the runner's frozen submission
step; this module is not itself a security boundary or an inaccessible dataset.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Any


CASE_ID = "synthetic_trend_reversal_v1"
CASE_KIND = "synthetic_fixture"
SEED = 20260905
DEVELOPMENT_DAYS = 600
TOTAL_DAYS = 840
FEE_RATE = 0.001
INITIAL_CASH = 10_000.0
TRADING_DAYS_PER_YEAR = 252
MIN_COMPLETED_TRADES = 5

_FIELDS: dict[str, dict[str, Any]] = {
    "lookback": {"type": "integer", "minimum": 3, "maximum": 60},
    "decline_threshold": {"type": "number", "minimum": 0.005, "maximum": 0.25},
    "confirmation_days": {"type": "integer", "minimum": 0, "maximum": 4},
    "hold_days": {"type": "integer", "minimum": 1, "maximum": 30},
    "stop_loss": {"type": "number", "minimum": 0.01, "maximum": 0.25},
    "volume_filter": {"type": "number", "minimum": 0.0, "maximum": 3.0},
}

_SCORING_SPEC = {
    "version": "synthetic_close_signal_next_open_v1",
    "score": "sqrt(252) * mean(all daily portfolio returns) / sample_std(ddof=1)",
    "zero_variance_sharpe": 0.0,
    "annualized_return": "(final_equity / initial_cash) ** (252 / days) - 1",
    "risk_free_rate": 0.0,
    "fee_each_side": FEE_RATE,
    "position": "one asset, all available cash, fractional units, no leverage",
    "idle_cash_return": 0.0,
    "entry": "signal at close t; purchase at open t+1 including fee",
    "signal": "decline over lookback ending confirmation_days before t, followed by that many strictly rising closes; optional volume ratio uses preceding lookback sessions",
    "exit": "close-based stop or holding limit at t; sale at open t+1 including fee",
    "terminal_exit": "predeclared split-end close liquidation, including fee",
    "split_start": "cash only; earlier bars may supply signal warmup",
    "eligibility": f"at least {MIN_COMPLETED_TRADES} completed trades; not significance",
}


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@lru_cache(maxsize=1)
def _bars() -> tuple[_Bar, ...]:
    """Generate noisy OHLCV with changing dynamics, without a planted DSL answer."""
    rng = random.Random(SEED)
    rows: list[_Bar] = []
    day = date(2000, 1, 3)
    previous_close = 100.0
    previous_return = 0.0
    # The generator is intentionally public and reconstructible. This is a
    # pipeline fixture, not a claim that hidden data is unavailable to an agent.
    regimes = ((0.0003, 0.012, 0.16), (-0.0002, 0.018, -0.12), (0.0001, 0.010, 0.0))
    for index in range(TOTAL_DAYS):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        drift, volatility, autocorrelation = regimes[(index // 100) % len(regimes)]
        innovation = rng.gauss(0.0, volatility)
        log_return = max(-0.16, min(0.16, drift + autocorrelation * previous_return + innovation))
        gap = rng.gauss(0.0, volatility * 0.30)
        opening = previous_close * math.exp(gap)
        close = previous_close * math.exp(log_return)
        spread = abs(rng.gauss(0.0, volatility * 0.45))
        high = max(opening, close) * (1.0 + spread)
        low = min(opening, close) / (1.0 + spread)
        volume = max(1, int(1_000_000 * math.exp(rng.gauss(0.0, 0.35)) * (1.0 + abs(log_return) * 12)))
        rows.append(_Bar(str(day), round(opening, 8), round(high, 8), round(low, 8), round(close, 8), volume))
        previous_close = close
        previous_return = log_return
        day += timedelta(days=1)
    return tuple(rows)


def strategy_schema() -> dict[str, Any]:
    """JSON-only strategy language; every parameter is mandatory and bounded."""
    descriptions = {
        "lookback": "Number of sessions measuring the decline before confirmation.",
        "decline_threshold": "Minimum fractional close-to-close decline (positive number).",
        "confirmation_days": "Consecutive rising closes after the decline; zero disables confirmation.",
        "hold_days": "Maximum closes held before a next-open exit instruction.",
        "stop_loss": "Fractional loss from entry open observed at close, exited next open; gaps apply.",
        "volume_filter": "0 disables; otherwise signal volume / preceding lookback mean must reach this ratio.",
    }
    return {
        "type": "object",
        "properties": {key: {**value, "description": descriptions[key]} for key, value in _FIELDS.items()},
        "required": list(_FIELDS),
        "additionalProperties": False,
    }


def baseline_strategy() -> dict[str, int | float]:
    return {
        "lookback": 20,
        "decline_threshold": 0.035,
        "confirmation_days": 1,
        "hold_days": 7,
        "stop_loss": 0.05,
        "volume_filter": 0.0,
    }


def _validate(strategy: dict[str, Any]) -> dict[str, int | float]:
    if not isinstance(strategy, dict):
        raise ValueError("strategy must be a JSON object")
    missing = set(_FIELDS) - strategy.keys()
    extra = strategy.keys() - set(_FIELDS)
    if missing or extra:
        raise ValueError(f"strategy keys do not match allowlist; missing={sorted(missing)}, extra={sorted(extra)}")
    clean: dict[str, int | float] = {}
    for key, spec in _FIELDS.items():
        value = strategy[key]
        if spec["type"] == "integer":
            valid_type = type(value) is int
        else:
            valid_type = type(value) in (int, float)
        if not valid_type or (type(value) is float and not math.isfinite(value)):
            raise ValueError(f"{key} must be a finite {spec['type']} (booleans are not numbers)")
        if not spec["minimum"] <= value <= spec["maximum"]:
            raise ValueError(f"{key} must be in [{spec['minimum']}, {spec['maximum']}]")
        clean[key] = int(value) if spec["type"] == "integer" else float(value)
    return clean


def _split_bounds(split: str) -> tuple[int, int]:
    if split == "development":
        return 0, DEVELOPMENT_DAYS
    if split == "final":
        return DEVELOPMENT_DAYS, TOTAL_DAYS
    raise ValueError("split must be 'development' or 'final'")


def case_manifest() -> dict[str, Any]:
    rows = _bars()
    return {
        "case_id": CASE_ID,
        "kind": CASE_KIND,
        "purpose": "Synthetic plumbing and research-loop smoke test only; not alpha or architecture superiority evidence.",
        "asset_count": 1,
        "data_columns": ["date", "open", "high", "low", "close", "volume"],
        "splits": {
            name: {"start": rows[start].date, "end": rows[end - 1].date, "rows": end - start}
            for name, (start, end) in {
                "development": (0, DEVELOPMENT_DAYS), "final": (DEVELOPMENT_DAYS, TOTAL_DAYS)
            }.items()
        },
        "scoring_spec": dict(_SCORING_SPEC),
        "scoring_hash": _hash(_SCORING_SPEC),
        "blindness_limit": "Final is withheld at the model interface only. Public synthetic code and seed make it reconstructible.",
    }


def _data_overview(rows: tuple[_Bar, ...]) -> dict[str, Any]:
    daily = [rows[i].close / rows[i - 1].close - 1.0 for i in range(1, len(rows))]
    return {
        "rows": len(rows),
        "start": rows[0].date,
        "end": rows[-1].date,
        "close_min": min(row.close for row in rows),
        "close_max": max(row.close for row in rows),
        "close_to_close_return": rows[-1].close / rows[0].close - 1.0,
        "annualized_close_volatility": statistics.stdev(daily) * math.sqrt(252) if len(daily) > 1 else 0.0,
        "mean_volume": statistics.mean(row.volume for row in rows),
    }


def development_packet() -> dict[str, Any]:
    """Model-facing packet: no final rows, summaries, scores or final baseline."""
    rows = _bars()[:DEVELOPMENT_DAYS]
    return {
        "case_id": CASE_ID,
        "kind": CASE_KIND,
        "task": "Research a long-only trend-reversal strategy: after a preceding decline, consider evidence of reversal, holding duration and close-based risk control. Propose a bounded DSL strategy and test falsifiable hypotheses using development feedback.",
        "limitations": "One public synthetic asset. This checks system behavior, not real-market profitability or general research ability.",
        "data_overview": _data_overview(rows),
        "data_hash": _hash([asdict(row) for row in rows]),
        "recent_ohlcv": [asdict(row) for row in rows[-32:]],
        "chronological_overview": [_data_overview(rows[start:start + 200]) for start in range(0, len(rows), 200)],
        "baseline_strategy": baseline_strategy(),
        "strategy_schema": strategy_schema(),
        "execution_rules": dict(_SCORING_SPEC),
        "scoring_hash": _hash(_SCORING_SPEC),
    }


def _signal(rows: tuple[_Bar, ...], t: int, strategy: dict[str, int | float]) -> bool:
    lookback = int(strategy["lookback"])
    confirmation = int(strategy["confirmation_days"])
    anchor = t - confirmation
    if anchor - lookback < 0:
        return False
    decline = rows[anchor].close / rows[anchor - lookback].close - 1.0
    if decline > -strategy["decline_threshold"]:
        return False
    if any(rows[i].close <= rows[i - 1].close for i in range(anchor + 1, t + 1)):
        return False
    volume_ratio = float(strategy["volume_filter"])
    if volume_ratio > 0:
        prior_volume = statistics.mean(row.volume for row in rows[t - lookback:t])
        if rows[t].volume < volume_ratio * prior_volume:
            return False
    return True


def _metrics(returns: list[float]) -> dict[str, float | int]:
    wealth = 1.0
    peak = 1.0
    drawdown = 0.0
    for change in returns:
        wealth *= 1.0 + change
        peak = max(peak, wealth)
        drawdown = min(drawdown, wealth / peak - 1.0)
    mean = statistics.mean(returns) if returns else 0.0
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    return {
        "days": len(returns),
        "total_return": wealth - 1.0,
        "annualized_return": wealth ** (252 / len(returns)) - 1.0 if returns else 0.0,
        "sharpe": math.sqrt(252) * mean / std if std > 1e-12 else 0.0,
        "max_drawdown": drawdown,
        "annualized_volatility": std * math.sqrt(252),
    }


def _simulate(
    rows: tuple[_Bar, ...], strategy: dict[str, int | float], start: int, end: int,
    *, fee_rate: float = FEE_RATE,
) -> dict[str, Any]:
    """Cash accounting and close-only decisions, with queued next-open orders."""
    cash = INITIAL_CASH
    shares = 0.0
    entry: dict[str, Any] | None = None
    pending_buy = start > 0 and _signal(rows, start - 1, strategy)
    pending_sell: str | None = None
    previous_equity = INITIAL_CASH
    fees = 0.0
    turnover = 0.0
    exposed_days = 0
    signals = 0
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []

    def sell(price: float, index: int, reason: str) -> None:
        nonlocal cash, shares, entry, fees, turnover
        assert entry is not None
        notional = shares * price
        exit_fee = notional * fee_rate
        cash = notional - exit_fee
        fees += exit_fee
        turnover += notional
        trades.append({
            **entry, "exit_date": rows[index].date, "exit_price": price,
            "exit_reason": reason, "holding_sessions": index - entry["entry_index"] + (reason == "terminal_close"),
            "net_return": cash / entry["capital_at_entry"] - 1.0,
            "exit_fee": exit_fee,
        })
        shares = 0.0
        entry = None

    for index in range(start, end):
        row = rows[index]
        if pending_sell and shares:
            sell(row.open, index, pending_sell)
        elif pending_buy and not shares:
            capital = cash
            shares = capital / (row.open * (1.0 + fee_rate))
            entry_fee = shares * row.open * fee_rate
            fees += entry_fee
            turnover += shares * row.open
            cash = 0.0
            entry = {
                "signal_date": rows[index - 1].date,
                "entry_date": row.date, "entry_price": row.open,
                "entry_index": index, "capital_at_entry": capital, "entry_fee": entry_fee,
            }
        pending_buy = False
        pending_sell = None
        exposed_days += int(shares > 0)
        if index == end - 1 and shares:
            sell(row.close, index, "terminal_close")
        equity = cash + shares * row.close
        daily.append({"date": row.date, "equity": equity, "return": equity / previous_equity - 1.0})
        previous_equity = equity
        if index == end - 1:
            continue
        if shares:
            assert entry is not None
            if row.close / entry["entry_price"] - 1.0 <= -strategy["stop_loss"]:
                pending_sell = "close_stop_next_open"
            elif index - entry["entry_index"] + 1 >= strategy["hold_days"]:
                pending_sell = "holding_limit_next_open"
        elif _signal(rows, index, strategy):
            pending_buy = True
            signals += 1

    metrics = _metrics([row["return"] for row in daily])
    metrics.update({
        "final_equity": daily[-1]["equity"],
        "completed_trades": len(trades),
        "win_rate": sum(trade["net_return"] > 0 for trade in trades) / len(trades) if trades else 0.0,
        "exposure_fraction": exposed_days / (end - start),
        "fees_paid": fees,
        "turnover_over_initial_cash": turnover / INITIAL_CASH,
        "buy_hold_net_return": rows[end - 1].close / rows[start].open * (1.0 - fee_rate) / (1.0 + fee_rate) - 1.0,
    })
    periods = []
    for part in range(3):
        left, right = part * len(daily) // 3, (part + 1) * len(daily) // 3
        if left == right:
            continue
        subset = daily[left:right]
        periods.append({
            "start": subset[0]["date"], "end": subset[-1]["date"],
            **_metrics([row["return"] for row in subset]),
        })
    return {
        "metrics": metrics,
        "subperiods": periods,
        "diagnostics": {
            "accepted_entry_signals": signals + int(start > 0 and _signal(rows, start - 1, strategy)),
            "exit_counts": {reason: sum(trade["exit_reason"] == reason for trade in trades) for reason in (
                "close_stop_next_open", "holding_limit_next_open", "terminal_close"
            )},
            "positive_subperiods": sum(part["total_return"] > 0 for part in periods),
            "subperiod_interpretation": "Three consecutive slices of the same portfolio path, not independent folds.",
            "evidence_status": "synthetic_only" if len(trades) >= MIN_COMPLETED_TRADES else "insufficient_trades_synthetic_only",
            "trades": trades,
        },
        "daily": daily,
    }


def evaluate(strategy: dict[str, Any], split: str = "development") -> dict[str, Any]:
    """Evaluate one DSL proposal. Only the external referee may call final."""
    clean = _validate(strategy)
    start, end = _split_bounds(split)
    rows = _bars()
    result = _simulate(rows, clean, start, end)
    result.pop("daily")
    metrics = result["metrics"]
    return {
        "case_id": CASE_ID, "kind": CASE_KIND, "split": split,
        "strategy": clean, "strategy_hash": _hash(clean),
        "data_hash": _hash([asdict(row) for row in rows[start:end]]),
        "warmup_data_hash": _hash([asdict(row) for row in rows[max(0, start - 65):start]]),
        "scoring_hash": _hash(_SCORING_SPEC),
        "score": metrics["sharpe"],
        "eligible": metrics["completed_trades"] >= MIN_COMPLETED_TRADES,
        **result,
        "limitations": "Synthetic fixture; eligibility is only a minimum-trade check, not evidence of alpha or architecture superiority.",
    }
