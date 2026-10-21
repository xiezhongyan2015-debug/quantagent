"""Evaluate direct boundary observations and whole-day replacements separately.

An unchanged-minute control evaluates the proposed hourly result against source
truth. This script publishes candidates and rejection reasons; it never admits
them into a training panel by merely passing a provider-level sanity check.
"""
from pathlib import Path
import hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_hf_minute_samples import OUT as HFOUT,ROOT,pq,pd,np,normalize_day
OUT=ROOT/'outputs/gru_beta_neutral_20261008'


def check_boundary(x,n,dr):
    r={'status':'rejected'}
    if dr is None:r['reason']='independent_daily_missing';return r
    opening=float(dr['open']);end=float(dr['close']);volume=float(dr['volume'])
    if abs(float(n.open.iloc[0])-opening)>1e-8 or abs(float(n.close.iloc[-1])-end)>1e-8:
        r['reason']='independent_daily_open_or_close_conflict';return r
    if abs(float(n.volume.sum())-volume)>100:
        r['reason']='independent_daily_volume_conflict';return r
    if abs(float(x.high.iloc[0])-float(n.high.iloc[0]))>1e-8 or abs(float(x.close.iloc[0])-float(n.close.iloc[0]))>1e-8:
        r['reason']='unchanged_first_minute_high_or_close_conflict';return r
    if abs(float(x.volume.iloc[0])-float(n.volume.iloc[0]))>100:
        r['reason']='first_minute_volume_conflict';return r
    raw=np.array([opening,x.high.iloc[:60].max(),min(n.low.iloc[0],x.low.iloc[1:60].min()),x.close.iloc[59]],float)
    if raw.min()<=0 or raw[1]<max(raw[0],raw[2],raw[3]) or raw[2]>min(raw[0],raw[1],raw[3]):
        r['reason']='repaired_hour_bounds_conflict';return r
    r.update(status='candidate_hour_boundary_observed',raw_open=raw[0],raw_high=raw[1],raw_low=raw[2],raw_close=raw[3],
             observed_first_minute_low=float(n.low.iloc[0]),first_minute_only_hf_fields_used='open,low',
             daily_high_or_low_disagreement_recorded=bool(abs(n.high.max()-float(dr['high']))>1e-8 or abs(n.low.min()-float(dr['low']))>1e-8))
    return r


def main():
    defects=pq.read_table(ROOT/'gru_research/artifacts/minute_invalid_details.parquet').to_pandas()
    bad={(r.date,r.symbol) for r in defects[defects.cause.eq('nonpositive_raw_price')].itertuples()}
    march=pq.read_table(ROOT/'gru_research/artifacts/minute_day_audit/2019-03-29.parquet').to_pandas()
    zero=set()
    for path in (OUT/'minute_original_selected/2019-03-29').glob('*.parquet'):
        x=pq.read_table(path).to_pandas()
        if x.volume.iloc[:180].sum()==0 and x.volume.sum()>0:zero.add(('2019-03-29',path.stem))
    originals=list((OUT/'minute_original_selected').glob('*/*.parquet'))
    grouped={}
    for p in originals:grouped.setdefault(p.stem,[]).append(p)
    rows=[];controls=[];source_files=[];completed=0
    for symbol,paths in sorted(grouped.items()):
        src=HFOUT/'snapshot/data/stock_1m'/symbol[-2:]/(symbol[:6]+'.parquet')
        if not src.exists():continue
        dates={p.parent.name for p in paths};start=pd.Timestamp(min(dates));end=pd.Timestamp(max(dates))+pd.Timedelta(days=1)
        h=pq.read_table(src,filters=[('timestamp','>=',start),('timestamp','<',end)]).to_pandas()
        hg={day:part for day,part in h.groupby(h.timestamp.dt.strftime('%Y-%m-%d')) if day in dates}
        daily_path=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'/f'raw_DEV_{symbol[-2:].lower()+symbol[:6]}.json'
        daily={}
        if daily_path.exists():
            obj=json.loads(daily_path.read_text());assert obj['error_code']=='0' and obj['params']['adjustflag']=='3'
            daily={r[0]:dict(zip(obj['fields'],r)) for r in obj['rows'] if r[0] in dates}
        digest=hashlib.sha256(src.read_bytes()).hexdigest();source_files.append({'symbol':symbol,'sha256':digest})
        for path in paths:
            day=path.parent.name;key=(day,symbol);x=pq.read_table(path).to_pandas()
            is_bad=key in bad;is_zero=key in zero
            intact=bool((x[['open','high','low','close']].to_numpy()>0).all() and x.volume.iloc[:60].sum()>0)
            if not is_bad and not is_zero and not intact:continue
            rec={'date':day,'symbol':symbol,'kind':'bad_boundary' if is_bad else 'zero_volume_original' if is_zero else 'intact_control',
                 'hf_sha256':digest,'original_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'status':'rejected'}
            if day not in hg:rec['reason']='no_hf_day';(controls if intact and not is_bad else rows).append(rec);continue
            n,reason=normalize_day(hg[day])
            if reason:rec['reason']=reason;(controls if intact and not is_bad else rows).append(rec);continue
            dr=daily.get(day)
            if is_zero:
                rec.update(hf_positive_first_180_volume=bool(n.volume.iloc[:180].sum()>0),
                           hf_first_180_volume=int(n.volume.iloc[:180].sum()),hf_total_volume=int(n.volume.sum()))
                if dr is None:rec['reason']='independent_daily_missing'
                else:
                    errors=np.abs(np.array([n.open.iloc[0],n.high.max(),n.low.min(),n.close.iloc[-1]])-
                                  np.array([float(dr[k]) for k in ['open','high','low','close']]))
                    rec['independent_daily_max_price_error']=float(errors.max())
                    rec['independent_daily_volume_error']=abs(n.volume.sum()-float(dr['volume']))
                    if (errors<1e-8).all() and rec['independent_daily_volume_error']<=100:
                        rec['status']='candidate_whole_day_exact_daily_corroboration'
                    else:rec['reason']='whole_day_daily_ohlc_or_volume_conflict'
                rows.append(rec);continue
            proposed=check_boundary(x,n,dr);rec.update(proposed)
            if intact and not is_bad:
                if rec['status']=='candidate_hour_boundary_observed':
                    truth=np.array([x.open.iloc[0],x.high.iloc[:60].max(),x.low.iloc[:60].min(),x.close.iloc[59]])
                    inferred=np.array([rec[k] for k in ['raw_open','raw_high','raw_low','raw_close']])
                    rec['withheld_hour_ohlc_match']=bool(np.allclose(truth,inferred,atol=1e-8,rtol=0))
                    rec['max_price_error']=float(np.abs(truth-inferred).max())
                controls.append(rec)
            else:rows.append(rec)
        completed+=1
        if completed%25==0:
            (OUT/'hf_2019_repair_progress.json').write_text(json.dumps({'status':'auditing','symbols_checked':completed,'target_rows_checked':len(rows),'control_rows_checked':len(controls)}))
            print('2019 source audit',completed,'symbols',len(rows),'target days',flush=True)
    frame=pd.DataFrame(rows);control=pd.DataFrame(controls)
    frame.to_csv(OUT/'hf_2019_direct_repair_candidates.csv',index=False);control.to_csv(OUT/'hf_2019_direct_repair_controls.csv',index=False)
    admitted=control[control.status.eq('candidate_hour_boundary_observed')]
    mismatch=admitted[~admitted.withheld_hour_ohlc_match.astype(bool)] if len(admitted) else admitted
    receipt={'status':'candidate_audit_only','source_files_available':len(source_files),'target_days_checked':len(frame),
             'target_status_counts':frame.status.value_counts().to_dict(),
             'target_rejections':frame.loc[frame.status.eq('rejected'),'reason'].value_counts().to_dict(),
             'controls_checked':len(control),'controls_admitted':len(admitted),'control_hour_ohlc_mismatches':len(mismatch),
             'control_validation_passed':len(admitted)>=50 and not len(mismatch),'frozen_dataset_modified':False,
             'source_minute_bars_invented':False,'candidate_repairs_panel_applied':False,'source_files':source_files}
    (OUT/'hf_2019_direct_repair_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='source_files'},ensure_ascii=False),flush=True)
    if len(mismatch):print(mismatch[['date','symbol','max_price_error']].head(15).to_string(index=False))


if __name__=='__main__':main()
