"""Chronological period selection over the original immutable audited panel."""
from __future__ import annotations

import numpy as np
from .protocol import require

NS_DAY = 86_400_000_000_000
CHINA_OFFSET = 8 * 3_600_000_000_000


def day(date):
    return int(np.datetime64(date, "D").astype(np.int64))


def period_indices(dataset, start, end, config, require_labels=True):
    require(start < end, "invalid chronological interval")
    preceding = np.asarray(dataset.trading_days)[np.asarray(dataset.trading_days) < day(end)]
    require(len(preceding) >= 2 and config["embargo_trading_days"] == 2, "two real complete trading days required")
    cutoff_day = int(preceding[-2])
    cutoff_ns = cutoff_day * NS_DAY - CHINA_OFFSET
    candidate = (dataset.days >= day(start)) & (dataset.days < day(end)) & (dataset.days < cutoff_day)
    if require_labels:
        mature = (dataset.label_end_times > dataset.earliest_execution_times[:, None]).all(axis=1)
        mature &= (np.diff(dataset.label_end_times, axis=1) > 0).all(axis=1)
        mature &= (dataset.label_end_times < cutoff_ns).all(axis=1)
        mature &= dataset.label_end_times[:, 3] <= dataset.times[-1]
        candidate &= mature
    indices, below_count, low_variance = [], 0, 0
    for t in np.flatnonzero(candidate):
        valid = dataset.eligible_mask(int(t), 80, require_labels)
        if int(valid.sum()) < config["min_stocks"]:
            below_count += 1
            continue
        if require_labels and np.any(np.var(np.asarray(dataset.labels[t, valid], dtype=np.float64), axis=0) <= config["label_variance_floor"]):
            low_variance += 1
            continue
        indices.append(t)
    return np.asarray(indices, dtype=np.int64), {
        "start_inclusive": start, "end_exclusive": end,
        "embargo_cutoff_day": str(np.datetime64(cutoff_day, "D")),
        "all_actual_label_end_times_strictly_before_ns": cutoff_ns,
        "last_two_complete_calendar_dates": [str(np.datetime64(int(d), "D")) for d in preceding[-2:]],
        "eligible_sections": len(indices), "excluded_below_min_stocks": below_count,
        "excluded_label_variance": low_variance, "require_labels": require_labels,
        "sequence_length": 80, "slots_kept_by_rule": [0, 1, 2, 3],
    }
