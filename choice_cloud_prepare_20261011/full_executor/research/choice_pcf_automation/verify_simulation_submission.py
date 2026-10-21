"""Private, one-shot, explicitly authorized Choice CONTEST simulation probe.

Uses the production ChoiceBroker.submit function with an independently bounded
transport; does not falsify the production validated-contract setting. The PCF
daemon and configuration remain disabled. Never imports credentials at import time.
No credentials or complete account/order identifiers are written to the report.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

import requests

from verify_readonly import session_tokens, GetOnlySession, ROOT
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker, BASE, TradeError, number
from quanta_agents.factor_lab_a.pcf_automation import write_json

ACCOUNT = __import__('os').environ['CHOICE_SIMULATION_ACCOUNT']  # Previously observed contest SIMULATION account only.
DAY = '2026-09-20'
CN = dt.timezone(dt.timedelta(hours=8))
ORDER = {'code':'SH600000','side':1,'quantity':100,'price':8.15}
OUTPUT = ROOT / 'research/choice_pcf_automation/submission_verification_20260920.json'
INTENT = OUTPUT.with_suffix('.attempt.json')


def today():
    return dt.datetime.now(CN).date().isoformat()


def response_order_path(obj):
    """Recognize explicit ID field or a scalar Data ID; never match old orders."""
    data = obj.get('Data')
    path = 'Data.orderID' if isinstance(data,dict) and 'orderID' in data else 'Data'
    value = data.get('orderID') if path.endswith('.orderID') else data
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not re.fullmatch(r'[0-9]{12,30}',str(value)):
        raise TradeError('Unrecognized order-ID response; no resubmission')
    return path, str(value)


class BoundedSession(requests.Session):
    def __init__(self, credentials):
        super().__init__()
        self.credentials = credentials
        self.headers.update(token=credentials['JToken'])
        self.contract = {}
        self.oid = None
        self.verified_oid = None
        self.submit_calls = 0
        self.cancel_calls = 0
        self.audit = []

    def request(self, method, url, **kwargs):
        endpoint = url[len(BASE):] if url.startswith(BASE) else ''
        if kwargs.get('allow_redirects') is not False or kwargs.get('data') is not None:
            raise TradeError('Transport restriction')
        if method.upper() == 'GET':
            params = kwargs.get('params',{})
            if (endpoint not in GetOnlySession.KEYS or set(params) != GetOnlySession.KEYS[endpoint]
                    or params.get('accId') != ACCOUNT or kwargs.get('json') is not None):
                raise TradeError('Query outside probe scope')
        elif method.upper() == 'POST':
            if today()!=DAY or dt.date.fromisoformat(DAY).weekday()!=6 or kwargs.get('params'):
                raise TradeError('Probe expired or invalid write')
            expected = dict(accId=ACCOUNT,mktCode='1',stkCode='600000',
                uToken=self.credentials['utoken'],cToken=self.credentials['ctoken'],uid=self.credentials['UID'])
            if endpoint == 'AguTrade/SpoOrder':
                expected.update(price='8.15',volume=100,orderDrt=1,orderType=1,stkType=1)
                if self.submit_calls or INTENT.exists() or kwargs.get('json') != expected:
                    raise TradeError('Single-order restriction')
                # Durable, exclusive intent before network; never remove it for retry.
                with INTENT.open('x',encoding='utf-8') as f:
                    json.dump({'state':'SENDING','account_tail':ACCOUNT[-4:],'order':ORDER,
                               'created_at':dt.datetime.now(CN).isoformat()},f)
                    f.flush(); os.fsync(f.fileno())
                self.submit_calls += 1
            elif endpoint == 'AguTrade/SpoCancel':
                expected['orderId'] = self.verified_oid
                if (not self.oid or self.verified_oid != self.oid or self.cancel_calls or
                        kwargs.get('json') != expected):
                    raise TradeError('Only this probe verified order may be cancelled once')
                self.cancel_calls += 1
            else:
                raise TradeError('Write outside probe scope')
        else:
            raise TradeError('Unsupported method')
        start = time.perf_counter()
        item = {'method':method.upper(),'endpoint':endpoint,'outcome':'unknown'}
        self.audit.append(item)
        try:
            response = super().request(method,url,**kwargs)
            item.update(http_status=response.status_code,elapsed_ms=round((time.perf_counter()-start)*1000,3))
            obj = response.json()
            item['business_code'] = str(obj.get('Code')) if isinstance(obj,dict) else 'invalid'
            item['outcome'] = 'response_received'
            if endpoint=='AguTrade/SpoOrder' and str(obj.get('Code'))=='0':
                # Determine mapping from this exact POST response, before submit reads it.
                path, self.oid = response_order_path(obj)
                self.contract['order_id'] = path
            return response
        except Exception:
            item['elapsed_ms'] = round((time.perf_counter()-start)*1000,3)
            raise


class Probe:
    """Uses actual submit/query implementation; separate single-order transport gate."""
    submit = ChoiceBroker.submit
    _rows = ChoiceBroker._rows
    order_status = ChoiceBroker.order_status

    def __init__(self, session):
        self.session, self.account, self.contract = session, ACCOUNT, session.contract

    def _request(self,path,params=None,payload=None):
        response = (self.session.get(BASE+path,params=params,timeout=15,allow_redirects=False)
                    if payload is None else self.session.post(BASE+path,json=payload,timeout=15,allow_redirects=False))
        if response.status_code!=200:
            raise TradeError('Unexpected HTTP result')
        obj = response.json()
        if not isinstance(obj,dict) or str(obj.get('Code'))!='0' or 'Data' not in obj:
            raise TradeError('Response not confirmed successful')
        return obj

    def cancel_verified(self,oid):
        state=self.order_status(oid,ORDER)
        if state['state'] in ('4','7','8','9','5','6'):
            return {'request_sent':False,'state':state}
        if state['state']=='10':
            raise TradeError('Existing cancellation failure')
        self.session.verified_oid=oid
        return self._request('AguTrade/SpoCancel',payload=dict(accId=ACCOUNT,mktCode='1',stkCode='600000',
            orderId=oid,uToken=self.session.credentials['utoken'],cToken=self.session.credentials['ctoken'],
            uid=self.session.credentials['UID']))


def account_state(probe):
    orders=probe._rows('AguTrade/SpoOrders')
    holds=probe._rows('AguTrade/SpoHold')
    deals=probe._rows('AguTrade/SpoDeal')
    balance=probe._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data']
    return orders,holds,deals,balance


def main():
    if sys.argv[1:] != ['--authorized-contest-simulation'] or today()!=DAY or INTENT.exists():
        raise TradeError('Explicit one-shot invocation missing, expired, or already attempted')
    report={'checked_at':dt.datetime.now(CN).isoformat(),'account_tail':ACCOUNT[-4:],
            'order':ORDER,'submission_verified':False,'cancellation_verified':False,
            'fill_linkage_verified':False,'sellable_field_verified':False,
            'production_execution_enabled':False,'status':'preflight'}
    envmap={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    previous={v:os.environ.get(v) for v in envmap.values()}
    session=None
    try:
        credentials=session_tokens(tuple(envmap))
        for key,env in envmap.items(): os.environ[env]=credentials[key]
        session=BoundedSession(credentials)
        probe=Probe(session)
        orders,holds,deals,balance=account_state(probe)
        if holds or deals or number(balance['frozenMoney'])!=0 or number(balance['availBalance'])<815*1.001:
            raise TradeError('Preflight account not empty/unfrozen/funded')
        if any(str(x['status']) not in ('4','7','8','9') for x in orders):
            raise TradeError('Preflight has outstanding orders')
        oldids={str(x['orderId']) for x in orders}
        report['before']={'orders':len(orders),'holdings':len(holds),'deals':len(deals),
                          'available':number(balance['availBalance']),'frozen':number(balance['frozenMoney'])}
        report['status']='submitting'
        write_json(OUTPUT,report)
        oid=probe.submit(ORDER.copy())
        if oid in oldids:
            raise TradeError('POST returned a preexisting order ID')
        report.update(order_id_tail=oid[-4:],order_id_sha256=hashlib.sha256(oid.encode()).hexdigest(),
                      submission_response_path=probe.contract['order_id'])
        status=None
        for attempt in range(6):
            try:
                status=probe.order_status(oid,ORDER)
                break
            except (TradeError,requests.RequestException):
                if attempt==5: raise
                time.sleep(1)
        report.update(submission_verified=True,initial_order_state=status,status='cancelling')
        write_json(OUTPUT,report)
        try:
            probe.cancel_verified(oid)
            report['cancel_response_success']=True
        except Exception:
            report['cancel_response_success']=False
        # No additional POST, even if acknowledgement was lost or malformed.
        for attempt in range(16):
            status=probe.order_status(oid,ORDER)
            report['final_order_state']=status
            if status['state'] in ('4','7','8','9'):
                break
            if attempt<15: time.sleep(1)
        orders,holds,deals,balance=account_state(probe)
        report['after']={'orders':len(orders),'holdings':len(holds),'deals':len(deals),
                        'available':number(balance['availBalance']),'frozen':number(balance['frozenMoney'])}
        report['cancellation_verified']=(status['state']=='8' and status['filled']==0 and
            not holds and not deals and number(balance['frozenMoney'])==0 and
            number(balance['availBalance'])==report['before']['available'])
        report['status']='submitted_and_cancelled' if report['cancellation_verified'] else 'review_required'
    except Exception as error:
        report.update(status='review_required' if session and session.submit_calls else 'preflight_failed',
                      error_type=type(error).__name__)
    finally:
        if session:
            report.update(network_audit=session.audit,new_orders_submitted=session.submit_calls,
                          cancellation_requests=session.cancel_calls)
            session.headers.clear(); session.credentials.clear(); session.close()
        for env,value in previous.items():
            if value is None: os.environ.pop(env,None)
            else: os.environ[env]=value
        write_json(OUTPUT,report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('Probe refused:',type(error).__name__)
        raise SystemExit(1)
