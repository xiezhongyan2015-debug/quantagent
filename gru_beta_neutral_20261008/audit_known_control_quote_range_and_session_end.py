"""Test known quote-candle range gates using real, unsplit actual trade prefixes.

Original amount is absent: calculated turnover below belongs to the native
actual trade prefix only, and is never written into an original minute file.
The session-end rule is one further fixed diagnostic for all controls.
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
PREFIX = WHOLE / 'full_union_trade_prefix_audit'
OUT = WHOLE / 'known_control_quote_range_and_session_end'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ms(value):
    h, m, s = value.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(D(s) * 1000)


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'known_original_quote_ranges_and_fixed_session_end_diagnostics.json'
    row_path = OUT / 'all_119760_known_original_trade_prefix_and_session_end_diagnostics.jsonl'
    assert not output.exists() and not row_path.exists()
    proof_path = PREFIX / 'independent_full_union_trade_prefix_verification.json'
    proof = json.loads(proof_path.read_text())
    assert proof['passed'] and proof['all_original_observed_quantity_cutpoint_witnesses_verified'] == 119760
    prefix_path = PREFIX / 'full_union_native_trade_prefix_diagnostics.json'
    assert sha(prefix_path) == proof['audited_full_union_trade_prefix_receipt_sha256']
    prefix = {r['symbol']: r for r in json.loads(prefix_path.read_text())['records']}
    original_path = ORIGINAL / 'all_original_control_reference_audit.json'
    originals = json.loads(original_path.read_text())['records']
    native_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    native_receipt = json.loads(native_receipt_path.read_text())
    leaves = {(r['symbol'], r['kind']): r for r in native_receipt['records']}
    summaries, five_rows, source_definitions = [], [], []
    started = last = time.monotonic()
    with row_path.open('w') as output_rows:
        for ref in originals:
            symbol, venue = ref['symbol'], ref['symbol'][-2:]
            assert sha(ref['path']) == ref['sha256']
            original = pq.ParquetFile(ref['path']).read(columns=FIELDS).to_pylist()
            for kind in ['events', 'snapshot']:
                assert sha(leaves[symbol, kind]['path']) == leaves[symbol, kind]['sha256']
            t = pq.ParquetFile(leaves[symbol, 'events']['path']).read(columns=['RecordType', 'EventTime', 'EventSeqNo', 'ChannelNo', 'Price', 'Qty'])
            t = t.filter(pc.equal(t['RecordType'], 2))
            times = t['EventTime'].cast(pa.int32()).to_pylist()
            trades = sorted([(tt, r) for tt, r in zip(times, t.to_pylist()) if tt == 33900000 or 34200000 <= tt <= 41400000 or 46800000 <= tt <= 54000000],
                            key=lambda v: (v[0], v[1]['EventSeqNo'], v[1]['ChannelNo']))
            cq, ca = [0], [0]
            for tt, r in trades:
                cq.append(cq[-1] + r['Qty'])
                ca.append(ca[-1] + r['Qty'] * r['Price'])
            known_leaf = prefix[symbol]['original_control_witnesses']
            assert sha(known_leaf['path']) == known_leaf['sha256']
            known = pq.ParquetFile(known_leaf['path']).read().to_pylist()
            vf, phase = ('TradVolume', 'InstruStatus') if venue == 'SH' else ('Volume', 'TradingPhaseCode')
            quotes = pq.ParquetFile(leaves[symbol, 'snapshot']['path']).read(columns=['UpdateTime', 'LocalTime', 'SeqNo', vf, 'Turnover', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', phase]).to_pylist()
            quotes.sort(key=lambda q: (ms(q['UpdateTime']), ms(q['LocalTime']), q['SeqNo']))
            qt = [ms(q['UpdateTime']) for q in quotes]
            qv = [{f: D(str(q[f])) for f in [vf, 'Turnover', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice']} for q in quotes]
            am_cut = bisect.bisect_right([tt for tt, r in trades], 41400000)
            morning_candidates = [i for i, tt in enumerate(qt) if 41400000 <= tt <= 41410000 and qv[i][vf] == cq[am_cut]
                                  and qv[i]['Turnover'] == D(ca[am_cut]) / 10000 and qv[i]['LastPrice'] == D(trades[am_cut - 1][1]['Price']) / 10000]
            closing_candidates = [i for i, tt in enumerate(qt) if 54000000 <= tt <= 54010000 and qv[i][vf] == cq[-1]
                                  and qv[i]['Turnover'] == D(ca[-1]) / 10000 and qv[i]['LastPrice'] == D(trades[-1][1]['Price']) / 10000
                                  and (quotes[i][phase].strip() == 'CLOSE' if venue == 'SH' else quotes[i][phase].strip().startswith('E'))]
            assert morning_candidates and closing_candidates
            original_total, before_cut, previous_quote_qty = 0, 0, D(0)
            stock_rows = []
            for index, (minute, obs, witness) in enumerate(zip(GRID, original, known)):
                assert witness['symbol'] == symbol and witness['left_minute'] == minute
                original_total += obs['volume']
                actual_cut = witness['observed_reference_prefix_count']
                assert cq[actual_cut] == original_total and witness['whole_actual_trade_quantity_cutpoint_exact']
                actual_amount = D(ca[actual_cut] - ca[before_cut]) / 10000
                low, high, volume = D(str(obs['low'])), D(str(obs['high'])), D(str(obs['volume']))
                range_gate = low * volume - 1 <= actual_amount <= high * volume + 1
                matching_quotes_anywhere = [j for j, value in enumerate(qv) if value[vf] == original_total and value['LastPrice'] == D(str(obs['close']))]
                left, end = minute * 60000, (minute + 1) * 60000
                lower = bisect.bisect_left(qt, left) if venue == 'SH' else bisect.bisect_right(qt, left)
                upper = bisect.bisect_left(qt, end) if venue == 'SH' else bisect.bisect_right(qt, end)
                final = morning_candidates[0] if minute == 689 else closing_candidates[0] if minute == 899 else upper - 1
                before = lower - 1
                observed = [i for i in range(lower, upper) if qv[i][vf] > 0 and qv[i]['LastPrice'] > 0]
                if minute in [689, 899] and final not in observed:
                    observed.append(final)
                assert observed
                opening = qv[final]['OpenPrice'] if minute == 570 else qv[before]['LastPrice']
                opening_q = None
                if minute == 780:
                    first_at_open = bisect.bisect_left(qt, left)
                    opening_q = next(j for j in range(first_at_open, upper) if qv[j][vf] > 0 and qv[j]['LastPrice'] > 0)
                    opening = qv[opening_q]['LastPrice']
                    before = first_at_open - 1
                sampled = [qv[j]['LastPrice'] for j in observed]
                hi, lo = max([opening] + sampled), min([opening] + sampled)
                if minute == 570 or qv[final]['HighPrice'] > qv[before]['HighPrice']:
                    hi = max(hi, qv[final]['HighPrice'])
                if minute == 570 or qv[final]['LowPrice'] < qv[before]['LowPrice']:
                    lo = min(lo, qv[final]['LowPrice'])
                native_values = [opening, hi, lo, qv[final]['LastPrice'], qv[final][vf] - previous_quote_qty]
                assert native_values[-1] >= 0
                previous_quote_qty = qv[final][vf]
                row = {'symbol': symbol, 'left_minute': minute, 'observed_original_volume': obs['volume'],
                       'observed_original_low': str(low), 'observed_original_high': str(high),
                       'native_actual_trade_prefix_start': before_cut, 'native_actual_trade_prefix_end': actual_cut,
                       'native_actual_trade_prefix_calculated_turnover': str(actual_amount),
                       'unchanged_low_times_quantity_minus_one_to_high_times_quantity_plus_one_gate': range_gate,
                       'amount_gate_excess_above_upper_yuan': str(max(D(0), actual_amount - high * volume - 1)),
                       'amount_gate_excess_below_lower_yuan': str(max(D(0), low * volume - 1 - actual_amount)),
                       'known_reference_cum_qty_and_close_match_any_retained_native_quote': bool(matching_quotes_anywhere),
                       'fixed_source_quote_session_end_values': {f: str(v) for f, v in zip(FIELDS, native_values)},
                       'fixed_source_quote_session_end_exact_original_fields': {f: v == D(str(obs[f])) for f, v in zip(FIELDS, native_values)},
                       'fixed_source_end_quote_UpdateTime': quotes[final]['UpdateTime'], 'fixed_source_end_quote_LocalTime': quotes[final]['LocalTime'],
                       'original_minute_amount_was_not_available_or_created': True}
                stock_rows.append(row)
                output_rows.write(json.dumps(row, ensure_ascii=False) + '\n')
                before_cut = actual_cut
            per_five = []
            for index in range(0, 240, 5):
                block = original[index:index + 5]
                observations = stock_rows[index:index + 5]
                qty = sum(D(str(r['volume'])) for r in block)
                lo, hi = min(D(str(r['low'])) for r in block), max(D(str(r['high'])) for r in block)
                amount = sum(D(r['native_actual_trade_prefix_calculated_turnover']) for r in observations)
                per_five.append(lo * qty - 1 <= amount <= hi * qty + 1)
            counts = {'minutes': 240, 'all_five_fields_exact_minutes': sum(all(r['fixed_source_quote_session_end_exact_original_fields'].values()) for r in stock_rows),
                      'exact_minutes_by_field': {f: sum(r['fixed_source_quote_session_end_exact_original_fields'][f] for r in stock_rows) for f in FIELDS}}
            summary = {'symbol': symbol, 'venue': venue, 'original_source_sha256': ref['sha256'], 'event_source_sha256': leaves[symbol, 'events']['sha256'],
                       'snapshot_source_sha256': leaves[symbol, 'snapshot']['sha256'], 'known_prefix_witness_sha256': known_leaf['sha256'],
                       'native_morning_trade_quantity': cq[am_cut], 'native_morning_calculated_trade_amount': str(D(ca[am_cut]) / 10000),
                       'corroborated_morning_end_quote_UpdateTime': quotes[morning_candidates[0]]['UpdateTime'],
                       'corroborated_morning_end_quote_LocalTime': quotes[morning_candidates[0]]['LocalTime'],
                       'original_quote_minutes_failing_unchanged_actual_amount_price_range_gate': sum(not r['unchanged_low_times_quantity_minus_one_to_high_times_quantity_plus_one_gate'] for r in stock_rows),
                       'original_aggregated_quote_five_minutes_failing_unchanged_actual_amount_price_range_gate': per_five.count(False),
                       'observed_original_cum_qty_and_close_without_any_exact_retained_native_quote': sum(not r['known_reference_cum_qty_and_close_match_any_retained_native_quote'] for r in stock_rows),
                       'fixed_source_quote_with_corroborated_both_session_ends_vs_original': counts}
            summaries.append(summary)
            if time.monotonic() - last >= 20:
                print(json.dumps({'known_controls_checked': len(summaries), 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                last = time.monotonic()
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'full_union_native_trade_prefix_verification_sha256': sha(proof_path), 'full_union_native_trade_prefix_diagnostics_sha256': sha(prefix_path),
              'all_original_reference_audit_sha256': sha(original_path), 'native_member_acquisition_sha256': sha(native_receipt_path),
              'all_original_stockday_controls_checked': len(summaries), 'known_original_minutes_checked': 240 * len(summaries),
              'known_original_five_minute_aggregates_checked': 48 * len(summaries),
              'known_original_minutes_failing_unchanged_actual_amount_price_range_gate': sum(r['original_quote_minutes_failing_unchanged_actual_amount_price_range_gate'] for r in summaries),
              'known_original_five_minutes_failing_unchanged_actual_amount_price_range_gate': sum(r['original_aggregated_quote_five_minutes_failing_unchanged_actual_amount_price_range_gate'] for r in summaries),
              'known_original_cum_qty_and_close_without_any_exact_retained_native_quote': sum(r['observed_original_cum_qty_and_close_without_any_exact_retained_native_quote'] for r in summaries),
              'fixed_quote_session_end_rule_all_five_fields_exact_minutes': sum(r['fixed_source_quote_with_corroborated_both_session_ends_vs_original']['all_five_fields_exact_minutes'] for r in summaries),
              'fixed_quote_session_end_rule_all_240_minutes_exact_stockdays': sum(r['fixed_source_quote_with_corroborated_both_session_ends_vs_original']['all_five_fields_exact_minutes'] == 240 for r in summaries),
              'fixed_quote_session_end_rule_exact_minutes_by_field': {f: sum(r['fixed_source_quote_with_corroborated_both_session_ends_vs_original']['exact_minutes_by_field'][f] for r in summaries) for f in FIELDS},
              'diagnostic_minute_jsonl_path': str(row_path), 'diagnostic_minute_jsonl_sha256': sha(row_path), 'records': summaries,
              'fixed_boundary_rule': 'SH [left,end), SZ (left,end]; observed afternoon opening quote; whole actual-trade-corroborated source closing quotes for morning and close sessions',
              'price_quantity_amount_range_test_is_of_known_reference_quotes_and_actual_prefix_not_original_amount': True,
              'historical_native_sources_not_qualified_by_this_counterexample': True,
              'original_failed_amount_or_coarse_gates_not_changed_or_bypassed': True,
              'no_per_stock_or_minute_favorable_rule_choice': True, 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
