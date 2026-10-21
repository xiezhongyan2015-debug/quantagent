"""Causal factor combinations and a fast continuous daily development account.

Signal scores do not determine execution eligibility. Orders use yesterday's
signals and today's open-time fields; today's volume/high/low/close cannot decide
whether an opening order fills. Accounting uses adjusted units, with explicit
costs and conservative constraints, without separate dividend/tax cash entries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import time
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioSpec:
    name: str
    factor_weights: Mapping[str, float]
    top_n: int = 20
    rebalance_sessions: int = 5
    gross_exposure: float = 1.0
    max_stock_weight: float = .1
    weighting: str = "equal"
    market_filter: str = "none"
    rebalance_schedule: str = "sessions"
    membership_buffer: int = 0
    crowding_gate_factor: str = ""
    metadata: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not self.name or not self.factor_weights:
            raise ValueError("named portfolio and nonempty factor weights required")
        if any(not isinstance(k, str) or not np.isfinite(v) for k, v in self.factor_weights.items()):
            raise ValueError("finite named factor weights required")
        if sum(abs(v) for v in self.factor_weights.values()) <= 0:
            raise ValueError("nonzero factor weight mass required")
        if type(self.top_n) is not int or not 1 <= self.top_n <= 1000:
            raise ValueError("top_n must be an integer in 1..1000")
        if type(self.rebalance_sessions) is not int or not 1 <= self.rebalance_sessions <= 120:
            raise ValueError("rebalance_sessions must be in 1..120")
        if not 0 <= self.gross_exposure <= 1 or not 0 < self.max_stock_weight <= 1:
            raise ValueError("long-only unlevered capital weights required")
        if self.weighting not in {"equal", "inverse_volatility"}:
            raise ValueError("unsupported weighting")
        if self.market_filter not in {"none", "trend60", "trend120"}:
            raise ValueError("unsupported causal market filter")
        if self.rebalance_schedule not in {"sessions", "weekly_last_session"}:
            raise ValueError("unsupported rebalance calendar")
        if type(self.membership_buffer) is not int or not (self.membership_buffer == 0 or self.membership_buffer >= self.top_n):
            raise ValueError("membership buffer must be zero or at least top_n")
        object.__setattr__(self, "factor_weights", dict(self.factor_weights))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def portfolio_id(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, ensure_ascii=False,
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AccountPolicy:
    capital: float = 1_000_000.
    risk_free_rate: float = .02
    buy_commission: float = .0003
    sell_commission: float = .0003
    sell_levy_before_2023_08_28: float = .001
    sell_levy_from_2023_08_28: float = .0005
    slippage: float = .001
    min_commission: float = 5.
    lot_size: int = 100
    max_prior_day_amount_fraction: float = .01

    def __post_init__(self):
        values = asdict(self)
        if any(not np.isfinite(v) or v < 0 for v in values.values()):
            raise ValueError("nonnegative finite policy values required")
        if self.capital <= 0 or type(self.lot_size) is not int or self.lot_size < 1:
            raise ValueError("positive capital and integer lot size required")
        if not 0 < self.max_prior_day_amount_fraction <= 1:
            raise ValueError("positive bounded prior-day participation required")
        if any(v >= 1 for k, v in values.items() if k in {
                "buy_commission", "sell_commission", "sell_levy_before_2023_08_28",
                "sell_levy_from_2023_08_28", "slippage"}):
            raise ValueError("cost rates must be less than one")


def _selection_weights(plan, spec, held, n):
    order = np.array(plan["order"], dtype=int)
    if len(set(order)) != len(order) or (order < 0).any() or (order >= n).any():
        raise ValueError("invalid frozen selection order")
    retained = [j for j in order[:spec.membership_buffer] if held[j]] if spec.membership_buffer else []
    winners = retained[:spec.top_n]
    winners += [j for j in order if j not in winners][:max(0, spec.top_n - len(winners))]
    weights = np.zeros(n)
    if winners:
        budget = plan["exposure"] * len(winners) / spec.top_n
        coeff = np.array(plan["coefficient"], dtype=float)[winners]
        selected = budget * coeff / coeff.sum() if coeff.sum() > 0 else coeff * 0
        weights[winners] = np.minimum(selected, spec.max_stock_weight) * np.array(plan["gate"], dtype=float)[winners]
    if not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() > 1 + 1e-10:
        raise ValueError("selection plan produced invalid capital weights")
    return weights


def target_weights(panel, scores: Mapping[str, pd.DataFrame], spec: PortfolioSpec, *, start, end):
    """Return sparse signal-date targets. Signed weights orient raw factor scores.

    Every active factor must be present for a stock. Missing factors never cause
    an implicit per-stock reweight. Missing top-N slots stay cash. A 120-session
    observable-history filter avoids treating new listings as seasoned stocks.
    """
    dates, columns = panel.eligible.index, panel.eligible.columns
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    positions = np.flatnonzero((dates >= start) & (dates <= end))
    if not len(positions) or positions[0] == 0:
        raise ValueError("evaluation needs a preceding signal session and nonempty range")
    close = panel.fields["close"]
    eligible = (panel.eligible & close.rolling(120, min_periods=120).count().ge(120)
                & panel.fields["amount"].rolling(20, min_periods=20).mean().gt(0))
    for name in ("is_st", "is_delisting"):
        if name in panel.fields:
            eligible &= panel.fields[name].eq(0)
    combined = pd.DataFrame(0., index=dates, columns=columns)
    mass = sum(abs(v) for v in spec.factor_weights.values())
    for name, weight in spec.factor_weights.items():
        if weight == 0:
            continue
        if name not in scores or not scores[name].index.equals(dates) or not scores[name].columns.equals(columns):
            raise ValueError("factor scores must exactly align with the fixed panel: " + name)
        value = scores[name].where(eligible).replace([np.inf, -np.inf], np.nan)
        # Multiplication before ranking makes orientation explicit and tie-safe.
        combined += value.mul(1 if weight > 0 else -1).rank(axis=1, method="average", pct=True) * abs(weight) / mass
    combined = combined.where(eligible)
    gate = pd.DataFrame(1., index=dates, columns=columns)
    if spec.crowding_gate_factor:
        if spec.crowding_gate_factor not in scores:
            raise ValueError("crowding gate factor unavailable")
        crowding = scores[spec.crowding_gate_factor].where(eligible)
        combined = combined.where(crowding.notna())
        crowded = crowding.rank(axis=1, method="average", pct=True).gt(.8) & close.pct_change(5, fill_method=None).gt(0)
        gate = gate.mask(crowded, .5)
    vol = close.pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=1)
    exposure = pd.Series(spec.gross_exposure, index=dates)
    if spec.market_filter != "none":
        window = int(spec.market_filter.removeprefix("trend"))
        market_returns = close.pct_change(fill_method=None).where(panel.eligible).mean(axis=1)
        market_index = (1 + market_returns).cumprod()
        trend = market_index.gt(market_index.rolling(window, min_periods=window).mean())
        exposure = exposure.where(trend & market_returns.notna(), 0.)
    if spec.rebalance_schedule == "weekly_last_session":
        weeks = dates.to_period("W-SUN")
        signal_positions = [i for i in range(int(positions[0]) - 1, int(positions[-1]))
                            if weeks[i] != weeks[i + 1]]
    else:
        signal_positions = range(int(positions[0]) - 1, int(positions[-1]), spec.rebalance_sessions)
    rows, row_dates, plans = [], [], {}
    for i in signal_positions:
        values = combined.iloc[i].to_numpy()
        valid = np.flatnonzero(np.isfinite(values))
        # Stable fixed column order is the precommitted tie-break.
        order = valid[np.argsort(-values[valid], kind="stable")]
        observed_vol = vol.iloc[i].to_numpy()
        coeff = (np.ones(len(columns)) if spec.weighting == "equal" else
                 np.divide(1., observed_vol, out=np.zeros_like(observed_vol),
                           where=np.isfinite(observed_vol) & (observed_vol > 0)))
        plan = {"order": order.tolist(), "exposure": float(exposure.iloc[i]),
                "coefficient": coeff.tolist(), "gate": gate.iloc[i].tolist()}
        weights = _selection_weights(plan, spec, np.zeros(len(columns), dtype=bool), len(columns))
        if spec.membership_buffer:
            plans[str(dates[i].date())] = plan
        rows.append(weights)
        row_dates.append(dates[i])
    result = pd.DataFrame(rows, index=pd.DatetimeIndex(row_dates, name="signal_date"), columns=columns)
    if plans:
        # Account-dependent turnover buffer uses actual holdings at D close,
        # not hypothetical holdings from unfilled historical target orders.
        result.attrs["selection_plans"] = plans
        result.attrs["portfolio_spec"] = asdict(spec)
    return result


class DailyAccount:
    """One prepared numeric market shared by many sequential candidate accounts."""

    def __init__(self, panel, policy=AccountPolicy()):
        self.panel, self.policy = panel, policy
        self.dates = panel.eligible.index
        self.codes = list(panel.eligible.columns)
        required = {"open", "close", "raw_open", "raw_prev_close", "adjustment_factor",
                    "amount", "is_st", "is_delisting"}
        if required - set(panel.fields):
            raise ValueError("missing account fields: " + str(sorted(required - set(panel.fields))))
        self.a = {k: panel.fields[k].to_numpy(dtype=float, copy=True) for k in required}
        shape = self.a["open"].shape
        st = self.a["is_st"]
        rate = np.where(st == 1, .05, .10)
        for j, code in enumerate(self.codes):
            if code.startswith(("sh688", "sh689", "sz301")):
                rate[:, j] = .20
            elif code.startswith(("sz300", "sz302")):
                rate[self.dates >= "2020-08-24", j] = .20
            elif not code.startswith(("sh60", "sz00")):
                raise ValueError("this account adapter has no registered trading rules for " + code)
        prev_cent = np.floor(self.a["raw_prev_close"] * 100 + .5)
        self.upper = np.floor(prev_cent * (1 + rate) + .5) / 100
        self.lower = np.floor(prev_cent * (1 - rate) + .5) / 100
        valid = np.ones(shape, dtype=bool)
        for key in ("open", "raw_open", "raw_prev_close", "adjustment_factor"):
            valid &= np.isfinite(self.a[key]) & (self.a[key] > 0)
        valid &= np.isin(st, [0., 1.]) & np.isin(self.a["is_delisting"], [0., 1.])
        if "open_observed" in panel.fields:
            valid &= panel.fields["open_observed"].to_numpy() == 1
        aligned = np.isclose(self.a["open"], self.a["raw_open"] * self.a["adjustment_factor"],
                             rtol=1e-6, atol=1e-6)
        if (valid & ~aligned).any():
            raise ValueError("adjusted open and raw open disagree with conversion factor")
        raw_cent = np.floor(self.a["raw_open"] * 100 + .5) / 100
        self.special_delisting_exit = valid & (self.a["is_delisting"] == 1) & (
            (raw_cent < self.lower - 1e-8) | (raw_cent > self.upper + 1e-8))
        self.can_sell = valid & ((raw_cent > self.lower + 1e-8) | self.special_delisting_exit)
        # No current-day eligible mask: it includes end-of-day volume/OHLC.
        self.can_buy = valid & (raw_cent < self.upper - 1e-8) & (st == 0) & (self.a["is_delisting"] == 0)
        self.valid_open = valid
        self.prior_capacity = np.full(shape, np.nan)
        self.prior_capacity[1:] = self.a["amount"][:-1] * policy.max_prior_day_amount_fraction
        self.membership = None
        membership_path = panel.provenance.get("request", {}).get("membership_path")
        if membership_path:
            from .data import membership_intervals
            expected_hash = panel.provenance["request"].get("membership_sha256")
            from pathlib import Path
            if expected_hash is None or hashlib.sha256(Path(membership_path).read_bytes()).hexdigest() != expected_hash:
                raise ValueError("membership source changed from the frozen market panel")
            self.membership = np.zeros(shape, dtype=bool)
            loc = {c: j for j, c in enumerate(self.codes)}
            for code, first, last in membership_intervals(membership_path):
                if code in loc:
                    self.membership[(self.dates >= first) & (self.dates <= last), loc[code]] = True
            self.can_buy &= self.membership

    def run(self, targets, *, start, end, cost_multiplier=1., cancelled=None):
        start_time = time.perf_counter()
        if not np.isfinite(cost_multiplier) or cost_multiplier < 0:
            raise ValueError("nonnegative finite cost multiplier required")
        if not targets.columns.equals(self.panel.eligible.columns) or not targets.index.is_unique:
            raise ValueError("target axes must exactly match market and have unique signal dates")
        values = targets.to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any() or (values.sum(axis=1) > 1 + 1e-10).any():
            raise ValueError("finite unlevered long-only target weights required")
        if not targets.index.is_monotonic_increasing or not targets.index.isin(self.dates).all():
            raise ValueError("ordered market signal dates required")
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        selected = np.flatnonzero((self.dates >= start) & (self.dates <= end))
        if not len(selected) or selected[0] == 0:
            raise ValueError("nonempty evaluation needs prior signal/mark session")
        signal_rows = {int(i) + 1: row for i, row in zip(self.dates.get_indexer(targets.index), values)}
        p, a, n = self.policy, self.a, len(self.codes)
        cash, previous_nav = p.capital, p.capital
        units = np.zeros(n)
        last_close = a["close"][selected[0] - 1].copy()
        stale_age = np.zeros(n, dtype=int)
        daily, trades = [], []
        for i in selected:
            if cancelled is not None and cancelled():
                raise InterruptedError("daily account cancelled; no completed financial result")
            date = self.dates[i]
            reference = a["open"][i]
            factor = a["adjustment_factor"][i]
            day_fees = day_slip = day_turnover = 0.
            blocked, trade_count = 0, 0
            target = signal_rows.get(int(i))
            if target is not None and targets.attrs.get("selection_plans"):
                plan = targets.attrs["selection_plans"][str(self.dates[i - 1].date())]
                spec = PortfolioSpec(**targets.attrs["portfolio_spec"])
                target = _selection_weights(plan, spec, units > 1e-10, n)
            if target is not None:
                mark = np.where(self.valid_open[i], reference, last_close)
                if ((units > 0) & ~np.isfinite(mark)).any():
                    raise ValueError("held inventory lacks any causal valuation")
                open_nav = cash + np.sum(units * np.nan_to_num(mark))
                blocked += int((~self.valid_open[i] &
                                (np.abs(open_nav * target - units * np.nan_to_num(last_close)) > 1e-6)).sum())
                desired = np.divide(open_nav * target, reference, out=units.copy(), where=self.valid_open[i])
                capacity = self.prior_capacity[i].copy()
                capacity = np.where(np.isfinite(capacity) & (capacity > 0), capacity, 0.)
                delta = desired - units
                for side, order in (("sell", np.flatnonzero(delta < -1e-9)),
                                    ("buy", np.flatnonzero(delta > 1e-9))):
                    for j in order:
                        allowed = self.can_sell[i, j] if side == "sell" else self.can_buy[i, j]
                        if not allowed or capacity[j] <= 0:
                            blocked += 1
                            continue
                        slip_rate = p.slippage * cost_multiplier
                        if slip_rate >= 1:
                            raise ValueError("stress slippage must stay below one")
                        price = (max(reference[j] * (1 - slip_rate), self.lower[i, j] * factor[j])
                                 if side == "sell" else
                                 min(reference[j] * (1 + slip_rate), self.upper[i, j] * factor[j]))
                        if side == "sell" and self.special_delisting_exit[i, j]:
                            price = reference[j] * (1 - slip_rate)
                        wanted = min(abs(delta[j]), capacity[j] / price)
                        step = p.lot_size / factor[j]
                        full_exit = side == "sell" and desired[j] <= 1e-10 and wanted >= units[j] - 1e-9
                        quantity = units[j] if full_exit else math.floor(wanted / step + 1e-10) * step
                        commission_rate = (p.sell_commission if side == "sell" else p.buy_commission) * cost_multiplier
                        minimum = p.min_commission * cost_multiplier
                        levy_rate = (p.sell_levy_before_2023_08_28 if date < pd.Timestamp("2023-08-28")
                                     else p.sell_levy_from_2023_08_28) * cost_multiplier if side == "sell" else 0.
                        if side == "buy":
                            affordable = min(max(0., cash - minimum) / price,
                                             cash / (price * (1 + commission_rate)))
                            quantity = min(quantity, math.floor(affordable / step + 1e-10) * step)
                        if quantity <= 1e-10:
                            continue
                        notional = quantity * price
                        fee = max(minimum, notional * commission_rate) + notional * levy_rate
                        if side == "sell":
                            if cash + notional < fee:
                                blocked += 1
                                continue
                            units[j] -= quantity
                            cash += notional - fee
                        else:
                            if notional + fee > cash + 1e-7:
                                raise ValueError("buy order would borrow cash")
                            units[j] += quantity
                            cash -= notional + fee
                        if -1e-7 < cash < 0:
                            cash = 0.
                        units[j] = max(0., units[j])
                        capacity[j] -= notional
                        slip = quantity * abs(price - reference[j])
                        day_fees += fee
                        day_slip += slip
                        day_turnover += notional
                        trade_count += 1
                        trades.append({"date": date, "signal_date": self.dates[i - 1], "code": self.codes[j],
                                       "side": side, "adjusted_units": quantity,
                                       "raw_share_equivalent": quantity * factor[j], "price": price,
                                       "reference_price": reference[j], "notional": notional,
                                       "fees": fee, "slippage": slip})
            valid_close = np.isfinite(a["close"][i]) & (a["close"][i] > 0)
            last_close[valid_close] = a["close"][i, valid_close]
            stale_age = np.where(valid_close, 0, stale_age + 1)
            held = units > 1e-10
            if (held & ~np.isfinite(last_close)).any():
                raise ValueError("held inventory has no valuation")
            value = units * np.nan_to_num(last_close)
            nav = cash + value.sum()
            if not np.isfinite(nav) or nav <= 0:
                raise ValueError("nonpositive/nonfinite account NAV")
            stale_value = value[held & ~valid_close].sum()
            daily.append({"date": date, "nav": nav, "return": nav / previous_nav - 1,
                          "cash": cash, "position_value": float(value.sum()),
                          "exposure": float(value.sum() / nav), "fees": day_fees,
                          "slippage": day_slip, "turnover": day_turnover / previous_nav,
                          "trade_count": trade_count, "blocked_orders": blocked,
                          "stale_value": stale_value, "stale_fraction": stale_value / nav,
                          "oldest_held_mark_sessions": int(stale_age[held].max()) if held.any() else 0})
            previous_nav = nav
        daily = pd.DataFrame(daily).set_index("date")
        summary, annual = account_metrics(daily, p, start=start, end=end, expected_calendar=self.dates)
        terminal_levy = (p.sell_levy_before_2023_08_28 if self.dates[selected[-1]] < pd.Timestamp("2023-08-28")
                         else p.sell_levy_from_2023_08_28)
        liquidation_cost = (value.sum() * (p.slippage + terminal_levy)
                            + np.maximum(p.min_commission, value[value > 0] * p.sell_commission).sum()) * cost_multiplier
        summary.update({"duration_seconds": time.perf_counter() - start_time,
                        "accounting_mode": "adjusted_units_development_approximation",
                        "raw_cash_dividend_ledger": False, "cost_multiplier": cost_multiplier,
                        "terminal_inventory_value": float(value.sum()),
                        "terminal_liquidation_cost_estimate": float(liquidation_cost),
                        "terminal_liquidation_executed": False,
                        "execution_certified": False,
                        "limitations": ["open fill and prior-day amount capacity approximation",
                                        "source historical adjustment/publication vintage not certified",
                                        "adjusted units; no separate dividend withholding cash accounting",
                                        "stale held marks reported, not fresh executable quotes"]})
        return {"summary": summary, "annual": annual, "daily": daily,
                "trades": pd.DataFrame(trades), "policy": asdict(p)}


def account_metrics(daily, policy, *, start, end, expected_calendar):
    expected_calendar = pd.DatetimeIndex(expected_calendar)
    if not expected_calendar.is_unique or not expected_calendar.is_monotonic_increasing:
        raise ValueError("unique ordered authoritative calendar required")
    if daily.empty or not daily.index.is_unique or not daily.index.is_monotonic_increasing:
        raise ValueError("nonempty ordered unique daily account required")
    expected = expected_calendar[(expected_calendar >= pd.Timestamp(start)) & (expected_calendar <= pd.Timestamp(end))]
    if not daily.index.isin(expected).all():
        raise ValueError("account observations lie outside the declared market calendar")
    rf = (1 + policy.risk_free_rate) ** (1 / 252) - 1
    def metrics(frame, initial):
        returns = frame["return"]
        std = returns.std(ddof=1)
        sharpe = float((returns.mean() - rf) / std * np.sqrt(252)) if np.isfinite(std) and std > 0 else None
        nav = frame["nav"]
        peak = nav.cummax().clip(lower=initial)
        return {"sessions": len(frame), "return": float(nav.iloc[-1] / initial - 1),
                "sharpe": sharpe, "max_drawdown": float((nav / peak - 1).min()),
                "fees": float(frame["fees"].sum()), "slippage": float(frame["slippage"].sum()),
                "turnover": float(frame["turnover"].sum()), "mean_exposure": float(frame["exposure"].mean()),
                "stale_held_sessions": int((frame["stale_value"] > 0).sum()),
                "max_stale_fraction": float(frame["stale_fraction"].max()),
                "oldest_held_mark_sessions": int(frame["oldest_held_mark_sessions"].max()),
                "trade_count": int(frame["trade_count"].sum()), "blocked_orders": int(frame["blocked_orders"].sum())}
    rows, previous = [], policy.capital
    for year in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
        frame = daily.loc[daily.index.year == year]
        expected_year = expected[expected.year == year]
        full_requested = pd.Timestamp(start) <= pd.Timestamp(year, 1, 1) and pd.Timestamp(end) >= pd.Timestamp(year, 12, 31)
        calendar_spans_year = len(expected_year) > 0 and expected_year[0].month == 1 and expected_year[-1].month == 12
        complete = full_requested and calendar_spans_year and frame.index.equals(expected_year)
        values = metrics(frame, previous) if len(frame) else {"sessions": 0, "return": None, "sharpe": None}
        rows.append({"year": int(year), "full_calendar_year": full_requested,
                     "calendar_complete": complete, "expected_sessions": len(expected_year), **values})
        if len(frame):
            previous = float(frame["nav"].iloc[-1])
    annual = pd.DataFrame(rows)
    complete_rows = [r for r in rows if r["full_calendar_year"]]
    available = bool(complete_rows) and all(r["calendar_complete"] and r["sharpe"] is not None for r in complete_rows)
    mean = float(np.mean([r["sharpe"] for r in complete_rows])) if available else None
    summary = {**metrics(daily, policy.capital), "mean_full_year_sharpe": mean,
               "full_years": len(complete_rows), "all_full_year_sharpes_available": available,
               "worst_full_year_sharpe": min(r["sharpe"] for r in complete_rows) if available else None,
               "threshold_exceeded_on_supplied_simulation": mean > 1 if mean is not None else False,
               "formal_target_success": False}
    return summary, annual
