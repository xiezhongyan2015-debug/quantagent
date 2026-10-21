import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_watch_engine import Fake
from run_snapshot_resume_20260922 import ResumeWatcher
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import CN


class Tests(unittest.TestCase):
    def test_inherited_filled_order_is_verified_twice_and_not_rebought(self):
        b=Fake();order=dict(code='SH600001',quantity=300,price=10,side=1,reference_price=10)
        oid=b.submit(order);b.fill(oid,300);b.orders[0]['orderDate']='2026-09-22'
        now=dt.datetime(2026,9,22,13,30,tzinfo=CN)
        state=dict(day='2026-09-22',prior={},attempts=[dict(oid=oid,order=order,state='4',filled=300,terminal=True,terminal_at=now.isoformat(),reserve='3013')],
                   targets={'SH600001':dict(quantity=300,reference_price=10)},continuation_preflight_verified_at=now.isoformat(),snapshot_resume=dict(inherited_attempts=1))
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'read',wraps=b.read) as reads:
            sleeper=Mock();quote=Mock(side_effect=AssertionError('No quote required for completed target'))
            w=ResumeWatcher(Path(tmp)/'state.json',state,b,quote,lambda:now,sleeper)
            w.tick()
            self.assertEqual(reads.call_count,2)
            sleeper.assert_called_once_with(5)
            self.assertEqual(len(b.orders),1)
            self.assertEqual(len(state['attempts']),1)
            self.assertIn('preflight_verified_at',state['snapshot_resume'])

    def test_second_preflight_mismatch_prevents_tick_submissions(self):
        b=Fake();now=dt.datetime(2026,9,22,13,30,tzinfo=CN)
        raw=b.read()
        state=dict(day='2026-09-22',prior={},attempts=[],targets={'SH600001':dict(quantity=300,reference_price=10)},
                   continuation_preflight_verified_at=now.isoformat(),snapshot_resume=dict(inherited_attempts=0))
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'read',side_effect=[raw,TradeError('Mismatch')]):
            w=ResumeWatcher(Path(tmp)/'state.json',state,b,None,lambda:now,lambda _:None)
            with self.assertRaises(TradeError):w.tick()
            self.assertFalse(b.orders)
            self.assertNotIn('preflight_verified_at',state['snapshot_resume'])

if __name__=='__main__':unittest.main()
