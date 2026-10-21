"""Checkpointed weekly market-cap phase of the historical PIT project."""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / 'data_expansion/ifind_fundamental_control'
DEST = Path('E:/中证500_历年成分股_基本面_2010-2026_iFinD_PIT')


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    for attempt in range(50):
        try:
            temp.replace(path)
            return
        except PermissionError:
            time.sleep(0.1)
    raise RuntimeError(f'Cannot save {path}')


def read(path):
    for attempt in range(30):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (PermissionError, json.JSONDecodeError):
            time.sleep(0.1)
    raise RuntimeError(f'Cannot read {path}')


class Download:
    def __init__(self):
        self.universe = pd.read_parquet(DEST / 'plan/weekly_universe.parquet')
        self.db = sqlite3.connect(DEST / 'manifest.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS market(asof TEXT PRIMARY KEY,rows INTEGER,cells INTEGER,path TEXT,sha256 TEXT,completed TEXT)')
        self.pending = {}
        self.started = time.monotonic()
        self.current_quota = None

    def progress(self, status, **extra):
        count, rows, cells = self.db.execute('SELECT COUNT(*),COALESCE(SUM(rows),0),COALESCE(SUM(cells),0) FROM market').fetchone()
        save(DEST / 'progress.json', dict(status=status, phase='weekly_market_cap', pid=os.getpid(),
             updated_at=datetime.now().isoformat(), completed_weekly_market_points=count,
             planned_weekly_points=self.universe.asof_date.nunique(), completed_weekly_market_rows=rows,
             planned_weekly_rows=len(self.universe), market_phase_data_cells=cells,
             financial_phase_status='awaiting_verified_indicator_metadata', industry_phase_status='awaiting_verified_indicator_metadata',
             active_requests=len(self.pending), latest_basic_quota=self.current_quota, **extra))

    def submit(self, key, function, args, kwargs=None):
        path = CONTROL / 'jobs' / (key + '.json')
        job = dict(function=function, args=args, kwargs=kwargs or {})
        if path.exists():
            if read(path) != job:
                raise RuntimeError(f'Conflicting request: {key}')
        else:
            save(path, job)

    def consume(self, key):
        path = CONTROL / 'results' / (key + '.json')
        if not path.exists():
            return None
        result = read(path)
        rawpath = DEST / 'raw' / (key + '.json.gz')
        with gzip.open(rawpath, 'wt', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False)
        if result.get('errorcode') != 0:
            raise RuntimeError(f"API {key}: {result.get('errorcode')}: {result.get('errmsg')}")
        return result

    def cleanup(self, key):
        # Remove job first, so consuming a response never schedules a duplicate.
        (CONTROL / 'jobs' / (key + '.json')).unlink(missing_ok=True)
        (CONTROL / 'results' / (key + '.json')).unlink(missing_ok=True)

    def quota(self):
        key = 'fund_quota_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.submit(key, 'THS_DataStatistics', [])
        start = time.monotonic()
        while time.monotonic() - start < 120:
            result = self.consume(key)
            if result is not None:
                quota = result['data']['tables']['BasicDataStat']
                remaining = int(quota['limit']) - int(quota['usage'])
                self.current_quota = dict(**quota, remaining=remaining, checked_at=datetime.now().isoformat())
                save(DEST / 'audit/latest_quota.json', self.current_quota)
                self.cleanup(key)
                if remaining < 100000:
                    raise RuntimeError('Basic quota reserve reached; no new requests will be dispatched.')
                return remaining
            time.sleep(0.25)
        raise RuntimeError('Quota check timed out')

    def validate_write(self, date, codes, result):
        values = {}
        for table in result.get('data', []):
            code = table['thscode']
            if code in values:
                raise RuntimeError(f'Duplicate API security at {date}: {code}')
            raw = table['table']['ths_market_value_stock']
            if len(raw) != 1:
                raise RuntimeError(f'Unexpected value shape: {date}/{code}')
            values[code] = raw[0]
        if set(values) != set(codes):
            raise RuntimeError(f'Incomplete or extra securities: {date}')
        frame = pd.DataFrame([dict(asof_date=date, thscode=code, total_market_cap_cny=values[code]) for code in codes])
        frame['total_market_cap_cny'] = pd.to_numeric(frame.total_market_cap_cny, errors='raise')
        if (frame.total_market_cap_cny.dropna() < 0).any():
            raise RuntimeError('Negative market capitalization')
        output = DEST / 'weekly' / f'market_cap_{date}.parquet'
        temporary = output.with_suffix('.tmp')
        frame.to_parquet(temporary, index=False)
        temporary.replace(output)
        sha = hashlib.sha256(output.read_bytes()).hexdigest()
        cells = int(result.get('dataVol') or len(codes))
        self.db.execute('INSERT OR REPLACE INTO market VALUES(?,?,?,?,?,?)',
            (date,len(frame),cells,str(output.relative_to(DEST)),sha,datetime.now().isoformat()))
        self.db.commit()

    def run(self, limit=None):
        done = {row[0] for row in self.db.execute('SELECT asof FROM market')}
        groups = [(date, sorted(df.thscode.tolist())) for date, df in self.universe.groupby('asof_date') if date not in done]
        if limit:
            groups = groups[:limit]
        remaining = self.quota()
        requested = sum(len(codes) for _, codes in groups)
        if requested + 100000 > remaining:
            raise RuntimeError('Insufficient quota for planned market phase plus reserve')
        completed_since_quota = 0
        cursor = 0
        while cursor < len(groups) or self.pending:
            if (DEST / 'STOP').exists():
                self.progress('pausing')
                if not self.pending:
                    self.progress('paused')
                    return
            state = read(CONTROL / 'worker_status.json')
            if state['status'] in ('blocked','crashed','stopped','login_failed','login_cancelled'):
                raise RuntimeError(f'Worker unavailable: {state}')
            if completed_since_quota >= 50 and not self.pending:
                self.quota()
                completed_since_quota = 0
            while (cursor < len(groups) and len(self.pending) < 12 and completed_since_quota < 50
                   and not (DEST / 'STOP').exists()):
                date, codes = groups[cursor]
                key = 'fund_market_' + date
                self.submit(key, 'THS_BD', [','.join(codes), 'ths_market_value_stock', date], {'format':'format:list'})
                self.pending[key] = (date, codes, time.monotonic())
                cursor += 1
            for key, (date, codes, started) in list(self.pending.items()):
                result = self.consume(key)
                if result is None:
                    if time.monotonic() - started > 300:
                        raise RuntimeError(f'Request timeout; response and checkpoint retained: {key}')
                    continue
                self.validate_write(date, codes, result)
                self.cleanup(key)
                del self.pending[key]
                completed_since_quota += 1
                self.progress('downloading', last_completed_date=date)
            time.sleep(0.15)
        self.quota()
        self.progress('sample_completed' if limit else 'market_cap_complete_waiting_metadata')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    download = Download()
    try:
        download.run(args.limit)
    except Exception:
        download.progress('blocked', error=traceback.format_exc())
        raise
