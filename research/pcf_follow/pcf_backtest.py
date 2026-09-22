"""Delayed historical PCF look-through replication, explicit archived-data caveats."""
import gzip
import json
import sys
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd
from pcf_research import ROOT,OUT
from experiment import simulate,metrics

RESULT=Path(__file__).resolve().parent/'results'
CONVENTIONS=json.loads((Path(__file__).parent/'reporting_conventions.json').read_text(encoding='utf-8'))
COST=CONVENTIONS['cost_per_side']

def read_extra(symbol,adjust):
    p=OUT/'prices'/(symbol+'_'+adjust+'.json.gz')
    if not p.exists():return pd.DataFrame()
    with gzip.open(p,'rt',encoding='utf-8') as f:r=json.load(f)
    data=r['response'].get('data',{})
    if isinstance(data,dict) and 'klines' in data:data=data['klines']
    d=pd.DataFrame(data)
    if 'timestamp' not in d:return pd.DataFrame()
    d['date']=pd.to_datetime(d.timestamp,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    return d.set_index('date').sort_index().loc['2023-03-01':'2026-09-17'].apply(pd.to_numeric,errors='coerce')

def make_targets(components,basic,raw_close,dates,symbols):
    # No backward filling. Prices are strictly before each PCF date.
    prev=raw_close.ffill().shift(1)
    targets=pd.DataFrame(np.nan,index=dates,columns=symbols)
    audits=[]
    for date,g in components.groupby('date',sort=True):
        if date not in dates:continue
        q=g.set_index('symbol')['L_NUMBER'].astype(float)
        pp=prev.loc[date].reindex(q.index)
        value=q*pp
        fallback=g.set_index('symbol')['F_TDJE'].astype(float).fillna(0).clip(lower=0)
        unavailable=(q>0)&pp.isna()
        # Unknown values remain cash, never redistribute missing allocations.
        value=value.where(~unavailable,fallback).fillna(0)
        nav=float(basic.loc[date,'NAVPERCU'])
        assert nav>0 and (q>=0).all()
        w=value/nav
        scale=min(1.,.98/w.sum()) if w.sum()>0 else 1.
        intended=w*scale
        w=intended.where(~unavailable,0.)
        row=pd.Series(0.,index=symbols);row.loc[w.index]=w
        targets.loc[date]=row
        audits.append({'date':date,'components':len(g),'raw_stock_weight':float(value.sum()/nav),
                       'invested_target':float(w.sum()),'missing_price_stocks':int(unavailable.sum()),
                       'missing_weight_proxy':float(intended[unavailable].sum()),
                       'unknown_value_stocks':int((unavailable&(fallback==0)).sum()),
                       'zero_quantity_stocks':int((q==0).sum()),
                       'zero_quantity_cash':float(fallback[q==0].sum()/nav)})
    return targets,pd.DataFrame(audits).set_index('date')

def main():
    RESULT.mkdir(exist_ok=True)
    components=pd.read_parquet(OUT/'components.parquet')
    components['date']=pd.to_datetime(components.date)
    components['symbol']=components.C_STOCKCODE+np.where(components.C_STOCKCODE.str.startswith('6'),'.SH','.SZ')
    assert components.C_STOCKCODE.str.match(r'^[036]\d{5}$').all(), 'non-stock code requires explicit handling'
    basic=pd.read_json(OUT/'basic.json').set_index('date');basic.index=pd.to_datetime(basic.index)
    benchmark=pd.read_parquet(ROOT/'data_expansion/normalized/benchmark_index_daily.parquet')
    benchmark=benchmark.loc[(benchmark.symbol=='000905.SH')&(benchmark.date>='2023-03-01')&(benchmark.date<='2026-09-17')].set_index('date').sort_index()
    dates=benchmark.index;symbols=sorted(components.symbol.unique())
    cols=['date','symbol','open','close','raw_close','amount','is_st_bs']
    stock=pd.read_parquet(ROOT/'data_expansion/normalized/daily_stock_panel.parquet',columns=cols,
        filters=[('date','>=',pd.Timestamp('2023-03-01')),('date','<=',pd.Timestamp('2026-09-17')),('symbol','in',symbols)])
    present=set(stock.symbol);added=[];unavailable=[]
    for symbol in sorted(set(symbols)-present):
        a=read_extra(symbol,'forward');r=read_extra(symbol,'none')
        if a.empty or r.empty:unavailable.append(symbol);continue
        z=a[['open','close']].join(r[['close','amount']].rename(columns={'close':'raw_close'}),how='outer')
        z['symbol']=symbol;z['is_st_bs']=np.nan;added.append(z.reset_index())
    if added:stock=pd.concat([stock]+added,ignore_index=True)
    assert not stock.duplicated(['date','symbol']).any()
    p={c:stock.pivot(index='date',columns='symbol',values=c).reindex(index=dates,columns=symbols).astype(float) for c in cols[2:]}
    targets,coverage=make_targets(components,basic,p['raw_close'],dates,symbols)
    targets.to_parquet(RESULT/'pcf_date_targets.parquet');coverage.to_parquet(RESULT/'coverage.parquet')
    # ST status is lagged; historical PCF security names supplement unavailable flags.
    st=components.assign(st=components.C_STOCKSHORT.str.upper().str.contains('ST',regex=False)).pivot(index='date',columns='symbol',values='st').reindex(index=dates,columns=symbols).ffill().shift(1).fillna(False)
    band=np.broadcast_to(np.array([.20 if s.startswith(('300','301','688','689')) else .10 for s in symbols]),(len(dates),len(symbols))).copy()
    band=np.where((p['is_st_bs'].shift(1)==1)|st,.05,band)
    # Growth/STAR ST stocks keep the board's 20% rule in this date range.
    for j,s in enumerate(symbols):
        if s.startswith(('300','301','688','689')):band[:,j]=.20
    op=p['open'].where(p['amount']>0);close=p['close']
    first=components.date.min();start=dates[dates.get_loc(first)+5]
    series={};rows=[];execution=[]
    for lag in [1,3,5]:
        shifted=targets.shift(lag-1) # simulate consumes prior row at next open.
        for cost in [COST]:
            name=f'pcf_lag{lag}_cost{int(cost*10000)}bp'
            r=simulate(op,close,shifted,cost,start,limit=band)
            series[name]=r;r.to_parquet(RESULT/(name+'.parquet'))
        for d in components.date.unique():
            i=dates.get_loc(d)
            if i+lag<len(dates):
                execution.append({'pcf_date':pd.Timestamp(d),'price_date':dates[i-1],'trade_date':dates[i+lag],'lag':lag})
    for symbol,name in [('563030.SH','etf563030_buyhold'),('510500.SH','etf510500_buyhold')]:
        a=read_extra(symbol,'forward').reindex(dates)
        assert a.loc[start:,'close'].notna().all(), 'ETF comparison coverage incomplete'
        w=pd.DataFrame(np.nan,index=dates,columns=[symbol]);w.loc[dates[dates.get_loc(start)-1],symbol]=.98
        r=simulate(a[['open']].rename(columns={'open':symbol}),a[['close']].rename(columns={'close':symbol}),w,COST,start)
        series[name]=r;r.to_parquet(RESULT/(name+'.parquet'))
    tri_path=OUT/'H00905_csindex_raw.json'
    tri=pd.DataFrame(json.loads(tri_path.read_text(encoding='utf-8'))['data'])
    assert tri.indexCode.eq('H00905').all() and not tri.tradeDate.duplicated().any()
    tri['date']=pd.to_datetime(tri.tradeDate,format='%Y%m%d')
    tri=tri.set_index('date').sort_index().reindex(dates)
    assert tri.close.notna().all() and (tri.close>0).all(), 'TRI coverage incomplete; no price-index fallback'
    br=tri.close.pct_change(fill_method=None).loc[start:]
    series['csi500_total_return_index']=pd.DataFrame({'ret':br,'nav':(1+br).cumprod()})
    periods=[('full',start,dates[-1])]+[(str(y),max(start,pd.Timestamp(y,1,1)),min(dates[-1],pd.Timestamp(y,12,31))) for y in [2023,2024,2025,2026]]+[('2026H2',pd.Timestamp('2026-07-01'),dates[-1])]
    reference=series['csi500_total_return_index'].ret
    for name,r in series.items():
        for period,lo,hi in periods:
            sub=r.loc[lo:hi];ref=reference.loc[lo:hi]
            relative=((1+sub.ret)/(1+ref)).cumprod()
            peak=np.maximum.accumulate(np.r_[1.,relative.values])[1:]
            m=dict(days=len(sub),excess_total=float(relative.iloc[-1]-1),
                excess_annual=float(relative.iloc[-1]**(252/len(sub))-1),
                excess_max_drawdown=float((relative.values/peak-1).min()),
                model=name,period=period,start=str(sub.index.min().date()),end=str(sub.index.max().date()))
            if 'turnover' in sub:m['annual_two_way_turnover']=float(sub.turnover.mean()*252);m['average_exposure']=float(sub.exposure.mean());m['average_blocked_order_fraction']=float(sub.blocked.mean())
            rows.append(m)
    summary=pd.DataFrame(rows);summary.to_json(RESULT/'summary.json',orient='records',indent=2,force_ascii=False)
    pd.concat({k:v.ret for k,v in series.items()},axis=1).to_parquet(RESULT/'daily_returns.parquet')
    ex=pd.DataFrame(execution);assert (ex.pcf_date<ex.trade_date).all() and (ex.price_date<ex.pcf_date).all()
    ex.to_parquet(RESULT/'execution_audit.parquet')
    # Cutoff invariance: no post-cutoff PCF or prices can affect prior targets or NAV.
    cut=dates[dates.get_loc(start)+80]
    subcomp=components[components.date<=cut]
    ct,_=make_targets(subcomp,basic.loc[:cut],p['raw_close'].loc[:cut],dates[dates<=cut],symbols)
    pd.testing.assert_frame_equal(ct,targets.loc[:cut])
    short=simulate(op.loc[:cut],close.loc[:cut],targets.loc[:cut],COST,start,limit=band[:dates.get_loc(cut)+1])
    pd.testing.assert_frame_equal(short,series['pcf_lag1_cost10bp'].loc[:cut])
    # No target for a missing PCF date means no new trade; holdings are carried.
    holdings_gaps=(close.isna()&close.ffill().notna()).sum().sum()
    audit={'start':str(start.date()),'end':str(dates[-1].date()),'pcf_days':int(components.date.nunique()),'stocks':len(symbols),
           'supplemented_stocks':len(added),'no_price_symbols':unavailable,'missing_price_pcf_rows':int(coverage.missing_price_stocks.sum()),
           'max_missing_weight_proxy':float(coverage.missing_weight_proxy.max()),'unvalued_pcf_rows':int(coverage.unknown_value_stocks.sum()),
           'calendar_gaps_in_prices_all_symbols':int(holdings_gaps),'cutoff_invariance_target_and_nav_passed':True,
           'execution_date_checks_passed':True,'historical_publication_timestamp_verified':False,
           'benchmark_sha256':hashlib.sha256(tri_path.read_bytes()).hexdigest(),
           'benchmark_rows':len(tri),'benchmark_dates_match_passed':True,
           'original_protocol_sha256':hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest(),
           'user_requested_reporting_override':CONVENTIONS}
    (RESULT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2),flush=True)
    print(summary.loc[summary.period=='full',['model','period','excess_total','excess_annual','excess_max_drawdown']].to_string(index=False),flush=True)

if __name__=='__main__':main()
