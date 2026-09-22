"""Standalone supervisor and Choice simulation worker; never needs a Codex session."""
import argparse
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
from console_core import BASE_DIR, HOME as APP_DIR, ROOT, DEFAULTS, TERMINAL, config_checked, days_for, session_phase, legacy_state, roll_day, reconcile, dec, pcf_targets, parse_targets, differences
from support import save, reserve
from market_checks import validate_quote, stable_price_at_ask
from choice_auth import ChoiceAuthenticator, HumanLoginRequired, install_tokens
from local_access import GetOnlySession, account_id, binding
ACCOUNT=account_id(required=False)
from quanta_agents.factor_lab_a.choice_pcf import BASE, ChoiceBroker, TradeError, SessionExpired, FillSnapshotPending, order_payload
from quanta_agents.factor_lab_a.choice_quotes import quote
from quanta_agents.factor_lab_a.pcf import download_pcf, normalize_code
from quanta_agents.factor_lab_a.pcf_automation import now_cn, read_json, digest

PYTHON=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
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
    return sorted((APP_DIR/'runs').glob('*/*'))


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
    if not candidates:raise TradeError('请先运行 setup_account.py 初始化自己的模拟账户，详见 README')
    def chronology(p):
        s=read_json(p/'private_state.json')
        return (s.get('day',''),len(s.get('attempts',[])),(p/'private_state.json').stat().st_mtime)
    return max(candidates,key=chronology)


def historical_rows(broker,endpoint):
    rows=[]
    for page in range(100):
        data=broker._request(endpoint,params=dict(accId=broker.account,dataRange=2,pageNo=page,pageSize=1000))['Data']
        part=data['data']
        if not isinstance(part,list):raise TradeError('历史查询格式异常')
        rows.extend(part)
        if len(part)<1000:
            if any(int(data[k])>len(rows) for k in ('total','totalCount','count') if k in data):raise TradeError('历史查询分页不完整')
            return rows
    raise TradeError('历史查询超过分页上限，请核查账户')


def account_snapshot(broker,state):
    """The platform moves today's rows into history after close; retain exact IDs."""
    orders=broker._rows('AguTrade/SpoOrders');deals=broker._rows('AguTrade/SpoDeal')
    expected=set(state.get('prior',{}))|{a['oid'] for a in state['attempts'] if a.get('oid')}
    ids={str(r['orderId']) for r in orders}
    if not orders or expected-ids:
        history=historical_rows(broker,'AguTrade/SpoOrders')
        seen=set()
        for row in history:
            oid=str(row['orderId'])
            if oid in seen:raise TradeError('历史委托分页出现重复ID')
            seen.add(oid)
            if oid not in ids:orders.append(row)
        current_deal_ids={str(r['orderId']) for r in deals}
        deals.extend(row for row in historical_rows(broker,'AguTrade/SpoDeal') if str(row['orderId']) not in current_deal_ids)
    return dict(orders=orders,deals=deals,holds=broker._rows('AguTrade/SpoHold'),
        balance=broker._request('AguTrade/SpoBalInfo',params={'accId':broker.account})['Data'])


def status_view(directory=None):
    directory=Path(directory) if directory else active_session() or latest_session()
    result=dict(directory=str(directory),running=alive(directory))
    if (directory/'status_redacted.json').exists():result.update(read_json(directory/'status_redacted.json'))
    if (directory/'private_state.json').exists():
        state=read_json(directory/'private_state.json');a=state.get('last_audit',{})
        result.update(submitted=len(state['attempts']),filled_shares=sum(x.get('filled',0) for x in state['attempts']),
            verified_at=a.get('checked_at'),stock_fraction=a.get('stock_fraction'),cash=a.get('cash'),frozen=a.get('frozen'),
            remaining_count=len(differences(state.get('targets',{}),a)) if a else None,
            unresolved=sum(not x.get('oid') for x in state['attempts']))
    return result


def start_console(config):
    c=config_checked(config);now=now_cn();day=now.date().isoformat()
    APP_DIR.mkdir(parents=True,exist_ok=True)
    with mutex(APP_DIR/'launch.lock'):
        active=active_session()
        if active:return dict(action='attached',directory=str(active))
        days_for(c,day)
        if now.time()>=dt.time(14,50):raise TradeError('今天新增委托窗口已结束；下个交易日再启动')
        identity=binding()
        if ACCOUNT!=identity['account_id']:raise TradeError('账户绑定已变化，请重启控制台')
        source=latest_session();old=read_json(source/'private_state.json')
        if old.get('account_binding_sha256')!=digest(identity):raise TradeError('来源账本与本机绑定账户不一致，禁止复用别人的账本')
        if any(not a.get('oid') for a in old['attempts']):raise TradeError('上次存在结果不明的委托，不能自动重发')
        state=roll_day(old,day)
        run=APP_DIR/'runs'/day/(now.strftime('%H%M%S')+'_'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
        save(run/'config.json',c)
        save(run/'source_seal.json',dict(path=str(source/'private_state.json'),sha256=digest(old)))
        save(run/'seed.json',state)
        save(run/'authorization.json',dict(at=now.isoformat(),execution_date=day,action='User pressed Start in the simulation console',
            config_sha256=digest(c),seed_sha256=digest(state),binding_sha256=digest(identity),account_mode='competition_simulation_only'))
        save(run/'status_redacted.json',dict(at=now.isoformat(),status='starting',reason=None))
        with (run/'stdout.log').open('ab') as out,(run/'stderr.log').open('ab') as err:
            process=subprocess.Popen([str(PYTHON),'-u',str(Path(__file__).resolve()),'--run',str(run)],cwd=str(ROOT),stdout=out,stderr=err,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        save(run/'process.json',dict(pid=process.pid,created_seconds=psutil.Process(process.pid).create_time(),started_at=now.isoformat(),
             script='console_runtime.py',execution_date=day,account_mode='competition_simulation_only'))
        return dict(action='started',directory=str(run))


class Transport(requests.Session):
    def __init__(self,run,c,day):
        super().__init__();self.run=run;self.c=c;self.day=day;self.armed=None;self.writes_suspended=True;self.cancelled=set()
    def request(self,method,url,**kw):
        method=method.upper();ep=url[len(BASE):] if url.startswith(BASE) else ''
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
                if p.get('orderDrt')!=o['side'] or p.get('volume')!=o['quantity'] or dec(p['price'])!=dec(o['price']):raise TradeError('写入委托与持久化意图不一致')
                if o['side']==2 and not self.c['allow_sells']:raise TradeError('当前未开启卖出')
                if o['quantity']*o['price']>self.c['max_order_value'] or abs(dec(o['price'])/dec(o['reference_price'])-1)>dec(self.c['price_guard']):raise TradeError('写入层限额或价格保护')
            elif kind=='cancel':
                oid=str(p.get('orderId'))
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
        finally:
            with (self.run/'http_trace_redacted.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')


class Broker(ChoiceBroker):
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
        self.run=Path(run);self.c=config_checked(read_json(self.run/'config.json'));a=read_json(self.run/'authorization.json')
        self.state=read_json(self.run/'seed.json');seal=read_json(self.run/'source_seal.json')
        if digest(self.c)!=a['config_sha256'] or digest(self.state)!=a['seed_sha256'] or digest(read_json(seal['path']))!=seal['sha256']:raise TradeError('启动快照或来源账本已变化')
        if a.get('binding_sha256')!=digest(binding()):raise TradeError('启动后账户绑定发生变化')
        if self.state.get('account_binding_sha256')!=a['binding_sha256']:raise TradeError('账户账本绑定不一致')
        self.day=a['execution_date']
        if self.day!=now_cn().date().isoformat() or self.state['day']!=self.day:raise TradeError('过期启动授权')
        self.http=Transport(self.run,self.c,self.day);self.public=requests.Session();self.auth=ChoiceAuthenticator();self.broker=None
        self.last_submit=0.;self.last_success=None;self.fail_since=None;self.status='starting';self.reason=None
        self.state['config_snapshot']=self.c;self.state.setdefault('deferred',{});self.flush()
    def flush(self):save(self.run/'private_state.json',self.state)
    def publish(self,status,reason=None):
        self.status=status;self.reason=reason;a=self.state.get('last_audit',{})
        save(self.run/'status_redacted.json',dict(at=now_cn().isoformat(),status=status,reason=reason,execution_date=self.day,
            submitted=len(self.state['attempts']),filled_shares=sum(x.get('filled',0) for x in self.state['attempts']),
            stock_fraction=a.get('stock_fraction'),frozen_cny=a.get('frozen'),pending=a.get('pending'),
            remaining=differences(self.state.get('targets',{}),a) if a else None,deferred=self.state['deferred'],
            unresolved_intents=sum(not x.get('oid') for x in self.state['attempts']),excluded_cash_cny=self.c['excluded_cash_cny'],
            trading_writes_suspended=self.http.writes_suspended))
    def raw(self):
        if self.broker is None:raise SessionExpired()
        return account_snapshot(self.broker,self.state)
    def audit(self):
        a=reconcile(self.raw(),self.state,self.c['excluded_cash_cny'])
        for attempt in self.state['attempts']:
            attempt.update(a['states'][attempt['oid']])
            if attempt['terminal'] and not attempt.get('terminal_at'):attempt['terminal_at']=now_cn().isoformat()
        a['checked_at']=now_cn().isoformat();self.state['last_audit']=a;self.last_success=time.monotonic();self.fail_since=None;self.flush();return a
    def check_stop(self):return (self.run/'STOP').exists() or now_cn().date().isoformat()!=self.day
    def bind(self,tokens):
        install_tokens(tokens,self.http,ACCOUNT);tokens.clear()
        self.broker=Broker(dict(initial_buy_only=True,validated_submission_with_authorized_test=True,validated_cancel_with_authorized_test=True),True,self.http)
    def two_audits(self,allow_stop=False):
        consistent=0;until=time.monotonic()+90
        while consistent<2:
            if not allow_stop and self.check_stop():raise TradeError('用户已停止')
            try:self.audit();consistent+=1
            except FillSnapshotPending as e:self.publish('reconciling',str(e));consistent=0
            if time.monotonic()>until:raise TradeError('连续90秒账户核对未通过')
            if consistent<2:time.sleep(5)
    def login(self,initial=False):
        self.http.writes_suspended=True
        recovery=self.state.setdefault('auth_recovery',dict(attempts=0,phase='ready',events=[]))
        if initial:
            try:self.bind(self.auth.current_tokens());self.two_audits();self.http.writes_suspended=False;return
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
    def prepare(self):
        _,signal=days_for(self.c,self.day)
        seed=read_json(self.run/'seed.json');prior_cfg=seed.get('config_snapshot')
        keys=('target_fraction','capital_limit','max_security_value','max_order_value')
        legacy_same=(self.c['strategy']=='pcf_563030' and all(seed.get(k)==self.c[k] for k in keys) and self.c['price_guard']==.03)
        if seed.get('targets') and (seed.get('plan_config_sha256')==digest(self.c) or (not prior_cfg and legacy_same)):
            self.state['targets']=seed['targets'];self.state['plan_origin']='inherited_same_day_frozen_targets'
            self.state['plan_config_sha256']=digest(self.c);self.flush()
            save(self.run/'plan.json',dict(day=self.day,targets=self.state['targets'],origin=self.state['plan_origin']));return
        self.publish('preparing','正在生成今日目标持仓；只读查询，不下单')
        a=self.audit();strategy=self.c['strategy'];obj=None;record=None
        if strategy=='pcf_563030':
            record=download_pcf('563030',signal,str(self.run/'pcf_download.json'))
            codes={normalize_code(str(r['C_STOCKCODE'])) for r in record['stocklist']}
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
        for code in sorted(codes):
            if self.check_stop():raise TradeError('用户已停止')
            q=quote(code,self.public);stamp=dt.datetime.fromisoformat(q['timestamp'])
            if stamp.date().isoformat()!=self.day or stamp>now_cn() or q['previous_close']<=0:raise TradeError('参考行情不是今日数据：'+code)
            quotes[code]=q
        save(self.run/'reference_quotes.json',quotes)
        if record:
            import pandas as pd
            members=pd.read_parquet(self.c['membership_path']);universe=set(members.symbol.map(normalize_code))
            targets=pcf_targets(record,self.day,signal,a['total_assets'],self.c,quotes,universe)
        else:targets=parse_targets(obj,self.day,signal,a['total_assets'],self.c,quotes)
        for code in a['positions']:targets.setdefault(code,dict(code=code,quantity=0,reference_price=quotes[code]['previous_close']))
        reductions={c:o['quantity']-a['positions'].get(c,0) for c,o in targets.items() if o['quantity']<a['positions'].get(c,0)}
        save(self.run/'plan.json',dict(day=self.day,signal_date=signal,strategy=strategy,targets=targets,requires_sells=bool(reductions),created_at=now_cn().isoformat()))
        if reductions and not self.c['allow_sells']:raise TradeError('今日目标需要减仓；请查看计划，并在设置中明确开启“允许卖出调仓”后启动')
        self.state['targets']=targets;self.state['plan_config_sha256']=digest(self.c);self.flush()
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
        gap=differences(self.state['targets'],audit)
        if not gap and not audit['pending'] and not dec(audit['frozen']):
            self.two_audits();return 'completed_targets'
        pending_codes={o['code'] for o in audit['pending'].values()}
        for code,delta in sorted(gap.items(),key=lambda item:(item[1]>0,item[0])):
            if code in pending_codes:continue
            attempts=[a for a in self.state['attempts'] if a['order']['code']==code]
            if len(attempts)>=3:self.state['deferred'][code]='达到当日最多3次委托';continue
            if attempts and attempts[-1]['state']=='9':self.state['deferred'][code]='拒单需核查';continue
            if attempts and (now_cn()-dt.datetime.fromisoformat(attempts[-1]['terminal_at'])).total_seconds()<60:continue
            side=1 if delta>0 else 2
            if side==2 and not self.c['allow_sells']:self.state['deferred'][code]='未开启卖出';continue
            try:q=quote(code,self.public);validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
            except Exception:self.state['deferred'][code]='行情不可用、停牌或ST';continue
            if q['code']!=code:raise TradeError('行情证券身份异常')
            ref=self.state['targets'][code]['reference_price'];price=stable_price_at_ask(q['ask'] if side==1 else q['bid'],0)
            if price is None or abs(dec(price)/dec(ref)-1)>dec(self.c['price_guard']):self.state['deferred'][code]='价格保护或价格精度';continue
            quantity=abs(delta)
            if side==2:quantity=min(quantity,audit['sellable'].get(code,0))
            max_quantity=int(self.c['max_order_value']/price//100)*100
            quantity=min(quantity,max_quantity)
            if side==1 and (quantity<(200 if code.startswith('SH688') else 100) or quantity%100):self.state['deferred'][code]='不足买入整手';continue
            if side==2 and quantity<=0:self.state['deferred'][code]='T+1或可卖数量不足';continue
            if side==2 and quantity!=audit['sellable'].get(code,0):quantity=quantity//100*100
            if side==2 and code.startswith('SH688') and quantity<200 and not (quantity==audit['positions'].get(code,0) and self.state['targets'][code]['quantity']==0):
                self.state['deferred'][code]='科创板不足200股的非清仓减仓暂不执行';continue
            if quantity<=0:continue
            need=reserve(dec(price)*quantity)
            buy_reserves=sum((dec(a['reserve']) for a in self.state['attempts'] if a['order'].get('side',1)==1),Decimal(0))
            pending_buy=sum(dec(o['price'])*o['quantity'] for o in audit['pending'].values() if o['side']==1)
            security_mark=max(q['last'],price)*(audit['positions'].get(code,0)+quantity)
            security_reserved=dec(self.state.get('baseline_security_reserve',{}).get(code,0))+sum((dec(a['reserve']) for a in attempts if a['order'].get('side',1)==1),Decimal(0))+need
            if side==1 and (need>dec(audit['cash']) or dec(audit['paid'])+dec(audit['frozen'])+need>dec(self.c['capital_limit'])
                    or dec(self.state['carried_reserve'])+buy_reserves+need>dec(self.c['capital_limit'])
                    or dec(audit['market_value'])+pending_buy+need>dec(self.c['capital_limit'])
                    or security_mark>self.c['max_security_value'] or security_reserved>dec(self.c['max_security_value'])):
                self.state['deferred'][code]='总资金或单股限额';continue
            if time.monotonic()-self.last_submit<self.c['spacing_seconds']:return 'watching'
            if self.check_stop() or session_phase(now_cn())!='trading':return 'session_paused'
            validate_quote(q,dict(quote_max_age_seconds=30),now_cn())
            order=dict(code=code,side=side,quantity=quantity,price=price,reference_price=ref)
            order_payload(ACCOUNT,code,side,quantity,price)
            a=dict(order=order,reserve=str(need),state='SENDING',filled=0,submitted_at=now_cn().isoformat())
            self.state['attempts'].append(a);self.state['deferred'].pop(code,None);self.flush();self.http.armed=('submit',order)
            try:a.update(oid=self.broker.submit(order),state='ACK')
            except Exception:a['state']='UNKNOWN';self.flush();raise
            finally:self.http.armed=None
            self.last_submit=time.monotonic();self.flush();return 'watching'
        self.flush();return 'watching'
    def execute(self):
        outcome='stopped_review_required';reason=None
        try:
            self.login(initial=True)
            while session_phase(now_cn()) in ('waiting_open','lunch'):
                self.publish('waiting_open' if session_phase(now_cn())=='waiting_open' else 'session_paused','等待交易窗口，自动继续')
                if self.check_stop():raise TradeError('用户已停止')
                time.sleep(5)
            self.prepare();self.two_audits();self.publish('preflight_passed')
            while now_cn().date().isoformat()==self.day and now_cn().time()<dt.time(15):
                try:
                    outcome=self.step();self.publish(outcome)
                    if outcome in ('stopped','completed_targets'):break
                except SessionExpired:self.login()
                except FillSnapshotPending as e:
                    stamp=time.monotonic();self.fail_since=self.fail_since if self.fail_since is not None else stamp
                    if self.last_success is not None:self.fail_since=max(self.fail_since,self.last_success)
                    self.state['last_snapshot_mismatch']=dict(at=now_cn().isoformat(),reason=str(e),continuous_failure_seconds=stamp-self.fail_since);self.flush()
                    self.publish('reconciling',str(e))
                    if stamp-self.fail_since>90:raise TradeError('连续90秒核账失败：'+str(e))
                time.sleep(5)
            else:outcome='closed'
        except Exception as e:outcome='stopped_review_required';reason=str(e) if isinstance(e,TradeError) else type(e).__name__
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
            self.http.writes_suspended=True;self.publish(outcome,reason)
            save(self.run/'result_redacted.json',read_json(self.run/'status_redacted.json'))
            self.http.headers.clear();self.http.close();self.public.close();self.auth.close()
        return outcome



def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);args=ap.parse_args();run=Path(args.run).resolve()
    if not run.is_relative_to((APP_DIR/'runs').resolve()):raise TradeError('运行目录必须位于控制台数据目录')
    try:
        with mutex(APP_DIR/'account_execution.lock'):
            for directory in session_dirs():
                if directory.resolve()==run:continue
                if alive(directory):raise TradeError('旧交易进程仍运行，禁止并发启动')
            with (run/'launch_attempt.json').open('x',encoding='utf-8') as f:json.dump(dict(at=now_cn().isoformat(),pid=os.getpid()),f)
            Worker(run).execute()
    except Exception as e:
        save(run/'status_redacted.json',dict(at=now_cn().isoformat(),status='stopped_review_required',reason=str(e) if isinstance(e,TradeError) else type(e).__name__))
        raise SystemExit(1)


if __name__=='__main__':main()
