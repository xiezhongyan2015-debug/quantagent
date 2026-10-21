"""Frozen sixteen-stock2018 source preparation; no model or strategy execution."""
import argparse
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
import gzip,hashlib,io,json,time
from pathlib import Path
import sys

import requests

ROOT=Path(__file__).resolve().parents[1]
SCOPE=ROOT/'experiment_traces/meta_framework_v3/structural_inputs_001'
sys.path.insert(0,str(ROOT/'src'))
from quanta_agents.meta_v3.ledger import digest,need
from quanta_agents.meta_v3.research_tools import save_once
from audit_historical_corporate_actions import parse_index


def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def contract():
    plan=read(SCOPE/'plan.json')
    file=SCOPE/'source_execution_contract_001.json'
    value={'plan_hash':digest(plan),'script_sha256':sha(__file__),
        'index_parser_sha256':sha(ROOT/'scripts/audit_historical_corporate_actions.py'),
        'classification':'Preparation only; later-vintage saved price data and provider indexes are not actual fills or complete action proof.'}
    if file.exists():need(read(file)==value,'source preparation contract drift')
    else:save_once(file,value)
    return plan


def audit():
    plan=contract();out=SCOPE/'raw_audit';out.mkdir(exist_ok=False)
    saved=[];findings=[]
    for code in plan['codes']:
        folder=Path(plan['saved_source_directories'][code]);record={'code':code,'status':'pending','reference_changes':[]}
        try:
            receipt=read(folder/'receipt.json');compressed=folder/'rows.json.gz';manifest=folder/'source_manifest.json'
            need(compressed.stat().st_size<=2097152 and sha(compressed)==receipt['rows_sha256'],'original compressed source hash/bound')
            need(sha(manifest)==receipt['source_manifest_sha256'],'original manifest hash')
            with gzip.GzipFile(fileobj=io.BytesIO(compressed.read_bytes())) as stream:body=stream.read(8388609)
            need(len(body)<=8388608,'expanded source bound');rows=json.loads(body)
            need(len(rows)<=2000,'original row bound')
            dates=set(plan['calendar']);selected=[r for r in rows if r['date'] in dates]
            need([r['date'] for r in selected]==plan['calendar'],'whole requested cash calendar required')
            boundary=next(r for r in rows if r['date']==plan['previous_reference_date'])
            for previous,current in zip([boundary]+selected,selected):
                text=current.get('raw_price_text') or {};prev=previous.get('raw_price_text') or {}
                a=text.get('raw_prev_close');b=prev.get('raw_close')
                if a is None or b is None:
                    record['reference_changes'].append({'date':current['date'],'difference':None,'reason':'missing original price evidence'})
                else:
                    difference=Decimal(a)-Decimal(b)
                    if abs(difference)>Decimal('0.01'):record['reference_changes'].append({'date':current['date'],'difference':str(difference),'reason':'needs action/reference explanation'})
            path=out/(code+'_rows.json');save_once(path,{'previous_reference':boundary,'selected_rows':selected})
            record.update(status='all_saved_rows_retained',rows=len(selected),names=sorted({r['stock_name'] for r in selected}),
                accepted_rows=sum(r['accepted'] for r in selected),reasons=dict(Counter(reason for r in selected for reason in r.get('reason_codes',[]))),
                membership_days=sum(any(a<=r['date']<=b for a,b in plan['membership_intervals'][code]) for r in selected),
                source={'root':str(folder),'rows_file':compressed.name,'rows_sha256':sha(compressed),'manifest_file':manifest.name,'manifest_sha256':sha(manifest)},
                projection_sha256=sha(path))
            saved.append(path)
        except Exception as exc:record.update(status='source_gap',error=f'{type(exc).__name__}: {exc}')
        findings.append(record)
        save_once(out/(code+'_receipt.json'),record)
    result={'kind':'whole_fixed_scope_raw_reference_audit','findings':findings,'fixed_codes':len(plan['codes']),
        'fixed_stock_days':len(plan['codes'])*len(plan['calendar']),'complete_saved_stock_grids':sum(r['status']=='all_saved_rows_retained' for r in findings),
        'all_reference_changes_unadmitted':True,'new_model_calls':0,'new_raw_csv_reads':0,'strategy_runs':0,'formal_target_success':False}
    save_once(out/'result.json',result)
    print(json.dumps({'fixed_stock_days':result['fixed_stock_days'],'complete_grids':result['complete_saved_stock_grids'],
        'findings':[{k:v for k,v in r.items() if k not in ('source','projection_sha256')} for r in findings]},ensure_ascii=True))


def fetch(url,discovery):
    plan=contract();limits=plan['source_acquisition_bounds'];deadline=datetime.fromisoformat(limits['deadline']).timestamp()
    need(time.time()<deadline,'frozen source deadline reached')
    folder=SCOPE/'downloads';folder.mkdir(exist_ok=True)
    intents=sorted(folder.glob('*_intent.json'));receipts=sorted(folder.glob('*_receipt.json'))
    need(len(intents)==len(receipts),'unresolved request; inspect existing intent without retry')
    need(not any(read(p)['url']==url for p in intents),'URL already attempted; no repeat')
    need(len(intents)<limits['max_get_requests'],'source request bound')
    used=sum(read(p)['bytes'] for p in receipts);limit=min(limits['max_bytes_per_response'],limits['max_total_response_bytes']-used)
    need(limit>0,'source byte budget exhausted')
    n=len(intents)+1;save_once(folder/f'{n:03d}_intent.json',{'url':url,'discovery':discovery,'created_at':datetime.now(timezone.utc).isoformat()})
    path=folder/f'{n:03d}_body.bin';record={'url':url,'discovery':discovery,'bytes':0,'status':'pending','error':None};start=time.monotonic()
    try:
        with requests.get(url,stream=True,allow_redirects=False,timeout=(10,10),headers={'User-Agent':'Mozilla/5.0 (compatible; bounded source review)'}) as response:
            record.update(http_status=response.status_code,content_type=response.headers.get('Content-Type'),location=response.headers.get('Location'))
            with path.open('xb') as stream:
                for chunk in response.iter_content(65536):
                    need(time.monotonic()-start<limits['per_request_seconds'] and time.time()<deadline,'source request time limit')
                    need(record['bytes']+len(chunk)<=limit,'source response byte limit')
                    stream.write(chunk);record['bytes']+=len(chunk)
            need(response.status_code==200,'source HTTP status is not200')
            record['status']='downloaded'
    except Exception as exc:record.update(status='failed',error=f'{type(exc).__name__}: {exc}')
    record.update(completed_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-start,
        path=str(path),sha256=sha(path) if path.exists() else None)
    save_once(folder/f'{n:03d}_receipt.json',record)
    return record


def indexes():
    plan=contract();out=SCOPE/'provider_indexes';out.mkdir(exist_ok=False)
    old=ROOT/'experiment_traces/meta_corporate_action_pilot';old_sources=read(old/'sources.json')['sources'];results=[]
    for code in plan['codes']:
        number=code[2:];url=f'https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{number}.phtml'
        matches=[r for r in old_sources if r['url']==url and r['status']=='downloaded']
        if matches:
            previous=matches[0];path=old/previous['file'];need(sha(path)==previous['sha256'],'saved provider source drift')
            record={'url':url,'status':'reused_saved','path':str(path),'sha256':sha(path),'bytes':0,'new_http_requests':0}
        else:record=fetch(url,'Observed fixed stockid route in scripts/audit_historical_corporate_actions.py and saved600006source')
        entry={'code':code,'source':record,'events':[]}
        if record['status'] in ('downloaded','reused_saved'):
            parsed=parse_index(Path(record['path']).read_bytes(),number,url)
            entry.update(events=[r for r in parsed['events'] if any(str(r.get(k,'')).startswith('2018-') for k in ('announcement_date','record_date','ex_date'))],
                tables=parsed['tables'],rejected_rows=parsed['rejected_rows'],complete_action_coverage=False)
        save_once(out/(code+'.json'),entry);results.append(entry)
        print(json.dumps({'code':code,'source_status':record['status'],'bytes':record['bytes'],'events':entry['events']},ensure_ascii=True),flush=True)
    save_once(out/'summary.json',{'fixed_codes':plan['codes'],'sources':[{k:r[k] for k in ('code','source')} for r in results],
        'events':sum(len(r['events']) for r in results),'complete_action_coverage':False,'formal_target_success':False})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['audit','indexes','get']);p.add_argument('--url');p.add_argument('--discovery');a=p.parse_args()
    if a.mode=='audit':audit()
    elif a.mode=='indexes':indexes()
    else:
        need(a.url and a.discovery,'explicit discovered source URL required')
        print(json.dumps(fetch(a.url,a.discovery),ensure_ascii=True))
