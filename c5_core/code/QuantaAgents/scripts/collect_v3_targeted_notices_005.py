"""Bounded primary notices for three unchanged 2018 coverage gaps."""
from pathlib import Path
import argparse
import datetime
import json
import time
import collect_v3_structural_sources_003 as base

ROOT=Path(__file__).resolve().parents[1]
SCOPE=ROOT/'experiment_traces/meta_framework_v3/targeted_notices_005'
ORIGINAL_CONTRACT=base.contract
base.SCOPE=SCOPE

def contract():
    p=ORIGINAL_CONTRACT()
    base.need(base.sha(__file__)==p['wrapper_sha256'],'targeted collector drift')
    return p
base.contract=contract

def initialize():
    parents=[];urls=set();queries=[]
    for name in ('structural_inputs_001','structural_sources_002','structural_sources_003','structural_rules_004'):
        folder=ROOT/'experiment_traces/meta_framework_v3'/name
        base.need((folder/'closed.json').is_file(),'prior source scope not closed')
        for name in ('plan.json','closed.json','manifest.json'):
            p=folder/name;parents.append({'path':str(p),'sha256':base.sha(p)})
        for entry in base.read(folder/'manifest.json')['files']:
            p=folder/entry['path'];base.need(base.sha(p)==entry['sha256'],'prior source evidence drift')
        for p in folder.rglob('*intent.json'):
            x=base.read(p)
            if x.get('url'):urls.add(x['url'])
            for op in x.get('operations',[]):
                if op.get('kind')=='query':queries.append(op)
    plan={'kind':'three_targeted_primary_notice_gaps_v1',
        'registered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'deadline_epoch':time.time()+2700,'collector_sha256':base.sha(base.__file__),
        'wrapper_sha256':base.sha(__file__),'parent_pins':parents,
        'unchanged_market_scope':{'symbols':16,'sessions':243,'stock_days':3888,'raw_missing_rows':100},
        'targets':['sh600037 suspension start 2018-02-06, issuer 2018-005',
                   'sh600064 missing quote 2018-03-28 and resumption 2018-03-29',
                   'sh600006 2017 annual cash distribution implemented June 2018'],
        'max_get_requests':12,'max_response_bytes':8*1024**2,'max_total_response_bytes':32*1024**2,
        'per_request_seconds':35,'max_web_queries':8,'max_web_operations':16,
        'web_allowed_kinds':['query','open','click','screenshot'],'max_local_pdf_pages_reviewed':20,
        'new_strategy_runs':0,'new_research_model_calls':0,'new_market_file_reads':0,
        'sealed_local_2024_2025_access':False,'prior_source002_remote_outcome_unknown_queries':4,
        'prior_direct_urls':sorted(urls),'prior_search_operations':queries,
        'exposure':'Retrospective historical source audit only. Preserve all prior development/unknown query exposure. Later incidental public metadata is not a signal or sealed local dataset.',
        'stop':'No automatic retry, duplicate prior direct URL, unknown dispatch replacement or quota extension; no broad historical tax/annual-report search. Stop each branch after target primary notice or supported unavailable finding. Readability does not certify annual completeness.'}
    SCOPE.mkdir(exist_ok=False);base.save_once(SCOPE/'plan.json',plan)
    return {'scope':str(SCOPE),'plan_sha256':base.sha(SCOPE/'plan.json'),'prior_urls':len(urls),'prior_queries':len(queries)}

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','reserve-web','get']);p.add_argument('--json');p.add_argument('--url');p.add_argument('--discovery');a=p.parse_args()
    if a.command=='init':result=initialize()
    elif a.command=='reserve-web':
        operations=base.read(a.json);plan=contract()
        base.need(not any(o in plan['prior_search_operations'] for o in operations),'prior search query cannot be repeated')
        result=base.reserve_web(operations)
    else:
        plan=contract();base.need(a.url not in plan['prior_direct_urls'],'closed-scope direct URL cannot be retried')
        result=base.fetch(a.url,a.discovery)
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':main()
