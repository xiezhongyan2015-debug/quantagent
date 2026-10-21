"""Audit all1178 market-minute V10 histories, keeping reception timestamps unknown."""
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
from .verify_native_raw_full_history import header

PARENT=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw'
DEST=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'


def main():
    lock=p.locked('verify_cached_sept21_source_second_raw')
    merge_path=DEST/'raw_panel_merge_receipt.json';merge=p.json.loads(merge_path.read_text())
    meta_path=DEST/'dataset/meta.json';meta=p.json.loads(meta_path.read_text())
    parent_meta_path=PARENT/'dataset/meta.json';parent=p.json.loads(parent_meta_path.read_text())
    assert p.sha(meta_path)==merge['new_raw_meta_sha256'] and p.sha(parent_meta_path)==merge['parent_meta_sha256']
    prior_audit_path=PARENT/'raw_full_history_audit.json';prior=p.json.loads(prior_audit_path.read_text())
    assert prior['passed'] and prior['stocks_checked']==1178 and prior['audited_raw_meta_sha256']==p.sha(parent_meta_path)
    assert p.sha(prior_audit_path)==merge['parent_full_history_audit_sha256']
    stage_path=p.OUT/'cached_sept21_source_second_v26_staging/receipt.json';stage=p.json.loads(stage_path.read_text())
    proof_path=stage_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)==merge['minute_staging_receipt_sha256']
    assert p.sha(proof_path)==merge['independently_verified_minute_staging_sha256']
    stage_records={r['symbol']:r for r in stage['records'] if r['hours']}
    symbols=p.json.loads((DEST/'dataset/symbols.json').read_text())
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    assert symbols==calendar['union_symbols'] and len(symbols)==1178
    assert sorted(q.name for q in (DEST/'dataset').iterdir())==sorted(['hour_ohlcv.npy','hour_complete.npy','times.npy','symbols.json','meta.json'])
    for name in ['times.npy','symbols.json']:
        assert p.sha(DEST/'dataset'/name)==parent['files_sha256'][name]==meta['files_sha256'][name]
    original=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    execution=p.np.load(original/'earliest_execution_times.npy')
    midnight=int(p.datetime.fromisoformat('2026-09-21').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())*1000000000
    expected={}
    for record in merge['records']:
        symbol=record['symbol'];sr=stage_records[symbol]
        primary_path=p.Path(record['primary_factor_source_path'])
        assert p.sha(primary_path)==record['primary_factor_source_sha256']==sr['primary_daily_reference']['sha256']
        primary=p.json.loads(primary_path.read_text())
        events=[dict(zip(primary['factor_fields'],r)) for r in primary['factors']]
        eligible=[r for r in events if r['dividOperateDate']<='2026-09-21']
        latest=max(eligible,key=lambda r:r['dividOperateDate']) if eligible else None
        assert latest is not None or not events
        assert record['date']=='2026-09-21' and sr['date']=='2026-09-21'
        factor=float(latest['foreAdjustFactor']) if latest else 1.0
        if record['special_factor_route'] is not None:
            from .canonical_ipo_v6 import validated_factor_bridge as bridge
            assert bridge.provenance(symbol)==record['special_factor_route']
            factor_frame=p.pq.read_table(bridge.SOURCE/'canonical_observed_fore_factor_events.parquet').to_pandas()
            applicable=factor_frame.loc[factor_frame.symbol.eq(symbol)&factor_frame.effective_date.le('2026-09-21')].sort_values('effective_date')
            factor=float(applicable.iloc[-1].effective_fore_factor)
        path=p.Path(sr['minute_path'])
        assert p.identity(path)==sr['verified_identity'] and p.sha(path)==sr['minute_sha256']
        minutes=p.pq.ParquetFile(path).read().to_pylist();byminute={r['left_minute']:r for r in minutes}
        day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]=='2026-09-21')
        assert day['tradestatus']=='1'
        native_source=p.Path(sr['native_source_path']);native_raw=native_source.read_bytes();assert p.hashlib.sha256(native_raw).hexdigest()==sr['native_source_sha256']
        native_frame=p.pq.ParquetFile(p.pa.BufferReader(native_raw)).read().to_pandas();native_frame=native_frame.loc[native_frame.ticker.eq(symbol[:6])].sort_values('left_minute');native_index=native_frame.set_index('left_minute')
        for r in minutes:
            source_row=native_index.loc[r['left_minute']]
            assert all(int(r[f])==int(source_row[f]) for f in ['open','high','low','close','volume','amount_x10000','trades','first_ts','first_id','last_ts','last_id'])
        assert [int(native_frame.iloc[0].open),int(native_frame.high.max()),int(native_frame.low.min()),int(native_frame.iloc[-1].close),int(native_frame.volume.sum()),int(native_frame.amount_x10000.sum())]==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
        assert sorted(h['slot'] for h in record['hours'])==sorted(h['slot'] for h in sr['hours'])
        for h in record['hours']:
            slot=h['slot'];grid=list(range(570,690))+list(range(780,900))
            block=[byminute[m] for m in grid[slot*60:(slot+1)*60]]
            prices=[block[0]['open'],max(r['high'] for r in block),min(r['low'] for r in block),block[-1]['close']]
            assert prices==[h['exact_raw_price_x10000'][f] for f in ['open','high','low','close']]
            volume=sum(r['volume'] for r in block)
            assert volume==h['exact_native_volume_shares'] and sum(r['amount_x10000'] for r in block)==h['exact_native_calculated_amount_x10000']
            assert factor==h['adjustment_factor']
            raw=[float(D(v)/10000) for v in prices]
            values=p.np.array([v*factor for v in raw]+[volume]+raw+[factor],dtype=p.np.float32)
            assert values.tolist()==h['canonical_float32_values']
            row,column=h['row_index'],h['column_index']
            assert row==calendar['dates'].index('2026-09-21')*4+slot and column==symbols.index(symbol)
            assert (row,column) not in expected;expected[row,column]=values
            reported_second=max(r['last_ts'] for r in block)
            assert reported_second==h['reported_trade_second_max']
            assert midnight+reported_second*1000000000<int(execution[row])==h['existing_earliest_execution_utc_ns']
            assert h['source_local_arrival_ms'] is None and not h['source_arrival_before_execution_certified']
    assert len(expected)==proof['hours_minute_truth_verified']==merge['added_raw_hours']
    identities={p.Path(r['path']).name:r['verified_identity'] for r in merge['arrays']}
    old_counts=p.np.zeros(1178,p.np.int64);counts=old_counts.copy();windows=old_counts.copy();runs=p.np.zeros(1178,p.np.int32)
    files=[]
    for name in ['hour_ohlcv.npy','hour_complete.npy']:
        source=PARENT/'dataset'/name;target=DEST/'dataset'/name
        before=p.identity(source);assert p.identity(target)==identities[name]
        old_hash=p.hashlib.sha256();new_hash=p.hashlib.sha256();differences=0
        with source.open('rb') as old_file,target.open('rb') as new_file:
            shape,dtype,prefix=header(old_file);shape2,dtype2,prefix2=header(new_file)
            assert shape==shape2 and dtype==dtype2 and prefix==prefix2 and shape[:2]==(8476,1178)
            old_hash.update(prefix);new_hash.update(prefix2)
            row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize
            for start in range(0,8476,256):
                length=min(256,8476-start)
                old_raw=old_file.read(length*row_bytes);new_raw=new_file.read(length*row_bytes)
                assert len(old_raw)==len(new_raw)==length*row_bytes
                old_hash.update(old_raw);new_hash.update(new_raw)
                a=p.np.frombuffer(old_raw,dtype=dtype).reshape((length,*shape[1:]));b=p.np.frombuffer(new_raw,dtype=dtype).reshape((length,*shape[1:]))
                equal=a==b
                if name=='hour_ohlcv.npy':
                    equal|=p.np.isnan(a)&p.np.isnan(b);changed=~equal.all(axis=2)
                else:
                    changed=~equal;old_counts+=a.sum(axis=0);counts+=b.sum(axis=0)
                    for complete in b:
                        runs=p.np.where(complete,runs+1,0);windows+=runs>=80
                changed_coordinates={(start+int(r),int(c)) for r,c in p.np.argwhere(changed)}
                required={coord for coord in expected if start<=coord[0]<start+length}
                assert changed_coordinates==required,(name,start,'unexpected raw changes')
                for row,column in changed_coordinates:
                    if name=='hour_ohlcv.npy':assert p.np.isnan(a[row-start,column]).all() and p.np.array_equal(b[row-start,column],expected[row,column])
                    else:assert not a[row-start,column] and b[row-start,column]
                differences+=len(changed_coordinates)
                del a,b,equal,changed,old_raw,new_raw
            assert old_file.read(1)==new_file.read(1)==b''
        assert p.identity(source)==before and p.identity(target)==identities[name]
        assert old_hash.hexdigest()==parent['files_sha256'][name] and new_hash.hexdigest()==meta['files_sha256'][name]
        files.append(dict(name=name,stock_hours_checked=8476*1178,changed_stock_hours=differences,sha256=new_hash.hexdigest(),verified_identity=identities[name]))
    assert int(counts.sum()-old_counts.sum())==len(expected)
    inherited={r['symbol']:r for r in prior['stocks']};stocks=[]
    for column,symbol in enumerate(symbols):
        assert old_counts[column]==inherited[symbol]['complete_hours']
        assert inherited[symbol]['primary_suspension_hours_promoted']==0
        added=int(counts[column]-old_counts[column]);assert added==len(stage_records.get(symbol,{}).get('hours',[]))
        stocks.append(dict(symbol=symbol,complete_hours=int(counts[column]),new_complete80_hours=int(windows[column]),added_hours=added,primary_suspension_hours_promoted=0))
    proof=dict(passed=True,status='full_1178_raw_history_audit_passed_derived_model_inputs_still_absent',checked_utc=p.utc(),
        source_code_sha256=p.sha(__file__),audited_merge_receipt_sha256=p.sha(merge_path),audited_raw_meta_sha256=p.sha(meta_path),
        inherited_parent_full_history_audit_sha256=p.sha(prior_audit_path),stocks_checked=1178,hour_rows_per_stock=8476,
        all_raw_stock_hours_checked=8476*1178,added_raw_hours=len(expected),total_admitted_raw_hours=meta['admitted_raw_hours'],
        stockdays_with_any_admitted_raw_hours=meta['stockdays_with_any_admitted_raw_hours'],stockdays_with_all_four_admitted_raw_hours=meta['stockdays_with_all_four_admitted_raw_hours'],
        primary_suspension_hours_promoted=0,unexpected_cell_changes=0,arrays=files,stocks=stocks,
        inherited_recorded_source_dependency_checks_preserved=True,new_source_arrival_before_execution_certified=False,new_unknown_arrival_hours_preserved=len(expected),source_reported_seconds_before_unchanged_execution=True,features_labels_execution_prices_absent=True,
        previous_source_clock_and_coverage_failures_not_waived=True,model_input_complete=False,production_ready=False,
        resident_scope='256 hour rows per raw-array comparison; one native minute leaf at a time',peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(DEST/'raw_full_history_audit.json',proof);lock.close()


if __name__=='__main__':
    main()
