"""Standalone supervisor and Choice simulation worker; never needs a Codex session."""
import argparse
import gc
import threading
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import datetime as dt
import json
import os
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
import psutil
import requests
from console_core import BASE_DIR, HOME as APP_DIR, ROOT, DEFAULTS, TERMINAL, config_checked, days_for, session_phase, legacy_state, roll_day, reconcile, dec, pcf_targets, parse_targets, differences, isolated_attempts
from watch_engine import save, reserve
from batch_engine import validate_quote, stable_price_at_ask
from choice_auth import ChoiceAuthenticator, HumanLoginRequired, LoginUnavailable, install_tokens
from cloud_account import ACCOUNT
from verify_readonly import GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import BASE, ChoiceBroker, TradeError, SessionExpired, FillSnapshotPending, order_payload
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf import download_pcf, normalize_code
from quanta_agents.factor_lab_a.pcf_automation import CN, now_cn, read_json, digest, lock as legacy_lock
import position_floor as floor
import receipt_recovery as receipts
import pilot_one_lot as pilot
import workflow_rebalance as workflow
from console_core import submission_count

PYTHON=Path(sys.executable).with_name('python.exe')
LEGACY=[BASE_DIR/'cloud_ledger']
APPROVED_PROFILES={}


@contextlib.contextmanager
def mutex(path):
    """OS-owned lock releases on crashes; no deleting stale lock files."""
    import msvcrt
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise TradeError('另一个启动或交易进程正在运行') from None
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)


def session_dirs():
    discovered=[p for p in BASE_DIR.iterdir() if p.is_dir() and ((p/'process.json').exists() or (p/'private_state.json').exists())]
    return sorted(set(LEGACY+discovered+list((APP_DIR/'runs').glob('*/*'))))


def alive(directory):
    path=Path(directory)/'process.json'
    if not path.exists():return False
    info=read_json(path)
    try:
        process=psutil.Process(int(info['pid']))
        cmd=' '.join(process.cmdline())
        if 'created_seconds' in info and abs(process.create_time()-info['created_seconds'])>1:return False
        return info.get('script','') in cmd and bool(info.get('script')) and process.is_running()
    except psutil.NoSuchProcess:return False
    except psutil.AccessDenied:raise TradeError('无法确认已有进程身份，暂不启动第二个进程') from None


def active_session():
    active=[p for p in session_dirs() if alive(p)]
    if len(active)>1:raise TradeError('检测到多个交易进程，请先停止并核查')
    return active[0] if active else None


def latest_session():
    candidates=[p for p in session_dirs() if (p/'private_state.json').exists()]
    if not candidates:raise TradeError('找不到已核验账户账本，不能猜测初始持仓或现金')
    def chronology(p):
        s=read_json(p/'private_state.json')
        return (s.get('day',''),submission_count(s),(p/'private_state.json').stat().st_mtime)
    return max(candidates,key=chronology)


def historical_rows(broker,endpoint):
    rows=[]
    for page in range(100):
        data=broker._request(endpoint,params=dict(accId=ACCOUNT,dataRange=2,pageNo=page,pageSize=1000))['Data']
        part=data['data']
        if not isinstance(part,list):raise TradeError('历史查询格式异常')
        rows.extend(part)
        if len(part)<1000:
            if any(int(data[k])>len(rows) for k in ('total','totalCount','count') if k in data):raise TradeError('历史查询分页不完整')
            return rows
    raise TradeError('历史查询超过分页上限，请核查账户')


def account_snapshot(broker,state):
    """The platform moves today's rows into history after close; retain exact IDs."""
    # These four GETs have no dependency. Read them concurrently so hold marks
    # and the account balance are sampled closer together, without batching or
    # skipping exact order/fill/share reconciliation.
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='choice-read') as pool:
        orders_future=pool.submit(broker._rows,'AguTrade/SpoOrders')
        deals_future=pool.submit(broker._rows,'AguTrade/SpoDeal')
        holds_future=pool.submit(broker._rows,'AguTrade/SpoHold')
        balance_future=pool.submit(broker._request,'AguTrade/SpoBalInfo',params={'accId':ACCOUNT})
        orders=orders_future.result();deals=deals_future.result()
        holds=holds_future.result();balance=balance_future.result()['Data']
    expected=set(state.get('prior',{}))|{a['oid'] for a in state['attempts'] if a.get('oid')}
    ids={str(r['orderId']) for r in orders}
    history_complete=False
    if not orders or expected-ids or any(not a.get('oid') for a in state['attempts']) or state.get('expired_unacknowledged'):
        history=historical_rows(broker,'AguTrade/SpoOrders')
        seen=set()
        for row in history:
            oid=str(row['orderId'])
            if oid in seen:raise TradeError('历史委托分页出现重复ID')
            seen.add(oid)
            if oid not in ids:orders.append(row)
        current_deal_ids={str(r['orderId']) for r in deals}
        deals.extend(row for row in historical_rows(broker,'AguTrade/SpoDeal') if str(row['orderId']) not in current_deal_ids)
        history_complete=True
    return dict(orders=orders,deals=deals,holds=holds,balance=balance,history_complete=history_complete)


def status_view(directory=None):
    directory=Path(directory) if directory else active_session() or latest_session()
    result=dict(directory=str(directory),running=alive(directory))
    if (directory/'status_redacted.json').exists():result.update(read_json(directory/'status_redacted.json'))
    if all(key in result for key in ('verified_at','cash','frozen','remaining_count')):
        result['unresolved']=result.get('unresolved_intents',0)
        return result
    if (directory/'private_state.json').exists():
        state=read_json(directory/'private_state.json');a=state.get('last_audit',{})
        result.update(submitted=submission_count(state),filled_shares=sum(x.get('filled',0) for x in state['attempts']),
            verified_at=a.get('checked_at'),stock_fraction=a.get('stock_fraction'),cash=a.get('cash'),frozen=a.get('frozen'),
            remaining_count=len(differences(state.get('targets',{}),a)) if a else None,
            unresolved=sum(not x.get('oid') for x in state['attempts']),position_compliance=state.get('position_compliance'))
    return result


def pilot_start_day(c,now):
    """Allow the operator to arm the fixed pilot at most one calendar day early."""
    day=c['execution_date'];today=now.date();execution=dt.date.fromisoformat(day)
    days_for(c,day)
    if not 0<=(execution-today).days<=1:raise TradeError('仅可在试单当天或前一天预约固定日期的一手试单')
    if today==execution and now.time()>=dt.time(14,50):raise TradeError('试单新增委托窗口已结束，预约已失效')
    return day


def wait_for_pilot_open(run):
    """An operator-created run waits locally; no tokens, broker or network calls.

    Authentication, fresh prices and complete account checks happen only after
    the execution window opens. STOP before then prevents all account activity.
    """
    c=config_checked(read_json(run/'config.json'));a=read_json(run/'authorization.json')
    seed=read_json(run/'seed.json')
    if (a.get('execution_date')!=c['execution_date'] or a.get('config_sha256')!=digest(c)
        or a.get('seed_sha256')!=digest(seed) or a.get('account_mode')!='competition_simulation_only'):
        raise TradeError('预约计划与本人启动记录不一致')
    day=pilot_start_day(c,now_cn())
    reason='已预约10月12日09:30；先核验并试100股，完整成交和两轮核账通过后进入封存目标的正式调仓' if c['strategy']=='pilot_then_targets' else '已预约10月12日09:30；届时重新核验身份、账本及实时报价，仅尝试一手'
    save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='scheduled_waiting',reason=reason,execution_date=day))
    while True:
        if (run/'STOP').exists():
            save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='stopped',reason='预约已取消；未启动账户查询或交易'))
            return False
        now=now_cn()
        pilot_start_day(c,now)
        if now.date().isoformat()==day and now.time()>=dt.time(9,30):return True
        time.sleep(2)


def start_console(config):
    from cloud_account import verify_deployment
    verify_deployment()
    c=config_checked(config);now=now_cn();day=pilot_start_day(c,now)
    APP_DIR.mkdir(parents=True,exist_ok=True)
    with mutex(APP_DIR/'launch.lock'):
        active=active_session()
        if active:
            existing=read_json(active/'config.json') if (active/'config.json').exists() else {}
            if c['strategy']=='pilot_then_targets' and existing.get('strategy') in ('pilot_one_lot','pilot_then_targets') and digest(existing)!=digest(c):
                # Only the operator's explicit upgrade button invokes this.
                # Never rewrite the earlier one-lot authorization in place.
                status=read_json(active/'status_redacted.json')
                seed=read_json(active/'seed.json')
                if (status.get('status')!='scheduled_waiting' or (now.date().isoformat()==day and now.time()>=dt.time(9,30))
                    or seed['attempts'] or (active/'private_state.json').exists()):
                    raise TradeError('原一手预约已进入执行或核账，禁止自动扩大；保留原记录')
                workflow.inputs(c)
                if existing.get('strategy')=='pilot_then_targets':
                    workflow.inputs(existing)
                    before_scope={k:v for k,v in config_checked(existing).items() if k!='rebalance_after_pilot'}
                    after_scope={k:v for k,v in c.items() if k!='rebalance_after_pilot'}
                    if digest(before_scope)!=digest(after_scope):raise TradeError('已预约完整流程只允许更新封存目标，不能同时扩大其他范围')
                save(active/'UPGRADE_REQUEST.json',dict(at=now.isoformat(),action='User explicitly selected pilot then rebalance',new_config_sha256=digest(c)))
                (active/'STOP').touch()
                for _ in range(10):
                    if not alive(active):break
                    time.sleep(1)
                else:raise TradeError('原预约尚未退出，禁止创建第二个进程')
                if active_session():raise TradeError('仍有运行中的进程，禁止重复预约')
            else:return dict(action='attached',directory=str(active))
        if not c['pilot_symbol']:raise TradeError('先运行只读核验并锁定单只试单计划')
        profile=APPROVED_PROFILES.get(day) if c['prefer_sealed_plan'] and c['strategy']=='pcf_563030' else None
        if profile and (BASE_DIR/profile['directory']/'approval_seal.json').exists():
            destination=BASE_DIR/profile['directory']
            if now.time()>dt.time.fromisoformat(profile['end']):raise TradeError('当日封存计划的启动窗口已结束；不会自动换用其他规则')
            if any((destination/name).exists() for name in ('launch_attempt.json','launcher_attempt.json','process.json','STOP')):raise TradeError('当日专用计划已有启动记录或STOP，不能重放；请先检查其状态')
            run=APP_DIR/'runs'/day/(now.strftime('%H%M%S')+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
            save(run/'approved_profile.json',dict(day=day,profile=profile,user_clicked_at=now.isoformat()))
            save(run/'status_redacted.json',dict(at=now.isoformat(),status='waiting_open',reason='将按当天已封存的专用计划启动，其规则优先于普通PCF设置'))
            with (run/'stdout.log').open('ab') as out,(run/'stderr.log').open('ab') as err:
                process=subprocess.Popen([str(PYTHON),'-u',str(Path(__file__).resolve()),'--run',str(run)],cwd=str(ROOT),stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            save(run/'process.json',dict(pid=process.pid,created_seconds=psutil.Process(process.pid).create_time(),started_at=now.isoformat(),script='console_runtime.py',execution_date=day))
            return dict(action='approved_plan_waiting',directory=str(run))
        source=latest_session();old=read_json(source/'private_state.json')
        # An unresolved receipt may enter GET-only recovery, never direct replay.
        if len(receipts.unresolved(old))>1:raise TradeError('上次存在多笔结果不明委托，需人工核查')
        if old['day']<day and receipts.unresolved(old):
            from reconcile_previous_receipt import resolve_source
            old,_=resolve_source(source,c,apply=True)
        if old['day']<day and not old.get('last_audit') and not old['attempts']:
            from refresh_stopped_ledger import refresh_source
            old=refresh_source(source,c)
        state=roll_day(old,day)
        if submission_count(state)>1 and not workflow.formal_state(state):raise TradeError('试单账本出现多笔意图，停止核查')
        if c['strategy']=='pilot_then_targets':workflow.inputs(c)
        run=APP_DIR/'runs'/day/(now.strftime('%H%M%S')+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
        save(run/'config.json',c)
        save(run/'source_seal.json',dict(path=str(source/'private_state.json'),sha256=digest(old)))
        save(run/'seed.json',state)
        save(run/'authorization.json',dict(at=now.isoformat(),execution_date=day,action='User pressed Start in the simulation console',
            config_sha256=digest(c),seed_sha256=digest(state),account_mode='competition_simulation_only'))
        scheduled=now.date().isoformat()<day or now.time()<dt.time(9,30)
        save(run/'status_redacted.json',dict(at=now.isoformat(),status='scheduled_waiting' if scheduled else 'starting',
            reason=('本人已预约：先一手验收，再进入已封存的正式调仓' if c['strategy']=='pilot_then_targets' else '本人已预约10月12日09:30，开盘后自动核验并尝试唯一一手') if scheduled else None,execution_date=day))
        with (run/'stdout.log').open('ab') as out,(run/'stderr.log').open('ab') as err:
            process=subprocess.Popen([str(PYTHON),'-u',str(Path(__file__).resolve()),'--run',str(run)],cwd=str(ROOT),stdout=out,stderr=err,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        save(run/'process.json',dict(pid=process.pid,created_seconds=psutil.Process(process.pid).create_time(),started_at=now.isoformat(),
             script='console_runtime.py',execution_date=day,account_mode='competition_simulation_only'))
        return dict(action='pipeline_scheduled' if scheduled and c['strategy']=='pilot_then_targets' else 'scheduled' if scheduled else 'started',directory=str(run))


class Transport(requests.Session):
    def __init__(self,run,c,day):
        super().__init__();self.trust_env=False;self.run=run;self.c=c;self.day=day;self.armed=None;self.writes_suspended=True;self.cancelled=set();self._trace_lock=threading.Lock()
    def request(self,method,url,**kw):
        method=method.upper();ep=url[len(BASE):] if url.startswith(BASE) else ''
        # requests.Session.post adds data=None even when the caller supplies only
        # json. Remove that empty wrapper argument; nonempty data stays blocked.
        if method=='POST' and 'data' in kw and kw['data'] is None:
            kw=dict(kw);kw.pop('data')
        if set(kw)-({'params','timeout','allow_redirects'} if method=='GET' else {'json','timeout','allow_redirects'}):raise TradeError('交易请求出现额外选项，已阻止')
        if kw.get('allow_redirects') is not False:raise TradeError('禁止交易请求重定向')
        if method=='GET':
            params=kw.get('params') or {}
            if ep not in GetOnlySession.KEYS or set(params)!=GetOnlySession.KEYS[ep] or params['accId']!=ACCOUNT:raise TradeError('非固定模拟账户查询')
        elif method=='POST':
            arm=self.armed;self.armed=None;p=kw.get('json') or {};now=now_cn()
            if self.writes_suspended or not arm or p.get('accId')!=ACCOUNT or now.date().isoformat()!=self.day:raise TradeError('交易写入未解锁或已过期')
            kind,o=arm
            if p.get('stkCode')!=o['code'][2:] or p.get('mktCode')!=('1' if o['code'].startswith('SH') else '0'):raise TradeError('写入证券身份不一致')
            if kind=='submit':
                if ep!='AguTrade/SpoOrder' or session_phase(now)!='trading':raise TradeError('禁止窗口外新增委托')
                ledger=read_json(self.run/'private_state.json')
                if workflow.formal_state(ledger):
                    original=ledger['config_snapshot']
                    if digest(workflow.effective(original,ledger))!=digest(self.c):raise TradeError('正式阶段运行参数与已封存范围不一致')
                    workflow.guard_formal(original,ledger,o,now)
                else:pilot.guard_submission(self.c,ledger,o,now)
                if set(p)!={'accId','mktCode','stkCode','price','volume','orderDrt','orderType','stkType','uToken','cToken','uid'} or p['orderType']!=1 or p['stkType']!=1:raise TradeError('试单写入字段异常')
                blocked=set(ledger.get('blocked_receipt_symbols',[]))|{a['order']['code'] for a in ledger['attempts'] if not a.get('oid') and a.get('state')=='UNKNOWN'}
                if o['code'] in blocked:raise TradeError('该证券有未确认或已隔离委托，写入层禁止再次下单')
                if p.get('orderDrt')!=o['side'] or p.get('volume')!=o['quantity'] or dec(p['price'])!=dec(o['price']):raise TradeError('写入委托与持久化意图不一致')
                if o['side']==2 and not self.c['allow_sells']:raise TradeError('当前未开启卖出')
                price_ok=(workflow.directional_price_allowed(o['side'],o['price'],o['reference_price'],self.c['price_guard'])
                    if workflow.formal_state(ledger) else abs(dec(o['price'])/dec(o['reference_price'])-1)<=dec(self.c['price_guard']))
                if o['quantity']*o['price']>self.c['max_order_value'] or not price_ok:raise TradeError('写入层限额或价格保护')
            elif kind=='cancel':
                oid=str(p.get('orderId'))
                ledger=read_json(self.run/'private_state.json')
                known=[a for a in ledger['attempts'] if a.get('oid')==oid and a['order']=={k:v for k,v in o.items() if k!='oid'}]
                if len(known)!=1 or known[0].get('terminal'):raise TradeError('只能撤销本执行器已核验且未结束的原试单')
                if ep!='AguTrade/SpoCancel' or oid!=o['oid'] or oid in self.cancelled or now.time()>=dt.time(15):raise TradeError('撤单身份、次数或窗口异常')
                self.cancelled.add(oid)
            else:raise TradeError('未知写入动作')
        else:raise TradeError('禁止HTTP方法')
        event=dict(at=now_cn().isoformat(),method=method,endpoint=ep)
        try:
            response=super().request(method,url,**kw);event['http_status']=response.status_code
            try:event['business_code']=response.json().get('Code')
            except ValueError:pass
            return response
        except requests.RequestException as error:
            event['error_type']=type(error).__name__
            raise
        finally:
            with self._trace_lock:
                with (self.run/'http_trace_redacted.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')


class Broker(ChoiceBroker):
    def _request(self,path,params=None,payload=None):
        if payload is not None:
            return super()._request(path,params=params,payload=payload)
        transient={'Choice transport/JSON failure; do not retry orders','Choice HTTP response requires reconciliation'}
        for attempt in range(3):
            try:return super()._request(path,params=params)
            except TradeError as error:
                if type(error) is not TradeError or str(error) not in transient:raise
                if attempt==2:raise FillSnapshotPending(f'Choice只读查询暂不可用（{path}，连续3次失败）；暂停交易并等待重新核验') from None
                time.sleep((1,3)[attempt])
    def submit(self,o):
        # Same observed order payload; optional sells use the observed availCount
        # field and exact per-order reconciliation. Live sell fills are not yet tested.
        p=order_payload(self.account,o['code'],o['side'],o['quantity'],o['price'])
        p.update(uToken=os.environ['CHOICE_UTOKEN'],cToken=os.environ['CHOICE_CTOKEN'],uid=os.environ['CHOICE_UID'])
        obj=self._request('AguTrade/SpoOrder',payload=p);oid=obj['Data'].get('orderID')
        if isinstance(oid,bool) or not isinstance(oid,(str,int)) or not str(oid).strip():raise TradeError('提交回执缺少确切委托ID')
        return str(oid)


class Worker:
    def __init__(self,run):
        from cloud_account import verify_deployment
        verify_deployment()
        self.run=Path(run);self.c=config_checked(read_json(self.run/'config.json'));a=read_json(self.run/'authorization.json')
        self.state=read_json(self.run/'seed.json');seal=read_json(self.run/'source_seal.json')
        if digest(self.c)!=a['config_sha256'] or digest(self.state)!=a['seed_sha256'] or digest(read_json(seal['path']))!=seal['sha256']:raise TradeError('启动快照或来源账本已变化')
        self.day=a['execution_date']
        if self.day!=now_cn().date().isoformat() or self.state['day']!=self.day:raise TradeError('过期启动授权')
        self.original_config=copy.deepcopy(self.c)
        self.c=workflow.effective(self.original_config,self.state)
        self.http=Transport(self.run,self.c,self.day);self.public=requests.Session();self.public.trust_env=False;self.auth=ChoiceAuthenticator();self.broker=None
        self.last_submit=0.;self.last_success=None;self.fail_since=None;self.status='starting';self.reason=None
        self.state['config_snapshot']=self.original_config;self.state.setdefault('deferred',{});self.flush()
    def flush(self):save(self.run/'private_state.json',self.state)
    def publish(self,status,reason=None):
        self.status=status;self.reason=reason;a=self.state.get('last_audit',{})
        save(self.run/'status_redacted.json',dict(at=now_cn().isoformat(),status=status,reason=reason,execution_date=self.day,
            submitted=submission_count(self.state),filled_shares=sum(x.get('filled',0) for x in self.state['attempts']),
            stock_fraction=a.get('stock_fraction'),frozen_cny=a.get('frozen'),pending=a.get('pending'),
            remaining=differences(self.state.get('targets',{}),a) if a else None,deferred=self.state['deferred'],
            unresolved_intents=sum(not x.get('oid') for x in self.state['attempts']),excluded_cash_cny=self.c['excluded_cash_cny'],
            trading_writes_suspended=self.http.writes_suspended,position_compliance=self.state.get('position_compliance'),
            cash_variance=self.state.get('cash_variance'),last_snapshot_mismatch=self.state.get('last_snapshot_mismatch'),
            ack_recovery=self.state.get('ack_recovery'),automatic_recovery=self.state.get('automatic_recovery'),
            verified_at=a.get('checked_at'),cash=a.get('cash'),frozen=a.get('frozen'),remaining_count=len(differences(self.state.get('targets',{}),a)) if a else None))
    def raw(self):
        if self.broker is None:raise SessionExpired()
        return account_snapshot(self.broker,self.state)
    def audit(self):
        isolated=isolated_attempts(self.state) if self.c.get('isolate_unknown_orders') else []
        recovering=len(receipts.unresolved(self.state))>len(isolated)
        restore_writes=recovering and not self.http.writes_suspended
        if recovering:self.recover_submission()
        try:
            raw=self.raw()
            if isolated:
                known=set(self.state.get('historical',{}))|set(self.state.get('prior',{}))|{x['oid'] for x in self.state['attempts'] if x.get('oid')}
                if any(str(x['orderId']) not in known and str(x['orderDate']).replace('-','')>=self.day.replace('-','') for x in raw['orders']):
                    restore_writes=not self.http.writes_suspended
                    self.recover_submission();raw=self.raw()
            a=reconcile(raw,self.state,self.c['excluded_cash_cny'],self.c['cash_drift_tolerance_cny'],self.c.get('isolate_unknown_orders',False))
            if workflow.formal_state(self.state):workflow.consistent_position_marks(a)
        except TradeError:
            self._cash_candidate=None
            raise
        if a['cash_drift_requires_confirmation']:
            candidate=dec(a['cash_drift_cny']);previous=getattr(self,'_cash_candidate',None)
            if previous is None or abs(previous-candidate)>Decimal('.03'):
                self._cash_candidate=candidate
                raise FillSnapshotPending(f'小额现金差额{candidate:+.2f}元；持仓和订单核对通过，等待第二次一致快照确认')
            event=dict(at=now_cn().isoformat(),day=self.day,amount_cny=str(candidate),tolerance_cny=a['cash_drift_tolerance_cny'],
                source='two_consistent_snapshots_no_pending_or_frozen',baseline_cash=self.state['baseline']['cash'])
            self.state['cash_variance']=event
            self.state.setdefault('cash_variance_events',[]).append(event.copy())
            a['cash_drift_requires_confirmation']=False
        self._cash_candidate=None
        for attempt in self.state['attempts']:
            if not attempt.get('oid'):continue
            attempt.update(a['states'][attempt['oid']])
            if attempt['terminal'] and not attempt.get('terminal_at'):attempt['terminal_at']=now_cn().isoformat()
        a['checked_at']=now_cn().isoformat();self.state['last_audit']=a
        calendar=read_json(self.c['calendar_path']) if floor.active(self.c,self.day) else []
        self.state['position_compliance']=floor.observe(self.state.get('position_compliance'),a,self.day,self.c,calendar)
        self.last_success=time.monotonic();self.fail_since=None;self.flush()
        if restore_writes:self.http.writes_suspended=False
        return a
    def receipt_trace(self):
        trace=[];directory=self.run;seen=set()
        for _ in range(20):
            if directory in seen or not directory.resolve().is_relative_to(BASE_DIR.resolve()):break
            seen.add(directory);path=directory/'http_trace_redacted.jsonl'
            if path.exists():
                trace.extend(json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line)
            source=directory/'source_seal.json'
            if not source.exists():break
            directory=Path(read_json(source)['path']).parent
        return sorted(trace,key=lambda event:event['at'])
    def recover_submission(self):
        """Never resubmit. Require one exact, new server ID and two full audits."""
        self.http.writes_suspended=True
        missing=receipts.unresolved(self.state)
        if len(missing)!=1:raise TradeError('多笔委托回执不明，需人工核查')
        intent=missing[0];order=intent['order'];trace=self.receipt_trace() if not intent.get('submit_failed_at') else []
        receipts.submission_window(intent,trace)
        started=time.monotonic();until=started+90;previous=None;absent=None;last_reason='等待服务器委托查询'
        while True:
            if self.check_stop():raise TradeError('用户已停止回执核验')
            progress=dict(phase='querying',at=now_cn().isoformat(),code=order['code'],quantity=order['quantity'],price=order['price'],
                          submitted_at=intent['submitted_at'],reason=last_reason)
            self.state['ack_recovery']=progress;self.flush();self.publish('recovering_submission',last_reason)
            try:
                raw=self.raw();proposed,audit,oid=receipts.candidate(raw,self.state,self.c,trace)
                absent=None
                if previous is not None and previous['oid']==oid:
                    # Also disallow a fill-count regression from the first read.
                    reconcile(raw,previous['state'],self.c['excluded_cash_cny'],self.c['cash_drift_tolerance_cny'])
                    save(self.run/'receipt_recovery_confirmed.json',raw)
                    target=next(a for a in proposed['attempts'] if a.get('oid')==oid)
                    target.update(receipt_recovered_at=now_cn().isoformat(),receipt_recovery_source='two_exact_server_snapshots')
                    evidence=dict(at=now_cn().isoformat(),code=order['code'],oid=oid,
                                  first_snapshot_sha256=previous['sha256'],second_snapshot_sha256=digest(raw),orders_resent=0)
                    proposed.setdefault('receipt_recovery_events',[]).append(evidence)
                    proposed['ack_recovery']=dict(progress,phase='recovered',reason='两轮完整账户核验通过，已接回原委托；未重发')
                    self.state=proposed;self.flush();return
                save(self.run/'receipt_recovery_candidate.json',raw)
                previous=dict(oid=oid,state=proposed,sha256=digest(raw))
                last_reason=f'找到 {order["code"]} 唯一匹配的服务器委托，等待第二轮完整账户核验；不会重发'
            except receipts.ReceiptAbsent as error:
                previous=None;last_reason=str(error)
                if self.c.get('isolate_unknown_orders'):
                    try:known_audit,stable=receipts.isolation_candidate(raw,self.state,self.c,now_cn(),trace)
                    except FillSnapshotPending as waiting:
                        absent=None;last_reason=str(waiting)
                    else:
                        if absent is not None and absent['stable']==stable:
                            proposed=receipts.isolate(self.state,known_audit,absent['sha256'],digest(raw),now_cn().isoformat())
                            reconcile(raw,proposed,self.c['excluded_cash_cny'],self.c['cash_drift_tolerance_cny'],True)
                            save(self.run/'isolation_confirmed_snapshot.json',raw)
                            proposed['ack_recovery']=dict(progress,phase='isolated',reserve_cny=intent['reserve'],
                                reason=f'两轮账户核对一致；{order["code"]}及{intent["reserve"]}元预算单独保留，今日跳过该证券，其他股票可继续')
                            self.state=proposed;self.flush();return
                        save(self.run/'isolation_first_snapshot.json',raw)
                        absent=dict(stable=stable,sha256=digest(raw))
                        last_reason=f'{order["code"]}未出现在当日或历史订单中；其余账户数据一致，等待第二轮确认后保留资金并跳过该证券'
            except FillSnapshotPending as error:
                previous=None;absent=None;last_reason=str(error)
            except TradeError as error:
                self.state['ack_recovery']=dict(progress,phase='blocked',reason=str(error));self.flush();raise
            if time.monotonic()>=until:
                reason=f'回执自动核查90秒仍未确认：{last_reason}；原委托保留，不自动重发，需确认服务器是否受理'
                self.state['ack_recovery']=dict(progress,phase='unresolved',reason=reason);self.flush()
                raise TradeError(reason)
            time.sleep(5)
    def check_stop(self):return (self.run/'STOP').exists() or now_cn().date().isoformat()!=self.day
    def bind(self,tokens):
        install_tokens(tokens,self.http,ACCOUNT);tokens.clear()
        self.broker=Broker(dict(initial_buy_only=True,validated_submission_with_authorized_test=True,validated_cancel_with_authorized_test=True,contract_source='teammate_authorized_20260920_mapping',new_cloud_order_fill_verified=False),True,self.http)
    def two_audits(self,allow_stop=False):
        consistent=0;started=time.monotonic();until=started+90;last_reason=None;proofs=[]
        while consistent<2:
            if not allow_stop and self.check_stop():raise TradeError('用户已停止')
            try:proofs.append(copy.deepcopy(self.audit()));consistent+=1
            except FillSnapshotPending as e:
                last_reason=str(e);consistent=0;proofs=[]
                self.state['last_snapshot_mismatch']=dict(at=now_cn().isoformat(),reason=last_reason,continuous_failure_seconds=time.monotonic()-started)
                self.flush();self.publish('reconciling',last_reason)
            if time.monotonic()>until:raise TradeError('连续90秒账户核对未通过'+('：'+last_reason if last_reason else ''))
            if consistent<2:time.sleep(5)
        return proofs
    def login(self,initial=False):
        self.http.writes_suspended=True
        recovery=self.state.setdefault('auth_recovery',dict(attempts=0,phase='ready',events=[]))
        if initial:
            try:self.bind(self.auth.current_tokens());self.two_audits();self.http.writes_suspended=False;return
            except (SessionExpired,HumanLoginRequired):pass
            # A user-requested new process needs its own official SSO session.
            # Successful startup exchange is not a failed automatic recovery;
            # old successful restarts must not exhaust today's recovery budget.
            try:self.bind(self.auth.login());self.two_audits();self.http.writes_suspended=False;return
            except (SessionExpired,HumanLoginRequired):pass
        while recovery.get('attempts',0)<3:
            n=recovery.get('attempts',0);wait=0 if initial and n==0 else (300,600,900)[n]
            until=time.monotonic()+wait
            self.publish('waiting_login','自动恢复登录等待中；也可在Choice正常登录后点击“已登录，重试”')
            while time.monotonic()<until:
                if self.check_stop() or now_cn().time()>=dt.time(15):raise TradeError('登录恢复已停止或超时')
                if (self.run/'LOGIN_READY').exists():(self.run/'LOGIN_READY').unlink();break
                time.sleep(1)
            recovery['attempts']=n+1;self.flush()
            try:
                self.bind(self.auth.login());self.two_audits();self.http.writes_suspended=False
                recovery.update(phase='ready');self.flush();return
            except (SessionExpired,HumanLoginRequired):
                self.publish('login_required','请在Choice正常登录并打开金融实验室，再点击“已登录，重试”；验证码需你本人完成')
                deadline=time.monotonic()+720
                while time.monotonic()<deadline:
                    if self.check_stop() or now_cn().time()>=dt.time(15):raise TradeError('登录恢复窗口结束')
                    if (self.run/'LOGIN_READY').exists():(self.run/'LOGIN_READY').unlink();break
                    time.sleep(1)
                else:raise TradeError('未在12分钟内完成Choice登录')
        raise TradeError('当天登录恢复次数已用尽，请检查Choice会话')
    def reference_quote(self,code):
        """Retry public reference reads only; validate the fresh response normally."""
        for attempt in range(3):
            if self.check_stop():raise TradeError('用户已停止')
            try:return quote(code,self.public)
            except (requests.RequestException,TradeError) as error:
                if isinstance(error,TradeError) and str(error)!='Quote HTTP failure':raise
                self.state.setdefault('read_retry_events',[]).append(dict(at=now_cn().isoformat(),source='public_reference_quote',code=code,attempt=attempt+1,error_type=type(error).__name__))
                self.flush()
                if attempt==2:raise TradeError(f'公共参考行情查询连续3次失败：{code}；未使用旧行情，请稍后重试') from None
                self.publish('preparing',f'参考行情 {code} 暂时超时或不可用，正在重试 {attempt+2}/3')
                time.sleep((1,3)[attempt])
    def prepare(self):
        _,signal=days_for(self.c,self.day)
        if self.c['strategy'] in ('pilot_one_lot','pilot_then_targets'):
            pilot.assert_membership_current(self.c,self.public)
            a=self.audit()
            if submission_count(self.state):raise TradeError('已有试单意图，禁止重新生成或提交')
            q=self.reference_quote(self.c['pilot_symbol'])
            plan=pilot.make_plan(self.c,BASE_DIR,a,q,now_cn(),executable=True)
            self.state['pilot_plan']=plan;self.state['targets']=plan['targets']
            self.state['quotes']={q['code']:q};self.state['plan_config_sha256']=digest(self.c)
            self.flush();save(self.run/'plan.json',plan);return
        seed=read_json(self.run/'seed.json');prior_cfg=seed.get('config_snapshot')
        isolation_only_change=False
        if prior_cfg and seed.get('plan_config_sha256')==digest(prior_cfg):
            old_plan_config=config_checked(prior_cfg);new_plan_config=copy.deepcopy(self.c)
            old_plan_config.pop('isolate_unknown_orders');new_plan_config.pop('isolate_unknown_orders')
            isolation_only_change=old_plan_config==new_plan_config
        keys=('target_fraction','capital_limit','max_security_value','max_order_value')
        legacy_same=(self.c['strategy']=='pcf_563030' and all(seed.get(k)==self.c[k] for k in keys) and self.c['price_guard']==.03)
        if seed.get('targets') and (seed.get('plan_config_sha256')==digest(self.c) or isolation_only_change or (not prior_cfg and legacy_same)):
            self.state['targets']=seed['targets'];self.state['plan_origin']='inherited_same_day_frozen_targets'
            self.state['plan_config_sha256']=digest(self.c)
            self.state.setdefault('floor_reference_targets',copy.deepcopy(self.state['targets']));self.flush()
            save(self.run/'plan.json',dict(day=self.day,targets=self.state['targets'],origin=self.state['plan_origin']));return
        self.publish('preparing','正在生成今日目标持仓；只读查询，不下单')
        try:a=self.audit()
        except FillSnapshotPending:
            self.two_audits();a=self.state['last_audit']
        strategy=self.c['strategy'];obj=None;record=None
        if strategy=='pcf_563030':
            cached=BASE_DIR/'daily_public_pcf/state/archive'/signal
            if (cached/'REVISION_BLOCK.json').exists():raise TradeError('前日PCF有修订，不能自动使用')
            if (cached/'pcf.json').exists():record=read_json(cached/'pcf.json')
            else:record=download_pcf('563030',signal,str(self.run/'pcf_download.json'))
            codes={normalize_code(str(r['C_STOCKCODE'])) for r in record['stocklist'] if float(r['L_NUMBER'])>0}
            save(self.run/'signal_pcf.json',record)
        else:
            if strategy=='local_script':
                script=Path(self.c['external_script']).resolve()
                if not script.is_file() or script.suffix.lower()!='.py':raise TradeError('策略脚本必须为本地Python文件')
                save(self.run/'strategy_input.json',dict(execution_date=self.day,signal_date=signal,nav=a['total_assets'],positions=a['positions'],cash=a['cash'],limits=self.c))
                output=self.run/'strategy_output.json'
                with (self.run/'strategy.log').open('wb') as log:
                    subprocess.run([str(PYTHON),str(script),'--execution-date',self.day,'--signal-date',signal,'--account-snapshot',str(self.run/'strategy_input.json'),'--output',str(output)],
                        cwd=str(script.parent),check=True,timeout=300,stdout=log,stderr=log,
                        env={k:v for k,v in os.environ.items() if not k.startswith('CHOICE_')},
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                obj=read_json(output)
            else:
                path=self.c['external_file'].replace('{date}',self.day).replace('{yyyymmdd}',self.day.replace('-',''))
                obj=read_json(path)
            codes={normalize_code(str(r['code'])) for r in obj.get('targets',[])}
            save(self.run/'signal_targets.json',obj)
        codes|=set(a['positions']);quotes={}
        for index,code in enumerate(sorted(codes),1):
            if self.check_stop():raise TradeError('用户已停止')
            if index==1 or index%10==0:self.publish('preparing',f'正在读取今日参考行情 {index}/{len(codes)}')
            q=self.reference_quote(code);stamp=dt.datetime.fromisoformat(q['timestamp'])
            if stamp.date().isoformat()!=self.day or stamp>now_cn() or q['previous_close']<=0:raise TradeError('参考行情不是今日数据：'+code)
            quotes[code]=q
        save(self.run/'reference_quotes.json',quotes)
        if record:
            import pandas as pd
            members=pd.read_parquet(self.c['membership_path']);universe=set(members.symbol.map(normalize_code))
            targets=pcf_targets(record,self.day,signal,a['total_assets'],self.c,quotes,universe,a['positions'])
        else:targets=parse_targets(obj,self.day,signal,a['total_assets'],self.c,quotes)
        for code in a['positions']:targets.setdefault(code,dict(code=code,quantity=0,reference_price=quotes[code]['previous_close']))
        floor_refs=copy.deepcopy(targets)
        if floor.active(self.c,self.day) and not self.c['allow_sells']:
            # Maintain the floor without authorizing sales of legacy holdings.
            # Zero-weight/out-of-universe names remain ineligible for extra buys.
            for code,held in a['positions'].items():targets[code]['quantity']=max(held,targets[code]['quantity'])
        reductions={c:o['quantity']-a['positions'].get(c,0) for c,o in targets.items() if o['quantity']<a['positions'].get(c,0)}
        save(self.run/'plan.json',dict(day=self.day,signal_date=signal,strategy=strategy,targets=targets,requires_sells=bool(reductions),created_at=now_cn().isoformat()))
        if reductions and not self.c['allow_sells']:raise TradeError('今日目标需要减仓；请查看计划，并在设置中明确开启“允许卖出调仓”后启动')
        self.state['targets']=targets;self.state['floor_reference_targets']=floor_refs
        self.state['plan_config_sha256']=digest(self.c);self.flush()

    def transition_to_formal(self,audits):
        original=self.original_config
        proof=workflow.acceptance(original,self.state,audits)
        self.http.writes_suspended=True
        self.publish('pilot_accepted_preparing_targets','100股试单及两轮账户核验通过，正在只读校验正式调仓目标')
        workflow.verify_current_sources(original,self.public)
        obj,m=workflow.inputs(original);a=self.state['last_audit'];quotes={}
        codes={normalize_code(x['code']) for x in obj['targets']}|set(a['positions'])
        for i,code in enumerate(sorted(codes),1):
            if self.check_stop():raise TradeError('正式接入准备已停止')
            if i==1 or i%10==0:self.publish('pilot_accepted_preparing_targets',f'核验正式参考行情 {i}/{len(codes)}')
            q=self.reference_quote(code)
            validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
            if q['code']!=code or dt.datetime.fromisoformat(q['timestamp']).astimezone(CN).date().isoformat()!=self.day:raise TradeError('正式参考行情日期或身份异常')
            quotes[code]=q
        candidate=copy.deepcopy(original);candidate.update(strategy='target_file',allow_sells=True,target_fraction=.95)
        valuation_time=now_cn()
        targets=parse_targets(obj,self.day,workflow.SIGNAL,float(dec(a['total_assets'])),candidate,quotes,valuation_time=valuation_time)
        for code in a['positions']:targets.setdefault(code,dict(code=code,quantity=0,reference_price=quotes[code]['previous_close']))
        nav=dec(a['total_assets']);value=sum(dec(quotes[code]['last'])*x['quantity'] for code,x in targets.items())
        c500=set(m['csi500']);member=sum(dec(quotes[code]['last'])*x['quantity'] for code,x in targets.items() if code in c500)
        if value<nav*dec('.90') or member<nav*dec('.80'):raise TradeError('正式整手目标未达到90%股票或80%中证500资产比例')
        # The preparation quotes took time; another two audits must still prove
        # the accepted pilot and unchanged inventory before any formal order.
        fresh=self.two_audits();proof=workflow.acceptance(original,self.state,fresh)
        # Recheck live values against the post-preparation account NAV. Preserve
        # prior-close references for the order price guard, and every sealed share.
        nav=dec(self.state['last_audit']['total_assets']);valuation_time=now_cn()
        targets=parse_targets(obj,self.day,workflow.SIGNAL,float(nav),candidate,quotes,valuation_time=valuation_time)
        for code in self.state['last_audit']['positions']:targets.setdefault(code,dict(code=code,quantity=0,reference_price=quotes[code]['previous_close']))
        value=sum(dec(quotes[code]['last'])*x['quantity'] for code,x in targets.items())
        member=sum(dec(quotes[code]['last'])*x['quantity'] for code,x in targets.items() if code in c500)
        if value<nav*dec('.90') or member<nav*dec('.80'):raise TradeError('正式盘中目标未达到90%股票或80%中证500资产比例')
        self.state['targets']=targets;self.state['floor_reference_targets']=copy.deepcopy(targets)
        self.state['quotes']=quotes;self.state['deferred']={}
        self.state['rebalance_workflow']=dict(proof,phase='formal',targets_sha256=digest(targets),strategy_layer=obj['strategy_layer'],transition_at=now_cn().isoformat(),valuation=dict(method='fresh_live_last',at=valuation_time.isoformat(),stock_value_cny=str(value),csi500_value_cny=str(member),nav_cny=str(nav),quotes_sha256=digest(quotes)))
        self.state['plan_config_sha256']=digest(original)
        self.c=workflow.effective(original,self.state);self.http.c=self.c
        self.flush();save(self.run/'rebalance_transition.json',self.state['rebalance_workflow'])
        save(self.run/'formal_plan.json',dict(day=self.day,signal_date=workflow.SIGNAL,strategy_layer=obj['strategy_layer'],targets=targets,source_sha256=workflow.checked(original)['target_sha256']))
        self.http.writes_suspended=False
        return 'formal_rebalancing'
    def cancel(self,force=False):
        for a in self.state['attempts']:
            if not a.get('oid') or a.get('terminal'):continue
            if a.get('cancel_requested_at'):
                if (now_cn()-dt.datetime.fromisoformat(a['cancel_requested_at'])).total_seconds()>60:raise TradeError('撤单超过60秒未确认，禁止重报')
                continue
            if not force and (now_cn()-dt.datetime.fromisoformat(a['submitted_at'])).total_seconds()<60:continue
            a['cancel_requested_at']=now_cn().isoformat();self.flush();self.http.armed=('cancel',dict(a['order'],oid=a['oid']))
            try:self.broker.cancel(a['oid'],a['order'])
            except SessionExpired as error:
                if not error.request_was_write:a['cancel_not_sent_auth_at']=a.pop('cancel_requested_at');self.flush()
                raise
            finally:self.http.armed=None
    def step(self):
        audit=self.audit();phase=session_phase(now_cn());self.cancel(force=phase!='trading' or self.check_stop())
        if self.check_stop():return 'stopped'
        if phase!='trading':return 'session_paused' if phase=='lunch' else phase
        if self.c['strategy'] in ('pilot_one_lot','pilot_then_targets') and submission_count(self.state):
            if not audit['pending'] and not dec(audit['frozen']):
                audits=self.two_audits()
                if self.c['strategy']=='pilot_then_targets':return self.transition_to_formal(audits)
                return 'completed_pilot'
            return 'watching'
        formal=workflow.formal_state(self.state)
        if formal and (audit['pending'] or dec(audit['frozen'])):return 'watching'
        gap=differences(self.state['targets'],audit)
        if not gap and not audit['pending'] and not dec(audit['frozen']):
            self.two_audits()
            if not floor.active(self.c,self.state['day']):return 'completed_targets'
            audit=self.state['last_audit']
            if not self.state['position_compliance']['met']:
                return 'position_topup_planned' if self.plan_position_topup(audit) else 'position_floor_unmet'
            return 'maintaining_position_floor'
        if formal and submission_count(self.state)>=workflow.MAX_ORDERS+1:raise TradeError('达到正式阶段拆单总数上限，停止并保留原记录')
        pending_codes={o['code'] for o in audit['pending'].values()}
        blocked_codes=set(self.state.get('blocked_receipt_symbols',[]))|set(audit.get('isolated_codes',[]))
        pending_sells=sum(dec(o['price'])*o['quantity'] for o in audit['pending'].values() if o['side']==2)
        floor_room=dec(audit['market_value'])-dec(self.c['target_fraction'])*dec(audit['total_assets'])-pending_sells
        bounds=workflow.valuation_bounds(audit) if formal else None
        buy_first=(bounds['lower_stock']-bounds['upper_nav']*dec('.90')<workflow.ORDER_CAP) if formal else floor.active(self.c,self.state['day']) and floor_room<0
        for code,delta in sorted(gap.items(),key=lambda item:((item[1]<0 if buy_first else item[1]>0),item[0])):
            if code in blocked_codes:self.state['deferred'][code]='回执不明，保留资金并跳过该证券';continue
            if code in pending_codes:continue
            attempts=[a for a in self.state['attempts'] if a['order']['code']==code]
            if formal:
                formal_attempts=[x for x in attempts if x.get('phase')=='formal']
                if not workflow.prior_slices_allowed(formal_attempts):
                    self.state['deferred'][code]='原单部分成交、拒单、未知或已达零成交撤单两次重试上限';continue
                attempts=formal_attempts
            if len(attempts)>=self.c['max_attempts_per_code']:self.state['deferred'][code]='达到当日试单笔数上限';continue
            if attempts and attempts[-1]['state']=='9':self.state['deferred'][code]='拒单需核查';continue
            if not formal and attempts and (now_cn()-dt.datetime.fromisoformat(attempts[-1]['terminal_at'])).total_seconds()<60:continue
            side=1 if delta>0 else 2
            if side==2 and not self.c['allow_sells']:self.state['deferred'][code]='未开启卖出';continue
            try:q=quote(code,self.public);validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
            except Exception:self.state['deferred'][code]='行情不可用、停牌或ST';continue
            if q['code']!=code:raise TradeError('行情证券身份异常')
            ref=self.state['targets'][code]['reference_price'];price=stable_price_at_ask(q['ask'] if side==1 else q['bid'],0 if formal else 1 if side==1 and code in ('SZ000415','SZ002152') else 0)
            price_ok=(price is not None and (workflow.directional_price_allowed(side,price,ref,self.c['price_guard'])
                if formal else abs(dec(price)/dec(ref)-1)<=dec(self.c['price_guard'])))
            if not price_ok:self.state['deferred'][code]='不利方向超过3%价格保护或价格精度' if formal else '价格保护或价格精度';continue
            quantity=abs(delta)
            if side==2:quantity=min(quantity,audit['sellable'].get(code,0))
            max_quantity=workflow.max_order_quantity(price) if formal else int(self.c['max_order_value']/price//100)*100
            quantity=min(quantity,max_quantity)
            if side==1 and (quantity<(200 if code.startswith('SH688') else 100) or quantity%100):self.state['deferred'][code]='不足买入整手';continue
            if side==2 and quantity<=0:self.state['deferred'][code]='T+1或可卖数量不足';continue
            if side==2 and quantity!=audit['sellable'].get(code,0):quantity=quantity//100*100
            if formal and code.startswith('SH688') and not (side==2 and self.state['targets'][code]['quantity']==0):
                # A 100-share nonliquidation remainder cannot be submitted.
                # Use a smaller valid slice when possible; otherwise defer
                # the whole remaining gap instead of creating a stranded lot.
                remaining=abs(delta)-quantity
                if remaining==100:quantity-=100
                if quantity<200:self.state['deferred'][code]='5万元内无法合法拆分科创板剩余目标';continue
            if side==2 and floor.active(self.c,self.state['day']):
                # A complete target reduction may temporarily cross the floor
                # intraday. Never slice it into tiny orders to fit headroom.
                # Before another reduction, restore the floor with confirmed
                # buys. From 14:35 onward, defer the WHOLE reduction if it would
                # cross the floor, leaving time for the closing requirement.
                if floor_room<0:
                    self.state['deferred'][code]='调仓暂低于90%，优先完成补买后再执行整笔卖出';continue
                if now_cn().time()>=dt.time(14,35) and max(dec(price),dec(q['last']))*quantity>floor_room:
                    self.state['deferred'][code]='临近收盘，整笔卖出会低于90%；等待补买，不拆成小单';continue
            if side==2 and code.startswith('SH688') and quantity<200 and not (quantity==audit['positions'].get(code,0) and self.state['targets'][code]['quantity']==0):
                self.state['deferred'][code]='科创板不足200股的非清仓减仓暂不执行';continue
            if quantity<=0:continue
            if formal and side==2:
                _,members=workflow.inputs(self.original_config)
                if not workflow.sell_room(self.original_config,audit,members,code,quantity,max(price,q['last'])):
                    self.state['deferred'][code]='卖出将低于90%股票或80%中证500比例，等待已确认补买';continue
            need=reserve(dec(price)*quantity)
            if self.c['strategy'] in ('pilot_one_lot','pilot_then_targets'):
                if code!=self.c['pilot_symbol'] or side!=1 or quantity!=100 or submission_count(self.state):raise TradeError('试单数量或提交次数异常')
                if need>pilot.CAP:self.state['deferred'][code]='含费用预留超过1万元';continue
            buy_reserves=self.used_buy_reserves(self.state['attempts'])
            sell_credit=self.verified_sell_credit(self.state['attempts'],audit)
            pending_buy=sum(dec(o['price'])*o['quantity'] for o in audit['pending'].values() if o['side']==1)
            security_mark=max(q['last'],price)*(audit['positions'].get(code,0)+quantity)
            security_reserved=dec(self.state.get('baseline_security_reserve',{}).get(code,0))+self.used_buy_reserves(attempts)+need
            uncertain=dec(audit.get('uncertain_buy_reserve',0))
            if side==1 and (need>dec(audit['cash']) or dec(audit['paid'])+dec(audit['frozen'])+uncertain+need>dec(self.c['capital_limit'])
                    or dec(self.state['carried_reserve'])+buy_reserves-sell_credit+need>dec(self.c['capital_limit'])
                    or dec(audit['market_value'])+pending_buy+uncertain+need>dec(self.c['capital_limit'])
                    or security_mark>self.c['max_security_value'] or security_reserved>dec(self.c['max_security_value'])):
                self.state['deferred'][code]='总资金或单股限额';continue
            if formal and side==1 and bounds['upper_stock']+need>min(dec(self.c['capital_limit']),bounds['lower_nav']*dec('.95')):
                self.state['deferred'][code]='正式买入超过95%仓位上限';continue
            if time.monotonic()-self.last_submit<self.c['spacing_seconds']:return 'watching'
            if self.check_stop() or session_phase(now_cn())!='trading':return 'session_paused'
            if formal and not workflow.zero_cancel_proofs_valid(self.state,attempts,now_cn()):
                audits=self.two_audits()
                proofs=self.state.setdefault('zero_cancel_retry_proofs',{})
                for prior in attempts:
                    if workflow.zero_cancelled(prior):
                        proofs[prior['oid']]=workflow.make_zero_cancel_proof(prior,audits,now_cn())
                self.flush()
                # Recompute the target gap, risk headroom and fresh quote on the
                # next step; never send the plan made before these two audits.
                return 'watching'
            validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
            order=dict(code=code,side=side,quantity=quantity,price=price,reference_price=ref)
            order_payload(ACCOUNT,code,side,quantity,price)
            self.state['quotes'][code]=q
            a=dict(order=order,reserve=str(need),state='SENDING',filled=0,submitted_at=now_cn().isoformat())
            if formal:a['phase']='formal'
            self.state['attempts'].append(a);self.state['deferred'].pop(code,None);self.flush();self.http.armed=('submit',order)
            try:a.update(oid=self.broker.submit(order),state='ACK')
            except Exception as error:
                a.update(state='UNKNOWN',submit_failed_at=now_cn().isoformat(),submit_error_type=type(error).__name__)
                self.flush()
                raise receipts.SubmissionReceiptPending(f'{code} 提交回执未收到，转为只读核查，禁止直接重发') from None
            finally:self.http.armed=None
            self.last_submit=time.monotonic();self.flush();return 'watching'
        self.flush()
        if floor.active(self.c,self.state['day']) and not audit['pending'] and not self.state.get('position_compliance',{}).get('met',True):
            return 'position_topup_planned' if self.plan_position_topup(audit) else 'position_floor_unmet'
        return 'watching'

    def used_buy_reserves(self,attempts):
        total=Decimal(0)
        for attempt in attempts:
            if attempt['order'].get('side',1)!=1:continue
            amount=dec(attempt['reserve'])
            if (floor.active(self.c,self.state['day']) and attempt.get('oid') and attempt.get('terminal')
                    and attempt.get('state') in TERMINAL):
                filled=attempt.get('filled',0)
                amount=reserve(dec(attempt['order']['price'])*filled) if filled else Decimal(0)
            total+=amount
        return total

    def verified_sell_credit(self,attempts,audit):
        # Reconciliation proves sell fills executed at or above the sell limit.
        # Subtract ALL session fees for a conservative lower bound on proceeds;
        # never credit submitted/unfilled quantity or assumed future proceeds.
        gross=Decimal(0)
        for attempt in attempts:
            if attempt['order'].get('side',1)!=2 or not attempt.get('oid'):continue
            verified=audit['states'].get(attempt['oid'])
            if verified:gross+=dec(attempt['order']['price'])*verified['filled']
        return max(Decimal(0),gross-dec(audit.get('fees',0)))

    def plan_position_topup(self,audit):
        """Extend only existing, already-filled strategy names; normal execution rechecks every limit."""
        if (not floor.active(self.c,self.state['day']) or self.check_stop() or session_phase(now_cn())!='trading'
                or audit['pending'] or dec(audit['frozen']) or self.state['position_compliance']['met']):return False
        refs=self.state.get('floor_reference_targets',self.state['targets'])
        blocked=set(self.state.get('blocked_receipt_symbols',[]))|set(audit.get('isolated_codes',[]))
        candidates=[c for c,t in refs.items() if c not in blocked and t['quantity']>0 and audit['positions'].get(c,0)>=self.state['targets'][c]['quantity']]
        candidates.sort(key=lambda c:(audit['positions'].get(c,0)/refs[c]['quantity'],c))
        gap=floor.plan_fraction(self.c)*dec(audit['total_assets'])-dec(audit['market_value'])
        reserves=self.used_buy_reserves(self.state['attempts'])
        sell_credit=self.verified_sell_credit(self.state['attempts'],audit)
        uncertain=dec(audit.get('uncertain_buy_reserve',0))
        account_room=min(dec(audit['cash']),dec(self.c['capital_limit'])-dec(audit['paid'])-dec(audit['frozen'])-uncertain,
            dec(self.c['capital_limit'])-dec(self.state['carried_reserve'])-reserves+sell_credit,
            dec(self.c['capital_limit'])-dec(audit['market_value'])-uncertain)
        if account_room<=10 or gap<=0:return False
        for code in candidates:
            attempts=[a for a in self.state['attempts'] if a['order']['code']==code]
            if len(attempts)>=3 or any(not a.get('oid') or not a.get('terminal') for a in attempts):continue
            if attempts and (attempts[-1]['state']=='9' or (now_cn()-dt.datetime.fromisoformat(attempts[-1]['terminal_at'])).total_seconds()<60):continue
            reference=self.state['targets'][code]['reference_price']
            try:
                q=quote(code,self.public);validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
                if q['code']!=code:raise TradeError('补足仓位行情身份异常')
                price=stable_price_at_ask(q['ask'],0)
                if price is None or abs(dec(price)/dec(reference)-1)>dec(self.c['price_guard']):continue
                security_room=dec(self.c['max_security_value'])-dec(self.state.get('baseline_security_reserve',{}).get(code,0))-self.used_buy_reserves(attempts)
                quantity=floor.topup_quantity(code,price,q['last'],audit['positions'].get(code,0),gap,min(account_room,security_room),self.c)
            except (TradeError,ValueError,KeyError):continue
            if not quantity:continue
            old=self.state['targets'][code]['quantity'];new=audit['positions'].get(code,0)+quantity
            self.state['targets'][code]['quantity']=new
            self.state.setdefault('position_topups',[]).append(dict(at=now_cn().isoformat(),code=code,old_target=old,new_target=new,
                actual_fraction=audit['stock_fraction'],minimum_fraction=self.c['target_fraction'],reason='Actual position below minimum; same strategy universe and unchanged order protections'))
            self.state['deferred'].pop(code,None);self.flush()
            save(self.run/'position_topups.json',self.state['position_topups'])
            return True
        return False
    def transient_pause(self,error,*,bootstrap=False):
        """Stay in the same authorized run; never clear or resend an intent."""
        self.http.writes_suspended=True
        if isinstance(error,MemoryError):gc.collect()
        old=self.state.get('automatic_recovery',{})
        count=int(old.get('count',0))+1
        self.state['automatic_recovery']=dict(phase='readonly_backoff',at=now_cn().isoformat(),
            count=count,error_type=type(error).__name__,reason=str(error) if not isinstance(error,MemoryError) else '内存暂不可用，保留全部账本并释放临时资源',
            retry_seconds=min(30,5*count),same_authorized_run=True,orders_resent=0)
        self._resume_after_snapshot=not bootstrap
        self.flush();self.publish('recovering_readonly',self.state['automatic_recovery']['reason'])
        for _ in range(min(30,5*count)):
            if self.check_stop() or now_cn().time()>=dt.time(15):return
            time.sleep(1)

    def execute(self):
        outcome='stopped_review_required';reason=None
        try:
            while not self.check_stop() and now_cn().time()<dt.time(15):
                try:
                    self.login(initial=True);break
                except (FillSnapshotPending,LoginUnavailable,MemoryError) as error:self.transient_pause(error,bootstrap=True)
            else:raise TradeError('初始化恢复已停止或执行窗口结束')
            while session_phase(now_cn()) in ('waiting_open','lunch'):
                self.publish('waiting_open' if session_phase(now_cn())=='waiting_open' else 'session_paused','等待交易窗口，自动继续')
                if self.check_stop():raise TradeError('用户已停止')
                time.sleep(5)
            while not self.check_stop() and now_cn().time()<dt.time(15):
                try:
                    if not submission_count(self.state):self.prepare()
                    self.two_audits();break
                except (FillSnapshotPending,LoginUnavailable,MemoryError) as error:self.transient_pause(error,bootstrap=True)
            else:raise TradeError('只读预检恢复已停止或执行窗口结束')
            self.publish('preflight_passed')
            while now_cn().date().isoformat()==self.day and now_cn().time()<dt.time(15):
                try:
                    if getattr(self,'_login_retry',False):self.login();self._login_retry=False
                    if getattr(self,'_resume_after_snapshot',False):
                        self.two_audits();self.http.writes_suspended=False;self._resume_after_snapshot=False
                        self.state['automatic_recovery'].update(phase='ready',ready_at=now_cn().isoformat());self.flush()
                    outcome=self.step();self.publish(outcome)
                    if outcome in ('stopped','completed_targets','completed_pilot'):break
                except receipts.SubmissionReceiptPending as error:
                    self.http.writes_suspended=True;self.publish('recovering_submission',str(error))
                    try:
                        self.audit();self.two_audits();self.http.writes_suspended=False
                    except SessionExpired:
                        try:self.login()
                        except LoginUnavailable as login_error:self._login_retry=True;self.transient_pause(login_error)
                    except LoginUnavailable as login_error:self._login_retry=True;self.transient_pause(login_error)
                    except (FillSnapshotPending,MemoryError) as retry_error:self.transient_pause(retry_error)
                except SessionExpired:
                    try:self.login()
                    except LoginUnavailable as error:self._login_retry=True;self.transient_pause(error)
                except LoginUnavailable as error:self._login_retry=True;self.transient_pause(error)
                except MemoryError as error:self.transient_pause(error)
                except FillSnapshotPending as e:
                    stamp=time.monotonic();self.fail_since=self.fail_since if self.fail_since is not None else stamp
                    if self.last_success is not None:self.fail_since=max(self.fail_since,self.last_success)
                    self.state['last_snapshot_mismatch']=dict(at=now_cn().isoformat(),reason=str(e),continuous_failure_seconds=stamp-self.fail_since);self.flush()
                    self.publish('reconciling',str(e))
                    if stamp-self.fail_since>90:self.transient_pause(e)
                time.sleep(5)
            else:outcome='closed'
        except Exception as e:
            outcome='stopped_review_required';reason=str(e) if isinstance(e,TradeError) else type(e).__name__
            import traceback
            with (self.run/'exception_trace.log').open('a',encoding='utf-8') as error_log:
                traceback.print_exc(file=error_log)
        finally:
            try:
                if self.broker is not None and not self.http.writes_suspended:
                    self.audit()
                    if now_cn().time()<dt.time(15):self.cancel(force=True)
                    self.http.writes_suspended=True
                    self.two_audits(allow_stop=True)
                    if self.state['last_audit']['pending'] or dec(self.state['last_audit']['frozen']):raise TradeError('退出时仍有挂单或冻结，需核查')
                    save(self.run/'final_readonly_audit.json',dict(checked_at=now_cn().isoformat(),issues=[],audit=self.state['last_audit']))
            except Exception as e:outcome='stopped_review_required';reason=str(e) if isinstance(e,TradeError) else type(e).__name__
            compliance=self.state.get('position_compliance',{})
            if compliance.get('enabled') and not compliance.get('met'):
                if outcome in ('closed','completed_targets','closing','stopped'):
                    outcome='position_floor_deadline_missed' if now_cn().time()>=dt.time(15) and self.day>=compliance['deadline'] else 'closed_below_position_floor'
                floor_reason=f"实际仓位{compliance['actual_fraction']:.4%}低于最低{compliance['minimum_fraction']:.2%}，缺口{compliance['gap_cny']}元，最迟{compliance['deadline']}收盘恢复"
                reason=(reason+'；' if reason else '')+floor_reason
            isolated=isolated_attempts(self.state) if self.c.get('isolate_unknown_orders') else []
            if isolated:
                if outcome in ('closed','completed_targets'):outcome='closed_with_isolated_order'
                reason=(reason+'；' if reason else '')+'仍保留未确认委托及资金预约，不会自动跨日清除'
            self.http.writes_suspended=True;self.publish(outcome,reason)
            save(self.run/'result_redacted.json',read_json(self.run/'status_redacted.json'))
            self.http.headers.clear();self.http.close();self.public.close();self.auth.close()
        return outcome


def launch_approved_profile(run):
    data=read_json(run/'approved_profile.json');day=data['day'];profile=APPROVED_PROFILES.get(day)
    if profile!=data['profile'] or now_cn().date().isoformat()!=day:raise TradeError('封存计划启动请求已过期')
    while now_cn().time()<dt.time.fromisoformat(profile['start']):
        if (run/'STOP').exists():raise TradeError('已取消封存计划启动')
        time.sleep(2)
        if now_cn().date().isoformat()!=day:raise TradeError('启动请求跨日失效')
    if (run/'STOP').exists() or now_cn().time()>dt.time.fromisoformat(profile['end']):raise TradeError('已停止或错过封存计划启动窗口')
    with mutex(APP_DIR/'account_execution.lock'):
        for directory in session_dirs():
            if directory.resolve()!=run and alive(directory):
                save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='handed_off',reason='已有交易进程，取消重复启动'));return
        result=subprocess.run(['powershell.exe','-NoProfile','-File',str(BASE_DIR/profile['launcher'])],cwd=str(ROOT),capture_output=True,
                              timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (run/'approved_launcher.log').write_bytes(result.stdout+result.stderr)
        if result.returncode:raise TradeError('专用计划启动未通过，详见approved_launcher.log；未改动其保护规则')
        save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='handed_off',reason='已交给当天封存的专用交易进程'))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);args=ap.parse_args();run=Path(args.run).resolve()
    if not run.is_relative_to((APP_DIR/'runs').resolve()):raise TradeError('运行目录必须位于控制台数据目录')
    try:
        if (run/'approved_profile.json').exists():launch_approved_profile(run);return
        with mutex(APP_DIR/'account_execution.lock'),contextlib.ExitStack() as compatibility:
            for directory in session_dirs():
                if directory.resolve()==run:continue
                if alive(directory):raise TradeError('旧交易进程仍运行，禁止并发启动')
            # All dated runners acquire this lock before any account write.
            # Sharing it closes the race with a separately scheduled legacy launch.
            from cloud_account import DIRECTORIES
            compatibility.enter_context(legacy_lock(DIRECTORIES[0]/'state/choice.lock'))
            with (run/'launch_attempt.json').open('x',encoding='utf-8') as f:json.dump(dict(at=now_cn().isoformat(),pid=os.getpid()),f)
            if wait_for_pilot_open(run):Worker(run).execute()
    except Exception as e:
        save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='stopped_review_required',reason=str(e) if isinstance(e,TradeError) else type(e).__name__))
        raise SystemExit(1)


if __name__=='__main__':main()
