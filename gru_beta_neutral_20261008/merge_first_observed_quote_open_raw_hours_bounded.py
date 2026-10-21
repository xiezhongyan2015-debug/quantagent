"""Merge actual native millisecond hours after independent minute and coarse-definition reconciliation.

All retained raw data stays immutable; actual receiver clocks and share quantities remain observed.
"""
from decimal import Decimal as D
from collections import defaultdict
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
from .merge_native_candidate_raw_panel import states
from .bounded_clone_array_copy import copy_patched
from .stage_first_observed_quote_open_v23_bounded import DEST as STAGE

PARENT=p.BASE/'backfilled_panel_candidate_native_observed_second_v22_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_first_quote_v23_raw'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'


def main():
    lock=p.locked('merge_first_observed_quote_open_raw');dataset=DEST/'dataset';dataset.mkdir(parents=True,exist_ok=True)
    output=DEST/'raw_panel_merge_receipt.json';assert not output.exists()
    stage_path=STAGE/'receipt.json';stage=p.json.loads(stage_path.read_text());proof_path=STAGE/'independent_verification.json';proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
    parent_meta_path=PARENT/'dataset/meta.json';parent=p.json.loads(parent_meta_path.read_text());parent_audit_path=PARENT/'raw_full_history_audit.json';audit=p.json.loads(parent_audit_path.read_text())
    assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(parent_meta_path) and audit['stocks_checked']==1178
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates,symbols=calendar['dates'],calendar['union_symbols'];mask=p.np.load(PARENT/'dataset/hour_complete.npy',mmap_mode='r')
    original_execution=p.np.load(ORIGINAL/'dataset/earliest_execution_times.npy')
    patches={};mask_patches={};keys=defaultdict(set);updates=[];new_records=[]
    for r in stage['records']:
        symbol,date=r['symbol'],r['date'];assert date in dates
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
                exact_native_volume_shares=h['volume'],exact_native_calculated_amount_x10000=h['amount_x10000'],quantity_reporting_precision='actual_native_shares',adjustment_factor=factor,
                minute_source_path=r['minute_path'],minute_source_sha256=r['minute_sha256'],source_ready_ms=h['source_ready_ms'],source_event_ms_max=h['source_event_ms_max'],
                source_arrival_before_execution_certified=int(p.datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())*10**9+h['source_ready_ms']*1000000<int(original_execution[row]),actual_receiver_time_certified=True,
                existing_earliest_execution_utc_ns=int(original_execution[row]),all60_source_defined_minutes_verified=True,independent_positive_minute_ohlcv_exact=True,real_zero_trade_quote_witnesses_exact=True,actual_trade_minutes=h['actual_trade_minutes'],genuine_zero_trade_quote_minutes=h['genuine_zero_trade_quote_minutes'],coarse_previous_close_formula_reconciled=h['coarse_reference_kind']=='fixed_previous_close_coarse_reconciled',coarse_reference_kind=h['coarse_reference_kind'],coarse_reference_definition_verified=True,
                actual_native_first_trade_definition_unchanged=True,coarse_reference=h['coarse_reference'],coarse_model=h['coarse_model'],current_quote_cut=h['current_quote_cut'],previous_quote_cut=h['previous_quote_cut'],opening_quote=h['opening_quote'],sampled_quote_count=h['sampled_quote_count'],all_sampled_quote_whole_prefixes_exact=h['all_sampled_quote_whole_prefixes_exact'],coarse_quote_dependency_ready_ms=h['coarse_quote_dependency_ready_ms'],observed_quote_price_extrema_do_not_replace_actual_minute_extrema=True)
            assert not mask[row,column]
            assert (row,column) not in patches;patches[row,column]=values;mask_patches[row,column]=True;details.append(detail)
        if details:new_records.append(dict(symbol=symbol,date=date,primary_factor_source_path=str(path),primary_factor_source_sha256=r['primary_source_sha256'],special_factor_route=route,retained_factor_hours_exactly_matching_parent_compiler=checked,hours=details))
    assert len(patches)==proof['hours_verified']==stage['hours_staged'] and not updates
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),bounded_copy_source_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/bounded_clone_array_copy.py'),
        parent_candidate=PARENT.name,parent_meta_sha256=p.sha(parent_meta_path),parent_full_history_audit_sha256=p.sha(parent_audit_path),
        minute_staging_receipt_sha256=p.sha(stage_path),independently_verified_minute_staging_sha256=p.sha(proof_path),
        candidate_version=DEST.name,stage='raw_panel_only',records=new_records,reception_evidence_updates=updates,
        reception_evidence_updates_do_not_change_previously_admitted_market_values=True,arrays=[],model_input_complete=False,
        production_ready=False,training_admission_ready=False,features_labels_execution_prices_not_copied=True,new_training_or_backtest_run=False,
        source_live_clock_certified=False,source_original_clock_and_coverage_failures_preserved=True,status='factor_checked_actual_millisecond_reconciled_hour_merge')
    p.atomic_json(output,receipt);del mask
    for name,changes in [('hour_ohlcv.npy',patches),('hour_complete.npy',mask_patches)]:
        receipt['arrays'].append(copy_patched(PARENT/'dataset'/name,dataset/name,parent['files_sha256'][name],changes));p.atomic_json(output,receipt)
    for name in ['times.npy','symbols.json']:
        raw=(PARENT/'dataset'/name).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==parent['files_sha256'][name];(dataset/name).write_bytes(raw)
        receipt['arrays'].append(dict(path=str(dataset/name),sha256=parent['files_sha256'][name],verified_identity=p.identity(dataset/name),patched_stock_hours=0))
    for version in ['backfilled_panel_candidate_native_event_v7_raw','backfilled_panel_candidate_native_event_v8_raw','backfilled_panel_candidate_native_event_v9_raw','backfilled_panel_candidate_native_second_v10_raw','backfilled_panel_candidate_native_millisecond_v11_raw','backfilled_panel_candidate_native_quote_v12_raw','backfilled_panel_candidate_historical_reported_v13_raw','backfilled_panel_candidate_historical_partial_reported_v14_raw','backfilled_panel_candidate_native_reconciled_v15_raw','backfilled_panel_candidate_native_expanded_v16_raw','backfilled_panel_candidate_native_quote_reconciled_v17_raw','backfilled_panel_candidate_native_nominal_cut_v18_raw','backfilled_panel_candidate_native_sampled_quote_v19_raw','backfilled_panel_candidate_native_strict_endpoint_v20_raw','backfilled_panel_candidate_native_first_hour_extrema_v21_raw','backfilled_panel_candidate_native_observed_second_v22_raw']:
        for r in p.json.loads((p.BASE/version/'raw_panel_merge_receipt.json').read_text())['records']:
            for h in r['hours']:keys[h['date'],r['symbol']].add(h['slot'])
    for r in new_records:
        for h in r['hours']:keys[h['date'],r['symbol']].add(h['slot'])
    meta=dict(schema_version='native_first_observed_quote_open_raw_candidate_v23',candidate_version=DEST.name,stage='raw_panel_only',parent_candidate=PARENT.name,parent_meta_sha256=receipt['parent_meta_sha256'],
        original_candidate=ORIGINAL.name,historical_union_stocks=1178,hour_rows=8476,hour_ohlcv_columns=parent['hour_ohlcv_columns'],canonical_array_representation=parent['canonical_array_representation'],
        files_sha256={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']},model_input_complete=False,production_ready=False,training_admission_ready=False,
        raw_market_data_is_synthetic=False,admitted_raw_hours=parent['admitted_raw_hours']+len(patches),added_raw_hours_this_version=len(patches),
        stockdays_with_any_admitted_raw_hours=len(keys),stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in keys.values()),
        previously_unknown_arrival_hours_with_observed_evidence=0,unknown_local_arrival_hours=parent['unknown_local_arrival_hours'],arrival_evidence_for_added_hours_is_observed=True,historical_execution_proxy_policy=parent['historical_execution_proxy_policy'],reported_share_volume_not_exact_true_minute_volume=True,
        source_availability_live_clock_certificate=False,original_failed_quote_clock_and_coverage_gates_retained=True,historical_minimum_missing_hours=parent['historical_minimum_missing_hours'],historical_admitted_raw_hours=parent['historical_admitted_raw_hours'],tail_admitted_raw_hours=parent['tail_admitted_raw_hours']+len(patches),
        tail_missing_hours=parent['tail_missing_hours']-len(patches),full_new_raw_history_audit_pending=True,stale_features_labels_or_execution_prices_present=False)
    p.atomic_json(dataset/'meta.json',meta)
    receipt.update(status='native_quote_reconciled_hours_merged_pending_full1178_audit',finished_utc=p.utc(),added_raw_hours=len(patches),total_admitted_raw_hours=meta['admitted_raw_hours'],
        previously_unknown_reception_hours_updated=0,new_raw_meta_sha256=p.sha(dataset/'meta.json'),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
