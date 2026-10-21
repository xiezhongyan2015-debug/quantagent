"""Independent GET-only audit of the exact acknowledged retest order."""
import json
import os
import sqlite3
from decimal import Decimal
from run_retest import ACCOUNT, DAY, DEST
from verify_readonly import session_token, GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker
from quanta_agents.factor_lab_a.pcf_automation import now_cn, write_json


def main():
    if now_cn().date().isoformat()!=DAY:raise RuntimeError('Audit date mismatch')
    with sqlite3.connect((DEST/'state/choice.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        entries=db.execute('SELECT code,oid,filled FROM orders WHERE day=?',(DAY,)).fetchall()
    if len(entries)!=1:raise RuntimeError('Expected exactly one retest order')
    code,oid,filled=entries[0]
    session=GetOnlySession(ACCOUNT)
    old={k:os.environ.get(k) for k in ('CHOICE_ACCOUNT_ID','CHOICE_JTOKEN')}
    try:
        os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        os.environ['CHOICE_JTOKEN']=session_token()
        b=ChoiceBroker({'initial_buy_only':True},False,session)
        orders=b._rows('AguTrade/SpoOrders');holds=b._rows('AguTrade/SpoHold');deals=b._rows('AguTrade/SpoDeal')
        balance=b._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data']
        matches=[x for x in orders if str(x['orderId'])==str(oid)]
        linked=[x for x in deals if str(x['orderId'])==str(oid)]
        inventory=[x for x in holds if str(x['secCode'])==code[2:]]
        if len(matches)!=1 or len(inventory)!=1:raise RuntimeError('Exact identity/inventory missing')
        r=matches[0]
        confirmed=(str(r['status'])=='4' and r['secCode']==code[2:] and int(r['drt'])==1 and
                   int(r['orderCount'])==int(r['tradeCount'])==filled==sum(int(x['tradeCount']) for x in linked)==int(inventory[0]['count']) and
                   all(str(x['secCode'])==code[2:] and int(x['drt'])==1 for x in linked))
        result={'checked_at':now_cn().isoformat(),'account_tail':ACCOUNT[-4:],'code':code,
                'order_id_tail':str(oid)[-4:],'independently_confirmed_full_fill':confirmed,
                'quantity':filled,'limit_price':r['orderPrice'],'order_trade_price':r['tradePrice'],
                'order_time':r['orderTime'],'holdings':inventory[0]['count'],'sellable_observed':inventory[0].get('availCount'),
                'linked_deals':[{k:x.get(k) for k in ('secCode','drt','tradeCount','price','qsYj','ghCost','yhs','orderDate','orderTime')} for x in linked],
                'balance':{k:balance[k] for k in ('totalAssets','availBalance','frozenMoney','totalPosValue')},
                'pending_orders_count':sum(str(x['status']) not in ('4','7','8','9') for x in orders),
                'cash_paid_including_actual_charges':str(Decimal('10000000')-Decimal(balance['availBalance'])-Decimal(balance['frozenMoney'])),
                'new_orders':0,'cancellations':0,'network_audit':session.audit}
        write_json(DEST/'independent_readonly_audit.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('GET-only audit failed:',type(error).__name__)
        raise SystemExit(1)
