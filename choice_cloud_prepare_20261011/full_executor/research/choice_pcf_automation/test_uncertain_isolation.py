"""An absent receipt can reserve money and block its symbol, never become a failure."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import console_core as core
import console_runtime as runtime
import receipt_recovery as receipts
import test_console
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending


def fixture():
    s=test_console.state();r=test_console.raw()
    s['attempts']=[dict(order=dict(code='SH600001',side=1,quantity=100,price=10,reference_price=10),
        reserve='1011.00',state='UNKNOWN',filled=0,submitted_at='2026-09-23T10:00:00+08:00',submit_failed_at='2026-09-23T10:00:15+08:00')]
    s['targets']['SH600001']=dict(code='SH600001',quantity=100,reference_price=10)
    c=core.config_checked(dict(core.DEFAULTS,isolate_unknown_orders=True))
    return s,r,c


class Isolation(unittest.TestCase):
    def setUp(self):
        self.s,self.r,self.c=fixture();self.now=dt.datetime(2026,9,23,13,5,tzinfo=core.CN)
    def propose(self):return receipts.isolation_candidate(self.r,self.s,self.c,self.now)
    def isolate(self):
        a,_=self.propose();return receipts.isolate(self.s,a,'first','second',self.now.isoformat())
    def test_original_intent_remains_unknown_and_reserve_is_subtracted(self):
        marked=self.isolate();a=core.reconcile(self.r,marked,cash_tolerance_cny=500,allow_quarantined=True)
        self.assertEqual(marked['attempts'][0]['state'],'UNKNOWN');self.assertNotIn('oid',marked['attempts'][0])
        self.assertEqual(marked['attempts'][0]['reserve'],'1011.00')
        self.assertEqual(core.dec(a['cash']),core.dec('8998989'))
        self.assertEqual(a['isolated_codes'],['SH600001']);self.assertEqual(a['uncertain_buy_reserve'],'1011.00')
        self.assertNotIn('isolation',self.s['attempts'][0]);self.assertEqual(marked['baseline'],self.s['baseline'])
    def test_strict_default_still_stops_with_an_isolation_record(self):
        with self.assertRaises(TradeError):core.reconcile(self.r,self.isolate())
    def test_opt_out_cannot_create_isolation(self):
        self.c['isolate_unknown_orders']=False
        with self.assertRaises(TradeError):self.propose()
    def test_cannot_isolate_before_90_seconds_or_next_day(self):
        for when in (self.now.replace(hour=10,minute=1),self.now+dt.timedelta(days=1)):
            self.now=when
            with self.assertRaises(FillSnapshotPending):self.propose()
    def test_changed_positions_or_cash_still_stop(self):
        self.r['holds'][0]['count']=1100
        with self.assertRaises(FillSnapshotPending):self.propose()
        self.s,self.r,self.c=fixture();self.r['balance']['availBalance']='9001395.66'
        with self.assertRaises(FillSnapshotPending):self.propose()
    def test_unconfirmed_small_cash_variance_is_not_combined_with_isolation(self):
        self.r['balance']['availBalance']='9000495.66'
        with self.assertRaisesRegex(FillSnapshotPending,'现金差异尚未确认'):self.propose()
    def test_frozen_or_other_pending_orders_block_isolation(self):
        self.r['balance']['frozenMoney']='100'
        with self.assertRaises(FillSnapshotPending):self.propose()
    def test_external_or_candidate_order_cannot_be_ignored(self):
        self.r['orders']=[dict(orderId='outside',orderDate=self.s['day'],secCode='600099',drt=1,orderCount=100,orderPrice=10,orderType=1,orderTime='10:00:01')]
        with self.assertRaises(TradeError):self.propose()
    def test_unconfirmed_sell_is_never_auto_isolated(self):
        self.s['attempts'][0]['order']['side']=2
        with self.assertRaisesRegex(TradeError,'卖出'):self.propose()
    def test_tampered_reserve_or_order_invalidates_proof(self):
        for mutate in ('order','reserve','date','proof'):
            s=self.isolate()
            if mutate=='order':s['attempts'][0]['order']['quantity']=200
            elif mutate=='reserve':s['attempts'][0]['reserve']='0'
            elif mutate=='date':s['day']='2026-09-24'
            else:s['attempts'][0]['isolation']['verified_snapshots']=1
            with self.assertRaises(TradeError):core.reconcile(self.r,s,cash_tolerance_cny=500,allow_quarantined=True)
    def test_only_one_unknown_is_allowed(self):
        self.s['attempts'].append(copy.deepcopy(self.s['attempts'][0]))
        with self.assertRaises(TradeError):self.propose()
    def test_overnight_roll_does_not_clear_unknown(self):
        s=self.isolate();s['last_audit']=core.reconcile(self.r,s,cash_tolerance_cny=500,allow_quarantined=True)
        with self.assertRaisesRegex(TradeError,'未确认委托'):core.roll_day(s,'2026-09-24')


class IsolationRuntime(unittest.TestCase):
    def setUp(self):
        self.s,self.r,self.c=fixture();self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=object.__new__(runtime.Worker);self.w.run=Path(self.tmp.name);self.w.state=self.s;self.w.c=self.c
        self.w.day=self.s['day'];self.w.check_stop=lambda:False;self.w.http=SimpleNamespace(writes_suspended=False,armed=None)
        self.w.publish=Mock();self.w.broker=Mock();self.w.public=None
        self.now=dt.datetime(2026,9,23,13,5,tzinfo=core.CN)
    def run_audit(self):
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime.time,'sleep'):return self.w.audit()
    def test_two_absent_snapshots_isolate_then_fresh_audit_allows_other_trading(self):
        self.w.raw=Mock(return_value=self.r);a=self.run_audit()
        self.assertEqual(self.w.raw.call_count,3);self.assertEqual(a['isolated_order_count'],1)
        self.assertFalse(self.w.http.writes_suspended);self.assertNotIn('oid',self.w.state['attempts'][0])
        self.w.broker.submit.assert_not_called();self.w.broker.cancel.assert_not_called()
        self.w.raw.reset_mock();self.run_audit();self.assertEqual(self.w.raw.call_count,1)
    def test_other_symbol_can_trade_but_isolated_symbol_is_never_replayed(self):
        self.w.raw=Mock(return_value=self.r);a=self.run_audit()
        self.w.audit=Mock(return_value=a);self.w.cancel=Mock();self.w.last_submit=0
        self.w.broker.submit.return_value='new-safe-order'
        q=dict(code='SH600000',timestamp=self.now.isoformat(),ask=10,last=10,bid=10,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote',return_value=q):self.w.step()
        self.assertEqual(self.w.broker.submit.call_count,1)
        self.assertEqual(self.w.broker.submit.call_args.args[0]['code'],'SH600000')
        self.assertEqual(self.w.state['attempts'][0]['state'],'UNKNOWN')
        self.w.audit=Mock(return_value=dict(a,positions={'SH600000':1200}))
        self.w.broker.submit.reset_mock()
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote') as quote:
            self.w.step()
        self.w.broker.submit.assert_not_called();quote.assert_not_called()
    def test_reserve_remains_counted_against_capital_limit(self):
        self.w.raw=Mock(return_value=self.r);a=self.run_audit();self.w.audit=Mock(return_value=a)
        self.w.cancel=Mock();self.w.last_submit=0;self.w.c['capital_limit']=1002500
        q=dict(code='SH600000',timestamp=self.now.isoformat(),ask=10,last=10,bid=10,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote',return_value=q):self.w.step()
        self.w.broker.submit.assert_not_called()
    def test_late_receipt_reconciles_and_releases_only_uncertain_reserve(self):
        self.w.raw=Mock(return_value=self.r);self.run_audit()
        late=copy.deepcopy(self.r)
        late['orders']=[dict(orderId='late',orderDate=self.s['day'],secCode='600001',drt=1,orderCount=100,orderPrice=10,orderType=1,orderTime='10:00:01',tradeCount=100,status='4')]
        late['deals']=[dict(orderId='late',secCode='600001',drt=1,tradeCount=100,price=10,qsYj=5,ghCost=0,yhs=0)]
        late['holds'].append(dict(secCode='600001',count=100,availCount=0))
        late['balance']['availBalance']=str(core.dec(late['balance']['availBalance'])-1005)
        self.w.raw=Mock(return_value=late);a=self.run_audit()
        self.assertEqual(self.w.raw.call_count,4);self.assertEqual(a['isolated_order_count'],0)
        self.assertEqual(self.w.state['attempts'][0]['oid'],'late')
        self.assertIn('SH600001',self.w.state['blocked_receipt_symbols'])
        self.assertEqual(core.dec(a['cash']),8998995)
        self.w.broker.submit.assert_not_called()


if __name__=='__main__':unittest.main()
