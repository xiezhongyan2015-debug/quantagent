"""Retrieve public official suspension events using Chrome-observed API fields.

No browser cookie, login, credential, executable download, or TLS override.
Keep the observed page size and paginate a fixed historical date interval.
"""
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/official_sse_suspension_chrome_20261009'
assert len(sys.argv) == 2 and sys.argv[1] in ['2019', '2020', '2021', '2022']
YEAR = sys.argv[1]
START = YEAR + ('1001' if YEAR == '2019' else '0101')
END = YEAR + '1231'
PAGES = OUT / ('history_' + START + '_' + END)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def parse(raw, callback):
    text = raw.decode('utf-8')
    prefix = callback + '('
    assert text.startswith(prefix)
    obj, offset = json.JSONDecoder().raw_decode(text[len(prefix):])
    assert text[len(prefix) + offset:].strip() in [')', ');']
    assert not obj['actionErrors'] and not obj['fieldErrors']
    assert obj['result'] == obj['pageHelp']['data']
    return obj


def main():
    PAGES.mkdir(exist_ok=True)
    observed = OUT / 'observed_browser_query_page2_receipt.json'
    basis = json.loads(observed.read_text())
    assert basis['TLS_validation_preserved'] and not basis['personal_credentials_used']
    assert sha(ROOT / basis['raw_path']) == basis['raw_sha256']
    url = urllib.parse.urlsplit(basis['source_url_observed_in_Chrome'])
    assert url.scheme == 'https' and url.netloc == 'query.sse.com.cn' and url.path == '/commonSoaQuery.do'
    params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
    assert params['sqlId'] == 'GW_PL_JYTS_TFPXX' and params['pageHelp.pageSize'] == '25'
    params.update({'startStopDate': START, 'endStopDate': END, 'productCode': '', 'keyWords': ''})
    records, events = [], []
    total = pages = None
    start = time.monotonic()
    try:
        for page in range(1, 201):
            if pages is not None and page > pages:
                break
            params.update({name: str(page) for name in ['pageHelp.pageNo', 'pageHelp.beginPage', 'pageHelp.endPage']})
            query_url = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, urllib.parse.urlencode(params), ''))
            rawpath = PAGES / ('page_%04d.raw.txt' % page)
            receiptpath = PAGES / ('page_%04d.receipt.json' % page)
            if rawpath.exists() and receiptpath.exists():
                record = json.loads(receiptpath.read_text())
                assert record['URL'] == query_url and sha(rawpath) == record['raw_sha256']
                raw = rawpath.read_bytes()
            else:
                time.sleep(1)
                request = urllib.request.Request(query_url, headers={'Referer': 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/stock/', 'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=20) as response:
                    assert response.status == 200
                    assert urllib.parse.urlsplit(response.url).scheme == 'https'
                    assert urllib.parse.urlsplit(response.url).netloc == url.netloc
                    raw = response.read(2 * 1024 * 1024 + 1)
                assert len(raw) <= 2 * 1024 * 1024
                rawpath.write_bytes(raw)
                record = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'page': page, 'URL': query_url,
                          'raw_path': str(rawpath), 'raw_sha256': sha(rawpath), 'bytes': len(raw)}
                receiptpath.write_text(json.dumps(record, indent=2))
            obj = parse(raw, params['jsonCallBack'])
            help = obj['pageHelp']
            assert help['pageNo'] == page and help['pageSize'] == 25
            if total is None:
                total, pages = help['total'], help['pageCount']
                assert 0 < pages <= 200 and pages == math.ceil(total / 25)
            assert total == help['total'] and pages == help['pageCount']
            rows = obj['result']
            assert len(rows) == (25 if page < pages else total - 25 * (pages - 1))
            for row in rows:
                assert len(row['productCode']) == 6 and row['productCode'].isdigit()
                # Official queries also include older intervals still open
                # during the selected range; retain them without truncation.
                assert len(row['startStopDate']) == 8 and row['startStopDate'].isdigit()
                assert row['startStopDate'] <= END
                ended = row['endStopDate']
                assert ended in ['', None] or (len(ended) == 8 and ended.isdigit() and ended >= START)
                assert row['type'] in ['LSTP', 'LXTP']
            events.extend(rows)
            records.append(record)
            print(json.dumps({'pages_saved': page, 'pages_total': pages, 'events_saved': len(events),
                              'events_total': total, 'elapsed_seconds': round(time.monotonic() - start, 2)}), flush=True)
        assert len(events) == total and len(records) == pages
        resultpath = PAGES / 'observed_official_events.json'
        resultpath.write_text(json.dumps(events, ensure_ascii=False, indent=2))
        receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'status': 'all_requested_pages_saved',
                   'date_interval': [START, END], 'page_size': 25, 'pages_saved': pages,
                   'reported_total_events': total, 'events_saved': len(events), 'pages': records,
                   'events_path': str(resultpath), 'events_sha256': sha(resultpath),
                   'Chrome_observed_endpoint_basis_sha256': sha(observed), 'source_code_sha256': sha(Path(__file__)),
                   'personal_credentials_used': False, 'TLS_validation_preserved': True,
                   'prices_or_minutes_created': False, 'panel_changed': False, 'production_ready': False}
        (PAGES / 'complete_source_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    except Exception as exc:
        (PAGES / 'incomplete_attempt_receipt.json').write_text(json.dumps({
            'checked_utc': datetime.now(timezone.utc).isoformat(), 'pages_saved': len(records),
            'pages_total': pages, 'events_saved': len(events), 'error_type': type(exc).__name__,
            'error': str(exc)[:200], 'source_code_sha256': sha(Path(__file__)),
            'panel_changed': False, 'completion_claimed': False}, indent=2))
        raise


if __name__ == '__main__':
    main()
