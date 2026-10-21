"""Audited streaming minute -> four hourly bars -> causal features/labels.

The source parquet uses *start* timestamps: 09:30..11:29,13:00..14:59.
An hour is available at its right boundary. Execution uses five one-minute
opens at 10:31..10:35 etc. All serialized timestamps are UTC nanoseconds.
No missing market observations are interpolated or fabricated.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import gzip
import hashlib
import functools
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import time

# Optional project-local runtime dependencies already present in this workspace.
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / 'gru_research' / 'artifacts'
SOURCE_DAILY = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权'
TZ = 'Asia/Shanghai'
MINUTE_OFFSETS = np.r_[np.arange(570, 690), np.arange(780, 900)]
SIGNAL_MINUTES = np.array([630, 690, 840, 900])
EXEC_MINUTES = np.array([571, 631, 781, 841])
HOUR_FEATURES = [
    'h_return_1', 'h_open_gap', 'h_open_close_return', 'h_amplitude',
    'h_upper_shadow', 'h_lower_shadow', 'h_close_location',
    'h_volume_same_slot_20d', 'h_volume_prev', 'h_ma_deviation_4',
    'h_ma_deviation_20', 'h_ma_deviation_80', 'h_return_4', 'h_return_20',
    'h_realized_volatility', 'h_minute_return_skew', 'h_minute_return_max',
    'h_minute_return_min', 'h_up_semivariance_share', 'h_return_logvolume_corr',
    'h_last15_volume_share', 'h_zero_volume_share', 'h_path_efficiency',
    'h_upminute_volume_share',
]


@functools.lru_cache(maxsize=16)
def next_known_open_time(last_day):
    """Next actual trading session from the independently stored iFinD calendar."""
    future_days = sorted(p.name[:10] for p in (SOURCE_DAILY / 'memberships').glob('*.json.gz') if p.name[:10] > last_day)
    if not future_days:
        raise ValueError(f'No verified next trading session after {last_day}; do not fabricate it')
    return int(pd.Timestamp(future_days[0], tz=TZ).tz_convert('UTC').value + EXEC_MINUTES[0] * 60 * 10**9)


def atomic_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    tmp.replace(path)


def write_parquet(frame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    table = frame if isinstance(frame, pa.Table) else pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, tmp, compression='zstd')
    tmp.replace(path)


def build_memberships():
    rows, dates, counts = [], [], {}
    for path in sorted((SOURCE_DAILY / 'memberships').glob('*.json.gz')):
        day = path.name[:10]
        if not '2018-01-01' <= day <= '2026-09-23':
            continue
        source = json.load(gzip.open(path))
        syms = source.get('thscode') or source['data'][0]['table']['THSCODE']
        if source['errorcode'] != 0 or len(syms) != len(set(syms)) or not 498 <= len(syms) <= 502:
            raise ValueError(f'Invalid historical membership: {path}')
        dates.append(day)
        counts[day] = len(syms)
        rows.extend((day, s) for s in syms)
    write_parquet(pd.DataFrame(rows, columns=['date', 'symbol']), ARTIFACTS / 'minute_memberships.parquet')
    atomic_json(ARTIFACTS / 'minute_calendar.json', {
        'dates': dates, 'union_symbols': sorted({s for _, s in rows}), 'counts': counts,
        'source': str(SOURCE_DAILY / 'memberships'), 'timezone': TZ, 'timestamp_format': 'utc_ns',
    })
    return dates, rows


class SplitFile(io.RawIOBase):
    """Read one compressed stream over the read-only .000..004 files."""
    def __init__(self, paths, fingerprint=False):
        self.paths = list(paths)
        self.index = 0
        self.handle = open(self.paths[0], 'rb')
        self.bytes_read = 0
        self.digests = [hashlib.sha256() for _ in self.paths] if fingerprint else None

    def readable(self):
        return True

    def readinto(self, b):
        n = self.handle.readinto(b)
        if n == 0 and self.index + 1 < len(self.paths):
            self.handle.close()
            self.index += 1
            self.handle = open(self.paths[self.index], 'rb')
            n = self.handle.readinto(b)
        self.bytes_read += n
        if self.digests is not None and n:
            self.digests[self.index].update(memoryview(b)[:n])
        return n

    def close(self):
        self.handle.close()
        super().close()


def _ratio(a, b):
    return float(a / b) if np.isfinite(b) and b != 0 else np.nan


def aggregate_stock_day(payload):
    """Numpy/Arrow implementation with explicit corruption/incompleteness audit."""
    day, symbol, contents = payload
    audit = {'date': day, 'symbol': symbol, 'source_rows': 0, 'duplicate_rows': 0,
             'duplicate_conflicts': 0, 'outside_session_rows': 0, 'invalid_rows': 0,
             'timestamp_mismatch_rows': 0, 'qfq_mismatch_rows': 0, 'error': ''}
    try:
        table = pq.read_table(io.BytesIO(contents), use_threads=False)
        required = ['股票代码', 'datetime', 'datetime_nano', 'open', 'high', 'low',
                    'close', 'volume', 'qfq_ratio', 'qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']
        if not set(required) <= set(table.column_names):
            raise ValueError(f'Missing source fields: {set(required) - set(table.column_names)}')
        audit['source_rows'] = len(table)
        a = {name: table[name].to_numpy(zero_copy_only=False) for name in required}
        declared = np.asarray(a['股票代码']).astype(str)
        if not np.all(declared == symbol[:6]):
            raise ValueError('File name/source symbol mismatch')
        nanos = a['datetime_nano'].astype(np.int64)
        # Unix nanoseconds are already UTC, verified against local timestamp strings.
        texts = np.asarray(a['datetime']).astype('datetime64[ns]').astype(np.int64)
        audit['timestamp_mismatch_rows'] = int(np.count_nonzero(texts - 8 * 3600 * 10**9 != nanos))
        midnight = pd.Timestamp(day, tz=TZ).tz_convert('UTC').value
        offsets = (nanos - midnight) // (60 * 10**9)
        order = np.argsort(nanos, kind='stable')
        for name in a:
            a[name] = a[name][order]
        nanos, offsets = nanos[order], offsets[order]
        unique = np.r_[True, np.diff(nanos) != 0] if len(nanos) else np.array([], bool)
        audit['duplicate_rows'] = int(np.count_nonzero(~unique))
        for j in np.flatnonzero(~unique):
            if any(a[k][j] != a[k][j - 1] for k in required if k not in ('datetime',)):
                audit['duplicate_conflicts'] += 1
        for name in a:
            a[name] = a[name][unique]
        nanos, offsets = nanos[unique], offsets[unique]
        raw = np.column_stack([a[x] for x in ['open', 'high', 'low', 'close']]).astype(float)
        adj = np.column_stack([a[x] for x in ['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']]).astype(float)
        vol, factor = a['volume'].astype(float), a['qfq_ratio'].astype(float)
        valid = (np.isfinite(raw).all(1) & np.isfinite(adj).all(1) & (raw > 0).all(1)
                 & (adj > 0).all(1) & np.isfinite(vol) & (vol >= 0)
                 & np.isfinite(factor) & (factor > 0)
                 & (raw[:, 1] >= raw[:, [0, 2, 3]].max(1) - 1e-8)
                 & (raw[:, 2] <= raw[:, [0, 1, 3]].min(1) + 1e-8))
        mismatch = ~np.isclose(adj, raw * factor[:, None], rtol=1e-7, atol=1e-6).all(1)
        audit['qfq_mismatch_rows'] = int(mismatch.sum())
        valid &= ~mismatch
        audit['invalid_rows'] = int((~valid).sum())
        audit['outside_session_rows'] = int((~np.isin(offsets, MINUTE_OFFSETS)).sum())
        whole_bad = audit['timestamp_mismatch_rows'] > 0 or audit['duplicate_conflicts'] > 0
        # Corporate actions cannot occur in the middle of one A-share session.
        if len(factor) and not np.allclose(factor, factor[0], rtol=1e-7, atol=1e-10):
            whole_bad = True
            audit['error'] = 'intraday_adjustment_factor_change'
        output = []
        for slot in range(4):
            expected = MINUTE_OFFSETS[slot * 60:(slot + 1) * 60]
            pos = np.flatnonzero(np.isin(offsets, expected))
            complete = (len(pos) == 60 and np.array_equal(offsets[pos], expected)
                        and valid[pos].all() and not whole_bad and vol[pos].sum() > 0)
            row = {'date': day, 'symbol': symbol, 'slot': slot,
                   'signal_time': int(midnight + SIGNAL_MINUTES[slot] * 60 * 10**9),
                   'hour_complete': bool(complete), 'minute_count': len(pos)}
            row.update({name: np.nan for name in HOUR_FEATURES[14:]})
            row.update({name: np.nan for name in ['open', 'high', 'low', 'close', 'volume',
                         'raw_open', 'raw_high', 'raw_low', 'raw_close', 'adjustment_factor']})
            if complete:
                o, h, l, c = adj[pos[0], 0], adj[pos, 1].max(), adj[pos, 2].min(), adj[pos[-1], 3]
                v = vol[pos]
                minute_r = np.log(adj[pos, 3] / np.r_[o, adj[pos[:-1], 3]])
                sr = float(np.square(minute_r).sum())
                std = minute_r.std()
                logv = np.log1p(v)
                corr = float(np.corrcoef(minute_r, logv)[0, 1]) if std > 1e-12 and logv.std() > 1e-12 else np.nan
                path = float(np.abs(np.diff(np.r_[o, adj[pos, 3]])).sum())
                stats = [np.sqrt(sr), float(np.mean(((minute_r - minute_r.mean()) / std)**3)) if std > 1e-12 else np.nan,
                         minute_r.max(), minute_r.min(), _ratio(np.square(minute_r[minute_r > 0]).sum(), sr),
                         corr, _ratio(v[-15:].sum(), v.sum()), float(np.mean(v == 0)),
                         _ratio(abs(c - o), path), _ratio(v[minute_r > 0].sum(), v.sum())]
                row.update(dict(zip(HOUR_FEATURES[14:], stats)))
                row.update({'open': o, 'high': h, 'low': l, 'close': c, 'volume': v.sum(),
                            'raw_open': raw[pos[0], 0], 'raw_high': raw[pos, 1].max(),
                            'raw_low': raw[pos, 2].min(), 'raw_close': raw[pos[-1], 3],
                            'adjustment_factor': factor[pos[-1]]})
            # Store the four day's execution windows alongside its four hourly slots.
            start = EXEC_MINUTES[slot]
            epos = np.flatnonzero((offsets >= start) & (offsets < start + 5))
            ecomplete = (len(epos) == 5 and np.array_equal(offsets[epos], np.arange(start, start + 5))
                         and valid[epos].all() and not whole_bad and vol[epos].sum() > 0)
            row.update({'window_start_time': int(midnight + start * 60 * 10**9),
                        'window_end_time': int(midnight + (start + 5) * 60 * 10**9),
                        'window_complete': bool(ecomplete),
                        'window_price': float(adj[epos, 0].mean()) if ecomplete else np.nan,
                        'window_raw_price': float(raw[epos, 0].mean()) if ecomplete else np.nan,
                        'window_volume': float(vol[epos].sum()) if ecomplete else np.nan,
                        'window_tradable': bool(ecomplete and vol[epos].sum() > 0),
                        'window_all_flat': bool(ecomplete and (raw[epos, 1] == raw[epos, 2]).all())})
            output.append(row)
        return output, audit
    except Exception as exc:
        audit['error'] = f'{type(exc).__name__}: {exc}'
        return [], audit


def aggregate_batch(batch):
    rows, audits = [], []
    for payload in batch:
        r, a = aggregate_stock_day(payload)
        rows.extend(r)
        audits.append(a)
    return rows, audits


def scan_archive(workers=6, max_days=None):
    """One full source pass, bounded memory, atomic day checkpoints, resumable."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if not (ARTIFACTS / 'minute_calendar.json').exists():
        build_memberships()
    members = pd.read_parquet(ARTIFACTS / 'minute_memberships.parquet')
    byday = {d: set(g.symbol) for d, g in members.groupby('date')}
    union = set(members.symbol)
    completed = {p.stem for p in (ARTIFACTS / 'minute_hourly_raw').glob('*.parquet')}
    parts = sorted(ROOT.glob('A股分钟k线数据.tar.gz.*'))
    if len(parts) != 5:
        raise ValueError('Expected all five source volumes .000..004')
    started = time.time()
    counters = collections.Counter()
    archive_dates, source_sets, day_rows, day_audits = set(), collections.defaultdict(set), {}, {}
    pending = collections.deque()
    fully_seen_days = 0
    stream = SplitFile(parts)
    def persist(result, day):
        nonlocal fully_seen_days
        rows, audits = result
        write_parquet(pd.DataFrame(rows), ARTIFACTS / 'minute_hourly_raw' / f'{day}.parquet')
        write_parquet(pd.DataFrame(audits), ARTIFACTS / 'minute_day_audit' / f'{day}.parquet')
        completed.add(day)
        fully_seen_days += 1
        counters['stockdays_processed'] += len(audits)
        counters['hour_rows_written'] += len(rows)
        counters['complete_hour_rows'] += sum(r['hour_complete'] for r in rows)
        for a in audits:
            for k in ['source_rows', 'duplicate_rows', 'duplicate_conflicts', 'invalid_rows',
                      'outside_session_rows', 'timestamp_mismatch_rows', 'qfq_mismatch_rows']:
                counters[k] += a[k]
            counters['stockday_errors'] += bool(a['error'])
        atomic_json(ARTIFACTS / 'minute_scan_progress.json', {
            'status': 'scanning', 'elapsed_seconds': round(time.time() - started, 1),
            'completed_days': len(completed), 'compressed_bytes_read': stream.bytes_read,
            'source_compressed_bytes': sum(p.stat().st_size for p in parts),
            'counters': dict(counters), 'last_date_completed': day,
            'source_parts': [{'name': p.name, 'bytes': p.stat().st_size} for p in parts],
        })
        if fully_seen_days % 25 == 0:
            print(f'completed {len(completed)} days, {counters["stockdays_processed"]} stock-days; {time.time()-started:.1f}s', flush=True)
    # Tar members of each date are contiguous. Submit an entire date to a worker.
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        current_day, batch = None, []
        with io.BufferedReader(stream, buffer_size=1024 * 1024) as source, gzip.GzipFile(fileobj=source) as zipped:
            with tarfile.open(fileobj=zipped, mode='r|') as archive:
                for member in archive:
                    pieces = member.name.split('/')
                    if len(pieces) >= 2 and len(pieces[1]) == 8 and pieces[1].isdigit():
                        rawday = pieces[1]
                        day = f'{rawday[:4]}-{rawday[4:6]}-{rawday[6:]}'
                        archive_dates.add(day)
                    if not member.isfile():
                        continue
                    counters['archive_files'] += 1
                    if len(pieces) != 3 or not pieces[-1].endswith('.parquet'):
                        counters['unexpected_archive_files'] += 1
                        continue
                    code = pieces[-1][:-8]
                    symbol = code + ('.SH' if code.startswith(('6', '9')) else '.SZ')
                    source_sets[day].add(symbol)
                    if day != current_day:
                        if batch:
                            pending.append((current_day, pool.submit(aggregate_batch, batch)))
                            batch = []
                        current_day = day
                        while len(pending) >= workers * 2:
                            pd_, future = pending.popleft()
                            persist(future.result(), pd_)
                        if max_days is not None and fully_seen_days >= max_days:
                            break
                    if day in completed or not '2018-01-01' <= day <= '2026-09-23' or symbol not in union:
                        continue
                    batch.append((day, symbol, archive.extractfile(member).read()))
                if batch:
                    pending.append((current_day, pool.submit(aggregate_batch, batch)))
        while pending:
            d, future = pending.popleft()
            persist(future.result(), d)
    checks = []
    for day, found in source_sets.items():
        expected = byday.get(day, set())
        checks.append({'date': day, 'source_symbols': len(found), 'historical_members': len(expected),
                       'missing_historical_symbols': len(expected - found), 'extra_source_symbols': len(found - expected)})
    write_parquet(pd.DataFrame(checks), ARTIFACTS / 'minute_archive_membership_audit.parquet')
    atomic_json(ARTIFACTS / 'minute_scan_progress.json', {
        'status': 'complete' if max_days is None else 'partial', 'elapsed_seconds': time.time() - started,
        'completed_days': len(completed), 'compressed_bytes_read': stream.bytes_read,
        'counters': dict(counters), 'archive_dates': sorted(archive_dates),
        'calendar_dates_missing_from_archive': sorted(set(byday) - archive_dates),
        'archive_dates_without_membership': sorted(archive_dates - set(byday)),
        'timestamp_format': 'utc_ns', 'timezone': TZ,
    })


def add_features_and_labels(frame, dates):
    """For one symbol: expand true trading grid so gaps break the 80-bar window."""
    times = np.concatenate([pd.Timestamp(d, tz=TZ).tz_convert('UTC').value + SIGNAL_MINUTES * 60 * 10**9 for d in dates])
    grid = pd.Index(times, name='signal_time')
    f = frame.set_index('signal_time').reindex(grid)
    f['date'] = np.repeat(dates, 4)
    f['slot'] = np.tile(np.arange(4), len(dates))
    f['symbol'] = frame.symbol.iloc[0]
    f['hour_complete'] = f['hour_complete'].eq(True)
    f['window_complete'] = f['window_complete'].eq(True)
    f['window_complete'] &= f['window_tradable'].eq(True)
    f['window_price'] = f['window_price'].where(f.window_complete)
    f['window_raw_price'] = f['window_raw_price'].where(f.window_complete)
    c, o, h, l, v = [f[x].where(f.hour_complete) for x in ['close', 'open', 'high', 'low', 'volume']]
    p = c.shift(1)
    f['h_return_1'] = np.log(c / p)
    f['h_open_gap'] = np.log(o / p)
    f['h_open_close_return'] = np.log(c / o)
    f['h_amplitude'] = (h - l) / o
    spread = (h - l).replace(0, np.nan)
    f['h_upper_shadow'] = (h - pd.concat([o, c], axis=1).max(axis=1)) / spread
    f['h_lower_shadow'] = (pd.concat([o, c], axis=1).min(axis=1) - l) / spread
    f['h_close_location'] = (c - l) / spread
    same_slot = v.groupby(f['slot']).transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    f['h_volume_same_slot_20d'] = v / same_slot.replace(0, np.nan)
    f['h_volume_prev'] = v / v.shift(1).replace(0, np.nan)
    for n in [4, 20, 80]:
        f[f'h_ma_deviation_{n}'] = c / c.rolling(n, min_periods=n).mean() - 1
    for n in [4, 20]:
        f[f'h_return_{n}'] = np.log(c / c.shift(n))
    # No labels participate in any feature formula, nor in validity of past inputs.
    f['window80_complete'] = f.hour_complete.astype(np.int16).rolling(80, min_periods=80).sum().eq(80)
    midnight = np.repeat(np.array([pd.Timestamp(d, tz=TZ).tz_convert('UTC').value for d in dates], np.int64), 4)
    starts = midnight + np.tile(EXEC_MINUTES, len(dates)) * 60 * 10**9
    ends = starts + 5 * 60 * 10**9
    # A signal uses the *next* execution window, including 15:00 -> next open.
    f['earliest_execution_time'] = pd.Series(starts, index=grid).shift(-1)
    f.iloc[-1, f.columns.get_loc('earliest_execution_time')] = next_known_open_time(dates[-1])
    entry = f.window_price.shift(-1)
    f['execution_price'] = entry
    f['execution_raw_price'] = f.window_raw_price.shift(-1)
    f['execution_window_complete'] = f.window_complete.shift(-1).eq(True)
    f['execution_tradable'] = f.window_tradable.shift(-1).eq(True)
    f['execution_all_flat'] = f.window_all_flat.shift(-1).eq(True)
    for delta in range(1, 5):
        f[f'label_end_time{delta}'] = pd.Series(ends, index=grid).shift(-(1 + delta))
        f[f'label_end_price{delta}'] = f.window_price.shift(-(1 + delta))
        f[f'y{delta}'] = np.log(f[f'label_end_price{delta}'] / entry)
    for name in HOUR_FEATURES + ['y1', 'y2', 'y3', 'y4']:
        f[name] = f[name].replace([np.inf, -np.inf], np.nan).astype(np.float32)
    return f.reset_index()


def finalize():
    """Process each symbol separately, retaining only true historical members."""
    metadata = json.loads((ARTIFACTS / 'minute_calendar.json').read_text())
    dates, symbols = metadata['dates'], metadata['union_symbols']
    memberships = pd.read_parquet(ARTIFACTS / 'minute_memberships.parquet')
    raw_paths = sorted((ARTIFACTS / 'minute_hourly_raw').glob('*.parquet'))
    if not raw_paths:
        raise ValueError('No audited minute aggregation to finalize')
    import duckdb
    con = duckdb.connect()
    con.execute('SET threads=1')
    con.execute('SET memory_limit=\'4GB\'')
    con.execute('SET preserve_insertion_order=false')
    con.execute('SET partitioned_write_max_open_files=16')
    con.execute('SET partitioned_write_flush_threshold=65536')
    raw_pattern = str(ARTIFACTS / 'minute_hourly_raw' / '*.parquet')
    counts = collections.Counter()
    outdir = ARTIFACTS / 'minute_hourly'
    outdir.mkdir(exist_ok=True)
    symbol_dir = ARTIFACTS / 'minute_symbol_features'
    symbol_dir.mkdir(exist_ok=True)
    raw_symbol_dir = ARTIFACTS / 'minute_raw_by_symbol'
    partition_done = ARTIFACTS / 'minute_partition_complete.json'
    if not partition_done.exists():
        # One source read rather than 1,178 repeated scans over 2,119 files.
        sql_source = "'" + raw_pattern.replace("'", "''") + "'"
        sql_destination = "'" + str(raw_symbol_dir).replace("'", "''") + "'"
        con.execute(f"COPY (SELECT * FROM read_parquet({sql_source})) TO {sql_destination} (FORMAT PARQUET, PARTITION_BY(symbol), COMPRESSION ZSTD, OVERWRITE_OR_IGNORE)")
        atomic_json(partition_done, {'raw_days': len(raw_paths), 'status': 'complete'})
    elif json.loads(partition_done.read_text())['raw_days'] != len(raw_paths):
        raise ValueError('Raw coverage changed after partition; remove only derived partition marker/output to rebuild')
    shape = (len(dates) * 4, len(symbols))
    counts['all_symbol_grid_rows'] = int(np.prod(shape))
    arrays = {}
    array_specs = {
        'hour_features': (np.float32, shape + (24,), np.nan),
        'hour_complete': (np.bool_, shape, False),
        'window80_complete': (np.bool_, shape, False),
        'members': (np.bool_, shape, False),
        'labels4': (np.float32, shape + (4,), np.nan),
        'execution_prices': (np.float32, shape, np.nan),
        'execution_raw_prices': (np.float32, shape, np.nan),
        'execution_complete': (np.bool_, shape, False),
        'execution_tradable': (np.bool_, shape, False),
        'execution_all_flat': (np.bool_, shape, False),
        'hour_ohlcv': (np.float32, shape + (10,), np.nan),
    }
    for name, (dtype, dims, fill) in array_specs.items():
        arrays[name] = np.lib.format.open_memmap(ARTIFACTS / f'minute_{name}.npy', mode='w+', dtype=dtype, shape=dims)
        arrays[name][:] = fill
    midnight = np.repeat(np.array([pd.Timestamp(d, tz=TZ).tz_convert('UTC').value for d in dates], np.int64), 4)
    times = midnight + np.tile(SIGNAL_MINUTES, len(dates)) * 60 * 10**9
    starts = midnight + np.tile(EXEC_MINUTES, len(dates)) * 60 * 10**9
    earliest = np.r_[starts[1:], np.int64(next_known_open_time(dates[-1]))]
    label_ends = np.full((shape[0], 4), -1, dtype=np.int64)
    for delta in range(1, 5):
        label_ends[:-(delta + 1), delta - 1] = starts[delta + 1:] + 5 * 60 * 10**9
    np.save(ARTIFACTS / 'minute_times.npy', times)
    np.save(ARTIFACTS / 'minute_earliest_execution_times.npy', earliest)
    np.save(ARTIFACTS / 'minute_label_end_times.npy', label_ends)
    ohlcv_names = ['open', 'high', 'low', 'close', 'volume', 'raw_open', 'raw_high', 'raw_low', 'raw_close', 'adjustment_factor']
    for i, symbol in enumerate(symbols):
        path = symbol_dir / f'{symbol}.parquet'
        if not path.exists():
            symbol_source = raw_symbol_dir / f'symbol={symbol}'
            if symbol_source.exists():
                raw = con.execute('SELECT * FROM read_parquet(?) ORDER BY signal_time', [str(symbol_source / '*.parquet')]).df()
            else:
                # Historical members with no source observations stay present as
                # genuinely missing; they must never disappear from coverage.
                row = {name: np.nan for name in pq.read_schema(raw_paths[0]).names}
                row.update({'symbol': symbol, 'date': dates[0], 'signal_time': int(times[0]), 'slot': 0,
                    'hour_complete': False, 'window_complete': False, 'window_tradable': False,
                    'window_all_flat': False, 'minute_count': 0})
                raw = pd.DataFrame([row])
            f = add_features_and_labels(raw, dates)
            current_member_dates = set(memberships.loc[memberships.symbol.eq(symbol), 'date'])
            f['is_member'] = f.date.isin(current_member_dates)
            # Historical normalization references only current members. Keep grid
            # with missing observations for root's per-symbol sequence construction.
            write_parquet(f, path)
        else:
            f = pd.read_parquet(path)
        arrays['hour_features'][:, i] = f[HOUR_FEATURES].to_numpy(np.float32)
        arrays['hour_complete'][:, i] = f.hour_complete.to_numpy(bool)
        arrays['window80_complete'][:, i] = f.window80_complete.to_numpy(bool)
        arrays['members'][:, i] = f.is_member.to_numpy(bool)
        arrays['labels4'][:, i] = f[['y1', 'y2', 'y3', 'y4']].to_numpy(np.float32)
        arrays['execution_prices'][:, i] = f.execution_price.to_numpy(np.float32)
        arrays['execution_raw_prices'][:, i] = f.execution_raw_price.to_numpy(np.float32)
        arrays['execution_complete'][:, i] = f.execution_window_complete.to_numpy(bool)
        arrays['execution_tradable'][:, i] = f.execution_tradable.to_numpy(bool)
        arrays['execution_all_flat'][:, i] = f.execution_all_flat.to_numpy(bool)
        arrays['hour_ohlcv'][:, i] = f[ohlcv_names].to_numpy(np.float32)
        if (i + 1) % 50 == 0:
            print(f'causal hourly features {i+1}/{len(symbols)} symbols', flush=True)
            atomic_json(ARTIFACTS / 'minute_finalize_progress.json', {'status': 'features', 'symbols_processed': i + 1, 'symbols_total': len(symbols)})
    for arr in arrays.values():
        arr.flush()
    # Monthly member-only label/feature tables support independent factor screening;
    # dense arrays preserve the full actual observation grid for sequence assembly.
    pattern = str(symbol_dir / '*.parquet')
    for month in sorted({d[:7] for d in dates}):
        f = con.execute('SELECT * FROM read_parquet(?) WHERE substr(date,1,7)=? AND is_member ORDER BY signal_time,symbol', [pattern, month]).df()
        write_parquet(f, outdir / f'{month}.parquet')
        counts['member_long_rows'] += len(f)
        counts['member_hour_rows'] += int(f.is_member.sum())
        counts['member_complete_hour_rows'] += int((f.is_member & f.hour_complete).sum())
        counts['member_window80_complete_rows'] += int((f.is_member & f.window80_complete).sum())
        counts['member_y4_mature_rows'] += int((f.is_member & f.y4.notna()).sum())
        counts['trainable_member_y4_rows'] += int((f.is_member & f.window80_complete & f.y4.notna()).sum())
        print(f'final month {month}: {int(f.is_member.sum())} member-hour rows', flush=True)
    atomic_json(ARTIFACTS / 'minute_feature_metadata.json', {
        'hour_features': HOUR_FEATURES, 'timezone': TZ, 'timestamp_format': 'utc_ns',
        'source_minute_timestamp_semantics': 'left/start boundary',
        'hour_bounds': ['09:30..10:30', '10:30..11:30', '13:00..14:00', '14:00..15:00'],
        'execution_windows_left_timestamps': ['09:31..09:35', '10:31..10:35', '13:01..13:05', '14:01..14:05'],
        'execution_price': 'mean of five complete minute qfq_open; raw open retained separately',
        'adjusted_price_consistency': 'qfq_ohlc == raw_ohlc * qfq_ratio within rtol=1e-7/atol=1e-6',
        'volume_unit': 'shares; daily-volume cross-check recorded separately',
        'label_formula': 'log(P[u+delta]/P[u]); u=signal_grid_index+1, delta=1,2,3,4',
        'label_end_time_semantics': 'right boundary of the final five-minute execution window',
        'window80_complete': '80 consecutive true trading-grid hours, no price interpolation',
        'source_readonly': True, 'counts': dict(counts),
        'dense_shape': shape, 'union_symbols': symbols, 'calendar_dates': dates,
        'hour_ohlcv_columns': ohlcv_names,
        'dense_files': {name: f'minute_{name}.npy' for name in arrays},
    })
    atomic_json(ARTIFACTS / 'minute_finalize_progress.json', {'status': 'complete', 'counts': dict(counts)})


def detailed_audit():
    """Resolve every corrupt minute to a specific cause; fingerprint source once.

    The cleaning pass stores counts/flags without retaining 254m raw minute rows.
    This independent source pass only decodes flagged stock-days and also records
    each compressed volume's SHA256. It never changes or re-cleans source data.
    """
    import duckdb
    c = duckdb.connect()
    pattern = str(ARTIFACTS / 'minute_day_audit' / '*.parquet')
    audits = c.execute('SELECT * FROM read_parquet(?)', [pattern]).df()
    bad = audits.loc[(audits.invalid_rows > 0) | (audits.duplicate_conflicts > 0) |
                     (audits.timestamp_mismatch_rows > 0) | (audits.qfq_mismatch_rows > 0) |
                     audits.error.ne('')]
    needed = {(r.date, r.symbol[:6]) for r in bad.itertuples()}
    if (ARTIFACTS / 'minute_invalid_details.parquet').exists() and (ARTIFACTS / 'minute_source_fingerprints.json').exists():
        reasons = pd.read_parquet(ARTIFACTS / 'minute_invalid_details.parquet').to_dict('records')
        found = {(r['date'], r['symbol'][:6]) for r in reasons}
        _write_quality_report(audits, reasons, needed, found)
        return
    reasons, found = [], set()
    stream = SplitFile(sorted(ROOT.glob('A股分钟k线数据.tar.gz.*')), fingerprint=True)
    with io.BufferedReader(stream, buffer_size=1024 * 1024) as source, gzip.GzipFile(fileobj=source) as zipped:
        with tarfile.open(fileobj=zipped, mode='r|') as tf:
            for member in tf:
                if not member.isfile():
                    continue
                pieces = member.name.split('/')
                if len(pieces) != 3 or not pieces[-1].endswith('.parquet'):
                    continue
                x = pieces[1]
                day, code = f'{x[:4]}-{x[4:6]}-{x[6:]}', pieces[-1][:-8]
                if (day, code) not in needed:
                    continue
                found.add((day, code))
                d = pq.read_table(io.BytesIO(tf.extractfile(member).read()), use_threads=False).to_pandas()
                raw = d[['open', 'high', 'low', 'close']].to_numpy(float)
                adj = d[['qfq_open', 'qfq_high', 'qfq_low', 'qfq_close']].to_numpy(float)
                factor = d.qfq_ratio.to_numpy(float)
                masks = {
                    'nonfinite_raw_price': ~np.isfinite(raw).all(1),
                    'nonpositive_raw_price': (raw <= 0).any(1),
                    'nonfinite_adjusted_price': ~np.isfinite(adj).all(1),
                    'nonpositive_adjusted_price': (adj <= 0).any(1),
                    'high_below_open_low_close': raw[:, 1] < raw[:, [0, 2, 3]].max(1) - 1e-8,
                    'low_above_open_high_close': raw[:, 2] > raw[:, [0, 1, 3]].min(1) + 1e-8,
                    'invalid_volume': ~np.isfinite(d.volume.to_numpy(float)) | (d.volume.to_numpy(float) < 0),
                    'invalid_adjustment_factor': ~np.isfinite(factor) | (factor <= 0),
                    'adjusted_raw_factor_mismatch': ~np.isclose(adj, raw * factor[:, None], rtol=1e-7, atol=1e-6).all(1),
                }
                nanos = d.datetime_nano.to_numpy(np.int64)
                midnight = pd.Timestamp(day, tz=TZ).tz_convert('UTC').value
                offsets = (nanos - midnight) // (60 * 10**9)
                for cause, mask in masks.items():
                    for j in np.flatnonzero(mask):
                        slot = next((s for s in range(4) if offsets[j] in MINUTE_OFFSETS[s * 60:(s + 1) * 60]), -1)
                        reasons.append({'date': day, 'symbol': code + ('.SH' if code.startswith(('6', '9')) else '.SZ'),
                            'minute_start_time': int(nanos[j]), 'slot': slot, 'cause': cause,
                            'open': raw[j, 0], 'high': raw[j, 1], 'low': raw[j, 2], 'close': raw[j, 3],
                            'volume': float(d.volume.iloc[j]), 'adjustment_factor': factor[j]})
    write_parquet(pd.DataFrame(reasons), ARTIFACTS / 'minute_invalid_details.parquet')
    hashes = []
    if stream.bytes_read != sum(p.stat().st_size for p in stream.paths):
        raise ValueError('Fingerprint source pass did not consume every compressed byte')
    for p, digest in zip(stream.paths, stream.digests):
        hashes.append({'name': p.name, 'size_bytes': p.stat().st_size, 'sha256': digest.hexdigest()})
    atomic_json(ARTIFACTS / 'minute_source_fingerprints.json', {'algorithm': 'SHA256', 'parts': hashes})
    _write_quality_report(audits, reasons, needed, found)


def _write_quality_report(audits, reasons, needed, found):
    import duckdb
    c = duckdb.connect()
    source_cover = pd.read_parquet(ARTIFACTS / 'minute_archive_membership_audit.parquet')
    raw = str(ARTIFACTS / 'minute_hourly_raw' / '*.parquet')
    annual = c.execute("SELECT substr(date,1,4) AS calendar_year, count(*) AS hours, sum(CAST(hour_complete AS INT)) AS complete_hours, sum(CASE WHEN minute_count != 60 THEN 1 ELSE 0 END) AS minute_missing_hours, sum(CASE WHEN NOT hour_complete AND minute_count=60 AND volume IS NULL THEN 1 ELSE 0 END) AS quarantined_or_suspended_hours FROM read_parquet(?) GROUP BY 1 ORDER BY 1", [raw]).df()
    write_parquet(annual, ARTIFACTS / 'minute_annual_quality.parquet')
    atomic_json(ARTIFACTS / 'minute_quality_report.json', {
        'stock_days_scanned': len(audits), 'source_minute_rows': int(audits.source_rows.sum()),
        'invalid_rows': int(audits.invalid_rows.sum()), 'duplicate_rows': int(audits.duplicate_rows.sum()),
        'timestamp_mismatch_rows': int(audits.timestamp_mismatch_rows.sum()),
        'qfq_mismatch_rows': int(audits.qfq_mismatch_rows.sum()), 'stockday_errors': int(audits.error.ne('').sum()),
        'source_membership_days': len(source_cover), 'missing_member_total': int(source_cover.missing_historical_symbols.sum()),
        'extra_source_symbols_total': int(source_cover.extra_source_symbols.sum()),
        'bad_stockdays': len(needed), 'bad_stockdays_diagnosed': len(found),
        'undecoded_bad_stockdays': sorted(needed - found),
        'invalid_cause_counts': dict(collections.Counter(r['cause'] for r in reasons)),
        'annual_hour_quality': annual.to_dict('records'),
        'missing_policy': 'no interpolation; minute/hour incompleteness or corrupt OHLC quarantines affected hour; 80 consecutive complete hours required',
        'source_volume_evidence': 'minute_volume_crosscheck.json',
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['memberships', 'scan', 'finalize', 'audit'])
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--max-days', type=int)
    args = parser.parse_args()
    if args.command == 'memberships':
        build_memberships()
    elif args.command == 'scan':
        scan_archive(args.workers, args.max_days)
    elif args.command == 'finalize':
        finalize()
    else:
        detailed_audit()


if __name__ == '__main__':
    main()
