"""Append-only, genuinely prospective GRU predictions; never places trades.

The live data adapter supplies a features-only audited 80-hour NPZ and JSON
manifest. It reuses the frozen feature list/formulas/PIT rules and never reranks
factors. This module rejects historical replay, future source arrival, late
forecast insertion, changed model/schema, and outcomes without prior forecasts.

Nothing is scheduled or fetched automatically. ``status`` is read-only and is
pending until the final training freeze, verified exchange calendar and real new
market features exist. ``--output`` explicitly writes a derived status manifest;
forecast/outcome records remain immutable. Fixtures are not research evidence.

Live adapter boundary: NPZ contains only X(float32[80,U,F]),
industry(int64[80,U]), members(bool[U]), hour_complete(bool[80,U]),
times(int64 UTC-ns[80]), and source_available_times(int64 UTC-ns[80,U]).
Its JSON manifest supplies symbols, exact frozen feature_names, normalization,
factor_selection_sha256, raw_market_data_is_synthetic=False,
point_in_time_audit_passed=True, source_hashes, captured_at_utc, and
earliest_execution_time_ns. Source availability bounds refer to each historical
row, not today's retrieval time. It must retain missing prices as NaN and mark
missing standard hours incomplete, use historically visible membership for
normalization, and recompute ages when carrying a prior financial version.
The input calendar must also cover warmup and future execution/label sessions.
These declarations require the adapter's actual audit receipts; this module
does not claim an unavailable live data adapter has already passed an audit.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, time as clock_time, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import numpy as np

from .evaluate import correlation, ranks, breakdown

CHINA = ZoneInfo("Asia/Shanghai")
MINUTE_NS = 60_000_000_000
HOUR_ENDS = ((10, 30), (11, 30), (14, 0), (15, 0))
SCORE_COLUMNS = ("symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag")


class Pending(RuntimeError):
    """Required real evidence has not arrived; no forecast is created."""


class IntegrityError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_ns(value):
    if isinstance(value, (int, np.integer)):
        return int(value)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise IntegrityError("UTC/offset-aware timestamp required")
    return int(dt.timestamp() * 1_000_000_000)


def local_ns(date, hour, minute):
    return utc_ns(datetime.combine(datetime.fromisoformat(date).date(), clock_time(hour, minute), CHINA).isoformat())


def local_date(value):
    return datetime.fromtimestamp(int(value) / 1e9, CHINA).date().isoformat()


def groups_from_meta(meta):
    aliases = {"hour": "hour24", "daily": "daily16", "factors": "factors", "fund": "fund16", "ages": "ages2"}
    result = {name: list(meta["groups"].get(name, meta["groups"].get(alias, []))) for name, alias in aliases.items()}
    if any(len(result[name]) != length for name, length in (("hour", 24), ("daily", 16), ("fund", 16), ("ages", 2))):
        raise IntegrityError("Frozen PLAN feature groups are incomplete")
    if len(result["factors"]) > 64 or sorted(sum(result.values(), [])) != list(range(len(meta["features"]))):
        raise IntegrityError("Frozen groups must partition the exact feature list")
    return result


@dataclass(frozen=True)
class ExchangeCalendar:
    dates: tuple[str, ...]
    source: str
    coverage_start: str
    coverage_end: str
    synthetic: bool = False
    evidence: dict | None = None

    @classmethod
    def from_dict(cls, data):
        if data.get("point_in_time_verified") is not True:
            raise IntegrityError("A verified complete exchange-session calendar is required")
        dates = tuple(data["dates"])
        if not dates or list(dates) != sorted(set(dates)):
            raise IntegrityError("Calendar dates must be nonempty, unique and sorted")
        start, end = data["coverage_start"], data["coverage_end"]
        if start > dates[0] or end < dates[-1] or not data.get("source"):
            raise IntegrityError("Calendar must declare its verified coverage and source")
        for date in dates:
            datetime.fromisoformat(date)
        evidence = data.get("official_evidence") or {key: data[key] for key in ("source_urls", "verified_at_utc", "basis") if key in data}
        return cls(dates, data["source"], start, end, bool(data.get("synthetic", False)), evidence)

    @classmethod
    def read(cls, path):
        path = Path(path)
        if not path.exists():
            raise Pending("verified_live_exchange_calendar_not_available")
        value = cls.from_dict(json.loads(path.read_text()))
        return replace(value, evidence={**(value.evidence or {}), "path": str(path.resolve()), "sha256": file_hash(path)})

    def as_dict(self):
        return {"dates": list(self.dates), "source": self.source, "coverage_start": self.coverage_start,
                "coverage_end": self.coverage_end, "synthetic": self.synthetic, "point_in_time_verified": True,
                "official_evidence": self.evidence or {}}

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical(self.as_dict())).hexdigest()

    @property
    def signals(self):
        return np.array([local_ns(day, hour, minute) for day in self.dates for hour, minute in HOUR_ENDS], dtype=np.int64)

    def first_full_day(self, frozen_at):
        if self.coverage_start > local_date(frozen_at):
            raise Pending("calendar_does_not_cover_freeze_date")
        for day in self.dates:
            if local_ns(day, 9, 30) > frozen_at:
                return day
        raise Pending("verified_calendar_has_no_complete_session_after_final_freeze")

    def execution_time(self, signal):
        slots = self.signals
        position = int(np.searchsorted(slots, signal))
        if position == len(slots) or int(slots[position]) != signal:
            raise IntegrityError("Signal is not a verified standard exchange hour end")
        day, slot = self.dates[position // 4], position % 4
        if slot == 0:
            return local_ns(day, 10, 31)
        if slot == 1:
            return local_ns(day, 13, 1)
        if slot == 2:
            return local_ns(day, 14, 1)
        if position // 4 + 1 >= len(self.dates):
            raise Pending("next_exchange_session_for_execution_not_yet_verified")
        return local_ns(self.dates[position // 4 + 1], 9, 31)

    def y4_end(self, signal):
        slots = self.signals
        position = int(np.searchsorted(slots, signal))
        if position == len(slots) or int(slots[position]) != signal:
            raise IntegrityError("Unknown signal")
        if position + 4 >= len(slots):
            raise Pending("future_y4_exit_session_not_yet_verified")
        return self.execution_time(int(slots[position + 4])) + 5 * MINUTE_NS


@dataclass(frozen=True)
class FrozenContract:
    freeze: dict
    meta: dict
    freeze_hash: str
    meta_hash: str
    fixture: bool = False

    @classmethod
    def read(cls, freeze_path, meta_path, fixture=False):
        freeze_path, meta_path = Path(freeze_path), Path(meta_path)
        if not freeze_path.exists():
            raise Pending("final_model_freeze_json_not_available_at_requested_path")
        if not meta_path.exists():
            raise Pending("frozen_training_feature_metadata_not_available")
        freeze, meta = json.loads(freeze_path.read_text()), json.loads(meta_path.read_text())
        for field in ("freeze_id", "frozen_at_utc", "config", "evaluation_models", "checkpoint_hashes", "dataset_fingerprint"):
            if field not in freeze:
                raise IntegrityError("Incomplete final freeze: " + field)
        fingerprint = hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if fingerprint != freeze["dataset_fingerprint"]:
            raise IntegrityError("Training metadata differs from the final model freeze")
        groups_from_meta(meta)
        if meta.get("raw_market_data_is_synthetic") is not bool(fixture) or meta.get("causality_audit", {}).get("passed") is not True:
            raise IntegrityError("Final freeze must originate from audited real training data")
        if not meta.get("factor_selection_sha256") or len(set(meta["features"])) != len(meta["features"]):
            raise IntegrityError("Frozen feature identities/selection hash are required")
        if freeze["config"].get("sequence_length") != 80 or freeze["config"].get("dtype") != "float32":
            raise IntegrityError("Prospective inference must use the frozen 80-hour FP32 scheme")
        if not fixture and int(freeze["config"].get("min_stocks", 0)) < 100:
            raise IntegrityError("Real PLAN prospective cross sections require at least100 stocks")
        paths = freeze["evaluation_models"].get("selected_full", [])
        if len(paths) != len(freeze["config"]["seeds"]) or not paths or any(p not in freeze["checkpoint_hashes"] for p in paths):
            raise IntegrityError("All frozen selected-full seed checkpoints must be bound")
        identity = hashlib.sha256(json.dumps({"data": freeze["dataset_fingerprint"], "config": freeze["config"],
                    "selection": freeze["selection"], "hashes": freeze["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
        if identity != freeze["freeze_id"] or freeze.get("no_test_driven_tuning") is not True:
            raise IntegrityError("Final model/config/selection identity failed verification")
        return cls(freeze, meta, file_hash(freeze_path), file_hash(meta_path), fixture)

    @property
    def frozen_at(self):
        return utc_ns(self.freeze["frozen_at_utc"])

    @property
    def model_version(self):
        return self.freeze["freeze_id"] + ":selected_full"

    def binding(self, calendar, fixture):
        return {"freeze_sha256": self.freeze_hash, "training_meta_sha256": self.meta_hash,
                "model_version": self.model_version, "features": self.meta["features"],
                "factor_selection_sha256": self.meta["factor_selection_sha256"],
                "checkpoint_hashes": {p: self.freeze["checkpoint_hashes"][p] for p in self.freeze["evaluation_models"]["selected_full"]},
                "prospective_code_sha256": file_hash(__file__), "frozen_at_ns": self.frozen_at,
                "start_trading_day": calendar.first_full_day(self.frozen_at), "target_trading_days": 60,
                "mode": "fixture_not_research_evidence" if fixture else "genuine_prospective_no_trading"}


@dataclass
class FeatureWindow:
    X: np.ndarray
    industry: np.ndarray
    members: np.ndarray
    hour_complete: np.ndarray
    times: np.ndarray
    source_available_times: np.ndarray
    symbols: list[str]
    metadata: dict

    @classmethod
    def read(cls, npz_path, manifest_path):
        meta = json.loads(Path(manifest_path).read_text())
        with np.load(npz_path, allow_pickle=False) as arrays:
            required = {"X", "industry", "members", "hour_complete", "times", "source_available_times"}
            if set(arrays.files) != required:
                raise IntegrityError("Forecast NPZ accepts only feature arrays; label/price-target arrays are forbidden")
            data = {name: np.array(arrays[name], copy=True) for name in required}
        meta["snapshot_file_sha256"] = file_hash(npz_path)
        meta["snapshot_manifest_sha256"] = file_hash(manifest_path)
        return cls(**data, symbols=meta["symbols"], metadata=meta)

    @property
    def signal(self):
        return int(self.times[-1])

    def validate(self, contract, calendar, observed_now, fixture):
        meta = self.metadata
        if meta.get("raw_market_data_is_synthetic") is not bool(fixture):
            raise IntegrityError("Synthetic fixtures cannot enter genuine prospective evidence")
        if meta.get("point_in_time_audit_passed") is not True or not meta.get("source_hashes"):
            raise IntegrityError("Live adapter must provide causal audit and immutable source receipts")
        if meta.get("feature_names") != contract.meta["features"] or meta.get("factor_selection_sha256") != contract.meta["factor_selection_sha256"]:
            raise IntegrityError("Live features differ from the frozen list/selection")
        if meta.get("normalization") != contract.meta["normalization"]:
            raise IntegrityError("Live normalization differs from the frozen transform")
        if self.X.dtype != np.float32 or self.X.shape != (80, len(self.symbols), len(contract.meta["features"])):
            raise IntegrityError("Live X must be FP32 [80,universe,frozen_fields]")
        if len(self.symbols) != len(set(self.symbols)) or not self.symbols:
            raise IntegrityError("Live symbols must be unique and nonempty")
        for name in ("industry", "hour_complete", "source_available_times"):
            if getattr(self, name).shape != self.X.shape[:2]:
                raise IntegrityError(name + " shape differs from historical window")
        if self.industry.dtype != np.int64:
            raise IntegrityError("Frozen industry category indices must be int64")
        if self.members.dtype != np.bool_ or self.members.shape != (len(self.symbols),) or self.hour_complete.dtype != np.bool_:
            raise IntegrityError("True current membership and historical completeness masks are required")
        if self.times.dtype != np.int64 or self.times.shape != (80,) or self.source_available_times.dtype != np.int64:
            raise IntegrityError("Live times/arrival bounds must be UTC ns int64")
        expected = calendar.signals
        index = int(np.searchsorted(expected, self.signal))
        if index < 79 or index == len(expected) or not np.array_equal(self.times, expected[index - 79:index + 1]):
            raise IntegrityError("Live history must contain exactly 80 consecutive verified standard hours")
        if np.any(self.source_available_times > self.times[:, None]) or np.any(self.source_available_times <= 0):
            raise IntegrityError("A historical feature uses unavailable/future source information")
        if np.any(self.industry < 0) or np.any(self.industry >= int(contract.meta["n_industries"])):
            raise IntegrityError("Unverified new industry mappings must use frozen unknown category0")
        if np.any(np.isfinite(self.X) & (np.abs(self.X) > 5.00001)):
            raise IntegrityError("Live continuous values violate the frozen +/-5 transform")
        if np.isinf(self.X).any():
            raise IntegrityError("Live missing features must remain NaN, never infinity")
        start = calendar.first_full_day(contract.frozen_at)
        if self.signal <= contract.frozen_at or local_date(self.signal) < start:
            raise IntegrityError("Historical/partial-freeze-day signals are not prospective")
        captured = utc_ns(meta["captured_at_utc"])
        execution = calendar.execution_time(self.signal)
        if captured < self.signal or captured > observed_now or observed_now < self.signal:
            raise IntegrityError("A real completed signal and current snapshot timestamp are required")
        if observed_now >= execution:
            raise IntegrityError("Forecast insertion after its first execution window is forbidden; audit a gap")
        if int(meta["earliest_execution_time_ns"]) != execution:
            raise IntegrityError("Incorrect frozen execution-window clock")
        return self.members & self.hour_complete.all(axis=0)


class FrozenEnsemble:
    """Only feature tensors are exposed to the frozen five-seed scoring path."""
    def __init__(self, contract, device="cuda", checkpoint_map=None):
        if contract.fixture:
            raise IntegrityError("Fixture contract is not a production model")
        try:
            import torch
            from .model import PlanGRU
        except ImportError as exc:
            raise Pending("frozen_inference_runtime_not_available; no dependency installation attempted") from exc
        self.contract, self.torch, self.device = contract, torch, device
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise Pending("configured_inference_GPU_not_available")
        self.models = []
        seen = []
        for frozen_path in contract.freeze["evaluation_models"]["selected_full"]:
            path = Path((checkpoint_map or {}).get(frozen_path, frozen_path))
            if not path.exists():
                raise Pending("frozen_selected_checkpoint_not_available: " + str(path))
            if file_hash(path) != contract.freeze["checkpoint_hashes"][frozen_path]:
                raise IntegrityError("Frozen model checkpoint was altered")
            state = torch.load(path, map_location=device, weights_only=False)
            signature = state["signature"]
            if signature["dataset_fingerprint"] != contract.freeze["dataset_fingerprint"] or signature["config"] != contract.freeze["config"] or signature["variant"] != "full":
                raise IntegrityError("Checkpoint training identity differs from the final freeze")
            if state["groups"] != groups_from_meta(contract.meta) or state["n_industries"] != contract.meta["n_industries"]:
                raise IntegrityError("Checkpoint feature schema changed")
            seen.append(int(signature["seed"]))
            model = PlanGRU(state["groups"], state["n_industries"], "full").to(device)
            model.load_state_dict(state["model"])
            self.models.append(model.eval())
        if sorted(seen) != sorted(contract.freeze["config"]["seeds"]):
            raise IntegrityError("Frozen seed ensemble is incomplete/duplicated")

    def score(self, window, mask):
        torch = self.torch
        x = torch.from_numpy(np.array(window.X[:, mask].transpose(1, 0, 2), copy=True)).to(self.device)
        industry = torch.from_numpy(np.array(window.industry[:, mask].T, dtype=np.int64, copy=True)).to(self.device)
        slot_map = {10: 0, 11: 1, 14: 2, 15: 3}
        slot = np.array([slot_map[datetime.fromtimestamp(int(t) / 1e9, CHINA).hour] for t in window.times])
        slots = torch.from_numpy(np.broadcast_to(slot, (int(mask.sum()), 80)).copy()).to(self.device)
        scores = []
        with torch.inference_mode():
            for model in self.models:
                value = model(x, industry, slots).cpu().numpy().astype(np.float64)
                if not np.isfinite(value).all():
                    raise IntegrityError("Nonfinite frozen model score")
                std = value.std(ddof=0)
                scores.append((value - value.mean()) / std if std > 0 else np.zeros_like(value))
        return np.mean(scores, axis=0)


def exclusive_json(path, payload):
    """O_EXCL means a previously committed forecast/outcome cannot be replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def score_csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SCORE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def exclusive_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


class ProspectiveLedger:
    def __init__(self, path, contract, calendar, fixture=False):
        if calendar.synthetic is not bool(fixture) or contract.fixture is not bool(fixture):
            raise IntegrityError("Fixture calendar cannot enter genuine evidence")
        self.path, self.contract, self.calendar, self.fixture = Path(path), contract, calendar, fixture
        self.binding = contract.binding(calendar, fixture)

    def now(self, fixture_clock=None):
        if fixture_clock is not None:
            if not self.fixture:
                raise IntegrityError("Production wall clock cannot be supplied/backdated")
            return int(fixture_clock)
        return time.time_ns()

    @contextmanager
    def locked(self):
        self.path.mkdir(parents=True, exist_ok=True)
        with (self.path / ".append.lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def records(self):
        header = self.path / "header.json"
        if header.exists() and json.loads(header.read_text()) != self.binding:
            raise IntegrityError("Frozen ledger binding was altered; no model/code/schema changes allowed")
        result, previous = [], hashlib.sha256(canonical(self.binding)).hexdigest()
        for path in sorted((self.path / "forecasts").glob("*.json")):
            record = json.loads(path.read_text())
            payload = record["payload"]
            digest = hashlib.sha256(canonical(payload)).hexdigest()
            if record["sha256"] != digest or payload["previous_record_sha256"] != previous:
                raise IntegrityError("Forecast ledger hash chain is broken")
            if payload["binding_sha256"] != hashlib.sha256(canonical(self.binding)).hexdigest():
                raise IntegrityError("Forecast record was bound to another frozen scheme")
            if int(payload["recorded_at_ns"]) >= int(payload["earliest_execution_time_ns"]):
                raise IntegrityError("Ledger contains a retrospective forecast")
            csv_path = self.path / "forecasts" / f"{int(payload['signal_time_ns']):020d}.csv"
            csv_bytes = score_csv_bytes(payload["forecasts"])
            if (not csv_path.is_file() or csv_path.read_bytes() != csv_bytes
                    or payload["forecast_csv_sha256"] != hashlib.sha256(csv_bytes).hexdigest()):
                raise IntegrityError("Immutable actual forecast CSV differs from its committed record")
            previous = digest
            result.append(record)
        return result

    def verify_calendar_extension(self):
        for path in (self.path / "calendars").glob("*.json"):
            old = ExchangeCalendar.from_dict(json.loads(path.read_text()))
            if old.source != self.calendar.source or self.calendar.coverage_start > old.coverage_start or self.calendar.coverage_end < old.coverage_end:
                raise IntegrityError("Calendar source/verified coverage cannot shrink or change")
            overlap = tuple(d for d in self.calendar.dates if old.coverage_start <= d <= old.coverage_end)
            if overlap != old.dates:
                raise IntegrityError("A previously verified trading calendar was revised; audit explicitly")

    def append_forecast(self, window, forecaster=None, fixture_scores=None, fixture_clock=None):
        observed = self.now(fixture_clock)
        mask = window.validate(self.contract, self.calendar, observed, self.fixture)
        if fixture_scores is not None and not self.fixture:
            raise IntegrityError("Fixture scores cannot enter genuine evidence")
        minimum = int(self.contract.freeze["config"]["min_stocks"])
        if int(mask.sum()) >= minimum:
            if fixture_scores is not None:
                score = np.asarray(fixture_scores, dtype=np.float64)
            else:
                forecaster = forecaster or FrozenEnsemble(self.contract)
                if forecaster.contract.freeze_hash != self.contract.freeze_hash:
                    raise IntegrityError("Scorer does not match the frozen ledger")
                score = forecaster.score(window, mask)
            if score.shape != (int(mask.sum()),) or not np.isfinite(score).all():
                raise IntegrityError("Scores must match the complete eligible current cross section")
        else:
            # Entire cross section is retained as an explicit unscoreable record.
            score = np.array([], dtype=np.float64)
            mask = np.zeros_like(mask)
        percentiles = (ranks(score) - .5) / len(score) if len(score) else np.array([])
        scored = {int(u): j for j, u in enumerate(np.flatnonzero(mask))}
        rows = []
        for u in np.flatnonzero(window.members):
            flags = []
            if u not in scored:
                flags.append("below_min_cross_section" if window.hour_complete[:, u].all() else "incomplete_hour_window")
            if np.isnan(window.X[:, u]).any():
                flags.append("feature_missing")
            if window.industry[-1, u] == 0:
                flags.append("industry_unknown")
            j = scored.get(int(u))
            rows.append({"symbol": window.symbols[u], "signal_time": window.signal,
                         "earliest_execution_time": self.calendar.execution_time(window.signal),
                         "score": float(score[j]) if j is not None else None,
                         "percentile": float(percentiles[j]) if j is not None else None,
                         "model_version": self.contract.model_version, "data_quality_flag": "|".join(flags) or "ok"})
        with self.locked():
            previous_records = self.records()
            if previous_records and window.signal <= previous_records[-1]["payload"]["signal_time_ns"]:
                raise IntegrityError("Forecast append order is immutable; past gaps cannot be backfilled")
            self.verify_calendar_extension()
            if not (self.path / "header.json").exists():
                exclusive_json(self.path / "header.json", self.binding)
            calendar_path = self.path / "calendars" / (self.calendar.fingerprint + ".json")
            if not calendar_path.exists():
                exclusive_json(calendar_path, self.calendar.as_dict())
            committed = self.now(fixture_clock)
            if committed >= self.calendar.execution_time(window.signal):
                raise IntegrityError("Inference missed the execution deadline; cannot insert a late forecast")
            binding_hash = hashlib.sha256(canonical(self.binding)).hexdigest()
            payload = {"binding_sha256": binding_hash, "previous_record_sha256": previous_records[-1]["sha256"] if previous_records else binding_hash,
                       "signal_time_ns": window.signal, "earliest_execution_time_ns": self.calendar.execution_time(window.signal),
                       "recorded_at_ns": committed, "calendar_sha256": self.calendar.fingerprint,
                       "snapshot_manifest": window.metadata, "pool_count": int(window.members.sum()), "scored_count": len(score), "forecasts": rows}
            csv_bytes = score_csv_bytes(rows)
            payload["forecast_csv_sha256"] = hashlib.sha256(csv_bytes).hexdigest()
            record = {"payload": payload, "sha256": hashlib.sha256(canonical(payload)).hexdigest()}
            exclusive_bytes(self.path / "forecasts" / f"{window.signal:020d}.csv", csv_bytes)
            if self.now(fixture_clock) >= self.calendar.execution_time(window.signal):
                raise IntegrityError("Forecast commit missed the deadline; orphan CSV is not a recorded forecast")
            exclusive_json(self.path / "forecasts" / (f"{window.signal:020d}.json"), record)
        return record

    def append_outcome(self, signal, symbols, entry_prices, exit_prices, label_end_ns, source,
                       source_available_ns, synthetic=False, fixture_clock=None):
        """Observe only matured real execution-price windows, after prior prediction."""
        observed = self.now(fixture_clock)
        if (synthetic is not bool(self.fixture) or not source or not source.get("hashes")
                or source.get("price_policy") != "five_complete_minute_qfq_open_TWAP"):
            raise IntegrityError("Outcome requires genuine price-source receipts (fixtures stay separate)")
        records = self.records()
        found = [r for r in records if r["payload"]["signal_time_ns"] == int(signal)]
        if not found:
            raise IntegrityError("No outcome may precede its immutable forecast")
        forecast = found[0]
        expected_end = self.calendar.y4_end(int(signal))
        if int(label_end_ns) != expected_end or int(source_available_ns) < expected_end:
            raise IntegrityError("Label must cover the exact frozen next-day execution window")
        if observed < expected_end or int(source_available_ns) > observed:
            raise Pending("one_day_label_not_mature_or_real_price_source_not_arrived")
        entry, exit_ = np.asarray(entry_prices, dtype=float), np.asarray(exit_prices, dtype=float)
        if entry.shape != exit_.shape or entry.shape != (len(symbols),) or len(set(symbols)) != len(symbols):
            raise IntegrityError("Outcome symbol/price arrays do not align")
        by_symbol = {row["symbol"]: row for row in forecast["payload"]["forecasts"]}
        if set(symbols) - by_symbol.keys():
            raise IntegrityError("Outcome contains stocks absent from the recorded historical pool")
        values, predicted, targets = [], [], []
        for u, symbol in enumerate(symbols):
            target = float(np.log(exit_[u] / entry[u])) if np.isfinite([entry[u], exit_[u]]).all() and min(entry[u], exit_[u]) > 0 else None
            score = by_symbol[symbol]["score"]
            values.append({"symbol": symbol, "y4": target})
            if target is not None and score is not None:
                predicted.append(score)
                targets.append(target)
        x, y = np.asarray(predicted), np.asarray(targets)
        minimum = int(self.contract.freeze["config"]["min_stocks"])
        ic = correlation(x, y, minimum)
        rank_ic = correlation(ranks(x), ranks(y), minimum) if len(x) >= minimum else float("nan")
        payload = {"forecast_sha256": forecast["sha256"], "signal_time_ns": int(signal), "observed_at_ns": observed,
                   "label_end_time_ns": expected_end, "source_available_at_ns": int(source_available_ns),
                   "source": source, "labels": values, "paired_count": len(x),
                   "ic": float(ic) if np.isfinite(ic) else None, "rank_ic": float(rank_ic) if np.isfinite(rank_ic) else None}
        with self.locked():
            self.records()
            exclusive_json(self.path / "outcomes" / f"{int(signal):020d}.json", {"payload": payload, "sha256": hashlib.sha256(canonical(payload)).hexdigest()})
        return payload

    def status(self, fixture_clock=None):
        observed = self.now(fixture_clock)
        records = self.records()
        self.verify_calendar_extension()
        by_signal = {r["payload"]["signal_time_ns"]: r for r in records}
        start = self.binding["start_trading_day"]
        expected_days = [d for d in self.calendar.dates if d >= start][:60]
        complete_days, mature_days, gaps, pending, metrics = [], [], [], [], []
        pool_total = scored_total = 0
        for day in expected_days:
            signals = [local_ns(day, h, m) for h, m in HOUR_ENDS]
            complete, matured = True, True
            for slot, signal in enumerate(signals):
                if signal not in by_signal:
                    complete = matured = False
                    try:
                        deadline = self.calendar.execution_time(signal)
                    except Pending:
                        pending.append({"date": day, "slot": slot, "reason": "next_execution_session_calendar_pending"})
                        continue
                    if observed >= deadline:
                        gaps.append({"date": day, "slot": slot, "signal_time_ns": signal, "reason": "missed_forecast_deadline_no_backfill"})
                    else:
                        pending.append({"date": day, "slot": slot, "reason": "future_signal_or_feature_snapshot_not_received"})
                    continue
                forecast = by_signal[signal]
                pool_total += forecast["payload"]["pool_count"]
                scored_total += forecast["payload"]["scored_count"]
                if forecast["payload"]["scored_count"] < self.contract.freeze["config"]["min_stocks"]:
                    complete = False
                    gaps.append({"date": day, "slot": slot, "reason": "recorded_below_minimum_complete_stock_count"})
                path = self.path / "outcomes" / f"{signal:020d}.json"
                if not path.exists():
                    matured = False
                    pending.append({"date": day, "slot": slot, "reason": "one_day_outcome_not_received"})
                    continue
                outcome = json.loads(path.read_text())
                payload = outcome["payload"]
                if outcome["sha256"] != hashlib.sha256(canonical(payload)).hexdigest() or payload["forecast_sha256"] != forecast["sha256"]:
                    raise IntegrityError("Outcome/forecast immutable identity was altered")
                if payload["observed_at_ns"] < payload["label_end_time_ns"] or payload["label_end_time_ns"] > observed:
                    raise IntegrityError("Unmatured outcome entered prospective evidence")
                if payload["paired_count"] < self.contract.freeze["config"]["min_stocks"]:
                    matured = False
                if payload["ic"] is None or payload["rank_ic"] is None:
                    matured = False
                    gaps.append({"date": day, "slot": slot, "reason": "realized_cross_section_not_evaluable"})
                metrics.append({"date": day, "slot": slot, "signal_time_ns": signal,
                                "pool_count": forecast["payload"]["pool_count"], "scored_count": forecast["payload"]["scored_count"],
                                "ic": payload["ic"] if payload["ic"] is not None else np.nan,
                                "rank_ic": payload["rank_ic"] if payload["rank_ic"] is not None else np.nan})
            if complete:
                complete_days.append(day)
            if complete and matured:
                mature_days.append(day)
        achieved = len(expected_days) == 60 and len(complete_days) == 60 and len(mature_days) == 60 and not gaps
        started = any(r["payload"]["scored_count"] > 0 for r in records)
        state = "complete" if achieved else "collecting" if started else "pending_market_data"
        return {"status": state, "mode": self.binding["mode"], "start_trading_day": start,
                "target_trading_days": 60, "verified_calendar_target_days_available": len(expected_days),
                "complete_forecast_trading_days": len(complete_days), "mature_evaluated_trading_days": len(mature_days),
                "complete_forecast_dates": complete_days, "mature_evaluated_dates": mature_days,
                "coverage": scored_total / pool_total if pool_total else None,
                "pool_stock_hours": pool_total, "scored_stock_hours": scored_total,
                "gaps": gaps, "pending": pending, "prediction_metrics": breakdown(metrics) if metrics else None,
                "calendar_horizon_pending": len(expected_days) < 60,
                "forecast_collection_started": started, "actual_forecast_records": len(records),
                "research_evidence": not self.fixture, "missing_days_never_reclassified_or_replaced": True, "no_trading_actions": True}

    def manifest(self, calendar_path=None, fixture_clock=None):
        """Derive acceptance evidence; create day receipts only for actual mature days."""
        state = self.status(fixture_clock)
        artifacts = []
        by_signal = {r["payload"]["signal_time_ns"]: r for r in self.records()}
        forecast_artifacts = []
        for signal, record in by_signal.items():
            csv_path = self.path / "forecasts" / f"{signal:020d}.csv"
            json_path = self.path / "forecasts" / f"{signal:020d}.json"
            forecast_artifacts.append({"path": str(csv_path.resolve()), "sha256": file_hash(csv_path),
                "record_path": str(json_path.resolve()), "record_file_sha256": file_hash(json_path),
                "record_payload_sha256": record["sha256"], "date": local_date(signal), "signal_time_ns": signal,
                "recorded_at_ns": record["payload"]["recorded_at_ns"],
                "earliest_execution_time_ns": record["payload"]["earliest_execution_time_ns"],
                "pool_count": record["payload"]["pool_count"], "scored_count": record["payload"]["scored_count"]})
        for day in state["mature_evaluated_dates"]:
            forecasts, outcomes, slots = [], [], []
            for hour, minute in HOUR_ENDS:
                signal = local_ns(day, hour, minute)
                path = self.path / "outcomes" / f"{signal:020d}.json"
                forecasts.append(by_signal[signal]["sha256"])
                outcomes.append(file_hash(path))
                slots.append({"signal_time_ns": signal,
                    "forecast": next(a for a in forecast_artifacts if a["signal_time_ns"] == signal),
                    "outcome": {"path": str(path.resolve()), "sha256": file_hash(path)}})
            payload = {"date": day, "freeze_id": self.contract.freeze["freeze_id"],
                       "model_version": self.contract.model_version, "forecast_record_hashes": forecasts,
                       "outcome_file_hashes": outcomes, "slots": slots, "mode": self.binding["mode"]}
            path = self.path / "days" / (day + ".json")
            with self.locked():
                if path.exists():
                    if json.loads(path.read_text()) != payload:
                        raise IntegrityError("Previously completed forward-day evidence changed")
                else:
                    exclusive_json(path, payload)
            artifacts.append({"path": str(path.resolve()), "sha256": file_hash(path), "date": day})
        evidence = {**(self.calendar.evidence or {}), "source": self.calendar.source, "coverage_start": self.calendar.coverage_start,
                    "coverage_end": self.calendar.coverage_end, "calendar_semantic_sha256": self.calendar.fingerprint}
        if calendar_path:
            evidence.update(path=str(Path(calendar_path).resolve()), sha256=file_hash(calendar_path))
        return {**state, "freeze_id": self.contract.freeze["freeze_id"], "freeze_sha256": self.contract.freeze_hash,
                "binding_created": (self.path / "header.json").exists(), "target_complete_trading_sessions": 60,
                "frozen_model_and_rules": True, "first_session_is_first_full_trading_day_after_freeze": True,
                "historical_2025_2026_are_forward_evidence": False, "first_session": self.binding["start_trading_day"],
                "completed_sessions": state["mature_evaluated_trading_days"], "completed_official_sessions": state["mature_evaluated_dates"],
                "official_calendar_evidence": evidence, "daily_artifacts": artifacts,
                "forecast_artifacts": forecast_artifacts,
                "ledger_binding_sha256": hashlib.sha256(canonical(self.binding)).hexdigest(),
                "frozen_feature_names": self.contract.meta["features"],
                "frozen_checkpoint_hashes": self.binding["checkpoint_hashes"]}


def write_status(path, payload):
    """Explicitly requested derived summaries can change; source logs never do."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(canonical(payload) + b"\n")
    temporary.replace(path)


# Name used by the root postprocessor; the two names identify the same ledger.
ForecastLedger = ProspectiveLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "forecast"])
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--training-meta", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-manifest", type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", type=Path, help="Explicitly write derived forward_validation/manifest.json")
    args = parser.parse_args()
    contract = ledger = None
    try:
        contract = FrozenContract.read(args.freeze, args.training_meta)
        if not args.calendar.exists():
            raise Pending("verified_live_exchange_calendar_not_available")
        calendar = ExchangeCalendar.read(args.calendar)
        ledger = ProspectiveLedger(args.ledger, contract, calendar)
        if args.action == "status":
            result = ledger.manifest(args.calendar) if args.output else ledger.status()
        else:
            if not args.snapshot or not args.snapshot_manifest or not args.snapshot.exists() or not args.snapshot_manifest.exists():
                raise Pending("real_post_freeze_feature_window_not_available")
            window = FeatureWindow.read(args.snapshot, args.snapshot_manifest)
            window.validate(contract, calendar, time.time_ns(), False)
            record = ledger.append_forecast(window, FrozenEnsemble(contract, args.device))
            result = {"status": "forecast_recorded", "record_sha256": record["sha256"], "signal_time_ns": window.signal}
    except Pending as exc:
        if ledger is not None:
            result = ledger.manifest(args.calendar) if args.output else ledger.status()
            result.update(action_status="pending_market_data", reason=str(exc), forecasts_created=0)
        else:
            result = {"status": "pending_market_data", "reason": str(exc), "forecasts_created": 0,
                      "forecast_collection_started": False, "actual_forecast_records": 0, "completed_sessions": 0,
                      "target_complete_trading_sessions": 60,
                      "freeze_id": contract.freeze["freeze_id"] if contract else None,
                      "freeze_sha256": contract.freeze_hash if contract else None,
                      "binding_created": False, "frozen_model_and_rules": contract is not None,
                      "first_session_is_first_full_trading_day_after_freeze": False,
                      "historical_2025_2026_are_forward_evidence": False, "first_session": None,
                      "completed_official_sessions": [], "daily_artifacts": [], "forecast_artifacts": [], "research_evidence": True,
                      "no_trading_actions": True}
    if args.output:
        write_status(args.output, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
