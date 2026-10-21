"""Send the previously fixed eight quote-reference buys through cash admission.

No synthetic completion of real source gaps; fees stay null and actions unknown.
Only the previously saved small snapshots are read. No network or model call.
"""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import argparse
import hashlib
import json
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from quanta_agents.meta_v3.l2_cash_account import L2CashAccount,KINDS,digest

SOURCE=REPO/'experiment_traces/meta_framework_v3/l2_execution_evidence_001'
OUT=REPO/'experiment_traces/meta_framework_v3/l2_cash_bridge_001'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,v):
    with p.open('x',encoding='utf-8') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')


def freeze():
    assert not OUT.exists()
    old=read(SOURCE/'plan.json')
    files=[SOURCE/'plan.json',SOURCE/'result.json',SOURCE/'independent_audit/selected_snapshots.json']
    assert sum(p.stat().st_size for p in files)<1024**2
    obligations=[{'code':code,'date':day,'kind':kind,
        'status':'declared_simulation' if kind=='capacity' else 'pending',
        'evidence_sha256':sha(SOURCE/'result.json') if kind=='capacity' else None,
        'note':'Previously frozen finite displayed-depth scenario only' if kind=='capacity' else 'Source/account obligation remains unresolved; never assumed absent or zero'}
        for code in old['codes'] for day in old['dates'] for kind in KINDS]
    account={'mode':'real_development','codes':old['codes'],'calendar':old['dates'],'initial_time':old['dates'][0]+'T00:00:00+08:00',
        'initial_cash':'1000000.00','policy':old['quote_policy'],'fees':None,'corporate_events':[],
        'obligations':obligations,'empty_corporate_event_list_means_no_admitted_events_not_no_events':True}
    plan={'created_at':datetime.now(timezone.utc).isoformat(),'deadline_epoch':time.time()+1800,'account_plan':account,
        'parent_quote_plan_sha256':old['plan_sha256'],'inputs':[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files],
        'source_pins':{str(p.relative_to(REPO)):sha(p) for p in [Path(__file__),REPO/'src/quanta_agents/meta_v3/l2_cash_account.py']},
        'max_orders':8,'new_model_calls':0,'network_calls':0,'original_market_reads':0,'strategy_backtests':0,
        'execution_valid':False,'formal_target_success':False,'stops':['30 minute deadline','source/hash mismatch','pending database intent or unhandled error; no rematch/restart','all eight saved buy intents accounted for']}
    plan['plan_sha256']=digest(plan)
    OUT.mkdir();save(OUT/'plan.json',plan)
    return {'plan_sha256':plan['plan_sha256'],'orders':8,'fees':None,'full_initial_cash':'1000000.00'}


def run():
    plan=read(OUT/'plan.json');assert digest({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
    assert time.time()<plan['deadline_epoch'] and not (OUT/'intent.json').exists()
    for path,value in plan['source_pins'].items():assert sha(REPO/path)==value
    for source in plan['inputs']:
        path=Path(source['path']);assert path.stat().st_size==source['bytes'] and sha(path)==source['sha256']
    save(OUT/'intent.json',{'started_at':time.time(),'plan_sha256':plan['plan_sha256']})
    previous=read(SOURCE/'result.json');snapshots=read(SOURCE/'independent_audit/selected_snapshots.json')
    assert previous['plan_sha256']==plan['parent_quote_plan_sha256']
    a=L2CashAccount(OUT/'account.sqlite',plan['account_plan']);results=[]
    try:
        for index,item in enumerate(previous['records']):
            assert time.time()<plan['deadline_epoch'] and index<plan['max_orders']
            buy=item['buy'];code=item['wind_code']
            choices=[x for x in snapshots if x['code']==code and x['side']=='buy'];assert len(choices)==1
            quote=choices[0]['execution']
            assert quote['source_row_no']==buy['execution_snapshot']['source_row_no']
            decision=datetime.fromisoformat(buy['decision_time'])
            order={'id':f'fixed-buy-{index+1:02d}-{code}','code':code,'date':buy['date'],'side':'buy',
                'quantity':buy['requested_quantity_native'],'limit_price_scaled':buy['limit_price_scaled'],
                'decision_time':buy['decision_time'],'arrival_time':(decision+timedelta(milliseconds=plan['account_plan']['policy']['assumed_order_latency_ms'])).isoformat()}
            result=a.submit(order,quote);results.append(result)
            save(OUT/f'{index+1:02d}_order.json',{'order':order,'receipt':result})
        snapshot=a.snapshot()
        save(OUT/'account_snapshot.json',snapshot)
        save(OUT/'ledger_genesis.json',a.ledger.genesis);save(OUT/'ledger_journal.json',a.ledger.journal)
        assert len(results)==8 and all(r['status']=='rejected_order' for r in results)
        assert snapshot['cash']==snapshot['external_cash_flow']=='1000000.00'
        assert snapshot['fees_paid']=='0.00' and snapshot['lots']=={} and snapshot['actions']=={} and snapshot['event_count']==1
        assert snapshot['pending_orders']==0 and a.ledger.verify_state_commitment()
        result={'completed_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':plan['plan_sha256'],'attempted_buy_orders':len(results),
            'rejected_unresolved_source_orders':sum(r['status']=='rejected_order' for r in results),'filled_orders':0,
            'initial_cash':snapshot['external_cash_flow'],'ending_cash':snapshot['cash'],'fees_charged':snapshot['fees_paid'],
            'holdings':snapshot['lots'],'registered_actions':snapshot['actions'],'pending_orders':0,'journal_events':1,
            'next_day_sell_orders_created':0,'no_sell_orders_reason':'No admitted purchase created inventory; reference quote shares were never imported as owned shares',
            'portfolio_pnl':None,'execution_valid':False,'formal_target_success':False,'formal_case_denominator':0,'new_model_calls':0,
            'interpretation':'Cash/admission bridge exercise using saved real quote inputs. Rejected orders are not a zero-return strategy backtest.'}
        save(OUT/'result.json',result)
    finally:a.close()
    # Independent persisted-state reopen: no new matching or source reads.
    recovered=L2CashAccount(OUT/'account.sqlite')
    try:assert recovered.snapshot()==snapshot
    finally:recovered.close()
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['freeze','run']);a=p.parse_args()
    print(json.dumps(freeze() if a.action=='freeze' else run(),ensure_ascii=True))
