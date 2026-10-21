"""Fault-injection tests: staged sell accounting, transport boundary and guards."""
import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import session23_engine as e
import run_session_20260923 as runner
from run_intraday_watch import Adapter, ACCOUNT, BASE
from quanta_agents.factor_lab_a.choice_pcf import TradeError, FillSnapshotPending, order_payload

NOW=dt.datetime.fromisoformat('2026-09-23T09:35:00+08:00')
A='SH600060';B='SZ000050'


def fixture():
    s=dict(day=e.DAY,pcf_sha256='sealed',historical={},baseline=dict(positions={A:200},cash='8000',paid='2000'),
        targets={A:dict(quantity=100,reference_price=10),B:dict(quantity=200,reference_price=10)},requires_sell_orders=True,
        attempts=[],deferred={},quotes={},quote_failures={})
    raw=dict(orders=[],deals=[],holds=[dict(secCode=A,count=200,availCount=200)],
        balance=dict(availBalance=8395.66,frozenMoney=0,totalPosValue=2000,totalAssets=10395.66))
    return s,raw


class PaperExchange:
    def __init__(self,raw):self.raw=raw;self.sends=[]
    def _rows(self,path):
        return copy.deepcopy(self.raw[{'AguTrade/SpoOrders':'orders','AguTrade/SpoDeal':'deals','AguTrade/SpoHold':'holds'}[path]])
    def _request(self,*args,**kw):return dict(Data=copy.deepcopy(self.raw['balance']))
    def order_status(self,oid,o):
        r=next(x for x in self.raw['orders'] if x['orderId']==oid)
        return dict(state=r['status'],filled=r['tradeCount'])
    def request(self,session,method,url,**kw):
        p=kw['json'];self.sends.append(copy.deepcopy(p));oid=str(len(self.sends));side=p['orderDrt'];qty=p['volume'];px=float(p['price']);c=('SH' if p['mktCode']=='1' else 'SZ')+p['stkCode']
        self.raw['orders'].append(dict(orderId=oid,orderDate='20260923',secCode=c,drt=side,orderCount=qty,orderPrice=px,tradeCount=qty,status='4'))
        self.raw['deals'].append(dict(orderId=oid,secCode=c,drt=side,tradeCount=qty,price=px,qsYj=5,ghCost=0,yhs=0))
        self.raw['balance']['availBalance']=round(self.raw['balance']['availBalance']+(px*qty-5 if side==2 else -px*qty-5),2)
        h=next((h for h in self.raw['holds'] if h['secCode']==c),None)
        if h is None:h=dict(secCode=c,count=0,availCount=0);self.raw['holds'].append(h)
        h['count']+=qty if side==1 else -qty
        if side==2:h['availCount']-=qty
        self.raw['balance']['totalPosValue']=sum(h['count']*10 for h in self.raw['holds'])
        return type('Response',(),dict(status_code=200,json=lambda _:dict(Code=0,Data=dict(orderID=oid))))()


class Session23Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'private_state.json';self.now=NOW
        self.s,self.raw=fixture();self.exchange=PaperExchange(self.raw)
        self.transport=e.PlanSession(Path(self.tmp.name)/'trace.jsonl')
        self.adapter=Adapter(e.PlanBroker(self.exchange,self.transport),self.transport)
        self.w=e.Watcher(self.path,self.s,self.adapter,self.quote,lambda:self.now,self.advance);self.transport.watcher=self.w
        self.env=patch.dict(os.environ,CHOICE_UTOKEN='fake',CHOICE_CTOKEN='fake',CHOICE_UID='fake');self.env.start()
        self.clock=patch.object(e,'now_cn',lambda:self.now);self.clock.start()
        exchange=self.exchange
        self.net=patch('requests.Session.request',lambda session,method,url,**kw:exchange.request(session,method,url,**kw));self.net.start()
    def tearDown(self):self.net.stop();self.clock.stop();self.env.stop();self.transport.close();self.tmp.cleanup()
    def advance(self,seconds):self.now+=dt.timedelta(seconds=seconds)
    def quote(self,c):return dict(code=c,timestamp=self.now.isoformat(),tradable=1,is_st=0,bid=10.,ask=10.,last=10.,previous_close=10.,lower_limit=9.,upper_limit=11.)
    def order(self,c=A,side=2,qty=100,price=10.):return dict(code=c,side=side,quantity=qty,price=price,reference_price=10.)
    def prime(self,o):self.w.audit();self.s['quotes'][o['code']]=self.quote(o['code'])
    def buy_ready(self):
        self.s['targets'][A]['quantity']=200;self.s['requires_sell_orders']=False
        o=self.order(B,1,200);self.prime(o);return o
    def test_first_sell_then_two_reconciliations_then_buy(self):
        self.assertEqual(self.w.tick(),'verifying_first_planned_sell')
        self.assertEqual([x['orderDrt'] for x in self.exchange.sends],[2]);self.assertNotIn('sell_contract_verified_at',self.s)
        self.assertEqual(self.s['attempts'][0]['order']['quantity'],100)
        self.advance(5);self.w.tick();a=self.w.audit()
        self.assertEqual([x['orderDrt'] for x in self.exchange.sends],[2,1]);self.assertIn('sell_contract_verified_at',self.s)
        self.assertEqual(a['positions'],{A:100,B:200});self.assertEqual(e.D(a['cash']),e.D(6990));self.assertEqual(e.D(a['fees']),e.D(10))
        self.assertEqual(e.D(a['paid']),e.D(3010));self.assertTrue(runner.target_complete(self.w))
    def test_premarket_never_submits(self):
        self.now=self.now.replace(hour=9,minute=20);self.assertEqual(self.w.tick(),'session_paused');self.assertFalse(self.exchange.sends)
    def test_close_never_submits(self):
        self.now=self.now.replace(hour=14,minute=50);self.assertEqual(self.w.tick(),'stopped');self.assertFalse(self.exchange.sends)
    def test_probe_inventory_mismatch_cannot_certify(self):
        self.w.tick();self.raw['holds'][0]['count']=200
        with self.assertRaises(FillSnapshotPending):self.w.tick()
        self.assertNotIn('sell_contract_verified_at',self.s);self.assertEqual(len(self.exchange.sends),1)
    def test_probe_cash_mismatch_cannot_certify(self):
        self.w.tick();self.raw['balance']['availBalance']+=5
        with self.assertRaises(FillSnapshotPending):self.w.tick()
        self.assertNotIn('sell_contract_verified_at',self.s)
    def test_unknown_intent_stops(self):
        self.s['attempts'].append(dict(order=self.order(),state='UNKNOWN',filled=0))
        with self.assertRaises(TradeError):self.w.audit()
        self.assertFalse(self.exchange.sends)
    def test_uncertain_response_never_retries(self):
        with patch.object(e.PlanBroker,'post',side_effect=TradeError('uncertain')):
            with self.assertRaises(TradeError):self.w.tick()
        self.assertEqual(self.s['attempts'][0]['state'],'UNKNOWN')
        with self.assertRaises(TradeError):self.w.tick()
    def test_cannot_buy_before_reductions(self):
        o=self.order(B,1,200);self.prime(o)
        with self.assertRaisesRegex(TradeError,'Complete reductions'):self.w.validate_order(o,self.now)
    def test_sellable_t_plus_one_enforced(self):
        self.raw['holds'][0]['availCount']=0;o=self.order();self.prime(o)
        with self.assertRaisesRegex(TradeError,r'T\+1'):self.w.validate_order(o,self.now)
    def test_first_sell_cannot_be_large(self):
        self.s['targets'][A]['quantity']=0;o=self.order(qty=200);self.prime(o)
        with self.assertRaisesRegex(TradeError,'first plan-required'):self.w.validate_order(o,self.now)
    def test_sell_not_blocked_by_three_percent_drop(self):
        o=self.order(price=9.5);self.prime(o);self.s['quotes'][A].update(bid=9.5,ask=9.51,last=9.5)
        self.w.validate_order(o,self.now)
    def test_buy_over_three_percent_rejected(self):
        o=self.buy_ready();o['price']=10.31;self.s['quotes'][B].update(bid=10.3,ask=10.31,last=10.31)
        with self.assertRaisesRegex(TradeError,'chase'):self.w.validate_order(o,self.now)
    def test_deep_drop_requires_evidence(self):
        o=self.buy_ready();o['price']=9.6;self.s['quotes'][B].update(bid=9.59,ask=9.6,last=9.6)
        with self.assertRaisesRegex(TradeError,'evidenced review'):self.w.validate_order(o,self.now)
        review=dict(execution_date=e.DAY,code=B,pcf_sha256='sealed',reference_price=10,status='cleared',announcements_checked=True,corporate_actions_checked=True,quote_verified=True,
            evidence_urls=['https://www.szse.cn/example'],reviewed_at=self.now.isoformat(),expires_at=(self.now+dt.timedelta(minutes=20)).isoformat())
        self.path.with_name('drop_reviews.json').write_text(json.dumps({B:review}),encoding='utf-8');self.w.validate_order(o,self.now)
        review['pcf_sha256']='wrong';self.path.with_name('drop_reviews.json').write_text(json.dumps({B:review}),encoding='utf-8')
        with self.assertRaisesRegex(TradeError,'evidenced review'):self.w.validate_order(o,self.now)
    def test_near_limit_down_no_buy(self):
        o=self.buy_ready();o['price']=9.01;self.s['quotes'][B].update(bid=9,ask=9.01,last=9.01)
        with self.assertRaisesRegex(TradeError,'Near lower'):self.w.validate_order(o,self.now)
    def test_abnormal_spread_no_buy(self):
        o=self.buy_ready();self.s['quotes'][B]['bid']=9.8
        with self.assertRaisesRegex(TradeError,'spread'):self.w.validate_order(o,self.now)
    def test_reference_change_no_buy(self):
        o=self.buy_ready();self.s['quotes'][B]['previous_close']=9.5
        with self.assertRaisesRegex(TradeError,'Corporate-action'):self.w.validate_order(o,self.now)
    def test_stale_quote_no_buy(self):
        o=self.buy_ready();self.s['quotes'][B]['timestamp']=(self.now-dt.timedelta(seconds=31)).isoformat()
        with self.assertRaisesRegex(TradeError,'stale'):self.w.validate_order(o,self.now)
    def test_cap_counts_existing_paid(self):
        o=self.buy_ready();self.s['last_audit']['paid']='5499000'
        with self.assertRaisesRegex(TradeError,'Capital'):self.w.validate_order(o,self.now)
    def test_single_stock_cap_counts_existing_holding(self):
        o=self.buy_ready();self.s['last_audit']['positions'][B]=49900;self.s['targets'][B]['quantity']=50100
        with self.assertRaisesRegex(TradeError,'single-security'):self.w.validate_order(o,self.now)
    def test_foreign_order_is_not_adopted(self):
        self.raw['orders']=[dict(orderId='foreign',orderDate='20260923')]
        with self.assertRaisesRegex(TradeError,'Foreign'):self.w.audit()
    def test_prior_day_query_clear_is_valid_only_with_exact_baseline(self):
        self.s['historical']={'old':dict(code=A,side=1,quantity=200,price=10,filled=200,state='4')};self.w.audit()
        self.raw['balance']['availBalance']+=1
        with self.assertRaises(FillSnapshotPending):self.w.audit()
    def test_transport_rejects_unarmed_or_changed_payload(self):
        o=self.order();self.prime(o);p=order_payload(ACCOUNT,A,2,100,10)
        with self.assertRaises(TradeError):self.transport.post(BASE+'AguTrade/SpoOrder',json=p,allow_redirects=False)
        self.transport.armed=('submit',o);p['volume']=200
        with self.assertRaises(TradeError):self.transport.post(BASE+'AguTrade/SpoOrder',json=p,allow_redirects=False)
        self.assertFalse(self.exchange.sends)
    def test_transport_requires_durable_intent(self):
        o=self.order();self.prime(o)
        with self.assertRaisesRegex(TradeError,'durable'):self.adapter.submit(o)
        self.assertFalse(self.exchange.sends)
    def test_transport_requires_second_gate(self):
        o=self.order();self.prime(o);self.s['attempts'].append(dict(order=o,state='SENDING',filled=0))
        self.transport.writes_suspended=True
        with self.assertRaises(TradeError):self.adapter.submit(o)
    def test_date_and_launch_boundaries(self):
        self.assertEqual(runner.launch_deadline(NOW.replace(minute=20)).minute,50)
        for when in (NOW-dt.timedelta(days=1),NOW+dt.timedelta(days=1),NOW.replace(minute=19),NOW.replace(minute=51)):
            with self.assertRaises(TradeError):runner.launch_deadline(when)
    def test_missing_fill_detail_blocks_sell_validation(self):
        self.w.tick();self.raw['deals']=[]
        with self.assertRaises(FillSnapshotPending):self.w.audit()
    def test_terminal_order_cannot_regress(self):
        self.w.tick();self.w.audit();self.raw['orders'][0]['status']='3'
        with self.assertRaisesRegex(TradeError,'identity/state'):self.w.audit()
    def test_partial_sell_cancel_accounts_only_filled_net_cash(self):
        o=self.order(qty=200);self.s['targets'][A]['quantity']=0
        self.s['attempts']=[dict(order=o,oid='p',state='ACK',filled=0)]
        self.raw['orders']=[dict(orderId='p',orderDate='20260923',secCode=A,drt=2,orderCount=200,orderPrice=10,tradeCount=100,status='7')]
        self.raw['deals']=[dict(orderId='p',secCode=A,drt=2,tradeCount=100,price=10,qsYj=5,ghCost=.01,yhs=.5)]
        self.raw['holds'][0].update(count=100,availCount=100);self.raw['balance']['availBalance']=9390.15
        a=self.w.audit();self.assertEqual(e.D(a['cash']),e.D('8994.49'));self.assertEqual(a['missing'][A],-100);self.assertFalse(a['pending'])
    def test_pending_buy_cap_includes_reserved_fees_and_freeze(self):
        o=self.buy_ready();self.s['last_audit'].update(paid='5496000',frozen='2500')
        with self.assertRaisesRegex(TradeError,'Capital'):self.w.validate_order(o,self.now)
    def test_cancel_sent_once_until_terminal(self):
        o=self.order();self.s['attempts']=[dict(order=o,oid='pending',state='1',filled=0,submitted_at=(self.now-dt.timedelta(seconds=61)).isoformat())]
        with patch.object(self.adapter,'cancel') as cancel:
            self.w.cancel_known();self.w.cancel_known();self.assertEqual(cancel.call_count,1)
            self.advance(61)
            with self.assertRaisesRegex(TradeError,'Cancel not confirmed'):self.w.cancel_known()
            self.assertEqual(cancel.call_count,1)
    def test_star_cannot_reduce_only_100_out_of_large_position(self):
        c='SH688728';self.s['targets'][c]=dict(quantity=2500,reference_price=10);o=self.order(c,2,100)
        self.prime(o);self.s['sell_contract_verified_at']=self.now.isoformat();self.s['last_audit']['positions'][c]=2600;self.s['last_audit']['sellable'][c]=2600
        with self.assertRaisesRegex(TradeError,'STAR sell'):self.w.validate_order(o,self.now)


if __name__=='__main__':unittest.main()
