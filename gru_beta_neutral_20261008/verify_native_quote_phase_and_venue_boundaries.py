"""Independently reproduce saved phase diagnostics, then test a fixed venue rule.

No stock-specific boundary tuning, missing quantity inference or panel mutation.
The Shanghai half-open rule is a further diagnostic, not an admitted definition.
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
WHOLE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
NATIVE = WHOLE / 'full_union_day_native_members'
ORIGINAL = WHOLE / 'all_original_day_controls'
OUT = WHOLE / 'quote_phase_control_audit'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume']
OLD_NAMES = ['prior_boundary_open_with_explicit_closing_quote', 'session_open_quote_with_explicit_closing_quote']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def clock_ms(value):
    hh, mm, ss = value.split(':')
    return int(hh) * 3600000 + int(mm) * 60000 + int(D(ss) * 1000)


def main():
    receipt_path = OUT / 'independent_phase_and_fixed_venue_boundary_verification.json'
    minute_path = OUT / 'fixed_venue_boundary_vs_all_499_original_minutes.jsonl'
    assert not receipt_path.exists() and not minute_path.exists()
    old_path = OUT / 'all_499_native_quote_phase_control_diagnostics.json'
    old = json.loads(old_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_native_quote_phase_controls.py') == old['source_code_sha256']
    old_minutes = Path(old['diagnostic_minute_jsonl_path'])
    assert sha(old_minutes) == old['diagnostic_minute_jsonl_sha256']
    native_proof_path = NATIVE / 'independent_complete_native_member_verification.json'
    original_proof_path = ORIGINAL / 'independent_all_original_control_verification.json'
    assert sha(native_proof_path) == old['complete_native_member_verification_sha256']
    assert sha(original_proof_path) == old['all_original_control_verification_sha256']
    assert json.loads(native_proof_path.read_text())['passed']
    assert json.loads(original_proof_path.read_text())['passed']
    native_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    original_audit_path = ORIGINAL / 'all_original_control_reference_audit.json'
    assert sha(native_receipt_path) == old['native_member_acquisition_sha256']
    assert sha(original_audit_path) == old['all_original_reference_audit_sha256']
    leaves = {(r['symbol'], r['kind']): r for r in json.loads(native_receipt_path.read_text())['records']}
    originals = {r['symbol']: r for r in json.loads(original_audit_path.read_text())['records']}
    old_records = {r['symbol']: r for r in old['records']}
    summary, new_records = [], []
    started = last = time.monotonic()
    with old_minutes.open() as saved, minute_path.open('w') as new_file:
        for symbol in sorted(originals):
            venue = symbol[-2:]
            vf = 'TradVolume' if venue == 'SH' else 'Volume'
            pf = 'InstruStatus' if venue == 'SH' else 'TradingPhaseCode'
            for kind in ['events', 'snapshot']:
                assert sha(leaves[symbol, kind]['path']) == leaves[symbol, kind]['sha256']
            ref = originals[symbol]
            assert sha(ref['path']) == ref['sha256']
            original = pq.read_table(ref['path'], columns=FIELDS).to_pylist()
            quotes = pq.read_table(leaves[symbol, 'snapshot']['path'], columns=['UpdateTime', 'LocalTime', 'SeqNo', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', 'Turnover', vf, pf]).to_pylist()
            quotes.sort(key=lambda r: (clock_ms(r['UpdateTime']), clock_ms(r['LocalTime']), r['SeqNo']))
            reported = [clock_ms(r['UpdateTime']) for r in quotes]
            values = [{f: D(str(q[f])) for f in ['LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', 'Turnover', vf]} for q in quotes]
            trades = pq.read_table(leaves[symbol, 'events']['path'], columns=['RecordType', 'EventTime', 'Price', 'Qty'])
            trades = trades.filter(pc.equal(trades['RecordType'], 2))
            times = trades['EventTime'].cast(pa.int32()).to_pylist()
            prices = trades['Price'].to_pylist()
            quantities = trades['Qty'].to_pylist()
            selected = [(p, v) for t, p, v in zip(times, prices, quantities) if t == 33900000 or 34200000 <= t <= 41400000 or 46800000 <= t <= 54000000]
            qty = sum(v for p, v in selected)
            amount = D(sum(p * v for p, v in selected)) / 10000
            close_index = next((i for i, (q, v, t) in enumerate(zip(quotes, values, reported))
                                if 54000000 <= t <= 54010000 and (q[pf].strip() == 'CLOSE' if venue == 'SH' else q[pf].strip().startswith('E'))
                                and v[vf] == qty and v['Turnover'] == amount and v['LastPrice'] > 0), None)
            assert close_index is not None
            closing = quotes[close_index]
            assert old_records[symbol]['regular_actual_trade_volume_shares'] == qty
            assert D(old_records[symbol]['regular_calculated_actual_trade_amount_yuan']) == amount
            assert old_records[symbol]['closing_quote_UpdateTime'] == closing['UpdateTime']
            assert old_records[symbol]['closing_quote_LocalTime'] == closing['LocalTime']
            all_rows = {name: [] for name in OLD_NAMES + ['fixed_venue_SH_left_closed_SZ_right_closed']}
            for name in all_rows:
                cumulative_before = D(0)
                for index, minute in enumerate(GRID):
                    left, end = minute * 60000, (minute + 1) * 60000
                    half_open = name == 'fixed_venue_SH_left_closed_SZ_right_closed' and venue == 'SH'
                    lower = bisect.bisect_left(reported, left) if half_open else bisect.bisect_right(reported, left)
                    upper = bisect.bisect_left(reported, end) if half_open else bisect.bisect_right(reported, end)
                    before = lower - 1
                    final = close_index if minute == 899 else upper - 1
                    observed = [j for j in range(lower, upper) if values[j][vf] > 0 and values[j]['LastPrice'] > 0]
                    if minute == 899 and close_index not in observed:
                        observed.append(close_index)
                    assert observed
                    op = values[final]['OpenPrice'] if minute == 570 else values[before]['LastPrice']
                    session_quote = None
                    if minute == 780:
                        after_open = bisect.bisect_left(reported, left)
                        session_quote = next(j for j in range(after_open, upper) if values[j][vf] > 0 and values[j]['LastPrice'] > 0)
                        if name != OLD_NAMES[0]:
                            op = values[session_quote]['LastPrice']
                            before = after_open - 1
                    observed_prices = [values[j]['LastPrice'] for j in observed]
                    hi, lo = max([op] + observed_prices), min([op] + observed_prices)
                    if minute == 570 or values[final]['HighPrice'] > values[before]['HighPrice']:
                        hi = max(hi, values[final]['HighPrice'])
                    if minute == 570 or values[final]['LowPrice'] < values[before]['LowPrice']:
                        lo = min(lo, values[final]['LowPrice'])
                    volume = values[final][vf] - cumulative_before
                    cumulative_before = values[final][vf]
                    assert volume >= 0 and volume == volume.to_integral_value()
                    vals = [op, hi, lo, values[final]['LastPrice'], volume]
                    row = {'symbol': symbol, 'definition': name, 'left_minute': minute,
                           **{f: str(v) for f, v in zip(FIELDS, vals)},
                           'exact_original_fields': {f: v == D(str(original[index][f])) for f, v in zip(FIELDS, vals)},
                           'end_quote_UpdateTime': quotes[final]['UpdateTime'], 'end_quote_LocalTime': quotes[final]['LocalTime'],
                           'end_quote_seq_no': str(quotes[final]['SeqNo']), 'explicit_closing_phase_quote_used': minute == 899,
                           'opening_session_quote_UpdateTime': quotes[session_quote]['UpdateTime'] if session_quote is not None else None}
                    all_rows[name].append(row)
                    if name in OLD_NAMES:
                        assert row == json.loads(next(saved)), (symbol, name, minute)
                    else:
                        new_file.write(json.dumps(row, ensure_ascii=False) + '\n')
            counts = {name: {'minutes': len(rows), 'all_five_fields_exact_minutes': sum(all(v['exact_original_fields'].values()) for v in rows),
                             'exact_minutes_by_field': {f: sum(v['exact_original_fields'][f] for v in rows) for f in FIELDS},
                             'failed_left_labels': [v['left_minute'] for v in rows if not all(v['exact_original_fields'].values())]}
                      for name, rows in all_rows.items()}
            for name in OLD_NAMES:
                assert counts[name] == old_records[symbol]['models'][name]
            summary.append({'symbol': symbol, 'venue': venue, 'models': counts})
            new_records.append({'symbol': symbol, 'venue': venue, **counts['fixed_venue_SH_left_closed_SZ_right_closed']})
            if time.monotonic() - last > 20:
                print(json.dumps({'controls_verified': len(summary), 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                last = time.monotonic()
        assert not saved.read()
    totals = {name: {'observed_minutes': sum(r['models'][name]['minutes'] for r in summary),
                     'all_five_fields_exact_minutes': sum(r['models'][name]['all_five_fields_exact_minutes'] for r in summary),
                     'stockdays_all_240_minutes_all_five_fields_exact': sum(r['models'][name]['all_five_fields_exact_minutes'] == 240 for r in summary),
                     'exact_minutes_by_field': {f: sum(r['models'][name]['exact_minutes_by_field'][f] for r in summary) for f in FIELDS}}
              for name in all_rows}
    for name in OLD_NAMES:
        assert totals[name] == old['models_total'][name]
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'passed': True, 'audited_phase_diagnostic_receipt_sha256': sha(old_path),
              'all_239520_saved_diagnostic_rows_independently_reproduced': True,
              'complete_native_member_verification_sha256': sha(native_proof_path),
              'all_original_control_verification_sha256': sha(original_proof_path),
              'fixed_venue_boundary_minute_path': str(minute_path), 'fixed_venue_boundary_minute_sha256': sha(minute_path),
              'models_total': totals, 'fixed_venue_records': new_records,
              'fixed_venue_definition': 'SH observed quotes [left,end), previous open <left; SZ quotes (left,end], previous open <=left; observed afternoon first quote >=13:00; explicit corroborated closing phase quote',
              'fixed_venue_rule_is_diagnostic_and_not_source_documentation': True,
              'no_rule_selected_per_stock_or_minute': True, 'original_strict_coarse_gates_not_changed_or_bypassed': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'fixed_venue_records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
