"""Synthetic export correctness fixtures; no historical financial results."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from gru_research.evaluate import file_hash
from gru_research.score_export_audit import COLUMNS, CHINA_OFFSET, ScorePanel, export_scores


class ScoreExportTests(unittest.TestCase):
    def read_rows(self, path):
        with path.open(newline="") as stream:
            return list(csv.DictReader(stream))

    def fixture(self, root):
        data = root / "synthetic_panel"
        data.mkdir()
        dates = np.busday_offset("2024-12-02", np.arange(40), roll="forward")
        times = np.array([int(d.astype("datetime64[ns]").astype(np.int64)) + (h * 60 + m) * 60_000_000_000 - CHINA_OFFSET
                         for d in dates for h, m in [(10, 30), (11, 30), (14, 0), (15, 0)]], np.int64)
        T, U = len(times), 100
        first = int(np.flatnonzero((times + CHINA_OFFSET) // 86_400_000_000_000 >= int(np.datetime64("2025-01-01").astype(np.int64)))[0])
        X = np.zeros((T, U, 2), np.float32)
        X[first - 5, 1, 0] = np.nan
        complete = np.ones((T, U), bool)
        complete[first + 1, 0] = False
        arrays = {"X": X, "hour_complete": complete, "members": np.ones((T, U), bool),
            "industry": np.ones((T, U), np.int16), "times": times, "earliest_execution_times": times + 60_000_000_000}
        for name, value in arrays.items():
            np.save(data / (name + ".npy"), value)
        (data / "symbols.json").write_text(json.dumps([f"fixture_{i}" for i in range(U)]))
        hashes = {p.name: file_hash(p) for p in data.iterdir()}
        meta = {"production_ready": True, "raw_market_data_is_synthetic": True,
            "causality_audit": {"passed": True, "fixture_only": True}, "files_sha256": hashes}
        (data / "meta.json").write_text(json.dumps(meta))
        panel = ScorePanel(data, {"sequence_length": 80, "min_stocks": 100, "embargo_trading_days": 2}, fixture=True)
        raw = root / "raw_scores.csv"
        self.write_raw(panel, raw)
        return panel, raw, first

    def write_raw(self, panel, path, omit=None, fabricate_low=False):
        indices, boundary = panel.sections("test_2025")
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            for t in indices:
                complete, _ = panel.window_flags(t)
                eligible = complete & panel.members[t]
                if t == omit or boundary[t] or (eligible.sum() < 100 and not fabricate_low):
                    continue
                for i in np.flatnonzero(panel.members[t]):
                    writer.writerow({"symbol": panel.symbols[i], "signal_time": str(panel.times[t]),
                        "earliest_execution_time": str(panel.earliest_execution_times[t]), "score": f"{i}.0000000001",
                        "percentile": str((i + .5) / 100), "model_version": "fixture:selected_full", "data_quality_flag": "ok"})

    def test_full_window_flags_and_whole_pool_blank_rows_preserve_raw_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel, raw, first = self.fixture(Path(tmp))
            raw_hash = file_hash(raw)
            before = self.read_rows(raw)
            output = Path(tmp) / "derived" / "scores.csv"
            receipt = export_scores(panel, "test_2025", raw, output, "fixture:selected_full")
            after = self.read_rows(output)
            mapping = {(r["symbol"], r["signal_time"]): r for r in after}
            for row in before:
                derived = mapping[(row["symbol"], row["signal_time"])]
                self.assertEqual([row[k] for k in ("score", "percentile", "model_version")], [derived[k] for k in ("score", "percentile", "model_version")])
            self.assertIn("feature_missing", mapping[("fixture_1", str(panel.times[first]))]["data_quality_flag"])
            low = [r for r in after if r["signal_time"] == str(panel.times[first + 1])]
            self.assertEqual(len(low), 100)
            self.assertTrue(all(r["score"] == r["percentile"] == "" and "below_min_cross_section" in r["data_quality_flag"] for r in low))
            self.assertEqual(file_hash(raw), raw_hash)
            self.assertEqual(receipt["status"], "complete_export")
            self.assertFalse(receipt["research_evidence"])
            self.assertFalse(receipt["full_PLAN_complete"])
            self.assertGreater(receipt["pool_stock_hours"], receipt["scored_stock_hours"])
            self.assertEqual(receipt["coverage_all_split_calendar_members"], receipt["scored_stock_hours"] / receipt["pool_stock_hours"])
            self.assertTrue(any(r["boundary_embargo"] for r in receipt["sections"]))

    def test_eligible_missing_original_sections_stay_unresolved_and_never_gain_zero_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel, raw, first = self.fixture(Path(tmp))
            self.write_raw(panel, raw, omit=first)
            output = Path(tmp) / "derived" / "scores.csv"
            receipt = export_scores(panel, "test_2025", raw, output, "fixture:selected_full")
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual(receipt["unresolved_original_score_sections"], [int(panel.times[first])])
            rows = [r for r in self.read_rows(output) if r["signal_time"] == str(panel.times[first])]
            self.assertTrue(all(r["score"] == r["percentile"] == "" and "original_score_section_missing" in r["data_quality_flag"] for r in rows))

    def test_fabricated_low_count_score_is_rejected_without_publishing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel, raw, _ = self.fixture(Path(tmp))
            self.write_raw(panel, raw, fabricate_low=True)
            output = Path(tmp) / "derived" / "scores.csv"
            with self.assertRaisesRegex(ValueError, "violates frozen eligibility"):
                export_scores(panel, "test_2025", raw, output, "fixture:selected_full")
            self.assertFalse(output.exists())

    def test_changed_future_features_do_not_change_past_window_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel, _, first = self.fixture(Path(tmp))
            prior = panel.window_flags(first)
            X = np.load(panel.path / "X.npy")
            X[first + 1:] = np.nan
            np.save(panel.path / "X.npy", X)
            meta_path = panel.path / "meta.json"
            meta = json.loads(meta_path.read_text())
            meta["files_sha256"]["X.npy"] = file_hash(panel.path / "X.npy")
            meta_path.write_text(json.dumps(meta))
            updated = ScorePanel(panel.path, panel.config, fixture=True)
            for a, b in zip(prior, updated.window_flags(first)):
                np.testing.assert_array_equal(a, b)

    def test_raw_hash_guard_and_existing_derived_or_partial_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel, raw, _ = self.fixture(Path(tmp))
            output = Path(tmp) / "derived" / "scores.csv"
            export_scores(panel, "test_2025", raw, output, "fixture:selected_full")
            digest = file_hash(output)
            with self.assertRaises(FileExistsError):
                export_scores(panel, "test_2025", raw, output, "fixture:selected_full")
            self.assertEqual(file_hash(output), digest)
            second = Path(tmp) / "derived" / "other.csv"
            partial = second.with_suffix(".csv.partial")
            partial.write_text("preexisting evidence")
            with self.assertRaises(FileExistsError):
                export_scores(panel, "test_2025", raw, second, "fixture:selected_full")
            self.assertEqual(partial.read_text(), "preexisting evidence")
            values = np.load(panel.path / "X.npy")
            values[0, 0, 0] = 1.
            np.save(panel.path / "X.npy", values)
            with self.assertRaisesRegex(ValueError, "Actual panel bytes changed"):
                ScorePanel(panel.path, panel.config, fixture=True)


if __name__ == "__main__":
    unittest.main()
