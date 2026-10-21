"""Do not count a long successfully audited submission batch as snapshot lag."""
import unittest
from types import SimpleNamespace
from run_session_20260922 import snapshot_wait_started


class Tests(unittest.TestCase):
    def test_long_batch_with_successful_audits_resets_old_failure(self):
        watcher=SimpleNamespace(last_successful_audit_monotonic=329)
        start=snapshot_wait_started(watcher,0,330)
        self.assertEqual(start,329)
        self.assertLess(330-start,90)

    def test_continuous_failure_still_expires(self):
        watcher=SimpleNamespace(last_successful_audit_monotonic=10)
        start=snapshot_wait_started(watcher,None,11)
        for current in (41,71,102): start=snapshot_wait_started(watcher,start,current)
        self.assertGreater(102-start,90)

    def test_each_success_resets_only_to_real_success_time(self):
        watcher=SimpleNamespace(last_successful_audit_monotonic=80)
        self.assertEqual(snapshot_wait_started(watcher,11,100),80)
        self.assertEqual(snapshot_wait_started(watcher,80,175),80)

    def test_first_failure_without_audit_starts_now(self):
        self.assertEqual(snapshot_wait_started(SimpleNamespace(),None,30),30)

if __name__=='__main__':unittest.main()
