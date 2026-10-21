"""Bounded public-source GET collector for the frozen L2 source-audit scope."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse,urljoin
import argparse,hashlib,json,time
import requests

ROOT=Path(__file__).resolve().parents[1]/'experiment_traces/meta_framework_v3/l2_public_source_001'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(p,value):
    with p.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def get(url,parent):
    plan=read(ROOT/'plan.json');assert time.time()<plan['deadline_epoch']
    folder=ROOT/'downloads';folder.mkdir(exist_ok=True)
    intents=list(folder.glob('*_intent.json'));n=len(intents)+1
    assert n<=plan['max_direct_get_requests']
    assert not any(read(p)['url']==url for p in intents),'already attempted URL; no blind retry'
    receipts=[read(p) for p in folder.glob('*_receipt.json')]
    assert len(receipts)==len(intents),'unresolved request; inspect existing intent'
    used=sum(x['bytes'] for x in receipts)
    assert used<plan['max_total_download_bytes']
    save(folder/f'{n:02d}_intent.json',{'url':url,'discovery_parent':parent,'created_at':datetime.now(timezone.utc).isoformat()})
    session=requests.Session();session.max_redirects=plan['redirects_max']
    session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; public source verification)','Referer':parent if parent.startswith('http') else 'https://data.eastmoney.com/'})
    receipt={'url':url,'bytes':0,'error':None,'redirects':[]};started=time.monotonic();payload=folder/f'{n:02d}_body.bin'
    try:
        with session.get(url,stream=True,timeout=plan['request_timeout_seconds']) as response:
            receipt.update(status=response.status_code,final_url=response.url,content_type=response.headers.get('Content-Type'),
                redirects=[{'url':r.url,'status':r.status_code,'location':r.headers.get('Location')} for r in response.history])
            limit=min(plan['max_single_download_bytes'],plan['max_total_download_bytes']-used)
            with payload.open('xb') as f:
                for chunk in response.iter_content(65536):
                    assert time.time()<plan['deadline_epoch'] and time.monotonic()-started<60,'request wall deadline'
                    assert receipt['bytes']+len(chunk)<=limit,'download byte limit'
                    f.write(chunk);receipt['bytes']+=len(chunk)
            response.raise_for_status()
    except Exception as exc:receipt['error']={'type':type(exc).__name__,'message':str(exc)}
    finally:session.close()
    receipt.update(completed_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-started,
                   sha256=hashlib.sha256(payload.read_bytes()).hexdigest() if payload.exists() else None)
    save(folder/f'{n:02d}_receipt.json',receipt)
    return {'request':n,'path':str(payload),**receipt}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('--parent',required=True);a=p.parse_args()
    print(json.dumps(get(a.url,a.parent),ensure_ascii=True))
