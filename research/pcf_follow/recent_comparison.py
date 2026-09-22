"""Compare a continuous PCF book with newly funded ETF buy-and-hold accounts."""
import argparse, gzip, json
from pathlib import Path
import numpy as np
import pandas as pd
from experiment import simulate

def excess_stats(r,b):
    assert r.index.equals(b.index) and r.notna().all() and b.notna().all()
    v=(1+r).cumprod()/(1+b).cumprod()
    peak=np.maximum.accumulate(np.r_[1.,v.to_numpy()])[1:]
    return {'excess':float(v.iloc[-1]-1),'drawdown':float((v.to_numpy()/peak-1).min())}

def main():
    a=argparse.ArgumentParser();a.add_argument('--returns',required=True);a.add_argument('--prices',required=True);a.add_argument('--out',required=True);args=a.parse_args()
    daily=pd.read_parquet(args.returns).loc['2026-03-18':'2026-09-17']
    b=daily.csi500_total_return_index;rows=[]
    for lag in [1,3,5]:
        name=f'pcf_lag{lag}_cost10bp'
        rows.append(dict(model=name,type='continuous_pcf_book',**excess_stats(daily[name],b)))
    for path in sorted(Path(args.prices).glob('*.json.gz')):
        with gzip.open(path,'rt',encoding='utf-8') as f:raw=json.load(f)
        x=pd.DataFrame(raw['data']['data'])
        x.index=pd.to_datetime(x.timestamp,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
        x=x.sort_index().loc[:b.index[-1]]
        o=x[['open']].rename(columns={'open':'etf'});c=x[['close']].rename(columns={'close':'etf'})
        t=pd.DataFrame(np.nan,index=x.index,columns=['etf']);t.iloc[x.index.get_loc(b.index[0])-1]=.98
        ret=simulate(o,c,t,.001,b.index[0]).ret.reindex(b.index)
        rows.append(dict(model=raw['symbol'],type='new_etf_book_98pct_first_open_buy_no_final_sale',**excess_stats(ret,b)))
    result={'start':str(b.index[0].date()),'end':str(b.index[-1].date()),'days':len(b),'benchmark':'H00905 total return','cost_per_side':.001,'results':rows}
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
