"""Training-scope descriptive factor, risk, condition and interaction evidence.

Labels live only in this evaluator. Candidate trees are explicit hypotheses over
the original scores, never fitted residual signals or automatically admitted alpha.
"""
from __future__ import annotations

from itertools import combinations
from typing import Mapping

import numpy as np
import pandas as pd

from quanta_agents.research_kernel.compiler import canonical_expression, factor_ids
from quanta_agents.research_kernel.store import clean
from .execution import execution_pool
from .temporal import scope_frames, scope_panel

VERSION = "v7_training_factor_lab_v1"
MINIMUM = 5
TAIL_LOSS_THRESHOLD = .05
ROLES = {"return", "risk", "condition", "interaction"}
ROLE_ALIASES = {"return_prediction": "return", "risk_information": "risk",
                "conditional_gate": "condition", "conditional_information": "condition"}


def semantics(horizons=(5, 20)):
    if (not isinstance(horizons, (list, tuple)) or not horizons or len(horizons) > 8
            or any(type(h) is not int or not 1 <= h <= 2520 for h in horizons)
            or len(set(horizons)) != len(horizons)):
        raise ValueError("unique bounded positive session horizons required")
    return {"version": VERSION, "horizons": list(horizons), "minimum_cross_section": MINIMUM,
        "scope": "caller-frozen training end; truncate panel and scores before numeric validation",
        "pool": "same signal-day execution pool: membership/complete bar, 120 closes, positive amount20, non-ST/non-delisting",
        "return_label": "open[t+1+h]/open[t+1]-1; both observed positive endpoints; h+1 terminal sessions unknown",
        "risk_labels": "complete h open-open steps from t+1 to t+h+1; no filling; no future membership filter",
        "future_vol": "sample std of complete h returns * sqrt(252); unavailable at h=1",
        "future_downside": "sqrt(mean(min(step_return,0)^2))*sqrt(252)",
        "future_entry_max_loss": "max(0,1-min(open[t+2:t+h+2])/open[t+1]); final endpoint t+h+1",
        "future_loss_event": {"definition": "future_entry_max_loss >= threshold", "threshold": TAIL_LOSS_THRESHOLD},
        "rank_ic": "same-day Spearman on common finite scores and label; average ties; min 5 stocks",
        "risk_rank_ic": "raw score versus positive risk; no inferred direction or return-IC admission gate",
        "pair_transform": "(average percentile rank(left)-0.5)*(average percentile rank(right)-0.5), each rank in the signal-day execution pool; ties preserved",
        "centering": "fixed 0.5 percentile midpoint, not fitted mean; no whole-sample normalization",
        "conditions": "right factor rank <=0.5 and >0.5; report left return IC in both signal-day groups",
        "incremental": "same-day common-sample OLS partial Spearman controlling both marginal score ranks and intercept; descriptive only; no residual score returned",
        "candidate_policy": "save all requested interaction hypotheses irrespective of marginal IC; explicit original-score trees, no automatic promotion",
        "label_access": "diagnostic output only; no label frames, fitted weights or residual signals returned",
        "coverage": "pool denominator includes missing scores and terminal labels; missing sessions never compressed",
        "historical_arrival_verified": False, "independent_evidence_claimed": False,
        "multiple_testing_corrected": False, "execution_certified": False}


def _stats(daily, counts=None):
    result = {"mean_ic": float(daily.mean()), "daily_ic_std": float(daily.std()),
              "observed_days": int(daily.notna().sum()), "calendar_days": len(daily),
              "missing_days": int(daily.isna().sum()),
              "annual": [{"year": int(year), "mean_ic": float(part.mean()),
                          "observed_days": int(part.notna().sum()), "calendar_days": len(part)}
                         for year, part in daily.groupby(daily.index.year)]}
    if counts is not None:
        result["paired_cells"] = int(counts.sum())
    return result


def _ic(left, right, mask=None):
    common = left.notna() & right.notna()
    if mask is not None:
        common &= mask
    a = left.where(common).rank(axis=1, method="average")
    b = right.where(common).rank(axis=1, method="average")
    counts = common.sum(axis=1)
    daily = a.corrwith(b, axis=1).where((counts >= MINIMUM) & a.nunique(axis=1).gt(1) & b.nunique(axis=1).gt(1))
    return _stats(daily, counts)


def _coverage(scores, pool, label=None):
    valid = scores.notna() & pool
    if label is not None:
        valid &= label.notna()
    denominator = int(pool.to_numpy().sum())
    n = int(valid.to_numpy().sum())
    return {"pool_cells": denominator, "observed_cells": n, "missing_cells": denominator - n,
            "fraction": n / denominator if denominator else None,
            "annual": [{"year": int(year), "pool_cells": int(pool.loc[dates].to_numpy().sum()),
                        "observed_cells": int(valid.loc[dates].to_numpy().sum())}
                       for year, dates in pd.Series(pool.index, index=pool.index).groupby(pool.index.year)]}


def _labels(panel, horizons):
    opening = panel.fields["open"].where(np.isfinite(panel.fields["open"]) & panel.fields["open"].gt(0))
    if "open_observed" in panel.fields:
        opening = opening.where(panel.fields["open_observed"].eq(1))
    entry = opening.shift(-1)
    result = {}
    for h in horizons:
        exit_price = opening.shift(-h - 1)
        returns = (exit_price / entry - 1).where(np.isfinite(exit_price / entry - 1))
        complete = entry.notna()
        mean = entry * 0
        variance_mass = entry * 0
        downside = entry * 0
        minimum = entry.copy()
        previous = entry
        for step in range(1, h + 1):
            current = opening.shift(-step - 1)
            step_return = current / previous - 1
            complete &= current.notna() & np.isfinite(step_return)
            delta = step_return - mean
            mean = mean + delta / step
            variance_mass = variance_mass + delta * (step_return - mean)
            downside = downside + step_return.clip(upper=0).pow(2)
            minimum = np.minimum(minimum, current)
            previous = current
        loss = (1 - minimum / entry).clip(lower=0).where(complete)
        vol = (variance_mass.clip(lower=0) / (h - 1)).pow(.5) * np.sqrt(252) if h > 1 else entry * np.nan
        result[h] = {"return": returns,
            "future_vol": vol.where(complete),
            "future_downside": (downside / h).pow(.5).mul(np.sqrt(252)).where(complete),
            "future_entry_max_loss": loss,
            "future_loss_event": loss.ge(TAIL_LOSS_THRESHOLD).astype(float).where(loss.notna())}
    return result


def _roles(name, frame, overrides):
    declared = str(frame.attrs.get("field_role", frame.attrs.get("role", ""))).lower()
    if frame.attrs.get("is_label") is True or any(word in declared for word in ("label", "future", "forward", "target", "outcome")):
        raise ValueError("label-role frames cannot be factor lab signal inputs: " + name)
    values = overrides.get(name, frame.attrs.get("roles", frame.attrs.get("role", ["return"])))
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values or any(not isinstance(v, str) for v in values):
        raise ValueError("factor roles must be a nonempty role list: " + name)
    result = sorted({ROLE_ALIASES.get(role, role) for role in values})
    if set(result) - ROLES:
        raise ValueError("unknown factor role: " + name)
    return result


def _partial(interaction, label, left, right):
    common = interaction.notna() & label.notna() & left.notna() & right.notna()
    ranked = [frame.where(common).rank(axis=1).to_numpy(dtype=float) for frame in (interaction, label, left, right)]
    values = np.full(len(interaction), np.nan)
    counts = common.sum(axis=1)
    deficient = 0
    for i, mask in enumerate(common.to_numpy()):
        if mask.sum() < MINIMUM:
            continue
        x, y, a, b = (frame[i, mask] for frame in ranked)
        design = np.column_stack([np.ones(len(x)), a, b])
        if np.linalg.matrix_rank(design) < 3:
            deficient += 1
            continue
        residual = np.column_stack([x, y]) - design @ np.linalg.lstsq(design, np.column_stack([x, y]), rcond=None)[0]
        if min(np.linalg.norm(residual[:, 0]), np.linalg.norm(residual[:, 1])) > 1e-10:
            values[i] = np.corrcoef(residual.T)[0, 1]
    return {**_stats(pd.Series(values, index=interaction.index), counts), "rank_deficient_days": deficient,
            "controls": "intercept plus both marginal ranks", "residual_signal_created": False}


def evaluate_factors(panel, frames, *, start, end, horizons=(5, 20), pairs=None, controls=None):
    """Return a JSON-safe training report; caller must freeze end before reading scores.

    controls currently accepts only roles={factor_id:[role,...]}; statistical
    controls beyond the two declared pair marginals require a future capability.
    """
    contract = semantics(horizons)
    if not isinstance(frames, Mapping) or not frames or len(frames) > 256:
        raise ValueError("provide 1..256 registered factor score frames")
    if controls is None:
        controls = {}
    if not isinstance(controls, dict) or set(controls) - {"roles"}:
        raise ValueError("unsupported factor lab controls; only explicit roles mapping is available")
    overrides = controls.get("roles", {})
    if not isinstance(overrides, dict) or set(overrides) - set(frames):
        raise ValueError("role overrides must reference provided factors")
    scoped = scope_panel(panel, end=end)
    values = scope_frames(frames, scoped)
    start_day = pd.Timestamp(start)
    if pd.isna(start_day) or start_day.tz is not None or start_day > pd.Timestamp(end):
        raise ValueError("finite ordered training dates required")
    dates = scoped.dates[scoped.dates >= start_day]
    if not len(dates):
        raise ValueError("no training signal dates")
    pool = execution_pool(scoped)
    values = {name: frame.where(np.isfinite(frame)).where(pool).loc[dates] for name, frame in values.items()}
    # Use the original score attrs for roles; numerical transforms never create roles.
    roles = {name: _roles(name, frames[name], overrides) for name in values}
    for name in values:
        factor_ids({"op": "factor", "id": name})
    ranks = {name: value.rank(axis=1, method="average", pct=True) for name, value in values.items()}
    pool = pool.loc[dates]
    labels = {h: {key: value.loc[dates] for key, value in group.items()} for h, group in _labels(scoped, horizons).items()}
    reports = {}
    for name, scores in values.items():
        row = {"roles": roles[name], "coverage": _coverage(scores, pool), "horizons": {}, "risk": {},
               "automatic_admission": False, "return_ic_is_not_role_admission": True}
        for h, group in labels.items():
            row["horizons"][str(h)] = {**_ic(scores, group["return"]), "coverage": _coverage(scores, pool, group["return"])}
            if "risk" in roles[name]:
                row["risk"][str(h)] = {metric: {**_ic(scores, label), "coverage": _coverage(scores, pool, label)}
                                      for metric, label in group.items() if metric != "return"}
        reports[name] = row
    correlations = [{"left": a, "right": b, **_ic(values[a], values[b])} for a, b in combinations(sorted(values), 2)]
    if pairs is None:
        pairs = []
    if not isinstance(pairs, list) or len(pairs) > 64:
        raise ValueError("pairs must be an explicit list of at most 64 pair declarations")
    conditions, interactions, candidates, seen = [], [], [], set()
    for pair in pairs:
        if (not isinstance(pair, dict) or set(pair) != {"left", "right"}
                or pair["left"] not in values or pair["right"] not in values or pair["left"] == pair["right"]):
            raise ValueError("pair must reference two distinct provided factors")
        left, right = pair["left"], pair["right"]
        if (left, right) in seen:
            interactions.append({**pair, "status": "duplicate", "automatic_admission": False})
            continue
        seen.add((left, right))
        score = (ranks[left] - .5) * (ranks[right] - .5)
        expression = canonical_expression({"op": "multiply", "args": [
            {"op": "subtract", "args": [{"op": "rank", "args": [{"op": "factor", "id": name}]},
                                           {"op": "constant", "value": .5}]} for name in (left, right)]})
        row = {**pair, "status": "evaluated", "expression": expression, "coverage": _coverage(score, pool),
               "horizons": {}, "automatic_admission": False}
        for h, group in labels.items():
            row["horizons"][str(h)] = {"raw_ic": _ic(score, group["return"]),
                "partial_ic": _partial(score, group["return"], values[left], values[right]),
                "coverage": _coverage(score, pool, group["return"])}
            for name, mask in (("low", ranks[right].le(.5)), ("high", ranks[right].gt(.5))):
                conditions.append({**pair, "horizon": h, "condition": name,
                                   **_ic(values[left], group["return"], mask), "condition_uses_future": False})
        interactions.append(row)
        candidates.append({"kind": "interaction_hypothesis", "factor_ids": [left, right], "expression": expression,
                           "automatically_admitted": False, "direction": "not selected; hypothesis only"})
    return clean({"version": VERSION, "status": "completed", "scope": {"start": str(dates[0].date()),
        "end": str(dates[-1].date()), "role": "training_development", "sessions": len(dates),
        "panel_fingerprint": scoped.fingerprint(), "validation_values_returned": False}, "semantics": contract,
        "factors": reports, "correlations": correlations, "conditions": conditions, "interactions": interactions,
        "candidate_expressions": candidates, "shared_computation": {"label_horizons": len(labels),
            "rank_frames": len(ranks), "account_executions": 0},
        "limitations": ["Caller must freeze the training cutoff; this function does not authorize data access.",
            "Training diagnostics are exposed evidence, not independent validation or multiple-testing correction.",
            "Observed prices are not certified fresh or executable quotes; historical publication remains unverified.",
            "Pair partial IC is descriptive and does not create a tradable residual alpha."]})
