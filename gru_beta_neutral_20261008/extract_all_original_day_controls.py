"""Authenticate every current complete Sep23 stockday's original minute payload."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
sys.path.insert(0, str(ROOT / 'gru_research'))
from minute_data import SplitFile

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive/all_original_day_controls'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'extraction_receipt.json'
    assert not output.exists()
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    assert sha(meta_path) == '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    complete_path = meta_path.parent / 'hour_complete.npy'
    assert sha(complete_path) == '56b016d3a33d5c3a4c640de156ea2e716e16c304c7b307879c34810cc095eb59'
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == 'bb3d3b9f0a013a9d4ae51eb1ac43475418401f178fdb894d776e38dc48170d25'
    calendar = json.loads(calendar_path.read_text())
    complete = np.load(complete_path, mmap_mode='r')
    d = calendar['dates'].index('2026-09-23')
    indices = np.flatnonzero(np.all(complete[d * 4:d * 4 + 4], axis=0))
    targets = {('20260923', calendar['union_symbols'][i][:6]) for i in indices}
    assert len(targets) == 499
    parts = sorted(ROOT.glob('A股分钟k线数据.tar.gz.*'))
    expected = json.loads((ROOT / 'gru_research/artifacts/minute_source_fingerprints.json').read_text())['parts']
    assert len(parts) == len(expected) == 5
    stream = SplitFile(parts, fingerprint=True)
    records, keys = [], set()
    members, written = 0, 0
    start = last = time.monotonic()
    with io.BufferedReader(stream, buffer_size=1024 * 1024) as raw, gzip.GzipFile(fileobj=raw) as zipped:
        with tarfile.open(fileobj=zipped, mode='r|') as archive:
            for member in archive:
                members += 1
                pieces = member.name.split('/')
                if member.isfile() and len(pieces) == 3 and pieces[-1].endswith('.parquet'):
                    key = (pieces[1], pieces[-1][:-8])
                    if key in targets:
                        assert key not in keys and member.size <= 1024 * 1024
                        keys.add(key)
                        payload = archive.extractfile(member).read()
                        assert len(payload) == member.size
                        path = OUT / (key[1] + '_' + key[0] + '.parquet')
                        if path.exists():
                            assert path.read_bytes() == payload
                        else:
                            path.write_bytes(payload)
                        written += len(payload)
                        assert written <= 16 * 1024 * 1024
                        records.append({'date': key[0], 'code': key[1], 'original_tar_member': member.name,
                                        'path': str(path), 'bytes': len(payload),
                                        'sha256': hashlib.sha256(payload).hexdigest(),
                                        'raw_columns': pq.read_schema(path).names})
                if time.monotonic() - last > 20:
                    print(json.dumps({'members_seen': members, 'payloads_copied': len(records),
                                      'compressed_bytes_read': stream.bytes_read,
                                      'elapsed_seconds': round(time.monotonic() - start, 2)}), flush=True)
                    last = time.monotonic()
                if members % 1000 == 0:
                    archive.members.clear()
        while zipped.read(1024 * 1024):
            pass
    fingerprints = [{'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': h.hexdigest()}
                    for p, h in zip(parts, stream.digests)]
    assert fingerprints == expected
    assert stream.bytes_read == sum(r['size_bytes'] for r in expected)
    receipt = {'status': 'original_intraday_samples_extracted', 'source_parts': fingerprints,
               'payloads': records, 'not_present_in_original': sorted(targets - keys),
               'full_split_archive_sha256_verified': True, 'gzip_footer_checked': True,
               'members_seen': members, 'written_bytes': written,
               'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'source_modified': False, 'panel_modified': False,
               'elapsed_seconds': round(time.monotonic() - start, 2)}
    receipt.update(checked_utc=datetime.now(timezone.utc).isoformat(), all_current_complete_control_stockdays_requested=499,
                   scope_date='2026-09-23', current_candidate_meta_sha256=sha(meta_path),
                   current_hour_complete_sha256=sha(complete_path), calendar_sha256=sha(calendar_path),
                   source_fingerprint_receipt_sha256=sha(ROOT / 'gru_research/artifacts/minute_source_fingerprints.json'))
    assert not receipt['not_present_in_original'] and len(records) == 499
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'payloads'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
