"""Bounded public inventories for the real tail hole and remaining history.

No account access, downloaded code execution, or inference of minute values.
"""
import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/new_public_history_and_tail_inventory_20261009'
REPOS = ['phields/a-share-l2-trades', 'phields/a-share-l2-market-depth', 'cooronxon/AShareTickData', 'Lucifer744/KiT_data_demo']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fetch(url, path, cap=4 * 1024 * 1024):
    assert not path.exists()
    req = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
    try:
        response = urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(cap + 1); assert len(data) <= cap
        path.write_bytes(data)
        return {'url': url, 'path': str(path), 'sha256': sha(path), 'bytes': len(data), 'http_status': response.status}, data


def inspect(repo):
    directory = OUT / repo.replace('/', '__'); directory.mkdir()
    result = {'repository': repo, 'status': 'pending', 'requests': []}
    try:
        request, raw = fetch('https://huggingface.co/api/datasets/' + repo + '?blobs=true', directory / 'info.json')
        result['requests'].append(request)
        if request['http_status'] != 200:
            result['status'] = 'terminal_public_metadata_not_accessible_no_bypass'
            return result
        info = json.loads(raw)
        result.update(pinned_revision=info['sha'], gated=info['gated'], private=info['private'], last_modified=info.get('lastModified'))
        if info['gated'] is not False or info['private'] is not False:
            result['status'] = 'terminal_not_anonymous_public_dataset'
            return result
        files = info['siblings']; result['all_published_files'] = files
        for f in files:
            if f['rfilename'] == 'README.md':
                request, data = fetch('https://huggingface.co/datasets/' + repo + '/resolve/' + info['sha'] + '/README.md', directory / 'README.md')
                result['requests'].append(request)
                assert request['http_status'] == 200 and len(data) == f['size']
                assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == f['blobId']
        result.update(status='complete_pinned_anonymous_metadata', published_files=len(files), total_published_bytes=sum(f.get('size', 0) for f in files))
    except Exception as error:
        result.update(status='terminal_bounded_probe_failure', error_type=type(error).__name__)
    return result


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'new_public_history_and_tail_inventory.json'; assert not output.exists()
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(inspect, REPOS))
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__), 'records': records,
              'personal_credentials_cookies_or_tokens_used': False, 'TLS_and_hostname_verification_enabled': True,
              'quote_payloads_acquired': 0, 'external_code_downloaded_or_executed': False,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({**result, 'records': [{k: v for k, v in r.items() if k not in ['all_published_files', 'requests']} for r in records]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
