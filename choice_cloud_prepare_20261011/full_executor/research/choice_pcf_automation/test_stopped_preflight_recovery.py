import copy
import datetime as dt
import unittest
import console_core as core
from test_console import state,raw
from refresh_stopped_ledger import verified_state
from quanta_agents.factor_lab_a.choice_pcf import TradeError


class Recovery(unittest.TestCase):
    def setUp(self):
        self.state=state();self.config=core.config_checked(dict(core.DEFAULTS,cash_drift_tolerance_cny=5000))
        self.now=dt.datetime(2026,9,28,13,30,tzinfo=core.CN)
        self.raw=raw();self.raw['history_complete']=True;self.raw['balance']['availBalance']='9002697.393'
        self.observations=[dict(at=(self.now-dt.timedelta(seconds=seconds)).isoformat(),raw=copy.deepcopy(self.raw)) for seconds in (7,1)]
    def check(self):return verified_state(self.state,self.config,self.observations,self.now)
    def test_positive_drift_is_confirmed_without_rebasing_and_roll_requires_fresh_confirmation(self):
        original=copy.deepcopy(self.state);proposed=self.check()
        self.assertEqual(self.state,original);self.assertEqual(proposed['baseline'],original['baseline'])
        self.assertEqual(proposed['cash_variance']['amount_cny'],'2301.733')
        self.assertEqual(core.dec(proposed['last_audit']['cash']),9000000)
        rolled=core.roll_day(proposed,'2026-09-28')
        self.assertTrue(core.reconcile(self.raw,rolled,cash_tolerance_cny=5000)['cash_drift_requires_confirmation'])
    def test_existing_attempts_not_allowed_even_if_acknowledged(self):
        self.state['attempts']=[dict(oid='example')]
        with self.assertRaises(TradeError):self.check()
    def test_bad_cash_or_holdings_or_frozen_money_still_stop(self):
        for kind in ('cash','holdings','frozen'):
            with self.subTest(kind=kind):
                self.setUp();r=self.observations[1]['raw']
                if kind=='cash':r['balance']['availBalance']='9005395.67'
                elif kind=='holdings':r['holds'][0]['count']+=100
                else:r['balance']['frozenMoney']=100
                with self.assertRaises(TradeError):self.check()
    def test_unstable_cash_and_missing_history_stop(self):
        self.observations[1]['raw']['balance']['availBalance']='9002697.403'
        with self.assertRaises(TradeError):self.check()
        self.setUp();self.observations[0]['raw']['history_complete']=False
        with self.assertRaises(TradeError):self.check()
    def test_future_or_one_snapshot_rejected(self):
        self.observations[1]['at']=(self.now+dt.timedelta(seconds=1)).isoformat()
        with self.assertRaises(TradeError):self.check()
        self.setUp();self.observations.pop()
        with self.assertRaises(TradeError):self.check()


if __name__=='__main__':unittest.main()
