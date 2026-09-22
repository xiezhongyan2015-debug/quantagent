"""Supplement prices for historical basket members, including stocks absent today."""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
from pathlib import Path
import sys
import pandas as pd
from pcf_research import ROOT,OUT
from price_client import Client,ms

def main():
    codes=set()
    for f in (OUT/'raw').glob('*.gz'):
        with gzip.open(f,'rt',encoding='utf-8') as h:r=json.load(h)
        codes.update(x['C_STOCKCODE'] for x in r['stocklist'])
    available=set(pd.read_parquet(ROOT/'data_expansion/normalized/daily_stock_panel.parquet',columns=['symbol']).symbol.str[:6])
    symbols=[x+('.SH' if x[0]=='6' else '.SZ') for x in sorted(codes-available)]
    symbols+=['563030.SH','510500.SH']
    dest=OUT/'prices';dest.mkdir(exist_ok=True)
    client=Client(None,.5,base_url='https://free-api.tickflow.org')
    def fetch(job):
        symbol,adjust=job;p=dest/(symbol+'_'+adjust+'.json.gz')
        if p.exists():return
        params=dict(symbol=symbol,period='1d',count=10000,adjust=adjust,start_time=ms('2023-03-01'),end_time=ms('2026-09-17',True))
        body=client.get(params)
        record={'request':params,'response':body,'fetched_at':dt.datetime.now(dt.timezone.utc).isoformat()}
        with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(record,f)
    errors=[]
    jobs=[(s,a) for s in symbols for a in ['none','forward']]
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        fs={ex.submit(fetch,j):j for j in jobs}
        for i,f in enumerate(cf.as_completed(fs),1):
            try:f.result()
            except Exception as e:errors.append({'job':fs[f],'error':str(e)})
            if i%40==0 or i==len(jobs):print('PRICE',i,'/',len(jobs),'errors',len(errors),flush=True)
    (dest/'audit.json').write_text(json.dumps({'symbols':symbols,'errors':errors},indent=2),encoding='utf-8')

if __name__=='__main__':main()
