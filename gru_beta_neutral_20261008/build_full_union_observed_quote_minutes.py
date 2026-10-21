"""Retain a full-union day of native observed quote candles with traceable cuts.

Fixed source clocks and both session ends, no per-stock tuning. No panel write,
missing-share inference, synthesized trades, or claim of nominal/live PIT.
"""
import bisect
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
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
WHOLE = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
NATIVE = WHOLE / 'full_union_day_native_members'
PREFIX = WHOLE / 'full_union_trade_prefix_audit'
OUT = WHOLE / 'full_union_observed_quote_minute_layer_v2'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']
DAY = '2026-09-23'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ms(value):
    h, m, s = value.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(D(s) * 1000)


def save(rows, path):
    assert not path.exists()
    keys = sorted({k for row in rows for k in row})
    normalized = [{k: row.get(k) for k in keys} for row in rows]
    pq.write_table(pa.Table.from_pylist(normalized), path, compression='zstd')
    return {'path': str(path), 'sha256': sha(path), 'rows': len(rows)}


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'full_union_observed_quote_minute_layer_receipt.json'
    assert not output.exists() and not any(OUT.glob('symbol=*'))
    member_receipt_path = NATIVE / 'complete_native_member_acquisition.json'
    prefix_receipt_path = PREFIX / 'full_union_native_trade_prefix_diagnostics.json'
    prefix_proof_path = PREFIX / 'independent_full_union_trade_prefix_verification.json'
    proof = json.loads(prefix_proof_path.read_text()); assert proof['passed']
    assert sha(prefix_receipt_path) == proof['audited_full_union_trade_prefix_receipt_sha256']
    prefix = json.loads(prefix_receipt_path.read_text())
    member_receipt = json.loads(member_receipt_path.read_text())
    assert sha(member_receipt_path) == prefix['native_member_acquisition_sha256']
    leaves = {(r['symbol'], r['kind']): r for r in member_receipt['records']}
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    meta = json.loads(meta_path.read_text())
    execution_path = meta_path.parent / 'earliest_execution_times.npy'
    assert sha(execution_path) == meta['files_sha256']['earliest_execution_times.npy']
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == member_receipt['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    d = calendar['dates'].index(DAY)
    execution = np.load(execution_path, mmap_mode='r')[d * 4:(d + 1) * 4]
    midnight = int(datetime.fromisoformat(DAY).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()) * 1000000000
    targets = set(member_receipt['actual_missing_target_symbols'])
    controls = set(member_receipt['all_current_complete_stockday_control_symbols'])
    assert len(targets) == 624 and len(controls) == 499
    all_hours, records = [], []
    started = last = time.monotonic()
    for native in prefix['records']:
        symbol, venue = native['symbol'], native['venue']
        for kind in ['events', 'snapshot']:
            assert sha(leaves[symbol, kind]['path']) == leaves[symbol, kind]['sha256']
        table = pq.ParquetFile(leaves[symbol, 'events']['path']).read(columns=['RecordType', 'EventTime', 'EventSeqNo', 'ChannelNo', 'Price', 'Qty'])
        table = table.filter(pc.equal(table['RecordType'], 2))
        tt = table['EventTime'].cast(pa.int32()).to_numpy()
        mask = (tt == 33900000) | ((tt >= 34200000) & (tt <= 41400000)) | ((tt >= 46800000) & (tt <= 54000000))
        table = table.filter(pa.array(mask)); tt = table['EventTime'].cast(pa.int32()).to_numpy()
        seq, channels, price, qty = [table[k].to_numpy() for k in ['EventSeqNo', 'ChannelNo', 'Price', 'Qty']]
        order = np.lexsort((channels, seq, tt)); tt, price, qty = tt[order], price[order], qty[order]
        cumqty, cumamount = np.concatenate(([0], np.cumsum(qty))), np.concatenate(([0], np.cumsum(price * qty)))
        cumhigh, cumlow = np.maximum.accumulate(price), np.minimum.accumulate(price)
        leaf = native['quote_prefix_witnesses']; assert sha(leaf['path']) == leaf['sha256']
        witnesses = pq.ParquetFile(leaf['path']).read().to_pylist()
        witness_by_index = {r['quote_sorted_index']: r for r in witnesses}
        vf, phase = ('TradVolume', 'InstruStatus') if venue == 'SH' else ('Volume', 'TradingPhaseCode')
        quotes = pq.ParquetFile(leaves[symbol, 'snapshot']['path']).read(columns=['UpdateTime', 'LocalTime', 'SeqNo', vf, 'Turnover', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', phase]).to_pylist()
        quotes.sort(key=lambda q: (ms(q['UpdateTime']), ms(q['LocalTime']), q['SeqNo']))
        times = [ms(q['UpdateTime']) for q in quotes]
        values = [{f: D(str(q[f])) for f in [vf, 'Turnover', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice']} for q in quotes]
        price_prefix_checks = []
        for w in witnesses:
            cut, index = w['whole_trade_prefix_count'], w['quote_sorted_index']
            assert w['whole_trade_quantity_prefix_exact'] and w['calculated_trade_prefix_amount_exact'] and w['last_trade_price_exact']
            q = values[index]
            price_prefix_checks.append({'source_quote_sorted_index': index, 'source_quote_seq_no': str(quotes[index]['SeqNo']),
                                        'actual_trade_prefix_count': cut, 'native_source_OpenPrice': str(q['OpenPrice']),
                                        'native_source_HighPrice': str(q['HighPrice']), 'native_source_LowPrice': str(q['LowPrice']),
                                        'OpenPrice_exact_first_actual_regular_trade': q['OpenPrice'] == D(int(price[0])) / 10000,
                                        'HighPrice_exact_whole_actual_trade_prefix_high': q['HighPrice'] == D(int(cumhigh[cut - 1])) / 10000,
                                        'LowPrice_exact_whole_actual_trade_prefix_low': q['LowPrice'] == D(int(cumlow[cut - 1])) / 10000})
        am_cut = int(np.searchsorted(tt, 41400000, side='right'))
        morning = [i for i, t in enumerate(times) if am_cut > 0 and 41400000 <= t <= 41410000 and values[i][vf] == int(cumqty[am_cut]) and
                   values[i]['Turnover'] == D(int(cumamount[am_cut])) / 10000 and values[i]['LastPrice'] == D(int(price[am_cut - 1])) / 10000]
        closing = [i for i, t in enumerate(times) if 54000000 <= t <= 54010000 and values[i][vf] == int(cumqty[-1]) and
                   values[i]['Turnover'] == D(int(cumamount[-1])) / 10000 and values[i]['LastPrice'] == D(int(price[-1])) / 10000 and
                   (quotes[i][phase].strip() == 'CLOSE' if venue == 'SH' else quotes[i][phase].strip().startswith('E'))]
        assert closing
        minutes, before_cut, before_qty, before_amount = [], 0, D(0), D(0)
        for left in GRID:
            begin, end = left * 60000, (left + 1) * 60000
            start_q = bisect.bisect_left(times, begin) if venue == 'SH' else bisect.bisect_right(times, begin)
            stop_q = bisect.bisect_left(times, end) if venue == 'SH' else bisect.bisect_right(times, end)
            before = start_q - 1
            final = morning[0] if left == 689 and morning else closing[0] if left == 899 else stop_q - 1
            observed = [i for i in range(start_q, stop_q) if values[i][vf] > 0 and values[i]['LastPrice'] > 0]
            if left in [689, 899] and final not in observed:
                observed.append(final)
            assert final >= 0 and before >= 0
            final_witness = witness_by_index.get(final)
            cut = final_witness['whole_trade_prefix_count'] if final_witness else 0
            assert values[final][vf] == int(cumqty[cut]) and values[final]['Turnover'] == D(int(cumamount[cut])) / 10000
            volume, amount = values[final][vf] - before_qty, values[final]['Turnover'] - before_amount
            assert volume >= 0 and volume == volume.to_integral_value() and amount >= 0 and cut >= before_cut
            assert volume == int(cumqty[cut] - cumqty[before_cut]) and amount == D(int(cumamount[cut] - cumamount[before_cut])) / 10000
            if not observed or final_witness is None:
                minutes.append({'symbol': symbol, 'date': DAY, 'left_minute': left,
                                'minute_observation_status': 'no_real_positive_in_interval_quotes',
                                'boundary_definition': 'SH_left_closed_SZ_right_closed_with_both_corroborated_session_ends',
                                **dict.fromkeys(FIELDS, None), 'source_end_quote_sorted_index': final,
                                'source_end_quote_seq_no': str(quotes[final]['SeqNo']),
                                'source_end_quote_UpdateTime': quotes[final]['UpdateTime'], 'source_end_quote_LocalTime': quotes[final]['LocalTime'],
                                'actual_unsplit_trade_prefix_delta_rows': cut - before_cut,
                                'raw_native_counter_prefix_start': before_cut, 'raw_native_counter_prefix_end': cut,
                                'stored_dependency_ready_ms': None,
                                'missing_quote_interval_not_filled_or_claimed_zero_actual_trades': True})
                before_cut, before_qty, before_amount = cut, values[final][vf], values[final]['Turnover']
                continue
            opening_index = before
            op = values[final]['OpenPrice'] if left == 570 else values[before]['LastPrice']
            if left == 780:
                at_open = bisect.bisect_left(times, begin)
                opening_index = next(i for i in range(at_open, stop_q) if values[i][vf] > 0 and values[i]['LastPrice'] > 0)
                before = at_open - 1
                op = values[opening_index]['LastPrice']
            samples = [values[i]['LastPrice'] for i in observed]
            hi, lo = max([op] + samples), min([op] + samples)
            if left == 570 or values[final]['HighPrice'] > values[before]['HighPrice']:
                hi = max(hi, values[final]['HighPrice'])
            if left == 570 or values[final]['LowPrice'] < values[before]['LowPrice']:
                lo = min(lo, values[final]['LowPrice'])
            assert D(0) < lo <= min(op, values[final]['LastPrice']) <= max(op, values[final]['LastPrice']) <= hi
            dependencies = set(observed + [before, final, opening_index])
            # Includes the AM cumulative baseline used to form first afternoon V/A.
            if left == 780 and morning:
                dependencies.add(morning[0])
            quote_ready = max(ms(quotes[i]['LocalTime']) for i in dependencies)
            actual_ready = max((witness_by_index[i]['all_prefix_trades_latest_LocalTime_ms'] for i in dependencies if i in witness_by_index), default=0)
            actual_event_ready = max((witness_by_index[i]['last_actual_trade_EventTime_ms'] for i in dependencies if i in witness_by_index), default=0)
            minute = {'symbol': symbol, 'date': DAY, 'left_minute': left, 'minute_observation_status': 'actual_quote_candle_observed',
                      'boundary_definition': 'SH_left_closed_SZ_right_closed_with_both_corroborated_session_ends',
                      **{f: str(v) for f, v in zip(FIELDS, [op, hi, lo, values[final]['LastPrice'], volume, amount])},
                      'source_end_quote_sorted_index': final, 'source_end_quote_seq_no': str(quotes[final]['SeqNo']),
                      'source_end_quote_UpdateTime': quotes[final]['UpdateTime'], 'source_end_quote_LocalTime': quotes[final]['LocalTime'],
                      'source_opening_quote_sorted_index': opening_index, 'source_extrema_baseline_quote_sorted_index': before,
                      'all_observed_quote_sorted_indices': observed, 'all_dependency_quote_sorted_indices': sorted(dependencies),
                      'whole_actual_trade_prefix_start': before_cut, 'whole_actual_trade_prefix_end': cut,
                      'observed_quotes_in_interval': len(observed), 'actual_unsplit_trade_prefix_delta_rows': cut - before_cut,
                      'actual_observed_quote_age_at_nominal_end_ms': end - times[final],
                      'latest_quote_dependency_LocalTime_ms': quote_ready, 'latest_all_trade_prefix_dependency_LocalTime_ms': actual_ready,
                      'latest_included_actual_trade_EventTime_ms': actual_event_ready,
                      'stored_dependency_ready_ms': max(quote_ready, actual_ready, actual_event_ready),
                      'last_actual_trade_event_age_at_nominal_end_ms': end - final_witness['last_actual_trade_EventTime_ms'],
                      'last_actual_trade_EventTime_ms': final_witness['last_actual_trade_EventTime_ms'],
                      'quote_candle_not_full_actual_minute_trade_price_extrema': True,
                      'actual_trade_volume_and_calculated_turnover_exact': True}
            minutes.append(minute)
            before_cut, before_qty, before_amount = cut, values[final][vf], values[final]['Turnover']
        assert before_cut == len(price)
        all_observed = all(r['minute_observation_status'] == 'actual_quote_candle_observed' for r in minutes)
        if all_observed:
            assert sum(D(r['volume']) for r in minutes) == native['regular_actual_trade_total_quantity']
            assert sum(D(r['amount']) for r in minutes) == D(native['regular_calculated_actual_trade_total_amount'])
        dest = OUT / ('symbol=' + symbol); dest.mkdir()
        minute_artifact = save(minutes, dest / 'native_observed_quote_minutes.parquet')
        price_artifact = save(price_prefix_checks, dest / 'all_positive_quote_native_OHL_vs_actual_prefix.parquet')
        hours = []
        for slot in range(4):
            block = minutes[slot * 60:(slot + 1) * 60]
            genuine = [r for r in block if r['minute_observation_status'] == 'actual_quote_candle_observed']
            full = len(genuine) == 60
            signal = [630, 690, 840, 900][slot] * 60000
            ready_ms = max((r['stored_dependency_ready_ms'] for r in genuine), default=None)
            exec_ns = int(execution[slot])
            volume = sum(D(r['volume']) for r in block) if full else None
            h = {'symbol': symbol, 'date': DAY, 'slot': slot, 'actual_current_missing_target': symbol in targets,
                 'original_complete_control': symbol in controls, 'native_observed_minutes': len(genuine), 'all_60_native_quote_minutes_observed': full,
                 'open': block[0]['open'] if full else None, 'high': str(max(D(r['high']) for r in block)) if full else None,
                 'low': str(min(D(r['low']) for r in block)) if full else None,
                 'close': block[-1]['close'] if full else None, 'volume': str(volume) if full else None,
                 'amount': str(sum(D(r['amount']) for r in block)) if full else None,
                 'nominal_signal_end_ms': signal, 'stored_source_dependency_ready_ms': ready_ms,
                 'stored_source_dependency_ready_utc_ns': midnight + ready_ms * 1000000 if ready_ms is not None else None,
                 'current_earliest_execution_utc_ns': exec_ns,
                 'all_recorded_dependencies_before_current_earliest_execution': bool(full and midnight + ready_ms * 1000000 < exec_ns),
                 'all_recorded_dependencies_before_or_at_nominal_signal_end': bool(full and ready_ms <= signal),
                 'latest_observed_quote_older_than_60s_minutes': sum(r['actual_observed_quote_age_at_nominal_end_ms'] > 60000 for r in genuine),
                 'last_actual_trade_older_than_60s_minutes': sum(r['last_actual_trade_event_age_at_nominal_end_ms'] > 60000 for r in genuine),
                 'minute_prefix_deltas_without_actual_trades': sum(r['actual_unsplit_trade_prefix_delta_rows'] == 0 for r in genuine),
                 'positive_actual_hour_trade_volume': bool(full and volume > 0), 'PIT_clock_synchronisation_or_live_admission_certified': False}
            hours.append(h); all_hours.append(h)
        count_price = {f: sum(r[f] for r in price_prefix_checks) for f in ['OpenPrice_exact_first_actual_regular_trade', 'HighPrice_exact_whole_actual_trade_prefix_high', 'LowPrice_exact_whole_actual_trade_prefix_low']}
        records.append({'symbol': symbol, 'native_minute_artifact': minute_artifact, 'all_positive_quote_OHL_prefix_artifact': price_artifact,
                        'all_240_native_quote_minutes_observed': all_observed,
                        'actual_native_quote_candle_minutes': sum(r['minute_observation_status'] == 'actual_quote_candle_observed' for r in minutes),
                        'missing_actual_quote_interval_left_labels': [r['left_minute'] for r in minutes if r['minute_observation_status'] != 'actual_quote_candle_observed'],
                        'positive_quote_OHL_prefix_exact_counts': count_price, 'positive_source_quote_rows_checked': len(witnesses),
                        'native_snapshot_sha256': leaves[symbol, 'snapshot']['sha256'], 'native_events_sha256': leaves[symbol, 'events']['sha256'],
                        'native_quote_prefix_witness_sha256': leaf['sha256'], 'hours': hours})
        if time.monotonic() - last >= 20:
            print(json.dumps({'complete_union_stockdays_processed': len(records), 'native_minutes_retained': len(records) * 240,
                              'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
            last = time.monotonic()
    hour_artifact = save(all_hours, OUT / 'all_4492_source_quote_hours_and_recorded_execution_availability.parquet')
    target_hours = [r for r in all_hours if r['actual_current_missing_target']]
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'source_native_member_acquisition_sha256': sha(member_receipt_path), 'full_union_prefix_independent_proof_sha256': sha(prefix_proof_path),
              'current_candidate_meta_sha256': sha(meta_path), 'current_execution_times_sha256': sha(execution_path), 'calendar_sha256': sha(calendar_path),
              'full_historical_union_requested_symbols': 1178, 'all_complete_source_pairs_processed': len(records),
              'native_observed_quote_minutes_retained': len(records) * 240, 'native_observed_quote_hours_retained': len(all_hours),
              'actual_native_quote_candle_minutes_retained': sum(r['actual_native_quote_candle_minutes'] for r in records),
              'stockdays_all_240_native_quote_minutes_observed': sum(r['all_240_native_quote_minutes_observed'] for r in records),
              'actual_missing_target_stockdays_in_layer': len(targets), 'actual_missing_target_hours_in_layer': len(target_hours),
              'actual_missing_target_hours_all_recorded_dependencies_before_execution': sum(r['all_recorded_dependencies_before_current_earliest_execution'] for r in target_hours),
              'actual_missing_target_hours_all_recorded_dependencies_at_nominal_end': sum(r['all_recorded_dependencies_before_or_at_nominal_signal_end'] for r in target_hours),
              'actual_missing_target_hours_positive_actual_volume': sum(r['positive_actual_hour_trade_volume'] for r in target_hours),
              'actual_missing_target_hours_latest_observed_quote_age_within_60s_all_minutes': sum(r['all_60_native_quote_minutes_observed'] and r['latest_observed_quote_older_than_60s_minutes'] == 0 for r in target_hours),
              'all_positive_quotes_OHL_checked': sum(r['positive_source_quote_rows_checked'] for r in records),
              'all_positive_quotes_OHL_exact_prefix_counts': {f: sum(r['positive_quote_OHL_prefix_exact_counts'][f] for r in records) for f in count_price},
              'hour_summary_artifact': hour_artifact, 'records': records, 'fixed_definition_validated_against_known_controls_is_not_provider_documentation': True,
              'zero_actual_trade_minutes_retain_real_native_quotes_not_synthetic_trades': True,
              'stored_native_times_do_not_certify_clock_synchronisation_compute_latency_or_live_chain': True,
              'independent_reproduction_of_this_layer_pending': True, 'historical_source_quantity_precision_not_certified_by_one_day_layer': True,
              'original_failed_coarse_gates_and_denominators_retained': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
