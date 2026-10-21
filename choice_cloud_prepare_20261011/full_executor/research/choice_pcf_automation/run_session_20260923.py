"""Date-bound user-approved competition simulation; no real-money accounts."""
from contextlib import ExitStack
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
import sys
import time
import requests
from run_intraday_watch import ROOT, ACCOUNT, DIRECTORIES, Adapter
from verify_readonly import GetOnlySession
from session23_engine import DAY, PlanSession, PlanBroker, Watcher, reconcile, public_report
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError, SessionExpired, FillSnapshotPending
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn, read_json, digest, lock, calendar
from choice_auth import ChoiceAuthenticator, HumanLoginRequired, install_tokens, ENV_KEYS
from auth_recovery import RecoveryController, native_login_bridge
from run_session_20260922 import target_complete, confirm_completion, snapshot_wait_started

SIGNAL='2026-09-22'
DEST=ROOT/'research/choice_pcf_automation/session_20260923'
SOURCE=DEST.parent/'afternoon_resume_20260922'


def file_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def initial_state():
    seal=read_json(DEST/'approval_seal.json')
    if seal['user_instruction']!='好，就这样执行' or seal['execution_date']!=DAY or seal['simulation_only'] is not True:raise TradeError('Authorization mismatch')
    for relative,sha in seal['files'].items():
        if file_hash(ROOT/relative)!=sha:raise TradeError('Sealed input or code changed: '+relative)
    p=read_json(DEST/'plan.json');old=read_json(SOURCE/'private_state.json');proof=read_json(SOURCE/'independent_readonly_audit.json')
    if (p['execution_date'],p['pcf_date'],p['target_fraction'],p['capital_limit'],p['max_security_value'],p['max_order_value'],p['buy_upper_guard'],p['buy_drop_review'],p['sell_deviation_guard'],p['spacing_seconds'],p['excluded_cash_cny'])!=(DAY,SIGNAL,.5,5500000,500000,500000,.03,.03,None,5,'395.66'):raise TradeError('Plan scope changed')
    if p['simulation_only'] is not True or p['new_pcf_only'] is not True or DAY not in calendar({'calendar':str(DEST/'calendar.json')}):raise TradeError('Scope/calendar invalid')
    arc=DEST/'state/archive'/SIGNAL;record=read_json(arc/'pcf.json');manifest=read_json(arc/'manifest.json')
    stamp=dt.datetime.fromisoformat(manifest['captured_at'])
    if stamp.tzinfo is None or stamp.date().isoformat()!=SIGNAL or (arc/'REVISION_BLOCK.json').exists() or manifest['execution_date']!=DAY:raise TradeError('PCF capture invalid/revised')
    if record['date']!=SIGNAL or manifest['pcf_sha256']!=p['pcf_sha256'] or digest({k:record[k] for k in ('date','baseinfo','stocklist')})!=p['pcf_sha256']:raise TradeError('PCF digest mismatch')
    if digest(old)!=p['source_state_sha256'] or digest(proof)!=p['source_audit_sha256']:raise TradeError('Baseline evidence changed')
    if proof['issues'] or proof['pending'] or Decimal(proof['frozen_cny']) or proof['reconciliation_passes']!=2 or proof['submitted']!=len(old['attempts']) or read_json(SOURCE/'result_redacted.json')['status']!='closed':raise TradeError('Previous day unresolved')
    if old['last_audit']['positions']!=p['baseline_positions'] or Decimal(str(old['last_audit']['cash']))!=Decimal(str(p['baseline_cash'])) or Decimal(proof['cumulative_paid_cny'])!=Decimal(str(p['baseline_actual_paid'])):raise TradeError('Baseline mismatch')
    history=dict(old['historical']);history.update(old.get('prior',{}))
    for a in old['attempts']:
        if not a.get('oid') or not a.get('terminal') or a['state'] not in ('4','7','8','9') or a['oid'] in history:raise TradeError('Prior attempt unresolved/duplicate')
        history[a['oid']]=dict(a['order'],filled=a['filled'],state=a['state'])
    for c,t in p['targets'].items():
        if c!=t['code'] or t['quantity']<0 or t['quantity']%100 or t['reference_price']<=0 or t['quantity']*t['reference_price']>500000:raise TradeError('Invalid target')
        change=t['quantity']-p['baseline_positions'].get(c,0)
        if c.startswith('SH688') and 0<abs(change)<200 and not (change<0 and t['quantity']==0):raise TradeError('Untradeable STAR adjustment')
    return dict(day=DAY,pcf_sha256=p['pcf_sha256'],account_tail=ACCOUNT[-4:],historical=history,
        baseline=dict(positions=p['baseline_positions'],cash=str(p['baseline_cash']),paid=str(p['baseline_actual_paid'])),
        targets=p['targets'],requires_sell_orders=p['requires_sell_orders'],attempts=[],deferred={},quotes={},quote_failures={})


def seal_local():
    if now_cn().date().isoformat()!=SIGNAL or any((DEST/n).exists() for n in ('launch_attempt.json','process.json','private_state.json')):raise TradeError('Seal only before execution')
    # Seal the loaded execution dependency closure, not unrelated workspace apps/tests.
    # Other concurrent tasks can edit those without changing this authorized runner.
    files=[]
    for module in tuple(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if filename:
            path=__import__('pathlib').Path(filename).resolve()
            if path.suffix=='.py' and path.is_relative_to(ROOT):files.append(path)
    files=list(set(files))+[DEST.parent/'start_session_20260923.ps1']
    files += [DEST/n for n in ('plan.json','close_quotes.json','calendar.json','state/archive/'+SIGNAL+'/pcf.json','state/archive/'+SIGNAL+'/manifest.json')]
    files += [SOURCE/n for n in ('private_state.json','independent_readonly_audit.json','result_redacted.json')]
    seal=dict(user_instruction='好，就这样执行',authorized_on=SIGNAL,sealed_at=now_cn().isoformat(),execution_date=DAY,simulation_only=True,
        account_tail=ACCOUNT[-4:],first_sell_validation='One plan-required 100-share non-STAR sell, then two exact reconciliations before any further submission',
        files={f.relative_to(ROOT).as_posix():file_hash(f) for f in files})
    save(DEST/'approval_seal.json',seal);s=initial_state()
    print(json.dumps(dict(sealed=True,targets=len(s['targets']),historical_orders=len(s['historical']),orders_submitted=0)))


def readonly(preflight=False):
    if now_cn().date().isoformat() not in ((SIGNAL,DAY) if preflight else (DAY,)):raise TradeError('Read-only audit date invalid')
    state=initial_state() if preflight else read_json(DEST/'private_state.json')
    session=GetOnlySession(ACCOUNT);auth=ChoiceAuthenticator();keys=[*ENV_KEYS.values(),'CHOICE_ACCOUNT_ID'];oldenv={k:os.environ.get(k) for k in keys}
    try:
        method='existing_session'
        tokens=auth.current_tokens();install_tokens(tokens,session,ACCOUNT);tokens.clear()
        adapter=Adapter(ChoiceBroker({'initial_buy_only':True},False,session),session)
        try:raw=adapter.read()
        except SessionExpired:
            tokens=auth.login();install_tokens(tokens,session,ACCOUNT);tokens.clear();method='official_SSO';raw=adapter.read()
        for n in range(2):
            if n:time.sleep(5);raw=adapter.read()
            a=reconcile(raw,state)
        result=dict(checked_at=now_cn().isoformat(),mode='next_day_baseline' if preflight else 'independent_close_audit',account_tail=ACCOUNT[-4:],
            issues=['unfinished_orders'] if a['pending'] or Decimal(a['frozen']) else [],orders_submitted=0,cancellations=0,reconciliation_passes=2,
            held_securities=len(a['positions']),pending=a['pending'],frozen_cny=a['frozen'],account_cash_cny=a['account_cash_cny'],
            usable_cash_cny=a['cash'],excluded_cash_cny='395.66',market_value_cny=a['market_value'],stock_fraction=a['stock_fraction'],
            paid_cny=a['paid'],daily_fees_cny=a['fees'],remaining=a['missing'],sellable_securities=sum(v>0 for v in a['sellable'].values()),
            visible_orders=len(raw['orders']),visible_deals=len(raw['deals']),authentication=method)
        save(DEST/('preflight_redacted.json' if preflight else 'independent_readonly_audit.json'),result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();auth.close()


def launch_deadline(now):
    if now.date().isoformat()!=DAY or not dt.time(9,20)<=now.time()<=dt.time(9,50):raise TradeError('Outside authorized launch date/window')
    return now.replace(hour=9,minute=50,second=0,microsecond=0)


def report(w,status,reason=None):
    return dict(public_report(w,status,reason),trading_writes_suspended=w.broker.session.writes_suspended,pcf_date=SIGNAL,
        target_fraction=.5,capital_limit=5500000,max_security_value=500000,completion_verified_at=w.state.get('completion_verified_at'))


def execute():
    now=now_cn();deadline=launch_deadline(now);state=initial_state()
    if (DEST/'STOP').exists():raise TradeError('STOP exists')
    with (DEST/'launch_attempt.json').open('x',encoding='utf-8') as f:
        json.dump(dict(at=now.isoformat(),pid=os.getpid(),simulation_only=True,execution_date=DAY,initial_deadline=deadline.isoformat()),f);f.flush();os.fsync(f.fileno())
    oldenv={k:os.environ.get(k) for k in [*ENV_KEYS.values(),'CHOICE_ACCOUNT_ID']}
    session=PlanSession(DEST/'http_trace_redacted.jsonl');session.writes_suspended=True
    public=requests.Session();auth=ChoiceAuthenticator();locks=ExitStack();watcher=None;status='preflight';reason=None
    try:
        for directory in DIRECTORIES:locks.enter_context(lock(directory/'state/choice.lock'))
        for name in ('intraday_watch_20260921_v2','until_close_20260921','session_20260922','cash_continuation_20260922','snapshot_resume_20260922','afternoon_resume_20260922','session_20260923'):
            locks.enter_context(lock(DEST.parent/name/'watch.lock'))
        adapter=Adapter(None,session)
        def bind(tokens):
            install_tokens(tokens,session,ACCOUNT)
            adapter.broker=PlanBroker(ChoiceBroker({'initial_buy_only':True},False,session),session)
        try:tokens=auth.current_tokens();bind(tokens);tokens.clear()
        except HumanLoginRequired:pass
        watcher=Watcher(DEST/'private_state.json',state,adapter,lambda c:quote(c,public),now_cn,time.sleep);session.watcher=watcher
        recovery=RecoveryController(watcher,session,auth.login,bind,now_cn,time.sleep,
            lambda phase:save(DEST/'status_redacted.json',report(watcher,phase)),native_login=native_login_bridge,initial_deadline=deadline)
        try:recovery.verify();session.writes_suspended=False
        except SessionExpired:recovery.recover(initial=True)
        lag_since=None
        save(DEST/'status_redacted.json',report(watcher,'preflight_passed_waiting_for_0935'))
        while now_cn().time()<dt.time(15):
            if now_cn().date().isoformat()!=DAY:raise TradeError('Execution day expired')
            stopping=(DEST/'STOP').exists()
            try:status=watcher.tick(stopping=stopping);lag_since=None
            except SessionExpired:recovery.recover();status='resumed_after_authenticated_reconciliation';lag_since=None
            except FillSnapshotPending as error:
                current=time.monotonic();lag_since=snapshot_wait_started(watcher,lag_since,current);status='waiting_for_consistent_snapshot'
                watcher.state['last_snapshot_mismatch']=dict(at=now_cn().isoformat(),reason=str(error),continuous_failure_seconds=current-lag_since);watcher.flush()
                if current-lag_since>90:raise TradeError('Snapshots failed to converge within 90 seconds: '+str(error))
            if now_cn().time()>=dt.time(14,50):status='closing_readonly'
            r=report(watcher,status);save(DEST/'status_redacted.json',r)
            print(json.dumps({k:r[k] for k in ('at','status','submitted','stock_fraction','pending')}),flush=True)
            if stopping:break
            if target_complete(watcher):
                try:
                    if confirm_completion(watcher):status='completed_targets';session.writes_suspended=True;break
                except SessionExpired:recovery.recover()
                except FillSnapshotPending:pass
            close=now_cn().replace(hour=15,minute=0,second=0,microsecond=0)
            time.sleep(min(30,max(0,(close-now_cn()).total_seconds())))
        if status!='completed_targets':status='stopped_by_stop_file' if (DEST/'STOP').exists() else 'closed'
    except Exception as error:
        status='stopped_review_required';reason=str(error) if isinstance(error,TradeError) else type(error).__name__
    finally:
        if watcher:
            try:
                if not session.writes_suspended and now_cn().time()<dt.time(15):watcher.cancel_known(force=True)
            except Exception as error:status='stopped_review_required';reason='Cleanup: '+type(error).__name__
            end=time.monotonic()+60
            while True:
                try:
                    a=watcher.audit()
                    if not a['pending'] and Decimal(a['frozen'])==0:break
                except Exception as error:reason=str(error) if isinstance(error,TradeError) else type(error).__name__
                if time.monotonic()>=end:status='stopped_review_required';break
                time.sleep(5)
            session.writes_suspended=True;r=report(watcher,status,reason)
        else:r=dict(at=now_cn().isoformat(),status=status,reason=reason,transport_submissions=len(session.sent))
        save(DEST/'result_redacted.json',r);save(DEST/'status_redacted.json',r)
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();public.close();locks.close();auth.close()
    if status=='stopped_review_required':raise SystemExit(1)


def main():
    if sys.argv[1:]==['--seal-local']:seal_local()
    elif sys.argv[1:]==['--validate-local']:
        state=initial_state();print(json.dumps(dict(valid=True,targets=len(state['targets']),orders_submitted=0)))
    elif sys.argv[1:]==['--preflight-readonly']:readonly(True)
    elif sys.argv[1:]==['--audit-readonly']:readonly()
    elif sys.argv[1:]==['--execute-approved-simulation']:execute()
    else:raise TradeError('Explicit bounded mode required')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__,flush=True);raise SystemExit(1)
