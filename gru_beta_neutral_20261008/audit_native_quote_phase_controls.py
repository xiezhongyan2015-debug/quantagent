"""Test two fixed quote candle definitions against every original day control.

The session rule uses an observed afternoon opening quote and an explicit
closing phase quote with exactly corroborated whole regular-session trades.
No definition is selected per stock/minute and nothing is admitted.
"""
import bisect
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
NATIVE = SOURCE / 'full_union_day_native_members'
ORIGINAL = SOURCE / 'all_original_day_controls'
OUT = SOURCE / 'quote_phase_control_audit'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ms(value):
    h, m, s = value.split(':')
    return (int(h) * 60 + int(m)) * 60000 + int(D(s) * 1000)


def dec(value):
    return D(str(value))


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'all_499_native_quote_phase_control_diagnostics.json'
    assert not output.exists()
    native_proof_path = NATIVE / 'independent_complete_native_member_verification.json'
    native_proof = json.loads(native_proof_path.read_text())
    assert native_proof['passed'] and native_proof['current_complete_controls_with_complete_source_pairs'] == 499
    original_proof_path = ORIGINAL / 'independent_all_original_control_verification.json'
    original_proof = json.loads(original_proof_path.read_text())
    assert original_proof['passed'] and original_proof['original_native_minutes_checked'] == 119760
    original_audit_path = ORIGINAL / 'all_original_control_reference_audit.json'
    original_audit = json.loads(original_audit_path.read_text())
    assert sha(original_audit_path) == original_proof['audited_reference_receipt_sha256']
    original_receipt = json.loads((ORIGINAL / 'extraction_receipt.json').read_text())
    native_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    assert sha(native_receipt_path) == native_proof['native_member_acquisition_receipt_sha256']
    native_receipt = json.loads(native_receipt_path.read_text())
    leaves = {(r['symbol'], r['kind']): r for r in native_receipt['records']}
    codes = {r['symbol']: r for r in original_audit['records']}
    assert len(codes) == 499
    summaries, generated = [], []
    started = last = time.monotonic()
    for symbol in sorted(codes):
        source = leaves[symbol, 'snapshot']
        events = leaves[symbol, 'events']
        assert sha(source['path']) == source['sha256'] and sha(events['path']) == events['sha256']
        venue = symbol[-2:]
        volume_field = 'TradVolume' if venue == 'SH' else 'Volume'
        phase_field = 'InstruStatus' if venue == 'SH' else 'TradingPhaseCode'
        columns = ['UpdateTime', 'LocalTime', 'SeqNo', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', 'Turnover', volume_field, phase_field]
        quotes = pq.read_table(source['path'], columns=columns).to_pylist()
        quotes.sort(key=lambda r: (ms(r['UpdateTime']), ms(r['LocalTime']), r['SeqNo']))
        times = [ms(r['UpdateTime']) for r in quotes]
        table = pq.read_table(events['path'], columns=['RecordType', 'EventTime', 'EventSeqNo', 'Price', 'Qty'])
        table = table.filter(pc.equal(table['RecordType'], 2))
        event_ms = table['EventTime'].cast(pa.int32())
        regular_mask = pc.or_(pc.equal(event_ms, 33900000), pc.or_(pc.and_(pc.greater_equal(event_ms, 34200000), pc.less_equal(event_ms, 41400000)),
                                                                       pc.and_(pc.greater_equal(event_ms, 46800000), pc.less_equal(event_ms, 54000000))))
        table = table.filter(regular_mask)
        trades = table.to_pylist()
        regular_volume = sum(r['Qty'] for r in trades)
        regular_amount = D(sum(r['Price'] * r['Qty'] for r in trades)) / 10000
        assert regular_volume > 0
        assert all(r['Qty'] > 0 and r['Price'] > 0 for r in trades)
        candidates = [r for r, t in zip(quotes, times) if 54000000 <= t <= 54010000
                      and (r[phase_field].strip() == 'CLOSE' if venue == 'SH' else r[phase_field].strip().startswith('E'))
                      and dec(r[volume_field]) == regular_volume and dec(r['Turnover']) == regular_amount and dec(r['LastPrice']) > 0]
        closing = candidates[0] if candidates else None
        raw_path = Path(codes[symbol]['path'])
        assert sha(raw_path) == codes[symbol]['sha256']
        original = pq.read_table(raw_path).to_pylist()
        assert len(original) == 240
        models = {'prior_boundary_open_with_explicit_closing_quote': [], 'session_open_quote_with_explicit_closing_quote': []}
        previous_volume = D(0)
        failure = None
        for i, left in enumerate(GRID):
            end = (left + 1) * 60000
            first = bisect.bisect_right(times, left * 60000)
            stop = bisect.bisect_right(times, end)
            before = quotes[first - 1]
            final = quotes[stop - 1]
            observed = [r for r in quotes[first:stop] if dec(r[volume_field]) > 0 and dec(r['LastPrice']) > 0]
            if left == 899 and closing is not None:
                final = closing
                if closing not in observed:
                    observed.append(closing)
            if not observed or dec(final['LastPrice']) <= 0:
                failure = {'left_minute': left, 'reason': 'no_valid_observed_interval_quotes'}
                break
            volume = dec(final[volume_field]) - previous_volume
            previous_volume = dec(final[volume_field])
            assert volume >= 0 and volume == volume.to_integral_value()
            sampled = [dec(r['LastPrice']) for r in observed]
            prior_open = dec(final['OpenPrice']) if left == 570 else dec(before['LastPrice'])
            session_open = prior_open
            session_before = before
            opening_quote = None
            if left == 780:
                at_or_after = bisect.bisect_left(times, left * 60000)
                opening_quotes = [r for r in quotes[at_or_after:stop] if dec(r[volume_field]) > 0 and dec(r['LastPrice']) > 0]
                assert opening_quotes
                opening_quote = opening_quotes[0]
                session_open = dec(opening_quote['LastPrice'])
                session_before = quotes[at_or_after - 1]
            for name, op, baseline in [
                ('prior_boundary_open_with_explicit_closing_quote', prior_open, before),
                ('session_open_quote_with_explicit_closing_quote', session_open, session_before),
            ]:
                hi, lo = max([op] + sampled), min([op] + sampled)
                if left == 570 or dec(final['HighPrice']) > dec(baseline['HighPrice']):
                    hi = max(hi, dec(final['HighPrice']))
                if left == 570 or dec(final['LowPrice']) < dec(baseline['LowPrice']):
                    lo = min(lo, dec(final['LowPrice']))
                values = [op, hi, lo, dec(final['LastPrice']), volume]
                same = {f: v == dec(original[i][f]) for f, v in zip(FIELDS, values)}
                row = {'symbol': symbol, 'definition': name, 'left_minute': left,
                       **{f: str(v) for f, v in zip(FIELDS, values)}, 'exact_original_fields': same,
                       'end_quote_UpdateTime': final['UpdateTime'], 'end_quote_LocalTime': final['LocalTime'],
                       'end_quote_seq_no': str(final['SeqNo']), 'explicit_closing_phase_quote_used': left == 899 and closing is not None,
                       'opening_session_quote_UpdateTime': opening_quote['UpdateTime'] if left == 780 and opening_quote is not None else None}
                models[name].append(row)
        counts = {name: {'minutes': len(rows), 'all_five_fields_exact_minutes': sum(all(r['exact_original_fields'].values()) for r in rows),
                         'exact_minutes_by_field': {f: sum(r['exact_original_fields'][f] for r in rows) for f in FIELDS},
                         'failed_left_labels': [r['left_minute'] for r in rows if not all(r['exact_original_fields'].values())]} for name, rows in models.items()}
        for rows in models.values():
            generated.extend(rows)
        summaries.append({'symbol': symbol, 'venue': venue, 'snapshot_volume_field': volume_field,
                          'regular_actual_trade_volume_shares': regular_volume, 'regular_calculated_actual_trade_amount_yuan': str(regular_amount),
                          'explicit_closing_quote_found': closing is not None,
                          'closing_quote_UpdateTime': closing['UpdateTime'] if closing else None,
                          'closing_quote_LocalTime': closing['LocalTime'] if closing else None,
                          'closing_quote_volume_and_turnover_exact_regular_actual_trades': closing is not None,
                          'interval_failure': failure, 'models': counts})
        if time.monotonic() - last >= 20:
            print(json.dumps({'phase': 'native_quote_original_controls', 'controls_checked': len(summaries),
                              'native_diagnostic_minutes': len(generated), 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
            last = time.monotonic()
    diagnostic_path = OUT / 'all_499_quote_definitions_vs_original_minutes.jsonl'
    assert not diagnostic_path.exists()
    with diagnostic_path.open('w') as f:
        for row in generated:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'complete_native_member_verification_sha256': sha(native_proof_path), 'all_original_control_verification_sha256': sha(original_proof_path),
              'native_member_acquisition_sha256': sha(native_receipt_path), 'all_original_reference_audit_sha256': sha(original_audit_path),
              'scope_date': '2026-09-23', 'all_complete_original_stockday_controls_checked': 499,
              'stockdays_with_corroborated_explicit_closing_phase_quote': sum(r['explicit_closing_quote_found'] for r in summaries),
              'models_total': {name: {'observed_minutes': sum(r['models'][name]['minutes'] for r in summaries),
                                    'all_five_fields_exact_minutes': sum(r['models'][name]['all_five_fields_exact_minutes'] for r in summaries),
                                    'stockdays_all_240_minutes_all_five_fields_exact': sum(r['models'][name]['all_five_fields_exact_minutes'] == 240 for r in summaries),
                                    'exact_minutes_by_field': {f: sum(r['models'][name]['exact_minutes_by_field'][f] for r in summaries) for f in FIELDS}} for name in models},
              'diagnostic_minute_jsonl_path': str(diagnostic_path), 'diagnostic_minute_jsonl_sha256': sha(diagnostic_path), 'records': summaries,
              'all_source_quantity_values_used_without_unit_conversion_or_rounding': True,
              'closing_quotes_kept_with_actual_later_reported_and_received_times': True,
              'quantity_prefix_or_exact_nominal_signal_PIT_admission_certified': False,
              'original_strict_coarse_gates_not_changed_or_bypassed': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
