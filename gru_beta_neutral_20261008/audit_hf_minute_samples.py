"""Empirical public minute-source audit; provider claims are not acceptance."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np
from public_hf_backfill import OUT, ROOT, REVISION, index

END_TIMES=pd.Index(list(pd.date_range('2000-01-01 09:31','2000-01-01 11:30',freq='min').strftime('%H:%M'))+
                   list(pd.date_range('2000-01-01 13:01','2000-01-01 15:00',freq='min').strftime('%H:%M')))


def normalize_day(q):
    """End-labeled bars, plus an observed opening boundary when it traded.

    No absent minute is synthesized. Positive-volume boundary OHLCV joins the
    first regular minute. Zero-volume boundary quotes are excluded because they
    can contain the previous close rather than today's opening trade.
    """
    q=q.copy();clock=q.timestamp.dt.strftime('%H:%M');q.index=clock
    if q.index.duplicated().any():return None,'duplicate_timestamp'
    if not END_TIMES.isin(q.index).all():return None,'missing_expected_end_minute'
    if not q.index.isin(END_TIMES.union(['09:30','13:00'])).all():return None,'outside_session'
    prices=q[['open','high','low','close']].to_numpy()
    if not np.isfinite(prices).all() or (prices<=0).any():return None,'nonpositive_or_nonfinite_price'
    if (np.abs(prices-np.round(prices,2))>.0001).any():return None,'price_not_cent_representation'
    q[['open','high','low','close']]=np.round(prices,2)
    if (q.high+1e-8<q[['open','low','close']].max(axis=1)).any() or (q.low-1e-8>q[['open','high','close']].min(axis=1)).any():return None,'ohlc_bounds'
    if q[['volume','turnover']].isna().any().any() or (q[['volume','turnover']]<0).any().any():return None,'invalid_volume_or_turnover'
    if '13:00' in q.index and q.loc['13:00','volume']>0:return None,'positive_volume_at_ambiguous_lunch_boundary'
    n=q.loc[END_TIMES].copy();auction='09:30' in q.index and q.loc['09:30','volume']>0
    if auction:
        a=q.loc['09:30'];j=n.index[0]
        n.loc[j,'open']=a.open;n.loc[j,'high']=max(a.high,n.loc[j,'high']);n.loc[j,'low']=min(a.low,n.loc[j,'low'])
        n.loc[j,'volume']+=a.volume;n.loc[j,'turnover']+=a.turnover
    n['timestamp']=n.timestamp-pd.Timedelta(minutes=1)
    return n.reset_index(drop=True),''


def main():
    symbols=['600754.SH','600169.SH','000034.SZ','300244.SZ']
    days=[];overlaps=[];source_files=[]
    dataset=ROOT/'gru_research/artifacts/dataset';times=np.load(dataset/'times.npy')
    cols=json.loads((dataset/'symbols.json').read_text());old=np.load(dataset/'hour_ohlcv.npy',mmap_mode='r')
    local_times=pd.to_datetime(times,utc=True).tz_convert('Asia/Shanghai').tz_localize(None)
    for symbol in symbols:
        relative=f'data/stock_1m/{symbol[-2:]}/{symbol[:6]}.parquet';path=OUT/'snapshot'/relative
        digest=hashlib.sha256(path.read_bytes()).hexdigest();assert digest==index()[relative]['lfs']['sha256']
        q=pq.read_table(path,filters=[('timestamp','>=',pd.Timestamp('2018-01-01'))]).to_pandas()
        assert q.symbol.eq(symbol[:6]).all() and q.exchange.eq(symbol[-2:]).all()
        assert q.timestamp.is_monotonic_increasing
        source_files.append({'symbol':symbol,'rows_2018_onward':len(q),'sha256':digest})
        raw_daily=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'/f'raw_DEV_{symbol[-2:].lower()+symbol[:6]}.json'
        daily={}
        if raw_daily.exists():
            obj=json.loads(raw_daily.read_text());assert obj['error_code']=='0' and obj['params']['adjustflag']=='3'
            daily={r[0]:dict(zip(obj['fields'],r)) for r in obj['rows']}
        for day,part in q.groupby(q.timestamp.dt.strftime('%Y-%m-%d'),sort=False):
            normalized,reason=normalize_day(part)
            rec={'symbol':symbol,'date':day,'source_rows':len(part),'grid_valid':not reason,'grid_reject_reason':reason,
                 'zero_price_rows':int((part[['open','high','low','close']]<=0).any(axis=1).sum())}
            if reason:days.append(rec);continue
            values=np.array([normalized.open.iloc[0],normalized.high.max(),normalized.low.min(),normalized.close.iloc[-1]])
            rec.update(raw_open=values[0],raw_high=values[1],raw_low=values[2],raw_close=values[3],
                       raw_volume=int(normalized.volume.sum()),raw_turnover=float(normalized.turnover.sum()))
            dr=daily.get(day)
            if dr:
                observed=np.array([float(dr[k]) for k in ['open','high','low','close']])
                error=np.abs(values-observed);volume_error=abs(rec['raw_volume']-float(dr['volume']))
                rec.update(independent_daily_available=True,daily_max_price_error=float(error.max()),
                           daily_volume_error=volume_error,daily_ohlc_exact_match=bool((error<1e-8).all()),
                           daily_ohlc_within_one_tick=bool((error<=.01000001).all()),
                           daily_volume_within_one_lot=bool(volume_error<=100))
            days.append(rec)
        # Compare provider time labels against the existing unadjusted hour tape.
        observed=q.set_index('timestamp').close.reindex(local_times).to_numpy()
        previous=q.set_index('timestamp').close.reindex(local_times-pd.Timedelta(minutes=1)).to_numpy()
        orig=np.array(old[:,cols.index(symbol),8],dtype=float)
        finite=np.isfinite(orig)&np.isfinite(observed)
        differences=np.abs(orig[finite]-observed[finite]);wrong=np.abs(orig[finite]-previous[finite])
        overlaps.append({'symbol':symbol,'common_hour_closes':int(finite.sum()),
                         'end_label_price_equal_within_float_representation':float((differences<=.0001).mean()),
                         'end_label_price_within_one_tick':float((differences<=.0101).mean()),
                         'one_minute_earlier_price_equal':float((wrong<=.0001).mean()),
                         'max_hour_close_difference':float(differences.max())})
    day_frame=pd.DataFrame(days);day_frame.to_csv(OUT/'sample_day_audit.csv',index=False)
    pd.DataFrame(overlaps).to_csv(OUT/'sample_hour_clock_audit.csv',index=False)
    independent=day_frame[day_frame.independent_daily_available.eq(True)]
    receipt={'revision':REVISION,'source_files':source_files,'stockdays_audited':len(day_frame),
             'complete_valid_minute_grids':int(day_frame.grid_valid.sum()),
             'grid_rejection_reasons':day_frame.loc[~day_frame.grid_valid,'grid_reject_reason'].value_counts().to_dict(),
             'independent_raw_daily_comparisons':len(independent),
             'independent_daily_ohlc_exact':int(independent.daily_ohlc_exact_match.sum()),
             'independent_daily_ohlc_within_one_tick':int(independent.daily_ohlc_within_one_tick.sum()),
             'independent_daily_volume_within_one_lot':int(independent.daily_volume_within_one_lot.sum()),
             'hour_clock_overlaps':overlaps,'timestamp_interpretation':'240 end-labeled regular bars, shifted one minute to original start-label grid; positive-volume 09:30 boundary joined to first regular minute; zero-volume boundary quote excluded.',
             'provider_level_provenance_disclosed':False,'price_adjustment_factors_present':False,
             'production_admitted':False,'entire_historical_union_admitted':False,
             'source_choice_needed':'These are independent provider observations with recorded disagreements; exact equality to the older quote-derived minute stream is not assumed.'}
    (OUT/'sample_semantics_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False))
    print(day_frame[day_frame.date.isin(['2019-03-29','2019-06-06','2024-12-06'])][['symbol','date','source_rows','grid_valid','raw_volume','daily_max_price_error','daily_volume_error']].to_string(index=False))


if __name__=='__main__':main()
