"""Durable evidence, read-only capture and alert outbox for corporate actions."""
import datetime as dt
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from corporate_actions import (CorporateActionError,CorporateActionPending,
                               digest,propose,dec,stable,moment)
from corporate_action_sources import fetch_events,announcement_terms,SourceError

def root():
 from console_core import HOME
 return HOME/'corporate_actions'
def read(path):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError):raise CorporateActionError('公司行动证据缺失或不可读：'+Path(path).name) from None
def save(path,value):
 from watch_engine import save as atomic_save
 atomic_save(Path(path),value)
def clocks():
 from quanta_agents.factor_lab_a.pcf_automation import now_cn
 return now_cn()
def closes_for(day):
 start=(dt.date.fromisoformat(day)-dt.timedelta(days=30)).isoformat();out={}
 for p in sorted((root()/'closes').glob('????-??-??.json')):
  if not start<=p.stem<=day:continue
  snapshot=read(p);folder=root()/'close_evidence'/p.stem
  ledger=read(folder/'source_ledger.json');config=read(folder/'risk_config.json')
  if snapshot.get('risk_config_sha256')!=digest(config):raise CorporateActionError('登记日风控配置证据哈希变化')
  observations=[read(folder/f'account_snapshot_{i}.json') for i in range(2)]
  if snapshot.get('snapshot_sha256')!=[digest(x) for x in observations] or snapshot['source_ledger_sha256']!=digest(ledger):raise CorporateActionError('登记日收盘原始账户/账本证据哈希变化')
  t=[moment(x['at']) for x in observations]
  if not 5<=(t[1]-t[0]).total_seconds()<=120 or any(x.date().isoformat()!=p.stem or x.hour<15 for x in t) or stable(observations[0]['raw'])!=stable(observations[1]['raw']):raise CorporateActionError('登记日两轮收盘账户证据无效')
  for x in observations:
   from console_core import reconcile
   audit=reconcile(x['raw'],ledger,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'],False)
   if audit['pending'] or dec(audit['frozen']):raise CorporateActionError('登记日存在未完成核账')
   positions={('SH' if str(h['secCode'])[0] in '569' else 'SZ')+str(h['secCode']):int(h['count']) for h in x['raw']['holds'] if int(h['count'])}
   if x['raw'].get('history_complete') is not True or positions!=snapshot['positions']:raise CorporateActionError('登记日持仓与原始完整账户快照不一致')
  out[p.stem]=snapshot
 return out
def scope_for(state,closes):
 codes=set(state['baseline']['positions'])
 for snapshot in closes.values():codes.update(snapshot['positions'])
 return codes

def queue_notice(kind,text,key):
 from console_runtime import mutex
 folder=root()/'outbox';name=hashlib.sha256(key.encode()).hexdigest()+'.json';path=folder/name
 with mutex(root()/'notice.lock'):
  if not path.exists():save(path,dict(schema=1,at=clocks().isoformat(),kind=kind,text=text,delivered=False,attempts=0))
 return path

def health():
 """Local task-health checks only; never queries or changes the account."""
 # Preserve the original module-path bootstrap before importing clock helpers.
 p=root()/'installation.json'
 # A read-only watchdog failure must not suppress existing alert delivery.
 try:
  from symbol_watchdog import run_once
  symbol_warnings=run_once(now=clocks())
 except Exception as error:
  symbol_warnings=1
  try:queue_notice('symbol_watchdog_failed',f'⚠️ {clocks().date().isoformat()} 单股监控任务异常：{type(error).__name__}；交易未被本监控暂停，原告警继续发送。','symbol-watchdog-hook:'+clocks().date().isoformat())
  except Exception:print(json.dumps(dict(symbol_watchdog_error=type(error).__name__,alert_outbox_error=True)))
 if not p.exists():return 0
 installed=read(p);now=clocks();day=now.date().isoformat()
 calendar=read(Path('C:/ChoiceBridge/quant/current/实盘日更/config/calendar_2026.json'))
 if day not in calendar:return 0
 warnings=symbol_warnings
 if day>=installed['first_close_date'] and now.time()>=dt.time(15,45) and not (root()/'closes'/f'{day}.json').exists():
  queue_notice('health_failed',f'⚠️ {day} 15:45 仍未封存登记日收盘持仓，自动分红送转证据未就绪；请检查收盘任务日志，交易风控未改变。','health:close:'+day);warnings+=1
 if day>=installed['first_preopen_date'] and dt.time(9,25)<=now.time()<dt.time(15):
  p=root()/'cache'/f'{day}.json';cache=read(p) if p.exists() else {}
  if not cache.get('complete') or cache.get('day')!=day or not 0<=(now-moment(cache.get('as_of'))).total_seconds()<=86400:
   queue_notice('health_failed',f'⚠️ {day} 09:25 后公司行动公告预检仍未就绪；无法核验的账户差异将按原风控暂停并告警。','health:preopen:'+day);warnings+=1
 save(root()/'health_last_seen.json',dict(at=now.isoformat(),warnings=warnings,day=day,symbol_watchdog_revision=1))
 return warnings

def dispatch(limit=5):
 """Failed deliveries remain durable; a one-minute task retries them."""
 from console_runtime import mutex
 path=Path('C:/ChoiceBridge/quant/current/实盘日更/tools/notify.py')
 spec=importlib.util.spec_from_file_location('ca_notifier',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 failed=0;sent=0
 with mutex(root()/'dispatch.lock'):
  pending=[p for p in (root()/'outbox').glob('*.json') if not read(p).get('delivered')]
  pending.sort(key=lambda p:(read(p).get('attempts',0),read(p).get('at','')))
  pending=pending[:limit]
  for p in pending:
   entry=read(p);entry['attempts']+=1;entry['last_attempt_at']=clocks().isoformat()
   try:ok=module.send(entry['text'])
   except Exception:ok=False
   entry['delivered']=bool(ok)
   if ok:entry['delivered_at']=clocks().isoformat();sent+=1
   else:failed+=1
   save(p,entry)
 return dict(sent=sent,failed=failed)

def refresh_cache(day,state):
 from cloud_account import ACCOUNT_SHA256
 closes=closes_for(day);codes=scope_for(state,closes)
 # With no record-date snapshot, do not pretend future booking is ready.
 if not closes:raise CorporateActionError('登记日收盘持仓库尚未初始化')
 cache=fetch_events(day,codes,now=clocks(),record_from=min(closes),evidence_dir=root()/'source_evidence')
 cache['account_sha256']=ACCOUNT_SHA256
 save(root()/'cache'/f'{day}.json',cache)
 save(root()/'source_status.json',dict(at=clocks().isoformat(),day=day,status='ready',events=len(cache['events']),scope=len(codes),cache_sha256=digest(cache)))
 return cache

def load_cache(state):
 day=state['day'];p=root()/'cache'/f'{day}.json';now=clocks()
 cache=read(p) if p.exists() else None
 if not cache or cache.get('complete') is not True or not 0<=(now-moment(cache.get('as_of'))).total_seconds()<=86400:cache=refresh_cache(day,state)
 # Recheck cached original source bytes before trusting stored parsed terms.
 hashes=list(cache.get('discovery_response_sha256',[]))
 if not hashes:raise CorporateActionError('缺少完整公告索引原文证据')
 for event in cache['events']:hashes.extend([event.get('announcement_sha256',''),event.get('listing_sha256','')])
 for value in hashes:
  blob=root()/'source_evidence'/(value+'.bin')
  if len(value)!=64 or not blob.is_file() or hashlib.sha256(blob.read_bytes()).hexdigest()!=value:raise CorporateActionError('公告原文证据缺失或哈希变化')
 for event in cache['events']:
  verified=announcement_terms((root()/'source_evidence'/(event['announcement_sha256']+'.bin')).read_bytes(),event)
  if any(verified[k]!=event[k] for k in ('pay_date','diluted_cash_per_share','announcement_sha256')):raise CorporateActionError('缓存方案与公告原文不一致')
 return cache

def recover_worker(worker):
 """Called ONLY for inventory/cash mismatch; original guards are retained."""
 from console_core import reconcile
 from console_runtime import account_snapshot
 now=clocks();old=worker.state
 from corporate_actions import known_inventory_and_cash
 # Ordinary in-flight settlement retains the original 90-second grace.
 if old['attempts']:
  stamps=[a.get('terminal_at') for a in old['attempts']]
  if not all(stamps) or any((now-moment(x)).total_seconds()<120 for x in stamps):raise CorporateActionPending('刚发生委托，沿用原核账等待，不猜测公司行动')
 probe=account_snapshot(worker.broker,old,full_history=True)
 inventory,expected_cash=known_inventory_and_cash(probe,old)
 seen={('SH' if str(x['secCode'])[0] in '569' else 'SZ')+str(x['secCode']):int(x['count']) for x in probe['holds']}
 variance=old.get('cash_variance',{});confirmed=dec(variance.get('amount_cny',0)) if variance and variance.get('day')==old['day'] else dec(0)
 if any(seen.get(c,0)<n for c,n in inventory.items()) or dec(probe['balance']['availBalance'])-dec(worker.c['excluded_cash_cny'])<expected_cash+confirmed:raise CorporateActionPending('成交快照尚未对齐，沿用原核账等待')
 cache=load_cache(old);closes=closes_for(old['day']);observations=[]
 for i in range(2):
  if worker.check_stop():raise CorporateActionError('执行器已停止，公司行动核验取消')
  raw=account_snapshot(worker.broker,old,full_history=True)
  observations.append(dict(at=clocks().isoformat(),raw=raw))
  if i==0:time.sleep(5)
 proposed,receipt=propose(old,observations,cache,closes,clocks(),reconcile,worker.c)
 # Use the existing conservative market-mark check as well; no new risk buffer.
 import workflow_rebalance as workflow
 if workflow.formal_state(old):workflow.valuation_bounds(proposed['last_audit'])
 if digest(read(worker.run/'private_state.json'))!=digest(old):raise CorporateActionError('核验期间账本发生变化')
 directory=worker.run/'corporate_action_evidence'/(clocks().strftime('%H%M%S')+'_'+uuid.uuid4().hex[:8])
 save(directory/'before_ledger.json',old);save(directory/'cache.json',cache);save(directory/'close_snapshots.json',closes)
 for i,obs in enumerate(observations):save(directory/f'account_snapshot_{i}.json',obs)
 save(directory/'receipt.json',receipt);save(directory/'proposed_ledger.json',proposed)
 worker.state=proposed;worker.flush()
 queue_notice('booked',f'✅ {old["day"]} 公司行动已自动入账：送转 {receipt["shares_credited"] or "无"}，现金分红 {receipt["cash_credit_cny"]} 元；两轮完整核账通过，原策略及风控未变。', 'booked:'+digest(receipt))
 return observations[-1]['raw']

def blocked(worker,error):
 reason=str(error)[:240]
 detail=dict(at=clocks().isoformat(),day=worker.day,status='blocked',reason=reason,source_ledger_sha256=digest(worker.state),run=str(worker.run),automatic_booking=False)
 save(worker.run/'corporate_action_block.json',detail)
 queue_notice('blocked',f'⚠️ {worker.day} 公司行动自动核验未通过：{reason}；保持原风控，暂停新增委托，需人工审核。',f'blocked:{worker.day}:{worker.run}:{reason}')

class ReadOnlyBooking:
 """Close-time ledger booking only. No executor launch or account writes."""
 def __init__(self,source,state,config,broker):
  self.run=source;self.state=state;self.c=config;self.broker=broker;self.day=state['day']
 def check_stop(self):return False
 def flush(self):save(self.run/'private_state.json',self.state)

def snapshot_close(day):
 """Book strictly proven late actions, then capture actual close inventory."""
 from console_core import reconcile,config_checked
 from quanta_agents.factor_lab_a.choice_pcf import FillSnapshotPending
 from console_runtime import latest_session,active_session,mutex,APP_DIR,account_snapshot,Broker
 from cloud_account import ACCOUNT,ACCOUNT_SHA256,verify_deployment
 from choice_auth import ChoiceAuthenticator,install_tokens
 from verify_readonly import GetOnlySession
 now=clocks()
 if day!=now.date().isoformat() or now.time()<dt.time(15,0):raise CorporateActionError('收盘持仓只能在当天15:00后记录')
 verify_deployment()
 with mutex(APP_DIR/'launch.lock'),mutex(APP_DIR/'account_execution.lock'):
  if active_session():raise CorporateActionError('交易进程仍在运行，暂不写登记日收盘证据')
  source=latest_session();state=read(source/'private_state.json')
  config=config_checked(read(source/'config.json') if (source/'config.json').exists() else state['config_snapshot'])
  import workflow_rebalance as workflow
  config=workflow.effective(config,state);before=digest(state)
  if state['day']!=day:raise CorporateActionError('缺少当天账本，不能猜测登记日持仓')
  http=GetOnlySession(ACCOUNT);auth=ChoiceAuthenticator();observations=[];audits=[]
  try:
   tokens=auth.login();install_tokens(tokens,http,ACCOUNT);tokens.clear();broker=Broker({},allow_orders=False,session=http)
   preliminary=account_snapshot(broker,state,full_history=True)
   try:reconcile(preliminary,state,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'],False)
   except FillSnapshotPending as error:
    if not any(text in str(error) for text in ('持仓数量与精确关联成交','可用现金、冻结资金与成交净额')):raise
    booking=ReadOnlyBooking(source,state,config,broker)
    recover_worker(booking)
    state=booking.state;before=digest(state)
   for i in range(2):
    raw=account_snapshot(broker,state,full_history=True);at=clocks().isoformat()
    from corporate_actions import known_inventory_and_cash
    known_inventory_and_cash(raw,state)
    a=reconcile(raw,state,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'],False)
    if workflow.formal_state(state):workflow.valuation_bounds(a)
    if a['pending'] or dec(a['frozen']):raise CorporateActionError('收盘仍有挂单或冻结资金')
    observations.append(dict(at=at,raw=raw));audits.append(a)
    if i==0:time.sleep(5)
   if stable(observations[0]['raw'])!=stable(observations[1]['raw']) or digest(read(source/'private_state.json'))!=before or active_session():raise CorporateActionError('收盘两轮核验期间账户或账本变化')
   if any(x['method']!='GET' for x in http.audit):raise CorporateActionError('收盘持仓证据出现账户写请求')
   positions=audits[-1]['positions'];p=root()/'closes'/f'{day}.json'
   result=dict(schema=1,day=day,at=observations[-1]['at'],account_sha256=ACCOUNT_SHA256,positions=positions,positions_sha256=digest(positions),verified=True,source_ledger=str(source/'private_state.json'),source_ledger_sha256=before,risk_config_sha256=digest(config),snapshot_sha256=[digest(x) for x in observations],new_orders=0,cancellations=0)
   if p.exists():
    if read(p)['positions']!=positions:raise CorporateActionError('已封存的登记日持仓发生变化，禁止覆盖')
    return read(p)
   evidence=root()/'close_evidence'/day
   for i,x in enumerate(observations):save(evidence/f'account_snapshot_{i}.json',x)
   save(evidence/'source_ledger.json',state);save(evidence/'risk_config.json',config)
   save(evidence/'http_trace_redacted.json',http.audit);save(p,result)
   return result
  finally:http.headers.clear();http.close();auth.close()

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--preload');parser.add_argument('--close');parser.add_argument('--dispatch',action='store_true');args=parser.parse_args()
 try:
  if args.dispatch:
   warnings=health();result=dispatch();result['health_warnings']=warnings;print(json.dumps(result));return 2 if result['failed'] else 0
  if args.close:result=snapshot_close(args.close)
  elif args.preload:
   from console_runtime import latest_session
   from console_core import days_for
   from cloud_account import verify_deployment
   verify_deployment();state=read(latest_session()/'private_state.json')
   # Validate the date against the existing trading calendar.
   from console_runtime import APP_DIR
   days_for(read(APP_DIR/'settings.json'),args.preload)
   result=refresh_cache(args.preload,state)
  else:parser.error('Choose --preload, --close or --dispatch')
  print(json.dumps(dict(ok=True,day=result.get('day'),events=len(result.get('events',[])),positions=len(result.get('positions',{}))),ensure_ascii=False));return 0
 except Exception as error:
  reason=str(error) if isinstance(error,(CorporateActionError,CorporateActionPending,SourceError)) else type(error).__name__
  day=args.close or args.preload or clocks().date().isoformat()
  queue_notice('precheck_failed',f'⚠️ {day} 公司行动预检/收盘持仓封存失败：{reason}；未放宽核账或交易风控。',f'precheck:{day}:{reason}')
  save(root()/'source_status.json',dict(at=clocks().isoformat(),day=day,status='blocked',reason=reason))
  print(json.dumps(dict(ok=False,reason=reason),ensure_ascii=False));dispatch();return 2

if __name__=='__main__':raise SystemExit(main())
