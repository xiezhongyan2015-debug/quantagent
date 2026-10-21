"""Assemble an audited panel without materializing overlapping 80-hour windows."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import warnings

sys.path.insert(0, str(Path(__file__).parent / '.deps'))
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / 'gru_research/artifacts'
TZ_OFFSET = 8 * 3600 * 10**9
NS_DAY = 86400 * 10**9


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + '\n')
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def normalize_cross_section(raw, members):
    """Fit clipping and z-score solely on the actual historical membership row.

    NaNs retain their missingness for runtime masks. Statistics do not depend on
    future times, labels, or whether a stock later has an eligible input window.
    """
    x = np.asarray(raw, dtype=np.float64).copy()
    x[~np.isfinite(x)] = np.nan
    ref = x[np.asarray(members, dtype=bool)]
    if ref.shape[0] == 0:
        raise ValueError('Empty historical reference membership')
    count = np.isfinite(ref).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        lo, hi = np.nanquantile(ref, [0.01, 0.99], axis=0)
        ref_clipped = np.clip(ref, lo, hi)
        mean = np.nanmean(ref_clipped, axis=0)
        std = np.nanstd(ref_clipped, axis=0, ddof=0)
    valid = count >= 2
    lo, hi, mean = [np.where(valid, a, np.nan) for a in (lo, hi, mean)]
    safe_std = np.where(std > 0, std, 1.0)
    output = np.clip((np.clip(x, lo, hi) - mean) / safe_std, -5, 5)
    output[~np.isfinite(x)] = np.nan
    return output.astype(np.float32), {'count': count, 'mean': mean, 'std': std, 'lo': lo, 'hi': hi}


def validate_slow_dates(batch):
    """Verify historical query dates and EOD values do not become visible early."""
    day = pd.to_datetime(batch.date)
    violations = {}
    for column in ['financial_available_date', 'report_version_available_date', 'market_available_date']:
        if column not in batch:
            raise ValueError('PIT provenance column missing: ' + column)
        values = pd.to_datetime(batch[column])
        violations[column] = int((values.notna() & (values > day)).sum())
    for column in ['event_query_date', 'report_query_date', 'market_query_date', 'daily_source_date']:
        if column not in batch:
            raise ValueError('PIT query/source provenance missing: ' + column)
        values = pd.to_datetime(batch[column])
        violations[column] = int((values.notna() & (values >= day)).sum())
    for column in ['report_period']:
        values = pd.to_datetime(batch[column])
        violations[column] = int((values.notna() & (values > day)).sum())
    if any(violations.values()):
        raise ValueError('Future-visible slow data: ' + json.dumps(violations))
    return violations


def verify_labels(prices, labels, times, earliest, label_ends, members):
    """Independent label reconstruction from the retained execution price tape."""
    bad, compared = 0, 0
    for delta in range(1, 5):
        start = np.asarray(prices[:-delta], dtype=np.float64)
        end = np.asarray(prices[delta:], dtype=np.float64)
        expected = np.log(end / start)
        actual = np.asarray(labels[:-delta, :, delta - 1], dtype=np.float64)
        finite = np.isfinite(actual)
        bad += int(np.count_nonzero(finite & (~np.isfinite(expected) | ~np.isclose(actual, expected, atol=2e-6, rtol=2e-5))))
        compared += int(finite.sum())
        actual_ends = np.asarray(label_ends[:-delta, delta - 1])
        valid_ends = actual_ends >= 0
        expected_ends = np.asarray(earliest[delta:]) + 5 * 60 * 10**9
        if np.any(actual_ends[valid_ends] != expected_ends[valid_ends]):
            raise ValueError('Label ends differ from their actual five-minute execution windows')
    if np.any(earliest <= times):
        raise ValueError('An execution window is not strictly after its signal')
    mature = (label_ends >= 0).all(axis=1)
    if np.any(label_ends[mature, 0] <= earliest[mature]) or np.any(np.diff(label_ends[mature], axis=1) <= 0):
        raise ValueError('Label ends do not strictly follow common entry and horizon order')
    if bad or compared == 0:
        raise ValueError(f'Independent execution price labels failed: {bad}/{compared}')
    return {'compared_finite_labels': compared, 'mismatches': bad,
            'actual_entry_strictly_after_signal': True, 'actual_label_ends_ordered': True,
            'actual_label_ends_match_execution_tape': True}


def assemble(output, tests_receipt):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    calendar = json.loads((ART / 'minute_calendar.json').read_text())
    minute = json.loads((ART / 'minute_feature_metadata.json').read_text())
    slow = json.loads((ART / 'slow_manifest.json').read_text())
    selection = json.loads((ART / 'slow_factor_selection.json').read_text())
    tests = json.loads(Path(tests_receipt).read_text())
    if tests.get('passed') is not True or not tests.get('source_code_sha256'):
        raise ValueError('Independent source tests have not passed')
    for filename, digest in tests['source_code_sha256'].items():
        if sha256(ROOT / 'gru_research' / filename) != digest:
            raise ValueError('Source changed since validation: ' + filename)
    dates, symbols = calendar['dates'], calendar['union_symbols']
    if dates[0] != '2018-01-02' or dates[-1] != '2026-09-23':
        raise ValueError('The requested full history is not present')
    if list(minute['union_symbols']) != symbols or list(minute['calendar_dates']) != dates:
        raise ValueError('Minute grids disagree')
    rows, universe = 4 * len(dates), len(symbols)
    members = np.load(ART / 'minute_members.npy', mmap_mode='r')
    hour = np.load(ART / 'minute_hour_features.npy', mmap_mode='r')
    if hour.shape != (rows, universe, 24):
        raise ValueError('Incorrect real minute feature shape')
    if not np.array_equal(members[::4], members[1::4]) or not np.array_equal(members[::4], members[2::4]) or not np.array_equal(members[::4], members[3::4]):
        raise ValueError('Membership changes within a session')
    if np.any(members.sum(axis=1) < 498) or np.any(members.sum(axis=1) > 502):
        raise ValueError('Unverified historical pool size')
    names = list(minute['hour_features']) + list(slow['daily16']) + list(slow['factors']) + list(slow['fund16']) + list(slow['ages2'])
    if len(slow['factors']) != len(selection['selected_ids']) or len(selection['selected_ids']) > 64:
        raise ValueError('Frozen factor selection does not match panel inputs')
    groups, cursor = {}, 0
    for group, fields in [('hour', minute['hour_features']), ('daily', slow['daily16']), ('factors', slow['factors']), ('fund', slow['fund16']), ('ages', slow['ages2'])]:
        groups[group] = list(range(cursor, cursor + len(fields)))
        cursor += len(fields)
    slow_names = names[24:]
    slow_path = Path(slow['raw_path'])
    if sha256(slow_path) != slow['raw_sha256'] or sha256(ART / 'slow_factor_selection.json') != slow['factor_selection_sha256']:
        raise ValueError('Frozen slow features or selection changed')
    temp = np.lib.format.open_memmap(output / 'slow_raw_by_day.npy', mode='w+', dtype=np.float32, shape=(len(dates), universe, len(slow_names)))
    temp[:] = np.nan
    industry_day = np.zeros((len(dates), universe), np.int16)
    seen = np.zeros((len(dates), universe), bool)
    days = np.asarray(dates, dtype='datetime64[D]').astype(np.int64)
    symbol_index = {s: i for i, s in enumerate(symbols)}
    available_columns = pq.ParquetFile(slow_path).schema_arrow.names
    provenance = ['financial_available_date', 'report_version_available_date', 'market_available_date', 'event_query_date', 'report_query_date', 'market_query_date', 'daily_source_date', 'report_period']
    cols = ['date', 'symbol', 'industry_code'] + slow_names + provenance
    if not set(cols) <= set(available_columns):
        raise ValueError('Slow panel lacks requested inputs or provenance')
    pit_checks = {c: 0 for c in provenance}
    for batch in pq.ParquetFile(slow_path).iter_batches(batch_size=65536, columns=cols):
        frame = batch.to_pandas()
        for c, n in validate_slow_dates(frame).items():
            pit_checks[c] += n
        day_values = pd.to_datetime(frame.date).values.astype('datetime64[D]').astype(np.int64)
        di = np.searchsorted(days, day_values)
        si = frame.symbol.map(symbol_index).to_numpy()
        if np.any(di >= len(days)) or np.any(di < 0) or not np.array_equal(days[di], day_values) or np.any(pd.isna(si)):
            raise ValueError('Slow panel contains unknown session or security')
        si = si.astype(np.int64)
        combined = di * universe + si
        if len(np.unique(combined)) != len(combined) or seen[di, si].any():
            raise ValueError('Duplicate date/security in slow panel')
        if not members[4 * di, si].all():
            raise ValueError('Slow rows are not actual historical members')
        seen[di, si] = True
        values = frame[slow_names].to_numpy(np.float32)
        values[~np.isfinite(values)] = np.nan
        temp[di, si] = values
        industry_day[di, si] = frame.industry_code.to_numpy(np.int16)
    if not np.array_equal(seen, members[::4]):
        raise ValueError('Slow features are missing actual member keys')
    temp.flush()
    X = np.lib.format.open_memmap(output / 'X.npy', mode='w+', dtype=np.float32, shape=(rows, universe, len(names)))
    X[:] = np.nan
    industry = np.lib.format.open_memmap(output / 'industry.npy', mode='w+', dtype=np.int16, shape=(rows, universe))
    finite_counts = np.zeros(len(names), np.int64)
    for d in range(len(dates)):
        slow_normalized, _ = normalize_cross_section(temp[d], members[4 * d])
        for slot in range(4):
            t = 4 * d + slot
            X[t, :, :24], _ = normalize_cross_section(hour[t], members[t])
            X[t, :, 24:] = slow_normalized
            industry[t] = industry_day[d]
            finite_counts += np.isfinite(X[t, members[t]]).sum(axis=0)
        if (d + 1) % 50 == 0 or d + 1 == len(dates):
            X.flush()
            atomic_json(ART / 'assembly_progress.json', {'status': 'normalizing', 'completed_days': d + 1, 'total_days': len(dates), 'elapsed_seconds': round(time.time() - started, 1)})
    X.flush()
    industry.flush()
    mappings = {'hour_complete': 'minute_hour_complete', 'members': 'minute_members', 'labels': 'minute_labels4', 'times': 'minute_times', 'label_end_times': 'minute_label_end_times', 'earliest_execution_times': 'minute_earliest_execution_times', 'execution_prices': 'minute_execution_prices', 'execution_raw_prices': 'minute_execution_raw_prices', 'execution_complete': 'minute_execution_complete', 'execution_tradable': 'minute_execution_tradable', 'execution_all_flat': 'minute_execution_all_flat', 'hour_ohlcv': 'minute_hour_ohlcv'}
    for target, source in mappings.items():
        shutil.copyfile(ART / (source + '.npy'), output / (target + '.npy'))
    times, earliest, ends = [np.load(output / (n + '.npy'), mmap_mode='r') for n in ['times', 'earliest_execution_times', 'label_end_times']]
    labels = np.load(output / 'labels.npy', mmap_mode='r')
    prices = np.load(output / 'execution_prices.npy', mmap_mode='r')
    label_checks = verify_labels(prices, labels, times, earliest, ends, members)
    complete = np.load(output / 'hour_complete.npy', mmap_mode='r')
    prefix = np.cumsum(~complete, axis=0, dtype=np.int32)
    prefix = np.concatenate((np.zeros((1, universe), np.int32), prefix))
    historical80 = np.zeros((rows, universe), bool)
    historical80[79:] = prefix[80:] == prefix[:-80]
    finite_labels = np.isfinite(labels).all(axis=2)
    eligible = members & historical80 & finite_labels
    coverage = {}
    for year in range(2018, 2027):
        mask = np.asarray([d.startswith(str(year)) for d in dates]).repeat(4)
        denominator = int(members[mask].sum())
        coverage[str(year)] = {'pool_stock_hours': denominator, 'complete_80_hours': int((members & historical80)[mask].sum()), 'all_4_labels_eligible_stock_hours': int(eligible[mask].sum()), 'cross_sections_min_100': int((eligible[mask].sum(axis=1) >= 100).sum())}
        if year >= 2019 and coverage[str(year)]['cross_sections_min_100'] == 0:
            raise ValueError('A requested training/evaluation year has no eligible samples: ' + str(year))
    atomic_json(output / 'symbols.json', symbols)
    atomic_json(output / 'feature_names.json', names)
    for source in ['minute_quality_report.json', 'minute_source_fingerprints.json',
                   'minute_volume_crosscheck.json', 'minute_actual_data_verification.json',
                   'slow_full_validation.json', 'slow_independent_pit_spotcheck.json',
                   'slow_factor_selection.json', 'minute_feature_metadata.json',
                   'slow_manifest.json']:
        shutil.copyfile(ART / source, output / source)
    atomic_json(output / 'dataset_audit.json', {'passed': True, 'full_calendar_sessions': len(dates), 'union_securities': universe, 'source_tests_receipt': tests, 'PIT_date_violations': pit_checks, 'label_checks': label_checks, 'coverage_by_year': coverage, 'feature_member_finite_coverage': {name: int(n) / int(members.sum()) for name, n in zip(names, finite_counts)}, 'industry_policy': slow['industry_code_policy'], 'nonnormalized_price_tape_retained': True, 'source_minute_definition': minute['source_minute_timestamp_semantics'], 'readonly_raw_sources': True})
    file_hashes = {path.name: sha256(path) for path in sorted(output.iterdir()) if path.suffix in ['.npy', '.json'] and path.name not in ['meta.json', 'slow_raw_by_day.npy']}
    fingerprint = hashlib.sha256(json.dumps(file_hashes, sort_keys=True).encode()).hexdigest()
    meta = {'schema_version': 1, 'production_ready': True, 'causality_audit': {'passed': True, 'receipt': 'dataset_audit.json'}, 'data_fingerprint': fingerprint, 'files_sha256': file_hashes, 'timestamp_format': 'utc_ns', 'timezone': 'Asia/Shanghai', 'groups': groups, 'n_industries': max(1, int(industry.max()) + 1), 'features': names, 'factor_selection_sha256': slow['factor_selection_sha256'], 'history': {'start': dates[0], 'end': dates[-1]}, 'normalization': '1/99 percentile clipping, population z-score, +-5 cap; current true members at each historical timestamp; NaNs retain runtime missing masks', 'sequence_length': 80, 'raw_market_data_is_synthetic': False, 'coverage_by_year': coverage, 'hour_ohlcv_columns': minute['hour_ohlcv_columns'], 'future_forecast_validation': '60 complete trading days begins after final model/config freeze; historical tests were previously exposed research data'}
    atomic_json(output / 'meta.json', meta)
    atomic_json(ART / 'assembly_progress.json', {'status': 'complete', 'dataset': str(output), 'shape': list(X.shape), 'data_fingerprint': fingerprint, 'elapsed_seconds': round(time.time() - started, 1)})
    print(json.dumps({'event': 'dataset_ready', 'shape': list(X.shape), 'dataset': str(output), 'data_fingerprint': fingerprint, 'coverage': coverage}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ART / 'dataset')
    parser.add_argument('--tests-receipt', type=Path, default=ART / 'source_tests_receipt.json')
    args = parser.parse_args()
    assemble(args.output, args.tests_receipt)


if __name__ == '__main__':
    main()
