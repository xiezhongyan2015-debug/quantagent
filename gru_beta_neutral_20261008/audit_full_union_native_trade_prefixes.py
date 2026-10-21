"""Audit every complete native pair, keeping quantities and clocks unchanged.

Reference cumulative quantities are used only to identify observed whole
trade cutpoints for existing controls. They never infer a missing quantity.
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
OUT = WHOLE / 'full_union_trade_prefix_audit'
GRID = list(range(570, 690)) + list(range(780, 900))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def clock(value):
    h, m, s = value.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(D(s) * 1000)


def regular(ms):
    return ms == 33900000 or 34200000 <= ms <= 41400000 or 46800000 <= ms <= 54000000


def save(rows, path):
    assert not path.exists()
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression='zstd')
    return {'path': str(path), 'sha256': sha(path), 'rows': len(rows)}


def audit(symbol, event_leaf, quote_leaf, original_ref):
    for leaf in [event_leaf, quote_leaf]:
        assert sha(leaf['path']) == leaf['sha256']
    columns = ['Symbol', 'RecordType', 'ChannelNo', 'EventSeqNo', 'EventTime', 'LocalTime', 'Price', 'Qty', 'Amount']
    table = pq.read_table(event_leaf['path'], columns=columns)
    assert table.schema.metadata[b'price_scale'] == b'10000' and table.schema.metadata[b'qty_scale'] == b'1'
    assert table.schema.metadata[b'event_time_unit'] == b'millisecond'
    table = table.filter(pc.equal(table['RecordType'], 2))
    times = table['EventTime'].cast(pa.int32()).to_pylist()
    received = table['LocalTime'].cast(pa.int32()).to_pylist()
    rows = table.to_pylist()
    trades = sorted([(t, l, r) for t, l, r in zip(times, received, rows) if regular(t)],
                    key=lambda x: (x[0], x[2]['EventSeqNo'], x[2]['ChannelNo']))
    outside = [(t, l, r) for t, l, r in zip(times, received, rows) if not regular(t)]
    trade_fields_valid = bool(trades) and all(r['Symbol'] == symbol and r['Price'] is not None and r['Price'] > 0 and r['Qty'] > 0 for _, _, r in trades)
    identity_unique = len({(r['ChannelNo'], r['EventSeqNo']) for _, _, r in trades}) == len(trades)
    assert trade_fields_valid and identity_unique, (symbol, 'native trade quality failure; no outputs admitted')
    cumulative_qty, cumulative_amount, latest_receipt = [0], [0], [0]
    for _, l, r in trades:
        cumulative_qty.append(cumulative_qty[-1] + r['Qty'])
        cumulative_amount.append(cumulative_amount[-1] + r['Price'] * r['Qty'])
        latest_receipt.append(max(latest_receipt[-1], l))
    venue = symbol[-2:]
    vf = 'TradVolume' if venue == 'SH' else 'Volume'
    pf = 'InstruStatus' if venue == 'SH' else 'TradingPhaseCode'
    quotes = pq.read_table(quote_leaf['path'], columns=['SecurityID', 'UpdateTime', 'LocalTime', 'SeqNo', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', 'Turnover', vf, pf]).to_pylist()
    quotes.sort(key=lambda r: (clock(r['UpdateTime']), clock(r['LocalTime']), r['SeqNo']))
    reported = [clock(q['UpdateTime']) for q in quotes]
    quote_volume = [D(str(q[vf])) for q in quotes]
    quote_amount = [D(str(q['Turnover'])) for q in quotes]
    quote_last = [D(str(q['LastPrice'])) for q in quotes]
    quote_qty_groups = {}
    witnesses = []
    for index, (q, t, quantity, amount, price) in enumerate(zip(quotes, reported, quote_volume, quote_amount, quote_last)):
        if quantity <= 0:
            continue
        integral_qty = quantity == quantity.to_integral_value()
        cut = bisect.bisect_left(cumulative_qty, quantity) if integral_qty else len(cumulative_qty)
        cut_exists = 0 < cut < len(cumulative_qty) and cumulative_qty[cut] == quantity
        last = trades[cut - 1] if cut_exists else None
        amount_exact = cut_exists and D(cumulative_amount[cut]) / 10000 == amount
        price_exact = cut_exists and D(last[2]['Price']) / 10000 == price
        quote_qty_groups.setdefault(quantity, []).append(index)
        witnesses.append({'symbol': symbol, 'quote_sorted_index': index, 'quote_seq_no': str(q['SeqNo']),
                          'quote_UpdateTime': q['UpdateTime'], 'quote_LocalTime': q['LocalTime'],
                          'quote_reported_ms': t, 'quote_received_ms': clock(q['LocalTime']),
                          'quote_quantity_source_field': vf, 'quote_quantity': str(quantity), 'quote_Turnover': str(amount), 'quote_LastPrice': str(price),
                          'quote_security_identity_exact': q['SecurityID'] == symbol[:6], 'quote_quantity_is_exact_integer': integral_qty,
                          'whole_trade_quantity_prefix_exact': cut_exists, 'whole_trade_prefix_count': cut if cut_exists else None,
                          'calculated_trade_prefix_amount_exact': amount_exact, 'last_trade_price_exact': price_exact,
                          'last_actual_trade_channel': last[2]['ChannelNo'] if last else None,
                          'last_actual_trade_id': last[2]['EventSeqNo'] if last else None,
                          'last_actual_trade_EventTime_ms': last[0] if last else None,
                          'last_actual_trade_LocalTime_ms': last[1] if last else None,
                          'all_prefix_trades_latest_LocalTime_ms': latest_receipt[cut] if last else None,
                          'last_actual_trade_after_quote_reported_by_ms': last[0] - t if last else None})
    per_symbol = OUT / ('symbol=' + symbol)
    per_symbol.mkdir()
    quote_artifact = save(witnesses, per_symbol / 'all_positive_quote_whole_trade_prefix_witnesses.parquet')
    control_rows = []
    if original_ref is not None:
        assert sha(original_ref['path']) == original_ref['sha256']
        original = pq.read_table(original_ref['path'], columns=['open', 'high', 'low', 'close', 'volume']).to_pylist()
        assert len(original) == 240
        total, prior_cut = 0, 0
        for minute, reference in zip(GRID, original):
            assert reference['volume'] >= 0 and D(str(reference['volume'])) == D(str(reference['volume'])).to_integral_value()
            total += reference['volume']
            cut = bisect.bisect_left(cumulative_qty, total)
            exact = cut < len(cumulative_qty) and cumulative_qty[cut] == total
            last = trades[cut - 1] if exact and cut else None
            end = (minute + 1) * 60000
            same_qty_quotes = quote_qty_groups.get(D(str(total)), [])
            near = [i for i in same_qty_quotes if end - 10000 <= reported[i] <= end + 10000
                    and quote_last[i] == D(str(reference['close']))]
            strict_end = bisect.bisect_left(reported, end) if venue == 'SH' else bisect.bisect_right(reported, end)
            chosen = strict_end - 1
            if minute == 899:
                explicit = [i for i, (q, t) in enumerate(zip(quotes, reported)) if 54000000 <= t <= 54010000
                            and (q[pf].strip() == 'CLOSE' if venue == 'SH' else q[pf].strip().startswith('E'))
                            and quote_volume[i] == cumulative_qty[-1] and quote_amount[i] == D(cumulative_amount[-1]) / 10000 and quote_last[i] > 0]
                chosen = explicit[0] if explicit else chosen
            included = trades[prior_cut:cut] if exact and prior_cut is not None else []
            trade_prices = [D(t[2]['Price']) / 10000 for t in included]
            control_rows.append({'symbol': symbol, 'left_minute': minute, 'original_observed_volume': reference['volume'],
                                 'original_observed_cumulative_volume': total, 'whole_actual_trade_quantity_cutpoint_exact': exact,
                                 'observed_reference_prefix_count': cut if exact else None,
                                 'last_actual_trade_EventTime_ms': last[0] if last else None,
                                 'last_actual_trade_LocalTime_ms': last[1] if last else None,
                                 'last_actual_trade_after_nominal_end_ms': last[0] - end if last else None,
                                 'observed_original_close_exact_last_trade_price': bool(last) and D(str(reference['close'])) == D(last[2]['Price']) / 10000,
                                 'whole_trades_in_observed_original_quantity_delta': len(included) if exact and prior_cut is not None else None,
                                 'native_price_range_with_original_carried_open_within_original_high_low': bool(exact and prior_cut is not None) and
                                     max([D(str(reference['open'])), D(str(reference['close']))] + trade_prices) <= D(str(reference['high'])) and
                                     min([D(str(reference['open'])), D(str(reference['close']))] + trade_prices) >= D(str(reference['low'])),
                                 'native_quotes_with_exact_observed_reference_cum_qty_and_close_near_end': len(near),
                                 'all_matching_quote_UpdateTimes_near_end': [quotes[i]['UpdateTime'] for i in near],
                                 'all_matching_quote_LocalTimes_near_end': [quotes[i]['LocalTime'] for i in near],
                                 'fixed_venue_end_quote_UpdateTime': quotes[chosen]['UpdateTime'],
                                 'fixed_venue_end_quote_quantity': str(quote_volume[chosen]),
                                 'fixed_venue_end_quote_quantity_exact_original_observed_cumulative_volume': quote_volume[chosen] == total,
                                 'reference_cutpoint_diagnostic_only_not_missing_quantity_inference': True})
            prior_cut = cut if exact else None
        control_artifact = save(control_rows, per_symbol / 'all_original_observed_quantity_cutpoint_witnesses.parquet')
    else:
        control_artifact = None
    closing = [i for i, (q, t) in enumerate(zip(quotes, reported)) if 54000000 <= t <= 54010000 and
               (q[pf].strip() == 'CLOSE' if venue == 'SH' else q[pf].strip().startswith('E')) and
               quote_volume[i] == cumulative_qty[-1] and quote_amount[i] == D(cumulative_amount[-1]) / 10000 and
               quote_last[i] == D(trades[-1][2]['Price']) / 10000]
    stats = {'positive_quantity_quotes': len(witnesses),
             'quote_whole_actual_trade_quantity_prefix_exact': sum(w['whole_trade_quantity_prefix_exact'] for w in witnesses),
             'quote_actual_trade_prefix_calculated_amount_exact': sum(w['calculated_trade_prefix_amount_exact'] for w in witnesses),
             'quote_last_actual_trade_price_exact': sum(w['last_trade_price_exact'] for w in witnesses),
             'quote_all_identity_quantity_amount_and_last_price_exact': sum(all(w[k] for k in ['quote_security_identity_exact', 'quote_quantity_is_exact_integer', 'whole_trade_quantity_prefix_exact', 'calculated_trade_prefix_amount_exact', 'last_trade_price_exact']) for w in witnesses),
             'quote_last_actual_trade_after_reported_quote_time': sum(w['last_actual_trade_after_quote_reported_by_ms'] is not None and w['last_actual_trade_after_quote_reported_by_ms'] > 0 for w in witnesses),
             'quote_maximum_last_actual_trade_after_reported_time_ms': max((w['last_actual_trade_after_quote_reported_by_ms'] for w in witnesses if w['last_actual_trade_after_quote_reported_by_ms'] is not None), default=None)}
    control_stats = {'observed_original_minutes': len(control_rows),
                     'observed_original_cum_qty_exact_whole_actual_trade_cutpoints': sum(w['whole_actual_trade_quantity_cutpoint_exact'] for w in control_rows),
                     'observed_original_close_exact_last_prefix_trade_price': sum(w['observed_original_close_exact_last_trade_price'] for w in control_rows),
                     'original_actual_prefix_price_range_contained': sum(w['native_price_range_with_original_carried_open_within_original_high_low'] for w in control_rows),
                     'original_cum_qty_and_close_match_some_real_quote_within_10s_of_end': sum(w['native_quotes_with_exact_observed_reference_cum_qty_and_close_near_end'] > 0 for w in control_rows),
                     'fixed_venue_end_quote_cum_qty_exact_original': sum(w['fixed_venue_end_quote_quantity_exact_original_observed_cumulative_volume'] for w in control_rows)}
    return {'symbol': symbol, 'venue': venue, 'actual_trade_rows': len(rows), 'regular_actual_trade_rows': len(trades),
            'actual_trade_rows_outside_fixed_regular_window': len(outside), 'outside_window_event_times_first_20': [t for t, l, r in outside[:20]],
            'regular_trade_fields_valid': trade_fields_valid, 'regular_trade_identity_unique': identity_unique,
            'regular_trade_channels': sorted({r['ChannelNo'] for _, _, r in trades}),
            'regular_source_Amount_exact_actual_Price_times_Qty_rows': sum(r['Amount'] == r['Price'] * r['Qty'] for _, _, r in trades),
            'regular_actual_trade_total_quantity': cumulative_qty[-1], 'regular_calculated_actual_trade_total_amount': str(D(cumulative_amount[-1]) / 10000),
            'native_quote_rows': len(quotes), 'source_quote_quantity_nondecreasing': all(a <= b for a, b in zip(quote_volume, quote_volume[1:])),
            'source_quote_turnover_nondecreasing': all(a <= b for a, b in zip(quote_amount, quote_amount[1:])),
            'corroborated_explicit_closing_phase_quote_found': bool(closing),
            'closing_quote_UpdateTime': quotes[closing[0]]['UpdateTime'] if closing else None,
            'closing_quote_LocalTime': quotes[closing[0]]['LocalTime'] if closing else None,
            'quote_prefix_stats': stats, 'quote_prefix_witnesses': quote_artifact,
            'original_control_cutpoint_stats': control_stats, 'original_control_witnesses': control_artifact,
            'event_source_sha256': event_leaf['sha256'], 'snapshot_source_sha256': quote_leaf['sha256'],
            'original_control_source_sha256': original_ref['sha256'] if original_ref else None}


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'full_union_native_trade_prefix_diagnostics.json'
    assert not output.exists() and not any(OUT.glob('symbol=*'))
    native_proof_path = NATIVE / 'independent_complete_native_member_verification.json'
    proof = json.loads(native_proof_path.read_text())
    assert proof['passed']
    native_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    assert sha(native_receipt_path) == proof['native_member_acquisition_receipt_sha256']
    receipt = json.loads(native_receipt_path.read_text())
    leaves = {(r['symbol'], r['kind']): r for r in receipt['records']}
    requested = receipt['requested_symbols']
    pairs = [s for s in requested if (s, 'events') in leaves and (s, 'snapshot') in leaves]
    assert len(requested) == 1178 and len(pairs) == 1123
    original_proof_path = ORIGINAL / 'independent_all_original_control_verification.json'
    original_proof = json.loads(original_proof_path.read_text())
    assert original_proof['passed']
    original_audit_path = ORIGINAL / 'all_original_control_reference_audit.json'
    assert sha(original_audit_path) == original_proof['audited_reference_receipt_sha256']
    originals = {r['symbol']: r for r in json.loads(original_audit_path.read_text())['records']}
    assert len(originals) == 499 and set(originals).issubset(pairs)
    results = []
    started = last = time.monotonic()
    for symbol in sorted(pairs):
        r = audit(symbol, leaves[symbol, 'events'], leaves[symbol, 'snapshot'], originals.get(symbol))
        results.append(r)
        if time.monotonic() - last >= 20:
            print(json.dumps({'full_union_native_pairs_checked': len(results), 'requested_pairs': len(pairs),
                              'regular_actual_trades_checked': sum(v['regular_actual_trade_rows'] for v in results),
                              'positive_quantity_quotes_checked': sum(v['quote_prefix_stats']['positive_quantity_quotes'] for v in results),
                              'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
            last = time.monotonic()
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'complete_native_member_verification_sha256': sha(native_proof_path), 'native_member_acquisition_sha256': sha(native_receipt_path),
              'all_original_control_verification_sha256': sha(original_proof_path), 'all_original_reference_audit_sha256': sha(original_audit_path),
              'scope_date': '2026-09-23', 'requested_historical_union_symbols': 1178,
              'complete_native_pairs_audited': len(results), 'actual_missing_target_stockdays_audited': len(set(pairs) & set(receipt['actual_missing_target_symbols'])),
              'all_original_complete_stockday_controls_audited': len(originals),
              'requested_union_symbols_without_complete_pairs': [s for s in requested if s not in pairs],
              'regular_actual_trade_rows_checked': sum(r['regular_actual_trade_rows'] for r in results),
              'native_snapshot_rows_checked': sum(r['native_quote_rows'] for r in results),
              'quote_prefix_totals': {k: sum(r['quote_prefix_stats'][k] for r in results) for k in results[0]['quote_prefix_stats'] if not k.startswith('quote_maximum')},
              'original_control_cutpoint_totals': {k: sum(r['original_control_cutpoint_stats'][k] for r in results) for k in results[0]['original_control_cutpoint_stats']},
              'stockdays_all_positive_quote_identity_qty_amount_last_price_exact': sum(r['quote_prefix_stats']['positive_quantity_quotes'] == r['quote_prefix_stats']['quote_all_identity_quantity_amount_and_last_price_exact'] for r in results),
              'stockdays_corroborated_explicit_closing_phase_quote_found': sum(r['corroborated_explicit_closing_phase_quote_found'] for r in results),
              'records': results, 'calculated_amount_is_not_independent_source_amount': True,
              'all_source_quantity_values_used_without_unit_conversion_or_rounding': True,
              'observed_original_cum_qty_only_diagnoses_known_controls_no_missing_quantity_inference': True,
              'original_strict_coarse_gates_not_changed_or_bypassed': True,
              'exact_nominal_signal_PIT_or_live_admission_certified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
