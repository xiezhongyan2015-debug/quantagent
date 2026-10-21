"""Local hour/window quality with immutable row-clock witnesses.

This adapter does not rebuild the original panel and does not establish feed
arrival times. Historical witnesses use a byte-bound original minute start plus
one minute. New live sources require their own independently verified clocks.
"""
from dataclasses import dataclass
import hashlib
import io
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

TZ = "Asia/Shanghai"
MINUTE = 60 * 10**9
OFFSETS = np.r_[np.arange(570, 690), np.arange(780, 900)]
SIGNALS = np.array([630, 690, 840, 900])
EXECS = np.array([571, 631, 781, 841])
FIELDS = ["股票代码", "datetime", "datetime_nano", "open", "high", "low", "close", "volume", "qfq_ratio", "qfq_open", "qfq_high", "qfq_low", "qfq_close"]
H24 = ["h_return_1", "h_open_gap", "h_open_close_return", "h_amplitude", "h_upper_shadow", "h_lower_shadow", "h_close_location", "h_volume_same_slot_20d", "h_volume_prev", "h_ma_deviation_4", "h_ma_deviation_20", "h_ma_deviation_80", "h_return_4", "h_return_20", "h_realized_volatility", "h_minute_return_skew", "h_minute_return_max", "h_minute_return_min", "h_up_semivariance_share", "h_return_logvolume_corr", "h_last15_volume_share", "h_zero_volume_share", "h_path_efficiency", "h_upminute_volume_share"]
CLOCK_BASIS = "IMMUTABLE_ORIGINAL_ROW_START_PLUS_ONE_MINUTE_HISTORICAL_COMPLETION_NOT_FEED_ARRIVAL"


@dataclass(frozen=True)
class HistoricalClockWitness:
    day: str
    symbol: str
    original_payload_sha256: str
    start_ns: tuple
    clock_basis: str = CLOCK_BASIS


def original_clock_witness(contents, day, symbol):
    """Bind valid original 240 starts before counterfactual value/time mutation."""
    table = pq.read_table(io.BytesIO(contents), columns=["股票代码", "datetime", "datetime_nano"], use_threads=False)
    ns = table["datetime_nano"].to_numpy().astype(np.int64)
    texts = np.asarray(table["datetime"].to_numpy()).astype("datetime64[ns]").astype(np.int64) - 8 * 3600 * 10**9
    midnight = pd.Timestamp(day, tz=TZ).tz_convert("UTC").value
    expected = midnight + OFFSETS * MINUTE
    codes = np.asarray(table["股票代码"].to_numpy()).astype(str)
    if len(ns) != 240 or not np.array_equal(ns, expected) or not np.array_equal(ns, texts) or not np.all(codes == symbol[:6]):
        raise ValueError("original payload does not establish complete exact ordered dual clocks")
    return HistoricalClockWitness(day, symbol, hashlib.sha256(contents).hexdigest(), tuple(map(int, ns)))


def _ratio(a, b):
    return float(a / b) if np.isfinite(b) and b != 0 else np.nan


def aggregate_stock_day(table, witness, row_ordinals, asof_ns=None):
    """Quality for one hour/window sees only rows known by its own boundary.

    row_ordinals refer to byte-bound original witness rows. Appended duplicate
    evidence uses its actual original ordinal; an altered event timestamp cannot
    move it to another hour. Without this independent witness the method refuses
    to infer historical availability from mutable timestamps alone.
    """
    if not isinstance(witness, HistoricalClockWitness) or witness.clock_basis != CLOCK_BASIS:
        raise ValueError("explicit independently bound historical row-clock witness required")
    if not set(FIELDS) <= set(table.column_names):
        raise ValueError("required raw minute fields missing")
    ordinals = np.asarray(row_ordinals)
    if ordinals.dtype.kind not in "iu" or ordinals.shape != (len(table),) or (ordinals < 0).any() or (ordinals >= len(witness.start_ns)).any():
        raise ValueError("row ordinal/source-clock identity mismatch")
    starts = np.asarray(witness.start_ns, dtype=np.int64)[ordinals]
    known = starts + MINUTE
    midnight = pd.Timestamp(witness.day, tz=TZ).tz_convert("UTC").value
    horizon = midnight + 900 * MINUTE if asof_ns is None else int(asof_ns)
    a = {k: table[k].to_numpy(zero_copy_only=False) for k in FIELDS}
    numeric_ns = pd.to_numeric(pd.Series(a["datetime_nano"]), errors="coerce").to_numpy()
    ns_safe = np.isfinite(numeric_ns) & (numeric_ns > 0) & (numeric_ns < np.iinfo(np.int64).max)
    ns = np.where(ns_safe, numeric_ns, 0).astype(np.int64)
    parsed_texts = pd.to_datetime(a["datetime"], errors="coerce")
    text_safe = ~pd.isna(parsed_texts)
    texts = np.asarray(parsed_texts).astype("datetime64[ns]").astype(np.int64) - 8 * 3600 * 10**9
    valid_clock = (ns_safe & text_safe & (ns == starts) & (texts == starts) & ((starts - midnight) % MINUTE == 0)
                   & (np.asarray(a["股票代码"]).astype(str) == witness.symbol[:6]))
    number = lambda k: pd.to_numeric(pd.Series(a[k]), errors="coerce").to_numpy(dtype=float)
    raw = np.column_stack([number(k) for k in ("open", "high", "low", "close")])
    adj = np.column_stack([number(k) for k in ("qfq_open", "qfq_high", "qfq_low", "qfq_close")])
    vol = number("volume")
    factor = number("qfq_ratio")
    valid = (valid_clock & np.isfinite(raw).all(1) & np.isfinite(adj).all(1)
             & (raw > 0).all(1) & (adj > 0).all(1) & np.isfinite(vol) & (vol >= 0)
             & np.isfinite(factor) & (factor > 0)
             & (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8)
             & (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8)
             & np.isclose(adj, raw * factor[:, None], rtol=1e-7, atol=1e-6).all(1))

    def local_group(expected_offsets, boundary):
        cutoff = min(int(boundary), horizon)
        expected = midnight + np.asarray(expected_offsets) * MINUTE
        pos = np.flatnonzero(np.isin(starts, expected) & (known <= cutoff))
        pos = pos[np.argsort(starts[pos], kind="stable")]
        kept, conflicts = [], 0
        for stamp in np.unique(starts[pos]):
            group = pos[starts[pos] == stamp]
            first = group[0]
            if len(group) > 1 and any(any(a[k][j] != a[k][first] for k in FIELDS) for j in group[1:]):
                conflicts += 1
            else:
                kept.append(first)
        kept = np.asarray(kept, dtype=np.int64)
        clock_bad = int((~valid_clock[pos]).sum())
        row_bad = int((~valid[pos]).sum())
        factor_bad = False
        observed = np.flatnonzero((known <= cutoff) & valid)
        if len(kept) and len(observed):
            first_observed = observed[np.argmin(starts[observed])]
            # Only already-completed source rows set the expected session factor.
            factor_bad = not np.allclose(factor[kept], factor[first_observed], rtol=1e-7, atol=1e-10)
        complete = bool(len(kept) == len(expected) and np.array_equal(starts[kept], expected)
                        and valid[kept].all() and not factor_bad and not conflicts
                        and vol[kept].sum() > 0 and int(boundary) <= horizon)
        flags = {"invalid_observed_rows": row_bad, "clock_invalid_observed_rows": clock_bad,
                 "conflicting_duplicate_groups": conflicts, "known_factor_inconsistency": bool(factor_bad),
                 "observed_unique_rows": len(kept), "quality_asof_ns": cutoff}
        return kept, complete, flags

    output = []
    for slot in range(4):
        signal = int(midnight + SIGNALS[slot] * MINUTE)
        pos, complete, flags = local_group(OFFSETS[slot*60:(slot+1)*60], signal)
        row = {"date": witness.day, "symbol": witness.symbol, "slot": slot, "signal_time": signal,
               "hour_complete": complete, "minute_count": len(pos), "hour_quality": flags}
        row.update({k: np.nan for k in H24[14:] + ["open", "high", "low", "close", "volume", "raw_open", "raw_high", "raw_low", "raw_close", "adjustment_factor"]})
        if complete:
            o, h, l, c = adj[pos[0], 0], adj[pos, 1].max(), adj[pos, 2].min(), adj[pos[-1], 3]
            v = vol[pos]
            ret = np.log(adj[pos, 3] / np.r_[o, adj[pos[:-1], 3]])
            sr, std = float(np.square(ret).sum()), ret.std()
            logv = np.log1p(v)
            corr = float(np.corrcoef(ret, logv)[0, 1]) if std > 1e-12 and logv.std() > 1e-12 else np.nan
            path = float(np.abs(np.diff(np.r_[o, adj[pos, 3]])).sum())
            stats = [np.sqrt(sr), float(np.mean(((ret-ret.mean())/std)**3)) if std > 1e-12 else np.nan,
                     ret.max(), ret.min(), _ratio(np.square(ret[ret > 0]).sum(), sr), corr,
                     _ratio(v[-15:].sum(), v.sum()), float(np.mean(v == 0)), _ratio(abs(c-o), path), _ratio(v[ret > 0].sum(), v.sum())]
            row.update(dict(zip(H24[14:], stats)))
            row.update({"open": o, "high": h, "low": l, "close": c, "volume": v.sum(),
                        "raw_open": raw[pos[0], 0], "raw_high": raw[pos, 1].max(), "raw_low": raw[pos, 2].min(), "raw_close": raw[pos[-1], 3], "adjustment_factor": factor[pos[-1]]})
        start = int(midnight + EXECS[slot] * MINUTE)
        epos, ecomplete, eflags = local_group(np.arange(EXECS[slot], EXECS[slot]+5), start+5*MINUTE)
        row.update({"window_start_time": start, "window_end_time": start+5*MINUTE,
                    "window_complete": ecomplete, "window_quality": eflags,
                    "window_price": float(adj[epos, 0].mean()) if ecomplete else np.nan,
                    "window_raw_price": float(raw[epos, 0].mean()) if ecomplete else np.nan,
                    "window_volume": float(vol[epos].sum()) if ecomplete else np.nan,
                    "window_tradable": ecomplete,
                    "window_all_flat": bool(ecomplete and (raw[epos, 1] == raw[epos, 2]).all())})
        output.append(row)
    return output


def add_causal_hour_features(frame):
    """Same 24 feature algebra over real ordered hours; no execution/label read."""
    f = frame.sort_values("signal_time").copy().reset_index(drop=True)
    if f.signal_time.duplicated().any():
        raise ValueError("duplicate hour times")
    c, o, h, l, v = [f[k].where(f.hour_complete) for k in ("close", "open", "high", "low", "volume")]
    p = c.shift(1)
    f["h_return_1"], f["h_open_gap"] = np.log(c/p), np.log(o/p)
    f["h_open_close_return"], f["h_amplitude"] = np.log(c/o), (h-l)/o
    spread = (h-l).replace(0, np.nan)
    f["h_upper_shadow"] = (h-pd.concat([o,c],axis=1).max(axis=1))/spread
    f["h_lower_shadow"] = (pd.concat([o,c],axis=1).min(axis=1)-l)/spread
    f["h_close_location"] = (c-l)/spread
    same = v.groupby(f["slot"]).transform(lambda s:s.shift(1).rolling(20,min_periods=20).mean())
    f["h_volume_same_slot_20d"] = v/same.replace(0,np.nan)
    f["h_volume_prev"] = v/v.shift(1).replace(0,np.nan)
    for n in (4,20,80):
        f[f"h_ma_deviation_{n}"] = c/c.rolling(n,min_periods=n).mean()-1
    for n in (4,20):
        f[f"h_return_{n}"] = np.log(c/c.shift(n))
    f["window80_complete"] = f.hour_complete.astype(np.int16).rolling(80,min_periods=80).sum().eq(80)
    # Preserve the frozen feature payload's float32 serialization contract.
    for name in H24:
        f[name] = f[name].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    return f
