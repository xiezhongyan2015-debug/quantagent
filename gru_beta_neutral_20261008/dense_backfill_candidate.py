"""Assemble hourly tensors only after all source-backed stock files exist."""
import hashlib,json,sys,shutil
from pathlib import Path
from backfilled_hour_panel import ROOT,OUT,HOUR_FEATURES,OHLC
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
from gru_research.assemble import verify_labels


def sha(path):return hashlib.file_digest(Path(path).open('rb'),'sha256').hexdigest()


def main():
    original=ROOT/'gru_research/artifacts/dataset';calendar=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    symbols=calendar['union_symbols'];times=np.load(original/'times.npy');members=np.load(original/'members.npy')
    missing=[s for s in symbols if not (OUT/'symbol_receipts'/(s+'.json')).exists()]
    if missing:raise RuntimeError(f'Hourly source assembly incomplete: {len(missing)} stocks; no final tensors created')
    shape=members.shape;assert shape==(len(times),len(symbols))
    dest=OUT/'dataset';dest.mkdir(exist_ok=True)
    specs={'hour_features':(np.float32,shape+(24,),np.nan),'hour_complete':(np.bool_,shape,False),
        'labels':(np.float32,shape+(4,),np.nan),'execution_prices':(np.float32,shape,np.nan),'execution_raw_prices':(np.float32,shape,np.nan),
        'execution_complete':(np.bool_,shape,False),'execution_tradable':(np.bool_,shape,False),'execution_all_flat':(np.bool_,shape,False),
        'hour_ohlcv':(np.float32,shape+(10,),np.nan)}
    arrays={}
    for name,(dtype,dims,fill) in specs.items():
        arrays[name]=np.lib.format.open_memmap(dest/(name+'.npy'),mode='w+',dtype=dtype,shape=dims);arrays[name][:]=fill
    sources=[]
    for i,s in enumerate(symbols):
        p=OUT/'symbol_hour_features'/(s+'.parquet');r=json.loads((OUT/'symbol_receipts'/(s+'.json')).read_text());assert sha(p)==r['source_hour_parquet_sha256']
        f=pq.read_table(p).to_pandas();assert np.array_equal(f.signal_time.to_numpy(np.int64),times)
        arrays['hour_features'][:,i]=f[HOUR_FEATURES].to_numpy(np.float32);arrays['hour_complete'][:,i]=f.hour_complete.to_numpy(bool)
        arrays['labels'][:,i]=f[['y1','y2','y3','y4']].to_numpy(np.float32);arrays['hour_ohlcv'][:,i]=f[OHLC].to_numpy(np.float32)
        for field in ['execution_prices','execution_raw_prices','execution_complete','execution_tradable','execution_all_flat']:
            column={'execution_prices':'execution_price','execution_raw_prices':'execution_raw_price','execution_complete':'execution_window_complete'}.get(field,field)
            arrays[field][:,i]=f[column].to_numpy(specs[field][0])
        sources.append(r)
        if (i+1)%50==0:print(f'Validated hourly tensor assembly {i+1}/{len(symbols)}',flush=True)
    for a in arrays.values():a.flush()
    for name in ['times','members','earliest_execution_times','label_end_times']:shutil.copyfile(original/(name+'.npy'),dest/(name+'.npy'))
    earliest=np.load(dest/'earliest_execution_times.npy');ends=np.load(dest/'label_end_times.npy')
    checks=verify_labels(arrays['execution_prices'],arrays['labels'],times,earliest,ends,members)
    hour=arrays['hour_ohlcv'];mask=arrays['hour_complete'];raw=np.asarray(hour[:,:,5:9]);adjusted=np.asarray(hour[:,:,:4]);factor=np.asarray(hour[:,:,9])
    assert np.isfinite(raw[mask]).all() and (raw[mask]>0).all()
    assert np.allclose(adjusted[mask],raw[mask]*factor[mask,None],rtol=2e-6,atol=2e-6)
    assert (raw[:,:,1][mask]+1e-6>=raw[mask].max(axis=1)).all() and (raw[:,:,2][mask]-1e-6<=raw[mask].min(axis=1)).all()
    prefix=np.concatenate([np.zeros((1,len(symbols)),np.int32),np.cumsum(~mask,axis=0,dtype=np.int32)])
    window=np.zeros(shape,bool);window[79:]=prefix[80:]==prefix[:-80]
    years=np.repeat([int(d[:4]) for d in calendar['dates']],4);coverage={}
    for year in sorted(set(years)):
        m=years==year;pool=int(members[m].sum());valid=int((members[m]&window[m]).sum())
        coverage[str(year)]={'pool_stock_hours':pool,'complete_80_hours':valid,'complete_80_hour_coverage':valid/pool,
            'all_4_labels_eligible_stock_hours':int((members[m]&window[m]&np.isfinite(arrays['labels'][m]).all(axis=2)).sum())}
    np.save(dest/'window80_complete.npy',window);(dest/'symbols.json').write_text(json.dumps(symbols,indent=2))
    receipt={'status':'hourly_tensor_candidate_complete_slow_features_and_X_pending','shape':shape,'label_checks':checks,'coverage_by_year':coverage,
        'membership_equals_original':np.array_equal(members,np.load(original/'members.npy')),'corporate_action_factor':'Baostock foreAdjustFactor events <= each session date, six decimal vendor precision',
        'donor_gate':'240 valid observed regular minutes; positive-volume 09:30 boundary only; primary raw daily O/C exact, volume discrepancy <=100 shares, all quotes inside primary daily price range; positive factor state; traded hour volume >0',
        'donor_daily_high_low_exact_equality_required':False,'donor_high_low_extrema_disagreement_retained_and_reported':True,
        'original_verified_2019_repair_gates_changed':False,'source_tick_extrema_completeness_certified':False,
        'production_ready':False,'new_X_built':False,'original_frozen_dataset_modified':False,'source_minute_bars_invented':False,
        'prices_forward_filled':False,'retrospective_daily_source_QA_not_a_live_selection_rule':True,
        'symbol_source_receipts':sources,'new_donor_hours':sum(s['new_donor_complete_hours'] for s in sources),
        'source_code_sha256':{p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('backfilled_hour_panel.py')]}}
    (OUT/'hour_panel_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in receipt.items() if k!='symbol_source_receipts'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
