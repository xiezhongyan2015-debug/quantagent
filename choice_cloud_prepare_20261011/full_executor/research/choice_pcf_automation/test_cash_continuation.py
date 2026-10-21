"""Offline checks: exact cash exclusion, unexplained differences, two-pass gate."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import run_cash_continuation_20260922 as continuation
from watch_engine import reconcile
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending
from quanta_agents.factor_lab_a.pcf_automation import CN


class Tests(unittest.TestCase):
    def setUp(self):
        self.raw=dict(orders=[], deals=[], holds=[], balance=dict(availBalance='10000395.66', frozenMoney=0))

    def test_exclusion_keeps_original_baseline_and_source_snapshot(self):
        original=copy.deepcopy(self.raw)
        result=reconcile(continuation.quarantine(self.raw),{},[],{},'2026-09-22')
        self.assertEqual(result['cash'],'10000000.0')
        self.assertEqual(self.raw,original)

    def test_any_additional_cash_difference_still_blocks(self):
        self.raw['balance']['availBalance']='10000396.66'
        with self.assertRaises(FillSnapshotPending):
            reconcile(continuation.quarantine(self.raw),{},[],{},'2026-09-22')

    def test_low_cash_cannot_spend_quarantine(self):
        self.raw['balance']['availBalance']='395.65'
        with self.assertRaises(TradeError): continuation.quarantine(self.raw)

    def test_foreign_order_still_blocks(self):
        self.raw['orders']=[dict(orderId='foreign',orderDate='2026-09-22')]
        with self.assertRaises(TradeError): reconcile(continuation.quarantine(self.raw),{},[],{},'2026-09-22')

    def test_two_pass_gate_and_second_pass_failure(self):
        now=dt.datetime(2026,9,22,10,55,tzinfo=CN)
        for failure in (False,True):
            state=dict(day='2026-09-22',prior={},attempts=[],targets={})
            broker=unittest.mock.Mock()
            broker.read.side_effect=[continuation.quarantine(self.raw),TradeError('changed') if failure else continuation.quarantine(self.raw)]
            sleeper=unittest.mock.Mock()
            with tempfile.TemporaryDirectory() as tmp:
                w=continuation.VerifiedWatcher(Path(tmp)/'state.json',state,broker,None,lambda:now,sleeper)
                if failure:
                    with self.assertRaises(TradeError):w.audit()
                    self.assertNotIn('continuation_preflight_verified_at',state)
                else:
                    w.audit();self.assertIn('continuation_preflight_verified_at',state)
                self.assertEqual(broker.read.call_count,2)
                sleeper.assert_called_once_with(5)

if __name__=='__main__': unittest.main()
