"""Independent retained-CSV arithmetic and 5m clock-grouping verification."""
import csv
import hashlib
import io
import json
import sys
import zlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow
import pandas as pd
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_public_archives_20261009/wind17'
OUT = BASE / 'independent_gap_findings'
FIELDS = {'开盘价': 'open', '收盘价': 'close', '最高价': 'high', '最低价': 'low', '成交量': 'lots', '成交额': 'amount'}
PRICE = ['open', 'high', 'low', 'close']


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    receipt_path = OUT / 'independent_findings_verification.json'
    assert not receipt_path.exists()
    ap = BASE / 'target_gap_audit/target_gap_audit_decimal_receipt.json'
    cp = BASE / 'target_300114_2023_coarse_concordance/coarse_concordance_receipt.json'
    audit, cross = [json.loads(path.read_text()) for path in [ap, cp]]
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py') == audit['source_code_sha256']
    assert sha(audit['base_daily_helper_path']) == audit['base_daily_helper_sha256']
    assert sha(audit['prior_float_audit_path']) == audit['prior_float_audit_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_wind17_300114_coarse_concordance.py') == cross['source_code_sha256']
    assert cross['target_gap_audit_receipt_sha256'] == audit['prior_float_audit_sha256']
    assert sha(audit['gap_stockday_path']) == audit['gap_stockday_sha256']
    acquisitions = []
    for item in audit['acquisition_receipts']:
        assert sha(item['path']) == item['sha256']
        acquisitions.extend(json.loads(Path(item['path']).read_text())['results'])
    day_checks = 0
    stockyear_checks = []
    for result in audit['results']:
        assert sha(result['path']) == result['sha256']
        assert sha(result['primary_daily_path']) == result['primary_daily_sha256']
        item = next(r for r in acquisitions if r['symbol'] == result['source_symbol'] and r['year'] == result['source_year'])
        raw = Path(result['path']).read_bytes()
        crc = item.get('CRC32', item.get('central_directory_entry', {}).get('CRC32'))
        assert zlib.crc32(raw) & 0xffffffff == crc
        for fragment in item['range_receipts']:
            if 'sha256' in fragment:
                assert sha(fragment['path']) == fragment['sha256']
        frame = pd.read_csv(io.BytesIO(raw), encoding='gbk').rename(columns=FIELDS)
        frame['stamp'] = pd.to_datetime(frame['k线结束时间'])
        primary = json.loads(Path(result['primary_daily_path']).read_text())
        daily = {row[0]: dict(zip(primary['fields'], row)) for row in primary['rows']}
        assert len(frame) == result['source_row_count']
        assert str(frame.stamp.min()) == result['first_observed_timestamp']
        assert str(frame.stamp.max()) == result['last_observed_timestamp']
        pass_days = 0
        for expected in result['target_stockday_checks']:
            date = expected['date']
            native = frame[frame.stamp.dt.strftime('%Y-%m-%d').eq(date)].set_index('stamp').sort_index()
            regular = native[(native.index.time >= pd.Timestamp('09:31').time()) & (native.index.time <= pd.Timestamp('15:00').time())]
            reg = regular[(regular.index.time <= pd.Timestamp('11:30').time()) | (regular.index.time >= pd.Timestamp('13:01').time())].copy()
            assert len(reg) == 240
            reg['volume'] = reg.lots * 100
            stamp = pd.Timestamp(date + ' 09:30')
            auction = native.loc[stamp]
            if auction.lots > 0:
                reg.loc[reg.index[0], 'open'] = auction.open
                reg.loc[reg.index[0], 'high'] = max(reg.high.iloc[0], auction.high)
                reg.loc[reg.index[0], 'low'] = min(reg.low.iloc[0], auction.low)
                reg.loc[reg.index[0], 'volume'] += auction.lots * 100
                reg.loc[reg.index[0], 'amount'] += auction.amount
            primary_row = daily[date]
            vd = float(reg.volume.sum()) - float(primary_row['volume'])
            assert vd == expected['volume_difference_shares']
            assert (float(reg.open.iloc[0]) == float(primary_row['open'])) == expected['open_agrees']
            assert (float(reg.close.iloc[-1]) == float(primary_row['close'])) == expected['close_agrees']
            assert (reg.high.max() <= float(primary_row['high']) and reg.low.min() >= float(primary_row['low'])) == expected['quotes_inside_primary_range']
            assert (abs(vd) <= 100) == expected['volume_difference_within_100_shares']
            conflicts = sum(not Decimal(str(row.low)) * Decimal(str(row.volume)) - 1 <= Decimal(str(row.amount)) <= Decimal(str(row.high)) * Decimal(str(row.volume)) + 1 for row in reg.itertuples())
            assert conflicts == expected['minute_amount_vs_reported_price_times_lot_volume_conflicts']
            assert bool((reg[PRICE] > 0).all().all() and (reg.volume >= 0).all()) == expected['source_rows_valid']
            positive_hours = all(reg.iloc[slot * 60:(slot + 1) * 60].volume.sum() > 0 for slot in range(4))
            passed = bool(primary_row['tradestatus'] == '1' and float(primary_row['volume']) > 0 and expected['source_rows_valid']
                          and expected['grid_valid'] and expected['open_agrees'] and expected['close_agrees']
                          and expected['quotes_inside_primary_range'] and abs(vd) <= 100 and positive_hours)
            assert passed == expected['existing_daily_and_hour_gates_passed']
            pass_days += passed
            day_checks += 1
        assert pass_days == result['target_days_surviving_existing_daily_and_hour_gates']
        stockyear_checks.append({'symbol': result['source_symbol'], 'year': result['source_year'],
                                'target_stockdays_recomputed': len(result['target_stockday_checks']), 'daily_gate_pass_days': pass_days})
    source = pd.read_csv(cross['native_source_path'], encoding='gbk').rename(columns=FIELDS)
    source.index = pd.to_datetime(source['k线结束时间'])
    records = []
    for query in cross['queries']:
        assert sha(query['wire_path']) == query['sha256']
        wire = Path(query['wire_path']).read_bytes()
        header = wire[:21].decode().split('\x01')
        text = zlib.decompress(wire[21:21 + int(header[2])]).decode() if header[1] == '96' else wire[21:].decode()
        parts = text.split('\x01')
        assert parts[0] == '0' and parts[7] == query['code'] and parts[11] == '5'
        data = json.loads(parts[6]) if parts[6] else []
        decoded_rows = data.get('record', []) if isinstance(data, dict) else data
        saved = json.loads(Path(query['wire_path']).with_suffix('.json').read_text())
        assert saved['data'] == data and saved['fields'] == parts[8].split(',')
        records.extend(dict(zip(saved['fields'], row)) for row in decoded_rows)
    coarse = pd.DataFrame(records)
    coarse.index = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
    coarse[PRICE + ['volume']] = coarse[PRICE + ['volume']].astype(float)
    qualified, amount_pass_hours, coarse_pass_hours = [], 0, 0
    close_volume_days = exact_ohlc_days = 0
    for expected in cross['results']:
        date = expected['date']
        native = source.loc[date].copy().sort_index()
        auction = native.loc[date + ' 09:30']
        native = native.drop(pd.Timestamp(date + ' 09:30'))
        native['volume'] = native.lots * 100
        if auction.lots > 0:
            native.loc[native.index[0], 'open'] = auction.open
            native.loc[native.index[0], 'high'] = max(native.high.iloc[0], auction.high)
            native.loc[native.index[0], 'low'] = min(native.low.iloc[0], auction.low)
            native.loc[native.index[0], 'volume'] += auction.lots * 100
            native.loc[native.index[0], 'amount'] += auction.amount
        grouped = native.groupby(pd.Grouper(freq='5min', closed='right', label='right')).agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
        reference = coarse.loc[date].sort_index()
        observed = grouped.loc[reference.index]
        eq = np.isclose(observed[PRICE], reference[PRICE], rtol=0, atol=1e-8)
        vd = observed.volume.to_numpy(float) - reference.volume.to_numpy(float)
        within = abs(vd) <= 100
        assert {f: int(eq[:, i].sum()) for i, f in enumerate(PRICE)} == expected['exact_price_buckets_by_field']
        assert int((vd == 0).sum()) == expected['volume_exact_buckets']
        assert int(within.sum()) == expected['volume_within_100_buckets']
        assert int((eq[:, 3] & within).sum()) == expected['close_and_volume_within_100_buckets']
        close_volume_days += bool((eq[:, 3] & within).all())
        exact_ohlc_days += bool((eq.all(1) & within).all())
        for slot in range(4):
            block = native.iloc[slot * 60:(slot + 1) * 60]
            amount_pass = all(Decimal(str(row.low)) * Decimal(str(row.volume)) - 1 <= Decimal(str(row.amount)) <= Decimal(str(row.high)) * Decimal(str(row.volume)) + 1 for row in block.itertuples())
            coarse_pass = bool((eq.all(1) & within)[slot * 12:(slot + 1) * 12].all())
            coarse_pass_hours += coarse_pass
            amount_pass_hours += amount_pass
            if amount_pass and coarse_pass:
                qualified.append({'date': date, 'slot_zero_based': slot, 'native_minutes': len(block),
                                  'status': 'strict_hour_diagnostic_only_not_admitted'})
    assert close_volume_days == cross['days_all_5m_close_and_volume_within_100_passed'] == 10
    assert exact_ohlc_days == cross['days_all_5m_OHLC_and_volume_within_100_passed'] == 8
    assert qualified == [{'date': date, 'slot_zero_based': 0, 'native_minutes': 60, 'status': 'strict_hour_diagnostic_only_not_admitted'} for date in ['2023-06-19', '2023-06-21', '2023-06-29']]
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'source_code_sha256': sha(__file__), 'target_gap_audit_receipt_sha256': sha(ap),
        'coarse_concordance_receipt_sha256': sha(cp),
        'method': 'Independent native CSV CRC, Decimal amount bounds, pandas right-clock grouping and fresh public wire decompression; no producer audit code imported',
        'target_stockdays_recomputed': day_checks, 'stockyear_checks': stockyear_checks,
        'raw_5m_rows_independently_decoded': len(records), 'coarse_OHLC_and_volume_pass_hours': coarse_pass_hours,
        'native_amount_price_volume_pass_hours': amount_pass_hours, 'strict_hour_diagnostics': qualified,
        'strict_hour_diagnostic_count': len(qualified), 'minute_provider_and_volume_precision_certified': False,
        'candidate_hours_admitted': 0, 'candidate_changed': False, 'production_ready': False}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
