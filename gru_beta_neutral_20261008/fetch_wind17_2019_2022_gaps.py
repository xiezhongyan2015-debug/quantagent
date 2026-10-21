"""Acquire exact native CSV members for the actual 2019-2022 minimum gaps."""
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from inspect_wind17_remote_zip import RangeReader, sha, BASE
from audit_wind17_target_gaps import ROOT, FIELDS
from audit_wind17_target_gaps_decimal import day_check
import pandas as pd

OUT = BASE / 'remaining_2019_2022'


def main():
    OUT.mkdir(exist_ok=True)
    path = OUT / 'acquisition_and_daily_diagnostics_receipt.json'
    assert not path.exists()
    metadata_path = BASE / 'dataset_info_blobs.json'
    metadata = json.loads(metadata_path.read_text())
    assert metadata['sha'] == 'ca4c51770f1fafb40317cc35bfd92e7c2be17827'
    assert not metadata.get('private') and not metadata.get('gated')
    gap_path = ROOT / 'outputs/gru_beta_neutral_20261008/backfilled_panel_candidate_canonical_ipo_v6/gap_audit/missing_stockdays.parquet'
    gaps = pd.read_parquet(gap_path)
    gaps = gaps[gaps.minimum_requested & gaps.primary_tradestatus.eq('1') & gaps.primary_volume.gt(0)].copy()
    gaps['year'] = gaps.date.str[:4]
    targets = gaps[gaps.year.isin(['2019', '2020', '2021', '2022'])]
    assert len(targets) == 208 and int(targets.missing_hours.sum()) == 829
    target_path = OUT / 'actual_2019_2022_targets.csv'
    assert not target_path.exists()
    targets.to_csv(target_path, index=False)
    identity_path = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock/old_code_factor_identity_20261009/bridge_validation_receipt.json'
    identity = json.loads(identity_path.read_text())
    allowed_aliases = {}
    for record in identity['records']:
        if record['symbol'] in identity['research_bridge_symbols']:
            assert sha(record['official_statement_path']) == record['official_statement_sha256']
            allowed_aliases[record['symbol']] = record['historical_code'].replace('.', '')
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'metadata_path': str(metadata_path), 'metadata_sha256': sha(metadata_path),
               'gap_path': str(gap_path), 'gap_sha256': sha(gap_path),
               'actual_target_csv_path': str(target_path), 'actual_target_csv_sha256': sha(target_path),
               'official_identity_receipt_path': str(identity_path), 'official_identity_receipt_sha256': sha(identity_path),
               'range_reader_sha256': sha(ROOT / 'gru_beta_neutral_20261008/inspect_wind17_remote_zip.py'),
               'daily_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps_decimal.py'),
               'base_daily_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/audit_wind17_target_gaps.py'),
               'target_stockyears': int(targets.groupby(['symbol', 'year']).ngroups),
               'target_missing_hours': int(targets.missing_hours.sum()), 'results': [],
               'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'whole_archive_LFS_SHA256_verified': False, 'minute_quantity_precision_certified': False,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    for (symbol, year), wanted in targets.groupby(['symbol', 'year'], sort=True):
        file = next(f for f in metadata['siblings'] if f['rfilename'] == year + '.zip')
        source_code = symbol[-2:].lower() + symbol[:6]
        member = year + '/' + source_code + '.csv'
        url = 'https://huggingface.co/datasets/wind17/china-a-share-zip/resolve/' + metadata['sha'] + '/' + file['rfilename']
        reader = RangeReader(url, file['size'], OUT / (source_code + '_' + year + '_ranges'))
        item = {'canonical_symbol': symbol, 'year': year, 'observed_source_symbol': source_code,
                'zip_member': member, 'source_file': file, 'pinned_revision': metadata['sha'],
                'target_stockdays': len(wanted), 'target_missing_hours': int(wanted.missing_hours.sum()),
                'range_receipts': reader.receipts, 'status': 'pending'}
        try:
            with zipfile.ZipFile(reader) as archive:
                assert len(archive.infolist()) <= 200000
                alias = allowed_aliases.get(symbol)
                if member not in archive.namelist() and alias and (year + '/' + alias + '.csv') in archive.namelist():
                    source_code = alias
                    member = year + '/' + alias + '.csv'
                    item.update(observed_source_symbol=source_code, zip_member=member,
                                officially_verified_code_alias_used=True)
                if member not in archive.namelist():
                    item['status'] = 'exact_target_not_in_central_directory'
                else:
                    entry = archive.getinfo(member)
                    assert not entry.flag_bits & 1 and entry.file_size <= 16 * 1024 * 1024
                    assert entry.compress_size <= 4 * 1024 * 1024 and entry.file_size <= entry.compress_size * 500
                    raw = archive.read(entry)
                    raw_path = OUT / (source_code + '_' + year + '.csv')
                    assert not raw_path.exists()
                    raw_path.write_bytes(raw)
                    frame = pd.read_csv(raw_path, encoding='gbk').rename(columns=FIELDS)
                    assert frame['股票代码'].eq(source_code).all()
                    frame['time'] = pd.to_datetime(frame['k线结束时间'])
                    assert frame.time.dt.year.eq(int(year)).all() and not frame.time.duplicated().any()
                    frame['date'] = frame.time.dt.strftime('%Y-%m-%d')
                    pp = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
                    primary = json.loads(pp.read_text())
                    daily = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
                    checks = []
                    for target in wanted.itertuples():
                        day = frame[frame.date.eq(target.date)]
                        checked = day_check(day, daily[target.date]) if len(day) else {'existing_daily_and_hour_gates_passed': False, 'status': 'actual_target_date_absent'}
                        checks.append({'date': target.date, 'existing_missing_hours': int(target.missing_hours), **checked})
                    item.update(status='exact_CSV_acquired_and_daily_diagnostics_complete', raw_path=str(raw_path),
                                raw_sha256=sha(raw_path), raw_bytes=len(raw), CRC32=entry.CRC,
                                member_CRC32_verified=True, whole_ZIP_digest_verified=False,
                                first_source_timestamp=str(frame.time.min()), last_source_timestamp=str(frame.time.max()),
                                source_rows=len(frame), primary_daily_path=str(pp), primary_daily_sha256=sha(pp),
                                target_stockday_checks=checks,
                                target_dates_observed=sum(c.get('status') != 'actual_target_date_absent' for c in checks),
                                daily_gate_pass_hours=sum(c['existing_missing_hours'] for c in checks if c['existing_daily_and_hour_gates_passed']),
                                candidate_hours_admitted=0)
        except Exception as error:
            item.update(status='scoped_member_acquisition_or_daily_audit_failed', error_type=type(error).__name__, error=str(error))
        item['range_bytes_acquired'] = reader.total
        receipt['results'].append(item)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: item.get(k) for k in ['canonical_symbol', 'year', 'status', 'target_missing_hours',
                                                'first_source_timestamp', 'last_source_timestamp', 'target_dates_observed', 'daily_gate_pass_hours', 'error']}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    receipt['daily_gate_pass_hours'] = sum(r.get('daily_gate_pass_hours', 0) for r in receipt['results'])
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
