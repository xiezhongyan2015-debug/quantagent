"""Deterministic research diagnostics. Descriptive evidence is not admission."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .store import clean


def factor_report(panel, frames, *, start, end, horizons=(5, 20)):
    """Cross-sectional IC with next-open labels and a terminal label purge.

    No label is inserted into the expression input registry. All comparisons
    remain development diagnostics, including negative and insufficient results.
    """
    from quanta_agents.meta_v6.factors import FactorEngine

    engine = FactorEngine(panel)
    selected = (panel.dates >= pd.Timestamp(start)) & (panel.dates <= pd.Timestamp(end))
    dates = panel.dates[selected]
    reports, ranked = {}, {}
    for factor_id, frame in frames.items():
        values = frame.where(panel.eligible).loc[dates]
        ranked[factor_id] = values.rank(axis=1, pct=True)
        denominator = int(panel.eligible.loc[dates].to_numpy().sum())
        reports[factor_id] = {
            "coverage": float(values.notna().to_numpy().sum() / denominator) if denominator else None,
            "horizons": {}, "standalone_ic_is_not_an_admission_gate": True}
    for horizon in horizons:
        # A signal at t is observed at t close; outcome ends at open[t+1+h].
        labels = engine.labels(horizon).loc[dates]
        end_pos = panel.dates.searchsorted(pd.Timestamp(end), side="right") - 1
        allowed_dates = panel.dates[:max(0, end_pos - horizon)]
        labels = labels.copy()
        labels.loc[~labels.index.isin(allowed_dates), :] = np.nan
        for factor_id, frame in frames.items():
            x = frame.loc[dates].where(labels.notna() & panel.eligible.loc[dates])
            y = labels.where(x.notna())
            count = x.notna().sum(axis=1)
            ic = x.rank(axis=1).corrwith(y.rank(axis=1), axis=1).where(count >= 5)
            annual = [{"year": int(year), "mean_ic": float(group.mean()),
                       "days": int(group.notna().sum())} for year, group in ic.groupby(ic.index.year)]
            reports[factor_id]["horizons"][str(horizon)] = {
                "mean_ic": float(ic.mean()), "daily_ic_std": float(ic.std()),
                "days": int(ic.notna().sum()), "annual": annual,
                "interpretation": "descriptive; serial dependence and multiple trials not corrected"}
    correlations = []
    ids = sorted(frames)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            x, y = frames[left].loc[dates], frames[right].loc[dates]
            mask = x.notna() & y.notna() & panel.eligible.loc[dates]
            daily = x.where(mask).rank(axis=1).corrwith(y.where(mask).rank(axis=1), axis=1)
            correlations.append({"left": left, "right": right,
                                 "mean_daily_rank_correlation": float(daily.mean()),
                                 "days": int(daily.notna().sum())})
    return clean({"scope": {"start": start, "end": end, "role": "exposed_development",
                            "label_timing": "open[t+1+h]/open[t+1]-1; terminal purge"},
                  "factors": reports, "correlations": correlations,
                  "limitations": ["Low standalone IC does not exclude conditional or interaction value.",
                                   "Correlation is descriptive; no causal contribution is asserted."]})


def compact_account(result):
    annual = result["annual"].reset_index().to_dict("records")
    daily = result["daily"]
    return clean({"summary": result["summary"], "annual": annual,
                  "execution": {"days": len(daily), "trades": len(result["trades"]),
                                "mean_exposure": float(daily.exposure.mean()),
                                "mean_turnover": float(daily.turnover.mean()),
                                "blocked_orders": int(daily.blocked_orders.sum()),
                                "max_stale_fraction": float(daily.stale_fraction.max())},
                  "diagnostics": result.get("diagnostics", {}),
                  "limitations": ["Development account evidence; independent holdout not established.",
                                   "Passing software acceptance does not establish profitable strategy performance."]})


def next_diagnostics(account):
    """Transparent routing suggestions, never an inferred causal explanation."""
    suggestions = ["paired_component_ablation", "slippage_sensitivity", "temporal_validation"]
    if account["execution"]["mean_exposure"] < .7:
        suggestions.append("exposure_matched_control")
    if account["execution"]["max_stale_fraction"] > 0:
        suggestions.append("stale_mark_attribution")
    return suggestions
