"""Bounded trade-to-minute observations with fixed, globally applied clocks.

All native rows count toward inventory checks. Off-session trades stay in the
raw subsets. No prices, quantities or empty-minute bars are manufactured.
The two clocks are diagnostics until one passes a predeclared control gate.
"""
import argparse
from collections import defaultdict
from decimal import Decimal
from . import bounded_tail_pipeline as p

MODELS = ('closed_right', 'floor_left')
PRICE = ('open', 'high', 'low', 'close')
GRID = list(range(570, 690)) + list(range(780, 900))


def labels(seconds, model):
    """Fixed 2s phase-end allowance, preserving native clock separately."""
    regular = ((seconds >= 33900) & (seconds <= 33902)) | ((seconds >= 34200) & (seconds <= 41402)) | ((seconds >= 46800) & (seconds <= 54002))
    minute = seconds // 60 if model == 'floor_left' else (seconds - 1) // 60
    minute = p.np.where((seconds >= 33900) & (seconds <= 34200), 570, minute)
    minute = p.np.where((seconds >= 41400) & (seconds <= 41402), 689, minute)
    minute = p.np.where(seconds == 46800, 780, minute)
    minute = p.np.where((seconds >= 54000) & (seconds <= 54002), 899, minute)
    return regular, minute


class Aggregate:
    def __init__(self):
        self.bars = {model: {} for model in MODELS}
        self.raw = {}
        self.batches = 0
        self.max_batch_rows = 0

    def consume(self, batch):
        self.batches += 1
        self.max_batch_rows = max(self.max_batch_rows, len(batch))
        f = batch.to_pandas()
        for ticker, one in f.groupby('ticker', sort=False):
            ids = one.tran_id.to_numpy()
            a = self.raw.setdefault(ticker, dict(count=0, minimum_time=2**31, maximum_time=-1,
                                                last_id=-1, strictly_increasing_ids=True, positive=True))
            a['count'] += len(one)
            a['minimum_time'] = min(a['minimum_time'], int(one.time_s.min()))
            a['maximum_time'] = max(a['maximum_time'], int(one.time_s.max()))
            a['strictly_increasing_ids'] &= bool((p.np.diff(ids) > 0).all() and ids[0] > a['last_id'])
            a['last_id'] = int(ids[-1])
            a['positive'] &= bool((one.price_x10000 > 0).all() and (one.volume > 0).all())
        # Source integer products/sums must fit int64 before vector aggregation.
        max_price = int(f.price_x10000.abs().max()) if len(f) else 0
        max_qty = int(f.volume.abs().max()) if len(f) else 0
        assert max_price * max_qty * len(f) < 2**63
        f['amount_x10000'] = f.price_x10000 * f.volume
        f = f.sort_values(['time_s', 'tran_id'], kind='stable')
        for model in MODELS:
            mask, minute = labels(f.time_s.to_numpy(), model)
            work = f.loc[mask].copy()
            work['minute'] = minute[mask]
            assert work.minute.isin(GRID).all()
            summary = work.groupby(['ticker', 'minute'], sort=False).agg(
                open=('price_x10000', 'first'), high=('price_x10000', 'max'), low=('price_x10000', 'min'), close=('price_x10000', 'last'),
                volume=('volume', 'sum'), amount_x10000=('amount_x10000', 'sum'), trades=('tran_id', 'size'),
                first_ts=('time_s', 'first'), first_id=('tran_id', 'first'), last_ts=('time_s', 'last'), last_id=('tran_id', 'last'))
            cache = self.bars[model]
            for key, row in summary.iterrows():
                b = {k: int(v) for k, v in row.items()}
                if key not in cache:
                    cache[key] = b
                    continue
                a = cache[key]
                if (b['first_ts'], b['first_id']) < (a['first_ts'], a['first_id']):
                    a.update(open=b['open'], first_ts=b['first_ts'], first_id=b['first_id'])
                if (b['last_ts'], b['last_id']) > (a['last_ts'], a['last_id']):
                    a.update(close=b['close'], last_ts=b['last_ts'], last_id=b['last_id'])
                a['high'] = max(a['high'], b['high'])
                a['low'] = min(a['low'], b['low'])
                for k in ('volume', 'amount_x10000', 'trades'):
                    a[k] += b[k]

    def frame(self, model, date):
        rows = [dict(ticker=code, date=date, left_minute=int(minute), **bar)
                for (code, minute), bar in sorted(self.bars[model].items())]
        return p.pd.DataFrame(rows)


def references(wanted):
    """Scan inventory once and retain only requested keys; read each daily once."""
    keys = {(date, code) for (date, prefix), group in wanted.items() for code in group}
    calendar = {}
    pf = p.pq.ParquetFile(p.SOURCE / 'metadata_ticker_calendar.parquet')
    tickers = p.pa.array(sorted({k[1] for k in keys}))
    for batch in pf.iter_batches(batch_size=p.BATCH_ROWS, use_threads=False):
        table = p.pa.Table.from_batches([batch])
        table = table.filter(p.pc.is_in(table['ticker'], value_set=tickers))
        for r in table.to_pylist():
            if (r['trade_date'], r['ticker']) in keys:
                calendar[r['trade_date'], r['ticker']] = r
    dates = defaultdict(set)
    for (date, prefix), group in wanted.items():
        for code, target in group.items():
            dates[target['symbol']].add(date)
    daily = {}
    for symbol, days in dates.items():
        file = p.BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
        obj = p.json.loads(file.read_text())
        digest = p.sha(file)
        for row in obj['rows']:
            if row[0] in days:
                r = dict(zip(obj['fields'], row))
                assert r['adjustflag'] == '3'
                values = [int(Decimal(r[k]) * 10000) for k in PRICE] + [int(r['volume']), int(Decimal(r['amount']) * 10000)]
                daily[row[0], symbol[:6]] = dict(values=values, sha256=digest, path=str(file), tradestatus=r['tradestatus'])
    return calendar, daily


def validate_stockdays(aggregate, date, group, calendar, daily):
    rows = []
    # The two models must preserve identical unsplit session totals.
    for code, target in group.items():
        raw = aggregate.raw.get(code)
        inv = calendar.get((date, code))
        ref = daily.get((date, code))
        item = dict(symbol=target['symbol'], date=date, actual_source_rows=raw['count'] if raw else 0,
                    inventory_rows=inv['row_count'] if inv else None, source_inventory_present=inv is not None,
                    source_inventory_exact=bool(raw and inv and raw['count'] == inv['row_count'] and
                        [raw['minimum_time'], raw['maximum_time']] == [inv['min_time_s'], inv['max_time_s']]),
                    raw_positive=bool(raw and raw['positive']), unique_transaction_order_certified=bool(raw and raw['strictly_increasing_ids']),
                    primary_daily_reference=ref, candidate_hours_admitted=0, clocks={})
        for model in MODELS:
            bars = [(minute, bar) for (ticker, minute), bar in aggregate.bars[model].items() if ticker == code]
            bars.sort()
            actual = [bars[0][1]['open'], max(b['high'] for _, b in bars), min(b['low'] for _, b in bars), bars[-1][1]['close'],
                      sum(b['volume'] for _, b in bars), sum(b['amount_x10000'] for _, b in bars)] if bars else None
            checks = [a == b for a, b in zip(actual, ref['values'])] if actual and ref else [False] * 6
            item['clocks'][model] = dict(observed_minutes=len(bars), unobserved_minutes=sorted(set(GRID) - {m for m, _ in bars}),
                    daily_actual=actual, daily_six_fields_exact=all(checks), daily_field_exact=dict(zip((*PRICE, 'volume', 'calculated_amount'), checks)),
                    independent_minute_concordance_passed=False, independent_coarse_concordance_passed=False,
                    source_seconds_do_not_certify_nominal_end_PIT=True)
        assert item['clocks'][MODELS[0]]['daily_actual'] == item['clocks'][MODELS[1]]['daily_actual']
        rows.append(item)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit-groups', type=int)
    parser.add_argument('--date')
    args = parser.parse_args()
    process_lock = p.locked('minutes')
    wanted = p.targets()
    calendar, daily = references(wanted)
    op = p.OUT / 'minute_observation_progress.json'
    d = p.json.loads(op.read_text()) if op.exists() else dict(started_utc=p.utc(), source_code_sha256=p.sha(__file__),
        fixed_auction_phase_end_grace_seconds=2, clock_models=list(MODELS), batch_rows=p.BATCH_ROWS,
        requested_stockdays=21215, requested_tail_hours=84860, candidate_hours_admitted=0, groups=[], status='observing')
    done = {(r['date'], r['prefix']) for r in d['groups']}
    processed = 0
    while True:
        acq = p.json.loads((p.OUT / 'acquisition.json').read_text())
        extraction_path = p.OUT / 'extraction.json'
        if not extraction_path.exists():
            p.time.sleep(5)
            continue
        extracted = p.json.loads(extraction_path.read_text())
        parts = {r['filename']: r for r in extracted['partitions']}
        groups = defaultdict(list)
        for r in acq['records']:
            groups[r['date'], r['prefix']].append(r)
        for key, records in sorted(groups.items()):
            if key in done or (args.date and key[0] != args.date) or not all(r['filename'] in parts for r in records):
                continue
            aggregate = Aggregate()
            for r in records:
                part = parts[r['filename']]
                file = p.Path(part['selected_native_column_subset_path'])
                assert p.identity(file) == part['verified_identity']
                pf = p.pq.ParquetFile(file)
                assert pf.metadata.num_rows == part['selected_rows']
                for batch in pf.iter_batches(batch_size=p.BATCH_ROWS, columns=p.CORE, use_threads=False):
                    aggregate.consume(batch)
            directory = p.OUT / 'minute_observations' / ('trade_date=' + key[0]) / ('code_prefix=' + key[1])
            directory.mkdir(parents=True, exist_ok=True)
            files = []
            for model in MODELS:
                file = directory / (model + '.parquet')
                frame = aggregate.frame(model, key[0])
                temp = file.with_suffix('.parquet.tmp')
                p.pq.write_table(p.pa.Table.from_pandas(frame, preserve_index=False), temp, compression='zstd')
                temp.replace(file)
                files.append(dict(model=model, path=str(file), sha256=p.sha(file), observed_minutes=len(frame)))
            stockdays = validate_stockdays(aggregate, key[0], wanted[key], calendar, daily)
            report_path = directory / 'stockday_validation.json'
            p.atomic_json(report_path, dict(source_parts=[parts[r['filename']] for r in records],
                    source_code_sha256=p.sha(__file__), stockdays=stockdays, minute_files=files,
                    rule='2s phase-end grace fixed for every stockday; both clocks diagnostic; no empty-minute fill', candidate_hours_admitted=0))
            group = dict(date=key[0], prefix=key[1], stockdays=len(stockdays), native_rows=sum(r['actual_source_rows'] for r in stockdays),
                    inventory_exact_stockdays=sum(r['source_inventory_exact'] for r in stockdays),
                    daily_six_fields_exact_stockdays=sum(r['clocks'][MODELS[0]]['daily_six_fields_exact'] for r in stockdays),
                    transaction_order_certified_stockdays=sum(r['unique_transaction_order_certified'] for r in stockdays),
                    max_batch_rows=aggregate.max_batch_rows, batches=aggregate.batches, validation_path=str(report_path),
                    validation_sha256=p.sha(report_path), minute_files=files)
            d['groups'].append(group)
            done.add(key)
            d.update(updated_utc=p.utc(), processed_groups=len(done), processed_stockdays=sum(r['stockdays'] for r in d['groups']),
                     peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
            p.atomic_json(op, d)
            del aggregate
            processed += 1
            if args.limit_groups and processed >= args.limit_groups:
                process_lock.close()
                return
        if acq['status'] in ('complete', 'incomplete_preserved') and extracted['status'] in ('complete', 'all_available_complete_parts_processed'):
            d['status'] = 'observations_complete_pending_independent_concordance' if len(done) == len(groups) else 'available_groups_observed'
            p.atomic_json(op, d)
            process_lock.close()
            return
        p.time.sleep(5)


if __name__ == '__main__':
    main()
