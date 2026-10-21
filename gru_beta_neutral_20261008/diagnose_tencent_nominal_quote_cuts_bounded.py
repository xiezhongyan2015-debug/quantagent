"""Diagnose explicit nominal-time quote cuts, without changing candle clocks.

The fixed minute truth already has independent all60 OHLCV evidence. This asks
whether a different coarse boundary is an actual observed quote at the declared
nominal endpoint. No timestamp search, boundary tuning or admission occurs.
"""
from collections import Counter, defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_sep22_quote_witness_minutes_bounded import clock

RAW = p.BASE/'backfilled_panel_candidate_native_quote_reconciled_v17_raw'
DEST = p.OUT/'tencent_nominal_quote_cut_diagnostic'
WHOLE = p.BASE/'public_source_probes/additional_native_inventory_20261009'
END = [37800000,41400000,50400000,54000000]


def main():
    lock = p.locked('diagnose_tencent_nominal_quote_cuts');DEST.mkdir(exist_ok=True);output = DEST/'receipt.json';assert not output.exists()
    audit_path = RAW/'raw_full_history_audit.json';audit = p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256'] == p.sha(RAW/'dataset/meta.json')
    identity = next(a['verified_identity'] for a in audit['arrays'] if a['name'] == 'hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy') == identity
    mask = p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal = p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    requested = defaultdict(lambda:defaultdict(list));source_pins = []
    for name in ['sina_tail_minute_expanded','sina_tail_nonmonotonic_references','sina_remaining376_tail_references']:
        path = p.OUT/name/'independent_verification.json';proof = p.json.loads(path.read_text());assert proof['passed'] and proof['audited_receipt_sha256'] == p.sha(path.parent/'receipt.json')
        source_pins.append(dict(path=str(path),sha256=p.sha(path)))
        for h in proof['qualified_minute_diagnostic_hours']:
            if mask[cal['dates'].index(h['date'])*4+h['slot'],cal['union_symbols'].index(h['symbol'])]:continue
            requested[h['date']][h['symbol']].append(h)
    requested_keys = {(h['date'],h['symbol'],h['slot']) for stocks in requested.values() for hs in stocks.values() for h in hs}
    assert len(requested_keys) == sum(len(hs) for stocks in requested.values() for hs in stocks.values())
    coarse_path = p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse = p.json.loads(coarse_path.read_text());coarse_proof_path = coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof = p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256'] == p.sha(coarse_path)
    acquisition_path = coarse_path.parent/'all_target_stock_acquisition.json';assert p.sha(acquisition_path) == coarse['source_acquisition_sha256']
    refs = defaultdict(dict);cumulative = {};counts = Counter();results = []
    symbols = {key[1] for key in requested_keys}
    for r in p.json.loads(acquisition_path.read_text())['records']:
        if r['symbol'] not in symbols:continue
        path = p.Path(r['path']);assert p.identity(path) == r['verified_identity'];data = path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == r['sha256']
        for q in p.json.loads(data)['data'][r['code']]['m60']:
            t = q[0];date = f'{t[:4]}-{t[4:6]}-{t[6:8]}';slot = ['1030','1130','1400','1500'].index(t[-4:])
            if date not in requested or r['symbol'] not in requested[date]:continue
            values = {f:int(D(q[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]};shares = D(q[5])*100
            assert shares == shares.to_integral_value();values['volume'] = int(shares);refs[date,r['symbol']][slot] = values
        assert p.identity(path) == r['verified_identity']
    for key,hours in refs.items():
        assert set(hours) == {0,1,2,3};running = 0
        for slot in range(4):running += hours[slot]['volume'];cumulative[*key,slot] = running
    result = dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='diagnosing_fixed_nominal_quote_endpoints',parent_raw_audit_sha256=p.sha(audit_path),
        independent_minute_proof_pins=source_pins,coarse_receipt_sha256=p.sha(coarse_path),coarse_proof_sha256=p.sha(coarse_proof_path),requested_hours=len(requested_keys),
        fixed_rule='For each native minute-exact missing hour, compare Tencent cumulative same-day hour volume and close only with an actual source quote whose reported time is EXACTLY10:30:00,11:30:00,14:00:00 or15:00:00. No selecting a different timestamp to improve the match. Nominal quote equality is diagnostic; actual trade-prefix intervalOHLC and source arrival must be independently replayed before any qualification.',
        canonical_minute_clock_changed=False,coarse_vendor_implementation_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,records=[],source_quote_pins=[],counts={})
    p.atomic_json(output,result)
    for date,stocks in sorted(requested.items()):
        if date == '2026-09-23':
            source_path = WHOLE/'whole_event_archive/full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json';proof_path = source_path.parent/'independent_full_union_trade_prefix_verification.json'
            source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_full_union_trade_prefix_receipt_sha256'] == p.sha(source_path)
            leaves = {r['symbol']:r['quote_prefix_witnesses'] for r in source['records']};kind = 'accepted_independently_verified_whole_trade_prefix_quote_witness'
        else:
            assert date == '2026-09-22';source_path = WHOLE/'whole_event_archive_sep22/acquisition_and_extraction.json';proof_path = source_path.parent/'independent_member_verification.json'
            source = p.json.loads(source_path.read_text());proof = p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256'] == p.sha(source_path)
            leaves = {r['symbol']:r for r in source['records'] if r['kind'] == 'snapshot'};kind = 'native_snapshot_prefix_replay_pending'
        result['source_quote_pins'].append(dict(date=date,receipt_path=str(source_path),receipt_sha256=p.sha(source_path),proof_path=str(proof_path),proof_sha256=p.sha(proof_path),kind=kind))
        for symbol,hs in sorted(stocks.items()):
            leaf = leaves[symbol];path = p.Path(leaf['path']);before = p.identity(path);observed = defaultdict(list)
            if date == '2026-09-23':
                data = path.read_bytes();assert p.hashlib.sha256(data).hexdigest() == leaf['sha256'];pf = p.pq.ParquetFile(p.pa.BufferReader(data));assert pf.metadata.num_rows == leaf['rows'];cols = None
            else:
                assert before == leaf['verified_identity'];pf = p.pq.ParquetFile(path);vf = 'TradVolume' if symbol.endswith('.SH') else 'Volume';cols = ['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','Turnover',vf]
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if date == '2026-09-23':
                        if not (q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']):continue
                        t = q['quote_reported_ms'];quantity = D(q['quote_quantity']);price = D(q['quote_LastPrice'])*10000;received = q['quote_received_ms'];sequence = str(q['quote_seq_no']);prefix_event = q['last_actual_trade_EventTime_ms']
                    else:
                        if q['SecurityID'] != symbol[:6]:continue
                        t = clock(q['UpdateTime']);quantity = D(str(q[vf]));price = D(str(q['LastPrice']))*10000;received = clock(q['LocalTime']);sequence = str(q['SeqNo']);prefix_event = None
                    if t not in END:continue
                    slot = END.index(t);observed[slot].append(dict(source_quote_reported_ms=t,source_quote_received_ms=received,source_quote_sequence=sequence,
                        source_quote_quantity=int(quantity),source_quote_last_price_x10000=int(price),last_actual_prefix_event_ms=prefix_event,quantity_and_price_integral=bool(quantity==quantity.to_integral_value() and price==price.to_integral_value())))
            assert p.identity(path) == before
            for h in sorted(hs,key=lambda h:h['slot']):
                slot = h['slot'];key = date,symbol,slot;quotes = observed[slot];wanted_qty = cumulative[key];wanted_close = refs[date,symbol][slot]['close']
                qty_matching = [q for q in quotes if q['quantity_and_price_integral'] and q['source_quote_quantity'] == wanted_qty]
                exact = [q for q in qty_matching if q['source_quote_last_price_x10000'] == wanted_close]
                status = 'actual_nominal_quote_quantity_and_close_exact' if exact else 'nominal_quote_quantity_matches_close_conflict' if qty_matching else 'nominal_quote_quantity_conflict' if quotes else 'no_actual_quote_at_fixed_nominal_endpoint'
                counts[status] += 1;result['records'].append(dict(date=date,symbol=symbol,slot=slot,status=status,tencent_hour=refs[date,symbol][slot],tencent_cumulative_volume_shares=wanted_qty,
                    observed_nominal_quotes=quotes,exact_quotes=exact,quote_source_path=str(path),quote_source_sha256=leaf['sha256'],quote_source_identity=before,quote_source_kind=kind,
                    actual_native_interval_ohlc_or_independence_not_yet_certified=True,sina_reference_path=h['sina_reference_path'],sina_reference_sha256=h['sina_reference_sha256']))
            result.update(updated_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    assert len(result['records']) == len(requested_keys) and p.identity(RAW/'dataset/hour_complete.npy') == identity
    result.update(status='complete_nominal_endpoint_diagnostic_no_admission',finished_utc=p.utc());p.atomic_json(output,result);lock.close()


if __name__ == '__main__':main()
