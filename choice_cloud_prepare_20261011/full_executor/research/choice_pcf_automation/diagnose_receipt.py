"""One further 815-CNY contest simulation probe authorized by the repair request.
Captures structure only, learns the receipt path from an ID present in the actual
response and independently queried new order, then cancels only that test order.
"""
import datetime as dt
import json
import os
import re
import time

import verify_simulation_submission as p
from verify_readonly import session_tokens

OUT=p.ROOT/'research/choice_pcf_automation/receipt_diagnosis_20260920.json'


def leaves(obj,path=''):
    if isinstance(obj,dict):
        for key,value in obj.items(): yield from leaves(value,path+'.'+key if path else key)
    elif isinstance(obj,list):
        for i,value in enumerate(obj): yield from leaves(value,path+'.'+str(i))
    else: yield path,obj


def shape(obj):
    if isinstance(obj,dict): return {k:shape(v) for k,v in obj.items()}
    if isinstance(obj,list): return [shape(x) for x in obj[:3]]
    return type(obj).__name__


def main():
    p.INTENT=OUT.with_suffix('.attempt.json')
    if p.today()!=p.DAY or p.INTENT.exists(): raise p.TradeError('Expired or already attempted')
    receipt={}
    def capture(obj):
        receipt.update(obj)
        return 'Data','UNRESOLVED'
    p.response_order_path=capture
    cred=session_tokens(('JToken','utoken','ctoken','UID'))
    session=p.BoundedSession(cred)
    probe=p.Probe(session)
    report={'checked_at':dt.datetime.now(p.CN).isoformat(),'kind':'authorized repair diagnosis',
            'order':p.ORDER,'account_tail':p.ACCOUNT[-4:],'new_orders_submitted':0}
    try:
        orders,holds,deals,balance=p.account_state(probe)
        if holds or deals or p.number(balance['frozenMoney']) or any(str(x['status']) not in ('4','7','8','9') for x in orders):
            raise p.TradeError('Unexpected account activity')
        before={str(x['orderId']) for x in orders}
        payload=p.ChoiceBroker.submit.__globals__['order_payload'](p.ACCOUNT,**{
            'code':'600000','side':1,'quantity':100,'price':8.15})
        payload.update(uToken=cred['utoken'],cToken=cred['ctoken'],uid=cred['UID'])
        probe._request('AguTrade/SpoOrder',payload=payload)
        report['response_schema']=shape(receipt)
        new=[x for x in probe._rows('AguTrade/SpoOrders') if str(x['orderId']) not in before]
        if len(new)!=1: raise p.TradeError('Ambiguous new order')
        oid=str(new[0]['orderId'])
        state=probe.order_status(oid,p.ORDER)
        matches=[path for path,value in leaves(receipt) if isinstance(value,(str,int)) and str(value)==oid]
        embedded=[path for path,value in leaves(receipt) if isinstance(value,str) and oid in value and str(value)!=oid]
        report.update(order_id_tail=oid[-4:],literal_id_paths=matches,embedded_id_paths=embedded,
                      initial_state=state,response_code=str(receipt.get('Code')))
        p.write_json(OUT,report)
        session.oid=oid
        try: probe.cancel_verified(oid)
        except Exception as error: report['cancel_response_error']=type(error).__name__
        for i in range(15):
            state=probe.order_status(oid,p.ORDER)
            if state['state'] in ('4','7','8','9'): break
            time.sleep(1)
        orders,holds,deals,balance=p.account_state(probe)
        report.update(final_state=state,cancellation_verified=state=={'state':'8','filled':0},
            final_holdings=len(holds),final_deals=len(deals),final_frozen=p.number(balance['frozenMoney']),
            final_available=p.number(balance['availBalance']))
        # Save the exact successful response as types plus redacted ID for fixtures.
        def redacted(obj):
            if isinstance(obj,dict): return {k:redacted(v) for k,v in obj.items()}
            if isinstance(obj,list): return [redacted(v) for v in obj]
            if str(obj)==oid: return 'REDACTED_PRIVATE_ID'
            if isinstance(obj,str):
                return '<text>REDACTED_PRIVATE_ID<text>' if oid in obj else '<redacted:string>'
            if obj is None or isinstance(obj,bool): return obj
            return 0
        # Emit only the narrowly relevant response, with every other long numeric leaf suppressed.
        safe=redacted(receipt)
        report['receipt_fixture']=safe
    except Exception as error:
        report['error_type']=type(error).__name__
        if receipt: report['response_schema']=shape(receipt)
    finally:
        report.update(network_audit=session.audit,new_orders_submitted=session.submit_calls,cancellation_requests=session.cancel_calls)
        p.write_json(OUT,report)
        session.headers.clear(); session.credentials.clear(); session.close()
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
