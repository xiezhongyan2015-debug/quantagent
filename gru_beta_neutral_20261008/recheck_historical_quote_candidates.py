"""Recheck the fixed 405-day native scope without a trade-extrema assumption.

Read each original CSV once, hash that same byte stream, persist selected native
days, then use bounded public coarse queries. This produces diagnostics only:
coarse bars never become minutes and historical failure receipts stay intact.
"""
import csv
import io
import hashlib
import traceback
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .resume_gap_references import load

DEST = p.OUT / 'historical_quote_candidate_recheck'
GRID = list(range(571, 691)) + list(range(781, 901))
PRICE = ['open', 'high', 'low', 'close']
FIELDS = dict(zip(PRICE, ['开盘价', '最高价', '最低价', '收盘价']))


class DigestReader(io.RawIOBase):
    def __init__(self, path):
        super().__init__()
        self.stream = path.open('rb', buffering=0)
        self.digest = hashlib.sha256()
        self.count = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        n = self.stream.readinto(buffer)
        if n:
            self.digest.update(memoryview(buffer)[:n])
            self.count += n
        return n

    def close(self):
        self.stream.close()
        super().close()


def normalize_rows(rows):
    bars, auction = {}, None
    for raw in rows:
        stamp = datetime.fromisoformat(raw['k线结束时间'])
        minute = stamp.hour * 60 + stamp.minute
        bar = {f: D(raw[key]) for f, key in FIELDS.items()}
        bar.update(volume=D(raw['成交量']) * 100, amount=D(raw['成交额']))
        assert all(v.is_finite() for v in bar.values())
        assert bar['volume'] >= 0 and bar['amount'] >= 0
        assert bar['volume'] == bar['volume'].to_integral_value()
        if minute in GRID:
            assert minute not in bars and stamp.second == 0
            bars[minute] = bar
        elif minute == 570 and bar['volume'] > 0:
            assert auction is None
            auction = bar
    assert sorted(bars) == GRID
    if auction is not None:
        first = bars[571]
        first.update(open=auction['open'], high=max(first['high'], auction['high']),
                     low=min(first['low'], auction['low']),
                     volume=first['volume'] + auction['volume'], amount=first['amount'] + auction['amount'])
    return [bars[i] for i in GRID]


def parse_files(requests, receipt, output):
    groups = defaultdict(list)
    for r in requests:
        groups[r['source_raw_path']].append(r)
    prior = {r['path']: r for r in receipt['native_files']}
    directory = DEST / 'native_selected_days'
    directory.mkdir(exist_ok=True)
    for file_name, group in sorted(groups.items()):
        path = p.Path(file_name)
        expected = {r['source_raw_sha256'] for r in group}
        assert len(expected) == 1
        current = p.identity(path)
        if file_name in prior:
            old = prior[file_name]
            if old['source_identity'] == current and all(p.Path(r['selected_path']).is_file() for r in old['days']):
                continue
            raise ValueError('Previously scanned native source or selected leaf changed: ' + file_name)
        dates = {r['date'] for r in group}
        selected = {date: [] for date in dates}
        raw = DigestReader(path)
        with io.TextIOWrapper(io.BufferedReader(raw, 1 << 20), encoding='gbk', newline='') as stream:
            for row in csv.DictReader(stream):
                date = row['k线结束时间'][:10]
                if date in selected:
                    selected[date].append(row)
            digest, byte_count = raw.digest.hexdigest(), raw.count
        assert p.identity(path) == current and digest in expected and byte_count == current['size']
        record = dict(path=file_name, sha256=digest, bytes=byte_count, source_identity=current, days=[])
        for request in group:
            bars = normalize_rows(selected[request['date']])
            leaf = directory / (request['symbol'] + '_' + request['date'] + '.json')
            result = dict(symbol=request['symbol'], date=request['date'], slots=request['slots'],
                          native_source_path=file_name, native_source_sha256=digest,
                          raw_selected_rows=selected[request['date']],
                          end_minute_grid=GRID, normalized_bars=[{f: str(v) for f, v in b.items()} for b in bars])
            p.atomic_json(leaf, result)
            record['days'].append(dict(symbol=request['symbol'], date=request['date'],
                                       selected_path=str(leaf), selected_sha256=p.sha(leaf)))
        receipt['native_files'].append(record)
        receipt.update(updated_utc=p.utc(), native_files_complete=len(receipt['native_files']))
        p.atomic_json(output, receipt)


def compare(request, q, leaf):
    native = p.json.loads(leaf.read_text())
    bars = [{f: D(v) for f, v in b.items()} for b in native['normalized_bars']]
    payload = q.get('data', [])
    rows = payload.get('record', []) if isinstance(payload, dict) else payload
    refs = [dict(zip(q['fields'], row)) for row in rows] if q['error_code'] == '0' else []
    result = dict(symbol=request['symbol'], date=request['date'], slots=request['slots'],
                  reference_code=q.get('code'),
                  old_native_amount_pass_by_slot=request['old_native_amount_pass_by_slot'],
                  reference_rows=len(refs), reference_path=q['wire_path'], reference_sha256=q['sha256'],
                  reference_error_code=q['error_code'], native_selected_path=str(leaf),
                  native_selected_sha256=p.sha(leaf), strict_coarse_pass_slots=[], all_fields_exact_slots=[])
    if len(refs) != 48:
        result['status'] = 'no_complete_reference'
        return result
    assert [int(r['time'][8:10]) * 60 + int(r['time'][10:12]) for r in refs] == GRID[4::5]
    comparisons = []
    for i, row in enumerate(refs):
        block = bars[i * 5:(i + 1) * 5]
        actual = dict(open=block[0]['open'], high=max(b['high'] for b in block),
                      low=min(b['low'] for b in block), close=block[-1]['close'],
                      volume=sum(b['volume'] for b in block), amount=sum(b['amount'] for b in block))
        differences = {f: actual[f] - D(row[f]) for f in actual}
        exact = {f: v == 0 for f, v in differences.items()}
        strict = all(exact[f] for f in PRICE) and abs(differences['volume']) <= 100
        comparisons.append(dict(end_minute=GRID[i * 5 + 4], exact=exact, strict_gate_pass=strict,
                                differences={f: str(v) for f, v in differences.items()},
                                actual={f: str(v) for f, v in actual.items()},
                                reference={f: row[f] for f in actual}))
    result.update(status='complete_reference_diagnostic', comparisons=comparisons,
                  strict_coarse_pass_slots=[s for s in request['slots'] if all(b['strict_gate_pass'] for b in comparisons[s*12:(s+1)*12])],
                  all_fields_exact_slots=[s for s in request['slots'] if all(all(b['exact'].values()) for b in comparisons[s*12:(s+1)*12])])
    return result


def main():
    lock = p.locked('historical_quote_candidate_recheck')
    DEST.mkdir(exist_ok=True)
    output = DEST / 'receipt.json'
    source = p.BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/every_actual_missing_hour_diagnostics.csv'
    with source.open(newline='') as f:
        rows = list(csv.DictReader(f))
    selected = [r for r in rows if r['daily_and_positive_hour_pass'] == 'True']
    days = {}
    for r in selected:
        request = days.setdefault((r['symbol'], r['date']), dict(symbol=r['symbol'], date=r['date'], slots=[],
             source_raw_path=r['source_raw_path'], source_raw_sha256=r['source_raw_sha256'], old_native_amount_pass_by_slot={}))
        assert request['source_raw_path'] == r['source_raw_path'] and request['source_raw_sha256'] == r['source_raw_sha256']
        request['slots'].append(int(r['slot']))
        request['old_native_amount_pass_by_slot'][r['slot']] = r['native_amount_pass'] == 'True'
    assert len(rows) == 3042 and len(selected) == 1620 and len(days) == 405
    requests = [days[k] for k in sorted(days)]
    receipt = p.json.loads(output.read_text()) if output.exists() else dict(
        started_utc=p.utc(), source_code_sha256=p.sha(__file__), immutable_scope_sha256=p.sha(source),
        requested_hours=1620, requested_stockdays=405, native_files=[], results=[], sessions=[],
        old_failed_trade_amount_bound_receipts_preserved=True,
        gate='all_four_coarse_prices_exact_and_volume_difference_at_most_100_shares_diagnostic_only',
        native_money_precision_policy='exact_decimal_differences_preserved_no_rounding_or_tolerance_admission',
        native_quantity_scale=100, native_quantity_scale_is_existing_legacy_policy=True,
        public_reference_is_corroboration_only=True, candidate_hours_admitted=0, status='scanning_native')
    assert receipt['immutable_scope_sha256'] == p.sha(source)
    if receipt['status'] == 'complete':
        return
    parse_files(requests, receipt, output)
    alias_path = p.BASE / 'official_old_code_minute_audit_20261009/receipt.json'
    alias = p.json.loads(alias_path.read_text())
    aliases = {(r['canonical_symbol'], r['observed_symbol'], r['official_code_change_date']) for r in alias['payloads'] if r['admitted']}
    assert ('302132.SZ', '300114.SZ', '2025-02-17') in aliases
    receipt.update(active_source_code_sha256=p.sha(__file__), official_alias_receipt_sha256=p.sha(alias_path),
                   old_code_query_routing='Only 302132 before its already verified 2025-02-17 code change; native rows must declare sz300114.')
    # Index only small per-query leaves. Prefer a previously complete response;
    # no repeatedly decoding parent receipts or year CSVs.
    cache = defaultdict(list)
    for path in (p.BASE / 'public_source_probes').rglob('*_5_p1.json'):
        if path.stat().st_size < 2 << 20:
            cache[path.name].append(path)
    done = {(r['symbol'], r['date']) for r in receipt['results']}
    source_lock = (p.ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    p.fcntl.flock(source_lock, p.fcntl.LOCK_EX | p.fcntl.LOCK_NB)
    public = load('public_baostock_probe')
    client = None
    session = None
    query_count = 0
    try:
        for request in requests:
            key = request['symbol'], request['date']
            if key in done:
                continue
            code = request['symbol'][-2:].lower() + '.' + request['symbol'][:6]
            leaf = DEST / 'native_selected_days' / (request['symbol'] + '_' + request['date'] + '.json')
            if request['symbol'] == '302132.SZ' and request['date'] < '2025-02-17':
                native_day = p.json.loads(leaf.read_text())
                assert {r['股票代码'] for r in native_day['raw_selected_rows']} == {'sz300114'}
                code = 'sz.300114'
            name = f"{code}_{request['date']}_{request['date']}_5_p1.json"
            q = None
            for cached in cache[name]:
                obj = p.json.loads(cached.read_text())
                if obj.get('code') != code or obj.get('start') != request['date'] or obj.get('frequency') != '5' or obj.get('adjustflag') != '3':
                    continue
                wire = p.Path(obj['wire_path'])
                if wire.is_file() and p.sha(wire) == obj['sha256']:
                    q = obj
                    raw_rows = obj.get('data', [])
                    raw_rows = raw_rows.get('record', []) if isinstance(raw_rows, dict) else raw_rows
                    if len(raw_rows) == 48:
                        break
            if q is None:
                if client is None or query_count >= 70:
                    if client:
                        client.close()
                        session.update(status='complete', finished_utc=p.utc())
                    directory = DEST / ('session_' + str(len(receipt['sessions']) + 1))
                    directory.mkdir(exist_ok=True)
                    session = dict(started_utc=p.utc(), status='querying', queries=0)
                    receipt['sessions'].append(session)
                    p.atomic_json(output, receipt)
                    client = public.PublicClient(directory)
                    client.socket.settimeout(8)
                    client.login()
                    query_count = 0
                session.update(symbol=request['symbol'], date=request['date'])
                q = client.query(code, request['date'], request['date'], '5')
                query_count += 1
                session['queries'] = query_count
                p.time.sleep(0.5)
            result = compare(request, q, leaf)
            receipt['results'].append(result)
            done.add(key)
            receipt.update(status='querying', updated_utc=p.utc(), completed_stockdays=len(done),
                strict_coarse_diagnostic_hours=sum(len(r['strict_coarse_pass_slots']) for r in receipt['results']),
                all_fields_exact_diagnostic_hours=sum(len(r['all_fields_exact_slots']) for r in receipt['results']))
            p.atomic_json(output, receipt)
    except Exception as error:
        receipt.update(status='bounded_session_failure_preserved', error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        if session:
            session.update(status='failed_preserved', finished_utc=p.utc())
    finally:
        if client:
            try:
                client.socket.settimeout(3)
                client.close()
            except Exception as error:
                receipt['logout_error'] = str(error)
        if session and session['status'] == 'querying':
            session.update(status='complete', finished_utc=p.utc())
        if len(done) == 405:
            receipt.update(status='complete', finished_utc=p.utc())
        receipt['complete_reference_hours'] = sum(len(r['slots']) for r in receipt['results'] if r['status'] == 'complete_reference_diagnostic')
        receipt['peak_rss_bytes'] = __import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss
        p.atomic_json(output, receipt)
        source_lock.close()
        lock.close()


if __name__ == '__main__':
    main()
