"""Endpoint-specific recovery for APIs that ignore limit/offset.

Business segments use non-overlapping report-date intervals; responses at or
above the documented 100-row ceiling are split until strictly below it.
Name histories are single-stock responses with a conservative size guard.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path


class RecoveryError(Exception):
    pass


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def recover(api, params, fields, request, folder, job_id):
    root = Path(folder)
    cache = root / 'recovery_v2'
    cache.mkdir(exist_ok=True)
    artifacts, leaves = {}, []

    def fetch(query, reuse_original=False):
        key = hashlib.sha256(canonical(query).encode()).hexdigest()
        path = cache / (key + '.json.gz')
        original = root / 'page_0000.json.gz'
        if reuse_original and original.exists():
            envelope = json.loads(gzip.decompress(original.read_bytes()))
            if envelope.get('job_id') != job_id or envelope.get('params') != query or envelope.get('offset') != 0:
                raise RecoveryError('Original response identity mismatch')
            path = original
        elif path.exists():
            envelope = json.loads(gzip.decompress(path.read_bytes()))
            if envelope.get('job_id') != job_id or envelope.get('params') != query:
                raise RecoveryError('Recovery cache identity mismatch')
        else:
            obj = request(query)
            envelope = {'job_id': job_id, 'api': api, 'params': query,
                        'requested_fields': fields, 'downloaded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'method': 'single_response_no_offset', 'response': obj}
            packed = gzip.compress(canonical(envelope).encode(), compresslevel=1, mtime=0)
            temp = path.with_suffix('.tmp')
            temp.write_bytes(packed)
            os.replace(temp, path)
        packed = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        artifacts[rel] = {'file': rel, 'bytes': len(packed), 'sha256': hashlib.sha256(packed).hexdigest()}
        obj = envelope['response']
        data = obj.get('data') or {}
        names, items = data.get('fields') or [], data.get('items') or []
        if obj.get('code') != 0 or set(fields) - set(names):
            raise RecoveryError('Response failed or omitted requested columns')
        if any(len(row) != len(names) for row in items):
            raise RecoveryError('Response column count mismatch')
        indices = [names.index(name) for name in fields]
        rows = [[row[i] for i in indices] for row in items]
        code_index = fields.index('ts_code')
        if any(row[code_index] != params['ts_code'] for row in rows):
            raise RecoveryError('Endpoint ignored stock filter')
        if api == 'fina_mainbz':
            date_index = fields.index('end_date')
            for row in rows:
                date = str(row[date_index])
                if not (query['start_date'] <= date <= query['end_date']):
                    raise RecoveryError('Business endpoint ignored report-date filter')
                if 'bz_code' in fields and row[fields.index('bz_code')] not in (query['type'], None, ''):
                    raise RecoveryError('Business endpoint ignored type filter')
        return rows

    def split(query, depth=0):
        rows = fetch(query, reuse_original=(depth == 0))
        if len(rows) < 100:
            leaves.append({'params': query, 'rows': len(rows)})
            return rows
        lo = dt.datetime.strptime(query['start_date'], '%Y%m%d').date()
        hi = dt.datetime.strptime(query['end_date'], '%Y%m%d').date()
        if lo >= hi or depth > 20:
            raise RecoveryError('Single report date still reaches the documented row cap; requires review')
        mid = lo + (hi - lo) // 2
        left = {**query, 'end_date': mid.strftime('%Y%m%d')}
        right = {**query, 'start_date': (mid + dt.timedelta(days=1)).strftime('%Y%m%d')}
        combined = split(left, depth + 1) + split(right, depth + 1)
        parent_counts = collections.Counter(canonical(row) for row in rows)
        child_counts = collections.Counter(canonical(row) for row in combined)
        if parent_counts - child_counts:
            raise RecoveryError('Split-date responses lost records from the parent response')
        return combined

    if api == 'namechange':
        rows = fetch(params, reuse_original=True)
        if len(rows) >= 1000:
            raise RecoveryError('Unexpectedly large single-stock name history requires review')
        leaves.append({'params': params, 'rows': len(rows)})
        mode = 'single_stock_nonpaginated'
    elif api == 'fina_mainbz':
        rows = split(params)
        mode = 'date_intervals_below_documented_row_cap'
    else:
        raise RecoveryError('Unsupported recovery API')
    return rows, list(artifacts.values()), {'mode': mode, 'leaves': leaves,
            'terminal_empty_page': False, 'coverage_note': 'Extraction validated within returned source history; empty or short responses do not prove complete historical coverage.'}
