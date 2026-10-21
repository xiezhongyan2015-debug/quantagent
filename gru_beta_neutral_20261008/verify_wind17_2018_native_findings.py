"""Independently recompute all 2018 gaps using raw CSV string Decimals."""
import csv
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

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_2018_minimum_gaps'
GRID = [*range(571, 691), *range(781, 901)]
PRICE = {'开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    ap, npth = OUT / 'all_2018_gap_audit_receipt.json', OUT / 'all_native_missing_hours_receipt.json'
    retry_path, failed_path = OUT / 'bounded_coarse_eligible_days_receipt.json', OUT / 'hour_diagnostics_receipt.json'
    output = OUT / 'independent_all_2018_native_verification.json'
    assert not output.exists()
    audit, native, retry, failed = [json.loads(p.read_text()) for p in [ap, npth, retry_path, failed_path]]
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_verified_wind17_all_2018_gaps.py') == audit['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_all_2018_native_hours.py') == native['source_code_sha256']
    assert native['daily_audit_sha256'] == sha(ap)
    assert retry['status'] == 'bounded_coarse_session_failed' and retry['error_type'] == 'TimeoutError'
    assert failed['status'] == 'failed' and failed['error_type'] == 'TimeoutError'
    assert not retry['queries'] and not failed['queries']
    assert retry['prior_terminal_failure_sha256'] == sha(failed_path)
    assert retry['native_audit_sha256'] == sha(npth)
    archive_receipt = Path(audit['archive_acquisition_path'])
    assert sha(archive_receipt) == audit['archive_acquisition_sha256']
    acquisition = json.loads(archive_receipt.read_text())
    whole_digest = sha(acquisition['path'])
    assert whole_digest == acquisition['expected_whole_LFS_SHA256']
    dataset = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'meta.json') == audit['candidate_meta_sha256']
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    mask = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == audit['calendar_sha256']
    calendar = json.loads(calendar_path.read_text())
    assert sha(audit['gap_path']) == audit['gap_sha256']
    with Path(audit['gap_path']).open(newline='') as stream:
        targets = {(r['symbol'], r['date']): int(r['missing_hours']) for r in csv.DictReader(stream) if r['date'].startswith('2018')}
    observed = {(r['symbol'], r['date']): r for r in native['results']}
    assert set(observed) == set(targets) and len(observed) == 310
    daily_pass_hours, selected_positive_hours, native_pass_hours = 0, 0, 0
    checks = []
    failed_gate_counts = Counter()
    with zipfile.ZipFile(acquisition['path']) as archive:
        for stock in audit['results']:
            raw = Path(stock['raw_path']).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == stock['raw_sha256']
            assert zlib.crc32(raw) & 0xffffffff == stock['member_CRC32']
            assert archive.read(stock['zip_member']) == raw
            rows = list(csv.DictReader(io.StringIO(raw.decode('gbk'))))
            assert len(rows) == stock['source_rows']
            assert all(r['股票代码'] == stock['observed_source_symbol'] for r in rows)
            stamps = [r['k线结束时间'] for r in rows]
            assert len(set(stamps)) == len(stamps)
            assert min(stamps) == stock['first_source_timestamp'] and max(stamps) == stock['last_source_timestamp']
            assert sha(stock['primary_daily_path']) == stock['primary_daily_sha256']
            obj = json.loads(Path(stock['primary_daily_path']).read_text())
            primary = {r[0]: dict(zip(obj['fields'], r)) for r in obj['rows']}
            column = calendar['union_symbols'].index(stock['canonical_symbol'])
            for expected in stock['target_stockday_checks']:
                date = expected['date']
                key = stock['canonical_symbol'], date
                index = calendar['dates'].index(date) * 4
                missing = np.flatnonzero(~mask[index:index + 4, column]).tolist()
                assert missing == expected['actual_missing_slots'] and len(missing) == targets[key] == expected['existing_missing_hours']
                day = sorted((r for r in rows if r['k线结束时间'].startswith(date)), key=lambda r: r['k线结束时间'])
                regular = []
                auction = None
                for row in day:
                    stamp = datetime.fromisoformat(row['k线结束时间'])
                    assert stamp.second == 0
                    clock = stamp.hour * 60 + stamp.minute
                    r = {f: Decimal(row[k]) for k, f in PRICE.items()}
                    r.update(volume=Decimal(row['成交量'])*100, amount=Decimal(row['成交额']), clock=clock)
                    if clock in GRID:
                        regular.append(r)
                    elif clock == 570 and r['volume'] > 0:
                        assert auction is None
                        auction = r
                    elif clock == 780:
                        assert r['volume'] == 0
                assert [r['clock'] for r in regular] == GRID
                if auction:
                    first = regular[0]
                    first['open'] = auction['open']
                    first['high'] = max(first['high'], auction['high'])
                    first['low'] = min(first['low'], auction['low'])
                    first['volume'] += auction['volume']
                    first['amount'] += auction['amount']
                p = primary[date]
                vd = sum(r['volume'] for r in regular) - Decimal(p['volume'])
                hour_positive = [sum(r['volume'] for r in regular[s*60:(s+1)*60]) > 0 for s in range(4)]
                flags = {'grid_valid': True,
                         'source_rows_valid': all(r['open'] > 0 and r['low'] > 0 and r['high'] >= max(r['open'], r['close']) and
                                                 r['low'] <= min(r['open'], r['close']) and r['volume'] >= 0 and
                                                 all(r[f] == r[f].quantize(Decimal('.01')) for f in PRICE.values()) for r in regular),
                         'primary_day_trading': p['tradestatus'] == '1' and Decimal(p['volume']) > 0,
                         'open_agrees': regular[0]['open'] == Decimal(p['open']),
                         'close_agrees': regular[-1]['close'] == Decimal(p['close']),
                         'volume_difference_within_100_shares': abs(vd) <= 100,
                         'quotes_inside_primary_range': max(r['high'] for r in regular) <= Decimal(p['high']) and min(r['low'] for r in regular) >= Decimal(p['low']),
                         'all_four_hours_positive_volume': all(hour_positive)}
                assert all(expected[k] == v for k, v in flags.items()), key
                assert float(vd) == expected['volume_difference_shares']
                assert bool(auction) == expected['positive_0930_source_bar_merged']
                amount_good = [r['low']*r['volume']-1 <= r['amount'] <= r['high']*r['volume']+1 for r in regular]
                assert amount_good.count(False) == expected['minute_amount_vs_reported_price_times_lot_volume_conflicts'], key
                assert all(flags.values()) == expected['existing_daily_and_hour_gates_passed']
                daily_pass_hours += len(missing) if all(flags.values()) else 0
                day_passed = all(v for k, v in flags.items() if k != 'all_four_hours_positive_volume')
                n = observed[key]
                assert n['daily_source_gate_passed'] == day_passed
                positive = [s for s in missing if hour_positive[s]] if day_passed else []
                eligible = [s for s in positive if all(amount_good[s*60:(s+1)*60])]
                assert positive == n['positive_volume_missing_slots'] and eligible == n['native_amount_pass_missing_slots']
                selected_positive_hours += len(positive)
                native_pass_hours += len(eligible)
                failed_gate_counts.update(k for k, v in flags.items() if not v)
                if eligible:
                    checks.append({'symbol': key[0], 'date': date, 'native_pass_missing_slots': eligible,
                                   'coarse_reference_checked': False, 'status': 'pending_coarse_and_source_precision_admission'})
    assert len(audit['results']) == audit['target_stock_count'] == 93
    assert sum(targets.values()) == audit['target_missing_hours'] == native['actual_missing_hours'] == 1217
    assert daily_pass_hours == audit['target_hours_surviving_daily_and_hour_gates'] == 148
    assert selected_positive_hours == native['daily_and_selected_hour_positive_pass_hours'] == 148
    assert native_pass_hours == native['native_positive_volume_and_amount_pass_hours'] == 24
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(__file__), 'daily_audit_sha256': sha(ap), 'native_audit_sha256': sha(npth),
               'coarse_terminal_failure_sha256': sha(retry_path), 'whole_archive_sha256': whole_digest,
               'candidate_meta_sha256': sha(dataset / 'meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
               'stocks_verified': 93, 'stockdays_verified': 310, 'actual_missing_hours': 1217,
               'daily_and_selected_positive_hour_pass_hours': 148, 'native_amount_pass_hours': 24,
               'failed_gate_stockday_counts': dict(failed_gate_counts), 'native_pending_hours': checks,
               'coarse_reference_rows_acquired': 0, 'coarse_reference_qualified_hour_count': None,
               'method': 'Fresh whole ZIP digest, every selected CSV byte/CRC and every native current missing slot; independent csv.DictReader and exact Decimal arithmetic; no producer or shared normalization helper imported',
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'quantity_precision_certified': False,
               'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'native_pending_hours'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
