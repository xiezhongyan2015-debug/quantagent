"""Audit every raw history and historical reported-bar provenance with dated factors."""
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
from .verify_native_raw_full_history import header
from .merge_historical_partial_reported_raw_hours_bounded import PARENT,DEST,STAGE


def main():
    lock=p.locked('verify_historical_partial_reported_raw')
    merge_path=DEST/'raw_panel_merge_receipt.json';merge=p.json.loads(merge_path.read_text())
    assert merge['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/merge_historical_partial_reported_raw_hours_bounded.py')
    meta_path=DEST/'dataset/meta.json';meta=p.json.loads(meta_path.read_text());parent_meta_path=PARENT/'dataset/meta.json';parent=p.json.loads(parent_meta_path.read_text())
    assert p.sha(meta_path)==merge['new_raw_meta_sha256'] and p.sha(parent_meta_path)==merge['parent_meta_sha256']
    prior_path=PARENT/'raw_full_history_audit.json';prior=p.json.loads(prior_path.read_text());assert prior['passed'] and prior['stocks_checked']==1178 and prior['audited_raw_meta_sha256']==p.sha(parent_meta_path)
    assert p.sha(prior_path)==merge['parent_full_history_audit_sha256']
    stage_path=STAGE/'receipt.json';stage=p.json.loads(stage_path.read_text());proof_path=STAGE/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(stage_path)==merge['minute_staging_receipt_sha256'] and p.sha(proof_path)==merge['independently_verified_minute_staging_sha256']
    expected_added=proof['hours_verified'];assert expected_added==stage['hours_staged'] and not merge['reception_evidence_updates']
    symbols=p.json.loads((DEST/'dataset/symbols.json').read_text());calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());assert symbols==calendar['union_symbols'] and len(symbols)==1178
    assert sorted(f.name for f in (DEST/'dataset').iterdir())==sorted(['hour_ohlcv.npy','hour_complete.npy','times.npy','symbols.json','meta.json'])
    for name in ['times.npy','symbols.json']:assert p.sha(DEST/'dataset'/name)==meta['files_sha256'][name]==parent['files_sha256'][name]
    staged={(r['symbol'],r['date']):r for r in stage['records']};new={};updates={};execution=p.np.load(p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset/earliest_execution_times.npy')
    detail_records=[(r['symbol'],h,False) for r in merge['records'] for h in r['hours']]+[(h['symbol'],h,True) for h in merge['reception_evidence_updates']]
    cache={};current=None
    for symbol,h,is_update in sorted(detail_records,key=lambda r:(r[0],r[1]['date'],r[1]['slot'])):
        date=h['date'];sr=staged[symbol,date]
        if (symbol,date)!=current:
            path=p.Path(sr['minute_path']);assert p.identity(path)==sr['verified_identity'];raw=path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==sr['minute_sha256'];cache={b['left_minute']:b for b in p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pylist()}
            primary_path=p.Path(sr['primary_source_path']);raw=primary_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==sr['primary_source_sha256'];primary=p.json.loads(raw)
            events=[dict(zip(primary['factor_fields'],r)) for r in primary['factors']];eligible=[r for r in events if r['dividOperateDate']<=date]
            factor=float(max(eligible,key=lambda r:r['dividOperateDate'])['foreAdjustFactor']) if eligible else 1.0 if not events else None
            from .canonical_ipo_v6 import validated_factor_bridge as bridge
            if bridge.provenance(symbol) is not None:
                frame=p.pq.read_table(bridge.SOURCE/'canonical_observed_fore_factor_events.parquet').to_pandas();selected=frame.loc[frame.symbol.eq(symbol)&frame.effective_date.le(date)].sort_values('effective_date');factor=float(selected.iloc[-1].effective_fore_factor)
            assert factor is not None and p.np.isfinite(factor) and factor>0
            day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date);assert day['tradestatus']=='1'
            current=symbol,date
        grid=list(range(570,690))+list(range(780,900));block=[cache[m] for m in grid[h['slot']*60:(h['slot']+1)*60]]
        assert all(b['volume']>=0 and b['source_kind']=='observed_vendor_end_labelled_ohlcv_integer_lots' for b in block) and sum(b['volume'] for b in block)>0
        exact=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'])
        assert exact==h['exact_raw_price_x10000'] and sum(b['volume'] for b in block)==h['reported_volume_shares'] and sum(b['amount_x10000'] for b in block)==h['reported_amount_x10000']
        assert factor==h['adjustment_factor']
        prices=[float(D(exact[f])/10000) for f in ['open','high','low','close']];values=p.np.asarray([v*factor for v in prices]+[h['reported_volume_shares']]+prices+[factor],p.np.float32)
        assert values.tolist()==h['canonical_float32_values']
        row,column=h['row_index'],h['column_index'];assert row==calendar['dates'].index(date)*4+h['slot'] and column==symbols.index(symbol)
        assert h['minute_source_path']==sr['minute_path'] and h['minute_source_sha256']==sr['minute_sha256']
        assert h['source_ready_ms'] is h['source_event_ms_max'] is None and all(b['source_dependency_ready_ms'] is b['source_last_event_ms'] is None for b in block)
        assert h['existing_earliest_execution_utc_ns']==int(execution[row])
        assert not h['source_arrival_before_execution_certified'] and not h['actual_receiver_time_certified']
        assert h['execution_proxy_policy']=='original_retrospective_observed_minute_open_mean'
        target=updates if is_update else new;assert (row,column) not in target;target[row,column]=values
    assert len(new)==expected_added and not updates
    identities={p.Path(r['path']).name:r['verified_identity'] for r in merge['arrays']};files=[];old_counts=p.np.zeros(1178,p.np.int64);counts=old_counts.copy();windows=old_counts.copy();runs=p.np.zeros(1178,p.np.int32)
    for name in ['hour_ohlcv.npy','hour_complete.npy']:
        source=PARENT/'dataset'/name;target=DEST/'dataset'/name;before=p.identity(source);assert p.identity(target)==identities[name]
        old_hash=p.hashlib.sha256();new_hash=p.hashlib.sha256();changed_total=0
        with source.open('rb') as astream,target.open('rb') as bstream:
            shape,dtype,prefix=header(astream);shape2,dtype2,prefix2=header(bstream);assert shape==shape2 and dtype==dtype2 and prefix==prefix2 and shape[:2]==(8476,1178)
            old_hash.update(prefix);new_hash.update(prefix2);row_bytes=int(p.np.prod(shape[1:]))*dtype.itemsize
            for start in range(0,8476,256):
                length=min(256,8476-start);old=astream.read(length*row_bytes);fresh=bstream.read(length*row_bytes);assert len(old)==len(fresh)==length*row_bytes
                old_hash.update(old);new_hash.update(fresh);a=p.np.frombuffer(old,dtype=dtype).reshape((length,*shape[1:]));b=p.np.frombuffer(fresh,dtype=dtype).reshape((length,*shape[1:]))
                equal=a==b
                if name=='hour_ohlcv.npy':equal|=p.np.isnan(a)&p.np.isnan(b);changed=~equal.all(axis=2)
                else:
                    changed=~equal;old_counts+=a.sum(axis=0);counts+=b.sum(axis=0)
                    for complete in b:runs=p.np.where(complete,runs+1,0);windows+=runs>=80
                actual={(start+int(r),int(c)) for r,c in p.np.argwhere(changed)};required={key for key in new if start<=key[0]<start+length};assert actual==required
                for row,column in required:
                    if name=='hour_ohlcv.npy':assert p.np.isnan(a[row-start,column]).all() and p.np.array_equal(b[row-start,column],new[row,column])
                    else:assert not a[row-start,column] and b[row-start,column]
                for (row,column),values in updates.items():
                    if start<=row<start+length:
                        if name=='hour_ohlcv.npy':assert p.np.array_equal(a[row-start,column],values) and p.np.array_equal(b[row-start,column],values)
                        else:assert a[row-start,column] and b[row-start,column]
                changed_total+=len(actual);del a,b,equal,changed,old,fresh
            assert astream.read(1)==bstream.read(1)==b''
        assert p.identity(source)==before and p.identity(target)==identities[name] and old_hash.hexdigest()==parent['files_sha256'][name] and new_hash.hexdigest()==meta['files_sha256'][name]
        files.append(dict(name=name,sha256=new_hash.hexdigest(),verified_identity=identities[name],stock_hours_checked=8476*1178,changed_stock_hours=changed_total))
    inherited={r['symbol']:r for r in prior['stocks']};added={symbol:sum(len(r['hours']) for r in merge['records'] if r['symbol']==symbol) for symbol in symbols};stocks=[]
    for c,symbol in enumerate(symbols):
        assert old_counts[c]==inherited[symbol]['complete_hours'] and inherited[symbol]['primary_suspension_hours_promoted']==0 and counts[c]-old_counts[c]==added.get(symbol,0)
        stocks.append(dict(symbol=symbol,complete_hours=int(counts[c]),new_complete80_hours=int(windows[c]),added_hours=int(counts[c]-old_counts[c]),primary_suspension_hours_promoted=0))
    assert int(counts.sum()-old_counts.sum())==expected_added and meta['admitted_raw_hours']==parent['admitted_raw_hours']+expected_added and meta['unknown_local_arrival_hours']==parent['unknown_local_arrival_hours']+expected_added
    p.atomic_json(DEST/'raw_full_history_audit.json',dict(passed=True,status='full1178_historical_reported_bar_delta_verified_derived_inputs_absent',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),
        audited_merge_receipt_sha256=p.sha(merge_path),audited_raw_meta_sha256=p.sha(meta_path),inherited_parent_full_history_audit_sha256=p.sha(prior_path),stocks_checked=1178,
        hour_rows_per_stock=8476,all_raw_stock_hours_checked=8476*1178,added_raw_hours=expected_added,total_admitted_raw_hours=meta['admitted_raw_hours'],
        stockdays_with_any_admitted_raw_hours=meta['stockdays_with_any_admitted_raw_hours'],stockdays_with_all_four_admitted_raw_hours=meta['stockdays_with_all_four_admitted_raw_hours'],
        primary_suspension_hours_promoted=0,unexpected_cell_changes=0,arrays=files,stocks=stocks,receiver_times_not_invented=True,reported_quantity_precision_and_retrospective_execution_contract_explicit=True,
        all_previously_admitted_market_values_unchanged=True,source_original_clock_and_coverage_failures_preserved=True,model_input_complete=False,production_ready=False,
        features_labels_execution_prices_absent=True,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
