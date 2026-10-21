"""Bounded anonymous GETs on a documented free history route.

Shared field names are a provenance hypothesis, never a provider attribution.
The public documentation describes futures; test stock applicability once,
and stop stock requests immediately on any access-denial response.
"""
import csv
import hashlib
import io
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/documented_edb_history_20261009'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def get(url, filename, limit):
    path = OUT / filename
    assert not path.exists()
    request = urllib.request.Request(url, headers={'User-Agent': 'QuantResearch-public-data-audit/1.0'})
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(limit + 1)
        assert len(data) <= limit, 'Do not retain unbounded responses'
        path.write_bytes(data)
        return {'url': url, 'http_status': response.code, 'content_type': response.headers.get('Content-Type'),
                'path': str(path), 'sha256': sha(path), 'bytes': len(data), 'elapsed_seconds': round(time.monotonic() - start, 3)}


def main():
    OUT.mkdir(exist_ok=True)
    output = OUT / 'anonymous_documented_history_probe.json'
    assert not output.exists()
    documents = []
    for name, url in [('edb_history_documentation.html', 'https://doc.shinnytech.com/edb/latest/md_server.html'),
                      ('sdk_kline_documentation.html', 'https://doc.shinnytech.com/tqsdk/latest/reference/tqsdk.api.html'),
                      ('sdk_professional_documentation.html', 'https://doc.shinnytech.com/tqsdk/latest/profession.html')]:
        documents.append(get(url, name, 1024 * 1024))
    assert all(r['http_status'] == 200 for r in documents)
    documented = Path(documents[0]['path']).read_text()
    assert 'https://edb.shinnytech.com/md' in documented and 'datetime_nano' in documented
    cases = [('documented_futures_transport_control', 'SHFE.cu2607', '2026-06-23', '09:00:00', '09:10:00'),
             ('original_SH_stock_control_applicability', 'SSE.600004', '2026-09-23', '09:30:00', '15:00:00'),
             ('actual_missing_SZ_stockday_applicability', 'SZSE.000006', '2026-09-23', '09:30:00', '15:00:00')]
    records = []
    stock_denied = False
    for name, symbol, date, start, end in cases:
        if stock_denied and symbol.startswith(('SSE.', 'SZSE.')):
            records.append({'case': name, 'symbol': symbol, 'status': 'not_requested_after_stock_access_denial'})
            continue
        params = {'period': 60, 'symbol': symbol, 'start_time': date + ' ' + start, 'end_time': date + ' ' + end}
        url = 'https://edb.shinnytech.com/md/kline?' + urllib.parse.urlencode(params)
        try:
            response = get(url, name + '.response.txt', 2 * 1024 * 1024)
            body = Path(response['path']).read_text(errors='replace')
            first_line = body.splitlines()[0] if body.splitlines() else ''
            access_denied = response['http_status'] in [401, 403] or first_line.startswith(('401,', '403,'))
            has_rows = body.startswith('datetime_nano,')
            rows = list(csv.DictReader(io.StringIO(body))) if has_rows else []
            response.update(case=name, symbol=symbol, date=date, status='actual_csv_rows_received' if rows else 'no_usable_native_stock_or_control_rows',
                            actual_csv_rows=len(rows), access_denied=access_denied,
                            first_response_line=first_line[:300], returned_fields=list(rows[0]) if rows else [])
            records.append(response)
            if symbol.startswith(('SSE.', 'SZSE.')) and access_denied:
                stock_denied = True
        except (TimeoutError, urllib.error.URLError) as error:
            records.append({'case': name, 'symbol': symbol, 'status': 'transport_failure', 'error_type': type(error).__name__})
        print(json.dumps({k: v for k, v in records[-1].items() if k not in ['url', 'path']}, ensure_ascii=False), flush=True)
    local_original = BASE / 'public_source_probes/additional_native_inventory_20261009/whole_event_archive/all_original_day_controls/600004_20260923.parquet'
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__), 'documents': documents, 'records': records,
              'official_documentation_scope_is_futures_not_proven_stock_history': True,
              'original_minute_common_field_names_support_only_format_hypothesis': ['datetime_nano', 'open', 'high', 'low', 'close', 'volume', 'open_oi', 'close_oi'],
              'original_local_control_sha256': sha(local_original), 'original_provider_identity_not_established': True,
              'personal_credentials_cookies_tokens_used': False, 'TLS_and_hostname_verification_enabled': True,
              'stock_requests_stopped_on_access_denial': stock_denied,
              'external_code_downloaded_or_executed': False, 'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ['documents', 'records']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
