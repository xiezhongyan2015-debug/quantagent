"""Cloud bootstrap and preflight. All account calls are guarded GETs.

Never imports or invokes a worker, submit, cancel, or execution authorization.
New ledgers derive from observed snapshots, including genuine historic IDs.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from console_core import BASE_DIR, HOME, DEFAULTS, TERMINAL, config_checked, reconcile, dec
from cloud_account import ACCOUNT, ACCOUNT_SHA256, assert_binding
from choice_auth import ChoiceAuthenticator, install_tokens, ENV_KEYS
from verify_readonly import GetOnlySession
from watch_engine import save
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, TradeError, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import now_cn, read_json, digest
from quanta_agents.factor_lab_a.choice_quotes import quote
import pilot_one_lot as pilot

LEDGER=BASE_DIR/'cloud_ledger'

def complete_history(broker,endpoint):
    rows=[]
    for page in range(100):
        data=broker._request(endpoint,params=dict(accId=ACCOUNT,dataRange=2,pageNo=page,pageSize=1000))['Data']
        part=data.get('data')
        if not isinstance(part,list):raise TradeError('历史查询格式异常')
        rows.extend(part)
        if len(part)<1000:
            if any(number(data[k],True)>len(rows) for k in ('total','totalCount','count') if k in data):raise TradeError('历史分页不完整')
            return rows
    raise TradeError('历史查询超过上限')

def fetch_raw(broker):
    orders=broker._rows('AguTrade/SpoOrders');deals=broker._rows('AguTrade/SpoDeal')
    ids={str(x['orderId']) for x in orders}
    if len(ids)!=len(orders):raise TradeError('委托重复')
    history=complete_history(broker,'AguTrade/SpoOrders')
    if len({str(x['orderId']) for x in history})!=len(history):raise TradeError('历史委托重复')
    orders.extend(x for x in history if str(x['orderId']) not in ids)
    current_deal_orders={str(x['orderId']) for x in deals}
    deals.extend(x for x in complete_history(broker,'AguTrade/SpoDeal') if str(x['orderId']) not in current_deal_orders)
    return dict(orders=orders,deals=deals,holds=broker._rows('AguTrade/SpoHold'),
        balance=broker._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data'],history_complete=True)

def seed_from_observation(raw,observed_at):
    day=observed_at.date().isoformat();positions={};historical={};total=dec(0)
    b=raw['balance']
    if dec(b['frozenMoney'])!=0:raise TradeError('有冻结资金，不能新建账本')
    for r in raw['holds']:
        code=normalize_code(str(r['secCode']));shares=number(r['count'],True)
        if code in positions or number(r['availCount'],True)>shares:raise TradeError('持仓重复或可卖股数异常')
        if shares:positions[code]=shares
        total+=dec(r['value'])
    if abs(total-dec(b['totalPosValue']))>dec('.03') or abs(dec(b['availBalance'])+total-dec(b['totalAssets']))>dec('.03'):
        raise TradeError('现金、持仓市值、总资产不一致')
    for r in raw['orders']:
        oid=str(r['orderId']);date=str(r['orderDate']).replace('-','')
        if (len(date)!=8 or not date.isdecimal() or date>=day.replace('-','')
            or oid in historical or str(r['status']) not in TERMINAL):
            raise TradeError('新账本不能接纳当日委托、未来委托、重复或未结束的历史委托')
        quantity=number(r['orderCount'],True);filled=number(r['tradeCount'],True);price=dec(r['orderPrice']);side=int(r['drt'])
        if side not in (1,2) or filled>quantity or quantity<=0 or price<=0 or (str(r['status'])=='4' and filled!=quantity):raise TradeError('历史委托格式异常')
        historical[oid]=dict(code=normalize_code(str(r['secCode'])),side=side,quantity=quantity,price=str(price),filled=filled,state=str(r['status']))
    if any(str(r['orderId']) not in historical for r in raw['deals']):raise TradeError('历史成交缺少对应委托')
    for oid,order in historical.items():
        linked=[x for x in raw['deals'] if str(x['orderId'])==oid]
        if sum(number(x['tradeCount'],True) for x in linked)!=order['filled']:raise TradeError('历史成交未完整关联，不能建立新账本')
    state=dict(day=day,baseline=dict(positions=positions,cash=str(dec(b['availBalance'])),paid='0'),
        historical=historical,prior={},attempts=[],targets={},carried_reserve='0',baseline_security_reserve={},deferred={},quotes={},
        origin=dict(kind='observed_new_cloud_ledger',observed_at=observed_at.isoformat(),account_sha256=ACCOUNT_SHA256,
            snapshot_sha256=digest(raw),prior_account_activity_included=True,execution_authorization_created=False),
        auth_recovery=dict(attempts=0,phase='ready',events=[]))
    state['last_audit']=reconcile(raw,state,'0.00','.03')
    state['last_audit']['checked_at']=observed_at.isoformat()
    return state

def prepare_cloud(bootstrap=False,preview=False):
    from cloud_account import verify_deployment
    verify_deployment()
    previous={k:os.environ.get(k) for k in tuple(ENV_KEYS.values())+('CHOICE_ACCOUNT_ID',)}
    auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);path=LEDGER/'private_state.json'
    c=config_checked(dict(DEFAULTS,**read_json(HOME/'settings.json'))) if (HOME/'settings.json').exists() else config_checked(DEFAULTS)
    try:
        tokens=auth.current_tokens();install_tokens(tokens,http,ACCOUNT);tokens.clear()
        # allow_orders=False remains immutable throughout this read-only utility.
        broker=ChoiceBroker({'sellable':'availCount'},allow_orders=False,session=http)
        first=fetch_raw(broker);t1=now_cn()
        if path.exists():
            # Once a real run exists, audit its latest durable ledger. Never
            # reset to the initial snapshot to forget a pilot submission.
            from console_runtime import latest_session, active_session
            if active_session():raise TradeError('交易进程运行中，请直接查看状态；不并发读取或修改配置')
            current=latest_session();state=read_json(current/'private_state.json');reconcile(first,state,'0.00','.03')
        elif bootstrap:state=seed_from_observation(first,t1)
        else:raise TradeError('尚未建立观测账本')
        time.sleep(5)
        second=fetch_raw(broker);t2=now_cn()
        if t2.date()!=t1.date():raise TradeError('核验跨日，需重新只读读取')
        a=reconcile(second,state,'0.00','.03')
        if digest(first)!=digest(second):
            # Quote marks may change during trading, but inventory, cash and
            # every history row must agree. Never rebase to accept external trades.
            stable=lambda r:dict(holds=[{k:x[k] for k in ('secCode','count','availCount')} for x in r['holds']],
                orders=r['orders'],deals=r['deals'],cash=r['balance']['availBalance'],frozen=r['balance']['frozenMoney'])
            if digest(stable(first))!=digest(stable(second)):raise TradeError('两轮账户关键字段不一致')
        state['last_audit']=dict(a,checked_at=t2.isoformat())
        marks={normalize_code(str(x['secCode'])):dec(x['value'])/number(x['count'],True) for x in second['holds'] if number(x['count'],True)>0}
        candidate_codes,evidence=pilot.candidates(c,BASE_DIR,a['positions'],marks)
        if not c['pilot_symbol']:c['pilot_symbol']=candidate_codes[0]
        if c['pilot_symbol'] not in candidate_codes:raise TradeError('锁定的试单证券不再符合候选条件，停止并保留计划')
        proof=dict(first_at=t1.isoformat(),second_at=t2.isoformat(),first_sha256=digest(first),second_sha256=digest(second),
            identity_bound=True,account_tail=ACCOUNT[-4:],account_sha256=ACCOUNT_SHA256,queries=http.audit,
            new_orders_submitted=0,cancellations_submitted=0,execution_authorization_created=False)
        if bootstrap and not path.exists():
            LEDGER.mkdir(parents=True,exist_ok=True)
            save(LEDGER/'bootstrap_first_raw.json',first);save(LEDGER/'bootstrap_second_raw.json',second)
            state['origin']['bootstrap_proof']=proof
            save(path,state)
        save(HOME/'settings.json',c);save(LEDGER/'readonly_check.json',proof)
        result=dict(checked_at=t2.isoformat(),account_tail=ACCOUNT[-4:],holdings=len(a['positions']),history_orders=len(state['historical']),
            cash=a['cash'],total_assets=a['total_assets'],market_value=a['market_value'],selected_symbol=c['pilot_symbol'],
            new_orders_submitted=0,cancellations_submitted=0,execution_authorization_created=False)
        if preview:
            import requests
            with requests.Session() as public:
                public.trust_env=False;result['latest_membership_file_verified']=pilot.assert_membership_current(c,public)
                q=quote(c['pilot_symbol'],public)
            plan=pilot.make_plan(c,BASE_DIR,a,q,now_cn(),executable=False)
            save(HOME/'preflight_preview.json',plan)
            result.update(name=plan['selected_name'],held_before=plan['held_before'],incremental_shares=100,target_shares=plan['target_shares'],
                indicative_limit_price=plan['limit_price'],reserved_total_cny=plan['reserved_total_cny'],quote_timestamp=q['timestamp'],preview_only=True)
        save(LEDGER/'status_redacted.json',dict(at=t2.isoformat(),status='prepared_readonly',reason='队长账户两轮核验通过；尚未启动交易',**result))
        save(HOME/'cloud_preparation_receipt.json',result)
        return result
    finally:
        http.headers.clear();http.close();auth.close()
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bootstrap',action='store_true');ap.add_argument('--preview',action='store_true');args=ap.parse_args()
    try:print(json.dumps(prepare_cloud(args.bootstrap,args.preview),ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(status='blocked',error=str(error) if isinstance(error,TradeError) else type(error).__name__,new_orders_submitted=0),ensure_ascii=False))
        raise SystemExit(1)

if __name__=='__main__':main()
