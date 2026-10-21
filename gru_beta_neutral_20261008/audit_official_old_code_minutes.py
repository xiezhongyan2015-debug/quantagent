"""Corroborate unchanged old-code delivery payloads before canonical admission.

Only derived rows receive the officially documented canonical symbol. All raw
files keep their original declared symbol, timestamps, prices, and file hashes.
The observed daily O/C, volume and price-range gate is the candidate's existing
gate, not a relaxation made for renamed securities.
"""
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / 'factor_bridge_v2'))
from validated_factor_bridge import override_factor_state, provenance
from gru_research.minute_data import aggregate_stock_day, MINUTE_OFFSETS

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'official_old_code_minute_delivery_20261009'
PRIMARY = BASE / 'public_source_probes/baostock/union_daily_factors/normalized'
IDENTITY = BASE / 'public_source_probes/baostock/old_code_factor_identity_20261009/bridge_validation_receipt.json'
OUT = BASE / 'official_old_code_minute_audit_20261009'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    extraction = json.loads((SOURCE / 'receipt.json').read_text())
    assert extraction['status'] == 'original_pre_rename_payload_extraction_complete'
    assert not extraction['original_source_modified'] and not extraction['prices_or_minutes_fabricated']
    assert sha(IDENTITY) == extraction['identity_receipt_sha256']
    assert extraction['source_parts'] == json.loads((ROOT / 'gru_research/artifacts/minute_source_fingerprints.json').read_text())['parts']
    records, hours, inputs = [], [], {}
    for record in extraction['payloads']:
        day, symbol, old = record['date'], record['canonical_symbol'], record['observed_symbol']
        assert day < record['official_code_change_date']
        proof = provenance(symbol)
        if symbol not in inputs:
            primary_path = PRIMARY / (symbol + '.json')
            obj = json.loads(primary_path.read_text())
            daily = pd.DataFrame(obj['rows'], columns=obj['fields']).set_index('date')
            for k in ['open', 'high', 'low', 'close', 'volume']:
                daily[k] = pd.to_numeric(daily[k], errors='coerce')
            inputs[symbol] = (obj, daily, sha(primary_path))
        obj, daily, primary_sha = inputs[symbol]
        payload_path = Path(record['path'])
        payload = payload_path.read_bytes()
        assert len(payload) == record['bytes'] and hashlib.sha256(payload).hexdigest() == record['sha256']
        table = pq.read_table(io.BytesIO(payload)).to_pandas().sort_values('datetime_nano')
        rows, audit = aggregate_stock_day((day, old, payload))
        midnight = pd.Timestamp(day, tz='Asia/Shanghai').tz_convert('UTC').value
        offsets = (table.datetime_nano.to_numpy(np.int64) - midnight) // (60 * 10**9)
        grid = len(table) == 240 and np.array_equal(offsets, MINUTE_OFFSETS)
        bad_fields = ['invalid_rows', 'duplicate_rows', 'duplicate_conflicts', 'outside_session_rows',
                      'timestamp_mismatch_rows', 'qfq_mismatch_rows']
        source_valid = grid and not audit['error'] and all(audit[k] == 0 for k in bad_fields)
        present = day in daily.index
        primary = daily.loc[day] if present else None
        observed = {'open': float(table.open.iloc[0]), 'high': float(table.high.max()),
                    'low': float(table.low.min()), 'close': float(table.close.iloc[-1]),
                    'volume': float(table.volume.sum())}
        trading = present and primary.tradestatus == '1' and primary.volume > 0
        same_open = present and abs(observed['open'] - primary.open) <= 1e-8
        same_close = present and abs(observed['close'] - primary.close) <= 1e-8
        same_volume = present and abs(observed['volume'] - primary.volume) <= 100
        inside = present and observed['high'] <= primary.high + 1e-8 and observed['low'] >= primary.low - 1e-8
        state = override_factor_state(obj, [day]) if proof is not None else None
        factor_ready = state is not None and np.isfinite(state[0]) and state[0] > 0
        admitted = bool(source_valid and trading and same_open and same_close and same_volume and inside and factor_ready)
        exact = bool(present and all(abs(observed[k] - primary[k]) <= 1e-8 for k in ['open', 'high', 'low', 'close']))
        failures = [name for name, ok in [('minute_grid_or_source_invalid', source_valid), ('primary_daily_not_trading', trading),
                    ('raw_open_conflict', same_open), ('raw_close_conflict', same_close), ('raw_volume_conflict', same_volume),
                    ('raw_price_outside_daily_range', inside), ('validated_factor_bridge_unavailable', factor_ready)] if not ok]
        records.append({**record, 'source_aggregator_audit': audit, 'raw_daily_observed': observed,
                        'primary_daily': {k: str(primary[k]) for k in ['open', 'high', 'low', 'close', 'volume', 'tradestatus']} if present else None,
                        'source_valid': bool(source_valid), 'primary_trading': bool(trading), 'same_open': bool(same_open),
                        'same_close': bool(same_close), 'same_volume_within_100_shares': bool(same_volume),
                        'inside_primary_raw_price_range': bool(inside), 'all_daily_ohlc_exact': exact,
                        'factor_bridge_ready': bool(factor_ready), 'admitted': admitted, 'failure_reasons': failures,
                        'primary_daily_source_sha256': primary_sha, 'complete_source_hours': sum(r['hour_complete'] for r in rows)})
        if admitted:
            assert len(rows) == 4
            for row in rows:
                if row['hour_complete']:
                    row.update({'symbol': symbol, 'source_observed_symbol': old, 'source_payload_sha256': record['sha256'],
                                'source_original_tar_member': record['original_tar_member'], 'source_observed_adjustment_factor': row['adjustment_factor'],
                                'source_official_code_change_date': record['official_code_change_date'], 'donor_daily_all_ohlc_exact': exact})
                    hours.append(row)
    frame = pd.DataFrame(hours)
    assert len(frame) and not frame.duplicated(['symbol', 'signal_time']).any()
    destination = OUT / 'qualified_canonical_hour_rows.parquet'
    frame.to_parquet(destination, index=False, compression='zstd')
    symbol_counts = {}
    for symbol in sorted(inputs):
        selected = [r for r in records if r['canonical_symbol'] == symbol]
        symbol_counts[symbol] = {'source_stockdays': len(selected), 'qualified_stockdays': sum(r['admitted'] for r in selected),
                                'qualified_complete_hours': int(frame.symbol.eq(symbol).sum()),
                                'failure_counts_overlapping': dict(Counter(k for r in selected for k in r['failure_reasons']))}
    receipt = {'status': 'old_code_minutes_corroborated_for_isolated_candidate', 'source_code_sha256': sha(Path(__file__)),
               'source_extraction_receipt_sha256': sha(SOURCE / 'receipt.json'), 'identity_receipt_sha256': sha(IDENTITY),
               'original_aggregator_sha256': sha(ROOT / 'gru_research/minute_data.py'), 'factor_bridge_contract': provenance(None),
               'qualified_hour_parquet_sha256': sha(destination), 'symbol_counts': symbol_counts, 'payloads': records,
               'raw_files_modified': False, 'raw_symbol_rewritten': False, 'synthetic_prices': False,
               'source_qa_is_retrospective': True, 'canonical_mapping_is_identity_dictionary_not_selection_signal': True,
               'original_frozen_panel_modified': False, 'panel_applied': False}
    (OUT / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ['payloads', 'factor_bridge_contract']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
