"""2026-09-21 authorized afternoon simulation test; never real-money trading."""
import argparse
from contextlib import ExitStack
import datetime as dt
import json
import os
from pathlib import Path
import time
import requests
from run_full_basket import ROOT,OLD,RETEST,ACCOUNT,DAY,journal_read
from run_queued_basket import DEST as QUEUED,PREVIOUS
from verify_readonly import GetOnlySession,session_tokens
from quanta_agents.factor_lab_a.choice_pcf import BASE,ChoiceBroker,TradeError,SessionExpired,FillSnapshotPending
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json,digest,lock,calendar
from watch_engine import Watcher,save,in_session

FAILED_PREFLIGHT=ROOT/'research/choice_pcf_automation/intraday_watch_20260921'
DEST=ROOT/'research/choice_pcf_automation/intraday_watch_20260921_v2'
SUPPLEMENT=QUEUED.parent/'queued_supplement_20260921'
DIRECTORIES=[OLD,RETEST,PREVIOUS,QUEUED,SUPPLEMENT]


class WatchSession(requests.Session):
    """Allow only a single explicitly armed write, in the fixed simulation account."""
    def __init__(self,path,day=DAY,max_order_value=100000):
        super().__init__();self.path=path;self.armed=None;self.sent=set();self.cancelled=set();self.counter=0
        self.day=day;self.max_order_value=max_order_value
        self.writes_suspended=False

    def request(self,method,url,**kw):
        endpoint=url[len(BASE):] if url.startswith(BASE) else '';method=method.upper()
        if kw.get('allow_redirects') is not False:raise TradeError('Redirects prohibited')
        if method=='GET':
            params=kw.get('params') or {}
            if endpoint not in GetOnlySession.KEYS or set(params)!=GetOnlySession.KEYS[endpoint] or params['accId']!=ACCOUNT:raise TradeError('Query outside fixed simulation account')
        elif method=='POST':
            arm=self.armed;self.armed=None
            if self.writes_suspended:raise TradeError('Trading writes suspended until authenticated reconciliation')
            p=kw.get('json') or {};now=now_cn()
            if not arm or p.get('accId')!=ACCOUNT or now.date().isoformat()!=self.day:raise TradeError('Unarmed/expired simulation write')
            kind,expected=arm
            if p.get('stkCode')!=expected['code'][2:] or p.get('mktCode')!=('1' if expected['code'].startswith('SH') else '0'):raise TradeError('Wrong armed security')
            if kind=='submit':
                if endpoint!='AguTrade/SpoOrder' or not in_session(now,self.day):raise TradeError('Wrong submission endpoint/window')
                if p.get('orderDrt')!=1 or p.get('volume')!=expected['quantity'] or float(p.get('price',0))!=expected['price']:raise TradeError('Wrong armed buy quantity/price')
                if expected['quantity']*expected['price']>self.max_order_value or abs(expected['price']/expected['reference_price']-1)>.03000000001:raise TradeError('Transport price/value guard')
                self.counter+=1;self.sent.add(self.counter)
            elif kind=='cancel':
                oid=str(p.get('orderId'))
                if endpoint!='AguTrade/SpoCancel' or oid!=expected['oid'] or oid in self.cancelled or now.time()>=dt.time(15):raise TradeError('Wrong/duplicate/late cancellation')
                self.cancelled.add(oid)
            else:raise TradeError('Unknown armed write')
        else:raise TradeError('HTTP method prohibited')
        event=dict(at=now_cn().isoformat(),method=method,endpoint=endpoint)
        try:
            r=super().request(method,url,**kw);event['http_status']=r.status_code
            try:event['business_code']=r.json().get('Code')
            except ValueError:pass
            return r
        except Exception as e:event['error_type']=type(e).__name__;raise
        finally:
            with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n');f.flush()


class Adapter:
    def __init__(self,broker,session):self.broker=broker;self.session=session
    def read(self):
        if self.broker is None:raise SessionExpired()
        return dict(orders=self.broker._rows('AguTrade/SpoOrders'),deals=self.broker._rows('AguTrade/SpoDeal'),
                    holds=self.broker._rows('AguTrade/SpoHold'),balance=self.broker._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data'])
    def submit(self,order):
        if self.broker is None:raise TradeError('Unauthenticated trading adapter')
        self.session.armed=('submit',dict(order))
        try:return self.broker.submit(order)
        finally:self.session.armed=None
    def cancel(self,oid,order):
        if self.broker is None:raise TradeError('Unauthenticated trading adapter')
        self.session.armed=('cancel',dict(order,oid=oid))
        try:return self.broker.cancel(oid,order)
        finally:self.session.armed=None


def initial_state():
    reviewed=read_json(SUPPLEMENT/'independent_whole_account_audit.json')
    age=(now_cn()-dt.datetime.fromisoformat(reviewed['checked_at'])).total_seconds()
    if not 0<=age<=600 or reviewed['issues'] or reviewed['pending_count']:raise TradeError('Fresh clean independent account audit required')
    original,_,_=journal_read(OLD);targets={o['code']:o for o in original['orders']}
    manifest=read_json(OLD/'state/archive/2026-09-18/manifest.json');pcf=read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if digest({k:pcf[k] for k in ('date','baseinfo','stocklist')})!=original['pcf_sha256'] or manifest['pcf_sha256']!=original['pcf_sha256']:raise TradeError('Archived PCF digest changed')
    prior={};evidence={(r['run'],r['code']):r for r in reviewed['verified_orders']}
    for directory in DIRECTORIES:
        _,rows,_=journal_read(directory)
        for code,state,oid,filled in rows:
            if not oid:
                if state not in ('DEFERRED_PRICE','DEFERRED_PRECISION'):raise TradeError('Previous unknown intent')
                continue
            e=evidence[(directory.name,code)]
            if e['status'] not in ('4','7','8','9'):raise TradeError('Prior pending order')
            if str(oid) in prior:raise TradeError('Duplicate prior acknowledgment')
            prior[str(oid)]=dict(code=code,side=1,quantity=e['quantity'],price=e['accepted_limit'],filled=e['filled'],state=e['status'])
    return dict(day=DAY,pcf_sha256=original['pcf_sha256'],account_tail=ACCOUNT[-4:],prior=prior,targets=targets,
                carried_reserve=reviewed['cumulative_reserved_cny'],attempts=[],deferred={},quotes={},
                blocked_codes={'SZ300223':'Public quote name does not match independent issuer disclosure; not verified'},
                padding_ticks={'SZ000415':1,'SZ002152':1},fill_timeout_seconds=60,retry_cooldown_seconds=60,max_attempts_per_code=3,
                research_fee_per_side=.0002,capital_limit=2000000,max_order_value=100000,quote_max_age_seconds=30,
                scan_interval_seconds=30,submission_spacing_seconds=5,stop_new_orders='14:50:00')


def public_report(watcher,status,reason=None):
    s=watcher.state;a=s.get('last_audit',{});positions=a.get('positions',{})
    attempts=[dict(code=x['order']['code'],quantity=x['order']['quantity'],limit=x['order']['price'],
                   order_id_tail=x.get('oid','')[-4:],state=x['state'],filled=x.get('filled',0),
                   cancel_requested=bool(x.get('cancel_requested_at'))) for x in s['attempts']]
    if not a:
        return dict(at=now_cn().isoformat(),account_tail=ACCOUNT[-4:],status=status,reason=reason,
                    submitted=len(attempts),filled_shares=None,attempts=attempts,held_securities=None,fully_held_targets=None,
                    remaining=[],paid_cny=None,actual_fees_cny=None,frozen_cny=None,pending=None,last_verified_at=None,
                    account_state_unknown=True,unresolved_intents=sum(not x.get('oid') for x in s['attempts']))
    return dict(at=now_cn().isoformat(),account_tail=ACCOUNT[-4:],status=status,reason=reason,
                submitted=len(attempts),filled_shares=sum(x['filled'] for x in attempts),attempts=attempts,
                held_securities=len(positions),fully_held_targets=sum(positions.get(c,0)==o['quantity'] for c,o in s['targets'].items()),
                remaining=[dict(code=c,target=o['quantity'],held=positions.get(c,0),pending=a.get('pending',{}).get(c,0),reason=s['deferred'].get(c)) for c,o in s['targets'].items() if positions.get(c,0)<o['quantity']],
                paid_cny=a.get('paid'),actual_fees_cny=a.get('fees'),frozen_cny=a.get('frozen'),pending=a.get('pending',{}),
                last_verified_at=a.get('checked_at'),unresolved_intents=sum(not x.get('oid') for x in s['attempts']))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute-approved-simulation',action='store_true');ap.add_argument('--test-seconds',type=int,default=300)
    args=ap.parse_args()
    if not args.execute_approved_simulation or not 60<=args.test_seconds<=3600:raise TradeError('Explicit simulation test flag and bounded duration required')
    now=now_cn();cfg=read_json(OLD/'pcf_automation.local.json')
    if now.date().isoformat()!=DAY or now.time()<dt.time(13,5) or not in_session(now,DAY) or DAY not in calendar(cfg):raise TradeError('Outside authorized afternoon/date/calendar')
    # Repair continuation is permitted only after a proven pre-submit failure.
    failed=read_json(FAILED_PREFLIGHT/'result_redacted.json');old_state=read_json(FAILED_PREFLIGHT/'private_state.json')
    traces=[json.loads(x) for x in (FAILED_PREFLIGHT/'http_trace_redacted.jsonl').read_text(encoding='utf-8').splitlines()]
    if failed.get('transport_submissions')!=0 or failed.get('transport_cancellations')!=0 or old_state['attempts'] or any(x['method']!='GET' for x in traces):
        raise TradeError('First test had writes/uncertain intents; repair continuation prohibited')
    state=initial_state();DEST.mkdir(exist_ok=True)
    # Single run, durable marker. Never remove to replay an uncertain test.
    with (DEST/'watch_attempt.json').open('x',encoding='utf-8') as f:
        json.dump(dict(at=now.isoformat(),authorization='User: add watcher functions and test this afternoon in existing competition simulation account',seconds=args.test_seconds),f);f.flush();os.fsync(f.fileno())
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    old={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    session=WatchSession(DEST/'http_trace_redacted.jsonl');public=requests.Session();watcher=None;status='preflight';reason=None;locks=ExitStack()
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker(cfg['choice_contract'],True,session)
        for directory in DIRECTORIES:locks.enter_context(lock(directory/'state/choice.lock'))
        locks.enter_context(lock(DEST/'watch.lock'))
        watcher=Watcher(DEST/'private_state.json',state,Adapter(broker,session),lambda code:quote(code,public),now_cn,time.sleep)
        end=time.monotonic()+args.test_seconds;lag_since=None
        while time.monotonic()<end and now_cn().time()<dt.time(14,50):
            try:
                status=watcher.tick();lag_since=None
            except FillSnapshotPending:
                status='waiting_for_consistent_snapshot';lag_since=lag_since or time.monotonic()
                if time.monotonic()-lag_since>90:raise TradeError('Account snapshots failed to converge')
            report=public_report(watcher,status);save(DEST/'status_redacted.json',report)
            print(json.dumps(dict(at=report['at'],status=status,submitted=report['submitted'],fully_held=report['fully_held_targets'],remaining=len(report['remaining']),pending=report['pending']),ensure_ascii=False),flush=True)
            if status=='stopped':break
            if report['fully_held_targets']==len(state['targets']) and not report['pending']:break
            time.sleep(min(30,max(0,end-time.monotonic())))
        status='test_completed'
    except Exception as error:
        status='stopped_review_required';reason=str(error) if isinstance(error,TradeError) else type(error).__name__
    finally:
        if watcher:
            # Only this watcher's known IDs. Unknown outcomes remain blocked.
            try:watcher.cancel_known(force=True)
            except Exception as e:status='stopped_review_required';reason='Cleanup request: '+type(e).__name__
            deadline=time.monotonic()+60
            while True:
                try:
                    audit=watcher.audit()
                    if not audit['pending']:break
                except Exception as e:
                    reason=str(e) if isinstance(e,TradeError) else type(e).__name__
                if time.monotonic()>=deadline:
                    status='stopped_review_required';break
                time.sleep(5)
            result=public_report(watcher,status,reason);result['test_seconds_requested']=args.test_seconds
            result['transport_submissions']=len(session.sent);result['transport_cancellations']=len(session.cancelled)
            save(DEST/'result_redacted.json',result);save(DEST/'status_redacted.json',result)
            print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        else:print(json.dumps(dict(status=status,reason=reason),ensure_ascii=False))
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close();public.close()
        locks.close()
    if status=='stopped_review_required':raise SystemExit(1)


if __name__=='__main__':
    try:main()
    except TradeError as e:print(str(e));raise SystemExit(1)
