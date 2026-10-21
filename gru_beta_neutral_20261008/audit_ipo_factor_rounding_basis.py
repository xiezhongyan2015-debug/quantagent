"""Reproduce both factor conventions before proposing canonical-basis IPO seeds.

The factor values are calculated in the existing retrospective canonical price
basis; only their IPO effective dates come from the actual IPO observations.
Neither vendor's future factors replace the existing canonical event sequence.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
OUT = BASE / 'ipo_factor_rounding_method_20261009'
PRIMARY = BASE / 'baostock/union_daily_factors/normalized'
HF = BASE / 'huggingface'
GRID = np.r_[np.arange(571, 691), np.arange(781, 901)]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    sp = OUT / 'source_acquisition_receipt.json'
    output = OUT / 'canonical_basis_prefix_audit_receipt.json'
    assert not output.exists()
    sources = json.loads(sp.read_text())
    assert sources['completed_utc']
    assert sha(ROOT / 'gru_beta_neutral_20261008/pin_ipo_rounding_method_sources.py') == sources['source_code_sha256']
    inventory_path = HF / 'verified_union_file_inventory.csv'
    inventory = pd.read_csv(inventory_path).set_index('symbol')
    records, event_rows = [], []
    for symbol in ['688582.SH', '688709.SH']:
        ipo_doc = next(r for r in sources['records'] if r['symbol'] == symbol and r['kind'] == 'IPO')
        actions = next(r for r in sources['records'] if r['symbol'] == symbol and r['kind'] == 'provider_action_table')
        assert ipo_doc['status'] == actions['status'] == 'source_bytes_and_code_date_verified'
        for source in [ipo_doc, actions]:
            assert sha(source['path']) == source['sha256']
            assert sha(source['extracted_text_path']) == source['extracted_text_sha256']
        ipo = ipo_doc['expected_date']
        pp = PRIMARY / (symbol + '.json')
        obj = json.loads(pp.read_text())
        rows = {r[0]: dict(zip(obj['fields'], r)) for r in obj['rows']}
        days = sorted(rows)
        canonical = sorted((r[1], Decimal(r[2])) for r in obj['factors'])
        assert canonical[-1][1] == 1 and ipo < canonical[0][0]
        cash = {r['ex_date']: Decimal(r['cash_before_tax_per_10']) / 10 for r in actions['observed_implemented_actions']}
        assert all(Decimal(r['bonus_shares_per_10']) == 0 and Decimal(r['capital_transfer_per_10']) == 0 for r in actions['observed_implemented_actions'])
        assert set(cash) == {d for d, v in canonical[1:]}
        js = BASE / 'sina_ipo_factor_prefix_20261009' / (symbol[:6] + '.js')
        raw = js.read_text()
        source, _ = json.JSONDecoder().raw_decode(raw[raw.index('{'):])
        sina = sorted((r['d'], Decimal(r['f'])) for r in source['data'] if r['d'] != '1900-01-01')
        assert sina[0][0] == ipo and sina[-1][1] == 1
        assert [d for d, f in sina[1:]] == [d for d, f in canonical[1:]]
        rounded_product = exact_cash_product = Decimal(1)
        rounded_values = {canonical[-1][0]: Decimal(1)}
        exact_cash_divisors = {sina[-1][0]: Decimal(1)}
        steps = []
        for i in range(len(canonical)-1, 0, -1):
            date = canonical[i][0]
            previous_date = days[days.index(date)-1]
            previous_close = Decimal(rows[previous_date]['close'])
            observed_preclose = Decimal(rows[date]['preclose'])
            unrounded = previous_close - cash[date]
            assert unrounded.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) == observed_preclose
            rounded_step = observed_preclose / previous_close
            exact_step = unrounded / previous_close
            rounded_product *= rounded_step
            exact_cash_product *= exact_step
            rounded_values[canonical[i-1][0]] = rounded_product
            exact_cash_divisors[sina[i-1][0]] = 1 / exact_cash_product
            steps.append({'ex_date': date, 'previous_trading_date': previous_date,
                          'observed_previous_raw_close': str(previous_close), 'observed_raw_ex_date_preclose': str(observed_preclose),
                          'provider_table_cash_before_tax_per_share': str(cash[date]),
                          'unrounded_cash_reference_price': str(unrounded),
                          'rounded_reference_price_agrees_exactly': True,
                          'rounded_reference_ratio': str(rounded_step), 'unrounded_cash_reference_ratio': str(exact_step)})
        canonical_checks = [{'date': date, 'observed_factor': str(value), 'recomputed_factor': str(rounded_values[date]),
                             'six_decimal_reproduction_exact': rounded_values[date].quantize(Decimal('.000001'), rounding=ROUND_HALF_UP) == value}
                            for date, value in canonical]
        sina_checks = [{'date': date, 'observed_divisor': str(value), 'recomputed_cash_divisor': str(exact_cash_divisors[date]),
                        'absolute_difference': str(abs(value-exact_cash_divisors[date])),
                        'thirteen_decimal_reproduction_exact': exact_cash_divisors[date].quantize(Decimal('.0000000000001'), rounding=ROUND_HALF_UP) == value.quantize(Decimal('.0000000000001'), rounding=ROUND_HALF_UP)}
                       for date, value in sina]
        assert all(r['six_decimal_reproduction_exact'] for r in canonical_checks)
        assert all(r['thirteen_decimal_reproduction_exact'] for r in sina_checks)
        first = canonical[0][0]
        assert all(d > first for d in cash)
        prefix_dates = [d for d in days if ipo <= d < first and rows[d]['tradestatus'] == '1' and Decimal(rows[d]['volume']) > 0]
        assert prefix_dates == [ipo]
        factor = rounded_values[first].quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
        assert factor == canonical[0][1]
        mp = HF / 'snapshot' / inventory.loc[symbol, 'file']
        assert sha(mp) == inventory.loc[symbol, 'expected_lfs_sha256']
        minute = pq.read_table(mp, filters=[('timestamp', '>=', pd.Timestamp(ipo)), ('timestamp', '<', pd.Timestamp(ipo)+pd.Timedelta(days=1))]).to_pandas().sort_values('timestamp')
        assert minute.symbol.astype(str).eq(symbol[:6]).all() and minute.exchange.eq('SH').all()
        clocks = minute.timestamp.dt.hour.to_numpy()*60 + minute.timestamp.dt.minute.to_numpy()
        regular = minute[np.isin(clocks, GRID)]
        assert len(regular) == 240 and not minute.timestamp.duplicated().any() and minute.timestamp.dt.second.eq(0).all()
        assert np.array_equal(regular.timestamp.dt.hour.to_numpy()*60+regular.timestamp.dt.minute.to_numpy(), GRID)
        assert not ((clocks == 780) & minute.volume.gt(0)).any()
        q, volume = regular[['open', 'high', 'low', 'close']].to_numpy(float).round(2), regular.volume.to_numpy(float).copy()
        auction = minute[(clocks == 570) & minute.volume.gt(0)]
        assert len(auction) <= 1
        if len(auction):
            a = auction.iloc[0]
            q[0,0], q[0,1], q[0,2] = round(float(a.open),2), max(q[0,1], round(float(a.high),2)), min(q[0,2], round(float(a.low),2))
            volume[0] += float(a.volume)
        p = rows[ipo]
        gates = {'raw_source_valid': bool(np.isfinite(q).all() and (q > 0).all() and np.isfinite(volume).all() and (volume >= 0).all() and (q[:,1] >= q.max(1)).all() and (q[:,2] <= q.min(1)).all()),
                 'daily_open_exact': abs(q[0,0]-float(p['open'])) <= 1e-8,
                 'daily_close_exact': abs(q[-1,3]-float(p['close'])) <= 1e-8,
                 'daily_volume_within_100_shares': abs(volume.sum()-float(p['volume'])) <= 100,
                 'all_quotes_inside_primary_daily_range': bool(q[:,1].max() <= float(p['high'])+1e-8 and q[:,2].min() >= float(p['low'])-1e-8),
                 'all_four_observed_hours_positive_volume': bool((volume.reshape(4,60).sum(1) > 0).all())}
        gates = {k: bool(v) for k, v in gates.items()}
        assert all(gates.values()), (symbol, gates)
        records.append({'symbol': symbol, 'observed_IPO_seed_date': ipo, 'first_canonical_factor_date': first,
                        'primary_daily_factor_path': str(pp), 'primary_daily_factor_sha256': sha(pp),
                        'sina_factor_path': str(js), 'sina_factor_sha256': sha(js),
                        'IPO_primary_document_sha256': ipo_doc['sha256'], 'provider_action_table_sha256': actions['sha256'],
                        'canonical_factor_checks': canonical_checks, 'sina_cash_factor_checks': sina_checks,
                        'action_steps': sorted(steps, key=lambda r: r['ex_date']),
                        'no_observed_action_between_IPO_and_first_canonical_event': True,
                        'prefix_factor_in_existing_canonical_basis': str(factor),
                        'prefix_factor_value_is_calculated_not_directly_observed_at_IPO': True,
                        'factorless_trading_dates': prefix_dates, 'qualified_observed_minute_hours': 4,
                        'minute_source_path': str(mp), 'minute_source_sha256': sha(mp),
                        'minute_day_gate_checks': gates, 'source_native_volume_shares': float(volume.sum()),
                        'positive_0930_merged': bool(len(auction)),
                        'method_reason': 'Known primary factors reproduced using observed cent-rounded daily reference prices; vendor cash factors reproduced using unrounded cash amounts. Only the observed IPO date receives a calculated value in the unchanged canonical basis; all later canonical factors stay byte-for-byte numeric-identical.',
                        'all_implemented_company_actions_exchange_certified': False,
                        'first_dividend_primary_document_download_complete': False,
                        'applied_to_candidate': False})
        event_rows.append({'symbol': symbol, 'effective_date': ipo, 'effective_fore_factor': float(factor),
                           'source_kind': 'calculated_canonical_basis_on_observed_IPO_seed_date'})
        event_rows.extend({'symbol': symbol, 'effective_date': d, 'effective_fore_factor': float(v),
                           'source_kind': 'unchanged_canonical_observed_factor_event'} for d, v in canonical)
    ep = OUT / 'proposed_canonical_basis_IPO_events.parquet'
    assert not ep.exists()
    pd.DataFrame(event_rows).to_parquet(ep, index=False, compression='zstd')
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True, 'source_code_sha256': sha(__file__),
               'source_acquisition_receipt_sha256': sha(sp), 'minute_inventory_path': str(inventory_path),
               'minute_inventory_sha256': sha(inventory_path), 'records': records, 'proposed_event_path': str(ep), 'proposed_event_sha256': sha(ep),
               'qualified_prefix_hours': 8, 'all_ten_known_canonical_factor_values_reproduced_exactly': True,
               'all_ten_source_cash_factor_divisors_reproduced_at_thirteen_decimals': True,
               'known_canonical_factors_overwritten': False, 'generic_backward_fill_used': False,
               'effective_date_basis': 'Actual source IPO seed date, independently present in pinned issuer/exchange IPO documents',
               'factor_value_basis': 'Calculated existing retrospective canonical normalization; not claimed historically published at the IPO date',
               'candidate_changed': False, 'candidate_hours_admitted': 0, 'production_ready': False,
               'limitation': 'Mathematical and source-record concordance; full corporate-action issuer-history certification and the first-dividend primary-document download are still incomplete. Proposed prefix stays unapplied pending independent and source-completeness checks.'}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
