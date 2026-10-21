"""Offline workflow contracts. No credentials, network or broker writes."""
import contextlib,copy,datetime as dt,hashlib,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
try:import psutil
except ImportError:sys.modules['psutil']=types.ModuleType('psutil')
import console_core as core
import console_runtime as rt
import workflow_rebalance as w
from quanta_agents.factor_lab_a.choice_pcf import TradeError
from quanta_agents.factor_lab_a.pcf_automation import CN,digest
NOW=dt.datetime(2026,10,12,9,40,tzinfo=CN)
def save(p,x):p.write_text(json.dumps(x),encoding='utf8');return hashlib.sha256(p.read_bytes()).hexdigest()
def quote(c='SZ000001',price=10):return dict(code=c,name='fixture',timestamp=NOW.isoformat(),tradable=1,is_st=0,bid=price,ask=price,last=price,previous_close=price,lower_limit=price*.9,upper_limit=price*1.1)
class Workflow(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.members=dict(as_of=w.SIGNAL,allowed=[f'SZ{i:06d}' for i in range(1,1801)],csi500=[f'SZ{i:06d}' for i in range(1,501)],sources=[dict(index=name,snapshot_date=w.SIGNAL,source_url='https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/'+code+'cons.xls',source_file_sha256=hashlib.sha256(b'official').hexdigest()) for name,code in [('csi300','000300'),('csi500','000905'),('csi1000','000852')]])
  self.obj=dict(execution_date=w.DAY,signal_date=w.SIGNAL,mode='shares',strategy_layer='P0',review_only=False,execution_enabled=True,targets=[dict(code=f'SZ{i:06d}',shares=6400 if i==50 else 200 if i==1 else 100) for i in range(1,81)])
  self.c=copy.deepcopy(core.DEFAULTS);self.c.update(strategy='pilot_then_targets',pilot_symbol='SZ000050',rebalance_after_pilot=dict(target_path=str(self.root/'target.json'),target_sha256=save(self.root/'target.json',self.obj),membership_path=str(self.root/'members.json'),membership_sha256=save(self.root/'members.json',self.members),target_fraction=.95,minimum_stock_fraction=.90,minimum_csi500_nav=.80,max_orders=2000,max_slices_per_code=128,max_order_value=50000))
  positions={f'SZ{i:06d}':6400 if i==50 else 100 for i in range(1,81)};marks={c:str(n*10) for c,n in positions.items()};mv=sum(int(x) for x in marks.values())
  self.audit=dict(checked_at=NOW.isoformat(),positions=positions,sellable=positions.copy(),cash=str(155000-mv),frozen='0',market_value=str(mv),total_assets='155000',position_mark_values=marks,pending={},states={'pilot':dict(state='4',filled=100,terminal=True)},cash_drift_requires_confirmation=False,isolated_order_count=0,paid='1000',fees='0')
  self.state=dict(day=w.DAY,baseline=dict(positions=dict(positions,SZ000050=6300)),attempts=[dict(oid='pilot',order=dict(code='SZ000050',side=1,quantity=100,price=10,reference_price=10),reserve='1011',state='4',filled=100,terminal=True,terminal_at=(NOW-dt.timedelta(seconds=90)).isoformat())],pilot_plan=dict(preview_only=False,held_before=6300,target_shares=6400),targets={},last_audit=copy.deepcopy(self.audit),quotes={},deferred={},carried_reserve='0',historical={},prior={},baseline_security_reserve={})
  self.audits=[dict(copy.deepcopy(self.audit),checked_at=(NOW-dt.timedelta(seconds=6)).isoformat()),copy.deepcopy(self.audit)]
 def reseal(self):
  r=self.c['rebalance_after_pilot'];r['target_sha256']=save(self.root/'target.json',self.obj);r['membership_sha256']=save(self.root/'members.json',self.members)
 def formal(self,code='SZ000001',side=1,quantity=100,price=10):
  proof=w.acceptance(self.c,self.state,self.audits);targets={r['code']:dict(code=r['code'],quantity=r['shares'],reference_price=price if r['code']==code else 10) for r in self.obj['targets']};self.state.update(targets=targets,rebalance_workflow=dict(proof,phase='formal',targets_sha256=digest(targets)))
  order=dict(code=code,side=side,quantity=quantity,price=price,reference_price=price);self.state['quotes'][code]=quote(code,price)
  self.state['attempts'].append(dict(phase='formal',state='SENDING',order=order,filled=0,reserve=str(w.reserved(price,quantity)),submitted_at=NOW.isoformat()))
  return order
 def valuation_fixture(self):
  obj=dict(execution_date=w.DAY,signal_date=w.SIGNAL,mode='shares',targets=[dict(code='SZ000001',shares=900)])
  c=copy.deepcopy(self.c);c.update(target_fraction=.95,max_security_value=20000)
  q=quote('SZ000001',10);q['previous_close']=10.7
  return obj,c,{'SZ000001':q}
 def test_live_valuation_matches_live_nav_without_changing_price_reference(self):
  obj,c,quotes=self.valuation_fixture()
  with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes)
  t=core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes,valuation_time=NOW)
  self.assertEqual(t['SZ000001']['quantity'],900);self.assertEqual(t['SZ000001']['reference_price'],10.7)
 def test_live_target_above_95_percent_still_blocks(self):
  obj,c,quotes=self.valuation_fixture();quotes['SZ000001']=quote('SZ000001',11)
  with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes,valuation_time=NOW)
 def test_live_target_stale_quote_still_blocks(self):
  obj,c,quotes=self.valuation_fixture();quotes['SZ000001']['timestamp']=(NOW-dt.timedelta(seconds=31)).isoformat()
  with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes,valuation_time=NOW)
 def test_live_target_wrong_day_blocks(self):
  obj,c,quotes=self.valuation_fixture()
  with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes,valuation_time=NOW+dt.timedelta(days=1))
 def test_live_target_single_security_cap_still_blocks(self):
  obj,c,quotes=self.valuation_fixture();c['max_security_value']=5000
  with self.assertRaises(TradeError):core.parse_targets(obj,w.DAY,w.SIGNAL,10000,c,quotes,valuation_time=NOW)
 def test_transition_rechecks_cap_against_second_audit_nav(self):
  worker=rt.Worker.__new__(rt.Worker);worker.state=copy.deepcopy(self.state);worker.c=self.c;worker.original_config=self.c;worker.day=w.DAY;worker.run=self.root;worker.http=Mock();worker.publish=Mock();worker.public=Mock();worker.public.get.return_value=Mock(status_code=200,content=b'official');worker.check_stop=Mock(return_value=False);worker.reference_quote=lambda code:quote(code);worker.flush=Mock();worker.broker=Mock()
  def fresh():
   worker.state['last_audit']=dict(copy.deepcopy(self.audit),total_assets='140000')
   return copy.deepcopy(self.audits)
  worker.two_audits=fresh
  with patch.object(rt,'now_cn',return_value=NOW),self.assertRaisesRegex(TradeError,'总市值超过'):worker.transition_to_formal(self.audits)
  self.assertFalse(w.formal_state(worker.state));worker.broker.submit.assert_not_called()
 def test_resume_preserves_completed_pilot_and_skips_another_submission(self):
  worker=rt.Worker.__new__(rt.Worker);worker.state=copy.deepcopy(self.state);worker.c=self.c;worker.audit=Mock(return_value=self.audit);worker.cancel=Mock();worker.check_stop=Mock(return_value=False);worker.two_audits=Mock(return_value=self.audits);worker.transition_to_formal=Mock(return_value='formal_rebalancing');worker.broker=Mock()
  with patch.object(rt,'now_cn',return_value=NOW):self.assertEqual(worker.step(),'formal_rebalancing')
  worker.broker.submit.assert_not_called();self.assertEqual(worker.state['attempts'][0]['oid'],'pilot');self.assertEqual(len(worker.state['attempts']),1)
 def test_base_trial_limit_stays_10000(self):
  c=core.config_checked(self.c);self.assertEqual(c['max_order_value'],10000);self.assertFalse(c['allow_sells']);self.assertEqual(w.checked(c)['max_order_value'],50000)
 def test_missing_post_scope_fails(self):
  del self.c['rebalance_after_pilot']['target_sha256']
  with self.assertRaises(TradeError):core.config_checked(self.c)
 def test_post_cap_cannot_be_silently_changed(self):
  self.c['rebalance_after_pilot']['max_order_value']=50001
  with self.assertRaises(TradeError):w.checked(self.c)
 def test_file_change_blocks(self):
  (self.root/'target.json').write_text('{}')
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_shadow_blocks(self):
  self.obj['generated_from']={'shadow':True};self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_research_only_blocks(self):
  self.obj['research_only']=True;self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_duplicate_canonical_code_blocks(self):
  self.obj['targets'][1]['code']='000001.SZ';self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_outside_union_blocks(self):
  self.obj['targets'][1]['code']='SZ300001';self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_boolean_shares_blocks(self):
  self.obj['targets'][1]['shares']=True;self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_wrong_date_blocks(self):
  self.obj['signal_date']='2026-09-28';self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_source_url_is_exact(self):
  self.members['sources'][0]['source_url']+='?arbitrary';self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_duplicate_members_blocks(self):
  self.members['allowed'][1]=self.members['allowed'][0];self.reseal()
  with self.assertRaises(TradeError):w.inputs(self.c)
 def test_current_public_sources_match(self):
  public=Mock();public.get.return_value=Mock(status_code=200,content=b'official');w.verify_current_sources(self.c,public);self.assertEqual(public.get.call_count,3)
 def test_changed_public_source_blocks(self):
  public=Mock();public.get.return_value=Mock(status_code=200,content=b'new official')
  with self.assertRaises(TradeError):w.verify_current_sources(self.c,public)
 def test_full_pilot_two_audits_accepts(self):self.assertEqual(w.acceptance(self.c,self.state,self.audits)['pilot_oid'],'pilot')
 def test_partial_rejected_unknown_no_transition(self):
  for state,filled in [('7',50),('9',0),('UNKNOWN',0),('4',99)]:
   s=copy.deepcopy(self.state);s['attempts'][0].update(state=state,filled=filled)
   with self.subTest(state=state),self.assertRaises(TradeError):w.acceptance(self.c,s,self.audits)
 def test_missing_receipt_blocks(self):
  del self.state['attempts'][0]['oid']
  with self.assertRaises(TradeError):w.acceptance(self.c,self.state,self.audits)
 def test_second_trial_blocks(self):
  self.state['attempts']*=2
  with self.assertRaises(TradeError):w.acceptance(self.c,self.state,self.audits)
 def test_pending_freeze_drift_blocks_acceptance(self):
  for key,value in [('pending',{'x':{}}),('frozen','1'),('cash_drift_requires_confirmation',True),('isolated_order_count',1)]:
   audits=copy.deepcopy(self.audits);audits[1][key]=value
   with self.subTest(key=key),self.assertRaises(TradeError):w.acceptance(self.c,self.state,audits)
 def test_inventory_difference_blocks_acceptance(self):
  self.audits[1]['positions']['SZ000050']=6300
  with self.assertRaises(TradeError):w.acceptance(self.c,self.state,self.audits)
 def test_old_date_or_short_interval_blocks(self):
  for seconds in (1,121,86400):
   audits=copy.deepcopy(self.audits);audits[0]['checked_at']=(NOW-dt.timedelta(seconds=seconds)).isoformat()
   with self.subTest(seconds=seconds),self.assertRaises(TradeError):w.acceptance(self.c,self.state,audits)
 def test_effective_keeps_original_authority(self):
  o=self.formal();before=copy.deepcopy(self.c);derived=w.effective(self.c,self.state);self.assertEqual(derived['max_order_value'],50000);self.assertTrue(derived['allow_sells']);self.assertEqual(self.c,before);w.guard_formal(self.c,self.state,o,NOW)
 def test_target_quantity_tampering_blocks(self):
  self.formal();self.state['targets']['SZ000001']['quantity']+=100;self.state['rebalance_workflow']['targets_sha256']=digest(self.state['targets'])
  with self.assertRaises(TradeError):w.effective(self.c,self.state)
 def test_original_config_tampering_blocks(self):
  self.formal();self.c['price_guard']=.04
  with self.assertRaises(TradeError):w.effective(self.c,self.state)
 def test_proof_tampering_blocks(self):
  self.formal();self.state['rebalance_workflow']['pilot_oid']='invented'
  with self.assertRaises(TradeError):w.effective(self.c,self.state)
 def test_repeat_transition_blocks(self):
  self.formal()
  with self.assertRaises(TradeError):w.acceptance(self.c,self.state,self.audits)
 def test_no_formal_before_acceptance(self):
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,{},NOW)
 def test_over_target_gap_blocks(self):
  o=self.formal(quantity=200)
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_stale_audit_blocks(self):
  o=self.formal();self.state['last_audit']['checked_at']=(NOW-dt.timedelta(seconds=31)).isoformat()
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_stale_or_wrong_day_quote_blocks(self):
  o=self.formal();self.state['quotes'][o['code']]['timestamp']=(NOW-dt.timedelta(days=1)).isoformat()
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_fee_cap_rejects_gross_below_50000(self):
  o=self.formal(price=499.9)
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_cap_chunk_rounding_includes_fees(self):
  for p in ('6.23','170.72','309.50','499.90'):
   n=w.max_order_quantity(p);self.assertLessEqual(w.reserved(p,n),50000);self.assertGreater(w.reserved(p,n+100),50000)
 def test_pending_previous_order_blocks_writer(self):
  o=self.formal();self.state['attempts'][0]['terminal']=False
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_partial_cancelled_slice_not_resent(self):
  o=self.formal();prior=copy.deepcopy(self.state['attempts'][-1]);prior.update(oid='slice',state='7',filled=50,terminal=True);self.state['attempts'].insert(1,prior)
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_fully_filled_slice_can_continue(self):
  o=self.formal();prior=copy.deepcopy(self.state['attempts'][-1]);prior.update(oid='slice',state='4',filled=100,terminal=True);self.state['attempts'].insert(1,prior);w.guard_formal(self.c,self.state,o,NOW)
 def test_stock_floor_prevents_sell(self):
  self.assertFalse(w.sell_room(self.c,dict(self.audit,positions={'SZ000001':14300},market_value='143000',position_mark_values={'SZ000001':'143000'}),self.members,'SZ000001',500,10))
 def test_membership_floor_prevents_sell(self):
  a=dict(self.audit,positions={'SZ000001':12400,'SZ001800':1900},market_value='143000',position_mark_values={'SZ000001':'124000','SZ001800':'19000'});self.assertFalse(w.sell_room(self.c,a,self.members,'SZ000001',100,10))
 def test_incomplete_marks_block_sells(self):
  a=dict(self.audit,position_mark_values={'SZ000001':'1'})
  with self.assertRaises(TradeError):w.sell_room(self.c,a,self.members,'SZ000001',100,10)
 def test_transient_mark_sum_mismatch_waits_without_relaxing_tolerance(self):
  a=dict(copy.deepcopy(self.audit),market_value=str(int(self.audit['market_value'])+100000))
  with self.assertRaises(rt.FillSnapshotPending):w.consistent_position_marks(a)
 def test_missing_position_mark_is_structural_failure(self):
  a=copy.deepcopy(self.audit);a['position_mark_values'].pop('SZ000001')
  with self.assertRaises(TradeError) as e:w.consistent_position_marks(a)
  self.assertNotIsInstance(e.exception,rt.FillSnapshotPending)
 def test_negative_position_mark_is_structural_failure(self):
  a=copy.deepcopy(self.audit);a['position_mark_values']['SZ000001']='-1'
  with self.assertRaises(TradeError) as e:w.consistent_position_marks(a)
  self.assertNotIsInstance(e.exception,rt.FillSnapshotPending)
 def test_consistent_marks_pass_after_transient_refresh(self):
  self.assertEqual(w.consistent_position_marks(self.audit),self.audit['position_mark_values'])
 def test_formal_audit_mismatch_keeps_last_success_and_blocks_writes(self):
  self.formal();self.state['attempts'].pop();worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=w.effective(self.c,self.state);worker._cash_candidate=None;worker.http=Mock();worker.raw=Mock(return_value={});worker.flush=Mock();worker.broker=Mock();worker.last_success=123;worker.fail_since=100
  old=copy.deepcopy(worker.state['last_audit']);a=dict(copy.deepcopy(self.audit),market_value=str(int(self.audit['market_value'])+100000))
  with patch.object(rt,'reconcile',return_value=a),self.assertRaises(rt.FillSnapshotPending):worker.audit()
  self.assertEqual(worker.last_success,123);self.assertEqual(worker.fail_since,100);self.assertEqual(worker.state['last_audit'],old);worker.flush.assert_not_called();worker.broker.submit.assert_not_called()
 def test_worker_serializes_pending_formal_orders(self):
  self.formal();worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=w.effective(self.c,self.state);worker.original_config=self.c;worker.audit=Mock(return_value=dict(self.audit,pending={'pending':{}}));worker.cancel=Mock();worker.check_stop=Mock(return_value=False);worker.broker=Mock()
  with patch.object(rt,'now_cn',return_value=NOW):self.assertEqual(worker.step(),'watching')
  worker.broker.submit.assert_not_called()
 def test_worker_rejection_never_enters_formal(self):
  self.state['attempts'][0].update(state='9',filled=0);worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=self.c;worker.audit=Mock(return_value=self.audit);worker.cancel=Mock();worker.check_stop=Mock(return_value=False);worker.two_audits=Mock(return_value=self.audits);worker.transition_to_formal=lambda audits:w.acceptance(self.c,self.state,audits)
  with patch.object(rt,'now_cn',return_value=NOW),self.assertRaises(TradeError):worker.step()
 def sc_sell(self,quantity,target,held):
  self.members['allowed'][-1]='SH688002';self.members['csi500'][-1]='SH688002';self.obj['targets'][0]=dict(code='SH688002',shares=target) if target else dict(code='SZ000001',shares=200);self.reseal()
  o=self.formal('SH688002',2,quantity);self.state['last_audit']['positions']['SH688002']=held;self.state['last_audit']['sellable']['SH688002']=held;self.state['last_audit']['position_mark_values']['SH688002']=str(held*10);self.state['last_audit']['market_value']=str(int(self.audit['market_value'])+held*10)
  if target==0:
   self.state['targets']['SH688002']=dict(code='SH688002',quantity=0,reference_price=10);self.state['rebalance_workflow']['targets_sha256']=digest(self.state['targets'])
  return o
 def test_sc_nonliquidation_sell100_blocked(self):
  o=self.sc_sell(100,200,300)
  with self.assertRaises(TradeError):w.guard_formal(self.c,self.state,o,NOW)
 def test_sc_sell200_allowed(self):
  o=self.sc_sell(200,200,400);w.guard_formal(self.c,self.state,o,NOW)
 def test_sc_full_odd_balance_liquidation_allowed(self):
  o=self.sc_sell(100,0,100);w.guard_formal(self.c,self.state,o,NOW)
 def test_worker_valid_transition_persists_original_scope(self):
  worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=self.c;worker.original_config=self.c;worker.day=w.DAY;worker.run=self.root;worker.http=Mock();worker.publish=Mock();worker.public=Mock();worker.public.get.return_value=Mock(status_code=200,content=b'official');worker.check_stop=Mock(return_value=False);worker.reference_quote=lambda code:quote(code);worker.two_audits=Mock(return_value=self.audits);worker.flush=Mock();worker.broker=Mock()
  with patch.object(rt,'now_cn',return_value=NOW):self.assertEqual(worker.transition_to_formal(self.audits),'formal_rebalancing')
  self.assertTrue(w.formal_state(worker.state));self.assertTrue(worker.c['allow_sells']);self.assertEqual(worker.c['max_order_value'],50000);self.assertEqual(self.c['max_order_value'],10000);worker.broker.submit.assert_not_called();self.assertTrue((self.root/'rebalance_transition.json').exists())
 def test_explicit_upgrade_preserves_old_authorization(self):
  from test_cloud_pilot import cfg,book,OBSERVED
  active=self.root/'old';source=self.root/'source';active.mkdir();source.mkdir();home=self.root/'console'
  rt.save(active/'config.json',cfg());rt.save(active/'seed.json',book());rt.save(active/'status_redacted.json',dict(status='scheduled_waiting'));rt.save(active/'authorization.json',dict(scope='original trial'))
  rt.save(source/'private_state.json',book());oldhash=hashlib.sha256((active/'authorization.json').read_bytes()).hexdigest()
  with patch('cloud_account.verify_deployment'),patch.object(rt,'APP_DIR',home),patch.object(rt,'mutex',side_effect=lambda p:contextlib.nullcontext()),patch.object(rt,'now_cn',return_value=OBSERVED),patch.object(rt,'active_session',side_effect=[active,None]),patch.object(rt,'alive',return_value=False),patch.object(rt,'latest_session',return_value=source),patch.object(rt.subprocess,'Popen',return_value=Mock(pid=888)) as start,patch.object(rt.psutil,'Process',create=True,return_value=Mock(create_time=Mock(return_value=123))):
   result=rt.start_console(self.c)
  self.assertEqual(result['action'],'pipeline_scheduled');self.assertTrue((active/'STOP').exists());self.assertEqual(hashlib.sha256((active/'authorization.json').read_bytes()).hexdigest(),oldhash);self.assertEqual(start.call_count,1);self.assertEqual(rt.read_json(Path(result['directory'])/'config.json')['strategy'],'pilot_then_targets')
 def test_started_trial_cannot_expand_scope(self):
  from test_cloud_pilot import cfg,book,OBSERVED
  active=self.root/'old';active.mkdir();rt.save(active/'config.json',cfg());rt.save(active/'seed.json',book());rt.save(active/'status_redacted.json',dict(status='watching'))
  with patch('cloud_account.verify_deployment'),patch.object(rt,'APP_DIR',self.root/'console'),patch.object(rt,'mutex',side_effect=lambda p:contextlib.nullcontext()),patch.object(rt,'now_cn',return_value=OBSERVED),patch.object(rt,'active_session',return_value=active),patch.object(rt.subprocess,'Popen') as start,self.assertRaises(TradeError):rt.start_console(self.c)
  start.assert_not_called();self.assertFalse((active/'STOP').exists())
 def test_open_window_is_exactly_0930(self):
  self.assertEqual(core.session_phase(NOW.replace(hour=9,minute=29,second=59)),'waiting_open')
  self.assertEqual(core.session_phase(NOW.replace(hour=9,minute=30,second=0)),'trading')
 def test_operator_can_replace_unstarted_sealed_p0_with_v5(self):
  from test_cloud_pilot import book,OBSERVED
  active=self.root/'old';source=self.root/'source';active.mkdir();source.mkdir();home=self.root/'console'
  old=copy.deepcopy(self.c);old['rebalance_after_pilot']['target_path']=str(self.root/'old_target.json');old['rebalance_after_pilot']['target_sha256']=save(self.root/'old_target.json',self.obj)
  self.obj['strategy_layer']='V5';self.reseal()
  rt.save(active/'config.json',old);rt.save(active/'seed.json',book());rt.save(active/'status_redacted.json',dict(status='scheduled_waiting'));rt.save(active/'authorization.json',dict(scope='original P0'));rt.save(source/'private_state.json',book());before=(active/'authorization.json').read_bytes()
  with patch('cloud_account.verify_deployment'),patch.object(rt,'APP_DIR',home),patch.object(rt,'mutex',side_effect=lambda p:contextlib.nullcontext()),patch.object(rt,'now_cn',return_value=OBSERVED),patch.object(rt,'active_session',side_effect=[active,None]),patch.object(rt,'alive',return_value=False),patch.object(rt,'latest_session',return_value=source),patch.object(rt.subprocess,'Popen',return_value=Mock(pid=888)) as start,patch.object(rt.psutil,'Process',create=True,return_value=Mock(create_time=Mock(return_value=123))):result=rt.start_console(self.c)
  self.assertEqual(result['action'],'pipeline_scheduled');self.assertEqual((active/'authorization.json').read_bytes(),before);self.assertEqual(start.call_count,1)
if __name__=='__main__':unittest.main()
