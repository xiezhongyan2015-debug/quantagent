"""Offline rollover and aggregate-risk regression tests; no account access."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_watch_engine import Fake
from watch_engine import Watcher,reconcile
from plan_20260922 import allocate
from quanta_agents.factor_lab_a.choice_pcf import TradeError,FillSnapshotPending
from quanta_agents.factor_lab_a.pcf_automation import CN

DAY='2026-09-22'

class RolloverTests(unittest.TestCase):
    def setUp(self):
        self.base=dict(positions={'SH600001':10000},cash=9900000,paid=100000)
        self.history={'old':dict(code='SH600001',quantity=10000,price=10,filled=10000,state='4')}
        self.targets={'SH600001':dict(quantity=40000,reference_price=10)}
        self.raw=dict(orders=[],deals=[],holds=[dict(secCode='600001',count=10000)],balance=dict(availBalance=9900000,frozenMoney=0))
        self.attempts=[]
        self.net=patch('requests.sessions.Session.request',side_effect=AssertionError('Network forbidden'));self.net.start()
    def tearDown(self):self.net.stop()
    def check(self):return reconcile(self.raw,{},self.attempts,self.targets,DAY,self.base,self.history,5500000)
    def add_fill(self,qty=20000):
        self.attempts=[dict(oid='new',order=dict(code='SH600001',quantity=qty,price=10))]
        self.raw['orders']=[dict(orderId='new',secCode='600001',drt=1,orderCount=qty,orderPrice=10,tradeCount=qty,status=4,orderDate=DAY)]
        self.raw['deals']=[dict(orderId='new',secCode='600001',drt=1,tradeCount=qty,price=10,qsYj=60,ghCost=1,yhs=0)]
        self.raw['holds'][0]['count']+=qty;self.raw['balance']['availBalance']-=qty*10+61
    def test_overnight_holding_is_not_a_new_fill(self):
        r=self.check();self.assertEqual(r['missing'],{'SH600001':30000});self.assertEqual(float(r['daily_paid']),0)
    def test_old_records_optional_and_not_double_counted(self):
        before=self.check()
        self.raw['orders']=[dict(orderId='old',secCode='600001',drt=1,orderCount=10000,orderPrice=10,tradeCount=10000,status=4,orderDate='2026-09-21')]
        self.raw['deals']=[dict(orderId='old',secCode='600001',drt=1,tradeCount=10000,price=10,qsYj=0,ghCost=0,yhs=0)]
        self.assertEqual(before,self.check())
    def test_baseline_plus_new_fills_and_fees(self):
        self.add_fill();r=self.check();self.assertEqual(r['positions']['SH600001'],30000);self.assertEqual(float(r['paid']),300061)
    def test_unknown_overnight_fill_stops(self):
        self.raw['deals']=[dict(orderId='foreign')]
        with self.assertRaises(TradeError):self.check()
    def test_changed_holdings_or_cash_stops(self):
        self.raw['holds'][0]['count']+=100
        with self.assertRaises(FillSnapshotPending):self.check()
        self.raw['holds'][0]['count']-=100;self.raw['balance']['availBalance']+=1
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_new_pending_or_unknown_order_stops(self):
        self.raw['orders']=[dict(orderId='foreign',orderDate=DAY)]
        with self.assertRaises(TradeError):self.check()
    def test_total_cap_includes_prior_paid(self):
        self.add_fill();self.base['paid']=5400000
        with self.assertRaises(TradeError):self.check()
    def test_authorized_expansion_above_old_two_million(self):
        self.add_fill();self.base['paid']=3000000;self.assertEqual(float(self.check()['paid']),3200061)
    def test_combined_holdings_and_pending_cannot_exceed_target(self):
        self.add_fill();self.targets['SH600001']['quantity']=20000
        with self.assertRaises(TradeError):self.check()
    def test_changed_historical_order_stops(self):
        self.raw['orders']=[dict(orderId='old',secCode='600001',drt=1,orderCount=10000,orderPrice=10,tradeCount=9000,status=7,orderDate='2026-09-21')]
        with self.assertRaises(TradeError):self.check()


class AggregateGuardTests(unittest.TestCase):
    def test_existing_security_cost_counts_against_cap(self):
        # The new order itself is legal, but aggregate position cost exceeds 500k.
        b=Fake();now=dt.datetime(2026,9,21,13,30,tzinfo=CN)
        state=dict(day='2026-09-21',prior={},targets={'SH600001':dict(quantity=20000,reference_price=10)},
                   carried_reserve=400000,baseline_security_reserve={'SH600001':400000},attempts=[],deferred={},quotes={},blocked_codes={},
                   fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3,capital_limit=5500000,max_order_value=500000,max_security_value=500000)
        q=lambda c:dict(code=c,timestamp=now.isoformat(),ask=10,last=10,bid=9.99,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
        with tempfile.TemporaryDirectory() as tmp:
            w=Watcher(Path(tmp)/'state.json',state,b,q,lambda:now,lambda _:None);w.tick()
        self.assertFalse(b.orders);self.assertEqual(state['deferred']['SH600001'],'single_security_guard')
    def test_market_value_can_block_below_cost_limit(self):
        b=Fake();now=dt.datetime(2026,9,21,13,30,tzinfo=CN)
        state=dict(day='2026-09-21',prior={},targets={'SH600001':dict(quantity=49000,reference_price=10)},
                   carried_reserve=0,attempts=[],deferred={},quotes={},blocked_codes={},fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3,
                   capital_limit=5500000,max_order_value=500000,max_security_value=500000)
        q=lambda c:dict(code=c,timestamp=now.isoformat(),ask=10.3,last=10.3,bid=10.29,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
        with tempfile.TemporaryDirectory() as tmp:Watcher(Path(tmp)/'state.json',state,b,q,lambda:now,lambda _:None).tick()
        self.assertFalse(b.orders)
    def test_allocation_uses_lots_and_respects_stock_cap(self):
        qty,ideal,value=allocate({'SH600001':1,'SH688001':1},{'SH600001':10,'SH688001':30},100000)
        self.assertGreaterEqual(value,100000);self.assertTrue(all(n%100==0 for n in qty.values()));self.assertGreaterEqual(qty['SH688001'],200)

if __name__=='__main__':unittest.main()
