"""Build a whole-day original minute reference for every current complete stock."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive/all_original_day_controls'
DAY = '2026-09-23'
GRID = list(range(570, 690)) + list(range(780, 900))
PRICE = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def number(value):
    return Decimal(str(value))


def main():
    output = OUT / 'all_original_control_reference_audit.json'
    reference_path = OUT / 'all_499_original_stockday_minutes.parquet'
    assert not output.exists() and not reference_path.exists()
    receipt_path = OUT / 'extraction_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['full_split_archive_sha256_verified'] and receipt['gzip_footer_checked']
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_all_original_day_controls.py') == receipt['source_code_sha256']
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    complete_path = meta_path.parent / 'hour_complete.npy'
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(meta_path) == receipt['current_candidate_meta_sha256']
    assert sha(complete_path) == receipt['current_hour_complete_sha256']
    assert sha(calendar_path) == receipt['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    d = calendar['dates'].index(DAY)
    complete = np.load(complete_path, mmap_mode='r')
    symbols = {calendar['union_symbols'][i] for i in np.flatnonzero(complete[d * 4:d * 4 + 4].all(0))}
    assert len(symbols) == len(receipt['payloads']) == 499
    records, source_records = [], []
    for source in sorted(receipt['payloads'], key=lambda r: r['code']):
        path = Path(source['path'])
        assert sha(path) == source['sha256'] and path.stat().st_size == source['bytes']
        rows = pq.read_table(path).to_pylist()
        symbol = source['code'] + ('.SH' if source['code'].startswith('6') else '.SZ')
        assert symbol in symbols
        expected_nanos = []
        physical_grid = []
        validity = []
        prices = []
        volumes = []
        for row in rows:
            local = datetime.fromisoformat(row['datetime'])
            assert str(local.date()) == DAY
            left = local.hour * 60 + local.minute
            assert local.second == local.microsecond == 0
            physical_grid.append(left)
            expected_nanos.append(int(local.replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() * 1000000000))
            raw = [number(row[f]) for f in PRICE]
            v = number(row['volume'])
            finite = all(p.is_finite() for p in raw) and v.is_finite()
            valid = finite and all(p > 0 for p in raw) and v >= 0 and v == v.to_integral_value()
            valid = valid and raw[1] >= max(raw) and raw[2] <= min(raw)
            validity.append(valid)
            prices.append(raw); volumes.append(v)
            records.append({'symbol': symbol, 'date': DAY, 'left_minute': left,
                            'source_datetime': row['datetime'], 'source_datetime_nano': row['datetime_nano'],
                            **{f: str(row[f]) for f in PRICE}, 'volume': str(row['volume']),
                            'original_price_quantity_valid': valid, 'original_source_payload_sha256': source['sha256']})
        assert len(rows) == 240 and physical_grid == GRID
        assert expected_nanos == [r['datetime_nano'] for r in rows]
        primary_path = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
        obj = json.loads(primary_path.read_text())
        primary = next(dict(zip(obj['fields'], r)) for r in obj['rows'] if r[0] == DAY)
        price_daily = [prices[0][0], max(p[1] for p in prices), min(p[2] for p in prices), prices[-1][3]]
        exact = {f: price_daily[i] == number(primary[f]) for i, f in enumerate(PRICE)}
        exact['volume'] = sum(volumes) == number(primary['volume'])
        source_records.append({'symbol': symbol, 'path': source['path'], 'sha256': source['sha256'],
                               'original_minute_rows': len(rows), 'unique_expected_left_minute_grid': True,
                               'datetime_nano_exact_Asia_Shanghai_left_label': True, 'all_original_price_qty_valid': all(validity),
                               'invalid_original_price_qty_minutes': sum(not v for v in validity),
                               'primary_daily_path': str(primary_path), 'primary_daily_sha256': sha(primary_path),
                               'original_aggregate_vs_primary_daily_exact': exact,
                               'original_aggregate_daily_volume_shares': str(sum(volumes)),
                               'primary_daily_volume_shares': primary['volume'],
                               'original_minute_amount_is_not_available': True})
    assert len(records) == 119760 and {r['symbol'] for r in source_records} == symbols
    pq.write_table(pa.Table.from_pylist(records), reference_path, compression='zstd')
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'original_control_extraction_receipt_sha256': sha(receipt_path), 'current_candidate_meta_sha256': sha(meta_path),
              'current_hour_complete_sha256': sha(complete_path), 'calendar_sha256': sha(calendar_path),
              'scope_date': DAY, 'all_current_complete_stockdays_checked': len(source_records), 'original_minutes_checked': len(records),
              'all_499_controls_have_true_240_left_label_minutes_and_matching_nanos': True,
              'stockdays_all_original_price_quantity_valid': sum(r['all_original_price_qty_valid'] for r in source_records),
              'stockdays_all_OHLC_V_daily_fields_exact': sum(all(r['original_aggregate_vs_primary_daily_exact'].values()) for r in source_records),
              'stockdays_daily_exact_by_field': {f: sum(r['original_aggregate_vs_primary_daily_exact'][f] for r in source_records) for f in PRICE + ['volume']},
              'minute_reference_path': str(reference_path), 'minute_reference_sha256': sha(reference_path),
              'records': source_records, 'daily_source_independence_certified': False,
              'original_minute_amount_reconstructed': False, 'candidate_changed': False,
              'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
