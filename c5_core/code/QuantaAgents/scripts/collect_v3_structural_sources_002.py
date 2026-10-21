"""Separately bounded primary2018 notice/rule acquisition; no strategy or model."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import requests

ROOT=Path(__file__).resolve().parents[1]
SCOPE=ROOT/'experiment_traces/meta_framework_v3/structural_sources_002'
sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.ledger import digest,need,worker_lease
from quanta_agents.meta_v3.research_tools import save_once

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def initialize():
    parent=ROOT/'experiment_traces/meta_framework_v3/structural_inputs_001'
    old=read(parent/'plan.json');need((parent/'closed.json').is_file(),'previous scope must remain closed')
    plan={'kind':'bounded_primary_notice_and_rule_followup','registered_at':datetime.now(timezone.utc).isoformat(),
        'deadline_epoch':time.time()+7200,'parent_scope':str(parent),'parent_plan_sha256':sha(parent/'plan.json'),
        'parent_closed_sha256':sha(parent/'closed.json'),'collector_sha256':sha(__file__),
        'codes':old['codes'],'market_interval':['2018-01-01','2018-12-31'],
        'purpose':'Primary implementation/non-distribution/correction and suspension evidence for the unchanged16stocks; historical mainland individual bonus/capitalization/tax/lot/fraction rules. Existing two mixed-distribution PDFs are reused.',
        'max_get_requests':60,'max_response_bytes':16777216,'max_total_response_bytes':134217728,'per_request_seconds':45,
        'max_web_queries':32,'max_web_operations':64,'new_strategy_runs':0,'new_research_model_calls':0,
        'sealed_local_2024_2025_access':False,'readiness_not_inferred_from_download_success':True,
        'exposure':'Public2018notices and rules applicable then; incidental later public metadata is archived but not admitted as strategy inputs. Earlier development exposure remains recorded.',
        'stop':'No automatic retry, redirects, duplicate URL, source drift, unresolved request, deadline or exceeded bound. Close with all failures and gaps.'}
    SCOPE.mkdir(exist_ok=False);save_once(SCOPE/'plan.json',plan)
    return {'root':str(SCOPE),'plan_hash':digest(plan)}


def contract():
    plan=read(SCOPE/'plan.json')
    need(not (SCOPE/'closed.json').exists(),'scope is closed')
    need(time.time()<plan['deadline_epoch'] and sha(__file__)==plan['collector_sha256'],'deadline or collector source drift')
    parent=Path(plan['parent_scope'])
    need(sha(parent/'plan.json')==plan['parent_plan_sha256'] and sha(parent/'closed.json')==plan['parent_closed_sha256'],'parent scope drift')
    return plan


def reserve_web(operations):
    plan=contract();need(type(operations) is list and 1<=len(operations)<=4,'bounded explicit web operations')
    folder=SCOPE/'web';folder.mkdir(exist_ok=True)
    with worker_lease(SCOPE):
        prior=sorted(folder.glob('*_intent.json'))
        need(all(p.with_name(p.name.replace('_intent','_receipt')).exists() for p in prior),'unresolved web operation; no automatic retry')
        all_ops=[v for p in prior for v in read(p)['operations']]
        need(len(all_ops)+len(operations)<=plan['max_web_operations'],'web operation budget')
        need(sum(o.get('kind')=='query' for o in all_ops+operations)<=plan['max_web_queries'],'web query budget')
        need(all(o not in all_ops for o in operations),'duplicate web operation')
        n=len(prior)+1;save_once(folder/f'{n:03d}_intent.json',{'operations':operations,'at':datetime.now(timezone.utc).isoformat()})
        return {'id':n,'intent_path':str(folder/f'{n:03d}_intent.json'),'receipt_path':str(folder/f'{n:03d}_receipt.json')}


def fetch(url,discovery):
    plan=contract();parsed=urlsplit(url)
    need(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password,'public HTTPS source required')
    need(type(discovery) is str and 0<len(discovery)<=2000,'observed source link provenance required')
    folder=SCOPE/'downloads';folder.mkdir(exist_ok=True)
    with worker_lease(SCOPE):
        intents=sorted(folder.glob('*_intent.json'));receipts=sorted(folder.glob('*_receipt.json'))
        need(len(intents)==len(receipts),'unresolved direct request')
        need(len(intents)<plan['max_get_requests'] and not any(read(p)['url']==url for p in intents),'request budget or duplicate URL')
        used=sum(read(p)['bytes'] for p in receipts);limit=min(plan['max_response_bytes'],plan['max_total_response_bytes']-used)
        need(limit>0,'response byte budget')
        n=len(intents)+1;save_once(folder/f'{n:03d}_intent.json',{'url':url,'discovery':discovery,'at':datetime.now(timezone.utc).isoformat()})
        path=folder/f'{n:03d}_body.bin';start=time.monotonic();record={'url':url,'discovery':discovery,'bytes':0,'status':'pending'}
        try:
            with requests.get(url,stream=True,allow_redirects=False,timeout=(10,10),headers={'User-Agent':'Mozilla/5.0 (bounded historical evidence review)'}) as response:
                record.update(http_status=response.status_code,content_type=response.headers.get('Content-Type'),location=response.headers.get('Location'))
                with path.open('xb') as stream:
                    for chunk in response.iter_content(65536):
                        need(time.monotonic()-start<plan['per_request_seconds'] and time.time()<plan['deadline_epoch'],'source request deadline')
                        need(record['bytes']+len(chunk)<=limit,'source response byte limit')
                        stream.write(chunk);record['bytes']+=len(chunk)
                need(response.status_code==200,'non200source response')
                record['status']='downloaded'
        except Exception as exc:record.update(status='failed',error=type(exc).__name__+': '+str(exc))
        record.update(path=str(path),sha256=sha(path) if path.exists() else None,elapsed_seconds=time.monotonic()-start,completed_at=datetime.now(timezone.utc).isoformat())
        save_once(folder/f'{n:03d}_receipt.json',record);return record


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['init','get']);p.add_argument('--url');p.add_argument('--discovery');a=p.parse_args()
    print(json.dumps(initialize() if a.mode=='init' else fetch(a.url,a.discovery),ensure_ascii=True))
