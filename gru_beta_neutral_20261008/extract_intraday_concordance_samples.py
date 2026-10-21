"""Copy exact diagnostic payloads from the immutable split minute archive."""
import gzip
import hashlib
import io
import json
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
sys.path.insert(0, str(ROOT / 'gru_research'))
from minute_data import SplitFile

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/original_intraday_concordance_20261009'
TARGETS = {('20220606', '002831'), ('20220606', '600031'),
           ('20220606', '600167'), ('20230106', '688281')}


def main():
    OUT.mkdir(exist_ok=True)
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
                    if key in TARGETS:
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
                        assert written <= 4 * 1024 * 1024
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
               'payloads': records, 'not_present_in_original': sorted(TARGETS - keys),
               'full_split_archive_sha256_verified': True, 'gzip_footer_checked': True,
               'members_seen': members, 'written_bytes': written,
               'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'source_modified': False, 'panel_modified': False,
               'elapsed_seconds': round(time.monotonic() - start, 2)}
    (OUT / 'extraction_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
