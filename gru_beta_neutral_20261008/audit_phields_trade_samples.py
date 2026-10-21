"""Audit actual integer trades without filling minutes or changing the panel."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/phields_trades_20261009'
NAME = 'backfilled_panel_candidate_canonical_ipo_v6'
DATE = '2026-09-23'
PRICE = ['open', 'high', 'low', 'close']
GRID = list(range(570, 690)) + list(range(780, 900))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def records(frame):
    return json.loads(frame.to_json(orient='records'))


def regular(frame):
    return frame[frame.time_s.eq(33900) | frame.time_s.between(34200, 41400) |
                 frame.time_s.between(46800, 54000)].copy()


def group(frame, model, width):
    """Two explicit diagnostics: floor clock and closed-right clock, no tuning."""
    work = regular(frame)
    if model == 'floor_second_left_interval':
        minute = work.time_s // 60
    else:
        assert model == 'closed_right_second_interval'
        minute = (work.time_s - 1) // 60
    work['left_minute'] = minute
    for second, left_minute in [(33900, 570), (34200, 570), (41400, 689),
                                (46800, 780), (54000, 899)]:
        work.loc[work.time_s.eq(second), 'left_minute'] = left_minute
    assert work.left_minute.isin(GRID).all()
    work['bucket'] = work.left_minute // width * width
    result = work.groupby('bucket', sort=True).agg(
        open=('price_x10000', 'first'), high=('price_x10000', 'max'),
        low=('price_x10000', 'min'), close=('price_x10000', 'last'),
        volume=('volume', 'sum'), observed_trade_count=('tran_id', 'size'))
    result[PRICE] = result[PRICE].astype(float) / 10000
    return result


def compare(a, b):
    common = sorted(set(a.index) & set(b.index))
    x, y = a.loc[common], b.loc[common]
    equal = np.isclose(x[PRICE].to_numpy(float), y[PRICE].to_numpy(float), rtol=0, atol=1e-8)
    vd = x.volume.to_numpy(np.int64) - y.volume.to_numpy(np.int64)
    tests = (equal[:, 0] & equal[:, 3] &
             (x.high.to_numpy(float) <= y.high.to_numpy(float) + 1e-8) &
             (x.low.to_numpy(float) >= y.low.to_numpy(float) - 1e-8) & (abs(vd) <= 100))
    return {
        'common_observed_intervals': len(common),
        'exact_price_intervals_by_field': {f: int(equal[:, i].sum()) for i, f in enumerate(PRICE)},
        'volume_exact_intervals': int((vd == 0).sum()),
        'volume_within_100_intervals': int((abs(vd) <= 100).sum()),
        'open_close_range_volume_diagnostic_pass_intervals': int(tests.sum()),
        'volume_difference_shares': int(vd.sum()),
        'maximum_absolute_volume_difference_shares': int(abs(vd).max()),
        'unobserved_reference_intervals': sorted(set(b.index) - set(a.index)),
        'failed_common_interval_left_labels': [int(c) for c, good in zip(common, tests) if not good],
    }


def main():
    path = OUT / 'actual_trade_sample_diagnostics.json'
    assert not path.exists()
    acquisition_path = OUT / 'verified_whole_sample_acquisition.json'
    acquisition = json.loads(acquisition_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_phields_verified_trade_samples.py') == acquisition['source_code_sha256']
    info_path = OUT / 'dataset_info_blobs.json'
    assert sha(info_path) == acquisition['dataset_metadata_sha256']
    info = json.loads(info_path.read_text()); published = {r['rfilename']: r for r in info['siblings']}
    assert info['sha'] == acquisition['pinned_revision'] and info['gated'] is False
    calendar_path = OUT / 'metadata_ticker_calendar.parquet'
    assert sha(calendar_path) == published['metadata/ticker_calendar.parquet']['lfs']['sha256']
    inventory = {(r['ticker'], r['trade_date']): r for r in pq.read_table(calendar_path).to_pylist()}
    candidate = BASE / NAME / 'dataset'
    meta_path = candidate / 'meta.json'; meta = json.loads(meta_path.read_text())
    for filename in ['members.npy', 'hour_complete.npy']:
        assert sha(candidate / filename) == meta['files_sha256'][filename]
    complete = np.load(candidate / 'hour_complete.npy', mmap_mode='r')
    members = np.load(candidate / 'members.npy', mmap_mode='r')
    local_calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    local_calendar = json.loads(local_calendar_path.read_text())
    day_index = local_calendar['dates'].index(DATE)
    results, payloads, references = [], [], []
    reference_root = OUT / 'sample_references'
    reference_receipt_path = reference_root / 'bounded_reference_acquisition.json'
    reference_receipt = json.loads(reference_receipt_path.read_text())
    assert 'completed_utc' in reference_receipt
    assert sha(ROOT / 'gru_beta_neutral_20261008/acquire_phields_sample_references.py') == reference_receipt['source_code_sha256']
    for record in acquisition['results']:
        assert record['whole_payload_LFS_SHA256_verified'] and record['status'].startswith('complete_whole')
        source_path = Path(record['path'])
        assert source_path.stat().st_size == published[record['filename']]['size']
        assert sha(source_path) == record['sha256'] == published[record['filename']]['lfs']['sha256']
        prefix = record['filename'].split('code_prefix=')[1].split('/')[0]
        codes = ['000006', '000099'] if prefix == '00' else ['600004']
        parquet = pq.ParquetFile(source_path)
        pieces, scanned = [], []
        for i in range(parquet.num_row_groups):
            stats = parquet.metadata.row_group(i).column(0).statistics
            if any(stats.min <= code <= stats.max for code in codes):
                table = parquet.read_row_group(i)
                selected = table.filter(pc.is_in(table['ticker'], value_set=pa.array(codes)))
                pieces.append(selected)
                scanned.append({'row_group': i, 'source_rows': table.num_rows,
                                'ticker_min': stats.min, 'ticker_max': stats.max,
                                'selected_rows': selected.num_rows})
        table = pa.concat_tables(pieces)
        selected_path = OUT / ('diagnostic_selected_trades_20260923_' + prefix + '.parquet')
        assert pq.read_table(selected_path).equals(table), 'Existing selected rows must exactly match the immutable whole source'
        payloads.append({'source_path': str(source_path), 'whole_source_sha256': sha(source_path),
                         'whole_source_bytes': source_path.stat().st_size, 'source_parquet_rows': parquet.metadata.num_rows,
                         'source_row_groups': parquet.num_row_groups, 'row_groups_scanned': scanned,
                         'selection_path': str(selected_path), 'selection_sha256': sha(selected_path),
                         'selected_rows': table.num_rows, 'schema': str(table.schema)})
        frame = table.to_pandas()
        for code in codes:
            raw = frame[frame.ticker.eq(code)].copy()
            inv = inventory[code, DATE]
            assert len(raw) == inv['row_count']
            assert int(raw.time_s.min()) == inv['min_time_s'] and int(raw.time_s.max()) == inv['max_time_s']
            assert not raw.isna().any().any()
            sorted_frame = raw.sort_values(['time_s', 'tran_id'], kind='stable').reset_index(drop=True)
            symbol = code + ('.SZ' if prefix == '00' else '.SH')
            daily_path = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
            daily_obj = json.loads(daily_path.read_text())
            day = next(dict(zip(daily_obj['fields'], r)) for r in daily_obj['rows'] if r[0] == DATE)
            assert day['adjustflag'] == '3' and day['tradestatus'] == '1'
            normal = regular(sorted_frame)
            volume = sum(int(v) for v in normal.volume)
            amount_scaled = sum(int(p) * int(v) for p, v in zip(normal.price_x10000, normal.volume))
            prices_scaled = [int(normal.price_x10000.iloc[0]), int(normal.price_x10000.max()),
                             int(normal.price_x10000.min()), int(normal.price_x10000.iloc[-1])]
            checks = {f + '_exact': Decimal(prices_scaled[i]) / 10000 == Decimal(day[f]) for i, f in enumerate(PRICE)}
            checks.update(volume_exact=Decimal(volume) == Decimal(day['volume']),
                          calculated_trade_price_times_volume_amount_exact=Decimal(amount_scaled) / 10000 == Decimal(day['amount']))
            column = local_calendar['union_symbols'].index(symbol)
            start = day_index * 4
            coarse_path = (BASE / 'public_source_probes/sina_hour_local_concordance_20261009/sh.600004_2026-09-23_2026-09-23_5_p1.json'
                           if prefix == '60' else reference_root / ('sz.' + code + '_2026-09-23_2026-09-23_5_p1.json'))
            coarse = None
            if coarse_path.exists():
                query = json.loads(coarse_path.read_text())
                assert sha(query['wire_path']) == query['sha256']
                assert query['frequency'] == '5' and query['adjustflag'] == '3' and query['error_code'] == '0'
                source_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
                if source_rows:
                    coarse = pd.DataFrame(source_rows, columns=query['fields'])
                    ct = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
                    coarse.index = ct.dt.hour.to_numpy() * 60 + ct.dt.minute.to_numpy() - 5
                    coarse[PRICE] = coarse[PRICE].astype(float); coarse['volume'] = coarse.volume.astype(np.int64)
                    assert len(coarse) == 48 and set(coarse.index) == set(GRID[::5])
                    references.append({'path': str(coarse_path), 'sha256': sha(coarse_path),
                                       'wire_path': query['wire_path'], 'wire_sha256': query['sha256']})
            original = None
            if prefix == '60':
                original_path = BASE / 'public_source_probes/original_recent_sina_control_20261009/600004_20260923.parquet'
                original = pq.read_table(original_path).to_pandas()
                ot = pd.to_datetime(original.datetime)
                original.index = ot.dt.hour.to_numpy() * 60 + ot.dt.minute.to_numpy()
                assert list(original.index) == GRID and len(original) == 240
                references.append({'path': str(original_path), 'sha256': sha(original_path), 'kind': 'immutable_original_positive_control'})
            models = []
            for model in ['floor_second_left_interval', 'closed_right_second_interval']:
                minutes = group(sorted_frame, model, 1)
                five = group(sorted_frame, model, 5)
                minute_path = OUT / (code + '_20260923_' + model + '_observed_trade_minutes.parquet')
                assert not minute_path.exists()
                pq.write_table(pa.Table.from_pandas(minutes.reset_index(), preserve_index=False), minute_path, compression='zstd')
                item = {'clock_diagnostic_model': model, 'observed_nonempty_minutes': len(minutes),
                        'unobserved_trade_minute_left_labels': sorted(set(GRID) - set(minutes.index)),
                        'derived_minute_path': str(minute_path), 'derived_minute_sha256': sha(minute_path),
                        'derived_minutes_volume_equals_regular_trades': int(minutes.volume.sum()) == volume,
                        'observed_5m_buckets': len(five), 'hours': []}
                if original is not None:
                    item['minute_vs_original_positive_control'] = compare(minutes, original)
                    item['original_volume_at_unobserved_trade_minutes'] = {str(i): int(original.loc[i].volume)
                                                                          for i in sorted(set(GRID) - set(minutes.index))}
                if coarse is not None:
                    item['five_minute_vs_independent_reference'] = compare(five, coarse)
                for slot in range(4):
                    grid = GRID[slot * 60:(slot + 1) * 60]
                    hour = {'slot': slot, 'observed_nonempty_trade_minutes': sum(i in minutes.index for i in grid),
                            'missing_nonempty_trade_minute_labels': [i for i in grid if i not in minutes.index]}
                    if coarse is not None:
                        comparison = compare(five.loc[[i for i in grid[::5] if i in five.index]], coarse.loc[grid[::5]])
                        hour['five_minute_reference_diagnostic'] = comparison
                        hour['all_12_coarse_diagnostic_buckets_passed'] = comparison['open_close_range_volume_diagnostic_pass_intervals'] == 12
                    item['hours'].append(hour)
                models.append(item)
            outside = sorted_frame.loc[~sorted_frame.index.isin(normal.index)]
            result = {'symbol': symbol, 'date': DATE, 'source_rows': len(raw),
                      'source_row_count_equals_complete_ticker_inventory': True,
                      'selected_physical_order_equals_time_and_transaction_id_sort': raw.reset_index(drop=True).equals(sorted_frame),
                      'duplicate_transaction_ids': int(raw.tran_id.duplicated().sum()),
                      'nonpositive_trade_prices': int(raw.price_x10000.le(0).sum()),
                      'nonpositive_trade_volumes': int(raw.volume.le(0).sum()),
                      'trade_prices_not_on_one_cent_grid': int(raw.price_x10000.mod(100).ne(0).sum()),
                      'observed_side_counts': {str(k): int(v) for k, v in raw.side.value_counts().items()},
                      'raw_all_trades_volume': int(raw.volume.sum()), 'regular_trades_volume': volume,
                      'regular_trade_price_times_volume_amount': str(Decimal(amount_scaled) / 10000),
                      'amount_is_calculated_from_actual_trades_not_independent_source_amount_field': True,
                      'regular_tick_daily_checks': checks, 'primary_daily_path': str(daily_path),
                      'primary_daily_sha256': sha(daily_path), 'observed_outside_regular_session_trades': records(outside),
                      'current_candidate_member_slots': members[start:start + 4, column].tolist(),
                      'current_candidate_complete_slots': complete[start:start + 4, column].tolist(),
                      'minute_clock_models': models, 'one_second_source_precision_cannot_certify_millisecond_boundary_order': True,
                      'source_native_millisecond_trade_clock_available': False, 'candidate_hours_admitted': 0}
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k not in ('minute_clock_models', 'observed_outside_regular_session_trades')}, ensure_ascii=False), flush=True)
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'pinned_revision': info['sha'], 'whole_acquisition_receipt_sha256': sha(acquisition_path),
               'source_ticker_calendar_sha256': sha(calendar_path), 'reference_acquisition_receipt_sha256': sha(reference_receipt_path),
               'current_candidate': NAME, 'candidate_meta_sha256': sha(meta_path),
               'candidate_fingerprint': meta['data_fingerprint'], 'local_calendar_sha256': sha(local_calendar_path),
               'whole_payloads': payloads, 'references': references, 'results': results,
               'trade_price_or_quantity_changed': False, 'unobserved_minute_prices_filled': False,
               'source_tick_seconds_relabelled_to_milliseconds': False,
               'clock_models_are_diagnostics_not_admission_certificates': True,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
               'scope': 'Actual three-stockday trade samples from two whole files; full tail payload quality and all historical source coverage remain unverified.'}
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'actual_trade_sample_diagnostics_complete', 'candidate_hours_admitted': 0,
                      'receipt_sha256': sha(path)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
