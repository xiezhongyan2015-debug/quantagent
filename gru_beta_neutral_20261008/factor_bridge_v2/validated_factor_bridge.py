"""Explicit, verified factor-history override for this isolated v2 only."""
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np

SOURCE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock/old_code_factor_identity_20261009'
PRIMARY = SOURCE.parent / 'union_daily_factors/normalized'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def verified_inputs():
    receipt_path = SOURCE / 'bridge_validation_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['research_bridge_symbols'] == ['001914.SZ', '302132.SZ']
    assert receipt['pending_bridge_symbols'] == ['001872.SZ']
    assert not receipt['bridge_applied_to_panel']
    validation = ROOT / 'gru_beta_neutral_20261008/validate_old_code_factor_bridge.py'
    assert sha(validation) == receipt['source_code_sha256']
    event_path = SOURCE / 'canonical_observed_fore_factor_events.parquet'
    assert sha(event_path) == receipt['factor_event_parquet_sha256']
    frame = pq.read_table(event_path).to_pandas()
    events, records = {}, {}
    for record in receipt['records']:
        symbol = record['symbol']
        if not record['prefix_admitted_to_research_bridge']:
            continue
        for field in ['open', 'high', 'low', 'close', 'preclose', 'tradestatus', 'adjustflag']:
            assert record['raw_daily_field_mismatches'][field] == 0
        assert record['raw_daily_trading_volume_mismatches'] == 0
        old_name = record['historical_code'].replace('.', '')
        assert sha(SOURCE / f'{old_name}_factors.json') == record['historical_factor_source_sha256']
        assert sha(SOURCE / f'{old_name}_raw_daily.json') == record['historical_raw_daily_source_sha256']
        assert sha(Path(record['official_statement_path'])) == record['official_statement_sha256']
        assert sha(PRIMARY / f'{symbol}.json') == record['primary_source_sha256']
        selected = frame.loc[frame.symbol.eq(symbol)].sort_values('effective_date')
        assert not selected.effective_date.duplicated().any()
        dates = selected.effective_date.to_numpy(str)
        values = selected.effective_fore_factor.to_numpy(float)
        assert np.isfinite(values).all() and (values > 0).all()
        assert dates[0] < '2017-01-01'
        primary = json.loads((PRIMARY / f'{symbol}.json').read_text())
        by_date = dict(zip(dates, values))
        for row in primary['factors']:
            assert by_date[row[1]] == float(row[2])
        prefix = selected.source_kind.eq('historical_code_observed_event')
        assert prefix.sum() == record['prefix_observed_factor_events']
        assert (selected.loc[prefix, 'effective_date'] < record['first_canonical_factor_date']).all()
        # Historical event ratios are unchanged by the documented constant scale.
        original = selected.loc[prefix, 'observed_fore_factor'].to_numpy(float)
        effective = selected.loc[prefix, 'effective_fore_factor'].to_numpy(float)
        np.testing.assert_allclose(effective[1:] / effective[:-1], original[1:] / original[:-1],
                                   rtol=2e-15, atol=2e-15)
        events[symbol] = (dates, values)
        records[symbol] = record
    assert set(events) == set(receipt['research_bridge_symbols'])
    return events, records, {
        'bridge_validation_receipt': str(receipt_path), 'bridge_validation_receipt_sha256': sha(receipt_path),
        'factor_event_parquet_sha256': sha(event_path), 'bridge_module_sha256': sha(Path(__file__)),
        'applied_symbols': sorted(events), 'source_records': records,
        'price_observations_modified': False, 'synthetic_minute_bars': False,
        'factor_unit_normalization': receipt['normalization'],
        'point_in_time_semantics': receipt['point_in_time_semantics'],
    }


def override_factor_state(obj, dates):
    events, _, _ = verified_inputs()
    symbol = obj['symbol']
    if symbol not in events:
        return None
    event_dates, event_values = events[symbol]
    queries = np.asarray(dates, dtype=str)
    positions = np.searchsorted(event_dates, queries, side='right') - 1
    values = np.full(len(queries), np.nan)
    known = positions >= 0
    values[known] = event_values[positions[known]]
    assert (event_dates[positions[known]] <= queries[known]).all()
    return values


def provenance(symbol):
    _, records, contract = verified_inputs()
    if symbol is None:
        return contract
    if symbol not in records:
        return None
    return {**{k: v for k, v in contract.items() if k != 'source_records'}, 'source_record': records[symbol]}


def validate_asof():
    events, _, contract = verified_inputs()
    for symbol, (days, values) in events.items():
        primary = json.loads((PRIMARY / f'{symbol}.json').read_text())
        output = override_factor_state(primary, days)
        np.testing.assert_array_equal(output, values)
        cutoff = '2018-01-01'
        queries = np.array(['2017-01-03', '2017-06-01', '2017-12-29'])
        positions = np.searchsorted(days[days <= cutoff], queries, side='right') - 1
        expected = values[days <= cutoff][positions]
        np.testing.assert_array_equal(override_factor_state(primary, queries), expected)
        unknown = override_factor_state(primary, ['1900-01-01'])
        assert np.isnan(unknown).all()
    return {'passed': True, 'event_date_asof_checks': True, 'relative_event_ratios_preserved': True,
            'known_canonical_factor_values_preserved_exactly': True, 'future_event_append_invariance': True,
            'unknown_pre_first_observation_stays_missing': True, 'source_contract': contract}


if __name__ == '__main__':
    print(json.dumps(validate_asof(), ensure_ascii=False))
