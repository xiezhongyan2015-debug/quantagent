"""Synthetic integrity fixtures only; none of these records is market evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np

from gru_research.prospective import (ExchangeCalendar, FeatureWindow, ForecastLedger,
    FrozenContract, IntegrityError, Pending, canonical, file_hash, local_ns, main)


def clock_text(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


class ProspectiveIntegrityFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        # These weekdays are synthetic, not an asserted exchange calendar.
        dates = tuple(str(np.busday_offset("2030-01-01", i)) for i in range(100))
        self.calendar = ExchangeCalendar(dates, "synthetic_unit_fixture_NOT_official_sessions", dates[0], dates[-1], True)
        self.meta = {"groups": {"hour": list(range(24)), "daily": list(range(24, 40)), "factors": [40, 41],
                    "fund": list(range(42, 58)), "ages": [58, 59]}, "features": [f"fixture_{i}" for i in range(60)],
                    "raw_market_data_is_synthetic": True, "causality_audit": {"passed": True},
                    "factor_selection_sha256": hashlib.sha256(b"fixture-only factor selection").hexdigest(),
                    "normalization": "fixture matching frozen transform", "n_industries": 1}
        fingerprint = hashlib.sha256(json.dumps(self.meta, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        paths = [str(self.path / f"fixture_seed{s}.pt") for s in [11, 22, 33, 44, 55]]
        self.freeze = {"dataset_fingerprint": fingerprint, "frozen_at_utc": clock_text(local_ns(dates[20], 12, 0)),
                       "config": {"sequence_length": 80, "dtype": "float32", "min_stocks": 3, "seeds": [11, 22, 33, 44, 55]},
                       "selection": {"selected_label": 4, "selected_method": "fixed"},
                       "evaluation_models": {"selected_full": paths},
                       "checkpoint_hashes": {p: hashlib.sha256(p.encode()).hexdigest() for p in paths},
                       "no_test_driven_tuning": True}
        self.freeze["freeze_id"] = hashlib.sha256(json.dumps({"data": fingerprint, "config": self.freeze["config"],
            "selection": self.freeze["selection"], "hashes": self.freeze["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
        self.freeze_path, self.meta_path = self.path / "freeze.json", self.path / "meta.json"
        self.freeze_path.write_bytes(canonical(self.freeze))
        self.meta_path.write_bytes(canonical(self.meta))
        self.contract = FrozenContract.read(self.freeze_path, self.meta_path, fixture=True)
        self.ledger = ForecastLedger(self.path / "fixture_ledger", self.contract, self.calendar, fixture=True)
        self.first_day = dates[21]
        self.signal = local_ns(self.first_day, 10, 30)
        self.source = {"price_policy": "five_complete_minute_qfq_open_TWAP", "hashes": {"fixture_only": hashlib.sha256(b"not real prices").hexdigest()}}

    def tearDown(self):
        self.temporary.cleanup()

    def window(self, signal=None, count=4):
        signal = self.signal if signal is None else signal
        times = self.calendar.signals
        index = np.searchsorted(times, signal)
        times = times[index - 79:index + 1].copy()
        meta = {"feature_names": self.meta["features"], "factor_selection_sha256": self.meta["factor_selection_sha256"],
                "normalization": self.meta["normalization"], "raw_market_data_is_synthetic": True,
                "point_in_time_audit_passed": True, "source_hashes": {"synthetic_fixture": hashlib.sha256(b"fixture").hexdigest()},
                "captured_at_utc": clock_text(signal + 10_000_000_000),
                "earliest_execution_time_ns": self.calendar.execution_time(signal)}
        return FeatureWindow(np.zeros((80, count, 60), np.float32), np.zeros((80, count), np.int64),
                             np.ones(count, bool), np.ones((80, count), bool), times,
                             np.broadcast_to(times[:, None], (80, count)).copy(),
                             [f"{i:06d}.SZ" for i in range(count)], meta)

    def append(self, signal=None, count=4):
        window = self.window(signal, count)
        return self.ledger.append_forecast(window, fixture_scores=np.linspace(-1, 1, count),
                                           fixture_clock=window.signal + 30_000_000_000)

    def outcome(self, signal=None, now=None):
        signal = self.signal if signal is None else signal
        window = self.window(signal)
        end = self.calendar.y4_end(signal)
        return self.ledger.append_outcome(signal, window.symbols, np.repeat(10., 4),
            10. * np.exp(np.linspace(-.01, .01, 4)), end, self.source, end,
            synthetic=True, fixture_clock=end + 1_000_000_000 if now is None else now)

    def test_final_freeze_missing_is_pending_and_creates_no_ledger(self):
        output = self.path / "pending_manifest.json"
        argv = ["prospective", "status", "--freeze", str(self.path / "not_yet_freeze.json"),
                "--training-meta", str(self.meta_path), "--calendar", str(self.path / "calendar.json"),
                "--ledger", str(self.path / "genuine_ledger"), "--output", str(output)]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            main()
        manifest = json.loads(output.read_text())
        self.assertEqual(manifest["status"], "pending_market_data")
        self.assertEqual(manifest["reason"], "final_model_freeze_json_not_available_at_requested_path")
        self.assertIsNone(manifest["freeze_id"])
        self.assertFalse(manifest["binding_created"])
        self.assertFalse(manifest["forecast_collection_started"])
        self.assertEqual(manifest["completed_sessions"], 0)
        self.assertFalse((self.path / "genuine_ledger").exists())

    def test_fixture_contract_cannot_enter_genuine_ledger(self):
        real_calendar = ExchangeCalendar(self.calendar.dates, self.calendar.source, self.calendar.coverage_start, self.calendar.coverage_end, False)
        with self.assertRaises(IntegrityError):
            ForecastLedger(self.path / "no_fake_research", self.contract, real_calendar)
        with self.assertRaises(IntegrityError):
            FrozenContract.read(self.freeze_path, self.meta_path)

    def test_unavailable_new_snapshot_preserves_existing_append_only_counts(self):
        self.append()
        output = self.path / "fixture_pending_snapshot_manifest.json"
        argv = ["prospective", "forecast", "--freeze", str(self.freeze_path),
                "--training-meta", str(self.meta_path), "--calendar", str(self.freeze_path),
                "--ledger", str(self.ledger.path), "--output", str(output)]
        with patch.object(sys, "argv", argv), patch("gru_research.prospective.FrozenContract.read", return_value=self.contract), \
                patch("gru_research.prospective.ExchangeCalendar.read", return_value=self.calendar), \
                patch("gru_research.prospective.ProspectiveLedger", return_value=self.ledger), \
                patch("gru_research.prospective.time.time_ns", return_value=self.signal + 30_000_000_000), \
                redirect_stdout(io.StringIO()):
            main()
        state = json.loads(output.read_text())
        self.assertEqual(state["action_status"], "pending_market_data")
        self.assertEqual(state["actual_forecast_records"], 1)
        self.assertEqual(state["forecasts_created"], 0)
        self.assertTrue(state["forecast_collection_started"])
        self.assertFalse(state["research_evidence"])

    def test_first_complete_session_excludes_partial_freeze_day(self):
        self.assertEqual(self.calendar.first_full_day(self.contract.frozen_at), self.first_day)
        old = local_ns(self.calendar.dates[20], 15, 0)
        with self.assertRaises(IntegrityError):
            self.ledger.append_forecast(self.window(old), fixture_scores=np.arange(4.), fixture_clock=old + 30_000_000_000)

    def test_future_source_arrival_and_changed_feature_list_are_rejected(self):
        window = self.window()
        window.source_available_times[0, 0] += 1
        with self.assertRaises(IntegrityError):
            self.ledger.append_forecast(window, fixture_scores=np.arange(4.), fixture_clock=self.signal + 30_000_000_000)
        window = self.window()
        window.metadata["feature_names"] = list(reversed(self.meta["features"]))
        with self.assertRaises(IntegrityError):
            self.ledger.append_forecast(window, fixture_scores=np.arange(4.), fixture_clock=self.signal + 30_000_000_000)

    def test_missing_hour_or_forecast_deadline_never_gets_fabricated_score(self):
        window = self.window()
        window.hour_complete[1, 0] = False
        record = self.ledger.append_forecast(window, fixture_scores=np.arange(3.), fixture_clock=self.signal + 30_000_000_000)
        self.assertIsNone(record["payload"]["forecasts"][0]["score"])
        self.assertIn("incomplete_hour_window", record["payload"]["forecasts"][0]["data_quality_flag"])
        later = local_ns(self.first_day, 11, 30)
        with self.assertRaises(IntegrityError):
            self.ledger.append_forecast(self.window(later), fixture_scores=np.arange(4.), fixture_clock=self.calendar.execution_time(later))

    def test_duplicate_forecast_and_tampered_forecast_are_rejected(self):
        record = self.append()
        path = self.ledger.path / "forecasts" / f"{self.signal:020d}.json"
        before = path.read_bytes()
        with self.assertRaises(IntegrityError):
            self.append()
        self.assertEqual(path.read_bytes(), before)
        record["payload"]["forecasts"][0]["score"] = 999.
        path.write_bytes(canonical(record))
        with self.assertRaises(IntegrityError):
            self.ledger.records()

    def test_actual_csv_content_is_bound_to_immutable_forecast(self):
        self.append()
        path = self.ledger.path / "forecasts" / f"{self.signal:020d}.csv"
        path.write_text(path.read_text().replace("-1.0", "-2.0"))
        with self.assertRaises(IntegrityError):
            self.ledger.records()

    def test_skipped_warmup_session_cannot_be_compressed_out_or_imputed_complete(self):
        compressed = self.window()
        compressed.times[30] = compressed.times[29]
        with self.assertRaises(IntegrityError):
            compressed.validate(self.contract, self.calendar, self.signal + 30_000_000_000, True)
        explicit_gap = self.window()
        explicit_gap.X[30] = np.nan
        explicit_gap.hour_complete[30] = False
        record = self.ledger.append_forecast(explicit_gap, fixture_scores=np.array([]),
                                             fixture_clock=self.signal + 30_000_000_000)
        self.assertEqual(record["payload"]["scored_count"], 0)
        self.assertTrue(all(row["score"] is None for row in record["payload"]["forecasts"]))
        state = self.ledger.status(fixture_clock=self.signal + 30_000_000_000)
        self.assertEqual(state["status"], "pending_market_data")
        self.assertFalse(state["forecast_collection_started"])

    def test_outcome_requires_prior_forecast_maturity_and_cannot_overwrite(self):
        with self.assertRaises(IntegrityError):
            self.outcome()
        self.append()
        end = self.calendar.y4_end(self.signal)
        with self.assertRaises(Pending):
            self.outcome(now=end - 1)
        outcome = self.outcome()
        self.assertEqual(outcome["paired_count"], 4)
        self.assertAlmostEqual(outcome["rank_ic"], 1.)
        with self.assertRaises(FileExistsError):
            self.outcome()

    def test_calendar_cannot_rewrite_prior_sessions_and_schema_cannot_change(self):
        self.append()
        revised = copy.copy(self.calendar)
        bad = ExchangeCalendar(revised.dates[1:], revised.source, revised.coverage_start, revised.coverage_end, True)
        other = ForecastLedger(self.ledger.path, self.contract, bad, fixture=True)
        with self.assertRaises(IntegrityError):
            other.status(fixture_clock=self.signal + 30_000_000_000)
        altered_meta = copy.deepcopy(self.meta)
        altered_meta["features"][0] = "changed"
        self.meta_path.write_bytes(canonical(altered_meta))
        with self.assertRaises(IntegrityError):
            FrozenContract.read(self.freeze_path, self.meta_path, fixture=True)

    def test_label_arrays_cannot_enter_features_only_snapshot(self):
        window = self.window()
        path, manifest = self.path / "features.npz", self.path / "features.json"
        np.savez(path, X=window.X, industry=window.industry, members=window.members,
                 hour_complete=window.hour_complete, times=window.times,
                 source_available_times=window.source_available_times, y4=np.ones(4))
        manifest.write_bytes(canonical({**window.metadata, "symbols": window.symbols}))
        with self.assertRaises(IntegrityError):
            FeatureWindow.read(path, manifest)

    def test_full60_sessions_count_actual_forecasts_and_mature_labels_only(self):
        days = self.calendar.dates[21:81]
        signals = [local_ns(day, h, m) for day in days for h, m in ((10, 30), (11, 30), (14, 0), (15, 0))]
        before = self.ledger.status(fixture_clock=signals[0] - 1)
        self.assertFalse(before["forecast_collection_started"])
        self.assertEqual(before["complete_forecast_trading_days"], 0)
        for signal in signals:
            self.append(signal)
        state = self.ledger.status(fixture_clock=self.calendar.y4_end(signals[-1]) + 1)
        self.assertEqual(state["complete_forecast_trading_days"], 60)
        self.assertEqual(state["mature_evaluated_trading_days"], 0)
        self.assertNotEqual(state["status"], "complete")
        for signal in signals:
            self.outcome(signal)
        state = self.ledger.manifest(fixture_clock=self.calendar.y4_end(signals[-1]) + 1_000_000_000)
        self.assertEqual(state["completed_sessions"], 60)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(len(state["daily_artifacts"]), 60)
        self.assertFalse(state["research_evidence"])
        self.assertEqual(state["actual_forecast_records"], 240)
        self.assertEqual(len(state["forecast_artifacts"]), 240)
        for receipt in state["forecast_artifacts"]:
            self.assertEqual(file_hash(receipt["path"]), receipt["sha256"])
            self.assertEqual(file_hash(receipt["record_path"]), receipt["record_file_sha256"])
            self.assertLess(receipt["recorded_at_ns"], receipt["earliest_execution_time_ns"])
        for receipt in state["daily_artifacts"]:
            self.assertEqual(file_hash(receipt["path"]), receipt["sha256"])
            day = json.loads(Path(receipt["path"]).read_text())
            self.assertEqual(len(day["slots"]), 4)
            for slot in day["slots"]:
                for name in ("forecast", "outcome"):
                    self.assertEqual(file_hash(slot[name]["path"]), slot[name]["sha256"])


if __name__ == "__main__":
    unittest.main()
