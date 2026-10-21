"""Independent integer/Decimal verification of source coverage and sample bars."""
import csv
import hashlib
import json
import sys
from collections import Counter
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
OUT = BASE / 'public_source_probes/phields_trades_20261009'
PRICE = ['open', 'high', 'low', 'close']
GRID = list(range(570, 690)) + list(range(780, 900))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def make_bars(rows, model, width):
    bars = {}
    for row in rows:
        second = row['time_s']
        if not (second == 33900 or 34200 <= second <= 41400 or 46800 <= second <= 54000):
            continue
        minute = second // 60 if model == 'floor_second_left_interval' else (second - 1) // 60
        minute = {33900: 570, 34200: 570, 41400: 689, 46800: 780, 54000: 899}.get(second, minute)
        assert minute in GRID
        bucket = minute // width * width
        price = Decimal(row['price_x10000']) / 10000
        if bucket not in bars:
            bars[bucket] = {'open': price, 'high': price, 'low': price, 'close': price,
                            'volume': row['volume'], 'observed_trade_count': 1}
        else:
            bar = bars[bucket]
            bar.update(high=max(bar['high'], price), low=min(bar['low'], price), close=price,
                       volume=bar['volume'] + row['volume'], observed_trade_count=bar['observed_trade_count'] + 1)
    return bars


def metrics(a, b):
    common = sorted(set(a) & set(b))
    counts = {field: 0 for field in PRICE}
    exact_volume, tolerant_volume, good, differences, failed = 0, 0, 0, [], []
    for clock in common:
        left, right = a[clock], b[clock]
        eq = {field: abs(left[field] - right[field]) <= Decimal('0.00000001') for field in PRICE}
        for field in PRICE:
            counts[field] += eq[field]
        difference = int(left['volume']) - int(right['volume'])
        differences.append(difference); exact_volume += difference == 0; tolerant_volume += abs(difference) <= 100
        passed = (eq['open'] and eq['close'] and left['high'] <= right['high'] + Decimal('0.00000001') and
                  left['low'] >= right['low'] - Decimal('0.00000001') and abs(difference) <= 100)
        good += passed
        if not passed:
            failed.append(clock)
    return {'common_observed_intervals': len(common), 'exact_price_intervals_by_field': counts,
            'volume_exact_intervals': exact_volume, 'volume_within_100_intervals': tolerant_volume,
            'open_close_range_volume_diagnostic_pass_intervals': good, 'volume_difference_shares': sum(differences),
            'maximum_absolute_volume_difference_shares': max(map(abs, differences)),
            'unobserved_reference_intervals': sorted(set(b) - set(a)), 'failed_common_interval_left_labels': failed}


def main():
    output = OUT / 'independent_phields_findings_verification.json'
    assert not output.exists()
    audit_path = OUT / 'actual_trade_sample_diagnostics.json'
    audit = json.loads(audit_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_phields_trade_samples.py') == audit['source_code_sha256']
    coverage_path = OUT / 'exact_tail_metadata_coverage_receipt.json'
    coverage = json.loads(coverage_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_phields_tail_metadata_coverage.py') == coverage['source_code_sha256']
    info = json.loads((OUT / 'dataset_info_blobs.json').read_text())
    published = {r['rfilename']: r for r in info['siblings']}
    assert info['sha'] == audit['pinned_revision'] == coverage['pinned_revision']
    for filename, local in [('metadata/ticker_calendar.parquet', 'metadata_ticker_calendar.parquet'),
                            ('metadata/daily_manifest.parquet', 'metadata_daily_manifest.parquet')]:
        assert sha(OUT / local) == published[filename]['lfs']['sha256']
    schema_bytes = (OUT / 'metadata_schema.json').read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(schema_bytes)).encode() + b'\0' + schema_bytes).hexdigest() == published['metadata/schema.json']['blobId']
    inventory = {}
    for row in pq.read_table(OUT / 'metadata_ticker_calendar.parquet').to_pylist():
        key = row['ticker'], row['trade_date']
        assert key not in inventory and row['code_prefix'] == row['ticker'][:2]
        inventory[key] = row
    assert len(inventory) == coverage['source_ticker_calendar_rows'] == 639177
    candidate = BASE / audit['current_candidate'] / 'dataset'
    meta_path = candidate / 'meta.json'; meta = json.loads(meta_path.read_text())
    assert sha(meta_path) == audit['candidate_meta_sha256'] == coverage['candidate_meta_sha256']
    assert meta['data_fingerprint'] == audit['candidate_fingerprint']
    for filename in ['members.npy', 'hour_complete.npy']:
        assert sha(candidate / filename) == meta['files_sha256'][filename]
    complete = np.load(candidate / 'hour_complete.npy', mmap_mode='r')
    members = np.load(candidate / 'members.npy', mmap_mode='r')
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    assert sha(calendar_path) == audit['local_calendar_sha256']
    calendar = json.loads(calendar_path.read_text()); columns = {s: i for i, s in enumerate(calendar['union_symbols'])}
    dates = {d: i for i, d in enumerate(calendar['dates'])}
    gap_path = BASE / audit['current_candidate'] / 'gap_audit/missing_stockdays.parquet'
    assert sha(gap_path) == coverage['current_gap_stockdays_sha256']
    targets = {}
    for row in pq.read_table(gap_path).to_pylist():
        if row['date'] > '2026-08-06' and row['primary_tradestatus'] == '1' and row['primary_volume'] > 0:
            start, col = dates[row['date']] * 4, columns[row['symbol']]
            mask = ~members[start:start + 4, col] & ~complete[start:start + 4, col]
            if mask.any():
                assert int(mask.sum()) == row['missing_hours'] - row['current_member_hours']
                targets[row['symbol'], row['date']] = int(mask.sum())
    assert len(targets) == coverage['target_nonmember_stockdays'] == 21215 and sum(targets.values()) == 84860
    csv_path = Path(coverage['coverage_csv_path']); assert sha(csv_path) == coverage['coverage_csv_sha256']
    observed = list(csv.DictReader(csv_path.open()))
    assert len(observed) == len(targets) and len({(r['symbol'], r['date']) for r in observed}) == len(targets)
    absent_dates = Counter(); present_days = present_hours = missing_hours = 0
    for row in observed:
        key = row['symbol'], row['date']; count = targets[key]
        assert count == int(row['actual_missing_nonmember_hours'])
        source = inventory.get((row['symbol'].split('.')[0], row['date']))
        assert (row['source_inventory_record_present'] == 'True') == (source is not None)
        if source is not None:
            present_days += 1; present_hours += count
            for field in ['row_count', 'min_time_s', 'max_time_s']:
                assert int(row['source_inventory_' + field]) == source[field]
        else:
            missing_hours += count; absent_dates[row['date']] += 1
    assert present_days == coverage['target_stockdays_present_in_source_inventory'] == 21079
    assert present_hours == coverage['target_missing_hours_with_source_inventory_record'] == 84316
    assert missing_hours == coverage['target_missing_hours_without_source_inventory_record'] == 544
    assert dict(absent_dates) == coverage['source_missing_target_dates'] == {'2026-08-12': 136}
    raw_by_ticker = {}
    for payload in audit['whole_payloads']:
        source_path = Path(payload['source_path'])
        assert sha(source_path) == payload['whole_source_sha256']
        filename = next(r['filename'] for r in json.loads((OUT / 'verified_whole_sample_acquisition.json').read_text())['results'] if r['path'] == str(source_path))
        assert sha(source_path) == published[filename]['lfs']['sha256'] and source_path.stat().st_size == published[filename]['size']
        assert sha(payload['selection_path']) == payload['selection_sha256']
        codes = ['000006', '000099'] if 'code_prefix=00/' in filename else ['600004']
        parquet = pq.ParquetFile(source_path); selected = []
        independent_groups = []
        for i in range(parquet.num_row_groups):
            stats = parquet.metadata.row_group(i).column(0).statistics
            if any(stats.min <= code <= stats.max for code in codes):
                independent_groups.append(i)
                for batch in parquet.iter_batches(batch_size=131072, row_groups=[i]):
                    table = pa.Table.from_batches([batch])
                    filtered = table.filter(pc.is_in(table['ticker'], value_set=pa.array(codes)))
                    if filtered.num_rows:
                        selected.append(filtered)
        assert independent_groups == [r['row_group'] for r in payload['row_groups_scanned']]
        table = pa.concat_tables(selected)
        assert table.equals(pq.read_table(payload['selection_path']))
        for row in table.to_pylist():
            raw_by_ticker.setdefault(row['ticker'], []).append(row)
    checks = []
    for result in audit['results']:
        code = result['symbol'].split('.')[0]
        rows = raw_by_ticker[code]; assert len(rows) == result['source_rows'] == inventory[code, result['date']]['row_count']
        assert rows == sorted(rows, key=lambda r: (r['time_s'], r['tran_id']))
        assert len({r['tran_id'] for r in rows}) == len(rows)
        assert all(r['price_x10000'] > 0 and r['volume'] > 0 and r['price_x10000'] % 100 == 0 for r in rows)
        normal = [r for r in rows if r['time_s'] == 33900 or 34200 <= r['time_s'] <= 41400 or 46800 <= r['time_s'] <= 54000]
        outside = [r for r in rows if not (r['time_s'] == 33900 or 34200 <= r['time_s'] <= 41400 or 46800 <= r['time_s'] <= 54000)]
        assert outside == result['observed_outside_regular_session_trades']
        assert sum(r['volume'] for r in rows) == result['raw_all_trades_volume']
        assert sum(r['volume'] for r in normal) == result['regular_trades_volume']
        amount = sum(Decimal(r['price_x10000']) * r['volume'] / 10000 for r in normal)
        assert amount == Decimal(result['regular_trade_price_times_volume_amount'])
        assert sha(result['primary_daily_path']) == result['primary_daily_sha256']
        primary = json.loads(Path(result['primary_daily_path']).read_text())
        day = next(dict(zip(primary['fields'], r)) for r in primary['rows'] if r[0] == result['date'])
        prices = [normal[0]['price_x10000'], max(r['price_x10000'] for r in normal),
                  min(r['price_x10000'] for r in normal), normal[-1]['price_x10000']]
        assert all(Decimal(value) / 10000 == Decimal(day[field]) for value, field in zip(prices, PRICE))
        assert amount == Decimal(day['amount']) and sum(r['volume'] for r in normal) == int(day['volume'])
        assert all(result['regular_tick_daily_checks'].values())
        coarse_record = next(r for r in audit['references'] if ('sz.' + code in r['path'] or code == '600004' and 'sh.600004_' in r['path']))
        assert sha(coarse_record['path']) == coarse_record['sha256'] and sha(coarse_record['wire_path']) == coarse_record['wire_sha256']
        query = json.loads(Path(coarse_record['path']).read_text())
        source_rows = query['data']['record']
        reference = {}
        for data in source_rows:
            r = dict(zip(query['fields'], data)); clock = int(r['time'][8:10]) * 60 + int(r['time'][10:12]) - 5
            reference[clock] = {f: Decimal(r[f]) for f in PRICE} | {'volume': int(r['volume'])}
        assert len(reference) == 48 and sorted(reference) == GRID[::5]
        original = None
        if code == '600004':
            opath = next(r for r in audit['references'] if r.get('kind') == 'immutable_original_positive_control')
            assert sha(opath['path']) == opath['sha256'] == '37b70c488c89a37e4c57d6cbef088719854d159a324a929430f2e10b794b57ca'
            original = {}
            for r in pq.read_table(opath['path']).to_pylist():
                clock = int(r['datetime'][11:13]) * 60 + int(r['datetime'][14:16])
                original[clock] = {f: Decimal(str(r[f])) for f in PRICE} | {'volume': r['volume']}
            assert sorted(original) == GRID
            assert len(outside) == 2 and sum(r['volume'] for r in outside) == 1900
            assert all(r['time_s'] == 55171 and r['price_x10000'] == 76300 for r in outside)
        diagnostic_hours_passed = 0
        for model in result['minute_clock_models']:
            minutes = make_bars(rows, model['clock_diagnostic_model'], 1)
            five = make_bars(rows, model['clock_diagnostic_model'], 5)
            assert len(minutes) == model['observed_nonempty_minutes']
            assert sorted(set(GRID) - set(minutes)) == model['unobserved_trade_minute_left_labels']
            assert sha(model['derived_minute_path']) == model['derived_minute_sha256']
            for r in pq.read_table(model['derived_minute_path']).to_pylist():
                expected = minutes.pop(r['bucket'])
                for field in PRICE:
                    assert Decimal(str(r[field])) == expected[field]
                assert r['volume'] == expected['volume'] and r['observed_trade_count'] == expected['observed_trade_count']
            assert not minutes
            minutes = make_bars(rows, model['clock_diagnostic_model'], 1)
            assert metrics(five, reference) == model['five_minute_vs_independent_reference']
            if original is not None:
                assert metrics(minutes, original) == model['minute_vs_original_positive_control']
                assert {str(i): original[i]['volume'] for i in set(GRID) - set(minutes)} == model['original_volume_at_unobserved_trade_minutes']
            for hour in model['hours']:
                grid = GRID[hour['slot'] * 60:(hour['slot'] + 1) * 60]
                assert sum(i in minutes for i in grid) == hour['observed_nonempty_trade_minutes']
                comparison = metrics({i: five[i] for i in grid[::5]}, {i: reference[i] for i in grid[::5]})
                assert comparison == hour['five_minute_reference_diagnostic']
                passed = comparison['open_close_range_volume_diagnostic_pass_intervals'] == 12
                assert passed == hour['all_12_coarse_diagnostic_buckets_passed']
                diagnostic_hours_passed += passed
        assert diagnostic_hours_passed == 0
        start, col = dates[result['date']] * 4, columns[result['symbol']]
        assert complete[start:start + 4, col].tolist() == result['current_candidate_complete_slots']
        assert members[start:start + 4, col].tolist() == result['current_candidate_member_slots']
        checks.append({'symbol': result['symbol'], 'source_trade_rows': len(rows), 'regular_daily_all_six_checks_exact': True,
                       'candidate_member_slots': result['current_candidate_member_slots'],
                       'diagnostic_hours_with_all_12_coarse_buckets_passed_across_both_clock_models': 0})
    reference_acquisition_path = OUT / 'sample_references/bounded_reference_acquisition.json'
    reference_acquisition = json.loads(reference_acquisition_path.read_text())
    for r in reference_acquisition['requests']:
        assert r['status'] == 'retained_complete_HTTP_response' and sha(r['path']) == r['sha256']
        if 'extracted_text_path' in r:
            assert sha(r['extracted_text_path']) == r['extracted_text_sha256']
    rule_text = (OUT / 'sample_references/sse_2026_rules.txt').read_text()
    deferred = (OUT / 'sample_references/sse_2026_deferred_rules.txt').read_text()
    assert '3.7.1' in rule_text and '15:05至15:30' in rule_text and '3.7.10' in rule_text
    assert '3.7.' not in deferred
    depth = json.loads((OUT / 'sample_references/depth_info.json').read_text())
    depth_dates = sorted({r['rfilename'].split('trade_date=')[1].split('/')[0] for r in depth['siblings'] if 'trade_date=' in r['rfilename']})
    assert depth_dates[-1] == '2026-07-30'
    assert not audit['candidate_changed'] and audit['candidate_hours_admitted'] == 0
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(__file__), 'audited_sample_receipt_sha256': sha(audit_path),
               'audited_coverage_receipt_sha256': sha(coverage_path), 'pinned_revision': info['sha'],
               'candidate_meta_sha256': sha(meta_path), 'candidate_fingerprint': meta['data_fingerprint'],
               'exact_missing_tail_stockdays_verified': len(targets), 'exact_missing_tail_hours_verified': sum(targets.values()),
               'source_inventory_present_stockdays_verified': present_days, 'source_inventory_present_hours_verified': present_hours,
               'source_inventory_absent_hours_verified': missing_hours, 'absent_dates': dict(absent_dates),
               'two_whole_sample_files_published_LFS_SHA256_freshly_reproduced': True,
               'sample_source_rows_independently_rescanned_and_matched': True, 'checks': checks,
               'sse_2026_regular_and_after_close_sessions_checked_against_official_rule_and_deferred_attachment': True,
               'after_close_rows_temporally_and_price_consistent_with_current_fixed_price_rules_transaction_type_not_independently_certified': True,
               'companion_market_depth_last_actual_partition': depth_dates[-1],
               'companion_market_depth_revision': depth['sha'], 'source_millisecond_trade_clock_certified': False,
               'minute_source_precision_certified': False, 'full_tail_trade_payload_coverage_certified': False,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
               'method': 'Fresh whole published-file hashes, independent batched raw row selection, exact bitmap-derived tail intersection and Python integer/Decimal grouping; no producer module imports and no missing-price fill.'}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
