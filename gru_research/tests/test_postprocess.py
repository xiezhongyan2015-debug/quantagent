"""Synthetic proof-collection failure fixtures; never model/research evidence."""
import json
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gru_research.postprocess import collect, corrected_score_exports, MODELS
from gru_research.evaluate import file_hash


class PostprocessEvidenceTests(unittest.TestCase):
    def test_absent_actual_freeze_or_raw_scores_cannot_complete_reporting(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "synthetic_formal"
            run.mkdir()
            self.assertEqual(corrected_score_exports(run, Path(directory))["status"], "pending")
            (run / "freeze.json").write_text('{"fixture_only":true}')
            result = corrected_score_exports(run, Path(directory))
            self.assertEqual(result["status"], "pending")
            self.assertEqual(len(result["missing"]), 18)

    def test_failed_export_preserves_raw_csvs_and_failure_log(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "synthetic_formal"
            run.mkdir()
            (run / "freeze.json").write_text('{"fixture_only":true}')
            originals = []
            for split in ("test_2025", "recent_2026"):
                for name in MODELS:
                    path = run / "frozen_evaluation" / split / (name + "_scores.csv")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("SYNTHETIC_UNCHANGED_RAW_EVIDENCE")
                    originals.append(path)
            failure = SimpleNamespace(returncode=1, stdout="SYNTHETIC_PROBE\n", stderr="required score missing\n")
            with patch("gru_research.postprocess.subprocess.run", return_value=failure):
                result = corrected_score_exports(run, Path(directory))
            self.assertEqual(result["status"], "failed")
            self.assertIn("EXPORT_EXIT=1", Path(result["log"]).read_text())
            self.assertTrue(all(p.read_text() == "SYNTHETIC_UNCHANGED_RAW_EVIDENCE" for p in originals))
            self.assertFalse((run / "corrected_score_exports/manifest.json").exists())
            original_log = Path(result["log"]).read_bytes()
            with patch("gru_research.postprocess.subprocess.run") as retry:
                repeated = corrected_score_exports(run, Path(directory))
            retry.assert_not_called()
            self.assertFalse(repeated["retry_performed"])
            self.assertEqual(repeated["exit_code"], 1)
            self.assertEqual(Path(repeated["log"]).read_bytes(), original_log)

    def cached_fixture(self, root):
        """Tiny nonmarket byte fixtures; no real model output is used."""
        run, dataset = root / "synthetic_formal", root / "synthetic_dataset"
        run.mkdir(); dataset.mkdir()
        hashes = {}
        for name in ("X.npy", "hour_complete.npy", "members.npy", "industry.npy", "times.npy",
                     "earliest_execution_times.npy", "symbols.json"):
            path = dataset / name
            path.write_bytes(b"EXPLICIT_SYNTHETIC_HASH_GUARD_FIXTURE")
            hashes[name] = file_hash(path)
        metadata = {"fixture_only": True, "files_sha256": hashes}
        (dataset / "meta.json").write_text(json.dumps(metadata))
        fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        (run / "freeze.json").write_text(json.dumps({"freeze_id": "synthetic_freeze",
            "dataset_fingerprint": fingerprint, "fixture_only": True}))
        destination = run / "corrected_score_exports"
        reports = {}
        for split in ("test_2025", "recent_2026"):
            for name in MODELS:
                raw = run / "frozen_evaluation" / split / (name + "_scores.csv")
                corrected = destination / split / (name + "_scores.csv")
                for path in (raw, corrected):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("SYNTHETIC_UNCHANGED_SCORE_STRINGS")
                reports[split + "/" + name] = {"raw_csv": str(raw.resolve()),
                    "corrected_csv": str(corrected.resolve()), "raw_sha256": file_hash(raw),
                    "corrected_sha256": file_hash(corrected), "model_version": "synthetic_freeze:" + name,
                    "dataset_fingerprint": fingerprint, "research_evidence": True, "raw_preserved": True,
                    "scores_percentiles_versions_changed": False, "inference_performed": False,
                    "labels_or_IC_metrics_read": False, "status": "complete_export",
                    "unresolved_original_score_sections": []}
        manifest = {"fixture_only": True, "freeze_id": "synthetic_freeze",
            "freeze_sha256": file_hash(run / "freeze.json"),
            "export_code_sha256": file_hash(Path(__file__).parents[1] / "score_export_audit.py"),
            "verified_input_hashes": hashes, "reports": reports, "status": "complete_export"}
        path = destination / "manifest.json"
        path.write_text(json.dumps(manifest))
        return run, dataset, path, manifest

    def test_cached_exports_require_each_model_split_identity_and_exact_input_set(self):
        for mutation in ("alias", "version", "fingerprint", "empty_inputs", "changed_frozen_hash"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                run, dataset, path, manifest = self.cached_fixture(Path(directory))
                keys = list(manifest["reports"])
                if mutation == "alias":
                    manifest["reports"][keys[1]] = dict(manifest["reports"][keys[0]])
                elif mutation == "version":
                    manifest["reports"][keys[0]]["model_version"] = "wrong_model"
                elif mutation == "fingerprint":
                    manifest["reports"][keys[0]]["dataset_fingerprint"] = "wrong_panel"
                elif mutation == "empty_inputs":
                    manifest["verified_input_hashes"] = {}
                else:
                    name = "X.npy"
                    (dataset / name).write_bytes(b"ALTERED_SYNTHETIC_PANEL")
                    manifest["verified_input_hashes"][name] = file_hash(dataset / name)
                path.write_text(json.dumps(manifest))
                with patch("gru_research.postprocess.subprocess.run") as retry:
                    result = corrected_score_exports(run, dataset)
                retry.assert_not_called()
                self.assertEqual(result["status"], "failed")

    def test_exact_cached_identity_can_be_verified_without_another_export(self):
        with tempfile.TemporaryDirectory() as directory:
            run, dataset, _, _ = self.cached_fixture(Path(directory))
            with patch("gru_research.postprocess.subprocess.run") as retry:
                result = corrected_score_exports(run, dataset)
            retry.assert_not_called()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["actual_model_split_exports"], 18)

    def test_successful_job_exit_and_old_audit_pass_are_insufficient_without_actual_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "synthetic_formal"
            run.mkdir()
            (run / "job_exit_405900.txt").write_text("JOB_EXIT=0\n")
            # Deliberately optimistic synthetic predecessor: the reporting gate
            # must independently prevent a false completion claim.
            prior = {"full_PLAN_complete": True, "historical_experiment_complete": True,
                     "status_counts": {"complete": 1}, "items": [], "observations": []}
            with patch("gru_research.postprocess.AcceptanceAudit") as audit:
                audit.return_value.report.return_value = prior
                result = collect(run, root / "synthetic_dataset", root / "synthetic_receipts",
                                 root / "synthetic_calendar.json", root / "synthetic_delivery", 405900)
            self.assertFalse(result["training_and_prediction_complete"])
            self.assertFalse(result["full_PLAN_complete"])
            report = json.loads((run / "acceptance.json").read_text())
            self.assertFalse(report["historical_experiment_complete"])
            self.assertEqual(report["independent_score_metadata_and_coverage_correction"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
