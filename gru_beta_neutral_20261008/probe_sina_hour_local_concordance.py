"""Acquire raw 5m references and audit already observed native Sina minutes.

No bar expansion, interpolation, price overwrite, or panel admission. This
checks whether any complete native 60-minute hour survives independent QC.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from public_baostock_probe import PublicClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'sina_hour_local_concordance_20261009'
PRICE = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit(day, minutes, query, daily):
    source_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
    if not source_rows:
        return {'date': day, 'status': 'no_independent_raw_5m_rows', 'hours_passed': 0}
    coarse = pd.DataFrame(source_rows, columns=query['fields'])
    assert len(coarse) == 48 and coarse.adjustflag.eq('3').all() and coarse.code.eq('sh.600004').all()
    times = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
    coarse['clock'] = times.dt.hour * 60 + times.dt.minute
    np.testing.assert_array_equal(coarse.clock.to_numpy(), np.r_[np.arange(575, 691, 5), np.arange(785, 901, 5)])
    source = minutes[minutes.day.str.startswith(day)].copy()
    times = pd.to_datetime(source.day)
    source['clock'] = times.dt.hour * 60 + times.dt.minute
    assert not source.clock.duplicated().any()
    source = source.set_index('clock')
    coarse = coarse.set_index('clock')
    grids = [range(571, 631), range(631, 691), range(781, 841), range(841, 901)]
    hours = []
    for slot, grid in enumerate(grids):
        missing = [clock for clock in grid if clock not in source.index]
        if missing:
            hours.append({'slot_zero_based': slot, 'missing_native_minutes': missing, 'hour_strictly_passed': False})
            continue
        native = source.loc[list(grid)]
        assert (native[PRICE].to_numpy(float) > 0).all() and (native.volume.astype(float) >= 0).all()
        amount_checks = []
        for clock, row in native.iterrows():
            volume, amount = float(row.volume), float(row.amount)
            if not float(row.low) * volume - 1 <= amount <= float(row.high) * volume + 1:
                amount_checks.append(int(clock))
        buckets = []
        for i in range(12):
            block = native.iloc[i * 5: (i + 1) * 5]
            end = int(block.index[-1])
            reference = coarse.loc[end]
            observed = [float(block.open.iloc[0]), float(block.high.astype(float).max()),
                        float(block.low.astype(float).min()), float(block.close.iloc[-1])]
            cp = [float(reference[field]) for field in PRICE]
            volume = float(block.volume.astype(float).sum())
            tests = {'open_exact': abs(observed[0] - cp[0]) <= 1e-8,
                     'close_exact': abs(observed[3] - cp[3]) <= 1e-8,
                     'quotes_inside_independent_5m_range': observed[1] <= cp[1] + 1e-8 and observed[2] >= cp[2] - 1e-8,
                     'volume_within_100_shares': abs(volume - float(reference.volume)) <= 100}
            buckets.append({'ending_clock': end, **tests, 'all_passed': all(tests.values()),
                            'native_aggregate_ohlc': observed, 'independent_ohlc': cp,
                            'volume_difference_shares': volume - float(reference.volume)})
        daily_range = native.high.astype(float).max() <= float(daily['high']) + 1e-8 and native.low.astype(float).min() >= float(daily['low']) - 1e-8
        opening = slot != 0 or abs(float(native.open.iloc[0]) - float(daily['open'])) <= 1e-8
        positive_volume = native.volume.astype(float).sum() > 0
        passed = daily_range and opening and positive_volume and not amount_checks and all(b['all_passed'] for b in buckets)
        hours.append({'slot_zero_based': slot, 'native_minute_count': len(native),
            'quotes_inside_primary_daily_range': bool(daily_range), 'first_hour_open_gate_passed_or_inapplicable': opening,
            'positive_observed_hour_volume': bool(positive_volume), 'amount_price_volume_conflict_clocks': amount_checks,
            'all_12_independent_5m_buckets_passed': all(b['all_passed'] for b in buckets),
            'hour_strictly_passed': bool(passed), 'bucket_checks': buckets})
    return {'date': day, 'status': 'native_hour_local_comparison_complete', 'hours': hours,
            'hours_passed': sum(h['hour_strictly_passed'] for h in hours)}


def main():
    OUT.mkdir(exist_ok=True)
    source_path = BASE / 'sina_latest_minutes_20261009/sh600004_m1_1970_raw.txt'
    receipt_path = source_path.parent / 'sample_probe_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert sha(source_path) == receipt['source_sha256']
    raw = source_path.read_text()
    rows, _ = json.JSONDecoder().raw_decode(raw[raw.index('['):])
    minutes = pd.DataFrame(rows)
    daily_path = BASE / 'baostock/union_daily_factors/normalized/600004.SH.json'
    obj = json.loads(daily_path.read_text())
    daily = {row[0]: dict(zip(obj['fields'], row)) for row in obj['rows']}
    client = None
    results, queries = [], []
    try:
        for day in ['2026-09-22', '2026-09-23']:
            cached = OUT / ('sh.600004_' + day + '_' + day + '_5_p1.json')
            if cached.exists():
                query = json.loads(cached.read_text())
            else:
                if client is None:
                    client = PublicClient(OUT)
                    client.login()
                query = client.query('sh.600004', day, day, '5')
            assert query['error_code'] == '0' and query['frequency'] == '5'
            assert sha(query['wire_path']) == query['sha256']
            queries.append(query)
            result = audit(day, minutes, query, daily[day])
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != 'hours'}), flush=True)
    finally:
        if client is not None:
            client.close()
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'hour_local_diagnostic_complete',
        'source_code_sha256': sha(Path(__file__)), 'native_minute_source_path': str(source_path), 'native_minute_source_sha256': sha(source_path),
        'native_source_receipt_path': str(receipt_path), 'native_source_receipt_sha256': sha(receipt_path),
        'primary_daily_source_path': str(daily_path), 'primary_daily_source_sha256': sha(daily_path),
        'independent_coarse_queries': queries, 'results': results,
        'native_hours_surviving_strict_cross_source_tests': sum(r['hours_passed'] for r in results),
        'clock_labels_compared_as_observed_no_shift': True, 'coarse_bars_expanded_as_minutes': False,
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
        'scope': 'Two-stockday diagnostic only; no full-day price/volume gate waiver or panel admission'}
    (OUT / 'hour_local_concordance_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
