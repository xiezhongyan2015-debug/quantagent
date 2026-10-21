"""Offline tests for multi-day control, configurable risk and exact buy/sell accounting."""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import console_core as core
import console_runtime as runtime
from quanta_agents.factor_lab_a.choice_pcf import TradeError,FillSnapshotPending
from quanta_agents.factor_lab_a.pcf_automation import CN,digest


def state():
    return dict(day='2026-09-23',baseline=dict(positions={'SH600000':1000},cash='9000000',paid='1000000'),
        prior={},historical={},attempts=[],targets={'SH600000':dict(quantity=1200,reference_price=10)},carried_reserve='1000000',deferred={})


def raw():
    return dict(orders=[],deals=[],holds=[dict(secCode='600000',count=1000,availCount=1000)],
        balance=dict(availBalance='9000395.66',frozenMoney=0,totalPosValue=10000,totalAssets=9010395.66))


class Accounting(unittest.TestCase):
    def setUp(self):self.s=state();self.r=raw()
    def check(self):return core.reconcile(self.r,self.s)
    def order(self,side,qty,price=10,filled=None,status='4',fee=5):
        filled=qty if filled is None else filled;oid=str(len(self.s['attempts'])+1)
        o=dict(code='SH600000',side=side,quantity=qty,price=price)
        self.s['attempts'].append(dict(oid=oid,order=o,state='ACK',filled=0,reserve='2012',submitted_at='2026-09-23T10:00:00+08:00'))
        self.r['orders'].append(dict(orderId=oid,orderDate='2026-09-23',secCode='600000',drt=side,orderCount=qty,orderPrice=price,tradeCount=filled,status=status))
        if filled:
            self.r['deals'].append(dict(orderId=oid,secCode='600000',drt=side,tradeCount=filled,price=price,qsYj=fee,ghCost=0,yhs=0))
            self.r['holds'][0]['count']+=filled if side==1 else -filled
            if side==2:self.r['holds'][0]['availCount']-=filled
            net=price*filled+fee if side==1 else -price*filled+fee
            self.r['balance']['availBalance']=str(core.dec(self.r['balance']['availBalance'])-core.dec(net))
        return oid
    def test_cash_exclusion_and_available_shares(self):
        a=self.check();self.assertEqual(core.dec(a['cash']),9000000);self.assertEqual(a['sellable']['SH600000'],1000)
    def test_buy_fill_and_fee(self):
        self.order(1,200);a=self.check();self.assertEqual(a['positions']['SH600000'],1200);self.assertEqual(core.dec(a['daily_paid']),2005)
    def test_sell_fill_adds_cash_after_fee(self):
        self.order(2,200);a=self.check();self.assertEqual(a['positions']['SH600000'],800);self.assertEqual(core.dec(a['cash']),9001995);self.assertEqual(core.dec(a['daily_paid']),-1995)
    def test_partial_sell_and_pending_preserved(self):
        oid=self.order(2,400,filled=100,status='3');a=self.check();self.assertEqual(a['pending'][oid]['quantity'],300)
    def test_buy_and_sell_same_day_account_exactly(self):
        self.order(1,200);self.order(2,100);a=self.check();self.assertEqual(a['positions']['SH600000'],1100);self.assertEqual(core.dec(a['daily_paid']),1010)
    def test_cash_drift_still_blocks(self):
        self.r['balance']['availBalance']='9000396.66'
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_unknown_intent_cannot_replay(self):
        self.s['attempts'].append(dict(order={}))
        with self.assertRaises(TradeError):self.check()
    def test_external_order_blocks(self):
        self.r['orders'].append(dict(orderId='external',orderDate='2026-09-23'))
        with self.assertRaises(TradeError):self.check()
    def test_fill_lag_waits(self):
        self.order(1,200);self.r['deals']=[]
        with self.assertRaises(FillSnapshotPending):self.check()
    def test_wrong_sell_fill_price_blocks(self):
        self.order(2,200);self.r['deals'][0]['price']=9
        with self.assertRaises(TradeError):self.check()
    def test_duplicate_order_blocks(self):
        self.order(1,200);self.r['orders']*=2
        with self.assertRaises(TradeError):self.check()
    def test_invalid_available_quantity_blocks(self):
        self.r['holds'][0]['availCount']=1001
        with self.assertRaises(TradeError):self.check()
    def test_overnight_roll_carries_exact_history(self):
        self.order(1,200);a=self.check();self.s['attempts'][0].update(a['states']['1']);self.s['last_audit']=a
        nextday=core.roll_day(self.s,'2026-09-24')
        self.assertFalse(nextday['attempts']);self.assertEqual(nextday['historical']['1']['filled'],200)
        self.assertEqual(nextday['baseline']['positions'],{'SH600000':1200})
        self.assertEqual(nextday['baseline']['cash'],a['cash'])
    def test_overnight_pending_cannot_reset(self):
        self.order(1,200,filled=0,status='2');self.s['last_audit']=self.check()
        with self.assertRaises(TradeError):core.roll_day(self.s,'2026-09-24')
    def test_same_day_keeps_all_attempts(self):
        self.order(1,200);self.assertEqual(core.roll_day(self.s,'2026-09-23')['attempts'],self.s['attempts'])


class ConfigurationAndTargets(unittest.TestCase):
    def setUp(self):
        self.c=core.config_checked(core.DEFAULTS);self.q={'SH600000':dict(previous_close=10)}
        self.obj=dict(execution_date='2026-09-23',signal_date='2026-09-22',mode='weights',targets=[dict(code='600000',weight=.03)])
    def parse(self):return core.parse_targets(self.obj,'2026-09-23','2026-09-22',10000000,self.c,self.q)
    def test_weights_are_total_account_weights(self):self.assertEqual(self.parse()['SH600000']['quantity'],30000)
    def test_shares_are_targets_not_new_order_size(self):
        self.obj.update(mode='shares',targets=[dict(code='600000',shares=1200)]);self.assertEqual(self.parse()['SH600000']['quantity'],1200)
    def test_date_and_future_signal_rejected(self):
        self.obj['signal_date']='2026-09-23'
        with self.assertRaises(TradeError):self.parse()
    def test_weight_overallocation_rejected(self):
        self.obj['targets'][0]['weight']=.6
        with self.assertRaises(TradeError):self.parse()
    def test_duplicate_code_alias_rejected(self):
        self.obj['targets'].append(dict(code='SH600000',weight=.01))
        with self.assertRaises(TradeError):self.parse()
    def test_invalid_numbers_and_risk_hierarchy(self):
        for override in (dict(capital_limit=float('nan')),dict(spacing_seconds=1),dict(max_order_value=600000),dict(excluded_cash_cny='0')):
            with self.assertRaises(TradeError):core.config_checked(dict(core.DEFAULTS,**override))
    def test_editable_limits(self):
        c=core.config_checked(dict(core.DEFAULTS,target_fraction=.4,capital_limit=4500000,max_security_value=400000,max_order_value=200000,price_guard=.02))
        self.assertEqual(c['capital_limit'],4500000)
    def test_calendar_expiry_not_guessed(self):
        with self.assertRaises(TradeError):core.days_for(self.c,'2027-01-04')
    def test_lunch_and_cutoff(self):
        for hour,minute,phase in [(9,30,'waiting_open'),(9,35,'trading'),(11,25,'lunch'),(13,5,'trading'),(14,50,'closing'),(15,0,'closed')]:
            self.assertEqual(core.session_phase(dt.datetime(2026,9,23,hour,minute,tzinfo=CN)),phase)


class Supervisor(unittest.TestCase):
    def test_approved_profile_calls_only_registered_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runtime.save(p/'approved_profile.json',dict(day='2026-09-23',profile=runtime.APPROVED_PROFILES['2026-09-23']))
            with patch.object(runtime,'now_cn',return_value=dt.datetime(2026,9,23,9,25,tzinfo=CN)),patch.object(runtime,'mutex'),patch.object(runtime,'session_dirs',return_value=[]),patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b'')) as launch:
                runtime.launch_approved_profile(p)
                self.assertTrue(launch.call_args.args[0][-1].endswith('start_session_20260923.ps1'))
    def test_approved_profile_expiry_does_not_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runtime.save(p/'approved_profile.json',dict(day='2026-09-23',profile=runtime.APPROVED_PROFILES['2026-09-23']))
            with patch.object(runtime,'now_cn',return_value=dt.datetime(2026,9,24,9,25,tzinfo=CN)),patch.object(runtime.subprocess,'run') as launch:
                with self.assertRaises(TradeError):runtime.launch_approved_profile(p)
                launch.assert_not_called()
    def test_approved_profile_does_not_race_existing_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runtime.save(p/'approved_profile.json',dict(day='2026-09-23',profile=runtime.APPROVED_PROFILES['2026-09-23']))
            with patch.object(runtime,'now_cn',return_value=dt.datetime(2026,9,23,9,25,tzinfo=CN)),patch.object(runtime,'mutex'),patch.object(runtime,'session_dirs',return_value=[Path('another')]),patch.object(runtime,'alive',return_value=True),patch.object(runtime.subprocess,'run') as launch:
                runtime.launch_approved_profile(p);launch.assert_not_called()
    def test_new_dated_session_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);new=base/'afternoon_new';new.mkdir();runtime.save(new/'private_state.json',dict(day='2026-09-23',attempts=[{},{}]))
            with patch.object(runtime,'BASE_DIR',base),patch.object(runtime,'APP_DIR',base/'console'),patch.object(runtime,'LEGACY',[]):
                self.assertIn(new,runtime.session_dirs());self.assertEqual(runtime.latest_session(),new)
    def test_after_close_history_recovers_exact_rows_without_duplicate_deals(self):
        broker=Mock();broker._rows.side_effect=[[],[],[]];broker._request.return_value={'Data':{'availBalance':0}}
        old_order=dict(orderId='one');old_deal=dict(orderId='one',tradeCount=100)
        with patch.object(runtime,'historical_rows',side_effect=[[old_order],[old_deal]]):
            result=runtime.account_snapshot(broker,dict(attempts=[dict(oid='one')]))
        self.assertEqual(result['orders'],[old_order]);self.assertEqual(result['deals'],[old_deal])
    def test_missing_exact_id_does_not_drop_other_visible_fills(self):
        broker=Mock();broker._rows.side_effect=[[dict(orderId='one')],[dict(orderId='one',tradeCount=100)],[]];broker._request.return_value={'Data':{}}
        with patch.object(runtime,'historical_rows',side_effect=[[dict(orderId='one'),dict(orderId='two')],[dict(orderId='one',tradeCount=100),dict(orderId='two',tradeCount=200)]]):
            result=runtime.account_snapshot(broker,dict(attempts=[dict(oid='one'),dict(oid='two')]))
        self.assertEqual(len(result['orders']),2);self.assertEqual(sum(d['tradeCount'] for d in result['deals']),300)
    def test_start_attaches_and_never_spawns_duplicate(self):
        with patch.object(runtime,'active_session',return_value=Path('existing')),patch.object(runtime,'mutex'),patch.object(runtime.subprocess,'Popen') as spawn:
            result=runtime.start_console(core.DEFAULTS)
            self.assertEqual(result['action'],'attached');spawn.assert_not_called()
    def test_reused_pid_is_not_the_trading_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runtime.save(p/'process.json',dict(pid=1,created_seconds=100,script='console_runtime.py'))
            process=Mock();process.create_time.return_value=200;process.cmdline.return_value=['console_runtime.py']
            with patch.object(runtime.psutil,'Process',return_value=process):self.assertFalse(runtime.alive(p))
    def test_process_access_denied_does_not_allow_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);runtime.save(p/'process.json',dict(pid=1,script='console_runtime.py'))
            with patch.object(runtime.psutil,'Process',side_effect=runtime.psutil.AccessDenied):
                with self.assertRaises(TradeError):runtime.alive(p)
    def test_stop_file_allows_readonly_final_audit(self):
        w=object.__new__(runtime.Worker);w.check_stop=lambda:True;w.audit=Mock();w.publish=Mock()
        with patch.object(runtime.time,'sleep'):w.two_audits(allow_stop=True)
        self.assertEqual(w.audit.call_count,2)


class ExecutionRisk(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=object.__new__(runtime.Worker);self.w.run=Path(self.tmp.name)
        self.w.c=core.config_checked(core.DEFAULTS);self.w.state=state();self.w.last_submit=0
        self.a=core.reconcile(raw(),self.w.state);self.w.audit=lambda:self.a;self.w.cancel=lambda force=False:None;self.w.check_stop=lambda:False
        self.w.http=SimpleNamespace(armed=None);self.w.broker=Mock();self.w.broker.submit.return_value='new'
        self.now=dt.datetime(2026,9,23,10,0,tzinfo=CN)
        self.q=dict(code='SH600000',timestamp=self.now.isoformat(),ask=10,last=10,bid=10,lower_limit=9,upper_limit=11,is_st=0,tradable=1)
        self.w.public=None
    def tearDown(self):self.tmp.cleanup()
    def tick(self):
        with patch.object(runtime,'now_cn',return_value=self.now),patch.object(runtime,'quote',return_value=self.q):return self.w.step()
    def test_buy_intent_is_saved_before_submit(self):
        def submit(order):
            saved=runtime.read_json(self.w.run/'private_state.json');self.assertEqual(saved['attempts'][-1]['state'],'SENDING');return 'new'
        self.w.broker.submit.side_effect=submit;self.tick();self.assertEqual(self.w.state['attempts'][0]['oid'],'new')
    def test_lost_ack_remains_unknown(self):
        self.w.broker.submit.side_effect=TradeError('network')
        with self.assertRaises(TradeError):self.tick()
        self.assertEqual(self.w.state['attempts'][0]['state'],'UNKNOWN')
    def test_price_and_cap_block(self):
        self.q['ask']=10.4;self.q['last']=10.4;self.tick();self.w.broker.submit.assert_not_called()
        self.q['ask']=10;self.q['last']=10;self.w.c['capital_limit']=1000010;self.tick();self.w.broker.submit.assert_not_called()
    def test_sells_opt_in_and_t_plus_one(self):
        self.w.state['targets']['SH600000']['quantity']=500;self.tick();self.w.broker.submit.assert_not_called()
        self.w.c['allow_sells']=True;self.a['sellable']['SH600000']=0;self.tick();self.w.broker.submit.assert_not_called()
        self.a['sellable']['SH600000']=200;self.tick();o=self.w.broker.submit.call_args.args[0];self.assertEqual(o['side'],2);self.assertEqual(o['quantity'],200)
    def test_spacing_cannot_be_bypassed(self):
        self.w.last_submit=runtime.time.monotonic();self.tick();self.w.broker.submit.assert_not_called()
    def test_completed_target_never_rebought(self):
        self.w.state['targets']['SH600000']['quantity']=1000;self.w.two_audits=Mock();self.assertEqual(self.tick(),'completed_targets');self.w.broker.submit.assert_not_called()


if __name__=='__main__':unittest.main()
