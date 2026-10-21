"""Prepared pure missing/malformed capture-contract fixtures; no real driver."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gru_research import capture_v4_actual as capture


class CaptureContractFixtures(unittest.TestCase):
    def test_default_contract_has_no_filesystem_or_driver_access(self):
        with patch.object(Path, "stat", side_effect=AssertionError("no stat in dry contract")), \
             patch.object(Path, "open", side_effect=AssertionError("no file read in dry contract")), \
             patch.object(Path, "resolve", side_effect=AssertionError("no resolve in dry contract")), \
             patch.object(capture.subprocess, "Popen", side_effect=AssertionError("no real subprocess")), \
             contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(capture.main([]), 0)
        result = json.loads(stream.getvalue())
        self.assertEqual(result["status"], "prepared_only")
        self.assertFalse(result["packet_is_completion_receipt"])
        self.assertIsNone(result["point_net_excess_return"])

    def test_execute_without_all_explicit_inputs_fails_before_access(self):
        with patch.object(capture, "hash_file", side_effect=AssertionError("must not read any source")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("must not create attempt")), \
             patch.object(capture.subprocess, "Popen", side_effect=AssertionError("must not launch")):
            with self.assertRaisesRegex(ValueError, "all explicit execute"):
                capture.main(["--execute"])

    def test_malformed_map_digest_or_conflict_rejected(self):
        for mapping in ({}, {"source": "not-a-hash"}, {"": "0" * 64}):
            with self.assertRaises(ValueError):
                capture.merge_maps(mapping)
        with self.assertRaisesRegex(ValueError, "conflicting source identity"):
            capture.merge_maps({"same": "0" * 64}, {"same": "1" * 64})
        self.assertEqual(capture.merge_maps({"same": "0" * 64}, {"same": "0" * 64}), {"same": "0" * 64})

    def test_missing_comparison_and_malformed_json_preserve_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                capture.packet_for({}, {}, {}, root, {}, {})
            self.assertFalse((root / "candidate_actual_v4_packet.json").exists())
            source = root / "malformed.json"
            for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
                source.write_bytes(raw)
                with self.assertRaises(ValueError):
                    capture.load_json(source)
                self.assertEqual(source.read_bytes(), raw)

    def test_post_Popen_event_failure_reaps_child_with_raw_streams_open(self):
        process = Mock(pid=321)
        process.poll.return_value = None
        process.wait.return_value = -15
        state = {"comparison_first_observed_at_utc": None}
        stdout, stderr = io.BytesIO(b"original-out"), io.BytesIO(b"original-err")
        def terminating():
            self.assertFalse(stdout.closed); self.assertFalse(stderr.closed)
            stdout.seek(0, 2); stdout.write(b"final-before-exit")
        process.terminate.side_effect = terminating
        with patch.object(capture.subprocess, "Popen", return_value=process) as launched:
            with self.assertRaisesRegex(OSError, "event journal failed"):
                capture.run_captured_child(["SYNTHETIC_NEVER_LAUNCHED"], stdout, stderr,
                    Mock(side_effect=OSError("event journal failed")), Mock(), state)
        launched.assert_called_once()
        process.terminate.assert_called_once(); process.wait.assert_called_once_with(timeout=3)
        process.kill.assert_not_called()
        self.assertEqual(state["exit_code"], -15); self.assertFalse(state["child_exit_unknown"])
        self.assertEqual(stdout.getvalue(), b"original-outfinal-before-exit")
        self.assertEqual(stderr.getvalue(), b"original-err")
        self.assertEqual(state["process_id"], 321)

    def test_KeyboardInterrupt_escalates_bounded_wait_then_kill_without_retry(self):
        process = Mock(pid=654)
        process.poll.side_effect = [KeyboardInterrupt(), None]
        process.wait.side_effect = [capture.subprocess.TimeoutExpired("SYNTHETIC", 3), -9]
        state = {"comparison_first_observed_at_utc": None}
        stdout, stderr = io.BytesIO(), io.BytesIO()
        def killing():
            self.assertFalse(stdout.closed); self.assertFalse(stderr.closed)
            stderr.write(b"original child final stderr")
        process.kill.side_effect = killing
        with patch.object(capture.subprocess, "Popen", return_value=process) as launched:
            with self.assertRaises(KeyboardInterrupt):
                capture.run_captured_child(["SYNTHETIC_NEVER_LAUNCHED"], stdout, stderr, Mock(), Mock(), state)
        launched.assert_called_once(); process.terminate.assert_called_once(); process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(all(call.kwargs == {"timeout": 3} for call in process.wait.call_args_list))
        self.assertEqual(state["exit_code"], -9); self.assertFalse(state["child_exit_unknown"])
        self.assertEqual(stderr.getvalue(), b"original child final stderr")
        self.assertEqual(state["child_cleanup"]["errors"][0]["operation"], "first_wait")

    def test_unobservable_child_exit_keeps_pid_and_unknown_without_success(self):
        process = Mock(pid=987)
        process.poll.return_value = None
        process.wait.side_effect = capture.subprocess.TimeoutExpired("SYNTHETIC", 3)
        state = {"comparison_first_observed_at_utc": None}
        with patch.object(capture.subprocess, "Popen", return_value=process) as launched:
            with self.assertRaisesRegex(OSError, "event journal failed"):
                capture.run_captured_child(["SYNTHETIC_NEVER_LAUNCHED"], io.BytesIO(), io.BytesIO(),
                    Mock(side_effect=OSError("event journal failed")), Mock(), state)
        launched.assert_called_once(); process.terminate.assert_called_once(); process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(state["exit_code"]); self.assertTrue(state["child_exit_unknown"])
        self.assertEqual(state["process_id"], 987)
        self.assertFalse(state["child_cleanup"]["child_exit_observed"])
        self.assertNotIn("finished_at_utc", state)


if __name__ == "__main__":
    unittest.main()
