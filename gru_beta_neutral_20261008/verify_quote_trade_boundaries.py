"""Independently reproduce quote/trade witnesses and both candle conventions."""
import hashlib
import io
import json
import sys
import tarfile
import zlib
from datetime import datetime, time, timezone
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009'
OUT = SOURCE / 'quote_boundary_audit'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ms(value):
    t = time.fromisoformat(value) if isinstance(value, str) else value
    return ((t.hour * 60 + t.minute) * 60 + t.second) * 1000 + t.microsecond // 1000


def decimal(value):
    return D(str(value))


def grouped(minutes):
    result = []
    for i in range(48):
        rows = minutes[i * 5:i * 5 + 5]
        bar = {'left_minute': rows[0]['left_minute'], 'open': rows[0]['open'], 'close': rows[-1]['close'],
               'high': max(decimal(r['high']) for r in rows), 'low': min(decimal(r['low']) for r in rows),
               'volume': sum(r['volume'] for r in rows)}
        if all('amount' in r for r in rows):
            bar['amount'] = sum(decimal(r['amount']) for r in rows)
        result.append(bar)
    return result


def check_comparison(actual, reference, fields, published):
    details = []
    for a, r in zip(actual, reference):
        same = {f: decimal(a[f]) == decimal(r[f]) for f in fields}
        strict = (same['open'] and same['close'] and decimal(a['high']) <= decimal(r['high'])
                  and decimal(a['low']) >= decimal(r['low']) and abs(decimal(a['volume']) - decimal(r['volume'])) <= 100)
        details.append({'left_minute': a['left_minute'], 'exact': same, 'strict_existing_O_C_range_V_gate': strict})
    result = {'intervals': len(actual), 'exact_intervals_by_field': {f: sum(r['exact'][f] for r in details) for f in fields},
              'all_fields_exact_intervals': sum(all(r['exact'].values()) for r in details),
              'strict_existing_O_C_range_V_gate_pass_intervals': sum(r['strict_existing_O_C_range_V_gate'] for r in details),
              'failed_strict_left_labels': [r['left_minute'] for r in details if not r['strict_existing_O_C_range_V_gate']], 'details': details}
    assert result == published


def main():
    output = OUT / 'independent_quote_trade_boundary_verification.json'
    assert not output.exists()
    producer_path = OUT / 'quote_trade_boundary_diagnostics.json'
    producer = json.loads(producer_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_quote_trade_boundaries.py') == producer['source_code_sha256']
    assert producer['candidate_hours_admitted'] == 0 and not producer['candidate_changed']
    # Re-extract whole selected native members from exact retained range bytes.
    acquisition = json.loads((SOURCE / 'bounded_native_millisecond_sample_acquisition.json').read_text())
    native_bytes = []
    for record in [acquisition['initial_range'], acquisition['extension_range']]:
        assert record['http_status'] == 206 and sha(record['path']) == record['retained_range_sha256']
        native_bytes.append(Path(record['path']).read_bytes())
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    prefix = decoder.decompress(b''.join(native_bytes), 80 * 1024 * 1024)
    assert not decoder.eof and len(prefix) == acquisition['decompressed_prefix_bytes']
    wanted = {}
    for record in producer['results']:
        code = record['symbol'][:6]
        for kind, field in [('events', 'event_path'), ('snapshot', 'snapshot_path')]:
            wanted['20260923/' + code + '.SZ/' + kind + '.parquet'] = Path(record[field])
    extracted = []
    with tarfile.open(fileobj=io.BytesIO(prefix), mode='r|') as archive:
        for member in archive:
            if member.name in wanted:
                assert member.offset_data + member.size <= len(prefix)
                raw = archive.extractfile(member).read()
                assert len(raw) == member.size and raw == wanted[member.name].read_bytes()
                extracted.append(member.name)
                if len(extracted) == 4:
                    break
    assert set(extracted) == set(wanted)
    # Bind the control extraction to freshly hashed original source parts.
    original_receipt_path = OUT / 'original_control/extraction_receipt.json'
    assert sha(original_receipt_path) == producer['original_positive_control_acquisition_sha256']
    original_receipt = json.loads(original_receipt_path.read_text())
    assert original_receipt['full_split_archive_sha256_verified'] and original_receipt['gzip_footer_checked']
    for record in original_receipt['source_parts']:
        path = ROOT / record['name']
        assert path.stat().st_size == record['size_bytes'] and sha(path) == record['sha256']
    # Freshly scan actual whole-file-backed phields trades for both symbols.
    prior_path = BASE / 'public_source_probes/phields_trades_20261009/actual_trade_sample_diagnostics.json'
    prior = json.loads(prior_path.read_text())
    payload = next(r for r in prior['whole_payloads'] if '_00_' in r['source_path'])
    assert sha(payload['source_path']) == payload['whole_source_sha256']
    parquet = pq.ParquetFile(payload['source_path'])
    seconds = {}
    scanned = []
    for i in range(parquet.num_row_groups):
        stats = parquet.metadata.row_group(i).column(0).statistics
        if any(stats.min <= code <= stats.max for code in ['000006', '000009']):
            table = parquet.read_row_group(i, columns=['ticker', 'time_s', 'tran_id', 'price_x10000', 'volume'])
            table = table.filter(pc.is_in(table['ticker'], value_set=pa.array(['000006', '000009'])))
            for r in table.to_pylist():
                key = (r['ticker'], r['tran_id'])
                assert key not in seconds
                seconds[key] = r
            scanned.append(i)
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == producer['current_candidate_meta_sha256']
    meta = json.loads(meta_path.read_text())
    assert meta['data_fingerprint'] == producer['candidate_fingerprint']
    for name, digest in producer['candidate_bound_file_sha256'].items():
        assert sha(meta_path.parent / name) == digest == meta['files_sha256'][name]
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == producer['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    execution = np.load(meta_path.parent / 'earliest_execution_times.npy', mmap_mode='r')
    complete = np.load(meta_path.parent / 'hour_complete.npy', mmap_mode='r')
    members = np.load(meta_path.parent / 'members.npy', mmap_mode='r')
    stockday = calendar['dates'].index('2026-09-23')
    midnight = int(datetime(2026, 9, 23, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() * 1e9)
    verified = []
    for record in producer['results']:
        code = record['symbol'][:6]
        assert sha(record['event_path']) == record['event_sha256'] and sha(record['snapshot_path']) == record['snapshot_sha256']
        events = [r for r in pq.read_table(record['event_path']).to_pylist() if r['RecordType'] == 2]
        events.sort(key=lambda r: (r['EventTime'], r['EventSeqNo']))
        assert len(events) == record['actual_trade_rows']
        assert {r['EventSeqNo'] for r in events} == {k[1] for k in seconds if k[0] == code}
        prefixes = {0: (0, 0, 0)}
        qty = amount = received = 0
        for n, r in enumerate(events, 1):
            source = seconds[code, r['EventSeqNo']]
            assert (source['price_x10000'], source['volume'], source['time_s']) == (r['Price'], r['Qty'], ms(r['EventTime']) // 1000)
            qty += r['Qty']; amount += r['Price'] * r['Qty']; received = max(received, ms(r['LocalTime']))
            assert qty not in prefixes
            prefixes[qty] = (n, amount, received)
        quotes = pq.read_table(record['snapshot_path']).to_pylist()
        quote_pairs = [(ms(r['UpdateTime']), r) for r in quotes]
        assert len(quotes) == record['native_quote_rows']
        artifact = record['quote_prefix_witnesses']
        assert sha(artifact['path']) == artifact['sha256']
        actual_witnesses = pq.read_table(artifact['path']).to_pylist()
        expected_witnesses = []
        for reported, q in quote_pairs:
            if q['Volume'] <= 0:
                continue
            n, amount_scaled, recv_max = prefixes[q['Volume']]
            last = events[n - 1]
            assert decimal(q['LastPrice']) == D(last['Price']) / 10000
            assert decimal(q['Turnover']) == D(amount_scaled) / 10000
            expected_witnesses.append({'quote_seq_no': q['SeqNo'], 'quote_UpdateTime': q['UpdateTime'], 'quote_LocalTime': q['LocalTime'],
                'quote_reported_ms': reported, 'quote_received_ms': ms(q['LocalTime']), 'quote_Volume': q['Volume'],
                'quote_Turnover': str(q['Turnover']), 'quote_LastPrice': str(q['LastPrice']), 'whole_trade_prefix_count': n,
                'last_actual_trade_id': last['EventSeqNo'], 'last_actual_trade_EventTime_ms': ms(last['EventTime']),
                'last_actual_trade_LocalTime_ms': ms(last['LocalTime']), 'prefix_cumulative_qty_exact': True,
                'last_actual_trade_price_exact': True, 'prefix_calculated_amount_exact': True,
                'last_trade_EventTime_after_reported_quote_ms': ms(last['EventTime']) - reported,
                'quote_receipt_after_reported_quote_ms': ms(q['LocalTime']) - reported})
        assert actual_witnesses == expected_witnesses and len(expected_witnesses) == record['positive_volume_quotes']
        previous_n = previous_qty = previous_amt = 0
        expected_models = {r['definition']: [] for r in record['models']}
        for left in GRID:
            end = (left + 1) * 60000
            observed = [q for t, q in quote_pairs if left * 60000 < t <= end and q['Volume'] > 0]
            assert observed
            last_time, end_quote = [(t, q) for t, q in quote_pairs if t <= end][-1]
            before = [q for t, q in quote_pairs if t <= left * 60000][-1]
            n, amt_scaled, recv_max = prefixes[end_quote['Volume']]
            trades = events[previous_n:n]
            assert sum(r['Qty'] for r in trades) == end_quote['Volume'] - previous_qty
            assert sum(r['Price'] * r['Qty'] for r in trades) == amt_scaled - previous_amt
            op = decimal(end_quote['OpenPrice']) if left == 570 else decimal(before['LastPrice'])
            sampled = [decimal(q['LastPrice']) for q in observed]
            pop = decimal(end_quote['OpenPrice']) if left == 570 else sampled[0]
            hi, lo = max([op] + sampled), min([op] + sampled)
            hc = left == 570 or decimal(end_quote['HighPrice']) > decimal(before['HighPrice'])
            lc = left == 570 or decimal(end_quote['LowPrice']) < decimal(before['LowPrice'])
            if hc:
                hi = max(hi, decimal(end_quote['HighPrice']))
            if lc:
                lo = min(lo, decimal(end_quote['LowPrice']))
            close = decimal(end_quote['LastPrice'])
            traded = [D(r['Price']) / 10000 for r in trades]
            values = {'quote_prices_closed_right': [pop, max([pop] + sampled), min([pop] + sampled), close],
                      'original_quote_boundary_and_new_daily_extrema': [op, hi, lo, close],
                      'actual_trade_prefix_with_prior_quote_open': [op, max([op, close] + traded), min([op, close] + traded), close]}
            basis = {'left_minute': left, 'end_ms': end, 'volume': end_quote['Volume'] - previous_qty,
                     'amount': str(D(amt_scaled - previous_amt) / 10000), 'quote_count_observed_in_interval': len(observed),
                     'actual_trade_count_in_quote_prefix_delta': len(trades), 'first_trade_prefix_index': previous_n,
                     'end_trade_prefix_index': n, 'first_actual_trade_id': trades[0]['EventSeqNo'] if trades else None,
                     'last_actual_trade_id': trades[-1]['EventSeqNo'] if trades else None,
                     'end_quote_seq_no': end_quote['SeqNo'], 'end_quote_UpdateTime': end_quote['UpdateTime'],
                     'end_quote_LocalTime': end_quote['LocalTime'], 'opening_boundary_quote_seq_no': before['SeqNo'],
                     'observed_quote_age_ms': end - last_time, 'quote_boundary_available_ms': max(ms(q['LocalTime']) for q in observed),
                     'actual_prefix_and_quote_available_ms': max(recv_max, max(ms(q['LocalTime']) for q in observed)),
                     'last_actual_trade_EventTime_ms': ms(events[n - 1]['EventTime']),
                     'last_actual_trade_event_age_at_end_ms': end - ms(events[n - 1]['EventTime']),
                     'new_daily_high_observed': hc, 'new_daily_low_observed': lc}
            for name, price in values.items():
                expected_models[name].append(dict(basis, **{f: str(v) for f, v in zip(FIELDS[:4], price)}))
            previous_n, previous_qty, previous_amt = n, end_quote['Volume'], amt_scaled
        assert previous_n == len(events)
        query = json.loads(Path(record['coarse_reference_path']).read_text())
        assert sha(record['coarse_reference_path']) == record['coarse_reference_sha256'] and sha(query['wire_path']) == record['coarse_wire_sha256']
        rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
        coarse = [dict(zip(query['fields'], r)) for r in rows]
        original = None
        if record['original_control_path']:
            assert sha(record['original_control_path']) == record['original_control_sha256']
            original = [dict(r, left_minute=left) for left, r in zip(GRID, pq.read_table(record['original_control_path']).to_pylist())]
        for model in record['models']:
            artifact = model['minutes']
            assert sha(artifact['path']) == artifact['sha256']
            actual = pq.read_table(artifact['path']).to_pylist()
            expected = expected_models[model['definition']]
            for a, e in zip(actual, expected):
                assert set(a) == set(e)
                assert all(decimal(a[k]) == decimal(e[k]) if k in FIELDS else a[k] == e[k] for k in a)
            check_comparison(expected, original, FIELDS[:5], model['minute_original_control_comparison']) if original else None
            if original:
                check_comparison(grouped(expected), grouped(original), FIELDS[:5], model['five_minute_aggregated_original_control_comparison'])
            check_comparison(grouped(expected), coarse, FIELDS, model['five_minute_reference_comparison'])
        original_model = expected_models['original_quote_boundary_and_new_daily_extrema']
        for slot, nominal in enumerate([630, 690, 840, 900]):
            available = max(r['actual_prefix_and_quote_available_ms'] for r in original_model[slot * 60:(slot + 1) * 60])
            execution_ms = (int(execution[stockday * 4 + slot]) - midnight) // 1000000
            assert record['signal_and_execution_availability'][slot] == {
                'slot': slot, 'signal_nominal_end_ms': nominal * 60000, 'all_hour_quote_and_actual_prefix_available_ms': available,
                'available_after_nominal_signal_by_ms': available - nominal * 60000,
                'current_earliest_execution_after_midnight_ms': execution_ms,
                'stored_source_receipts_before_current_earliest_execution': available < execution_ms,
                'last_actual_trade_event_age_at_hour_end_ms': original_model[slot * 60 + 59]['last_actual_trade_event_age_at_end_ms']}
        col = calendar['union_symbols'].index(code + '.SZ')
        assert complete[stockday * 4:stockday * 4 + 4, col].tolist() == record['current_candidate_complete_slots']
        assert members[stockday * 4:stockday * 4 + 4, col].tolist() == record['current_candidate_member_slots']
        daily_path = Path(record['daily_reference_path'])
        assert sha(daily_path) == record['daily_reference_sha256']
        dayobj = json.loads(daily_path.read_text())
        day = next(dict(zip(dayobj['fields'], r)) for r in dayobj['rows'] if r[0] == '2026-09-23')
        vals = [D(events[0]['Price']) / 10000, D(max(r['Price'] for r in events)) / 10000,
                D(min(r['Price'] for r in events)) / 10000, D(events[-1]['Price']) / 10000, qty, D(amount) / 10000]
        assert all(decimal(v) == decimal(day[f]) for v, f in zip(vals, FIELDS))
        assert record['quotes_with_last_trade_event_time_after_reported_UpdateTime'] == sum(r['last_trade_EventTime_after_reported_quote_ms'] > 0 for r in expected_witnesses)
        assert record['maximum_last_trade_event_time_after_reported_quote_ms'] == max(r['last_trade_EventTime_after_reported_quote_ms'] for r in expected_witnesses)
        for key, expected in {
            'maximum_minute_end_quote_age_ms': max(r['observed_quote_age_ms'] for r in original_model),
            'minutes_not_all_available_at_nominal_end': sum(r['actual_prefix_and_quote_available_ms'] > r['end_ms'] for r in original_model),
            'minutes_with_last_actual_trade_older_than_60s': sum(r['last_actual_trade_event_age_at_end_ms'] > 60000 for r in original_model),
            'zero_actual_trade_prefix_delta_minutes': [r['left_minute'] for r in original_model if r['actual_trade_count_in_quote_prefix_delta'] == 0],
        }.items():
            assert record[key] == expected
        verified.append({'symbol': record['symbol'], 'actual_whole_file_backed_trade_rows_rejoined': len(events),
                         'all_positive_quotes_exact_qty_price_and_calculated_amount_prefixes': len(expected_witnesses),
                         'all_240_minutes_all_three_diagnostic_definitions_independently_reproduced': True,
                         'all_field_and_existing_strict_gate_comparisons_reproduced': True,
                         'current_candidate_and_execution_times_exactly_bound': True})
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'producer_receipt_sha256': sha(producer_path), 'producer_code_sha256': producer['source_code_sha256'],
              'current_candidate_meta_sha256': sha(meta_path), 'candidate_fingerprint': meta['data_fingerprint'],
              'cached_native_members_reextracted': extracted, 'whole_file_backed_phields_payload_sha256': payload['whole_source_sha256'],
              'whole_phields_row_groups_rescanned': scanned, 'fresh_original_source_part_SHA256_verified': True,
              'results': verified, 'whole_native_event_outer_archive_LFS_SHA256_verified': False,
              'whole_native_event_outer_gzip_CRC_verified': False, 'strict_at_nominal_signal_PIT_certified': False,
              'stored_receipt_times_before_execution_do_not_certify_live_latency': True,
              'source_independence_not_claimed': True, 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
