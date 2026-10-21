"""Compare new native CSVs with retained original, already complete stockdays."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009/wind17'
OUT = SOURCE / 'original_300114_controls'
PRICE = ['open', 'high', 'low', 'close']
GRID = np.r_[np.arange(571, 691), np.arange(781, 901)]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'original_control_receipt.json'
    assert not output.exists()
    ap = SOURCE / 'all_2023_minimum_gaps/all_2023_gap_audit_receipt.json'
    audited = json.loads(ap.read_text())
    source_item = next(r for r in audited['results'] if r['canonical_symbol'] == '302132.SZ')
    assert sha(source_item['raw_path']) == source_item['raw_sha256']
    frame = pd.read_csv(source_item['raw_path'], encoding='gbk').rename(columns={
        '开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close', '成交量': 'lots', '成交额': 'amount'})
    frame.index = pd.to_datetime(frame['k线结束时间'])
    original_path = BASE / 'official_old_code_minute_audit_20261009/receipt.json'
    original_audit = json.loads(original_path.read_text())
    dataset = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar = json.loads((ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())
    column = calendar['union_symbols'].index('302132.SZ')
    results = []
    for date in ['2023-12-11', '2023-12-12']:
        item = next(r for r in original_audit['payloads'] if r['date'] == date and r['canonical_symbol'] == '302132.SZ')
        assert item['admitted'] and sha(item['path']) == item['sha256']
        index = calendar['dates'].index(date) * 4
        assert complete[index:index + 4, column].all()
        original = pq.read_table(item['path']).to_pandas().sort_values('datetime_nano')
        assert len(original) == 240 and original['股票代码'].eq('300114').all()
        text_time = pd.to_datetime(original.datetime)
        nano_time = pd.to_datetime(original.datetime_nano, utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
        np.testing.assert_array_equal(text_time.to_numpy(), nano_time.to_numpy())
        np.testing.assert_array_equal(text_time.dt.hour * 60 + text_time.dt.minute + 1, GRID)
        native = frame.loc[date].copy().sort_index()
        auction = native[native.index.hour * 60 + native.index.minute == 570]
        native = native[np.isin(native.index.hour * 60 + native.index.minute, GRID)].copy()
        assert len(native) == 240
        native['volume'] = native.lots * 100
        if len(auction) and auction.lots.iloc[0] > 0:
            a, first = auction.iloc[0], native.index[0]
            native.loc[first, 'open'] = a.open
            native.loc[first, 'high'] = max(native.high.iloc[0], a.high)
            native.loc[first, 'low'] = min(native.low.iloc[0], a.low)
            native.loc[first, 'volume'] += a.lots * 100
            native.loc[first, 'amount'] += a.amount
        op, np_ = original[PRICE].to_numpy(float), native[PRICE].to_numpy(float)
        ov, nv = original.volume.to_numpy(float), native.volume.to_numpy(float)
        vd = nv - ov
        cumulative = nv.cumsum() - ov.cumsum()
        native_lot_cum = [Decimal(str(x)) / 100 for x in nv.cumsum()]
        original_lot_cum = [Decimal(str(x)) / 100 for x in ov.cumsum()]
        amount_conflicts = [native.index[i].strftime('%H:%M') for i, row in enumerate(native.itertuples())
                            if not Decimal(str(row.low)) * Decimal(str(row.volume)) - 1 <= Decimal(str(row.amount)) <= Decimal(str(row.high)) * Decimal(str(row.volume)) + 1]
        results.append({'canonical_symbol': '302132.SZ', 'observed_source_symbol': 'sz300114', 'date': date,
                        'original_payload_path': item['path'], 'original_payload_sha256': item['sha256'],
                        'existing_candidate_four_hours_complete': True,
                        'exact_minute_prices_by_field': {f: int((abs(op[:, i] - np_[:, i]) < 1e-8).sum()) for i, f in enumerate(PRICE)},
                        'exact_minute_volumes': int((vd == 0).sum()), 'minute_volumes_within_100_shares': int((abs(vd) <= 100).sum()),
                        'maximum_minute_volume_difference_shares': float(abs(vd).max()),
                        'native_minus_original_day_volume_shares': float(vd.sum()),
                        'cumulative_volume_times_within_100_shares': int((abs(cumulative) <= 100).sum()),
                        'maximum_cumulative_volume_difference_shares': float(abs(cumulative).max()),
                        'cumulative_native_lots_equal_original_cumulative_lots_rounded_half_up': sum(a == b.quantize(Decimal('1'), rounding=ROUND_HALF_UP) for a, b in zip(native_lot_cum, original_lot_cum)),
                        'cumulative_native_lots_equal_original_cumulative_lots_truncated': sum(a == b.quantize(Decimal('1'), rounding=ROUND_DOWN) for a, b in zip(native_lot_cum, original_lot_cum)),
                        'native_amount_price_volume_conflict_count': len(amount_conflicts),
                        'native_amount_price_volume_conflict_clocks': amount_conflicts,
                        'daily_gate_passed_in_prior_original_audit': item['source_valid'] and item['same_open'] and item['same_close'] and item['same_volume_within_100_shares'],
                        'hypothetical_rounding_not_used_to_reconstruct_shares': True})
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'native_source_path': source_item['raw_path'], 'native_source_sha256': source_item['raw_sha256'],
               'original_audit_path': str(original_path), 'original_audit_sha256': sha(original_path),
               'candidate_meta_sha256': sha(dataset / 'meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
               'results': results, 'scope': 'Two already complete native stockdays, positive controls only; differences do not establish tick-level correctness of either provider',
               'time_pairing': 'Observed original left label paired with native end label one minute later; only observed positive 09:30 auction merged',
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'missing_quantity_reconstructed': False,
               'minute_provider_and_volume_precision_certified': False, 'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
