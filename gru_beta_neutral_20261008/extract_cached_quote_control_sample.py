"""Copy two complete native control members from already retained source bytes."""
import hashlib
import io
import json
import sys
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/additional_native_inventory_20261009'
OUT = SOURCE / 'quote_boundary_audit'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'cached_control_member_acquisition.json'
    assert not output.exists()
    receipt_path = SOURCE / 'bounded_native_millisecond_sample_acquisition.json'
    prior = json.loads(receipt_path.read_text())
    chunks = [prior['initial_range'], prior['extension_range']]
    for c in chunks:
        assert sha(c['path']) == c['retained_range_sha256']
    raw = b''.join(Path(c['path']).read_bytes() for c in chunks)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    native = decoder.decompress(raw, 80 * 1024 * 1024)
    assert len(native) == prior['decompressed_prefix_bytes'] and not decoder.eof
    wanted = {'20260923/000009.SZ/events.parquet', '20260923/000009.SZ/snapshot.parquet'}
    records = []
    with tarfile.open(fileobj=io.BytesIO(native), mode='r|') as archive:
        for member in archive:
            if member.name in wanted:
                assert member.isfile() and member.offset_data + member.size <= len(native)
                payload = archive.extractfile(member).read()
                assert len(payload) == member.size
                path = OUT / ('000009_20260923_' + member.name.rsplit('/', 1)[1])
                assert not path.exists()
                path.write_bytes(payload)
                records.append({'member': member.name, 'path': str(path), 'bytes': len(payload),
                                'sha256': sha(path), 'rows': pq.ParquetFile(path).metadata.num_rows})
                if len(records) == 2:
                    break
    assert {r['member'] for r in records} == wanted
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
              'source_acquisition_receipt_sha256': sha(receipt_path), 'repository': prior['repository'],
              'pinned_revision': prior['pinned_revision'], 'source_ranges': chunks, 'selected_members': records,
              'network_bytes_acquired_this_run': 0, 'whole_outer_LFS_SHA256_verified': False,
              'whole_outer_gzip_CRC_verified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
