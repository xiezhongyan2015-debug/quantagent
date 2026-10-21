"""Offline batch tests: no credentials, no HTTP, only temporary journals."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from verify_readonly import ROOT
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.pcf_automation import Journal,CN,write_json,order_spacing
from batch_engine import execute_batch,stable_price_at_ask


class FakeBroker(ChoiceBroker):
    def __init__(self,fill=True,fail_at=None):
        self.account='fake';self.contract={'initial_buy_only':True};self.rows=[];self.log=[];self.fill=fill;self.fail_at=fail_at
    def snapshot(self):return {'cash':10000000.,'positions':{},'frozen':0}
    def submit(self,o):
        self.log.append(('submit',o['code']))
        if self.fail_at==len(self.log):raise TradeError('Simulated lost submission response')
        oid='fake-'+o['code']
        self.rows.append({'orderId':oid,'secCode':o['code'][2:],'drt':1,'orderCount':o['quantity'],
                          'orderPrice':o['price'],'tradeCount':o['quantity'] if self.fill else 0,
                          'status':4 if self.fill else 2,'orderDate':'2026-09-21'})
        return oid
    def _rows(self,path):
        self.log.append(('read',path))
        if path.endswith('SpoOrders'):return [dict(r) for r in self.rows]
        if path.endswith('SpoHold'):return [{'secCode':r['secCode'],'count':r['tradeCount']} for r in self.rows if r['tradeCount']]
        return [{'secCode':r['secCode'],'drt':1,'tradeCount':r['tradeCount'],'orderId':r['orderId']} for r in self.rows if r['tradeCount']]
    def _request(self,*a,**kw):return {'Data':{'availBalance':10000000.,'frozenMoney':0}}
    def cancel(self,oid,o):
        self.log.append(('cancel',o['code']))
        for r in self.rows:
            if r['orderId']==oid:r['status']=8


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=dt.datetime(2026,9,21,10,45,tzinfo=CN)
        self.cfg={'capital_limit':2000000,'max_order_value':100000,'max_price_move':.03,'quote_max_age_seconds':30,
                  'state_dir':str(self.root/'state'),'calendar':str(self.root/'calendar.json'),'execution_fee_buffer_per_order':10,
                  'fill_timeout_seconds':0,'cancel_timeout_seconds':0,'poll_seconds':0}
        Path(self.cfg['state_dir']).mkdir();write_json(self.cfg['calendar'],['2026-09-21'])
        self.j=Journal(self.root/'state/test.sqlite','fake')
        self.plan={'execution_date':'2026-09-21','initial_positions':{},'orders':[
            {'code':c,'quantity':100,'side':1,'reference_price':10.} for c in ('SH600001','SH600002')]}
        self.no_net=patch('requests.sessions.Session.request',side_effect=AssertionError('No network'));self.no_net.start()
    def tearDown(self):self.no_net.stop();self.j.db.close();self.temp.cleanup()
    def quote(self,code):return {'code':code,'timestamp':self.now.isoformat(),'last':10.,'ask':10.,'bid':9.99,'upper_limit':11.,'lower_limit':9.,'tradable':1,'is_st':0}
    def run_batch(self,b,provider=None):return execute_batch(self.cfg,self.plan,b,self.j,provider or self.quote,{},clock=lambda:self.now,sleeper=lambda _:None)
    def test_all_submissions_precede_first_fill_query(self):
        b=FakeBroker();result=self.run_batch(b)
        self.assertEqual(b.log[:2],[('submit','SH600001'),('submit','SH600002')])
        self.assertEqual(result['filled_orders'],2);self.assertEqual(result['status'],'complete')

    def test_slow_dispatch_is_applied_and_next_quote_fetched_after_wait(self):
        b=FakeBroker();events=[]
        self.cfg['order_spacing_seconds']=1  # Historic config must not bypass minimum.
        def quote(code):
            events.append(('quote',code));return self.quote(code)
        def wait(seconds):events.append(('sleep',seconds))
        result=execute_batch(self.cfg,self.plan,b,self.j,quote,{},clock=lambda:self.now,sleeper=wait)
        self.assertEqual(events[:3],[('quote','SH600001'),('sleep',5.),('quote','SH600002')])
        self.assertEqual(result['filled_orders'],2)

    def test_spacing_validates_before_any_submission(self):
        b=FakeBroker()
        for value in (float('nan'),float('inf'),-1):
            with self.assertRaises(TradeError):order_spacing({'order_spacing_seconds':value},b)
        self.assertEqual(order_spacing({},b),5)
        self.assertEqual(order_spacing({'order_spacing_seconds':8},b),8)
        self.assertEqual(b.log,[])
    def test_known_unfilled_cancel_once_then_confirm(self):
        b=FakeBroker(fill=False);result=self.run_batch(b)
        self.assertEqual(result['cancelled_orders'],2);self.assertEqual(result['unresolved_codes'],[])
        self.assertEqual(len([r for r in b.log if r[0]=='cancel']),2)
        self.assertEqual(len([r for r in b.log if r[0]=='submit']),2)
    def test_unknown_submission_stops_no_retry(self):
        b=FakeBroker(fail_at=2);result=self.run_batch(b)
        self.assertEqual(result['status'],'review_required')
        self.assertEqual(result['unresolved_codes'],['SH600002'])
        self.assertEqual(len([r for r in b.log if r[0]=='submit']),2)
    def test_price_guard_defers_before_network(self):
        self.plan['orders'][0]['reference_price']=9.
        b=FakeBroker();result=self.run_batch(b)
        self.assertEqual(result['price_deferred'],['SH600001']);self.assertEqual(result['submitted'],1)
    def test_stale_quote_sends_nothing(self):
        b=FakeBroker()
        def stale(c):return dict(self.quote(c),timestamp=(self.now-dt.timedelta(seconds=31)).isoformat())
        result=self.run_batch(b,stale)
        self.assertEqual(result['submitted'],0);self.assertEqual(result['status'],'review_required')
    def test_existing_reserve_counts_against_whole_batch(self):
        self.j.reserve_capital('2026-09-21','PRIOR',1999500,2000000)
        result=self.run_batch(FakeBroker())
        self.assertEqual(result['submitted'],0)
        self.assertIn('capital',result['dispatch_failure'])

    def test_observed_one_cent_truncation_never_silently_raises_limit(self):
        for price in (4.31,9.29,8.53,16.65,144.98):
            self.assertIsNone(stable_price_at_ask(price))
        self.assertEqual(stable_price_at_ask(4.31,1),4.32)
        self.assertEqual(stable_price_at_ask(9.29,1),9.30)
        self.assertIsNone(stable_price_at_ask(8.53,1))
        self.assertEqual(stable_price_at_ask(47.54),47.54)

    def test_authorized_one_cent_padding_still_cannot_cross_three_percent_guard(self):
        self.cfg['authorized_padding_ticks_by_code']={'SH600001':1}
        self.plan['orders'][0]['reference_price']=9.02
        def current(code):
            if code=='SH600001':return dict(self.quote(code),ask=9.29,last=9.29,bid=9.28)
            return self.quote(code)
        result=self.run_batch(FakeBroker(),current)
        self.assertEqual(result['price_deferred'],['SH600001'])
        self.assertEqual(result['submitted'],1)


if __name__=='__main__':unittest.main()
