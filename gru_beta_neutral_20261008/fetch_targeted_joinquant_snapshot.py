"""Retrieve two known public minute files with pinned LFS bytes and no login."""
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/huggingface'
OUT = BASE / 'joinquant_targeted_concordance_20261009'


def main():
    OUT.mkdir(exist_ok=True)
    metadata = BASE / 'joinquant_dataset_info.json'
    info = json.loads(metadata.read_text())
    assert info['id'] == 'jobs-git/StockChina-Minute' and not info['gated'] and not info['private']
    entries = {entry['rfilename']: entry for entry in info['siblings']}
    records = []
    for name in ['README.md', '000099.XSHE.csv', '002831.XSHE.csv']:
        entry = entries[name]
        path = OUT / name
        url = 'https://huggingface.co/datasets/' + info['id'] + '/resolve/' + info['sha'] + '/' + name
        if path.exists():
            raw_bytes = path.stat().st_size
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        else:
            time.sleep(1)
            temp = OUT / (name + '.partial')
            digest_obj = hashlib.sha256()
            raw_bytes = 0
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'audited-minute-source/1.0'}), timeout=45) as response, temp.open('wb') as stream:
                assert response.status == 200 and response.url.startswith('https://')
                while chunk := response.read(1024 * 1024):
                    raw_bytes += len(chunk)
                    assert raw_bytes <= 128 * 1024 * 1024
                    digest_obj.update(chunk)
                    stream.write(chunk)
            digest = digest_obj.hexdigest()
            assert raw_bytes == entry['size']
            if 'lfs' in entry:
                assert digest == entry['lfs']['sha256']
            temp.replace(path)
        assert raw_bytes == entry['size']
        if 'lfs' in entry:
            assert digest == entry['lfs']['sha256']
        record = {'URL': url, 'path': str(path), 'bytes': raw_bytes, 'sha256': digest,
                  'expected_LFS_sha256_verified': 'lfs' in entry}
        records.append(record)
        print(json.dumps(record), flush=True)
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'all_target_files_verified',
        'repository': info['id'], 'revision': info['sha'], 'metadata_path': str(metadata),
        'metadata_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(), 'records': records,
        'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'personal_credentials_used': False, 'TLS_validation_preserved': True,
        'candidate_changed': False, 'candidate_hours_admitted': 0}
    (OUT / 'source_acquisition_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
