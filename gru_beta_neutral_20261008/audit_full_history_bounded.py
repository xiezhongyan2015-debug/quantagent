"""Read every V6 stock history with one stock/batch resident at a time.

This certifies internal panel consistency, not previously failed source gates.
Final new-panel admission still requires independent minute/source checks.
"""
from . import bounded_tail_pipeline as p


def main():
    process_lock = p.locked('full_history_audit')
    candidate = p.BASE / 'backfilled_panel_candidate_canonical_ipo_v6'
    dataset = candidate / 'dataset'
    meta_path = dataset / 'meta.json'
    meta_sha = p.sha(meta_path)
    meta = p.json.loads(meta_path.read_text())
    symbols = p.json.loads((dataset / 'symbols.json').read_text())
    calendar = p.json.loads((p.ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())
    assert symbols == calendar['union_symbols'] and len(symbols) == 1178
    complete = p.np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    window = p.np.load(dataset / 'window80_complete.npy', mmap_mode='r')
    panel = p.np.load(dataset / 'hour_ohlcv.npy', mmap_mode='r')
    times = p.np.load(dataset / 'times.npy', mmap_mode='r')
    reasons = p.np.load(candidate / 'gap_audit/missing_hour_reason_codes.npy', mmap_mode='r')
    assert complete.shape == window.shape == reasons.shape == panel.shape[:2]
    assert complete.shape == (len(calendar['dates']) * 4, 1178)
    op = p.OUT / 'full_history_audit.json'
    d = dict(started_utc=p.utc(), source_code_sha256=p.sha(__file__), candidate_meta_sha256=meta_sha,
        candidate_name=candidate.name, symbols_requested=1178, hours_per_symbol=len(times),
        resident_scope='one stock, <=1024 hourly rows per read batch', stocks=[], anomalies=[],
        candidate_hours_admitted=0, semantic_or_independent_source_admission_certified=False,
        final_new_panel_audit_complete=False, status='auditing_existing_V6')
    fields = meta['hour_ohlcv_columns']
    for column, symbol in enumerate(symbols):
        file = candidate / 'symbol_hour_features' / (symbol + '.parquet')
        pf = p.pq.ParquetFile(file)
        assert pf.metadata.num_rows == len(times)
        c = p.np.asarray(complete[:, column])
        w = p.np.asarray(window[:, column])
        prefix = p.np.r_[0, p.np.cumsum(~c)]
        expected = p.np.zeros(len(c), dtype=bool)
        expected[79:] = (prefix[80:] - prefix[:-80]) == 0
        errors = []
        if not p.np.array_equal(w, expected):
            errors.append('window80_mask_not_exact_80_observed_hours')
        if not p.np.array_equal(p.np.asarray(reasons[:,column]) == 0, c):
            errors.append('reason_mask_disagrees_with_complete_mask')
        offset = 0
        for batch in pf.iter_batches(batch_size=1024, columns=['signal_time', 'hour_complete', 'window80_complete', *fields], use_threads=False):
            frame = batch.to_pandas()
            n = len(frame)
            view = panel[offset:offset+n,column,:]
            observed = frame[fields].to_numpy(dtype=view.dtype)
            if not p.np.array_equal(observed, view, equal_nan=True):
                errors.append('saved_panel_differs_from_stock_hour_source')
            if not p.np.array_equal(frame.signal_time.to_numpy(), times[offset:offset+n]):
                errors.append('stock_hour_signal_time_differs_from_panel')
            if not p.np.array_equal(frame.hour_complete.fillna(False).to_numpy(bool), c[offset:offset+n]):
                errors.append('stock_hour_complete_mask_differs_from_panel')
            if not p.np.array_equal(frame.window80_complete.fillna(False).to_numpy(bool), w[offset:offset+n]):
                errors.append('stock_window80_mask_differs_from_panel')
            good = view[c[offset:offset+n]]
            if len(good) and (not p.np.isfinite(good).all() or not (good > 0).all()):
                errors.append('complete_hour_contains_nonpositive_or_missing_value')
            if len(good) and (not (good[:,1] >= p.np.maximum(good[:,0], good[:,3])).all() or
                              not (good[:,2] <= p.np.minimum(good[:,0], good[:,3])).all()):
                errors.append('complete_hour_OHLC_range_invalid')
            offset += n
        daily = p.pq.ParquetFile(candidate / 'donor_daily_audit' / (symbol + '.parquet'))
        suspended = promoted = 0
        date_index = {date:i for i,date in enumerate(calendar['dates'])}
        for batch in daily.iter_batches(batch_size=1024, columns=['date','tradestatus'],use_threads=False):
            for r in batch.to_pylist():
                if r['tradestatus']=='0' and r['date'] in date_index:
                    i = date_index[r['date']]*4
                    suspended += 1
                    promoted += int(c[i:i+4].sum())
        if promoted:
            errors.append('primary_suspended_day_has_complete_hours_requires_review')
        record = dict(symbol=symbol, source_hour_rows=offset, complete_hours=int(c.sum()),
            complete80_hours=int(w.sum()), primary_suspended_stockdays_observed=suspended,
            complete_hours_on_primary_suspended_days=promoted, internal_consistency_passed=not errors)
        d['stocks'].append(record)
        if errors:
            d['anomalies'].append(dict(symbol=symbol, errors=sorted(set(errors))))
        if (column+1)%50==0:
            d.update(stocks_checked=column+1,updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
            p.atomic_json(op,d)
    assert p.sha(meta_path)==meta_sha
    d.update(stocks_checked=1178,finished_utc=p.utc(),status='complete_internal_audit',
        passed=not d['anomalies'],peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss,
        original_missing_historical_hours=3042,original_missing_tail_hours=84860)
    p.atomic_json(op,d)
    process_lock.close()


if __name__=='__main__':
    main()
