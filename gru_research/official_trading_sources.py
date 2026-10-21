"""Bounded TLS snapshots of public research evidence; no account or installation.

Saved bytes and request dates establish retrieval evidence, not historical PIT
arrival. Tencent is a vendor probe only and never a certified minute substitute.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import urllib.request

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'trading_data/official_sources'
SOURCES = {
    'chinaclear_faq_csrc_repost': 'https://www.csrc.gov.cn/shenzhen/c105614/c1575308/content.shtml',
    'sse_st_rule_2026': 'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml',
    'szse_st_rule_2026': 'https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html',
    'szse_2026_full_rules': 'https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf',
    '301308_2026_half_year_official': 'https://disc.static.szse.cn/disc/disk03/finalpage/2026-08-11/35b248b6-6b97-4fe1-92f5-15a610d4e19c.PDF',
    'tencent_m1_600004_probe': 'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600004,m1,,320',
}

def snapshot(name, url):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt_path = OUTPUT / (name + '.json')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        path = OUTPUT / receipt.get('file', '')
        if receipt.get('status') == 'SUCCESS' and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == receipt['sha256']:
            return receipt
    receipt = {'name': name, 'url': url, 'fetched_utc': datetime.now(timezone.utc).isoformat(),
        'historical_arrival_verified': False, 'tls_certificate_verification': True}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/' if name.startswith('tencent') else url})
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as response:
            data = response.read(16_000_001)
            if len(data) > 16_000_000:
                raise ValueError('public evidence exceeds bounded byte budget')
            receipt.update({'status': 'SUCCESS', 'http_status': response.status, 'final_url': response.url,
                'content_type': response.headers.get('Content-Type'), 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'file': name + '.bin'})
        (OUTPUT / receipt['file']).write_bytes(data)
    except Exception as exc:
        receipt.update({'status': 'FAILED', 'error': type(exc).__name__ + ': ' + str(exc)})
    receipt_data = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
    archive = OUTPUT / 'receipts'; archive.mkdir(exist_ok=True)
    (archive / (name + '_' + hashlib.sha256(receipt_data).hexdigest()[:16] + '.json')).write_bytes(receipt_data)
    receipt_path.write_bytes(receipt_data)
    return receipt

if __name__ == '__main__':
    for name, url in SOURCES.items():
        print(json.dumps(snapshot(name, url), ensure_ascii=False), flush=True)
