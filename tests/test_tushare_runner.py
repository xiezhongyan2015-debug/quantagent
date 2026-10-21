"""Offline queue scheduling and service-outage recovery checks."""
import datetime as dt
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_tushare_sync as runner
from tushare_sync import DataError


class RunnerTests(unittest.TestCase):
    def test_bounded_calendar_accepts_complete_natural_partition(self):
        c = runner.BoundedClient("fake")
        c.query = Mock(return_value=(["exchange", "cal_date", "is_open"], [["SSE", "20260101", 0], ["SSE", "20260102", 1]]))
        fields, rows = c.fetch_all("trade_cal", {"exchange": "SSE", "start_date": "20260101", "end_date": "20260102"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(c.query.call_count, 1)
        self.assertNotIn("offset", c.query.call_args.args[1])

    def test_bounded_calendar_rejects_partial(self):
        c = runner.BoundedClient("fake")
        c.query = Mock(return_value=(["exchange", "cal_date", "is_open"], [["SSE", "20260101", 0]]))
        with self.assertRaisesRegex(DataError, "Incomplete calendar"):
            c.fetch_all("trade_cal", {"exchange": "SSE", "start_date": "20260101", "end_date": "20260102"})

    def test_bounded_cap_is_not_silently_accepted(self):
        c = runner.BoundedClient("fake")
        c.CAPS = dict(c.CAPS, daily=2)
        c.query = Mock(return_value=(["ts_code", "trade_date"], [["000001.SZ", "20260102"], ["600000.SH", "20260102"]]))
        with self.assertRaisesRegex(DataError, "row cap"):
            c.fetch_all("daily", {"trade_date": "20260102"})

    def test_recent_gap_precedes_older_history(self):
        jobs = list(runner.research_jobs(dt.date(2026, 6, 11), dt.date(2026, 6, 12),
                    ["20260611", "20260612"], ["000001.SZ"], ["000001.SZ"]))
        self.assertEqual(jobs[0]["api"], "daily_basic")
        self.assertEqual(jobs[0]["params"], {"trade_date": "20260612"})
        phase = [j["phase"] for j in jobs]
        self.assertLess(phase.index("indices"), phase.index("historical_daily"))
        mins = [j for j in jobs if j["api"] == "stk_mins"]
        self.assertEqual(mins[0]["params"]["start_date"], "2026-06-12 09:00:00")

    def test_failure_classification(self):
        self.assertEqual(runner.failure_kind("HTTP 503 from configured provider"), "transient")
        self.assertEqual(runner.failure_kind("API daily: code=-2001 token已过期"), "credential_or_transport")
        self.assertEqual(runner.failure_kind("抱歉，您没有接口访问权限"), "api_unavailable")
        self.assertEqual(runner.failure_kind("Duplicate key/repeated page"), "data_validation")

    def test_outage_does_not_complete_partition(self):
        with tempfile.TemporaryDirectory() as d:
            client = Mock(token="test-secret", requests=3)
            r = runner.Runner(Path(d), client, dt.date(2026, 1, 1), dt.date(2026, 1, 2), 1)
            r.add([runner.job("trade_cal", {"exchange": "SSE"}, "bootstrap")])
            r.archive.partition = Mock(side_effect=DataError("HTTP 503 from configured provider"))
            def stop_after_wait(*_):
                r.deadline = 0
            r.wait_retry = Mock(side_effect=stop_after_wait)
            with patch.object(runner.shutil, "disk_usage", return_value=Mock(free=100*1024**3)):
                self.assertFalse(r.process_pending())
            self.assertEqual(r.db.execute("SELECT status,rows FROM jobs").fetchone(), ("pending", 0))
            self.assertEqual(r.wait_retry.call_args.args[0], 60)
            r.db.close()

    def test_resume_add_is_idempotent_and_preserves_done(self):
        with tempfile.TemporaryDirectory() as d:
            client = Mock(token="fake", requests=0)
            args = (Path(d), client, dt.date(2026, 1, 1), dt.date(2026, 1, 2), 1)
            r = runner.Runner(*args)
            jobs = [runner.job("daily", {"trade_date": "20260102"}, "recent_daily")]
            r.add(jobs); r.db.execute("UPDATE jobs SET status='done',rows=123"); r.db.commit(); r.db.close()
            r = runner.Runner(*args); r.add(jobs)
            self.assertEqual(r.db.execute("SELECT status,rows FROM jobs").fetchall(), [("done", 123)])
            r.db.close()

    def test_incomplete_calendar_prevents_expansion(self):
        with tempfile.TemporaryDirectory() as d:
            r = runner.Runner(Path(d), Mock(token="fake", requests=0), dt.date(2026, 1, 1), dt.date(2026, 1, 2), 1)
            with self.assertRaisesRegex(DataError, "Incomplete calendar"):
                r.expand()
            self.assertIsNone(r.db.execute("SELECT value FROM meta WHERE key='expanded'").fetchone())
            r.db.close()


if __name__ == "__main__":
    unittest.main()
