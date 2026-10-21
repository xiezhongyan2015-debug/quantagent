"""Validate the new reconciler against current real simulation receipts; GET only."""
import json
import os
import time
from console_core import legacy_state,reconcile,HOME
from console_runtime import latest_session,ACCOUNT,account_snapshot
from choice_auth import ChoiceAuthenticator
from verify_readonly import GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,SessionExpired
from quanta_agents.factor_lab_a.pcf_automation import read_json,now_cn
from watch_engine import save


def main():
    source=latest_session();state=legacy_state(read_json(source/'private_state.json'))
    auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT);old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')}
    try:
        tokens=auth.current_tokens();os.environ['CHOICE_JTOKEN']=tokens.pop('JToken');tokens.clear();os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker({'initial_buy_only':True},False,http)
        def snapshot():
            return account_snapshot(broker,state)
        try:raw=snapshot()
        except SessionExpired:
            tokens=auth.login();http.headers['token']=tokens.pop('JToken');tokens.clear();raw=snapshot()
        observed={str(r['orderId']) for r in raw['orders']};expected={a['oid'] for a in state['attempts']}
        if expected-observed:
            from collections import Counter
            diagnosis=dict(source_day=state['day'],current_time=now_cn().isoformat(),order_dates=dict(Counter(str(r['orderDate']) for r in raw['orders'])),
                returned_orders=len(raw['orders']),missing_known_orders=len(expected-observed),extra_orders=len(observed-expected))
            for endpoint in ('AguTrade/SpoOrders','AguTrade/SpoDeal'):
                data=broker._request(endpoint,params=dict(accId=ACCOUNT,dataRange=2,pageNo=0,pageSize=1000))['Data']['data']
                diagnosis[endpoint]=dict(rows=len(data),dates=dict(Counter(str(r.get('orderDate')) for r in data)),known_matches=len(expected & {str(r['orderId']) for r in data}))
            print(json.dumps(diagnosis,ensure_ascii=False,indent=2))
        for i in range(2):
            if i:time.sleep(5);raw=snapshot()
            a=reconcile(raw,state)
        output=dict(checked_at=now_cn().isoformat(),source_session=source.name,passes=2,orders_submitted=0,cancellations=0,
            known_orders=len(state['attempts']),held_securities=len(a['positions']),pending=len(a['pending']),frozen=a['frozen'],
            cash_after_quarantine=a['cash'],excluded_cash_cny='395.66',stock_fraction=a['stock_fraction'],issues=[])
        save(HOME/'readonly_validation.json',output);print(json.dumps(output,ensure_ascii=False,indent=2))
    finally:
        http.headers.clear();http.close();auth.close()
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


if __name__=='__main__':main()
