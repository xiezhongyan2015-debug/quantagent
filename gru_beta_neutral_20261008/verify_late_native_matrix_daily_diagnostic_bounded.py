"""Independently decode retained original column bytes, then check real gap days.

No rounding, field substitution, or admission; partially retained remote matrices
are never represented as fully fingerprinted source files.
"""
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p
from .acquire_late_remaining_native_matrix_columns_bounded import SparseView, SOURCE, DEST, FIELDS, CODES
from .recheck_historical_quote_candidates import GRID

def main():
    lock=p.locked('verify_late_native_matrix_daily');output=DEST/'independent_daily_diagnostic.json';assert not output.exists()
    source_path=DEST/'receipt.json';source=p.json.loads(source_path.read_text());assert source['status']=='complete_late_four_stock_native_columns_no_admission'
    old_path=SOURCE/'native_matrix_pilot/bounded_column_acquisition.json';assert p.sha(old_path)==source['prior_acquisition_sha256'];old=p.json.loads(old_path.read_text());close=next(r for r in old['results'] if r['field']=='close')
    idxpath=p.Path(close['selection_path']);assert p.sha(idxpath)==close['selection_sha256'];fullindex=p.pq.ParquetFile(idxpath).read(columns=['__index_level_0__'],use_threads=False).column(0)
    dates=[x.strftime('%Y-%m-%d') for x in fullindex.to_pylist()];wanted={c:{r['date'] for r in source['actual_requested_stockdays'] if r['symbol'][:6]==c} for c in CODES};fields={};timestamps={};pins=[]
    for r in source['results']:
        code,field=r['code'],r['field'];indices=[i for i,date in enumerate(dates) if date in wanted[code]];segments=[]
        for q in r['reused_cached_footer_and_header']:
            raw=p.Path(q['path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==q['retained_range_sha256'];segments.append((q['start'],raw))
        path=p.Path(r['range_path']);assert p.identity(path)==r['range_verified_identity'];raw=path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['range_sha256'];segments.append((r['range_start'],raw))
        parser=p.pq.ParquetFile(SparseView(r['published_bytes'],segments),pre_buffer=False,page_checksum_verification=True)
        decoded=parser.read(columns=[code],use_threads=False).column(0).take(p.pa.array(indices,type=p.pa.int64()))
        target=p.Path(r['selected_path']);assert p.identity(target)==r['selected_verified_identity'];data=target.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['selected_sha256'];selected=p.pq.ParquetFile(p.pa.BufferReader(data)).read(use_threads=False)
        assert selected.column('value').equals(decoded) and selected.column('datetime').equals(fullindex.take(p.pa.array(indices,type=p.pa.int64())))
        stamp=selected.column('datetime').to_pylist();assert stamp==sorted(stamp) and len(stamp)==len(set(stamp))==240*len(wanted[code]);assert code not in timestamps or stamp==timestamps[code];timestamps[code]=stamp
        fields[code,field]=decoded.to_pylist();pins.append(dict(path=str(target),sha256=r['selected_sha256'],verified_identity=r['selected_verified_identity']));del segments,parser,decoded,data,selected,raw
    failure_path=p.OUT/'historical_daily_failure_definition_diagnostic/receipt.json';failure=p.json.loads(failure_path.read_text());prior={(r['symbol'],r['date']):r for r in failure['records']};records=[];counts=Counter()
    for request in source['actual_requested_stockdays']:
        symbol,date=request['symbol'],request['date'];code=symbol[:6];rows=[i for i,t in enumerate(timestamps[code]) if t.strftime('%Y-%m-%d')==date];stamps=[timestamps[code][i] for i in rows]
        assert len(rows)==240 and [t.hour*60+t.minute for t in stamps]==GRID and all(t.second==t.microsecond==0 for t in stamps)
        bars=[{f:D(str(fields[code,f][i])) if fields[code,f][i] is not None else None for f in FIELDS} for i in rows]
        primary_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(primary_path.read_text());day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
        nulls=sum(v is None for b in bars for v in b.values());record=dict(symbol=symbol,date=date,slots=request['slots'],minute_rows=240,source_null_fields=nulls,primary_path=str(primary_path),primary_sha256=p.sha(primary_path))
        if nulls:record.update(status='actual_null_fields_no_admission');counts['null_field_days']+=1;records.append(record);continue
        flags=dict(raw_price_shape_valid=all(all(b[f].is_finite() and b[f]>0 and b[f]==b[f].quantize(D('.01')) for f in ['open','high','low','close']) and b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] for b in bars),raw_volume_integer_nonnegative=all(b['volume'].is_finite() and b['volume']>=0 and b['volume']==b['volume'].to_integral_value() for b in bars),primary_day_trading=day['tradestatus']=='1',open_exact=bars[0]['open']==D(day['open']),close_exact=bars[-1]['close']==D(day['close']),inside_primary_range=max(b['high'] for b in bars)<=D(day['high']) and min(b['low'] for b in bars)>=D(day['low']),volume_within100=abs(sum(b['volume'] for b in bars)-D(day['volume']))<=100)
        conflict=[i for i,b in enumerate(bars) if not b['low']*b['volume']-1<=b['amount']<=b['high']*b['volume']+1];positive=[s for s in request['slots'] if sum(b['volume'] for b in bars[s*60:(s+1)*60])>0];passes=positive if all(flags.values()) else []
        record.update(status='actual240_native_minute_day_diagnostic_no_admission',daily_flags=flags,source_volume_sum=str(sum(b['volume'] for b in bars)),primary_volume=day['volume'],daily_quantity_difference=str(sum(b['volume'] for b in bars)-D(day['volume'])),source_amount_sum=str(sum(b['amount'] for b in bars)),primary_amount=day['amount'],quantity_corrects_existing_daily_failure=(symbol,date) in prior and not flags['volume_within100']==False and 'volume_difference_within_100_shares' in prior[symbol,date]['failed_daily_fields'],prior_reported_daily_quantity_difference=prior.get((symbol,date),{}).get('volume_difference_shares'),additional_minute_amount_conflicts=conflict,daily_and_positive_pass_slots=passes)
        counts['checked_days']+=1;counts['checked_minutes']+=240;counts['actual_missing_hours']+=len(request['slots']);counts['daily_positive_pass_hours']+=len(passes);counts['quantity_corrected_days']+=record['quantity_corrects_existing_daily_failure'];counts['days_with_minute_amount_conflicts']+=bool(conflict)
        for flag,passed in flags.items():counts[flag+'_failed_days']+=not passed
        records.append(record)
    receipt=dict(passed=True,status='complete_late_native_day_diagnostic_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_acquisition_sha256=p.sha(source_path),all_retained_ranges_and_decoded_subsets_reproduced=True,selected_pins=pins,counts=dict(counts),records=records,whole_remote_field_SHA_verified=False,source_upstream_and_independence_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,source_quantity_not_rounded_or_reallocated=True,source_zero_quantity_rows_not_created_or_relabelled=True,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close();print(p.json.dumps(dict(counts=counts,peak_rss_bytes=receipt['peak_rss_bytes'])),flush=True)

if __name__=='__main__':main()
