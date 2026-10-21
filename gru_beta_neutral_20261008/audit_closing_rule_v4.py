"""Independently check every added quote hour and immutable prior candidates."""
import ast
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
OUT = BASE / 'backfilled_panel_candidate_closing_rule_v4'
V3 = BASE / 'backfilled_panel_candidate_code_bridge_v3'
ART = ROOT / 'gru_research/artifacts'
HF = BASE / 'public_source_probes/huggingface'
RULE = BASE / 'public_source_probes/sse_historical_close_rule_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def normalize(values, members):
    result = np.full(values.shape, np.nan, np.float32)
    for k in range(values.shape[1]):
        x = values[:, k].astype(float)
        ref = x[members & np.isfinite(x)]
        if len(ref) < 2:
            continue
        lo, hi = np.percentile(ref, [1, 99])
        ref = np.minimum(np.maximum(ref, lo), hi)
        center, scale = ref.mean(), ref.std(ddof=0)
        scale = scale if scale > 0 else 1
        use = np.isfinite(x)
        result[use, k] = np.clip((np.clip(x[use], lo, hi) - center) / scale, -5, 5)
    return result


def main():
    started = time.monotonic()
    prep = json.loads((OUT / 'version_preparation_receipt.json').read_text())
    meta = json.loads((OUT / 'dataset/meta.json').read_text())
    assert not meta['production_ready'] and meta['research_candidate']
    hashes = []
    for directory in [ART / 'dataset', BASE / 'backfilled_panel_candidate/dataset', V3 / 'dataset', OUT / 'dataset']:
        contract = json.loads((directory / 'meta.json').read_text())
        for filename, expected in contract['files_sha256'].items():
            actual = sha(directory / filename)
            assert actual == expected, (str(directory), filename)
            hashes.append({'path': str(directory / filename), 'sha256': actual})
    for name, digest in prep['source_version_metadata'].items():
        assert sha(BASE / name / 'hour_panel_receipt.json') == digest
    for record in prep['copied_files'] + prep['slow_feature_files_reused']:
        assert sha(Path(record['source'])) == record['sha256']
        assert sha(Path(record['destination'])) == record['sha256']
    for record in prep['code_inputs']:
        assert sha(Path(record['source'])) == record['source_sha256']
    print('Verified all candidate arrays, original versions and copied daily features', flush=True)
    calendar = json.loads((ART / 'minute_calendar.json').read_text())
    dates, symbols = calendar['dates'], calendar['union_symbols']
    complete = np.load(OUT / 'dataset/hour_complete.npy', mmap_mode='r')
    previous = np.load(V3 / 'dataset/hour_complete.npy', mmap_mode='r')
    members = np.load(OUT / 'dataset/members.npy', mmap_mode='r')
    assert np.array_equal(members, np.load(ART / 'dataset/members.npy', mmap_mode='r'))
    assert not (previous & ~complete).any()
    added = complete & ~previous
    qualified = pq.read_table(RULE / 'qualified_historical_closing_statistics_v2.parquet').to_pandas()
    accepted = {(row.symbol, row.date) for row in qualified.itertuples()}
    total_checked = 0
    total_days = 0
    changed_symbols = []
    boundary_count = 0
    for u, symbol in enumerate(symbols):
        where = np.flatnonzero(added[:, u])
        if not len(where):
            continue
        changed_symbols.append(symbol)
        assert symbol.endswith('.SH')
        wanted = sorted({dates[t // 4] for t in where})
        assert all((symbol, day) in accepted and '2018-01-01' <= day < '2018-08-20' for day in wanted)
        rows = pq.read_table(OUT / 'symbol_hour_features' / (symbol + '.parquet')).to_pandas().set_index(['date', 'slot'])
        path = HF / 'snapshot/data/stock_1m/SH' / (symbol[:6] + '.parquet')
        quotes = pq.read_table(path, filters=[('timestamp', '>=', pd.Timestamp(min(wanted))),
                                           ('timestamp', '<', pd.Timestamp(max(wanted)) + pd.Timedelta(days=1))]).to_pandas()
        assert quotes.symbol.astype(str).eq(symbol[:6]).all() and quotes.exchange.eq('SH').all()
        quotes['date'] = quotes.timestamp.dt.strftime('%Y-%m-%d')
        selected = {day: frame.sort_values('timestamp') for day, frame in quotes[quotes.date.isin(wanted)].groupby('date')}
        changed_by_day = {day: [int(t % 4) for t in where if dates[t // 4] == day] for day in wanted}
        for day in wanted:
            table = selected[day]
            clocks = table.timestamp.dt.hour.to_numpy() * 60 + table.timestamp.dt.minute.to_numpy()
            expected_clock = np.r_[np.arange(571, 691), np.arange(781, 901)]
            regular = table.loc[np.isin(clocks, expected_clock)]
            assert len(regular) == 240 and np.array_equal(regular.timestamp.dt.hour.to_numpy() * 60 + regular.timestamp.dt.minute.to_numpy(), expected_clock)
            raw = regular[['open', 'high', 'low', 'close']].to_numpy(float).round(2)
            volumes = regular.volume.to_numpy(float).copy()
            boundary = table[(clocks == 570) & table.volume.gt(0)]
            assert len(boundary) <= 1
            if len(boundary):
                b = boundary.iloc[0]
                raw[0, 0] = round(float(b.open), 2)
                raw[0, 1] = max(raw[0, 1], round(float(b.high), 2))
                raw[0, 2] = min(raw[0, 2], round(float(b.low), 2))
                volumes[0] += b.volume
                boundary_count += 1
            matrix = raw.reshape(4, 60, 4)
            vol = volumes.reshape(4, 60)
            midnight = pd.Timestamp(day, tz='Asia/Shanghai').tz_convert('UTC').value
            for slot in changed_by_day[day]:
                row = rows.loc[(day, slot)]
                bar = matrix[slot]
                expected = [bar[0, 0], bar[:, 1].max(), bar[:, 2].min(), bar[-1, 3]]
                np.testing.assert_allclose(row[['raw_open', 'raw_high', 'raw_low', 'raw_close']].to_numpy(float), expected, atol=0, rtol=0)
                np.testing.assert_allclose(row[['open', 'high', 'low', 'close']].to_numpy(float), np.array(expected) * row.adjustment_factor, atol=1e-12, rtol=1e-12)
                assert row.volume == vol[slot].sum() and row.hour_complete
                assert row.signal_time == midnight + [630, 690, 840, 900][slot] * 60 * 10**9
                assert row.hour_source == 'corroborated_hf_preauction_closing_statistic'
                assert row.historical_closing_reference_corroborated
                if row.window_complete:
                    assert row.window_volume == vol[slot, 1:6].sum()
                    np.testing.assert_allclose(row.window_raw_price, bar[1:6, 0].mean(), atol=1e-12, rtol=1e-12)
                    np.testing.assert_allclose(row.window_price, bar[1:6, 0].mean() * row.adjustment_factor, atol=1e-12, rtol=1e-12)
                total_checked += 1
            total_days += 1
        if len(changed_symbols) % 50 == 0:
            print('Independently verified', total_checked, 'new raw quote hours in', len(changed_symbols), 'stocks', flush=True)
    assert total_checked == int(added.sum())
    old_hour = np.load(V3 / 'dataset/hour_ohlcv.npy', mmap_mode='r')
    new_hour = np.load(OUT / 'dataset/hour_ohlcv.npy', mmap_mode='r')
    for start in range(0, len(complete), 128):
        end = start + 128
        np.testing.assert_array_equal(new_hour[start:end][previous[start:end]], old_hour[start:end][previous[start:end]])
    first_2019 = 4 * next(i for i, day in enumerate(dates) if day >= '2019-01-01')
    for name in ['labels', 'execution_prices', 'execution_raw_prices', 'execution_complete', 'execution_tradable']:
        new = np.load(OUT / 'dataset' / (name + '.npy'), mmap_mode='r')
        old = np.load(V3 / 'dataset' / (name + '.npy'), mmap_mode='r')
        np.testing.assert_array_equal(new[first_2019:], old[first_2019:])
    # Different early observations change the compensated rolling accumulator's
    # rounding history. Source prices and all labels/executions remain exact.
    # Recompute every differing moving-average value from its finite window;
    # keep exact equality for the other 21 features and all missingness masks.
    new_features = np.load(OUT / 'dataset/hour_features.npy', mmap_mode='r')
    old_features = np.load(V3 / 'dataset/hour_features.npy', mmap_mode='r')
    exact_columns = [i for i in range(24) if i not in [9, 10, 11]]
    rounding_points = {}
    feature_maximum = 0.0
    rounding_count = 0
    for start in range(first_2019, len(complete), 64):
        a, b = new_features[start:start+64], old_features[start:start+64]
        np.testing.assert_array_equal(np.isnan(a), np.isnan(b))
        np.testing.assert_array_equal(a[:, :, exact_columns], b[:, :, exact_columns])
        finite = np.isfinite(a) & np.isfinite(b)
        different = finite & (a != b)
        if different.any():
            error = float(np.abs(a[different]-b[different]).max())
            feature_maximum = max(feature_maximum, error)
            assert error <= 1e-12
            for local_t, u, column in np.argwhere(different):
                assert int(column) in [9, 10, 11]
                rounding_points.setdefault(int(u), []).append((start + int(local_t), int(column)))
                rounding_count += 1
    direct_mean_error = 0.0
    for u, points in rounding_points.items():
        stock = pq.read_table(OUT / 'symbol_hour_features' / (symbols[u]+'.parquet')).to_pandas()
        old_stock = pq.read_table(V3 / 'symbol_hour_features' / (symbols[u]+'.parquet')).to_pandas()
        closes = stock.close.where(stock.hour_complete).to_numpy(float)
        prior_closes = old_stock.close.where(old_stock.hour_complete).to_numpy(float)
        for t, column in points:
            n = {9: 4, 10: 20, 11: 80}[column]
            observed = closes[t+1-n:t+1]
            np.testing.assert_array_equal(observed, prior_closes[t+1-n:t+1])
            assert np.isfinite(observed).all() and len(observed) == n
            independent_value = np.float32(observed[-1] / observed.mean() - 1)
            for value in [new_features[t,u,column], old_features[t,u,column]]:
                error = float(abs(float(value)-float(independent_value)))
                direct_mean_error = max(direct_mean_error, error)
                assert error <= 1e-12
    slow = np.load(OUT / 'slow_features/slow_raw_by_day.npy', mmap_mode='r')
    assert sha(OUT / 'slow_features/slow_raw_by_day.npy') == sha(V3 / 'slow_features/slow_raw_by_day.npy')
    X = np.load(OUT / 'dataset/X.npy', mmap_mode='r')
    old_X = np.load(V3 / 'dataset/X.npy', mmap_mode='r')
    for start in range(0, len(X), 64):
        np.testing.assert_array_equal(X[start:start+64, :, 24:], old_X[start:start+64, :, 24:])
    features = np.load(OUT / 'dataset/hour_features.npy', mmap_mode='r')
    sample = set(range(0, len(dates), 80))
    for day in ['2018-01-02', '2018-01-05', '2018-06-15', '2018-08-17', '2018-08-20', '2018-09-20', '2019-01-02', '2026-09-23']:
        if day in dates:
            sample.add(dates.index(day))
    maximum = 0.0
    for d in sorted(sample):
        expected_slow = normalize(slow[d], members[4*d])
        for slot in range(4):
            t = 4*d+slot
            expected = np.concatenate([normalize(features[t], members[t]), expected_slow], axis=1)
            actual = np.asarray(X[t])
            assert np.array_equal(np.isnan(actual), np.isnan(expected))
            use = np.isfinite(expected)
            error = float(np.abs(actual[use]-expected[use]).max()) if use.any() else 0
            maximum = max(maximum, error)
            np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=2e-6, equal_nan=True)
    windows = np.load(OUT / 'dataset/window80_complete.npy', mmap_mode='r')
    old_windows = np.load(V3 / 'dataset/window80_complete.npy', mmap_mode='r')
    assert not (old_windows & ~windows).any()
    gap = json.loads((OUT / 'gap_audit/receipt.json').read_text())
    old_gap = json.loads((V3 / 'gap_audit/receipt.json').read_text())
    assert gap['full_union_august_september'] == old_gap['full_union_august_september']
    result = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(Path(__file__)),
              'candidate_meta_sha256': sha(OUT / 'dataset/meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
              'array_sha_checks': hashes, 'unchanged_stock_files_checked': len(prep['copied_files']),
              'all_original_and_prior_candidates_preserved': True, 'added_raw_minute_quote_hours_verified': total_checked,
              'stockdays_with_added_hours_verified': total_days, 'stocks_with_added_hours': len(changed_symbols),
              'positive_volume_auction_boundaries_verified': boundary_count, 'lost_complete_hours': 0,
              'previous_complete_raw_and_adjusted_hour_prices_exactly_preserved': True,
              'all_2019_onward_labels_execution_and_21_hour_features_exactly_equal_v3': True,
              'all_2019_onward_hour_feature_missingness_exactly_equal_v3': True,
              'rolling_ma_floating_point_differences': rounding_count,
              'rolling_ma_max_absolute_difference_v3': feature_maximum,
              'all_differing_rolling_ma_values_independently_recomputed': True,
              'rolling_ma_direct_window_mean_max_absolute_error': direct_mean_error,
              'rolling_ma_numerical_tolerance': 1e-12,
              'initial_bitwise_audit_failure_and_diagnosis_preserved': 'independent_audit_attempts/attempt_1/failure_diagnosis.json',
              'all_98_slow_features_and_normalized_slow_X_values_exactly_equal_v3': True,
              'normalization_sample_sessions': len(sample), 'normalization_sample_full_crosssections': 4*len(sample),
              'independent_normalization_max_abs_error': maximum, 'added_complete80_windows': int((windows & ~old_windows).sum()),
              'lost_complete80_windows': 0, 'minimum_interval_missing_primary_trading_hours': gap['minimum_intervals']['missing_primary_trading_hours'],
              'minimum_interval_missing_hours_reduced': old_gap['minimum_intervals']['missing_primary_trading_hours'] - gap['minimum_intervals']['missing_primary_trading_hours'],
              'full_union_tail_missing_nonmember_primary_trading_hours': gap['full_union_august_september']['missing_nonmember_primary_trading_hours'],
              'synthetic_or_daily_substituted_minute_prices': False, 'exact_last_tick_trailing_minute_certified': False,
              'tick_extrema_or_live_source_admission_certified': False, 'production_ready': False,
              'elapsed_seconds': round(time.monotonic()-started, 2)}
    (OUT / 'independent_completion_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'array_sha_checks'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
