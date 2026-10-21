"""Evidence checker tests only; these fixtures are never training results."""
import csv
from pathlib import Path
import tempfile
import unittest
import json
import numpy as np

from gru_research.experiment_acceptance import (AcceptanceAudit, required_comparisons, MODELS,
    DEFAULT_STATISTICS_PROTOCOL, audited_epoch_count, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256,
    verify_forecast_exports, canonical_payload_hash)
from gru_research.evaluate import breakdown, file_hash, write_json


class AcceptanceTests(unittest.TestCase):
    def test_missing_experiment_is_not_complete_and_script_existence_is_irrelevant(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AcceptanceAudit(Path(tmp), None, None)
            report = audit.report()
            self.assertFalse(report["full_PLAN_complete"])
            self.assertFalse(report["historical_experiment_complete"])
            self.assertFalse(report["future_60_sessions_complete"])
            self.assertFalse(any(i["status"] == "complete" for i in report["items"] if i["scope"] != "statistical_preregistration"))
            self.assertGreater(report["status_counts"]["missing"], 40)

    def test_required_pairs_cover_direct_and_conditional_increments(self):
        selection = {"alternatives": {"fixed_1": {"mean_ic": .01}, "fixed_2": {"mean_ic": .04},
                                      "fixed_3": {"mean_ic": .02}, "fixed_4": {"mean_ic": .03}}}
        pairs = required_comparisons(selection)
        self.assertEqual(len(pairs), 9)
        self.assertEqual(pairs["blo_minus_best_fixed"], ("blo_selected", "fixed_label_2"))
        self.assertEqual(pairs["label_1_minus_direct_y4"], ("fixed_label_1", "fixed_label_4"))
        self.assertEqual(pairs["factor_increment_over_pv"], ("pv_factors", "pv"))
        self.assertEqual(pairs["fundamental_increment_given_factors"], ("selected_full", "pv_factors"))

    def test_existing_failing_artifact_does_not_become_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text('{}')
            audit = AcceptanceAudit(Path(tmp), None, None)
            def fail():
                raise ValueError("Actual evidence contradicts the requirement")
            item = audit.check("fixture", "Has passed", [path], fail)
            self.assertEqual(item["status"], "failed")
            self.assertIn("contradicts", item["detail"])

    def test_expected_preregistration_hash_rejects_even_a_numerically_identical_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.json"
            path.write_bytes(DEFAULT_STATISTICS_PROTOCOL.read_bytes())
            expected = file_hash(path)
            audit = AcceptanceAudit(Path(tmp), None, None, statistics_protocol=path, expected_protocol_sha256=expected)
            audit.verify_statistics_protocol()
            path.write_text(path.read_text() + "\n")
            # Use a new audit instance, as the previous instance deliberately caches validated bytes.
            audit = AcceptanceAudit(Path(tmp), None, None, statistics_protocol=path, expected_protocol_sha256=expected)
            with self.assertRaisesRegex(AssertionError, "content hash changed"):
                audit.verify_statistics_protocol()

    def test_only_the_exact_frozen_early_stop_count_defect_is_allowed(self):
        config = {"epochs": 40, "patience": 8}
        epochs = {i: {"bad_epochs": i - 1} for i in range(1, 10)}
        result = audited_epoch_count(10, epochs, config, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)
        self.assertTrue(result["allowed_frozen_metadata_exception"])
        self.assertEqual(result["actual_completed_epochs"], 9)
        self.assertEqual(result["reported_completed_epochs"], 10)
        with self.assertRaisesRegex(AssertionError, "Missing actual epoch"):
            audited_epoch_count(10, {k: v for k, v in epochs.items() if k != 5}, config, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)
        with self.assertRaisesRegex(AssertionError, "actual early stopping"):
            audited_epoch_count(10, {i: {"bad_epochs": 0} for i in range(1, 10)}, config, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)
        with self.assertRaisesRegex(AssertionError, "Unexpected source"):
            audited_epoch_count(10, epochs, config, "different_source")
        with self.assertRaisesRegex(AssertionError, "plus-one"):
            audited_epoch_count(11, epochs, config, FROZEN_EARLY_STOP_COUNT_BUG_SOURCE_SHA256)

    def test_future_calendar_binding_with_zero_actual_predictions_is_not_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "forward_validation").mkdir()
            write_json(root / "freeze.json", {"freeze_id": "fixture_only"})
            write_json(root / "forward_validation" / "manifest.json", {"freeze_id": "fixture_only",
                "target_complete_trading_sessions": 60, "frozen_model_and_rules": True,
                "first_session_is_first_full_trading_day_after_freeze": True,
                "historical_2025_2026_are_forward_evidence": False, "official_calendar_evidence": {"fixture": True},
                "forecast_collection_started": False, "research_evidence": False, "actual_forecast_records": 0,
                "status": "pending_market_data", "completed_sessions": 0})
            audit = AcceptanceAudit(root, None, None)
            audit.external_deliverables()
            started = next(i for i in audit.items if i["id"] == "forward_validation_started")
            complete = next(i for i in audit.items if i["id"] == "forward_validation_60_sessions")
            self.assertEqual(started["status"], "missing")
            self.assertEqual(complete["status"], "missing")
            self.assertIn("has not started", started["detail"])
            manifest = json.loads((root / "forward_validation" / "manifest.json").read_text())
            manifest.update(forecast_collection_started=True, research_evidence=True, actual_forecast_records=1)
            write_json(root / "forward_validation" / "manifest.json", manifest)
            audit = AcceptanceAudit(root, None, None)
            audit.external_deliverables()
            started = next(i for i in audit.items if i["id"] == "forward_validation_started")
            self.assertEqual(started["status"], "failed")
            self.assertIn("actual immutable ledger", started["detail"])

    def test_score_csv_keeps_missing_stock_explicit_and_rejects_fabrication(self):
        fields = ["symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scores.csv"
            rows = [["A", 100, 101, .5, .5, "f:model", "ok"],
                    ["B", 100, 101, "", "", "f:model", "incomplete_hour_window"]]
            def write():
                with path.open("w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(fields)
                    writer.writerows(rows)
            write()
            audit = AcceptanceAudit(Path(tmp), None, None)
            records = [{"signal_time_ns": 100, "pool_count": 2, "scored_count": 1}]
            result = audit.score_csv(path, records, "f:model")
            self.assertEqual(result["csv_rows_verified"], 2)
            rows[1][3], rows[1][4] = 0., .5
            write()
            with self.assertRaises(AssertionError):
                audit.score_csv(path, records, "f:model")

    def test_forecast_csv_receipt_requires_actual_unchanged_bytes_and_original_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "forward_validation"
            (ledger / "forecasts").mkdir(parents=True)
            signal = 1893447000000000000
            rows = [{"symbol": "A", "signal_time": signal, "earliest_execution_time": signal + 1,
                "score": .5, "percentile": .5, "model_version": "fixture_only:selected_full", "data_quality_flag": "ok"},
                {"symbol": "B", "signal_time": signal, "earliest_execution_time": signal + 1,
                "score": None, "percentile": None, "model_version": "fixture_only:selected_full", "data_quality_flag": "missing_hour"}]
            csv_path = ledger / "forecasts" / f"{signal:020d}.csv"
            record_path = csv_path.with_suffix(".json")
            with csv_path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            original_csv = csv_path.read_bytes()
            payload = {"signal_time_ns": signal, "recorded_at_ns": signal,
                "earliest_execution_time_ns": signal + 1, "pool_count": 2, "scored_count": 1,
                "forecasts": rows, "forecast_csv_sha256": file_hash(csv_path)}
            record = {"payload": payload, "sha256": canonical_payload_hash(payload)}
            write_json(record_path, record)
            receipt = {"path": str(csv_path), "sha256": file_hash(csv_path), "record_path": str(record_path),
                "record_file_sha256": file_hash(record_path), "record_payload_sha256": record["sha256"], "date": "2030-01-01",
                **{k: payload[k] for k in ("signal_time_ns", "recorded_at_ns", "earliest_execution_time_ns", "pool_count", "scored_count")}}
            manifest = {"forecast_artifacts": [receipt]}
            self.assertEqual(set(verify_forecast_exports(root, ledger, manifest, {signal: record})), {signal})
            csv_path.write_text(csv_path.read_text().replace("missing_hour", "invented_score"))
            with self.assertRaisesRegex(AssertionError, "CSV differs"):
                verify_forecast_exports(root, ledger, manifest, {signal: record})
            csv_path.write_bytes(original_csv)
            receipt["scored_count"] = 2
            with self.assertRaisesRegex(AssertionError, "metadata differs"):
                verify_forecast_exports(root, ledger, manifest, {signal: record})
            with self.assertRaisesRegex(AssertionError, "Every actual forecast"):
                verify_forecast_exports(root, ledger, {"forecast_artifacts": []}, {signal: record})

    def test_additional_statistics_leave_frozen_predictions_and_selection_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            directory = run / "frozen_evaluation" / "test_2025"
            directory.mkdir(parents=True)
            selection = {"alternatives": {"fixed_1": {"mean_ic": .01}, "fixed_2": {"mean_ic": .04},
                                          "fixed_3": {"mean_ic": .02}, "fixed_4": {"mean_ic": .03}}}
            write_json(run / "selection.json", selection)
            write_json(run / "freeze.json", {"freeze_id": "synthetic_fixture", "dataset_fingerprint": "fixture_only", "frozen_at_utc": "2027-01-01T00:00:00Z"})
            summary = {"freeze_id": "synthetic_fixture", "dataset_fingerprint": "fixture_only", "models": {}}
            for i, model in enumerate(MODELS):
                values = []
                for d, date in enumerate(np.busday_offset("2025-01-02", np.arange(60), roll="forward")):
                    for slot in range(4):
                        values.append({"signal_time_ns": 4 * d + slot, "date": str(date), "slot": slot,
                            "ic": .01 * i + .001 * slot, "rank_ic": .02 * i + .001 * slot,
                            "pool_count": 120, "scored_count": 120, "label_mature": True, "mature_label_count": 120})
                seeds = {str(s): [{**r, "seed": s} for r in values] for s in (11, 22, 33, 44, 55)}
                metrics = breakdown(values)
                ic = metrics["overall"]["ic"]
                summary["models"][model] = {**metrics, "per_seed": {s: breakdown(r) for s, r in seeds.items()},
                    "five_seed_ic_distribution": {"mean": ic, "std": 0., "min": ic, "max": ic}}
                write_json(directory / (model + "_sections.json"), {"records": values, "seed_records": seeds})
            write_json(directory / "summary.json", summary)
            original_files = [run / "selection.json", run / "freeze.json", directory / "summary.json"] + list(directory.glob("*_sections.json"))
            hashes = {p: file_hash(p) for p in original_files}
            audit = AcceptanceAudit(run, None, None, run / "independent_comparisons", verify_scores=False)
            audit.historical_evaluation()
            item = next(i for i in audit.items if i["id"] == "test_2025_required_paired_comparisons")
            self.assertEqual(item["status"], "complete", item.get("detail"))
            result = json.loads((run / "independent_comparisons" / "test_2025_paired_comparisons.json").read_text())
            self.assertEqual(len(result["comparisons"]), 9)
            contrast = result["comparisons"]["factor_increment_over_pv"]
            self.assertAlmostEqual(contrast["mean_ic_difference"], .01)
            np.testing.assert_allclose(contrast["ci_95"], [.01, .01], atol=1e-12)
            self.assertEqual(hashes, {p: file_hash(p) for p in original_files})


if __name__ == "__main__":
    unittest.main()
