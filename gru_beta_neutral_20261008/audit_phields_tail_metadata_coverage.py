"""Check observed source ticker inventory against the exact unresolved V6 tail."""
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from inspect_phields_trade_archive import ROOT, OUT, REPO, REVISION, fetch

sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
NAME = 'backfilled_panel_candidate_canonical_ipo_v6'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'exact_tail_metadata_coverage_receipt.json'
    assert not output.exists()
    ap = OUT / 'pinned_metadata_and_target_inventory.json'
    a = json.loads(ap.read_text())
    for request in a['requests']:
        assert sha(request['path']) == request['sha256']
    info = json.loads((OUT / 'dataset_info_blobs.json').read_text())
    siblings = {r['rfilename']: r for r in info['siblings']}
    requests = []
    for filename in ['metadata/schema.json', 'metadata/ticker_calendar.parquet', 'metadata/daily_manifest.parquet']:
        published = siblings[filename]
        receipt, raw = fetch('https://huggingface.co/datasets/' + REPO + '/resolve/' + REVISION + '/' + filename, filename.replace('/', '_'))
        assert len(raw) == published['size']
        if 'lfs' in published:
            assert hashlib.sha256(raw).hexdigest() == published['lfs']['sha256']
            receipt['whole_metadata_LFS_SHA256_verified'] = True
        else:
            assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == published['blobId']
            receipt['metadata_git_blob_SHA1_verified'] = True
        requests.append(receipt)
    table = pq.read_table(OUT / 'metadata_ticker_calendar.parquet')
    source_rows = table.to_pylist()
    source = {}
    for row in source_rows:
        key = row['ticker'], str(row['trade_date'])
        assert key not in source
        assert row['ticker'][:2] == row['code_prefix']
        source[key] = row
    gap_path = BASE / NAME / 'gap_audit/missing_stockdays.parquet'
    targets = [r for r in pq.read_table(gap_path).to_pylist() if r['date'] > '2026-08-06' and
               r['primary_tradestatus'] == '1' and r['primary_volume'] > 0 and r['missing_hours'] > r['current_member_hours']]
    assert sum(r['missing_hours'] - r['current_member_hours'] for r in targets) == 84860
    coverage = []
    for target in targets:
        key = target['symbol'].split('.')[0], target['date']
        row = source.get(key)
        coverage.append({
            'symbol': target['symbol'], 'date': target['date'],
            'actual_missing_nonmember_hours': target['missing_hours'] - target['current_member_hours'],
            'source_inventory_record_present': row is not None,
            'source_inventory_row_count': None if row is None else row['row_count'],
            'source_inventory_min_time_s': None if row is None else row['min_time_s'],
            'source_inventory_max_time_s': None if row is None else row['max_time_s'],
        })
    path = OUT / 'every_missing_tail_stockday_metadata_coverage.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(coverage[0]))
        writer.writeheader(); writer.writerows(coverage)
    manifest = pq.read_table(OUT / 'metadata_daily_manifest.parquet')
    result = {
        'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
        'metadata_acquisition_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/inspect_phields_trade_archive.py'),
        'pinned_revision': REVISION, 'metadata_inventory_receipt_sha256': sha(ap), 'requests': requests,
        'source_ticker_calendar_rows': table.num_rows, 'source_ticker_calendar_schema': str(table.schema),
        'source_daily_manifest_rows': manifest.num_rows, 'source_daily_manifest_schema': str(manifest.schema),
        'current_candidate_version': NAME, 'candidate_meta_sha256': sha(BASE / NAME / 'dataset/meta.json'),
        'current_gap_stockdays_sha256': sha(gap_path), 'target_nonmember_stockdays': len(targets),
        'actual_missing_nonmember_hours': 84860,
        'target_stockdays_present_in_source_inventory': sum(r['source_inventory_record_present'] for r in coverage),
        'target_missing_hours_with_source_inventory_record': sum(r['actual_missing_nonmember_hours'] for r in coverage if r['source_inventory_record_present']),
        'target_missing_hours_without_source_inventory_record': sum(r['actual_missing_nonmember_hours'] for r in coverage if not r['source_inventory_record_present']),
        'source_missing_target_dates': dict(Counter(r['date'] for r in coverage if not r['source_inventory_record_present'])),
        'coverage_csv_path': str(path), 'coverage_csv_sha256': sha(path), 'source_trade_payloads_verified': False,
        'source_payload_trade_sequence_and_quantity_certified': False, 'candidate_changed': False,
        'candidate_hours_admitted': 0, 'production_ready': False,
        'method': 'Complete ticker-calendar metadata file SHA256, original schema JSON Git blob, exact intersection with current V6 primary-trading nonmember missing stockdays; source inventory presence is not trade payload quality or actual minute admission.'
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'requests'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
