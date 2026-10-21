"""Isolated hourly candidate from immutable observed sources.

Preserves existing valid bars and verified repair overlay. Donor quotes require
raw daily O/C and volume corroboration and lie inside the independent daily
price range. Exact whole-day OHLC corroboration is tracked separately. Quotes
that missed an intraday traded extreme are never expanded to invent a price.
"""
import argparse,hashlib,io,json,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(1,str(Path(__file__).resolve().parents[1]))
from public_hf_backfill import ROOT,OUT as HFOUT
from acquire_union_daily_factors import OUT as BAO
sys.path.insert(0,str(ROOT/'gru_research/.deps'));sys.path.insert(0,str(ROOT))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np
from gru_research.minute_data import HOUR_FEATURES,SIGNAL_MINUTES,EXEC_MINUTES,add_features_and_labels,aggregate_stock_day

OUT=ROOT/'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_closing_rule_v4'
PRICE_NAMES=['open','high','low','close'];OHLC=['open','high','low','close','volume','raw_open','raw_high','raw_low','raw_close','adjustment_factor']
END_MINUTES=np.r_[np.arange(571,691),np.arange(781,901)]


def divide(a,b):
    result=np.full(np.shape(a),np.nan,dtype=float);np.divide(a,b,out=result,where=np.isfinite(b)&(b!=0));return result


def aggregate_matrix(symbol,days,raw,volume,factors):
    """The original four-hour and execution formulas, over observed 240 bars."""
    assert raw.shape==(len(days),240,4) and volume.shape==(len(days),240)
    assert np.isfinite(raw).all() and (raw>0).all() and np.isfinite(volume).all() and (volume>=0).all()
    assert np.isfinite(factors).all() and (factors>0).all()
    a=raw.reshape(-1,60,4);v=volume.reshape(-1,60).astype(float);factor=np.repeat(factors,4)
    o,h,l,c=a[:,0,0],a[:,:,1].max(axis=1),a[:,:,2].min(axis=1),a[:,-1,3]
    minute_returns=np.log(a[:,:,3]/np.c_[o,a[:,:-1,3]]);squares=minute_returns**2
    mean=minute_returns.mean(axis=1);std=minute_returns.std(axis=1);logv=np.log1p(v);logv_std=logv.std(axis=1)
    normalized=divide(minute_returns-mean[:,None],np.broadcast_to(std[:,None],minute_returns.shape))
    skew=(normalized**3).mean(axis=1);skew[std<=1e-12]=np.nan
    correlation=divide(((minute_returns-mean[:,None])*(logv-logv.mean(axis=1)[:,None])).mean(axis=1),std*logv_std)
    correlation[(std<=1e-12)|(logv_std<=1e-12)]=np.nan
    path=np.abs(np.diff(np.c_[o,a[:,:,3]],axis=1)).sum(axis=1);totalv=v.sum(axis=1);sumsq=squares.sum(axis=1)
    features=np.column_stack([np.sqrt(sumsq),skew,minute_returns.max(axis=1),minute_returns.min(axis=1),
        divide(np.where(minute_returns>0,squares,0).sum(axis=1),sumsq),correlation,divide(v[:,-15:].sum(axis=1),totalv),
        (v==0).mean(axis=1),divide(abs(c-o),path),divide(np.where(minute_returns>0,v,0).sum(axis=1),totalv)])
    windows=a[:,1:6];window_v=v[:,1:6].sum(axis=1);window_complete=window_v>0
    midnight=np.repeat(pd.to_datetime(days).tz_localize('Asia/Shanghai').tz_convert('UTC').asi8,4)
    slot=np.tile(np.arange(4),len(days));complete=totalv>0
    frame=pd.DataFrame({'date':np.repeat(days,4),'symbol':symbol,'slot':slot,'signal_time':midnight+SIGNAL_MINUTES[slot]*60*10**9,
        'hour_complete':complete,'minute_count':60,'open':o*factor,'high':h*factor,'low':l*factor,'close':c*factor,'volume':totalv,
        'raw_open':o,'raw_high':h,'raw_low':l,'raw_close':c,'adjustment_factor':factor,
        'window_start_time':midnight+EXEC_MINUTES[slot]*60*10**9,'window_end_time':midnight+(EXEC_MINUTES[slot]+5)*60*10**9,
        'window_complete':window_complete,'window_price':windows[:,:,0].mean(axis=1)*factor,
        'window_raw_price':windows[:,:,0].mean(axis=1),'window_volume':window_v,'window_tradable':window_complete,
        'window_all_flat':window_complete&(windows[:,:,1]==windows[:,:,2]).all(axis=1)})
    frame[HOUR_FEATURES[14:]]=features
    frame.loc[~complete,OHLC+HOUR_FEATURES[14:]]=np.nan
    frame.loc[~window_complete,['window_price','window_raw_price','window_volume']]=np.nan
    return frame


def observed_arrays(symbol,days):
    p=HFOUT/'snapshot/data/stock_1m'/symbol[-2:]/(symbol[:6]+'.parquet')
    q=pq.read_table(p,filters=[('timestamp','>=',pd.Timestamp(min(days))),('timestamp','<',pd.Timestamp(max(days))+pd.Timedelta(days=1))]).to_pandas()
    wanted=set(days);q=q[q.timestamp.dt.strftime('%Y-%m-%d').isin(wanted)].copy()
    ns=q.timestamp.to_numpy(dtype='datetime64[ns]').astype(np.int64);clock=ns%(86400*10**9)//(60*10**9)
    regular=q[np.isin(clock,END_MINUTES)].copy();regular['date']=regular.timestamp.dt.strftime('%Y-%m-%d')
    assert regular.groupby('date').size().eq(240).all() and list(regular.date.drop_duplicates())==list(days)
    assert np.array_equal(np.tile(END_MINUTES,len(days)),regular.timestamp.dt.hour.to_numpy()*60+regular.timestamp.dt.minute.to_numpy())
    raw=regular[PRICE_NAMES].to_numpy().round(2).reshape(len(days),240,4);volume=regular.volume.to_numpy().reshape(len(days),240).astype(float)
    boundary=q[(clock==570)&q.volume.gt(0)].copy();boundary['date']=boundary.timestamp.dt.strftime('%Y-%m-%d')
    positions={d:i for i,d in enumerate(days)}
    for r in boundary.to_dict('records'):
        i=positions[r['date']];raw[i,0,0]=round(r['open'],2);raw[i,0,1]=max(raw[i,0,1],round(r['high'],2));raw[i,0,2]=min(raw[i,0,2],round(r['low'],2));volume[i,0]+=r['volume']
    return raw,volume


def factor_state(obj,dates):
    from validated_factor_bridge import override_factor_state
    override=override_factor_state(obj,dates)
    if override is not None:return override
    events=pd.DataFrame(obj['factors'],columns=obj['factor_fields'])
    if events.empty:return np.ones(len(dates),dtype=float)
    assert events.code.eq(obj['code']).all() and not events.dividOperateDate.duplicated().any()
    events=events.sort_values('dividOperateDate');values=pd.to_numeric(events.foreAdjustFactor,errors='raise').to_numpy(float)
    assert np.isfinite(values).all() and (values>0).all()
    positions=np.searchsorted(events.dividOperateDate.to_numpy(),np.array(dates),side='right')-1
    out=np.full(len(dates),np.nan);valid=positions>=0;out[valid]=values[positions[valid]];return out


def donor_qualification(audit,primary):
    joined=audit.merge(primary,on='date',how='left',validate='one_to_one')
    present=joined.code.notna();trading=joined.tradestatus.eq('1')&(joined.volume>0)
    same_open=(joined.raw_open-joined.open).abs()<=1e-8;same_close=(joined.raw_close-joined.close).abs()<=1e-8
    same_volume=(joined.raw_volume-joined.volume).abs()<=100
    in_range=(joined.raw_high<=joined.high+1e-8)&(joined.raw_low>=joined.low-1e-8)
    from validated_historical_closing import matches as historical_closing_matches
    historical_closing=historical_closing_matches(joined)
    valid=joined.grid_valid&present&trading&same_open&(same_close|historical_closing)&same_volume&in_range
    exact=np.column_stack([(joined['raw_'+k]-joined[k]).abs()<=1e-8 for k in PRICE_NAMES]).all(axis=1)
    joined['donor_corroborated']=valid;joined['donor_daily_all_ohlc_exact']=valid&exact
    joined['donor_historical_closing_reference_corroborated']=historical_closing
    joined['donor_daily_closing_reference_basis']=np.where(historical_closing,'observed_final_minute_VWAP_concordant_with_historical_daily_close','raw_last_trade_close')
    return joined


def validate_matrix():
    from audit_hf_minute_samples import normalize_day
    checks=[]
    for symbol in ['000034.SZ','600754.SH','300244.SZ']:
        days=['2019-03-29','2019-06-06','2024-12-06'];raw,vol=observed_arrays(symbol,days);factors=np.array([.713014,.889966,1.])
        vector=aggregate_matrix(symbol,days,raw,vol,factors)
        for i,day in enumerate(days):
            left=np.r_[np.arange(570,690),np.arange(780,900)];timestamps=pd.Timestamp(day)+pd.to_timedelta(left,unit='min')
            x=pd.DataFrame(raw[i],columns=PRICE_NAMES);x['volume']=vol[i];x['股票代码']=symbol[:6];x['datetime']=timestamps.strftime('%Y-%m-%d %H:%M:%S.%f')
            x['datetime_nano']=timestamps.tz_localize('Asia/Shanghai').tz_convert('UTC').asi8;x['qfq_ratio']=factors[i]
            for k in PRICE_NAMES:x['qfq_'+k]=x[k]*factors[i]
            b=io.BytesIO();x.to_parquet(b,index=False);gold,audit=aggregate_stock_day((day,symbol,b.getvalue()));assert not audit['error'] and audit['invalid_rows']==0
            for slot,g in enumerate(gold):
                r=vector.iloc[i*4+slot]
                for key,value in g.items():
                    if isinstance(value,(float,np.floating)):assert np.isclose(value,r[key],atol=1e-11,rtol=1e-11,equal_nan=True),(symbol,day,slot,key,value,r[key])
                    else:assert value==r[key],(symbol,day,slot,key)
            checks.append({'symbol':symbol,'date':day,'hour_rows':4,'mismatches':0})
    OUT.mkdir(exist_ok=True);receipt={'passed':True,'checks':checks,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'reference':'Original aggregate_stock_day over separately serialized observed minute bars.'}
    (OUT/'aggregation_validation.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)


def build_symbol(symbol,calendar,repair):
    objpath=BAO/'normalized'/(symbol+'.json');obj=json.loads(objpath.read_text())
    assert obj['symbol']==symbol and obj['end']=='2026-09-23'
    primary=pd.DataFrame(obj['rows'],columns=obj['fields'])
    assert primary.code.eq(obj['code']).all() and primary.adjustflag.eq('3').all()
    for k in PRICE_NAMES+['volume','amount','turn']:primary[k]=pd.to_numeric(primary[k],errors='coerce')
    assert not primary.date.duplicated().any()
    factors=factor_state(obj,calendar);factor_by_day=dict(zip(calendar,factors))
    audit=pq.read_table(HFOUT/'union_grid_audit_v2'/(symbol+'.parquet')).to_pandas()
    donor=donor_qualification(audit,primary);donor=donor[donor.date.isin(calendar)].copy()
    donor['factor']=donor.date.map(factor_by_day)
    donor['donor_corroborated'] &= np.isfinite(donor.factor)&donor.factor.gt(0)
    donor['retrospective_source_qa_only']=True
    accepted=donor[donor.donor_corroborated].sort_values('date')
    source=ROOT/'gru_research/artifacts/minute_raw_by_symbol'/('symbol='+symbol)
    if source.exists():base=pq.read_table(source).to_pandas()
    else:base=repair.iloc[:0].copy()
    base['symbol']=symbol;base['hour_source']='original_minute_archive'
    overlay=repair[repair.symbol.eq(symbol)].copy();overlay['hour_source']='independently_verified_2019_repair'
    columns=set(base.columns)|set(overlay.columns)
    base=pd.concat([base.reindex(columns=sorted(columns)),overlay.reindex(columns=sorted(columns))],ignore_index=True)
    base=base.drop_duplicates('signal_time',keep='last').sort_values('signal_time').set_index('signal_time')
    original_complete=base.hour_complete.fillna(False).astype(bool).sum()
    from validated_alias_minutes import incoming_for_symbol,provenance as alias_provenance
    alias=incoming_for_symbol(symbol).set_index('signal_time')
    alias_added=0;alias_new_windows=0
    if len(alias):
        alias['hour_source']='corroborated_original_delivery_official_old_code'
        choose=alias.hour_complete&~base.hour_complete.reindex(alias.index).eq(True)
        selected=alias[choose];alias_added=len(selected)
        alias_new_windows=int((selected.window_complete&~base.window_complete.reindex(selected.index).eq(True)).sum())
        base=pd.concat([base,selected]).reset_index().drop_duplicates('signal_time',keep='last').set_index('signal_time').sort_index()
    # A common observed corporate-action state is applied to raw retained prices.
    # No observed price is forward-filled or inferred from a daily price.
    bf=base.date.map(factor_by_day).to_numpy(float);factor_ok=np.isfinite(bf)&(bf>0)
    base['adjustment_factor']=bf
    for k in PRICE_NAMES:base[k]=base['raw_'+k].to_numpy(float)*bf
    base['window_price']=base.window_raw_price.to_numpy(float)*bf
    base['hour_complete']=base.hour_complete.eq(True)&factor_ok
    base['window_complete']=base.window_complete.eq(True)&factor_ok
    base['window_tradable']=base.window_tradable.eq(True)&factor_ok
    primary_status=primary.set_index('date').tradestatus
    suspension=base.date.map(primary_status).eq('0')
    base.loc[suspension,['hour_complete','window_complete','window_tradable']]=False
    added=0;new_windows=0;historical_closing_added=0
    if len(accepted):
        days=accepted.date.tolist();raw,vol=observed_arrays(symbol,days)
        incoming=aggregate_matrix(symbol,days,raw,vol,accepted.factor.to_numpy(float)).set_index('signal_time')
        incoming['hour_source']='corroborated_hf_observed_quotes'
        exact=dict(zip(accepted.date,accepted.donor_daily_all_ohlc_exact))
        incoming['donor_daily_all_ohlc_exact']=incoming.date.map(exact)
        closing=dict(zip(accepted.date,accepted.donor_historical_closing_reference_corroborated))
        incoming['historical_closing_reference_corroborated']=incoming.date.map(closing)
        incoming.loc[incoming.historical_closing_reference_corroborated,'hour_source']='corroborated_hf_preauction_closing_statistic'
        old_complete=base.hour_complete.reindex(incoming.index).eq(True)
        choose=incoming.hour_complete&~old_complete
        selected=incoming[choose];added=len(selected)
        historical_closing_added=int(selected.historical_closing_reference_corroborated.sum())
        # The source version is explicit when the new hour replaces execution.
        old_windows=base.window_complete.reindex(selected.index).eq(True)
        new_windows=int((selected.window_complete&~old_windows).sum())
        base=pd.concat([base,selected]).reset_index().drop_duplicates('signal_time',keep='last').set_index('signal_time').sort_index()
    base=base.reset_index();base=base[base.date.isin(calendar)]
    if base.empty:raise ValueError('No observed symbol rows in requested grid')
    features=add_features_and_labels(base,calendar)
    assert len(features)==4*len(calendar) and not features.signal_time.duplicated().any()
    destination=OUT/'symbol_hour_features'/(symbol+'.parquet');destination.parent.mkdir(exist_ok=True)
    features.to_parquet(destination,index=False,compression='zstd')
    donor.to_parquet(OUT/'donor_daily_audit'/(symbol+'.parquet'),index=False,compression='zstd')
    from validated_factor_bridge import provenance
    from validated_historical_closing import provenance as closing_provenance
    receipt={'factor_bridge_provenance':provenance(symbol),'old_code_minute_provenance':alias_provenance(symbol),
        'historical_closing_provenance':closing_provenance(symbol),'new_historical_closing_statistic_hours':historical_closing_added,
        'new_old_code_complete_hours':alias_added,'new_old_code_execution_windows':alias_new_windows,
        'symbol':symbol,'source_daily_factor_sha256':hashlib.sha256(objpath.read_bytes()).hexdigest(),
        'original_complete_hours_with_repair_before_factor_gate':int(original_complete),'new_donor_complete_hours':added,
        'new_donor_execution_windows':new_windows,'final_complete_hours':int(features.hour_complete.sum()),
        'final_window80_complete':int(features.window80_complete.sum()),'donor_trading_days_qualified':len(accepted),
        'donor_days_all_daily_ohlc_exact':int(accepted.donor_daily_all_ohlc_exact.sum()),
        'original_source_bytes_modified':False,'missing_minute_bars_invented':False,'prices_forward_filled':False,
        'source_hour_parquet_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    (OUT/'symbol_receipts'/(symbol+'.json')).write_text(json.dumps(receipt,indent=2))
    return receipt


def main():
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['validate','available','dense']);args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.phase=='validate':validate_matrix();return
    validation=json.loads((OUT/'aggregation_validation.json').read_text())
    assert validation['passed'] and validation['source_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.phase=='dense':
        from dense_backfill_candidate import main as build_dense
        build_dense();return
    for name in ['symbol_hour_features','symbol_receipts','donor_daily_audit']:(OUT/name).mkdir(exist_ok=True)
    metadata=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text());symbols=metadata['union_symbols'];calendar=metadata['dates']
    repair=pq.read_table(ROOT/'outputs/gru_beta_neutral_20261008/combined_2019_hour_repairs.parquet').to_pandas()
    available=[s for s in symbols if (BAO/'normalized'/(s+'.json')).exists()];done=0
    def build_or_check(s):
        receipt=OUT/'symbol_receipts'/(s+'.json');destination=OUT/'symbol_hour_features'/(s+'.parquet')
        if not receipt.exists():build_symbol(s,calendar,repair)
        else:
            obj=json.loads(receipt.read_text());assert obj['symbol']==s and destination.exists()
            assert obj['source_daily_factor_sha256']==hashlib.sha256((BAO/'normalized'/(s+'.json')).read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=[pool.submit(build_or_check,s) for s in available]
        for job in as_completed(jobs):
            job.result();done+=1
            if done%25==0:
                progress={'status':'building_available_hourly_symbols','symbols_complete':done,'symbols_available':len(available),
                    'symbols_total':len(symbols),'new_X_built':False,'production_ready':False,'checked_utc':datetime.now(timezone.utc).isoformat()}
                (OUT/'progress.json').write_text(json.dumps(progress,indent=2));print(json.dumps(progress),flush=True)
    progress={'status':'available_hourly_symbols_built','symbols_complete':done,'symbols_available':len(available),'symbols_total':len(symbols),
        'new_X_built':False,'production_ready':False,'checked_utc':datetime.now(timezone.utc).isoformat()}
    (OUT/'progress.json').write_text(json.dumps(progress,indent=2));print(json.dumps(progress),flush=True)


if __name__=='__main__':main()
