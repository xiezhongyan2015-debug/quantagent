"""Independent checks of retained native CSVs, current gaps and raw coarse wire.

This verifier imports no source-audit or provider implementation. Reported lot
quantities are preserved; multiplying by 100 is only a diagnostic unit basis.
"""
import hashlib
import io
import json
import sys
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_public_archives_20261009/wind17'
OUT = SOURCE / 'independent_all_2023_gap_findings'
FIELDS = {'开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close',
          '成交量': 'lots', '成交额': 'amount'}
PRICE = ['open', 'high', 'low', 'close']
GRID = [*range(571, 691), *range(781, 901)]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_wire(query):
    path = Path(query['wire_path'])
    assert sha(path) == query['sha256']
    wire = path.read_bytes()
    header = wire[:21].decode().split('\x01')
    body = zlib.decompress(wire[21:21 + int(header[2])]) if header[1] == '96' else wire[21:]
    parts = body.decode().split('\x01')
    assert parts[0] == '0' and parts[7] == query['code'] and parts[11] == '5'
    fields = parts[8].split(',')
    data = json.loads(parts[6]) if parts[6] else []
    saved = json.loads(path.with_suffix('.json').read_text())
    assert saved['fields'] == fields and saved['data'] == data
    rows = data.get('record', []) if isinstance(data, dict) else data
    return [dict(zip(fields, row)) for row in rows]


def normalized_day(frame, date):
    native = frame[frame.index.strftime('%Y-%m-%d') == date].sort_index()
    assert not native.index.duplicated().any()
    clocks = native.index.hour * 60 + native.index.minute
    regular = native[np.isin(clocks, GRID)].copy()
    assert list(regular.index.hour * 60 + regular.index.minute) == GRID
    assert len(regular) == 240 and all(regular.index.second == 0)
    auction = native[(clocks == 570) & native.lots.map(Decimal).gt(0)]
    assert len(auction) <= 1 and not ((clocks == 780) & native.lots.map(Decimal).gt(0)).any()
    for field in PRICE + ['amount']:
        regular[field] = regular[field].map(Decimal)
    regular['volume'] = regular.lots.map(Decimal) * 100
    if len(auction):
        a = auction.iloc[0]
        first = regular.index[0]
        regular.loc[first, 'open'] = Decimal(a.open)
        regular.loc[first, 'high'] = max(regular.high.iloc[0], Decimal(a.high))
        regular.loc[first, 'low'] = min(regular.low.iloc[0], Decimal(a.low))
        regular.loc[first, 'volume'] += Decimal(a.lots) * 100
        regular.loc[first, 'amount'] += Decimal(a.amount)
    return native, regular, bool(len(auction))


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'independent_all_2023_findings_verification.json'
    assert not output.exists()
    ap = SOURCE / 'all_2023_minimum_gaps/all_2023_gap_audit_receipt.json'
    hp = SOURCE / 'all_2023_hour_concordance/hour_concordance_receipt.json'
    audit, hours = [json.loads(p.read_text()) for p in [ap, hp]]
    assert hours['status'] == 'complete_all_2023_gap_hour_diagnostics'
    assert hours['all_2023_gap_audit_sha256'] == sha(ap)
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_verified_wind17_all_2023_gaps.py') == audit['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_verified_wind17_2023_gap_hours.py') == hours['source_code_sha256']
    assert sha(audit['daily_helper_path']) == audit['daily_helper_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps.py') == audit['base_daily_helper_sha256']
    acquisition_path = Path(audit['verified_whole_archive_receipt_path'])
    assert sha(acquisition_path) == audit['verified_whole_archive_receipt_sha256']
    acquired = json.loads(acquisition_path.read_text())
    assert sha(acquired['path']) == acquired['expected_whole_LFS_SHA256']
    gaps = pd.read_csv(audit['gap_path'])
    assert sha(audit['gap_path']) == audit['gap_sha256']
    targets = gaps[gaps.date.str.startswith('2023')]
    assert len(targets) == audit['target_stockdays'] == 218
    assert targets.symbol.nunique() == audit['target_stock_count'] == 29
    assert int(targets.missing_hours.sum()) == audit['target_missing_hours'] == 872
    dataset = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'meta.json') == audit['candidate_meta_sha256']
    for name in ['hour_complete.npy', 'members.npy']:
        assert sha(dataset / name) == meta['files_sha256'][name]
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    cp = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(cp) == audit['calendar_sha256']
    calendar = json.loads(cp.read_text())
    prior_path = Path(hours['prior_300114_coarse_path'])
    assert sha(prior_path) == hours['prior_300114_coarse_sha256']
    prior = json.loads(prior_path.read_text())
    old_rows = [row for query in prior['queries'] for row in read_wire(query)]
    new_rows = {q['wire_path']: read_wire(q) for q in hours['queries']}
    hourly = {(r['canonical_symbol'], r['date']): r for r in hours['results']}
    assert len(hourly) == len(hours['results']) == 218
    counts = Counter()
    strict = []
    stock_checks = []
    missing, day_pass, amount_pass = 0, 0, 0
    with zipfile.ZipFile(acquired['path']) as archive:
        for item in audit['results']:
            symbol = item['canonical_symbol']
            raw = Path(item['raw_path']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item['raw_sha256']
            entry = archive.getinfo(item['zip_member'])
            assert entry.CRC == item['member_CRC32'] == zlib.crc32(raw) & 0xffffffff
            assert archive.read(entry) == raw
            frame = pd.read_csv(io.BytesIO(raw), encoding='gbk', dtype=str).rename(columns=FIELDS)
            assert frame['股票代码'].eq(item['observed_source_symbol']).all()
            frame.index = pd.to_datetime(frame['k线结束时间'])
            assert not frame.index.duplicated().any() and (frame.index.year == 2023).all()
            assert len(frame) == item['source_rows']
            assert str(frame.index.min()) == item['first_source_timestamp']
            assert str(frame.index.max()) == item['last_source_timestamp']
            assert sha(item['primary_daily_path']) == item['primary_daily_sha256']
            primary = json.loads(Path(item['primary_daily_path']).read_text())
            daily = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
            column = calendar['union_symbols'].index(symbol)
            survived = 0
            for expected in item['target_stockday_checks']:
                date = expected['date']
                index = calendar['dates'].index(date) * 4
                slots_missing = np.flatnonzero(~complete[index:index + 4, column]).tolist()
                assert len(slots_missing) == expected['existing_missing_hours'] == 4
                matching = targets[targets.symbol.eq(symbol) & targets.date.eq(date)]
                assert len(matching) == 1 and int(matching.iloc[0].missing_hours) == len(slots_missing)
                native, reg, auction = normalized_day(frame, date)
                p = daily[date]
                vd = sum(reg.volume) - Decimal(p['volume'])
                valid = all(row.open > 0 and row.high >= max(row.open, row.close) and
                            row.low > 0 and row.low <= min(row.open, row.close) and row.volume >= 0 and
                            all(v == v.quantize(Decimal('0.01')) for v in [row.open, row.high, row.low, row.close])
                            for row in reg.itertuples())
                flags = {'source_rows_valid': valid, 'grid_valid': True,
                         'primary_day_trading': p['tradestatus'] == '1' and Decimal(p['volume']) > 0,
                         'open_agrees': reg.open.iloc[0] == Decimal(p['open']),
                         'close_agrees': reg.close.iloc[-1] == Decimal(p['close']),
                         'volume_difference_within_100_shares': abs(vd) <= 100,
                         'quotes_inside_primary_range': max(reg.high) <= Decimal(p['high']) and min(reg.low) >= Decimal(p['low']),
                         'all_four_hours_positive_volume': all(sum(reg.iloc[s*60:(s+1)*60].volume) > 0 for s in range(4))}
                assert all(expected[k] == v for k, v in flags.items()), (symbol, date, flags)
                assert float(vd) == expected['volume_difference_shares']
                assert auction == expected['positive_0930_source_bar_merged']
                assert [float(reg.open.iloc[0]), float(max(reg.high)), float(min(reg.low)), float(reg.close.iloc[-1])] == expected['observed_raw_ohlc']
                money_good = [row.low * row.volume - 1 <= row.amount <= row.high * row.volume + 1 for row in reg.itertuples()]
                assert money_good.count(False) == expected['minute_amount_vs_reported_price_times_lot_volume_conflicts'], (symbol, date)
                passed = all(flags.values())
                assert passed == expected['existing_daily_and_hour_gates_passed']
                survived += passed
                missing += len(slots_missing)
                day_pass += len(slots_missing) if passed else 0
                h = hourly[(symbol, date)]
                assert h['current_missing_hours'] == len(slots_missing)
                counts[h['status']] += 1
                if not passed:
                    assert h['status'] == 'source_failed_prior_daily_gate' and not h['strict_hours']
                    continue
                eligible = [s for s in range(4) if all(money_good[s*60:(s+1)*60])]
                assert eligible == h['native_amount_pass_slots'], (symbol, date)
                amount_pass += len(eligible)
                if not eligible:
                    assert h['status'] == 'all_hours_failed_native_minute_amount_bounds' and not h['strict_hours']
                    continue
                rows = [r for r in old_rows if r['date'] == date] if symbol == '302132.SZ' else new_rows[h['coarse_wire_path']]
                if len(rows) != 48:
                    assert h['status'] == 'public_coarse_reference_absent_or_incomplete' and h['coarse_rows'] == len(rows)
                    continue
                reference = pd.DataFrame(rows)
                reference.index = pd.to_datetime(reference.time, format='%Y%m%d%H%M%S%f')
                assert list(reference.index.hour * 60 + reference.index.minute) == GRID[4::5]
                grouped = reg.groupby(pd.Grouper(freq='5min', closed='right', label='right')).agg(
                    {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
                observed = grouped.loc[reference.index]
                compatible = []
                differences = []
                for stamp, row in reference.iterrows():
                    actual = observed.loc[stamp]
                    diff = actual.volume - Decimal(row.volume)
                    differences.append(abs(diff))
                    compatible.append(all(actual[f] == Decimal(row[f]) for f in PRICE) and abs(diff) <= 100)
                surviving = [s for s in eligible if all(compatible[s*12:(s+1)*12])]
                assert surviving == h['strict_hours']
                assert sum(compatible) == h['exact_5m_OHLC_volume_buckets']
                assert float(max(differences)) == h['volume_max_difference_shares']
                strict.extend({'canonical_symbol': symbol, 'date': date, 'slot_zero_based': slot,
                               'status': 'strict_diagnostic_only_not_admitted'} for slot in surviving)
            assert survived == item['days_surviving_daily_and_hour_gates']
            stock_checks.append({'canonical_symbol': symbol, 'stockdays_recomputed': len(item['target_stockday_checks']),
                                 'daily_gate_pass_stockdays': survived})
    assert missing == 872 and day_pass == audit['target_hours_surviving_daily_and_hour_gates'] == 852
    assert len(strict) == hours['strict_hour_diagnostic_count'] == 3
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(__file__), 'audited_daily_receipt_path': str(ap), 'audited_daily_receipt_sha256': sha(ap),
               'audited_hour_receipt_path': str(hp), 'audited_hour_receipt_sha256': sha(hp),
               'whole_archive_fresh_sha256': sha(acquired['path']), 'candidate_meta_sha256': sha(dataset / 'meta.json'),
               'candidate_fingerprint': meta['data_fingerprint'], 'stock_count': len(stock_checks),
               'stockdays_recomputed': sum(r['stockdays_recomputed'] for r in stock_checks),
               'missing_hours_recomputed': missing, 'daily_gate_pass_hours': day_pass,
               'native_amount_pass_hours_among_daily_gate_pass_days': amount_pass,
               'stock_checks': stock_checks, 'stockday_status_counts': dict(counts),
               'raw_coarse_rows_decoded': len(old_rows) + sum(map(len, new_rows.values())),
               'new_coarse_queries_verified': len(new_rows), 'strict_hour_diagnostics': strict,
               'strict_hour_diagnostic_count': len(strict),
               'method': 'Fresh whole ZIP digest, every selected CSV byte and CRC, actual current missing mask, exact Decimal price/volume/amount arithmetic, independent right-clock grouping, raw coarse-wire decompression; no producer modules imported',
               'minute_provider_and_volume_precision_certified': False, 'candidate_changed': False,
               'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'stock_checks'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
