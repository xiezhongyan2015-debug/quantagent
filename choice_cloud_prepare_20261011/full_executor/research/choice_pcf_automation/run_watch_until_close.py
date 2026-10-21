"""Explicit user-authorized continuation through today's close, simulation only."""
from contextlib import ExitStack
import datetime as dt
import hashlib
import json
import os
import sys
import time
import requests
from run_intraday_watch import (ROOT,OLD,DAY,ACCOUNT,DIRECTORIES,DEST as PREVIOUS_WATCH,
                                 WatchSession,Adapter,public_report)
from verify_readonly import session_tokens
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError,FillSnapshotPending
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.security_identity import validate_known_name,RENAME_300223
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,lock,calendar,digest
from watch_engine import Watcher,save,in_session

DEST=ROOT/'research/choice_pcf_automation/until_close_20260921'


def continuation_state():
    previous=read_json(PREVIOUS_WATCH/'result_redacted.json')
    state=read_json(PREVIOUS_WATCH/'private_state.json')
    if previous['status']!='test_completed' or previous['pending'] or previous['unresolved_intents']:
        raise TradeError('Previous watcher not cleanly finished')
    if any(not a.get('oid') or not a.get('terminal') for a in state['attempts']):raise TradeError('Prior attempt unresolved')
    proof=read_json(ROOT/'research/choice_pcf_automation/identity_300223_20260921/verification.json')
    if not proof['identity_verified'] or not proof['prices_same_previous_close'] or proof['security']!='SZ300223' or proof['issuer_notice']!=RENAME_300223['source']:
        raise TradeError('Missing independent name verification')
    validate_known_name('SZ300223',proof['current_name'],DAY)
    pcf=read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if state['pcf_sha256']!=digest({k:pcf[k] for k in ('date','baseinfo','stocklist')}):raise TradeError('PCF archive changed')
    state['blocked_codes'].pop('SZ300223',None);state['deferred'].pop('SZ300223',None)
    state['identity_event']=RENAME_300223
    state['continuation_started_at']=now_cn().isoformat()
    state['previous_attempt_count']=len(state['attempts'])
    state['previous_state_sha256']=hashlib.sha256((PREVIOUS_WATCH/'private_state.json').read_bytes()).hexdigest()
    return state


def main():
    if sys.argv[1:]!=['--execute-approved-simulation-until-close']:raise TradeError('Explicit continuation authorization required')
    now=now_cn();cfg=read_json(OLD/'pcf_automation.local.json')
    if now.date().isoformat()!=DAY or not in_session(now,DAY) or now.time()<dt.time(13,5) or DAY not in calendar(cfg):raise TradeError('Outside authorized afternoon')
    state=continuation_state();DEST.mkdir(exist_ok=True)
    with (DEST/'continuation_attempt.json').open('x',encoding='utf-8') as f:
        json.dump(dict(at=now.isoformat(),authorization='User: fix 300223 name issue and continue until close if normal',previous_attempts=state['previous_attempt_count'],stop_new_orders='14:50:00',finish='15:00:00'),f);f.flush();os.fsync(f.fileno())
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    oldenv={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    session=WatchSession(DEST/'http_trace_redacted.jsonl');public=requests.Session();locks=ExitStack();watcher=None
    status='preflight';reason=None;last_notice=None
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker(cfg['choice_contract'],True,session)
        for directory in DIRECTORIES:locks.enter_context(lock(directory/'state/choice.lock'))
        locks.enter_context(lock(PREVIOUS_WATCH/'watch.lock'));locks.enter_context(lock(DEST/'watch.lock'))
        watcher=Watcher(DEST/'private_state.json',state,Adapter(broker,session),lambda c:quote(c,public),now_cn,time.sleep)
        watcher.audit()  # Fresh exact-ID account check before any new submission.
        q=quote('SZ300223',public)
        if not q.get('identity_verification'):raise TradeError('300223 effective-dated quote identity not verified')
        lag_since=None
        while now_cn().time()<dt.time(15):
            if now_cn().date().isoformat()!=DAY:raise TradeError('Continuation expired')
            stop=(DEST/'STOP').exists()
            try:
                phase=watcher.tick(stopping=stop);lag_since=None
                status='closing_readonly' if now_cn().time()>=dt.time(14,50) else phase
            except FillSnapshotPending:
                status='waiting_for_consistent_snapshot';lag_since=lag_since or time.monotonic()
                if time.monotonic()-lag_since>90:raise TradeError('Account snapshots failed to converge')
            report=public_report(watcher,status)
            report.update(previous_attempt_count=state['previous_attempt_count'],new_submissions=len(session.sent),new_cancellations=len(session.cancelled),stop_new_orders='14:50:00',finish='15:00:00')
            save(DEST/'status_redacted.json',report)
            with (DEST/'observations_redacted.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(at=report['at'],status=status,remaining=report['remaining'],quotes=state['quotes'],new_submissions=len(session.sent)),ensure_ascii=False)+'\n')
            notice=(status,report['fully_held_targets'],len(session.sent),tuple(sorted((report['pending'] or {}).items())))
            if notice!=last_notice:
                print(json.dumps(dict(at=report['at'],status=status,held=report['held_securities'],new_submissions=len(session.sent),remaining=len(report['remaining']),pending=report['pending']),ensure_ascii=False),flush=True);last_notice=notice
            if stop:break
            closing=now_cn().replace(hour=15,minute=0,second=0,microsecond=0)
            time.sleep(min(30,max(0,(closing-now_cn()).total_seconds())))
        status='stopped_by_stop_file' if (DEST/'STOP').exists() else 'closed'
    except Exception as e:
        status='stopped_review_required';reason=str(e) if isinstance(e,TradeError) else type(e).__name__
    finally:
        if watcher:
            try:watcher.cancel_known(force=True)
            except Exception as e:status='stopped_review_required';reason='Cleanup: '+type(e).__name__
            deadline=time.monotonic()+60
            while True:
                try:
                    a=watcher.audit()
                    if not a['pending']:break
                except Exception as e:reason=str(e) if isinstance(e,TradeError) else type(e).__name__
                if time.monotonic()>=deadline:status='stopped_review_required';break
                time.sleep(5)
            result=public_report(watcher,status,reason)
            result.update(previous_attempt_count=state['previous_attempt_count'],new_submissions=len(session.sent),new_cancellations=len(session.cancelled),stop_new_orders='14:50:00',finish='15:00:00')
            save(DEST/'result_redacted.json',result);save(DEST/'status_redacted.json',result)
            print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        else:save(DEST/'result_redacted.json',dict(status=status,reason=reason,at=now_cn().isoformat(),new_submissions=len(session.sent)))
        for k,v in oldenv.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();public.close();locks.close()
    if status=='stopped_review_required':raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except TradeError as e:print(str(e));raise SystemExit(1)
