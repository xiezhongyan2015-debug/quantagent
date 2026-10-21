"""Cancel-only recovery for the single observed test order. NO submission endpoint.

The sole new ID is recovered from the before/after account inventory, then checked
against the independently observed exact time, date, instrument, side, size and
price. This is a one-off reviewed cleanup, not an automatic order-ID guessing rule.
"""
import datetime as dt
import json
import os
import time

import requests

from verify_readonly import session_tokens, GetOnlySession
from verify_simulation_submission import (ACCOUNT, BASE, DAY, CN, ORDER, OUTPUT, Probe,
    account_state, today, TradeError, number, write_json)

PRIOR_ID='REDACTED_PRIVATE_ID'
CANCEL_INTENT=OUTPUT.with_suffix('.cancel_attempt.json')


class CancelOnlySession(GetOnlySession):
    def __init__(self,credentials):
        super().__init__(ACCOUNT)
        self.headers.update(token=credentials['JToken'])
        self.credentials=credentials
        self.contract={}
        self.approved_id=None
        self.cancel_calls=0

    def request(self,method,url,**kwargs):
        if method.upper()=='GET': return super().request(method,url,**kwargs)
        expected=dict(accId=ACCOUNT,mktCode='1',stkCode='600000',orderId=self.approved_id,
            uToken=self.credentials['utoken'],cToken=self.credentials['ctoken'],uid=self.credentials['UID'])
        if (method.upper()!='POST' or url!=BASE+'AguTrade/SpoCancel' or
                not self.approved_id or kwargs.get('json')!=expected or
                kwargs.get('allow_redirects') is not False or kwargs.get('params') or
                kwargs.get('data') is not None or today()!=DAY or
                self.cancel_calls or CANCEL_INTENT.exists()):
            raise TradeError('Cancellation outside the approved single cleanup')
        with CANCEL_INTENT.open('x',encoding='utf-8') as f:
            json.dump({'order_id_tail':self.approved_id[-4:],'state':'CANCEL_SENDING'},f)
            f.flush(); os.fsync(f.fileno())
        self.cancel_calls+=1
        item={'method':'POST','endpoint':'AguTrade/SpoCancel','outcome':'unknown'}
        self.audit.append(item)
        start=time.perf_counter()
        response=requests.Session.request(self,method,url,**kwargs)
        obj=response.json()
        item.update(http_status=response.status_code,business_code=str(obj.get('Code')),
                    elapsed_ms=round((time.perf_counter()-start)*1000,3),outcome='response_received')
        return response


def main():
    if today()!=DAY or CANCEL_INTENT.exists():
        raise TradeError('Cleanup expired or already attempted; query only')
    report=json.loads(OUTPUT.read_text(encoding='utf-8'))
    if report['new_orders_submitted']!=1 or report['cancellation_requests']!=0:
        raise TradeError('No matching single submission incident')
    credentials=session_tokens(('JToken','utoken','ctoken','UID'))
    session=CancelOnlySession(credentials)
    try:
        probe=Probe(session)
        orders,holds,deals,balance=account_state(probe)
        prior=[x for x in orders if str(x['orderId'])==PRIOR_ID]
        new=[x for x in orders if str(x['orderId'])!=PRIOR_ID]
        if len(orders)!=2 or len(prior)!=1 or str(prior[0]['status'])!='8' or len(new)!=1 or holds or deals:
            raise TradeError('Account inventory changed; cannot identify cleanup order')
        row=new[0]; oid=str(row['orderId'])
        if (not oid.endswith('4009') or row['orderDate']!=DAY or row['orderTime']!='20:33:29' or
                row['secCode']!='600000' or number(row['orderCount'],True)!=100 or
                number(row['orderPrice'])!=8.15 or int(row['drt'])!=1 or
                int(row['orderType'])!=1 or number(row['tradeCount'])!=0):
            raise TradeError('Independent observed order identity mismatch')
        status=probe.order_status(oid,ORDER)
        report.update(server_order_accepted_and_queried=True,order_id_tail=oid[-4:],
                      submit_function_returned_order_id=False,
                      id_recovery='Single new ID versus known preflight ID, exact independently observed order; reviewed cleanup only')
        if status['state'] in ('1','2','3'):
            session.approved_id=oid
            try:
                probe._request('AguTrade/SpoCancel',payload=dict(accId=ACCOUNT,mktCode='1',stkCode='600000',
                    orderId=oid,uToken=credentials['utoken'],cToken=credentials['ctoken'],uid=credentials['UID']))
            except Exception:
                report['cancel_response_uncertain']=True
        for attempt in range(16):
            status=probe.order_status(oid,ORDER)
            if status['state'] in ('4','7','8','9'): break
            if attempt<15: time.sleep(1)
        orders,holds,deals,balance=account_state(probe)
        report['after']={'orders':len(orders),'holdings':len(holds),'deals':len(deals),
                        'available':number(balance['availBalance']),'frozen':number(balance['frozenMoney'])}
        report['final_order_state']=status
        report['cancellation_verified']=(status['state']=='8' and status['filled']==0 and not holds and not deals
            and number(balance['frozenMoney'])==0 and number(balance['availBalance'])==report['before']['available'])
        report['status']='submitted_and_cancelled_with_receipt_mapping_issue' if report['cancellation_verified'] else 'review_required'
    except Exception as error:
        report.update(status='review_required',cleanup_error_type=type(error).__name__)
    finally:
        report['cleanup_checked_at']=dt.datetime.now(CN).isoformat()
        report['cleanup_audit']=session.audit
        report['cancellation_requests']=session.cancel_calls
        write_json(OUTPUT,report)
        session.headers.clear(); session.credentials.clear(); session.close()
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
