"""Real incremental sources -> the frozen GRU's features-only 80-hour window.

This adapter never selects factors, reads labels, changes frozen files, fetches
data, schedules work, or creates forecasts. ``status`` inspects source readiness;
``prime-slow`` manually precomputes known slow values before the session's first
signal. ``prepare`` requires that cache and the final model freeze, and writes an immutable
NPZ/manifest only when a real complete window can be scored. Replay checks are
engineering evidence, never genuine prospective observations.

The new-source JSON contract is available with ``contract``. Incoming files must
carry exact byte hashes and verified units/timing/PIT receipts. Raw daily volume,
amount and turnover can be used without an adjustment bridge; raw daily prices
cannot be silently combined with frozen HFQ prices. Missing slow fields stay
NaN. Missing exchange hours cannot be removed from the 80-hour grid.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .assemble import normalize_cross_section, validate_slow_dates
from .minute_data import HOUR_FEATURES, aggregate_stock_day
from .prospective import (CHINA, ExchangeCalendar, FeatureWindow, FrozenContract,
    IntegrityError, Pending, canonical, file_hash, local_date, local_ns, utc_ns)
from .slow_features import (ROOT, DAILY_ROOT, FUND_ROOT, DAILY16, FUND16, AGES2,
    daily_state, financial_events, pit_join, read_daily, wide_fields, load_legacy_compiler)

HOUR_RAW = ["open", "high", "low", "close", "volume", "raw_open", "raw_high",
            "raw_low", "raw_close", "adjustment_factor"]
MINUTE_REQUIRED = ["股票代码", "datetime", "datetime_nano", "open", "high", "low",
    "close", "volume", "qfq_ratio", "qfq_open", "qfq_high", "qfq_low", "qfq_close",
    "available_at_ns"]
SOURCE_CONTRACT = {
    "schema_version": 1, "raw_market_data_is_synthetic": False,
    "audit": {"path": "immutable source audit JSON", "sha256": "actual bytes SHA256",
              "required_content": {"passed": True, "source_semantics_verified": True}},
    "minutes": [{"date": "YYYY-MM-DD", "symbol": "000006.SZ", "path": "actual parquet",
                 "sha256": "actual bytes SHA256", "timestamp_semantics": "left/start",
                 "volume_unit": "shares", "price_basis": "qfq", "required_columns": MINUTE_REQUIRED}],
    "membership": {"path": "actual historical effective membership parquet", "sha256": "actual bytes SHA256",
                   "required_columns": ["date", "symbol", "available_at_ns"],
                   "availability": "verified information availability, never invented from date"},
    "daily": [{"path": "actual new daily parquet", "sha256": "actual bytes SHA256",
               "price_basis": "hfq or raw", "volume_unit": "shares", "amount_unit": "CNY",
               "turnover_unit": "percent", "required_columns": ["date", "symbol", "open", "high",
                   "low", "close", "volume", "amount", "turnoverRatio", "available_at_ns"]}],
    "financial": {"path": "optional additional verified 19-field PIT parquet", "sha256": "actual bytes SHA256",
        "required_columns": ["asof_date", "thscode", "report_period", "source_update_date", "first_provider_event_date",
            "revenue", "operating_cost", "operating_profit", "total_profit", "parent_net_profit", "total_assets",
            "total_liabilities", "parent_equity", "current_assets", "current_liabilities", "accounts_receivable",
            "inventory", "bonds_payable", "cash", "short_term_borrowing", "long_term_borrowing", "rd_expense",
            "operating_cash_flow", "capex_cash_paid"],
        "currency_unit": "CNY", "timing": "Historical query asof_date EOD, usable strictly next verified session; never announcement-date backfill"},
    "market": {"path": "optional additional weekly cap PIT parquet", "sha256": "actual bytes SHA256",
        "required_columns": ["asof_date", "thscode", "total_market_cap_cny"], "currency_unit": "CNY",
        "timing": "Historical query asof_date EOD, usable strictly next verified session"},
    "minute_rebase_policy": "required overlapping complete source/frozen hour with matching raw OHLCV; constant per-symbol adjusted price bridge",
    "source_audit_note": "source availability is a verified event/PIT bound, not a backdated retrieval timestamp",
}


def verify_file(item, label):
    path = Path(item.get("path", ""))
    if not path.is_file():
        raise Pending(label + "_file_not_available")
    if item.get("sha256") != file_hash(path):
        raise IntegrityError(label + " source bytes differ from receipt")
    return path


def legacy_source_paths():
    root = Path(__file__).resolve().parents[1] / "quantagent/src/quanta_agents"
    return [root / "__init__.py", root / "factor_lab_a/__init__.py",
            root / "factor_lab_a/dsl.py", root / "factor_lab_a/minute_features.py"]


@dataclass
class FrozenFeatures:
    dataset: Path
    meta: dict
    selection: dict
    selection_path: Path
    symbols: list[str]
    times: np.ndarray

    @classmethod
    def read(cls, dataset, selection_path):
        dataset, selection_path = Path(dataset), Path(selection_path)
        meta = json.loads((dataset / "meta.json").read_text())
        if file_hash(selection_path) != meta["factor_selection_sha256"]:
            raise IntegrityError("Frozen factor selection hash differs from training metadata")
        selection = json.loads(selection_path.read_text())
        factors = ["factor_" + d["id"] for d in selection["selected"]]
        names = HOUR_FEATURES + DAILY16 + factors + FUND16 + AGES2
        if names != meta["features"] or len(factors) > 64:
            raise IntegrityError("Exact frozen feature order/formulas are required")
        if meta.get("causality_audit", {}).get("passed") is not True:
            raise IntegrityError("Frozen base panel lacks its causal audit")
        if meta.get("raw_market_data_is_synthetic") is False:
            audit_path = dataset / "dataset_audit.json"
            if not audit_path.is_file() or file_hash(audit_path) != meta["files_sha256"].get("dataset_audit.json"):
                raise IntegrityError("Original independent source audit bytes are missing or altered")
            receipt = json.loads(audit_path.read_text())["source_tests_receipt"]
            if receipt.get("passed") is not True:
                raise IntegrityError("Original independent feature operator tests did not pass")
            for name in ("minute_data.py", "slow_features.py", "assemble.py"):
                if file_hash(Path(__file__).with_name(name)) != receipt["source_code_sha256"][name]:
                    raise IntegrityError("Frozen feature operator source changed: " + name)
        for name in ("symbols.json", "times.npy"):
            if meta["files_sha256"].get(name) != file_hash(dataset / name):
                raise IntegrityError("Frozen historical clock/universe bytes changed: " + name)
        symbols = json.loads((dataset / "symbols.json").read_text())
        times = np.load(dataset / "times.npy", mmap_mode="r")
        if times.dtype != np.int64 or len(times) == 0 or np.any(np.diff(times) <= 0):
            raise IntegrityError("Frozen historical timestamps are invalid")
        return cls(dataset, meta, selection, selection_path, symbols, times)

    def verified_array(self, name):
        path = self.dataset / (name + ".npy")
        if self.meta["files_sha256"].get(path.name) != file_hash(path):
            raise IntegrityError("Frozen base bytes changed: " + path.name)
        return np.load(path, mmap_mode="r")

    def check_model_freeze(self, contract):
        if contract.meta != self.meta:
            raise IntegrityError("Feature adapter base differs from final model metadata")


@dataclass
class IncrementalSources:
    spec: dict
    path: Path | None = None

    @classmethod
    def read(cls, path, fixture=False):
        if path is None or not Path(path).is_file():
            raise Pending("real_incremental_source_manifest_not_available")
        path = Path(path)
        spec = json.loads(path.read_text())
        if spec.get("schema_version") != 1 or spec.get("raw_market_data_is_synthetic") is not bool(fixture):
            raise IntegrityError("Incoming source mode/schema must match genuine or explicit fixture mode")
        audit_path = verify_file(spec.get("audit", {}), "source_audit")
        audit = json.loads(audit_path.read_text())
        if audit.get("passed") is not True or audit.get("source_semantics_verified") is not True:
            raise Pending("real_source_units_timing_and_PIT_audit_not_verified")
        return cls(spec, path)

    @property
    def hashes(self):
        result = {str(self.path.resolve()): file_hash(self.path)} if self.path else {}
        items = self.spec.get("minutes", []) + self.spec.get("daily", [])
        items += [self.spec[k] for k in ("audit", "membership", "financial", "market") if self.spec.get(k)]
        for item in items:
            path = verify_file(item, "incremental")
            result[str(path.resolve())] = item["sha256"]
        return result

    def membership(self):
        if not self.spec.get("membership"):
            raise Pending("real_new_historical_membership_source_not_available")
        frame = pd.read_parquet(verify_file(self.spec["membership"], "membership"))
        require = {"date", "symbol", "available_at_ns"}
        if not require <= set(frame):
            raise Pending("membership_effective_dates_or_verified_availability_missing")
        frame["date"] = pd.to_datetime(frame.date).dt.strftime("%Y-%m-%d")
        if frame.duplicated(["date", "symbol"]).any():
            raise IntegrityError("Duplicate new historical membership keys")
        if frame.available_at_ns.dtype != np.int64 or (frame.available_at_ns <= 0).any():
            raise IntegrityError("Membership availability must be positive UTC ns int64")
        return frame

    def minute_rows(self, signal):
        rows, audits = [], []
        for item in self.spec.get("minutes", []):
            if item["date"] > local_date(signal):
                continue
            if (item.get("timestamp_semantics") != "left/start" or item.get("volume_unit") != "shares"
                    or item.get("price_basis") != "qfq"):
                raise Pending("minute_timestamp_volume_or_adjustment_semantics_not_frozen_compatible")
            path = verify_file(item, "minute")
            table = pq.read_table(path)
            if not set(MINUTE_REQUIRED) <= set(table.column_names):
                raise Pending("complete_minute_adjusted_fields_or_verified_availability_missing")
            nanos = table["datetime_nano"].to_numpy().astype(np.int64)
            arrival = table["available_at_ns"].to_numpy()
            if arrival.dtype != np.int64 or np.any(arrival <= 0):
                raise IntegrityError("Minute source availability must be positive UTC ns int64")
            # A source file may contain later bars; no future row reaches an operator.
            keep = nanos + 60_000_000_000 <= signal
            table = table.filter(pa.array(keep))
            if len(table) == 0:
                continue
            buffer = io.BytesIO()
            pq.write_table(table, buffer)
            result, audit = aggregate_stock_day((item["date"], item["symbol"], buffer.getvalue()))
            if audit["error"] or audit["timestamp_mismatch_rows"] or audit["duplicate_conflicts"] or audit["qfq_mismatch_rows"]:
                raise IntegrityError("Incremental minute corruption: " + json.dumps(audit))
            available = arrival[keep]
            minute_ns = nanos[keep]
            for row in result:
                if row["signal_time"] > signal:
                    continue
                within = (minute_ns >= row["signal_time"] - 60 * 60_000_000_000) & (minute_ns < row["signal_time"])
                row["source_available_at_ns"] = int(available[within].max()) if within.any() else row["signal_time"]
                if row["source_available_at_ns"] > row["signal_time"]:
                    raise Pending("minute_historical_availability_after_its_feature_row_not_verified")
                rows.append(row)
            audits.append(audit)
        result = pd.DataFrame(rows)
        if len(result) and result.duplicated(["signal_time", "symbol"]).any():
            raise IntegrityError("Overlapping new minute files create duplicate stock hours")
        return result, audits

    def daily_rows(self, signal, available_now=None):
        frames, raw_price_rows = [], 0
        for item in self.spec.get("daily", []):
            if item.get("volume_unit") != "shares" or item.get("amount_unit") != "CNY" or item.get("turnover_unit") != "percent":
                raise Pending("daily_volume_amount_or_turnover_units_not_verified")
            f = pd.read_parquet(verify_file(item, "daily"))
            require = set(SOURCE_CONTRACT["daily"][0]["required_columns"])
            if not require <= set(f):
                raise Pending("daily_source_fields_or_verified_availability_missing")
            if f.available_at_ns.dtype != np.int64 or (f.available_at_ns <= 0).any():
                raise IntegrityError("Daily source availability must be positive UTC ns int64")
            f["date"] = pd.to_datetime(f.date)
            cutoff = int(signal) if available_now is None else min(int(signal), int(available_now))
            f = f[(f.available_at_ns <= cutoff) & (f.date < pd.Timestamp(local_date(signal)))].copy()
            if item.get("price_basis") == "raw":
                raw_price_rows += len(f)
                f[["open", "high", "low", "close"]] = np.nan
            elif item.get("price_basis") != "hfq":
                raise Pending("daily_adjusted_price_basis_not_verified")
            f["_price_basis"] = item["price_basis"]
            f["turnover"] = f.turnoverRatio / 100.0
            if (f[["volume", "amount"]] < 0).any().any():
                raise IntegrityError("Negative real daily volume/amount")
            if item.get("price_basis") == "hfq":
                prices = f[["open", "high", "low", "close"]]
                valid = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
                valid &= f.high.ge(prices.max(axis=1)) & f.low.le(prices.min(axis=1))
                f.loc[~valid, ["open", "high", "low", "close", "volume", "amount", "turnover"]] = np.nan
            frames.append(f)
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if len(result) and result.duplicated(["date", "symbol"]).any():
            raise IntegrityError("Duplicate incremental daily rows")
        return result, {"new_daily_rows": len(result), "raw_daily_price_rows_masked": raw_price_rows}


def feature_only_hours(frame, dates):
    """Exact frozen h24 formulas, with no label or future-execution-clock operations."""
    names = ["date", "symbol", "slot", "signal_time", "hour_complete"] + HOUR_RAW + HOUR_FEATURES[14:]
    f = frame.reindex(columns=names).copy()
    if f.empty or f.symbol.nunique() != 1:
        raise IntegrityError("Hour feature input must contain one actual security")
    symbol = f.symbol.iloc[0]
    times = np.array([local_ns(d, h, m) for d in dates for h, m in ((10, 30), (11, 30), (14, 0), (15, 0))], np.int64)
    f = f.set_index("signal_time").reindex(pd.Index(times, name="signal_time"))
    f["date"], f["slot"], f["symbol"] = np.repeat(dates, 4), np.tile(np.arange(4), len(dates)), symbol
    f["hour_complete"] = f.hour_complete.eq(True)
    c, o, h, l, v = [f[n].where(f.hour_complete) for n in ("close", "open", "high", "low", "volume")]
    previous = c.shift(1)
    f["h_return_1"] = np.log(c / previous)
    f["h_open_gap"] = np.log(o / previous)
    f["h_open_close_return"] = np.log(c / o)
    f["h_amplitude"] = (h - l) / o
    spread = (h - l).replace(0, np.nan)
    f["h_upper_shadow"] = (h - pd.concat([o, c], axis=1).max(axis=1)) / spread
    f["h_lower_shadow"] = (pd.concat([o, c], axis=1).min(axis=1) - l) / spread
    f["h_close_location"] = (c - l) / spread
    same_slot = v.groupby(f.slot).transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    f["h_volume_same_slot_20d"] = v / same_slot.replace(0, np.nan)
    f["h_volume_prev"] = v / v.shift(1).replace(0, np.nan)
    for n in (4, 20, 80):
        f[f"h_ma_deviation_{n}"] = c / c.rolling(n, min_periods=n).mean() - 1
    for n in (4, 20):
        f[f"h_return_{n}"] = np.log(c / c.shift(n))
    for name in HOUR_FEATURES:
        f[name] = f[name].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    return f.reset_index()[["signal_time", "date", "symbol", "hour_complete"] + HOUR_FEATURES]


def bridge_minute_prices(new, base, frozen_times, frozen_symbols, frozen_hour, frozen_complete, exact_old=None):
    """Infer a constant adjustment-base bridge only from matching real overlap."""
    if len(new) == 0:
        return new, {}
    result, bridges = new.copy(), {}
    si = {s: i for i, s in enumerate(frozen_symbols)}
    ti = {int(t): i for i, t in enumerate(frozen_times)}
    for symbol, group in new.groupby("symbol"):
        if symbol not in si:
            # No invented warmup/base for a symbol never represented in the frozen universe.
            raise Pending("new_symbol_adjusted_history_and_frozen_normalization_not_yet_audited: " + symbol)
        u, ratios, overlaps = si[symbol], [], []
        for row in group.itertuples():
            t = ti.get(int(row.signal_time))
            if t is None or not row.hour_complete or not frozen_complete[t, u]:
                continue
            if exact_old is not None and (int(row.signal_time), symbol) in exact_old.index:
                old = exact_old.loc[(int(row.signal_time), symbol), HOUR_RAW].to_numpy(dtype=float)
            else:
                old = np.asarray(frozen_hour[t, u], dtype=float)
            raw = np.array([row.raw_open, row.raw_high, row.raw_low, row.raw_close, row.volume])
            if not np.allclose(raw, old[[5, 6, 7, 8, 4]], rtol=1e-7, atol=1e-6):
                raise IntegrityError("New raw overlap differs from frozen OHLCV: " + symbol)
            ratio = old[:4] / np.array([row.open, row.high, row.low, row.close])
            ratios.extend(ratio.tolist())
            overlaps.append(int(row.signal_time))
        if not ratios:
            raise Pending("real_adjustment_base_overlap_not_available: " + symbol)
        ratio = float(np.median(ratios))
        if not np.isfinite(ratio) or ratio <= 0 or not np.allclose(ratios, ratio, rtol=1e-7, atol=1e-10):
            raise IntegrityError("New adjusted source has a nonconstant overlap bridge: " + symbol)
        chosen = result.symbol.eq(symbol)
        result.loc[chosen, ["open", "high", "low", "close", "adjustment_factor"]] *= ratio
        bridges[symbol] = {"new_adjusted_to_frozen_scale": ratio, "overlap_signal_times": overlaps}
    return result, bridges


def historical_hour_rows(base, start, end):
    """Read the original double-precision derived bars; FP32 tape is not a new feature seed."""
    first, last = local_date(base.times[start]), local_date(base.times[end])
    paths = sorted((ROOT / "gru_research/artifacts/minute_hourly_raw").glob("*.parquet"))
    paths = [p for p in paths if first <= p.stem <= last]
    cols = ["date", "symbol", "slot", "signal_time", "hour_complete"] + HOUR_RAW + HOUR_FEATURES[14:]
    if not paths:
        raise Pending("original_double_precision_hour_warmup_not_available")
    frames = [pd.read_parquet(p, columns=cols) for p in paths]
    raw = pd.concat(frames, ignore_index=True)
    raw = raw[(raw.signal_time >= int(base.times[start])) & (raw.signal_time <= int(base.times[end]))]
    if raw.duplicated(["signal_time", "symbol"]).any():
        raise IntegrityError("Original derived hour warmup has duplicate keys")
    return raw, {str(p.resolve()): file_hash(p) for p in paths}


def full_daily_calendar(exchange, last_day):
    old = [p.name[:10] for p in (DAILY_ROOT / "memberships").glob("*.json.gz") if p.name[:10] < exchange.coverage_start]
    return pd.DatetimeIndex(sorted(set(old + [d for d in exchange.dates if d <= last_day])))


def merge_daily_sources(base, additional, cutoff):
    """Use only arrived versions; verify HFQ base using real overlapping OHLC."""
    if len(additional) == 0:
        return base.copy(), {}
    extra = additional[additional.available_at_ns <= cutoff].copy()
    indexed = base.set_index(["date", "symbol"])
    bridges = {}
    for symbol, group in extra[extra._price_basis.eq("hfq")].groupby("symbol"):
        ratios = []
        for row in group.itertuples():
            key = (row.date, symbol)
            if key not in indexed.index:
                continue
            old = indexed.loc[key]
            new = np.array([row.open, row.high, row.low, row.close], dtype=float)
            previous = old[["open", "high", "low", "close"]].to_numpy(dtype=float)
            if np.isfinite(new).all() and np.isfinite(previous).all() and (new > 0).all():
                ratios.extend((previous / new).tolist())
        if not ratios:
            # Genuine unbridged price observations cannot be spliced across two
            # adjustment bases. Keep independently verified quantities usable.
            extra.loc[extra.symbol.eq(symbol) & extra._price_basis.eq("hfq"), ["open", "high", "low", "close"]] = np.nan
            bridges[symbol] = None
            continue
        ratio = float(np.median(ratios))
        if not np.isfinite(ratio) or ratio <= 0 or not np.allclose(ratios, ratio, rtol=1e-7, atol=1e-10):
            raise IntegrityError("New daily HFQ base is inconsistent: " + symbol)
        extra.loc[extra.symbol.eq(symbol) & extra._price_basis.eq("hfq"), ["open", "high", "low", "close"]] *= ratio
        bridges[symbol] = ratio
    # Overlap is bridge evidence only, never an overwrite of old query versions.
    # The old provider queried the dated CSI500 pool. A different provider's
    # historical union cannot fill old nonmember holes or revise EMA/rank history.
    new = extra.loc[extra.date.gt(pd.to_datetime(base.date).max())].drop(columns=["_price_basis"])
    return pd.concat([base, new], ignore_index=True), bridges


def slow_rows(target_members, daily, financial, market, dates, symbols, selected):
    """Execute the fixed64 formulas and original PIT operators, never selection."""
    fields = wide_fields(daily, dates, symbols)
    raw = pit_join(target_members[["date", "symbol"]], financial, market, dates)
    di = dates.get_indexer(pd.to_datetime(raw.date))
    si = pd.Index(symbols).get_indexer(raw.symbol)
    for name, values in daily_state(fields).items():
        raw[name] = values.to_numpy()[di, si]
    raw["d_log_total_cap"] = np.log(raw.total_market_cap_cny.where(raw.total_market_cap_cny > 0))
    compiler = load_legacy_compiler()
    for definition in selected:
        value = compiler.evaluate(definition["canonical"], fields=fields).shift(1)
        name = "factor_" + definition["id"]
        raw[name] = value.to_numpy()[di, si]
        # Preserve the exact frozen DSL missing-input behavior. An EMA state can
        # remain finite using known old observations; this does not create a new
        # price. Missing raw inputs stay NaN and their source gap is audited.
    names = DAILY16 + ["factor_" + d["id"] for d in selected] + FUND16 + AGES2
    raw[names] = raw[names].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    raw = raw.copy()
    raw["daily_source_date"] = dates.to_series().shift(1).reindex(pd.to_datetime(raw.date)).to_numpy()
    validate_slow_dates(raw)
    return raw


def slow_source_identity(base, exchange, sources, signal, now, target_days):
    """Slow cache identity excludes changing minute snapshots, includes arrival cohorts."""
    hashes = {str(base.selection_path.resolve()): file_hash(base.selection_path)}
    for path in (FUND_ROOT / "financial_verified_19_fields.parquet", FUND_ROOT / "weekly_market_and_industry.parquet"):
        hashes[str(path.resolve())] = file_hash(path)
    for path in sorted((DAILY_ROOT / "bars").rglob("*.parquet")):
        if "2010" <= path.relative_to(DAILY_ROOT / "bars").parts[0] <= local_date(signal)[:4]:
            hashes[str(path.resolve())] = file_hash(path)
    for label in ("audit", "membership", "financial", "market"):
        if sources.spec.get(label):
            path = verify_file(sources.spec[label], label)
            hashes[str(path.resolve())] = file_hash(path)
    for item in sources.spec.get("daily", []):
        path = verify_file(item, "daily")
        hashes[str(path.resolve())] = file_hash(path)
    daily, _ = sources.daily_rows(signal, now)
    # A file can already contain declared later availability. Changing the
    # admitted cohort must invalidate a cache even when its bytes are identical.
    cohort = hashlib.sha256(pd.util.hash_pandas_object(daily, index=False).to_numpy().tobytes()).hexdigest()
    operators = {str(Path(__file__).with_name(name).resolve()): file_hash(Path(__file__).with_name(name))
                 for name in ("minute_data.py", "slow_features.py", "assemble.py")}
    operators.update({str(p.resolve()): file_hash(p) for p in legacy_source_paths()})
    previous = {day: exchange.dates[exchange.dates.index(day) - 1] for day in target_days}
    identity = {"base_fingerprint": base.meta["data_fingerprint"], "selection_sha256": base.meta["factor_selection_sha256"],
        "features": base.meta["features"], "target_session_first_signal_ns": local_ns(local_date(signal), 10, 30), "target_days": target_days,
        "previous_complete_sessions": previous, "source_sha256": hashes, "frozen_operator_sha256": operators,
        "source_schema_sha256": hashlib.sha256(canonical(SOURCE_CONTRACT)).hexdigest(),
        "historical_calendar_dates_sha256": hashlib.sha256(canonical([str(d.date()) for d in full_daily_calendar(exchange, local_date(signal))])).hexdigest(),
        "daily_arrival_cohort_sha256": cohort, "known_query_date_bound": min(local_date(now), local_date(signal)),
        "adapter_source_sha256": file_hash(__file__), "exchange_calendar_sha256": exchange.fingerprint}
    return hashlib.sha256(canonical(identity)).hexdigest(), identity, hashes


def prepare_slow_values(base, exchange, sources, membership, memberships, target_days, signal, now,
                        cache_dir=None, require_cache=False, fixture=False):
    """Manual pre-signal priming or exact cache read, without future prices/labels."""
    identity_hash, identity, hashes = slow_source_identity(base, exchange, sources, signal, now, target_days)
    first_signal = identity["target_session_first_signal_ns"]
    cache_path = Path(cache_dir) / (identity_hash + ".parquet") if cache_dir else None
    receipt_path = Path(cache_dir) / (identity_hash + ".json") if cache_dir else None
    if cache_path and cache_path.exists() != receipt_path.exists():
        raise IntegrityError("Incomplete immutable slow cache receipt; no overwrite permitted")
    if cache_path and cache_path.exists() and receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["identity"] != identity or receipt["sha256"] != file_hash(cache_path):
            raise IntegrityError("Immutable pre-signal slow cache was altered")
        if receipt["prepared_at_ns"] >= first_signal or receipt["input_cutoff_ns"] > receipt["prepared_at_ns"]:
            raise IntegrityError("Slow cache was not actually committed before its target signal")
        if not fixture and max(cache_path.stat().st_mtime_ns, receipt_path.stat().st_mtime_ns) >= first_signal:
            raise IntegrityError("Slow cache files were committed after their target signal")
        if receipt["raw_market_data_is_synthetic"] is not bool(fixture):
            raise IntegrityError("Synthetic slow cache cannot become real source evidence")
        raw = pd.read_parquet(cache_path)
        hashes[str(cache_path.resolve())] = receipt["sha256"]
        hashes[str(receipt_path.resolve())] = file_hash(receipt_path)
        return raw, receipt["audit"], hashes, {"hit": True, "path": str(cache_path.resolve()),
            "sha256": receipt["sha256"], "receipt_path": str(receipt_path.resolve()),
            "receipt_sha256": file_hash(receipt_path), "prepared_at_ns": receipt["prepared_at_ns"]}
    if require_cache:
        raise Pending("valid_slow_cache_not_primed; run prime-slow before the signal using actual sources")
    daily, daily_audit = read_daily(root=DAILY_ROOT, history_start="2010-01-01", end=min(local_date(signal), local_date(now)), symbols=base.symbols)
    extra, extra_audit = sources.daily_rows(signal, now)
    if len(extra):
        new_rows = extra.date.gt(pd.to_datetime(daily.date).max())
        keys = set(zip(pd.to_datetime(membership.date), membership.symbol))
        permitted = np.array([(d, s) in keys for d, s in zip(extra.date, extra.symbol)])
        extra = extra.loc[~new_rows | permitted].copy()
        extra_audit["new_daily_nonmember_rows_excluded"] = int((new_rows & ~permitted).sum())
    financial = pd.read_parquet(FUND_ROOT / "financial_verified_19_fields.parquet")
    market = pd.read_parquet(FUND_ROOT / "weekly_market_and_industry.parquet")
    for label in ("financial", "market"):
        if sources.spec.get(label):
            if sources.spec[label].get("currency_unit") != "CNY":
                raise Pending("new_verified_" + label + "_currency_unit_not_verified")
            added = pd.read_parquet(verify_file(sources.spec[label], label))
            if not set(SOURCE_CONTRACT[label]["required_columns"]) <= set(added):
                raise Pending("new_verified_" + label + "_PIT_schema_incomplete")
            if label == "financial":
                financial = pd.concat([financial, added], ignore_index=True)
            else:
                market = pd.concat([market, added], ignore_index=True)
    financial = financial[pd.to_datetime(financial.asof_date).le(pd.Timestamp(local_date(now)))]
    market = market[pd.to_datetime(market.asof_date).le(pd.Timestamp(local_date(now)))]
    calendar = full_daily_calendar(exchange, local_date(signal))
    versions, bridges = [], {}
    for day in target_days:
        visible, bridge = merge_daily_sources(daily, extra, local_ns(day, 10, 30))
        bridges[day] = bridge
        versions.append(slow_rows(memberships[day][["date", "symbol"]], visible, financial, market,
                                 calendar, base.symbols, base.selection["selected"]))
    raw = pd.concat(versions, ignore_index=True)
    audit = {"daily_audit": {**daily_audit, **extra_audit}, "daily_adjustment_bridges_by_day": bridges,
             "factor_selection_rerun": False, "future_raw_prices_read": False,
             "weekly_cap_policy": "Frozen backward PIT join; actual old version persists until a strictly next-session-available query version",
             "market_version_audit": [
                 {"date": str(row.date.date()), "symbol": row.symbol,
                  "market_query_date": str(row.market_query_date.date()) if pd.notna(row.market_query_date) else None,
                  "market_available_date": str(row.market_available_date.date()) if pd.notna(row.market_available_date) else None,
                  "calendar_days_since_actual_cap_query": int((row.date - row.market_query_date).days) if pd.notna(row.market_query_date) else None,
                  "cap_value_missing": not np.isfinite(row.total_market_cap_cny)}
                 for row in raw.itertuples()]}
    if cache_path:
        prepared = now if fixture else time.time_ns()
        if prepared >= first_signal:
            raise Pending("slow_cache_preparation_finished_after_target_signal; no backdated_cache_created")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("xb") as handle:
            raw.to_parquet(handle, index=False)
        prepared = now if fixture else time.time_ns()
        if prepared >= first_signal:
            cache_path.unlink()
            raise Pending("slow_cache_commit_finished_after_target_signal; no backdated_cache_created")
        receipt = {"identity": identity, "sha256": file_hash(cache_path), "audit": audit,
            "research_evidence": False, "forecasts_created": 0, "raw_market_data_is_synthetic": bool(fixture),
            "input_cutoff_ns": int(now), "prepared_at_ns": int(prepared),
            "prepared_at_utc": datetime.fromtimestamp(prepared / 1e9, timezone.utc).isoformat()}
        with receipt_path.open("xb") as handle:
            handle.write(canonical(receipt) + b"\n")
        if not fixture and time.time_ns() >= first_signal:
            receipt_path.unlink()
            cache_path.unlink()
            raise Pending("slow_cache_receipt_finished_after_target_signal; no backdated_cache_created")
        hashes[str(cache_path.resolve())] = receipt["sha256"]
        hashes[str(receipt_path.resolve())] = file_hash(receipt_path)
    return raw, audit, hashes, {"hit": False, "path": str(cache_path.resolve()) if cache_path else None,
                              "sha256": file_hash(cache_path) if cache_path else None,
                              "receipt_path": str(receipt_path.resolve()) if receipt_path else None,
                              "receipt_sha256": file_hash(receipt_path) if receipt_path else None}


class FeatureAdapter:
    def __init__(self, base, exchange, sources=None, slow_cache=None):
        self.base, self.exchange, self.sources = base, exchange, sources
        self.slow_cache = Path(slow_cache) if slow_cache else None
        # Calendar must cover all requested warmup slots, not just forecast days.
        self.time_to_base = {int(t): i for i, t in enumerate(base.times)}

    def expected_window(self, signal):
        times = self.exchange.signals
        i = int(np.searchsorted(times, signal))
        if i < 79 or i == len(times) or int(times[i]) != int(signal):
            raise Pending("verified_calendar_does_not_cover_complete80_or_signal")
        return np.array(times[i - 79:i + 1], copy=True)

    def readiness(self, signal):
        needed = self.expected_window(signal)
        new = [int(t) for t in needed if int(t) not in self.time_to_base]
        missing = sorted(set(local_date(t) for t in new))
        available_days = sorted(set(i["date"] for i in self.sources.spec.get("minutes", []))) if self.sources else []
        result = {"status": "pending_market_data" if new and not self.sources else "source_audit_required" if new else "historical_replay_only",
                "signal_time_ns": int(signal), "required_standard_hours": 80,
                "base_history_end": local_date(self.base.times[-1]),
                "new_hours_not_in_frozen_base": len(new), "new_session_dates": missing,
                "missing_warmup_session_dates": [d for d in missing if d < local_date(signal)],
                "sessions_without_new_minute_files": [d for d in missing if d not in available_days],
                "real_forecasts_created": 0, "research_evidence": False,
                "frozen_factor_count": len(self.base.selection["selected"]),
                "frozen_feature_count": len(self.base.meta["features"]),
                "future_session_clock_does_not_create_market_observations": True}
        evidence_path = ROOT / "gru_research/trading_data/future_minute_probe/sina_20261001/feature_source_readiness.json"
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text())
            result["actual_public_minute_probe_evidence"] = {"path": str(evidence_path.resolve()),
                "sha256": file_hash(evidence_path), "status": evidence["status"],
                "future_source_ready": evidence["future_source_ready"],
                "actual_dates": evidence["actual_dates"]}
        return result

    def target_memberships(self, signal, cutoff, fixture=False):
        if self.sources is None:
            raise Pending("real_incremental_source_manifest_not_available")
        if self.sources.spec.get("raw_market_data_is_synthetic") is not bool(fixture):
            raise IntegrityError("Synthetic incremental source cannot become real feature evidence")
        if self.base.meta.get("raw_market_data_is_synthetic") is not bool(fixture):
            raise IntegrityError("Historical feature base mode differs from genuine/fixture mode")
        needed = self.expected_window(signal)
        target_days = sorted(set(local_date(t) for t in needed if int(t) not in self.time_to_base))
        if not target_days:
            raise IntegrityError("Historical frozen dataset cannot become prospective evidence")
        membership = self.sources.membership()
        memberships = {}
        for day in target_days:
            group = membership[membership.date.eq(day)]
            if len(group) == 0:
                raise Pending("new_real_historical_membership_missing: " + day)
            if not fixture and not 498 <= len(group) <= 502:
                raise IntegrityError("Unverified new CSI500 historical membership count")
            if (group.available_at_ns > min(cutoff, local_ns(day, 10, 30))).any():
                raise Pending("historical_membership_availability_not_verified_before_first_signal: " + day)
            unknown = set(group.symbol) - set(self.base.symbols)
            if unknown:
                raise Pending("new_symbol_80hour_history_requires_explicit_universe_extension_audit: " + ",".join(sorted(unknown)))
            memberships[day] = group
        return membership, memberships, target_days

    def prime_slow(self, signal, observed_now=None, fixture=False):
        now = observed_now if fixture else time.time_ns()
        if now is None or now >= local_ns(local_date(signal), 10, 30):
            raise Pending("prime_slow_requires_actual_pre_signal_clock; no retrospective_cache_creation")
        if self.slow_cache is None:
            raise ValueError("prime-slow requires an explicit slow cache directory")
        membership, memberships, days = self.target_memberships(signal, now, fixture)
        _, audit, hashes, cache = prepare_slow_values(self.base, self.exchange, self.sources,
            membership, memberships, days, signal, now, self.slow_cache, fixture=fixture)
        return {"status": "known_slow_values_cached", "cache": cache, "audit": audit,
                "source_sha256": hashes, "target_signal_time_ns": int(signal),
                "minute_window_readiness": self.readiness(signal), "features_created": 0,
                "forecasts_created": 0, "research_evidence": False}

    def build(self, signal, contract, observed_now, fixture=False):
        if not fixture:
            observed_now = time.time_ns()
        self.base.check_model_freeze(contract)
        times = self.expected_window(signal)
        if signal <= contract.frozen_at or local_date(signal) < self.exchange.first_full_day(contract.frozen_at):
            raise IntegrityError("Only genuine post-freeze complete-session signals can be prepared")
        if observed_now < signal:
            raise Pending("real_post_freeze_signal_hour_not_completed_yet")
        if observed_now >= self.exchange.execution_time(signal):
            raise Pending("forecast_deadline_missed_no_retrospective_window_insertion")
        if self.sources is None:
            raise Pending("real_incremental_source_manifest_not_available")
        source_hashes = self.sources.hashes
        new_times = [int(t) for t in times if int(t) not in self.time_to_base]
        if not new_times:
            raise IntegrityError("Historical frozen dataset cannot become prospective evidence")
        membership, memberships, _ = self.target_memberships(signal, observed_now, fixture)
        Xbase, industrybase, completebase, memberbase, hourlybase = [self.base.verified_array(n) for n in
            ("X", "industry", "hour_complete", "members", "hour_ohlcv")]
        X = np.full((80, len(self.base.symbols), len(self.base.meta["features"])), np.nan, np.float32)
        industry = np.zeros(X.shape[:2], np.int64)
        complete = np.zeros(X.shape[:2], bool)
        availability = np.broadcast_to(times[:, None], X.shape[:2]).copy()
        for i, t in enumerate(times):
            if int(t) in self.time_to_base:
                b = self.time_to_base[int(t)]
                X[i], industry[i], complete[i] = Xbase[b], industrybase[b], completebase[b]
        new_raw, minute_audits = self.sources.minute_rows(signal)
        if len(new_raw) == 0:
            raise Pending("real_new_complete_adjusted_minute_rows_not_available")
        # Include80 earlier hours for same-slot20d volume, without borrowing later prices.
        start = int(np.searchsorted(self.base.times, new_times[0])) - 80
        start = max(start, 0)
        old_positions = np.arange(start, len(self.base.times))
        raw_history, historical_hashes = historical_hour_rows(self.base, start, len(self.base.times) - 1)
        source_hashes.update(historical_hashes)
        # The source must contain a genuine overlapping base hour for each stock.
        overlap_start = new_raw[new_raw.signal_time.isin(self.base.times)].signal_time.min()
        if pd.notna(overlap_start) and overlap_start < self.base.times[start]:
            overlap_position = int(np.searchsorted(self.base.times, overlap_start))
            extra_history, extra_hashes = historical_hour_rows(self.base, overlap_position, start - 1)
            raw_history = pd.concat([extra_history, raw_history], ignore_index=True)
            source_hashes.update(extra_hashes)
        exact_old = raw_history.set_index(["signal_time", "symbol"])
        new_raw, bridges = bridge_minute_prices(new_raw, self.base, self.base.times,
            self.base.symbols, hourlybase, completebase, exact_old)
        grid_start = local_date(self.base.times[start])
        days = [d for d in self.exchange.dates if grid_start <= d <= local_date(signal)]
        target_new_days = sorted(set(local_date(t) for t in new_times))
        hourly_new = {}
        for u, symbol in enumerate(self.base.symbols):
            old = raw_history[raw_history.symbol.eq(symbol) & raw_history.signal_time.ge(int(self.base.times[start]))]
            new = new_raw[new_raw.symbol.eq(symbol)]
            # Overlap only establishes the bridge; frozen historical rows remain immutable.
            new = new[~new.signal_time.isin(self.base.times)]
            if len(new) == 0:
                continue
            frame = pd.concat([old, new], ignore_index=True)
            calculated = feature_only_hours(frame, days).set_index("signal_time")
            hourly_new[symbol] = calculated
        for i, t in enumerate(times):
            if int(t) in self.time_to_base:
                continue
            for u, symbol in enumerate(self.base.symbols):
                calculated = hourly_new.get(symbol)
                if calculated is not None and int(t) in calculated.index:
                    complete[i, u] = bool(calculated.loc[int(t), "hour_complete"])
        current_members = np.isin(self.base.symbols, memberships[local_date(signal)].symbol)
        if int((current_members & complete.all(axis=0)).sum()) < int(contract.freeze["config"]["min_stocks"]):
            missing_slots = [{"signal_time_ns": int(t), "date": local_date(t),
                "incomplete_current_member_stocks": int((~complete[i] & current_members).sum())}
                for i, t in enumerate(times) if (~complete[i] & current_members).any()]
            raise Pending("real_complete80_member_count_below_minimum; no price gap filling: " + json.dumps(missing_slots))
        slow, slow_audit, slow_hashes, cache = prepare_slow_values(self.base, self.exchange,
            self.sources, membership, memberships, target_new_days, signal, observed_now,
            self.slow_cache, require_cache=not fixture, fixture=fixture)
        source_hashes.update(slow_hashes)
        daily_bridges = slow_audit["daily_adjustment_bridges_by_day"]
        slow_names, si = self.base.meta["features"][24:], {s: i for i, s in enumerate(self.base.symbols)}
        slow_by_day = {}
        for day in target_new_days:
            rows = slow[pd.to_datetime(slow.date).eq(pd.Timestamp(day))]
            values = np.full((len(self.base.symbols), len(slow_names)), np.nan, np.float32)
            positions = rows.symbol.map(si).to_numpy(dtype=np.int64)
            values[positions] = rows[slow_names].to_numpy(dtype=np.float32)
            pool = np.isin(self.base.symbols, memberships[day].symbol)
            slow_by_day[day] = normalize_cross_section(values, pool)[0]
        for i, t in enumerate(times):
            if int(t) in self.time_to_base:
                continue
            day = local_date(t)
            pool = np.isin(self.base.symbols, memberships[day].symbol)
            values = np.full((len(self.base.symbols), 24), np.nan, np.float32)
            for symbol, calculated in hourly_new.items():
                if int(t) not in calculated.index:
                    continue
                row, u = calculated.loc[int(t)], si[symbol]
                values[u] = row[HOUR_FEATURES].to_numpy(dtype=np.float32)
                complete[i, u] = bool(row.hour_complete)
                original = new_raw[new_raw.symbol.eq(symbol) & new_raw.signal_time.eq(int(t))]
                if len(original):
                    availability[i, u] = int(original.source_available_at_ns.iloc[0])
            X[i, :, :24] = normalize_cross_section(values, pool)[0]
            X[i, :, 24:] = slow_by_day[day]
        members = np.isin(self.base.symbols, memberships[local_date(signal)].symbol)
        eligible = members & complete.all(axis=0)
        audit = {"passed": True, "same_frozen_feature_names_and_formulas": True,
            "factor_selection_rerun": False, "historical_rows_unchanged": True,
            "true_historical_membership_normalization": True,
            "consecutive_official_hours": 80, "member_stocks": int(members.sum()),
            "complete80_member_stocks": int(eligible.sum()), "minute_audits": minute_audits,
            "adjustment_bridges": bridges, "daily_adjustment_bridges_by_day": daily_bridges,
            "daily_audit": slow_audit["daily_audit"], "slow_cache": cache,
            "weekly_cap_policy": slow_audit["weekly_cap_policy"],
            "market_version_audit": slow_audit["market_version_audit"],
            "factor_missing_input_policy": "Exact frozen DSL; no replacement prices; native EMA state may remain finite from known earlier observations",
            "missing_hour_stock_counts": [(~complete[i] & members).sum().item() for i in range(80)],
            "missing_feature_member_counts": np.isnan(X[:, members]).sum(axis=(0, 1)).tolist(),
            "slow_query_dates": {day: {"financial": sorted(set(slow.loc[pd.to_datetime(slow.date).eq(pd.Timestamp(day)), "event_query_date"].dropna().astype(str))),
                "market": sorted(set(slow.loc[pd.to_datetime(slow.date).eq(pd.Timestamp(day)), "market_query_date"].dropna().astype(str)))} for day in target_new_days}}
        if int(eligible.sum()) < int(contract.freeze["config"]["min_stocks"]):
            raise Pending("real_complete80_member_count_below_minimum; missing hours remain explicit: " + json.dumps(audit["missing_hour_stock_counts"]))
        source_hashes[str((self.base.dataset / "meta.json").resolve())] = file_hash(self.base.dataset / "meta.json")
        if not fixture:
            observed_now = time.time_ns()
            if observed_now >= self.exchange.execution_time(signal):
                raise Pending("real_feature_preparation_missed_execution_deadline; no_forecast_created")
        metadata = {"symbols": self.base.symbols, "feature_names": self.base.meta["features"],
            "factor_selection_sha256": self.base.meta["factor_selection_sha256"], "normalization": self.base.meta["normalization"],
            "raw_market_data_is_synthetic": bool(fixture), "point_in_time_audit_passed": True,
            "source_hashes": source_hashes, "captured_at_utc": datetime.fromtimestamp(observed_now / 1e9, timezone.utc).isoformat(),
            "earliest_execution_time_ns": self.exchange.execution_time(signal),
            "adapter_source_sha256": file_hash(__file__), "adapter_audit": audit,
            "frozen_operator_sha256": {str(Path(__file__).with_name(name).resolve()): file_hash(Path(__file__).with_name(name))
                for name in ("minute_data.py", "slow_features.py", "assemble.py")},
            "baseline_dataset_fingerprint": self.base.meta["data_fingerprint"]}
        metadata["frozen_operator_sha256"].update({str(p.resolve()): file_hash(p) for p in legacy_source_paths()})
        window = FeatureWindow(X, industry, members, complete, times, availability, self.base.symbols, metadata)
        window.validate(contract, self.exchange, observed_now, fixture)
        return window


def write_window(window, output):
    if not window.metadata["raw_market_data_is_synthetic"] and time.time_ns() >= window.metadata["earliest_execution_time_ns"]:
        raise Pending("feature_snapshot_deadline_missed; no_retrospective_forecast_created")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{window.signal:020d}.npz"
    manifest = output / f"{window.signal:020d}.json"
    if path.exists() or manifest.exists():
        raise FileExistsError("Existing audited feature snapshot cannot be overwritten")
    binding = {k: window.metadata[k] for k in ("baseline_dataset_fingerprint", "feature_names", "normalization",
               "factor_selection_sha256", "adapter_source_sha256", "frozen_operator_sha256")}
    binding_path = output / "adapter_binding.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            raise IntegrityError("Audited live feature scheme/code changed after its immutable binding")
    else:
        with binding_path.open("xb") as handle:
            handle.write(canonical(binding) + b"\n")
    with path.open("xb") as handle:
        np.savez_compressed(handle, X=window.X, industry=window.industry, members=window.members,
            hour_complete=window.hour_complete, times=window.times, source_available_times=window.source_available_times)
    data = {**window.metadata, "adapter_binding_sha256": file_hash(binding_path),
            "snapshot_path": str(path.resolve()), "snapshot_file_sha256": file_hash(path)}
    with manifest.open("xb") as handle:
        handle.write(canonical(data) + b"\n")
    return {"status": "audited_features_ready", "path": str(path.resolve()), "sha256": file_hash(path),
            "manifest_path": str(manifest.resolve()), "manifest_sha256": file_hash(manifest), "forecasts_created": 0}


def replay_hour_audit(base, exchange, day):
    """Actual historical source calculation parity, deliberately not a forecast."""
    raw_path = ROOT / "gru_research/artifacts/minute_hourly_raw" / (day + ".parquet")
    if not raw_path.is_file():
        raise Pending("audited_historical_hour_source_not_available")
    positions = np.flatnonzero(np.array([local_date(t) == day for t in base.times]))
    if len(positions) != 4:
        raise IntegrityError("Replay day absent from frozen base")
    members, X = [base.verified_array(n) for n in ("members", "X")]
    raw = pd.read_parquet(raw_path)
    start = max(int(positions[0]) - 80, 0)
    old_positions = np.arange(start, positions[0])
    raw_history, raw_hashes = historical_hour_rows(base, start, int(positions[0]) - 1)
    days = [d for d in exchange.dates if local_date(base.times[start]) <= d <= day]
    values = np.full((4, len(base.symbols), 24), np.nan, np.float32)
    for u, symbol in enumerate(base.symbols):
        stock = raw[raw.symbol.eq(symbol)]
        if len(stock) == 0:
            continue
        old = raw_history[raw_history.symbol.eq(symbol)]
        calculated = feature_only_hours(pd.concat([old, stock], ignore_index=True), days).set_index("signal_time")
        values[:, u] = calculated.loc[np.asarray(base.times[positions]), HOUR_FEATURES].to_numpy(dtype=np.float32)
    bad, compared, max_diff = 0, 0, 0.0
    for slot, pos in enumerate(positions):
        actual = normalize_cross_section(values[slot], members[pos])[0]
        expected = np.asarray(X[pos, :, :24])
        selected = np.asarray(members[pos])[:, None]
        finite = np.isfinite(expected) & selected
        mismatches = selected & ((np.isfinite(actual) != np.isfinite(expected)) | (np.isfinite(expected) & ~np.isclose(actual, expected, rtol=2e-5, atol=2e-6)))
        bad += int(mismatches.sum())
        compared += int(finite.sum())
        if finite.any():
            max_diff = max(max_diff, float(np.abs(actual[finite] - expected[finite]).max()))
    if bad or compared == 0:
        raise IntegrityError(f"Real historical h24 parity failed: {bad}/{compared}")
    return {"status": "engineering_replay_verified", "research_evidence": False,
        "day": day, "hour_feature_fields": 24, "finite_member_values_compared": compared,
        "mismatches": bad, "maximum_absolute_difference": max_diff,
        "real_hour_source_path": str(raw_path.resolve()), "real_hour_source_sha256": file_hash(raw_path),
        "warmup_hour_source_sha256": raw_hashes,
        "no_label_files_read": True, "forecasts_created": 0}


def replay_slow_audit(base, exchange, day):
    """Real98-field replay includes all frozen64 formulas; no factor selection."""
    position = base.times.tolist().index(local_ns(day, 10, 30))
    pool = np.asarray(base.verified_array("members")[position])
    target = pd.DataFrame({"date": day, "symbol": np.array(base.symbols)[pool]})
    daily, source_audit = read_daily(root=DAILY_ROOT, history_start="2010-01-01", end=day, symbols=base.symbols)
    financial = pd.read_parquet(FUND_ROOT / "financial_verified_19_fields.parquet")
    market = pd.read_parquet(FUND_ROOT / "weekly_market_and_industry.parquet")
    raw = slow_rows(target, daily, financial, market, full_daily_calendar(exchange, day), base.symbols,
                    base.selection["selected"])
    names = base.meta["features"][24:]
    u = {s: i for i, s in enumerate(base.symbols)}
    matrix = np.full((len(base.symbols), len(names)), np.nan, np.float32)
    matrix[raw.symbol.map(u).to_numpy(dtype=np.int64)] = raw[names].to_numpy(dtype=np.float32)
    actual = normalize_cross_section(matrix, pool)[0]
    expected = np.asarray(base.verified_array("X")[position, :, 24:])
    finite = np.isfinite(expected) & pool[:, None]
    mismatch = pool[:, None] & ((np.isfinite(actual) != np.isfinite(expected)) |
                (np.isfinite(expected) & ~np.isclose(actual, expected, rtol=2e-5, atol=2e-6)))
    by_field = {n: int(c) for n, c in zip(names, mismatch.sum(axis=0)) if c}
    if mismatch.any() or not finite.any():
        raise IntegrityError("Real slow replay differs from frozen training input: " + json.dumps(by_field))
    return {"status": "engineering_replay_verified", "day": day, "research_evidence": False,
            "slow_fields": len(names), "fixed_formula_count": len(base.selection["selected"]),
            "finite_member_values_compared": int(finite.sum()), "mismatches": int(mismatch.sum()),
            "maximum_absolute_difference": float(np.abs(actual[finite] - expected[finite]).max()),
            "actual_daily_source_audit": source_audit, "factor_selection_rerun": False,
            "no_label_files_read": True, "forecasts_created": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("contract", "status", "prime-slow", "prepare", "replay-audit"))
    parser.add_argument("--dataset", type=Path, default=ROOT / "gru_research/artifacts/dataset")
    parser.add_argument("--selection", type=Path, default=ROOT / "gru_research/artifacts/slow_factor_selection.json")
    parser.add_argument("--calendar", type=Path, default=ROOT / "gru_research/artifacts/official_market_calendar_2026.json")
    parser.add_argument("--slow-cache", type=Path, default=ROOT / "gru_research/artifacts/prospective_feature_cache")
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--signal", help="Offset-aware ISO timestamp or UTC-ns integer; prime-slow must finish before that session's 10:30")
    parser.add_argument("--day", default="2026-09-23", help="Historical engineering replay day, never forward evidence")
    parser.add_argument("--replay-groups", choices=("hour", "all"), default="hour")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "contract":
        result = SOURCE_CONTRACT
    else:
        adapter, signal = None, None
        try:
            base = FrozenFeatures.read(args.dataset, args.selection)
            exchange = ExchangeCalendar.read(args.calendar)
            if args.action == "replay-audit":
                result = replay_hour_audit(base, exchange, args.day)
                if args.replay_groups == "all":
                    result = {"status": "engineering_replay_verified", "research_evidence": False,
                              "hour": result, "slow": replay_slow_audit(base, exchange, args.day)}
            else:
                if not args.signal:
                    raise ValueError("An explicit signal clock is required; calendar alone cannot create a forecast")
                signal = utc_ns(int(args.signal) if args.signal.isdigit() else args.signal)
                adapter = FeatureAdapter(base, exchange, slow_cache=args.slow_cache)
                sources = IncrementalSources.read(args.sources) if args.sources else None
                adapter.sources = sources
                if args.action == "status":
                    result = adapter.readiness(signal)
                elif args.action == "prime-slow":
                    result = adapter.prime_slow(signal)
                else:
                    if args.freeze is None or args.output is None:
                        raise ValueError("prepare requires the final --freeze and explicit --output")
                    contract = FrozenContract.read(args.freeze, args.dataset / "meta.json")
                    window = adapter.build(signal, contract, utc_ns(datetime.now(timezone.utc).isoformat()))
                    result = write_window(window, args.output)
        except Pending as exc:
            result = {"status": "pending_market_data", "reason": str(exc), "features_created": 0,
                      "forecasts_created": 0, "research_evidence": False}
            if adapter is not None and signal is not None:
                result["readiness"] = adapter.readiness(signal)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
