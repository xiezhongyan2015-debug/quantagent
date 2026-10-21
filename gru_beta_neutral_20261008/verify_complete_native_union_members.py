"""Verify full-source integrity and every retained union member without admission."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009'
OUT = SOURCE / 'whole_event_archive/full_union_day_native_members'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    output = OUT / 'independent_complete_native_member_verification.json'
    assert not output.exists()
    receipt_path = OUT / 'complete_native_member_acquisition.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'complete_archive_native_members_copied_pending_values_and_admission_audit'
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_complete_archive_full_union_day.py') == receipt['source_code_sha256']
    acquisition_path = SOURCE / 'whole_event_archive/complete_archive_acquisition.json'
    assert sha(acquisition_path) == receipt['complete_archive_receipt_sha256']
    acquisition = json.loads(acquisition_path.read_text())
    assert acquisition['whole_published_LFS_SHA256_verified'] and acquisition['whole_gzip_CRC_verified']
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_complete_native_event_archive.py') == acquisition['source_code_sha256']
    info_path = SOURCE / 'suncong__Ashare-events_info.json'
    assert sha(info_path) == acquisition['metadata_sha256']
    info = json.loads(info_path.read_text())
    published = next(r for r in info['siblings'] if r['rfilename'] == '20260923.tar.gz')
    path = Path(receipt['source_archive_path'])
    assert path.stat().st_size == published['size'] == acquisition['published_bytes'] == 15803513411
    assert sha(path) == published['lfs']['sha256'] == receipt['source_archive_sha256'] == acquisition['actual_sha256']
    assert info['sha'] == acquisition['pinned_revision'] == '301397490bea1a168083298da77f750a083a4ef1'
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    gap_path = meta_path.parent.parent / 'gap_audit/missing_stockdays.parquet'
    assert sha(meta_path) == receipt['candidate_meta_sha256'] and sha(gap_path) == receipt['current_missing_stockday_mask_sha256']
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    complete_path = meta_path.parent / 'hour_complete.npy'
    assert sha(calendar_path) == receipt['calendar_sha256'] and sha(complete_path) == receipt['current_hour_complete_sha256']
    calendar = json.loads(calendar_path.read_text())
    requested = set(calendar['union_symbols'])
    assert len(requested) == 1178 and requested == set(receipt['requested_symbols'])
    d = calendar['dates'].index('2026-09-23')
    complete = np.load(complete_path, mmap_mode='r')
    controls = {calendar['union_symbols'][i] for i in np.flatnonzero(complete[d * 4:d * 4 + 4].all(0))}
    targets = {r['symbol'] for r in pq.read_table(gap_path).to_pylist() if r['date'] == '2026-09-23' and r['primary_tradestatus'] == '1'
               and r['primary_volume'] > 0 and r['missing_hours'] > 0}
    assert len(controls) == 499 and len(targets) == 624 and not targets & controls
    assert targets == set(receipt['actual_missing_target_symbols']) and controls == set(receipt['all_current_complete_stockday_control_symbols'])
    by_member = {r['original_member']: r for r in receipt['records']}
    assert len(by_member) == len(receipt['records'])
    found = set()
    members = total = event_rows = snapshot_rows = 0
    started = last = time.monotonic()
    with gzip.open(path, 'rb') as zipped:
        with tarfile.open(fileobj=zipped, mode='r|') as archive:
            for member in archive:
                members += 1
                pieces = member.name.split('/')
                selected = member.isfile() and len(pieces) == 3 and pieces[0] == '20260923' and pieces[1] in requested and pieces[2] in ['events.parquet', 'snapshot.parquet']
                if selected:
                    assert member.name in by_member and member.name not in found
                    found.add(member.name)
                    record = by_member[member.name]
                    raw = archive.extractfile(member).read()
                    assert len(raw) == member.size == record['bytes']
                    assert hashlib.sha256(raw).hexdigest() == record['sha256']
                    assert raw == Path(record['path']).read_bytes()
                    parquet = pq.ParquetFile(io.BytesIO(raw))
                    assert parquet.metadata.num_rows == record['rows'] and parquet.metadata.num_row_groups == record['row_groups']
                    assert parquet.schema_arrow.names == record['columns']
                    metadata = {k.decode(): v.decode(errors='replace') for k, v in (parquet.schema_arrow.metadata or {}).items()}
                    assert metadata == record['schema_metadata']
                    total += len(raw)
                    if pieces[2] == 'events.parquet':
                        event_rows += record['rows']
                    else:
                        snapshot_rows += record['rows']
                if time.monotonic() - last >= 20:
                    print(json.dumps({'phase': 'independent_complete_source_member_rescan', 'members_seen': members,
                                      'complete_union_members_verified': len(found), 'native_member_bytes_verified': total}), flush=True)
                    last = time.monotonic()
                if members % 1000 == 0:
                    archive.members.clear()
        while zipped.read(4 * 1024 * 1024):
            pass
    assert found == set(by_member) and total == receipt['written_bytes'] and members == receipt['members_seen']
    pairs = {s for s in requested if '20260923/' + s + '/events.parquet' in found and '20260923/' + s + '/snapshot.parquet' in found}
    missing = ['20260923/' + s + '/' + k for s in sorted(requested) for k in ['events.parquet', 'snapshot.parquet']
               if '20260923/' + s + '/' + k not in found]
    assert len(pairs) == receipt['complete_event_snapshot_pairs']
    assert len(pairs & targets) == receipt['actual_missing_stockdays_with_complete_source_pairs']
    assert 4 * len(pairs & targets) == receipt['actual_missing_hours_with_complete_source_pairs']
    assert len(pairs & controls) == receipt['current_complete_control_stockdays_with_complete_source_pairs']
    assert missing == receipt['missing_requested_members']
    assert sha(meta_path) == receipt['candidate_meta_sha256']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'native_member_acquisition_receipt_sha256': sha(receipt_path), 'complete_archive_acquisition_receipt_sha256': sha(acquisition_path),
              'complete_outer_archive_fresh_published_LFS_SHA256_verified': True, 'whole_gzip_CRC_and_EOF_independently_verified': True,
              'whole_tar_members_scanned': members, 'whole_source_sha256': sha(path), 'all_retained_native_members_exact_to_whole_source': len(found),
              'all_retained_native_member_bytes_verified': total, 'retained_event_rows': event_rows, 'retained_snapshot_rows': snapshot_rows,
              'full_historical_union_requested_symbols': 1178, 'complete_event_snapshot_stockday_pairs': len(pairs),
              'actual_missing_stockdays_with_complete_source_pairs': len(pairs & targets), 'actual_missing_hours_with_complete_source_pairs': 4 * len(pairs & targets),
              'current_complete_controls_with_complete_source_pairs': len(pairs & controls), 'missing_requested_members': missing,
              'current_candidate_meta_sha256': sha(meta_path), 'quantity_precision_quote_definition_or_PIT_admission_certified': False,
              'payload_presence_not_claimed_hour_qualification': True, 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'missing_requested_members'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
