"""Offline tests: every network method is blocked by the runner."""
import copy,datetime as dt,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import console_core as core
import console_runtime as runtime
import corporate_actions as ca
import corporate_action_sources as sources
import corporate_action_runtime as io
from quanta_agents.factor_lab_a.choice_pcf import FillSnapshotPending,TradeError
from quanta_agents.factor_lab_a.pcf_automation import CN

NOW=dt.datetime(2026,10,13,9,31,tzinfo=CN)
CONFIG=dict(excluded_cash_cny='0.00',cash_drift_tolerance_cny=.03,isolate_unknown_orders=False)
ACCOUNT='a'*64

def event(c='SZ001309',cash='1.5',ratio='0.4'):
 return dict(code=c,record_date='2026-10-12',ex_date='2026-10-13',pay_date='2026-10-13',cash_per_share=cash,transfer_per_share=ratio,bonus_per_share='0',diluted_cash_per_share=[],evidence_verified=True,announcement_sha256='b'*64,announcement_url='https://vip.stock.finance.sina.com.cn/issuer-announcement')

def inputs():
 state=dict(day='2026-10-13',origin=dict(account_sha256=ACCOUNT),baseline=dict(positions={'SZ001309':700},cash='10000.00',paid='0'),historical={},prior={},attempts=[],targets={'SZ001309':dict(quantity=700,reference_price=321.43)},carried_reserve='0')
 raw=dict(history_complete=True,orders=[],deals=[],holds=[dict(secCode='001309',count=980,availCount=700,value=9800)],balance=dict(availBalance='11050.00',frozenMoney='0',totalPosValue='9800',totalAssets='20850'))
 obs=[dict(at=(NOW-dt.timedelta(seconds=6-i*5)).isoformat(),raw=copy.deepcopy(raw)) for i in range(2)]
 cache=dict(schema=1,day=state['day'],as_of=(NOW-dt.timedelta(minutes=20)).isoformat(),complete=True,scope=['SZ001309'],account_sha256=ACCOUNT,events=[event()])
 close=dict(schema=1,day='2026-10-12',at=NOW.replace(day=12,hour=15,minute=1).isoformat(),account_sha256=ACCOUNT,positions={'SZ001309':700},verified=True,positions_sha256=ca.digest({'SZ001309':700}))
 return state,obs,cache,{'2026-10-12':close}

def observed(obs,cash=None,shares=None):
 for x in obs:
  r=x['raw']
  if cash is not None:r['balance']['availBalance']=str(cash)
  if shares is not None:r['holds'][0].update(count=shares,availCount=min(shares,700),value=shares*10)
  r['balance']['totalPosValue']=str(sum(ca.dec(h['value']) for h in r['holds']))
  r['balance']['totalAssets']=str(ca.dec(r['balance']['totalPosValue'])+ca.dec(r['balance']['availBalance']))

def apply(data,now=NOW):return ca.propose(*data,now,core.reconcile,CONFIG)

class ExactAccounting(unittest.TestCase):
 def test_transfer_and_dividend_preserve_strategy_risk_and_original(self):
  data=inputs();before=copy.deepcopy(data);new,receipt=apply(data)
  self.assertEqual(data,before);self.assertEqual(new['baseline']['positions']['SZ001309'],980);self.assertEqual(new['baseline']['cash'],'11050.00')
  self.assertEqual(new['targets'],before[0]['targets']);self.assertEqual(new['historical'],before[0]['historical']);self.assertTrue(receipt['targets_unchanged']);self.assertEqual(new['last_audit']['cash_drift_cny'],'0.00')
 def test_cash_dividend_only(self):
  data=inputs();data[2]['events'][0]['transfer_per_share']='0';observed(data[1],shares=700)
  new,_=apply(data);self.assertEqual(new['baseline']['positions'],{'SZ001309':700})
 def test_bonus_shares(self):
  data=inputs();data[2]['events'][0].update(transfer_per_share='0',bonus_per_share='.4');new,_=apply(data);self.assertEqual(new['baseline']['positions']['SZ001309'],980)
 def test_stock_and_cash_can_arrive_separately(self):
  data=inputs();observed(data[1],cash='10000.00');new,_=apply(data);self.assertEqual(len(new['corporate_action_book']['applied']),1)
  data=list(data);data[0]=new;observed(data[1],cash='11050.00');final,_=apply(data);self.assertEqual(len(final['corporate_action_book']['applied']),2)
 def test_duplicate_booking_never_repeats(self):
  data=list(inputs());new,_=apply(data);data[0]=new
  with self.assertRaises(ca.CorporateActionPending):apply(data)
 def test_cross_day_keeps_booking_ids(self):
  new,_=apply(inputs());rolled=core.roll_day(new,'2026-10-14');self.assertEqual(rolled['corporate_action_book'],new['corporate_action_book'])
 def test_record_date_inventory_survives_sale_after_record(self):
  data=inputs();data[0]['baseline']['positions']={};data[2]['events'][0]['transfer_per_share']='0'
  for x in data[1]:x['raw']['holds']=[]
  observed(data[1]);new,_=apply(data);self.assertEqual(new['baseline']['cash'],'11050.00');self.assertEqual(new['baseline']['positions'],{})
 def test_cash_before_future_pay_date_is_not_adopted(self):
  data=inputs();data[2]['events'][0]['pay_date']='2026-10-14'
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_existing_confirmed_cash_variance_is_preserved(self):
  data=inputs();data[0]['cash_variance']=dict(day='2026-10-13',amount_cny='.02');observed(data[1],cash='11050.02');new,_=apply(data)
  self.assertEqual(new['baseline']['cash'],'11050.00');self.assertEqual(new['cash_variance'],data[0]['cash_variance']);self.assertEqual(ca.dec(new['last_audit']['cash_drift_cny']),ca.dec('.02'))
 def test_fractional_entitlements_require_review(self):
  data=inputs();data[2]['events'][0]['transfer_per_share']='.3333'
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_negative_cash_tax_is_never_guessed(self):
  data=inputs();observed(data[1],cash='9999.00',shares=700)
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_unknown_transfer_or_deposit_is_not_adopted(self):
  for cash,shares in [('11051.00',980),('11050.00',981),('11050.00',699)]:
   with self.subTest(cash=cash,shares=shares):
    data=inputs();observed(data[1],cash=cash,shares=shares)
    with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_ambiguous_equal_dividends_require_review(self):
  data=inputs();data[2]['events'][0]['transfer_per_share']='0';data[2]['events'].append(event('SH600001'));data[2]['scope'].append('SH600001');cl=data[3]['2026-10-12'];cl['positions']['SH600001']=700;cl['positions_sha256']=ca.digest(cl['positions']);observed(data[1],shares=700)
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_missing_record_date_snapshot_requires_review(self):
  data=inputs();data[3].clear()
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_changed_announcement_requires_review(self):
  data=list(inputs());data[0],_=apply(data);data[2]['events'][0]['cash_per_share']='1.6';observed(data[1],cash='11120.00')
  with self.assertRaises(ca.CorporateActionError):apply(data)
 def test_unknown_order_and_incomplete_history_require_review(self):
  for case in ('external','unknown','incomplete'):
   data=inputs()
   if case=='unknown':data[0]['attempts']=[dict(state='UNKNOWN',order={})]
   for x in data[1]:
    if case=='external':x['raw']['orders']=[dict(orderId='external',orderDate='20261013')]
    if case=='incomplete':x['raw']['history_complete']=False
   with self.subTest(case=case),self.assertRaises(ca.CorporateActionError):apply(data)
 def test_stale_changed_or_same_instant_observations_rejected(self):
  for case in ('stale','changed','same_time','naive'):
   data=inputs()
   if case=='stale':data[1][0]['at']=(NOW-dt.timedelta(minutes=10)).isoformat()
   if case=='changed':observed(data[1][:1],cash='11051.00')
   if case=='same_time':data[1][0]['at']=data[1][1]['at']
   if case=='naive':data[1][0]['at']=NOW.replace(tzinfo=None).isoformat()
   with self.subTest(case=case),self.assertRaises((ca.CorporateActionError,ca.CorporateActionPending)):apply(data)
 def test_cache_or_account_or_close_tampering_rejected(self):
  for case in ('stale','incomplete','identity','close_hash','close_intraday','close_future','unsigned'):
   data=inputs()
   if case=='stale':data[2]['as_of']=(NOW-dt.timedelta(days=2)).isoformat()
   if case=='incomplete':data[2]['complete']=False
   if case=='identity':data[2]['account_sha256']='other'
   if case=='close_hash':data[3]['2026-10-12']['positions']['SZ001309']=701
   if case=='close_intraday':data[3]['2026-10-12']['at']=NOW.replace(day=12,hour=14).isoformat()
   if case=='close_future':data[3]['2026-10-12']['at']=NOW.replace(day=14,hour=15).isoformat()
   if case=='unsigned':data[2]['events'][0]['evidence_verified']=False
   with self.subTest(case=case),self.assertRaises(ca.CorporateActionError):apply(data)

class OriginalIntegration(unittest.TestCase):
 def worker(self,state):
  worker=runtime.Worker.__new__(runtime.Worker);worker.state=state;worker.day=state['day'];worker.c=CONFIG;worker.http=Mock(writes_suspended=False);worker.raw=Mock();worker.flush=Mock();worker.publish=Mock();worker.last_success=None;worker.fail_since=None;worker._cash_candidate=None;return worker
 def test_no_mismatch_never_calls_corporate_action_provider(self):
  state,obs,_,_=inputs();observed(obs,cash='10000',shares=700);worker=self.worker(state);worker.raw.return_value=obs[0]['raw']
  with patch.object(io,'recover_worker',side_effect=AssertionError('Unexpected booking')),patch.object(runtime.floor,'active',return_value=False):worker.audit()
  self.assertFalse(worker.http.writes_suspended)
 def test_pending_settlement_keeps_original_grace_and_write_state(self):
  state,obs,_,_=inputs();worker=self.worker(state);worker.raw.return_value=obs[0]['raw']
  with patch.object(io,'recover_worker',side_effect=ca.CorporateActionPending('waiting')),self.assertRaises(FillSnapshotPending):worker.audit()
  self.assertFalse(worker.http.writes_suspended)
 def test_unhandled_company_action_queues_alert_and_preserves_account(self):
  state,obs,_,_=inputs();before=copy.deepcopy(state);worker=self.worker(state);worker.raw.return_value=obs[0]['raw']
  with patch.object(io,'recover_worker',side_effect=ca.CorporateActionError('unexplained')),patch.object(io,'blocked') as notice,self.assertRaises(FillSnapshotPending):worker.audit()
  notice.assert_called_once();self.assertEqual(worker.state,before);worker.flush.assert_not_called()
 def test_successful_accounting_runs_unchanged_audit_and_updates_state(self):
  state,obs,_,_=inputs();worker=self.worker(state);worker.raw.return_value=obs[0]['raw'];new,_=apply(inputs())
  def recover(w):w.state=new;return obs[-1]['raw']
  with patch.object(io,'recover_worker',side_effect=recover),patch.object(runtime.floor,'active',return_value=False):result=worker.audit()
  self.assertEqual(result['positions']['SZ001309'],980);self.assertEqual(result['cash_drift_cny'],'0.00');self.assertFalse(worker.http.writes_suspended)
 def test_full_history_flag_fetches_history_even_when_current_order_exists(self):
  b=Mock();b._rows.side_effect=lambda path:[] if not path.endswith('SpoHold') else [];b._request.return_value=dict(Data={})
  with patch.object(runtime,'historical_rows',return_value=[]) as history:runtime.account_snapshot(b,dict(attempts=[],prior={}),full_history=True)
  self.assertEqual(history.call_count,2)

 def test_recent_order_never_uses_public_provider_or_auto_books(self):
  data=inputs();worker=self.worker(data[0]);worker.state['attempts']=[dict(terminal_at=(NOW-dt.timedelta(seconds=30)).isoformat())]
  with patch.object(io,'clocks',return_value=NOW),patch.object(io,'load_cache',side_effect=AssertionError('No source fetch during settlement')),patch.object(runtime,'account_snapshot',side_effect=AssertionError('No speculative reads')),self.assertRaises(ca.CorporateActionPending):io.recover_worker(worker)
 def test_running_executor_blocks_close_capture_before_account_reads(self):
  import contextlib,cloud_account
  with patch.object(io,'clocks',return_value=NOW.replace(hour=15,minute=10)),patch.object(cloud_account,'verify_deployment'),patch.object(runtime,'mutex',side_effect=lambda p:contextlib.nullcontext()),patch.object(runtime,'active_session',return_value=Path('running')),patch.object(runtime,'account_snapshot',side_effect=AssertionError('No account reads')),self.assertRaises(ca.CorporateActionError):io.snapshot_close('2026-10-13')
 def test_close_books_late_dividend_with_get_only_and_archives_record_positions(self):
  import contextlib,cloud_account,choice_auth,verify_readonly,workflow_rebalance
  state,obs,cache,closes=inputs();clock=[NOW.replace(hour=15,minute=10)]
  def tick():
   value=clock[0];clock[0]+=dt.timedelta(seconds=6);return value
  with tempfile.TemporaryDirectory() as folder:
   folder=Path(folder);source=folder/'run';source.mkdir();io.save(source/'private_state.json',state);io.save(source/'config.json',CONFIG)
   http=Mock();http.headers={};http.audit=[dict(method='GET')];auth=Mock();auth.login.return_value={};broker=Mock()
   with patch.object(io,'root',return_value=folder/'ca'),patch.object(io,'clocks',side_effect=tick),patch.object(core,'config_checked',side_effect=lambda c:c),patch.object(workflow_rebalance,'effective',side_effect=lambda c,s:c),patch.object(workflow_rebalance,'formal_state',return_value=False),patch.object(cloud_account,'verify_deployment'),patch.object(runtime,'mutex',side_effect=lambda p:contextlib.nullcontext()),patch.object(runtime,'active_session',return_value=None),patch.object(runtime,'latest_session',return_value=source),patch.object(runtime,'Broker',return_value=broker) as constructor,patch.object(runtime,'account_snapshot',return_value=obs[-1]['raw']),patch.object(choice_auth,'ChoiceAuthenticator',return_value=auth),patch.object(choice_auth,'install_tokens'),patch.object(verify_readonly,'GetOnlySession',return_value=http),patch.object(io,'load_cache',return_value=cache),patch.object(io,'time') as timer:
    with patch.object(io,'closes_for',return_value=closes):snapshot=io.snapshot_close('2026-10-13')
    booked=io.read(source/'private_state.json');self.assertEqual(booked['baseline']['cash'],'11050.00');self.assertEqual(snapshot['positions'],{'SZ001309':980});constructor.assert_called_once_with({},allow_orders=False,session=http);broker.submit.assert_not_called();broker.cancel.assert_not_called()
    self.assertEqual(io.closes_for('2026-10-14')['2026-10-13']['positions'],{'SZ001309':980})
    path=folder/'ca/close_evidence/2026-10-13/account_snapshot_0.json';tampered=io.read(path);tampered['raw']['holds'][0]['count']+=1;io.save(path,tampered)
    with self.assertRaises(ca.CorporateActionError):io.closes_for('2026-10-14')
 def test_known_history_may_not_disappear_during_booking(self):
  data=inputs();data[0]['historical']={'123':dict(filled=100)}
  with self.assertRaises(ca.CorporateActionError):apply(data)

class SourceAndNotifications(unittest.TestCase):
 def test_announcement_rate_and_pay_date_cross_check(self):
  text='<p>证券代码001309 每10股派15.000000元人民币现金，每10股转增4股。股权登记日2026年10月12日，除权日2026年10月13日。现金红利将于2026年10月13日直接划入。</p>'
  terms=sources.announcement_terms(text.encode('gb18030'),event());self.assertEqual(terms['pay_date'],'2026-10-13')
  with self.assertRaises(sources.SourceError):sources.announcement_terms(text.replace('派15','派14').encode('gb18030'),event())
 def test_original_pdf_restores_missing_first_page_dates_without_guessing(self):
  fixture=Path(__file__).parent/'fixtures/600535_implementation.pdf'
  data=event('SH600535',cash='.24',ratio='0');data.update(record_date='2026-10-14',ex_date='2026-10-15',pay_date='2026-10-15')
  terms=sources.announcement_terms(fixture.read_bytes(),data);self.assertEqual(terms['pay_date'],'2026-10-15')
  data['ex_date']='2026-10-16'
  with self.assertRaises(sources.SourceError):sources.announcement_terms(fixture.read_bytes(),data)
 def test_cash_date_not_guessed_and_unsupported_host_blocked(self):
  text='证券代码001309 每10股派15元 每10股转增4股 登记日2026年10月12日 除权2026年10月13日'
  with self.assertRaises(sources.SourceError):sources.announcement_terms(text.encode('gb18030'),event())
  with self.assertRaises(sources.SourceError):sources.get(Mock(),'http://evil.invalid')
 def test_empty_cache_still_requires_untampered_discovery_evidence(self):
  import hashlib
  with tempfile.TemporaryDirectory() as folder,patch.object(io,'root',return_value=Path(folder)),patch.object(io,'clocks',return_value=NOW):
   payload=b'{"complete":true}';h=hashlib.sha256(payload).hexdigest();folder=Path(folder);(folder/'source_evidence').mkdir();p=folder/'source_evidence'/(h+'.bin');p.write_bytes(payload)
   cache=dict(schema=1,day='2026-10-13',as_of=NOW.isoformat(),complete=True,events=[],discovery_response_sha256=[h])
   io.save(folder/'cache'/'2026-10-13.json',cache);self.assertEqual(io.load_cache({'day':'2026-10-13'}),cache)
   p.write_bytes(b'tampered')
   with self.assertRaises(ca.CorporateActionError):io.load_cache({'day':'2026-10-13'})
 def test_notification_failure_remains_pending_and_retries(self):
  import contextlib
  with tempfile.TemporaryDirectory() as folder,patch.object(io,'root',return_value=Path(folder)),patch.object(io,'clocks',return_value=NOW),patch.object(runtime,'mutex',side_effect=lambda p:contextlib.nullcontext()):
   p=io.queue_notice('blocked','test','same');self.assertEqual(p,io.queue_notice('blocked','test','same'))
   module=Mock();module.send.side_effect=[False,True];spec=Mock()
   with patch.object(io.importlib.util,'spec_from_file_location',return_value=spec),patch.object(io.importlib.util,'module_from_spec',return_value=module):
    self.assertEqual(io.dispatch()['failed'],1);self.assertFalse(io.read(p)['delivered']);self.assertEqual(io.dispatch()['sent'],1)
   self.assertTrue(io.read(p)['delivered']);self.assertEqual(io.read(p)['attempts'],2)

if __name__=='__main__':unittest.main()
