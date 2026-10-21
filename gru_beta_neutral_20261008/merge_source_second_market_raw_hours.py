"""Merge independently validated historical market minutes, preserving unknown arrival.

Historical OHLCV truth is distinct from reception-time/live execution qualification.
No unknown reception timestamp is generated and no production gate is changed.
"""
from decimal import Decimal as D
from collections import defaultdict
from . import bounded_tail_pipeline as p
from .merge_native_candidate_raw_panel import states,copy_patched

PARENT=p.BASE/'backfilled_panel_candidate_native_event_v9_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_second_v10_raw'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'


def main():
    lock=p.locked('merge_source_second_market_raw')
    meta_path=PARENT/'dataset/meta.json';parent=p.json.loads(meta_path.read_text())
    audit_path=PARENT/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['stocks_checked']==1178 and audit['audited_raw_meta_sha256']==p.sha(meta_path)
    stage_path=p.OUT/'sina_corrob_native_minute_staging/receipt.json';stage=p.json.loads(stage_path.read_text());proof_path=stage_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
    plan=p.ROOT/'gru_research/PLAN.md';schema=p.SOURCE/'metadata_schema.json';spec=p.json.loads(schema.read_text());time=next(c for c in spec['columns'] if c['name']=='time_s');assert time['description']=='Seconds since midnight.'
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates,symbols=calendar['dates'],calendar['union_symbols']
    mask=p.np.load(PARENT/'dataset/hour_complete.npy',mmap_mode='r');execution=p.np.load(ORIGINAL/'dataset/earliest_execution_times.npy')
    dataset=DEST/'dataset';dataset.mkdir(parents=True,exist_ok=True);output=DEST/'raw_panel_merge_receipt.json';assert not output.exists()
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),bounded_copy_source_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/merge_native_candidate_raw_panel.py'),parent_candidate=PARENT.name,parent_meta_sha256=p.sha(meta_path),parent_full_history_audit_sha256=p.sha(audit_path),minute_staging_receipt_sha256=p.sha(stage_path),independently_verified_minute_staging_sha256=p.sha(proof_path),research_plan_path=str(plan),research_plan_sha256=p.sha(plan),published_second_schema_path=str(schema),published_second_schema_sha256=p.sha(schema),market_minute_admission_contract='nativepositiveactualtrades; fixedfloor_left clock with globallyunchanged2sphasegrace; every60actualminuteOHLCVexactSinaendlabels; TencenthourOHLCVexact; wholedaynativeprimarysixexact; nativeinventoryexact; transactionIDglobalorderstrict; originalfactorcompilerasofexact',source_arrival_admission_contract='not asserted: missing reception timestamps stayunknown; only retrospective marketdatacompleteness admitted, liveexecution/productionqualification is unchanged',candidate_version=DEST.name,stage='raw_panel_only',model_input_complete=False,production_ready=False,training_admission_ready=False,features_labels_execution_prices_not_copied=True,new_features_or_labels_recalculated=False,new_training_or_backtest_run=False,records=[],arrays=[],status='factor_checked_historical_market_minute_merge')
    raw_patches={};mask_patches={};keys=defaultdict(set)
    for record in stage['records']:
        symbol,date=record['symbol'],record['date'];source=p.Path(record['primary_daily_reference']['path']);assert p.sha(source)==record['primary_daily_reference']['sha256'];primary=p.json.loads(source.read_text())
        from .canonical_ipo_v6 import validated_factor_bridge as bridge
        factors=bridge.override_factor_state(primary,dates);route=bridge.provenance(symbol)
        if factors is None:factors,events=states(primary,dates)
        else:events=[]
        lookup=dict(zip(dates,factors));retained=ORIGINAL/'symbol_hour_features'/(symbol+'.parquet');checked=0
        for batch in p.pq.ParquetFile(retained).iter_batches(batch_size=1024,columns=['date','hour_complete','adjustment_factor'],use_threads=False):
            for r in batch.to_pylist():
                if r['hour_complete']:assert r['adjustment_factor']==lookup[r['date']];checked+=1
        factor=float(lookup[date]);assert checked>0 and p.np.isfinite(factor) and factor>0
        details=[]
        for h in record['hours']:
            row=dates.index(date)*4+h['slot'];column=symbols.index(symbol);assert not mask[row,column]
            assert h['source_local_arrival_ms'] is None
            raw=[float(D(h[f])/10000) for f in ['open','high','low','close']];values=[v*factor for v in raw]+[h['volume']]+raw+[factor]
            assert (row,column) not in raw_patches;raw_patches[row,column]=values;mask_patches[row,column]=True;keys[date,symbol].add(h['slot'])
            details.append(dict(symbol=symbol,date=date,slot=h['slot'],row_index=row,column_index=column,canonical_float32_values=p.np.asarray(values,p.np.float32).tolist(),exact_raw_price_x10000={f:h[f] for f in ['open','high','low','close']},exact_native_volume_shares=h['volume'],exact_native_calculated_amount_x10000=h['amount_x10000'],adjustment_factor=factor,minute_source_path=record['minute_path'],minute_source_sha256=record['minute_sha256'],reported_trade_second_max=h['source_event_seconds_max'],source_local_arrival_ms=None,source_arrival_before_execution_certified=False,existing_earliest_execution_utc_ns=int(execution[row]),all60_independent_minute_ohlcv_exact=True))
        receipt['records'].append(dict(symbol=symbol,date=date,retained_factor_hours_exactly_matching_parent_compiler=checked,primary_factor_source_path=str(source),primary_factor_source_sha256=record['primary_daily_reference']['sha256'],special_factor_route=route,hours=details))
    assert len(raw_patches)==proof['hours_minute_truth_verified']==179
    p.atomic_json(output,receipt);del mask
    for name,patches in [('hour_ohlcv.npy',raw_patches),('hour_complete.npy',mask_patches)]:
        receipt['arrays'].append(copy_patched(PARENT/'dataset'/name,dataset/name,parent['files_sha256'][name],patches));p.atomic_json(output,receipt)
    for name in ['times.npy','symbols.json']:
        data=(PARENT/'dataset'/name).read_bytes();assert p.hashlib.sha256(data).hexdigest()==parent['files_sha256'][name];(dataset/name).write_bytes(data);receipt['arrays'].append(dict(path=str(dataset/name),sha256=parent['files_sha256'][name],verified_identity=p.identity(dataset/name),patched_stock_hours=0))
    for version in ['backfilled_panel_candidate_native_event_v7_raw','backfilled_panel_candidate_native_event_v8_raw','backfilled_panel_candidate_native_event_v9_raw']:
        inherited=p.json.loads((p.BASE/version/'raw_panel_merge_receipt.json').read_text())
        for r in inherited['records']:
            for h in r['hours']:keys[h['date'],r['symbol']].add(h['slot'])
    total=parent['admitted_raw_hours']+len(raw_patches)
    meta=dict(schema_version='native_second_market_raw_candidate_v10',candidate_version=DEST.name,stage='raw_panel_only',parent_candidate=PARENT.name,parent_meta_sha256=receipt['parent_meta_sha256'],original_candidate=ORIGINAL.name,historical_union_stocks=1178,hour_rows=8476,hour_ohlcv_columns=parent['hour_ohlcv_columns'],canonical_array_representation=parent['canonical_array_representation'],files_sha256={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']},model_input_complete=False,production_ready=False,training_admission_ready=False,raw_market_data_is_synthetic=False,admitted_raw_hours=total,added_raw_hours_this_version=len(raw_patches),stockdays_with_any_admitted_raw_hours=len(keys),stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in keys.values()),source_reported_second_minutes_with_independent_ohlcv_verification=True,unknown_local_arrival_hours=len(raw_patches),unknown_local_arrival_timestamps_never_invented=True,inherited_recorded_arrival_checks_preserved=True,source_availability_live_clock_certificate=False,original_failed_quote_clock_and_coverage_gates_retained=True,historical_minimum_missing_hours=3042,tail_missing_hours=84860-total,full_new_raw_history_audit_pending=True,stale_features_labels_or_execution_prices_present=False,features_and_labels_recalculated=False,market_minute_admission_contract=receipt['market_minute_admission_contract'],source_arrival_admission_contract=receipt['source_arrival_admission_contract'])
    p.atomic_json(dataset/'meta.json',meta);receipt.update(status='raw_panel_merged_pending_full1178_audit_unknown_arrival_preserved',finished_utc=p.utc(),added_raw_hours=len(raw_patches),added_raw_stockdays_with_qualified_hours=len(receipt['records']),total_admitted_raw_hours=total,total_stockdays_with_any_admitted_raw_hours=len(keys),total_stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in keys.values()),new_raw_meta_sha256=p.sha(dataset/'meta.json'),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
