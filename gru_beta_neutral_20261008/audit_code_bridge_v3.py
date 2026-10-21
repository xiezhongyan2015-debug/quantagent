"""Independent raw-minute, array, unchanged-version and normalization checks."""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'backfilled_panel_candidate_code_bridge_v3'
V1 = BASE / 'backfilled_panel_candidate'
V2 = BASE / 'backfilled_panel_candidate_factor_bridge_v2'
ART = ROOT / 'gru_research/artifacts'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def independent_normalize(values, members):
    """Column-wise reference, without importing the production normalizer."""
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for column in range(values.shape[1]):
        x = values[:, column].astype(float)
        ref = x[members & np.isfinite(x)]
        if len(ref) < 2:
            continue
        lo, hi = np.percentile(ref, [1, 99])
        clipped_ref = np.minimum(np.maximum(ref, lo), hi)
        center, scale = clipped_ref.mean(), clipped_ref.std(ddof=0)
        if scale == 0:
            scale = 1
        valid = np.isfinite(x)
        result[valid, column] = np.minimum(np.maximum((np.minimum(np.maximum(x[valid], lo), hi) - center) / scale, -5), 5)
    return result


def main():
    started = time.monotonic()
    meta = json.loads((OUT / 'dataset/meta.json').read_text())
    assert meta['research_candidate'] and not meta['production_ready']
    metadata = json.loads((ART / 'minute_calendar.json').read_text())
    symbols, dates = metadata['union_symbols'], metadata['dates']
    immutable_checks = []
    for directory in [ART / 'dataset', V1 / 'dataset', OUT / 'dataset']:
        obj = json.loads((directory / 'meta.json').read_text())
        for filename, expected in obj['files_sha256'].items():
            actual = sha(directory / filename)
            assert actual == expected, (str(directory), filename)
            immutable_checks.append({'path': str(directory / filename), 'sha256': actual})
    prep = json.loads((OUT / 'version_preparation_receipt.json').read_text())
    for record in prep['copied_files']:
        assert sha(Path(record['source'])) == record['sha256']
        assert sha(Path(record['destination'])) == record['sha256']
    for name, expected in prep['source_version_metadata'].items():
        assert sha(BASE / name / 'hour_panel_receipt.json') == expected
    for record in prep['code_inputs']:
        assert sha(Path(record['source'])) == record['source_sha256']
    print('Verified original, v1, v2 and unchanged stock files', flush=True)
    member = np.load(OUT / 'dataset/members.npy', mmap_mode='r')
    complete = np.load(OUT / 'dataset/hour_complete.npy', mmap_mode='r')
    old_complete = np.load(V1 / 'dataset/hour_complete.npy', mmap_mode='r')
    old_window = np.load(V1 / 'dataset/window80_complete.npy', mmap_mode='r')
    window = np.load(OUT / 'dataset/window80_complete.npy', mmap_mode='r')
    assert np.array_equal(member, np.load(ART / 'dataset/members.npy', mmap_mode='r'))
    difference = complete != old_complete
    assert not (old_complete & ~complete).any()
    counts = difference.sum(axis=0)
    assert {symbols[i]: int(n) for i, n in enumerate(counts) if n} == {'302132.SZ': 1132}
    windows_added = window & ~old_window
    assert not (old_window & ~window).any()
    assert int(windows_added.sum()) == 1132
    observations = json.loads((BASE / 'official_old_code_minute_audit_20261009/receipt.json').read_text())
    events = pq.read_table(BASE / 'public_source_probes/baostock/old_code_factor_identity_20261009/canonical_observed_fore_factor_events.parquet').to_pandas()
    frame = pq.read_table(OUT / 'symbol_hour_features/302132.SZ.parquet').to_pandas().set_index(['date', 'slot'])
    chosen = events[events.symbol.eq('302132.SZ')].sort_values('effective_date')
    event_dates = chosen.effective_date.to_numpy(str)
    factor_values = chosen.effective_fore_factor.to_numpy(float)
    checks = 0
    signal_minutes = [630, 690, 840, 900]
    for record in observations['payloads']:
        if not record['admitted']:
            continue
        assert record['canonical_symbol'] == '302132.SZ'
        day = record['date']
        table = pq.read_table(record['path']).to_pandas().sort_values('datetime_nano')
        assert table['股票代码'].eq('300114').all() and len(table) == 240
        prices = table[['open', 'high', 'low', 'close']].to_numpy(float).reshape(4, 60, 4)
        volumes = table.volume.to_numpy(float).reshape(4, 60)
        factor = factor_values[np.searchsorted(event_dates, day, side='right') - 1]
        midnight = pd.Timestamp(day, tz='Asia/Shanghai').tz_convert('UTC').value
        for slot in range(4):
            row = frame.loc[(day, slot)]
            expected = np.array([prices[slot, 0, 0], prices[slot, :, 1].max(), prices[slot, :, 2].min(), prices[slot, -1, 3]])
            np.testing.assert_allclose(row[['raw_open', 'raw_high', 'raw_low', 'raw_close']].to_numpy(float), expected, atol=0, rtol=0)
            np.testing.assert_allclose(row[['open', 'high', 'low', 'close']].to_numpy(float), expected * factor, atol=1e-12, rtol=1e-12)
            assert row.volume == volumes[slot].sum() and row.adjustment_factor == factor
            assert row.signal_time == midnight + signal_minutes[slot] * 60 * 10**9
            assert row.source_payload_sha256 == record['sha256'] and row.source_observed_symbol == '300114.SZ'
            assert row.hour_complete and row.window_complete
            assert row.window_volume == volumes[slot, 1:6].sum()
            np.testing.assert_allclose(row.window_raw_price, prices[slot, 1:6, 0].mean(), atol=1e-12, rtol=1e-12)
            np.testing.assert_allclose(row.window_price, prices[slot, 1:6, 0].mean() * factor, atol=1e-12, rtol=1e-12)
            checks += 1
    assert checks == 1132
    print('Verified all 1132 added hours against unchanged raw minute payloads', flush=True)
    new_slow = np.load(OUT / 'slow_features/slow_raw_by_day.npy', mmap_mode='r')
    old_slow = np.load(V1 / 'slow_features/slow_raw_by_day.npy', mmap_mode='r')
    slow_receipt = json.loads((OUT / 'slow_features/slow_panel_receipt.json').read_text())
    names = slow_receipt['names']
    pit_names = ['d_log_total_cap'] + [n for n in names if n.startswith('f_') or n.startswith('age_')]
    # The frozen financial/date-derived block is independently identified by
    # the unchanged last 18 columns plus total market capitalization.
    pit_indices = [names.index('d_log_total_cap')] + list(range(80, 98))
    assert len(set(pit_indices)) == 19
    np.testing.assert_array_equal(new_slow[:, :, pit_indices], old_slow[:, :, pit_indices])
    X = np.load(OUT / 'dataset/X.npy', mmap_mode='r')
    hour = np.load(OUT / 'dataset/hour_features.npy', mmap_mode='r')
    sample = set(range(0, len(dates), 80))
    for day in ['2019-12-13', '2019-12-16', '2023-12-11', '2023-12-29', '2024-01-08', '2024-12-13',
                '2025-02-14', '2025-02-17', '2025-02-18', '2025-06-13', '2026-08-06', '2026-09-23']:
        sample.add(dates.index(day))
    maximum_error = 0.0
    for d in sorted(sample):
        expected_slow = independent_normalize(new_slow[d], member[4*d])
        for slot in range(4):
            t = 4*d + slot
            expected = np.concatenate([independent_normalize(hour[t], member[t]), expected_slow], axis=1)
            actual = np.asarray(X[t])
            assert np.array_equal(np.isnan(actual), np.isnan(expected))
            finite = np.isfinite(expected)
            error = float(np.max(np.abs(actual[finite] - expected[finite]))) if finite.any() else 0.0
            maximum_error = max(maximum_error, error)
            np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=2e-6, equal_nan=True)
    gap = json.loads((OUT / 'gap_audit/receipt.json').read_text())
    old_gap = json.loads((V1 / 'gap_audit/receipt.json').read_text())
    assert old_gap['minimum_intervals']['missing_primary_trading_hours'] - gap['minimum_intervals']['missing_primary_trading_hours'] == 1132
    assert gap['full_union_august_september'] == old_gap['full_union_august_september']
    years = np.repeat([int(d[:4]) for d in dates], 4)
    additions = {str(year): int((windows_added[years == year] & member[years == year]).sum()) for year in sorted(set(years))}
    result = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(Path(__file__)),
              'candidate_meta_sha256': sha(OUT / 'dataset/meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
              'array_sha_checks': immutable_checks, 'unchanged_source_stock_files_checked': len(prep['copied_files']),
              'original_and_preceding_candidates_unchanged': True, 'added_hours_raw_minute_checks': checks,
              'old_declared_raw_symbol_and_all_payload_hashes_preserved': True, 'added_complete_hours': int(difference.sum()),
              'lost_complete_hours': 0, 'added_complete80_windows': int(windows_added.sum()), 'lost_complete80_windows': 0,
              'member_complete80_additions_by_year': additions, 'frozen_PIT_19_columns_exactly_equal_v1': True,
              'normalization_sample_sessions': len(sample), 'normalization_sample_full_crosssections': 4 * len(sample),
              'independent_normalization_max_abs_error': maximum_error, 'minimum_interval_missing_trading_hours': gap['minimum_intervals']['missing_primary_trading_hours'],
              'full_union_tail_missing_nonmember_trading_hours': gap['full_union_august_september']['missing_nonmember_primary_trading_hours'],
              'all_coverage_targets_passed': all(r['target_passed'] for r in gap['coverage_by_year'] if r['target'] is not None),
              'synthetic_prices': False, 'production_ready': False, 'elapsed_seconds': round(time.monotonic() - started, 2)}
    (OUT / 'independent_completion_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'array_sha_checks'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
