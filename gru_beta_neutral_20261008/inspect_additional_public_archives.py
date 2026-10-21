"""Bounded anonymous metadata reads for newly identified archive candidates."""
import hashlib
import json
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_public_archives_20261009'
REPOS = ['wind17/china-a-share-zip', 'alphat04/Tick-by-Tick-Orders-China']


def fetch(url, path, limit):
    assert not path.exists()
    request = urllib.request.Request(url, headers={'User-Agent': 'PublicHistoricalArchiveMetadataReview/1.0', 'Accept': 'application/json,text/plain'})
    with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
        raw = response.read(limit + 1)
        assert len(raw) <= limit
        info = {'url': url, 'resolved_url': response.url, 'http_status': response.status,
                'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'fetched_utc': datetime.now(timezone.utc).isoformat()}
    path.write_bytes(raw)
    return raw, info


def main():
    OUT.mkdir(exist_ok=True)
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'downloads_are_metadata_or_documentation_only': True, 'results': [],
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    assert not (OUT / 'inspection_receipt.json').exists()
    for repo in REPOS:
        directory = OUT / repo.split('/')[0]
        directory.mkdir(exist_ok=True)
        result = {'repository': repo, 'status': 'pending', 'requests': []}
        try:
            raw, info = fetch('https://huggingface.co/api/datasets/' + repo + '?blobs=true', directory / 'dataset_info_blobs.json', 20 * 1024 * 1024)
            result['requests'].append(info)
            metadata = json.loads(raw)
            result.update(status='metadata_acquired', revision=metadata['sha'], gated=metadata.get('gated'),
                          private=metadata.get('private'), files=metadata.get('siblings', []))
            if any(f['rfilename'] == 'README.md' for f in result['files']):
                _, info = fetch('https://huggingface.co/datasets/' + repo + '/resolve/' + metadata['sha'] + '/README.md', directory / 'README.md', 256 * 1024)
                result['requests'].append(info)
        except Exception as error:
            result.update(status='metadata_request_failed', error_type=type(error).__name__, error=str(error))
        receipt['results'].append(result)
        print(json.dumps({'repository': repo, 'status': result['status'], 'revision': result.get('revision'), 'gated': result.get('gated'),
                          'file_count': len(result.get('files', [])), 'file_names_first_20': [f['rfilename'] for f in result.get('files', [])][:20]}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / 'inspection_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
