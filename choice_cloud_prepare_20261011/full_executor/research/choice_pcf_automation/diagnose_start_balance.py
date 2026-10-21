"""Read-only evidence for startup cash mismatch; no baseline/journal mutation."""
import os,json
from decimal import Decimal
from collections import Counter
from choice_auth import ChoiceAuthenticator
from verify_readonly import GetOnlySession
from run_intraday_watch import ACCOUNT,Adapter
from run_session_20260922 import initial_state,DEST
from quanta_agents.factor_lab_a.choice_pcf import ChoiceBroker,SessionExpired
from quanta_agents.factor_lab_a.pcf import normalize_code
from quanta_agents.factor_lab_a.pcf_automation import now_cn
from watch_engine import save

def main():
    state=initial_state();auth=ChoiceAuthenticator();http=GetOnlySession(ACCOUNT)
    old={k:os.environ.get(k) for k in ('CHOICE_JTOKEN','CHOICE_ACCOUNT_ID')};tokens=None
    try:
        tokens=auth.current_tokens();os.environ['CHOICE_JTOKEN']=tokens['JToken'];os.environ['CHOICE_ACCOUNT_ID']=ACCOUNT
        broker=ChoiceBroker({'initial_buy_only':True},False,http)
        try:raw=Adapter(broker,http).read()
        except SessionExpired:
            tokens=auth.login();http.headers['token']=tokens['JToken'];raw=Adapter(broker,http).read()
        b=raw['balance'];base=state['baseline']
        positions={normalize_code(str(x['secCode'])):int(x['count']) for x in raw['holds'] if int(x['count'])}
        delta={c:positions.get(c,0)-base['positions'].get(c,0) for c in set(positions)|set(base['positions']) if positions.get(c,0)!=base['positions'].get(c,0)}
        unknown=[x for x in raw['orders'] if str(x['orderId']) not in state['historical'] and str(x['orderDate']).replace('-','')=='20260922']
        deals=[x for x in raw['deals'] if str(x['orderId']) not in state['historical']]
        report=dict(checked_at=now_cn().isoformat(),account_tail=ACCOUNT[-4:],orders_submitted=0,cancellations=0,
             baseline_cash=base['cash'],baseline_paid=base['paid'],current_cash=b['availBalance'],current_frozen=b['frozenMoney'],
             cash_delta=str(Decimal(str(b['availBalance']))-Decimal(str(base['cash']))),held_securities=len(positions),position_quantity_differences=delta,
             observed_order_count=len(raw['orders']),order_date_counts=dict(Counter(str(x['orderDate']) for x in raw['orders'])),
             todays_unrecognized_orders=len(unknown),nonhistorical_deals=len(deals),
             known_historical_fees=str(sum((sum(Decimal(str(x.get(k,0))) for k in ('qsYj','ghCost','yhs')) for x in raw['deals'] if str(x['orderId']) in state['historical']),Decimal(0))),
             balance_numeric_fields={k:v for k,v in b.items() if isinstance(v,(int,float)) and not any(z in k.lower() for z in ('id','phone','account','user'))})
        # Official client index.js uses dataRange=2 for historical fills.
        obj=broker._request('AguTrade/SpoDeal',params={'accId':ACCOUNT,'dataRange':2,'pageNo':0,'pageSize':1000})
        hist=obj['Data']['data']
        principal=sum((Decimal(str(x['price']))*Decimal(str(x['tradeCount'])) for x in hist),Decimal(0))
        fees=sum((sum(Decimal(str(x.get(k,0))) for k in ('qsYj','ghCost','yhs')) for x in hist),Decimal(0))
        extra=[x for x in hist if str(x['orderId']) not in state['historical']]
        differences=[]
        for oid,o in state['historical'].items():
            linked=[x for x in hist if str(x['orderId'])==oid]
            quantity=sum(int(x['tradeCount']) for x in linked)
            if quantity!=o['filled']:differences.append({'code':o['code'],'expected':o['filled'],'observed':quantity})
        report['historical_settlement']=dict(row_count=len(hist),principal=str(principal),fees=str(fees),
              paid=str(principal+fees),implied_cash=str(Decimal('10000000')-principal-fees),
              unexplained_cash_delta=str(Decimal(str(b['availBalance']))+Decimal(str(b['frozenMoney']))-(Decimal('10000000')-principal-fees)),
              unrecognized_deals=len(extra),quantity_differences=differences,
              fee_components={k:str(sum((Decimal(str(x.get(k,0))) for x in hist),Decimal(0))) for k in ('qsYj','ghCost','yhs')})
        save(DEST/'startup_cash_diagnostic_redacted.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        for k,v in old.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        if tokens:tokens.clear()
        http.headers.clear();http.close();auth.close()

if __name__=='__main__':main()
