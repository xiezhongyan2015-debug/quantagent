"""Explicitly authorized PCF completion, with the original CNY2m aggregate cap.

Keeps prior journals intact; carries their reservations, even for cancelled orders.
The user explicitly retained the 3% price guard and deferred five named securities.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from decimal import Decimal

import requests
from run_retest import ACCOUNT, DAY, OLD, ROOT, safe_rows, final_audit
from verify_readonly import session_tokens, GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import BASE, ChoiceBroker, TradeError, number
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import (
    Journal, read_json, write_json, now_cn, calendar, validate_session, digest, lock,
    live_quote, order_cash_reserve, execute_orders,
)

RETEST=ROOT/'research/choice_pcf_automation/retest_20260921_v1'
DEST=ROOT/'research/choice_pcf_automation/full_basket_20260921'
USER_DEFERRED={'SH600601','SH688281','SH688336','SH688561','SH600502'}


def journal_read(directory):
    with sqlite3.connect((directory/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('SELECT account FROM binding WHERE id=1').fetchone()!=(hashlib.sha256(ACCOUNT.encode()).hexdigest(),):
            raise TradeError('Prior account binding mismatch')
        batch=db.execute('SELECT plan FROM batches WHERE day=?',(DAY,)).fetchone()
        return (json.loads(batch[0]),db.execute('SELECT code,state,oid,filled FROM orders WHERE day=?',(DAY,)).fetchall(),
                db.execute('SELECT code,reserved_cents FROM capital WHERE day=?',(DAY,)).fetchall())


class BasketSession(requests.Session):
    def __init__(self, path, carried):
        super().__init__();self.path=path;self.carried=Decimal(str(carried));self.expected={}
        self.sent=set();self.acknowledged={};self.cancelled=set();self.reserved=self.carried;self.active=None

    def request(self, method, url, **kw):
        endpoint=url[len(BASE):] if url.startswith(BASE) else ''
        payload=kw.get('json')
        method=method.upper()
        if kw.get('allow_redirects') is not False:raise TradeError('Redirects prohibited')
        if method=='GET':
            params=kw.get('params') or {}
            if endpoint not in GetOnlySession.KEYS or set(params)!=GetOnlySession.KEYS[endpoint] or params['accId']!=ACCOUNT:
                raise TradeError('Query outside fixed simulation account')
        elif method=='POST':
            if not isinstance(payload,dict) or payload.get('accId')!=ACCOUNT:
                raise TradeError('Write outside fixed simulation account')
            code=normalize_code(str(payload.get('stkCode','')))
            if code not in self.expected:raise TradeError('Write outside approved remaining PCF targets')
            self.active=code
            if endpoint=='AguTrade/SpoOrder':
                expected=self.expected[code]
                if code in self.sent or payload.get('orderDrt')!=1 or payload.get('volume')!=expected['quantity']:
                    raise TradeError('Duplicate, sell, or wrong quantity submission blocked')
                amount=Decimal(str(payload['price']))*expected['quantity']
                reserve=(amount*Decimal('1.001')+Decimal(10)).quantize(Decimal('.01'),rounding='ROUND_CEILING')
                if amount>100000 or self.reserved+reserve>2000000:raise TradeError('Submission capital guard failed')
                self.sent.add(code);self.reserved+=reserve
            elif endpoint=='AguTrade/SpoCancel':
                oid=str(payload.get('orderId'))
                if self.acknowledged.get(oid)!=code or oid in self.cancelled:
                    raise TradeError('Unknown or duplicate cancellation blocked')
                self.cancelled.add(oid)
            else:raise TradeError('Unsupported write')
        else:raise TradeError('Unsupported HTTP method')
        event={'at':now_cn().isoformat(),'method':method,'endpoint':endpoint,'active_code':self.active}
        start=time.monotonic()
        try:
            response=super().request(method,url,**kw)
            event['http_status']=response.status_code
            obj=response.json();event['business_code']=obj.get('Code');data=obj.get('Data')
            event['data_type']=type(data).__name__
            if isinstance(data,dict):
                event['data_keys']=sorted(data)
                if endpoint=='AguTrade/SpoOrder' and str(obj.get('Code'))=='0' and data.get('orderID'):
                    oid=str(data['orderID']);self.acknowledged[oid]=self.active;event['order_id_tail']=oid[-4:]
                if isinstance(data.get('data'),list):
                    event['total_rows']=len(data['data'])
                    event['active_rows']=safe_rows([r for r in data['data'] if self.active and str(r.get('secCode'))==self.active[2:]])
            return response
        except Exception as error:
            event['error_type']=type(error).__name__;raise
        finally:
            event['elapsed_ms']=round((time.monotonic()-start)*1000,3)
            with self.path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush()


def verify_prior(broker, original, prior_rows, expected_positions):
    state=broker.snapshot()
    positions={k:v['shares'] for k,v in state['positions'].items() if v['shares']}
    if positions!=expected_positions or state['frozen']:
        raise TradeError('Account differs from verified prior PCF fills')
    orders=broker._rows('AguTrade/SpoOrders');deals=broker._rows('AguTrade/SpoDeal')
    known_ids={str(o) for c,s,o,f in prior_rows}
    today=[r for r in orders if str(r['orderDate']).replace('-','')==DAY.replace('-','')]
    if {str(r['orderId']) for r in today}!=known_ids:
        raise TradeError('Unrecognized activity in account today')
    cancels={};by_code={x['code']:x for x in original['orders']}
    for code,status,oid,filled in prior_rows:
        rows=[r for r in today if str(r['orderId'])==str(oid)]
        if len(rows)!=1:raise TradeError('Prior exact order absent or ambiguous')
        row=rows[0];planned=by_code[code]
        if (normalize_code(str(row['secCode']))!=code or int(row['drt'])!=1 or
                number(row['orderCount'],True)!=planned['quantity'] or number(row['tradeCount'],True)!=filled):
            raise TradeError('Prior order identity/fill mismatch')
        linked=[r for r in deals if str(r.get('orderId'))==str(oid)]
        if status=='CANCELLED' and str(row['status'])=='8' and filled==0 and not linked:
            cancels[str(oid)]={'code':code,'quantity':planned['quantity'],'side':1,'price':number(row['orderPrice']),'date':DAY}
        elif status=='FILLED' and str(row['status'])=='4' and filled==planned['quantity']:
            if (sum(number(x['tradeCount'],True) for x in linked)!=filled or
                    any(str(x['secCode'])!=code[2:] or int(x['drt'])!=1 for x in linked)):
                raise TradeError('Prior exact order/deal linkage mismatch')
        else:raise TradeError('Prior order has unresolved state')
    if any(str(r.get('orderId')) not in known_ids for r in deals):
        raise TradeError('Unknown existing deal')
    paid=sum(Decimal(str(r['price']))*number(r['tradeCount'],True)+sum(Decimal(str(r[k])) for k in ('qsYj','ghCost','yhs')) for r in deals)
    if abs(Decimal('10000000')-Decimal(str(state['cash']))-paid)>Decimal('.02'):
        raise TradeError('Cash does not reconcile to verified prior fills and fees')
    return cancels,state


def main():
    if sys.argv[1:]!=['--execute-user-authorized-full-basket']:
        raise TradeError('Explicit full-basket execution flag required')
    cfg=read_json(OLD/'pcf_automation.local.json')
    if now_cn().date().isoformat()!=DAY:raise TradeError('Wrong approved date')
    validate_session(now_cn(),calendar(cfg))
    if cfg['capital_limit']!=2000000 or cfg['max_order_value']!=100000 or cfg['max_price_move']!=.03 or cfg['quote_max_age_seconds']!=30:
        raise TradeError('Approved boundaries changed')
    original,old_rows,old_res=journal_read(OLD)
    tested,test_rows,test_res=journal_read(RETEST)
    if old_rows[0][1]!='CANCELLED' or any(r[1]!='FILLED' for r in test_rows):
        raise TradeError('Prior runs must be reconciled before explicitly requested completion')
    reservations=dict(old_res)
    for code,cents in test_res:
        if code in reservations and reservations[code]!=cents:raise TradeError('Prior reservation mismatch')
        reservations[code]=cents
    carried=sum(reservations.values())/100
    positions={code:filled for code,status,oid,filled in test_rows}
    manifest=read_json(OLD/'state/archive/2026-09-18/manifest.json')
    pcf=read_json(OLD/'state/archive/2026-09-18/pcf.json')
    if (manifest['execution_date']!=DAY or manifest['pcf_sha256']!=original['pcf_sha256'] or
            digest({k:pcf[k] for k in ('date','baseinfo','stocklist')})!=manifest['pcf_sha256'] or
            not '2026-09-18'<=dt.datetime.fromisoformat(manifest['captured_at']).date().isoformat()<DAY):
        raise TradeError('Original PCF/archive identity failed')
    orders=[dict(x) for x in original['orders'] if x['code'] not in positions and x['code'] not in USER_DEFERRED]
    if len(original['orders'])!=108 or len(orders)!=102:raise TradeError('Unexpected target plan')
    if any(x['side']!=1 or x['quantity']<=0 for x in orders):raise TradeError('Only original positive buy targets are authorized')
    if carried+sum(order_cash_reserve(cfg,x['quantity']*x['reference_price']*1.03) for x in orders)>2000000:
        raise TradeError('Worst permitted prices exceed aggregate capital cap')
    DEST.mkdir(exist_ok=True)
    with (DEST/'basket_attempt.json').open('x',encoding='utf-8') as f:
        json.dump({'at':now_cn().isoformat(),'authorization':'User explicitly requested full PCF build under CNY2m, then retained the 3% guard and deferred five names',
                   'account_tail':ACCOUNT[-4:],'new_target_orders':len(orders),'carried_reserved_cny':carried},f)
        f.flush();os.fsync(f.fileno())
    cfg.update(state_dir=str(DEST/'state'),live_quotes=str(DEST/'live_quotes.json'),quote_command=[],quote_provider='choice_public',
               continuation_after_explicit_user_request=True,verified_initial_positions=positions,defer_price_outside_tolerance=True)
    Path(cfg['state_dir']).mkdir(exist_ok=True)
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    previous={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    session=BasketSession(DEST/'http_trace_redacted.jsonl',carried);journal=None;broker=None
    report={'started_at':now_cn().isoformat(),'status':'preflight','account_tail':ACCOUNT[-4:],'capital_limit':2000000,
            'original_target_count':108,'already_held':positions,'planned_new_orders':102,'user_deferred':sorted(USER_DEFERRED),
            'carried_reserved_cny':carried,'pcf_date':'2026-09-18'}
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        with lock(OLD/'state/choice.lock'),lock(RETEST/'state/choice.lock'),lock(Path(cfg['state_dir'])/'choice.lock'):
            broker=ChoiceBroker(cfg['choice_contract'],True,session)
            cancelled,state=verify_prior(broker,original,old_rows+test_rows,positions)
            broker.contract['verified_zero_fill_cancellations']=cancelled
            session.expected={x['code']:x for x in orders}
            journal=Journal(Path(cfg['state_dir'])/'choice.sqlite',ACCOUNT)
            journal.reserve_capital(DAY,'PRIOR_APPROVED_ATTEMPTS',carried,2000000)
            plan=dict(original,orders=orders,initial_positions=positions)
            write_json(DEST/'plan.json',plan)
            result=execute_orders(cfg,plan,broker,journal)
            report.update(status=result['status'],result=result)
    except Exception as error:
        report.update(status='stopped_review_required',error_type=type(error).__name__)
        if isinstance(error,TradeError):report['reason']=str(error)
    finally:
        if journal is not None and broker is not None:
            try:report['audit']=final_audit(broker,journal)
            except Exception as error:report['audit_error_type']=type(error).__name__
            journal.db.close()
        report.update(finished_at=now_cn().isoformat(),submit_requests=len(session.sent),acknowledged_orders=len(session.acknowledged),
                      cancel_requests=len(session.cancelled),transport_reserved_cny=float(session.reserved))
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close()
        write_json(DEST/'basket_result.json',report)
        print(json.dumps({k:v for k,v in report.items() if k!='audit'},ensure_ascii=False,indent=2))
        if 'audit' in report:
            a=report['audit']
            print(json.dumps({'balance':a['balance'],'holdings_count':len(a['holdings']),'deal_rows':len(a['deals']),
                              'pending_count':len(a['account_pending_orders']),'journal_state_counts':{s:sum(x['state']==s for x in a['journal']) for s in sorted({x['state'] for x in a['journal']})},
                              'reserved_cny':a['cumulative_reserved_cny']},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__)
        raise SystemExit(1)
