"""Separate GET-only verification of the watcher's exact order IDs and account."""
import json
import os
from decimal import Decimal
from run_intraday_watch import DEST as TEST_DEST,ROOT,ACCOUNT,DAY
from verify_readonly import GetOnlySession,session_token
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker
from quanta_agents.factor_lab_a.pcf_automation import now_cn,read_json
from watch_engine import reconcile,save


def main():
    dest=ROOT/'research/choice_pcf_automation/until_close_20260921'
    if not (dest/'private_state.json').exists():dest=TEST_DEST
    state=read_json(dest/'private_state.json')
    if now_cn().date().isoformat()!=DAY:raise RuntimeError('Wrong audit day')
    old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')}
    session=GetOnlySession(ACCOUNT)
    try:
        os.environ['CHOICE_JTOKEN']=session_token();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        b=ChoiceBroker({'initial_buy_only':True},False,session)
        raw=dict(orders=b._rows('AguTrade/SpoOrders'),deals=b._rows('AguTrade/SpoDeal'),holds=b._rows('AguTrade/SpoHold'),
                 balance=b._request('AguTrade/SpoBalInfo',params={'accId':ACCOUNT})['Data'])
        a=reconcile(raw,state['prior'],state['attempts'],state['targets'],DAY)
        watch_orders=[]
        for x in state['attempts']:
            linked=[r for r in raw['deals'] if str(r.get('orderId'))==x['oid']]
            amount=sum(Decimal(str(r['price']))*int(r['tradeCount']) for r in linked)
            fees=sum(sum(Decimal(str(r[k])) for k in ('qsYj','ghCost','yhs')) for r in linked)
            filled=a['states'][x['oid']]['filled']
            watch_orders.append(dict(code=x['order']['code'],order_id_tail=x['oid'][-4:],limit=x['order']['price'],
                                     **a['states'][x['oid']],principal_cny=str(amount),fees_cny=str(fees),
                                     average_fill_price=float(amount/filled) if filled else None))
        missing=[dict(code=c,target_shares=o['quantity'],held_shares=a['positions'].get(c,0),pending_shares=a['pending'].get(c,0),missing_shares=o['quantity']-a['positions'].get(c,0),reason=state['deferred'].get(c)) for c,o in state['targets'].items() if a['positions'].get(c,0)<o['quantity']]
        result=dict(checked_at=now_cn().isoformat(),account_tail=ACCOUNT[-4:],new_orders=0,cancellations=0,issues=[],
                    target_securities=len(state['targets']),held_securities=len(a['positions']),fully_held_targets=len(state['targets'])-len(missing),
                    pending_count=sum(not x['terminal'] for x in a['states'].values()),frozen_cny=a['frozen'],actual_paid_cny=a['paid'],actual_fees_cny=a['fees'],
                    remaining_targets=missing,watch_orders=watch_orders,
                    verification='Fresh GET-only order/deal/holding/cash reconciliation against exact saved IDs',inventory=a['positions'])
        save(dest/'independent_readonly_audit.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='inventory'},ensure_ascii=False,indent=2))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        session.headers.clear();session.close()

if __name__=='__main__':
    try:main()
    except Exception as e:print('Read-only watch audit failed:',type(e).__name__);raise SystemExit(1)
