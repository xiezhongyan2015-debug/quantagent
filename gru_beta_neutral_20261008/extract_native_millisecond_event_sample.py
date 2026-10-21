"""Extend a verified-offset public gzip prefix to inspect an actual ms sample."""
import hashlib
import io
import json
import sys
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from probe_new_archive_structure import fetch_range

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009'
TARGETS = {'20260923/000006.SZ/events.parquet', '20260923/000006.SZ/snapshot.parquet'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'bounded_native_millisecond_sample_acquisition.json'
    assert not output.exists()
    initial_path = OUT / '20260923.tar.gz.first8MiB.bin'
    structure_path = OUT / 'bounded_structure_probe_receipt.json'
    structure = json.loads(structure_path.read_text()); basis = structure['results'][-1]['prefix_request']
    assert sha(initial_path) == basis['retained_range_sha256']
    info_path = OUT / 'suncong__Ashare-events_info.json'; info = json.loads(info_path.read_text())
    published = next(r for r in info['siblings'] if r['rfilename'] == '20260923.tar.gz')
    request, extension = fetch_range(info['id'], info['sha'], published['rfilename'], published['size'],
                                     8 * 1024 * 1024, 32 * 1024 * 1024,
                                     '20260923.tar.gz.next32MiB.bin')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    plain = decoder.decompress(initial_path.read_bytes() + extension, 80 * 1024 * 1024)
    assert len(plain) <= 80 * 1024 * 1024
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'range_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/probe_new_archive_structure.py'),
               'pinned_revision': info['sha'], 'repository': info['id'], 'metadata_sha256': sha(info_path),
               'initial_prefix_sha256': sha(initial_path), 'initial_range': basis, 'extension_range': request,
               'compressed_bytes_retained': 40 * 1024 * 1024, 'decompressed_prefix_bytes': len(plain),
               'whole_outer_archive_LFS_SHA256_verified': False, 'whole_gzip_CRC_verified': False,
               'whole_gzip_eof_observed': decoder.eof, 'members_seen': [], 'selected_complete_members': [],
               'candidate_changed': False, 'candidate_hours_admitted': 0}
    try:
        stream = tarfile.open(fileobj=io.BytesIO(plain), mode='r|')
        for member in stream:
            receipt['members_seen'].append({'name': member.name, 'declared_bytes': member.size})
            if member.name in TARGETS:
                assert member.isfile() and member.size <= 16 * 1024 * 1024
                raw = stream.extractfile(member).read(); assert len(raw) == member.size
                dest = OUT / ('000006_20260923_' + Path(member.name).name)
                assert not dest.exists(); dest.write_bytes(raw)
                parquet = pq.ParquetFile(dest)
                receipt['selected_complete_members'].append({'member_name': member.name, 'path': str(dest),
                                                             'bytes': len(raw), 'sha256': sha(dest),
                                                             'rows': parquet.metadata.num_rows, 'schema': str(parquet.schema_arrow)})
                print(json.dumps({'complete_member': member.name, 'rows': parquet.metadata.num_rows, 'sha256': sha(dest)}), flush=True)
            if len(receipt['selected_complete_members']) == len(TARGETS):
                break
        receipt['status'] = 'both_target_members_complete' if len(receipt['selected_complete_members']) == len(TARGETS) else 'bounded_prefix_ended_before_all_targets'
    except Exception as error:
        receipt.update(status='bounded_prefix_terminal_boundary', error_type=type(error).__name__)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k in ('status', 'compressed_bytes_retained', 'decompressed_prefix_bytes', 'whole_outer_archive_LFS_SHA256_verified', 'whole_gzip_eof_observed', 'error_type')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
