"""Pinned, corroborated raw delivery observations under official old codes."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
AUDIT = BASE / 'official_old_code_minute_audit_20261009'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def verified_inputs():
    receipt_path = AUDIT / 'receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'old_code_minutes_corroborated_for_isolated_candidate'
    assert not any(receipt[k] for k in ['raw_files_modified', 'raw_symbol_rewritten', 'synthetic_prices', 'original_frozen_panel_modified'])
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_official_old_code_minutes.py') == receipt['source_code_sha256']
    assert sha(ROOT / 'gru_research/minute_data.py') == receipt['original_aggregator_sha256']
    extraction = BASE / 'official_old_code_minute_delivery_20261009/receipt.json'
    assert sha(extraction) == receipt['source_extraction_receipt_sha256']
    identity = BASE / 'public_source_probes/baostock/old_code_factor_identity_20261009/bridge_validation_receipt.json'
    assert sha(identity) == receipt['identity_receipt_sha256']
    accepted = []
    for record in receipt['payloads']:
        assert sha(Path(record['path'])) == record['sha256']
        primary = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (record['canonical_symbol'] + '.json')
        assert sha(primary) == record['primary_daily_source_sha256']
        if record['admitted']:
            assert all(record[k] for k in ['source_valid', 'primary_trading', 'same_open', 'same_close',
                       'same_volume_within_100_shares', 'inside_primary_raw_price_range', 'factor_bridge_ready'])
            assert not record['failure_reasons'] and record['date'] < record['official_code_change_date']
            accepted.append(record)
    path = AUDIT / 'qualified_canonical_hour_rows.parquet'
    assert sha(path) == receipt['qualified_hour_parquet_sha256']
    frame = pq.read_table(path).to_pandas()
    assert not frame.duplicated(['symbol', 'signal_time']).any() and frame.hour_complete.all()
    allowed = {(r['canonical_symbol'], r['date'], r['sha256'], r['observed_symbol']) for r in accepted}
    assert all((r.symbol, r.date, r.source_payload_sha256, r.source_observed_symbol) in allowed for r in frame.itertuples())
    assert len(frame) == sum(r['qualified_complete_hours'] for r in receipt['symbol_counts'].values())
    contract = {'audit_receipt': str(receipt_path), 'audit_receipt_sha256': sha(receipt_path),
                'qualified_hour_parquet_sha256': sha(path), 'alias_module_sha256': sha(Path(__file__)),
                'symbol_counts': receipt['symbol_counts'], 'retrospective_daily_source_qa_only': True,
                'raw_source_bytes_and_declared_codes_preserved': True, 'synthetic_prices': False}
    return frame, contract


def incoming_for_symbol(symbol):
    frame, _ = verified_inputs()
    return frame.loc[frame.symbol.eq(symbol)].copy()


def provenance(symbol=None):
    _, contract = verified_inputs()
    if symbol is not None and symbol not in contract['symbol_counts']:
        return None
    return contract
