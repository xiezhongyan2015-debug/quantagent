"""Historical official PCF collection. No brokerage access or current-member filtering."""
from pathlib import Path
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import time
import threading
import os
import requests
import pandas as pd

ROOT=Path(os.environ.get('PCF_PROJECT_ROOT',Path(__file__).resolve().parents[2]))
OUT=ROOT/'data_expansion/public_fund_holdings/pcf_history_563030'
BASE='https://api.efunds.com.cn/xcowch/front/etffund/'
LOCAL=threading.local()

def get_day(day):
    path=OUT/'raw'/(day+'.json.gz')
    if path.exists():
        with gzip.open(path,'rt',encoding='utf-8') as f: return json.load(f)
    if not hasattr(LOCAL,'session'): LOCAL.session=requests.Session()
    record={'date':day,'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source':BASE,'historical_publication_timestamp_verified':False}
    for ep in ['baseinfo','stocklist']:
        for attempt in range(3):
            try:
                r=LOCAL.session.get(BASE+ep,params={'fundCode':'563030','tDate':day,'listType':'1'},timeout=35)
                r.raise_for_status();j=r.json()
                if j.get('status')!=1:raise ValueError(str(j)[:150])
                record[ep]=j['data'];break
            except Exception:
                if attempt==2:raise
                time.sleep(2+attempt*2)
        time.sleep(.15)
    b=record['baseinfo'];s=record['stocklist']
    if b.get('tDate')!=day or not b.get('isMapShow'):raise ValueError('missing or substituted date '+day)
    if len(s)!=int(b['map']['RECORDNUM']):raise ValueError('record count mismatch '+day)
    if len({x['C_STOCKCODE'] for x in s})!=len(s):raise ValueError('duplicate security '+day)
    if not all(x['BUSI_DATE']==day.replace('-','') for x in s):raise ValueError('stock date mismatch '+day)
    with gzip.open(path,'wt',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False)
    return record

def main():
    (OUT/'raw').mkdir(parents=True,exist_ok=True)
    protocol={'fund':'563030','start':'2023-03-13','end':'2026-09-17',
        'universe':'each historical PCF; never filter by present index membership',
        'execution_lag_trading_days':[1,3,5], 'main_lag':1,
        'cost_per_side':[.001], 'main_cost':.001,
        'weight':'PCF quantity times prior trading day unadjusted close / prior NAV per creation unit; if total > 98%, scale down to 98%; remainder cash',
        'missing_price':'do not renormalize available stocks; reserve absent target weight in cash and disclose coverage',
        'timing':'T-dated PCF applied at T+lag open. No same-day-open result. Historical publication timestamps/revisions not certified; delayed archived-snapshot research only.',
        'selection_bias':'563030 was selected after seeing recent performance; this is not a prospective fund-selection test',
        'frozen_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    if not (OUT/'protocol.json').exists():(OUT/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    bench=pd.read_parquet(ROOT/'data_expansion/normalized/benchmark_index_daily.parquet')
    dates=bench.loc[(bench.symbol=='000905.SH')&(bench.date>='2023-03-13')&(bench.date<='2026-09-17'),'date'].sort_values().dt.strftime('%Y-%m-%d').tolist()
    records=[];errors=[]
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        tasks={ex.submit(get_day,d):d for d in dates}
        for i,f in enumerate(cf.as_completed(tasks),1):
            try:records.append(f.result())
            except Exception as e:errors.append({'date':tasks[f],'error':str(e)})
            if i%50==0 or i==len(dates): print('PCF',i,'/',len(dates),'errors',len(errors),flush=True)
    rows=[];basic=[]
    for r in records:
        b=r['baseinfo']['map'];day=r['date']
        basic.append({'date':day,**b})
        for x in r['stocklist']:rows.append({'date':day,**x})
    pd.DataFrame(rows).sort_values(['date','C_STOCKCODE']).to_parquet(OUT/'components.parquet',index=False)
    pd.DataFrame(basic).sort_values('date').to_json(OUT/'basic.json',orient='records',force_ascii=False,indent=2)
    (OUT/'download_audit.json').write_text(json.dumps({'expected_days':len(dates),'downloaded_days':len(records),'errors':errors},indent=2),encoding='utf-8')
    print('DONE',len(records),'days',len(rows),'components',len({x['C_STOCKCODE'] for x in rows}),'stocks',flush=True)

if __name__=='__main__':main()
