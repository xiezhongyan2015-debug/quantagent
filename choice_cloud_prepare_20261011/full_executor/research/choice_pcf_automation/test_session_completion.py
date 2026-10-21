"""Completion is based on reconciled holdings, not acknowledgment/fill counts."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_watch_engine import Tests as WatchTests
from run_session_20260922 import target_complete,confirm_completion
from quanta_agents.factor_lab_a.choice_pcf import FillSnapshotPending,TradeError

class CompletionTests(unittest.TestCase):
    setUp=WatchTests.setUp
    tearDown=WatchTests.tearDown
    quote=WatchTests.quote
    advance=WatchTests.advance
    def test_completion_two_fresh_checks_no_orders(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.w.tick();self.b.fill('1',300);self.w.audit();before=len(self.b.log)
        self.assertTrue(target_complete(self.w))
        with patch.object(self.w,'audit',wraps=self.w.audit) as audits:
            self.assertTrue(confirm_completion(self.w));self.assertEqual(audits.call_count,2)
        self.assertEqual(len(self.b.log),before)
        self.assertIn('completion_verified_at',self.s)
    def test_pending_order_is_not_complete_even_if_missing_is_empty(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.w.tick();self.w.audit()
        self.assertFalse(self.s['last_audit']['missing']);self.assertFalse(target_complete(self.w))
    def test_deferred_unsent_stock_prevents_completion(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.price=10.4;self.w.tick();self.assertFalse(target_complete(self.w))
    def test_second_audit_fails_without_marking_complete(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.w.tick();self.b.fill('1',300);self.w.audit()
        original=self.w.audit
        with patch.object(self.w,'audit',side_effect=[original(),FillSnapshotPending('lag')]):
            with self.assertRaises(FillSnapshotPending):confirm_completion(self.w)
        self.assertNotIn('completion_verified_at',self.s)
    def test_paused_auth_or_unknown_intent_cannot_finish(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.w.tick();self.b.fill('1',300);self.w.audit()
        self.b.session.writes_suspended=True;self.assertFalse(target_complete(self.w))
        self.b.session.writes_suspended=False
        self.s['attempts'].append({'state':'UNKNOWN'});self.assertFalse(target_complete(self.w))
    def test_stop_prevents_completion_confirmation(self):
        self.b.session=SimpleNamespace(writes_suspended=False)
        self.w.tick();self.b.fill('1',300);self.w.audit()
        self.w.path.with_name('STOP').touch();self.assertFalse(confirm_completion(self.w))

del WatchTests
