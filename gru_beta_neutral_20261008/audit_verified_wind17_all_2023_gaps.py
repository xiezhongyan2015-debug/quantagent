"""Inspect every 2023 minimum-gap stock from the fully verified public archive."""
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from audit_wind17_target_gaps import ROOT, BASE, SOURCE, FIELDS, sha
from audit_wind17_target_gaps_decimal import day_check

sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow
import pandas as pd
import numpy as np

OUT = SOURCE / 'all_2023_minimum_gaps'


def main():
    OUT.mkdir(exist_ok=True)
    receipt_path = OUT / 'all_2023_gap_audit_receipt.json'
    assert not receipt_path.exists()
    ap = SOURCE / 'verified_whole_2023_archive/archive_acquisition_receipt.json'
    acquired = json.loads(ap.read_text())
    assert acquired['status'] == 'complete_whole_archive_SHA256_and_target_CSV_verified' and acquired['whole_LFS_SHA256_verified']
    assert sha(acquired['path']) == acquired['expected_whole_LFS_SHA256']
    gap_path = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_closing_rule_v4/gap_audit/remaining_minimum_trading_stockdays.csv'
    gaps = pd.read_csv(gap_path)
    gaps = gaps[gaps.date.str.startswith('2023')]
    dataset = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
        'daily_helper_path': str(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py'),
        'daily_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py'),
        'base_daily_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps.py'),
        'verified_whole_archive_receipt_path': str(ap), 'verified_whole_archive_receipt_sha256': sha(ap),
        'full_archive_fresh_SHA256_verified': True, 'gap_path': str(gap_path), 'gap_sha256': sha(gap_path),
        'candidate_meta_sha256': sha(dataset / 'meta.json'), 'calendar_sha256': sha(calendar_path),
        'target_stock_count': int(gaps.symbol.nunique()), 'target_stockdays': len(gaps),
        'target_missing_hours': int(gaps.missing_hours.sum()), 'results': [],
        'quantity_multiplier_diagnostic': 100, 'minute_unit_precision_and_provider_semantics_certified': False,
        'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    raw_dir = OUT / 'exact_CSV_members'
    raw_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(acquired['path']) as archive:
        names = set(archive.namelist())
        for symbol, targets in gaps.groupby('symbol', sort=True):
            source_code = 'sz300114' if symbol == '302132.SZ' else symbol[-2:].lower() + symbol[:6]
            member = '2023/' + source_code + '.csv'
            item = {'canonical_symbol': symbol, 'observed_source_symbol': source_code,
                    'zip_member': member, 'target_stockdays': len(targets), 'target_missing_hours': int(targets.missing_hours.sum())}
            if member not in names:
                item.update(status='native_source_file_absent', candidate_hours_admitted=0)
                receipt['results'].append(item)
                continue
            entry = archive.getinfo(member)
            assert entry.file_size <= 16 * 1024 * 1024 and not entry.flag_bits & 1
            raw = archive.read(entry)
            path = raw_dir / (source_code + '_2023.csv')
            assert not path.exists()
            path.write_bytes(raw)
            frame = pd.read_csv(path, encoding='gbk').rename(columns=FIELDS)
            assert frame['股票代码'].eq(source_code).all()
            frame['time'] = pd.to_datetime(frame['k线结束时间'])
            assert frame.time.dt.year.eq(2023).all() and not frame.time.duplicated().any()
            frame['date'] = frame.time.dt.strftime('%Y-%m-%d')
            primary_path = BASE / 'baostock/union_daily_factors/normalized' / (symbol + '.json')
            primary = json.loads(primary_path.read_text())
            daily = {row[0]: dict(zip(primary['fields'], row)) for row in primary['rows']}
            checks = []
            column = calendar['union_symbols'].index(symbol)
            for target in targets.itertuples():
                day_index = calendar['dates'].index(target.date)
                actual_missing = int((~complete[day_index * 4:day_index * 4 + 4, column]).sum())
                assert actual_missing == target.missing_hours
                day = frame[frame.date.eq(target.date)]
                check = day_check(day, daily[target.date]) if len(day) else {'existing_daily_and_hour_gates_passed': False, 'status': 'native_target_stockday_absent'}
                checks.append({'date': target.date, 'existing_missing_hours': actual_missing, **check})
            item.update(status='all_minimum_2023_stockday_daily_diagnostics_complete', raw_path=str(path), raw_sha256=sha(path),
                member_CRC32_verified=True, member_CRC32=entry.CRC, primary_daily_path=str(primary_path), primary_daily_sha256=sha(primary_path),
                first_source_timestamp=str(frame.time.min()), last_source_timestamp=str(frame.time.max()),
                source_rows=len(frame), target_stockday_checks=checks,
                days_surviving_daily_and_hour_gates=sum(c['existing_daily_and_hour_gates_passed'] for c in checks),
                target_hours_surviving_daily_and_hour_gates=sum(c['existing_missing_hours'] for c in checks if c['existing_daily_and_hour_gates_passed']),
                candidate_hours_admitted=0)
            receipt['results'].append(item)
            print(json.dumps({'symbol': symbol, 'target_days': len(checks), 'daily_gate_pass_days': item['days_surviving_daily_and_hour_gates'],
                              'target_hours_surviving_daily_and_hour_gates': item['target_hours_surviving_daily_and_hour_gates']}, ensure_ascii=False), flush=True)
    receipt['target_hours_surviving_daily_and_hour_gates'] = sum(r.get('target_hours_surviving_daily_and_hour_gates', 0) for r in receipt['results'])
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'results'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
