"""Independently audit every raw cell, with at most 256 hour rows resident.

Hashes are calculated during the necessary comparison pass, not in separate
whole-array scans. Derived tensors deliberately remain absent at this stage.
"""
from decimal import Decimal
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p

PARENT=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
DEST=p.BASE/'backfilled_panel_candidate_native_event_v7_raw'


def header(handle):
    version=p.np.lib.format.read_magic(handle)
    reader={(1,0):p.np.lib.format.read_array_header_1_0,
            (2,0):p.np.lib.format.read_array_header_2_0}[version]
    shape,fortran,dtype=reader(handle)
    assert not fortran
    offset=handle.tell()
    handle.seek(0)
    prefix=handle.read(offset)
    return shape,dtype,prefix


def main():
    lock=p.locked('verify_native_raw_full_history')
    merge_path=DEST/'raw_panel_merge_receipt.json'
    merge=p.json.loads(merge_path.read_text())
    meta_path=DEST/'dataset/meta.json'
    meta=p.json.loads(meta_path.read_text())
    parent_meta=p.json.loads((PARENT/'meta.json').read_text())
    assert p.sha(meta_path)==merge['new_raw_meta_sha256']
    assert p.sha(PARENT/'meta.json')==merge['parent_meta_sha256']
    baseline_path=p.OUT/'full_history_audit.json'
    baseline=p.json.loads(baseline_path.read_text())
    assert baseline['passed'] and baseline['stocks_checked']==1178
    assert baseline['candidate_meta_sha256']==merge['parent_meta_sha256']
    assert p.sha(baseline_path)==merge['parent_full_history_audit_sha256']
    stage_path=p.OUT/'new_candidate_v7_staging/receipt.json'
    stage=p.json.loads(stage_path.read_text())
    proof_path=stage_path.parent/'independent_verification.json'
    proof=p.json.loads(proof_path.read_text())
    assert p.sha(proof_path)==merge['independently_verified_minute_staging_sha256']
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    symbols=p.json.loads((DEST/'dataset/symbols.json').read_text())
    assert symbols==calendar['union_symbols'] and len(symbols)==1178
    assert sorted(q.name for q in (DEST/'dataset').iterdir())==sorted(['hour_ohlcv.npy','hour_complete.npy','times.npy','symbols.json','meta.json'])
    for name in ['times.npy','symbols.json']:
        assert p.sha(DEST/'dataset'/name)==parent_meta['files_sha256'][name]==meta['files_sha256'][name]
    times=p.np.load(DEST/'dataset/times.npy')
    execution=p.np.load(PARENT/'earliest_execution_times.npy')
    expected={}
    stock_proofs=[]
    stage_records={r['symbol']:r for r in stage['records']}
    for record in merge['records']:
        symbol=record['symbol']
        source=p.Path(record['primary_factor_source_path'])
        assert p.sha(source)==record['primary_factor_source_sha256']
        primary=p.json.loads(source.read_text())
        events=[dict(zip(primary['factor_fields'],r)) for r in primary['factors']]
        observed=[r for r in events if r['dividOperateDate']<='2026-09-23']
        latest=max(observed,key=lambda r:r['dividOperateDate']) if observed else None
        # The parent uses 1 only for an entirely empty observed factor history.
        assert latest is not None or not events
        factor=float(latest['foreAdjustFactor']) if latest else 1.0
        assert latest==record['latest_effective_factor_event']
        sr=stage_records[symbol]
        minute_path=p.Path(sr['minute_path'])
        assert p.identity(minute_path)==sr['verified_identity'] and p.sha(minute_path)==sr['minute_sha256']
        minutes=p.pq.ParquetFile(minute_path).read().to_pylist()
        assert len(minutes)==240
        day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]=='2026-09-23')
        assert day['tradestatus']=='1' and int(day['volume'])>0
        native_day=[minutes[0]['open'],max(r['high'] for r in minutes),min(r['low'] for r in minutes),minutes[-1]['close'],sum(r['volume'] for r in minutes),sum(r['amount_x10000'] for r in minutes)]
        assert native_day==[int(Decimal(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(Decimal(day['amount'])*10000)]
        for hour in record['hours']:
            slot=hour['slot']
            block=minutes[slot*60:(slot+1)*60]
            prices=[block[0]['open'],max(r['high'] for r in block),min(r['low'] for r in block),block[-1]['close']]
            volume=sum(r['volume'] for r in block)
            assert prices==[hour['exact_raw_price_x10000'][f] for f in ['open','high','low','close']]
            assert volume==hour['exact_native_volume_shares']
            assert sum(r['amount_x10000'] for r in block)==hour['exact_native_calculated_amount_x10000']
            assert factor==hour['adjustment_factor']
            raw=[float(Decimal(v)/10000) for v in prices]
            values=p.np.array([v*factor for v in raw]+[volume]+raw+[factor],dtype=p.np.float32)
            assert values.tolist()==hour['canonical_float32_values']
            row,column=hour['row_index'],hour['column_index']
            assert column==symbols.index(symbol) and row==calendar['dates'].index('2026-09-23')*4+slot
            assert (row,column) not in expected
            expected[row,column]=values
            ready=max(r['source_dependency_ready_ms'] for r in block)
            midnight=int(p.datetime.fromisoformat('2026-09-23').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())*1000000000
            assert ready==hour['source_ready_ms']
            assert midnight+ready*1000000<int(execution[row])==hour['existing_earliest_execution_utc_ns']
        stock_proofs.append(dict(symbol=symbol,primary_traded_day_six_fields_exact=True,factor_exact=True,added_hours=4))
    assert len(expected)==88 and len(stock_proofs)==22
    identities={p.Path(r['path']).name:r['verified_identity'] for r in merge['arrays']}
    counts=p.np.zeros(1178,dtype=p.np.int64)
    old_counts=p.np.zeros(1178,dtype=p.np.int64)
    new_window=p.np.zeros(1178,dtype=p.np.int64)
    consecutive=p.np.zeros(1178,dtype=p.np.int32)
    audited=[]
    for name in ['hour_ohlcv.npy','hour_complete.npy']:
        source=PARENT/name
        target=DEST/'dataset'/name
        old_identity=p.identity(source)
        assert p.identity(target)==identities[name]
        old_digest=p.hashlib.sha256()
        new_digest=p.hashlib.sha256()
        differences=0
        with source.open('rb') as old_file,target.open('rb') as new_file:
            shape,dtype,old_header=header(old_file)
            shape2,dtype2,new_header=header(new_file)
            assert shape==shape2 and dtype==dtype2 and old_header==new_header
            assert shape[:2]==(8476,1178)
            old_digest.update(old_header)
            new_digest.update(new_header)
            row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize
            for start in range(0,shape[0],256):
                length=min(256,shape[0]-start)
                raw_old=old_file.read(length*row_bytes)
                raw_new=new_file.read(length*row_bytes)
                assert len(raw_old)==len(raw_new)==length*row_bytes
                old_digest.update(raw_old)
                new_digest.update(raw_new)
                a=p.np.frombuffer(raw_old,dtype=dtype).reshape((length,*shape[1:]))
                b=p.np.frombuffer(raw_new,dtype=dtype).reshape((length,*shape[1:]))
                equal=(a==b)
                if name=='hour_ohlcv.npy':
                    equal|=p.np.isnan(a)&p.np.isnan(b)
                    changed=~equal.all(axis=2)
                else:
                    changed=~equal
                    old_counts+=a.sum(axis=0)
                    counts+=b.sum(axis=0)
                    for observed_hour in b:
                        consecutive=p.np.where(observed_hour,consecutive+1,0)
                        new_window+=consecutive>=80
                actual_changes={(start+int(r),int(c)) for r,c in p.np.argwhere(changed)}
                required={coord for coord in expected if start<=coord[0]<start+length}
                assert actual_changes==required,(name,start,'unexpected changed cells')
                for row,col in actual_changes:
                    if name=='hour_ohlcv.npy':
                        assert p.np.isnan(a[row-start,col]).all()
                        assert p.np.array_equal(b[row-start,col],expected[row,col])
                    else:
                        assert not a[row-start,col] and b[row-start,col]
                differences+=len(actual_changes)
                del a,b,equal,changed,raw_old,raw_new
            assert old_file.read(1)==new_file.read(1)==b''
        assert p.identity(source)==old_identity and p.identity(target)==identities[name]
        assert old_digest.hexdigest()==parent_meta['files_sha256'][name]
        assert new_digest.hexdigest()==meta['files_sha256'][name]
        audited.append(dict(name=name,stock_hours_checked=8476*1178,changed_stock_hours=differences,sha256=new_digest.hexdigest(),verified_identity=identities[name]))
    assert int(counts.sum()-old_counts.sum())==88
    baseline_stocks={r['symbol']:r for r in baseline['stocks']}
    stocks=[]
    for i,symbol in enumerate(symbols):
        previous=baseline_stocks[symbol]
        assert old_counts[i]==previous['complete_hours']
        added=int(counts[i]-old_counts[i])
        assert added==(4 if symbol in stage_records else 0)
        assert previous['complete_hours_on_primary_suspended_days']==0
        stocks.append(dict(symbol=symbol,complete_hours=int(counts[i]),new_complete80_hours=int(new_window[i]),added_hours=added,primary_suspension_hours_promoted=0))
    result=dict(passed=True,status='full_1178_raw_history_audit_passed_derived_model_inputs_still_absent',checked_utc=p.utc(),
        source_code_sha256=p.sha(__file__),audited_merge_receipt_sha256=p.sha(merge_path),audited_raw_meta_sha256=p.sha(meta_path),
        inherited_parent_full_history_audit_sha256=p.sha(baseline_path),stocks_checked=1178,hour_rows_per_stock=8476,
        all_raw_stock_hours_checked=8476*1178,added_raw_stockdays=22,added_raw_hours=88,unchanged_stock_histories=1156,
        primary_suspension_hours_promoted=0,unexpected_cell_changes=0,arrays=audited,stocks=stocks,new_stockday_source_checks=stock_proofs,
        all_88_stored_source_dependencies_before_unchanged_execution=True,features_labels_execution_prices_absent=True,
        previous_source_clock_and_coverage_failures_not_waived=True,model_input_complete=False,production_ready=False,
        resident_scope='256 hour rows per raw-array comparison; one staged stockday at a time',
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(DEST/'raw_full_history_audit.json',result)
    lock.close()


if __name__=='__main__':
    main()
