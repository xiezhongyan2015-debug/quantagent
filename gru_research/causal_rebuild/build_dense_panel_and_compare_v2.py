"""Prepared full122 assembly/difference measurement; never train or infer models.

Explicit execution requires closed actual hourly products and a separately pinned
root measurement admission. Each input NPY is physically read once and hashed in
that read; Parquet slow98 is read once into a bounded byte buffer then projected.
Old normalization statistics were not saved: their measurement remains UNKNOWN.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import sys
import time
import warnings

NP = PD = PQ = None
CHUNK = 64 * 1024
T, U, F, DAYS = 8476, 1178, 122, 2119
META_SHA = '2e144ad60a9aba1e258ef7ff114bf538ed22f6307e368b378b425557793a2849'
META_FP = '6c2426b3f0d03421914a8c38b1a19bed5943a16f7dd74d93ca988dea73e4a2a4'
PROTOCOL_SHA = 'e8d5f6a15bba4496ab1895c9d9b781340acc1ef90782440a0b6c23822384205f'
GROUPS = {'hour': list(range(24)), 'daily': list(range(24, 40)), 'factors': list(range(40, 104)),
          'fund': list(range(104, 120)), 'ages': list(range(120, 122))}
COPIED = {'hour_complete': ('hour_complete', '|b1', (T, U)),
          'members': ('members', '|b1', (T, U)),
          'labels': ('labels4', '<f4', (T, U, 4)),
          'times': ('times', '<i8', (T,)),
          'label_end_times': ('label_end_times', '<i8', (T, 4)),
          'earliest_execution_times': ('earliest_execution_times', '<i8', (T,)),
          'execution_prices': ('execution_prices', '<f4', (T, U)),
          'execution_raw_prices': ('execution_raw_prices', '<f4', (T, U)),
          'execution_complete': ('execution_complete', '|b1', (T, U)),
          'execution_tradable': ('execution_tradable', '|b1', (T, U)),
          'execution_all_flat': ('execution_all_flat', '|b1', (T, U)),
          'hour_ohlcv': ('hour_ohlcv', '<f4', (T, U, 10))}
RELEVANT = {'X', 'industry', 'hour_complete', 'members', 'labels', 'times',
            'label_end_times', 'earliest_execution_times'}
SOURCE_INPUTS = {'original_meta', 'calendar', 'slow_manifest', 'r2_protocol', 'config',
                 'feature_names', 'symbols', 'factor_selection', 'original_minute_metadata',
                 'minute_receipt', 'minute_metadata', 'measurement_admission'}
PROVENANCE = ['financial_available_date', 'report_version_available_date', 'market_available_date',
              'event_query_date', 'report_query_date', 'market_query_date', 'daily_source_date', 'report_period']


def require(value, message):
    if not value:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def stat_id(path):
    p = Path(path); s = p.stat()
    return {'path': str(p.resolve()), 'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns,
            'inode': s.st_ino, 'device': s.st_dev}


def small(ref):
    p = Path(ref['path']); require(type(ref['bytes']) is int and 0 <= ref['bytes'] <= 4 * 1024**2, 'small budget')
    before = stat_id(p); require(before['bytes'] == ref['bytes'], 'small actual STAT size budget')
    with p.open('rb') as f: b = f.read(ref['bytes'] + 1)
    require(len(b) == ref['bytes'] and sha(b) == ref['sha256'] and stat_id(p) == before, 'small binding differs: ' + str(p))
    return b


def json_write(path, value):
    path = Path(path)
    with path.with_name(path.name + '.partial').open('xb') as f:
        f.write((json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode())
    os.replace(path.with_name(path.name + '.partial'), path)


def check_time(deadline):
    require(time.monotonic() <= deadline, 'measurement deadline exceeded; preserve attempt, no retry')


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def expected_clocks(dates, next_day):
    # Original signal at completed hour end; entry is next five-minute window.
    signals = NP.asarray([ns_day(d)*86400*10**9-8*3600*10**9+x*60*10**9
                          for d in dates for x in (630,690,840,900)], dtype='<i8')
    starts = NP.asarray([ns_day(d)*86400*10**9-8*3600*10**9+x*60*10**9
                         for d in dates for x in (571,631,781,841)], dtype='<i8')
    next_start = ns_day(next_day)*86400*10**9-8*3600*10**9+571*60*10**9
    entry_starts = NP.concatenate([starts[1:], NP.asarray([next_start],dtype='<i8')])
    ends = NP.full((T,4), -1, dtype='<i8')
    # The verified next-session open establishes only earliest entry time. No
    # prices/exit endpoints from that session are available in the actual tape.
    for delta in range(1,5): ends[:-(delta+1),delta-1] = starts[delta+1:]+5*60*10**9
    return signals, entry_starts, ends, next_start


class SequentialNPY:
    """Original product one physical forward read including the exact NPY header."""
    def __init__(self, ref, dtype, shape, deadline):
        self.ref, self.deadline = ref, deadline
        self.before = stat_id(ref['path']); self.f = Path(ref['path']).open('rb')
        self.h = hashlib.sha256(); self.bytes = 0; self.rows = 0; self.eof = False; self.header = bytearray()
        self.record_header = True
        try:
            version = NP.lib.format.read_magic(self)
            require(version in ((1, 0), (2, 0)), 'unsupported fixed NPY header version')
            method = NP.lib.format.read_array_header_1_0 if version == (1, 0) else NP.lib.format.read_array_header_2_0
            actual_shape, fortran, actual_dtype = method(self, max_header_size=64 * 1024)
            require(tuple(actual_shape) == tuple(shape) and not fortran and actual_dtype.str == dtype,
                    'fixed NPY shape/dtype/order differs: ' + str(ref['path']))
            self.shape, self.dtype = tuple(shape), actual_dtype
            self.row_bytes = int(NP.prod(shape[1:], dtype=NP.int64)) * actual_dtype.itemsize
            self.record_header = False
            require(self.before['bytes'] == len(self.header) + int(NP.prod(shape, dtype=NP.int64)) * actual_dtype.itemsize,
                    'NPY declared body byte length differs')
            require(self.before['bytes'] == ref['bytes'], 'actual NPY size differs from closed artifact')
        except BaseException:
            self.f.close(); raise

    def read(self, size):
        require(type(size) is int and 0 <= size <= 64 * 1024, 'header read must be bounded')
        check_time(self.deadline)
        b = self.f.read(size); self.h.update(b); self.bytes += len(b)
        if self.record_header:
            self.header.extend(b); require(len(self.header) <= 64 * 1024, 'header budget exceeded')
        return b

    def chunk(self, rows):
        require(type(rows) is int and 0 < rows <= 4 and self.rows + rows <= self.shape[0], 'forward NPY chunk bound')
        remaining = rows * self.row_bytes; pieces = []
        while remaining:
            b = self.read(min(CHUNK, remaining)); require(b, 'NPY truncated during forward read')
            pieces.append(b); remaining -= len(b)
        self.rows += rows
        return NP.frombuffer(b''.join(pieces), dtype=self.dtype).reshape((rows,) + self.shape[1:])

    def finish(self):
        check_time(self.deadline)
        require(self.rows == self.shape[0] and self.read(1) == b'', 'NPY not fully consumed/trailing bytes')
        require(self.h.hexdigest() == self.ref['sha256'] and stat_id(self.ref['path']) == self.before, 'same-read input SHA/STAT differs')
        self.eof = True; self.f.close()
        return self.observation()

    def observation(self):
        return {'path': str(Path(self.ref['path']).resolve()), 'bytes_read': self.bytes, 'read_sha256': self.h.hexdigest(),
                'entire_file_EOF_and_expected_SHA_verified': self.eof, 'rows_read': self.rows,
                'single_physical_source_pass': True, 'NPY_header_bytes': len(self.header),
                'STAT_before': self.before, 'STAT_after': stat_id(self.ref['path'])}

    def close(self):
        self.f.close()


class SequentialWriter:
    def __init__(self, path, dtype, shape, header=None):
        self.path, self.shape, self.dtype = Path(path), tuple(shape), NP.dtype(dtype)
        self.f = self.path.open('xb'); self.h = hashlib.sha256(); self.bytes = 0; self.rows = 0
        if header is None:
            tmp = io.BytesIO(); NP.lib.format.write_array_header_1_0(tmp,
                 {'descr': NP.lib.format.dtype_to_descr(self.dtype), 'fortran_order': False, 'shape': self.shape})
            header = tmp.getvalue()
        self._write(header)

    def _write(self, blob):
        for offset in range(0, len(blob), CHUNK):
            part = blob[offset:offset + CHUNK]; self.f.write(part); self.h.update(part); self.bytes += len(part)

    def append(self, array):
        a = NP.asarray(array)
        require(a.dtype.str == self.dtype.str and a.shape[1:] == self.shape[1:] and 0 < len(a) <= 4 and self.rows + len(a) <= self.shape[0],
                'generated NPY row schema differs')
        self._write(NP.ascontiguousarray(a).tobytes(order='C')); self.rows += len(a)

    def finish(self):
        require(self.rows == self.shape[0], 'generated output incomplete')
        self.f.flush(); self.f.close()
        require(self.path.stat().st_size == self.bytes, 'output size differs from measured write')
        return {'path': str(self.path.resolve()), 'bytes': self.bytes, 'sha256': self.h.hexdigest(),
                'shape': list(self.shape), 'dtype': self.dtype.str, 'actual_SHA_measured_in_single_write': True}

    def close(self):
        self.f.close()


def read_cache(ref, dtype, shape, deadline, readers):
    require(ref['bytes'] <= 128 * 1024**2, 'RAM-cache product budget exceeded')
    r = SequentialNPY(ref, dtype, shape, deadline); readers.append(r)
    result = NP.empty(shape, dtype=dtype)
    for t in range(0, shape[0], 4): result[t:t+4] = r.chunk(min(4, shape[0] - t))
    r.finish()
    return result, bytes(r.header)


def json_value(x):
    v = x.item() if hasattr(x, 'item') else x
    if isinstance(v, float) and not NP.isfinite(v):
        return 'NaN' if NP.isnan(v) else ('+Inf' if v > 0 else '-Inf')
    return v


class Difference:
    def __init__(self, name):
        self.name = name; self.elements = self.different = self.nan_different = 0; self.examples = []

    def add(self, original, rebuilt, start=0):
        a, b = NP.asarray(original), NP.asarray(rebuilt)
        require(a.shape == b.shape and a.dtype.str == b.dtype.str, 'comparison shape/dtype differs: ' + self.name)
        self.elements += a.size
        if a.dtype.kind == 'f':
            aa, bb = NP.isnan(a), NP.isnan(b)
            bits = NP.uint32 if a.dtype.itemsize == 4 else NP.uint64
            mismatch = ~(((a.view(bits) == b.view(bits)) & ~aa & ~bb) | (aa & bb))
            self.nan_different += int((aa != bb).sum())
        else:
            mismatch = a != b
        self.different += int(mismatch.sum())
        for idx in NP.argwhere(mismatch)[:max(0, 12 - len(self.examples))]:
            pos = tuple(int(x) for x in idx); key = list(pos); key[0] += start
            self.examples.append({'index': key, 'original': json_value(a[pos]), 'rebuilt': json_value(b[pos])})

    def result(self):
        return {'product': self.name, 'elements_compared': self.elements, 'different_elements': self.different,
                'different_NaN_masks': self.nan_different, 'first_differences': self.examples,
                'finite_bits_signed_zero_and_infinity_preserved': True, 'epsilon_used': False}


# Exact original assemble.py arithmetic; imports supplied only after actual gate.
def normalize_cross_section(raw, members):
    x = NP.asarray(raw, dtype=NP.float64).copy()
    x[~NP.isfinite(x)] = NP.nan
    ref = x[NP.asarray(members, dtype=bool)]
    if ref.shape[0] == 0:
        raise ValueError('Empty historical reference membership')
    count = NP.isfinite(ref).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        lo, hi = NP.nanquantile(ref, [0.01, 0.99], axis=0)
        ref_clipped = NP.clip(ref, lo, hi)
        mean = NP.nanmean(ref_clipped, axis=0)
        std = NP.nanstd(ref_clipped, axis=0, ddof=0)
    valid = count >= 2
    lo, hi, mean = [NP.where(valid, a, NP.nan) for a in (lo, hi, mean)]
    safe_std = NP.where(std > 0, std, 1.0)
    output = NP.clip((NP.clip(x, lo, hi) - mean) / safe_std, -5, 5)
    output[~NP.isfinite(x)] = NP.nan
    return output.astype(NP.float32), {'count': count, 'mean': mean, 'std': std, 'lo': lo, 'hi': hi}


def validate_slow_dates(batch):
    day = PD.to_datetime(batch.date)
    violations = {}
    for column in ['financial_available_date', 'report_version_available_date', 'market_available_date']:
        values = PD.to_datetime(batch[column]); violations[column] = int((values.notna() & (values > day)).sum())
    for column in ['event_query_date', 'report_query_date', 'market_query_date', 'daily_source_date']:
        values = PD.to_datetime(batch[column]); violations[column] = int((values.notna() & (values >= day)).sum())
    values = PD.to_datetime(batch['report_period']); violations['report_period'] = int((values.notna() & (values > day)).sum())
    require(not any(violations.values()), 'future-visible reused slow provenance')
    return violations


def load_slow(ref, manifest, dates, symbols, members, output, deadline):
    require(ref['sha256'] == manifest['raw_sha256'] and ref['bytes'] <= 320 * 1024**2, 'closed original slow raw identity/budget differs')
    before = stat_id(ref['path']); require(before['bytes'] == ref['bytes'], 'slow file size differs')
    h = hashlib.sha256(); parts = []; count = 0
    with Path(ref['path']).open('rb') as f:
        while True:
            check_time(deadline); b = f.read(CHUNK)
            if not b: break
            parts.append(b); h.update(b); count += len(b); require(count <= ref['bytes'], 'slow grew during read')
    require(count == ref['bytes'] and h.hexdigest() == ref['sha256'] and stat_id(ref['path']) == before, 'same-read slow SHA/STAT differs')
    blob = b''.join(parts); del parts
    pf = PQ.ParquetFile(io.BytesIO(blob))
    require(pf.metadata.num_rows == manifest['rows'] == 1059478 and 1 <= pf.metadata.num_row_groups <= 16,
            'original slow row/rowgroup metadata differs; no hardcoded single group')
    require(pf.metadata.num_columns<=128 and pf.metadata.serialized_size<=1024**2,
            'slow Parquet column/footer metadata cap exceeded')
    declared_uncompressed=sum(pf.metadata.row_group(g).column(c).total_uncompressed_size
                              for g in range(pf.metadata.num_row_groups) for c in range(pf.metadata.num_columns))
    require(0<=declared_uncompressed<=2*1024**3, 'slow Parquet declared inflation budget exceeded')
    names = manifest['daily16'] + manifest['factors'] + manifest['fund16'] + manifest['ages2']
    require(len(names) == 98, 'slow98 name count differs')
    columns = ['date', 'symbol', 'industry_code'] + names + PROVENANCE
    require(set(columns) <= set(pf.schema_arrow.names), 'slow98/provenance columns missing')
    raw = NP.lib.format.open_memmap(output / 'slow_raw_by_day.npy', mode='w+', dtype='<f4', shape=(DAYS, U, 98))
    raw[:] = NP.nan
    industry = NP.zeros((DAYS, U), dtype='<i2'); seen = NP.zeros((DAYS, U), dtype=bool)
    dates_map, syms_map = {v:i for i,v in enumerate(dates)}, {v:i for i,v in enumerate(symbols)}
    checks = Counter(); row_count = 0
    for batch in pf.iter_batches(batch_size=32768, columns=columns, use_threads=False):
        check_time(deadline); frame = batch.to_pandas(); checks.update(validate_slow_dates(frame))
        days = PD.to_datetime(frame.date).dt.strftime('%Y-%m-%d').tolist()
        di = NP.asarray([dates_map.get(x, -1) for x in days], dtype=NP.int64)
        si = NP.asarray([syms_map.get(x, -1) for x in frame.symbol], dtype=NP.int64)
        require((di >= 0).all() and (si >= 0).all(), 'slow unknown date/symbol')
        combined = di * U + si
        require(len(NP.unique(combined)) == len(combined) and not seen[di, si].any(), 'slow duplicate date/symbol')
        require(members[4 * di, si].all(), 'slow immutable row is not an actual dated member')
        values = frame[names].to_numpy(NP.float32); values[~NP.isfinite(values)] = NP.nan
        codes = frame.industry_code.to_numpy(dtype=NP.float64)
        require(NP.isfinite(codes).all() and (codes >= 0).all() and (codes <= 32767).all() and (codes == NP.floor(codes)).all(), 'industry integer bounds')
        raw[di, si], industry[di, si], seen[di, si] = values, codes.astype('<i2'), True
        row_count += len(frame)
    require(row_count == manifest['rows'] and NP.array_equal(seen, members[::4]), 'slow98 member key roster incomplete')
    raw.flush()
    return raw, industry, {'path': ref['path'], 'bytes_read': count, 'read_sha256': h.hexdigest(),
            'single_physical_source_pass': True, 'Parquet_numeric_projection_from_retained_RAM_bytes': True,
            'row_count': row_count, 'declared_uncompressed_bytes':declared_uncompressed,'STAT_before':before,'STAT_after':stat_id(ref['path']),
            'PIT_query_convention_checks': dict(checks), 'vendor_arrival_or_allissuer_PIT_certified': False}


def ns_day(value):
    return (date.fromisoformat(value) - date(1970, 1, 1)).days


def periods(protocol, dates):
    # Validate only small immutable metadata before any physical NPY/Parquet
    # source pass. Literal names below follow the unchanged e8d5 protocol.
    expected_folds = {
        '2021': {'fit':['2019-01-01','2021-01-01'], 'inner':['2021-01-01','2021-07-01'], 'outer':['2021-07-01','2022-01-01']},
        '2022': {'fit':['2019-01-01','2022-01-01'], 'inner':['2022-01-01','2022-07-01'], 'outer':['2022-07-01','2023-01-01']},
        '2023': {'fit':['2019-01-01','2023-01-01'], 'inner':['2023-01-01','2023-07-01'], 'outer':['2023-07-01','2024-01-01']}}
    require([fold['id'] for fold in protocol['folds']] == list(expected_folds), 'chronological fold names/order differ')
    require(all({k:fold[k] for k in ('fit','inner','outer')} == expected_folds[fold['id']]
                for fold in protocol['folds']), 'chronological fold boundaries differ')
    expected_research = {'validation_2024':['2024-01-01','2025-01-01'],
                         'historical_2025':['2025-01-01','2026-01-01'],
                         'recent_2026':['2026-01-01','2026-09-24']}
    require(protocol['research_evaluation_after_new_freeze']['periods'] == expected_research,
            'actual frozen research period names/boundaries differ')
    require(protocol['final_refit']['fit'] == ['2019-01-01','2024-01-01'], 'final chronological fit differs')
    result = {}
    for fold in protocol['folds']:
        for name in ('fit', 'inner', 'outer'):
            result['r2_' + str(fold['id']) + '_' + name] = fold[name]
    result['final_fit_2019_2023'] = ['2019-01-01', '2024-01-01']
    result.update(protocol['research_evaluation_after_new_freeze']['periods'])
    for name, interval in result.items():
        previous = [d for d in dates if d < interval[1]]
        require(len(previous) >= 2, 'period lacks last two true exchange sessions')
        result[name] = {'start': interval[0], 'end': interval[1], 'cutoff_day': previous[-2],
                        'cutoff_ns': ns_day(previous[-2]) * 86400 * 10**9 - 8 * 3600 * 10**9,
                        'last_two_complete_calendar_days': previous[-2:]}
    # Retain the original full40/R1 recent-2026 no-embargo cohort separately;
    # R2's recent cohort above has its predeclared two-day embargo.
    result['r1_train'] = result['final_fit_2019_2023'].copy()
    result['r1_blo_inner'] = result['r2_2023_fit'].copy()
    previous = [d for d in dates if d < '2024-01-01']
    result['r1_blo_outer'] = {'start':'2023-01-01','end':'2024-01-01','cutoff_day':previous[-2],
                            'cutoff_ns':ns_day(previous[-2])*86400*10**9-8*3600*10**9,
                            'last_two_complete_calendar_days':previous[-2:]}
    result['r1_validation_2024'] = result['validation_2024'].copy()
    result['r1_test_2025'] = result['historical_2025'].copy()
    result['r1_recent_2026'] = {'start':'2026-01-01','end':'2026-09-24','cutoff_day':'2026-09-24',
                              'cutoff_ns':ns_day('2026-09-24')*86400*10**9-8*3600*10**9,
                              'last_two_complete_calendar_days': [], 'original_recent_exception':True}
    expected_names = {f'r2_{fold}_{role}' for fold in expected_folds for role in ('fit','inner','outer')}
    expected_names |= set(expected_research) | {'final_fit_2019_2023','r1_train','r1_blo_inner',
                       'r1_blo_outer','r1_validation_2024','r1_test_2025','r1_recent_2026'}
    require(set(result) == expected_names and len(result) == 19, 'closed 19-period cohort schema differs')
    return result


class Roll80:
    def __init__(self):
        self.ring = NP.zeros((80, U), dtype=bool); self.count = NP.zeros(U, dtype=NP.int16); self.t = 0

    def update(self, complete):
        pos = self.t % 80
        if self.t >= 80: self.count -= self.ring[pos]
        self.ring[pos] = complete; self.count += complete
        ok = (self.count == 80) & (self.t >= 79); self.t += 1
        return ok


class Cohorts:
    def __init__(self, protocol, dates, config):
        self.periods, self.config = periods(protocol, dates), config
        self.counts = {side: {name: Counter() for name in self.periods} for side in ('original', 'rebuilt')}
        self.h = {side: {name: hashlib.sha256() for name in self.periods} for side in self.counts}
        self.diffs = {name: Counter() for name in self.periods}; self.first = []

    def section(self, side, name, d, t, signal, earliest, ends, member, mask80, label, last_time):
        p = self.periods[name]; c = self.counts[side][name]
        if not (p['start'] <= d < p['end'] and d < p['cutoff_day']): return None
        c['candidate_time_sections'] += 1
        mature = bool((ends > earliest).all() and (NP.diff(ends) > 0).all() and (ends < p['cutoff_ns']).all() and ends[3] <= last_time)
        if not mature: c['actual_4_label_end_purge_excluded'] += 1; return None
        valid = member & mask80 & NP.isfinite(label).all(axis=1)
        if int(valid.sum()) < self.config['min_stocks']: c['below_100_excluded'] += 1; return None
        if (NP.var(NP.asarray(label[valid], dtype=NP.float64), axis=0) <= self.config['label_variance_floor']).any():
            c['label_variance_excluded'] += 1; return None
        ids = NP.flatnonzero(valid).astype('<i2')
        self.h[side][name].update(NP.asarray([t, signal, len(ids)], dtype='<i8').tobytes() + ids.tobytes())
        c['eligible_sections'] += 1; c['eligible_stock_hours'] += len(ids); c['slot_' + str(t % 4)] += 1
        return valid

    def add(self, d, t, old, new, last_time):
        for name in self.periods:
            a = self.section('original', name, d, t, *old, last_time)
            b = self.section('rebuilt', name, d, t, *new, last_time)
            if (a is None) != (b is None) or (a is not None and not NP.array_equal(a, b)):
                self.diffs[name]['different_sections_or_populations'] += 1
                if len(self.first) < 12:
                    self.first.append({'period': name, 'time_index': t, 'signal_time': int(new[0]),
                        'original_count': None if a is None else int(a.sum()), 'rebuilt_count': None if b is None else int(b.sum())})

    def result(self):
        for side in self.counts:
            for name,c in self.counts[side].items():
                require(c['eligible_sections'] > 0 and all(c['slot_' + str(s)] > 0 for s in range(4)),
                        'empty/missing-slot original or rebuilt chronological cohort: ' + side + '/' + name)
        return {'period_boundaries': self.periods, 'counts': {s:{n:dict(c) for n,c in v.items()} for s,v in self.counts.items()},
                'population_sha256': {s:{n:h.hexdigest() for n,h in v.items()} for s,v in self.h.items()},
                'differences': {n:dict(v) for n,v in self.diffs.items()}, 'first_differences': self.first,
                'hash_definition': 'canonical sorted time_index/signal_ns/count int64 little-endian then ordered union stock index int16; source symbols.json separately bound',
                'all_4_slots_kept': True, 'embargo_two_real_calendar_days_except_original_R1_recent': True,
                'all_4_actual_label_ends_used': True, 'metrics_or_test_scores_computed':False}


def prepared_contract():
    return {'status':'PREPARED_ONLY_NO_INPUT_READS','schema_version':'PREPARED_FULL122_DENSE_BUILD_V2',
            'input_grid':[T,U,F],'upstream_actual_hourly_generation_and_root_measurement_admission_required':True,
            'slow98_frozen_reuse_only':True,'normalizer_pool':'all finite current dated members; never labels or eligible subset',
            'NPY_source_passes':1,'slow_Parquet_physical_passes':1,'old_normalizer_statistics':'UNKNOWN_NOT_SAVED',
            'no_Torch_forward_training_or_model_selection':True,'full_PLAN_complete':False,'all_PLAN_acceptance_complete':False,
            'internal_deadline_scope':'cooperative application reads and per-day computation; blocked OS/library operations require root-owned external capture/watchdog',
            'command':'python build_dense_panel_and_compare_v2.py --execute --contract <closed_actual_contract> --contract-sha256 <root_pin> --source-sha256 <reviewed_source_pin> --output <new_exclusive_directory>'}


def execute(args):
    global NP, PD, PQ
    started = time.monotonic(); readers, writers, outputs, source_reads = [], [], {}, []
    contract = json.loads(small({'path':str(args.contract),'bytes':args.contract.stat().st_size,'sha256':args.contract_sha256}))
    require(contract['schema_version'] == 'PREPARED_FULL122_DENSE_BUILD_V2' and contract['execution_allowed'] is True, 'prepared/missing execution contract')
    require(str(args.output.resolve()) == contract['exact_new_output_directory'], 'new output namespace differs')
    source_ref = contract['source']; require(source_ref['path'] == str(Path(__file__).resolve()) and source_ref['sha256'] == args.source_sha256, 'source pin differs')
    small(source_ref)
    refs = contract['small_inputs']; require(set(refs)==SOURCE_INPUTS, 'closed small-source role set differs')
    docs = {k:json.loads(small(v)) for k,v in refs.items()}
    oldmeta, calendar, slow, protocol, config = [docs[k] for k in ('original_meta','calendar','slow_manifest','r2_protocol','config')]
    require(refs['original_meta']['sha256'] == META_SHA and sha(json.dumps(oldmeta,sort_keys=True,separators=(',',':')).encode()) == META_FP, 'wrong original local/remote panel')
    require(refs['r2_protocol']['sha256'] == PROTOCOL_SHA and config['sequence_length'] == 80 and config['embargo_trading_days'] == 2 and config['min_stocks'] == 100, 'fixed protocol boundaries differ')
    names, symbols, dates = docs['feature_names'], docs['symbols'], calendar['dates']
    require(len(names)==F and len(symbols)==U and len(dates)==DAYS and symbols==calendar['union_symbols'] and dates==sorted(set(dates)), 'fixed calendar/order differs')
    require(names == oldmeta['features'] and oldmeta['groups']==GROUPS and names[24:]==slow['daily16']+slow['factors']+slow['fund16']+slow['ages2'], 'fixed122 groups/order differs')
    require(refs['factor_selection']['sha256']==slow['factor_selection_sha256']==oldmeta['factor_selection_sha256'], 'no factor reselection')
    require(docs['factor_selection']['selected_ids'] == [x.removeprefix('factor_') for x in slow['factors']], 'selection IDs differ')
    require(config['label_variance_floor'] > 0, 'frozen label variance floor required')
    # Cohorts.__init__ uses only dates, Counter and SHA constructors; no arrays
    # or numeric runtime imports. A period/schema failure cannot start source IO.
    cohort=Cohorts(protocol,dates,config)
    cohort_metadata_preflight={'complete':True,'large_products_read_at_check':0,
        'period_names':list(cohort.periods),'period_boundaries':cohort.periods,
        'protocol_sha256':refs['r2_protocol']['sha256'],
        'literal_names_match_actual_frozen_protocol':True}
    minute = docs['minute_receipt']; admission = docs['measurement_admission']
    statuses={'PASS_NEW_HOURLY_DENSE_FULL_GRID_EXACT_ORIGINAL_QUALIFIED','COMPLETE_NEW_HOURLY_DENSE_WITH_PRESERVED_DIFFERENCES_ADMISSION_PENDING'}
    require(minute['schema_version']=='ACTUAL_NEW_HOURLY_DENSE_V1' and minute['status'] in statuses
            and minute['generation_complete'] is True and minute['symbols_completed']==U
            and minute['dense_shape']==[T,U] and minute['full_source_and_original_grid_closed'] is True
            and minute['downstream_reassembly_or_training_admitted'] is False
            and minute['full_PLAN_complete'] is False, 'actual qualified upstream products incomplete')
    require(admission['minute_receipt_sha256']==refs['minute_receipt']['sha256'] and admission['measurement_only_admitted'] is True and admission['downstream_training_admitted'] is False,
            'root actual data measurement admission missing; no training permission')
    require(admission['builder_source_sha256']==args.source_sha256, 'measurement admission for another builder')
    artifacts=minute['artifacts']; roles=[v['role'] for v in artifacts]
    require(len(roles)==len(set(roles)), 'upstream duplicate artifact roles')
    actual={v['role']:v for v in artifacts}
    require('metadata' in actual and actual['metadata']==refs['minute_metadata'], 'minute metadata not its actual receipt artifact')
    mm,om=docs['minute_metadata'],docs['original_minute_metadata']
    require(mm['schema_version']=='NEW_HOURLY_DENSE_METADATA_V1' and mm['dense_shape']==[T,U]
            and mm['calendar_dates']==dates==om['calendar_dates'] and mm['union_symbols']==symbols==om['union_symbols']
            and mm['hour_features']==names[:24]==om['hour_features']
            and mm['next_known_trading_day']==om['next_known_trading_day']=='2026-09-24'
            and mm['whole_day_future_quality_gate_applied'] is False
            and mm['historical_provider_arrival_or_QFQ_vintage_proven'] is False,
            'new/reference metadata fixed grid/source qualification differs')
    require(minute['array_artifacts']==mm['dense_artifacts'], 'metadata arrays not actual generation identities')
    require(len(minute['array_artifacts'])==23 and all(v==actual[v['role']] for v in minute['array_artifacts']),
            'actual upstream23 array identities are not its closed artifacts')
    needed={v[0] for v in COPIED.values()}|{'hour_features','window80_complete','execution_prices_f64'}
    require(needed <= set(actual), 'required actual upstream arrays missing')
    require(contract['minute_arrays']=={k:actual[k] for k in sorted(needed)}, 'array mapping not the actual receipt identities')
    oldrefs=contract['original_arrays']
    require(set(oldrefs)==set(COPIED)|{'X','industry'}, 'original14 array set differs')
    for name,ref in oldrefs.items():
        require(ref['sha256']==oldmeta['files_sha256'][name+'.npy'], 'original declared array SHA differs')
    product_refs=[*oldrefs.values(),*[actual[k] for k in sorted(needed)],contract['slow_raw']]
    product_paths=[str(Path(v['path']).resolve()) for v in product_refs]
    require(len(product_paths)==len(set(product_paths)), 'physical input products aliased; single-pass contract broken')
    require(all(not Path(p).is_relative_to(args.output.resolve()) and not args.output.resolve().is_relative_to(Path(p))
                for p in product_paths), 'output overlaps physical input product')
    product_bytes=sum(v['bytes'] for v in product_refs)
    budgets=contract['budgets']
    require(type(budgets['wall_seconds']) is int and 0<budgets['wall_seconds']<=7200
            and type(budgets['process_RSS_bytes']) is int and 0<budgets['process_RSS_bytes']<=16*1024**3
            and type(budgets['source_application_bytes']) is int and product_bytes<=budgets['source_application_bytes']<=10*1024**3,
            'actual source products exceed declared bounded resources')
    generated_body_bytes=T*U*F*4+T*U*2+DAYS*U*98*4+T*F*5*8
    for role,dtype,shape in COPIED.values():
        itemsize=1 if dtype=='|b1' else (4 if dtype=='<f4' else 8)
        elements=1
        for n in shape:elements*=n
        generated_body_bytes+=elements*itemsize
    require(type(budgets['new_output_file_bytes']) is int and generated_body_bytes+2*1024**2<=budgets['new_output_file_bytes']<=12*1024**3,
            'generated arrays/spool/output resource limit differs')
    # Exact source guards are small; original array body hashes are measured in this actual purpose pass.
    guards = contract['source_guards']; expected_guards=protocol['original_dataset']['original_source19_sha256']
    require(len(guards)==19 and {g['relative_name']:g['sha256'] for g in guards}==expected_guards,
            'exact original source19 guards required')
    for g in guards:
        require(str(Path(g['path']).resolve())==str((Path(contract['original_workspace'])/g['relative_name']).resolve()), 'source guard location mismatch')
        small(g)
    require(admission['original_meta_sha256']==META_SHA and admission['original_metadata_fingerprint']==META_FP
            and admission['fixed_grid']==[T,U,F], 'measurement admission not original exact panel scope')
    deadline=started+budgets['wall_seconds']; check_time(deadline)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
    import numpy as numpy
    import pandas as pandas
    import pyarrow.parquet as parquet
    NP,PD,PQ=numpy,pandas,parquet
    args.output.mkdir(parents=True,exist_ok=False)
    marker={'status':'STARTED_ONCE_NO_RETRY','PID':os.getpid(),'source_sha256':args.source_sha256,
            'contract_sha256':args.contract_sha256,'started_utc':utc(),'full_PLAN_complete':False}
    json_write(args.output/'execution_marker_v2.json',marker)
    failure=None; differences={}; label_check=Counter(); eligible_diff=Difference('derived_80_members_4labels_eligibility')
    independent_labels={d:Difference('independent_new_y'+str(d)) for d in range(1,5)}
    window_check=Difference('new_supplied_vs_derived_window80')
    try:
        old_cache,new_cache,header={}, {}, {}
        streaming={'labels','hour_ohlcv','execution_prices','execution_raw_prices'}
        for name,(role,dtype,shape) in COPIED.items():
            if name not in streaming:
                old_cache[name],_=read_cache(oldrefs[name],dtype,shape,deadline,readers)
                new_cache[name],header[name]=read_cache(actual[role],dtype,shape,deadline,readers)
                differences[name]=Difference(name); differences[name].add(old_cache[name],new_cache[name])
                w=SequentialWriter(args.output/(name+'.npy'),dtype,shape,header[name]); writers.append(w)
                for t in range(0,T,4): w.append(new_cache[name][t:t+4])
                outputs[name+'.npy']=w.finish()
        require(NP.array_equal(old_cache['members'],new_cache['members']), 'changed historical membership prevents unchanged slow98 reuse; explicit new slow generation required')
        for side in (old_cache,new_cache):
            m=side['members'];require(NP.array_equal(m[::4],m[1::4]) and NP.array_equal(m[::4],m[2::4]) and NP.array_equal(m[::4],m[3::4]), 'within-day member change')
            require(((m.sum(axis=1)>=498)&(m.sum(axis=1)<=502)).all(),'actual current dated pool coverage')
            expected,entries,ends,next_start=expected_clocks(dates,mm['next_known_trading_day'])
            require(NP.array_equal(side['times'],expected) and NP.array_equal(side['earliest_execution_times'],entries)
                    and NP.array_equal(side['label_end_times'],ends) and mm['next_execution_start_ns']==next_start,
                    'actual signal/next-execution/all4label-end grid differs')
        entry,_=read_cache(actual['execution_prices_f64'],'<f8',(T,U),deadline,readers)
        require(NP.isnan(entry[-1]).all() and not NP.isinf(entry).any() and (entry[NP.isfinite(entry)]>0).all(),
                'F64 entry tape tail/positive prices differ from actual available execution windows')
        supplied80,_=read_cache(actual['window80_complete'],'|b1',(T,U),deadline,readers)
        raw,industry_day,slow_observation=load_slow(contract['slow_raw'],slow,dates,symbols,new_cache['members'],args.output,deadline)
        source_reads.append(slow_observation)
        stream_old={};stream_new={}
        for name in streaming:
            role,dtype,shape=COPIED[name]
            stream_old[name]=SequentialNPY(oldrefs[name],dtype,shape,deadline);readers.append(stream_old[name])
            stream_new[name]=SequentialNPY(actual[role],dtype,shape,deadline);readers.append(stream_new[name])
            differences[name]=Difference(name)
            w=SequentialWriter(args.output/(name+'.npy'),dtype,shape,stream_new[name].header);writers.append(w)
        ox=SequentialNPY(oldrefs['X'],'<f4',(T,U,F),deadline);readers.append(ox)
        oi=SequentialNPY(oldrefs['industry'],'<i2',(T,U),deadline);readers.append(oi)
        nh=SequentialNPY(actual['hour_features'],'<f4',(T,U,24),deadline);readers.append(nh)
        wx=SequentialWriter(args.output/'X.npy','<f4',(T,U,F),ox.header);wi=SequentialWriter(args.output/'industry.npy','<i2',(T,U),oi.header)
        writers.extend([wx,wi]);differences['X']=Difference('X');differences['industry']=Difference('industry')
        stats={n:SequentialWriter(args.output/('normalization_'+n+'.npy'),'<i8' if n=='count' else '<f8',(T,F)) for n in ('count','lo','hi','mean','std')}
        writers.extend(stats.values()); old80,new80=Roll80(),Roll80()
        coverage={s:{} for s in ('original','rebuilt')}; normalized_finite=NP.zeros(F,dtype=NP.int64)
        for day_index,d in enumerate(dates):
            check_time(deadline);require(rss_bytes()<=contract['budgets']['process_RSS_bytes'], 'process memory limit exceeded; preserved no retry')
            start=day_index*4; newh=nh.chunk(4);oldx=ox.chunk(4);oldi=oi.chunk(4)
            require(not NP.isinf(newh).any() and not NP.isinf(oldx).any(), 'raw H24/original X has infinite cell')
            old={n:r.chunk(4) for n,r in stream_old.items()}; new={n:r.chunk(4) for n,r in stream_new.items()}
            for n in streaming:
                differences[n].add(old[n],new[n],start)
                writers_for_name=next(w for w in writers if w.path==args.output/(n+'.npy'))
                writers_for_name.append(new[n])
            newx=NP.empty((4,U,F),dtype='<f4');newi=NP.repeat(industry_day[day_index][None,:],4,axis=0)
            slowx,ss=normalize_cross_section(raw[day_index],new_cache['members'][start])
            daystats={n:NP.empty((4,F),dtype='<i8' if n=='count' else '<f8') for n in stats}
            for slot in range(4):
                t=start+slot;pool=new_cache['members'][t]
                newx[slot,:,:24],hs=normalize_cross_section(newh[slot],pool);newx[slot,:,24:]=slowx
                normalized_finite+=NP.isfinite(newx[slot,pool]).sum(axis=0)
                for n in stats:daystats[n][slot]=NP.concatenate([hs[n],ss[n]])
                a80=old80.update(old_cache['hour_complete'][t]);b80=new80.update(new_cache['hour_complete'][t])
                window_check.add(supplied80[t:t+1],b80[None,:],t)
                av=old_cache['members'][t]&a80&NP.isfinite(old['labels'][slot]).all(axis=1)
                bv=pool&b80&NP.isfinite(new['labels'][slot]).all(axis=1)
                eligible_diff.add(av[None,:],bv[None,:],t)
                for side,cache,mask,eligible in [('original',old_cache,a80,av),('rebuilt',new_cache,b80,bv)]:
                    c=coverage[side].setdefault(d[:4],Counter());c['pool_stock_hours']+=int(cache['members'][t].sum())
                    c['member_80_complete_hours']+=int((cache['members'][t]&mask).sum());c['all_4_labels_eligible_hours']+=int(eligible.sum())
                    c['cross_sections_min_100']+=int(eligible.sum()>=100)
                old_tuple=(int(old_cache['times'][t]),int(old_cache['earliest_execution_times'][t]),old_cache['label_end_times'][t],old_cache['members'][t],a80,old['labels'][slot])
                new_tuple=(int(new_cache['times'][t]),int(new_cache['earliest_execution_times'][t]),new_cache['label_end_times'][t],pool,b80,new['labels'][slot])
                cohort.add(d,t,old_tuple,new_tuple,int(new_cache['times'][-1]))
                for delta in range(1,5):
                    expected=NP.full(U,NP.nan,dtype='<f4')
                    if t+delta<T:
                        with NP.errstate(divide='ignore',invalid='ignore'):
                            v=NP.log(entry[t+delta]/entry[t]);v[~NP.isfinite(v)]=NP.nan;expected=v.astype('<f4')
                    lab=independent_labels[delta]; before=lab.different
                    lab.add(expected[None,:],new['labels'][slot,:,delta-1][None,:],t)
                    label_check['elements']+=U;label_check['bit_or_NaN_mismatches']+=lab.different-before
            differences['X'].add(oldx,newx,start);differences['industry'].add(oldi,newi,start)
            wx.append(newx);wi.append(newi)
            for n,w in stats.items():w.append(daystats[n])
        require(window_check.different==0,'new supplied window80 differs from independent streaming80')
        require(label_check['bit_or_NaN_mismatches']==0,'new labels differ from independently retained F64 entry tape')
        for r in [*stream_old.values(),*stream_new.values(),ox,oi,nh]:r.finish()
        require(len(readers)+len(source_reads)==len(product_refs) and all(r.eof for r in readers)
                and sum(r.bytes for r in readers)+sum(o['bytes_read'] for o in source_reads)==product_bytes,
                'closed input-product one-pass roster or consumed byte totals incomplete')
        for w in writers:
            if not w.f.closed:outputs[w.path.name]=w.finish()
        require(sum(p.stat().st_size for p in args.output.iterdir() if p.is_file())<=budgets['new_output_file_bytes'],
                'actual output size exceeded declared quota')
        require(int(industry_day.max())<oldmeta['n_industries'],'industry embedding identity changed')
        reports={n:v.result() for n,v in differences.items()};cohort_result=cohort.result()
        relevant_equal=all(reports[n]['different_elements']==0 for n in RELEVANT) and eligible_diff.different==0 and all(not q for q in cohort_result['differences'].values())
        all_products_equal=all(v['different_elements']==0 for v in reports.values())
        for filename,contents in (('symbols.json',symbols),('feature_names.json',names)):
            json_write(args.output/filename,contents)
            outputs[filename]={'path':str((args.output/filename).resolve()),'bytes':(args.output/filename).stat().st_size,
                               'sha256':sha((args.output/filename).read_bytes())}
        metadata={'schema_version':1,'production_ready':False,'causality_audit':{'passed':False,'qualified_full122_assembly_measurement_completed':True},
            'timestamp_format':'utc_ns','timezone':'Asia/Shanghai','groups':GROUPS,'features':names,'sequence_length':80,'n_industries':oldmeta['n_industries'],
            'history':oldmeta['history'],'factor_selection_sha256':oldmeta['factor_selection_sha256'],'files_sha256':{n:v['sha256'] for n,v in outputs.items()},
            'source_operator_receipt_sha256':refs['minute_receipt']['sha256'],'normalization':'original1/99clip,ddof0population,+-5;fit actualcurrentmembersbeforelabels/windoweligibility',
            'raw_market_data_is_synthetic':False,'coverage_by_year':{s:{y:dict(c) for y,c in v.items()} for s,v in coverage.items()},
            'hour_ohlcv_columns':mm['hour_ohlcv_columns'],'future_forecast_validation':'new independent future version and ledger required',
            'full_end_to_end_causality_proven':False,'future_vendor_arrival_verified':False,'full_PLAN_complete':False}
        metadata['data_fingerprint']=sha(json.dumps(metadata['files_sha256'],sort_keys=True).encode())
        json_write(args.output/'meta.json',metadata)
        outputs['meta.json']={'path':str((args.output/'meta.json').resolve()),'bytes':(args.output/'meta.json').stat().st_size,'sha256':sha((args.output/'meta.json').read_bytes())}
        result={'schema_version':1,'status':'COMPLETE_FULL122_NUMERICAL_EQUIVALENCE_ROOT_REUSE_DECISION_PENDING' if relevant_equal else 'COMPLETE_FULL122_RELEVANT_DIFFERENCES_FRESH_RETRAIN_REQUIRED',
            'full_scope_completed':True,'synthetic':False,'actual_large_input_hashes_measured':True,
            'all_relevant_input_label_mask_and_cohort_elements_equal':relevant_equal,'all14_numeric_products_equal':all_products_equal,'product_differences':reports,
            'eligibility_difference':eligible_diff.result(),'new_window80_independent_check':window_check.result(),
            'independent_F64_label_check':dict(label_check),'independent_F64_label_differences':{str(d):v.result() for d,v in independent_labels.items()},'cohorts':cohort_result,
            'coverage_by_year':{s:{y:dict(c) for y,c in v.items()} for s,v in coverage.items()},
            'normalization_stats':{'new_stats_actual_files_saved':True,'original_saved_stats':'UNKNOWN_NOT_SAVED_NO_REVERSE_INFERENCE_FROM_CLIPPED_X','old_stats_independently_recomputed':False,'pool_fit_uses_labels_or_eligible_mask':False},
            'normalized_member_finite_cells_per_feature':normalized_finite.tolist(),'actual_outputs':list(outputs.values()),'slow_source_observation':slow_observation,
            'runtime_versions':{'numpy':NP.__version__,'pandas':PD.__version__},'model_reuse_is_automatic':False,'downstream_training_admitted':False,
            'original40_and_R2_remain_original_panel_research_records':True,'any_relevant_difference_requires_new_data_bound_seed_initialization_30plus5_protocol':not relevant_equal,
            'nonmodel_tape_differences_preserved_separately':{n:reports[n] for n in sorted(set(reports)-RELEVANT)},'vendor_arrival_or_allissuer_PIT':'UNKNOWN',
            'full_PLAN_complete':False,'all_PLAN_acceptance_complete':False,'economic_acceptance':False}
    except BaseException as exc:
        failure={'type':type(exc).__name__,'message':str(exc)}
        result={'schema_version':1,'status':'FAILED_PRESERVED_NO_RETRY','failure':failure,
                'measured_partial_product_differences':{n:v.result() for n,v in differences.items()},
                'partial_independent_F64_label_check':dict(label_check),
                'partial_independent_F64_label_differences':{str(d):v.result() for d,v in independent_labels.items()},
                'partial_window80_check':window_check.result(),'partial_eligibility_difference':eligible_diff.result(),
                'full_scope_completed':False,'model_reuse_is_automatic':False,'downstream_training_admitted':False,
                'full_PLAN_complete':False,'all_PLAN_acceptance_complete':False,'economic_acceptance':False}
    finally:
        for r in readers:r.close()
        for w in writers:w.close()
    # Small guards rechecked; failure here remains explicitly failed, never parity accepted.
    try:
        [small(v) for v in guards]; [small(v) for v in refs.values()]; small(source_ref)
    except BaseException as exc:
        failure=failure or {'type':type(exc).__name__,'message':str(exc)};result.update(status='FAILED_POST_GUARD_PRESERVED_NO_RETRY',failure=failure,downstream_training_admitted=False)
    result.update({'started_marker':marker,'finished_utc':utc(),'elapsed_seconds':time.monotonic()-started,
        'contract_sha256':args.contract_sha256,'source_sha256':args.source_sha256,
        'cohort_metadata_preflight':cohort_metadata_preflight,
        'actual_NPY_source_read_observations':[r.observation() for r in readers], 'automatic_retry':False,
        'maximum_observed_RSS_bytes':rss_bytes(),'original_meta_byte_SHA':META_SHA,'original_canonical_metadata_fingerprint':META_FP,
        'large_source_read_total_bytes':sum(r.bytes for r in readers)+sum(o['bytes_read'] for o in source_reads),
        'declared_large_source_bytes':product_bytes,'observed_large_source_passes':len(readers)+len(source_reads),
        'partial_constructor_or_cleanup_read_counts_may_be_unknown':failure is not None,
        'derived_spool_policy':'slow_raw_by_day.npy is an excluded local derived temporary, random write then chronological reread; not a second original source product pass',
        'source_io_scope':'application bytes only; OS readahead/page-cache/disk physical IO is not measured',
        'normalizer_equivalence_scope':'all122 output cells, missingness and unchanged source formula; original global normalization statistics remain UNKNOWN',
        'read_method':'single physical source-file pass with64KiB chunks; numerical arrays cached inRAM or reused local-derived disk spool are separate from source IO',
        'training_or_model_forward_performed':False})
    json_write(args.output/'actual_full122_dense_build_and_diff_receipt_v2.json',result)
    print(json.dumps({'status':result['status'],'receipt':str(args.output/'actual_full122_dense_build_and_diff_receipt_v2.json')}),flush=True)
    return 1 if failure else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--contract',type=Path)
    parser.add_argument('--contract-sha256');parser.add_argument('--source-sha256');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps(prepared_contract(),ensure_ascii=False,sort_keys=True));return
    require(all((args.contract,args.contract_sha256,args.source_sha256,args.output)),'explicit closed execution arguments required')
    try:
        code=execute(args)
    except BaseException as exc:
        # A preflight failure never claims a run. After our owned marker, preserve
        # an explicit failure even if receipt finalization/cleanup itself failed.
        try:
            marker_path=args.output/'execution_marker_v2.json'
            marker=json.loads(marker_path.read_text()) if marker_path.exists() else None
            receipt=args.output/'actual_full122_dense_build_and_diff_receipt_v2.json'
            if (marker and marker.get('PID')==os.getpid() and marker.get('source_sha256')==args.source_sha256
                    and marker.get('contract_sha256')==args.contract_sha256 and not receipt.exists()):
                json_write(receipt,{'status':'FAILED_POST_MARKER_PRESERVED_NO_RETRY','full_scope_completed':False,
                    'failure':{'type':type(exc).__name__,'message':str(exc)},'started_marker':marker,'finished_utc':utc(),
                    'unknown_partial_read_or_write_counts':True,'downstream_training_admitted':False,
                    'model_reuse_is_automatic':False,'full_PLAN_complete':False,'all_PLAN_acceptance_complete':False,
                    'economic_acceptance':False})
        except BaseException as receipt_failure:
            print(json.dumps({'status':'FAILED_RECEIPT_WRITE_UNKNOWN','primary_failure':str(exc),
                              'receipt_failure':str(receipt_failure),'preserve_namespace_no_retry':True}),file=sys.stderr,flush=True)
        raise
    raise SystemExit(code)


if __name__=='__main__':main()
