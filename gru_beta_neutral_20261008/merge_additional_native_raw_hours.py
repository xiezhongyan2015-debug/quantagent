"""Extend audited V7 raw history with independently verified partial-day hours."""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .merge_native_candidate_raw_panel import copy_patched,states

PARENT=p.BASE/'backfilled_panel_candidate_native_event_v7_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_event_v8_raw'
ORIGINAL=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'


def main():
    lock=p.locked('merge_additional_native_raw')
    parent_meta_path=PARENT/'dataset/meta.json'
    parent=p.json.loads(parent_meta_path.read_text())
    audit_path=PARENT/'raw_full_history_audit.json'
    audit=p.json.loads(audit_path.read_text())
    assert audit['passed'] and audit['stocks_checked']==1178 and audit['audited_raw_meta_sha256']==p.sha(parent_meta_path)
    stage_path=p.OUT/'additional_native_hour_staging/receipt.json'
    stage=p.json.loads(stage_path.read_text())
    proof_path=stage_path.parent/'independent_verification.json'
    proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    dates,symbols=calendar['dates'],calendar['union_symbols']
    original_meta=p.json.loads((ORIGINAL/'dataset/meta.json').read_text())
    special=set(original_meta['factor_bridge_provenance']['applied_symbols'])|set(original_meta['IPO_factor_prefix_provenance']['applied_symbols'])
    dataset=DEST/'dataset';dataset.mkdir(parents=True,exist_ok=True)
    output=DEST/'raw_panel_merge_receipt.json';assert not output.exists()
    receipt=dict(started_utc=p.utc(),status='factor_checked_incremental_merge',source_code_sha256=p.sha(__file__),
        bounded_copy_source_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/merge_native_candidate_raw_panel.py'),
        parent_candidate=PARENT.name,parent_meta_sha256=p.sha(parent_meta_path),parent_full_history_audit_sha256=p.sha(audit_path),
        independently_verified_minute_staging_sha256=p.sha(proof_path),minute_staging_receipt_sha256=p.sha(stage_path),
        candidate_version=DEST.name,stage='raw_panel_only',model_input_complete=False,production_ready=False,
        features_labels_execution_prices_not_copied=True,new_features_or_labels_recalculated=False,new_training_or_backtest_run=False,records=[],arrays=[])
    complete=p.np.load(PARENT/'dataset/hour_complete.npy',mmap_mode='r')
    raw_patches,mask_patches={},{}
    for record in stage['records']:
        if not record['hours']:continue
        symbol=record['symbol'];assert symbol not in special
        primary_path=p.Path(record['primary_source_path'])
        assert p.sha(primary_path)==record['primary_source_sha256']
        primary=p.json.loads(primary_path.read_text())
        factors,events=states(primary,dates)
        lookup=dict(zip(dates,factors))
        checked=0
        retained=ORIGINAL/'symbol_hour_features'/(symbol+'.parquet')
        for batch in p.pq.ParquetFile(retained).iter_batches(batch_size=1024,columns=['date','hour_complete','adjustment_factor'],use_threads=False):
            for r in batch.to_pylist():
                if r['hour_complete']:
                    assert r['adjustment_factor']==lookup[r['date']]
                    checked+=1
        assert checked>0
        factor=float(lookup['2026-09-23'])
        assert p.np.isfinite(factor) and factor>0
        column=symbols.index(symbol)
        offset=dates.index('2026-09-23')*4
        details=[]
        for hour in record['hours']:
            row=offset+hour['slot'];assert not complete[row,column]
            raw=[float(D(hour[f])/10000) for f in ['open','high','low','close']]
            values=[v*factor for v in raw]+[hour['volume']]+raw+[factor]
            assert (row,column) not in raw_patches
            raw_patches[row,column]=values;mask_patches[row,column]=True
            details.append(dict(symbol=symbol,date='2026-09-23',slot=hour['slot'],row_index=row,column_index=column,
                canonical_float32_values=p.np.asarray(values,dtype=p.np.float32).tolist(),
                exact_raw_price_x10000={f:hour[f] for f in ['open','high','low','close']},exact_native_volume_shares=hour['volume'],
                exact_native_calculated_amount_x10000=hour['amount_x10000'],adjustment_factor=factor,
                minute_source_path=record['minute_path'],minute_source_sha256=record['minute_sha256'],
                source_ready_ms=hour['stored_dependency_ready_ms'],existing_earliest_execution_utc_ns=hour['existing_earliest_execution_utc_ns']))
        receipt['records'].append(dict(symbol=symbol,retained_factor_hours_exactly_matching_parent_compiler=checked,
            primary_factor_source_path=str(primary_path),primary_factor_source_sha256=record['primary_source_sha256'],
            latest_effective_factor_event=next((e for e in reversed(events) if e['dividOperateDate']<='2026-09-23'),None),hours=details))
    assert len(raw_patches)==proof['additional_hours_verified']
    p.atomic_json(output,receipt);del complete
    for name,patches in [('hour_ohlcv.npy',raw_patches),('hour_complete.npy',mask_patches)]:
        receipt['arrays'].append(copy_patched(PARENT/'dataset'/name,dataset/name,parent['files_sha256'][name],patches))
        p.atomic_json(output,receipt)
    for name in ['times.npy','symbols.json']:
        raw=(PARENT/'dataset'/name).read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==parent['files_sha256'][name]
        (dataset/name).write_bytes(raw)
        receipt['arrays'].append(dict(path=str(dataset/name),sha256=parent['files_sha256'][name],verified_identity=p.identity(dataset/name),patched_stock_hours=0))
    prior=p.json.loads((PARENT/'raw_panel_merge_receipt.json').read_text())
    slots=defaultdict(set)
    for r in [*prior['records'],*receipt['records']]:
        slots[r['symbol']].update(h['slot'] for h in r['hours'])
    added=len(raw_patches);total=parent['admitted_raw_hours']+added
    meta=dict(schema_version='native_event_raw_candidate_v8',candidate_version=DEST.name,stage='raw_panel_only',
        model_input_complete=False,production_ready=False,raw_market_data_is_synthetic=False,
        parent_candidate=PARENT.name,parent_meta_sha256=receipt['parent_meta_sha256'],original_candidate=ORIGINAL.name,
        source_minute_contract=stage['definition'],new_minute_source_definition_differs_from_parent_sampled_quote_controls=True,
        original_failed_quote_clock_and_coverage_gates_retained=True,source_availability_live_clock_certificate=False,
        historical_union_stocks=1178,hour_rows=8476,hour_ohlcv_columns=parent['hour_ohlcv_columns'],
        canonical_array_representation=parent['canonical_array_representation'],files_sha256={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']},
        admitted_raw_hours=total,stockdays_with_any_admitted_raw_hours=len(slots),stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in slots.values()),
        added_raw_hours_this_version=added,historical_minimum_missing_hours=3042,tail_missing_hours=84860-total,
        full_new_raw_history_audit_pending=True,stale_features_labels_or_execution_prices_present=False,features_and_labels_recalculated=False)
    p.atomic_json(dataset/'meta.json',meta)
    receipt.update(status='raw_panel_merged_pending_full_1178_scope_audit',finished_utc=p.utc(),
        added_raw_stockdays_with_qualified_hours=len(receipt['records']),added_raw_hours=added,total_admitted_raw_hours=total,
        total_stockdays_with_any_admitted_raw_hours=len(slots),total_stockdays_with_all_four_admitted_raw_hours=sum(len(v)==4 for v in slots.values()),
        new_raw_meta_sha256=p.sha(dataset/'meta.json'),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':
    from collections import defaultdict
    main()
