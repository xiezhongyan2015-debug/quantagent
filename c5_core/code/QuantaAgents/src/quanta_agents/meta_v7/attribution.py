"""Executable pool benchmark and descriptive, paired account attribution.

The pool account allocates to every signal-day eligible stock. It is not a
constant-score top-N strategy and does not estimate a causal market counterfactual.
All accounts retain the existing adjusted-unit execution limitations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.portfolio import AccountPolicy, DailyAccount
from quanta_agents.research_kernel.compiler import validate_strategy
from quanta_agents.research_kernel.execution import _axes, _signal_positions
from .execution import execution_pool
from .temporal import scope_panel

VERSION = "v9_pool_attribution_v1"


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise InterruptedError("pool benchmark cancelled; no completed account")


def equal_pool_benchmark(panel, *, start, end, allocation, policy, cancelled=None):
    """Execute all eligible stocks at equal weights through the same account.

    Gross and per-stock cap, signal dates, capital and execution costs are shared
    with the supplied configuration. ``top_n`` is explicitly unused: a varying
    pool is normalized by its actual size. Binding caps leave cash without
    redistribution, consistently with the strategy execution engine. Holding
    buffers and inverse-risk allocations are rejected rather than ignored.
    """
    _cancel(cancelled)
    normalized = validate_strategy({"name": "equal_signal_day_pool",
        "score": {"op": "constant", "value": 0.}, "allocation": deepcopy(allocation)})
    allocation = normalized["allocation"]
    if allocation["weighting"] != "equal" or allocation["membership_buffer"] != 0:
        raise ValueError("equal pool benchmark requires equal weighting and zero membership buffer")
    if not isinstance(policy, AccountPolicy):
        raise ValueError("explicit AccountPolicy required for a comparable pool benchmark")
    scoped = scope_panel(panel, end=end)
    _axes(scoped, {})
    pool = execution_pool(scoped)
    positions = _signal_positions(scoped.dates, allocation, start, end)
    rows, counts = [], []
    for position in positions:
        _cancel(cancelled)
        eligible = pool.iloc[position].to_numpy(dtype=bool)
        count = int(eligible.sum())
        per_stock = (min(allocation["gross_exposure"] / count,
                         allocation["max_stock_weight"]) if count else 0.)
        rows.append(eligible.astype(float) * per_stock)
        counts.append(count)
    targets = pd.DataFrame(rows,
        index=pd.DatetimeIndex(scoped.dates[positions], name="signal_date"),
        columns=pool.columns, dtype=float)
    contract = {"version": VERSION, "kind": "equal_signal_day_execution_pool",
        "allocation": allocation, "top_n_applied": False,
        "pool": "same execution_pool as strategy diagnostics and execution",
        "weight_rule": "eligible * min(gross_exposure / eligible_count, max_stock_weight)",
        "empty_pool": "cash", "cap_redistribution": False,
        "signal_timing": "previous-session eligibility and next-session open execution",
        "exposure_matched_counterfactual": False}
    identifier = hashlib.sha256(json.dumps(contract, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    _cancel(cancelled)
    result = DailyAccount(scoped, policy).run(targets, start=start, end=end,
                                             cancelled=cancelled)
    _cancel(cancelled)
    result.update(targets=targets, benchmark_id=identifier,
        benchmark={**contract, "signal_dates": len(positions),
            "eligible_counts": counts,
            "preview_mean_gross_weight": float(targets.sum(axis=1).mean()) if len(targets) else None,
            "preview_is_executed_allocation": False},
        temporal_scope={**scoped.provenance["temporal_scope"],
                        "panel_fingerprint": scoped.fingerprint()})
    return result


def _validated_account(account, name):
    if not isinstance(account, dict) or not isinstance(account.get("policy"), dict):
        raise ValueError(name + " must contain original account and policy")
    try:
        policy = AccountPolicy(**account["policy"])
    except (TypeError, ValueError) as exc:
        raise ValueError(name + " has an invalid account policy") from exc
    if set(account["policy"]) != set(asdict(policy)):
        raise ValueError(name + " must preserve the complete execution policy")
    daily = account.get("daily")
    required = {"return", "nav", "exposure", "fees", "slippage", "turnover"}
    if (not isinstance(daily, pd.DataFrame) or len(daily) < 2
            or not required <= set(daily.columns)
            or not isinstance(daily.index, pd.DatetimeIndex)
            or daily.index.tz is not None or daily.index.hasnans
            or not daily.index.is_unique or not daily.index.is_monotonic_increasing
            or not daily.index.equals(daily.index.normalize())):
        raise ValueError(name + " requires a complete ordered daily account calendar")
    if not np.isfinite(daily[list(required)].to_numpy(dtype=float)).all():
        raise ValueError(name + " contains missing or nonfinite account observations")
    if daily["return"].le(-1).any() or daily["nav"].le(0).any():
        raise ValueError(name + " must have positive account wealth")
    expected_nav = policy.capital * (1 + daily["return"].to_numpy()).cumprod()
    if not np.allclose(daily["nav"].to_numpy(), expected_nav, rtol=1e-9, atol=1e-7):
        raise ValueError(name + " NAV and daily return lineage disagree")
    multiplier = account.get("summary", {}).get("cost_multiplier")
    if (isinstance(multiplier, bool) or not isinstance(multiplier, (int, float))
            or not np.isfinite(multiplier) or multiplier < 0):
        raise ValueError(name + " must retain a finite actual cost_multiplier")
    return daily, policy, float(multiplier)


def compare_accounts(candidate, benchmark):
    """Describe aligned net accounts without exposure matching or alpha claims.

    No missing rows are dropped and no mismatched calendars are intersected.
    OLS uses daily returns in excess of the common policy risk-free rate. Its
    intercept is descriptive, with no significance test or causal interpretation.
    """
    a, policy_a, cost_a = _validated_account(candidate, "candidate")
    b, policy_b, cost_b = _validated_account(benchmark, "benchmark")
    if not a.index.equals(b.index):
        raise ValueError("account calendars must exactly align")
    if asdict(policy_a) != asdict(policy_b) or cost_a != cost_b:
        raise ValueError("accounts require identical capital and execution cost policies")
    ar, br = a["return"].to_numpy(dtype=float), b["return"].to_numpy(dtype=float)
    difference = ar - br
    deviation = float(np.std(difference, ddof=1))
    relative_nav = float(np.prod(1 + ar) / np.prod(1 + br))
    rf = (1 + policy_a.risk_free_rate) ** (1 / 252) - 1
    x, y = br - rf, ar - rf
    xc, yc = x - x.mean(), y - y.mean()
    xmass, ymass = float(xc @ xc), float(yc @ yc)
    ols = {"status": "not_evaluable", "beta": None, "daily_intercept": None,
        "annualized_arithmetic_intercept": None, "r_squared": None,
        "regression": "candidate net return minus risk-free on benchmark net return minus risk-free, with intercept",
        "descriptive_only": True, "causal_alpha_estimated": False,
        "independent_evidence": False, "significance_test_performed": False}
    if len(ar) >= 3 and xmass > 0:
        beta = float(xc @ yc / xmass)
        intercept = float(y.mean() - beta * x.mean())
        residual = y - intercept - beta * x
        ols.update(status="computed", beta=beta, daily_intercept=intercept,
            annualized_arithmetic_intercept=252 * intercept,
            r_squared=float(1 - residual @ residual / ymass) if ymass > 0 else None)

    def observations(daily):
        return {"net_return": float(daily["nav"].iloc[-1] / policy_a.capital - 1),
            "mean_exposure": float(daily["exposure"].mean()),
            "fees": float(daily["fees"].sum()), "slippage": float(daily["slippage"].sum()),
            "turnover": float(daily["turnover"].sum())}

    am, bm = observations(a), observations(b)
    return {"version": VERSION, "sessions": len(a),
        "start": str(a.index[0].date()), "end": str(a.index[-1].date()),
        "exact_calendar_alignment": True, "rows_dropped": 0,
        "identical_execution_policy": True, "cost_multiplier": cost_a,
        "candidate": am, "benchmark": bm,
        "net_excess": {"mean_daily_return_difference": float(difference.mean()),
            "annualized_arithmetic_difference": float(252 * difference.mean()),
            "annualized_tracking_error": float(np.sqrt(252) * deviation),
            "information_ratio": float(np.sqrt(252) * difference.mean() / deviation) if deviation > 0 else None,
            "terminal_relative_nav": relative_nav,
            "cumulative_relative_return": relative_nav - 1.,
            "relative_return_semantics": "product(1+candidate net returns) / product(1+benchmark net returns) - 1"},
        "differences": {key: am[key] - bm[key] for key in am},
        "ols": ols, "exposure_matched": False,
        "selection_adjusted": False, "formal_financial_success": False,
        "limitations": ["relative performance can be positive while both accounts lose money",
            "full-pool versus selected-stock returns include selection, concentration, exposure and execution differences",
            "OLS intercept is descriptive and does not identify causal alpha or repair adaptive selection",
            "both accounts retain the existing adjusted-unit development execution limitations"]}
