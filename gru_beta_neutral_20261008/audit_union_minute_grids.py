"""Vectorized full-union raw minute/grid audit, separate from admission.

Actual observations only. Corporate actions and independent daily price checks
are separate gates. This cannot certify a new feature/label panel.
"""
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
from public_hf_backfill import ROOT,OUT,REVISION,index
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

AUDIT=OUT/'union_grid_audit_v2'
MINUTE=60*10**9;DAY=1440*MINUTE


def audit_symbol(symbol):
    source=OUT/'snapshot/data/stock_1m'/symbol[-2:]/(symbol[:6]+'.parquet')
    q=pq.read_table(source,filters=[('timestamp','>=',pd.Timestamp('2017-01-01')),
                                  ('timestamp','<',pd.Timestamp('2026-08-07'))]).to_pandas()
    assert q.symbol.eq(symbol[:6]).all() and q.exchange.eq(symbol[-2:]).all()
    t=q.timestamp.to_numpy(dtype='datetime64[ns]').astype(np.int64)
    if (np.diff(t)<0).any():raise ValueError('Nonmonotonic source timestamps')
    dates=t//DAY;minutes=t%DAY//MINUTE
    starts=np.r_[0,1+np.flatnonzero(np.diff(dates))];ends=np.r_[starts[1:],len(q)]
    counts=ends-starts;days=pd.to_datetime(dates[starts]*DAY).strftime('%Y-%m-%d')
    regular=((minutes>=571)&(minutes<=690))|((minutes>=781)&(minutes<=900))
    opening=minutes==570;lunch=minutes==780;allowed=regular|opening|lunch
    prices=q[['open','high','low','close']].to_numpy();volume=q.volume.to_numpy();turnover=q.turnover.to_numpy()
    valid=(np.isfinite(prices).all(axis=1)&(prices>0).all(axis=1)&
           (np.abs(prices-prices.round(2))<=.0001).all(axis=1)&
           np.isfinite(volume)&(volume>=0)&np.isfinite(turnover)&(turnover>=0))
    p=prices.round(2)
    valid &= (p[:,1]+1e-8>=p.max(axis=1))&(p[:,2]-1e-8<=p.min(axis=1))
    duplicate=np.r_[False,np.diff(t)==0]
    wrong_seconds=t%MINUTE!=0
    def count(mask):return np.add.reduceat(mask.astype(np.int32),starts)
    complete=(count(regular)==240)&(count(~allowed)==0)&(count(duplicate)==0)&(count(~valid)==0)&(count(wrong_seconds)==0)&(count(lunch&(volume>0))==0)
    safe_volume=np.where(np.isfinite(volume)&(volume>=0),volume,0)
    # 09:30 is an independently observed boundary row, attached to first bar.
    slot=np.select([((minutes>=571)&(minutes<=630))|opening,(minutes>=631)&(minutes<=690),
                    (minutes>=781)&(minutes<=840),(minutes>=841)&(minutes<=900)],[0,1,2,3],default=-1)
    hour_volumes=np.column_stack([np.add.reduceat(np.where(slot==h,safe_volume,0),starts) for h in range(4)])
    frame=pd.DataFrame({'symbol':symbol,'date':days,'source_rows':counts,'regular_minutes':count(regular),
                        'invalid_rows':count(~valid),'duplicate_rows':count(duplicate),'outside_session_rows':count(~allowed),
                        'wrong_seconds_rows':count(wrong_seconds),'positive_lunch_boundary_rows':count(lunch&(volume>0)),
                        'grid_valid':complete,'raw_open':p[np.where(opening[starts]&(volume[starts]<=0),starts+1,starts),0],
                        'raw_high':np.maximum.reduceat(np.where(regular|(opening&(volume>0)),p[:,1],-np.inf),starts),
                        'raw_low':np.minimum.reduceat(np.where(regular|(opening&(volume>0)),p[:,2],np.inf),starts),'raw_close':p[ends-1,3],
                        'raw_volume':np.add.reduceat(np.where(regular|opening,safe_volume,0),starts),
                        'raw_turnover':np.add.reduceat(np.where(regular|opening,turnover,0),starts)})
    for h in range(4):frame[f'hour{h}_volume']=hour_volumes[:,h];frame[f'hour{h}_complete']=complete&(hour_volumes[:,h]>0)
    return frame


def finish(symbols):
    frames=[pq.read_table(AUDIT/(s+'.parquet')).to_pandas() for s in symbols]
    all_days=pd.concat(frames,ignore_index=True)
    path=AUDIT/'all_union_day_audit.parquet';all_days.to_parquet(path,index=False,compression='zstd')
    dataset=ROOT/'gru_research/artifacts/dataset';calendar=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['dates']
    members=np.load(dataset/'members.npy');base=np.load(dataset/'hour_complete.npy')
    observation=np.zeros_like(base);dindex={d:i for i,d in enumerate(calendar)}
    for j,s in enumerate(symbols):
        f=frames[j];positions=np.array([dindex.get(d,-1) for d in f.date]);ok=positions>=0
        for h in range(4):observation[positions[ok]*4+h,j]=f.loc[ok,f'hour{h}_complete']
    merged=base|observation
    def valid80(x):
        total=np.cumsum(x.astype(np.int32),axis=0);window=total.copy();window[80:]-=total[:-80]
        return (window==80)&(np.arange(len(x))[:,None]>=79)
    years=np.repeat(np.array([int(d[:4]) for d in calendar]),4);before=valid80(base);after=valid80(merged);coverage=[]
    for year in sorted(set(years)):
        mask=years==year;pool=int(members[mask].sum());a=int((before[mask]&members[mask]).sum());b=int((after[mask]&members[mask]).sum())
        coverage.append({'year':int(year),'pool_stock_hours':pool,'original_complete_80_hours':a,
                         'grid_only_potential_complete_80_hours':b,'original_coverage':a/pool,'grid_only_potential_coverage':b/pool})
    pd.DataFrame(coverage).to_csv(AUDIT/'grid_only_coverage_potential.csv',index=False)
    receipt={'status':'full_union_grid_audit_complete_price_factor_status_admission_pending','revision':REVISION,
             'symbols':len(symbols),'audited_stockdays':len(all_days),'source_minute_rows_audited':int(all_days.source_rows.sum()),
             'valid_day_grids':int(all_days.grid_valid.sum()),'incomplete_or_invalid_day_grids':int((~all_days.grid_valid).sum()),
             'valid_hour_observations':int(all_days[[f'hour{h}_complete' for h in range(4)]].sum().sum()),
             'coverage_potential':coverage,'production_admitted':False,'new_panel_built':False,
             'prices_or_missing_minutes_invented':False,'original_frozen_dataset_modified':False,
             'audit_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'limitation':'Potential coverage requires independent price, volume, corporate-action and tradability checks before inclusion. Grid validity alone is insufficient.'}
    (AUDIT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args();AUDIT.mkdir(exist_ok=True)
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    if args.limit:symbols=symbols[:args.limit]
    for i,s in enumerate(symbols):
        path=AUDIT/(s+'.parquet')
        if not path.exists():audit_symbol(s).to_parquet(path,index=False,compression='zstd')
        if (i+1)%25==0:
            progress={'status':'auditing','symbols_complete':i+1,'symbols_total':len(symbols),'checked_utc':datetime.now(timezone.utc).isoformat()}
            (AUDIT/'progress.json').write_text(json.dumps(progress,indent=2));print(json.dumps(progress),flush=True)
    if not args.limit:finish(symbols)


if __name__=='__main__':main()
