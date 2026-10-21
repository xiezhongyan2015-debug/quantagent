"""Reusable date-by-stock panels with explicit source and exposure provenance.

The Parquet loader applies its authorized date predicate before returning any
numeric data to Python. Parquet row groups can span outside that predicate;
this is logical range isolation, not a promise of byte/page-level isolation.
Prices and returns are never forward-filled. No model or execution engine is
called here, and this module has no 16-stock or 512-session research limit.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Sequence
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


VERSION = "v6_market_panel_v1"
PRICE_FIELDS = ("open", "high", "low", "close")
BASE_FIELDS = (*PRICE_FIELDS, "volume", "amount")
OPTIONAL_SOURCE_FIELDS = (
    "float_shares", "total_shares", "float_market_cap", "total_market_cap",
    "is_st", "is_delisting", "gu_1m", "gd_1m", "rbar_up17", "rbar_down17",
    "r_0931_1000", "r_1001_1030", "overnight_return",
)
EXECUTION_FIELDS = ("raw_open", "raw_close", "raw_prev_close", "qfq_ratio",
                    "adjustment_factor", "bar_observed", "open_observed")


class PanelError(ValueError):
    """An input or cache cannot support the requested panel identity."""


def _need(condition, message):
    if not condition:
        raise PanelError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _day(value):
    result = pd.Timestamp(value)
    _need(result.tz is None and result == result.normalize(), "daily dates must be timezone-free calendar dates")
    return result


def _code(value):
    value = str(value).lower()
    _need(re.fullmatch(r"(?:sh|sz)\d{6}", value) is not None, "unsupported canonical stock code")
    return value


@dataclass
class MarketPanel:
    fields: dict[str, pd.DataFrame]
    eligible: pd.DataFrame
    provenance: dict
    # Measurements are operational observations, excluded from semantic identity.
    load_metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        _need(bool(self.fields), "panel requires fields")
        _need(isinstance(self.eligible, pd.DataFrame), "eligible must be a DataFrame")
        index, columns = self.eligible.index, self.eligible.columns
        _need(isinstance(index, pd.DatetimeIndex) and index.tz is None,
              "panel rows must be timezone-free DatetimeIndex")
        _need(index.is_unique and index.is_monotonic_increasing and columns.is_unique,
              "panel axes must be unique and dates ordered")
        _need(len(index) > 0 and len(columns) > 0, "empty panel axes")
        _need(not self.eligible.isna().any().any() and all(t == bool for t in self.eligible.dtypes),
              "eligibility must be explicit nonmissing booleans")
        for name, frame in self.fields.items():
            _need(isinstance(name, str) and isinstance(frame, pd.DataFrame), "named DataFrame fields required")
            _need(frame.index.equals(index) and frame.columns.equals(columns), "all panel fields must have identical axes")
            _need(all(pd.api.types.is_numeric_dtype(t) for t in frame.dtypes), "numeric field required: " + name)
            _need(not np.isinf(frame.to_numpy(dtype=float)).any(), "infinite field values are unsupported")

    @property
    def dates(self):
        return self.eligible.index

    @property
    def symbols(self):
        return list(self.eligible.columns)

    @property
    def nbytes(self):
        return int(sum(f.memory_usage(index=True, deep=True).sum() for f in self.fields.values())
                   + self.eligible.memory_usage(index=True, deep=True).sum())

    def copy(self):
        return MarketPanel({k: v.copy(deep=True) for k, v in self.fields.items()},
                           self.eligible.copy(deep=True), deepcopy(self.provenance), deepcopy(self.load_metrics))

    def fingerprint(self):
        h = hashlib.sha256(_json(self.provenance).encode())
        for name, frame in [("eligible", self.eligible), *sorted(self.fields.items())]:
            h.update(name.encode())
            h.update(_json([str(c) for c in frame.columns]).encode())
            h.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
        return h.hexdigest()


def membership_intervals(path):
    """Read metadata only; preserve the entire historical interval list."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        _need(len(parts) == 3, "membership row requires symbol/start/end")
        # Old interval files also contain retired source identifiers (e.g.
        # SHT00018 in 2005-2007). Keep their metadata; require a supported
        # canonical code only if its interval enters the requested universe.
        symbol, start, end = parts[0].lower(), _day(parts[1]), _day(parts[2])
        _need(re.fullmatch(r"[a-z0-9]+", symbol) is not None, "invalid membership identifier")
        _need(start <= end, "membership interval reversed")
        rows.append((symbol, start, end))
    _need(bool(rows), "empty membership source")
    return rows


def historical_universe(membership_path, *, start, end):
    start, end = _day(start), _day(end)
    return sorted({_code(c) for c, a, b in membership_intervals(membership_path) if a <= end and b >= start})


def inspect_parquet_sources(data_root, symbols: Sequence[str]):
    """Metadata/hash inventory only; no market value arrays are decoded."""
    root = Path(data_root).resolve()
    result = []
    for symbol in symbols:
        symbol = _code(symbol)
        path = root / (symbol + ".parquet")
        if not path.is_file():
            result.append({"symbol": symbol, "path": str(path), "status": "missing"})
            continue
        metadata = pq.ParquetFile(path)
        names = metadata.schema_arrow.names
        _need("date" in names and "code" in names, "source lacks date/code columns")
        date_column = names.index("date")
        groups = []
        for number in range(metadata.metadata.num_row_groups):
            group = metadata.metadata.row_group(number)
            stats = group.column(date_column).statistics
            groups.append({"rows": group.num_rows,
                "min_date": pd.Timestamp(stats.min).date().isoformat() if stats and stats.has_min_max else None,
                "max_date": pd.Timestamp(stats.max).date().isoformat() if stats and stats.has_min_max else None})
        result.append({"symbol": symbol, "path": str(path), "status": "present",
            "bytes": path.stat().st_size, "sha256": _sha(path), "columns": names,
            "rows_in_file": metadata.metadata.num_rows, "row_groups": groups})
    return result


class _MemorySample:
    def __enter__(self):
        self.wall, self.cpu = time.perf_counter(), time.process_time()
        self.peak = self.start = None
        self.stop = threading.Event()
        try:
            import psutil
            self.process = psutil.Process()
            self.peak = self.start = self.process.memory_info().rss
            def sample():
                while not self.stop.wait(.05):
                    self.peak = max(self.peak, self.process.memory_info().rss)
            self.thread = threading.Thread(target=sample, daemon=True)
            self.thread.start()
        except ImportError:
            self.thread = None
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
            self.peak = max(self.peak, self.process.memory_info().rss)
        self.metrics = {"wall_seconds": time.perf_counter() - self.wall,
            "process_cpu_seconds": time.process_time() - self.cpu, "rss_at_start_bytes": self.start,
            "sampled_peak_rss_bytes": self.peak, "rss_sample_interval_seconds": .05}


def _source_unchanged(proof):
    if proof["status"] == "missing":
        _need(not Path(proof["path"]).exists(), "missing source appeared during load")
    else:
        _need(Path(proof["path"]).stat().st_size == proof["bytes"] and _sha(proof["path"]) == proof["sha256"],
              "source changed during panel construction")


def _cache_read(folder, key):
    manifest_path = folder / "manifest.json"
    _need(manifest_path.is_file(), "cache manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _need(manifest["cache_key"] == key and manifest["version"] == VERSION, "cache identity mismatch")
    _need(manifest["provenance"]["cache_key"] == key
          and _digest(manifest["provenance"]["request"]) == key, "cache source binding mismatch")
    _need(_sha(folder / "panel.npz") == manifest["payload_sha256"], "cache payload hash mismatch")
    with np.load(folder / "panel.npz", allow_pickle=False) as data:
        dates = pd.DatetimeIndex(data["dates"].astype("datetime64[ns]"), name="date")
        symbols = pd.Index(data["symbols"].tolist(), name="symbol")
        fields = {name: pd.DataFrame(data["f" + str(i)].copy(), index=dates, columns=symbols)
                  for i, name in enumerate(manifest["field_names"])}
        eligible = pd.DataFrame(data["eligible"].copy(), index=dates, columns=symbols)
    panel = MarketPanel(fields, eligible, manifest["provenance"])
    _need(panel.fingerprint() == manifest["panel_fingerprint"], "cache semantic fingerprint mismatch")
    return panel


def _cache_write(folder, key, panel):
    folder.parent.mkdir(parents=True, exist_ok=True)
    temporary = folder.parent / ("." + key + "." + uuid4().hex + ".tmp")
    temporary.mkdir()
    try:
        names = sorted(panel.fields)
        arrays = {"dates": panel.dates.to_numpy().astype("datetime64[ns]").astype("int64"),
            "symbols": np.array(panel.symbols), "eligible": panel.eligible.to_numpy()}
        arrays.update({"f" + str(i): panel.fields[name].to_numpy() for i, name in enumerate(names)})
        np.savez_compressed(temporary / "panel.npz", **arrays)
        manifest = {"version": VERSION, "cache_key": key, "field_names": names,
            "payload_sha256": _sha(temporary / "panel.npz"), "panel_fingerprint": panel.fingerprint(),
            "provenance": panel.provenance}
        (temporary / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        try:
            temporary.rename(folder)
        except OSError:
            _need(folder.exists(), "cannot publish cache")
            _cache_read(folder, key)
    finally:
        # Only this uniquely created child of the explicit cache directory.
        if temporary.exists():
            _need(temporary.resolve().parent == folder.parent.resolve(), "unsafe temporary cache cleanup")
            shutil.rmtree(temporary)


def load_market_panel(data_root, *, start, end, authorized_start, authorized_end,
                      calendar_path, membership_path=None, symbols=None,
                      optional_fields=OPTIONAL_SOURCE_FIELDS, cache_dir=None,
                      exposure="previously_exposed_development") -> MarketPanel:
    """Load one explicit historical universe once and reuse it across factors.

    Membership is evaluated on the signal date only, never tomorrow's membership.
    Missing files/rows stay in the fixed axes with NaN and false eligibility.
    Fixed starting-anchor prices cancel a uniform future QFQ re-scaling; they do
    not certify correction history or historical publication/arrival times.
    """
    with _MemorySample() as meter:
        start, end = _day(start), _day(end)
        auth_start, auth_end = _day(authorized_start), _day(authorized_end)
        _need(auth_start <= start <= end <= auth_end, "requested numeric range outside explicit authorization")
        _need(exposure == "previously_exposed_development", "this loader does not grant holdout admission")
        _need(set(optional_fields) <= set(OPTIONAL_SOURCE_FIELDS), "unsupported optional source fields")
        calendar_path = Path(calendar_path).resolve()
        days = pd.DatetimeIndex([_day(d) for d in calendar_path.read_text(encoding="utf-8").splitlines() if d.strip()])
        _need(days.is_unique and days.is_monotonic_increasing, "market calendar must be unique and ordered")
        days = days[(days >= start) & (days <= end)].rename("date")
        _need(len(days) > 0, "no authorized market sessions")
        intervals = membership_intervals(membership_path) if membership_path is not None else None
        if symbols is None:
            _need(intervals is not None, "explicit symbols or historical membership source required")
            codes = sorted({_code(c) for c, a, b in intervals if a <= end and b >= start})
        else:
            codes = [_code(c) for c in symbols]
        _need(bool(codes) and len(codes) == len(set(codes)), "fixed unique nonempty symbols required")
        columns = pd.Index(codes, name="symbol")
        member = pd.DataFrame(False if intervals is not None else True, index=days, columns=columns)
        if intervals is not None:
            for code, first, last in intervals:
                if code in member.columns:
                    member.loc[(days >= first) & (days <= last), code] = True
        proofs = inspect_parquet_sources(data_root, codes)
        source_manifest = Path(data_root).resolve() / "manifest.json"
        identity = {"version": VERSION, "loader_sha256": _sha(__file__), "data_root": str(Path(data_root).resolve()),
            "start": start.date().isoformat(), "end": end.date().isoformat(),
            "authorized_start": auth_start.date().isoformat(), "authorized_end": auth_end.date().isoformat(),
            "symbols": codes, "calendar_path": str(calendar_path), "calendar_sha256": _sha(calendar_path),
            "membership_path": str(Path(membership_path).resolve()) if membership_path else None,
            "membership_sha256": _sha(membership_path) if membership_path else None,
            "source_manifest_sha256": _sha(source_manifest) if source_manifest.is_file() else None,
            "optional_fields": sorted(optional_fields), "source_files": proofs,
            "exposure": exposure, "adjustment": "source_qfq_divided_by_first_valid_requested_session_ratio"}
        key = _digest(identity)
        folder = Path(cache_dir).resolve() / key if cache_dir is not None else None
        cache_hit = folder is not None and folder.exists()
        if cache_hit:
            panel = _cache_read(folder, key)
        else:
            names = list(BASE_FIELDS) + list(optional_fields) + list(EXECUTION_FIELDS)
            fields = {name: pd.DataFrame(np.nan, index=days, columns=columns) for name in names}
            fields["bar_observed"].loc[:, :] = 0.
            fields["open_observed"].loc[:, :] = 0.
            anchors, quality = {}, {}
            for proof in proofs:
                code = proof["symbol"]
                if proof["status"] == "missing":
                    quality[code] = {"source_missing": True, "returned_rows": 0}
                    continue
                required = {"date", "code", *BASE_FIELDS, "qfq_ratio"}
                _need(required <= set(proof["columns"]), "source lacks mandatory adjusted daily fields: " + code)
                requested = sorted(required | ({"raw_open", "raw_close", "raw_prev_close", *optional_fields} & set(proof["columns"])))
                table = pq.read_table(proof["path"], columns=requested,
                    filters=[("date", ">=", start.to_pydatetime()), ("date", "<=", end.to_pydatetime())])
                frame = table.to_pandas()
                _need(not len(frame) or set(frame["code"].str.lower()) == {code}, "foreign stock inside source file")
                frame["date"] = pd.to_datetime(frame["date"])
                _need(not frame["date"].duplicated().any(), "duplicate source stock/date")
                _need(frame["date"].between(start, end).all(), "Parquet predicate returned out-of-scope numeric rows")
                _need(frame["date"].isin(days).all(), "source date absent from frozen calendar")
                frame = frame.set_index("date").sort_index()
                numeric = frame.drop(columns=["code"]).apply(pd.to_numeric, errors="raise").astype(float)
                numeric = numeric.replace([np.inf, -np.inf], np.nan)
                ratio = numeric["qfq_ratio"].where(numeric["qfq_ratio"] > 0)
                valid_anchor = ratio.notna() & numeric["close"].gt(0)
                first = valid_anchor[valid_anchor].index[0] if valid_anchor.any() else None
                anchor = float(ratio.loc[first]) if first is not None else None
                anchors[code] = {"date": first.date().isoformat() if first is not None else None, "source_qfq_ratio": anchor}
                for name in BASE_FIELDS:
                    values = numeric[name].where(numeric[name] > 0)
                    if name in PRICE_FIELDS:
                        values = values.where(ratio.notna()) / anchor if anchor is not None else values * np.nan
                    fields[name].loc[numeric.index, code] = values
                for name in optional_fields:
                    if name in numeric:
                        fields[name].loc[numeric.index, code] = numeric[name]
                raw_close = numeric["raw_close"] if "raw_close" in numeric else numeric["close"] / ratio
                raw_open = numeric["raw_open"] if "raw_open" in numeric else numeric["open"] / ratio
                fields["raw_close"].loc[numeric.index, code] = raw_close.where(raw_close > 0)
                fields["raw_open"].loc[numeric.index, code] = raw_open.where(raw_open > 0)
                if "raw_prev_close" in numeric:
                    fields["raw_prev_close"].loc[numeric.index, code] = numeric["raw_prev_close"].where(numeric["raw_prev_close"] > 0)
                fields["qfq_ratio"].loc[numeric.index, code] = ratio
                fields["adjustment_factor"].loc[numeric.index, code] = ratio / anchor if anchor is not None else np.nan
                fields["bar_observed"].loc[numeric.index, code] = 1.
                open_known = raw_open.gt(0) & np.isfinite(raw_open) & numeric["open"].gt(0) & ratio.notna()
                fields["open_observed"].loc[numeric.index, code] = open_known.astype(float)
                quality[code] = {"source_missing": False, "returned_rows": len(frame),
                    "raw_open_origin": "source_column" if "raw_open" in numeric else "derived_source_open_div_qfq_ratio",
                    "raw_close_origin": "source_column" if "raw_close" in numeric else "derived_source_close_div_qfq_ratio"}
                _source_unchanged(proof)
            valid = pd.DataFrame(True, index=days, columns=columns)
            for name in BASE_FIELDS:
                valid &= fields[name].notna() & fields[name].gt(0)
            valid &= fields["high"].ge(fields["open"]) & fields["high"].ge(fields["close"])
            valid &= fields["low"].le(fields["open"]) & fields["low"].le(fields["close"])
            eligible = member & valid
            provenance = {"version": VERSION, "cache_key": key, "request": identity,
                "source_class": exposure, "globally_unseen": False,
                "price_basis": {"signal_ohlc": "fixed first-valid requested-session raw-price anchor",
                    "raw_execution": "source raw columns when available; otherwise same-row adjusted price / qfq_ratio",
                    "qfq_ratio": "source adjusted price divided by raw price",
                    "adjustment_factor": "signal adjusted price divided by raw price = qfq_ratio / starting_anchor_ratio",
                    "anchors": anchors, "historical_adjustment_publication_verified": False},
                "causal_fields": list(BASE_FIELDS) + list(optional_fields), "execution_only_fields": list(EXECUTION_FIELDS),
                "timing": {"decision": "after completed daily bar", "execution": "next actual session open",
                    "historical_available_at_verified": False, "membership": "signal-date inclusive intervals; announcement provenance unverified"},
                "scope": {"sessions": len(days), "symbols": len(codes), "stock_days": len(days) * len(codes),
                    "historical_member_stock_days": int(member.to_numpy().sum()), "eligible_stock_days": int(eligible.to_numpy().sum())},
                "missing": {"policy": "no forward-fill/backfill; invalid or absent values remain NaN; no zero-return imputation",
                    "field_missing_cells": {name: int(value.isna().to_numpy().sum()) for name, value in fields.items()},
                    "source_quality": quality},
                "range_isolation": "Parquet predicate before Python materialization; no out-of-range rows returned/cached; storage pages may overlap held-out dates",
                "execution_certified": False, "model_calls": 0, "account_executions": 0}
            panel = MarketPanel(fields, eligible, provenance)
            if folder is not None:
                _cache_write(folder, key, panel)
        for proof in proofs:
            _source_unchanged(proof)
        _need(_sha(calendar_path) == identity["calendar_sha256"], "calendar changed during load")
        if membership_path:
            _need(_sha(membership_path) == identity["membership_sha256"], "membership changed during load")
        if identity["source_manifest_sha256"] is not None:
            _need(_sha(source_manifest) == identity["source_manifest_sha256"], "source manifest changed during load")
    panel.load_metrics = {**meter.metrics, "cache_hit": cache_hit, "cache_key": key,
        "panel_bytes": panel.nbytes, "source_file_bytes": sum(p.get("bytes", 0) for p in proofs),
        "source_files": len(proofs), "numeric_range": [start.date().isoformat(), end.date().isoformat()],
        "market_values_reloaded_on_cache_hit": False if cache_hit else None}
    return panel
