"""Bounded public SZSE pagination, derived from an observed Chrome request.

Retain raw bytes and receipts; no credentials or TLS changes. Sources are
retrospective stop/resume evidence, not executable prices or PIT signals.
"""
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/official_szse_suspension_chrome_20261009'
INTERVALS = [('2019-10-01', '2019-12-31'), ('2020-01-01', '2020-12-31'),
             ('2021-01-01', '2021-12-31'), ('2022-01-01', '2022-12-31')]


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def parse(raw, page, start, end):
    obj = json.loads(raw)
    assert isinstance(obj, list) and len(obj) == 1
    section = obj[0]
    md = section['metadata']
    assert not section.get('error')
    assert md['catalogid'] == '1798' and md['tabkey'] == 'tab1' and md['name'] == '停复牌提示'
    assert md['pagesize'] == 10 and md['pageno'] == page
    assert md['pagecount'] == math.ceil(md['recordcount'] / 10)
    assert 0 < md['pagecount'] <= 500
    conditions = {r['name']: r['defaultValue'] for r in md['conditions']}
    assert conditions['txtKsrq'] == start and conditions['txtZzrq'] == end
    assert conditions['txtDmorjc'] == ''
    rows = section['data']
    assert len(rows) == min(10, md['recordcount'] - (page - 1) * 10)
    for row in rows:
        assert re.fullmatch(r'\d{6}', row['zqdm'])
        dated = []
        for field in ['tpkssj', 'fpkssj']:
            if row[field]:
                match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (开市|\d{2}:\d{2}:\d{2})', row[field])
                assert match, row
                dated.append(match[1])
        # A combined stop/resume row can straddle a query boundary.
        assert any(start <= d <= end for d in dated), row
    return md, rows


def acquire(start, end, basis, url, params):
    folder = OUT / ('history_' + start.replace('-', '') + '_' + end.replace('-', ''))
    folder.mkdir(exist_ok=True)
    records, events = [], []
    pages = total = None
    begun = time.monotonic()
    try:
        for page in range(1, 501):
            if pages is not None and page > pages:
                break
            query = {**params, 'PAGENO': str(page), 'txtKsrq': start, 'txtZzrq': end}
            query_url = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, urllib.parse.urlencode(query), ''))
            rawpath = folder / ('page_%04d.raw.json' % page)
            receiptpath = folder / ('page_%04d.receipt.json' % page)
            if rawpath.exists() and receiptpath.exists():
                record = json.loads(receiptpath.read_text())
                assert record['URL'] == query_url and sha(rawpath) == record['raw_sha256']
                raw = rawpath.read_bytes()
            else:
                assert not rawpath.exists() and not receiptpath.exists(), 'Unpaired raw/receipt retained for inspection'
                time.sleep(1)
                request = urllib.request.Request(query_url, headers={'Referer': 'https://www.szse.cn/disclosure/memo/index.html', 'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=20) as response:
                    assert response.status == 200
                    target = urllib.parse.urlsplit(response.url)
                    assert target.scheme == 'https' and target.netloc == url.netloc
                    raw = response.read(2 * 1024 * 1024 + 1)
                assert len(raw) <= 2 * 1024 * 1024
                rawpath.write_bytes(raw)
                record = {'checked_utc': stamp(), 'page': page, 'URL': query_url,
                          'raw_path': str(rawpath), 'raw_sha256': sha(rawpath), 'bytes': len(raw)}
                receiptpath.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            md, rows = parse(raw, page, start, end)
            if pages is None:
                pages, total = md['pagecount'], md['recordcount']
            assert pages == md['pagecount'] and total == md['recordcount']
            records.append(record)
            events.extend(rows)
            print(json.dumps({'interval': [start, end], 'pages_saved': page, 'pages_total': pages,
                              'events_saved': len(events), 'elapsed_seconds': round(time.monotonic() - begun, 1)}), flush=True)
        assert len(records) == pages and len(events) == total
        path = folder / 'observed_official_events.json'
        path.write_text(json.dumps(events, ensure_ascii=False, indent=2))
        receipt = {'checked_utc': stamp(), 'status': 'all_requested_pages_saved', 'date_interval': [start, end],
                   'page_size': 10, 'pages_saved': pages, 'reported_total_events': total, 'events_saved': len(events),
                   'pages': records, 'events_path': str(path), 'events_sha256': sha(path),
                   'Chrome_observed_endpoint_basis_sha256': sha(basis), 'source_code_sha256': sha(Path(__file__)),
                   'personal_credentials_used': False, 'TLS_validation_preserved': True,
                   'prices_or_minutes_created': False, 'panel_changed': False, 'production_ready': False}
        (folder / 'complete_source_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    except Exception as exc:
        attempt = {'checked_utc': stamp(), 'pages_saved_and_validated': len(records), 'pages_total': pages,
                   'events_saved_and_validated': len(events), 'error_type': type(exc).__name__, 'error': str(exc)[:300],
                   'source_code_sha256': sha(Path(__file__)), 'panel_changed': False, 'completion_claimed': False}
        name = 'incomplete_attempt_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
        (folder / name).write_text(json.dumps(attempt, ensure_ascii=False, indent=2))
        raise


def main():
    basis = OUT / 'observed_browser_query_page2_receipt.json'
    receipt = json.loads(basis.read_text())
    assert receipt['TLS_validation_preserved'] and not receipt['personal_credentials_used']
    assert sha(receipt['raw_path']) == receipt['raw_sha256']
    parse(Path(receipt['raw_path']).read_bytes(), 2, '2021-01-01', '2021-12-31')
    url = urllib.parse.urlsplit(receipt['source_URL_observed_in_Chrome'])
    assert (url.scheme, url.netloc, url.path) == ('https', 'www.szse.cn', '/api/report/ShowReport/data')
    params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
    assert params['CATALOGID'] == '1798' and params['TABKEY'] == 'tab1' and params['PAGENO'] == '2'
    assert 'txtDmorjc' not in params
    for start, end in INTERVALS:
        acquire(start, end, basis, url, params)


if __name__ == '__main__':
    main()
