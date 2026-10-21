"""Pinned pre-auction closing-statistic concordance, without changing quotes."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCE = BASE / 'public_source_probes/sse_historical_close_rule_20261009'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


@lru_cache(maxsize=1)
def verified_inputs():
    path = SOURCE / 'audit_receipt_v2.json'
    receipt = json.loads(path.read_text())
    assert receipt['status'] == 'historical_SSE_closing_statistics_corroborated'
    assert not receipt['prior_candidate_modified'] and not receipt['raw_minute_close_replaced_by_daily_close']
    assert not receipt['synthetic_tradable_prices'] and not receipt['production_ready']
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_historical_sse_closing_semantics_v2.py') == receipt['source_code_sha256']
    assert sha(SOURCE / 'official_rule_receipt.json') == receipt['official_rule_receipt_sha256']
    rules = json.loads((SOURCE / 'official_rule_receipt.json').read_text())
    assert rules['new_stock_closing_auction_effective_date'] == '2018-08-20'
    for rule in rules['official_rule_sources']:
        assert sha(SOURCE / (rule['name'] + '.html')) == rule['raw_html_sha256']
        assert sha(SOURCE / (rule['name'] + '.txt')) == rule['text_sha256']
    for record in receipt['source_records']:
        primary = BASE / 'public_source_probes/baostock/union_daily_factors/normalized' / (record['symbol'] + '.json')
        assert sha(primary) == record['source_daily_factor_sha256']
        assert sha(SOURCE / 'symbol_audits_v2' / (record['symbol'] + '.parquet')) == record['new_audit_sha256']
    parquet = SOURCE / 'qualified_historical_closing_statistics_v2.parquet'
    assert sha(parquet) == receipt['qualified_parquet_sha256']
    frame = pq.read_table(parquet).to_pandas()
    assert len(frame) == receipt['qualified_stockdays'] and not frame.duplicated(['symbol', 'date']).any()
    assert frame.symbol.str.endswith('.SH').all() and frame.date.ge('2018-01-01').all() and frame.date.lt('2018-08-20').all()
    flags = ['minute_grid_valid', 'cent_rounding_unambiguous', 'all_minute_amounts_inside_independent_daily_range',
             'basic_turnover_valid', 'day_amount_reconciled', 'observed_volume_reproduces_prior_audit',
             'source_last_close_reproduces_prior_audit', 'closing_reference_matches_primary',
             'historical_close_reference_corroborated', 'no_source_price_overwritten']
    assert frame[flags].all().all() and not frame.tick_window_equivalence_certified.any()
    np.testing.assert_allclose(frame.rounded_final_minute_vwap_reference, frame.independent_daily_close, atol=1e-8, rtol=0)
    contract = {'historical_closing_audit_receipt': str(path), 'audit_receipt_sha256': sha(path),
                'qualified_parquet_sha256': sha(parquet), 'qualified_stockdays': len(frame),
                'official_rule_receipt_sha256': receipt['official_rule_receipt_sha256'], 'helper_module_sha256': sha(Path(__file__)),
                'period_scope': receipt['period_scope'], 'closing_reference_basis': receipt['closing_reference_basis'],
                'minute_price_basis': receipt['minute_price_basis'], 'limitations': receipt['limitations'],
                'retrospective_source_qa_only': True, 'raw_prices_overwritten': False, 'production_ready': False}
    return {symbol: data.set_index('date') for symbol, data in frame.groupby('symbol')}, contract


def matches(joined):
    sources, _ = verified_inputs()
    assert joined.symbol.nunique() == 1
    symbol = joined.symbol.iloc[0]
    if symbol not in sources:
        return np.zeros(len(joined), bool)
    observed = sources[symbol].reindex(joined.date)
    result = (observed.historical_close_reference_corroborated.eq(True).to_numpy()
              & (np.abs(observed.independent_daily_close.to_numpy(float) - joined.close.to_numpy(float)) <= 1e-8)
              & (np.abs(observed.source_last_trade_close.to_numpy(float) - joined.raw_close.to_numpy(float)) <= 1e-8)
              & joined.symbol.str.endswith('.SH').to_numpy()
              & joined.date.ge('2018-01-01').to_numpy() & joined.date.lt('2018-08-20').to_numpy())
    return result


def provenance(symbol=None):
    sources, contract = verified_inputs()
    if symbol is not None and symbol not in sources:
        return None
    return contract


def validate():
    sources, contract = verified_inputs()
    symbol = sorted(sources)[0]
    sample = sources[symbol].iloc[:4]
    joined = pd.DataFrame({'symbol': symbol, 'date': sample.index,
                           'close': sample.independent_daily_close.to_numpy(), 'raw_close': sample.source_last_trade_close.to_numpy()})
    assert matches(joined).all()
    altered = joined.copy();altered['close'] += .01
    assert not matches(altered).any()
    altered = joined.copy();altered['raw_close'] += .01
    assert not matches(altered).any()
    altered = joined.copy();altered['date'] = '2018-08-20'
    assert not matches(altered).any()
    altered = joined.copy();altered['symbol'] = '000034.SZ'
    assert not matches(altered).any()
    return {'passed': True, 'real_admitted_dates_match': True, 'altered_daily_or_minute_close_rejected': True,
            'cutoff_session_and_other_exchange_rejected': True, 'retrospective_source_contract': contract}


if __name__ == '__main__':
    print(json.dumps(validate(), ensure_ascii=False))
