"""Independent coefficient and update replay for the first V10A specification.

No research evaluator or fitting implementation is imported. Moment accumulation
is by date (each day equal weight), using literal tie ranks and source labels.
"""
from __future__ import annotations

import math
import hashlib
import json

import numpy as np
import pandas as pd

from .oracle import _need, ranks, raw_labels, scalar_pearson


def _rank_features(values, pool):
    output = {}
    for name, frame in values.items():
        ranked = np.full(frame.shape, np.nan)
        for day in range(len(frame)):
            valid = pool[day] & np.isfinite(frame[day])
            if valid.any():
                ranked[day, valid] = ranks(frame[day, valid]) / int(valid.sum()) - .5
        output[name] = ranked
    return output


def _windows(dates, update_rule):
    index = pd.DatetimeIndex(dates)
    target = index[(index >= "2019-01-01") & (index <= "2024-12-31")]
    _need(len(target) > 0, "missing six-year prediction calendar")
    if update_rule == "fixed":
        return [("2016-01-01", "2018-12-31", target[0], target[-1])]
    _need(update_rule in ("quarterly_rolling3y", "quarterly_expanding"), "unsupported update rule")
    windows = []
    for quarter in target.to_period("Q").unique():
        first = quarter.start_time
        last = quarter.end_time.normalize()
        part = target[(target >= first) & (target <= last)]
        train_start = pd.Timestamp("2016-01-01") if update_rule == "quarterly_expanding" else max(
            pd.Timestamp("2016-01-01"), first - pd.DateOffset(years=3))
        windows.append((str(train_start.date()), str((first - pd.Timedelta(days=1)).date()), part[0], part[-1]))
    return windows


def _train_rows(dates, labels, ranked, pool, names, start, end):
    """Only expired labels and all-feature common cells enter any statistic."""
    rows = []
    for day, date in enumerate(dates):
        if not start <= date <= end or day + 6 >= len(dates) or dates[day + 6] > end:
            continue
        common = pool[day] & np.isfinite(labels[day])
        for name in names:
            common &= np.isfinite(ranked[name][day])
        if int(common.sum()) >= 100:
            rows.append((day, common))
    _need(len(rows) >= 60, "insufficient independently replayed training days")
    return rows


def _fit_moments(rows, labels, ranked, names, target, regularization):
    width = len(names)
    sx, sxx, sxy = np.zeros(width), np.zeros((width, width)), np.zeros(width)
    for day, common in rows:
        x = np.column_stack([ranked[name][day, common] for name in names])
        y = labels[day, common]
        if target == "rank_return_demeaned":
            y = ranks(y) / len(y)
        y = y - math.fsum(y) / len(y)
        sx += x.mean(axis=0)
        sxx += (x.T @ x) / len(y)
        sxy += (x.T @ y) / len(y)
    mean = sx / len(rows)
    covariance = sxx / len(rows) - np.outer(mean, mean)
    scale = np.sqrt(np.maximum(0., np.diag(covariance)))
    _need(bool((scale > 1e-12).all()), "constant or degenerate independent Ridge feature")
    gram = covariance / np.outer(scale, scale)
    beta = np.linalg.solve(gram + regularization * np.eye(width), (sxy / len(rows)) / scale)
    _need(np.isfinite(beta).all(), "nonfinite independent Ridge coefficients")
    return mean, scale, beta


def _fixed_directions(dates, values, labels, pool, names):
    directions = []
    for name in names:
        values_by_day = []
        for day, date in enumerate(dates):
            if not "2016-01-01" <= date <= "2018-12-31":
                continue
            common = pool[day] & np.isfinite(values[name][day]) & np.isfinite(labels[day])
            if common.sum() >= 100:
                value = scalar_pearson(values[name][day, common], labels[day, common])
                if value is not None:
                    values_by_day.append(value)
        _need(len(values_by_day) >= 60, "unknown initial training direction")
        mean = math.fsum(values_by_day) / len(values_by_day)
        _need(mean != 0, "zero initial training direction")
        directions.append(1 if mean > 0 else -1)
    return np.array(directions, dtype=float)


def _same_dates_claim(value, expected, index, *, upper=False):
    """Calendar boundary and its corresponding final session are equivalent."""
    claimed, canonical = pd.Timestamp(value), pd.Timestamp(expected)
    if upper:
        a = index[index <= claimed]
        b = index[index <= canonical]
    else:
        a = index[index >= claimed]
        b = index[index >= canonical]
    return bool(len(a) and len(b) and (a[-1] == b[-1] if upper else a[0] == b[0]))


def replay_combination(package, authority, arrays, factor_values):
    spec = package["spec"]
    dates, pool = authority["dates"], arrays["pool"]
    index = pd.DatetimeIndex(dates)
    names = spec["feature_ids"]
    _need(isinstance(names, list) and names == sorted(set(names)) and set(names) <= set(factor_values),
          "fixed unique canonical feature order required")
    _need(len(names) in (4, 8, 12, 24, 46), "unsupported first-version member count")
    method, target = spec["method"], spec["target"]
    _need(method in ("ridge", "equal_direction"), "unsupported combination method")
    _need(target in ("raw_return_demeaned", "rank_return_demeaned"), "unsupported training target")
    _need(spec.get("preprocessing") == "average_percentile_rank_in_original_execution_pool_minus_0.5",
          "unsupported input preprocessing")
    _need(spec["fit_start"] == "2016-01-01" and spec["fit_end"] == "2018-12-31",
          "initial training window differs from approved scheme")
    _need(spec.get("membership_updates") is False, "this replay supports registered fixed members")
    _need(set(names) <= set(spec["candidate_ids"]), "members absent from registered candidate set")
    if method == "ridge":
        _need(spec["ridge_lambda"] in (.001, .01, .1, 1), "regularization differs from first-version specification")
    else:
        _need(len(names) != 46, "46-member dense control must use Ridge")
    labels = raw_labels(dates, arrays["open"], arrays["open_observed"])
    ranked = _rank_features({name: factor_values[name] for name in names}, pool)
    directions = _fixed_directions(dates, factor_values, labels, pool, names) if method == "equal_direction" else None
    expected = _windows(dates, spec["update_rule"])
    models = package["models_by_interval"]
    _need(len(models) == len(expected), "missing or excess fixed-rule model updates")
    output = np.full(pool.shape, np.nan)
    for model, (start, end, first, last) in zip(models, expected):
        _need(model["status"] in ("fitted", "complete", "ok"), "incomplete combination fit cannot certify replay")
        _need(model["feature_order"] == names, "member changes violate registered fixed-member scheme")
        _need(model.get("common_feature_ids") == names, "unregistered features changed common training sample")
        _need(model.get("method", method) == method and model.get("target", target) == target,
              "model method or target differs from fixed scheme")
        _need(_same_dates_claim(model["fit_start"], start, index) and
              _same_dates_claim(model["fit_end"], end, index, upper=True), "fit window differs from frozen update rule")
        _need(_same_dates_claim(model["predict_start"], first, index) and
              _same_dates_claim(model["predict_end"], last, index, upper=True), "prediction interval differs from frozen update rule")
        _need(pd.Timestamp(model["fit_end"]) < first, "fit can read observations from the prediction interval")
        rows = _train_rows(dates, labels, ranked, pool, names, start, end)
        _need(model["train_days"] == len(rows) and model["train_cells"] == sum(int(common.sum()) for _, common in rows),
              "independent common training sample counts differ")
        fit_positions = np.flatnonzero((index >= start) & (index <= end))
        common_frame = pd.DataFrame(False, index=index[fit_positions], columns=authority["columns"])
        for day, common in rows:
            common_frame.loc[index[day]] = common
        # Reproduce only the published byte-identity convention; the mask itself
        # above was reconstructed independently from source labels and scores.
        sample_hash = hashlib.sha256()
        sample_hash.update(json.dumps([str(x) for x in common_frame.columns], ensure_ascii=False).encode())
        sample_hash.update(pd.util.hash_pandas_object(common_frame.astype(bool), index=True).values.tobytes())
        _need(model["sample_mask_id"] == sample_hash.hexdigest(), "independent common training sample identity differs")
        allowed_signals = [i for i, day in enumerate(dates) if start <= day <= end
                           and i + 6 < len(dates) and dates[i + 6] <= end and dates[i + 6][:4] == day[:4]]
        _need(bool(allowed_signals), "empty causal training window")
        _need(str(model["last_safe_signal_date"])[:10] == dates[allowed_signals[-1]],
              "last safe signal differs from independently expired label calendar")
        endpoint = str(model["last_label_endpoint"])[:10]
        _need(endpoint <= end and pd.Timestamp(endpoint) < first,
              "unexpired label endpoint entered model fit")
        _need(endpoint == dates[allowed_signals[-1] + 6], "last label endpoint differs from independent calendar")
        if method == "ridge":
            means, scales, beta = _fit_moments(rows, labels, ranked, names, target, spec["ridge_lambda"])
        else:
            means, scales, beta = np.zeros(len(names)), np.ones(len(names)), directions / len(names)
        for key, actual in (("feature_means", means), ("feature_scales", scales), ("coefficients", beta)):
            claim = np.asarray(model[key], float)
            _need(claim.shape == actual.shape and np.allclose(claim, actual, rtol=1e-8, atol=1e-10),
                  "independently replayed " + key + " differs")
        _need(model.get("intercept", 0.) == 0., "unexpected fitted intercept")
        for row in np.flatnonzero((index >= first) & (index <= last)):
            common = pool[row].copy()
            for name in names:
                common &= np.isfinite(ranked[name][row])
            if common.any():
                design = np.column_stack([ranked[name][row, common] for name in names])
                output[row, common] = ((design - means) / scales) @ beta
    return output
