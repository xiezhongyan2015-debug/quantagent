"""Acquire two complete pinned parquet samples, retaining their whole published hashes."""
import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/phields_trades_20261009'
REVISION = '3acef8b18f56786e253ccd034072a8f1845b507e'
TARGETS = [
    'data/l2_trades/trade_date=2026-09-23/code_prefix=00/part-00000.parquet',
    'data/l2_trades/trade_date=2026-09-23/code_prefix=60/part-00000.parquet',
]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'verified_whole_sample_acquisition.json'
    assert not output.exists()
    info_path = OUT / 'dataset_info_blobs.json'
    info = json.loads(info_path.read_text())
    assert info['sha'] == REVISION and info['gated'] is False and info['private'] is False
    index = {r['rfilename']: r for r in info['siblings']}
    assert sum(index[p]['size'] for p in TARGETS) <= 600 * 1024 * 1024
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'pinned_revision': REVISION, 'dataset_metadata_sha256': sha(info_path),
               'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
               'maximum_total_sample_bytes': 600 * 1024 * 1024, 'results': []}
    dest = OUT / 'verified_whole_samples'; dest.mkdir(exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    for filename in TARGETS:
        published = index[filename]
        prefix = filename.split('code_prefix=')[1].split('/')[0]
        path = dest / ('2026-09-23_' + prefix + '_part-00000.parquet')
        temporary = path.with_suffix('.part')
        assert not path.exists() and not temporary.exists()
        url = 'https://huggingface.co/datasets/phields/a-share-l2-trades/resolve/' + REVISION + '/' + urllib.parse.quote(filename, safe='/')
        record = {'filename': filename, 'url': url, 'published_bytes': published['size'],
                  'published_LFS_sha256': published['lfs']['sha256'], 'path': str(path),
                  'started_utc': datetime.now(timezone.utc).isoformat()}
        receipt['results'].append(record)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
            with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
                assert response.status == 200 and response.url.startswith('https://')
                record.update(http_status=response.status, resolved_url=response.url)
                if response.headers.get('Content-Length'):
                    assert int(response.headers['Content-Length']) == published['size']
                count = 0
                with temporary.open('wb') as stream:
                    while True:
                        raw = response.read(1024 * 1024)
                        if not raw:
                            break
                        count += len(raw); assert count <= published['size']
                        stream.write(raw)
                        if count % (32 * 1024 * 1024) == 0:
                            record['received_bytes'] = count
                            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
                            print(json.dumps({'prefix': prefix, 'received_bytes': count, 'expected_bytes': published['size']}), flush=True)
            assert count == published['size'] and sha(temporary) == published['lfs']['sha256']
            temporary.rename(path)
            record.update(status='complete_whole_published_LFS_SHA256_verified', bytes=count, sha256=sha(path),
                          whole_payload_LFS_SHA256_verified=True, completed_utc=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            record.update(status='failed_terminal', error_type=type(error).__name__, completed_utc=datetime.now(timezone.utc).isoformat())
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            raise
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(record, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
