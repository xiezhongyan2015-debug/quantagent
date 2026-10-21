"""Explain candidate missing hours without inventing observations or exemptions.

Missing primary rows are not called pre-listing without a separate listing
reference. Suspensions remain in the original coverage denominator. A window
may contain several cause types; both multi-cause and exclusive counts persist.
"""
import hashlib,json,sys
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from backfilled_hour_panel import ROOT,OUT,BAO,factor_state
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd

REASONS=['no_primary_daily_reference','primary_suspension','primary_nonpositive_volume_or_invalid_status',
         'factor_state_unavailable','public_minute_tail_unavailable','no_observed_minute_stockday',
         'invalid_observed_minute_grid','daily_corroboration_failed','qualified_day_missing_or_zero_volume_hour']


def main():
    dest=OUT/'gap_audit';dest.mkdir(exist_ok=True)
    meta=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    dates=np.array(meta['dates']);symbols=meta['union_symbols'];years=np.repeat([int(d[:4]) for d in dates],4)
    ds=OUT/'dataset';complete=np.load(ds/'hour_complete.npy',mmap_mode='r');window=np.load(ds/'window80_complete.npy',mmap_mode='r')
    members=np.load(ds/'members.npy',mmap_mode='r');shape=complete.shape
    reasons=np.zeros(shape,np.uint8);status=np.zeros((len(dates),len(symbols)),np.uint8)
    requests=pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/backfill_requests.csv')
    minimum=np.zeros((len(dates),len(symbols)),bool);symbol_records=[];detail=[]
    for u,symbol in enumerate(symbols):
        obj=json.loads((BAO/'normalized'/(symbol+'.json')).read_text())
        primary=pd.DataFrame(obj['rows'],columns=obj['fields']).set_index('date').reindex(dates)
        present=primary.code.notna().to_numpy();volume=pd.to_numeric(primary.volume,errors='coerce').to_numpy()
        suspended=primary.tradestatus.eq('0').to_numpy();trading=primary.tradestatus.eq('1').to_numpy()&(volume>0)
        status[:,u]=np.where(~present,0,np.where(suspended,1,np.where(trading,2,3)))
        factor=factor_state(obj,dates);factor_valid=np.isfinite(factor)&(factor>0)
        donor=pq.read_table(OUT/'donor_daily_audit'/(symbol+'.parquet')).to_pandas().set_index('date').reindex(dates)
        donor_present=donor.grid_valid.notna().to_numpy();grid=donor.grid_valid.eq(True).to_numpy()
        admitted=donor.donor_corroborated.eq(True).to_numpy()
        cause=np.full(len(dates),REASONS.index('qualified_day_missing_or_zero_volume_hour')+1,np.uint8)
        # Earlier conditions take precedence; each is factual and reversible.
        ordered=[(~present,'no_primary_daily_reference'),(suspended,'primary_suspension'),
                 (present&~suspended&~trading,'primary_nonpositive_volume_or_invalid_status'),
                 (~factor_valid,'factor_state_unavailable'),
                 ((dates>'2026-08-06')&~donor_present,'public_minute_tail_unavailable'),
                 (~donor_present,'no_observed_minute_stockday'),(~grid,'invalid_observed_minute_grid'),
                 (~admitted,'daily_corroboration_failed')]
        assigned=np.zeros(len(dates),bool)
        for mask,name in ordered:
            use=mask&~assigned;cause[use]=REASONS.index(name)+1;assigned|=use
        reasons[:,u]=np.where(complete[:,u],0,np.repeat(cause,4))
        assert np.array_equal(reasons[:,u]>0,~complete[:,u])
        for r in requests[requests.symbol.eq(symbol)].itertuples():
            minimum[:,u]|=(dates>=r.requested_start)&(dates<=r.requested_end)
        for d in np.flatnonzero(np.any(~complete[:,u].reshape(-1,4),axis=1)):
            detail.append({'symbol':symbol,'date':dates[d],'missing_hours':int((~complete[d*4:d*4+4,u]).sum()),
                           'current_member_hours':int((members[d*4:d*4+4,u]&~complete[d*4:d*4+4,u]).sum()),
                           'minimum_requested':bool(minimum[d,u]),'cause':REASONS[int(cause[d])-1],
                           'primary_tradestatus':None if not present[d] else str(primary.tradestatus.iloc[d]),
                           'primary_volume':None if not np.isfinite(volume[d]) else float(volume[d])})
        symbol_records.append({'symbol':symbol,'minimum_requested_days':int(minimum[:,u].sum()),
             'minimum_trading_days':int((minimum[:,u]&(status[:,u]==2)).sum()),
             'minimum_complete_hours':int((np.repeat(minimum[:,u],4)&complete[:,u]).sum())})
        if (u+1)%200==0:print('Classified',u+1,'/',len(symbols),'stocks',flush=True)
    np.save(dest/'missing_hour_reason_codes.npy',reasons)
    pd.DataFrame(detail).to_parquet(dest/'missing_stockdays.parquet',index=False,compression='zstd')
    pd.DataFrame(symbol_records).to_csv(dest/'minimum_interval_by_symbol.csv',index=False)
    # No denominator is changed to make a gate pass.
    annual=[];cause_windows=[];unresolved=members&~window
    assigned=np.zeros(shape,bool)
    priority=['public_minute_tail_unavailable','no_observed_minute_stockday','invalid_observed_minute_grid',
              'daily_corroboration_failed','factor_state_unavailable','qualified_day_missing_or_zero_volume_hour',
              'no_primary_daily_reference','primary_nonpositive_volume_or_invalid_status','primary_suspension']
    for name in priority:
        hits=reasons==(REASONS.index(name)+1);prefix=np.r_[np.zeros((1,len(symbols)),np.int32),np.cumsum(hits,axis=0,dtype=np.int32)]
        affected=np.zeros(shape,bool)
        affected[79:]=(prefix[80:]-prefix[:-80])>0
        affected[:79]=prefix[1:80]>0
        exclusive=affected&unresolved&~assigned;assigned|=exclusive
        for year in sorted(set(years)):
            year=int(year)
            use=years==year;cause_windows.append({'year':year,'cause':name,
                'missing_member_hours':int((hits[use]&members[use]).sum()),
                'incomplete_member_windows_containing_cause':int((affected[use]&unresolved[use]).sum()),
                'exclusive_windows_by_declared_priority':int(exclusive[use].sum())})
    startup=unresolved&~assigned;assert not startup[79:].any()
    for year in sorted(set(years)):
        year=int(year)
        use=years==year;den=int(members[use].sum());num=int((members[use]&window[use]).sum())
        target=.95 if year==2019 else .995 if year>=2020 else None
        annual.append({'year':year,'original_member_stockhour_denominator':den,'complete80_stockhours':num,
                       'coverage':num/den,'target':target,'target_passed':None if target is None else num/den>=target,
                       'calendar_start_warmup_unavailable_windows':int(startup[use].sum())})
    minhours=np.repeat(minimum,4,axis=0);tradinghours=np.repeat(status==2,4,axis=0)
    tail=years==2026;tail&=np.repeat(dates>'2026-08-06',4)
    receipt={'checked_utc':datetime.now(timezone.utc).isoformat(),'original_denominators_preserved':True,
        'original_frozen_source_modified':False,'synthetic_or_forward_filled_prices':False,
        'source_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'reason_codes':dict(enumerate(['complete']+REASONS)),
        'exclusive_window_priority':priority,'missing_primary_rows_not_certified_as_prelisting':True,'coverage_by_year':annual,
        'minimum_intervals':{'interval_rows':len(requests),'symbols':len(symbols),'requested_stockdays':int(minimum.sum()),
          'requested_hours':int(minhours.sum()),'complete_hours':int((minhours&complete).sum()),
          'primary_trading_hours':int((minhours&tradinghours).sum()),'missing_primary_trading_hours':int((minhours&tradinghours&~complete).sum()),
          'august_september_nonmember_requested_hours':int((minhours[tail]&~members[tail]).sum())},
        'full_union_august_september':{'all_stockhours':int(tail.sum()*len(symbols)),
          'nonmember_hours':int((~members[tail]).sum()),'missing_nonmember_primary_trading_hours':int((~members[tail]&tradinghours[tail]&~complete[tail]).sum())}}
    pd.DataFrame(annual).to_csv(dest/'coverage_gates.csv',index=False)
    pd.DataFrame(cause_windows).to_csv(dest/'member_window_causes_by_year.csv',index=False)
    (dest/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
