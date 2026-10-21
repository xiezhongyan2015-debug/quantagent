"""Explicit temporal views: truncate numeric inputs before validating their values."""
from __future__ import annotations

from copy import deepcopy
from typing import Mapping

import pandas as pd

from quanta_agents.meta_v6.data import MarketPanel

VERSION = "v7_temporal_prefix_v1"


def _cutoff(end):
    value = pd.Timestamp(end)
    if pd.isna(value) or value.tz is not None or value != value.normalize():
        raise ValueError("end must be a finite timezone-free session date")
    return value


def _prefix(frame, cutoff):
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("dated DataFrame required")
    # Do not cast, scan, hash or validate numeric values after the cutoff.
    result = frame.loc[frame.index <= cutoff].copy(deep=True)
    result.attrs = deepcopy(frame.attrs)
    return result


def scope_panel(panel: MarketPanel, *, end: str) -> MarketPanel:
    """Keep all available history through end; never use later numeric rows.

    Original source/membership bindings remain provenance, not a claim of
    historical arrival certification. The resulting fingerprint is derived from
    this prefix; no full-panel fingerprint is computed while making the view.
    """
    cutoff = _cutoff(end)
    eligible = _prefix(panel.eligible, cutoff)
    if eligible.empty:
        raise ValueError("no sessions through the declared end")
    if eligible.isna().any().any() or not eligible.isin([True, False]).all().all():
        raise ValueError("scoped eligibility must be complete booleans")
    # Later missing/invalid eligibility may also upcast the original column.
    # Restore only the already validated prefix, without truth-coercing strings.
    eligible = eligible.astype(bool)
    fields = {}
    for name, frame in panel.fields.items():
        value = _prefix(frame, cutoff)
        # An out-of-scope malformed value may have made a column object-typed.
        # Coercion is deliberately after slicing and never fills missing values.
        fields[name] = value.apply(pd.to_numeric, errors="raise")
        fields[name].attrs = deepcopy(value.attrs)
    provenance = deepcopy(panel.provenance)
    provenance["temporal_scope"] = {"version": VERSION, "requested_end": cutoff.date().isoformat(),
        "first_session": str(eligible.index[0].date()), "last_session": str(eligible.index[-1].date()),
        "sessions": len(eligible), "history_preserved": True, "future_numeric_rows_read": False,
        "source_bindings_preserved": True, "new_arrival_certification": False}
    return MarketPanel(fields, eligible, provenance, {})


def scope_frames(frames: Mapping[str, pd.DataFrame], panel: MarketPanel) -> dict[str, pd.DataFrame]:
    """Select score prefixes and require exact axes; do not fill/reindex gaps."""
    if not isinstance(frames, Mapping):
        raise ValueError("frames must map identifiers to score DataFrames")
    result = {}
    cutoff = panel.dates[-1]
    for name, frame in frames.items():
        value = _prefix(frame, cutoff)
        if not value.index.equals(panel.dates) or not value.columns.equals(panel.eligible.columns):
            raise ValueError("score prefix must exactly match scoped market axes: " + str(name))
        result[name] = value.apply(pd.to_numeric, errors="raise")
        result[name].attrs = deepcopy(value.attrs)
    return result
