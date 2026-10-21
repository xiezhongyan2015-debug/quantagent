"""Cancel-only cleanup of this batch's exact known pending simulation IDs.

Server occasionally reports an accepted buy limit one cent below the request.
This helper records both values and validates the current accepted limit twice.
It never submits a buy, guesses an ID, raises a price, or retries a cancellation.
"""
import json
import os
import sqlite3
from decimal import Decimal
from run_queued_basket import DEST, ACCOUNT, DAY
from run_full_basket import BasketSession
from verify_readonly import session_tokens
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,TradeError
from quanta_agents.factor_lab_a.pcf_automation import read_json,write_json,now_cn


class CancelOnly(BasketSession):
    def request(self,method,url,**kw):
        if method.upper()=='POST' and not url.endswith('/AguTrade/SpoCancel'):
            raise TradeError('This helper cannot submit orders')
        return super().request(method,url,**kw)


def main():
    if now_cn().date().isoformat()!=DAY:raise TradeError('Wrong day')
    with sqlite3.connect((DEST/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        known={str(oid):code for code,oid in db.execute('SELECT code,oid FROM orders WHERE oid IS NOT NULL')}
    intents=read_json(DEST/'order_intents.json')
    sent_codes={e['active_code'] for line in (DEST/'http_trace_redacted.jsonl').read_text(encoding='utf-8').splitlines()
                if (e:=json.loads(line)).get('method')=='POST' and e.get('endpoint')=='AguTrade/SpoCancel'}
    with (DEST/'cleanup_attempt.json').open('x',encoding='utf-8') as f:
        json.dump({'at':now_cn().isoformat(),'policy':'Known pending orders only; no new submissions; accepted limit revalidated'},f)
        f.flush();os.fsync(f.fileno())
    s=CancelOnly(DEST/'cleanup_trace_redacted.jsonl',0)
    s.expected=intents;s.acknowledged=known
    keys={'JToken':'CHOICE_JTOKEN','utoken':'CHOICE_UTOKEN','ctoken':'CHOICE_CTOKEN','UID':'CHOICE_UID'}
    old={k:os.environ.get(k) for k in [*keys.values(),'CHOICE_ACCOUNT_ID']}
    report={'started_at':now_cn().isoformat(),'account_tail':ACCOUNT[-4:],'actions':[],'new_orders':0}
    try:
        tokens=session_tokens(tuple(keys))
        for k,v in keys.items():os.environ[v]=tokens[k]
        tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        b=ChoiceBroker({'initial_buy_only':True,'validated_submission_with_authorized_test':True,
                        'validated_cancel_with_authorized_test':True},True,s)
        rows=b._rows('AguTrade/SpoOrders')
        for row in rows:
            oid=str(row['orderId']);code=known.get(oid)
            if code is None or str(row['status']) in ('4','7','8','9'):continue
            order=intents[code]
            if (row['secCode']!=code[2:] or int(row['drt'])!=1 or int(row['orderCount'])!=order['quantity'] or
                    row['orderDate']!=DAY):raise TradeError('Known order identity mismatch; do not touch')
            difference=Decimal(str(order['price']))-Decimal(str(row['orderPrice']))
            if difference not in (Decimal(0),Decimal('.01')):raise TradeError('Unexplained accepted price difference')
            if code in sent_codes:
                report['actions'].append({'code':code,'order_id_tail':oid[-4:],'action':'query_only_prior_cancel_already_sent'})
                continue
            accepted=dict(order,price=float(row['orderPrice']))
            # cancel() independently re-queries this exact ID and checks accepted price.
            response=b.cancel(oid,accepted)
            report['actions'].append({'code':code,'order_id_tail':oid[-4:],'requested_limit':order['price'],
                                      'observed_accepted_limit':accepted['price'],'cancellation':response})
        after=b._rows('AguTrade/SpoOrders')
        report['known_pending_after_request']=[{'code':known[str(r['orderId'])],'order_id_tail':str(r['orderId'])[-4:],'state':r['status']}
            for r in after if str(r['orderId']) in known and str(r['status']) not in ('4','7','8','9')]
    except Exception as error:
        report['error_type']=type(error).__name__
        if isinstance(error,TradeError):report['reason']=str(error)
    finally:
        report.update(finished_at=now_cn().isoformat(),cancel_requests=len(s.cancelled))
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        s.headers.clear();s.close();write_json(DEST/'cleanup_result.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,TradeError) else type(error).__name__);raise SystemExit(1)
