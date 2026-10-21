"""Bounded inventories of two newly discovered native-data repositories."""
import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/additional_native_inventory_20261009'
TARGETS = [('suncong/ashare_1min', 'c05c0da3940e167d689392852e9d490107a2c3e1'),
           ('suncong/Ashare-events', '301397490bea1a168083298da77f750a083a4ef1')]
CAP = 4 * 1024 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url, path):
    assert not path.exists()
    request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
    with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
        assert response.status == 200 and response.url.startswith('https://')
        raw = response.read(CAP + 1); assert len(raw) <= CAP
    path.write_bytes(raw)
    return {'url': url, 'path': str(path), 'bytes': len(raw), 'sha256': sha(path),
            'http_status': 200, 'TLS_and_hostname_verification_enabled': True}, raw


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'bounded_metadata_receipt.json'; assert not output.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'personal_credentials_used': False, 'quote_payloads_acquired': 0,
               'external_code_downloaded_or_executed': False, 'results': [], 'candidate_changed': False,
               'candidate_hours_admitted': 0}
    for repo, revision in TARGETS:
        label = repo.replace('/', '__'); record = {'repository': repo, 'pinned_revision': revision}
        try:
            request, raw = fetch('https://huggingface.co/api/datasets/' + repo + '/revision/' + revision + '?blobs=true', OUT / (label + '_info.json'))
            info = json.loads(raw); assert info['sha'] == revision
            record.update(gated=info['gated'], private=info['private'], metadata_request=request,
                          total_published_files=len(info['siblings']))
            assert info['gated'] is False and info['private'] is False
            siblings = {r['rfilename']: r for r in info['siblings']}
            if 'README.md' in siblings:
                request, raw = fetch('https://huggingface.co/datasets/' + repo + '/resolve/' + revision + '/README.md', OUT / (label + '_README.md'))
                assert len(raw) == siblings['README.md']['size']
                assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == siblings['README.md']['blobId']
                record['readme_request'] = request
            record.update(status='pinned_inventory_complete', published_data_files=[r for r in info['siblings'] if r['rfilename'] != 'README.md' and not r['rfilename'].endswith(('.py', '.sh'))])
        except Exception as error:
            record.update(status='failed_terminal', error_type=type(error).__name__)
        receipt['results'].append(record)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in record.items() if k not in ('published_data_files', 'metadata_request', 'readme_request')}, ensure_ascii=False), flush=True)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
