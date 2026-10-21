"""Bounded public references for newly acquired real trade samples."""
import fcntl
import hashlib
import io
import json
import ssl
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from public_baostock_probe import PublicClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/phields_trades_20261009/sample_references'
RULES = {
    'sse_2026_rules.docx': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/10816482/files/959da0158c65434daa8a43a6e32be7ba.docx',
    'sse_2026_deferred_rules.docx': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/10816482/files/f6bd756cdd8248f49b7795f62e73237f.docx',
    'depth_info.json': 'https://huggingface.co/api/datasets/phields/a-share-l2-market-depth?blobs=true',
}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    path = OUT / 'bounded_reference_acquisition.json'
    assert not path.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'personal_credentials_used': False, 'requests': [], 'coarse_queries': [],
               'coarse_bars_expanded_to_minutes': False, 'candidate_changed': False,
               'candidate_hours_admitted': 0}
    for name, url in RULES.items():
        record = {'name': name, 'url': url}
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
            with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
                assert response.status == 200 and response.url.startswith('https://')
                raw = response.read(4 * 1024 * 1024 + 1)
                assert len(raw) <= 4 * 1024 * 1024
                record.update(http_status=response.status, TLS_and_hostname_verification_enabled=True)
            dest = OUT / name; dest.write_bytes(raw)
            record.update(path=str(dest), sha256=sha(dest), bytes=len(raw), status='retained_complete_HTTP_response')
            if name.endswith('.docx'):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    root = ElementTree.fromstring(archive.read('word/document.xml'))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                paragraphs = [''.join(p.itertext()) for p in root.findall('.//w:p', ns)]
                text_path = dest.with_suffix('.txt'); text_path.write_text('\n'.join(paragraphs))
                record.update(extracted_text_path=str(text_path), extracted_text_sha256=sha(text_path))
            else:
                info = json.loads(raw)
                record.update(pinned_revision=info['sha'], gated=info['gated'], private=info['private'])
        except Exception as error:
            record.update(status='failed_terminal', error_type=type(error).__name__)
        receipt['requests'].append(record)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(record, ensure_ascii=False), flush=True)
    client = None
    lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = PublicClient(OUT)
        client.login()
        for code in ['sz.000006', 'sz.000099']:
            record = client.query(code, '2026-09-23', '2026-09-23', '5')
            receipt['coarse_queries'].append({k: v for k, v in record.items() if k != 'data'})
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps(receipt['coarse_queries'][-1], ensure_ascii=False), flush=True)
        receipt['coarse_probe_status'] = 'completed'
    except Exception as error:
        receipt.update(coarse_probe_status='failed_terminal', coarse_error_type=type(error).__name__)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error_type'] = type(error).__name__
            receipt['all_protocol_records'] = client.records
        lock.close()
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({'completed': True, 'coarse_probe_status': receipt['coarse_probe_status']}), flush=True)


if __name__ == '__main__':
    main()
