"""Audit all 2018 target stocks, preserving actual missing-hour slots."""
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from audit_wind17_target_gaps import ROOT, BASE, SOURCE, FIELDS, sha
from audit_wind17_target_gaps_decimal import day_check
import pandas as pd
import numpy as np

OUT = SOURCE / 'all_2018_minimum_gaps'


def main():
    OUT.mkdir(exist_ok=True)
    path = OUT / 'all_2018_gap_audit_receipt.json'
    assert not path.exists()
    ap = SOURCE / 'verified_whole_2018_archive/archive_acquisition_receipt.json'
    acquired = json.loads(ap.read_text())
    assert acquired['whole_LFS_SHA256_verified'] and acquired['status'] == 'complete_whole_archive_SHA256_and_retained_controls_verified'
    assert sha(acquired['path']) == acquired['expected_whole_LFS_SHA256']
    gap_path = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_closing_rule_v4/gap_audit/remaining_minimum_trading_stockdays.csv'
    gaps = pd.read_csv(gap_path)
    gaps = gaps[gaps.date.str.startswith('2018')]
    dataset = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'daily_helper_path': str(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py'),
               'daily_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py'),
               'base_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps.py'),
               'archive_acquisition_path': str(ap), 'archive_acquisition_sha256': sha(ap),
               'whole_archive_fresh_SHA256_verified': True, 'gap_path': str(gap_path), 'gap_sha256': sha(gap_path),
               'candidate_meta_sha256': sha(dataset / 'meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
               'calendar_sha256': sha(calendar_path), 'target_stock_count': int(gaps.symbol.nunique()),
               'target_stockdays': len(gaps), 'target_missing_hours': int(gaps.missing_hours.sum()),
               'results': [], 'reported_quantity_multiplier_diagnostic': 100,
               'source_quantity_precision_certified': False, 'candidate_changed': False,
               'candidate_hours_admitted': 0, 'production_ready': False}
    raw_dir = OUT / 'exact_CSV_members'
    raw_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(acquired['path']) as archive:
        names = set(archive.namelist())
        for symbol, targets in gaps.groupby('symbol', sort=True):
            observed_code = {'001914.SZ': 'sz000043', '001872.SZ': 'sz000022'}.get(symbol, symbol[-2:].lower() + symbol[:6])
            member = '2018/' + observed_code + '.csv'
            item = {'canonical_symbol': symbol, 'observed_source_symbol': observed_code, 'zip_member': member,
                    'target_stockdays': len(targets), 'target_missing_hours': int(targets.missing_hours.sum()),
                    'old_code_factor_identity_conflict_pending': symbol == '001872.SZ'}
            if member not in names:
                item.update(status='actual_target_file_absent', target_stockday_checks=[], candidate_hours_admitted=0)
                receipt['results'].append(item)
                continue
            entry = archive.getinfo(member)
            assert entry.file_size <= 16 * 1024 * 1024 and not entry.flag_bits & 1
            raw = archive.read(entry)
            raw_path = raw_dir / (observed_code + '_2018.csv')
            assert not raw_path.exists()
            raw_path.write_bytes(raw)
            frame = pd.read_csv(raw_path, encoding='gbk').rename(columns=FIELDS)
            assert frame['股票代码'].eq(observed_code).all()
            frame['time'] = pd.to_datetime(frame['k线结束时间'])
            assert frame.time.dt.year.eq(2018).all() and not frame.time.duplicated().any()
            frame['date'] = frame.time.dt.strftime('%Y-%m-%d')
            pp = BASE / 'baostock/union_daily_factors/normalized' / (symbol + '.json')
            primary = json.loads(pp.read_text())
            daily = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
            column = calendar['union_symbols'].index(symbol)
            checks = []
            for target in targets.itertuples():
                idx = calendar['dates'].index(target.date) * 4
                slots = np.flatnonzero(~complete[idx:idx + 4, column]).tolist()
                assert len(slots) == target.missing_hours
                day = frame[frame.date.eq(target.date)]
                checked = day_check(day, daily[target.date]) if len(day) else {'existing_daily_and_hour_gates_passed': False, 'status': 'target_stockday_absent'}
                checks.append({'date': target.date, 'actual_missing_slots': slots, 'existing_missing_hours': len(slots), **checked})
            item.update(status='all_2018_target_stockday_diagnostics_complete', raw_path=str(raw_path), raw_sha256=sha(raw_path),
                        member_CRC32=entry.CRC, member_CRC32_verified=True,
                        first_source_timestamp=str(frame.time.min()), last_source_timestamp=str(frame.time.max()), source_rows=len(frame),
                        primary_daily_path=str(pp), primary_daily_sha256=sha(pp), target_stockday_checks=checks,
                        daily_and_hour_gate_pass_target_hours=sum(c['existing_missing_hours'] for c in checks if c['existing_daily_and_hour_gates_passed']),
                        candidate_hours_admitted=0)
            receipt['results'].append(item)
            print(json.dumps({'symbol': symbol, 'target_stockdays': len(checks),
                              'target_hours_passing_existing_daily_and_hour_gates': item['daily_and_hour_gate_pass_target_hours']}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt['target_hours_surviving_daily_and_hour_gates'] = sum(r.get('daily_and_hour_gate_pass_target_hours', 0) for r in receipt['results'])
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'results'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
