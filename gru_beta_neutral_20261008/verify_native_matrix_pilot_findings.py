"""Independent reproduction of exact historical pilot gates and coarse checks."""
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/native_matrix_pilot'
PRICE = ['open', 'high', 'low', 'close']
FIELDS = PRICE + ['volume', 'amount']
GRID = list(range(571, 691)) + list(range(781, 901))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class RealRanges(io.RawIOBase):
    def __init__(self, length, pieces):
        self.length, self.pieces, self.pos = length, pieces, 0
    def readable(self):
        return True
    def seekable(self):
        return True
    def tell(self):
        return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.length + offset
        return self.pos
    def read(self, count=-1):
        if count < 0:
            count = self.length - self.pos
        for start, raw in self.pieces:
            if start <= self.pos and self.pos + count <= start + len(raw):
                begin = self.pos - start; self.pos += count
                return raw[begin:begin + count]
        raise ValueError('Read outside real retained source bytes')


def main():
    output = OUT / 'independent_native_matrix_pilot_verification.json'; assert not output.exists()
    audit_path = OUT / 'actual_minimum_gap_pilot_with_verified_alias_diagnostics.json'
    audit = json.loads(audit_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_native_matrix_verified_alias.py') == audit['source_code_sha256']
    acquisition_path = OUT / 'bounded_column_acquisition.json'; acquisition = json.loads(acquisition_path.read_text())
    aliases_path = OUT / 'bounded_verified_alias_column_acquisition.json'; aliases = json.loads(aliases_path.read_text())
    assert sha(acquisition_path) == audit['acquisition_receipt_sha256'] and sha(aliases_path) == audit['verified_historical_alias_acquisition_receipt_sha256']
    assert sha(aliases['official_identity']['official_statement_path']) == aliases['official_identity']['official_statement_sha256']
    tables, date_indices, timestamp_array = {}, {}, None
    for record in acquisition['results']:
        field = record['field']; historical = next(r for r in aliases['results'] if r['field'] == field)
        pieces = []
        requests = record['requests'] + [historical['request']]
        if 'reused_retained_footer' in record:
            requests += [record['reused_retained_footer']]
        for request in requests:
            assert sha(request['path']) == request['retained_range_sha256'] and request['http_status'] == 206
            pieces.append((request['start'], Path(request['path']).read_bytes()))
        parser = pq.ParquetFile(RealRanges(record['published_bytes'], pieces), pre_buffer=False, page_checksum_verification=True)
        decoded = parser.read(columns=['600167', '600985', '302132', '600004', '300114', '__index_level_0__'], use_threads=False)
        assert sha(record['selection_path']) == record['selection_sha256']
        selected = pq.read_table(record['selection_path'])
        assert sha(historical['selection_path']) == historical['selection_sha256']
        selected_old = pq.read_table(historical['selection_path'])
        for code in ['600167', '600985', '302132', '600004']:
            assert decoded[code].equals(selected[code])
        assert decoded['300114'].equals(selected_old['300114'])
        assert decoded['__index_level_0__'].equals(selected['__index_level_0__']) and decoded['__index_level_0__'].equals(selected_old['__index_level_0__'])
        if timestamp_array is None:
            timestamp_array = decoded['__index_level_0__']
            for i, stamp in enumerate(timestamp_array.to_pylist()):
                date_indices.setdefault(stamp.strftime('%Y-%m-%d'), []).append(i)
        else:
            assert timestamp_array.equals(decoded['__index_level_0__'])
        tables[field] = decoded
    assert len(date_indices) == 1995 and len(timestamp_array) == 478800
    stamps = timestamp_array.to_pylist(); assert stamps == sorted(set(stamps))
    for indices in date_indices.values():
        assert len(indices) == 240 and [stamps[i].hour * 60 + stamps[i].minute for i in indices] == GRID
    candidate = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    meta_path = candidate / 'meta.json'; meta = json.loads(meta_path.read_text())
    assert sha(meta_path) == audit['candidate_meta_sha256']
    assert sha(candidate / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(candidate / 'hour_complete.npy', mmap_mode='r')
    calendar = json.loads((ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())
    columns = {s: i for i, s in enumerate(calendar['union_symbols'])}; calendar_dates = {d: i for i, d in enumerate(calendar['dates'])}
    daily_hours = native_hours = 0; failed = Counter(); all_equal_days = all_equal_minutes = 0
    bars_by_target, native_keys = {}, set()
    for target in audit['results']:
        symbol, date = target['symbol'], target['date']; i = calendar_dates[date] * 4; col = columns[symbol]
        assert np.flatnonzero(~complete[i:i + 4, col]).tolist() == target['actual_missing_slots']
        source_code = '300114' if symbol == '302132.SZ' else symbol.split('.')[0]
        if source_code == '300114':
            assert date < aliases['official_identity']['official_code_change_date']
        rows = [{f: tables[f][source_code][i].as_py() for f in FIELDS} for i in date_indices[date]]
        nulls = sum(v is None for r in rows for v in r.values()); assert nulls == target['raw_null_fields_on_target_date']
        if nulls:
            assert target['status'] == 'target_date_absent_or_actual_native_null_fields'; failed['missing_date_or_nulls'] += 1
            continue
        bars = [{f: Decimal(str(r[f])) for f in FIELDS} for r in rows]; bars_by_target[symbol, date] = bars
        assert sha(target['primary_daily_path']) == target['primary_daily_sha256']
        obj = json.loads(Path(target['primary_daily_path']).read_text()); daily = next(dict(zip(obj['fields'], r)) for r in obj['rows'] if r[0] == date)
        flags = {'raw_price_and_quantity_fields_valid': all(all(r[f].is_finite() and r[f] > 0 and r[f] == r[f].quantize(Decimal('.01')) for f in PRICE)
                                                        and r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']
                                                        and r['volume'].is_finite() and r['volume'] >= 0 and r['volume'] == r['volume'].to_integral_value() for r in bars),
                 'primary_day_trading': daily['tradestatus'] == '1' and Decimal(daily['volume']) > 0,
                 'open_exact': bars[0]['open'] == Decimal(daily['open']), 'close_exact': bars[-1]['close'] == Decimal(daily['close']),
                 'prices_inside_primary_daily_range': max(r['high'] for r in bars) <= Decimal(daily['high']) and min(r['low'] for r in bars) >= Decimal(daily['low']),
                 'day_volume_within_100_shares': abs(sum(r['volume'] for r in bars) - Decimal(daily['volume'])) <= 100}
        assert flags == target['daily_flags']
        for flag, good in flags.items():
            if not good:
                failed[flag] += 1
        pass_slots = [s for s in target['actual_missing_slots'] if sum(r['volume'] for r in bars[s * 60:(s + 1) * 60]) > 0] if all(flags.values()) else []
        amount_good = [r['low'] * r['volume'] - 1 <= r['amount'] <= r['high'] * r['volume'] + 1 for r in bars]
        native_slots = [s for s in pass_slots if all(amount_good[s * 60:(s + 1) * 60])]
        assert pass_slots == target['daily_pass_slots'] and native_slots == target['native_amount_pass_slots']
        assert amount_good.count(False) == target['minute_amount_conflicts']
        daily_hours += len(pass_slots); native_hours += len(native_slots)
        native_keys.update((symbol, date, s) for s in native_slots)
        assert sha(target['existing_source_path']) == target['existing_source_sha256']
        old = [r for r in csv.DictReader(Path(target['existing_source_path']).read_text(encoding='gbk').splitlines()) if r['k线结束时间'].startswith(date)]
        normalized = {}; auction = None
        for r in old:
            t = datetime.fromisoformat(r['k线结束时间']); clock = t.hour * 60 + t.minute
            b = {f: Decimal(r[k]) for f, k in zip(PRICE, ['开盘价', '最高价', '最低价', '收盘价'])}
            b.update(volume=Decimal(r['成交量']) * 100, amount=Decimal(r['成交额']))
            if clock in GRID:
                normalized[clock] = b
            elif clock == 570 and b['volume'] > 0:
                auction = b
        if auction:
            b = normalized[571]; b['open'] = auction['open']; b['high'] = max(b['high'], auction['high']); b['low'] = min(b['low'], auction['low'])
            b['volume'] += auction['volume']; b['amount'] += auction['amount']
        equal = {f: sum(b[f] == normalized[clock][f] for clock, b in zip(GRID, bars)) for f in FIELDS}
        assert equal == target['normalized_wind17_equal_minute_counts_by_field']
        all_equal_days += all(n == 240 for n in equal.values())
        all_equal_minutes += sum(all(b[f] == normalized[clock][f] for f in FIELDS) for clock, b in zip(GRID, bars))
    assert daily_hours == audit['daily_and_selected_positive_hour_pass_hours'] == 496
    assert native_hours == audit['native_amount_pass_hours'] == 56 and dict(failed) == audit['failed_daily_checks_stockdays']
    assert all_equal_days == audit['all_six_fields_all_240_minutes_equal_existing_normalized_wind17_stockdays'] == 0
    assert all_equal_minutes == audit['all_six_fields_equal_existing_normalized_wind17_minutes'] == 23640
    coarse_path = OUT / 'native_matrix_coarse_diagnostics.json'; coarse = json.loads(coarse_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_native_matrix_coarse_pilot.py') == coarse['source_code_sha256']
    refs = {}
    for query_ref in coarse['retained_query_references']:
        assert sha(query_ref['path']) == query_ref['sha256'] and sha(query_ref['wire_path']) == query_ref['wire_sha256']
        query = json.loads(Path(query_ref['path']).read_text())
        raw = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
        for row in raw:
            r = dict(zip(query['fields'], row)); symbol = '302132.SZ' if r['code'] == 'sz.300114' else '600985.SH'
            clock = int(r['time'][8:10]) * 60 + int(r['time'][10:12]); refs[symbol, r['date'], clock] = {f: Decimal(r[f]) for f in PRICE + ['volume']}
    assert {(r['symbol'], r['date'], r['slot']) for r in coarse['results']} == native_keys
    checked = strict = pending = 0
    for r in coarse['results']:
        if r['status'] == 'independent_coarse_not_available':
            pending += 1; continue
        checked += 1; bars = bars_by_target[r['symbol'], r['date']]; flags = []
        for bucket, record in enumerate(r['buckets']):
            j = r['slot'] * 60 + bucket * 5; group = bars[j:j + 5]
            b = {'open': group[0]['open'], 'high': max(v['high'] for v in group), 'low': min(v['low'] for v in group),
                 'close': group[-1]['close'], 'volume': sum(v['volume'] for v in group)}
            ref = refs[r['symbol'], r['date'], record['end_clock']]
            good = {'open_exact': b['open'] == ref['open'], 'close_exact': b['close'] == ref['close'],
                    'range_inside_reference': b['high'] <= ref['high'] and b['low'] >= ref['low'],
                    'volume_within_100': abs(b['volume'] - ref['volume']) <= 100}
            assert all(record[k] == v for k, v in good.items()) and record['all_passed'] == all(good.values())
            assert {f: Decimal(v) for f, v in record['native_ohlcv'].items()} == b
            assert {f: Decimal(v) for f, v in record['reference_ohlcv'].items()} == ref
            flags.append(all(good.values()))
        assert len(flags) == 12 and all(flags) == r['all_12_buckets_passed']; strict += all(flags)
    assert checked == coarse['native_eligible_hours_coarse_checked'] and pending == coarse['native_eligible_hours_coarse_not_checked']
    assert strict == coarse['strict_coarse_diagnostic_hours']
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'audited_native_receipt_sha256': sha(audit_path), 'audited_coarse_receipt_sha256': sha(coarse_path),
              'current_candidate_meta_sha256': sha(meta_path), 'candidate_fingerprint': meta['data_fingerprint'],
              'retained_range_source_columns_independently_redecoded': True,
              'historical_alias_official_statement_and_date_bounds_verified': True,
              'actual_target_stockdays': 155, 'actual_missing_target_hours': 620,
              'daily_selected_positive_hours': daily_hours, 'native_amount_pass_hours': native_hours,
              'native_hours_coarse_checked': checked, 'native_hours_coarse_not_checked': pending,
              'strict_coarse_diagnostic_hours': strict, 'all_six_fields_equal_old_source_minutes': all_equal_minutes,
              'whole_remote_LFS_SHA256_verified': False, 'minute_quantity_precision_certified': False,
              'source_independence_certified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
              'production_ready': False, 'method': 'Independent retained-range sparse reader, fresh source/selection hashes, raw Decimal daily and minute checks, exact current candidate masks and independently regrouped actual 5m references; no producer modules imported.'}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
