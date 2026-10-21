"""Resumable daily, point-in-time CSI500 minute downloader via local API worker."""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

CONTROL = Path(__file__).resolve().parent.parent / 'data_expansion' / 'ifind_1min_hfq_control'
FIELDS = ['open', 'high', 'low', 'close', 'avgPrice', 'volume', 'amount',
          'turnoverRatio', 'sellVolume', 'buyVolume']
LABELS = ['开盘价', '最高价', '最低价', '收盘价', '均价', '成交量', '成交额', '换手率', '内盘', '外盘']
PARAMS = 'Interval:1,CPS:backward1,baseDate:1900-01-01,Fill:Original'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    for attempt in range(100):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.1)


def read_json_retry(path):
    """Retry transient Windows sharing violations during atomic replacement."""
    for attempt in range(100):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.1)


def members_from_result(result, date, expected_count=500):
    if result['errorcode'] != 0:
        raise RuntimeError(f"Membership error: {result['errorcode']} {result['errmsg']}")
    tables = result.get('data') or []
    frames = [pd.DataFrame(t['table']) for t in tables if t.get('table')]
    if not frames:
        raise ValueError(f'No membership for {date}')
    frame = pd.concat(frames, ignore_index=True)
    frame.columns = [c.upper() for c in frame.columns]
    if not {'THSCODE', 'DATE'} <= set(frame.columns):
        raise ValueError(f'Unexpected membership columns: {list(frame.columns)}')
    actual_dates = set(pd.to_datetime(frame['DATE']).dt.strftime('%Y-%m-%d'))
    if actual_dates != {date}:
        raise ValueError(f'Membership date mismatch: expected {date}, received {actual_dates}')
    codes = sorted(set(frame['THSCODE'].astype(str)))
    # Independently re-queried iFinD lists during the Gezhouba/CEEC merger gap.
    # Match the entire verified set, not just a relaxed constituent count.
    verified_merger_gap = (
        '2021-09-13' <= date <= '2021-09-27'
        and len(codes) == 499
        and hashlib.sha256('\n'.join(codes).encode()).hexdigest()
        == '322916d7ce5c8f7876e289782f64517ea8d02f6fc15d43eb2690880167a4b75e'
    )
    if not codes or len(codes) != len(frame) or (expected_count is not None and len(codes) != expected_count and not verified_merger_gap):
        raise ValueError(f'Expected {expected_count} unique constituents on {date}; got {len(codes)} in {len(frame)} rows')
    if any(not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', c) for c in codes):
        raise ValueError('Invalid constituent code')
    return codes


def bars_from_result(result, codes, date):
    """Keep provider bars (including 09:30); never create/fill missing bars."""
    frames = []
    for table in result.get('data') or []:
        code = table.get('thscode')
        if code not in codes:
            raise ValueError(f'Unexpected security: {code}')
        times = table.get('time') or []
        if not times:
            continue
        values = table.get('table') or {}
        missing = set(FIELDS) - set(values)
        if missing:
            raise ValueError(f'Missing indicator columns for {code}: {missing}')
        if any(len(values[f]) != len(times) for f in FIELDS):
            raise ValueError('Unequal column lengths')
        frame = pd.DataFrame({f: values[f] for f in FIELDS})
        frame.insert(0, 'time', pd.to_datetime(times))
        frame.insert(0, 'thscode', code)
        if set(frame['time'].dt.strftime('%Y-%m-%d')) != {date}:
            raise ValueError('Provider returned data outside requested membership date')
        if not frame['time'].dt.second.eq(0).all():
            raise ValueError('Unexpected sub-minute timestamps')
        for col in FIELDS:
            frame[col] = pd.to_numeric(frame[col], errors='raise').astype('float64')
        if frame['close'].isna().all():
            raise ValueError(f'All close values missing: {code}')
        if frame.duplicated(['thscode', 'time']).any():
            raise ValueError('Duplicate minute bars')
        valid = frame[['open', 'high', 'low', 'close']].notna().all(axis=1)
        prices = frame.loc[valid]
        if ((prices['high'] + 1e-8 < prices[['open', 'close', 'low']].max(axis=1)) |
                (prices['low'] - 1e-8 > prices[['open', 'close', 'high']].min(axis=1))).any():
            raise ValueError('OHLC consistency error')
        frames.append(frame)
    if frames:
        out = pd.concat(frames, ignore_index=True).sort_values(['thscode', 'time'])
        if out.duplicated(['thscode', 'time']).any():
            raise ValueError('Duplicate security table')
        return out
    return pd.DataFrame(columns=['thscode', 'time'] + FIELDS)


class Downloader:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.output).resolve()
        if self.root.drive.upper() != 'E:':
            raise ValueError('Output must be on requested E: drive')
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ['bars', 'memberships', 'errors', 'audit']:
            (self.root / folder).mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'manifest.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS batches (date TEXT, batch TEXT, status TEXT, rows INTEGER, path TEXT, sha256 TEXT, detail TEXT, PRIMARY KEY(date,batch))')
        self.db.commit()
        self.current = {}

    def status(self, state, **kwargs):
        self.current.update(kwargs)
        totals = dict(self.db.execute('SELECT status,COUNT(*) FROM batches GROUP BY status').fetchall())
        rows = self.db.execute('SELECT COALESCE(SUM(rows),0) FROM batches').fetchone()[0]
        value = dict(state=state, pid=os.getpid(), updated_at=datetime.now().isoformat(),
                     output=str(self.root), start=self.args.start, end=self.args.end,
                     batch_size=self.args.batch_size, pause_seconds=5, total_rows=rows,
                     concurrency=self.args.concurrency, global_start_spacing_seconds=5,
                     adjustment='backward1', api_parameters=PARAMS,
                     batch_counts=totals, **self.current)
        atomic_json(self.root / 'progress.json', value)
        atomic_json(CONTROL / 'download_status.json', value)

    def check_stop(self):
        if (self.root / 'STOP').exists():
            raise InterruptedError('STOP file detected; safe to resume')
        if shutil.disk_usage(self.root).free < 5 * 1024**3:
            raise RuntimeError('Less than 5 GiB free space on E:')

    def queue_api(self, function, args, fmt='format:list'):
        self.check_stop()
        job = dict(function=function, args=args, kwargs={'format': fmt}, pause_seconds=5)
        digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()[:24]
        name = '100_' + digest + '.json'
        jobpath = CONTROL / 'jobs' / name
        resultpath = CONTROL / 'results' / name
        # Reuse a completed response after a crash, but never reinterpret an API error as success.
        if not jobpath.exists():
            atomic_json(jobpath, job)
        return job, digest, jobpath, resultpath

    def api(self, function, args, fmt='format:list'):
        job, digest, jobpath, resultpath = self.queue_api(function, args, fmt)
        started = time.monotonic()
        while not resultpath.exists():
            self.check_stop()
            if time.monotonic() - started > 240:
                raise TimeoutError(f'API worker did not finish {function} within 240 seconds; request remains queued')
            worker = read_json_retry(CONTROL / 'worker_status.json')
            if worker['status'] in ['crashed', 'stopped', 'blocked', 'login_failed', 'login_cancelled']:
                raise RuntimeError(f"API worker state: {worker['status']}")
            time.sleep(0.5)
        response = read_json_retry(resultpath)
        # Delete the request first so a removed response can never trigger a duplicate download.
        jobpath.unlink(missing_ok=True)
        resultpath.unlink(missing_ok=True)
        if response['errorcode'] != 0:
            atomic_json(self.root / 'errors' / (digest + '.json'), dict(request=job, response=response))
            raise RuntimeError(f"iFinD {function}: {response['errorcode']} {response['errmsg']}; saved progress")
        return response

    def membership(self, date):
        path = self.root / 'memberships' / (date + '.json.gz')
        if path.exists():
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                raw = json.load(f)
        else:
            probe_name = {'2010-01-04': '002_members_2010', '2026-09-23': '003_members_recent'}.get(date)
            probe = CONTROL / 'results' / f'{probe_name}.json'
            if probe_name and probe.exists():
                raw = json.loads(probe.read_text(encoding='utf-8'))
            else:
                raw = self.api('THS_DP', ['index', f'{date};000905.SH', 'date:Y,thscode:Y'])
            members_from_result(raw, date)
            tmp = path.with_suffix('.tmp')
            with gzip.open(tmp, 'wt', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False)
            tmp.replace(path)
        return members_from_result(raw, date)

    def record(self, date, batch, state, frame, path, detail):
        sha = hashlib.sha256(path.read_bytes()).hexdigest() if path else ''
        self.db.execute('INSERT OR REPLACE INTO batches VALUES (?,?,?,?,?,?,?)',
                        (date, batch, state, len(frame), str(path.relative_to(self.root)) if path else '', sha,
                         json.dumps(detail, ensure_ascii=False)))
        self.db.commit()

    def download_day(self, date, codes):
        # Only network requests overlap. SQLite commits and Parquet publication stay single-writer.
        candidates = []
        for offset in range(0, len(codes), self.args.batch_size):
            selected = codes[offset:offset + self.args.batch_size]
            batch = hashlib.sha256(','.join(selected).encode()).hexdigest()[:16]
            existing = self.db.execute('SELECT status FROM batches WHERE date=? AND batch=?', (date, batch)).fetchone()
            if not existing or (self.args.retry_gaps and existing[0] in ['empty', 'partial']):
                candidates.append(offset)
        for offset in range(0, len(codes), self.args.batch_size):
            selected = codes[offset:offset + self.args.batch_size]
            batch = hashlib.sha256(','.join(selected).encode()).hexdigest()[:16]
            existing = self.db.execute('SELECT status,path,sha256 FROM batches WHERE date=? AND batch=?', (date, batch)).fetchone()
            if existing and not (self.args.retry_gaps and existing[0] in ['empty', 'partial']):
                if existing[1]:
                    previous = self.root / existing[1]
                    if not previous.exists() or hashlib.sha256(previous.read_bytes()).hexdigest() != existing[2]:
                        raise ValueError(f'Existing data file failed verification: {previous}')
                continue
            position = candidates.index(offset)
            for upcoming in candidates[position:position + self.args.concurrency]:
                group = codes[upcoming:upcoming + self.args.batch_size]
                self.queue_api('THS_HF', [','.join(group), ';'.join(FIELDS), PARAMS,
                                         date + ' 09:00:00', date + ' 15:30:00'])
            self.status('downloading', date=date, batch_offset=offset, codes_in_request=len(selected))
            response = self.api('THS_HF', [','.join(selected), ';'.join(FIELDS), PARAMS,
                                          date + ' 09:00:00', date + ' 15:30:00'])
            frame = bars_from_result(response, selected, date)
            if frame.empty:
                # Retry empty calls once to distinguish a transient empty response from a recorded gap.
                response = self.api('THS_HF', [','.join(selected), ';'.join(FIELDS), PARAMS,
                                              date + ' 09:00:00', date + ' 15:30:00'])
                frame = bars_from_result(response, selected, date)
            received = sorted(frame['thscode'].unique()) if not frame.empty else []
            detail = dict(requested_codes=selected, received_codes=received, api_parameters=PARAMS,
                          codes_without_bars=sorted(set(selected) - set(received)),
                          dataVol=response.get('dataVol'), request_metrics=response.get('request_metrics'),
                          null_counts={k: int(v) for k, v in frame[FIELDS].isna().sum().items()},
                          rows_per_code=frame.groupby('thscode').size().to_dict() if received else {},
                          explanation='No bars may mean suspension, no coverage, or provider missing data; not silently considered complete.')
            path = None
            if received:
                folder = self.root / 'bars' / date[:4] / date
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (batch + '.parquet')
                tmp = path.with_suffix('.tmp.parquet')
                frame.to_parquet(tmp, engine='pyarrow', compression='zstd', index=False)
                # Verify persisted data, schema, timestamps and membership after writing.
                saved = pd.read_parquet(tmp)
                if not saved.equals(frame.reset_index(drop=True)):
                    raise ValueError('Parquet round-trip verification failed')
                tmp.replace(path)
            state = 'empty' if not received else 'partial' if len(received) != len(selected) else 'downloaded'
            if any(detail['null_counts'].values()) and received:
                state = 'partial'
            self.record(date, batch, state, frame, path, detail)
            self.status('downloading', date=date, batch_offset=offset + len(selected))

    def run(self):
        metadata = dict(index='000905.SH', frequency='1min', requested_start=self.args.start,
                        requested_end=self.args.end, constituent_rule='Daily historical members only',
                        adjustment='后复权 / CPS:backward1 / 分红方案计算',
                        adjustment_base='上市首日 / baseDate:1900-01-01', api_parameters=PARAMS,
                        fill='Original; no synthetic rows',
                        timezone='Asia/Shanghai', fields=dict(zip(FIELDS, LABELS)),
                        notes=['Provider may return a 09:30 opening bar in addition to the 240 regular bars.',
                               'Values and nulls are preserved from iFinD. sellVolume and buyVolume are provider fields, not derived.',
                               'Empty and partial responses are retained in manifest; suspension versus missing history is not inferred.',
                               '2026 future dates are excluded. Full requested coverage is not claimed when gaps exist.'],
                        documentation='https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html')
        metadata_path = self.root / 'dataset.json'
        if metadata_path.exists():
            previous = json.loads(metadata_path.read_text(encoding='utf-8'))
            if any(previous.get(k) != metadata[k] for k in ['index', 'frequency', 'requested_start', 'requested_end', 'fields', 'adjustment', 'adjustment_base', 'api_parameters']):
                raise ValueError('Existing dataset has incompatible configuration')
        atomic_json(metadata_path, metadata)
        self.status('calendar')
        raw = self.api('THS_Date_Query', ['SSE', 'dateType:0,period:D,dateFormat:YYYY-MM-DD', self.args.start, self.args.end], 'format:dict')
        dates = raw['data']['time']
        if not dates or dates != sorted(set(dates)):
            raise ValueError('Invalid trading calendar')
        atomic_json(self.root / 'audit' / 'trading_calendar.json', raw)
        self.current['total_trading_days'] = len(dates)
        # Newest first: useful recent bars are persisted even if old coverage is unavailable.
        for index, date in enumerate(reversed(dates)):
            self.status('memberships', trading_day_index=index + 1, date=date)
            self.download_day(date, self.membership(date))
            self.status('day_done', completed_trading_days=index + 1, date=date)
        gaps = self.db.execute("SELECT COUNT(*) FROM batches WHERE status!='downloaded'").fetchone()[0]
        self.status('finished_with_gaps' if gaps else 'finished', completed_trading_days=len(dates))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=r'E:\中证500_历年成分股_1min_2010-2026_iFinD_后复权')
    parser.add_argument('--start', default='2010-01-01')
    parser.add_argument('--end', default='2026-09-24')
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--retry-gaps', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50:
        raise ValueError('Batch size must be 1..50')
    if not 1 <= args.concurrency <= 4:
        raise ValueError('Concurrency must be 1..4')
    if args.end > datetime.now().strftime('%Y-%m-%d'):
        raise ValueError('Cannot request future dates')
    job = Downloader(args)
    # An OS-held lock releases automatically if the process crashes.
    import msvcrt
    lock = open(job.root / 'download.lock', 'a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        job.run()
    except InterruptedError as exc:
        job.status('stopped', error=str(exc))
    except Exception:
        job.status('blocked', error=traceback.format_exc())
        raise
    finally:
        job.db.close()
        lock.close()


if __name__ == '__main__':
    main()
