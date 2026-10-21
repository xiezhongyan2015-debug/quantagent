"""Once-only public-tool wiring using saved real quotes and an explicit control.

The constant target is a controller engineering input, not model research or
alpha. Existing source gaps remain pending, fees null, and PnL unavailable.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import json
import hashlib
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from quanta_agents.meta_v3.research_tools import ResearchTools,save_once
from quanta_agents.meta_v3.runtime import source_pins
from quanta_agents.meta_v3.ledger import digest

BASE=REPO/'experiment_traces/meta_framework_v3'
OUT=BASE/'l2_research_bridge_001'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def freeze():
    assert not OUT.exists()
    files=[BASE/'l2_cash_bridge_001/plan.json',BASE/'l2_execution_evidence_001/independent_audit/selected_snapshots.json',BASE/'l2_execution_evidence_001/result.json']
    assert sum(p.stat().st_size for p in files)<1024**2
    p=read(files[0])['account_plan'];codes=[c[-2:].lower()+c[:6] for c in p['codes']];days=p['calendar']
    control={'kind':'controller_engineering_input','target':'0.10 per fixed stock, following session; frozen terminal zero',
        'not_a_market_feature':True,'not_a_model_generated_strategy':True}
    fields=[];elig=[]
    for day in days:
        for code in codes:
            meta={'session':day,'symbol':code,'effective_at':day+'T15:05:00+08:00','available_at':day+'T15:05:00+08:00','source_evidence_id':digest(control)}
            fields.append({**meta,'field':'instruction_budget','value':0.1})
            elig.append({**meta,'eligible':True})
    # This eligibility is the supplied engineering allocation instruction;
    # actual security membership/status still resides in pending obligations.
    obs=[{'code':x['code'],'date':x['execution']['date'],'decision':x['decision'],'execution':x['execution'],
        'source_selection_sha256':sha(files[1])} for x in read(files[1])]
    case={'research_class':'real_saved_development','execution_backend':'v3_l2_cash_001',
        'description':'Actual fixed eight-stock saved quote evidence; constant controller allocation for wiring, no strategy inference; original pending source obligations retained',
        'initial_cash':p['initial_cash'],'decision_fixture':{'kind':'exposed_l2_decision_table','codes':codes,'calendar':days,
        'fields':[{'name':'instruction_budget','unit':'controller instruction proportion, not market data'}],
        'field_rows':fields,'eligibility_rows':elig},'raw_source_bindings':{'obligations':p['obligations'],'source_artifacts':[]},
        'l2_execution':{'account_plan':p,'observations':obs,'marks':[]},'controller_input':control}
    program={'version':'factor_strategy_program_v1','factors':[],'target_weight_expression':'instruction_budget',
        'hypothesis':'Fixed engineering target to verify source rejection propagation',
        'applicability':['Existing frozen eight-stock wiring scope only'],
        'invalidation_conditions':['Reference matches appear as owned shares or rejected run becomes a funded strategy']}
    plan={'created_at':datetime.now(timezone.utc).isoformat(),'deadline_epoch':time.time()+1800,
        'case_hash':digest(case),'program_hash':digest(program),'source_pins':source_pins(),
        'script_sha256':sha(Path(__file__)),'inputs':[{'path':str(f),'bytes':f.stat().st_size,'sha256':sha(f)} for f in files],
        'new_model_calls':0,'external_network_calls':0,'source_market_scans':0,'max_program_attempts':1,
        'strategy_return_research':False,'formal_target_success':False}
    plan['plan_sha256']=digest(plan);OUT.mkdir();save_once(OUT/'plan.json',plan)
    save_once(OUT/'case.json',case);save_once(OUT/'program.json',program)
    return {'plan_sha256':plan['plan_sha256'],'case_hash':plan['case_hash'],'new_model_calls':0}


def run():
    plan=read(OUT/'plan.json');case=read(OUT/'case.json');program=read(OUT/'program.json')
    assert digest({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']
    assert time.time()<plan['deadline_epoch'] and source_pins()==plan['source_pins']
    assert sha(Path(__file__))==plan['script_sha256'] and digest(case)==plan['case_hash'] and digest(program)==plan['program_hash']
    for row in plan['inputs']:assert sha(Path(row['path']))==row['sha256'] and Path(row['path']).stat().st_size==row['bytes']
    save_once(OUT/'intent.json',{'plan_sha256':plan['plan_sha256'],'started_at':datetime.now(timezone.utc).isoformat()})
    tools=ResearchTools(OUT,'entry_bridge',case,[])
    result=tools.execute('fixed_control','develop_strategy',{'program':program})
    save_once(OUT/'public_execution.json',result)
    tools.history=[{'id':'fixed_control','result':result,'response':{'action':'develop_strategy'}}]
    for table in ('daily_compact','trades_compact','rejections'):
        offset=0;pages=[]
        while True:
            page=tools.execute(f'page_{table}_{offset}','inspect_execution',{'evidence_id':'fixed_control','table':table,'offset':offset,'limit':32})
            pages.append(page)
            after=page['public']['next_offset']
            if after is None:break
            assert after>offset
            offset=after
        save_once(OUT/(table+'_pages.json'),pages)
    pub=result['public'];assert pub['raw_status']=='completed_execution_evidence'
    assert pub['final_snapshot']['cash']=='1000000.00' and pub['portfolio_pnl'] is None
    assert pub['table_counts']=={'daily':3,'trades':8,'rejections':8}
    assert not pub['costs_applied'] and not pub['formal_target_success']
    summary={'completed_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':plan['plan_sha256'],
        'public_tool_entry_verified':True,'ordinary_model_research_run':False,'model_final_created':False,
        'calendar_cash_days':3,'orders':8,'source_rejections':8,'initial_and_final_cash':'1000000.00',
        'portfolio_pnl':None,'new_model_calls':0,'external_network_calls':0,'formal_target_success':False}
    save_once(OUT/'result.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','run']);args=parser.parse_args()
    print(json.dumps(freeze() if args.action=='freeze' else run(),ensure_ascii=True))
