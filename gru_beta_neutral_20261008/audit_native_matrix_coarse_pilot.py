"""Use actual public 5m records as references, never as expanded minute data."""
import fcntl
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from public_baostock_probe import PublicClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009/native_matrix_pilot'
PRICE = ['open', 'high', 'low', 'close']
GRID = list(range(571, 691)) + list(range(781, 901))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'native_matrix_coarse_diagnostics.json'; assert not output.exists()
    native_path = OUT / 'actual_minimum_gap_pilot_with_verified_alias_diagnostics.json'
    native = json.loads(native_path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_native_matrix_verified_alias.py') == native['source_code_sha256']
    acquisition = json.loads((OUT / 'bounded_column_acquisition.json').read_text())
    aliases = json.loads((OUT / 'bounded_verified_alias_column_acquisition.json').read_text())
    fields, indices = {}, None
    for record, alias in zip(acquisition['results'], aliases['results']):
        assert record['field'] == alias['field']
        assert sha(record['selection_path']) == record['selection_sha256'] and sha(alias['selection_path']) == alias['selection_sha256']
        table = pq.read_table(record['selection_path']); historical = pq.read_table(alias['selection_path'])
        dates = table['__index_level_0__'].to_pylist()
        if indices is None:
            indices = dates
        else:
            assert dates == indices
        fields[record['field']] = {'600985.SH': table['600985'].to_pylist(), '302132.SZ': historical['300114'].to_pylist()}
    by_date = {}
    for i, stamp in enumerate(indices):
        by_date.setdefault(stamp.strftime('%Y-%m-%d'), []).append(i)
    old_path = BASE / 'public_source_probes/additional_public_archives_20261009/wind17/target_300114_2023_coarse_concordance/coarse_concordance_receipt.json'
    old = json.loads(old_path.read_text())
    references = {}; pinned_queries = []
    for metadata in old['queries']:
        wire = Path(metadata['wire_path']); assert sha(wire) == metadata['sha256']
        full_path = wire.with_suffix('.json'); query = json.loads(full_path.read_text())
        assert query['sha256'] == metadata['sha256'] and query['frequency'] == '5' and query['adjustflag'] == '3'
        data_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
        for row in data_rows:
            record = dict(zip(query['fields'], row)); assert record['code'] == 'sz.300114'
            clock = int(record['time'][8:10]) * 60 + int(record['time'][10:12])
            key = '302132.SZ', record['date'], clock; assert key not in references
            references[key] = {field: Decimal(record[field]) for field in PRICE + ['volume']}
        pinned_queries.append({'path': str(full_path), 'sha256': sha(full_path), 'wire_path': str(wire), 'wire_sha256': sha(wire)})
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(__file__),
               'native_pilot_receipt_sha256': sha(native_path), 'old_coarse_receipt_sha256': sha(old_path),
               'retained_query_references': pinned_queries, 'new_coarse_query_budget': 1,
               'candidate_hours_admitted': 0, 'candidate_changed': False, 'coarse_bars_expanded_to_minutes': False,
               'new_query_status': 'not_started', 'results': []}
    client = None; lock = (ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ref_root = OUT / 'coarse_reference_20190416'; ref_root.mkdir(exist_ok=True)
        client = PublicClient(ref_root); client.login()
        query = client.query('sh.600985', '2019-04-16', '2019-04-16', '5')
        assert query['error_code'] == '0'
        wire = Path(query['wire_path']); full_path = wire.with_suffix('.json')
        receipt['retained_query_references'].append({'path': str(full_path), 'sha256': sha(full_path),
                                                    'wire_path': str(wire), 'wire_sha256': sha(wire)})
        data_rows = query['data'].get('record', []) if isinstance(query['data'], dict) else query['data']
        for row in data_rows:
            r = dict(zip(query['fields'], row)); clock = int(r['time'][8:10]) * 60 + int(r['time'][10:12])
            references['600985.SH', r['date'], clock] = {field: Decimal(r[field]) for field in PRICE + ['volume']}
        receipt['new_query_status'] = 'response_received'
    except Exception as error:
        receipt.update(new_query_status='failed_terminal', error_type=type(error).__name__)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                receipt['logout_error_type'] = type(error).__name__
        lock.close()
    checked, strict, not_checked = 0, 0, 0
    for target in native['results']:
        for slot in target['native_amount_pass_slots']:
            symbol, date = target['symbol'], target['date']
            end_clocks = GRID[slot * 60 + 4:(slot + 1) * 60:5]
            record = {'symbol': symbol, 'date': date, 'slot': slot}
            if not all((symbol, date, clock) in references for clock in end_clocks):
                record['status'] = 'independent_coarse_not_available'; not_checked += 1
            else:
                buckets = []
                for bucket, clock in enumerate(end_clocks):
                    selected = by_date[date][slot * 60 + bucket * 5:slot * 60 + bucket * 5 + 5]
                    bars = [{field: Decimal(str(fields[field][symbol][i])) for field in fields} for i in selected]
                    aggregate = {'open': bars[0]['open'], 'high': max(r['high'] for r in bars),
                                 'low': min(r['low'] for r in bars), 'close': bars[-1]['close'],
                                 'volume': sum(r['volume'] for r in bars)}
                    reference = references[symbol, date, clock]
                    flags = {'open_exact': aggregate['open'] == reference['open'], 'close_exact': aggregate['close'] == reference['close'],
                             'range_inside_reference': aggregate['high'] <= reference['high'] and aggregate['low'] >= reference['low'],
                             'volume_within_100': abs(aggregate['volume'] - reference['volume']) <= 100}
                    buckets.append({'end_clock': clock, **flags, 'all_passed': all(flags.values()),
                                    'native_ohlcv': {f: str(aggregate[f]) for f in aggregate},
                                    'reference_ohlcv': {f: str(reference[f]) for f in reference}})
                passed = all(r['all_passed'] for r in buckets); checked += 1; strict += passed
                record.update(status='native_hour_coarse_diagnostic_complete', all_12_buckets_passed=passed, buckets=buckets)
            receipt['results'].append(record)
    assert len(receipt['results']) == native['native_amount_pass_hours'] == 56
    receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(), native_eligible_hours=56,
                   native_eligible_hours_coarse_checked=checked, native_eligible_hours_coarse_not_checked=not_checked,
                   strict_coarse_diagnostic_hours=strict, source_precision_certified=False, whole_LFS_SHA256_verified=False,
                   production_ready=False)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('results', 'retained_query_references')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
