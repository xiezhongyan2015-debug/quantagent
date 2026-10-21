"""Backfill daily observations from verified existing iFinD CSI1000 files.

Both datasets declare the same CPS:3, baseDate:1900-01-01 convention. Conflicting
duplicate keys are quarantined instead of resolved through source preference.
The daily layer is separate from minute inputs and historical membership.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gru_research/.deps'))
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/gru_beta_neutral_20261008'
SOURCES=['中证500_历年成分股_日频_2010-2026_iFinD_后复权',
         '中证1000_历年成分股_日频_2010-2026_iFinD_后复权']
FIELDS=['open','high','low','close','avgPrice','volume','amount','turnoverRatio']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    union=set(symbols);frames=[];provenance=[]
    for rank,folder in enumerate(SOURCES):
        source=ROOT/folder;meta=json.loads((source/'dataset.json').read_text())
        assert meta['adjustment_base']=='上市首日 / baseDate:1900-01-01'
        assert meta['api_parameters']=='Interval:D,CPS:3,baseDate:1900-01-01,Fill:Omit'
        db=sqlite3.connect((source/'manifest.sqlite').as_uri()+'?mode=ro',uri=True)
        records=db.execute("select path,sha256,rows from batches where rows>0 order by path").fetchall()
        db.close()
        for relative,expected,nrows in records:
            path=source/relative.replace('\\','/')
            if not path.exists():raise FileNotFoundError(path)
            digest=sha(path)
            if digest!=expected:raise ValueError(f'Existing source hash mismatch: {path}')
            frame=pq.read_table(path,columns=['thscode','date']+FIELDS).to_pandas()
            assert len(frame)==nrows
            frame=frame[frame.thscode.isin(union)&frame.date.between('2018-01-01','2026-09-23')].copy()
            if frame.empty:continue
            frame.rename(columns={'thscode':'symbol'},inplace=True)
            source_id=len(provenance)
            provenance.append({'id':source_id,'rank':rank,'source':folder,'path':str(path),
                               'sha256':digest,'target_rows':len(frame)})
            frame['source_id']=source_id;frame['source_rank']=rank;frames.append(frame)
        print(json.dumps({'source':folder,'files_verified':len(records),'target_rows':sum(len(f) for f in frames if f.source_rank.iloc[0]==rank)},ensure_ascii=False),flush=True)
    combined=pd.concat(frames,ignore_index=True)
    keys=['date','symbol'];duplicate=combined[combined.duplicated(keys,keep=False)]
    conflict_keys=[]
    if not duplicate.empty:
        grouped=duplicate.groupby(keys)[FIELDS]
        span=grouped.max()-grouped.min()
        scale=grouped.max().abs().clip(lower=1)
        conflict=(span>1e-8*scale).any(axis=1)
        conflict_keys=conflict[conflict].index.tolist()
    conflict_index=pd.MultiIndex.from_tuples(conflict_keys,names=keys)
    combined=combined[~pd.MultiIndex.from_frame(combined[keys]).isin(conflict_index)]
    combined.sort_values(keys+['source_rank','source_id'],inplace=True)
    combined.drop_duplicates(keys,inplace=True)
    valid=np.isfinite(combined[FIELDS]).all(axis=1)&(combined[['open','high','low','close']]>0).all(axis=1)
    valid &= (combined.high>=combined[['open','close','low']].max(axis=1))&(combined.low<=combined[['open','close','high']].min(axis=1))
    valid &= (combined.volume>=0)&(combined.amount>=0)
    invalid=combined[~valid].copy();combined=combined[valid].copy()
    combined.sort_values(keys,inplace=True)
    dest=OUT/'backfilled_daily_ifind_cps3.parquet'
    pq.write_table(pa.Table.from_pandas(combined,preserve_index=False),dest,compression='zstd')
    invalid.to_csv(OUT/'daily_backfill_invalid_rows.csv',index=False)
    pd.DataFrame(conflict_keys,columns=keys).to_csv(OUT/'daily_backfill_conflicting_keys.csv',index=False)
    dates=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['dates']
    by_symbol={s:g.set_index('date') for s,g in combined.groupby('symbol',sort=False)}
    requests=pd.read_csv(OUT/'backfill_requests.csv');coverage=[]
    for row in requests.to_dict('records'):
        sessions=[d for d in dates if row['requested_start']<=d<=row['requested_end']]
        frame=by_symbol.get(row['symbol'])
        base=donor=0
        if frame is not None:
            chosen=frame.reindex(sessions)
            base=int(chosen.source_rank.eq(0).sum());donor=int(chosen.source_rank.eq(1).sum())
        coverage.append(dict(**row,verified_ifind500_daily=base,new_ifind1000_daily=donor,
                             daily_still_missing=len(sessions)-base-donor))
    coverage=pd.DataFrame(coverage);coverage.to_csv(OUT/'daily_backfill_interval_coverage.csv',index=False)
    receipt={'status':'verified_daily_layer_saved_not_yet_panel_assembled',
             'symbols':len(symbols),'rows':len(combined),'new_donor_rows':int(combined.source_rank.eq(1).sum()),
             'source_files_verified':len(provenance),'conflicting_keys':len(conflict_keys),'invalid_rows':len(invalid),
             'price_convention':'iFinD CPS:3 / baseDate:1900-01-01 / Fill:Omit',
             'one_minute_prices_inferred':False,'membership_changed':False,'new_panel_built':False,
             'output':str(dest),'output_sha256':sha(dest),'sources':provenance,
             'interval_counts_may_overlap':True,
             'interval_totals':{k:int(coverage[k].sum()) for k in ['requested_sessions','verified_ifind500_daily','new_ifind1000_daily','daily_still_missing']}}
    (OUT/'daily_backfill_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='sources'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
