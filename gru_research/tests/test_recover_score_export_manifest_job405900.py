"""Pure rejection/publication mechanics; no production score/panel access."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gru_research import recover_score_export_manifest_job405900 as recovery


class RecoveryRejectTests(unittest.TestCase):
    def test_default_contract_has_no_evidence_file_access(self):
        with ExitStack() as stack:
            for name in ("open", "read_bytes", "read_text", "exists", "stat", "is_dir", "is_symlink"):
                stack.enter_context(mock.patch.object(Path, name, side_effect=AssertionError("File access forbidden")))
            stream = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(recovery.main([]), 0)
            contract = json.loads(stream.getvalue())
        self.assertEqual(contract["default"], "contract_only_no_file_access")
        self.assertFalse(contract["full_PLAN_complete"])

    def test_execute_missing_arguments_does_not_open_evidence(self):
        with mock.patch.object(Path, "open", side_effect=AssertionError("File access forbidden")):
            with self.assertRaisesRegex(ValueError, "every contract argument"):
                recovery.main(["--execute"])

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                recovery.strict_json(raw)

    def test_boolean_counts_are_not_integers(self):
        with self.assertRaisesRegex(ValueError, "Invalid integer"):
            recovery.integer(True, "CSV bytes", 1)
        with self.assertRaisesRegex(ValueError, "Invalid integer"):
            recovery.integer(-1, "CSV bytes")

    def _malformed_report(self):
        # Deliberately incomplete synthetic metadata. No successful original
        # export or real score values are simulated by these rejection cases.
        report = dict.fromkeys(recovery.REPORT_KEYS)
        report.update(status="complete_export", research_evidence=True, raw_preserved=True,
            full_PLAN_complete=False, scores_percentiles_versions_changed=False,
            inference_performed=False, labels_or_IC_metrics_read=False,
            unresolved_original_score_sections=[], dataset_fingerprint=recovery.FINGERPRINT,
            model_version=recovery.FREEZE_ID + ":pv",
            scope="all historical calendar rows in split, with frozen boundary exclusions explicit",
            raw_csv=recovery.REMOTE_RUN + "/frozen_evaluation/test_2025/pv_scores.csv",
            corrected_csv=recovery.REMOTE_RUN + "/corrected_score_exports/test_2025/pv_scores.csv",
            raw_sha256="1" * 64, corrected_sha256="2" * 64, sections=[])
        return report

    def test_changed_inference_claim_or_remote_path_is_rejected(self):
        report = self._malformed_report()
        report["inference_performed"] = True
        with self.assertRaisesRegex(ValueError, "Unsupported original report claim"):
            recovery.check_report(report, "test_2025", "pv")
        report = self._malformed_report()
        report["raw_csv"] = "/tmp/relocated_pv_scores.csv"
        with self.assertRaisesRegex(ValueError, "changed original remote CSV path"):
            recovery.check_report(report, "test_2025", "pv")
        report = self._malformed_report()
        with self.assertRaisesRegex(ValueError, "section receipts missing"):
            recovery.check_report(report, "test_2025", "pv")

    def test_exclusive_publication_preserves_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            receipt = Path(directory) / "receipt.json"
            manifest.write_bytes(b"existing original bytes")
            with self.assertRaises(FileExistsError):
                recovery.publish_pair(manifest, b"must not replace", receipt, b"must not publish")
            self.assertEqual(manifest.read_bytes(), b"existing original bytes")
            self.assertFalse(receipt.exists())
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["manifest.json"])

    def test_second_link_failure_is_explicit_without_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            receipt = Path(directory) / "receipt.json"
            actual_link = recovery.os.link
            calls = []
            def fail_second(source, destination):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("Synthetic second publication failure")
                return actual_link(source, destination)
            with mock.patch.object(recovery.os, "link", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "second publication failure"):
                    recovery.publish_pair(manifest, b"inline recovery provenance", receipt, b"success receipt")
            self.assertEqual(manifest.read_bytes(), b"inline recovery provenance")
            self.assertFalse(receipt.exists())
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["manifest.json"])


if __name__ == "__main__":
    unittest.main()
