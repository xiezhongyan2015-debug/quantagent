"""Audit staged source-second minutes; preserve absent arrival-time evidence."""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    lock=p.locked('verify_cached_sept21_source_second');directory=p.OUT/'cached_sept21_source_second_v26_staging';path=directory/'receipt.json';receipt=p.json.loads(path.read_text());assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_cached_sept21_source_second_bounded.py')
    proof_path=p.OUT/'cached_sina_sept21_actual_hour_comparison/receipt.json';comparison=p.json.loads(proof_path.read_text());assert comparison['status']=='complete_cached_sept21_minute_comparison_no_admission' and p.sha(proof_path)==receipt['minute_reference_proof_sha256']
    qualified={(h['date'],h['symbol'],h['slot']):dict(h,sina_reference_path=h['source_sina_path'],sina_reference_sha256=h['source_sina_sha256']) for h in comparison['records'] if h['status']=='direct_coarse_exact_minutes_pending_source_arrival_policy'}
    raw_dir=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw';audit_path=raw_dir/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and p.sha(audit_path)==receipt['latest_raw_audit_sha256'];meta=p.json.loads((raw_dir/'dataset/meta.json').read_text());mask_pin=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(raw_dir/'dataset/hour_complete.npy')==mask_pin
    mask=p.np.load(raw_dir/'dataset/hour_complete.npy',mmap_mode='r');calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());checked=hours=0
    source_fields=['open','high','low','close','volume','amount_x10000','trades','first_ts','first_id','last_ts','last_id']
    for record in receipt['records']:
        symbol,date=record['symbol'],record['date'];source=p.Path(record['native_source_path']);raw=source.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==record['native_source_sha256'];native=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pandas();native=native.loc[native.ticker.eq(symbol[:6])].set_index('left_minute')
        validation_raw=p.Path(record['validation_path']).read_bytes();assert p.hashlib.sha256(validation_raw).hexdigest()==record['validation_sha256'];validation=next(r for r in p.json.loads(validation_raw)['stockdays'] if r['symbol']==symbol);assert validation['source_inventory_exact'] and validation['raw_positive'] and validation['unique_transaction_order_certified'] and validation['clocks']['floor_left']['daily_six_fields_exact']
        ref=record['primary_daily_reference'];primary_path=p.Path(ref['path']);assert p.sha(primary_path)==ref['sha256'];primary=p.json.loads(primary_path.read_text());daily=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date);assert daily['tradestatus']=='1'
        actual=[int(native.iloc[0].open),int(native.high.max()),int(native.low.min()),int(native.iloc[-1].close),int(native.volume.sum()),int(native.amount_x10000.sum())]
        assert actual==[int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
        leaf=p.Path(record['minute_path']);assert p.identity(leaf)==record['verified_identity'];data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==record['minute_sha256'];rows=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist();byminute={r['left_minute']:r for r in rows};assert len(rows)==len(byminute)==60*len(record['hours'])
        for row in rows:
            source_row=native.loc[row['left_minute']];assert row['symbol']==symbol and row['date']==date and row['trades']>0 and row['volume']>0;assert all(row[f]==int(source_row[f]) for f in source_fields);assert 'source_dependency_ready_ms' not in row;checked+=1
        grid=list(range(570,690))+list(range(780,900))
        for h in record['hours']:
            key=date,symbol,h['slot'];q=qualified[key];assert h['source_local_arrival_ms'] is None and h['sina_reference_sha256']==q['sina_reference_sha256']
            row_index=calendar['dates'].index(date)*4+h['slot'];column=calendar['union_symbols'].index(symbol);assert not mask[row_index,column]
            source_data=p.Path(h['sina_reference_path']).read_bytes();assert p.hashlib.sha256(source_data).hexdigest()==h['sina_reference_sha256'];text=source_data.decode();refs,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);observed={r['day']:r for r in refs}
            block=[byminute[m] for m in grid[h['slot']*60:(h['slot']+1)*60]]
            for r in block:
                end=r['left_minute']+1;timestamp=f'{date} {end//60:02d}:{end%60:02d}:00';sina=observed[timestamp];assert all(r[f]==int(D(sina[f])*10000) for f in ['open','high','low','close']);assert D(r['volume'])==D(sina['volume'])
            derived=dict(open=block[0]['open'],high=max(r['high'] for r in block),low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),amount_x10000=sum(r['amount_x10000'] for r in block))
            assert all(h[f]==v for f,v in derived.items());hours+=1
        assert p.identity(leaf)==record['verified_identity']
    assert hours==receipt['hours_staged'] and checked==receipt['minute_observations_staged'];assert not receipt['raw_panel_modified'] and not receipt['candidate_hours_admitted']
    p.atomic_json(directory/'independent_verification.json',dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),stockdays_verified=len(receipt['records']),real_minutes_verified=checked,hours_minute_truth_verified=hours,all_minute_source_rows_exact=True,all_minute60_independent_ohlcv_exact=True,all_native_stockday_primary_six_exact=True,source_arrival_certificate=False,unknown_source_arrival_times_kept_null=True,clock_and_cash_failures_not_waived=True,candidate_hours_admitted=0,latest_raw_panel_unchanged=True,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
