"""Trace observed pre-rename minutes from the original immutable delivery."""
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

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'official_old_code_minute_delivery_20261009'
MAPPING = {'000022': ('001872.SZ', '2018-12-26'),
           '000043': ('001914.SZ', '2019-12-16'),
           '300114': ('302132.SZ', '2025-02-17')}


def main():
    OUT.mkdir(exist_ok=True)
    identity = BASE / 'public_source_probes/baostock/old_code_factor_identity_20261009/bridge_validation_receipt.json'
    proof = json.loads(identity.read_text())
    for code, (symbol, date) in MAPPING.items():
        official = next(r for r in proof['records'] if r['symbol'] == symbol)
        assert official['historical_code'] == 'sz.' + code and official['official_code_change_date'] == date
        p = Path(official['official_statement_path'])
        assert hashlib.sha256(p.read_bytes()).hexdigest() == official['official_statement_sha256']
    parts = sorted(ROOT.glob('A股分钟k线数据.tar.gz.*'))
    expected = json.loads((ROOT / 'gru_research/artifacts/minute_source_fingerprints.json').read_text())['parts']
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
                    code = pieces[-1][:-8]
                    if code in MAPPING:
                        d = pieces[1]
                        day = f'{d[:4]}-{d[4:6]}-{d[6:]}'
                        symbol, until = MAPPING[code]
                        if '2018-01-01' <= day < until:
                            assert member.size <= 1024 * 1024
                            payload = archive.extractfile(member).read()
                            key = (day, symbol)
                            assert key not in keys
                            keys.add(key)
                            path = OUT / symbol / (day + '.parquet')
                            path.parent.mkdir(exist_ok=True)
                            if path.exists():
                                assert path.read_bytes() == payload
                            else:
                                path.write_bytes(payload)
                            written += len(payload)
                            assert written < 128 * 1024 * 1024
                            records.append({'date': day, 'canonical_symbol': symbol, 'observed_symbol': code + '.SZ',
                                            'original_tar_member': member.name, 'path': str(path), 'bytes': len(payload),
                                            'sha256': hashlib.sha256(payload).hexdigest(),
                                            'official_code_change_date': until})
                if time.monotonic() - last > 25:
                    progress = {'status': 'reading_original_delivery', 'members_seen': members,
                                'stockdays_copied': len(records), 'compressed_bytes_read': stream.bytes_read,
                                'elapsed_seconds': round(time.monotonic() - start, 2)}
                    (OUT / 'progress.json').write_text(json.dumps(progress, indent=2))
                    print(json.dumps(progress), flush=True)
                    last = time.monotonic()
                if members % 1000 == 0:
                    archive.members.clear()
        while zipped.read(1024 * 1024):
            pass
    fingerprints = [{'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': h.hexdigest()}
                    for p, h in zip(parts, stream.digests)]
    assert fingerprints == expected
    assert stream.bytes_read == sum(r['size_bytes'] for r in expected)
    receipt = {'status': 'original_pre_rename_payload_extraction_complete', 'source_parts': fingerprints,
               'payloads': records, 'stockdays_copied': len(records), 'written_bytes': written,
               'identity_receipt_sha256': hashlib.sha256(identity.read_bytes()).hexdigest(),
               'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'original_source_modified': False, 'prices_or_minutes_fabricated': False,
               'panel_admitted': False, 'elapsed_seconds': round(time.monotonic() - start, 2)}
    (OUT / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'payloads'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
