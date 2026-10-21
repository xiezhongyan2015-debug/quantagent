"""Retain anonymous issuer listing/action documents and provider action tables."""
import hashlib
import html
import json
import re
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/ipo_factor_rounding_method_20261009'
REFERENCES = [
    ('688582.SH', 'IPO', '2023-06-30', 'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20230629_90144324.shtml', 'html'),
    ('688709.SH', 'IPO', '2024-02-07', 'https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-02-06/688709_20240206_JBMI.pdf', 'pdf'),
    ('688709.SH', 'first_dividend', '2024-06-18', 'https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-06-12/688709_20240612_DNF8.pdf', 'pdf'),
    ('688582.SH', 'provider_action_table', None, 'https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/688582.phtml', 'html'),
    ('688709.SH', 'provider_action_table', None, 'https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/688709.phtml', 'html'),
]


class TextAndRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.rows, self.current_row, self.cell = [], [], None, None
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style']:
            self.ignore += 1
        if tag == 'tr':
            self.current_row = []
        if tag in ['td', 'th'] and self.current_row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ['script', 'style']:
            self.ignore -= 1
        if tag in ['td', 'th'] and self.cell is not None:
            self.current_row.append(re.sub(r'\s+', '', ''.join(self.cell)))
            self.cell = None
        if tag == 'tr' and self.current_row is not None:
            self.rows.append(self.current_row)
            self.current_row = None

    def handle_data(self, data):
        if not self.ignore:
            self.parts.append(data)
            if self.cell is not None:
                self.cell.append(data)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'source_acquisition_receipt.json'
    assert not output.exists()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'records': [], 'authentication_used': False, 'TLS_and_hostname_verification_enabled': True,
               'downloaded_documents_are_data_not_instructions': True, 'candidate_changed': False}
    for symbol, kind, date, url, suffix in REFERENCES:
        record = {'symbol': symbol, 'kind': kind, 'expected_date': date, 'url': url}
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=25, context=ssl.create_default_context()) as response:
                assert response.status == 200 and response.url.startswith('https://')
                raw = response.read(10 * 1024**2 + 1)
                assert len(raw) <= 10 * 1024**2
                record.update(http_status=response.status, content_type=response.headers.get('Content-Type'))
            path = OUT / (symbol + '_' + kind + '.' + suffix)
            assert not path.exists()
            path.write_bytes(raw)
            parser = None
            if suffix == 'pdf':
                assert raw.startswith(b'%PDF')
                document = PdfReader(path)
                pages = document.pages[:10] if kind == 'IPO' else document.pages
                text = '\n'.join(p.extract_text() or '' for p in pages)
            else:
                try:
                    decoded = raw.decode('utf-8')
                except UnicodeDecodeError:
                    decoded = raw.decode('gb18030')
                parser = TextAndRows()
                parser.feed(decoded)
                text = '\n'.join(parser.parts)
            tp = path.with_suffix('.txt')
            tp.write_text(text)
            cleaned = re.sub(r'\s+', '', html.unescape(text))
            assert symbol[:6] in cleaned
            if kind == 'IPO':
                year, month, day = map(int, date.split('-'))
                assert f'{year}年{month}月{day}日' in cleaned
            if kind == 'first_dividend':
                assert '2024/6/18' in cleaned or '2024年6月18日' in cleaned
                assert '0.112' in cleaned
            record.update(status='source_bytes_and_code_date_verified', path=str(path), sha256=sha(path),
                          source_bytes=len(raw), extracted_text_path=str(tp), extracted_text_sha256=sha(tp))
            if kind == 'provider_action_table':
                events = []
                for r in parser.rows:
                    if len(r) >= 8 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', r[0]) and r[4] == '实施':
                        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', r[5])
                        events.append({'announcement_date': r[0], 'bonus_shares_per_10': r[1],
                                       'capital_transfer_per_10': r[2], 'cash_before_tax_per_10': r[3],
                                       'ex_date': r[5], 'record_date': r[6], 'source_cells': r})
                assert events
                record['observed_implemented_actions'] = sorted(events, key=lambda r: r['ex_date'])
        except Exception as error:
            record.update(status='source_acquisition_or_validation_failed', error_type=type(error).__name__, error=str(error))
        receipt['records'].append(record)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps({k: record.get(k) for k in ['symbol', 'kind', 'status', 'source_bytes', 'observed_implemented_actions', 'error']}, ensure_ascii=False), flush=True)
        time.sleep(.7)
    receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
