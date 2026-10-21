"""Independently reproduce raw selected strings, grid and coarse references.

Only one stockday's minutes are resident. Reuse the original CSV digest behind
the already accepted identity guard instead of rescanning year-size files.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_historical_reported_minutes_bounded import DEST, RAW


def main():
    lock=p.locked('verify_historical_reported_minutes');stage_path=DEST/'receipt.json';stage=p.json.loads(stage_path.read_text())
    assert stage['status']=='staged_pending_independent_audit' and stage['hours_staged']==348 and stage['quarantined_hours']==4
    assert stage['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_historical_reported_minutes_bounded.py')
    contract_path=p.OUT/'historical_contract_reconciliation/receipt.json';assert p.sha(contract_path)==stage['original_contract_receipt_sha256']
    contract=p.json.loads(contract_path.read_text());assert p.sha(contract['original_donor_source_path'])==contract['original_donor_source_sha256']
    history_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';history=p.json.loads(history_path.read_text())
    assert p.sha(history_path)==stage['historical_source_receipt_sha256']
    history_proof_path=history_path.parent/'independent_verification.json';history_proof=p.json.loads(history_proof_path.read_text())
    assert history_proof['passed'] and p.sha(history_proof_path)==stage['historical_source_independent_proof_sha256'] and history_proof['audited_receipt_sha256']==p.sha(history_path)
    accepted_files={r['path']:r for r in history['native_files']}
    alias_path=p.BASE/'official_old_code_minute_audit_20261009/receipt.json'
    assert p.sha(alias_path)==history['official_alias_receipt_sha256']
    aliases=p.json.loads(alias_path.read_text())
    assert any(r['admitted'] and (r['canonical_symbol'],r['observed_symbol'],r['official_code_change_date'])==('302132.SZ','300114.SZ','2025-02-17') for r in aliases['payloads'])
    raw_proof_path=RAW/'raw_full_history_audit.json';raw_proof=p.json.loads(raw_proof_path.read_text())
    assert raw_proof['passed'] and p.sha(raw_proof_path)==stage['parent_raw_full_history_audit_sha256'] and raw_proof['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    end_grid=list(range(571,691))+list(range(781,901));prices={'open':'开盘价','high':'最高价','low':'最低价','close':'收盘价'}
    results=[];zero_minutes=0;minutes_checked=0;hours_checked=0
    for record in stage['records']:
        symbol,date=record['symbol'],record['date'];leaf=p.Path(record['selected_native_source_path']);data=leaf.read_bytes()
        assert p.hashlib.sha256(data).hexdigest()==record['selected_native_source_sha256'];selected=p.json.loads(data)
        source=accepted_files[record['original_csv_source_path']]
        assert p.identity(source['path'])==source['source_identity']==record['original_csv_identity']
        assert source['sha256']==selected['native_source_sha256']==record['original_csv_source_sha256']
        assert selected['symbol']==symbol and selected['date']==date
        by_end={};auction=None
        for source_row in selected['raw_selected_rows']:
            stamp=p.datetime.fromisoformat(source_row['k线结束时间']);end=stamp.hour*60+stamp.minute
            assert stamp.date().isoformat()==date and stamp.second==stamp.microsecond==0
            code='sz300114' if symbol=='302132.SZ' and date<'2025-02-17' else symbol[-2:].lower()+symbol[:6]
            assert source_row['股票代码']==code
            bar={f:D(source_row[key]) for f,key in prices.items()};bar.update(volume=D(source_row['成交量'])*100,amount=D(source_row['成交额']))
            assert all(v.is_finite() for v in bar.values()) and bar['volume']>=0 and bar['amount']>=0
            assert D(source_row['成交量'])==D(source_row['成交量']).to_integral_value()
            assert all(bar[f]>0 and bar[f]*100==(bar[f]*100).to_integral_value() for f in prices)
            assert bar['low']<=min(bar['open'],bar['close'])<=max(bar['open'],bar['close'])<=bar['high']
            if end in end_grid:assert end not in by_end;by_end[end]=bar
            elif end==570:
                if bar['volume']>0:assert auction is None;auction=bar
            else:assert end==780 and bar['volume']==0
        assert sorted(by_end)==end_grid
        if auction:
            bar=by_end[571];bar['open']=auction['open'];bar['high']=max(bar['high'],auction['high']);bar['low']=min(bar['low'],auction['low'])
            bar['volume']+=auction['volume'];bar['amount']+=auction['amount']
        native=[by_end[m] for m in end_grid]
        assert [{f:str(v) for f,v in b.items()} for b in native]==selected['normalized_bars']
        primary_path=p.Path(record['primary_source_path']);data=primary_path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==record['primary_source_sha256'];primary=p.json.loads(data)
        day=next(dict(zip(primary['fields'],row)) for row in primary['rows'] if row[0]==date)
        assert day['tradestatus']=='1' and D(day['volume'])>0 and native[0]['open']==D(day['open']) and native[-1]['close']==D(day['close'])
        assert max(b['high'] for b in native)<=D(day['high']) and min(b['low'] for b in native)>=D(day['low'])
        vdelta=sum(b['volume'] for b in native)-D(day['volume']);adelta=sum(b['amount'] for b in native)-D(day['amount'])
        assert abs(vdelta)<=100 and str(vdelta)==record['primary_daily_volume_difference_shares'] and str(adelta)==record['primary_daily_amount_difference_CNY']
        wire=p.Path(record['coarse_reference_path']);data=wire.read_bytes();assert p.hashlib.sha256(data).hexdigest()==record['coarse_reference_sha256']
        response=p.json.loads(wire.with_suffix('.json').read_text())
        assert response['frequency']=='5' and response['adjustflag']=='3' and response['start']==response['end']==date
        reference_code='sz.300114' if symbol=='302132.SZ' and date<'2025-02-17' else symbol[-2:].lower()+'.'+symbol[:6]
        assert response['code']==reference_code
        payload=response['data'];payload=payload['record'] if isinstance(payload,dict) else payload
        references=[dict(zip(response['fields'],row)) for row in payload];assert len(references)==48
        for i,ref in enumerate(references):
            assert int(ref['time'][8:10])*60+int(ref['time'][10:12])==end_grid[i*5+4]
            bars=native[i*5:(i+1)*5]
            agg=dict(open=bars[0]['open'],high=max(b['high'] for b in bars),low=min(b['low'] for b in bars),close=bars[-1]['close'],volume=sum(b['volume'] for b in bars))
            assert all(agg[f]==D(ref[f]) for f in prices) and abs(agg['volume']-D(ref['volume']))<=100
        minute_path=p.Path(record['minute_path']);assert p.identity(minute_path)==record['verified_identity']
        data=minute_path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==record['minute_sha256']
        staged=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist();assert len(staged)==240
        for end,expected,row in zip(end_grid,native,staged):
            assert row['symbol']==symbol and row['date']==date and row['left_minute']==end-1 and row['reported_end_minute']==end
            assert all(row[f]==int(expected[f]*10000) for f in prices)
            assert row['volume']==int(expected['volume']) and row['amount_x10000']==int(expected['amount']*10000)
            assert D(row['source_reported_volume_lots'])*100==expected['volume'] and D(row['source_reported_amount_CNY'])==expected['amount']
            assert row['source_last_event_ms'] is None and row['source_dependency_ready_ms'] is None and not row['true_share_quantity_recovered']
            assert row['source_kind']=='observed_vendor_end_labelled_ohlcv_integer_lots'
            zero_minutes+=row['volume']==0;minutes_checked+=1
        for h in record['hours']:
            slot=h['slot'];bars=staged[slot*60:(slot+1)*60]
            assert not mask[calendar['dates'].index(date)*4+slot,calendar['union_symbols'].index(symbol)]
            expected=dict(open=bars[0]['open'],high=max(b['high'] for b in bars),low=min(b['low'] for b in bars),close=bars[-1]['close'],
                volume=sum(b['volume'] for b in bars),amount_x10000=sum(b['amount_x10000'] for b in bars))
            assert all(h[f]==value for f,value in expected.items()) and h['volume']>0
            assert h['source_ready_ms'] is h['source_event_ms_max'] is None and h['execution_proxy_policy']=='original_retrospective_observed_minute_open_mean'
            hours_checked+=1
        results.append(dict(symbol=symbol,date=date,hours=4,raw_strings_grid_auction_quantity_unit_verified=True,all48_independent_coarse_prices_exact=True,original_primary_daily_gate_reproduced=True))
    assert len(results)==87 and minutes_checked==20880 and hours_checked==348
    assert stage['quarantined_stockdays'][0]['symbol']=='302132.SZ' and stage['quarantined_stockdays'][0]['date']=='2023-06-16'
    p.atomic_json(DEST/'independent_verification.json',dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(stage_path),
        stockdays_verified=len(results),hours_verified=hours_checked,minutes_verified=minutes_checked,observed_zero_reported_volume_minutes=zero_minutes,
        original_csv_sources_not_rescanned_or_rehashed=True,reported_integer_lot_precision_preserved=True,true_share_volume_recovered=False,
        receiver_times_not_invented=True,original_retrospective_execution_proxy_policy_preserved=True,live_clock_certificate=False,
        results=results,candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    lock.close()


if __name__=='__main__':main()
