"""Diagnostic only: strict 5-minute corroboration of existing 1-minute quotes.

Never expands 5-minute candles into minutes and never changes a source price.
This identifies whether subsequent observed hours can be independently checked
when the source's day-opening quote disagrees with the primary daily source.
"""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from public_baostock_probe import PublicClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE/'hour_local_coarse_concordance_20261009'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def check(symbol,day,query):
    rows=query['data'].get('record',[]) if isinstance(query['data'],dict) else query['data']
    coarse = pd.DataFrame(rows,columns=query['fields'])
    assert len(coarse)==48 and coarse.adjustflag.eq('3').all()
    assert coarse.code.eq(symbol[-2:].lower()+'.'+symbol[:6]).all()
    times=pd.to_datetime(coarse.time,format='%Y%m%d%H%M%S%f')
    expected = np.r_[np.arange(575,691,5),np.arange(785,901,5)]
    np.testing.assert_array_equal(times.dt.hour.to_numpy()*60+times.dt.minute.to_numpy(),expected)
    p = BASE/'huggingface/snapshot/data/stock_1m'/symbol[-2:]/(symbol[:6]+'.parquet')
    q = pq.read_table(p,filters=[('timestamp','>=',pd.Timestamp(day)),('timestamp','<',pd.Timestamp(day)+pd.Timedelta(days=1))]).to_pandas().sort_values('timestamp')
    clock=q.timestamp.dt.hour.to_numpy()*60+q.timestamp.dt.minute.to_numpy()
    end=np.r_[np.arange(571,691),np.arange(781,901)]
    regular=q[np.isin(clock,end)]
    np.testing.assert_array_equal(regular.timestamp.dt.hour.to_numpy()*60+regular.timestamp.dt.minute.to_numpy(),end)
    assert len(regular)==240 and not q.timestamp.duplicated().any()
    raw=regular[['open','high','low','close']].to_numpy(float).round(2)
    vol=regular.volume.to_numpy(float).copy()
    amount=regular.turnover.to_numpy(float).copy()
    boundary=q[(clock==570)&q.volume.gt(0)]
    assert len(boundary)<=1 and not ((clock==780)&q.volume.gt(0)).any()
    if len(boundary):
        b=boundary.iloc[0];raw[0,0]=round(float(b.open),2)
        raw[0,1]=max(raw[0,1],round(float(b.high),2));raw[0,2]=min(raw[0,2],round(float(b.low),2))
        vol[0]+=float(b.volume);amount[0]+=float(b.turnover)
    result=[]
    coarse_prices=coarse[['open','high','low','close']].to_numpy(float)
    coarse_volume=coarse.volume.to_numpy(float);coarse_amount=coarse.amount.to_numpy(float)
    for i in range(48):
        bars=raw[i*5:(i+1)*5];v=vol[i*5:(i+1)*5].sum();a=amount[i*5:(i+1)*5].sum()
        hflimits=np.array([bars[0,0],bars[:,1].max(),bars[:,2].min(),bars[-1,3]])
        c=coarse_prices[i]
        tests={'raw_open_exact':abs(hflimits[0]-c[0])<=1e-8,'raw_close_exact':abs(hflimits[3]-c[3])<=1e-8,
               'all_quotes_inside_5m_price_range':bool(hflimits[1]<=c[1]+1e-8 and hflimits[2]>=c[2]-1e-8),
               'volume_within_100_shares':abs(v-coarse_volume[i])<=100}
        tests={k:bool(v) for k,v in tests.items()}
        result.append({'ending_clock':f'{expected[i]//60:02d}:{expected[i]%60:02d}',**tests,
                       'all_strict_tests_passed':all(tests.values()),'observed_minute_aggregate_ohlc':hflimits.tolist(),
                       'source_5m_ohlc':c.tolist(),'volume_difference_shares':float(v-coarse_volume[i]),
                       'amount_difference_cny':float(a-coarse_amount[i])})
    primary=BASE/'baostock/union_daily_factors/normalized'/(symbol+'.json')
    obj=json.loads(primary.read_text());daily=pd.DataFrame(obj['rows'],columns=obj['fields']).set_index('date').loc[day]
    global_tests={'day_volume_within_100':abs(vol.sum()-float(daily.volume))<=100,
                  'day_last_trade_close_exact':abs(raw[-1,3]-float(daily.close))<=1e-8,
                  'day_open_exact':abs(raw[0,0]-float(daily.open))<=1e-8,
                  'all_quotes_inside_daily_range':bool(raw[:,1].max()<=float(daily.high)+1e-8 and raw[:,2].min()>=float(daily.low)-1e-8)}
    global_tests={k:bool(v) for k,v in global_tests.items()}
    return {'symbol':symbol,'date':day,'source_1m_file_sha256':sha(p),'daily_source_sha256':sha(primary),
            **global_tests,'strict_5m_buckets_passed':sum(x['all_strict_tests_passed'] for x in result),
            'hour_all_12_buckets_passed':[all(x['all_strict_tests_passed'] for x in result[h*12:(h+1)*12]) for h in range(4)],
            'bucket_checks':result,'price_overwrites':False,'5m_bars_expanded_as_1m':False,'source_admitted_to_candidate':False}


def main():
    OUT.mkdir(exist_ok=True)
    client=None;results=[]
    try:
        for symbol,day in [('000099.SZ','2018-06-27'),('688281.SH','2023-01-06'),('002831.SZ','2022-06-06')]:
            path=OUT/(symbol[-2:].lower()+'.'+symbol[:6]+'_'+day+'_'+day+'_5_p1.json')
            if path.exists():
                r=json.loads(path.read_text());assert sha(r['wire_path'])==r['sha256']
            else:
                if client is None: client=PublicClient(OUT/'supplemental');client.login()
                r=client.query(symbol[-2:].lower()+'.'+symbol[:6],day,day,'5')
            assert r['error_code']=='0' and r['frequency']=='5'
            rows=r['data'].get('record',[]) if isinstance(r['data'],dict) else r['data']
            if not rows:
                results.append({'symbol':symbol,'date':day,'status':'no_coarse_rows'});continue
            result=check(symbol,day,r);results.append(result)
            print(json.dumps({k:v for k,v in result.items() if k!='bucket_checks'}),flush=True)
            time.sleep(1)
    finally:
        if client is not None: client.close()
        receipt={'checked_utc':datetime.now(timezone.utc).isoformat(),'source_code_sha256':sha(Path(__file__)),
                 'results':results,'wire_receipts':[] if client is None else client.records,
                 'candidate_values_changed':False,'minute_bars_created_from_coarse_bars':False,
                 'personal_credentials_used':False,'production_ready':False}
        (OUT/'sample_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
