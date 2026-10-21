"""Exact native-bar diagnostics for actual historical gaps; no source admission."""
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
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/native_matrix_pilot'
PRICE = ['open', 'high', 'low', 'close']
CSV_PRICE = dict(zip(PRICE, ['开盘价', '最高价', '最低价', '收盘价']))
GRID = list(range(571, 691)) + list(range(781, 901))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'actual_minimum_gap_pilot_with_verified_alias_diagnostics.json'; assert not output.exists()
    acquisition_path = OUT / 'bounded_column_acquisition.json'; acquisition = json.loads(acquisition_path.read_text())
    assert acquisition['status'] == 'six_field_native_pilot_columns_acquired'
    assert sha(ROOT / 'gru_beta_neutral_20261008/acquire_native_matrix_pilot.py') == acquisition['source_code_sha256']
    fields, index = {}, None
    for record in acquisition['results']:
        for request in record['requests']:
            assert sha(request['path']) == request['retained_range_sha256']
        assert sha(record['selection_path']) == record['selection_sha256']
        table = pq.read_table(record['selection_path'])
        current = table['__index_level_0__']
        if index is None:
            index = current
        else:
            assert index.equals(current), 'All six native fields must have exactly identical timestamp rows'
        fields[record['field']] = {code: table[code].to_pylist() for code in acquisition['codes']}
    alias_path = OUT / 'bounded_verified_alias_column_acquisition.json'
    alias = json.loads(alias_path.read_text())
    assert alias['status'] == 'six_historical_code_native_columns_acquired'
    assert sha(ROOT / 'gru_beta_neutral_20261008/acquire_native_matrix_verified_alias.py') == alias['source_code_sha256']
    assert sha(alias['official_identity']['official_statement_path']) == alias['official_identity']['official_statement_sha256']
    assert alias['official_identity']['official_code_change_date'] == '2025-02-17'
    for record in alias['results']:
        assert sha(record['selection_path']) == record['selection_sha256']
        assert sha(record['request']['path']) == record['request']['retained_range_sha256']
        table = pq.read_table(record['selection_path'])
        assert table['__index_level_0__'].equals(index)
        fields[record['field']]['302132'] = table['300114'].to_pylist()
    stamps = index.to_pylist(); assert len(stamps) == len(set(stamps)) == 478800
    assert stamps == sorted(stamps)
    date_rows = {}
    for i, stamp in enumerate(stamps):
        date_rows.setdefault(stamp.strftime('%Y-%m-%d'), []).append(i)
    assert len(date_rows) == 1995
    for indices in date_rows.values():
        assert len(indices) == 240 and [stamps[i].hour * 60 + stamps[i].minute for i in indices] == GRID
    scope = BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation/every_actual_missing_hour_diagnostics.csv'
    source_rows = list(csv.DictReader(scope.open()))
    wanted = {}
    selected_stockyears = {'600167.SH': '2018', '600985.SH': '2019', '302132.SZ': '2023'}
    for r in source_rows:
        if r['symbol'] in selected_stockyears and r['date'].startswith(selected_stockyears[r['symbol']]):
            wanted.setdefault((r['symbol'], r['date']), {'slots': [], 'raw_path': r['source_raw_path'], 'raw_sha256': r['source_raw_sha256']})['slots'].append(int(r['slot']))
    assert len(wanted) == 155 and sum(len(r['slots']) for r in wanted.values()) == 620
    cached_csv = {}
    for target in wanted.values():
        path = Path(target['raw_path'])
        if str(path) not in cached_csv:
            assert sha(path) == target['raw_sha256']
            raw = list(csv.DictReader(path.read_text(encoding='gbk').splitlines()))
            days = {}
            for r in raw:
                days.setdefault(r['k线结束时间'][:10], []).append(r)
            cached_csv[str(path)] = days
    results, failures = [], Counter()
    native_pass_hours = daily_pass_hours = exact_old_source_days = exact_old_source_intervals = 0
    for (symbol, date), target in sorted(wanted.items()):
        code = symbol.split('.')[0]
        assert symbol != '302132.SZ' or date < alias['official_identity']['official_code_change_date']
        primary_path = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (symbol + '.json')
        obj = json.loads(primary_path.read_text()); primary = next(dict(zip(obj['fields'], r)) for r in obj['rows'] if r[0] == date)
        indices = date_rows.get(date, [])
        record = {'symbol': symbol, 'date': date, 'actual_missing_slots': target['slots'], 'native_timestamp_rows': len(indices),
                  'primary_daily_path': str(primary_path), 'primary_daily_sha256': sha(primary_path)}
        values = [{field: fields[field][code][i] for field in fields} for i in indices]
        nulls = sum(v is None for r in values for v in r.values())
        record['raw_null_fields_on_target_date'] = nulls
        if len(values) != 240 or nulls:
            record.update(status='target_date_absent_or_actual_native_null_fields', native_amount_pass_slots=[], daily_pass_slots=[])
            failures['missing_date_or_nulls'] += 1; results.append(record); continue
        bars = [{k: Decimal(str(v)) for k, v in r.items()} for r in values]
        flags = {
            'raw_price_and_quantity_fields_valid': all(all(r[k].is_finite() and r[k] > 0 and r[k] == r[k].quantize(Decimal('.01')) for k in PRICE)
                                                   and r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']
                                                   and r['volume'].is_finite() and r['volume'] >= 0 and r['volume'] == r['volume'].to_integral_value() for r in bars),
            'primary_day_trading': primary['tradestatus'] == '1' and Decimal(primary['volume']) > 0,
            'open_exact': bars[0]['open'] == Decimal(primary['open']), 'close_exact': bars[-1]['close'] == Decimal(primary['close']),
            'prices_inside_primary_daily_range': max(r['high'] for r in bars) <= Decimal(primary['high']) and min(r['low'] for r in bars) >= Decimal(primary['low']),
            'day_volume_within_100_shares': abs(sum(r['volume'] for r in bars) - Decimal(primary['volume'])) <= 100,
        }
        positive = [sum(r['volume'] for r in bars[s * 60:(s + 1) * 60]) > 0 for s in range(4)]
        amount_checks = [r['low'] * r['volume'] - 1 <= r['amount'] <= r['high'] * r['volume'] + 1 for r in bars]
        daily_slots = [s for s in target['slots'] if positive[s]] if all(flags.values()) else []
        native_slots = [s for s in daily_slots if all(amount_checks[s * 60:(s + 1) * 60])]
        daily_pass_hours += len(daily_slots); native_pass_hours += len(native_slots)
        for flag, passed in flags.items():
            if not passed:
                failures[flag] += 1
        old_rows = cached_csv[target['raw_path']][date]
        normalized, auction = {}, None
        for raw in old_rows:
            t = datetime.fromisoformat(raw['k线结束时间']); clock = t.hour * 60 + t.minute
            bar = {f: Decimal(raw[key]) for f, key in CSV_PRICE.items()}
            bar.update(volume=Decimal(raw['成交量']) * 100, amount=Decimal(raw['成交额']))
            if clock in GRID:
                assert clock not in normalized; normalized[clock] = bar
            elif clock == 570 and bar['volume'] > 0:
                assert auction is None; auction = bar
        assert sorted(normalized) == GRID
        if auction is not None:
            bar = normalized[571]
            bar.update(open=auction['open'], high=max(bar['high'], auction['high']), low=min(bar['low'], auction['low']),
                       volume=bar['volume'] + auction['volume'], amount=bar['amount'] + auction['amount'])
        equal_counts = {field: sum(left[field] == normalized[clock][field] for clock, left in zip(GRID, bars)) for field in fields}
        all_equal = all(n == 240 for n in equal_counts.values())
        exact_old_source_days += all_equal
        exact_old_source_intervals += sum(all(bar[field] == normalized[clock][field] for field in fields) for clock, bar in zip(GRID, bars))
        record.update(status='actual_native_target_date_diagnostics_complete', daily_flags=flags,
                      day_volume_difference_shares=str(sum(r['volume'] for r in bars) - Decimal(primary['volume'])),
                      minute_amount_conflicts=amount_checks.count(False), daily_pass_slots=daily_slots,
                      native_amount_pass_slots=native_slots, normalized_wind17_equal_minute_counts_by_field=equal_counts,
                      all_240_native_minutes_equal_normalized_wind17_in_all_six_fields=all_equal,
                      existing_source_path=target['raw_path'], existing_source_sha256=target['raw_sha256'])
        results.append(record)
    meta_path = BASE / 'backfilled_panel_candidate_canonical_ipo_v6/dataset/meta.json'
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'acquisition_receipt_sha256': sha(acquisition_path), 'pinned_revision': acquisition['pinned_revision'],
               'verified_historical_alias_acquisition_receipt_sha256': sha(alias_path),
               'historical_300114_column_used_only_for_302132_targets_before_official_2025_02_17_change': True,
               'exact_minimum_gap_scope_csv_sha256': sha(scope), 'candidate_meta_sha256': sha(meta_path),
               'native_timestamp_first': stamps[0].isoformat(), 'native_timestamp_last': stamps[-1].isoformat(),
               'native_timestamp_rows': len(stamps), 'native_stockdays_in_each_selected_field': len(date_rows),
               'all_six_native_fields_have_identical_unique_240_minute_date_index': True,
               'actual_target_stockdays': len(wanted), 'actual_missing_target_hours': 620,
               'daily_and_selected_positive_hour_pass_hours': daily_pass_hours, 'native_amount_pass_hours': native_pass_hours,
               'failed_daily_checks_stockdays': dict(failures),
               'all_six_fields_all_240_minutes_equal_existing_normalized_wind17_stockdays': exact_old_source_days,
               'all_six_fields_equal_existing_normalized_wind17_minutes': exact_old_source_intervals,
               'target_native_minutes_compared': 240 * len(wanted), 'results': results,
               'full_remote_field_files_LFS_SHA256_verified': False,
               'source_independence_or_minute_quantity_precision_certified': False,
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
               'method': 'Raw selected native doubles converted to Decimal without rounding or unit conversion; exact current-gap dates, primary daily checks and all-six-field comparison against retained independently audited wind17 CSVs after their explicit opening-auction normalization.'}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'results'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
