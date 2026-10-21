"""Stage cached historical OHLCV under the pinned original donor contract.

Retain reported integer-lot quantities and original amount strings. No trade
records, share recovery, receiver clocks, or minute prices are manufactured.
"""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .recheck_historical_quote_candidates import normalize_rows, GRID

DEST=p.OUT/'historical_reported_v13_staging'
RAW=p.BASE/'backfilled_panel_candidate_native_quote_v12_raw'


def main():
    lock=p.locked('stage_historical_reported_minutes');DEST.mkdir(exist_ok=True)
    output=DEST/'receipt.json';assert not output.exists()
    contract_path=p.OUT/'historical_contract_reconciliation/receipt.json';contract=p.json.loads(contract_path.read_text())
    assert contract['next_batch_hours']==352 and not contract['original_numeric_clock_factor_coverage_gates_waived']
    directory=p.OUT/'historical_quote_candidate_recheck';source_path=directory/'receipt.json';source=p.json.loads(source_path.read_text())
    proof_path=directory/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source_path)==p.json.loads((p.OUT/'historical_cash_precision/original_donor_gate_comparison.json').read_text())['source_receipt_sha256']
    assert p.sha(proof_path)==contract['independent_historical_proof_sha256']
    donor_path=p.Path(contract['original_donor_source_path']);assert p.sha(donor_path)==contract['original_donor_source_sha256']
    assert p.sha(contract['plan_source_path'])==contract['plan_source_sha256']
    raw_audit_path=RAW/'raw_full_history_audit.json';raw_audit=p.json.loads(raw_audit_path.read_text())
    assert raw_audit['passed'] and raw_audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    results={(r['symbol'],r['date']):r for r in source['results']}
    files={r['path']:r for r in source['native_files']}
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='staging_historical_observed_reported_minutes',
        original_contract_receipt_sha256=p.sha(contract_path),historical_source_receipt_sha256=p.sha(source_path),historical_source_independent_proof_sha256=p.sha(proof_path),
        parent_raw_full_history_audit_sha256=p.sha(raw_audit_path),records=[],quarantined_stockdays=[],
        definition='Observed native end-labelled240 regular OHLCV bars; positive observed09:30 auction joins09:31 then end labels shift left1minute. Reported integer lots convert by100 to reported shares; no exact one-share quantity claim. Original primary dailyO/C,quantity<=100shares,pricebounds,datedfactor andpositive tradedhour gates retained; all48 independent5mOHLCexact andquantitydifference<=100shares.',
        quantity_reporting_precision='integer lots, normalized reported volume=lots*100; true one-share minute quantity unknown',
        amount_reporting_precision='original reported CNY strings retained as provenance; not a minute feature and not used to reconstruct shares',
        receiver_clock_certificate=False,source_live_clock_certificate=False,candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,
        retrospective_execution_proxy_policy='Original observed-bar five-minute open mean and reported positive five-minute volume; retained receiver clocks are unknown, no live latency qualification. This policy is separate from native tick LocalTime qualification.',
        original_hour_aggregation_source_path=str(donor_path),original_hour_aggregation_source_sha256=p.sha(donor_path))
    counts=Counter()
    for request in contract['records']:
        symbol,date=request['symbol'],request['date'];key=symbol,date;prior=results[key]
        if request['additional_cash_bound_diagnostic_failed_minutes']:
            receipt['quarantined_stockdays'].append(dict(symbol=symbol,date=date,hours=4,
                reason='reported minute amount incompatible with minute price range and volume even allowing100share uncertainty; retain pending independent minute definition/source evidence',
                failed_minute_indices=request['additional_cash_bound_diagnostic_failed_minutes']))
            counts['unresolved_minute_price_quantity_amount_conflict_hours']+=4;continue
        leaf=p.Path(prior['native_selected_path']);data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==prior['native_selected_sha256']
        selected=p.json.loads(data);native_file=files[selected['native_source_path']]
        assert selected['native_source_sha256']==native_file['sha256'] and p.identity(native_file['path'])==native_file['source_identity']
        assert selected['symbol']==symbol and selected['date']==date
        for row in selected['raw_selected_rows']:
            stamp=p.datetime.fromisoformat(row['k线结束时间']);minute=stamp.hour*60+stamp.minute
            assert stamp.date().isoformat()==date and not stamp.second and not stamp.microsecond
            assert minute in GRID+[570,780] and (minute!=780 or D(row['成交量'])==0)
            expected_code='sz300114' if symbol=='302132.SZ' and date<'2025-02-17' else symbol[-2:].lower()+symbol[:6]
            assert row['股票代码']==expected_code and D(row['成交量'])==D(row['成交量']).to_integral_value()
        bars=normalize_rows(selected['raw_selected_rows'])
        assert [{f:str(v) for f,v in b.items()} for b in bars]==selected['normalized_bars']
        primary_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(primary_path.read_text())
        day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
        assert day['tradestatus']=='1' and bars[0]['open']==D(day['open']) and bars[-1]['close']==D(day['close'])
        assert max(b['high'] for b in bars)<=D(day['high']) and min(b['low'] for b in bars)>=D(day['low'])
        assert abs(sum(b['volume'] for b in bars)-D(day['volume']))<=100 and len(bars)==240
        assert prior['strict_coarse_pass_slots']==[0,1,2,3]
        minutes=[];hours=[]
        for end_minute,b in zip(GRID,bars):
            assert b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']
            minute=dict(symbol=symbol,date=date,left_minute=end_minute-1,reported_end_minute=end_minute,
                **{f:int(b[f]*10000) for f in ['open','high','low','close']},volume=int(b['volume']),
                amount_x10000=int(b['amount']*10000),source_reported_amount_CNY=str(b['amount']),
                source_reported_volume_lots=str(b['volume']/100),source_kind='observed_vendor_end_labelled_ohlcv_integer_lots',
                source_dependency_ready_ms=None,source_last_event_ms=None,true_share_quantity_recovered=False)
            minutes.append(minute)
        for slot in range(4):
            assert not mask[calendar['dates'].index(date)*4+slot,calendar['union_symbols'].index(symbol)]
            block=minutes[slot*60:(slot+1)*60]
            h=dict(slot=slot,date=date,open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],
                volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),
                source_ready_ms=None,source_event_ms_max=None,source_quantity_precision='reported_integer_lots_scaled100',
                execution_proxy_policy='original_retrospective_observed_minute_open_mean',actual_receiver_time_certified=False)
            assert h['volume']>0;hours.append(h)
        folder=DEST/('symbol='+symbol);folder.mkdir(exist_ok=True);output_leaf=folder/(date+'_native_candidate_minutes.parquet')
        p.pq.write_table(p.pa.Table.from_pylist(minutes),output_leaf,compression='zstd')
        receipt['records'].append(dict(symbol=symbol,date=date,hours=hours,minute_path=str(output_leaf),minute_sha256=p.sha(output_leaf),verified_identity=p.identity(output_leaf),
            primary_source_path=str(primary_path),primary_source_sha256=p.sha(primary_path),selected_native_source_path=str(leaf),selected_native_source_sha256=prior['native_selected_sha256'],
            original_csv_source_path=native_file['path'],original_csv_source_sha256=native_file['sha256'],original_csv_identity=native_file['source_identity'],
            coarse_reference_path=prior['reference_path'],coarse_reference_sha256=prior['reference_sha256'],
            primary_daily_open_close_exact=True,primary_daily_volume_difference_shares=str(sum(b['volume'] for b in bars)-D(day['volume'])),
            primary_daily_amount_difference_CNY=str(sum(b['amount'] for b in bars)-D(day['amount'])),
            all48_coarse_price_exact_and_reported_volume_within100=True))
        receipt.update(updated_utc=p.utc(),stockdays_staged=len(receipt['records']));p.atomic_json(output,receipt)
    hours=sum(len(r['hours']) for r in receipt['records']);assert hours+sum(counts.values())==352
    receipt.update(status='staged_pending_independent_audit',finished_utc=p.utc(),hours_staged=hours,stockdays_staged=len(receipt['records']),
        minutes_staged=hours*60,quarantined_hours=sum(counts.values()),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
