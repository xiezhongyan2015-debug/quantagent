"""Compare observed minute days to cached/fresh anonymous raw daily references.

No conflicting quote is overwritten. Raw daily status is retained separately
from source grid validity and membership. Factor events are observed metadata.
"""
import hashlib,json,sys
from pathlib import Path
from public_hf_backfill import ROOT,OUT
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np
from acquire_union_daily_factors import FIELDS,verified_cache,OUT as BAO


def main():
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text());references=[];factors=[];provenance=[]
    for symbol in symbols:
        code=symbol[-2:].lower()+'.'+symbol[:6];new=BAO/'normalized'/(symbol+'.json')
        old=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'/('raw_DEV_'+code.replace('.','')+'.json')
        obj=None;path=None
        if new.exists():
            obj=json.loads(new.read_text());assert obj['symbol']==symbol and obj['fields']==FIELDS;path=new
            if obj['factors']:
                f=pd.DataFrame(obj['factors'],columns=obj['factor_fields']);f['symbol']=symbol;factors.append(f)
        else:
            cached=verified_cache(old,code)
            if cached is not None:obj=cached;path=old
        if obj is None:continue
        assert all(r[1]==code and r[9]=='3' for r in obj['rows'])
        frame=pd.DataFrame(obj['rows'],columns=FIELDS);frame['symbol']=symbol
        assert not frame.duplicated('date').any()
        for name in ['open','high','low','close','preclose','volume','amount','turn','pctChg']:frame[name]=pd.to_numeric(frame[name],errors='coerce')
        frame['source_id']=len(provenance);references.append(frame)
        provenance.append({'id':len(provenance),'symbol':symbol,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(frame),'fresh_through_2026':path==new})
    daily=pd.concat(references,ignore_index=True)
    dest=OUT/'union_grid_audit_v2';daily.to_parquet(dest/'independent_raw_daily_reference.parquet',index=False,compression='zstd')
    if factors:pd.concat(factors,ignore_index=True).to_parquet(dest/'observed_factor_events.parquet',index=False,compression='zstd')
    source=pq.read_table(dest/'all_union_day_audit.parquet').to_pandas()
    joined=source.merge(daily,on=['date','symbol'],how='left',validate='one_to_one')
    price_errors=np.column_stack([abs(joined['raw_'+k]-joined[k]) for k in ['open','high','low','close']])
    joined['daily_price_max_error']=np.max(price_errors,axis=1)
    joined['daily_volume_error']=abs(joined.raw_volume-joined.volume)
    joined['daily_turnover_relative_error']=abs(joined.raw_turnover-joined.amount)/joined.amount.where(joined.amount>0)
    present=joined.source_id.notna();trading=joined.tradestatus.eq('1')&(joined.volume>0)
    joined['independent_daily_available']=present
    joined['independent_trading_day']=present&trading
    joined['daily_ohlc_exact']=present&(joined.daily_price_max_error<=1e-8)
    joined['daily_ohlc_within_one_tick']=present&(joined.daily_price_max_error<=.01000001)
    joined['daily_close_exact']=present&((joined.raw_close-joined.close).abs()<=1e-8)
    joined['daily_volume_within_one_lot']=present&(joined.daily_volume_error<=100)
    joined['strict_daily_corroboration']=joined.grid_valid&trading&joined.daily_ohlc_exact&joined.daily_volume_within_one_lot
    joined['one_tick_daily_corroboration']=joined.grid_valid&trading&joined.daily_ohlc_within_one_tick&joined.daily_volume_within_one_lot
    joined.to_parquet(dest/'independent_daily_comparison.parquet',index=False,compression='zstd')
    use=joined[present&trading];annual=[]
    for year,g in use.groupby(use.date.str[:4]):
        annual.append({'year':int(year),'compared_trading_stockdays':len(g),**{c:int(g[c].sum()) for c in ['daily_ohlc_exact','daily_ohlc_within_one_tick','daily_close_exact','daily_volume_within_one_lot','strict_daily_corroboration','one_tick_daily_corroboration']}})
    pd.DataFrame(annual).to_csv(dest/'independent_daily_comparison_by_year.csv',index=False)
    receipt={'status':'independent_daily_comparison_current_available_references','source_symbols':len(provenance),
             'fresh_daily_and_factor_symbols':sum(p['fresh_through_2026'] for p in provenance),'raw_daily_reference_rows':len(daily),
             'observed_factor_event_rows':sum(len(f) for f in factors),'minute_stockdays_with_daily_reference':int(present.sum()),
             'trading_stockdays_compared':len(use),'comparison_by_year':annual,'minute_only_production_admitted':False,'new_panel_built':False,
             'source_prices_modified':False,'source_minute_bars_invented':False,'sources':provenance}
    (dest/'independent_daily_reference_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='sources'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
