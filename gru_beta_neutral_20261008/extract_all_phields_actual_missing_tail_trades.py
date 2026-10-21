"""Consume certified full-tail files and retain all requested real trade rows.

No bar expansion, quote/price filling, quantity inference or panel admission.
All source rows, including outside-session trades, remain in retained leaves.
"""
import csv
import fcntl
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/phields_trades_20261009'
ACQ = SOURCE / 'all_actual_missing_tail_partitions'
OUT = SOURCE / 'full_actual_missing_tail_trade_payloads'
CORE = ['ticker', 'time_s', 'tran_id', 'price_x10000', 'volume']


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def scale(value):
    v = Decimal(value) * 10000
    assert v == v.to_integral_value()
    return int(v)


def main():
    OUT.mkdir(exist_ok=True)
    lock = (OUT / 'single_extraction_process.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    op = OUT / 'full_actual_missing_tail_trade_payload_extraction.json'
    scopep = SOURCE / 'exact_tail_metadata_coverage_receipt.json'
    scope = json.loads(scopep.read_text())
    csvp = Path(scope['coverage_csv_path']); assert sha(csvp) == scope['coverage_csv_sha256']
    requested = list(csv.DictReader(csvp.open()))
    assert len(requested) == 21215 and sum(int(r['actual_missing_nonmember_hours']) for r in requested) == 84860
    targets = defaultdict(dict)
    for r in requested:
        targets[r['date'], r['symbol'][:2]][r['symbol'][:6]] = r
    assert len(targets) == 136
    ap = ACQ / 'all_actual_missing_tail_partition_acquisition.json'
    acquisition = json.loads(ap.read_text())
    assert acquisition['source_code_sha256'] == sha(ROOT / 'gru_beta_neutral_20261008/fetch_all_phields_missing_tail_partitions.py')
    assert acquisition['all_needed_published_partitions_requested'] == 226
    mp = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    expected_meta = '6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034'
    assert sha(mp) == expected_meta
    meta = pq.ParquetFile(SOURCE / 'metadata_ticker_calendar.parquet').read().to_pylist()
    source_calendar = {(r['trade_date'], r['ticker']): r for r in meta}
    if op.exists():
        receipt = json.loads(op.read_text()); assert receipt['source_code_sha256'] == sha(__file__)
    else:
        receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
                   'repository': 'phields/a-share-l2-trades', 'pinned_revision': acquisition['pinned_revision'],
                   'immutable_scope_receipt_sha256': sha(scopep), 'all_actual_missing_tail_stockdays_requested': 21215,
                   'all_actual_missing_tail_hours_requested': 84860, 'source_column_subset_retained': CORE,
                   'core_native_source_fields_not_transformed': True, 'outside_regular_session_rows_retained': True,
                   'candidate_meta_sha256': expected_meta, 'status': 'extracting_certified_partitions_as_they_arrive',
                   'partitions': [], 'stockdays': [], 'candidate_changed': False, 'candidate_hours_admitted': 0,
                   'production_ready': False}
    parts = {r['filename']: r for r in receipt['partitions']}
    stockdays = {(r['date'], r['symbol'][:6]): r for r in receipt['stockdays']}
    last_print = 0

    def persist():
        nonlocal last_print
        receipt.update(last_updated_utc=datetime.now(timezone.utc).isoformat(), complete_source_partitions_processed=len(parts),
                       native_actual_trade_rows_retained=sum(r['selected_rows'] for r in parts.values()),
                       requested_stockdays_all_published_parts_processed=len(stockdays),
                       exact_source_inventory_row_count_stockdays=sum(r['source_inventory_row_count_exact'] for r in stockdays.values()),
                       exact_primary_daily_all_OHLC_V_calculated_A_stockdays=sum(r['primary_daily_all_six_fields_exact'] for r in stockdays.values()))
        tmp = op.with_suffix('.json.tmp'); tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)); tmp.replace(op)
        if time.monotonic() - last_print > 20:
            print(json.dumps({k: receipt[k] for k in ['complete_source_partitions_processed', 'native_actual_trade_rows_retained', 'requested_stockdays_all_published_parts_processed', 'exact_primary_daily_all_OHLC_V_calculated_A_stockdays']}), flush=True)
            last_print = time.monotonic()

    persist()
    while True:
        acquisition = json.loads(ap.read_text())
        needed = defaultdict(list)
        for r in acquisition['records']:
            needed[r['date'], r['prefix']].append(r)
            if r['filename'] in parts or r['status'] != 'complete_whole_published_LFS_SHA256_and_Parquet_footer_verified':
                continue
            assert sha(r['path']) == r['published_LFS_SHA256']
            pf = pq.ParquetFile(r['path']); assert pf.metadata.num_rows == r['Parquet_footer_rows']
            tickers = pa.array(sorted(targets[r['date'], r['prefix']]))
            selected, scanned_rows = [], 0
            for rg in range(pf.num_row_groups):
                table = pf.read_row_group(rg, columns=CORE)
                scanned_rows += len(table)
                subset = table.filter(pc.is_in(table['ticker'], value_set=tickers))
                if len(subset):
                    selected.append(subset)
            assert scanned_rows == pf.metadata.num_rows
            table = pa.concat_tables(selected) if selected else pf.read_row_group(0, columns=CORE).slice(0, 0)
            directory = OUT / ('trade_date=' + r['date']) / ('code_prefix=' + r['prefix'])
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / Path(r['filename']).name
            assert not path.exists()
            pq.write_table(table, path, compression='zstd')
            record = {'filename': r['filename'], 'date': r['date'], 'prefix': r['prefix'],
                      'complete_source_path': r['path'], 'complete_source_SHA256_verified': r['published_LFS_SHA256'],
                      'all_source_row_groups_scanned': pf.num_row_groups, 'all_source_rows_scanned': scanned_rows,
                      'selected_native_column_subset_path': str(path), 'selected_native_column_subset_sha256': sha(path), 'selected_rows': len(table)}
            parts[r['filename']] = record; receipt['partitions'].append(record)
            persist()
        for group, records in needed.items():
            if not all(r['filename'] in parts for r in records):
                continue
            if all((group[0], ticker) in stockdays for ticker in targets[group]):
                continue
            tables = [pq.ParquetFile(parts[r['filename']]['selected_native_column_subset_path']).read() for r in records]
            table = pa.concat_tables(tables)
            for ticker, target in targets[group].items():
                key = group[0], ticker
                if key in stockdays:
                    continue
                one = table.filter(pc.equal(table['ticker'], ticker))
                expected = source_calendar.get(key)
                inventory_exact = expected is not None and len(one) == expected['row_count']
                comparison = {'open': False, 'high': False, 'low': False, 'close': False, 'volume': False, 'calculated_amount': False}
                result = {'symbol': target['symbol'], 'date': group[0], 'actual_missing_nonmember_hours_requested': int(target['actual_missing_nonmember_hours']),
                          'source_inventory_present': expected is not None, 'source_inventory_expected_rows': expected['row_count'] if expected else None,
                          'actual_selected_native_rows': len(one), 'source_inventory_row_count_exact': inventory_exact,
                          'all_published_prefix_parts_processed': True, 'source_partition_leaf_count': len(records),
                          'source_quantity_native_integer_not_inferred': True, 'source_seconds_are_not_millisecond_or_received_clock_certification': True}
                if len(one):
                    ts, ids, px, volume = [one[k].to_numpy() for k in CORE[1:]]
                    idx = np.lexsort((ids, ts)); ts, px, volume = [a[idx] for a in [ts, px, volume]]
                    regular = (ts == 33900) | ((ts >= 34200) & (ts <= 41400)) | ((ts >= 46800) & (ts <= 54000))
                    rt, rp, rv = ts[regular], px[regular], volume[regular]
                    result.update(native_price_quantity_positive=bool(np.all(px > 0) and np.all(volume > 0)),
                                  all_native_time_range=[int(ts.min()), int(ts.max())], actual_regular_rows=len(rt),
                                  outside_fixed_regular_session_rows=int((~regular).sum()),
                                  actual_regular_total_quantity=int(rv.sum()),
                                  calculated_regular_actual_amount=str(Decimal(int((rp * rv).sum())) / 10000))
                    if len(rt):
                        p = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (target['symbol'] + '.json')
                        primary = json.loads(p.read_text()); daily = [dict(zip(primary['fields'], row)) for row in primary['rows'] if row[0] == group[0]]
                        assert len(daily) == 1 and daily[0]['adjustflag'] == '3'
                        daily = daily[0]
                        actual = [int(rp[0]), int(rp.max()), int(rp.min()), int(rp[-1]), int(rv.sum()), int((rp * rv).sum())]
                        reference = [scale(daily[k]) for k in ['open', 'high', 'low', 'close']] + [int(daily['volume']), scale(daily['amount'])]
                        comparison = dict(zip(comparison, [x == y for x, y in zip(actual, reference)]))
                        result.update(primary_daily_source_sha256=sha(p), primary_daily_source_row=daily,
                                      actual_regular_OHLC_price_x10000=actual[:4], primary_daily_field_exact=comparison)
                    if expected:
                        result['source_inventory_time_range_exact'] = result['all_native_time_range'] == [expected['min_time_s'], expected['max_time_s']]
                result['primary_daily_all_six_fields_exact'] = all(comparison.values())
                stockdays[key] = result; receipt['stockdays'].append(result)
            persist()
        if acquisition['status'].startswith(('complete_', 'terminal_')):
            full = len(parts) == 226 and len(stockdays) == 21215
            receipt['status'] = 'complete_full_actual_missing_tail_native_payload_extraction' if full else 'terminal_incomplete_source_acquisition_all_available_certified_parts_processed'
            receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
            receipt['upstream_final_acquisition_sha256'] = sha(ap)
            receipt['full_tail_native_payload_extraction_complete'] = full
            assert sha(mp) == expected_meta
            persist()
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
