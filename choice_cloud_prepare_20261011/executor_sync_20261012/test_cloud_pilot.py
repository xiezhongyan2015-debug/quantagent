"""Offline boundary tests; real network and order transport are prohibited."""
import copy,datetime as dt,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch,Mock
try:import psutil
except ImportError:sys.modules['psutil']=types.ModuleType('psutil') # Local tests don't use process inventory.
import console_core as core
import console_runtime as rt
import pilot_one_lot as pilot
import readonly_cloud as ro
from cloud_account import ACCOUNT,assert_binding
from verify_readonly import GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import TradeError,FillSnapshotPending,BASE,order_payload
from quanta_agents.factor_lab_a.pcf_automation import CN
from watch_engine import reserve,save

NOW=dt.datetime(2026,10,12,9,36,tzinfo=CN)
OBSERVED=dt.datetime(2026,10,11,21,0,tzinfo=CN)

def raw():return dict(orders=[],deals=[],holds=[dict(secCode='000050',count=6300,availCount=6300,value=39249)],balance=dict(availBalance='789543.62',frozenMoney='0',totalPosValue='39249',totalAssets='828792.62'))
def cfg():return core.config_checked(dict(core.DEFAULTS,pilot_symbol='SZ000050'))
def q(price=6.23):return dict(code='SZ000050',name='深天马A',timestamp=NOW.isoformat(),tradable=1,is_st=0,bid=price,ask=price,last=price,previous_close=price,lower_limit=round(price*.9,2),upper_limit=round(price*1.1,2))
def book():
 s=ro.seed_from_observation(raw(),OBSERVED);return core.roll_day(s,pilot.EXECUTION_DATE)
def armed():
 c=cfg();s=book();a=core.reconcile(raw(),s);plan=pilot.make_plan(c,core.BASE_DIR,a,q(),NOW);s['pilot_plan']=plan;s['targets']=plan['targets'];s['quotes']={'SZ000050':q()}
 o=dict(code='SZ000050',side=1,quantity=100,price=6.23,reference_price=6.23)
 s['attempts']=[dict(order=o,state='SENDING',filled=0,reserve=str(reserve('623')),submitted_at=NOW.isoformat())];return c,s,o

class Accounting(unittest.TestCase):
 def test_actual_date_and_zero_legacy_reserve(self):
  s=ro.seed_from_observation(raw(),OBSERVED);self.assertEqual(s['day'],'2026-10-11');self.assertEqual(s['baseline']['cash'],'789543.62');self.assertEqual(s['baseline']['paid'],'0')
 def test_overnight_keeps_account_and_origin(self):
  s=book();self.assertEqual(s['baseline']['positions'],{'SZ000050':6300});self.assertFalse(s['attempts']);self.assertFalse(s['origin']['execution_authorization_created'])
 def test_no_backdating(self):
  with self.assertRaises(TradeError):core.roll_day(book(),'2026-10-09')
 def test_external_order_blocks(self):
  r=raw();r['orders']=[dict(orderId='external',orderDate='20261012')]
  with self.assertRaises(TradeError):core.reconcile(r,book())
 def test_cash_one_yuan_change_blocks(self):
  r=raw();r['balance']['availBalance']='789544.62'
  with self.assertRaises(FillSnapshotPending):core.reconcile(r,book())
 def test_changed_inventory_blocks(self):
  r=raw();r['holds'][0]['count']=6400
  with self.assertRaises(FillSnapshotPending):core.reconcile(r,book())
 def test_frozen_bootstrap_blocked(self):
  r=raw();r['balance']['frozenMoney']=100
  with self.assertRaises(TradeError):ro.seed_from_observation(r,OBSERVED)
 def test_nav_identity_blocked(self):
  r=raw();r['balance']['totalAssets']=1
  with self.assertRaises(TradeError):ro.seed_from_observation(r,OBSERVED)
 def test_today_order_not_silently_adopted(self):
  r=raw();r['orders']=[dict(orderId='x',orderDate='20261011',status='8')]
  with self.assertRaises(TradeError):ro.seed_from_observation(r,OBSERVED)
 def test_hist_orders_retain_exact_id_and_no_double_cash(self):
  r=raw();r['orders']=[dict(orderId='historic',orderDate='20261009',status='4',secCode='000050',drt=1,orderCount=100,orderPrice=6.23,tradeCount=100)]
  r['deals']=[dict(orderId='historic',secCode='000050',drt=1,tradeCount=100,price=6.23)]
  s=ro.seed_from_observation(r,OBSERVED);self.assertIn('historic',s['historical']);self.assertEqual(s['last_audit']['positions']['SZ000050'],6300);self.assertEqual(s['last_audit']['paid'],'0')
 def test_incomplete_historical_deals_blocks(self):
  r=raw();r['orders']=[dict(orderId='historic',orderDate='20261009',status='4',secCode='000050',drt=1,orderCount=100,orderPrice=6.23,tradeCount=100)]
  with self.assertRaises(TradeError):ro.seed_from_observation(r,OBSERVED)
 def test_unknown_receipt_cannot_reset(self):
  s=book();s['attempts']=[dict(order={},state='UNKNOWN')]
  with self.assertRaises(TradeError):core.reconcile(raw(),s)

class AdvanceReservation(unittest.TestCase):
 def test_sunday_arm_uses_actual_monday_execution_day(self):
  self.assertEqual(rt.pilot_start_day(cfg(),OBSERVED),'2026-10-12')
 def test_monday_before_open_allowed(self):
  self.assertEqual(rt.pilot_start_day(cfg(),NOW.replace(hour=8)),'2026-10-12')
 def test_expired_pilot_cannot_arm_next_day(self):
  with self.assertRaises(TradeError):rt.pilot_start_day(cfg(),NOW+dt.timedelta(days=1))
 def test_more_than_one_calendar_day_early_blocked(self):
  with self.assertRaises(TradeError):rt.pilot_start_day(cfg(),OBSERVED-dt.timedelta(days=1))
 def test_after_1450_cannot_arm(self):
  with self.assertRaises(TradeError):rt.pilot_start_day(cfg(),NOW.replace(hour=14,minute=50))
 def waiting_run(self,root):
  run=Path(root);c=cfg();s=book()
  save(run/'config.json',c);save(run/'seed.json',s)
  save(run/'authorization.json',dict(execution_date=c['execution_date'],config_sha256=rt.digest(c),seed_sha256=rt.digest(s),account_mode='competition_simulation_only'))
  return run
 def test_stop_before_open_never_creates_broker(self):
  with tempfile.TemporaryDirectory() as root:
   run=self.waiting_run(root);(run/'STOP').touch()
   with patch.object(rt,'now_cn',return_value=OBSERVED),patch.object(rt,'Broker',side_effect=AssertionError('Broker forbidden')),patch.object(rt,'ChoiceAuthenticator',side_effect=AssertionError('Credentials forbidden')):
    self.assertFalse(rt.wait_for_pilot_open(run))
   self.assertEqual(core.read_json(run/'status_redacted.json')['status'],'stopped')
 def test_wait_across_midnight_until_fresh_execution_window(self):
  with tempfile.TemporaryDirectory() as root:
   run=self.waiting_run(root)
   with patch.object(rt,'now_cn',side_effect=[OBSERVED,OBSERVED,OBSERVED,NOW]),patch.object(rt.time,'sleep') as sleep,patch.object(rt,'Broker',side_effect=AssertionError('Broker forbidden')),patch.object(rt,'ChoiceAuthenticator',side_effect=AssertionError('Credentials forbidden')):
    self.assertTrue(rt.wait_for_pilot_open(run));sleep.assert_called_once_with(2)
 def test_wait_does_not_release_before_0930(self):
  with tempfile.TemporaryDirectory() as root:
   run=self.waiting_run(root);early=NOW.replace(minute=29)
   with patch.object(rt,'now_cn',side_effect=[early,early,early,NOW]),patch.object(rt.time,'sleep') as sleep:
    self.assertTrue(rt.wait_for_pilot_open(run));sleep.assert_called_once_with(2)
 def test_changed_reservation_config_blocked(self):
  with tempfile.TemporaryDirectory() as root:
   run=self.waiting_run(root);a=core.read_json(run/'authorization.json');a['config_sha256']='changed';save(run/'authorization.json',a)
   with self.assertRaises(TradeError):rt.wait_for_pilot_open(run)

class Boundaries(unittest.TestCase):
 def test_one_increment_not_final_holding(self):
  c,s,o=armed();self.assertEqual(s['targets']['SZ000050']['quantity'],6400);self.assertEqual(core.differences(s['targets'],core.reconcile(raw(),book())),{'SZ000050':100});pilot.guard_submission(c,s,o,NOW)
 def test_all_other_holdings_preserved(self):
  c=cfg();a=core.reconcile(raw(),book());a['positions']['SH600000']=999
  p=pilot.make_plan(c,core.BASE_DIR,a,q(),NOW);self.assertEqual(p['targets']['SH600000']['quantity'],999)
 def test_cap_includes_fees(self):
  self.assertGreater(pilot.fee_reserve('99.90'),10000)
  with self.assertRaises(TradeError):pilot.make_plan(cfg(),core.BASE_DIR,core.reconcile(raw(),book()),q(99.9),NOW)
 def test_config_cannot_enable_full_strategy_or_sells(self):
  for values in ({'strategy':'pcf_563030'},{'allow_sells':True},{'max_new_orders':2},{'pilot_shares':200},{'pilot_shares':True},{'pilot_total_cap_cny':10001},{'max_order_value':11000},{'prefer_sealed_plan':True},{'isolate_unknown_orders':True},{'excluded_cash_cny':'395.66'},{'cash_drift_tolerance_cny':500}):
   with self.subTest(values=values),self.assertRaises(TradeError):core.config_checked(dict(core.DEFAULTS,**values))
 def test_floor_cannot_enable(self):
  c=copy.deepcopy(core.DEFAULTS);c['position_floor']['enabled']=True
  with self.assertRaises(TradeError):core.config_checked(c)
 def test_wrong_execution_date(self):
  c,s,o=armed()
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,OBSERVED)
 def test_second_durable_intent_blocked(self):
  c,s,o=armed();s['attempts']*=2
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_unknown_intent_not_reissued(self):
  c,s,o=armed();s['attempts'][0]['state']='UNKNOWN'
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_non100_quantity_blocked(self):
  c,s,o=armed();o['quantity']=200
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_sells_blocked_in_writer(self):
  c,s,o=armed();o['side']=2
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_expired_or_future_quote_blocked(self):
  for seconds in (-31,1):
   c,s,o=armed();s['quotes']['SZ000050']['timestamp']=(NOW+dt.timedelta(seconds=seconds)).isoformat()
   with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_friday_quotes_cannot_execute_monday(self):
  z=q();z['timestamp']='2026-10-09T15:00:00+08:00'
  with self.assertRaises(TradeError):pilot.make_plan(cfg(),core.BASE_DIR,core.reconcile(raw(),book()),z,NOW)
 def test_monday_preview_also_requires_monday_fresh_quote(self):
  z=q();z['timestamp']='2026-10-09T16:11:45+08:00'
  with self.assertRaises(TradeError):pilot.make_plan(cfg(),core.BASE_DIR,core.reconcile(raw(),book()),z,NOW,executable=False)
 def test_preview_is_not_executable(self):
  c,s,o=armed();s['pilot_plan']['preview_only']=True
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_bad_identity_blocked(self):
  with self.assertRaises(RuntimeError):assert_binding({'uid_sha256':'wrong','account_sha256':'wrong'})
 def test_calendar_monday_signal_friday(self):self.assertEqual(core.days_for(cfg(),'2026-10-12')[1],'2026-10-09')
 def test_holiday_blocked(self):
  with self.assertRaises(TradeError):core.days_for(cfg(),'2026-10-11')
 def test_prices_at_10000_gross_blocked_before_network(self):
  c,s,o=armed();o['price']=100;s['attempts'][0]['reserve']=str(reserve(10000))
  with self.assertRaises(TradeError):pilot.guard_submission(c,s,o,NOW)
 def test_terminal_pilot_never_gets_second_quote(self):
  w=rt.Worker.__new__(rt.Worker);w.c=cfg();w.state=book();w.state['attempts']=[dict(oid='one',terminal=True,state='8',filled=0,order={},submitted_at=NOW.isoformat())]
  w.audit=Mock(return_value=core.reconcile(raw(),book()));w.cancel=Mock();w.check_stop=Mock(return_value=False);w.two_audits=Mock()
  with patch.object(rt,'now_cn',return_value=NOW),patch.object(rt,'quote',side_effect=AssertionError('Unexpected extra quote')):
   self.assertEqual(w.step(),'completed_pilot')

class NetworkGuard(unittest.TestCase):
 def test_current_membership_hash_matches(self):
  record=json.loads(Path(cfg()['membership_path']).read_text());payload=b'public file fixture'
  record['source_file_sha256']=__import__('hashlib').sha256(payload).hexdigest()
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'members.json';save(path,record);c=dict(cfg(),membership_path=str(path))
   client=Mock();client.get.return_value=Mock(status_code=200,content=payload)
   pilot.assert_membership_current(c,client)
   self.assertFalse(client.get.call_args.kwargs['allow_redirects'])
 def test_changed_membership_blocks(self):
  client=Mock();client.get.return_value=Mock(status_code=200,content=b'changed official file')
  with self.assertRaises(TradeError):pilot.assert_membership_current(cfg(),client)
 def test_get_only_blocks_write_and_extra_options(self):
  h=GetOnlySession(ACCOUNT)
  with patch('requests.Session.request',side_effect=AssertionError('Real network prohibited')):
   for method,path,kwargs in [('POST','AguTrade/SpoOrder',dict(json={})),('GET','AguTrade/SpoBalInfo',dict(params={'accId':ACCOUNT},verify=False)),('GET','AguTrade/SpoBalInfo',dict(params={'accId':'wrong'})),('GET','Other',dict(params={'accId':ACCOUNT}))]:
    with self.assertRaises(RuntimeError):h.request(method,BASE+path,allow_redirects=False,**kwargs)
 def test_transport_valid_first_intent_mocked_only(self):
  c,s,o=armed()
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp);save(run/'private_state.json',s);h=rt.Transport(run,c,pilot.EXECUTION_DATE);h.writes_suspended=False;h.armed=('submit',o)
   p=order_payload(ACCOUNT,o['code'],1,100,6.23);p.update(uToken='mock',cToken='mock',uid='mock')
   with patch.object(rt,'now_cn',return_value=NOW),patch('requests.Session.request',return_value=Mock(status_code=200,json=Mock(return_value={'Code':0}))) as network:
    h.request('POST',BASE+'AguTrade/SpoOrder',json=p,timeout=15,allow_redirects=False);self.assertEqual(network.call_count,1)
    with self.assertRaises(TradeError):h.request('POST',BASE+'AguTrade/SpoOrder',json=p,timeout=15,allow_redirects=False)
    self.assertEqual(network.call_count,1)
 def test_real_post_wrapper_keeps_the_guard_and_dispatches_once(self):
  c,s,o=armed()
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp);save(run/'private_state.json',s);h=rt.Transport(run,c,pilot.EXECUTION_DATE);h.writes_suspended=False;h.armed=('submit',o)
   p=order_payload(ACCOUNT,o['code'],1,100,6.23);p.update(uToken='mock',cToken='mock',uid='mock')
   with patch.object(rt,'now_cn',return_value=NOW),patch('requests.Session.request',return_value=Mock(status_code=200,json=Mock(return_value={'Code':0}))) as network:
    h.post(BASE+'AguTrade/SpoOrder',json=p,timeout=15,allow_redirects=False)
    self.assertEqual(network.call_count,1);self.assertNotIn('data',network.call_args.kwargs)
    with self.assertRaises(TradeError):h.post(BASE+'AguTrade/SpoOrder',json=p,timeout=15,allow_redirects=False)
    self.assertEqual(network.call_count,1)
 def test_post_wrapper_nonempty_data_is_blocked_before_network(self):
  for data in ({'extra':'body'},'',b''):
   with tempfile.TemporaryDirectory() as tmp:
    h=rt.Transport(Path(tmp),cfg(),pilot.EXECUTION_DATE)
    with patch('requests.Session.request') as network:
     with self.assertRaises(TradeError):h.post(BASE+'AguTrade/SpoOrder',json={},data=data,timeout=15,allow_redirects=False)
     network.assert_not_called()
 def test_extra_options_cannot_bypass_writer(self):
  with tempfile.TemporaryDirectory() as tmp:
   h=rt.Transport(Path(tmp),cfg(),pilot.EXECUTION_DATE)
   with self.assertRaises(TradeError):h.request('POST',BASE+'AguTrade/SpoOrder',json={},verify=False,allow_redirects=False)

if __name__=='__main__':unittest.main(verbosity=2)
