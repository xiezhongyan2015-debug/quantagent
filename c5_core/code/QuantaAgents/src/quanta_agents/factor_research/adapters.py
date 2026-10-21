"""Thin, account-free V9A adapters over the existing market panel and DSL.

Numeric permissions are checked before the existing loader is entered. Calendar
and source metadata may describe later years, but no 2025 price rows are loaded.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd

from quanta_agents.meta_v6.data import MarketPanel, load_market_panel
from quanta_agents.meta_v7.temporal import scope_panel
from quanta_agents.research_kernel.universe import execution_pool

VERSION = "v9a_factor_adapters_1"
LAST_AUTHORIZED_NUMERIC_DAY = pd.Timestamp("2024-12-31")
SIGNAL_FIELDS = ("open", "high", "low", "close", "volume", "amount")
POOL_FIELDS = ("is_st", "is_delisting")


def _day(value):
    day = pd.Timestamp(value)
    if pd.isna(day) or day.tz is not None or day != day.normalize():
        raise ValueError("finite naive calendar date required")
    return day


def load_research_panel(data_root, *, start, end, calendar_path,
                        membership_path=None, symbols=None, cache_dir=None):
    """Use the established predicate-first loader with six permitted inputs."""
    first, last = _day(start), _day(end)
    if not first <= last <= LAST_AUTHORIZED_NUMERIC_DAY:
        raise ValueError("V9A has no numeric authorization after 2024-12-31")
    return load_market_panel(data_root, start=str(first.date()), end=str(last.date()),
        authorized_start=str(first.date()), authorized_end=str(last.date()),
        calendar_path=calendar_path, membership_path=membership_path,
        symbols=symbols, optional_fields=POOL_FIELDS, cache_dir=cache_dir,
        exposure="previously_exposed_development")


def factor_panel(panel, *, end=None):
    """Preserve lag history; use one execution pool inside every nested rank.

    A prefix is selected before numeric validation/fingerprinting. The caller
    still owns phase authorization; this adapter never grants final access.
    """
    last = _day(end if end is not None else panel.eligible.index[-1])
    if last > LAST_AUTHORIZED_NUMERIC_DAY:
        raise ValueError("V9A cannot construct a numeric engine for 2025")
    scoped = scope_panel(panel, end=str(last.date()))
    missing = set(SIGNAL_FIELDS + POOL_FIELDS) - set(scoped.fields)
    if missing:
        raise ValueError("missing frozen V9A fields: " + str(sorted(missing)))
    pool = execution_pool(scoped)
    provenance = deepcopy(scoped.provenance)
    provenance["factor_research"] = {
        "version": VERSION,
        "universe_id": "execution_pool_120close_amount20_nonst_nondelisting_v1",
        "rank_pool": "same_signal_day_execution_pool_at_every_rank_node",
        "allowed_signal_fields": list(SIGNAL_FIELDS),
        "pool_fields_are_not_factor_operands": list(POOL_FIELDS),
        "historical_available_at_verified": False,
        "numeric_end": str(last.date()),
    }
    provenance["execution_only_fields"] = sorted(set(
        provenance.get("execution_only_fields", [])) | set(POOL_FIELDS))
    # Execution-only columns remain for label observability, but cannot be DSL
    # operands. Unknown custom fields are not admitted by this first protocol.
    keep = set(SIGNAL_FIELDS + POOL_FIELDS + ("open_observed",))
    fields = {key: value for key, value in scoped.fields.items() if key in keep}
    return MarketPanel(fields, pool, provenance, deepcopy(scoped.load_metrics))


def source_metadata(path):
    """Simple file metadata, useful for calendar-only future-year inventories."""
    file = Path(path).resolve()
    stat = file.stat()
    return {"path": str(file), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "numeric_values_loaded": False}
