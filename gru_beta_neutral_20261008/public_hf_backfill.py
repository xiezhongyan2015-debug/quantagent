"""Pinned public historical minute snapshot download, no account or SDK install."""
from pathlib import Path
from urllib.request import Request, urlopen
import concurrent.futures
import datetime
import hashlib
import json
import time
import argparse
import threading

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes/huggingface'
REPO='neigezhu/china-a-share-1min-ohlcv'
REVISION='ba589a11534825044fe5a6b84838f50ba8d8d188'
BASE=f'https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/'
_index=None
_pace=threading.Lock()
_last_request=0.


def index():
    global _index
    if _index is None:
        metadata=json.loads((OUT/'dataset_info_blobs.json').read_text())
        assert metadata['sha']==REVISION and not metadata['private'] and not metadata['gated']
        _index={x['rfilename']:x for x in metadata['siblings']}
    return _index


def download(relative,cap=128*1024*1024):
    global _last_request
    entry=index()[relative]
    path=OUT/'snapshot'/relative;path.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    if path.exists():
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assert path.stat().st_size==entry['size']
        if entry.get('lfs'):assert digest==entry['lfs']['sha256']
        return {'file':relative,'path':str(path),'bytes':path.stat().st_size,'sha256':digest,'cached':True,'hub_lfs_hash_verified':bool(entry.get('lfs'))}
    temp=path.with_suffix(path.suffix+'.partial');digest=hashlib.sha256();total=0
    request=Request(BASE+relative,headers={'User-Agent':'audited-minute-backfill/1.0'})
    with _pace:
        time.sleep(max(0.,.7-(time.monotonic()-_last_request)))
        _last_request=time.monotonic()
    with urlopen(request,timeout=45) as response,temp.open('wb') as stream:
        size=int(response.headers.get('Content-Length','0'))
        if size>cap:raise ValueError(f'Download exceeds file cap: {relative} {size}')
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            total+=len(chunk)
            if total>cap:raise ValueError('Download exceeded streaming cap')
            digest.update(chunk);stream.write(chunk)
        if size and size!=total:raise ValueError('Incomplete HTTP body')
    assert total==entry['size']
    if entry.get('lfs'):assert digest.hexdigest()==entry['lfs']['sha256']
    temp.replace(path)
    return {'file':relative,'path':str(path),'bytes':total,'sha256':digest.hexdigest(),
            'url':BASE+relative,'downloaded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'elapsed_seconds':round(time.monotonic()-started,3),'cached':False,
            'hub_lfs_hash_verified':bool(entry.get('lfs'))}


def main():
    files=['README.md','LICENSE','metadata/coverage_by_instrument.csv',
           'metadata/no_bar_day_classification.csv','metadata/market_calendar.csv',
           'metadata/absent_instruments.csv','metadata/source_provenance.json','metadata/summary.json',
           'data/stock_1m/SH/600754.parquet','data/stock_1m/SH/600169.parquet',
           'data/stock_1m/SZ/000034.parquet','data/stock_1m/SZ/300244.parquet']
    receipt={'repo':REPO,'revision':REVISION,'personal_credentials_used':False,
             'status':'sample_download','records':[],'errors':[],'not_yet_panel_admitted':True}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(download,path):path for path in files}
        for future in concurrent.futures.as_completed(futures):
            path=futures[future]
            try:
                row=future.result();receipt['records'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
            except Exception as e:
                row={'file':path,'error':f'{type(e).__name__}: {e}'};receipt['errors'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
            (OUT/'sample_download_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    receipt['status']='sample_download_complete' if not receipt['errors'] else 'sample_download_partial'
    (OUT/'sample_download_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))


def bulk():
    import sys
    sys.path.insert(0,str(ROOT/'gru_research/.deps'))
    import pyarrow.parquet as pq
    import pandas as pd
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    defects=pq.read_table(ROOT/'gru_research/artifacts/minute_invalid_details.parquet').to_pandas()
    priority=set(defects.loc[defects.date.str.startswith('2019'),'symbol'])|{'000034.SZ','000403.SZ','000088.SZ','000657.SZ'}
    ordered=sorted(symbols,key=lambda x:(x not in priority,x))
    requests=[f'data/stock_1m/{s[-2:]}/{s[:6]}.parquet' for s in ordered]
    assert all(p in index() for p in requests)
    total_bytes=sum(index()[p]['size'] for p in requests)
    receipt={'repo':REPO,'revision':REVISION,'status':'downloading_union',
             'requested_symbols':len(requests),'requested_bytes':total_bytes,
             'verified_files':0,'verified_bytes':0,'errors':[],'not_yet_panel_admitted':True,
             'personal_credentials_used':False,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    log=(OUT/'union_download_records.jsonl').open('a')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(download,path):path for path in requests}
        for future in concurrent.futures.as_completed(futures):
            path=futures[future]
            try:
                row=future.result();receipt['verified_files']+=1;receipt['verified_bytes']+=row['bytes']
            except Exception as e:
                row={'file':path,'error':f'{type(e).__name__}: {e}'};receipt['errors'].append(row)
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            receipt['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
            temp=OUT/'union_download_progress.tmp';temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2));temp.replace(OUT/'union_download_progress.json')
            if receipt['verified_files']%25==0 or 'error' in row:
                print(json.dumps({k:v for k,v in receipt.items() if k!='errors'},ensure_ascii=False),flush=True)
    log.close()
    receipt['status']='union_download_complete' if not receipt['errors'] else 'union_download_partial'
    (OUT/'union_download_progress.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--union',action='store_true');args=ap.parse_args()
    bulk() if args.union else main()
