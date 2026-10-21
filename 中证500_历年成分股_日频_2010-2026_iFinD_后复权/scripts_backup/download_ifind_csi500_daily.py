"""Daily CSI500 history, bounded by exact daily index membership."""
import argparse
import gzip
import hashlib
import json
import os
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import download_ifind_csi500_1min as common

CONTROL = Path(__file__).resolve().parent.parent / 'data_expansion' / 'ifind_daily_hfq_control'
common.CONTROL = CONTROL
FIELDS = common.FIELDS
AVAILABLE = FIELDS[:8]
PARAMS = 'Interval:D,CPS:3,baseDate:1900-01-01,Fill:Omit'


def interval_groups(dates, membership, batch_size=500):
    """Each security/day pair occurs exactly once, including exit and re-entry."""
    groups = defaultdict(list)
    universe = sorted(set().union(*(set(membership[date]) for date in dates)))
    for code in universe:
        start = None
        for i in range(len(dates) + 1):
            present = i < len(dates) and code in membership[dates[i]]
            if present and start is None:
                start = i
            elif not present and start is not None:
                groups[(start, i - 1)].append(code)
                start = None
    result = []
    for (start, end), codes in sorted(groups.items()):
        for offset in range(0, len(codes), batch_size):
            result.append(dict(start=dates[start], end=dates[end], dates=dates[start:end + 1],
                               codes=codes[offset:offset + batch_size]))
    return result


def daily_frame(response, group, membership):
    if response['errorcode'] != 0:
        raise ValueError('Cannot parse an unsuccessful API result')
    frames = []
    for table in response.get('data') or []:
        code = table.get('thscode')
        if code not in group['codes']:
            raise ValueError(f'Unexpected code: {code}')
        times = table.get('time') or []
        if not times:
            continue
        values = table.get('table') or {}
        if not set(AVAILABLE) <= set(values):
            raise ValueError(f'Required daily fields missing for {code}: {set(AVAILABLE) - set(values)}')
        if any(len(values[field]) != len(times) for field in AVAILABLE):
            raise ValueError('Daily table column lengths differ')
        frame = pd.DataFrame({field: values.get(field, [None] * len(times)) for field in FIELDS})
        frame.insert(0, 'date', pd.to_datetime(times).strftime('%Y-%m-%d'))
        frame.insert(0, 'thscode', code)
        for date in frame['date']:
            if date not in group['dates'] or code not in membership[date]:
                raise ValueError(f'Out-of-membership observation: {code} {date}')
        for field in FIELDS:
            frame[field] = pd.to_numeric(frame[field], errors='raise').astype('float64')
        valid = frame[['open', 'high', 'low', 'close']].notna().all(axis=1)
        prices = frame.loc[valid]
        if ((prices['high'] + 1e-8 < prices[['open', 'close', 'low']].max(axis=1)) |
                (prices['low'] - 1e-8 > prices[['open', 'close', 'high']].min(axis=1))).any():
            raise ValueError('Daily OHLC consistency error')
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=['thscode', 'date'] + FIELDS)
    if frame.duplicated(['thscode', 'date']).any():
        raise ValueError('Duplicate security/date daily observations')
    return frame.sort_values(['date', 'thscode']).reset_index(drop=True)


class DailyDownloader(common.Downloader):
    def membership(self, date):
        """Confirm small count deviations and keep them visible for later review."""
        path = self.root / 'memberships' / (date + '.json.gz')
        args = ['index', f'{date};000905.SH', 'date:Y,thscode:Y']
        if path.exists():
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                raw = json.load(stream)
        else:
            raw = self.api('THS_DP', args)
        codes = common.members_from_result(raw, date, expected_count=None)
        if len(codes) != 500:
            audit = self.root / 'audit' / 'membership_count_anomalies' / (date + '.json')
            if not 498 <= len(codes) <= 502:
                common.atomic_json(audit, dict(date=date, count=len(codes), state='blocked_large_deviation', first_response=raw))
                raise ValueError(f'Large membership count deviation on {date}: {len(codes)}')
            prior = common.read_json_retry(audit) if audit.exists() else {}
            if prior.get('state') != 'confirmed_provider_set' or prior.get('codes') != codes:
                # api() consumes/removes the first result, so this is a fresh call.
                second = self.api('THS_DP', args)
                confirmed = common.members_from_result(second, date, expected_count=None)
                evidence = dict(date=date, count=len(codes), codes=codes,
                                first_response=raw, confirmation_response=second,
                                checked_at=datetime.now().isoformat(),
                                state='confirmed_provider_set' if codes == confirmed else 'blocked_inconsistent_set',
                                needs_independent_review=True,
                                note='Repeated provider responses agree; official membership completeness is not established. No constituents or prices invented.')
                common.atomic_json(audit, evidence)
                if codes != confirmed:
                    raise ValueError(f'Inconsistent membership re-query on {date}')
        if not path.exists():
            tmp = path.with_suffix('.tmp')
            with gzip.open(tmp, 'wt', encoding='utf-8') as stream:
                json.dump(raw, stream, ensure_ascii=False)
            tmp.replace(path)
        return codes

    def status(self, state, **kwargs):
        config_path = CONTROL / 'runtime_config.json'
        config = common.read_json_retry(config_path) if config_path.exists() else {}
        self.args.concurrency = int(config.get('concurrency', self.args.concurrency))
        if not 1 <= self.args.concurrency <= 12:
            raise ValueError('Concurrency must be 1..12')
        self.current.update(kwargs)
        totals = dict(self.db.execute('SELECT status,COUNT(*) FROM batches GROUP BY status').fetchall())
        rows = self.db.execute('SELECT COALESCE(SUM(rows),0) FROM batches').fetchone()[0]
        value = dict(state=state, pid=os.getpid(), updated_at=datetime.now().isoformat(),
                     output=str(self.root), frequency='daily', start=self.args.start, end=self.args.end,
                     concurrency=self.args.concurrency,
                     global_start_spacing_seconds=config.get('global_start_spacing_seconds', 5),
                     per_request_cooldown_seconds=config.get('per_request_cooldown_seconds', 5),
                     adjustment='CPS:3 / 后复权(分红再投)',
                     api_parameters=PARAMS, unavailable_fields=['sellVolume', 'buyVolume'],
                     total_rows=rows, batch_counts=totals,
                     membership_anomaly_days=len(list((self.root / 'audit' / 'membership_count_anomalies').glob('*.json'))),
                     **self.current)
        common.atomic_json(self.root / 'progress.json', value)
        common.atomic_json(CONTROL / 'download_status.json', value)

    def collect_memberships(self, dates):
        membership = {}
        missing = [date for date in dates if not (self.root / 'memberships' / (date + '.json.gz')).exists()]
        for date in dates:
            if date in missing:
                position = missing.index(date)
                for upcoming in missing[position:position + self.args.concurrency]:
                    self.queue_api('THS_DP', ['index', f'{upcoming};000905.SH', 'date:Y,thscode:Y'])
            self.status('memberships', membership_date=date)
            membership[date] = set(self.membership(date))
        return membership

    @staticmethod
    def group_id(group):
        return hashlib.sha256(json.dumps(dict(group=group, params=PARAMS, fields=AVAILABLE), sort_keys=True).encode()).hexdigest()[:20]

    def quote_args(self, group):
        return [','.join(group['codes']), ';'.join(AVAILABLE), PARAMS, group['start'], group['end']]

    def download_month(self, month, dates):
        members = self.collect_memberships(dates)
        groups = interval_groups(dates, members, self.args.batch_size)
        pending = []
        for group in groups:
            batch = self.group_id(group)
            row = self.db.execute('SELECT status,path,sha256 FROM batches WHERE date=? AND batch=?', (month, batch)).fetchone()
            if row and not (self.args.retry_gaps and row[0] in ['empty', 'partial']):
                if row[1]:
                    path = self.root / row[1]
                    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != row[2]:
                        raise ValueError(f'Existing file verification failed: {path}')
                continue
            pending.append(group)
        for index, group in enumerate(pending):
            for upcoming in pending[index:index + self.args.concurrency]:
                self.queue_api('THS_HQ', self.quote_args(upcoming))
            self.status('daily_quotes', month=month, group_index=index + 1, pending_groups=len(pending),
                        query_start=group['start'], query_end=group['end'], codes_in_request=len(group['codes']))
            response = self.api('THS_HQ', self.quote_args(group))
            frame = daily_frame(response, group, members)
            if frame.empty:
                response = self.api('THS_HQ', self.quote_args(group))
                frame = daily_frame(response, group, members)
            expected = {(code, date) for code in group['codes'] for date in group['dates']}
            actual = set(zip(frame.thscode, frame.date))
            missing_pairs = sorted(expected - actual)
            nulls = {field: int(frame[field].isna().sum()) for field in FIELDS}
            detail = dict(api_parameters=PARAMS, requested=group, request_metrics=response.get('request_metrics'),
                          dataVol=response.get('dataVol'), expected_observations=len(expected),
                          missing_observations=missing_pairs, null_counts=nulls,
                          unavailable_fields=['sellVolume', 'buyVolume'],
                          note='Missing observations may be suspension or missing provider history. No synthetic rows or prices.')
            batch = self.group_id(group)
            path = None
            if len(frame):
                path = self.root / 'bars' / month[:4] / month / (batch + '.parquet')
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix('.tmp.parquet')
                frame.to_parquet(tmp, index=False, compression='zstd')
                if not pd.read_parquet(tmp).equals(frame):
                    raise ValueError('Daily Parquet round-trip failed')
                tmp.replace(path)
            state = 'empty' if frame.empty else 'partial' if missing_pairs or any(nulls[f] for f in AVAILABLE) else 'downloaded'
            self.record(month, batch, state, frame, path, detail)
            self.status('daily_quotes', month=month, group_index=index + 1)
        all_paths = [self.root / row[0] for row in self.db.execute('SELECT path FROM batches WHERE date=? AND path!=?', (month, '')).fetchall()]
        if all_paths:
            frame = pd.concat([pd.read_parquet(path) for path in all_paths], ignore_index=True)
            if frame.duplicated(['thscode', 'date']).any():
                raise ValueError('Duplicate observations across monthly groups')
            if any(code not in members.get(date, set()) for code, date in zip(frame.thscode, frame.date)):
                raise ValueError('Monthly membership validation failed')
        common.atomic_json(self.root / 'audit' / f'{month}_membership_intervals.json', groups)

    def run(self):
        if not self.args.allow_missing_inout:
            raise ValueError('Daily THS_HQ omits sellVolume and buyVolume; explicit missing-field policy is required')
        metadata = dict(index='000905.SH', frequency='daily', requested_start=self.args.start,
                        requested_end=self.args.end, adjustment='CPS:3 / 后复权(分红再投)',
                        adjustment_base='上市首日 / baseDate:1900-01-01', api_parameters=PARAMS,
                        fields=dict(zip(FIELDS, common.LABELS)), available_fields=AVAILABLE,
                        unavailable_fields={'sellVolume': 'THS_HQ omitted this field in 2010 and 2026 probes; NULL',
                                            'buyVolume': 'THS_HQ omitted this field in 2010 and 2026 probes; NULL'},
                        constituent_rule='Daily iFinD historical sets; only request continuous membership intervals',
                        membership_count_policy='500 normally; 498..502 deviations require a matching fresh re-query, are audited, and remain flagged for independent review. Other deviations stop.',
                        null_policy='User accepts 8 available fields with inner/outer volume columns explicitly null',
                        timezone='Asia/Shanghai', fill='Omit: never forward-fill non-trading observations',
                        documentation='https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html')
        path = self.root / 'dataset.json'
        if path.exists():
            old = json.loads(path.read_text(encoding='utf-8'))
            for key in ['index', 'frequency', 'requested_start', 'requested_end', 'adjustment', 'adjustment_base', 'api_parameters', 'available_fields']:
                if old.get(key) != metadata[key]:
                    raise ValueError(f'Dataset configuration mismatch: {key}')
        common.atomic_json(path, metadata)
        calendar = self.root / 'audit' / 'trading_calendar.json'
        if calendar.exists():
            raw = json.loads(calendar.read_text(encoding='utf-8'))
        else:
            self.status('calendar')
            raw = self.api('THS_Date_Query', ['SSE', 'dateType:0,period:D,dateFormat:YYYY-MM-DD', self.args.start, self.args.end], 'format:dict')
            common.atomic_json(calendar, raw)
        dates = [date for date in raw['data']['time'] if self.args.start <= date <= self.args.end]
        if not dates or dates != sorted(set(dates)):
            raise ValueError('Invalid trading calendar')
        months = defaultdict(list)
        for date in dates:
            months[date[:7]].append(date)
        self.current.update(total_trading_days=len(dates), total_months=len(months))
        completed_days = 0
        for index, month in enumerate(sorted(months, reverse=True)):
            self.status('month_start', month=month, month_index=index + 1)
            self.download_month(month, months[month])
            completed_days += len(months[month])
            self.status('month_done', completed_months=index + 1, completed_trading_days=completed_days)
        gaps = self.db.execute("SELECT COUNT(*) FROM batches WHERE status!='downloaded'").fetchone()[0]
        anomalies = list((self.root / 'audit' / 'membership_count_anomalies').glob('*.json'))
        self.status('finished_with_gaps' if gaps or anomalies else 'finished')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=r'E:\中证500_历年成分股_日频_2010-2026_iFinD_后复权')
    parser.add_argument('--start', default='2010-01-01')
    parser.add_argument('--end', default='2026-09-24')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--concurrency', type=int, default=12)
    parser.add_argument('--retry-gaps', action='store_true')
    parser.add_argument('--allow-missing-inout', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500 or not 1 <= args.concurrency <= 12:
        raise ValueError('Invalid batch size or concurrency')
    if args.end > datetime.now().strftime('%Y-%m-%d'):
        raise ValueError('Future date requested')
    job = DailyDownloader(args)
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
