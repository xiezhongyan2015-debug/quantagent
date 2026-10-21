"""Copy exact native members for every currently missing trading stockday on Sep23.

The scope comes from the current immutable missing mask. Whole-source integrity
must pass first. No prices, trades, adjusted factors or panel cells are created.
"""
import gzip
import hashlib
import io
import json
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009'
OUT = SOURCE / 'whole_event_archive/current_tail_native_members'
DAY = '2026-09-23'
CODE_DAY = '20260923'
CONTROL_SYMBOLS = {'000009.SZ', '600004.SH'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    acquisition_path = SOURCE / 'whole_event_archive/complete_archive_acquisition.json'
    acquisition = json.loads(acquisition_path.read_text())
    assert acquisition['status'] == 'complete_whole_archive_SHA256_and_gzip_CRC_verified'
    assert acquisition['whole_published_LFS_SHA256_verified'] and acquisition['whole_gzip_CRC_verified']
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_complete_native_event_archive.py') == acquisition['source_code_sha256']
    archive_path = Path(acquisition['path'])
    assert sha(archive_path) == acquisition['published_LFS_sha256'] == acquisition['actual_sha256']
    assert shutil.disk_usage(SOURCE).free >= 24 * 1024 ** 3
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    gap_path = meta_path.parent.parent / 'gap_audit/missing_stockdays.parquet'
    assert sha(gap_path) == '5261872a20bd2cc988a5c5240b5642316439795841a5d9fd2f3dd5935164fb18'
    targets = [r for r in pq.read_table(gap_path).to_pylist() if r['date'] == DAY
               and r['primary_tradestatus'] == '1' and r['primary_volume'] > 0 and r['missing_hours'] > 0]
    assert len(targets) == 624
    assert all(r['missing_hours'] == 4 and r['current_member_hours'] == 0 for r in targets)
    requested = {r['symbol'] for r in targets} | CONTROL_SYMBOLS
    assert len(requested) == 626
    OUT.mkdir(exist_ok=True)
    output = OUT / 'complete_native_member_acquisition.json'
    assert not output.exists()
    expected = {'20260923/000006.SZ/events.parquet': SOURCE / '000006_20260923_events.parquet',
                '20260923/000006.SZ/snapshot.parquet': SOURCE / '000006_20260923_snapshot.parquet',
                '20260923/000009.SZ/events.parquet': SOURCE / 'quote_boundary_audit/000009_20260923_events.parquet',
                '20260923/000009.SZ/snapshot.parquet': SOURCE / 'quote_boundary_audit/000009_20260923_snapshot.parquet'}
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'complete_archive_receipt_sha256': sha(acquisition_path), 'published_outer_archive_SHA256_verified': True,
              'source_archive_path': str(archive_path), 'source_archive_sha256': sha(archive_path),
              'candidate_meta_sha256': sha(meta_path), 'current_missing_stockday_mask_sha256': sha(gap_path),
              'scope_date': DAY, 'actual_current_missing_stockdays': 624, 'actual_current_missing_hours': 2496,
              'positive_control_symbols': sorted(CONTROL_SYMBOLS), 'requested_symbols': sorted(requested),
              'retention_cap_bytes': 8 * 1024 ** 3, 'records': [], 'candidate_changed': False,
              'candidate_hours_admitted': 0, 'source_quote_definition_or_PIT_admission_certified': False,
              'production_ready': False, 'status': 'extracting_complete_native_source_members'}
    seen = set()
    written = members = 0
    last = started = time.monotonic()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    try:
        with archive_path.open('rb') as raw, gzip.GzipFile(fileobj=raw) as zipped:
            with tarfile.open(fileobj=zipped, mode='r|') as archive:
                for member in archive:
                    members += 1
                    pieces = member.name.split('/')
                    if member.isfile() and len(pieces) == 3 and pieces[0] == CODE_DAY and pieces[1] in requested and pieces[2] in ['events.parquet', 'snapshot.parquet']:
                        assert member.name not in seen and member.size <= 128 * 1024 * 1024
                        seen.add(member.name)
                        payload = archive.extractfile(member).read()
                        assert len(payload) == member.size and payload[:4] == payload[-4:] == b'PAR1'
                        written += len(payload)
                        assert written <= result['retention_cap_bytes']
                        if member.name in expected:
                            assert payload == expected[member.name].read_bytes(), 'Whole archive must reproduce cached range-backed members'
                        dest = OUT / ('symbol=' + pieces[1])
                        dest.mkdir(exist_ok=True)
                        path = dest / pieces[2]
                        assert not path.exists()
                        path.write_bytes(payload)
                        parquet = pq.ParquetFile(io.BytesIO(payload))
                        result['records'].append({'original_member': member.name, 'symbol': pieces[1], 'kind': pieces[2][:-8],
                            'path': str(path), 'bytes': len(payload), 'sha256': sha(path),
                            'rows': parquet.metadata.num_rows, 'row_groups': parquet.num_row_groups,
                            'columns': parquet.schema_arrow.names,
                            'schema_metadata': {k.decode(): v.decode(errors='replace') for k, v in (parquet.schema_arrow.metadata or {}).items()},
                            'matches_prior_cached_member_if_any': member.name in expected,
                            'actual_source_values_preserved': True})
                    if time.monotonic() - last >= 20:
                        result.update(members_seen=members, written_bytes=written)
                        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                        print(json.dumps({'phase': 'source_native_member_extraction', 'members_seen': members,
                                          'selected_complete_members': len(result['records']), 'written_bytes': written,
                                          'elapsed_seconds': round(time.monotonic() - started, 1)}), flush=True)
                        last = time.monotonic()
                    if members % 1000 == 0:
                        archive.members.clear()
            while zipped.read(4 * 1024 * 1024):
                pass
        paired = {symbol for symbol in requested if ('20260923/' + symbol + '/events.parquet') in seen
                  and ('20260923/' + symbol + '/snapshot.parquet') in seen}
        missing_members = ['20260923/' + symbol + '/' + field for symbol in sorted(requested)
                           for field in ['events.parquet', 'snapshot.parquet'] if ('20260923/' + symbol + '/' + field) not in seen]
        assert set(expected) <= seen
        result.update(status='complete_archive_native_members_copied_pending_values_and_admission_audit',
            gzip_footer_and_EOF_checked_during_member_extraction=True, members_seen=members, written_bytes=written,
            complete_event_snapshot_pairs=len(paired), actual_missing_stockdays_with_complete_source_pairs=len(paired - CONTROL_SYMBOLS),
            actual_missing_hours_with_complete_source_pairs=4 * len(paired - CONTROL_SYMBOLS),
            missing_requested_members=missing_members, completed_utc=datetime.now(timezone.utc).isoformat())
        assert sha(meta_path) == result['candidate_meta_sha256'] and sha(gap_path) == result['current_missing_stockday_mask_sha256']
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k not in ['records', 'requested_symbols']}, ensure_ascii=False), flush=True)
    except Exception as error:
        result.update(status='failed_terminal_preserved_complete_members', error_type=type(error).__name__,
                      completed_utc=datetime.now(timezone.utc).isoformat(), members_seen=members, written_bytes=written)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        raise


if __name__ == '__main__':
    main()
