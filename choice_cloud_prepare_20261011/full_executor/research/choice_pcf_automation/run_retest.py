"""One new, user-requested simulation diagnostic after the failed morning run.

Preserves the original launch/journal; one previously untraded PCF security only.
Previous budget reservations carry forward, even though those orders cancelled.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import requests
from verify_readonly import ROOT, session_tokens, GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import BASE, ChoiceBroker, TradeError, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import (
    Journal, read_json, write_json, now_cn, validate_session, calendar, digest, execute_orders, lock,
)

DAY = '2026-09-21'
ACCOUNT = __import__('os').environ['CHOICE_SIMULATION_ACCOUNT']
OLD = ROOT / 'research/choice_pcf_automation/launch_20260921'
DEST = ROOT / 'research/choice_pcf_automation/retest_20260921_v1'


def safe_rows(rows):
    result = []
    for r in rows:
        out = {k:r[k] for k in ('secCode','drt','orderCount','orderPrice','tradeCount','status',
                               'orderDate','orderTime','count','tradePrice','tradeAmount','tradeAmt',
                               'tradeTime','fee','commission','stampTax','transferFee') if k in r}
        if 'orderId' in r:
            out['order_id_tail'] = str(r['orderId'])[-4:]
            out['order_id_hash'] = hashlib.sha256(str(r['orderId']).encode()).hexdigest()
        out['field_names'] = sorted(r)
        result.append(out)
    return result


class TracedSimulationSession(requests.Session):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.expected = None
        self.submissions = 0
        self.cancellations = 0
        self.known_id = None

    def request(self, method, url, **kw):
        endpoint = url[len(BASE):] if url.startswith(BASE) else ''
        payload = kw.get('json')
        if method.upper() == 'GET':
            params = kw.get('params') or {}
            if endpoint not in GetOnlySession.KEYS or set(params) != GetOnlySession.KEYS[endpoint] or params['accId'] != ACCOUNT:
                raise TradeError('Read outside fixed simulated account allowlist')
        elif method.upper() == 'POST':
            if not self.expected or not isinstance(payload, dict) or payload.get('accId') != ACCOUNT:
                raise TradeError('Write outside fixed simulated account')
            if payload.get('stkCode') != self.expected['code'][2:]:
                raise TradeError('Write outside one selected PCF security')
            if endpoint == 'AguTrade/SpoOrder':
                if self.submissions or payload.get('orderDrt') != 1 or payload.get('volume') != self.expected['quantity']:
                    raise TradeError('Only one fixed buy submission is allowed')
                if number(payload['price']) * payload['volume'] > 100000:
                    raise TradeError('Per-order cap exceeded')
                self.submissions += 1
            elif endpoint == 'AguTrade/SpoCancel':
                if self.cancellations or not self.known_id or str(payload.get('orderId')) != self.known_id:
                    raise TradeError('Only one cancellation of the exact acknowledged order is allowed')
                self.cancellations += 1
            else:
                raise TradeError('Unsupported write endpoint')
        else:
            raise TradeError('Unsupported HTTP method')
        if kw.get('allow_redirects') is not False:
            raise TradeError('Redirects prohibited')
        event = {'at':now_cn().isoformat(),'method':method.upper(),'endpoint':endpoint}
        start = time.monotonic()
        try:
            response = super().request(method,url,**kw)
            event['http_status'] = response.status_code
            obj = response.json()
            event['business_code'] = obj.get('Code')
            data = obj.get('Data')
            event['data_type'] = type(data).__name__
            if isinstance(data,dict):
                event['data_keys'] = sorted(data)
                if endpoint == 'AguTrade/SpoOrder' and str(obj.get('Code')) == '0' and data.get('orderID'):
                    self.known_id = str(data['orderID'])
                    event['order_id_tail'] = self.known_id[-4:]
                if isinstance(data.get('data'),list):
                    event['rows'] = safe_rows(data['data'])
            return response
        except Exception as error:
            event['error_type'] = type(error).__name__
            raise
        finally:
            event['elapsed_ms'] = round((time.monotonic()-start)*1000,3)
            with self.path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(event,ensure_ascii=False)+'\n')
                f.flush()


def final_audit(broker, journal):
    orders = broker._rows('AguTrade/SpoOrders')
    holds = broker._rows('AguTrade/SpoHold')
    deals = broker._rows('AguTrade/SpoDeal')
    b = broker._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data']
    entries = journal.db.execute('SELECT code,state,oid,filled FROM orders WHERE day=?',(DAY,)).fetchall()
    known = {str(x[2]) for x in entries if x[2]}
    return {'checked_at':now_cn().isoformat(),
            'balance':{k:b.get(k) for k in ('totalAssets','availBalance','frozenMoney','totalPosValue')},
            'own_orders':safe_rows([x for x in orders if str(x['orderId']) in known]),
            'holdings':safe_rows(holds),'deals':safe_rows(deals),
            'account_pending_orders':safe_rows([x for x in orders if str(x['status']) not in ('4','7','8','9')]),
            'journal':[{'code':c,'state':s,'order_id_tail':str(oid)[-4:] if oid else None,'filled':f} for c,s,oid,f in entries],
            'events':[{'at':a,'code':c,'phase':p,'detail':json.loads(d)} for a,c,p,d in journal.db.execute('SELECT at,code,phase,detail FROM events')],
            'cumulative_reserved_cny':journal.db.execute('SELECT COALESCE(SUM(reserved_cents),0)/100.0 FROM capital WHERE day=?',(DAY,)).fetchone()[0]}


def main():
    if sys.argv[1:] != ['--execute-user-authorized-retest']:
        raise TradeError('Explicit new user-authorized diagnostic flag required')
    cfg = read_json(OLD/'pcf_automation.local.json')
    if now_cn().date().isoformat() != DAY:
        raise TradeError('This diagnostic is only authorized for the current day')
    validate_session(now_cn(), calendar(cfg))
    if cfg['capital_limit'] != 2000000 or cfg['max_order_value'] != 100000:
        raise TradeError('Original caps changed')
    with sqlite3.connect((OLD/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('SELECT account FROM binding WHERE id=1').fetchone() != (hashlib.sha256(ACCOUNT.encode()).hexdigest(),):
            raise TradeError('Original account binding mismatch')
        old_plan = json.loads(db.execute('SELECT plan FROM batches WHERE day=?',(DAY,)).fetchone()[0])
        old_orders = db.execute('SELECT code,state,oid,filled FROM orders WHERE day=?',(DAY,)).fetchall()
        reservations = db.execute('SELECT code,reserved_cents FROM capital WHERE day=?',(DAY,)).fetchall()
    if not old_orders or any(s != 'CANCELLED' or not oid or f for c,s,oid,f in old_orders):
        raise TradeError('Previous order must be independently verified as cancelled without fills')
    manifest = read_json(OLD/'state/archive/2026-09-18/manifest.json')
    pcf = read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if manifest['pcf_sha256'] != old_plan['pcf_sha256'] or digest({k:pcf[k] for k in ('date','baseinfo','stocklist')}) != manifest['pcf_sha256']:
        raise TradeError('Original PCF integrity changed')
    DEST.mkdir(exist_ok=True)
    with (DEST/'retest_attempt.json').open('x',encoding='utf-8') as f:
        json.dump({'started_at':now_cn().isoformat(),'account_tail':ACCOUNT[-4:],
                   'authorization':'User explicitly requested investigation and a new test after the failed morning run',
                   'max_new_orders':1,'total_budget_limit':2000000},f)
        f.flush();os.fsync(f.fileno())
    cfg.update(state_dir=str(DEST/'state'),live_quotes=str(DEST/'live_quotes.json'),
               quote_provider='choice_public',quote_command=[],max_orders=1)
    Path(cfg['state_dir']).mkdir(exist_ok=True)
    envmap={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    previous={k:os.environ.get(k) for k in [*envmap.values(),'CHOICE_ACCOUNT_ID']}
    session=TracedSimulationSession(DEST/'http_trace_redacted.jsonl')
    journal=None
    report={'mode':'Choice_competition_simulation','account_tail':ACCOUNT[-4:],'status':'preflight',
            'started_at':now_cn().isoformat(),'capital_limit':2000000,'max_new_orders':1}
    try:
        tokens=session_tokens(tuple(envmap))
        for k,v in envmap.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        with lock(OLD/'state/choice.lock'),lock(Path(cfg['state_dir'])/'choice.lock'):
            broker=ChoiceBroker(cfg['choice_contract'],True,session)
            state=broker.snapshot()
            if state['positions'] or state['frozen'] or state['cash']!=10000000:
                raise TradeError('Expected unchanged empty simulated account')
            server_orders=broker._rows('AguTrade/SpoOrders')
            for code,s,oid,f in old_orders:
                matching=[x for x in server_orders if str(x['orderId'])==str(oid)]
                if len(matching)!=1 or str(matching[0]['status'])!='8' or number(matching[0]['tradeCount'],True)!=0 or normalize_code(str(matching[0]['secCode']))!=code:
                    raise TradeError('Previous exact order not confirmed cancelled without fills')
            traded={normalize_code(str(x['secCode'])) for x in server_orders
                    if str(x['orderDate']).replace('-','')==DAY.replace('-','')}
            reserved_codes={c for c,v in reservations}
            selected=next((dict(o) for o in old_plan['orders'] if o['code'] not in traded|reserved_codes),None)
            if not selected:raise TradeError('No previously untraded PCF security')
            session.expected=selected
            report['order']=selected
            journal=Journal(Path(cfg['state_dir'])/'choice.sqlite',ACCOUNT)
            for code,cents in reservations:
                journal.reserve_capital(DAY,code,cents/100,2000000)
            report['prior_reservation_carried_cny']=sum(v for c,v in reservations)/100
            plan=dict(old_plan,orders=[selected],initial_positions={})
            write_json(DEST/'plan.json',plan)
            report['result']=execute_orders(cfg,plan,broker,journal)
            report['status']='complete'
    except Exception as error:
        report.update(status='stopped_review_required',error_type=type(error).__name__)
        if isinstance(error,TradeError):report['reason']=str(error)
    finally:
        if journal is not None:
            try:report['audit']=final_audit(broker,journal)
            except Exception as error:report['audit_error_type']=type(error).__name__
            journal.db.close()
        report.update(finished_at=now_cn().isoformat(),submit_requests=session.submissions,cancel_requests=session.cancellations)
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close()
        write_json(DEST/'retest_result.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__)
        raise SystemExit(1)
