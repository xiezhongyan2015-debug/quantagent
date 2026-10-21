"""Execute explicit expression strategies through the existing daily account.

This layer prepares causal signal-date selection plans.  The V6 account still
owns orders, actual holdings, next-session execution, cash, costs and marks.
No factor direction, rank, risk estimator or unsupported exit rule is inferred.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Mapping

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.portfolio import (
    AccountPolicy, DailyAccount, PortfolioSpec, _selection_weights,
)
from .compiler import evaluate_expression, factor_ids, strategy_id, validate_strategy
from .universe import execution_pool as _execution_pool


def strategy_factor_ids(spec: dict) -> set[str]:
    """Return every required asset, including gate and allocation-risk assets."""
    normalized = validate_strategy(spec)
    return set().union(*(factor_ids(normalized[key]) for key in
                        ("score", "gate", "risk_score") if normalized.get(key) is not None))


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("strategy execution cancelled; no completed financial result")


def _axes(panel, frames):
    eligible = panel.eligible
    if (not isinstance(eligible, pd.DataFrame)
            or not isinstance(eligible.index, pd.DatetimeIndex)
            or eligible.index.tz is not None or eligible.index.hasnans
            or not eligible.index.is_unique or not eligible.index.is_monotonic_increasing
            or not eligible.columns.is_unique or eligible.empty
            or eligible.isna().any().any()
            or not all(pd.api.types.is_bool_dtype(dtype) for dtype in eligible.dtypes)):
        raise ValueError("a nonempty unique ordered naive-calendar boolean market eligibility is required")
    for group, values in (("market", panel.fields), ("factor", frames)):
        for name, value in values.items():
            if (not isinstance(value, pd.DataFrame)
                    or not value.index.equals(eligible.index)
                    or not value.columns.equals(eligible.columns)):
                raise ValueError(f"{group} frame must exactly align with the fixed panel: {name}")


def _signal_positions(dates, allocation, start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if pd.isna(start) or pd.isna(end) or start.tz is not None or end.tz is not None or start > end:
        raise ValueError("ordered finite naive evaluation dates required")
    positions = np.flatnonzero((dates >= start) & (dates <= end))
    if not len(positions) or positions[0] == 0:
        raise ValueError("evaluation needs a preceding signal session and nonempty range")
    if allocation["rebalance_schedule"] == "weekly_last_session":
        weeks = dates.to_period("W-SUN")
        return [i for i in range(int(positions[0]) - 1, int(positions[-1]))
                if weeks[i] != weeks[i + 1]]
    return list(range(int(positions[0]) - 1, int(positions[-1]), allocation["rebalance_sessions"]))


def _bridge(normalized):
    # DailyAccount consumes PortfolioSpec only for _selection_weights.  This
    # sentinel is metadata, never a factor lookup or an implicit score transform.
    return PortfolioSpec(name=normalized["name"], factor_weights={"__compiled_score__": 1.},
                         **normalized["allocation"],
                         metadata={"strategy_id": strategy_id(normalized),
                                   "role": "selection_plan_account_compatibility"})


def _targets(panel, frames, normalized, *, start, end, cancelled=None):
    dates, columns = panel.eligible.index, panel.eligible.columns
    eligible = _execution_pool(panel)
    positions = _signal_positions(dates, normalized["allocation"], start, end)
    _cancel(cancelled)
    score = evaluate_expression(normalized["score"], frames, eligible)
    _cancel(cancelled)
    gate = (evaluate_expression(normalized["gate"], frames, eligible)
            if normalized.get("gate") is not None else pd.DataFrame(1., index=dates, columns=columns))
    _cancel(cancelled)
    risk = (evaluate_expression(normalized["risk_score"], frames, eligible)
            if normalized.get("risk_score") is not None else None)
    _cancel(cancelled)
    # Reject invalid gate values in the actual signal scope.  Future values are
    # not consulted to accept or reject a completed historical prefix.
    scoped_gate = gate.iloc[positions].to_numpy(dtype=float)
    if (np.isfinite(scoped_gate) & ((scoped_gate < 0) | (scoped_gate > 1))).any():
        raise ValueError("gate values at signal dates must lie in [0, 1] or be missing")
    bridge = _bridge(normalized)
    rows, row_dates, plans = [], [], {}
    for i in positions:
        _cancel(cancelled)
        values = score.iloc[i].to_numpy(dtype=float)
        good = np.isfinite(values)
        coefficient = np.ones(len(columns))
        if risk is not None:
            observed = risk.iloc[i].to_numpy(dtype=float)
            good &= np.isfinite(observed) & (observed > 0)
            # Normalize by a positive common scale before taking reciprocals to
            # avoid overflow for tiny finite risks. Relative weights are exact
            # mathematical inverse risk; this does not fabricate missing risks.
            valid_risk = observed[good]
            coefficient = np.zeros(len(columns))
            if len(valid_risk):
                coefficient[good] = np.min(valid_risk) / valid_risk
        valid = np.flatnonzero(good)
        order = valid[np.argsort(-values[valid], kind="stable")]
        gate_row = gate.iloc[i].to_numpy(dtype=float)
        gate_row = np.where(np.isfinite(gate_row), gate_row, 0.)
        plan = {"order": order.tolist(), "exposure": float(bridge.gross_exposure),
                "coefficient": coefficient.tolist(), "gate": gate_row.tolist()}
        weights = _selection_weights(plan, bridge, np.zeros(len(columns), dtype=bool), len(columns))
        rows.append(weights)
        row_dates.append(dates[i])
        if bridge.membership_buffer:
            plans[str(dates[i].date())] = plan
    targets = pd.DataFrame(rows, index=pd.DatetimeIndex(row_dates, name="signal_date"), columns=columns,
                           dtype=float)
    if plans:
        targets.attrs["selection_plans"] = plans
        targets.attrs["portfolio_spec"] = asdict(bridge)
    signal_pool = eligible.iloc[positions].to_numpy(dtype=bool)
    scored = np.isfinite(score.iloc[positions].to_numpy(dtype=float)) & signal_pool
    gated = np.isfinite(scoped_gate) & signal_pool
    risk_ok = (np.isfinite(risk.iloc[positions].to_numpy(dtype=float))
               & (risk.iloc[positions].to_numpy(dtype=float) > 0) & signal_pool) if risk is not None else signal_pool
    count = lambda value: int(np.count_nonzero(value))
    denominator = count(signal_pool)
    coverage = {
        "signal_dates": len(positions), "market_eligible_cells": count(panel.eligible.iloc[positions]),
        "seasoned_eligible_cells": denominator, "finite_score_cells": count(scored),
        "score_fraction_of_seasoned_pool": count(scored) / denominator if denominator else None,
        "missing_gate_on_scored_cells": count(scored & ~gated),
        "zero_gate_on_scored_cells": count(scored & (scoped_gate == 0)),
        "invalid_or_missing_risk_on_scored_cells": count(scored & ~risk_ok),
        "preview_nonzero_weight_cells": count(targets.to_numpy() > 0),
        "preview_mean_gross_weight": float(targets.sum(axis=1).mean()) if len(targets) else None,
        "factor_finite_cells": {name: count(np.isfinite(frames[name].iloc[positions].to_numpy(dtype=float))
                                           & signal_pool) for name in sorted(strategy_factor_ids(normalized))},
        "gate_missing_treatment": "zero multiplier after cap; ranked slot stays cash; no redistribution",
        "risk_missing_treatment": "nonpositive or missing risk cannot enter selection; no risk imputation",
        "preview_is_executed_allocation": False,
        "buffer_uses_actual_holdings": bool(bridge.membership_buffer),
    }
    return targets, coverage


def _diagnostics(result, coverage):
    summary, annual, daily = result["summary"], result["annual"], result["daily"]
    capital = float(result["policy"]["capital"])
    net_pnl = float(daily["nav"].iloc[-1] - capital)
    fees, slippage = float(daily["fees"].sum()), float(daily["slippage"].sum())
    observed_cost = fees + slippage
    same_fill_price_pnl = net_pnl + observed_cost
    annual_rows = []
    for row in annual.to_dict(orient="records"):
        annual_rows.append({key: (None if pd.isna(value) else value) for key, value in row.items()})
    return {
        "coverage": coverage,
        "annual_net_sharpe": [{"year": int(row["year"]), "sharpe": row.get("sharpe"),
                               "calendar_complete": bool(row["calendar_complete"]),
                               "full_calendar_year": bool(row["full_calendar_year"])} for row in annual_rows],
        "mean_full_year_net_sharpe": summary["mean_full_year_sharpe"],
        "worst_full_year_net_sharpe": summary["worst_full_year_sharpe"],
        "mean_exposure": summary["mean_exposure"], "turnover": summary["turnover"],
        "negative_net_return": summary["return"] < 0,
        "negative_return_years": [int(row["year"]) for row in annual_rows
                                  if row.get("return") is not None and row["return"] < 0],
        "cost_observations": {
            "net_pnl": net_pnl, "actual_fees": fees, "observed_fill_slippage": slippage,
            "observed_cost_over_initial_capital": observed_cost / capital,
            "same_filled_unit_price_pnl": same_fill_price_pnl,
            "positive_same_fill_price_pnl_but_net_loss": same_fill_price_pnl > 0 and net_pnl < 0,
            "fees_and_slippage_exceed_positive_net_pnl": 0 < net_pnl < observed_cost,
            "is_zero_cost_counterfactual": False,
            "interpretation": "costs already included in NAV; addback retains actual fills and capital feedback",
        },
        "causal_effect_estimated": False, "new_counterfactual_accounts": 0,
        "formal_target_success": False,
        "limitations": ["observational diagnostics do not identify factor or gate causal effects",
                        "cap, residual cash, inventory buffer and actual fills jointly affect the account",
                        *summary["limitations"]],
    }


def execute_strategy(panel, frames: Mapping[str, pd.DataFrame], spec: dict, *, start: str,
                     end: str, policy: AccountPolicy | None = None, cost_multiplier: float = 1.,
                     cancelled=None) -> dict:
    """Run one continuous account; failures and cancellation never return zero PnL.

    ``targets`` includes its original selection-plan attrs for caller persistence.
    Save/retry, subprocess budgets and source/asset provenance belong to callers.
    This function does not load assets, save files or launch another account.
    """
    _cancel(cancelled)
    normalized = validate_strategy(spec)
    missing = strategy_factor_ids(normalized) - frames.keys()
    if missing:
        raise ValueError("required strategy factor frames unavailable: " + str(sorted(missing)))
    _axes(panel, frames)
    if not isinstance(cost_multiplier, (int, float)) or not np.isfinite(cost_multiplier) or cost_multiplier < 0:
        raise ValueError("nonnegative finite cost multiplier required")
    policy = policy if policy is not None else AccountPolicy()
    targets, coverage = _targets(panel, frames, normalized, start=start, end=end, cancelled=cancelled)
    _cancel(cancelled)
    result = DailyAccount(panel, policy).run(targets, start=start, end=end,
                                           cost_multiplier=cost_multiplier, cancelled=cancelled)
    _cancel(cancelled)
    result.update({"diagnostics": _diagnostics(result, coverage), "normalized_spec": normalized,
                   "strategy_id": strategy_id(normalized), "targets": targets})
    return result
