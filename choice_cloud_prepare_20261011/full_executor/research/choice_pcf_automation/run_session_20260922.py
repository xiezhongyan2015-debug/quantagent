"""One authorized next-day continuation in the fixed Choice COMPETITION SIMULATION account."""
from contextlib import ExitStack
import datetime as dt
from decimal import Decimal
import json
import os
import sys
import time
import requests
from run_intraday_watch import ROOT,OLD,ACCOUNT,DIRECTORIES,WatchSession,Adapter,public_report
from verify_readonly import session_tokens,GetOnlySession
from watch_engine import Watcher,reconcile,reserve,save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError,SessionExpired,FillSnapshotPending
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,digest,lock,calendar
from choice_auth import ChoiceAuthenticator,HumanLoginRequired,install_tokens
from auth_recovery import RecoveryController,native_login_bridge

DAY='2026-09-22'
SIGNAL='2026-09-21'
DEST=ROOT/'research/choice_pcf_automation/session_20260922'
PREVIOUS=ROOT/'research/choice_pcf_automation/until_close_20260921'


def initial_state():
    plan=read_json(DEST/'plan.json');seal=read_json(DEST/'approval_seal.json')
    old=read_json(PREVIOUS/'private_state.json');proof=read_json(PREVIOUS/'independent_readonly_audit.json')
    if digest(plan)!=seal['plan_sha256'] or digest(old)!=seal['baseline_sha256']:raise TradeError('Sealed plan/baseline changed')
    if plan['execution_date']!=DAY or plan['pcf_date']!=SIGNAL or plan['requires_sell_orders']:raise TradeError('Wrong day or unsupported selling')
    if (plan['capital_limit'],plan['max_security_value'],plan['max_order_value'],plan['target_fraction'],plan['price_guard'],plan['spacing_seconds'])!=(5500000,500000,500000,.5,.03,5):raise TradeError('User-approved limits changed')
    if DAY not in calendar({'calendar':str(DEST/'calendar.json')}):raise TradeError('Not a verified trading day')
    arc=DEST/'state/archive'/SIGNAL
    pcf=read_json(arc/'pcf.json');manifest=read_json(arc/'manifest.json')
    captured=dt.datetime.fromisoformat(manifest['captured_at'])
    if captured.tzinfo is None or captured.date().isoformat()!=SIGNAL or (arc/'REVISION_BLOCK.json').exists():raise TradeError('PCF was not frozen before execution day, or revised')
    if pcf['date']!=SIGNAL or manifest['execution_date']!=DAY or digest({k:pcf[k] for k in ('date','baseinfo','stocklist')})!=plan['pcf_sha256'] or manifest['pcf_sha256']!=plan['pcf_sha256']:raise TradeError('PCF manifest mismatch')
    if proof['issues'] or proof['pending_count'] or float(proof['frozen_cny']) or read_json(PREVIOUS/'result_redacted.json')['status']!='closed':raise TradeError('Prior account was not cleanly closed')
    if proof['inventory']!=plan['baseline_positions'] or old['last_audit']['positions']!=plan['baseline_positions'] or Decimal(str(old['last_audit']['cash']))!=Decimal(str(plan['baseline_cash'])):raise TradeError('Prior holdings/cash baseline mismatch')
    history=dict(old['prior'])
    for a in old['attempts']:
        if not a.get('oid') or not a.get('terminal') or a['oid'] in history:raise TradeError('Prior attempt unresolved or duplicated')
        history[a['oid']]=dict(a['order'],filled=a['filled'],state=a['state'])
    security_reserve={}
    for o in history.values():
        if o['state'] not in ('4','7','8','9'):raise TradeError('Prior order still open')
        if o['filled']:
            c=o['code'];security_reserve[c]=security_reserve.get(c,Decimal(0))+reserve(Decimal(str(o['price']))*o['filled'])
    for c,o in plan['targets'].items():
        if o['quantity']<plan['baseline_positions'].get(c,0) or o['quantity']%100 or o['reference_price']<=0:raise TradeError('Invalid buy-only target')
        if o['quantity']*o['reference_price']>500000:raise TradeError('Target exceeds security limit')
    return dict(day=DAY,pcf_sha256=plan['pcf_sha256'],account_tail=ACCOUNT[-4:],prior={},historical=history,
                baseline=dict(positions=plan['baseline_positions'],cash=plan['baseline_cash'],paid=plan['baseline_actual_paid']),
                baseline_security_reserve={c:str(v) for c,v in security_reserve.items()},
                targets=plan['targets'],carried_reserve=plan['carried_reserve'],attempts=[],deferred={},quotes={},blocked_codes={},
                padding_ticks={'SZ000415':1,'SZ002152':1},fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3,
                research_fee_per_side=.0002,capital_limit=5500000,max_security_value=500000,max_order_value=500000,
                target_fraction=.50,quote_max_age_seconds=30,submission_spacing_seconds=5,stop_new_orders='14:50:00')


def report(watcher,status,reason=None):
    r=public_report(watcher,status,reason);a=watcher.state.get('last_audit',{})
    r.update(execution_date=DAY,pcf_date=SIGNAL,target_fraction=.50,capital_limit=5500000,max_security_value=500000,
             stock_market_value_cny=a.get('market_value'),total_assets_cny=a.get('total_assets'),stock_fraction=a.get('stock_fraction'),
             daily_paid_cny=a.get('daily_paid'),fees_scope='today only; cumulative paid includes prior-day fees',
             cumulative_reserved_cny=str(Decimal(str(watcher.state['carried_reserve']))+sum(Decimal(x['reserve']) for x in watcher.state['attempts'])))
    r['authentication_recovery']=watcher.state.get('auth_recovery',{})
    r['trading_writes_suspended']=getattr(watcher.broker.session,'writes_suspended',False)
    return r


def readonly(preflight=False):
    now=now_cn()
    if preflight:
        if now.date().isoformat() not in (SIGNAL,DAY):raise TradeError('Preflight date expired')
        state=initial_state()
    else:
        if now.date().isoformat()!=DAY:raise TradeError('Audit date expired')
        state=read_json(DEST/'private_state.json')
    session=GetOnlySession(ACCOUNT);old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')};auth=ChoiceAuthenticator()
    try:
        tokens=auth.current_tokens();os.environ['CHOICE_JTOKEN']=tokens.pop('JToken');tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker({'initial_buy_only':True},False,session)
        method='existing_session'
        try:raw=Adapter(broker,session).read()
        except SessionExpired:
            tokens=auth.login();session.headers['token']=tokens.pop('JToken');tokens.clear();method='official_SSO'
            raw=Adapter(broker,session).read()
        for check in range(2):
            if check:time.sleep(5);raw=Adapter(broker,session).read()
            a=reconcile(raw,state['prior'],state['attempts'],state['targets'],DAY,state['baseline'],state['historical'],5500000)
        # Read-only: never mutate the execution journal or mark an attempt complete.
        redacted=dict(checked_at=now_cn().isoformat(),mode='preflight' if preflight else 'independent_close_audit',issues=['unfinished_orders'] if a['pending'] else [],
                      account_tail=ACCOUNT[-4:],orders_submitted=0,cancellations=0,held_securities=len(a['positions']),
                      pending=a['pending'],frozen_cny=a['frozen'],cumulative_paid_cny=a['paid'],daily_paid_cny=a['daily_paid'],
                      daily_fees_cny=a['fees'],stock_fraction=a['stock_fraction'],stock_market_value_cny=a['market_value'],
                      authentication=method,reconciliation_passes=2,
                      remaining=a['missing'],submitted=len(state['attempts']),filled_shares=sum(x['filled'] for x in a['states'].values()),
                      cancelled=sum(x['state'] in ('7','8') for x in a['states'].values()))
        save(DEST/('preflight_redacted.json' if preflight else 'independent_readonly_audit.json'),redacted)
        print(json.dumps(redacted,ensure_ascii=False,indent=2))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();auth.close()


def target_complete(watcher):
    """Completion needs reconciled target holdings, not just filled sent orders."""
    state=watcher.state;a=state.get('last_audit')
    if not a or not state['targets'] or getattr(watcher.broker.session,'writes_suspended',False):return False
    if a['missing'] or a['pending'] or Decimal(str(a['frozen']))!=0:return False
    if any(a['positions'].get(c,0)!=o['quantity'] for c,o in state['targets'].items()):return False
    return all(x.get('oid') and x.get('terminal') for x in state['attempts'])


def confirm_completion(watcher):
    for check in range(2):
        if check:watcher.sleep(5)
        if watcher.path.with_name('STOP').exists() or watcher.clock().date().isoformat()!=watcher.state['day']:return False
        watcher.audit()
        if not target_complete(watcher):return False
    watcher.state['completion_verified_at']=watcher.clock().isoformat()
    watcher.flush()
    return True


def launch_deadline(now,immediate=False):
    if now.date().isoformat()!=DAY:raise TradeError('Wrong authorized launch date')
    if not immediate:
        if not dt.time(9,35)<=now.time()<=dt.time(9,50):raise TradeError('Outside single authorized launch date/window')
        return now.replace(hour=9,minute=50,second=0,microsecond=0)
    approval=read_json(DEST/'immediate_authorization.json')
    start=dt.datetime.fromisoformat(approval['authorized_at']);end=dt.datetime.fromisoformat(approval['expires_at'])
    if (approval.get('user_instruction')!='马上启动' or approval.get('execution_date')!=DAY or start.tzinfo is None or end.tzinfo is None
        or start.date().isoformat()!=DAY or end.date().isoformat()!=DAY or not dt.timedelta(0)<end-start<=dt.timedelta(minutes=15)
        or not start<=now<=end or now.time()>=dt.time(14,50)):
        raise TradeError('Immediate one-time authorization invalid/expired')
    return end


def snapshot_wait_started(watcher, previous, current):
    """Only consecutive failed audits count; successful in-batch audits reset lag."""
    return max(previous if previous is not None else current,
               getattr(watcher,'last_successful_audit_monotonic',0))


def execute(immediate=False):
    now=now_cn()
    initial_deadline=launch_deadline(now,immediate)
    state=initial_state();cfg=read_json(OLD/'pcf_automation.local.json')
    # Durable marker precedes authentication and all trading. Never delete/replay it.
    with (DEST/'launch_attempt.json').open('x',encoding='utf-8') as f:
        json.dump(dict(at=now.isoformat(),pid=os.getpid(),immediate_authorization=immediate,initial_deadline=initial_deadline.isoformat(),authorization='User approved 50% target, CNY5.5m total, CNY500k per security, +/-3%, simulation only'),f);f.flush();os.fsync(f.fileno())
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    oldenv={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    session=WatchSession(DEST/'http_trace_redacted.jsonl',DAY,500000);public=requests.Session();locks=ExitStack();watcher=None;auth=ChoiceAuthenticator()
    status='preflight';reason=None
    try:
        for directory in DIRECTORIES:locks.enter_context(lock(directory/'state/choice.lock'))
        for directory in (PREVIOUS,DEST,ROOT/'research/choice_pcf_automation/intraday_watch_20260921_v2'):locks.enter_context(lock(directory/'watch.lock'))
        session.writes_suspended=True
        adapter=Adapter(None,session)
        def bind_session(tokens):
            install_tokens(tokens,session,ACCOUNT)
            adapter.broker=ChoiceBroker(cfg['choice_contract'],True,session)
        try:
            tokens=auth.current_tokens();bind_session(tokens);tokens.clear()
        except HumanLoginRequired:pass
        watcher=Watcher(DEST/'private_state.json',state,adapter,lambda c:quote(c,public),now_cn,time.sleep)
        recovery=RecoveryController(watcher,session,auth.login,bind_session,now_cn,time.sleep,
                   lambda phase:save(DEST/'status_redacted.json',report(watcher,phase)),native_login=native_login_bridge,initial_deadline=initial_deadline)
        try:watcher.audit();session.writes_suspended=False
        except SessionExpired:recovery.recover(initial=True)
        lag_since=None
        save(DEST/'status_redacted.json',report(watcher,'preflight_passed'))
        while now_cn().time()<dt.time(15):
            if now_cn().date().isoformat()!=DAY:raise TradeError('Session expired')
            stopping=(DEST/'STOP').exists()
            try:status=watcher.tick(stopping=stopping);lag_since=None
            except SessionExpired:
                recovery.recover();status='resumed_after_authenticated_reconciliation';lag_since=None
            except FillSnapshotPending as error:
                current=time.monotonic()
                status='waiting_for_consistent_snapshot';lag_since=snapshot_wait_started(watcher,lag_since,current)
                watcher.state['last_snapshot_mismatch']=dict(at=now_cn().isoformat(),reason=str(error),
                    last_verified_at=watcher.state.get('last_audit',{}).get('checked_at'),continuous_failure_seconds=current-lag_since)
                watcher.flush()
                if current-lag_since>90:raise TradeError('Snapshots failed to converge within 90 seconds: '+str(error))
            if now_cn().time()>=dt.time(14,50):status='closing_readonly'
            r=report(watcher,status);save(DEST/'status_redacted.json',r)
            print(json.dumps({k:r[k] for k in ('at','status','submitted','fully_held_targets','stock_fraction','pending')},ensure_ascii=False),flush=True)
            if stopping:break
            if target_complete(watcher):
                try:
                    if confirm_completion(watcher):
                        status='completed_targets';session.writes_suspended=True;break
                except SessionExpired:recovery.recover()
                except FillSnapshotPending:pass
            close=now_cn().replace(hour=15,minute=0,second=0,microsecond=0)
            time.sleep(min(30,max(0,(close-now_cn()).total_seconds())))
        if status!='completed_targets':status='stopped_by_stop_file' if (DEST/'STOP').exists() else 'closed'
    except Exception as e:
        status='stopped_review_required';reason=str(e) if isinstance(e,TradeError) else type(e).__name__
    finally:
        if watcher:
            try:
                if not session.writes_suspended:watcher.cancel_known(force=True)
            except Exception as e:status='stopped_review_required';reason='Cleanup: '+type(e).__name__
            deadline=time.monotonic()+60
            while True:
                try:
                    a=watcher.audit()
                    if not a['pending']:break
                except Exception as e:reason=str(e) if isinstance(e,TradeError) else type(e).__name__
                if time.monotonic()>=deadline:status='stopped_review_required';break
                time.sleep(5)
            r=report(watcher,status,reason)
            r['completion_verified_at']=watcher.state.get('completion_verified_at')
        else:r=dict(at=now_cn().isoformat(),status=status,reason=reason,submissions=len(session.sent))
        save(DEST/'result_redacted.json',r);save(DEST/'status_redacted.json',r)
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();public.close();locks.close();auth.close()
    if status=='stopped_review_required':raise SystemExit(1)


def main():
    if sys.argv[1:]==['--preflight-readonly']:readonly(True)
    elif sys.argv[1:]==['--audit-readonly']:readonly(False)
    elif sys.argv[1:]==['--execute-approved-simulation']:execute()
    elif sys.argv[1:]==['--execute-approved-simulation','--immediate-authorization']:execute(immediate=True)
    else:raise TradeError('Choose explicit read-only or approved simulation mode')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(str(e) if isinstance(e,TradeError) else type(e).__name__,flush=True);raise SystemExit(1)
