"""Recalculate hourly features and labels, with one stock history resident.

Raw V26 must already have passed its full1178 audit. Future execution windows
need their own real minute data and recorded dependencies by the fixed window
end; otherwise they remain unavailable. Existing daily/slow sources are not
changed by this native hourly overlay. No model is trained here.
"""
from collections import defaultdict,Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from gru_research.minute_data import HOUR_FEATURES,EXEC_MINUTES,add_features_and_labels,aggregate_stock_day

RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'
DEST=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_derived'
OHLC=['open','high','low','close','volume','raw_open','raw_high','raw_low','raw_close','adjustment_factor']


def minute_statistics(block,factor):
    raw=p.np.array([[float(D(r[f])/10000) for f in ['open','high','low','close']] for r in block])
    adjusted=raw*factor
    volume=p.np.array([r['volume'] for r in block],dtype=float)
    o=adjusted[0,0];c=adjusted[-1,3]
    returns=p.np.log(adjusted[:,3]/p.np.r_[o,adjusted[:-1,3]])
    sr=float(p.np.square(returns).sum());std=returns.std();logv=p.np.log1p(volume)
    corr=float(p.np.corrcoef(returns,logv)[0,1]) if std>1e-12 and logv.std()>1e-12 else p.np.nan
    path=float(p.np.abs(p.np.diff(p.np.r_[o,adjusted[:,3]])).sum())
    ratio=lambda a,b:float(a/b) if p.np.isfinite(b) and b!=0 else p.np.nan
    values=[p.np.sqrt(sr),float(p.np.mean(((returns-returns.mean())/std)**3)) if std>1e-12 else p.np.nan,
        returns.max(),returns.min(),ratio(p.np.square(returns[returns>0]).sum(),sr),corr,
        ratio(volume[-15:].sum(),volume.sum()),float(p.np.mean(volume==0)),ratio(abs(c-o),path),ratio(volume[returns>0].sum(),volume.sum())]
    return dict(zip(HOUR_FEATURES[14:],values)),raw,volume


def independent_minute_formula_check(symbol,block,factor,statistics,date):
    # Serialize only these real60minutes; missing other intervals stay absent.
    raw=p.np.array([[float(D(r[f])/10000) for f in ['open','high','low','close']] for r in block])
    stamps=p.pd.Timestamp(date)+p.pd.to_timedelta([r['left_minute'] for r in block],unit='min')
    table=dict(股票代码=[symbol[:6]]*60,datetime=stamps.strftime('%Y-%m-%d %H:%M:%S.%f').tolist(),
        datetime_nano=stamps.tz_localize('Asia/Shanghai').tz_convert('UTC').asi8,volume=[r['volume'] for r in block],qfq_ratio=[factor]*60)
    for i,name in enumerate(['open','high','low','close']):
        table[name]=raw[:,i];table['qfq_'+name]=raw[:,i]*factor
    buffer=p.pa.BufferOutputStream();p.pq.write_table(p.pa.Table.from_pydict(table),buffer)
    reference,audit=aggregate_stock_day((date,symbol,buffer.getvalue().to_pybytes()))
    assert not audit['error'] and audit['invalid_rows']==audit['qfq_mismatch_rows']==0
    slot=(block[0]['left_minute']-570)//60 if block[0]['left_minute']<690 else (block[0]['left_minute']-780)//60+2
    gold=reference[slot];assert gold['hour_complete']
    for name,value in statistics.items():
        assert p.np.isclose(value,gold[name],rtol=1e-12,atol=1e-12,equal_nan=True),(symbol,slot,name)


def main():
    lock=p.locked('rebuild_cached_second_hour_features_labels')
    DEST.mkdir(exist_ok=True);(DEST/'symbol_hour_features').mkdir(exist_ok=True)
    output=DEST/'hour_feature_label_rebuild_receipt.json'
    assert not output.exists()
    raw_audit_path=RAW/'raw_full_history_audit.json';raw_audit=p.json.loads(raw_audit_path.read_text())
    assert raw_audit['passed'] and raw_audit['stocks_checked']==1178
    assert raw_audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates,symbols=calendar['dates'],calendar['union_symbols']
    incoming=defaultdict(list);stage_records={}
    source_contracts=[]
    for stage_name,version in [('new_candidate_v7_staging','backfilled_panel_candidate_native_event_v7_raw'),('additional_native_hour_staging','backfilled_panel_candidate_native_event_v8_raw'),('special_factor_native_hour_staging','backfilled_panel_candidate_native_event_v9_raw'),('sina_corrob_native_minute_staging','backfilled_panel_candidate_native_second_v10_raw'),('sep22_native_millisecond_staging','backfilled_panel_candidate_native_millisecond_v11_raw'),('sep22_quote_witness_minute_staging','backfilled_panel_candidate_native_quote_v12_raw'),('historical_reported_v13_staging','backfilled_panel_candidate_historical_reported_v13_raw'),('historical_partial_reported_v14_staging','backfilled_panel_candidate_historical_partial_reported_v14_raw'),('reconciled_tail_millisecond_staging','backfilled_panel_candidate_native_reconciled_v15_raw'),('expanded_tail_millisecond_v16_staging','backfilled_panel_candidate_native_expanded_v16_raw'),('two_day_quote_reconciled_v17_staging','backfilled_panel_candidate_native_quote_reconciled_v17_raw'),('nominal_quote_cut_reconciled_v18_staging','backfilled_panel_candidate_native_nominal_cut_v18_raw'),('sampled_quote_reconciled_v19_staging','backfilled_panel_candidate_native_sampled_quote_v19_raw'),('strict_endpoint_quote_reconciled_v20_staging','backfilled_panel_candidate_native_strict_endpoint_v20_raw'),('first_hour_cumulative_extrema_v21_staging','backfilled_panel_candidate_native_first_hour_extrema_v21_raw'),('first_hour_observed_second_close_v22_staging','backfilled_panel_candidate_native_observed_second_v22_raw'),('first_observed_quote_open_v23_staging','backfilled_panel_candidate_native_first_quote_v23_raw'),('afternoon_closed_morning_quantity_v24_staging','backfilled_panel_candidate_native_session_quantity_v24_raw'),('actual_interval_quantity_v25_staging','backfilled_panel_candidate_native_actual_quantity_v25_raw'),('cached_sept21_source_second_v26_staging','backfilled_panel_candidate_native_cached_second_v26_raw')]:
        stage_path=p.OUT/stage_name/'receipt.json';stage=p.json.loads(stage_path.read_text());proof_path=stage_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
        assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
        merges=p.json.loads((p.BASE/version/'raw_panel_merge_receipt.json').read_text())
        assert merges['independently_verified_minute_staging_sha256']==p.sha(proof_path)
        source_contracts.append(dict(stage_path=str(stage_path),stage_sha256=p.sha(stage_path),proof_path=str(proof_path),proof_sha256=p.sha(proof_path),merge_path=str(p.BASE/version/'raw_panel_merge_receipt.json'),merge_sha256=p.sha(p.BASE/version/'raw_panel_merge_receipt.json')))
        for record in stage['records']:
            if record.get('hours'):stage_records[record['symbol'],record.get('date','2026-09-23'),record['minute_path']]=record
        for record in merges['records']:incoming[record['symbol']].extend(record['hours'])
        for hour in merges.get('reception_evidence_updates',[]):
            symbol=hour['symbol'];matches=[i for i,old in enumerate(incoming[symbol]) if old['row_index']==hour['row_index']]
            assert len(matches)==1
            old=incoming[symbol][matches[0]];assert old['canonical_float32_values']==hour['canonical_float32_values']
            incoming[symbol][matches[0]]=hour
    assert sum(len(v) for v in incoming.values())==raw_audit['total_admitted_raw_hours']
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),source_formula_code_sha256=p.sha(p.ROOT/'gru_research/minute_data.py'),
        raw_candidate=RAW.name,raw_full_scope_audit_sha256=p.sha(raw_audit_path),raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),
        status='recalculating_stock_hour_features_and_execution_labels',stocks_requested=1178,records=[],source_contracts=source_contracts,
        resident_scope='one8476-row stock history, one real native minute leaf, no trade table held',
        new_X_built=False,slow_daily_features_recalculated=False,new_training_or_backtest_run=False,production_ready=False)
    counts=Counter();grid=list(range(570,690))+list(range(780,900))
    old_times=p.np.load(ORIGINAL/'dataset/times.npy');old_execution=p.np.load(ORIGINAL/'dataset/earliest_execution_times.npy')
    for number,symbol in enumerate(symbols,1):
        source=ORIGINAL/'symbol_hour_features'/(symbol+'.parquet')
        old_receipt=p.json.loads((ORIGINAL/'symbol_receipts'/(symbol+'.json')).read_text())
        before=p.identity(source);raw=source.read_bytes();assert len(raw)<=16*1024**2
        digest=p.hashlib.sha256(raw).hexdigest();assert digest==old_receipt['source_hour_parquet_sha256']
        frame=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pandas();del raw
        assert p.identity(source)==before and p.np.array_equal(frame.signal_time.to_numpy(),old_times)
        old_features=frame[HOUR_FEATURES].to_numpy(p.np.float32);old_labels=frame[['y1','y2','y3','y4']].to_numpy(p.np.float32)
        old_window=frame.window_complete.eq(True).to_numpy();execution_records=[]
        if symbol in incoming:
            minute_cache={}
            for name in HOUR_FEATURES[14:]:frame[name]=frame[name].astype(p.np.float64)
            for hour in incoming[symbol]:
                date=hour['date'];source_key=hour['minute_source_path'];staged=stage_records[symbol,date,source_key]
                if source_key not in minute_cache:
                    path=p.Path(staged['minute_path']);assert p.identity(path)==staged['verified_identity']
                    assert p.sha(path)==staged['minute_sha256'];minutes=p.pq.ParquetFile(path).read().to_pylist();minute_cache[source_key]={r['left_minute']:r for r in minutes}
                byminute=minute_cache[source_key]
                row=hour['row_index'];slot=hour['slot'];factor=hour['adjustment_factor']
                assert not frame.loc[row,'hour_complete'] and frame.loc[row,'signal_time']==old_times[row]
                block=[byminute[m] for m in grid[slot*60:(slot+1)*60]]
                stats,prices,volume=minute_statistics(block,factor)
                independent_minute_formula_check(symbol,block,factor,stats,date)
                exact=[float(D(hour['exact_raw_price_x10000'][f])/10000) for f in ['open','high','low','close']]
                values=[v*factor for v in exact]+[hour.get('exact_native_volume_shares',hour.get('reported_volume_shares'))]+exact+[factor]
                assert p.np.asarray(values,p.np.float32).tolist()==hour['canonical_float32_values']
                for name,value in zip(OHLC,values):frame.loc[row,name]=value
                for name,value in stats.items():frame.loc[row,name]=value
                frame.loc[row,'hour_complete']=True;frame.loc[row,'minute_count']=60
                frame.loc[row,'hour_source']='verified_native_event_time_and_genuine_zero_trade_quotes'
                frame.loc[row,'donor_daily_all_ohlc_exact']=True
                frame.loc[row,'native_minute_source_sha256']=staged['minute_sha256']
                frame.loc[row,'native_source_dependency_ready_ms']=hour.get('source_ready_ms') if hour.get('source_ready_ms') is not None else p.np.nan
                frame.loc[row,'native_source_arrival_known']=hour.get('source_ready_ms') is not None
                if old_window[row]:
                    execution_records.append(dict(hour_row_index=row,slot=slot,status='inherited_existing_window_retained',source_arrival_evidence_not_invented=True))
                    counts['execution_window_inherited_existing_window_retained']+=1
                    counts['native_minute_feature_hours_independently_checked']+=1
                    continue
                if hour.get('execution_proxy_policy')=='original_retrospective_observed_minute_open_mean':
                    start=int(EXEC_MINUTES[slot]);window=[byminute[m] for m in range(start,start+5)]
                    assert all(r['source_kind']=='observed_vendor_end_labelled_ohlcv_integer_lots' and r['source_dependency_ready_ms'] is None for r in window)
                    qty=sum(r['volume'] for r in window);valid=qty>0
                    opens=p.np.array([float(D(r['open'])/10000) for r in window])
                    for name in ['window_complete','window_tradable']:frame.loc[row,name]=valid
                    frame.loc[row,'window_volume']=qty if valid else p.np.nan
                    frame.loc[row,'window_raw_price']=opens.mean() if valid else p.np.nan
                    frame.loc[row,'window_price']=opens.mean()*factor if valid else p.np.nan
                    frame.loc[row,'window_all_flat']=valid and all(r['high']==r['low'] for r in window)
                    midnight=old_times[row]-[630,690,840,900][slot]*60*10**9
                    frame.loc[row,'window_start_time']=midnight+start*60*10**9;frame.loc[row,'window_end_time']=midnight+(start+5)*60*10**9
                    reason='accepted' if valid else 'zero_reported_window_volume'
                    execution_records.append(dict(hour_row_index=row,slot=slot,status=reason,
                        execution_proxy_policy=hour['execution_proxy_policy'],source_dependency_ready_ms=None,actual_receiver_time_certified=False,
                        reported_window_volume_shares=qty,quantity_reporting_precision='reported_integer_lots_scaled100',
                        raw_mean_five_observed_minute_opens=float(opens.mean()),adjustment_factor=factor))
                    counts['historical_reported_execution_proxy_accepted' if valid else 'historical_reported_zero_window_kept_missing']+=1
                    counts['execution_window_'+reason]+=1;counts['native_minute_feature_hours_independently_checked']+=1
                    continue
                if hour.get('source_ready_ms') is None:
                    for name in ['window_complete','window_tradable','window_all_flat']:frame.loc[row,name]=False
                    for name in ['window_volume','window_raw_price','window_price']:frame.loc[row,name]=p.np.nan
                    execution_records.append(dict(hour_row_index=row,slot=slot,status='unknown_source_arrival_window_kept_missing',source_dependency_ready_ms=None,adjustment_factor=factor))
                    counts['execution_window_unknown_source_arrival_window_kept_missing']+=1
                    counts['native_minute_feature_hours_independently_checked']+=1
                    continue
                start=int(EXEC_MINUTES[slot]);window=[byminute[m] for m in range(start,start+5)]
                ready=max(r['source_dependency_ready_ms'] for r in window);end=(start+5)*60000
                qty=sum(r['volume'] for r in window);valid=qty>0 and ready<=end
                reason='accepted' if valid else 'zero_true_window_volume' if qty==0 else 'recorded_source_dependency_after_fixed_window_end'
                assert not old_window[row],'Would overwrite existing execution proxy'
                for name in ['window_complete','window_tradable']:frame.loc[row,name]=valid
                frame.loc[row,'window_volume']=qty if valid else p.np.nan
                opens=p.np.array([float(D(r['open'])/10000) for r in window])
                frame.loc[row,'window_raw_price']=opens.mean() if valid else p.np.nan
                frame.loc[row,'window_price']=opens.mean()*factor if valid else p.np.nan
                frame.loc[row,'window_all_flat']=valid and all(r['high']==r['low'] for r in window)
                midnight=old_times[row]-[630,690,840,900][slot]*60*10**9
                frame.loc[row,'window_start_time']=midnight+start*60*10**9;frame.loc[row,'window_end_time']=midnight+(start+5)*60*10**9
                execution_records.append(dict(hour_row_index=row,slot=slot,source_dependency_ready_ms=ready,fixed_window_end_ms=end,
                    true_window_volume_shares=qty,status=reason,raw_mean_five_observed_minute_opens=float(opens.mean()),adjustment_factor=factor))
                counts['native_minute_feature_hours_independently_checked']+=1;counts['execution_window_'+reason]+=1
        rebuilt=add_features_and_labels(frame,dates)
        assert p.np.array_equal(rebuilt.signal_time.to_numpy(),old_times)
        assert p.np.array_equal(rebuilt.earliest_execution_time.to_numpy(p.np.int64),old_execution)
        new_features=rebuilt[HOUR_FEATURES].to_numpy(p.np.float32);new_labels=rebuilt[['y1','y2','y3','y4']].to_numpy(p.np.float32)
        source_rows=sorted({h['row_index'] for h in incoming.get(symbol,[])})
        allowed_features={r for source_row in source_rows for r in range(source_row,min(8476,source_row+81))}
        residuals=[]
        # Rolling summation compensation can leave machine-scale residuals long
        # after a changed value has left the window. These rows have no changed
        # operands: preserve their pinned baseline float32 representation, after
        # a tight float64-error bound, never alter a source-affected formula cell.
        rolling_fields={'h_ma_deviation_4','h_ma_deviation_20','h_ma_deviation_80','h_volume_same_slot_20d'}
        equality=(old_features==new_features)|(p.np.isnan(old_features)&p.np.isnan(new_features))
        for row,column in p.np.argwhere(~equality):
            if int(row) in allowed_features:continue
            old_value=float(old_features[row,column]);new_value=float(new_features[row,column])
            bound=128*p.np.finfo(p.np.float64).eps*max(1.,abs(old_value),abs(new_value))
            assert HOUR_FEATURES[column] in rolling_fields and p.np.isfinite(old_value) and p.np.isfinite(new_value) and abs(old_value-new_value)<=bound
            rebuilt.loc[int(row),HOUR_FEATURES[column]]=old_features[row,column]
            residuals.append(dict(row=int(row),feature=HOUR_FEATURES[column],difference=old_value-new_value,bound=bound))
        new_features=rebuilt[HOUR_FEATURES].to_numpy(p.np.float32)
        if symbol not in incoming:
            assert p.np.array_equal(old_features,new_features,equal_nan=True)
            assert p.np.array_equal(old_labels,new_labels,equal_nan=True)
        else:
            first_source_delta=min(h['row_index'] for h in incoming[symbol])
            assert p.np.array_equal(old_features[:first_source_delta],new_features[:first_source_delta],equal_nan=True),'Source overlay changed features before its first observation'
        feature_changed=~((old_features==new_features)|(p.np.isnan(old_features)&p.np.isnan(new_features))).all(axis=1)
        label_changed=~((old_labels==new_labels)|(p.np.isnan(old_labels)&p.np.isnan(new_labels))).all(axis=1)
        source_rows=sorted({h['row_index'] for h in incoming.get(symbol,[])})
        allowed_features={r for source_row in source_rows for r in range(source_row,min(8476,source_row+81))}
        allowed_labels={r for source_row in source_rows for r in range(max(0,source_row-5),source_row)}
        assert set(p.np.flatnonzero(feature_changed))<=allowed_features
        assert set(p.np.flatnonzero(label_changed))<=allowed_labels
        target=DEST/'symbol_hour_features'/(symbol+'.parquet');temp=target.with_suffix('.parquet.tmp')
        p.pq.write_table(p.pa.Table.from_pandas(rebuilt,preserve_index=False),temp,compression='zstd');temp.replace(target)
        receipt['records'].append(dict(symbol=symbol,source_hour_path=str(source),source_hour_sha256=digest,source_identity=before,
            new_hour_path=str(target),new_hour_sha256=p.sha(target),verified_identity=p.identity(target),rows=len(rebuilt),
            raw_hours_added=len(incoming.get(symbol,[])),source_delta_hour_rows=source_rows,unaffected_rolling_residuals_preserved=residuals,hour_feature_rows_changed=int(feature_changed.sum()),label_rows_changed=int(label_changed.sum()),
            execution_windows=execution_records,complete80_hours=int(rebuilt.window80_complete.sum()),complete_hours=int(rebuilt.hour_complete.sum()),
            full_source_history_recalculated=True,features_before_first_source_delta_unchanged=True,all_changed_features_within_causal80hour_scope=True))
        if number%25==0 or number==1178:
            p.pa.default_memory_pool().release_unused()
            receipt.update(stocks_recalculated=number,counts=dict(counts),updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
            p.atomic_json(output,receipt)
        del frame,rebuilt,old_features,new_features,old_labels,new_labels
    receipt.update(status='all1178_hour_features_and_labels_recalculated_pending_independent_derived_audit',finished_utc=p.utc(),
        stocks_recalculated=1178,counts=dict(counts),total_hour_feature_rows_changed=sum(r['hour_feature_rows_changed'] for r in receipt['records']),
        total_label_rows_changed=sum(r['label_rows_changed'] for r in receipt['records']),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':
    main()
