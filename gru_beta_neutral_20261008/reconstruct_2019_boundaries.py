"""Recover only uniquely identified hourly boundaries from independent sources.

Legacy five-slot prices have an affine corporate-action adjustment and cent
rounding. Fit the complete feasible affine polygon against unchanged minute
prices. Recover a raw hourly low only if its one-cent value is unambiguous, or
the unchanged later minutes provably determine the hour low. Never infer a
minute low, fabricate a minute bar, or interpolate an observation.
"""
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/gru_beta_neutral_20261008'
INTERVALS=[('10:00',0,30),('11:00',30,90),('12:00',90,120),('14:00',120,180),('15:00',180,240)]


def clip(poly,normal,bound):
    """Clip a convex polygon by normal dot vertex <= bound."""
    result=[]
    for i,current in enumerate(poly):
        previous=poly[i-1]
        dc=np.dot(normal,current)-bound;dp=np.dot(normal,previous)-bound
        inside=dc<=1e-10;was=dp<=1e-10
        if inside!=was:
            result.append(previous+(current-previous)*(dp/(dp-dc)))
        if inside:result.append(current)
    return np.array(result).reshape(-1,2)


def feasible_affine(pairs,tolerance=.005001):
    poly=np.array([[.000001,-10000],[100,-10000],[100,10000],[.000001,10000]],float)
    for raw,legacy in pairs:
        poly=clip(poly,np.array([raw,1.]),legacy+tolerance)
        if not len(poly):return poly
        poly=clip(poly,np.array([-raw,-1.]),-legacy+tolerance)
        if not len(poly):return poly
    return poly


def recover_boundary(x, dr, bars):
    """Identify hour OHLC without using the unknown first-minute open or low."""
    result={'status':'not_recovered'}
    independent_open=float(dr['open'])
    high_with_open=max(float(x.high.max()),independent_open)
    if float(dr['close'])!=float(x.close.iloc[-1]) or not np.isclose(float(dr['high']),high_with_open,atol=.000001,rtol=0):
        result['reason']='independent_raw_daily_high_close_conflict';return result
    known_low=float(x.low.iloc[1:].min())
    if float(dr['low'])>known_low+1e-6 or independent_open<=0:
        result['reason']='independent_daily_ohlc_conflict';return result
    if not all(label in bars.index for label,_,_ in INTERVALS) or bars.index.duplicated().any():
        result['reason']='legacy_five_slot_grid_incomplete';return result
    pairs=[]
    for label,start,end in INTERVALS:
        row=bars.loc[label];part=x.iloc[start:end]
        pairs.extend([(float(part.high.max()) if start>0 else max(float(part.high.max()),independent_open),float(row.high)),(float(part.close.iloc[-1]),float(row.close))])
        # The original source open is the previous minute's close; the
        # legacy interval open can be its first actual trade. Those are
        # different conventions and cannot be fitted as equal prices.
        if start>0:pairs.append((float(part.low.min()),float(row.low)))
    pairs.append((independent_open,float(bars.loc['10:00','open'])))
    poly=feasible_affine(pairs)
    if not len(poly):
        result['reason']='no_affine_cent_rounding_fit';return result
    y=float(bars.loc['10:00','low']);tol=.005001
    lo=float(np.min((y-tol-poly[:,1])/poly[:,0]));hi=float(np.max((y+tol-poly[:,1])/poly[:,0]))
    # The legacy first half-hour overlaps unchanged minute lows. Bound its
    # raw low accordingly; no assumptions about when the low occurred.
    hi=min(hi,float(x.low.iloc[1:30].min()),independent_open)
    other_hour_low=float(x.low.iloc[1:60].min())
    if hi < lo-1e-7:
        result['reason']='first_halfhour_low_constraints_conflict';return result
    tick_min=int(np.ceil((lo-1e-7)*100));tick_max=int(np.floor((hi+1e-7)*100))
    lows=(np.unique([min(tick_min/100,other_hour_low),min(tick_max/100,other_hour_low)])
          if tick_max>=tick_min else np.array([]))
    result.update(independent_raw_open=independent_open,raw_halfhour_low_lower=lo,
                  raw_halfhour_low_upper=hi,raw_hour_low_candidates=len(lows),
                  affine_a_min=float(poly[:,0].min()),affine_a_max=float(poly[:,0].max()),
                  unchanged_minute_low=other_hour_low)
    if len(lows)!=1 or lows[0]<=0:
        result['reason']='raw_hour_low_not_uniquely_identified';return result
    result.update(status='verified_hour_boundary_recovery',raw_open=independent_open,
                  raw_low=float(lows[0]),raw_high=max(float(x.high.iloc[:60].max()),independent_open),
                  raw_close=float(x.close.iloc[59]),first_minute_low_reconstructed=False,
                  original_minute_prices_modified=False)
    return result


def main():
    legacy=pq.read_table(OUT/'legacy_2019_selected_bars.parquet').to_pandas()
    groups={(d,s):g.set_index('legacy_time') for (d,s),g in legacy.groupby(['date','symbol'])}
    defects=pq.read_table(ROOT/'gru_research/artifacts/minute_invalid_details.parquet').to_pandas()
    defects=defects[defects.cause.eq('nonpositive_raw_price')].drop_duplicates(['date','symbol'])
    requested={s:set(g.date) for s,g in defects.groupby('symbol')}
    daily={};daily_sources=[]
    source_dir=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'
    for symbol,dates in requested.items():
        code=symbol[-2:].lower()+symbol[:6];path=source_dir/f'raw_DEV_{code}.json'
        if not path.exists():continue
        obj=json.loads(path.read_text())
        assert obj['error_code']=='0' and obj['params']['adjustflag']=='3'
        assert obj['params']['code']==symbol[-2:].lower()+'.'+symbol[:6]
        fields=obj['fields']
        selected=[dict(zip(fields,row)) for row in obj['rows'] if row[0] in dates]
        for row in selected:daily[(row['date'],symbol)]=row
        if selected:daily_sources.append({'symbol':symbol,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows_used':len(selected)})
    results=[]
    for item in defects.to_dict('records'):
        day,symbol=item['date'],item['symbol'];key=(day,symbol)
        result={'date':day,'symbol':symbol,'status':'not_recovered'}
        if key not in daily or key not in groups:
            result['reason']='independent_raw_daily_or_legacy_halfhour_missing';results.append(result);continue
        x=pq.read_table(OUT/'minute_original_selected'/day/(symbol+'.parquet')).to_pandas()
        if len(x)!=240 or x.datetime.str[11:16].iloc[0]!='09:30':
            result['reason']='unexpected_original_minute_grid';results.append(result);continue
        result.update(recover_boundary(x,daily[key],groups[key]))
        if result['status']=='verified_hour_boundary_recovery':
            result['original_payload_sha256']=hashlib.sha256((OUT/'minute_original_selected'/day/(symbol+'.parquet')).read_bytes()).hexdigest()
        results.append(result)
    df=pd.DataFrame(results);df.to_csv(OUT/'2019_hour_boundary_recovery.csv',index=False)
    receipt={'status':'hour_boundary_recovery_audited_not_yet_panel_applied',
             'defect_stockdays':len(defects),'verified_hour_boundaries':int(df.status.eq('verified_hour_boundary_recovery').sum()),
             'not_recovered_reasons':df.loc[df.status.ne('verified_hour_boundary_recovery'),'reason'].value_counts().to_dict(),
             'daily_sources':daily_sources,'method':'Feasible affine-adjustment polygon under cent rounding; uniquely identified raw hourly low; independent raw daily open.',
             'minute_bars_invented':False,'new_panel_built':False,
             'source_open_semantics':'Original minute source continues each prior close as the next open; legacy interval opens can differ. Only the independently observed daily opening is recovered.',
             'known_endpoints_used':'Legacy closes, highs and unchanged interval lows; later legacy opens deliberately excluded due to the distinct documented observed convention.'}
    (OUT/'2019_hour_boundary_recovery_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='daily_sources'},ensure_ascii=False))


if __name__=='__main__':main()
