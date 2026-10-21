"""Separate observed quote candles, actual trade prefixes, and availability times.

These are diagnostic derived observations. No candidate dataset is modified.
Two source conventions are retained explicitly, without choosing per minute.
"""
import bisect
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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
DAY = '2026-09-23'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dec(value):
    return Decimal(str(value))


def milliseconds(value):
    h, m, s = value.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(Decimal(s) * 1000)


def save(rows, path):
    table = pa.Table.from_pylist(rows)
    if path.exists():
        assert pq.read_table(path).equals(table), 'Retain partial-run artifacts only if exactly reproduced'
    else:
        pq.write_table(table, path, compression='zstd')
    return {'path': str(path), 'sha256': sha(path), 'rows': len(rows)}


def compare(actual, reference, fields):
    assert len(actual) == len(reference)
    counts = {f: 0 for f in fields}
    details = []
    for a, b in zip(actual, reference):
        exact = {f: dec(a[f]) == dec(b[f]) for f in fields}
        counts = {f: counts[f] + int(exact[f]) for f in fields}
        gate = (exact['open'] and exact['close'] and dec(a['high']) <= dec(b['high'])
                and dec(a['low']) >= dec(b['low']) and abs(dec(a['volume']) - dec(b['volume'])) <= 100)
        details.append({'left_minute': a['left_minute'], 'exact': exact, 'strict_existing_O_C_range_V_gate': gate})
    return {'intervals': len(actual), 'exact_intervals_by_field': counts,
            'all_fields_exact_intervals': sum(all(d['exact'].values()) for d in details),
            'strict_existing_O_C_range_V_gate_pass_intervals': sum(d['strict_existing_O_C_range_V_gate'] for d in details),
            'failed_strict_left_labels': [d['left_minute'] for d in details if not d['strict_existing_O_C_range_V_gate']],
            'details': details}


def aggregate(minutes):
    bars = []
    for k in range(0, 240, 5):
        block = minutes[k:k + 5]
        bar = {'left_minute': block[0]['left_minute'], 'open': block[0]['open'],
                     'high': str(max(dec(r['high']) for r in block)), 'low': str(min(dec(r['low']) for r in block)),
                     'close': block[-1]['close'], 'volume': sum(r['volume'] for r in block)}
        if all('amount' in r for r in block):
            bar['amount'] = str(sum(dec(r['amount']) for r in block))
        bars.append(bar)
    return bars


def audit(code, event_path, quote_path, coarse_path, original_path, complete, members, calendar, execution):
    table = pq.read_table(event_path)
    assert table.schema.metadata[b'event_time_unit'] == b'millisecond'
    assert table.schema.metadata[b'price_scale'] == b'10000' and table.schema.metadata[b'qty_scale'] == b'1'
    trade_table = table.filter(pc.equal(table['RecordType'], 2))
    rows = trade_table.to_pylist()
    pairs = sorted(zip(trade_table['EventTime'].cast(pa.int32()).to_pylist(),
                       trade_table['LocalTime'].cast(pa.int32()).to_pylist(), rows),
                   key=lambda x: (x[0], x[2]['EventSeqNo']))
    assert len({r['EventSeqNo'] for _, _, r in pairs}) == len(pairs)
    assert all(r['Symbol'] == code + '.SZ' and r['Price'] > 0 and r['Price'] % 100 == 0 and r['Qty'] > 0
               for _, _, r in pairs)
    assert all(t == 33900000 or 34200000 <= t <= 41400000 or 46800000 <= t <= 54000000 for t, _, _ in pairs)
    cumulative_qty, cumulative_amount, cumulative_local = [0], [0], [0]
    for _, received, r in pairs:
        cumulative_qty.append(cumulative_qty[-1] + r['Qty'])
        cumulative_amount.append(cumulative_amount[-1] + r['Price'] * r['Qty'])
        cumulative_local.append(max(cumulative_local[-1], received))
    quotes = pq.read_table(quote_path).to_pylist()
    quote_ms = [milliseconds(r['UpdateTime']) for r in quotes]
    assert all(r['SecurityID'] == code for r in quotes)
    assert all(a < b for a, b in zip(quote_ms, quote_ms[1:]))
    assert all(a['Volume'] <= b['Volume'] for a, b in zip(quotes, quotes[1:]))
    witnesses = []
    for quote, reported in zip(quotes, quote_ms):
        if quote['Volume'] <= 0:
            continue
        cut = bisect.bisect_left(cumulative_qty, quote['Volume'])
        assert cut <= len(pairs) and cumulative_qty[cut] == quote['Volume']
        event, received, last = pairs[cut - 1]
        assert dec(quote['LastPrice']) == Decimal(last['Price']) / 10000
        amount_exact = dec(quote['Turnover']) == Decimal(cumulative_amount[cut]) / 10000
        witnesses.append({'quote_seq_no': quote['SeqNo'], 'quote_UpdateTime': quote['UpdateTime'],
                          'quote_LocalTime': quote['LocalTime'], 'quote_reported_ms': reported,
                          'quote_received_ms': milliseconds(quote['LocalTime']), 'quote_Volume': quote['Volume'],
                          'quote_Turnover': str(quote['Turnover']), 'quote_LastPrice': str(quote['LastPrice']),
                          'whole_trade_prefix_count': cut, 'last_actual_trade_id': last['EventSeqNo'],
                          'last_actual_trade_EventTime_ms': event, 'last_actual_trade_LocalTime_ms': received,
                          'prefix_cumulative_qty_exact': True, 'last_actual_trade_price_exact': True,
                          'prefix_calculated_amount_exact': amount_exact,
                          'last_trade_EventTime_after_reported_quote_ms': event - reported,
                          'quote_receipt_after_reported_quote_ms': milliseconds(quote['LocalTime']) - reported})
    assert all(r['prefix_calculated_amount_exact'] for r in witnesses)
    witness_artifact = save(witnesses, OUT / (code + '_all_positive_quote_trade_prefix_witnesses.parquet'))
    models = {'quote_prices_closed_right': [], 'original_quote_boundary_and_new_daily_extrema': [],
              'actual_trade_prefix_with_prior_quote_open': []}
    previous_cut = 0
    previous_turnover = Decimal(0)
    for left in GRID:
        end = (left + 1) * 60000
        first = bisect.bisect_right(quote_ms, left * 60000)
        stop = bisect.bisect_right(quote_ms, end)
        observed = [r for r in quotes[first:stop] if r['Volume'] > 0]
        assert observed, 'A candle requires real in-interval positive-price quotes'
        end_quote = quotes[stop - 1]
        before = quotes[first - 1]
        cut = bisect.bisect_left(cumulative_qty, end_quote['Volume'])
        assert cumulative_qty[cut] == end_quote['Volume'] and cut >= previous_cut
        included = pairs[previous_cut:cut]
        volume = end_quote['Volume'] - cumulative_qty[previous_cut]
        amount = dec(end_quote['Turnover']) - previous_turnover
        assert sum(r['Qty'] for _, _, r in included) == volume
        assert Decimal(sum(r['Price'] * r['Qty'] for _, _, r in included)) / 10000 == amount
        quote_prices = [dec(r['LastPrice']) for r in observed]
        opening = dec(end_quote['OpenPrice']) if left == GRID[0] else dec(before['LastPrice'])
        public_opening = dec(end_quote['OpenPrice']) if left == GRID[0] else quote_prices[0]
        original_high = max([opening] + quote_prices)
        original_low = min([opening] + quote_prices)
        high_changed = left == GRID[0] or dec(end_quote['HighPrice']) > dec(before['HighPrice'])
        low_changed = left == GRID[0] or dec(end_quote['LowPrice']) < dec(before['LowPrice'])
        if high_changed:
            original_high = max(original_high, dec(end_quote['HighPrice']))
        if low_changed:
            original_low = min(original_low, dec(end_quote['LowPrice']))
        actual_prices = [Decimal(r['Price']) / 10000 for _, _, r in included]
        close = dec(end_quote['LastPrice'])
        basis = {'left_minute': left, 'end_ms': end, 'volume': volume, 'amount': str(amount),
                 'quote_count_observed_in_interval': len(observed), 'actual_trade_count_in_quote_prefix_delta': len(included),
                 'first_trade_prefix_index': previous_cut, 'end_trade_prefix_index': cut,
                 'first_actual_trade_id': included[0][2]['EventSeqNo'] if included else None,
                 'last_actual_trade_id': included[-1][2]['EventSeqNo'] if included else None,
                 'end_quote_seq_no': end_quote['SeqNo'], 'end_quote_UpdateTime': end_quote['UpdateTime'],
                 'end_quote_LocalTime': end_quote['LocalTime'], 'opening_boundary_quote_seq_no': before['SeqNo'],
                 'observed_quote_age_ms': end - quote_ms[stop - 1],
                 'quote_boundary_available_ms': max(milliseconds(r['LocalTime']) for r in observed),
                 'actual_prefix_and_quote_available_ms': max(cumulative_local[cut], max(milliseconds(r['LocalTime']) for r in observed)),
                 'last_actual_trade_EventTime_ms': pairs[cut - 1][0],
                 'last_actual_trade_event_age_at_end_ms': end - pairs[cut - 1][0],
                 'new_daily_high_observed': high_changed, 'new_daily_low_observed': low_changed}
        model_values = {
            'quote_prices_closed_right': [public_opening, max([public_opening] + quote_prices), min([public_opening] + quote_prices), close],
            'original_quote_boundary_and_new_daily_extrema': [opening, original_high, original_low, close],
            'actual_trade_prefix_with_prior_quote_open': [opening, max([opening, close] + actual_prices), min([opening, close] + actual_prices), close],
        }
        for name, values in model_values.items():
            models[name].append(dict(basis, **{f: str(p) for f, p in zip(FIELDS[:4], values)}))
        previous_cut = cut
        previous_turnover = dec(end_quote['Turnover'])
    assert previous_cut == len(pairs)
    daily_path = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (code + '.SZ.json')
    obj = json.loads(daily_path.read_text())
    day = next(dict(zip(obj['fields'], r)) for r in obj['rows'] if r[0] == DAY)
    actual_daily = {f: Decimal(v) / 10000 for f, v in zip(FIELDS[:4],
                   [pairs[0][2]['Price'], max(r['Price'] for _, _, r in pairs), min(r['Price'] for _, _, r in pairs), pairs[-1][2]['Price']])}
    actual_daily.update(volume=cumulative_qty[-1], amount=Decimal(cumulative_amount[-1]) / 10000)
    assert all(dec(actual_daily[f]) == dec(day[f]) for f in FIELDS)
    query = json.loads(coarse_path.read_text())
    assert sha(query['wire_path']) == query['sha256'] and query['error_code'] == '0' and query['adjustflag'] == '3'
    raw_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
    reference = [dict(zip(query['fields'], r)) for r in raw_rows]
    assert len(reference) == 48
    assert [int(r['time'][8:10]) * 60 + int(r['time'][10:12]) - 5 for r in reference] == GRID[::5]
    original = pq.read_table(original_path).to_pylist() if original_path else None
    if original is not None:
        assert len(original) == 240
        assert [int(r['datetime'][11:13]) * 60 + int(r['datetime'][14:16]) for r in original] == GRID
        original = [dict(r, left_minute=left) for left, r in zip(GRID, original)]
    results = []
    for name, minutes in models.items():
        minute_record = save(minutes, OUT / (code + '_' + name + '_diagnostic_minutes.parquet'))
        five = aggregate(minutes)
        five_comparison = compare(five, reference, FIELDS)
        record = {'definition': name, 'minutes': minute_record, 'five_minute_reference_comparison': five_comparison,
                  'candidate_admission_claimed': False}
        if original is not None:
            record['minute_original_control_comparison'] = compare(minutes, original, FIELDS[:5])
            record['five_minute_aggregated_original_control_comparison'] = compare(aggregate(minutes), aggregate(original), FIELDS[:5])
        results.append(record)
    day_index = calendar['dates'].index(DAY)
    stock_index = calendar['union_symbols'].index(code + '.SZ')
    from zoneinfo import ZoneInfo
    midnight = int(datetime.fromisoformat(DAY).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() * 1e9)
    timing = []
    for slot, signal_minute in enumerate([630, 690, 840, 900]):
        block = models['original_quote_boundary_and_new_daily_extrema'][slot * 60:(slot + 1) * 60]
        available = max(r['actual_prefix_and_quote_available_ms'] for r in block)
        exec_ms = (int(execution[day_index * 4 + slot]) - midnight) // 1000000
        timing.append({'slot': slot, 'signal_nominal_end_ms': signal_minute * 60000,
                       'all_hour_quote_and_actual_prefix_available_ms': available,
                       'available_after_nominal_signal_by_ms': available - signal_minute * 60000,
                       'current_earliest_execution_after_midnight_ms': exec_ms,
                       'stored_source_receipts_before_current_earliest_execution': available < exec_ms,
                       'last_actual_trade_event_age_at_hour_end_ms': block[-1]['last_actual_trade_event_age_at_end_ms']})
    return {'symbol': code + '.SZ', 'date': DAY, 'event_path': str(event_path), 'event_sha256': sha(event_path),
            'snapshot_path': str(quote_path), 'snapshot_sha256': sha(quote_path), 'actual_trade_rows': len(pairs),
            'native_quote_rows': len(quotes), 'positive_volume_quotes': len(witnesses), 'quote_prefix_witnesses': witness_artifact,
            'all_positive_quote_volume_exact_whole_trade_prefix': True, 'all_positive_quote_price_exact_last_actual_trade': True,
            'all_positive_quote_turnover_exact_calculated_actual_trade_prefix_amount': True,
            'quotes_with_last_trade_event_time_after_reported_UpdateTime': sum(r['last_trade_EventTime_after_reported_quote_ms'] > 0 for r in witnesses),
            'maximum_last_trade_event_time_after_reported_quote_ms': max(r['last_trade_EventTime_after_reported_quote_ms'] for r in witnesses),
            'original_daily_OHLC_V_and_calculated_A_all_exact': True, 'daily_reference_path': str(daily_path), 'daily_reference_sha256': sha(daily_path),
            'coarse_reference_path': str(coarse_path), 'coarse_reference_sha256': sha(coarse_path), 'coarse_wire_sha256': query['sha256'],
            'original_control_path': str(original_path) if original_path else None, 'original_control_sha256': sha(original_path) if original_path else None,
            'current_candidate_complete_slots': complete[day_index * 4:day_index * 4 + 4, stock_index].tolist(),
            'current_candidate_member_slots': members[day_index * 4:day_index * 4 + 4, stock_index].tolist(),
            'models': results, 'signal_and_execution_availability': timing,
            'maximum_minute_end_quote_age_ms': max(r['observed_quote_age_ms'] for r in models['quote_prices_closed_right']),
            'minutes_not_all_available_at_nominal_end': sum(r['actual_prefix_and_quote_available_ms'] > r['end_ms'] for r in models['quote_prices_closed_right']),
            'minutes_with_last_actual_trade_older_than_60s': sum(r['last_actual_trade_event_age_at_end_ms'] > 60000 for r in models['quote_prices_closed_right']),
            'zero_actual_trade_prefix_delta_minutes': [r['left_minute'] for r in models['quote_prices_closed_right'] if r['actual_trade_count_in_quote_prefix_delta'] == 0]}


def main():
    output = OUT / 'quote_trade_boundary_diagnostics.json'
    assert not output.exists()
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    ds = meta_path.parent
    meta_sha = sha(meta_path)
    meta = json.loads(meta_path.read_text())
    bound_files = ['hour_complete.npy', 'members.npy', 'earliest_execution_times.npy']
    for name in bound_files:
        assert sha(ds / name) == meta['files_sha256'][name]
    complete = np.load(ds / 'hour_complete.npy', mmap_mode='r')
    members = np.load(ds / 'members.npy', mmap_mode='r')
    execution = np.load(ds / 'earliest_execution_times.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    native_receipt_path = OUT / 'cached_control_member_acquisition.json'
    native_receipt = json.loads(native_receipt_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_cached_quote_control_sample.py') == native_receipt['source_code_sha256']
    for member in native_receipt['selected_members']:
        assert sha(member['path']) == member['sha256']
    original_receipt_path = OUT / 'original_control/extraction_receipt.json'
    original_receipt = json.loads(original_receipt_path.read_text())
    assert original_receipt['full_split_archive_sha256_verified'] and original_receipt['gzip_footer_checked']
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_original_quote_control_sample.py') == original_receipt['wrapper_sha256']
    original_record = original_receipt['payloads'][0]
    assert original_record['code'] == '000009' and sha(original_record['path']) == original_record['sha256']
    ms_proof_path = SOURCE / 'native_millisecond_trade_join_verification.json'
    ms_proof = json.loads(ms_proof_path.read_text())
    assert ms_proof['passed'] and sha(SOURCE / '000006_20260923_events.parquet') == ms_proof['event_member_sha256']
    assert sha(SOURCE / '000006_20260923_snapshot.parquet') == ms_proof['snapshot_member_sha256']
    results = [audit('000006', SOURCE / '000006_20260923_events.parquet', SOURCE / '000006_20260923_snapshot.parquet',
                     BASE / 'public_source_probes/phields_trades_20261009/sample_references/sz.000006_2026-09-23_2026-09-23_5_p1.json',
                     None, complete, members, calendar, execution),
               audit('000009', OUT / '000009_20260923_events.parquet', OUT / '000009_20260923_snapshot.parquet',
                     OUT / 'control_coarse_reference/sz.000009_2026-09-23_2026-09-23_5_p1.json',
                     Path(original_record['path']), complete, members, calendar, execution)]
    assert sha(meta_path) == meta_sha
    assert results[0]['current_candidate_complete_slots'] == [False] * 4
    assert results[1]['current_candidate_complete_slots'] == [True] * 4
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'current_candidate_meta_sha256': meta_sha, 'candidate_fingerprint': meta['data_fingerprint'],
              'calendar_sha256': sha(calendar_path), 'candidate_bound_file_sha256': {name: sha(ds / name) for name in bound_files},
              'prior_actual_trade_identity_proof_sha256': sha(ms_proof_path), 'cached_control_acquisition_sha256': sha(native_receipt_path),
              'original_positive_control_acquisition_sha256': sha(original_receipt_path), 'results': results,
              'whole_event_archive_LFS_SHA256_verified': False, 'whole_event_gzip_CRC_verified': False,
              'quote_candles_are_not_claimed_exchange_event_time_trade_candles': True,
              'stored_local_receive_times_are_not_claimed_independently_clock_synchronised': True,
              'actual_trade_amount_is_calculated_price_times_qty_not_an_independently_certified_trade_amount_field': True,
              'no_partial_trade_or_price_inversion_or_missing_price_forward_fill': True,
              'minute_prices_use_actual_in_interval_quotes_and_observed_open_and_daily_extrema': True,
              'earliest_execution_comparison_is_diagnostic_not_live_readiness_certification': True,
              'native_quote_definition_and_cross_provider_OHLC_differences_require_explicit_admission_review': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'receipt': str(output), 'results': [{k: r[k] for k in ['symbol', 'actual_trade_rows', 'positive_volume_quotes', 'maximum_minute_end_quote_age_ms', 'signal_and_execution_availability']} for r in results]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
