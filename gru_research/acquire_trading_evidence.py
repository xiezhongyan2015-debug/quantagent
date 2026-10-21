"""Bounded public-vendor trading evidence collector, no installation or fitting.

Loads the already archived, audited Baostock Python sources in memory. No
setup.py, installation hook, credential file or personal account is used.
Successful empty queries, failures and unavailable rows remain distinguishable.
This TCP vendor evidence is explicitly not exchange-certified/PIT receipt proof.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import tarfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / 'acquired_data/data_round2/raw/baostock_0_9_3_source_semantics_16699d82d05037a8.tar.gz'
LOCK = ROOT / 'acquired_data/data_round2/baostock_session.lock'
OUTPUT = ROOT / 'gru_research/trading_data'
HISTORY_FIELDS = 'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST'
ALLOWED = frozenset({'query_history_k_data_plus', 'query_dividend_data', 'query_stock_basic', 'query_trade_dates'})


class DeadlineReached(BaseException):
    pass


def stop(*_):
    raise DeadlineReached('public query deadline reached')


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


class ArchivedSDK(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Only baostock/*.py may be imported; never extract or execute setup files."""
    def __init__(self, archive=SDK):
        self.archive = Path(archive)
        self.sha256 = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.modules = {}
        with tarfile.open(self.archive, 'r:gz') as t:
            for member in t:
                prefix = 'baostock-0.9.3/baostock/'
                if not member.isfile() or not member.name.startswith(prefix) or not member.name.endswith('.py'):
                    continue
                relative = member.name[len('baostock-0.9.3/'):]
                if '..' in Path(relative).parts or member.size > 2_000_000:
                    raise ValueError('unexpected audited SDK member')
                package = relative.endswith('/__init__.py')
                module = relative[:-len('/__init__.py')] if package else relative[:-3]
                name = module.replace('/', '.')
                self.modules[name] = (t.extractfile(member).read(), package, member.name)
        if 'baostock' not in self.modules:
            raise ValueError('audited SDK package missing')

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.modules:
            return importlib.util.spec_from_loader(fullname, self, is_package=self.modules[fullname][1])
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        data, package, member = self.modules[module.__name__]
        module.__file__ = str(self.archive) + '::' + member
        if package:
            module.__path__ = []
        exec(compile(data, module.__file__, 'exec'), module.__dict__)

    def load(self):
        if any(k == 'baostock' or k.startswith('baostock.') for k in sys.modules):
            raise RuntimeError('refuse unidentified preloaded SDK; use fresh collector process')
        sys.meta_path.insert(0, self)
        import baostock
        return baostock


def vendor_symbol(value):
    m = re.fullmatch(r'(\d{6})\.(SH|SZ)', value)
    if not m:
        raise ValueError('explicit supported exchange suffix required')
    return m.group(2).lower() + '.' + m.group(1)


def specifications(symbols, start='2024-12-01', end='2026-09-23'):
    if not '2024-12-01' <= start <= end <= '2026-09-23':
        raise ValueError('research raw-history bound invalid')
    specs = [{'api': 'query_trade_dates', 'params': {'start_date': start, 'end_date': '2026-09-24'}}]
    for symbol in sorted(set(symbols)):
        code = vendor_symbol(symbol)
        specs.append({'api': 'query_history_k_data_plus', 'params': {'code': code, 'fields': HISTORY_FIELDS,
            'start_date': start, 'end_date': end, 'frequency': 'd', 'adjustflag': '3'}})
        specs.append({'api': 'query_stock_basic', 'params': {'code': code}})
        for year in ('2025', '2026'):
            specs.append({'api': 'query_dividend_data', 'params': {'code': code, 'year': year, 'yearType': 'operate'}})
    return specs


def key(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:24]


def collect(specs, destination, *, seconds=600, query_seconds=40, interval=.5):
    destination = Path(destination).resolve()
    if not destination.is_relative_to(OUTPUT.resolve()):
        raise ValueError('downloads restricted to research trading_data directory')
    if not 0 < len(specs) <= 7000 or not 0 < seconds <= 3600 or not 0 < query_seconds <= 60 or interval < .5:
        raise ValueError('bounded, serial public queries required')
    for spec in specs:
        if spec['api'] not in ALLOWED:
            raise ValueError('API outside evidence whitelist')
        if spec['api'] == 'query_history_k_data_plus' and spec['params']['adjustflag'] != '3':
            raise ValueError('raw unadjusted prices only')
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / 'responses'
    archive.mkdir(exist_ok=True)
    atomic_json(destination / 'planned_queries.json', specs)
    loader = ArchivedSDK()
    bs = loader.load()
    deadline = time.monotonic() + seconds
    completed = failures = skipped = consecutive_failures = 0
    logged_in = False
    started = now()
    state = 'INITIALIZED'
    def progress(current=None):
        atomic_json(destination / 'progress.json', {'pid': os.getpid(), 'state': state, 'started_utc': started,
            'updated_utc': now(), 'planned': len(specs), 'completed': completed, 'failures': failures,
            'skipped_prior_success': skipped, 'sdk_archive': str(SDK), 'sdk_sha256': loader.sha256,
            'current_query': current, 'evidence_class': 'ANONYMOUS_TCP_VENDOR_NOT_EXCHANGE_CERTIFIED'})
    old_handler = signal.signal(signal.SIGALRM, stop)
    socket.setdefaulttimeout(15)
    def arm():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineReached('overall bounded collection time limit')
        signal.setitimer(signal.ITIMER_REAL, min(query_seconds, remaining))
    with LOCK.open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            arm()
            try:
                login = bs.login()
            except (Exception, DeadlineReached) as exc:
                login = SimpleNamespace(error_code='CLIENT_LOGIN_FAILURE', error_msg=f'{type(exc).__name__}: {exc}')
            signal.setitimer(signal.ITIMER_REAL, 0)
            login_record = {'api': 'login', 'anonymous_public': True, 'error_code': str(login.error_code),
                'error_msg': str(login.error_msg), 'fetched_utc': now(), 'sdk_sha256': loader.sha256}
            atomic_json(destination / 'anonymous_login.json', login_record)
            if str(login.error_code) != '0':
                state = 'LOGIN_FAILED_NO_MARKET_COVERAGE'
                print(json.dumps(login_record), flush=True)
                return
            logged_in = True
            state = 'RUNNING'
            for spec in specs:
                progress(spec)
                identity = key(spec)
                latest = destination / (identity + '.json')
                if latest.exists() and json.loads(latest.read_text()).get('error_code') == '0':
                    skipped += 1
                    completed += 1
                    continue
                fetched_start = now()
                rows = []
                try:
                    arm()
                    rs = getattr(bs, spec['api'])(**spec['params'])
                    while rs.error_code == '0' and rs.next():
                        rows.append(rs.get_row_data())
                        if len(rows) > 10000:
                            raise ValueError('unexpected response row budget')
                except (Exception, DeadlineReached) as exc:
                    rs = SimpleNamespace(error_code='CLIENT_QUERY_FAILURE', error_msg=f'{type(exc).__name__}: {exc}', fields=[])
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                status = ('SUCCESS_NONEMPTY' if rows else 'SUCCESS_EMPTY') if str(rs.error_code) == '0' else 'FAILED_NOT_EMPTY_SUCCESS'
                record = {**spec, 'provider': 'Baostock archived 0.9.3 public anonymous API',
                    'endpoint': 'public-api.baostock.com:10030', 'sdk_sha256': loader.sha256,
                    'fetched_start_utc': fetched_start, 'fetched_end_utc': now(),
                    'error_code': str(rs.error_code), 'error_msg': str(rs.error_msg), 'fields': rs.fields,
                    'rows': rows, 'status': status, 'evidence_class': 'ANONYMOUS_TCP_VENDOR_NOT_EXCHANGE_CERTIFIED'}
                data = (json.dumps(record, ensure_ascii=False) + '\n').encode()
                sha = hashlib.sha256(data).hexdigest()
                immutable = archive / (identity + '_' + sha[:16] + '.json')
                if not immutable.exists():
                    immutable.write_bytes(data)
                tmp = latest.with_suffix('.json.tmp'); tmp.write_bytes(data); tmp.replace(latest)
                receipt = {'api': spec['api'], 'params': spec['params'], 'status': status, 'rows': len(rows),
                    'error_code': str(rs.error_code), 'sha256': sha, 'path': str(immutable), 'fetched_utc': now()}
                with (destination / 'manifest.jsonl').open('a') as f:
                    f.write(json.dumps(receipt) + '\n')
                completed += 1
                failures += status == 'FAILED_NOT_EMPTY_SUCCESS'
                consecutive_failures = consecutive_failures + 1 if status == 'FAILED_NOT_EMPTY_SUCCESS' else 0
                print(json.dumps(receipt), flush=True)
                if consecutive_failures >= 3:
                    state = 'STOPPED_THREE_CONSECUTIVE_FAILURES_CHECKPOINTED'
                    break
                time.sleep(interval)
            else:
                state = 'COMPLETED'
        except (Exception, DeadlineReached) as exc:
            state = 'STOPPED_CHECKPOINTED_' + type(exc).__name__
            atomic_json(destination / 'stop_reason.json', {'type': type(exc).__name__, 'message': str(exc), 'updated_utc': now()})
        finally:
            if logged_in:
                try:
                    signal.setitimer(signal.ITIMER_REAL, min(15, query_seconds))
                    bs.logout()
                except (Exception, DeadlineReached):
                    pass
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            progress()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=['600004.SH', '000001.SZ', '000004.SZ'])
    p.add_argument('--output-name', default='public_probe_20251001')
    p.add_argument('--seconds', type=int, default=600)
    p.add_argument('--spec', type=Path)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', a.output_name):
        raise ValueError('sanitized output name required')
    specs = json.loads(a.spec.read_text()) if a.spec else specifications(a.symbols)
    collect(specs, OUTPUT / a.output_name, seconds=a.seconds)


if __name__ == '__main__':
    main()
