"""Bounded anonymous documentation and dataset-directory discovery, no code execution."""
import concurrent.futures
import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/new_public_archive_discovery_20261009'
LIMIT = 2 * 1024 * 1024


def fetch(item):
    name, url = item
    assert url.startswith('https://')
    record = {'url': url, 'started_utc': datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
        with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
            raw = response.read(LIMIT + 1)
            assert len(raw) <= LIMIT and response.url.startswith('https://')
            record.update(http_status=response.status, resolved_url=response.url)
        path = OUT / name
        path.write_bytes(raw)
        record.update(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), status='retained_metadata_or_documentation')
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__)
        if isinstance(error, urllib.error.HTTPError):
            record['http_status'] = error.code
    record['completed_utc'] = datetime.now(timezone.utc).isoformat()
    return record


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'bounded_metadata_discovery_receipt.json'
    assert not output.exists()
    queries = ['china-a-share', 'ashare', 'a-share', 'stock-minute']
    requests = [('hf_query_%d.json' % i, 'https://huggingface.co/api/datasets?' + urllib.parse.urlencode({'search': q, 'limit': 100, 'full': 'true'})) for i, q in enumerate(queries)]
    requests += [('autoquant_intro.html', 'https://autoquant.com.cn/intro/'),
                 ('stockdb_repo.json', 'https://api.github.com/repos/hello245m/free-stockdb'),
                 ('stockdb_current_commit.json', 'https://api.github.com/repos/hello245m/free-stockdb/commits/main')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(fetch, requests))
    repositories = {}
    for query, item in zip(queries, results[:4]):
        if item['status'] != 'retained_metadata_or_documentation':
            continue
        value = json.loads(Path(item['path']).read_text())
        assert isinstance(value, list)
        for repo in value:
            key = repo['id']
            record = repositories.setdefault(key, {'id': key, 'queries': [], 'revision': repo.get('sha'),
                                                  'private': repo.get('private'), 'gated': repo.get('gated'),
                                                  'last_modified': repo.get('lastModified'),
                                                  'description': repo.get('description', '')[:2000]})
            record['queries'].append(query)
    current = results[-1]
    documentation = []
    if current['status'] == 'retained_metadata_or_documentation':
        revision = json.loads(Path(current['path']).read_text())['sha']
        for filename in ['README.md', 'sync_url.txt', 'docs/DATA_SOURCE.md']:
            documentation.append(fetch(('stockdb_' + filename.replace('/', '_'),
                                        'https://raw.githubusercontent.com/hello245m/free-stockdb/' + revision + '/' + filename)))
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'metadata_query_terms': queries, 'maximum_results_per_query': 100, 'maximum_response_bytes': LIMIT,
              'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
              'remote_software_downloaded_or_executed': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
              'requests': results + documentation, 'unique_dataset_metadata_records': len(repositories),
              'repositories': list(repositories.values()), 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('requests', 'repositories')}, ensure_ascii=False), flush=True)
    print(json.dumps([{'id': r['id'], 'gated': r['gated'], 'modified': r['last_modified']} for r in repositories.values()], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
