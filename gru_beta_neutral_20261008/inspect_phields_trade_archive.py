"""Pin a newly discovered public trade archive before downloading quote payloads."""
import hashlib
import json
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/phields_trades_20261009'
REPO = 'phields/a-share-l2-trades'
REVISION = '3acef8b18f56786e253ccd034072a8f1845b507e'
MAX_BYTES = 4 * 1024 * 1024


def fetch(url, name):
    path = OUT / name
    if path.exists():
        raw = path.read_bytes()
        assert len(raw) <= MAX_BYTES
        return {'url': url, 'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'status': 'reuse_retained_bytes_after_completed_request_and_metadata_parse_error',
                'http_status': None, 'HTTP_headers_not_retained_for_initial_request': True}, raw
    request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
    with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
        assert response.status == 200 and response.url.startswith('https://')
        raw = response.read(MAX_BYTES + 1)
        assert len(raw) <= MAX_BYTES
        result = {'url': url, 'resolved_url': response.url, 'http_status': response.status}
    path.write_bytes(raw)
    result.update(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), fetched_utc=datetime.now(timezone.utc).isoformat())
    return result, raw


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'pinned_metadata_and_target_inventory.json'
    assert not output.exists()
    requests = []
    receipt, raw = fetch('https://huggingface.co/api/datasets/' + REPO + '/revision/' + REVISION + '?blobs=true', 'dataset_info_blobs.json')
    requests.append(receipt); info = json.loads(raw)
    assert info['sha'] == REVISION and info['gated'] is False and info['private'] is False
    receipt, readme = fetch('https://huggingface.co/datasets/' + REPO + '/resolve/' + REVISION + '/README.md', 'README.md')
    requests.append(receipt)
    parquet = [r for r in info['siblings'] if r['rfilename'].endswith('.parquet') and r['rfilename'].startswith('data/l2_trades/trade_date=')]
    dates = sorted({r['rfilename'].split('trade_date=')[1].split('/')[0] for r in parquet})
    tail = [r for r in parquet if '2026-08-07' <= r['rfilename'].split('trade_date=')[1].split('/')[0] <= '2026-09-23']
    controls = [r for r in parquet if 'trade_date=2026-09-23/' in r['rfilename']]
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'repository': REPO, 'pinned_revision': REVISION, 'gated': False, 'private': False,
              'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
              'requests': requests, 'parquet_files': len(parquet), 'published_partition_dates': dates,
              'published_first_partition_date': dates[0], 'published_last_partition_date': dates[-1],
              'tail_partition_files': len(tail), 'tail_published_partition_dates': sorted({r['rfilename'].split('trade_date=')[1].split('/')[0] for r in tail}),
              'tail_partition_bytes': sum(r['size'] for r in tail), '20260923_partition_files': controls,
              'quote_payloads_acquired': 0, 'payload_date_coverage_verified': False,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('published_partition_dates', 'tail_published_partition_dates', 'requests')}, ensure_ascii=False), flush=True)
    print(readme.decode('utf-8'), flush=True)


if __name__ == '__main__':
    main()
