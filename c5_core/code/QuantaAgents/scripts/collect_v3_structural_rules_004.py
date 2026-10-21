"""Finite Shanghai historical settlement operational-guide lookup; no repeated queries."""
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
SCOPE=ROOT/'experiment_traces/meta_framework_v3/structural_rules_004'
sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.ledger import digest,need,worker_lease
from quanta_agents.meta_v3.research_tools import save_once

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def initialize():
    parents=[ROOT/'experiment_traces/meta_framework_v3'/name for name in ('structural_inputs_001','structural_sources_002','structural_sources_003')]
    pins=[]; prior_queries=[];prior_urls=[]
    for parent in parents:
        need((parent/'closed.json').is_file(),'prior scopes must remain closed')
        for name in ('plan.json','closed.json','manifest.json'):
            pins.append({'path':str(parent/name),'sha256':sha(parent/name)})
        for entry in read(parent/'manifest.json')['files']:
            need(sha(parent/entry['path'])==entry['sha256'],'prior source file drift')
        for path in parent.rglob('*intent.json'):
            d=read(path)
            if 'url' in d:prior_urls.append(d['url'])
            prior_queries.extend(o['q'] for o in d.get('queries',[]) if 'q' in o)
            prior_queries.extend(o['q'] for o in d.get('operations',[]) if o.get('kind')=='query')
    lead=parents[1]/'web/009_receipt.json';pins.append({'path':str(lead),'sha256':sha(lead)})
    plan={'kind':'bounded_historical_shanghai_settlement_operational_rules',
        'registered_at':datetime.now(timezone.utc).isoformat(),'deadline_epoch':time.time()+3600,
        'collector_sha256':sha(__file__),'parent_pins':pins,
        'purpose':'Find operational timing ofShanghai stock-distribution credits and its distinction from exchange trading/listing, plus participant-interface corporate-credit versus trade netting. This is an operational-guide/document lookup after completing the physical-rights layer, not a repeat of broad bonus-tax queries or an issuer/annual-report crawl. Prior2009ChinaClearFAQrepublication URL is a concrete saved lead.',
        'initial_queries':['中国结算上海分公司 证券发行人业务指南 2018 送股 登记','中国结算上海分公司 结算参与人 数据接口 股息红利 股份变动'],
        'observed_seed_url':'https://finance.sina.cn/sa/2009-03-23/detail-ikknscsi7050888.d.html?from=wap',
        'prior_queries_forbidden':prior_queries,'prior_direct_urls_forbidden':prior_urls,
        'max_get_requests':10,'max_response_bytes':16777216,'max_total_response_bytes':67108864,'per_request_seconds':40,
        'max_web_queries':8,'max_web_operations':24,'web_allowed_kinds':['query','open','screenshot'],
        'adaptive_queries':'Only exact titles/operational terms discovered from returned primary documents; no broad tax-age synonym retries.',
        'new_strategy_runs':0,'new_research_model_calls':0,'sealed_local_2024_2025_access':False,
        'prior_source002_remote_outcome_unknown_queries':4,'readiness_not_inferred_from_download_success':True,
        'exposure':'Later public document metadata is retained and cannot authenticate2018applicability. Only rule text with temporal and ordinary-account scope evidence may support2018simulation.',
        'stop':'Stop when operational timing/lineage evidence is sufficient, initial queries produce no relevant primary/document leads, unresolved request, deadline or bound. No duplicate/automatic retry/redirect or reopening old scopes. Save incomplete findings honestly.'}
    SCOPE.mkdir(exist_ok=False);save_once(SCOPE/'plan.json',plan)
    return {'root':str(SCOPE),'plan_sha256':sha(SCOPE/'plan.json'),'prior_query_count':len(prior_queries),'prior_direct_urls':len(prior_urls)}


def contract():
    plan=read(SCOPE/'plan.json')
    need(not (SCOPE/'closed.json').exists(),'scope is closed')
    need(time.time()<plan['deadline_epoch'] and sha(__file__)==plan['collector_sha256'],'deadline or collector source drift')
    for pin in plan['parent_pins']:
        need(sha(pin['path'])==pin['sha256'],'parent evidence drift')
    return plan


def reserve_web(operations):
    plan=contract();need(type(operations) is list and 1<=len(operations)<=4,'bounded explicit web operations')
    need(all(o.get('kind') in plan['web_allowed_kinds'] for o in operations),'unregistered web kind')
    need(not any(o.get('q') in plan['prior_queries_forbidden'] for o in operations),'prior query reuse forbidden')
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
    need(url not in plan['prior_direct_urls_forbidden'],'prior directURL reuse forbidden')
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
