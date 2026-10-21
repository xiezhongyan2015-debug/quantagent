"""Full independent tensor audit, with64 rows or one cross-section resident."""
from . import bounded_tail_pipeline as p
from .verify_native_raw_full_history import header
from .audit_canonical_ipo_v6 import normalize as independent_normalize
from gru_research.minute_data import HOUR_FEATURES

OLD=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6'
RAW=p.BASE/'backfilled_panel_candidate_native_event_v8_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_event_v8_derived'


def reference_changes(rebuild):
    fields={'hour_features.npy':HOUR_FEATURES,'labels.npy':['y1','y2','y3','y4'],
        'execution_prices.npy':['execution_price'],'execution_raw_prices.npy':['execution_raw_price'],
        'execution_complete.npy':['execution_window_complete'],'execution_tradable.npy':['execution_tradable'],'execution_all_flat.npy':['execution_all_flat']}
    result={name:{} for name in fields}
    for col,record in enumerate(rebuild['records']):
        if not record['raw_hours_added']:continue
        target=p.Path(record['new_hour_path']);source=p.Path(record['source_hour_path'])
        assert p.identity(target)==record['verified_identity'] and p.identity(source)==record['source_identity']
        cols=['signal_time',*[f for v in fields.values() for f in v]]
        actual=p.pq.ParquetFile(target).read(columns=cols).to_pandas()
        previous=p.pq.ParquetFile(source).read(columns=cols).to_pandas()
        assert p.np.array_equal(actual.signal_time.to_numpy(),previous.signal_time.to_numpy())
        for name,columns in fields.items():
            dtype=p.np.bool_ if name in ['execution_complete.npy','execution_tradable.npy','execution_all_flat.npy'] else p.np.float32
            a=previous[columns].to_numpy(dtype);b=actual[columns].to_numpy(dtype)
            equal=(a==b)
            if dtype==p.np.float32:equal|=p.np.isnan(a)&p.np.isnan(b)
            for row in p.np.flatnonzero(~equal.all(axis=1)):
                assert row>=8467
                result[name][int(row),col]=b[row] if len(columns)>1 else b[row,0]
    return result


def compare_arrays(assembly,changes,oldmeta,rawmeta):
    audited=[]
    for record in assembly['arrays']:
        target=p.Path(record['path']);name=target.name
        if target.suffix!='.npy' or name=='X.npy':continue
        assert p.identity(target)==record['verified_identity']
        source=RAW/'dataset'/name if name in ['hour_ohlcv.npy','hour_complete.npy'] else OLD/'dataset'/name
        expected_source=(rawmeta if source.parent.parent==RAW else oldmeta)['files_sha256'][name]
        before=p.identity(source);source_hash=p.hashlib.sha256();target_hash=p.hashlib.sha256()
        modified=0
        with source.open('rb') as baseline,target.open('rb') as candidate:
            shape,dtype,prefix=header(baseline);shape2,dtype2,prefix2=header(candidate)
            assert shape==shape2 and dtype==dtype2 and prefix==prefix2
            source_hash.update(prefix);target_hash.update(prefix2)
            row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize
            delta=changes.get(name,{})
            for start in range(0,shape[0],64):
                length=min(64,shape[0]-start)
                a_raw=baseline.read(length*row_bytes);b_raw=candidate.read(length*row_bytes)
                assert len(a_raw)==len(b_raw)==length*row_bytes
                source_hash.update(a_raw);target_hash.update(b_raw)
                a=p.np.frombuffer(a_raw,dtype).reshape((length,*shape[1:]));b=p.np.frombuffer(b_raw,dtype).reshape((length,*shape[1:]))
                equal=a==b
                if p.np.issubdtype(dtype,p.np.floating):equal|=p.np.isnan(a)&p.np.isnan(b)
                if name in changes:
                    bad=~equal.all(axis=2) if len(shape)==3 else ~equal
                    coordinates={(start+int(r),int(c)) for r,c in p.np.argwhere(bad)}
                    required={coord for coord in delta if start<=coord[0]<start+length}
                    assert coordinates==required,(name,start,'unexpected dense tensor changes')
                    for row,column in coordinates:
                        assert p.np.array_equal(b[row-start,column],delta[row,column],equal_nan=True)
                    modified+=len(coordinates)
                else:assert equal.all(),(name,start,'source-preserved tensor differs')
                del a,b,equal,a_raw,b_raw
            assert baseline.read(1)==candidate.read(1)==b''
        assert p.identity(source)==before and p.identity(target)==record['verified_identity']
        assert source_hash.hexdigest()==expected_source and target_hash.hexdigest()==record['sha256']
        audited.append(dict(name=name,sha256=target_hash.hexdigest(),verified_identity=record['verified_identity'],shape=list(shape),dtype=str(dtype),modified_stock_hours=modified))
    return audited


def verify_dense_labels(dataset,members,windows,calendar):
    entry=p.np.load(dataset/'execution_prices.npy')
    complete=p.np.load(dataset/'execution_complete.npy')
    tradable=p.np.load(dataset/'execution_tradable.npy')
    assert p.np.array_equal(complete,tradable)
    assert p.np.isfinite(entry[complete]).all() and (entry[complete]>0).all()
    assert p.np.isnan(entry[~complete]).all()
    times=p.np.load(dataset/'times.npy');earliest=p.np.load(dataset/'earliest_execution_times.npy');ends=p.np.load(dataset/'label_end_times.npy')
    assert (earliest>times).all()
    assert times.shape==earliest.shape==(8476,) and ends.shape==(8476,4)
    coverage={};finite=0
    with (dataset/'labels.npy').open('rb') as handle:
        shape,dtype,_=header(handle);assert shape==(8476,1178,4) and dtype==p.np.dtype('float32')
        for start in range(0,8476,128):
            length=min(128,8476-start);raw=handle.read(length*1178*4*4)
            assert len(raw)==length*1178*4*4
            y=p.np.frombuffer(raw,p.np.float32).reshape(length,1178,4)
            for horizon in range(1,5):
                count=min(length,8476-start-horizon)
                if count>0:
                    expected=p.np.log(entry[start+horizon:start+horizon+count].astype(float)/entry[start:start+count].astype(float))
                    actual=y[:count,:,horizon-1]
                    assert p.np.array_equal(p.np.isfinite(expected),p.np.isfinite(actual))
                    assert p.np.allclose(expected,actual,atol=2e-6,rtol=2e-5,equal_nan=True)
                    valid=ends[start:start+count,horizon-1]>=0
                    assert p.np.array_equal(ends[start:start+count,horizon-1][valid],(earliest[start+horizon:start+horizon+count]+5*60*10**9)[valid])
                    finite+=int(p.np.isfinite(actual).sum())
                assert p.np.isnan(y[max(count,0):,:,horizon-1]).all()
            for local in range(length):
                row=start+local;year=calendar['dates'][row//4][:4]
                c=coverage.setdefault(year,dict(pool_stock_hours=0,complete_80_hours=0,all_4_labels_eligible_stock_hours=0))
                c['pool_stock_hours']+=int(members[row].sum())
                c['complete_80_hours']+=int((members[row]&windows[row]).sum())
                c['all_4_labels_eligible_stock_hours']+=int((members[row]&windows[row]&p.np.isfinite(y[local]).all(axis=1)).sum())
            del y,raw
        assert handle.read(1)==b''
    for c in coverage.values():c['complete_80_hour_coverage']=c['complete_80_hours']/c['pool_stock_hours']
    return coverage,finite


def verify_all_normalization(dataset,assembly,oldmeta,invariance):
    paths=[dataset/'X.npy',dataset/'hour_features.npy',dataset/'members.npy',p.Path(invariance['slow_raw_path']),OLD/'dataset/X.npy']
    before={str(path):p.identity(path) for path in paths};hashes={str(path):p.hashlib.sha256() for path in paths}
    assert before[str(paths[3])]==invariance['slow_raw_identity']
    expected_shapes=[(8476,1178,122),(8476,1178,24),(8476,1178),(2119,1178,98),(8476,1178,122)]
    handles=[path.open('rb') for path in paths]
    try:
        for path,handle,expected in zip(paths,handles,expected_shapes):
            shape,dtype,prefix=header(handle);assert shape==expected
            hashes[str(path)].update(prefix)
        max_error=0.;changed_cells=0;changed_member_cells=0
        member_finite=p.np.zeros(122,p.np.int64);member_count=0
        for day in range(2119):
            sizes=[4*1178*122*4,4*1178*24*4,4*1178,1178*98*4,4*1178*122*4]
            parts=[handle.read(size) for handle,size in zip(handles,sizes)]
            assert all(len(raw)==size for raw,size in zip(parts,sizes))
            for path,raw in zip(paths,parts):hashes[str(path)].update(raw)
            x=p.np.frombuffer(parts[0],p.np.float32).reshape(4,1178,122)
            h=p.np.frombuffer(parts[1],p.np.float32).reshape(4,1178,24)
            m=p.np.frombuffer(parts[2],p.np.bool_).reshape(4,1178)
            s=p.np.frombuffer(parts[3],p.np.float32).reshape(1178,98)
            old=p.np.frombuffer(parts[4],p.np.float32).reshape(4,1178,122)
            assert (m==m[0]).all()
            normalized_slow=independent_normalize(s,m[0])
            for slot in range(4):
                reference=p.np.concatenate([independent_normalize(h[slot],m[slot]),normalized_slow],axis=1)
                assert p.np.array_equal(p.np.isnan(x[slot]),p.np.isnan(reference))
                assert p.np.allclose(x[slot],reference,rtol=2e-6,atol=2e-6,equal_nan=True)
                use=p.np.isfinite(reference)
                if use.any():max_error=max(max_error,float(p.np.max(p.np.abs(x[slot][use]-reference[use]))))
                member_finite+=p.np.isfinite(x[slot,m[slot]]).sum(axis=0);member_count+=int(m[slot].sum())
            if day<2118:assert p.np.array_equal(x,old,equal_nan=True),'New tail overlay changed past normalized model inputs'
            equal=(x==old)|(p.np.isnan(x)&p.np.isnan(old))
            changed_cells+=int((~equal).sum());changed_member_cells+=int((~equal)[m].sum())
            if (day+1)%50==0 or day+1==2119:
                p.atomic_json(DEST/'model_input_audit_progress.json',dict(status='independent_full122_normalization_audit',sessions_checked=day+1,
                    checked_utc=p.utc(),normalization_max_abs_error=max_error,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
            del x,h,m,s,old,parts,equal
        assert all(handle.read(1)==b'' for handle in handles)
    finally:
        for handle in handles:handle.close()
    files={p.Path(r['path']).name:r['sha256'] for r in assembly['arrays']}
    for path in paths:assert p.identity(path)==before[str(path)]
    for path in paths[:3]:assert hashes[str(path)].hexdigest()==files[path.name]
    assert hashes[str(paths[3])].hexdigest()==invariance['expected_slow_raw_sha256']
    assert hashes[str(paths[4])].hexdigest()==oldmeta['files_sha256']['X.npy']
    coverage={name:float(n/member_count) for name,n in zip(oldmeta['features'],member_finite)}
    assert coverage==assembly['member_feature_finite_coverage']
    return dict(full_hour_crosssections_checked=8476,normalized_feature_cells_checked=8476*1178*122,
        normalization_max_abs_error=max_error,changed_normalized_cells=changed_cells,changed_member_normalized_cells=changed_member_cells,
        all_normalized_inputs_before_last_session_exactly_preserved=True,member_feature_finite_coverage=coverage,
        X_sha256=hashes[str(paths[0])].hexdigest(),X_verified_identity=before[str(paths[0])])


def main():
    lock=p.locked('verify_native_model_inputs')
    assembly_path=DEST/'model_input_assembly_receipt.json';assembly=p.json.loads(assembly_path.read_text())
    assert assembly['status']=='full122_model_inputs_assembled_pending_independent_tensor_audit'
    assert assembly['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/assemble_native_model_inputs_bounded.py')
    dataset=DEST/'dataset';meta_path=dataset/'meta.json';meta=p.json.loads(meta_path.read_text())
    assert p.sha(meta_path)==assembly['new_dataset_meta_sha256'] and not meta['production_ready']
    initial_meta_sha=p.sha(meta_path)
    oldmeta=p.json.loads((OLD/'dataset/meta.json').read_text());rawmeta=p.json.loads((RAW/'dataset/meta.json').read_text())
    rebuild=p.json.loads((DEST/'hour_feature_label_rebuild_receipt.json').read_text())
    proof_path=DEST/'hour_feature_label_independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and p.sha(proof_path)==assembly['derived_hour_proof_sha256']
    assert proof['audited_rebuild_receipt_sha256']==p.sha(DEST/'hour_feature_label_rebuild_receipt.json')
    invariance_path=DEST/'slow_source_invariance_receipt.json';invariance=p.json.loads(invariance_path.read_text())
    assert invariance['passed'] and p.sha(invariance_path)==assembly['slow_invariance_receipt_sha256']
    for source in [*invariance['primary_sources'],*invariance['frozen_fundamental_sources']]:assert p.identity(source['path'])==source['verified_identity']
    changes=reference_changes(rebuild)
    arrays=compare_arrays(assembly,changes,oldmeta,rawmeta)
    symbols=p.json.loads((dataset/'symbols.json').read_text());calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    assert symbols==calendar['union_symbols'] and len(symbols)==1178
    assert p.json.loads((dataset/'feature_names.json').read_text())==meta['features']==oldmeta['features'] and len(meta['features'])==122
    for name in ['symbols.json','feature_names.json']:assert p.sha(dataset/name)==meta['files_sha256'][name]
    members=p.np.load(dataset/'members.npy');windows=p.np.load(dataset/'window80_complete.npy');complete=p.np.load(dataset/'hour_complete.npy')
    # Derive80 validity one row at a time; source halts remain false and break it.
    run=p.np.zeros(1178,p.np.int32)
    for row in range(8476):
        run=p.np.where(complete[row],run+1,0);assert p.np.array_equal(run>=80,windows[row])
    coverage,label_count=verify_dense_labels(dataset,members,windows,calendar)
    del complete
    normalization=verify_all_normalization(dataset,assembly,oldmeta,invariance)
    old_gates=p.json.loads((OLD/'gap_audit/receipt.json').read_text())['coverage_by_year'];gates=[]
    for original in old_gates:
        year=str(original['year']);current=coverage[year]
        assert current['pool_stock_hours']==original['original_member_stockhour_denominator']
        assert current['complete_80_hours']==original['complete80_stockhours']
        target=original['target'];passed=current['complete_80_hour_coverage']>=target if target is not None else None
        assert passed==original['target_passed']
        gates.append(dict(year=int(year),original_denominator=current['pool_stock_hours'],complete80_stockhours=current['complete_80_hours'],coverage=current['complete_80_hour_coverage'],original_target=target,passed=passed))
    audit=dict(passed=True,status='full1178_complete122_tensor_and_normalization_audit_passed_research_acceptance_pending',checked_utc=p.utc(),
        source_code_sha256=p.sha(__file__),audited_assembly_receipt_sha256=p.sha(assembly_path),creator_initial_meta_sha256=initial_meta_sha,
        raw_full_history_audit_sha256=assembly['raw_full_history_audit_sha256'],derived_hour_proof_sha256=p.sha(proof_path),
        slow_source_invariance_sha256=p.sha(invariance_path),arrays=arrays,normalization=normalization,
        finite_labels_reconstructed_from_execution_tape=label_count,coverage_by_year=coverage,original_coverage_gates=gates,
        failed_coverage_gate_years=[r['year'] for r in gates if r['passed'] is False],all_coverage_gates_passed=all(r['passed'] is not False for r in gates),
        all1178_stock_histories_and_masks_verified=True,historical_member_normalization_only=True,source_bars_invented=False,
        original_live_signal_clock_and_sampling_limitations_not_cleared_by_tensor_audit=True,production_ready=False,
        admitted_raw_hours_this_resume=691,historical_minimum_missing_hours=3042,tail_missing_hours=84169,
        new_training_or_backtest_run=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    audit_path=dataset/'dataset_audit.json';p.atomic_json(audit_path,audit)
    meta.update(complete_model_input_independent_audit_pending=False,complete_model_input_independent_audit_passed=True,
        coverage_by_year=coverage,original_coverage_gate_failures=audit['failed_coverage_gate_years'],
        causality_audit=dict(passed=False,complete_tensor_and_normalization_audit_passed=True,financial_PIT_source_date_audit_passed=True,
            label_maturity_and_execution_tape_reconstruction_passed=True,receipt='dataset_audit.json',
            reason='Source exact nominal signal-clock/live availability qualifications and original failed coverage gates remain; no production or training clearance.'),
        training_admission_ready=False,full_data_backfill_acceptance_complete=False)
    meta['files_sha256']['dataset_audit.json']=p.sha(audit_path)
    meta['data_fingerprint']=p.hashlib.sha256(p.json.dumps(meta['files_sha256'],sort_keys=True).encode()).hexdigest()
    p.atomic_json(meta_path,meta)
    result=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_assembly_receipt_sha256=p.sha(assembly_path),
        final_dataset_meta_sha256=p.sha(meta_path),dataset_audit_sha256=p.sha(audit_path),dataset_fingerprint=meta['data_fingerprint'],
        full122_model_input_assembly_complete=True,full1178_tensor_audit_complete=True,
        all122_normalized_feature_cells_checked=normalization['normalized_feature_cells_checked'],normalization_max_abs_error=normalization['normalization_max_abs_error'],
        changed_normalized_cells=normalization['changed_normalized_cells'],changed_member_normalized_cells=normalization['changed_member_normalized_cells'],
        failed_original_coverage_gate_years=audit['failed_coverage_gate_years'],training_admission_ready=False,production_ready=False,
        full_data_backfill_acceptance_complete=False,new_training_or_backtest_run=False,peak_rss_bytes=audit['peak_rss_bytes'])
    p.atomic_json(DEST/'model_input_independent_verification.json',result);lock.close()


if __name__=='__main__':
    try:main()
    except Exception:
        p.atomic_json(DEST/'model_input_audit_failure.json',dict(checked_utc=p.utc(),traceback=p.traceback.format_exc(),source_code_sha256=p.sha(__file__)))
        raise
