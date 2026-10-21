"""Re-read all native pairs and verify every saved prefix/cutpoint cell.

Independent vector ordering and cumulative arithmetic. No producer imports.
Native daily totals are additionally compared with retained daily references.
"""
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
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
WHOLE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
NATIVE = WHOLE / 'full_union_day_native_members'
ORIGINAL = WHOLE / 'all_original_day_controls'
OUT = WHOLE / 'full_union_trade_prefix_audit'
DAILY = BASE / 'public_source_probes/baostock/union_daily_factors/normalized'
GRID = list(range(570, 690)) + list(range(780, 900))


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def millis(value):
    parts = value.split(':')
    return 3600000 * int(parts[0]) + 60000 * int(parts[1]) + int(D(parts[2]) * 1000)


def main():
    output = OUT / 'independent_full_union_trade_prefix_verification.json'
    assert not output.exists()
    audit_path = OUT / 'full_union_native_trade_prefix_diagnostics.json'
    audit = json.loads(audit_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_full_union_native_trade_prefixes.py') == audit['source_code_sha256']
    native_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    native_proof_path = NATIVE / 'independent_complete_native_member_verification.json'
    original_audit_path = ORIGINAL / 'all_original_control_reference_audit.json'
    original_proof_path = ORIGINAL / 'independent_all_original_control_verification.json'
    for p, key in [(native_receipt_path, 'native_member_acquisition_sha256'), (native_proof_path, 'complete_native_member_verification_sha256'),
                   (original_audit_path, 'all_original_reference_audit_sha256'), (original_proof_path, 'all_original_control_verification_sha256')]:
        assert sha(p) == audit[key]
    assert json.loads(native_proof_path.read_text())['passed'] and json.loads(original_proof_path.read_text())['passed']
    native_receipt = json.loads(native_receipt_path.read_text())
    leaves = {(r['symbol'], r['kind']): r for r in native_receipt['records']}
    refs = {r['symbol']: r for r in json.loads(original_audit_path.read_text())['records']}
    pairs = sorted(s for s in native_receipt['requested_symbols'] if (s, 'events') in leaves and (s, 'snapshot') in leaves)
    assert [r['symbol'] for r in audit['records']] == pairs and len(pairs) == 1123
    checked_quotes, checked_controls, total_trades, checked_files, results = 0, 0, 0, 0, []
    started = last_print = time.monotonic()
    for summary in audit['records']:
        symbol = summary['symbol']
        for kind in ['events', 'snapshot']:
            assert sha(leaves[symbol, kind]['path']) == leaves[symbol, kind]['sha256'] == summary[kind[:-1] + '_source_sha256' if kind == 'events' else 'snapshot_source_sha256']
            checked_files += 1
        t = pq.ParquetFile(leaves[symbol, 'events']['path']).read(columns=['RecordType', 'Symbol', 'ChannelNo', 'EventSeqNo', 'EventTime', 'LocalTime', 'Price', 'Qty', 'Amount'])
        assert t.schema.metadata[b'price_scale'] == b'10000' and t.schema.metadata[b'qty_scale'] == b'1'
        assert t.schema.metadata[b'event_time_unit'] == b'millisecond'
        t = t.filter(pc.equal(t['RecordType'], 2))
        time_ms = t['EventTime'].cast(pa.int32()).to_numpy()
        in_regular = (time_ms == 33900000) | ((time_ms >= 34200000) & (time_ms <= 41400000)) | ((time_ms >= 46800000) & (time_ms <= 54000000))
        assert summary['actual_trade_rows'] == len(t) and summary['actual_trade_rows_outside_fixed_regular_window'] == int((~in_regular).sum())
        assert summary['outside_window_event_times_first_20'] == time_ms[~in_regular][:20].tolist()
        t = t.filter(pa.array(in_regular))
        event_ms = t['EventTime'].cast(pa.int32()).to_numpy()
        local_ms = t['LocalTime'].cast(pa.int32()).to_numpy()
        seq = t['EventSeqNo'].to_numpy()
        channel = t['ChannelNo'].to_numpy()
        price = t['Price'].to_numpy()
        quantity = t['Qty'].to_numpy()
        raw_amount = t['Amount'].to_numpy()
        assert all(s == symbol for s in t['Symbol'].to_pylist()) and np.all(price > 0) and np.all(quantity > 0)
        order = np.lexsort((channel, seq, event_ms))
        event_ms, local_ms, seq, channel, price, quantity, raw_amount = [a[order] for a in [event_ms, local_ms, seq, channel, price, quantity, raw_amount]]
        assert len({(int(c), int(s)) for c, s in zip(channel, seq)}) == len(seq)
        assert summary['regular_trade_fields_valid'] and summary['regular_trade_identity_unique']
        assert summary['regular_trade_channels'] == sorted(set(int(c) for c in channel))
        count = len(price)
        total_trades += count
        cq = np.concatenate(([0], np.cumsum(quantity, dtype=np.int64)))
        products = price * quantity
        ca = np.concatenate(([0], np.cumsum(products, dtype=np.int64)))
        cumulative_receipt = np.concatenate(([0], np.maximum.accumulate(local_ms)))
        # Python totals check the native int64 prefix arithmetic cannot overflow.
        assert int(cq[-1]) == sum(int(q) for q in quantity) == summary['regular_actual_trade_total_quantity']
        assert int(ca[-1]) == sum(int(p) * int(q) for p, q in zip(price, quantity))
        assert D(int(ca[-1])) / 10000 == D(summary['regular_calculated_actual_trade_total_amount'])
        assert int((raw_amount == products).sum()) == summary['regular_source_Amount_exact_actual_Price_times_Qty_rows']
        venue = symbol[-2:]
        vf, phase = ('TradVolume', 'InstruStatus') if venue == 'SH' else ('Volume', 'TradingPhaseCode')
        quotes = pq.ParquetFile(leaves[symbol, 'snapshot']['path']).read(columns=['SecurityID', 'UpdateTime', 'LocalTime', 'SeqNo', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', 'Turnover', vf, phase]).to_pylist()
        qt = np.array([millis(q['UpdateTime']) for q in quotes])
        qr = np.array([millis(q['LocalTime']) for q in quotes])
        qs = np.array([q['SeqNo'] for q in quotes])
        quote_order = np.lexsort((qs, qr, qt))
        quotes = [quotes[int(i)] for i in quote_order]
        qt, qr = qt[quote_order], qr[quote_order]
        qv = [D(str(q[vf])) for q in quotes]
        qa = [D(str(q['Turnover'])) for q in quotes]
        qp = [D(str(q['LastPrice'])) for q in quotes]
        assert len(quotes) == summary['native_quote_rows']
        assert all(a <= b for a, b in zip(qv, qv[1:])) == summary['source_quote_quantity_nondecreasing']
        assert all(a <= b for a, b in zip(qa, qa[1:])) == summary['source_quote_turnover_nondecreasing']
        leaf = summary['quote_prefix_witnesses']
        assert sha(leaf['path']) == leaf['sha256']
        saved = pq.ParquetFile(leaf['path']).read().to_pylist()
        positive = [i for i, v in enumerate(qv) if v > 0]
        assert len(saved) == leaf['rows'] == len(positive)
        quote_stats = {k: 0 for k in summary['quote_prefix_stats']}
        deltas = []
        quote_group = {}
        for index, w in zip(positive, saved):
            q, qty, amount, px = quotes[index], qv[index], qa[index], qp[index]
            assert qty == qty.to_integral_value()
            cut = int(np.searchsorted(cq, int(qty)))
            assert 0 < cut <= count and int(cq[cut]) == qty
            assert D(int(ca[cut])) / 10000 == amount and D(int(price[cut - 1])) / 10000 == px
            expected = {'symbol': symbol, 'quote_sorted_index': index, 'quote_seq_no': str(q['SeqNo']),
                        'quote_UpdateTime': q['UpdateTime'], 'quote_LocalTime': q['LocalTime'],
                        'quote_reported_ms': int(qt[index]), 'quote_received_ms': int(qr[index]),
                        'quote_quantity_source_field': vf, 'quote_quantity': str(qty), 'quote_Turnover': str(amount), 'quote_LastPrice': str(px),
                        'quote_security_identity_exact': q['SecurityID'] == symbol[:6], 'quote_quantity_is_exact_integer': True,
                        'whole_trade_quantity_prefix_exact': True, 'whole_trade_prefix_count': cut,
                        'calculated_trade_prefix_amount_exact': True, 'last_trade_price_exact': True,
                        'last_actual_trade_channel': int(channel[cut - 1]), 'last_actual_trade_id': int(seq[cut - 1]),
                        'last_actual_trade_EventTime_ms': int(event_ms[cut - 1]), 'last_actual_trade_LocalTime_ms': int(local_ms[cut - 1]),
                        'all_prefix_trades_latest_LocalTime_ms': int(cumulative_receipt[cut]),
                        'last_actual_trade_after_quote_reported_by_ms': int(event_ms[cut - 1] - qt[index])}
            assert w == expected, (symbol, index)
            quote_group.setdefault(int(qty), []).append(index)
            deltas.append(expected['last_actual_trade_after_quote_reported_by_ms'])
        for key in ['positive_quantity_quotes', 'quote_whole_actual_trade_quantity_prefix_exact', 'quote_actual_trade_prefix_calculated_amount_exact', 'quote_last_actual_trade_price_exact', 'quote_all_identity_quantity_amount_and_last_price_exact']:
            quote_stats[key] = len(saved)
        quote_stats['quote_last_actual_trade_after_reported_quote_time'] = sum(x > 0 for x in deltas)
        quote_stats['quote_maximum_last_actual_trade_after_reported_time_ms'] = max(deltas)
        assert quote_stats == summary['quote_prefix_stats']
        checked_quotes += len(saved)
        closing = [i for i, q in enumerate(quotes) if 54000000 <= qt[i] <= 54010000 and
                   (q[phase].strip() == 'CLOSE' if venue == 'SH' else q[phase].strip().startswith('E')) and
                   qv[i] == int(cq[-1]) and qa[i] == D(int(ca[-1])) / 10000 and qp[i] == D(int(price[-1])) / 10000]
        assert bool(closing) == summary['corroborated_explicit_closing_phase_quote_found']
        assert summary['closing_quote_UpdateTime'] == quotes[closing[0]]['UpdateTime']
        assert summary['closing_quote_LocalTime'] == quotes[closing[0]]['LocalTime']
        control_stats = {k: 0 for k in summary['original_control_cutpoint_stats']}
        if symbol in refs:
            ref = refs[symbol]
            assert sha(ref['path']) == ref['sha256'] == summary['original_control_source_sha256']
            original = pq.ParquetFile(ref['path']).read(columns=['open', 'high', 'low', 'close', 'volume']).to_pylist()
            artifact = summary['original_control_witnesses']
            assert sha(artifact['path']) == artifact['sha256'] and artifact['rows'] == 240
            reference_rows = pq.ParquetFile(artifact['path']).read().to_pylist()
            cumulative = np.cumsum([r['volume'] for r in original], dtype=np.int64)
            cuts = np.searchsorted(cq, cumulative)
            assert np.all(cq[cuts] == cumulative)
            previous_cut = 0
            for left, obs, quantity_end, cut, w in zip(GRID, original, cumulative, cuts, reference_rows):
                quantity_end, cut = int(quantity_end), int(cut)
                end = (left + 1) * 60000
                near = [i for i in quote_group.get(quantity_end, []) if end - 10000 <= qt[i] <= end + 10000 and qp[i] == D(str(obs['close']))]
                final = int(np.searchsorted(qt, end, side='left' if venue == 'SH' else 'right')) - 1
                if left == 899:
                    final = closing[0]
                prices_in_prefix = [D(int(p)) / 10000 for p in price[previous_cut:cut]]
                last_exists = cut > 0
                expected = {'symbol': symbol, 'left_minute': left, 'original_observed_volume': obs['volume'],
                            'original_observed_cumulative_volume': quantity_end, 'whole_actual_trade_quantity_cutpoint_exact': True,
                            'observed_reference_prefix_count': cut, 'last_actual_trade_EventTime_ms': int(event_ms[cut - 1]) if last_exists else None,
                            'last_actual_trade_LocalTime_ms': int(local_ms[cut - 1]) if last_exists else None,
                            'last_actual_trade_after_nominal_end_ms': int(event_ms[cut - 1]) - end if last_exists else None,
                            'observed_original_close_exact_last_trade_price': last_exists and D(str(obs['close'])) == D(int(price[cut - 1])) / 10000,
                            'whole_trades_in_observed_original_quantity_delta': cut - previous_cut,
                            'native_price_range_with_original_carried_open_within_original_high_low':
                                max([D(str(obs['open'])), D(str(obs['close']))] + prices_in_prefix) <= D(str(obs['high'])) and
                                min([D(str(obs['open'])), D(str(obs['close']))] + prices_in_prefix) >= D(str(obs['low'])),
                            'native_quotes_with_exact_observed_reference_cum_qty_and_close_near_end': len(near),
                            'all_matching_quote_UpdateTimes_near_end': [quotes[i]['UpdateTime'] for i in near],
                            'all_matching_quote_LocalTimes_near_end': [quotes[i]['LocalTime'] for i in near],
                            'fixed_venue_end_quote_UpdateTime': quotes[final]['UpdateTime'], 'fixed_venue_end_quote_quantity': str(qv[final]),
                            'fixed_venue_end_quote_quantity_exact_original_observed_cumulative_volume': qv[final] == quantity_end,
                            'reference_cutpoint_diagnostic_only_not_missing_quantity_inference': True}
                assert w == expected, (symbol, left)
                previous_cut = cut
            control_stats = {'observed_original_minutes': len(reference_rows),
                             'observed_original_cum_qty_exact_whole_actual_trade_cutpoints': len(reference_rows),
                             'observed_original_close_exact_last_prefix_trade_price': sum(w['observed_original_close_exact_last_trade_price'] for w in reference_rows),
                             'original_actual_prefix_price_range_contained': sum(w['native_price_range_with_original_carried_open_within_original_high_low'] for w in reference_rows),
                             'original_cum_qty_and_close_match_some_real_quote_within_10s_of_end': sum(w['native_quotes_with_exact_observed_reference_cum_qty_and_close_near_end'] > 0 for w in reference_rows),
                             'fixed_venue_end_quote_cum_qty_exact_original': sum(w['fixed_venue_end_quote_quantity_exact_original_observed_cumulative_volume'] for w in reference_rows)}
            checked_controls += len(reference_rows)
        else:
            assert summary['original_control_witnesses'] is None and summary['original_control_source_sha256'] is None
        assert control_stats == summary['original_control_cutpoint_stats']
        daily_path = DAILY / (symbol + '.json')
        daily_receipt = json.loads(daily_path.read_text())
        daily_rows = [dict(zip(daily_receipt['fields'], row)) for row in daily_receipt['rows'] if row[0] == audit['scope_date']]
        assert len(daily_rows) == 1
        daily_row = daily_rows[0]
        actual_daily = {'open': D(int(price[0])) / 10000, 'high': D(int(price.max())) / 10000,
                        'low': D(int(price.min())) / 10000, 'close': D(int(price[-1])) / 10000,
                        'volume': D(int(cq[-1])), 'amount': D(int(ca[-1])) / 10000}
        daily_exact = {f: v == D(daily_row[f]) for f, v in actual_daily.items()}
        results.append({'symbol': symbol, 'venue': venue, 'positive_quote_witnesses_verified': len(saved),
                        'original_observed_cutpoint_witnesses_verified': control_stats['observed_original_minutes'],
                        'regular_actual_trades_verified': count, 'native_daily_vs_retained_daily_exact': daily_exact,
                        'retained_daily_path': str(daily_path), 'retained_daily_sha256': sha(daily_path),
                        'reported_quote_clock_can_precede_last_included_actual_event': quote_stats['quote_last_actual_trade_after_reported_quote_time'],
                        'maximum_last_included_event_after_reported_quote_ms': max(deltas),
                        'minimum_last_included_event_after_reported_quote_ms': min(deltas),
                        'all_quote_witness_cells_reproduced': True, 'all_observed_original_cutpoint_cells_reproduced': True})
        if time.monotonic() - last_print >= 20:
            print(json.dumps({'native_pairs_independently_verified': len(results), 'positive_quote_witnesses_verified': checked_quotes,
                              'original_observed_cutpoints_verified': checked_controls, 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
            last_print = time.monotonic()
    assert total_trades == audit['regular_actual_trade_rows_checked']
    assert checked_quotes == audit['quote_prefix_totals']['positive_quantity_quotes'] == 5206443
    assert checked_controls == audit['original_control_cutpoint_totals']['observed_original_minutes'] == 119760
    for key in audit['quote_prefix_totals']:
        assert sum(r['quote_prefix_stats'][key] for r in audit['records']) == audit['quote_prefix_totals'][key]
    for key in audit['original_control_cutpoint_totals']:
        assert sum(r['original_control_cutpoint_stats'][key] for r in audit['records']) == audit['original_control_cutpoint_totals'][key]
    assert sum(r['reported_quote_clock_can_precede_last_included_actual_event'] for r in results) == 1222680
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__), 'passed': True,
              'audited_full_union_trade_prefix_receipt_sha256': sha(audit_path),
              'complete_native_member_verification_sha256': sha(native_proof_path), 'all_original_control_verification_sha256': sha(original_proof_path),
              'full_union_requested_symbols': 1178, 'all_complete_native_pairs_independently_verified': len(results),
              'all_actual_missing_target_stockdays_in_scope': audit['actual_missing_target_stockdays_audited'],
              'source_native_member_payloads_fresh_sha256_checked': checked_files,
              'regular_actual_trade_rows_verified': total_trades, 'all_positive_quote_prefix_witnesses_verified': checked_quotes,
              'all_original_observed_quantity_cutpoint_witnesses_verified': checked_controls,
              'daily_exact_stockdays_by_field': {f: sum(r['native_daily_vs_retained_daily_exact'][f] for r in results) for f in actual_daily},
              'daily_all_six_fields_exact_stockdays': sum(all(r['native_daily_vs_retained_daily_exact'].values()) for r in results),
              'source_ordered_quote_clock_not_exact_last_trade_event_clock_count': 1222680,
              'maximum_last_trade_after_reported_quote_ms': max(r['maximum_last_included_event_after_reported_quote_ms'] for r in results),
              'minimum_last_trade_after_reported_quote_ms': min(r['minimum_last_included_event_after_reported_quote_ms'] for r in results),
              'records': results, 'daily_comparison_does_not_certify_source_independence': True,
              'no_source_quantity_rounding_or_missing_quantity_inference': True,
              'original_failed_gates_and_denominators_retained': True, 'candidate_changed': False,
              'candidate_hours_admitted': 0, 'exact_nominal_signal_PIT_or_live_admission_certified': False, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
