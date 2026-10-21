"""Audit newly acquired native CSV stockdays against unchanged daily gates.

Multiplying reported lots by 100 is a diagnosed unit hypothesis; the source
rounds lot counts, and no lost shares are generated. This audit admits no data.
"""
import hashlib
import json
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
SOURCE = BASE / 'additional_public_archives_20261009/wind17'
OUT = SOURCE / 'target_gap_audit'
FIELDS = {'开盘价': 'open', '收盘价': 'close', '最高价': 'high', '最低价': 'low', '成交量': 'volume_lots', '成交额': 'amount'}
PRICE = ['open', 'high', 'low', 'close']
END = np.r_[np.arange(571, 691), np.arange(781, 901)]
CANONICAL = {'sz000099': '000099.SZ', 'sz002831': '002831.SZ', 'sz300114': '302132.SZ',
             'sh600167': '600167.SH', 'sh600004': '600004.SH', 'sz000022': '001872.SZ'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def day_check(day, primary):
    day = day.sort_values('time').copy()
    clock = day.time.dt.hour.to_numpy() * 60 + day.time.dt.minute.to_numpy()
    regular = day[np.isin(clock, END)].copy()
    auction = day[(clock == 570) & day.volume_lots.gt(0)]
    assert len(auction) <= 1 and not ((clock == 780) & day.volume_lots.gt(0)).any()
    grid = not day.time.duplicated().any() and len(regular) == 240 and np.array_equal(regular.time.dt.hour.to_numpy() * 60 + regular.time.dt.minute.to_numpy(), END)
    if not grid:
        return {'grid_valid': False, 'source_rows': len(day), 'existing_daily_and_hour_gates_passed': False}
    q = regular[PRICE].to_numpy(float)
    volume = regular.volume_lots.to_numpy(float) * 100
    amount = regular.amount.to_numpy(float)
    if len(auction):
        row = auction.iloc[0]
        q[0, 0] = float(row.open)
        q[0, 1] = max(q[0, 1], float(row.high))
        q[0, 2] = min(q[0, 2], float(row.low))
        volume[0] += float(row.volume_lots) * 100
        amount[0] += float(row.amount)
    source_valid = (np.isfinite(q).all() and np.isfinite(volume).all() and (q > 0).all() and (volume >= 0).all()
                    and (q[:, 1] >= q[:, [0, 3]].max(1)).all() and (q[:, 2] <= q[:, [0, 3]].min(1)).all()
                    and np.allclose(q, q.round(2), rtol=0, atol=1e-8))
    opening = abs(q[0, 0] - float(primary['open'])) < 1e-8
    closing = abs(q[-1, 3] - float(primary['close'])) < 1e-8
    volume_diff = float(volume.sum() - float(primary['volume']))
    bounds = q[:, 1].max() <= float(primary['high']) + 1e-8 and q[:, 2].min() >= float(primary['low']) - 1e-8
    hours_positive = (volume.reshape(4, 60).sum(1) > 0).all()
    trading = primary['tradestatus'] == '1' and float(primary['volume']) > 0
    gates = {'source_rows_valid': bool(source_valid), 'grid_valid': bool(grid), 'primary_day_trading': trading,
             'open_agrees': bool(opening), 'close_agrees': bool(closing),
             'volume_difference_within_100_shares': abs(volume_diff) <= 100,
             'quotes_inside_primary_range': bool(bounds), 'all_four_hours_positive_volume': bool(hours_positive)}
    return {**gates, 'source_rows': len(day), 'observed_regular_minutes': len(regular),
            'positive_0930_source_bar_merged': len(auction) == 1,
            'observed_raw_ohlc': [float(q[0, 0]), float(q[:, 1].max()), float(q[:, 2].min()), float(q[-1, 3])],
            'observed_volume_lots_times_100_shares': float(volume.sum()), 'volume_difference_shares': volume_diff,
            'amount_sum_difference_currency_units': float(amount.sum() - float(primary['amount'])),
            'minute_amount_vs_reported_price_times_lot_volume_conflicts': int(((amount < q[:, 2] * volume - 1) | (amount > q[:, 1] * volume + 1)).sum()),
            'existing_daily_and_hour_gates_passed': all(gates.values()),
            'additional_source_semantics_and_minute_quantity_admission_complete': False}


def main():
    OUT.mkdir(exist_ok=True)
    receipt_path = OUT / 'target_gap_audit_receipt.json'
    assert not receipt_path.exists()
    gap_path = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_closing_rule_v4/gap_audit/remaining_minimum_trading_stockdays.csv'
    gaps = pd.read_csv(gap_path)
    acquisitions = [SOURCE / 'target_samples/sample_acquisition_receipt.json', SOURCE / 'gap_year_samples/sample_acquisition_receipt.json']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'gap_stockday_path': str(gap_path), 'gap_stockday_sha256': sha(gap_path),
              'acquisition_receipts': [{'path': str(p), 'sha256': sha(p)} for p in acquisitions],
              'lot_volume_unit_multiplier_hypothesis': 100, 'lost_or_fractional_shares_reconstructed': False,
              'whole_ZIP_LFS_SHA256_verified': False,
              'results': [], 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    for acquisition_path in acquisitions:
        acquisition = json.loads(acquisition_path.read_text())
        assert acquisition.get('completed_utc')
        for item in acquisition['results']:
            if item['status'] != 'exact_CSV_member_acquired_CRC_verified':
                result['results'].append({'source_symbol': item['symbol'], 'source_year': item['year'], 'status': item['status']})
                continue
            path = Path(item['path'])
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item['sha256']
            crc = item.get('CRC32', item.get('central_directory_entry', {}).get('CRC32'))
            assert zlib.crc32(raw) & 0xffffffff == crc
            frame = pd.read_csv(path, encoding='gbk').rename(columns=FIELDS)
            assert frame['股票代码'].eq(item['symbol']).all()
            frame['time'] = pd.to_datetime(frame['k线结束时间'], format='%Y-%m-%d %H:%M:%S')
            assert frame.time.dt.year.eq(int(item['year'])).all() and not frame.time.duplicated().any()
            frame['date'] = frame.time.dt.strftime('%Y-%m-%d')
            symbol = CANONICAL[item['symbol']]
            primary_path = BASE / 'baostock/union_daily_factors/normalized' / (symbol + '.json')
            primary = json.loads(primary_path.read_text())
            daily = {row[0]: dict(zip(primary['fields'], row)) for row in primary['rows']}
            targets = gaps[gaps.symbol.eq(symbol) & gaps.date.str.startswith(item['year'])]
            days = {}
            all_matches = []
            for date, day in frame.groupby('date', sort=True):
                if date not in daily:
                    continue
                checked = day_check(day, daily[date])
                if checked.get('primary_day_trading'):
                    all_matches.append(checked)
                if date in set(targets.date):
                    wanted = targets[targets.date.eq(date)].iloc[0]
                    days[date] = {'date': date, 'current_missing_hours': int(wanted.missing_hours), **checked}
            special_date = '2017-10-24' if item['symbol'] == 'sz000022' else None
            special = day_check(frame[frame.date.eq(special_date)], daily[special_date]) if special_date and special_date in daily else None
            result['results'].append({'source_symbol': item['symbol'], 'canonical_symbol_for_diagnostic': symbol,
                'source_year': item['year'], 'status': 'stockyear_daily_gap_diagnostic_complete',
                'path': str(path), 'sha256': sha(path), 'CRC32_independently_verified': True,
                'primary_daily_path': str(primary_path), 'primary_daily_sha256': sha(primary_path),
                'source_row_count': len(frame), 'first_observed_timestamp': str(frame.time.min()), 'last_observed_timestamp': str(frame.time.max()),
                'source_stockdays': int(frame.date.nunique()), 'primary_trading_stockdays_compared': len(all_matches),
                'primary_trading_days_with_unit_multiplier_100_volume_within_100': sum(r['volume_difference_within_100_shares'] for r in all_matches),
                'minimum_target_stockdays': len(targets), 'minimum_target_stockdays_observed': len(days),
                'minimum_target_stockdays_not_observed': sorted(set(targets.date) - set(days)),
                'target_days_surviving_existing_daily_and_hour_gates': sum(d['existing_daily_and_hour_gates_passed'] for d in days.values()),
                'target_missing_hours_surviving_existing_daily_and_hour_gates': sum(d['current_missing_hours'] for d in days.values() if d['existing_daily_and_hour_gates_passed']),
                'target_stockday_checks': list(days.values()), 'historical_identity_conflict_day_diagnostic': special,
                'candidate_hours_admitted': 0})
    (OUT / 'target_gap_audit_receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps([{k: v for k, v in r.items() if k not in ('target_stockday_checks', 'historical_identity_conflict_day_diagnostic')} for r in result['results']], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
