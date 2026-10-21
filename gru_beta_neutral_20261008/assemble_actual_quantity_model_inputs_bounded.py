"""Assemble all122 model inputs without keeping dense tensors resident.

Every stock's hourly formulas/labels were freshly recalculated and audited.
Unchanged tensor cells are copied from the pinned baseline; changed cells come
from those audited recalculations. Slow inputs are reused only after all daily,
formula and PIT source pins match. The required normalization pass verifies the
slow raw digest in flight. No training admission is implied by assembly.
"""
import io
from . import bounded_tail_pipeline as p
from .bounded_clone_array_copy import copy_patched,clone_file
from .verify_native_raw_full_history import header
from gru_research.minute_data import HOUR_FEATURES
from gru_research.assemble import normalize_cross_section,validate_slow_dates

OLD=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'
RAW=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_derived'
SLOW=OLD/'slow_features'
ART=p.ROOT/'gru_research/artifacts'


def slow_invariance():
    slow_path=SLOW/'slow_panel_receipt.json';slow=p.json.loads(slow_path.read_text())
    prior_proof_path=OLD/'independent_completion_audit.json';prior=p.json.loads(prior_proof_path.read_text())
    assert prior['passed'] and prior['candidate_meta_sha256']==p.sha(OLD/'dataset/meta.json')
    assert prior['frozen_64_factor_definitions_preserved'] and prior['all_19_financial_PIT_fields_and_provenance_exactly_preserved']
    assert slow['shape']==[2119,1178,98] and not slow['factor_selection_changed']
    contract=slow['source_contract']
    formula_paths=[p.ROOT/'gru_beta_neutral_20261008/canonical_ipo_v6/backfilled_slow_panel.py',SLOW/'calculation_source.py']
    accepted_formula=next(path for path in formula_paths if path.exists() and p.sha(path)==contract['code_sha256'])
    for path,key in [(p.ROOT/'gru_beta_neutral_20261008/canonical_ipo_v6/historical_factor_compiler.py','compiler_sha256'),
                     (p.ROOT/'quantagent/src/quanta_agents/factor_lab_a/dsl.py','original_dsl_sha256'),(ART/'slow_factor_selection.json','selection_sha256')]:
        assert p.sha(path)==contract[key],(path,'slow formula or frozen selection changed')
    reusable_path=p.BASE/'backfilled_panel_candidate_native_event_v8_derived/slow_source_invariance_receipt.json'
    reusable=p.json.loads(reusable_path.read_text())
    parent_creator_path=reusable_path.parent/'model_input_assembly_receipt.json'
    parent_creator=p.json.loads(parent_creator_path.read_text())
    parent_proof_path=reusable_path.parent/'model_input_independent_verification.json'
    parent_proof=p.json.loads(parent_proof_path.read_text())
    assert parent_proof['passed'] and parent_proof['audited_assembly_receipt_sha256']==p.sha(parent_creator_path)
    assert parent_proof['final_dataset_meta_sha256']==p.sha(reusable_path.parent/'dataset/meta.json')
    assert parent_creator['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/assemble_native_model_inputs_bounded.py')
    assert parent_creator['slow_invariance_receipt_sha256']==p.sha(reusable_path)
    reusable_primary={r['symbol']:r for r in reusable['primary_sources']}
    reused_primary_count=0
    primary=[]
    for record in slow['primary_sources']:
        path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(record['symbol']+'.json')
        before=p.identity(path);previous=reusable_primary[record['symbol']]
        if previous['path']==str(path) and previous['verified_identity']==before:
            digest=previous['sha256'];reused_primary_count+=1
        else:
            raw=path.read_bytes();digest=p.hashlib.sha256(raw).hexdigest()
        assert digest==record['primary_sha256'] and p.identity(path)==before
        primary.append(dict(symbol=record['symbol'],path=str(path),sha256=digest,verified_identity=before))
    assert len(primary)==1178
    original_slow=p.json.loads((ART/'slow_manifest.json').read_text())
    frozen_source_pins=[]
    fundamental=p.ROOT/'中证500_历年成分股_基本面_2010-2026_iFinD_PIT'
    for name,digest in original_slow['sources'].items():
        path=ART/'minute_memberships.parquet' if name=='memberships' else fundamental/name
        assert p.sha(path)==digest
        frozen_source_pins.append(dict(path=str(path),sha256=digest,verified_identity=p.identity(path)))
    # Reproduce the exact historical CS cohort pin (including2017 warmup).
    daily=p.ROOT/'中证500_历年成分股_日频_2010-2026_iFinD_后复权/memberships'
    membership_digest=p.hashlib.sha256()
    calendar=p.json.loads((ART/'minute_calendar.json').read_text())
    cohort_files=sorted(path for path in daily.glob('*.json.gz') if '2017-01-01'<=path.name[:10]<=calendar['dates'][-1])
    for path in cohort_files:
        raw=path.read_bytes();membership_digest.update(path.name.encode());membership_digest.update(p.hashlib.sha256(raw).digest())
    assert membership_digest.hexdigest()==contract['source_membership_sha256']
    pit_path=SLOW/'pit_provenance.parquet'
    raw=pit_path.read_bytes();assert len(raw)<=64*1024**2
    pit_hash=p.hashlib.sha256(raw).hexdigest()
    provenance=['financial_available_date','report_version_available_date','market_available_date','event_query_date','report_query_date','market_query_date','daily_source_date','report_period']
    rows=0;violations={name:0 for name in provenance}
    for batch in p.pq.ParquetFile(p.pa.BufferReader(raw)).iter_batches(batch_size=32768,columns=['date',*provenance],use_threads=False):
        f=batch.to_pandas();checked=validate_slow_dates(f)
        for name,count in checked.items():violations[name]+=count
        rows+=len(f)
    del raw
    assert rows==2119*1178 and not any(violations.values())
    result=dict(checked_utc=p.utc(),passed=True,slow_values_recomputed=False,reused_primary_stat_guard_certifications=reused_primary_count,reused_prior_invariance_receipt_sha256=p.sha(reusable_path),reused_prior_model_proof_sha256=p.sha(parent_proof_path),
        reuse_basis='All1178 daily/adjustment inputs, frozen formulas/selection and PIT sources exactly pinned; native hourly overlay does not modify these daily inputs.',
        slow_receipt_path=str(slow_path),slow_receipt_sha256=p.sha(slow_path),prior_independent_audit_sha256=p.sha(prior_proof_path),
        accepted_formula_path=str(accepted_formula),accepted_formula_sha256=contract['code_sha256'],primary_sources=primary,
        frozen_fundamental_sources=frozen_source_pins,historical_member_cohort_sha256=membership_digest.hexdigest(),
        PIT_rows_rechecked=rows,PIT_date_violations=violations,pit_provenance_path=str(pit_path),pit_provenance_sha256=pit_hash,
        slow_raw_path=str(SLOW/'slow_raw_by_day.npy'),expected_slow_raw_sha256=slow['raw_sha256'],
        slow_raw_identity=p.identity(SLOW/'slow_raw_by_day.npy'),slow_raw_digest_to_be_verified_during_required_normalization=True)
    p.atomic_json(DEST/'slow_source_invariance_receipt.json',result)
    return slow,result


def patches_from_recalculation(rebuild):
    mapping={'hour_features':HOUR_FEATURES,'labels':['y1','y2','y3','y4'],
        'execution_prices':['execution_price'],'execution_raw_prices':['execution_raw_price'],
        'execution_complete':['execution_window_complete'],'execution_tradable':['execution_tradable'],'execution_all_flat':['execution_all_flat'],'window80_complete':['window80_complete']}
    patches={name:{} for name in mapping}
    for column,record in enumerate(rebuild['records']):
        if not record['raw_hours_added']:continue
        path=p.Path(record['new_hour_path']);assert p.identity(path)==record['verified_identity']
        cols=['signal_time',*[field for fields in mapping.values() for field in fields]]
        new=p.pq.ParquetFile(path).read(columns=cols).to_pandas()
        original=p.Path(record['source_hour_path']);assert p.identity(original)==record['source_identity']
        old=p.pq.ParquetFile(original).read(columns=cols).to_pandas()
        assert p.np.array_equal(new.signal_time.to_numpy(),old.signal_time.to_numpy())
        for name,fields in mapping.items():
            dtype=p.np.bool_ if name in ['execution_complete','execution_tradable','execution_all_flat','window80_complete'] else p.np.float32
            a=old[fields].to_numpy(dtype);b=new[fields].to_numpy(dtype)
            equal=a==b
            if dtype==p.np.float32:equal|=p.np.isnan(a)&p.np.isnan(b)
            changed=p.np.flatnonzero(~equal.all(axis=1))
            source_rows=record['source_delta_hour_rows']
            allowed_features={r for source_row in source_rows for r in range(source_row,min(8476,source_row+81))}
            allowed_labels={r for source_row in source_rows for r in range(max(0,source_row-5),source_row)}
            assert set(map(int,changed))<=(allowed_features if name in ['hour_features','window80_complete'] else allowed_labels)
            for row in changed:
                patches[name][int(row),column]=b[row] if len(fields)>1 else b[row,0].item()
        del new,old
    assert len(patches['hour_features'])==rebuild['total_hour_feature_rows_changed']
    assert len(patches['labels'])==rebuild['total_label_rows_changed']
    return patches


def normalize_stream(dataset,old_meta,slow,invariance,receipt,receipt_path):
    hour_path=dataset/'hour_features.npy';member_path=dataset/'members.npy';slow_path=SLOW/'slow_raw_by_day.npy'
    before={str(path):p.identity(path) for path in [hour_path,member_path,slow_path]}
    assert before[str(slow_path)]==invariance['slow_raw_identity']
    hashes={str(path):p.hashlib.sha256() for path in [hour_path,member_path,slow_path]}
    names=HOUR_FEATURES+slow['names'];assert names==old_meta['features'] and len(names)==122
    output=dataset/'X.npy';temp=output.with_suffix('.npy.tmp');digest=p.hashlib.sha256()
    shapes={hour_path:(8476,1178,24),member_path:(8476,1178),slow_path:(2119,1178,98)}
    prior=p.BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_derived'
    prior_meta_path=prior/'dataset/meta.json';prior_meta=p.json.loads(prior_meta_path.read_text())
    prior_proof_path=prior/'model_input_independent_verification.json';prior_proof=p.json.loads(prior_proof_path.read_text());assert prior_proof['passed'] and prior_proof['final_dataset_meta_sha256']==p.sha(prior_meta_path)
    prior_assembly_path=prior/'model_input_assembly_receipt.json';prior_assembly=p.json.loads(prior_assembly_path.read_text());assert prior_proof['audited_assembly_receipt_sha256']==p.sha(prior_assembly_path)
    baseline_X=prior/'dataset/X.npy';baseline_identity=p.identity(baseline_X);prior_X=next(r for r in prior_assembly['arrays'] if p.Path(r['path']).name=='X.npy');assert baseline_identity==prior_X['verified_identity'] and prior_X['sha256']==prior_meta['files_sha256']['X.npy']
    receipt['normalization_clone_baseline']=dict(path=str(baseline_X),verified_identity=baseline_identity,sha256=prior_X['sha256'],meta_sha256=p.sha(prior_meta_path),independent_proof_sha256=p.sha(prior_proof_path),basis='Fully audited previous candidate; all122 cells still recomputed and final tensor independently compared with frozenV6.')
    assert not temp.exists() and not output.exists()
    cloned=clone_file(baseline_X,temp);baseline_digest=p.hashlib.sha256();written_bytes=0;changed_sessions=0
    with hour_path.open('rb') as hours,member_path.open('rb') as members,slow_path.open('rb') as slow_file,temp.open('r+b' if cloned else 'w+b') as target:
        for path,handle in [(hour_path,hours),(member_path,members),(slow_path,slow_file)]:
            shape,dtype,prefix=header(handle);assert shape==shapes[path]
            assert dtype==(p.np.dtype('bool') if path==member_path else p.np.dtype('float32'))
            hashes[str(path)].update(prefix)
        prefix_buffer=io.BytesIO()
        p.np.lib.format.write_array_header_1_0(prefix_buffer,dict(descr='<f4',fortran_order=False,shape=(8476,1178,122)))
        prefix=prefix_buffer.getvalue()
        if cloned:
            baseline_prefix=target.read(len(prefix));assert baseline_prefix==prefix;baseline_digest.update(baseline_prefix)
        else:target.write(prefix);written_bytes+=len(prefix)
        digest.update(prefix)
        finite=p.np.zeros(122,p.np.int64);member_hours=0
        for day in range(2119):
            hraw=hours.read(4*1178*24*4);mraw=members.read(4*1178);sraw=slow_file.read(1178*98*4)
            assert len(hraw)==4*1178*24*4 and len(mraw)==4*1178 and len(sraw)==1178*98*4
            for path,raw in [(hour_path,hraw),(member_path,mraw),(slow_path,sraw)]:hashes[str(path)].update(raw)
            h=p.np.frombuffer(hraw,p.np.float32).reshape(4,1178,24)
            m=p.np.frombuffer(mraw,p.np.bool_).reshape(4,1178)
            s=p.np.frombuffer(sraw,p.np.float32).reshape(1178,98)
            assert (m==m[0]).all()
            normalized_slow,_=normalize_cross_section(s,m[0])
            x=p.np.empty((4,1178,122),p.np.float32)
            for slot in range(4):
                x[slot,:,:24],_=normalize_cross_section(h[slot],m[slot]);x[slot,:,24:]=normalized_slow
                finite+=p.np.isfinite(x[slot,m[slot]]).sum(axis=0);member_hours+=int(m[slot].sum())
            raw=x.tobytes()
            if cloned:
                prior_raw=target.read(len(raw));assert len(prior_raw)==len(raw);baseline_digest.update(prior_raw)
                if raw!=prior_raw:
                    target.seek(-len(raw),p.os.SEEK_CUR);target.write(raw);written_bytes+=len(raw);changed_sessions+=1
                del prior_raw
            else:target.write(raw);written_bytes+=len(raw)
            digest.update(raw)
            if (day+1)%25==0 or day+1==2119:
                target.flush()
                receipt.update(status='normalizing122_features_from_verified_hour_and_unchanged_slow_sources',sessions_normalized=day+1,
                    updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
                p.atomic_json(receipt_path,receipt)
            del h,m,s,x,raw,hraw,mraw,sraw
        assert hours.read(1)==members.read(1)==slow_file.read(1)==b''
        assert target.read(1)==b''
        if cloned:assert baseline_digest.hexdigest()==prior_meta['files_sha256']['X.npy']
        target.flush();p.os.fsync(target.fileno())
    for path in [hour_path,member_path,slow_path]:assert p.identity(path)==before[str(path)]
    existing={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']}
    assert hashes[str(hour_path)].hexdigest()==existing['hour_features.npy']
    assert hashes[str(member_path)].hexdigest()==existing['members.npy']
    assert hashes[str(slow_path)].hexdigest()==invariance['expected_slow_raw_sha256']
    assert p.identity(baseline_X)==baseline_identity
    temp.replace(output)
    receipt['arrays'].append(dict(path=str(output),sha256=digest.hexdigest(),verified_identity=p.identity(output),shape=[8476,1178,122],dtype='float32',
        digest_computed_during_required_write=True,resident_scope='one session/4rows of normalized features',independent_copy_on_write_clone=cloned,hard_link_used=False,baseline_X_identity_unchanged=True,baseline_X_digest_verified_during_required_delta_comparison=cloned,explicitly_written_normalized_bytes=written_bytes,changed_normalized_sessions_written=changed_sessions,logical_file_bytes=output.stat().st_size))
    receipt['slow_raw_digest_verified_during_required_normalization']=True
    receipt['member_feature_finite_coverage']={name:float(n/member_hours) for name,n in zip(names,finite)}
    return names


def main():
    lock=p.locked('assemble_first_hour_extrema_model_inputs')
    dataset=DEST/'dataset';dataset.mkdir(exist_ok=True)
    output=DEST/'model_input_assembly_receipt.json';assert not output.exists()
    rebuild_path=DEST/'hour_feature_label_rebuild_receipt.json';rebuild=p.json.loads(rebuild_path.read_text())
    proof_path=DEST/'hour_feature_label_independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_rebuild_receipt_sha256']==p.sha(rebuild_path)
    raw_audit_path=RAW/'raw_full_history_audit.json';raw_audit=p.json.loads(raw_audit_path.read_text())
    assert raw_audit['passed'] and proof['raw_full_history_audit_sha256']==p.sha(raw_audit_path)
    old_meta_path=OLD/'dataset/meta.json';old_meta=p.json.loads(old_meta_path.read_text())
    assert p.sha(old_meta_path)=='6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    raw_meta=p.json.loads((RAW/'dataset/meta.json').read_text())
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='verifying_unchanged_slow_inputs',
        derived_hour_proof_path=str(proof_path),derived_hour_proof_sha256=p.sha(proof_path),
        raw_full_history_audit_sha256=p.sha(raw_audit_path),baseline_meta_sha256=p.sha(old_meta_path),
        bounded_clone_helper_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/bounded_clone_array_copy.py'),clone_capability_proof_sha256=p.sha(p.OUT/'clone_copy_capability_probe/receipt.json'),
        model_input_complete=False,production_ready=False,new_training_or_backtest_run=False,arrays=[],
        resident_scope='32MiB binary copy batches; one stock for delta extraction; one session for normalization')
    p.atomic_json(output,receipt)
    slow,invariance=slow_invariance();receipt['slow_invariance_receipt_sha256']=p.sha(DEST/'slow_source_invariance_receipt.json')
    patches=patches_from_recalculation(rebuild)
    for name,delta in patches.items():
        receipt['arrays'].append(copy_patched(OLD/'dataset'/(name+'.npy'),dataset/(name+'.npy'),old_meta['files_sha256'][name+'.npy'],delta))
        p.atomic_json(output,receipt)
    for name in ['hour_ohlcv.npy','hour_complete.npy']:
        receipt['arrays'].append(copy_patched(RAW/'dataset'/name,dataset/name,raw_meta['files_sha256'][name],{}));p.atomic_json(output,receipt)
    for name in ['members.npy','industry.npy','earliest_execution_times.npy','label_end_times.npy','times.npy']:
        receipt['arrays'].append(copy_patched(OLD/'dataset'/name,dataset/name,old_meta['files_sha256'][name],{}));p.atomic_json(output,receipt)
    raw=(RAW/'dataset/symbols.json').read_bytes();assert p.hashlib.sha256(raw).hexdigest()==old_meta['files_sha256']['symbols.json']
    (dataset/'symbols.json').write_bytes(raw)
    receipt['arrays'].append(dict(path=str(dataset/'symbols.json'),sha256=p.hashlib.sha256(raw).hexdigest(),verified_identity=p.identity(dataset/'symbols.json')))
    names=normalize_stream(dataset,old_meta,slow,invariance,receipt,output)
    p.atomic_json(dataset/'feature_names.json',names)
    receipt['arrays'].append(dict(path=str(dataset/'feature_names.json'),sha256=p.sha(dataset/'feature_names.json'),verified_identity=p.identity(dataset/'feature_names.json')))
    files={p.Path(r['path']).name:r['sha256'] for r in receipt['arrays']}
    metadata={**old_meta,'schema_version':3,'candidate_version':'native_actual_quantity_v25_complete_model_inputs',
        'production_ready':False,'research_candidate':True,'model_input_complete':True,
        'causality_audit':dict(passed=False,receipt='dataset_audit.json',reason='Independent full assembled-tensor audit pending; research-source live clock limitations retained.'),
        'files_sha256':files,'data_fingerprint':p.hashlib.sha256(p.json.dumps(files,sort_keys=True).encode()).hexdigest(),
        'source_native_minute_contract':p.json.loads((p.OUT/'actual_interval_quantity_v25_staging/receipt.json').read_text())['definition'],'source_observed_second_minute_contract':p.json.loads((p.OUT/'first_hour_observed_second_close_v22_staging/receipt.json').read_text())['definition'],'admitted_raw_hours_this_resume':raw_meta['admitted_raw_hours'],'unknown_native_receiver_time_hours':raw_meta['unknown_local_arrival_hours'],'historical_reported_execution_proxy_policy_explicit':True,'unknown_native_tick_receiver_execution_windows_kept_unavailable':True,
        'historical_minimum_missing_hours':raw_meta['historical_minimum_missing_hours'],'historical_admitted_raw_hours':raw_meta['historical_admitted_raw_hours'],'tail_admitted_raw_hours':raw_meta['tail_admitted_raw_hours'],'tail_missing_hours':raw_meta['tail_missing_hours'],'original_failed_quote_clock_and_coverage_gates_retained':True,
        'slow_raw_values_recomputed':False,'slow_source_invariance_receipt':str(DEST/'slow_source_invariance_receipt.json'),
        'all122_normalized_features_recomputed':True,'source_availability_live_clock_certificate':False,
        'source_hour_availability_policy':'Captured event/reception times are preserved and checked against original execution times; nominal exact signal-clock/live qualification is not certified.',
        'new_execution_window_policy':'Native ticks retain observed receiver dependencies and fixedwindow end constraints. Historical reported bars use the original retrospective five observed minute opens and positive reported five-minute volume; receiver times stay unknown and no live certificate is asserted.',
        'complete_model_input_independent_audit_pending':True,'new_training_or_backtest_run':False}
    p.atomic_json(dataset/'meta.json',metadata)
    receipt.update(status='full122_model_inputs_assembled_pending_independent_tensor_audit',finished_utc=p.utc(),model_input_complete=True,
        normalized_feature_shape=[8476,1178,122],new_dataset_meta_sha256=p.sha(dataset/'meta.json'),
        feature_order_equal_frozen=True,source_slow_values_recomputed=False,all122_normalized_features_recomputed=True,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':
    try:main()
    except Exception:
        p.atomic_json(DEST/'model_input_assembly_failure.json',dict(checked_utc=p.utc(),traceback=p.traceback.format_exc(),source_code_sha256=p.sha(__file__)))
        raise
