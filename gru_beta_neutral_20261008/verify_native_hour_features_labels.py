"""Audit recomputed hourly data and labels for every stock, without dense X."""
from . import bounded_tail_pipeline as p
from gru_research.minute_data import HOUR_FEATURES,aggregate_stock_day
from .rebuild_native_hour_features_labels import independent_minute_formula_check

RAW=p.BASE/'backfilled_panel_candidate_native_event_v8_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_event_v8_derived'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'


def expected_causal_features(f):
    valid=f.hour_complete.eq(True)
    o,h,l,c,v=[f[name].where(valid) for name in ['open','high','low','close','volume']]
    previous=c.shift(1);spread=(h-l).replace(0,p.np.nan)
    values={
        'h_return_1':p.np.log(c/previous),'h_open_gap':p.np.log(o/previous),
        'h_open_close_return':p.np.log(c/o),'h_amplitude':(h-l)/o,
        'h_upper_shadow':(h-p.pd.concat([o,c],axis=1).max(axis=1))/spread,
        'h_lower_shadow':(p.pd.concat([o,c],axis=1).min(axis=1)-l)/spread,'h_close_location':(c-l)/spread,
        'h_volume_prev':v/v.shift(1).replace(0,p.np.nan)}
    denominator=p.pd.Series(p.np.nan,index=f.index)
    for slot in range(4):
        selected=f.slot.eq(slot)
        denominator.loc[selected]=v[selected].shift(1).rolling(20,min_periods=20).mean()
    values['h_volume_same_slot_20d']=v/denominator.replace(0,p.np.nan)
    for width in [4,20,80]:values[f'h_ma_deviation_{width}']=c/c.rolling(width,min_periods=width).mean()-1
    for width in [4,20]:values[f'h_return_{width}']=p.np.log(c/c.shift(width))
    return p.pd.DataFrame(values)[HOUR_FEATURES[:14]].replace([p.np.inf,-p.np.inf],p.np.nan).to_numpy(p.np.float32)


def main():
    lock=p.locked('verify_native_hour_features_labels')
    receipt_path=DEST/'hour_feature_label_rebuild_receipt.json';receipt=p.json.loads(receipt_path.read_text())
    assert receipt['stocks_recalculated']==1178 and receipt['status']=='all1178_hour_features_and_labels_recalculated_pending_independent_derived_audit'
    assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/rebuild_native_hour_features_labels.py')
    assert receipt['source_formula_code_sha256']==p.sha(p.ROOT/'gru_research/minute_data.py')
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text())
    assert audit['passed'] and receipt['raw_full_scope_audit_sha256']==p.sha(audit_path)
    assert audit['audited_raw_meta_sha256']==receipt['raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    for item in audit['arrays']:assert p.identity(RAW/'dataset'/item['name'])==item['verified_identity']
    rawmeta=p.json.loads((RAW/'dataset/meta.json').read_text())
    complete=p.np.load(RAW/'dataset/hour_complete.npy')
    prices=p.np.load(RAW/'dataset/hour_ohlcv.npy',mmap_mode='r')
    times=p.np.load(RAW/'dataset/times.npy')
    earliest=p.np.load(ORIGINAL/'dataset/earliest_execution_times.npy')
    label_ends=p.np.load(ORIGINAL/'dataset/label_end_times.npy')
    incoming={};sources={}
    for source_contract in receipt['source_contracts']:
        assert p.sha(source_contract['stage_path'])==source_contract['stage_sha256']
        assert p.sha(source_contract['proof_path'])==source_contract['proof_sha256']
        assert p.sha(source_contract['merge_path'])==source_contract['merge_sha256']
        stage=p.json.loads(p.Path(source_contract['stage_path']).read_text())
        for record in stage['records']:
            if record.get('hours'):sources[record['symbol']]=record
        merge=p.json.loads(p.Path(source_contract['merge_path']).read_text())
        for r in merge['records']:incoming.setdefault(r['symbol'],[]).extend(r['hours'])
    counts=dict(stocks_checked=0,raw_stock_hours_checked=0,causal_feature_cells_checked=0,label_cells_checked=0,
        actual_native_minute_feature_hours_checked=0,accepted_new_execution_windows=0,late_new_execution_windows_kept_missing=0,
        zero_volume_new_execution_windows_kept_missing=0,labels_missing_where_no_real_execution_price=0)
    stocks=[]
    for column,record in enumerate(receipt['records']):
        symbol=record['symbol'];path=p.Path(record['new_hour_path'])
        assert p.identity(path)==record['verified_identity']
        raw=path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==record['new_hour_sha256']
        f=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pandas();del raw
        assert len(f)==8476 and f.symbol.eq(symbol).all()
        assert p.np.array_equal(f.signal_time.to_numpy(p.np.int64),times)
        assert p.np.array_equal(f.hour_complete.to_numpy(bool),complete[:,column])
        assert p.np.array_equal(f[rawmeta['hour_ohlcv_columns']].to_numpy(p.np.float32),prices[:,column],equal_nan=True)
        expected=expected_causal_features(f)
        assert p.np.array_equal(expected,f[HOUR_FEATURES[:14]].to_numpy(p.np.float32),equal_nan=True)
        mask=f.hour_complete.to_numpy(bool);prefix=p.np.r_[0,p.np.cumsum(~mask)]
        windows=p.np.zeros(8476,bool);windows[79:]=prefix[80:]==prefix[:-80]
        assert p.np.array_equal(windows,f.window80_complete.to_numpy(bool))
        assert p.np.array_equal(f.earliest_execution_time.to_numpy(p.np.int64),earliest) and (earliest>times).all()
        tape=f.window_price.where(f.window_complete.eq(True)&f.window_tradable.eq(True))
        raw_tape=f.window_raw_price.where(f.window_complete.eq(True)&f.window_tradable.eq(True))
        entry=tape.shift(-1)
        assert p.np.array_equal(entry.to_numpy(),f.execution_price.to_numpy(),equal_nan=True)
        assert p.np.array_equal(raw_tape.shift(-1).to_numpy(),f.execution_raw_price.to_numpy(),equal_nan=True)
        assert p.np.array_equal(f.window_complete.shift(-1).eq(True).to_numpy(),f.execution_window_complete.to_numpy())
        assert p.np.array_equal(f.window_tradable.shift(-1).eq(True).to_numpy(),f.execution_tradable.to_numpy())
        assert p.np.array_equal(f.window_all_flat.shift(-1).eq(True).to_numpy(),f.execution_all_flat.to_numpy())
        for horizon in range(1,5):
            expected_label=p.np.log(tape.shift(-(1+horizon))/entry).replace([p.np.inf,-p.np.inf],p.np.nan).to_numpy(p.np.float32)
            actual=f[f'y{horizon}'].to_numpy(p.np.float32)
            assert p.np.array_equal(expected_label,actual,equal_nan=True)
            assert p.np.array_equal(f[f'label_end_time{horizon}'].fillna(-1).to_numpy(p.np.int64),label_ends[:,horizon-1])
            assert p.np.array_equal(f[f'label_end_price{horizon}'].to_numpy(),tape.shift(-(1+horizon)).to_numpy(),equal_nan=True)
            counts['labels_missing_where_no_real_execution_price']+=int(p.np.isnan(expected_label).sum())
        if symbol in incoming:
            src=sources[symbol];minute_path=p.Path(src['minute_path']);assert p.identity(minute_path)==src['verified_identity'] and p.sha(minute_path)==src['minute_sha256']
            byminute={r['left_minute']:r for r in p.pq.ParquetFile(minute_path).read().to_pylist()}
            execution_records={r['slot']:r for r in record['execution_windows']}
            grid=list(range(570,690))+list(range(780,900))
            for hour in incoming[symbol]:
                row=hour['row_index'];slot=hour['slot'];factor=hour['adjustment_factor']
                block=[byminute[m] for m in grid[slot*60:(slot+1)*60]]
                stats={name:float(f.loc[row,name]) for name in HOUR_FEATURES[14:]}
                # Stored features arefloat32: exact oracle rounding is audited below.
                price_fields=['open','high','low','close']
                native=p.np.array([[r[field]/10000 for field in price_fields] for r in block])
                stamp=p.pd.Timestamp('2026-09-23')+p.pd.to_timedelta([r['left_minute'] for r in block],unit='min')
                table=dict(股票代码=[symbol[:6]]*60,datetime=stamp.strftime('%Y-%m-%d %H:%M:%S.%f').tolist(),datetime_nano=stamp.tz_localize('Asia/Shanghai').tz_convert('UTC').asi8,volume=[r['volume'] for r in block],qfq_ratio=[factor]*60)
                for i,field in enumerate(price_fields):table[field]=native[:,i];table['qfq_'+field]=native[:,i]*factor
                buffer=p.pa.BufferOutputStream();p.pq.write_table(p.pa.Table.from_pydict(table),buffer)
                golden,minute_audit=aggregate_stock_day(('2026-09-23',symbol,buffer.getvalue().to_pybytes()))
                assert not minute_audit['error'] and golden[slot]['hour_complete']
                assert p.np.array_equal(p.np.array([stats[name] for name in HOUR_FEATURES[14:]],p.np.float32),p.np.array([golden[slot][name] for name in HOUR_FEATURES[14:]],p.np.float32),equal_nan=True)
                start=[571,631,781,841][slot];window=[byminute[m] for m in range(start,start+5)]
                ready=max(r['source_dependency_ready_ms'] for r in window);qty=sum(r['volume'] for r in window)
                eligible=qty>0 and ready<=(start+5)*60000
                expected_raw=p.np.mean([r['open']/10000 for r in window])
                assert f.loc[row,'window_complete']==f.loc[row,'window_tradable']==eligible
                if eligible:
                    assert f.loc[row,'window_raw_price']==expected_raw and f.loc[row,'window_price']==expected_raw*factor
                    assert f.loc[row,'window_volume']==qty
                    counts['accepted_new_execution_windows']+=1
                else:
                    assert p.np.isnan(f.loc[row,'window_price']) and p.np.isnan(f.loc[row,'window_raw_price'])
                    counts['late_new_execution_windows_kept_missing' if qty else 'zero_volume_new_execution_windows_kept_missing']+=1
                er=execution_records[slot]
                assert er['source_dependency_ready_ms']==ready and er['true_window_volume_shares']==qty
                counts['actual_native_minute_feature_hours_checked']+=1
        counts['stocks_checked']+=1;counts['raw_stock_hours_checked']+=8476
        counts['causal_feature_cells_checked']+=8476*14;counts['label_cells_checked']+=8476*4
        stocks.append(dict(symbol=symbol,full_history_audit_passed=True,complete80_hours=int(windows.sum()),finite_labels=int(p.np.isfinite(f[['y1','y2','y3','y4']].to_numpy()).sum())))
        del f
    assert counts['stocks_checked']==1178 and counts['actual_native_minute_feature_hours_checked']==691
    assert counts['accepted_new_execution_windows']==662 and counts['late_new_execution_windows_kept_missing']==28 and counts['zero_volume_new_execution_windows_kept_missing']==1
    for item in audit['arrays']:assert p.identity(RAW/'dataset'/item['name'])==item['verified_identity']
    result=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_rebuild_receipt_sha256=p.sha(receipt_path),
        raw_full_history_audit_sha256=p.sha(audit_path),status='all1178_hour_features_execution_and_labels_verified_dense_X_not_built',
        counts=counts,stocks=stocks,old_coverage_gates_not_waived=True,complete_model_input_panel_built=False,
        slow_daily_sources_unchanged_by_native_hour_overlay=True,slow_daily_features_not_recalculated_here=True,
        new_training_or_backtest_run=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(DEST/'hour_feature_label_independent_verification.json',result);lock.close()


if __name__=='__main__':
    main()
