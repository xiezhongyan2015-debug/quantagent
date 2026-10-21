"""Audit public adjusted-minute data without manufacturing unadjusted bars."""
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'huggingface/joinquant_targeted_concordance_20261009'
END = np.r_[np.arange(571, 691), np.arange(781, 901)]
PRICE = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def grid(frame):
    ts = pd.to_datetime(frame.iloc[:, 0])
    np.testing.assert_array_equal(ts.dt.hour.to_numpy() * 60 + ts.dt.minute.to_numpy(), END)
    assert len(frame) == 240 and not ts.duplicated().any()


def main():
    source_path = OUT / 'source_acquisition_receipt.json'
    receipt = json.loads(source_path.read_text())
    assert receipt['status'] == 'all_target_files_verified' and not receipt['personal_credentials_used']
    for record in receipt['records']:
        assert sha(record['path']) == record['sha256']
    metadata = json.loads(Path(receipt['metadata_path']).read_text())
    mirror_path = BASE / 'huggingface/perctrix_targeted_20261009/dataset_info_blobs.json'
    mirror = json.loads(mirror_path.read_text())
    original_files = {r['rfilename']: r for r in metadata['siblings']}
    mirrored_files = {r['rfilename']: r for r in mirror['siblings']}
    assert original_files.keys() == mirrored_files.keys()
    assert all(original_files[k].get('lfs') == mirrored_files[k].get('lfs') for k in original_files)
    daily_receipt_path = BASE / 'tencent_targeted_daily_20261009/acquisition_receipt.json'
    daily_receipt = json.loads(daily_receipt_path.read_text())
    secondary = {}
    for record in daily_receipt['records']:
        assert sha(record['raw_path']) == record['raw_sha256']
        obj = json.loads(Path(record['raw_path']).read_text())
        for row in obj['data'][record['queried_symbol']]['day']:
            secondary[(record['queried_symbol'][2:] + '.SZ', row[0])] = row
    extraction = json.loads((BASE / 'original_intraday_concordance_20261009/extraction_receipt.json').read_text())
    original_control = next(r for r in extraction['payloads'] if r['code'] == '002831')
    assert sha(original_control['path']) == original_control['sha256']
    original = pq.read_table(original_control['path']).to_pandas().sort_values('datetime_nano')
    gaps_path = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_closing_rule_v4/gap_audit/remaining_minimum_trading_stockdays.csv'
    gaps = pd.read_csv(gaps_path)
    samples, sources = [], []
    for code in ['000099', '002831']:
        full_path = OUT / (code + '.XSHE.csv')
        target_dates = set(gaps.loc[gaps.symbol.eq(code + '.SZ'), 'date'])
        if code == '002831':
            target_dates.add('2022-06-06')
        pieces, first, last, total = [], None, None, 0
        for chunk in pd.read_csv(full_path, chunksize=100000, dtype=str):
            first = first or chunk.iloc[0, 0]
            last = chunk.iloc[-1, 0]
            total += len(chunk)
            select = chunk.iloc[:, 0].str[:10].isin(target_dates)
            if select.any():
                pieces.append(chunk[select])
        frame = pd.concat(pieces, ignore_index=True)
        selected_path = OUT / (code + '_target_stockdays.csv')
        frame.to_csv(selected_path, index=False)
        daily_path = BASE / 'baostock/union_daily_factors/normalized' / (code + '.SZ.json')
        data = json.loads(daily_path.read_text())
        primary = {row['date']: row for row in (dict(zip(data['fields'], row)) for row in data['rows'])}
        hf_path = BASE / 'huggingface/snapshot/data/stock_1m/SZ' / (code + '.parquet')
        sources.append({'symbol': code + '.SZ', 'full_path': str(full_path), 'full_sha256': sha(full_path),
            'first_datetime': first, 'last_datetime': last, 'total_rows': total,
            'selected_path': str(selected_path), 'selected_sha256': sha(selected_path),
            'primary_daily_path': str(daily_path), 'primary_daily_sha256': sha(daily_path),
            'external_raw_minute_path': str(hf_path), 'external_raw_minute_sha256': sha(hf_path)})
        for date, group in frame.groupby(frame.iloc[:, 0].str[:10], sort=True):
            grid(group)
            factor_values = group.factor.astype(float)
            assert factor_values.nunique() == 1
            factor = float(factor_values.iloc[0])
            assert 0 < factor < 1 and group.paused.astype(float).eq(0).all()
            prices = group[PRICE].astype(float).to_numpy()
            # Rounded adjusted cents define an interval of possible raw ticks.
            low = (prices - .005) / factor * 100
            high = (prices + .005) / factor * 100
            raw_tick_counts = np.ceil(high - 1e-9) - np.ceil(low - 1e-9)
            assert (raw_tick_counts >= 1).all()
            day = pd.Timestamp(date)
            hf = pq.read_table(hf_path, filters=[('timestamp', '>=', day),
                ('timestamp', '<', day + pd.Timedelta(days=1))]).to_pandas().sort_values('timestamp')
            clocks = hf.timestamp.dt.hour * 60 + hf.timestamp.dt.minute
            regular = hf[np.isin(clocks, END)]
            hp = regular[PRICE].to_numpy(float).round(2)
            hv = regular.volume.to_numpy(float).copy()
            boundary = hf[(clocks == 570) & hf.volume.gt(0)]
            assert len(boundary) <= 1 and not ((clocks == 780) & hf.volume.gt(0)).any()
            if len(boundary):
                row = boundary.iloc[0]
                hp[0, 0] = round(float(row.open), 2)
                hp[0, 1] = max(hp[0, 1], round(float(row.high), 2))
                hp[0, 2] = min(hp[0, 2], round(float(row.low), 2))
                hv[0] += float(row.volume)
            assert hp.shape == (240, 4)
            hf_projected = np.abs(np.round(hp * factor, 2) - prices) < 1e-8
            primary_day = primary[date]
            secondary_day = secondary[(code + '.SZ', date)]
            assert secondary_day[0] == date
            secondary_open, secondary_close = float(secondary_day[1]), float(secondary_day[2])
            secondary_volume = float(secondary_day[5]) * 100
            record = {'symbol': code + '.SZ', 'date': date, 'target_missing_stockday': date in set(gaps.loc[gaps.symbol.eq(code + '.SZ'), 'date']),
                'source_factor': factor, 'source_factor_text': group.factor.iloc[0], 'source_minute_rows': len(group),
                'source_first_adjusted_OHLC': prices[0].tolist(), 'source_last_adjusted_close': float(prices[-1, 3]),
                'source_first_open_possible_raw_cents': list(range(int(np.ceil(low[0, 0] - 1e-9)), int(np.ceil(high[0, 0] - 1e-9)))),
                'rounded_adjusted_price_cells_with_multiple_possible_raw_ticks': int((raw_tick_counts > 1).sum()),
                'rounded_adjusted_price_cells_total': int(raw_tick_counts.size),
                'HF_raw_quotes_reproject_to_archive_adjusted_quotes_by_field': {field: int(hf_projected[:, i].sum()) for i, field in enumerate(PRICE)},
                'archive_factor_scaled_volume_total_minus_primary_shares': float(group.volume.astype(float).sum() * factor - float(primary_day['volume'])),
                'archive_money_total_minus_primary_amount': float(group.money.astype(float).sum() - float(primary_day['amount'])),
                'HF_first_raw_open': float(hp[0, 0]), 'primary_daily_open': float(primary_day['open']),
                'secondary_daily_open': secondary_open, 'secondary_daily_close': secondary_close,
                'secondary_daily_open_matches_primary': abs(secondary_open - float(primary_day['open'])) < 1e-8,
                'secondary_daily_close_matches_primary': abs(secondary_close - float(primary_day['close'])) < 1e-8,
                'secondary_daily_volume_difference_shares': secondary_volume - float(primary_day['volume']),
                'no_raw_prices_created_from_ambiguous_inverse': True}
            if code == '002831' and date == '2022-06-06':
                op = original[PRICE].to_numpy(float)
                projected = np.abs(np.round(op * factor, 2) - prices) < 1e-8
                record['original_raw_quotes_reproject_to_archive_adjusted_quotes_by_field'] = {field: int(projected[:, i].sum()) for i, field in enumerate(PRICE)}
                record['original_raw_minute_path'] = original_control['path']
                record['original_raw_minute_sha256'] = original_control['sha256']
            samples.append(record)
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'targeted_adjusted_archive_audit_complete',
        'source_code_sha256': sha(Path(__file__)), 'source_acquisition_receipt_path': str(source_path),
        'source_acquisition_receipt_sha256': sha(source_path), 'primary_gap_list_path': str(gaps_path), 'primary_gap_list_sha256': sha(gaps_path),
        'actual_repo_csv_files': sum(name.endswith('.csv') for name in original_files),
        'README_claimed_stock_count_not_verified': 5267, 'all_mirror_file_names_and_LFS_metadata_equal': True,
        'mirror_metadata_path': str(mirror_path), 'mirror_metadata_sha256': sha(mirror_path),
        'secondary_daily_acquisition_receipt_path': str(daily_receipt_path), 'secondary_daily_acquisition_receipt_sha256': sha(daily_receipt_path),
        'sources': sources, 'samples': samples, 'target_missing_stockdays_checked': sum(r['target_missing_stockday'] for r in samples),
        'ambiguous_adjusted_price_cells': sum(r['rounded_adjusted_price_cells_with_multiple_possible_raw_ticks'] for r in samples),
        'all_secondary_daily_open_and_close_agree_with_primary': all(r['secondary_daily_open_matches_primary'] and r['secondary_daily_close_matches_primary'] for r in samples),
        'minutes_or_prices_invented': False, 'candidate_hours_admitted': 0, 'candidate_changed': False, 'production_ready': False,
        'limitations': 'Two deep samples only. Rounded adjusted prices are not lossless raw prices; source volume is factor-dependent; public mirror is byte-metadata equivalent and samples end in 2022, so no 2026 extension. Whole-day comparison does not certify intraday precision.'}
    (OUT / 'targeted_concordance_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ['sources', 'samples']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
