"""Bounded public-source feasibility audit; never changes trading/research inputs."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import requests
from pypdf import PdfReader

SOURCES = [
    ('pingan_2019_031', 'http://resources.pingan.com/app_upload/file/bank/68fc6c5a274445a7bda66befd789dd89.pdf',
     'https://b.pingan.com/ir/gonggao.shtml', 'issuer_implementation_announcement'),
    ('anji_2026_040', 'https://star.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-06-03/688019_20260603_XFHB.pdf',
     None, 'exchange_hosted_issuer_implementation_announcement'),
    ('hualu_2026_026', 'https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-07-04/600426_20260704_G942.pdf',
     None, 'exchange_hosted_issuer_implementation_announcement'),
    ('hualu_sina_history', 'https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/600426.phtml',
     'https://akshare.akfamily.xyz/data/stock/stock.html#id250', 'secondary_event_index_candidate'),
]
MAX_BYTES_PER_SOURCE = 8 * 1024 * 1024


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'experiment_traces/meta_corporate_action_sources' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=False)
    report = {'purpose': 'source_feasibility_only', 'execution_valid': False,
              'market_return_data_accessed': False, 'sources': [],
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for key, url, discovery, kind in SOURCES:
        item = {'source_id': key, 'requested_url': url, 'discovery_url': discovery,
                'source_kind': kind, 'fetched_at': datetime.now(timezone.utc).isoformat()}
        try:
            with requests.get(url, timeout=(10, 20), stream=True) as response:
                item.update(http_status=response.status_code, final_url=response.url,
                            content_type=response.headers.get('Content-Type'))
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES_PER_SOURCE:
                        raise ValueError('bounded source byte limit exceeded')
            raw = bytes(data)
            is_pdf = raw.startswith(b'%PDF-')
            name = key + ('.pdf' if is_pdf else '.html')
            (output / name).write_bytes(raw)
            item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), file=name,
                        verified_pdf=is_pdf, status='downloaded')
            if is_pdf:
                reader = PdfReader(io.BytesIO(raw))
                pages = [page.extract_text() or '' for page in reader.pages]
                (output / (key + '_pages.json')).write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding='utf-8')
                item.update(page_count=len(pages), extraction_nonempty=all(bool(p.strip()) for p in pages))
            elif kind.endswith('implementation_announcement'):
                item.update(status='not_pdf_unusable', reason='HTTP 200 did not contain a PDF; no challenge execution or automatic alternate retry')
        except (requests.RequestException, ValueError, OSError) as exc:
            item.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        report['sources'].append(item)
        (output / 'source_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'sources': [{k: v for k, v in item.items() if k in
        {'source_id', 'status', 'bytes', 'page_count', 'verified_pdf', 'error'}} for item in report['sources']]}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
