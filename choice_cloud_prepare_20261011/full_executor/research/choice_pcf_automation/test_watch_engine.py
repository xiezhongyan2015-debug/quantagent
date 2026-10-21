"""No-network tests for persistent gap following and partial-fill replacement."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from verify_readonly import ROOT
from watch_engine import Watcher,reconcile,in_session
from quanta_agents.factor_lab_a.choice_pcf import TradeError,FillSnapshotPending
from quanta_agents.factor_lab_a.pcf_automation import CN


class Fake:
    def __init__(self):self.orders=[];self.deals=[];self.log=[];self.fail=False;self.freeze=0
    def read(self):
        positions={};paid=0
        for r in self.deals:positions[r['secCode']]=positions.get(r['secCode'],0)+r['tradeCount'];paid+=r['price']*r['tradeCount']
        return copy.deepcopy(dict(orders=self.orders,deals=self.deals,holds=[dict(secCode=c,count=n) for c,n in positions.items()],balance=dict(availBalance=10000000-paid-self.freeze,frozenMoney=self.freeze)))
    def submit(self,o):
        self.log.append(('submit',dict(o)))
        if self.fail:raise TradeError('lost receipt')
        oid=str(len(self.orders)+1)
        self.orders.append(dict(orderId=oid,secCode=o['code'][2:],drt=1,orderCount=o['quantity'],orderPrice=o['price'],tradeCount=0,status=2,orderDate='2026-09-21'))
        return oid
    def fill(self,oid,n):
        r=next(x for x in self.orders if x['orderId']==oid);r['tradeCount']+=n
        if r['tradeCount']==r['orderCount']:r['status']=4
        self.deals.append(dict(orderId=oid,secCode=r['secCode'],drt=1,tradeCount=n,price=r['orderPrice'],qsYj=0,ghCost=0,yhs=0))
    def cancel(self,oid,o):
        self.log.append(('cancel',oid));r=next(x for x in self.orders if x['orderId']==oid)
        r['status']=7 if r['tradeCount'] else 8


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=dt.datetime(2026,9,21,13,30,tzinfo=CN);self.price=10.;self.b=Fake()
        self.s=dict(day='2026-09-21',prior={},targets={'SH600001':dict(code='SH600001',quantity=300,reference_price=10)},
                    carried_reserve=0,attempts=[],deferred={},quotes={},blocked_codes={},fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3)
        self.w=Watcher(Path(self.tmp.name)/'state.json',self.s,self.b,self.quote,lambda:self.now,self.advance)
        self.net=patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):self.net.stop();self.tmp.cleanup()
    def advance(self,n):self.now+=dt.timedelta(seconds=n)
    def quote(self,code):return dict(code=code,timestamp=self.now.isoformat(),bid=self.price-.01,ask=self.price,last=self.price,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
    def test_waits_for_guard_then_submits_only_once(self):
        self.price=10.4;self.w.tick();self.assertFalse(self.b.log)
        self.price=10.;self.w.tick();self.w.tick();self.assertEqual(len(self.b.orders),1)
    def test_partial_cancel_then_only_remaining_quantity(self):
        self.w.tick();self.b.fill('1',100);self.advance(61);self.w.tick()
        self.assertEqual(self.b.orders[0]['status'],7);self.assertEqual(len(self.b.orders),1)
        self.w.tick();self.advance(61);self.w.tick()
        self.assertEqual([o['orderCount'] for o in self.b.orders],[300,200])
        self.b.fill('2',200);self.w.tick();self.assertEqual(self.w.audit()['positions'],{'SH600001':300})
    def test_unconfirmed_cancel_never_replaced(self):
        self.b.cancel=lambda oid,o:self.b.log.append(('cancel_unconfirmed',oid))
        self.w.tick();self.advance(61);self.w.tick();self.advance(100)
        with self.assertRaises(TradeError):self.w.tick()
        self.assertEqual(len(self.b.orders),1);self.assertEqual(sum(x[0]=='cancel_unconfirmed' for x in self.b.log),1)
    def test_unknown_receipt_is_durable_and_blocks_restart(self):
        self.b.fail=True
        with self.assertRaises(TradeError):self.w.tick()
        self.assertEqual(self.s['attempts'][0]['state'],'UNKNOWN')
        with self.assertRaises(TradeError):self.w.tick()
        self.assertEqual(len(self.b.log),1)
    def test_filled_order_not_bought_again(self):
        self.w.tick();self.b.fill('1',300);self.w.tick();self.assertEqual(len(self.b.orders),1)
    def test_existing_reservations_and_fees_limit_new_orders(self):
        self.s['carried_reserve']=1997000;self.w.tick();self.assertFalse(self.b.orders)
    def test_per_order_cap(self):
        self.s['targets']['SH600001']['quantity']=10100;self.w.tick();self.assertFalse(self.b.orders)
    def test_stale_or_wrong_security_quote_not_submitted(self):
        for change in [dict(timestamp=(self.now-dt.timedelta(seconds=31)).isoformat()),dict(code='SH600002')]:
            self.w.quote=lambda c:dict(self.quote(c),**change);self.w.tick()
        self.assertFalse(self.b.orders)
    def test_blocked_identity(self):
        self.s['blocked_codes']={'SH600001':'identity'};self.w.tick();self.assertFalse(self.b.orders)
    def test_cutoff_and_lunch(self):
        for hour,minute in [(11,40),(12,30),(13,0),(14,50)]:
            self.now=self.now.replace(hour=hour,minute=minute);self.w.tick()
        self.assertFalse(self.b.orders)
    def test_cutoff_cancels_known_only(self):
        self.w.tick();self.now=self.now.replace(hour=14,minute=50);self.w.tick()
        self.assertEqual(self.b.orders[0]['status'],8)
    def test_snapshot_lag_blocks_submission(self):
        self.w.tick();self.b.fill('1',100)
        raw=self.b.read();raw['holds']=[]
        with self.assertRaises(FillSnapshotPending):reconcile(raw,{},self.s['attempts'],self.s['targets'],self.s['day'])
    def test_foreign_order_blocks_all_activity(self):
        self.b.submit(dict(code='SH600999',quantity=100,price=10))
        with self.assertRaises(TradeError):self.w.tick()
    def test_old_zero_fill_cancel_is_harmless_but_pending_history_blocks(self):
        old=dict(orderId='yesterday',secCode='600000',drt=1,orderCount=100,orderPrice=8.15,tradeCount=0,status=8,orderDate='2026-09-20')
        self.b.orders=[old];self.w.tick();self.assertEqual(len(self.s['attempts']),1)
        old['status']=2
        with self.assertRaises(TradeError):self.w.tick()
    def test_price_truncation_deferred(self):
        self.price=9.29;self.s['targets']['SH600001']['reference_price']=9.3;self.w.tick();self.assertFalse(self.b.orders)
    def test_retry_limit(self):
        for _ in range(4):
            self.w.tick();self.advance(61);self.w.tick();self.w.tick();self.advance(61)
        self.assertEqual(len(self.b.orders),3)
    def test_spacing_between_two_submissions(self):
        self.s['targets']['SH600002']=dict(code='SH600002',quantity=100,reference_price=10)
        self.w.tick();times=[dt.datetime.fromisoformat(a['submitted_at']) for a in self.s['attempts']]
        self.assertGreaterEqual((times[1]-times[0]).total_seconds(),5)
    def test_no_overshoot_from_holdings_and_open_orders(self):
        self.w.tick();self.b.fill('1',100);a=self.w.audit()
        self.assertEqual(a['pending'],{'SH600001':200});self.assertEqual(a['missing'],{})

if __name__=='__main__':unittest.main()
