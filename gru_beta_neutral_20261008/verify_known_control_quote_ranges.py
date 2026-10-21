"""Verify actual-prefix range counterexamples and the fixed session-end change.

Reuse independently verified previous candle cells for unaffected minutes;
recompute both changed session boundaries directly from the native source.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
WHOLE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
OUT = WHOLE / 'known_control_quote_range_and_session_end'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def clock(value):
    h, m, s = value.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(D(s) * 1000)


def main():
    output = OUT / 'independent_known_control_quote_range_verification.json'
    assert not output.exists()
    receipt_path = OUT / 'known_original_quote_ranges_and_fixed_session_end_diagnostics.json'
    receipt = json.loads(receipt_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_known_control_quote_range_and_session_end.py') == receipt['source_code_sha256']
    phase_proof_path = WHOLE / 'quote_phase_control_audit/independent_phase_and_fixed_venue_boundary_verification.json'
    phase_proof = json.loads(phase_proof_path.read_text()); assert phase_proof['passed']
    previous_rows_path = Path(phase_proof['fixed_venue_boundary_minute_path'])
    assert sha(previous_rows_path) == phase_proof['fixed_venue_boundary_minute_sha256']
    previous = {}
    with previous_rows_path.open() as f:
        for line in f:
            row = json.loads(line); previous.setdefault(row['symbol'], []).append(row)
    native_receipt_path = WHOLE / 'full_union_day_native_members/complete_native_member_acquisition.json'
    original_audit_path = WHOLE / 'all_original_day_controls/all_original_control_reference_audit.json'
    prefix_proof_path = WHOLE / 'full_union_trade_prefix_audit/independent_full_union_trade_prefix_verification.json'
    assert sha(native_receipt_path) == receipt['native_member_acquisition_sha256']
    assert sha(original_audit_path) == receipt['all_original_reference_audit_sha256']
    assert sha(prefix_proof_path) == receipt['full_union_native_trade_prefix_verification_sha256']
    assert json.loads(prefix_proof_path.read_text())['passed']
    leaves = {(r['symbol'], r['kind']): r for r in json.loads(native_receipt_path.read_text())['records']}
    originals = {r['symbol']: r for r in json.loads(original_audit_path.read_text())['records']}
    row_path = Path(receipt['diagnostic_minute_jsonl_path']); assert sha(row_path) == receipt['diagnostic_minute_jsonl_sha256']
    checked, failed, five_failed, exact_minutes, exact_days = 0, 0, 0, 0, 0
    field_totals = dict.fromkeys(FIELDS, 0)
    counterexamples = []
    with row_path.open() as saved:
        for summary in receipt['records']:
            symbol = summary['symbol']; venue = symbol[-2:]
            ref = originals[symbol]; assert sha(ref['path']) == ref['sha256'] == summary['original_source_sha256']
            original = pq.ParquetFile(ref['path']).read(columns=FIELDS).to_pylist()
            for kind in ['events', 'snapshot']:
                assert sha(leaves[symbol, kind]['path']) == leaves[symbol, kind]['sha256'] == summary['event_source_sha256' if kind == 'events' else 'snapshot_source_sha256']
            t = pq.ParquetFile(leaves[symbol, 'events']['path']).read(columns=['RecordType', 'EventTime', 'EventSeqNo', 'ChannelNo', 'Price', 'Qty'])
            t = t.filter(pc.equal(t['RecordType'], 2))
            tm = t['EventTime'].cast(pa.int32()).to_numpy()
            mask = (tm == 33900000) | ((tm >= 34200000) & (tm <= 41400000)) | ((tm >= 46800000) & (tm <= 54000000))
            t = t.filter(pa.array(mask)); tm = t['EventTime'].cast(pa.int32()).to_numpy()
            seq, chan, p, v = [t[k].to_numpy() for k in ['EventSeqNo', 'ChannelNo', 'Price', 'Qty']]
            order = np.lexsort((chan, seq, tm)); tm, p, v = tm[order], p[order], v[order]
            cq, ca = np.concatenate(([0], np.cumsum(v))), np.concatenate(([0], np.cumsum(p * v)))
            cumul = np.cumsum([r['volume'] for r in original]); cuts = np.searchsorted(cq, cumul)
            assert np.array_equal(cq[cuts], cumul)
            vf = 'TradVolume' if venue == 'SH' else 'Volume'
            quotes = pq.ParquetFile(leaves[symbol, 'snapshot']['path']).read(columns=['UpdateTime', 'LocalTime', 'SeqNo', vf, 'Turnover', 'LastPrice', 'HighPrice', 'LowPrice']).to_pylist()
            quotes.sort(key=lambda q: (clock(q['UpdateTime']), clock(q['LocalTime']), q['SeqNo']))
            qt = np.array([clock(q['UpdateTime']) for q in quotes])
            values = [{k: D(str(q[k])) for k in [vf, 'Turnover', 'LastPrice', 'HighPrice', 'LowPrice']} for q in quotes]
            am_cut = int(np.searchsorted(tm, 41400000, side='right'))
            am_qty, am_amount = int(cq[am_cut]), D(int(ca[am_cut])) / 10000
            am_index = next(i for i, (tt, q) in enumerate(zip(qt, values)) if 41400000 <= tt <= 41410000 and q[vf] == am_qty and q['Turnover'] == am_amount and q['LastPrice'] == D(int(p[am_cut - 1])) / 10000)
            assert summary['native_morning_trade_quantity'] == am_qty and D(summary['native_morning_calculated_trade_amount']) == am_amount
            assert summary['corroborated_morning_end_quote_UpdateTime'] == quotes[am_index]['UpdateTime']
            assert summary['corroborated_morning_end_quote_LocalTime'] == quotes[am_index]['LocalTime']
            any_pairs = {(q[vf], q['LastPrice']) for q in values}
            prior_rows = previous[symbol]
            old_am_qty = sum(D(r['volume']) for r in prior_rows[:120])
            previous_cut = 0
            day_failed, day_five_failed, day_exact = 0, 0, 0
            actual_amounts = []
            for index, (left, obs, prior, cut) in enumerate(zip(GRID, original, prior_rows, cuts)):
                w = json.loads(next(saved)); assert w['symbol'] == symbol and w['left_minute'] == left
                cut = int(cut); amt = D(int(ca[cut] - ca[previous_cut])) / 10000
                low, high, qty = D(str(obs['low'])), D(str(obs['high'])), D(str(obs['volume']))
                assert w['observed_original_volume'] == obs['volume'] and D(w['observed_original_low']) == low and D(w['observed_original_high']) == high
                assert w['native_actual_trade_prefix_start'] == previous_cut and w['native_actual_trade_prefix_end'] == cut
                assert D(w['native_actual_trade_prefix_calculated_turnover']) == amt
                range_gate = low * qty - 1 <= amt <= high * qty + 1
                assert w['unchanged_low_times_quantity_minus_one_to_high_times_quantity_plus_one_gate'] == range_gate
                assert D(w['amount_gate_excess_above_upper_yuan']) == max(D(0), amt - high * qty - 1)
                assert D(w['amount_gate_excess_below_lower_yuan']) == max(D(0), low * qty - 1 - amt)
                assert (D(int(cumul[index])), D(str(obs['close']))) in any_pairs
                assert w['known_reference_cum_qty_and_close_match_any_retained_native_quote'] and w['original_minute_amount_was_not_available_or_created']
                expected = {f: D(prior[f]) for f in FIELDS}
                expected_reported, expected_local = prior['end_quote_UpdateTime'], prior['end_quote_LocalTime']
                if left == 689:
                    lower = int(np.searchsorted(qt, left * 60000, side='left' if venue == 'SH' else 'right'))
                    upper = int(np.searchsorted(qt, 41400000, side='left' if venue == 'SH' else 'right'))
                    observed = [values[j]['LastPrice'] for j in range(lower, upper) if values[j][vf] > 0 and values[j]['LastPrice'] > 0]
                    observed.append(values[am_index]['LastPrice'])
                    op = expected['open']; hi, lo = max([op] + observed), min([op] + observed)
                    if values[am_index]['HighPrice'] > values[lower - 1]['HighPrice']:
                        hi = max(hi, values[am_index]['HighPrice'])
                    if values[am_index]['LowPrice'] < values[lower - 1]['LowPrice']:
                        lo = min(lo, values[am_index]['LowPrice'])
                    expected.update(high=hi, low=lo, close=values[am_index]['LastPrice'], volume=D(am_qty) - sum(D(r['volume']) for r in prior_rows[:119]))
                    expected_reported, expected_local = quotes[am_index]['UpdateTime'], quotes[am_index]['LocalTime']
                elif left == 780:
                    expected['volume'] += old_am_qty - am_qty
                assert all(expected[f] == D(w['fixed_source_quote_session_end_values'][f]) for f in FIELDS), (symbol, left)
                assert w['fixed_source_end_quote_UpdateTime'] == expected_reported and w['fixed_source_end_quote_LocalTime'] == expected_local
                exact = {f: expected[f] == D(str(obs[f])) for f in FIELDS}
                assert exact == w['fixed_source_quote_session_end_exact_original_fields']
                for f in FIELDS:
                    field_totals[f] += exact[f]
                day_exact += all(exact.values()); day_failed += not range_gate
                actual_amounts.append(amt)
                if not range_gate and len(counterexamples) < 12:
                    counterexamples.append({'symbol': symbol, 'left_minute': left, 'original_quote_low': str(low), 'original_quote_high': str(high),
                                            'original_observed_quantity': str(qty), 'native_actual_prefix_calculated_turnover': str(amt),
                                            'excess_above_upper_yuan': w['amount_gate_excess_above_upper_yuan'], 'excess_below_lower_yuan': w['amount_gate_excess_below_lower_yuan']})
                previous_cut = cut; checked += 1
            for index in range(0, 240, 5):
                lo = min(D(str(r['low'])) for r in original[index:index + 5]); hi = max(D(str(r['high'])) for r in original[index:index + 5])
                qty = sum(D(str(r['volume'])) for r in original[index:index + 5]); amt = sum(actual_amounts[index:index + 5])
                day_five_failed += not lo * qty - 1 <= amt <= hi * qty + 1
            assert day_failed == summary['original_quote_minutes_failing_unchanged_actual_amount_price_range_gate']
            assert day_five_failed == summary['original_aggregated_quote_five_minutes_failing_unchanged_actual_amount_price_range_gate']
            assert day_exact == summary['fixed_source_quote_with_corroborated_both_session_ends_vs_original']['all_five_fields_exact_minutes']
            failed += day_failed; five_failed += day_five_failed; exact_minutes += day_exact; exact_days += day_exact == 240
        assert not saved.read()
    assert checked == receipt['known_original_minutes_checked'] == 119760
    assert failed == receipt['known_original_minutes_failing_unchanged_actual_amount_price_range_gate'] == 2326
    assert five_failed == receipt['known_original_five_minutes_failing_unchanged_actual_amount_price_range_gate'] == 26
    assert exact_minutes == receipt['fixed_quote_session_end_rule_all_five_fields_exact_minutes'] == 95907
    assert exact_days == receipt['fixed_quote_session_end_rule_all_240_minutes_exact_stockdays'] == 357
    assert field_totals == receipt['fixed_quote_session_end_rule_exact_minutes_by_field']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__), 'passed': True,
              'audited_known_original_range_receipt_sha256': sha(receipt_path), 'previous_fixed_venue_rule_independent_proof_sha256': sha(phase_proof_path),
              'full_union_native_trade_prefix_verification_sha256': sha(prefix_proof_path),
              'all_119760_diagnostic_rows_independently_reproduced': True,
              'known_quote_minutes_rejected_by_unchanged_actual_amount_range_gate': failed,
              'known_quote_five_minute_aggregates_rejected_by_unchanged_actual_amount_range_gate': five_failed,
              'all_original_cumulative_quantities_and_closes_match_some_actual_retained_source_quote': True,
              'fixed_quote_session_end_all_five_fields_exact_minutes': exact_minutes, 'fixed_quote_session_end_all_240_exact_stockdays': exact_days,
              'verified_actual_data_counterexamples': counterexamples, 'original_minute_amount_not_created': True,
              'historical_failed_amount_or_coarse_gates_not_changed_or_bypassed': True,
              'historical_native_quantity_precision_or_source_admission_not_established': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'verified_actual_data_counterexamples'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
