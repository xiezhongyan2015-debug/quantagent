"""Merge independently verified native minutes into a new raw-only panel.

Clone each source array in bounded binary chunks, checking the parent digest in
that required copy pass and hashing the new bytes as written. Never copy stale
features, labels or execution prices. Exact native integer minutes remain the
source of truth; raw arrays retain the existing float32 representation.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

PARENT=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
DEST=p.BASE/'backfilled_panel_candidate_native_event_v7_raw'


def states(obj,dates):
    if not obj['factors']:
        return p.np.ones(len(dates)),[]
    rows=[dict(zip(obj['factor_fields'],row)) for row in obj['factors']]
    assert all(r['code']==obj['code'] for r in rows)
    rows.sort(key=lambda r:r['dividOperateDate'])
    days=[r['dividOperateDate'] for r in rows]
    assert len(days)==len(set(days))
    values=p.np.array([float(r['foreAdjustFactor']) for r in rows])
    assert p.np.isfinite(values).all() and (values>0).all()
    pos=p.np.searchsorted(p.np.array(days),p.np.array(dates),side='right')-1
    result=p.np.full(len(dates),p.np.nan)
    valid=pos>=0
    result[valid]=values[pos[valid]]
    return result,rows


def copy_patched(source,destination,expected_sha,patches):
    before=p.identity(source)
    original_digest=p.hashlib.sha256()
    new_digest=p.hashlib.sha256()
    temp=destination.with_suffix('.npy.tmp')
    with source.open('rb') as original:
        version=p.np.lib.format.read_magic(original)
        assert version in [(1,0),(2,0)]
        reader=p.np.lib.format.read_array_header_1_0 if version==(1,0) else p.np.lib.format.read_array_header_2_0
        shape,fortran,dtype=reader(original)
        assert not fortran
        offset=original.tell()
        original.seek(0)
        header=original.read(offset)
        original_digest.update(header)
        new_digest.update(header)
        row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize
        rows_per_batch=max(1,(32*1024**2)//row_bytes)
        with temp.open('wb') as output:
            output.write(header)
            for start in range(0,shape[0],rows_per_batch):
                length=min(rows_per_batch,shape[0]-start)
                raw=original.read(length*row_bytes)
                assert len(raw)==length*row_bytes
                original_digest.update(raw)
                selected=[(r,c,value) for (r,c),value in patches.items() if start<=r<start+length]
                if selected:
                    raw=bytearray(raw)
                    view=p.np.frombuffer(raw,dtype=dtype).reshape((length,*shape[1:]))
                    for row,column,value in selected:
                        view[row-start,column]=value
                    del view
                output.write(raw)
                new_digest.update(raw)
            assert original.read(1)==b''
            output.flush()
            p.os.fsync(output.fileno())
    assert p.identity(source)==before and original_digest.hexdigest()==expected_sha
    temp.replace(destination)
    return dict(path=str(destination),sha256=new_digest.hexdigest(),shape=list(shape),dtype=str(dtype),
                verified_identity=p.identity(destination),source_expected_sha256=expected_sha,
                source_digest_verified_during_required_copy=True,patched_stock_hours=len(patches),batch_byte_target=32*1024**2)


def main():
    lock=p.locked('merge_native_raw_panel')
    assert p.sha(PARENT/'meta.json')=='6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    parent=p.json.loads((PARENT/'meta.json').read_text())
    stage_path=p.OUT/'new_candidate_v7_staging/receipt.json'
    stage=p.json.loads(stage_path.read_text())
    stage_proof_path=stage_path.parent/'independent_verification.json'
    stage_proof=p.json.loads(stage_proof_path.read_text())
    assert stage_proof['passed'] and stage_proof['audited_receipt_sha256']==p.sha(stage_path)
    audit_path=p.OUT/'full_history_audit.json'
    old_audit=p.json.loads(audit_path.read_text())
    assert old_audit['passed'] and old_audit['stocks_checked']==1178
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    symbols=calendar['union_symbols']
    dates=calendar['dates']
    selected={r['symbol'] for r in stage['records']}
    overrides=set(parent['factor_bridge_provenance']['applied_symbols'])|set(parent['IPO_factor_prefix_provenance']['applied_symbols'])|set(parent['IPO_factor_prefix_provenance']['previous_observed_seed_contract']['applied_symbols'])
    assert not selected&overrides,'Special factor override must use its existing verified compiler'
    DEST.mkdir(exist_ok=True)
    dataset=DEST/'dataset'
    dataset.mkdir(exist_ok=True)
    output=DEST/'raw_panel_merge_receipt.json'
    assert not output.exists()
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_meta_sha256=p.sha(PARENT/'meta.json'),
        independently_verified_minute_staging_sha256=p.sha(stage_proof_path),parent_full_history_audit_sha256=p.sha(audit_path),
        candidate_version=DEST.name,stage='raw_panel_only',model_input_complete=False,production_ready=False,
        features_labels_execution_prices_not_copied=True,new_features_or_labels_recalculated=False,
        new_training_or_backtest_run=False,records=[],arrays=[],status='preparing_factor_checked_raw_delta')
    raw_patches,complete_patches={},{}
    parent_complete=p.np.load(PARENT/'hour_complete.npy',mmap_mode='r')
    for record in stage['records']:
        symbol=record['symbol']
        primary_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        primary=p.json.loads(primary_path.read_text())
        assert primary['symbol']==symbol and primary['end']=='2026-09-23'
        state,events=states(primary,dates)
        lookup=dict(zip(dates,state))
        retained=PARENT.parent/'symbol_hour_features'/(symbol+'.parquet')
        checked=0
        for batch in p.pq.ParquetFile(retained).iter_batches(batch_size=1024,columns=['date','hour_complete','adjustment_factor'],use_threads=False):
            for old in batch.to_pylist():
                if old['hour_complete']:
                    assert old['adjustment_factor']==lookup[old['date']]
                    checked+=1
        assert checked>0
        factor=float(lookup['2026-09-23'])
        assert p.np.isfinite(factor) and factor>0
        column=symbols.index(symbol)
        index=dates.index('2026-09-23')*4
        details=[]
        for hour in record['hours']:
            row=index+hour['slot']
            assert not parent_complete[row,column]
            raw=[float(D(hour[f])/10000) for f in ['open','high','low','close']]
            value=[v*factor for v in raw]+[hour['volume']]+raw+[factor]
            raw_patches[row,column]=value
            complete_patches[row,column]=True
            details.append(dict(symbol=symbol,date='2026-09-23',slot=hour['slot'],row_index=row,column_index=column,
                canonical_float32_values=p.np.asarray(value,dtype=p.np.float32).tolist(),
                exact_raw_price_x10000={f:hour[f] for f in ['open','high','low','close']},
                exact_native_volume_shares=hour['volume'],exact_native_calculated_amount_x10000=hour['amount_x10000'],
                adjustment_factor=factor,minute_source_path=record['minute_path'],minute_source_sha256=record['minute_sha256'],
                source_ready_ms=hour['stored_dependency_ready_ms'],existing_earliest_execution_utc_ns=hour['existing_earliest_execution_utc_ns']))
        receipt['records'].append(dict(symbol=symbol,retained_factor_hours_exactly_matching_parent_compiler=checked,
            factor_policy='Parent default last observed foreAdjustFactor event whose effective date is at or before each session; no special override applies to these22stocks.',
            primary_factor_source_path=str(primary_path),primary_factor_source_sha256=p.sha(primary_path),hours=details,
            latest_effective_factor_event=next((e for e in reversed(events) if e['dividOperateDate']<='2026-09-23'),None)))
    assert len(raw_patches)==88
    p.atomic_json(output,receipt)
    del parent_complete
    for filename,patches in [('hour_ohlcv.npy',raw_patches),('hour_complete.npy',complete_patches)]:
        result=copy_patched(PARENT/filename,dataset/filename,parent['files_sha256'][filename],patches)
        receipt['arrays'].append(result)
        p.atomic_json(output,receipt)
    # Small raw coordinate files only; no stale derived features or masks.
    for filename in ['times.npy','symbols.json']:
        source=PARENT/filename
        raw=source.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==parent['files_sha256'][filename]
        (dataset/filename).write_bytes(raw)
        receipt['arrays'].append(dict(path=str(dataset/filename),sha256=parent['files_sha256'][filename],verified_identity=p.identity(dataset/filename),patched_stock_hours=0))
    meta=dict(schema_version='native_event_raw_candidate_v7',candidate_version=DEST.name,stage='raw_panel_only',
        model_input_complete=False,production_ready=False,raw_market_data_is_synthetic=False,
        parent_candidate='backfilled_panel_candidate_canonical_ipo_v6',parent_meta_sha256=receipt['parent_meta_sha256'],
        source_minute_contract=stage['definition'],new_minute_source_definition_differs_from_parent_sampled_quote_controls=True,
        original_failed_quote_clock_and_coverage_gates_retained=True,source_availability_live_clock_certificate=False,
        historical_union_stocks=1178,hour_rows=8476,hour_ohlcv_columns=parent['hour_ohlcv_columns'],
        canonical_array_representation='Existing float32 representation; exact new native integer prices/quantities/amount remain in minute leaves and merge receipt.',
        files_sha256={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']},
        admitted_raw_stockdays=22,admitted_raw_hours=88,historical_minimum_missing_hours=3042,
        tail_missing_hours=84860-88,full_new_raw_history_audit_pending=True,
        stale_features_labels_or_execution_prices_present=False,features_and_labels_recalculated=False)
    p.atomic_json(dataset/'meta.json',meta)
    receipt.update(status='raw_panel_merged_pending_full_1178_scope_audit',finished_utc=p.utc(),
        added_raw_stockdays=22,added_raw_hours=88,new_raw_meta_sha256=p.sha(dataset/'meta.json'),
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt)
    lock.close()


if __name__=='__main__':
    main()
