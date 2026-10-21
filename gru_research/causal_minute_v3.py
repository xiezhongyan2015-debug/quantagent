"""Independent minute -> local-quality hours -> H24 and four labels.

Default CLI is metadata only. Numerical execution requires pinned, exclusive
input/output contracts. No original generator, R2 adapter, model or scanner is
imported. Historical record clocks are explicit reconstruction conventions,
not observed supplier arrival times. V3 parses each text clock independently;
future records never supply a parse format or timezone policy. An immutable ledger must accompany any
changed/reordered/duplicated record; event timestamps never update that ledger.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import resource
import re
import stat
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("股票代码", "datetime", "datetime_nano", "open", "high", "low", "close",
          "volume", "qfq_ratio", "qfq_open", "qfq_high", "qfq_low", "qfq_close")
H24 = ("h_return_1", "h_open_gap", "h_open_close_return", "h_amplitude",
       "h_upper_shadow", "h_lower_shadow", "h_close_location", "h_volume_same_slot_20d",
       "h_volume_prev", "h_ma_deviation_4", "h_ma_deviation_20", "h_ma_deviation_80",
       "h_return_4", "h_return_20", "h_realized_volatility", "h_minute_return_skew",
       "h_minute_return_max", "h_minute_return_min", "h_up_semivariance_share",
       "h_return_logvolume_corr", "h_last15_volume_share", "h_zero_volume_share",
       "h_path_efficiency", "h_upminute_volume_share")
MINUTE = 60 * 10**9
REGULAR = tuple(range(570, 690)) + tuple(range(780, 900))
SIGNALS = (630, 690, 840, 900)
EXECUTIONS = (571, 631, 781, 841)
GRID_PROFILE = "IMMUTABLE_PHYSICAL_ORDINAL_FIXED_GRID_NOT_FEED_ARRIVAL"
NUMERIC_PROFILE = "IMMUTABLE_ORIGINAL_NUMERIC_CLOCK_WITH_PREFIX_SEQUENCE_NOT_FEED_ARRIVAL"
FUTURE_CASES = ("future_valid_price_volume", "future_conflicting_duplicate",
                "future_dual_clock_mismatch", "future_backdated_clock",
                "future_factor_change", "future_wrong_symbol",
                "future_reordered_slash_text_clock", "future_reordered_timezone_text_clock")
PAST_CASES = ("past_conflicting_duplicate", "past_dual_clock_mismatch",
              "past_future_claimed_clock", "past_factor_change", "past_wrong_symbol",
              "past_negative_volume", "past_missing_record", "past_slash_text_clock")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def runtime():
    global np, pd, pa, pq
    if "np" not in globals():
        # Existing isolated dependencies only; no installation hooks.
        sys.path.insert(0, str(ROOT / "gru_research/.deps"))
        import numpy as np
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq


def utc():
    return datetime.now(timezone.utc).isoformat()


def midnight_ns(day):
    require(datetime.fromisoformat(day).date().isoformat() == day, "ISO trading date required")
    return int(datetime.fromisoformat(day + "T00:00:00+08:00").timestamp()) * 10**9


def file_stat(path):
    path = Path(path)
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode), "regular file only: " + str(path))
    return {"path": str(path), "bytes": s.st_size, "mtime_ns": s.st_mtime_ns,
            "device": s.st_dev, "inode": s.st_ino}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def identity(path, cap=4 * 1024**2):
    path = Path(path)
    before = file_stat(path)
    require(before["bytes"] <= cap, "small identity exceeds cap")
    body = path.read_bytes()
    require(len(body) == before["bytes"] and file_stat(path) == before, "identity changed during read")
    return {"path": str(path), "bytes": len(body), "sha256": digest(body)}


def write_json(path, value):
    body = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    require(len(body) <= 4 * 1024**2, "JSON output exceeds cap")
    with Path(path).open("xb") as handle:
        handle.write(body)
    return {"path": str(path), "bytes": len(body), "sha256": digest(body)}


def safe_integer(value):
    try:
        n = int(value)
        return n if n == value and 0 < n < 2**63 - MINUTE else None
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(frozen=True)
class RecordClockLedger:
    day: str
    symbol: str
    original_payload_sha256: str
    profile: str
    original_start_ns: tuple
    historical_completion_ns: tuple
    original_clock_in_regular_grid: tuple
    feed_arrival: str = "UNKNOWN"

    def validate(self):
        require(self.profile in (GRID_PROFILE, NUMERIC_PROFILE), "unsupported/unbound clock profile")
        require(len(self.original_payload_sha256) == 64 and
                all(c in "0123456789abcdef" for c in self.original_payload_sha256), "source SHA required")
        require(len(self.symbol) == 9 and self.symbol[6:] in (".SH", ".SZ") and self.symbol[:6].isdigit(), "symbol required")
        require(len(self.original_start_ns) == len(self.historical_completion_ns) ==
                len(self.original_clock_in_regular_grid), "ledger lengths differ")
        require(all(type(n) is int for n in self.historical_completion_ns), "ledger completion must be immutable integers")
        require(all(a < b for a, b in zip(self.historical_completion_ns, self.historical_completion_ns[1:])),
                "source sequence completion must strictly increase by original physical ordinal")
        require(self.feed_arrival == "UNKNOWN", "historical ledger cannot certify supplier arrival")
        midnight_ns(self.day)
        return self


def historical_grid_ledger(day, symbol, payload_sha, row_count):
    """Create from file identity and physical ordinals, without quality gate.

    Missing records may shift subsequent slots in this strict source profile;
    those are reported as profile mismatches, not asserted vendor clock errors.
    Extra physical ordinals have no regular slot and a completion lower-bound
    after the last regular slot under this profile. This is not observed arrival.
    """
    require(type(row_count) is int and 0 <= row_count <= 1024, "row budget exceeded")
    m = midnight_ns(day)
    starts = tuple(m + REGULAR[i] * MINUTE if i < 240 else None for i in range(row_count))
    known = tuple(m + (REGULAR[i] + 1) * MINUTE if i < 240 else m + 900 * MINUTE + i - 239
                  for i in range(row_count))
    return RecordClockLedger(day, symbol, payload_sha, GRID_PROFILE, starts, known,
                             tuple(i < 240 for i in range(row_count))).validate()


def original_numeric_clock_ledger(day, symbol, payload_sha, original_numeric_ns):
    """Seal original numeric clocks independently of later row-field changes.

    No full-file row count, text-clock, code, price or factor test is required.
    The strictly increasing prefix envelope makes a late original backdated or
    invalid clock unavailable before its earlier physical record. Missing true
    minute gaps remain missing. Clock/profile anomalies remain local evidence;
    this source-order convention is not an observed transport-arrival clock.
    """
    require(len(original_numeric_ns) <= 1024, "row budget exceeded")
    m = midnight_ns(day)
    regular = {m + offset * MINUTE for offset in REGULAR}
    previous = m + 571 * MINUTE - 1
    starts, known, valid = [], [], []
    for value in original_numeric_ns:
        n = safe_integer(value)
        in_grid = n in regular
        # Invalid source clock has no independently resolved market slot.
        # Its order bound uses only the preceding record, never future records.
        completion = max(n + MINUTE if in_grid else previous + 1, previous + 1)
        starts.append(n)
        known.append(int(completion))
        valid.append(bool(in_grid))
        previous = completion
    return RecordClockLedger(day, symbol, payload_sha, NUMERIC_PROFILE, tuple(starts),
                             tuple(known), tuple(valid)).validate()


def ledger_identity(ledger):
    value = {"day": ledger.day, "symbol": ledger.symbol,
             "source_payload_sha256": ledger.original_payload_sha256, "profile": ledger.profile,
             "original_row_ordinals": list(range(len(ledger.original_start_ns))),
             "original_start_ns": list(ledger.original_start_ns),
             "historical_completion_ns": list(ledger.historical_completion_ns),
             "original_clock_in_regular_grid": list(ledger.original_clock_in_regular_grid),
             "feed_arrival": "UNKNOWN"}
    return value, digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _columns(table):
    runtime()
    if isinstance(table, dict):
        require(set(FIELDS) <= set(table), "required source columns absent")
        a = {k: np.asarray(table[k]) for k in FIELDS}
    else:
        require(set(FIELDS) <= set(table.column_names), "required source columns absent")
        a = {k: table[k].to_numpy(zero_copy_only=False) for k in FIELDS}
    require(len({len(v) for v in a.values()}) == 1, "source column lengths differ")
    require(len(a["datetime_nano"]) <= 2048, "record/evidence budget exceeded")
    return a


def _ratio(a, b):
    return float(a / b) if np.isfinite(b) and b != 0 else np.nan



# No Pandas/NumPy whole-column inference: one record cannot choose the format,
# dtype, timezone or rejection policy applied to another record. The original
# supplier's naive Shanghai convention is the only supported text-clock scope.
NAIVE_TEXT_CLOCK = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[ T]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,9}))?")
TEXT_CLOCK_POLICY = "PER_RECORD_NAIVE_ISO_SHANGHAI_NO_FORMAT_OR_TIMEZONE_INFERENCE"


def parse_naive_text_clock_ns(value):
    """Exact local-wall ns or None, independently for each source record.

    Accept naive datetime/Timestamp, numpy datetime64 and canonical ISO strings
    with space/T and zero to nine fractional digits. Calendar validity uses the
    standard library; nanosecond fractions use integer arithmetic without float
    timestamps. Reject timezone-aware values, slash dates, NaT, offsets/Z and
    unsupported source types for that row only. No format/type is learned from
    another row, including a reordered future first row.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return None
    elif not isinstance(value, (str, np.datetime64)):
        return None
    match = NAIVE_TEXT_CLOCK.fullmatch(str(value))
    if match is None:
        return None
    try:
        year, month, day, hour, minute, second = map(int, match.groups()[:6])
        local = datetime(year, month, day, hour, minute, second)
        delta = local - datetime(1970, 1, 1)
        fraction = match.group(7) or ""
        ns = (delta.days * 86400 + delta.seconds) * 10**9 + int(fraction.ljust(9, "0") or "0")
        # The later fixed Shanghai subtraction must not overflow int64 either.
        return ns if -(2**63) + 8 * 3600 * 10**9 <= ns < 2**63 else None
    except (ValueError, TypeError, OverflowError):
        return None


def aggregate_table(table, ledger, record_ordinals=None, asof_ns=None):
    """Four local hours/windows with immutable record availability.

    For changed, reordered or duplicate evidence, record_ordinals must refer to
    the original ledger; callers must not derive it again from changed clocks.
    Quality of each group is evaluated only at that group's own completion.
    Labels/execution data do not participate in any hour feature or eligibility.
    """
    runtime()
    require(isinstance(ledger, RecordClockLedger), "explicit clock ledger required")
    ledger.validate()
    a = _columns(table)
    n = len(a["datetime_nano"])
    if record_ordinals is None:
        require(n == len(ledger.original_start_ns), "changed evidence requires explicit immutable ordinals")
        record_ordinals = np.arange(n, dtype=np.int64)
    ordinals = np.asarray(record_ordinals)
    require(ordinals.dtype.kind in "iu" and ordinals.shape == (n,) and
            not (ordinals < 0).any() and not (ordinals >= len(ledger.original_start_ns)).any(), "ordinal identity differs")
    starts = np.asarray([ledger.original_start_ns[int(i)] or 0 for i in ordinals], dtype=np.int64)
    known = np.asarray(ledger.historical_completion_ns, dtype=np.int64)[ordinals]
    ledger_clock_ok = np.asarray(ledger.original_clock_in_regular_grid, dtype=bool)[ordinals]
    actual_ns = np.asarray([safe_integer(v) or 0 for v in a["datetime_nano"]], dtype=np.int64)
    parsed_text = [parse_naive_text_clock_ns(value) for value in a["datetime"]]
    texts = np.asarray([value if value is not None else 0 for value in parsed_text], dtype=np.int64)
    text_ok = np.asarray([value is not None for value in parsed_text], dtype=bool)
    dual_ok = text_ok & (texts - 8 * 3600 * 10**9 == actual_ns)
    profile_ok = ledger_clock_ok & (actual_ns == starts)
    symbol_ok = np.asarray(a["股票代码"]).astype(str) == ledger.symbol[:6]
    number = lambda k: pd.to_numeric(pd.Series(a[k]), errors="coerce").to_numpy(dtype=float)
    raw = np.column_stack([number(k) for k in ("open", "high", "low", "close")])
    adj = np.column_stack([number(k) for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close")])
    vol, factors = number("volume"), number("qfq_ratio")
    numeric_ok = (np.isfinite(raw).all(1) & np.isfinite(adj).all(1) & (raw > 0).all(1) &
                  (adj > 0).all(1) & np.isfinite(vol) & (vol >= 0) & np.isfinite(factors) & (factors > 0) &
                  (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8) &
                  (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8))
    relation_ok = np.isclose(adj, raw * factors[:, None], rtol=1e-7, atol=1e-6).all(1)
    valid = dual_ok & profile_ok & symbol_ok & numeric_ok & relation_ok
    m = midnight_ns(ledger.day)
    horizon = m + 900 * MINUTE if asof_ns is None else int(asof_ns)

    def local_group(offsets, boundary):
        cutoff = min(int(boundary), horizon)
        expected = m + np.asarray(offsets, dtype=np.int64) * MINUTE
        prefix = np.flatnonzero(known <= cutoff)
        # Only already-known valid original records can establish a factor anchor.
        anchor_rows = prefix[valid[prefix]]
        anchor_order = sorted(map(int, anchor_rows), key=lambda i: (int(ordinals[i]), i))
        anchor = float(factors[anchor_order[0]]) if anchor_order else np.nan
        factor_change = bool(anchor_order and not np.allclose(factors[anchor_rows], anchor, rtol=1e-7, atol=1e-10))
        pos = prefix[np.isin(starts[prefix], expected)]
        kept, conflicts, duplicate_count = [], 0, 0
        for start in expected:
            group = pos[starts[pos] == start]
            if not len(group):
                continue
            first = int(group[0])
            kept.append(first)
            duplicate_count += len(group) - 1
            for j in group[1:]:
                if any(a[k][int(j)] != a[k][first] for k in FIELDS):
                    conflicts += 1
        kept = np.asarray(kept, dtype=np.int64)
        complete = bool(boundary <= horizon and len(kept) == len(expected) and
                        np.array_equal(starts[kept], expected) and valid[pos].all() and
                        not conflicts and not factor_change and vol[kept].sum() > 0)
        flags = {"quality_asof_ns": cutoff, "group_completion_ns": int(boundary),
                 "observed_group_rows": int(len(pos)), "observed_unique_minutes": int(len(kept)),
                 "expected_minutes": int(len(expected)), "missing_expected_minutes": int(len(expected) - len(kept)),
                 "duplicate_rows_known": int(duplicate_count), "conflicting_duplicate_rows_known": int(conflicts),
                 "invalid_group_rows_known": int((~valid[pos]).sum()),
                 "dual_clock_mismatch_rows_known": int((~dual_ok[pos]).sum()),
                 "text_clock_format_or_timezone_invalid_rows_known": int((~text_ok[pos]).sum()),
                 "text_clock_policy": TEXT_CLOCK_POLICY,
                 "ordinal_clock_profile_mismatch_rows_known": int((~profile_ok[pos]).sum()),
                 "symbol_mismatch_rows_known": int((~symbol_ok[pos]).sum()),
                 "qfq_relation_mismatch_rows_known": int((~relation_ok[pos]).sum()),
                 "known_prefix_factor_change": factor_change,
                 "factor_anchor_original_ordinal": int(ordinals[anchor_order[0]]) if anchor_order else None,
                 "quality_basis": ledger.profile, "feed_arrival": "UNKNOWN"}
        return kept, complete, flags

    rows = []
    for slot in range(4):
        signal = int(m + SIGNALS[slot] * MINUTE)
        pos, complete, quality = local_group(REGULAR[slot * 60:(slot + 1) * 60], signal)
        row = {"date": ledger.day, "symbol": ledger.symbol, "slot": slot, "signal_time": signal,
               "hour_complete": complete, "minute_count": quality["observed_unique_minutes"],
               "hour_quality": quality}
        row.update({k: np.nan for k in H24[14:] + ("open", "high", "low", "close", "volume",
                    "raw_open", "raw_high", "raw_low", "raw_close", "adjustment_factor")})
        if complete:
            o, h, l, c = adj[pos[0], 0], adj[pos, 1].max(), adj[pos, 2].min(), adj[pos[-1], 3]
            v = vol[pos]
            returns = np.log(adj[pos, 3] / np.r_[o, adj[pos[:-1], 3]])
            sr, std = float(np.square(returns).sum()), returns.std()
            logv = np.log1p(v)
            path = float(np.abs(np.diff(np.r_[o, adj[pos, 3]])).sum())
            corr = float(np.corrcoef(returns, logv)[0, 1]) if std > 1e-12 and logv.std() > 1e-12 else np.nan
            stats = [np.sqrt(sr), float(np.mean(((returns - returns.mean()) / std)**3)) if std > 1e-12 else np.nan,
                     returns.max(), returns.min(), _ratio(np.square(returns[returns > 0]).sum(), sr), corr,
                     _ratio(v[-15:].sum(), v.sum()), float(np.mean(v == 0)), _ratio(abs(c - o), path),
                     _ratio(v[returns > 0].sum(), v.sum())]
            row.update(dict(zip(H24[14:], stats)))
            row.update({"open": o, "high": h, "low": l, "close": c, "volume": v.sum(),
                        "raw_open": raw[pos[0], 0], "raw_high": raw[pos, 1].max(),
                        "raw_low": raw[pos, 2].min(), "raw_close": raw[pos[-1], 3],
                        "adjustment_factor": factors[pos[-1]]})
        start = int(m + EXECUTIONS[slot] * MINUTE)
        epos, ecomplete, equality = local_group(range(EXECUTIONS[slot], EXECUTIONS[slot] + 5), start + 5 * MINUTE)
        row.update({"window_start_time": start, "window_end_time": start + 5 * MINUTE,
                    "window_complete": ecomplete, "window_quality": equality,
                    "window_price": float(adj[epos, 0].mean()) if ecomplete else np.nan,
                    "window_raw_price": float(raw[epos, 0].mean()) if ecomplete else np.nan,
                    "window_volume": float(vol[epos].sum()) if ecomplete else np.nan,
                    "window_tradable": ecomplete,
                    "window_all_flat": bool(ecomplete and (raw[epos, 1] == raw[epos, 2]).all())})
        rows.append(row)
    audit = {"date": ledger.day, "symbol": ledger.symbol, "source_payload_sha256": ledger.original_payload_sha256,
             "source_rows": n, "clock_profile": ledger.profile, "feed_arrival": "UNKNOWN",
             "immutable_ledger_record_count": len(ledger.original_start_ns),
             "original_clock_unresolved_rows": int(sum(not x for x in ledger.original_clock_in_regular_grid)),
             "sequence_envelope_deferred_original_records": int(sum(start is not None and known > start + MINUTE
                 for start, known in zip(ledger.original_start_ns, ledger.historical_completion_ns))),
             "whole_file_row_quality_gate_applied": False, "text_clock_policy": TEXT_CLOCK_POLICY,
             "grid_profile_missing_row_shift_is_not_asserted_vendor_clock_error": True,
             "error": "", "hour_quality": [r["hour_quality"] for r in rows],
             "execution_quality": [r["window_quality"] for r in rows]}
    return rows, audit


aggregate_stock_day = aggregate_table


def build_symbol_features(frame, calendar_dates, next_execution_start_ns):
    """Preserve the original24/FP32 algebra, grid,80mask and four horizons."""
    runtime()
    dates = list(calendar_dates)
    require(dates and dates == sorted(set(dates)), "positive exact chronological trading calendar required")
    f = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    require(len(f) and f.symbol.nunique() == 1, "exactly one symbol required")
    symbol = str(f.symbol.iloc[0])
    require(not f.signal_time.duplicated().any(), "duplicate hour records")
    times = np.asarray([midnight_ns(d) + x * MINUTE for d in dates for x in SIGNALS], dtype=np.int64)
    starts = np.asarray([midnight_ns(d) + x * MINUTE for d in dates for x in EXECUTIONS], dtype=np.int64)
    require(type(next_execution_start_ns) is int and next_execution_start_ns > int(times[-1]), "independently bound next opening required")
    require(int((next_execution_start_ns - midnight_ns(datetime.fromtimestamp(next_execution_start_ns / 1e9, timezone.utc).astimezone(timezone.utc).date().isoformat())) // MINUTE) == 571,
            "next execution must be an independently known09:31 Shanghai start")
    require(f.signal_time.isin(times).all(), "source hours outside explicit calendar")
    f = f.set_index("signal_time").reindex(times)
    f["date"], f["slot"], f["symbol"] = np.repeat(dates, 4), np.tile(np.arange(4), len(dates)), symbol
    f["hour_complete"] = f.hour_complete.eq(True)
    f["window_complete"] = f.window_complete.eq(True) & f.window_tradable.eq(True)
    f["window_tradable"], f["window_all_flat"] = f.window_tradable.eq(True), f.window_all_flat.eq(True)
    f["window_price"] = f.window_price.where(f.window_complete)
    f["window_raw_price"] = f.window_raw_price.where(f.window_complete)
    c, o, h, l, v = [f[k].where(f.hour_complete) for k in ("close", "open", "high", "low", "volume")]
    previous = c.shift(1)
    f[H24[0]], f[H24[1]], f[H24[2]], f[H24[3]] = np.log(c / previous), np.log(o / previous), np.log(c / o), (h - l) / o
    spread = (h - l).replace(0, np.nan)
    f[H24[4]] = (h - pd.concat([o, c], axis=1).max(axis=1)) / spread
    f[H24[5]] = (pd.concat([o, c], axis=1).min(axis=1) - l) / spread
    f[H24[6]] = (c - l) / spread
    prior_same = v.groupby(f["slot"]).transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    f[H24[7]], f[H24[8]] = v / prior_same.replace(0, np.nan), v / v.shift(1).replace(0, np.nan)
    for i, length in enumerate((4, 20, 80), 9):
        f[H24[i]] = c / c.rolling(length, min_periods=length).mean() - 1
    for i, length in enumerate((4, 20), 12):
        f[H24[i]] = np.log(c / c.shift(length))
    f["window80_complete"] = f.hour_complete.astype(np.int16).rolling(80, min_periods=80).sum().eq(80)
    earliest = np.r_[starts[1:], np.int64(next_execution_start_ns)]
    require((earliest > times).all(), "execution clock is not strictly later than signal")
    f["earliest_execution_time"] = earliest
    f["execution_window_end_time"] = earliest + 5 * MINUTE
    f["execution_price"], f["execution_raw_price"] = f.window_price.shift(-1), f.window_raw_price.shift(-1)
    f["execution_window_complete"] = f.window_complete.shift(-1).eq(True)
    f["execution_tradable"] = f.window_tradable.shift(-1).eq(True)
    f["execution_all_flat"] = f.window_all_flat.shift(-1).eq(True)
    entry = f.execution_price
    for delta in range(1, 5):
        ends = np.full(len(f), -1, dtype=np.int64)
        if len(f) > delta + 1:
            ends[:-(delta + 1)] = starts[delta + 1:] + 5 * MINUTE
        f[f"label_end_time{delta}"] = ends
        f[f"label_end_price{delta}"] = f.window_price.shift(-(delta + 1))
        f[f"y{delta}"] = np.log(f[f"label_end_price{delta}"] / entry)
    for name in H24 + ("y1", "y2", "y3", "y4"):
        f[name] = f[name].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    f.index.name = "signal_time"
    return f.reset_index()


finalize_symbol = build_symbol_features


def compare_features(left, right):
    return {"NaN_masks_equal": bool(np.array_equal(np.isnan(left), np.isnan(right))),
            "FP32_complete_body_equal": left.tobytes() == right.tobytes(),
            "left_body_sha256": digest(left.tobytes()), "right_body_sha256": digest(right.tobytes())}


def save_array(path, value, role):
    with Path(path).open("xb") as handle:
        np.save(handle, value, allow_pickle=False)
    return {"role": role, **identity(path), "dtype": value.dtype.str,
            "shape": list(value.shape), "body_sha256": digest(value.tobytes(order="C"))}


def pure_contract():
    return {"status": "PREPARED_PRODUCTION_OPERATOR_NO_NUMERIC_EXECUTION", "schema_version": 3,
            "worker_API": ["historical_grid_ledger(day,symbol,payload_sha,row_count)",
                           "original_numeric_clock_ledger(day,symbol,payload_sha,original_numeric_ns)",
                           "aggregate_table(table,ledger,record_ordinals=None,asof_ns=None)",
                           "build_symbol_features(frame,calendar_dates,next_execution_start_ns)"],
            "profiles": [GRID_PROFILE, NUMERIC_PROFILE], "feature_names": list(H24),
            "whole_file_row_quality_gate_applied": False, "feed_arrival": "UNKNOWN",
            "text_clock_policy": TEXT_CLOCK_POLICY,
            "timezone_aware_or_unknown_text_clock": "Per-record rejection only; no cross-row parse inference",
            "CLI_execute_requires": ["--contract", "--contract-sha256", "--source-manifest", "--source-manifest-sha256", "--source-sha", "--output"],
            "default_numeric_or_source_body_reads": 0, "full_PLAN_complete": False}


def inject(columns, ordinals, ledger, name, future):
    changed = {k: v.copy() for k, v in columns.items()}
    record_ids = ordinals.copy()
    cutoff = midnight_ns(ledger.day) + 630 * MINUTE
    known = np.asarray(ledger.historical_completion_ns, np.int64)[record_ids]
    mask = known > cutoff if future else known <= cutoff
    target = int(np.flatnonzero(mask)[-1]) if future else 2
    require(mask[target], "registered intervention is outside its clock scope")
    if name.endswith("valid_price_volume"):
        for k in ("open", "high", "low", "close", "qfq_open", "qfq_high", "qfq_low", "qfq_close"):
            changed[k][mask] *= 1.25
        changed["volume"][mask] *= 2
    elif name.endswith("conflicting_duplicate"):
        changed = {k: np.concatenate([v, v[target:target + 1]]) for k, v in changed.items()}
        changed["volume"][-1] += 1
        record_ids = np.r_[record_ids, record_ids[target]]
    elif name.endswith("dual_clock_mismatch"):
        # Preserve all other source text cells exactly; converting the entire
        # string column to datetime64 would itself rewrite untouched past text.
        changed["datetime"] = np.asarray([str(value) for value in changed["datetime"]], dtype=object)
        changed["datetime"][target] = str(np.datetime64(changed["datetime"][target], "ns") + np.timedelta64(1, "m"))
    elif name.endswith("backdated_clock") or name.endswith("future_claimed_clock"):
        stamp = "09:30:00" if future else "14:59:00"
        changed["datetime"] = np.asarray([str(value) for value in changed["datetime"]], dtype=object)
        changed["datetime"][target] = ledger.day + "T" + stamp
        changed["datetime_nano"][target] = midnight_ns(ledger.day) + (570 if future else 899) * MINUTE
    elif name.endswith("factor_change"):
        changed["qfq_ratio"][target] *= 1.005
        for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
            changed[k][target] *= 1.005
    elif name.endswith("wrong_symbol"):
        changed["股票代码"] = changed["股票代码"].astype(str)
        changed["股票代码"][target] = "000010"
    elif name.endswith("slash_text_clock") or name.endswith("timezone_text_clock"):
        # This is a changed future record plus a physical reordering, not a new
        # ledger or fabricated arrival time. Past records retain their IDs and
        # exact field contents, irrespective of their new physical positions.
        changed["datetime"] = changed["datetime"].astype(str).astype(object)
        if name.endswith("slash_text_clock"):
            wall_date = datetime.fromisoformat(ledger.day).strftime("%d/%m/%Y")
            changed["datetime"][target] = wall_date + (" 14:59:00" if future else " 09:32:00")
        else:
            require(future, "only future timezone case is registered")
            changed["datetime"][target] = ledger.day + "T14:59:00+08:00"
        require(str(changed["datetime"][target]) != str(columns["datetime"][target]), "text mutation is empty")
        if future:
            require(int(ordinals[target]) == 239, "registered reordered future record must be original239")
            order = np.r_[target, np.delete(np.arange(len(record_ids)), target)]
            changed = {k: v[order] for k, v in changed.items()}
            record_ids = record_ids[order]
            require(int(record_ids[0]) == 239, "future first-record permutation did not occur")
    elif name.endswith("negative_volume"):
        changed["volume"][target] = -1
    elif name.endswith("missing_record"):
        changed = {k: np.delete(v, target) for k, v in changed.items()}
        record_ids = np.delete(record_ids, target)
    else:
        raise ValueError("unregistered intervention")
    if future:
        # Compare by immutable source identity, not by mutable physical input
        # position. Reordering is permitted but no past row may change or vanish.
        for original_position in np.flatnonzero(~mask):
            matches = np.flatnonzero(record_ids == ordinals[original_position])
            require(len(matches) == 1 and all(
                str(changed[k][int(matches[0])]) == str(columns[k][int(original_position)]) for k in FIELDS),
                "future intervention changed/removed/duplicated original past record")
    return changed, record_ids, {"case": name, "intervention_original_ordinal": int(ordinals[target]),
                                "immutable_ledger_SHA256": ledger_identity(ledger)[1],
                                "input_first_record_original_ordinal": int(record_ids[0]),
                                "input_original_ordinal_order": list(map(int, record_ids)),
                                "past_records_unchanged_by_immutable_ID": True if future else None,
                                "text_clock_policy": TEXT_CLOCK_POLICY,
                                "mutated_event_timestamps_do_not_update_ledger": True,
                                "counterfactual_on_real_source_not_provider_fact": True}


def execute_once(args):
    started = time.monotonic()
    source = identity(Path(__file__))
    require(source["sha256"] == args.source_sha, "explicit source SHA differs")
    cp, mp = Path(args.contract), Path(args.source_manifest)
    require(identity(cp)["sha256"] == args.contract_sha256 and identity(mp)["sha256"] == args.source_manifest_sha256, "contract/manifest SHA differs")
    contract, manifest = json.loads(cp.read_bytes()), json.loads(mp.read_bytes())
    output = Path(args.output)
    require(output == Path(contract["output_directory"]) and output.is_absolute(), "exclusive output path differs")
    require(contract["source_manifest_sha256"] == args.source_manifest_sha256 and contract["source_sha256"] == args.source_sha, "closed configuration differs")
    require(manifest["symbol"] == "000009.SZ" and len(manifest["payloads"]) == 41, "first actual validation is fixed exact41 scope")
    require(len({x["path"] for x in manifest["payloads"]}) == 41 and
            all(x["symbol"] == manifest["symbol"] for x in manifest["payloads"]), "duplicate/mixed source roster")
    require(digest(json.dumps(manifest["payloads"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) ==
            contract["canonical_payload_roster_sha256"], "bound payload roster differs")
    output.mkdir(parents=True, exist_ok=False)
    marker = write_json(output / "EXECUTION_STARTED_v3.json", {"started_utc": utc(), "source": source,
                        "contract_sha256": args.contract_sha256, "manifest_sha256": args.source_manifest_sha256,
                        "automatic_retry": False})
    result = {"schema_version": 3, "status": "RUNNING_CAUSAL_MINUTE_V3", "started_utc": utc(), "source": source,
              "marker": marker, "contract_sha256": args.contract_sha256, "manifest_sha256": args.source_manifest_sha256,
              "automatic_retry": False, "full_PLAN_complete": False, "economic_comparison_eligible": False,
              "all_original_models_causal_pass": False, "feed_arrival": "UNKNOWN", "new_forward_or_training": False}
    failure, artifacts, before, after, ledgers, tables = None, [], {}, {}, {}, {}
    try:
        runtime()
        guards = contract["small_guards"]
        small_before = {x["path"]: identity(x["path"]) for x in guards}
        require(small_before == {x["path"]: x for x in guards}, "closed small identities differ")
        require([x["date"] for x in manifest["payloads"]] == contract["calendar_dates"], "fixed calendar/source roster differ")
        source_bytes, decoded_rows, rows, audits = 0, 0, [], []
        for item in manifest["payloads"]:
            require(time.monotonic() - started < contract["budget"]["elapsed_seconds"], "elapsed operation budget exceeded")
            path = Path(item["path"])
            before[str(path)] = file_stat(path)
            require(before[str(path)] == item["STAT"] and item["bytes"] <= 2 * 1024**2, "source STAT or size differs")
            body = path.read_bytes()  # Exactly one read for this original payload.
            source_bytes += len(body)
            require(len(body) == item["bytes"] and digest(body) == item["sha256"] and source_bytes <= contract["budget"]["payload_bytes"], "source bytes/SHA/budget differ")
            table = pq.read_table(io.BytesIO(body), columns=list(FIELDS), use_threads=False)
            a = _columns(table)
            # Clock ledger is sealed BEFORE price/code/text/factor validation.
            # No whole-file240/dual-clock/code or numeric validity gate here.
            ledger = original_numeric_clock_ledger(item["date"], item["symbol"], item["sha256"], a["datetime_nano"])
            tables[item["date"]] = {k: v.copy() for k, v in a.items()}
            ledgers[item["date"]] = ledger
            decoded_rows += len(table)
            rr, audit = aggregate_table(table, ledger)
            rows.extend(rr); audits.append(audit)
            del body, table
        require(source_bytes == contract["budget"]["payload_bytes"] and decoded_rows == 9840, "fixed original source total differs")
        dates = contract["calendar_dates"]
        f = build_symbol_features(rows, dates, contract["next_execution_start_ns"])
        target = f.iloc[81:161]
        values = np.ascontiguousarray(target[list(H24)].to_numpy(), dtype="<f4")
        complete = target.hour_complete.to_numpy(bool)
        eligibility = target.window80_complete.to_numpy(bool)
        require(values.shape == (80, 24) and complete.all() and eligibility.all(), "fixed source baseline is not complete; retain failure, do not adapt ordering/profile")
        reference = contract["baseline_H24_artifact"]
        rb = Path(reference["path"]).read_bytes()
        require(len(rb) == reference["bytes"] and digest(rb) == reference["sha256"], "baseline artifact changed")
        original = np.load(io.BytesIO(rb), allow_pickle=False)
        require(original.dtype.str == "<f4" and original.shape == (80, 24), "baseline registration differs")
        comparison = compare_features(values, original)
        result["baseline_comparison"] = comparison
        require(comparison["NaN_masks_equal"] and comparison["FP32_complete_body_equal"], "baseline FP32/NaN mismatch; no tolerance adjustment")
        # Real source values remain inaccessible until each group's completion.
        last = dates[-1]
        first_window_end = midnight_ns(last) + 576 * MINUTE
        before_window, _ = aggregate_table(tables[last], ledgers[last], asof_ns=first_window_end - 1)
        at_window, _ = aggregate_table(tables[last], ledgers[last], asof_ns=first_window_end)
        first_hour_end = midnight_ns(last) + 630 * MINUTE
        before_hour, _ = aggregate_table(tables[last], ledgers[last], asof_ns=first_hour_end - 1)
        at_hour, _ = aggregate_table(tables[last], ledgers[last], asof_ns=first_hour_end)
        require(not before_window[0]["window_complete"] and np.isnan(before_window[0]["window_raw_price"]) and
                at_window[0]["window_complete"] and not before_hour[0]["hour_complete"] and at_hour[0]["hour_complete"],
                "quote/hour became complete before its actual registered boundary")
        result["asof_boundary_checks"] = {"window_before_right_boundary_incomplete": True,
            "window_at_right_boundary_complete": True, "hour_before_right_boundary_incomplete": True,
            "hour_at_right_boundary_complete": True, "no_new_source_body_read": True}
        ledgers_json = {d: ledger_identity(l)[0] | {"ledger_sha256": ledger_identity(l)[1]} for d, l in ledgers.items()}
        artifacts.append({"role": "clock_ledgers", **write_json(output / "clock_ledgers_v3.json", ledgers_json)})
        artifacts.append(save_array(output / "H24_raw_80_24.npy", values, "H24_raw80"))
        artifacts.append(save_array(output / "hour_complete_80.npy", complete, "hour_complete80"))
        artifacts.append(save_array(output / "window80_complete_80.npy", eligibility, "window80_complete80"))
        artifacts.append({"role": "local_quality", **write_json(output / "stockday_local_quality_v3.json", audits)})
        serial = f.drop(columns=["hour_quality", "window_quality"])
        p = output / "symbol_hourly_features_labels.parquet"
        with p.open("xb") as handle:
            pq.write_table(pa.Table.from_pandas(serial, preserve_index=False), handle, compression="zstd")
        artifacts.append({"role": "hourly_features_labels", **identity(p)})
        original_ord = np.arange(len(tables[last]["datetime_nano"]), dtype=np.int64)
        cases = []
        baseline_past_windows = [(r["window_complete"], r["window_price"], r["window_raw_price"])
                                 for r in rows if r["window_end_time"] <= midnight_ns(last) + 630 * MINUTE]
        for future, names in ((True, FUTURE_CASES), (False, PAST_CASES)):
            for name in names:
                require(time.monotonic() - started < contract["budget"]["elapsed_seconds"], "elapsed counterfactual budget exceeded")
                changed, ordinals, witness = inject(tables[last], original_ord, ledgers[last], name, future)
                mutated_rows, qa = aggregate_table(changed, ledgers[last], ordinals)
                ff = build_symbol_features(rows[:-4] + mutated_rows, dates, contract["next_execution_start_ns"])
                current = ff.iloc[81:161]
                body = np.ascontiguousarray(current[list(H24)].to_numpy(), dtype="<f4")
                cmp = compare_features(body, values)
                flags_equal = np.array_equal(current.hour_complete.to_numpy(bool), complete) and np.array_equal(current.window80_complete.to_numpy(bool), eligibility)
                past_windows = [(r["window_complete"], r["window_price"], r["window_raw_price"])
                                for r in rows[:-4] + mutated_rows if r["window_end_time"] <= midnight_ns(last) + 630 * MINUTE]
                if future:
                    require(cmp["FP32_complete_body_equal"] and cmp["NaN_masks_equal"] and flags_equal and past_windows == baseline_past_windows,
                            "future evidence changed past feature/NaN/completeness/execution window")
                else:
                    require(not bool(current.hour_complete.iloc[-1]) and not bool(current.window80_complete.iloc[-1]) and
                            not mutated_rows[0]["window_complete"] and not cmp["FP32_complete_body_equal"], "known past error was not locally rejected")
                    require(body[:-1].tobytes() == values[:-1].tobytes(), "known current-hour error retroactively changed earlier hours")
                cases.append(witness | {"comparison": cmp, "hour_and80_flags_equal": bool(flags_equal),
                    "current_hour_complete": bool(current.hour_complete.iloc[-1]),
                    "current_window80_complete": bool(current.window80_complete.iloc[-1]),
                    "first_execution_window_complete": bool(mutated_rows[0]["window_complete"]),
                    "past_execution_windows_unchanged": bool(past_windows == baseline_past_windows),
                    "current_hour_quality": qa["hour_quality"][0],
                    "artifact": save_array(output / (name + "_H24_raw80.npy"), body, name)})
        artifacts.append({"role": "counterfactual_cases", **write_json(output / "counterfactual_cases_v3.json", cases)})
        result.update({"status": "PASS_CAUSAL_MINUTE_V3_FIXED41_PRODUCTION_PATH_AND_LOCAL_REJECTION_QUALIFIED",
                       "fixed_symbol": "000009.SZ", "clock_profile": NUMERIC_PROFILE,
                       "source_payloads_read_once": 41, "source_payload_bytes": source_bytes,
                       "source_Parquet_decodes": 41, "source_rows_decoded": decoded_rows,
                       "produced_hour_rows": len(f), "target_hour_indices": [6181, 6260],
                       "future_invariance_cases": len(FUTURE_CASES), "known_past_rejection_cases": len(PAST_CASES),
                       "160_hour_dependency_preserved": True, "feature_names": list(H24),
                       "clock_profile_and_missing_row_limits": contract["clock_profile_limits"],
                       "reference_output_body_sha256": digest(values.tobytes()),
                       "text_clock_policy": TEXT_CLOCK_POLICY,
                       "reordered_future_first_clock_cases_executed": [name for name in FUTURE_CASES if "reordered" in name],
                       "known_past_unsupported_text_clock_locally_rejected": True,
                       "new_original_panel_generated": False, "global_original_source_clock_or_arrival_proven": False})
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        result["status"] = "FAIL_CAUSAL_MINUTE_V3_FIXED41_NO_AUTOMATIC_RETRY"
        result["failure"] = {"type": type(exc).__name__, "reason": str(exc), "traceback": traceback.format_exc()[:65536]}
    finally:
        try:
            after = {p: file_stat(p) for p in before}
            require(before == after, "original payload STAT changed")
            small_after = {x["path"]: identity(x["path"]) for x in contract["small_guards"]}
            require(small_after == {x["path"]: x for x in contract["small_guards"]}, "small input SHA changed")
            require(identity(Path(__file__))["sha256"] == args.source_sha, "new source changed")
            require(identity(cp)["sha256"] == args.contract_sha256 and identity(mp)["sha256"] == args.source_manifest_sha256, "configuration changed")
            result["original_payload_STAT_before_after_unchanged"] = True
            result["small_input_SHA_before_after_unchanged"] = True
        except (Exception, KeyboardInterrupt) as exc:
            failure = failure or exc
            result["status"] = "FAIL_CAUSAL_MINUTE_V3_FIXED41_NO_AUTOMATIC_RETRY"
            result["guard_failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        result["artifacts"] = artifacts
        result["source_payload_STAT_before"] = before
        result["source_payload_STAT_after"] = after
        result["elapsed_seconds"] = time.monotonic() - started
        result["peak_RSS_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        if result["peak_RSS_bytes"] > contract["budget"]["peak_RSS_bytes"]:
            failure = failure or MemoryError("peak RSS exceeded contract")
            result["status"] = "FAIL_CAUSAL_MINUTE_V3_FIXED41_NO_AUTOMATIC_RETRY"
            result["resource_failure"] = "POSTCHECK_PEAK_RSS_BUDGET_EXCEEDED_NOT_OS_IO_MEASUREMENT"
        if result["elapsed_seconds"] > contract["budget"]["elapsed_seconds"]:
            failure = failure or TimeoutError("elapsed budget exceeded")
            result["status"] = "FAIL_CAUSAL_MINUTE_V3_FIXED41_NO_AUTOMATIC_RETRY"
            result["elapsed_failure"] = "POSTCHECK_ELAPSED_BUDGET_EXCEEDED_NOT_HARD_PROCESS_DEADLINE"
        result["finished_utc"] = utc()
        receipt = write_json(output / "actual_causal_minute_generation_receipt_v3.json", result)
        print(json.dumps({"status": result["status"], "receipt": receipt, "new_forward": False, "full_PLAN_complete": False}, ensure_ascii=False))
    return 1 if failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    for field in ("contract", "contract-sha256", "source-manifest", "source-manifest-sha256", "source-sha", "output"):
        parser.add_argument("--" + field)
    args = parser.parse_args()
    values = (args.contract, args.contract_sha256, args.source_manifest, args.source_manifest_sha256, args.source_sha, args.output)
    if not args.execute:
        require(not any(values), "actual configuration requires explicit execute")
        print(json.dumps(pure_contract(), ensure_ascii=False, indent=2))
        return 0
    require(all(values), "actual mode requires all source/input/output pins")
    for value in (args.contract_sha256, args.source_manifest_sha256, args.source_sha):
        require(len(value) == 64 and all(c in "0123456789abcdef" for c in value), "exact lowercase SHA required")
    return execute_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
