"""Merge qualified observed historical bars with explicit vendor quantity precision.

All retained raw data stays immutable; no receiver clock or actual share quantity is inferred.
"""
from decimal import Decimal as D
from collections import defaultdict
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
from .merge_native_candidate_raw_panel import states,copy_patched
from .stage_historical_reported_minutes_bounded import DEST as STAGE

PARENT=p.BASE/'backfilled_panel_candidate_native_quote_v12_raw'
DEST=p.BASE/'backfilled_panel_candidate_historical_reported_v13_raw'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'


def main():
    lock=p.locked('merge_historical_reported_raw');dataset=DEST/'dataset';dataset.mkdir(parents=True,exist_ok=True)
    output=DEST/'raw_panel_merge_receipt.json';assert not output.exists()
    stage_path=STAGE/'receipt.json';stage=p.json.loads(stage_path.read_text());proof_path=STAGE/'independent_verification.json';proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
    parent_meta_path=PARENT/'dataset/meta.json';parent=p.json.loads(parent_meta_path.read_text());parent_audit_path=PARENT/'raw_full_history_audit.json';audit=p.json.loads(parent_audit_path.read_text())
    assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(parent_meta_path) and audit['stocks_checked']==1178
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates,symbols=calendar['dates'],calendar['union_symbols'];mask=p.np.load(PARENT/'dataset/hour_complete.npy',mmap_mode='r')
    original_execution=p.np.load(ORIGINAL/'dataset/earliest_execution_times.npy')
    patches={};mask_patches={};keys=defaultdict(set);updates=[];new_records=[]
    for r in stage['records']:
        symbol,date=r['symbol'],r['date'];assert '2020-01-01'<=date<='2023-12-31'
        path=p.Path(r['primary_source_path']);assert p.sha(path)==r['primary_source_sha256'];primary=p.json.loads(path.read_text())
        canonical=p.ROOT/'gru_beta_neutral_20261008/canonical_ipo_v6'
        p.sys.path.insert(0,str(canonical))
        from .canonical_ipo_v6.backfilled_hour_panel import factor_state
        factors=factor_state(primary,dates);route='unchanged_original_v6_asof_factor_compiler'
        factor_lookup=dict(zip(dates,factors));factor=float(factor_lookup[date]);assert p.np.isfinite(factor) and factor>0
        retained=ORIGINAL/'symbol_hour_features'/(symbol+'.parquet');checked=0
        for b in p.pq.ParquetFile(retained).iter_batches(batch_size=1024,columns=['date','hour_complete','adjustment_factor'],use_threads=False):
            for q in b.to_pylist():
                if q['hour_complete']:assert q['adjustment_factor']==factor_lookup[q['date']];checked+=1
        assert checked>0
        details=[]
        for h in r['hours']:
            row=dates.index(date)*4+h['slot'];column=symbols.index(symbol)
            prices=[float(D(h[f])/10000) for f in ['open','high','low','close']];values=[v*factor for v in prices]+[h['volume']]+prices+[factor]
            detail=dict(symbol=symbol,date=date,slot=h['slot'],row_index=row,column_index=column,
                canonical_float32_values=p.np.asarray(values,p.np.float32).tolist(),exact_raw_price_x10000={f:h[f] for f in ['open','high','low','close']},
                reported_volume_shares=h['volume'],reported_amount_x10000=h['amount_x10000'],quantity_reporting_precision='reported_integer_lots_scaled100',true_share_quantity_recovered=False,adjustment_factor=factor,
                minute_source_path=r['minute_path'],minute_source_sha256=r['minute_sha256'],source_ready_ms=h['source_ready_ms'],source_event_ms_max=h['source_event_ms_max'],
                source_arrival_before_execution_certified=False,actual_receiver_time_certified=False,execution_proxy_policy=h['execution_proxy_policy'],
                existing_earliest_execution_utc_ns=int(original_execution[row]),independent_all48_five_minute_prices_exact=True,all60_observed_vendor_minutes_verified=True)
            assert not mask[row,column]
            assert (row,column) not in patches;patches[row,column]=values;mask_patches[row,column]=True;details.append(detail)
        if details:new_records.append(dict(symbol=symbol,date=date,primary_factor_source_path=str(path),primary_factor_source_sha256=r['primary_source_sha256'],special_factor_route=route,retained_factor_hours_exactly_matching_parent_compiler=checked,hours=details))
    assert len(patches)==proof['hours_verified']==stage['hours_staged'] and not updates
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),bounded_copy_source_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/merge_native_candidate_raw_panel.py'),
        parent_candidate=PARENT.name,parent_meta_sha256=p.sha(parent_meta_path),parent_full_history_audit_sha256=p.sha(parent_audit_path),
        minute_staging_receipt_sha256=p.sha(stage_path),independently_verified_minute_staging_sha256=p.sha(proof_path),
        candidate_version=DEST.name,stage='raw_panel_only',records=new_records,reception_evidence_updates=updates,
        reception_evidence_updates_do_not_change_previously_admitted_market_values=True,arrays=[],model_input_complete=False,
        production_ready=False,training_admission_ready=False,features_labels_execution_prices_not_copied=True,new_training_or_backtest_run=False,
        source_live_clock_certified=False,source_original_clock_and_coverage_failures_preserved=True,status='factor_checked_historical_observed_reported_bar_merge')
    p.atomic_json(output,receipt);del mask
    for name,changes in [('hour_ohlcv.npy',patches),('hour_complete.npy',mask_patches)]:
        receipt['arrays'].append(copy_patched(PARENT/'dataset'/name,dataset/name,parent['files_sha256'][name],changes));p.atomic_json(output,receipt)
    for name in ['times.npy','symbols.json']:
        raw=(PARENT/'dataset'/name).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==parent['files_sha256'][name];(dataset/name).write_bytes(raw)
        receipt['arrays'].append(dict(path=str(dataset/name),sha256=parent['files_sha256'][name],verified_identity=p.identity(dataset/name),patched_stock_hours=0))
    for version in ['backfilled_panel_candidate_native_event_v7_raw','backfilled_panel_candidate_native_event_v8_raw','backfilled_panel_candidate_native_event_v9_raw','backfilled_panel_candidate_native_second_v10_raw','backfilled_panel_candidate_native_millisecond_v11_raw','backfilled_panel_candidate_native_quote_v12_raw']:
        for r in p.json.loads((p.BASE/version/'raw_panel_merge_receipt.json').read_text())['records']:
            for h in r['hours']:keys[h['date'],r['symbol']].add(h['slot'])
    for r in new_records:
        for h in r['hours']:keys[h['date'],r['symbol']].add(h['slot'])
    meta=dict(schema_version='historical_reported_raw_candidate_v13',candidate_version=DEST.name,stage='raw_panel_only',parent_candidate=PARENT.name,parent_meta_sha256=receipt['parent_meta_sha256'],
        original_candidate=ORIGINAL.name,historical_union_stocks=1178,hour_rows=8476,hour_ohlcv_columns=parent['hour_ohlcv_columns'],canonical_array_representation=parent['canonical_array_representation'],
        files_sha256={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']},model_input_complete=False,production_ready=False,training_admission_ready=False,
        raw_market_data_is_synthetic=False,admitted_raw_hours=parent['admitted_raw_hours']+len(patches),added_raw_hours_this_version=len(patches),
        stockdays_with_any_admitted_raw_hours=len(keys),stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in keys.values()),
        previously_unknown_arrival_hours_with_observed_evidence=0,unknown_local_arrival_hours=parent['unknown_local_arrival_hours']+len(patches),arrival_evidence_for_added_hours_is_observed=False,historical_execution_proxy_policy=stage['retrospective_execution_proxy_policy'],reported_share_volume_not_exact_true_minute_volume=True,
        source_availability_live_clock_certificate=False,original_failed_quote_clock_and_coverage_gates_retained=True,historical_minimum_missing_hours=parent['historical_minimum_missing_hours']-len(patches),historical_admitted_raw_hours=parent.get('historical_admitted_raw_hours',0)+len(patches),tail_admitted_raw_hours=1369,
        tail_missing_hours=parent['tail_missing_hours'],full_new_raw_history_audit_pending=True,stale_features_labels_or_execution_prices_present=False)
    p.atomic_json(dataset/'meta.json',meta)
    receipt.update(status='historical_reported_bar_hours_merged_pending_full1178_audit',finished_utc=p.utc(),added_raw_hours=len(patches),total_admitted_raw_hours=meta['admitted_raw_hours'],
        previously_unknown_reception_hours_updated=0,new_raw_meta_sha256=p.sha(dataset/'meta.json'),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
