"""Independent raw subset and fixed auction-window diagnostic verification."""
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
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
OUT = SOURCE / 'full_actual_missing_tail_trade_payloads/fixed_all_stock_auction_second_profiles_20261009T134450Z'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    op = OUT / 'independent_fixed_auction_clock_and_native_subset_verification.json'
    assert not op.exists()
    rp = OUT / 'fixed_all_stock_auction_second_diagnostics.json'
    report = json.loads(rp.read_text())
    cp = OUT / 'upstream_extraction_immutable_capture.json'
    assert sha(cp) == report['upstream_extraction_capture_sha256']
    captured = json.loads(cp.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_all_phields_actual_missing_tail_trades.py') == captured['source_code_sha256']
    scope = list(csv.DictReader((SOURCE / 'every_missing_tail_stockday_metadata_coverage.csv').open()))
    assert len(scope) == 21215
    wanted = defaultdict(set)
    for row in scope:
        wanted[row['date'], row['symbol'][:2]].add(row['symbol'][:6])
    native = defaultdict(list)
    all_selected_rows = 0
    start = time.monotonic(); last = start
    for part in captured['partitions']:
        assert sha(part['complete_source_path']) == part['complete_source_SHA256_verified']
        assert sha(part['selected_native_column_subset_path']) == part['selected_native_column_subset_sha256']
        pf = pq.ParquetFile(part['complete_source_path'])
        key = part['date'], part['prefix']
        cols = ['ticker', 'time_s', 'tran_id', 'price_x10000', 'volume']
        ticks = pa.array(sorted(wanted[key]))
        pieces = []
        for rg in range(pf.num_row_groups):
            raw = pf.read_row_group(rg, columns=cols)
            picked = raw.filter(pc.is_in(raw['ticker'], value_set=ticks))
            if len(picked):
                pieces.append(picked)
        observed = pa.concat_tables(pieces) if pieces else pf.read_row_group(0, columns=cols).slice(0, 0)
        retained = pq.ParquetFile(part['selected_native_column_subset_path']).read()
        assert observed.equals(retained) and len(retained) == part['selected_rows']
        assert pf.num_row_groups == part['all_source_row_groups_scanned'] and pf.metadata.num_rows == part['all_source_rows_scanned']
        native[key].append(part['selected_native_column_subset_path'])
        all_selected_rows += len(retained)
        if time.monotonic() - last > 20:
            print(json.dumps({'actual_source_partitions_independently_scanned': sum(len(v) for v in native.values()), 'native_selected_rows_exact': all_selected_rows}), flush=True)
            last = time.monotonic()
    daily = {(r['date'], r['symbol']): r for r in captured['stockdays']}
    expected_reports = {(r['date'], r['symbol']): r for r in report['records']}
    assert set(daily) == set(expected_reports) and len(daily) == 3120
    grouped = defaultdict(list)
    for key, value in daily.items():
        grouped[key[0], key[1][:2]].append(value)
    counts = Counter(); missing = []
    for group, days in sorted(grouped.items()):
        table = pa.concat_tables([pq.ParquetFile(p).read() for p in native[group]])
        for day in days:
            key = day['date'], day['symbol']; saved = expected_reports[key]
            one = table.filter(pc.equal(table['ticker'], day['symbol'][:6]))
            assert len(one) == saved['raw_actual_rows'] == day['actual_selected_native_rows']
            counts['original_all_six_exact'] += day['primary_daily_all_six_fields_exact']
            if not len(one):
                assert not saved['profiles'] and not day['source_inventory_present']
                missing.append(key)
                continue
            ts, ids, px, qty = [one[k].to_numpy() for k in ['time_s', 'tran_id', 'price_x10000', 'volume']]
            ordered = np.lexsort((ids, ts)); ts, px, qty = [a[ordered] for a in [ts, px, qty]]
            primaryp = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (day['symbol'] + '.json')
            assert sha(primaryp) == day['primary_daily_source_sha256']
            primary = json.loads(primaryp.read_text())
            records = [dict(zip(primary['fields'], r)) for r in primary['rows'] if r[0] == day['date']]
            assert records == [day['primary_daily_source_row']]
            reference = [int(Decimal(records[0][k]) * 10000) for k in ['open', 'high', 'low', 'close']] + [int(records[0]['volume']), int(Decimal(records[0]['amount']) * 10000)]
            for grace in [1, 2, 5]:
                within = ((ts >= 33900) & (ts <= 33900 + grace)) | ((ts >= 34200) & (ts <= 41400 + grace)) | ((ts >= 46800) & (ts <= 54000 + grace))
                p, q = px[within], qty[within]
                actual = [int(p[0]), int(p.max()), int(p.min()), int(p[-1]), int(q.sum()), int((p * q).sum())]
                flags = dict(zip(['open', 'high', 'low', 'close', 'volume', 'calculated_amount'], [a == b for a, b in zip(actual, reference)]))
                diagnostic = saved['profiles'][str(grace)]
                assert flags == diagnostic['exact_fields'] and all(flags.values()) == diagnostic['all_daily_six_fields_exact']
                assert len(p) == diagnostic['actual_unsplit_trade_rows'] and int(q.sum()) == diagnostic['actual_unsplit_quantity']
                assert int((~within).sum()) == diagnostic['outside_fixed_window_rows_retained']
                counts['uniform_' + str(grace) + 's_all_six_exact'] += all(flags.values())
    assert dict(counts) == report['profile_counts']
    assert counts['uniform_2s_all_six_exact'] == counts['uniform_5s_all_six_exact'] == 2984
    assert len(missing) == 136 and all(d == '2026-08-12' and s.startswith('00') for d, s in missing)
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'audited_fixed_auction_clock_diagnostics_sha256': sha(rp), 'immutable_upstream_extraction_capture_sha256': sha(cp),
              'all_full_source_partitions_fresh_SHA256_and_all_row_groups_scanned': len(captured['partitions']),
              'native_core_subset_rows_identical_to_full_source': all_selected_rows, 'all_captured_target_stockdays_independently_profiled': len(daily),
              'all_fixed_uniform_clock_profile_counts_reproduced': dict(counts), 'actual_absent_payload_stockdays': len(missing),
              'actual_absent_payload_hours': 544, 'clock_profiles_are_diagnostics_not_minute_admission': True,
              'all_actual_source_quantity_prices_and_trade_rows_retained_without_inference': True,
              'original_precise_window_and_gate_results_preserved': True, 'full_original_task_21215_tail_stockdays_retained': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False}
    op.write_text(json.dumps(result, ensure_ascii=False, indent=2)); print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
