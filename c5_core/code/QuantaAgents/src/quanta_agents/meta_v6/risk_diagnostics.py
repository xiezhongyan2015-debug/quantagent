"""Pure, descriptive risk-information evaluation; labels never become signals.

Call risk_information_semantics and freeze its result before loading new factor
values. This module neither reads files nor changes factor direction, score
inputs, portfolio targets, or factor-language operands.
"""
from __future__ import annotations

import hashlib
from typing import Mapping

import numpy as np
import pandas as pd

VERSION = "v6_risk_information_open_path_v1"
BLOCK_LENGTH = 20
BOOTSTRAP_REPETITIONS = 1000
BOOTSTRAP_SEED = 20260909
METRICS = ("future_vol", "future_downside", "future_entry_max_loss")


def _parameters(direction, horizon, minimum, quantiles):
    if type(direction) is not int or direction not in (-1, 1):
        raise ValueError("direction must remain the declared -1 or +1")
    if type(horizon) is not int or horizon < 2:
        raise ValueError("horizon must be an integer >= 2")
    if type(minimum) is not int or minimum < 3:
        raise ValueError("minimum must be an integer >= 3")
    if type(quantiles) is not int or quantiles < 2:
        raise ValueError("quantiles must be an integer >= 2")


def risk_information_semantics(*, direction=-1, horizon=20, minimum=30, quantiles=5,
                               control_names=(), observed_price_mask_supplied=False):
    """JSON-safe predeclaration metadata; does not require/read factor values."""
    _parameters(direction, horizon, minimum, quantiles)
    return {"version": VERSION, "role": "risk_information", "direction": direction,
        "horizon": horizon, "minimum_cross_section": minimum, "quantiles": quantiles,
        "annualization_sessions": 252, "volatility_ddof": 1,
        "entry": "signal D -> adjusted open D+1",
        "return_path": f"Exactly {horizon} returns: open[D+2]/open[D+1]-1 through open[D+{horizon+1}]/open[D+{horizon}]-1",
        "future_vol": "std(complete return path, ddof=1) * sqrt(252)",
        "future_downside": "sqrt(mean(min(return,0)^2)) * sqrt(252)",
        "future_entry_max_loss": f"max(0, 1-min(open[D+2:D+{horizon+2}])/open[D+1]); final included endpoint D+{horizon+1}; not peak-to-trough drawdown",
        "missing": "No forward fill; all entry/path prices finite and positive and all returns finite; any missing step invalidates all three labels",
        "calendar": "Union of supplied price/score/eligibility/session axes preserves explicitly supplied missing sessions; unprovided exchange sessions cannot be inferred",
        "eligibility": "Signal-day eligibility only; future membership never filters a signal-day sample",
        "price_observation_limit": "Nonmissing prices do not certify fresh quotes or executable prices; caller provides unfilled fixed-anchor adjusted opens",
        "observed_price_mask_supplied": bool(observed_price_mask_supplied),
        "observed_mask": "If supplied, require mask true for entry and every path endpoint; report price-only versus strict coverage; diagnostic sensitivity only, not future trading eligibility",
        "raw_rank_ic": "Spearman(raw score, positive risk label), average ties",
        "oriented_rank_ic": "Spearman(direction * raw score, negative risk label), average ties; positive means higher declared preference associates with lower future risk",
        "quantile_assignment": "Same signal-day oriented score percentile ranks, average ties, ceil(rank_pct * quantiles); tied scores never split by stock order; empty quantiles retained",
        "quantile_order": "1=lowest declared preference, last=highest declared preference; each metric reports positive risk",
        "controls": sorted(control_names),
        "control_method": "Same-day common finite sample: independently regress oriented-score ranks and negative-risk ranks on intercept plus control ranks, then Pearson-correlate residuals; rank-deficient controls flagged and not estimated",
        "control_interpretation": "Descriptive partial Spearman; supplied old-factor and log-amount controls must be causal signal-date features; no residual signal is returned",
        "bootstrap": {"block_length": BLOCK_LENGTH, "repetitions": BOOTSTRAP_REPETITIONS,
            "seed": BOOTSTRAP_SEED, "confidence": .95, "method": "noncircular moving contiguous date blocks; concatenate ceil(T/20) blocks and trim to T",
            "missing_dates": "Preserve date positions including NaN rows and unselected sessions within the selected span; never compress observed dates",
            "unit": "daily cross-sectional statistic, all stocks together; never independent stock-days",
            "minimum_calendar_dates": 2 * BLOCK_LENGTH, "minimum_observed_dates": BLOCK_LENGTH,
            "interpretation": "Descriptive percentile CI, not independent market evidence, a calibrated significance test, or multiple-testing correction"},
        "label_access": "Diagnostics only; must never register these labels as factor-expression fields or feed them into scores/targets",
        "execution_certified": False, "independent_evidence_claimed": False}


def _dates(index, name):
    dates = pd.DatetimeIndex(index)
    if (not dates.is_unique or not dates.is_monotonic_increasing or dates.hasnans
            or dates.tz is not None or not dates.equals(dates.normalize())):
        raise ValueError(f"{name} must have unique chronological naive session dates")
    return dates


def _frame(value, name, columns):
    if not isinstance(value, pd.DataFrame) or not value.columns.is_unique or not value.columns.equals(columns):
        raise ValueError(f"{name} must use the score stock columns in the same order")
    result = value.copy(deep=False)
    result.index = _dates(value.index, name)
    return result


def _finite(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _rank_ic(left, right, minimum):
    common = left.notna() & right.notna()
    count = common.sum(axis=1)
    a = left.where(common).rank(axis=1, method="average")
    b = right.where(common).rank(axis=1, method="average")
    with np.errstate(divide="ignore", invalid="ignore"):
        result = a.corrwith(b, axis=1)
    return result.where((count >= minimum) & (a.nunique(axis=1) > 1) & (b.nunique(axis=1) > 1)), count


def _block_indices(length, block_length, repetitions, seed):
    rng = np.random.default_rng(seed)
    blocks = (length + block_length - 1) // block_length
    starts = rng.integers(0, length - block_length + 1, size=(repetitions, blocks))
    return (starts[..., None] + np.arange(block_length)).reshape(repetitions, -1)[:, :length]


def date_block_bootstrap(daily_values, *, block_length=BLOCK_LENGTH,
                         repetitions=BOOTSTRAP_REPETITIONS, seed=BOOTSTRAP_SEED, confidence=.95):
    """Descriptive CI for daily statistics; reusable for ordinary return IC.

    Keep a complete supplied date index with NaNs in unobserved positions. A
    short/sparse history returns its mean and an explicit unavailable CI.
    """
    if isinstance(daily_values, pd.Series):
        daily_values = daily_values.to_frame(daily_values.name or "value")
    if not isinstance(daily_values, pd.DataFrame) or not daily_values.columns.is_unique:
        raise ValueError("daily_values must be a Series or uniquely named DataFrame")
    _dates(daily_values.index, "bootstrap")
    if type(block_length) is not int or block_length < 1 or type(repetitions) is not int or repetitions < 1:
        raise ValueError("invalid bootstrap block/repetition count")
    if type(seed) is not int or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap seed/confidence")
    values = daily_values.astype(float).replace([np.inf, -np.inf], np.nan)
    length = len(values)
    indices = _block_indices(length, block_length, repetitions, seed) if length >= 2 * block_length else None
    plan_hash = hashlib.sha256(indices.tobytes()).hexdigest() if indices is not None else None
    results = []
    for name in values:
        data = values[name].to_numpy()
        observed = int(np.isfinite(data).sum())
        row = {"statistic": name, "mean": _finite(values[name].mean()), "calendar_dates": length,
            "observed_dates": observed, "missing_dates": length-observed, "block_length": block_length,
            "repetitions": repetitions, "seed": seed, "confidence": confidence,
            "resampling_unit": "contiguous_dates_not_stock_days", "stock_day_iid": False,
            "resample_plan_sha256": plan_hash, "ci_low": None, "ci_high": None,
            "bootstrap_std": None, "bootstrap_fraction_mean_above_zero": None, "valid_draws": 0,
            "status": "insufficient_calendar_or_observed_dates", "descriptive_only": True}
        if indices is not None and observed >= block_length:
            draws = data[indices]
            counts = np.isfinite(draws).sum(axis=1)
            means = np.divide(np.nansum(draws, axis=1), counts,
                              out=np.full(repetitions, np.nan), where=counts > 0)
            means = means[np.isfinite(means)]
            if len(means):
                low, high = np.quantile(means, [(1-confidence)/2, (1+confidence)/2])
                row.update(ci_low=float(low), ci_high=float(high), valid_draws=len(means),
                    bootstrap_std=_finite(means.std(ddof=1)),
                    bootstrap_fraction_mean_above_zero=float((means > 0).mean()), status="descriptive_ci")
        results.append(row)
    return pd.DataFrame(results)


def _path_labels(prices, horizon):
    returns = (prices / prices.shift(1) - 1).replace([np.inf, -np.inf], np.nan)
    window = returns.rolling(horizon, min_periods=horizon)
    full = window.count().shift(-(horizon+1)).eq(horizon)
    vol = window.std(ddof=1).shift(-(horizon+1)) * np.sqrt(252)
    downside = returns.clip(upper=0).pow(2).rolling(horizon, min_periods=horizon).mean().pow(.5).shift(-(horizon+1)) * np.sqrt(252)
    low = prices.rolling(horizon, min_periods=horizon).min().shift(-(horizon+1))
    loss = (1 - low / prices.shift(-1)).clip(lower=0)
    return {name: frame.where(full & np.isfinite(frame)) for name, frame in zip(METRICS, (vol, downside, loss))}


def _partial_rank_ic(oriented, negative_risk, controls, common, minimum):
    output = np.full(len(oriented), np.nan)
    rank_deficient = np.zeros(len(oriented), dtype=bool)
    x_rank = oriented.where(common).rank(axis=1, method="average").to_numpy()
    y_rank = negative_risk.where(common).rank(axis=1, method="average").to_numpy()
    c_rank = [frame.where(common).rank(axis=1, method="average").to_numpy() for frame in controls.values()]
    for i, mask in enumerate(common.to_numpy()):
        if mask.sum() < max(minimum, len(c_rank) + 3):
            continue
        design = np.column_stack([np.ones(mask.sum()), *[rank[i, mask] for rank in c_rank]])
        response = np.column_stack([x_rank[i, mask], y_rank[i, mask]])
        coefficients, _, rank, _ = np.linalg.lstsq(design, response, rcond=None)
        if rank < design.shape[1]:
            rank_deficient[i] = True
            continue
        residual = response - design @ coefficients
        # Near-zero residuals mean the factor/risk is explained by controls;
        # numerical noise must not become a spurious perfect correlation.
        if (np.linalg.norm(residual, axis=0) <= 1e-10 * np.maximum(1, np.linalg.norm(response, axis=0))).any():
            continue
        output[i] = np.corrcoef(residual.T)[0, 1]
    return pd.Series(output, index=oriented.index), pd.Series(rank_deficient, index=oriented.index)


def evaluate_risk_information(scores, open_prices, eligible, signal_dates, *, direction=-1,
                              horizon=20, minimum=30, quantiles=5, controls=None, observed_price_mask=None):
    """Evaluate frozen risk-information scores without producing a trading signal.

    signal_dates selects which signals may be diagnosed. Full supplied price
    axes provide label endpoints. Bootstrap restores omitted calendar positions
    inside the selected span as NaNs, rather than joining nonadjacent dates.
    Optional controls are already causal, same-day old-factor/log-amount values.
    """
    _parameters(direction, horizon, minimum, quantiles)
    if not isinstance(scores, pd.DataFrame) or scores.empty or not scores.columns.is_unique:
        raise ValueError("scores must be a nonempty date-by-stock DataFrame")
    if controls is not None and not isinstance(controls, Mapping):
        raise ValueError("controls must map names to causal signal-date DataFrames")
    controls = dict(controls or {})
    if any(not isinstance(name, str) or not name for name in controls):
        raise ValueError("control names must be nonempty strings")
    columns = scores.columns
    scores = _frame(scores, "scores", columns)
    prices = _frame(open_prices, "open_prices", columns)
    eligible = _frame(eligible, "eligible", columns)
    dates = _dates(signal_dates, "signal_dates")
    if not len(dates):
        raise ValueError("signal_dates cannot be empty")
    calendar = scores.index.union(prices.index).union(eligible.index).union(dates).sort_values()
    bootstrap_calendar = calendar[(calendar >= dates[0]) & (calendar <= dates[-1])]
    mask = eligible.reindex(index=calendar).eq(True).fillna(False)
    prices = prices.reindex(index=calendar).astype(float)
    prices = prices.where(np.isfinite(prices) & prices.gt(0))
    base_labels = _path_labels(prices, horizon)
    if observed_price_mask is not None:
        observed = _frame(observed_price_mask, "observed_price_mask", columns).reindex(index=calendar).eq(True).fillna(False)
        labels = _path_labels(prices.where(observed), horizon)
    else:
        labels = base_labels
    labels = {name: values.reindex(dates).where(mask.reindex(dates)) for name, values in labels.items()}
    values = scores.reindex(dates).astype(float).replace([np.inf, -np.inf], np.nan).where(mask.reindex(dates))
    oriented = direction * values
    controls = {name: _frame(frame, name, columns).reindex(dates).astype(float).replace([np.inf, -np.inf], np.nan)
                for name, frame in sorted(controls.items())}
    common = values.notna()
    for frame in [*labels.values(), *controls.values()]:
        common = common & frame.notna()
    common_n = common.sum(axis=1)
    score_n = values.notna().sum(axis=1)
    eligible_n = mask.reindex(dates).sum(axis=1)
    # Buckets depend exclusively on signal-date oriented scores, never labels.
    buckets = np.ceil(oriented.rank(axis=1, method="average", pct=True) * quantiles)
    buckets = buckets.where(score_n.ge(minimum))
    daily, quantile_rows, coverage = [], [], []
    for metric, label in labels.items():
        raw, paired_n = _rank_ic(values, label, minimum)
        preference, _ = _rank_ic(oriented, -label, minimum)
        common_raw, _ = _rank_ic(values.where(common), label.where(common), minimum)
        common_oriented, _ = _rank_ic(oriented.where(common), -label.where(common), minimum)
        partial, deficient = (pd.Series(np.nan, index=dates), pd.Series(False, index=dates))
        if controls:
            partial, deficient = _partial_rank_ic(oriented, -label, controls, common, minimum)
        frame = pd.DataFrame({"date": dates, "metric": metric, "horizon": horizon,
            "raw_rank_ic": raw.to_numpy(), "oriented_rank_ic": preference.to_numpy(),
            "paired_n": paired_n.to_numpy(), "common_n": common_n.to_numpy(),
            "common_raw_rank_ic": common_raw.to_numpy(), "common_oriented_rank_ic": common_oriented.to_numpy(),
            "partial_oriented_rank_ic": partial.to_numpy(), "control_rank_deficient": deficient.to_numpy()})
        daily.append(frame)
        price_n = (base_labels[metric].reindex(dates).notna() & mask.reindex(dates)).sum(axis=1)
        label_n = label.notna().sum(axis=1)
        coverage.append(pd.DataFrame({"date": dates, "metric": metric, "eligible_n": eligible_n.to_numpy(),
            "score_n": score_n.to_numpy(), "price_only_label_n": price_n.to_numpy(), "label_n": label_n.to_numpy(),
            "paired_n": paired_n.to_numpy(), "common_n": common_n.to_numpy(),
            "score_coverage": score_n.div(eligible_n.replace(0, np.nan)).to_numpy(),
            "label_coverage": label_n.div(eligible_n.replace(0, np.nan)).to_numpy(),
            "paired_coverage": paired_n.div(eligible_n.replace(0, np.nan)).to_numpy()}))
        for q in range(1, quantiles+1):
            members = buckets.eq(q)
            n = (members & label.notna()).sum(axis=1)
            denominator = members.sum(axis=1)
            quantile_rows.append(pd.DataFrame({"date": dates, "metric": metric, "quantile": q,
                "signal_members": denominator.to_numpy(), "labeled_members": n.to_numpy(),
                "risk_mean": label.where(members).mean(axis=1).to_numpy(),
                "risk_median": label.where(members).median(axis=1).to_numpy(),
                "coverage": n.div(denominator.replace(0, np.nan)).to_numpy(),
                "common_members": (members & common).sum(axis=1).to_numpy(),
                "common_risk_mean": label.where(members & common).mean(axis=1).to_numpy()}))
    daily = pd.concat(daily, ignore_index=True)
    coverage = pd.concat(coverage, ignore_index=True)
    quantile_risk = pd.concat(quantile_rows, ignore_index=True)
    annual, uncertainty = [], []
    statistics = ("raw_rank_ic", "oriented_rank_ic", "common_raw_rank_ic", "common_oriented_rank_ic", "partial_oriented_rank_ic")
    for metric in METRICS:
        rows = daily.loc[daily.metric.eq(metric)].set_index("date")
        for year in sorted(set(dates.year)):
            part = rows.loc[rows.index.year == year]
            row = {"year": int(year), "metric": metric, "signal_dates": len(part),
                "paired_stock_dates": int(part.paired_n.sum()), "common_stock_dates": int(part.common_n.sum())}
            for statistic in statistics:
                row["mean_"+statistic] = _finite(part[statistic].mean())
                row["valid_dates_"+statistic] = int(part[statistic].notna().sum())
            cov = coverage.loc[coverage.metric.eq(metric) & coverage.date.dt.year.eq(year)]
            row["mean_paired_coverage"] = _finite(cov.paired_coverage.mean())
            for q in range(1, quantiles+1):
                group = quantile_risk.loc[quantile_risk.metric.eq(metric) & quantile_risk.date.dt.year.eq(year) & quantile_risk["quantile"].eq(q)]
                row[f"q{q}_mean_risk"] = _finite(group.risk_mean.mean())
                row[f"q{q}_mean_common_risk"] = _finite(group.common_risk_mean.mean())
                row[f"q{q}_valid_dates"] = int(group.risk_mean.notna().sum())
            annual.append(row)
        boot = date_block_bootstrap(rows[list(statistics)].reindex(bootstrap_calendar))
        boot.insert(0, "metric", metric)
        uncertainty.append(boot)
    semantics = risk_information_semantics(direction=direction, horizon=horizon, minimum=minimum, quantiles=quantiles,
        control_names=controls, observed_price_mask_supplied=observed_price_mask is not None)
    summary = {"semantics": semantics, "signal_dates": len(dates), "bootstrap_calendar_dates": len(bootstrap_calendar),
        "stocks": len(columns), "metrics": {metric: {statistic: _finite(daily.loc[daily.metric.eq(metric), statistic].mean())
            for statistic in statistics} for metric in METRICS}, "descriptive_only": True,
        "alpha_selection_verdict": None, "risk_labels_registered_as_signal_fields": False}
    return {"summary": summary, "daily_ic": daily, "quantile_risk": quantile_risk,
        "coverage": coverage, "annual": pd.DataFrame(annual),
        "common_sample": daily[["date", "metric", "paired_n", "common_n", "common_raw_rank_ic", "common_oriented_rank_ic", "partial_oriented_rank_ic", "control_rank_deficient"]].copy(),
        "bootstrap": pd.concat(uncertainty, ignore_index=True), "labels": labels}
