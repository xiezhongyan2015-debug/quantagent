"""Pin three primary listing references before using observed IPO factor dates."""
import hashlib
import html
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/sina_ipo_factor_prefix_20261009/listing_sources'
REFERENCES = [
    ('688220.SH', '2022-01-14', 'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220113_84075081.shtml', 'html', '2022年1月14日'),
    ('688234.SH', '2022-01-12', 'https://static.cninfo.com.cn/finalpage/2022-01-11/1212136661.PDF', 'pdf', '2022年1月12日'),
    ('688331.SH', '2022-03-31', 'https://www.remegen.com/index.php?cid=115&id=166&v=show', 'html', 'OnMarch31st2022'),
]


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style']: self.ignore += 1

    def handle_endtag(self, tag):
        if tag in ['script', 'style']: self.ignore -= 1

    def handle_data(self, data):
        if not self.ignore: self.parts.append(data)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for symbol, date, url, kind, proof in REFERENCES:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=40) as response:
            data = response.read(10*1024*1024+1)
            assert len(data) <= 10*1024*1024
            assert response.status == 200
            assert response.url.startswith('https://')
            content_type = response.headers.get('Content-Type')
        path = OUT / (symbol + '.' + kind)
        path.write_bytes(data)
        textpath = OUT / (symbol + '.txt')
        if kind == 'pdf':
            assert data.startswith(b'%PDF')
            document = PdfReader(path)
            textpath.write_text('\n'.join(page.extract_text() or '' for page in document.pages[:10]))
        else:
            parser = Text(); parser.feed(data.decode('utf-8'))
            textpath.write_text('\n'.join(parser.parts))
        cleaned = re.sub(r'\s+', '', html.unescape(textpath.read_text()))
        assert symbol[:6] in cleaned and proof in cleaned, symbol
        records.append({'symbol': symbol, 'listing_date': date, 'primary_url': url,
                        'source_path': str(path), 'source_sha256': sha(path), 'source_bytes': len(data),
                        'content_type': content_type, 'extracted_text_path': str(textpath),
                        'extracted_text_sha256': sha(textpath), 'code_and_listing_date_present': True,
                        'credentials_used': False})
        print(symbol, 'primary listing reference verified', len(data), flush=True)
        time.sleep(.7)
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(Path(__file__)), 'records': records,
               'source_documents_treated_as_data_not_instructions': True}
    (OUT / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
