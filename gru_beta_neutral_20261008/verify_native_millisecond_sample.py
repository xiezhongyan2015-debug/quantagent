"""Cross-check actual ms events against every whole-file-backed trade sample."""
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

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
OUT = BASE / 'public_source_probes/additional_native_inventory_20261009'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    output = OUT / 'native_millisecond_trade_join_verification.json'; assert not output.exists()
    acquisition_path = OUT / 'bounded_native_millisecond_sample_acquisition.json'
    acquisition = json.loads(acquisition_path.read_text())
    assert acquisition['status'] == 'both_target_members_complete'
    assert sha(ROOT / 'gru_beta_neutral_20261008/extract_native_millisecond_event_sample.py') == acquisition['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_new_archive_structure.py') == acquisition['range_helper_sha256']
    for range_record in [acquisition['initial_range'], acquisition['extension_range']]:
        assert sha(range_record['path']) == range_record['retained_range_sha256'] and range_record['http_status'] == 206
    for member in acquisition['selected_complete_members']:
        assert sha(member['path']) == member['sha256']
    event_path = OUT / '000006_20260923_events.parquet'
    events = pq.read_table(event_path)
    assert events.schema.metadata[b'event_time_unit'] == b'millisecond'
    assert events.schema.metadata[b'qty_scale'] == b'1' and events.schema.metadata[b'price_scale'] == b'10000'
    trades = events.filter(pc.equal(events['RecordType'], 2))
    physical_ms = trades['EventTime'].cast(pa.int32()).to_pylist()
    rows = trades.to_pylist()
    by_id = {r['EventSeqNo']: (r, ms) for r, ms in zip(rows, physical_ms)}
    assert len(by_id) == len(rows) == 22282
    older_path = BASE / 'public_source_probes/phields_trades_20261009/actual_trade_sample_diagnostics.json'
    older = json.loads(older_path.read_text())
    payload = next(r for r in older['whole_payloads'] if '_00_' in r['source_path'])
    assert sha(payload['source_path']) == payload['whole_source_sha256']
    assert sha(payload['selection_path']) == payload['selection_sha256']
    table = pq.read_table(payload['selection_path'])
    seconds_rows = table.filter(pc.equal(table['ticker'], '000006')).to_pylist()
    assert {r['tran_id'] for r in seconds_rows} == set(by_id)
    floor_matches = nonzero_ms = exact_price_qty = 0
    minute_volumes_ms, minute_volumes_seconds = Counter(), Counter()
    for raw in seconds_rows:
        event, milliseconds = by_id[raw['tran_id']]
        assert event['Price'] == raw['price_x10000'] and event['Qty'] == raw['volume']
        exact_price_qty += 1
        assert milliseconds // 1000 == raw['time_s']
        floor_matches += 1; nonzero_ms += milliseconds % 1000 != 0
        ms_minute = milliseconds // 60000; second_minute = raw['time_s'] // 60
        for special, target in [(33900000, 570), (41400000, 689), (54000000, 899)]:
            if milliseconds == special:
                ms_minute = target
        second_minute = {33900: 570, 41400: 689, 54000: 899}.get(raw['time_s'], second_minute)
        minute_volumes_ms[ms_minute] += event['Qty']; minute_volumes_seconds[second_minute] += raw['volume']
    assert minute_volumes_ms == minute_volumes_seconds
    daily_path = BASE / 'public_source_probes/baostock/union_daily_factors/normalized/000006.SZ.json'
    obj = json.loads(daily_path.read_text()); day = next(dict(zip(obj['fields'], r)) for r in obj['rows'] if r[0] == '2026-09-23')
    assert sum(r['Qty'] for r in rows) == int(day['volume'])
    amount = sum(Decimal(r['Price']) * r['Qty'] / 10000 for r in rows)
    assert amount == Decimal(day['amount'])
    snapshot_path = OUT / '000006_20260923_snapshot.parquet'
    snapshots = pq.read_table(snapshot_path).to_pylist()
    assert len(snapshots) == 4413 and all(r['SecurityID'] == '000006' for r in snapshots)
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
              'acquisition_receipt_sha256': sha(acquisition_path), 'pinned_event_archive_revision': acquisition['pinned_revision'],
              'event_member_sha256': sha(event_path), 'snapshot_member_sha256': sha(snapshot_path),
              'whole_file_backed_seconds_audit_sha256': sha(older_path), 'primary_daily_sha256': sha(daily_path),
              'symbol': '000006.SZ', 'date': '2026-09-23', 'native_trade_rows': len(rows),
              'same_transaction_ids_and_exact_price_quantity': exact_price_qty,
              'actual_event_times_with_nonzero_milliseconds': nonzero_ms,
              'integer_second_field_is_exact_floor_of_actual_event_milliseconds': floor_matches,
              'floor_minute_volume_assignments_identical_between_ms_and_second_archives': True,
              'milliseconds_alone_did_not_resolve_existing_minute_and_coarse_concordance': True,
              'daily_actual_quantity_and_calculated_amount_exact': True, 'snapshot_native_rows': len(snapshots),
              'archive_sources_are_not_claimed_independent_trade_origins': True,
              'whole_event_archive_LFS_SHA256_verified': False, 'whole_event_gzip_CRC_verified': False,
              'original_quote_and_bar_definition_still_requires_verification': True,
              'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
              'method': 'Join every actual trade identifier, integer price and integer quantity; inspect physical millisecond times and independently count native minute quantities; retain all original source fields.'}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
