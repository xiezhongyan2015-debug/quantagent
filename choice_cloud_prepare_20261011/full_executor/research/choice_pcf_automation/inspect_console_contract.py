import os,json
from choice_auth import ChoiceAuthenticator
from run_intraday_watch import ACCOUNT,Adapter
from verify_readonly import GetOnlySession
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,SessionExpired
auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT)
try:
    tokens=auth.current_tokens();os.environ['CHOICE_JTOKEN']=tokens['JToken'];os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
    broker=ChoiceBroker({'initial_buy_only':True},False,http)
    try:raw=Adapter(broker,http).read()
    except SessionExpired:
        tokens=auth.login();http.headers['token']=tokens['JToken'];raw=Adapter(broker,http).read()
    rows=raw['holds']
    fields={k: type(v).__name__ for k,v in rows[0].items()} if rows else {}
    print(json.dumps({'holding_fields':fields,'numeric_examples':[{k:v for k,v in r.items() if any(s in k.lower() for s in ('count','avail','sell','usable','frozen'))} for r in rows[:3]], 'order_fields':list(raw['orders'][0]) if raw['orders'] else [], 'deal_fields':list(raw['deals'][0]) if raw['deals'] else []},ensure_ascii=False))
finally:
    http.headers.clear();http.close();auth.close()
