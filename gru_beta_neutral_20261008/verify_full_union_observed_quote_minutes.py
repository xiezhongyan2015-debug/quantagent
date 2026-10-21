"""Independently reproduce all minute observation cells from native source rows.

No producer imports; integer price/turnover arithmetic and source cumulative
quantity cuts. Missing observation intervals remain null, not trade candles.
"""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
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
OUT = WHOLE / 'full_union_observed_quote_minute_layer_v2'
GRID = list(range(570, 690)) + list(range(780, 900))
FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount']


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def ms(s):
    h, m, sec = s.split(':')
    return int(h) * 3600000 + int(m) * 60000 + int(Decimal(sec) * 1000)


def scaled(v, multiplier=10000):
    d = Decimal(str(v)) * multiplier
    assert d == d.to_integral_value()
    return int(d)


def main():
    output = OUT / 'independent_full_union_observed_quote_minute_verification.json'
    assert not output.exists()
    rp = OUT / 'full_union_observed_quote_minute_layer_receipt.json'
    receipt = json.loads(rp.read_text())
    producer = ROOT / 'gru_beta_neutral_20261008/build_full_union_observed_quote_minutes.py'
    assert sha(producer) == receipt['source_code_sha256']
    npth = WHOLE / 'full_union_day_native_members/complete_native_member_acquisition.json'
    native = json.loads(npth.read_text())
    assert sha(npth) == receipt['source_native_member_acquisition_sha256']
    pp = WHOLE / 'full_union_trade_prefix_audit/independent_full_union_trade_prefix_verification.json'
    assert sha(pp) == receipt['full_union_prefix_independent_proof_sha256']
    assert json.loads(pp.read_text())['passed']
    leaves = {(r['symbol'], r['kind']): r for r in native['records']}
    pairs = sorted(s for s in native['requested_symbols'] if (s, 'events') in leaves and (s, 'snapshot') in leaves)
    assert len(native['requested_symbols']) == 1178 and len(pairs) == 1123
    assert [r['symbol'] for r in receipt['records']] == pairs
    targets = set(native['actual_missing_target_symbols'])
    controls = set(native['all_current_complete_stockday_control_symbols'])
    assert len(targets) == 624 and len(controls) == 499 and not targets & controls
    mp = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    ep = mp.parent / 'earliest_execution_times.npy'
    cp = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(mp) == receipt['current_candidate_meta_sha256']
    assert sha(ep) == receipt['current_execution_times_sha256']
    assert sha(cp) == receipt['calendar_sha256']
    assert sha(mp.parent / 'hour_complete.npy') == '56b016d3a33d5c3a4c640de156ea2e716e16c304c7b307879c34810cc095eb59'
    date_index = json.loads(cp.read_text())['dates'].index('2026-09-23')
    executions = np.load(ep, mmap_mode='r')[4 * date_index:4 * date_index + 4]
    midnight = int(datetime(2026, 9, 23, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()) * 1000000000
    ha = receipt['hour_summary_artifact']
    assert sha(ha['path']) == ha['sha256']
    saved_hours = pq.ParquetFile(ha['path']).read().to_pylist()
    assert len(saved_hours) == 4492
    hours_by_key = {(r['symbol'], r['slot']): r for r in saved_hours}
    assert len(hours_by_key) == 4492
    total_minutes = actual_minutes = positive_quotes = full_days = trade_rows = 0
    verified_files = 0
    missing, verified_target_hours = [], []
    started = last = time.monotonic()
    for summary in receipt['records']:
        symbol = summary['symbol']
        for kind in ['events', 'snapshot']:
            leaf = leaves[symbol, kind]
            assert sha(leaf['path']) == leaf['sha256']
            verified_files += 1
        ev = pq.ParquetFile(leaves[symbol, 'events']['path']).read(columns=['RecordType', 'EventTime', 'LocalTime', 'EventSeqNo', 'ChannelNo', 'Price', 'Qty'])
        assert ev.schema.metadata[b'price_scale'] == b'10000' and ev.schema.metadata[b'qty_scale'] == b'1'
        ev = ev.filter(pc.equal(ev['RecordType'], 2))
        et = ev['EventTime'].cast(pa.int32()).to_numpy()
        regular = (et == 33900000) | ((et >= 34200000) & (et <= 41400000)) | ((et >= 46800000) & (et <= 54000000))
        ev = ev.filter(pa.array(regular))
        et, lt = [ev[k].cast(pa.int32()).to_numpy() for k in ['EventTime', 'LocalTime']]
        seq, ch, price, qty = [ev[k].to_numpy() for k in ['EventSeqNo', 'ChannelNo', 'Price', 'Qty']]
        ix = np.lexsort((ch, seq, et))
        et, lt, price, qty = [a[ix] for a in [et, lt, price, qty]]
        trade_rows += len(price)
        assert np.all(price > 0) and np.all(qty > 0)
        cq = np.r_[0, np.cumsum(qty, dtype=np.int64)]
        ca = np.r_[0, np.cumsum(price * qty, dtype=np.int64)]
        high, low = np.maximum.accumulate(price), np.minimum.accumulate(price)
        received = np.r_[0, np.maximum.accumulate(lt)]
        vf, phase = ('TradVolume', 'InstruStatus') if symbol.endswith('.SH') else ('Volume', 'TradingPhaseCode')
        qs = pq.ParquetFile(leaves[symbol, 'snapshot']['path']).read(columns=['UpdateTime', 'LocalTime', 'SeqNo', vf, 'Turnover', 'LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice', phase]).to_pylist()
        qs = sorted(qs, key=lambda q: (ms(q['UpdateTime']), ms(q['LocalTime']), q['SeqNo']))
        qt = np.array([ms(q['UpdateTime']) for q in qs])
        ql = np.array([ms(q['LocalTime']) for q in qs])
        qv = np.array([scaled(q[vf], 1) for q in qs])
        qa = np.array([scaled(q['Turnover']) for q in qs])
        qp, qo, qh, qlo = [np.array([scaled(q[f]) for q in qs]) for f in ['LastPrice', 'OpenPrice', 'HighPrice', 'LowPrice']]
        cuts = np.searchsorted(cq, qv)
        assert np.all(cuts < len(cq)) and np.array_equal(cq[cuts], qv) and np.array_equal(ca[cuts], qa)
        positives = np.flatnonzero(qv > 0)
        assert np.array_equal(qp[positives], price[cuts[positives] - 1])
        pf = summary['all_positive_quote_OHL_prefix_artifact']
        assert sha(pf['path']) == pf['sha256']
        pr = pq.ParquetFile(pf['path']).read().to_pylist()
        assert len(pr) == len(positives) == summary['positive_source_quote_rows_checked']
        for q, r in zip(positives, pr):
            c = int(cuts[q])
            assert r['source_quote_sorted_index'] == int(q) and r['actual_trade_prefix_count'] == c
            assert r['source_quote_seq_no'] == str(qs[q]['SeqNo'])
            for name, actual, expected in [('OpenPrice', qo[q], price[0]), ('HighPrice', qh[q], high[c - 1]), ('LowPrice', qlo[q], low[c - 1])]:
                assert scaled(r['native_source_' + name]) == actual == expected
            assert all(r[k] for k in r if k.endswith(('actual_regular_trade', 'trade_prefix_high', 'trade_prefix_low')))
        positive_quotes += len(pr)
        amcut = int(np.searchsorted(et, 41400000, side='right'))
        am = [i for i in np.flatnonzero((qt >= 41400000) & (qt <= 41410000)) if amcut > 0 and qv[i] == cq[amcut] and qa[i] == ca[amcut] and qp[i] == price[amcut - 1]]
        close = [i for i in np.flatnonzero((qt >= 54000000) & (qt <= 54010000)) if qv[i] == cq[-1] and qa[i] == ca[-1] and qp[i] == price[-1] and (qs[i][phase].strip() == 'CLOSE' if symbol.endswith('.SH') else qs[i][phase].strip().startswith('E'))]
        assert close
        mf = summary['native_minute_artifact']
        assert sha(mf['path']) == mf['sha256']
        minutes = pq.ParquetFile(mf['path']).read().to_pylist()
        assert len(minutes) == 240 and [r['left_minute'] for r in minutes] == GRID
        total_minutes += 240
        beforecut = 0
        observed_count, missing_labels = 0, []
        for left, r in zip(GRID, minutes):
            assert r['symbol'] == symbol and r['date'] == '2026-09-23'
            edge_side = 'left' if symbol.endswith('.SH') else 'right'
            first, stop = [int(np.searchsorted(qt, t, side=edge_side)) for t in [left * 60000, (left + 1) * 60000]]
            baseline = first - 1
            endq = am[0] if left == 689 and am else close[0] if left == 899 else stop - 1
            observed = [i for i in range(first, stop) if qv[i] > 0 and qp[i] > 0]
            if left in [689, 899] and endq not in observed:
                observed.append(endq)
            cut = int(cuts[endq])
            assert r['source_end_quote_sorted_index'] == endq and r['source_end_quote_seq_no'] == str(qs[endq]['SeqNo'])
            assert r['source_end_quote_UpdateTime'] == qs[endq]['UpdateTime'] and r['source_end_quote_LocalTime'] == qs[endq]['LocalTime']
            assert r['actual_unsplit_trade_prefix_delta_rows'] == cut - beforecut >= 0
            if not observed or qv[endq] <= 0:
                assert r['minute_observation_status'] == 'no_real_positive_in_interval_quotes'
                assert all(r[f] is None for f in FIELDS) and r['stored_dependency_ready_ms'] is None
                assert r['raw_native_counter_prefix_start'] == beforecut and r['raw_native_counter_prefix_end'] == cut
                assert r['missing_quote_interval_not_filled_or_claimed_zero_actual_trades']
                missing_labels.append(left)
                missing.append({'symbol': symbol, 'left_minute': left, 'actual_current_missing_target': symbol in targets, 'actual_trade_rows_in_source_counter_delta': cut - beforecut})
                beforecut = cut
                continue
            assert r['minute_observation_status'] == 'actual_quote_candle_observed'
            opening = baseline
            op = qo[endq] if left == 570 else qp[baseline]
            if left == 780:
                afternoon_start = int(np.searchsorted(qt, 46800000, side='left'))
                opening = next(i for i in range(afternoon_start, stop) if qv[i] > 0 and qp[i] > 0)
                baseline = afternoon_start - 1
                op = qp[opening]
            hi, lo = max([int(op)] + qp[observed].tolist()), min([int(op)] + qp[observed].tolist())
            if left == 570 or qh[endq] > qh[baseline]:
                hi = max(hi, int(qh[endq]))
            if left == 570 or qlo[endq] < qlo[baseline]:
                lo = min(lo, int(qlo[endq]))
            expected = [op, hi, lo, qp[endq], cq[cut] - cq[beforecut], ca[cut] - ca[beforecut]]
            assert [scaled(r[f], 1 if f == 'volume' else 10000) for f in FIELDS] == expected
            assert r['whole_actual_trade_prefix_start'] == beforecut and r['whole_actual_trade_prefix_end'] == cut
            assert r['source_opening_quote_sorted_index'] == opening and r['source_extrema_baseline_quote_sorted_index'] == baseline
            dependencies = set(observed + [baseline, opening, endq])
            if left == 780 and am:
                dependencies.add(am[0])
            assert r['all_observed_quote_sorted_indices'] == observed and r['all_dependency_quote_sorted_indices'] == sorted(dependencies)
            qc = max(int(ql[i]) for i in dependencies)
            tc = max(int(received[cuts[i]]) for i in dependencies if qv[i] > 0)
            ec = max(int(et[cuts[i] - 1]) for i in dependencies if qv[i] > 0)
            assert [r[k] for k in ['latest_quote_dependency_LocalTime_ms', 'latest_all_trade_prefix_dependency_LocalTime_ms', 'latest_included_actual_trade_EventTime_ms', 'stored_dependency_ready_ms']] == [qc, tc, ec, max(qc, tc, ec)]
            assert r['actual_observed_quote_age_at_nominal_end_ms'] == (left + 1) * 60000 - int(qt[endq])
            assert r['last_actual_trade_event_age_at_nominal_end_ms'] == (left + 1) * 60000 - int(et[cut - 1])
            beforecut = cut
            observed_count += 1
        assert beforecut == len(price)
        assert observed_count == summary['actual_native_quote_candle_minutes'] and missing_labels == summary['missing_actual_quote_interval_left_labels']
        assert summary['all_240_native_quote_minutes_observed'] == (observed_count == 240)
        if observed_count == 240:
            assert sum(scaled(r['volume'], 1) for r in minutes) == int(cq[-1])
            assert sum(scaled(r['amount']) for r in minutes) == int(ca[-1])
            full_days += 1
        actual_minutes += observed_count
        for slot in range(4):
            h = hours_by_key[symbol, slot]
            assert h == summary['hours'][slot]
            block = minutes[slot * 60:slot * 60 + 60]
            valid = [r for r in block if r['minute_observation_status'] == 'actual_quote_candle_observed']
            full = len(valid) == 60
            ready = max((r['stored_dependency_ready_ms'] for r in valid), default=None)
            assert h['native_observed_minutes'] == len(valid) and h['all_60_native_quote_minutes_observed'] == full
            assert h['actual_current_missing_target'] == (symbol in targets) and h['original_complete_control'] == (symbol in controls)
            assert h['stored_source_dependency_ready_ms'] == ready
            assert h['stored_source_dependency_ready_utc_ns'] == (midnight + ready * 1000000 if ready is not None else None)
            assert h['current_earliest_execution_utc_ns'] == int(executions[slot])
            signal = [630, 690, 840, 900][slot] * 60000
            assert h['nominal_signal_end_ms'] == signal
            assert h['all_recorded_dependencies_before_current_earliest_execution'] == bool(full and midnight + ready * 1000000 < executions[slot])
            assert h['all_recorded_dependencies_before_or_at_nominal_signal_end'] == bool(full and ready <= signal)
            assert not h['PIT_clock_synchronisation_or_live_admission_certified']
            for key, expr in [('latest_observed_quote_older_than_60s_minutes', lambda r: r['actual_observed_quote_age_at_nominal_end_ms'] > 60000), ('last_actual_trade_older_than_60s_minutes', lambda r: r['last_actual_trade_event_age_at_nominal_end_ms'] > 60000), ('minute_prefix_deltas_without_actual_trades', lambda r: r['actual_unsplit_trade_prefix_delta_rows'] == 0)]:
                assert h[key] == sum(expr(r) for r in valid)
            if full:
                vals = [scaled(block[0]['open']), max(scaled(r['high']) for r in block), min(scaled(r['low']) for r in block), scaled(block[-1]['close']), sum(scaled(r['volume'], 1) for r in block), sum(scaled(r['amount']) for r in block)]
                assert [scaled(h[f], 1 if f == 'volume' else 10000) for f in FIELDS] == vals
                assert h['positive_actual_hour_trade_volume'] == (vals[4] > 0)
            else:
                assert all(h[f] is None for f in FIELDS) and not h['positive_actual_hour_trade_volume']
            if symbol in targets:
                verified_target_hours.append(h)
        if time.monotonic() - last > 20:
            print(json.dumps({'stockdays_independently_verified': (total_minutes // 240), 'all_quote_price_prefixes_verified': positive_quotes, 'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
            last = time.monotonic()
    assert total_minutes == 269520 and actual_minutes == 269513 and full_days == 1120 and positive_quotes == 5206443
    assert trade_rows == 40588594 and len(verified_target_hours) == 2496
    assert sum(h['all_recorded_dependencies_before_current_earliest_execution'] for h in verified_target_hours) == 2491
    assert sum(h['all_recorded_dependencies_before_or_at_nominal_signal_end'] for h in verified_target_hours) == 667
    assert sha(mp) == receipt['current_candidate_meta_sha256']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'audited_observed_minute_layer_receipt_sha256': sha(rp), 'source_leaf_files_fresh_SHA256_verified': verified_files,
              'all_1123_complete_pairs_independently_verified': True, 'full_union_scope_requested': 1178,
              'minute_grid_rows_verified': total_minutes, 'actual_observed_quote_candle_minutes_verified': actual_minutes,
              'all_positive_quotes_Open_High_Low_independently_exact_actual_prefix': positive_quotes,
              'regular_actual_trade_rows_re_read': trade_rows, 'all_240_actual_observed_minutes_stockdays': full_days,
              'hour_summary_rows_independently_verified': len(saved_hours), 'actual_missing_target_hours_verified': 2496,
              'actual_missing_target_hours_before_recorded_execution': 2491, 'actual_missing_target_hours_ready_by_nominal_signal_end': 667,
              'actual_missing_target_incomplete_hours': [h for h in verified_target_hours if not h['all_60_native_quote_minutes_observed']],
              'missing_quote_intervals_retained_null': missing, 'source_integer_quantity_and_unsplit_actual_trade_amount_exact': True,
              'quote_dependency_and_actual_event_receipt_times_independently_recomputed': True,
              'not_a_nominal_PIT_clock_synchronisation_or_live_latency_certificate': True,
              'not_a_historical_source_precision_or_uniform_provider_definition_certificate': True,
              'current_candidate_meta_sha256': sha(mp), 'original_failed_coverage_gates_and_denominators_retained': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
