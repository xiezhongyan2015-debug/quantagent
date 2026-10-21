"""Validate official code continuity and observed factor prefixes, read-only.

Produces a separately versioned research factor bridge. It does not rewrite
vendor replies, the existing candidate, the frozen panel, or any model result.
Only observed corporate-action dates are carried forward; no market price is
filled. Fore-factor levels share the existing candidate's normalization basis.
"""
import hashlib
import json
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BAO = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock'
OUT = BAO / 'old_code_factor_identity_20261009'
SPECS = [
    ('sz000022', '001872.SZ', '2018-12-26', 'https://static.cninfo.com.cn/finalpage/2018-12-26/1205690369.PDF'),
    ('sz000043', '001914.SZ', '2019-12-16', 'https://static.cninfo.com.cn/finalpage/2019-12-16/1207164397.PDF'),
    ('sz300114', '302132.SZ', '2025-02-17', 'https://static.cninfo.com.cn/finalpage/2025-02-07/1222485220.PDF'),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    records, events = [], []
    for old, symbol, change_day, url in SPECS:
        statement = OUT / f'{symbol}_official_code_change.pdf'
        if not statement.exists():
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=25) as response:
                content = response.read(10 * 1024 * 1024 + 1)
            assert content.startswith(b'%PDF-') and len(content) <= 10 * 1024 * 1024
            statement.write_bytes(content)
        old_factor_path = OUT / f'{old}_factors.json'
        old_daily_path = OUT / f'{old}_raw_daily.json'
        primary_path = BAO / 'union_daily_factors/normalized' / f'{symbol}.json'
        old_f = json.loads(old_factor_path.read_text())
        old_d = json.loads(old_daily_path.read_text())
        primary = json.loads(primary_path.read_text())
        old_raw = pd.DataFrame(old_d['rows'], columns=old_d['fields'])
        new_raw = pd.DataFrame(primary['rows'], columns=primary['fields'])
        assert old_raw.code.eq(old_f['historical_code']).all()
        assert new_raw.code.eq(primary['code']).all()
        assert not old_raw.date.duplicated().any() and not new_raw.date.duplicated().any()
        joined = old_raw.merge(new_raw, on='date', suffixes=('_old', '_canonical'), validate='one_to_one')
        assert len(joined) == len(old_raw)
        assert len(joined) >= 400
        numerical = ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn', 'pctChg']
        field_errors = {}
        for field in numerical:
            a = pd.to_numeric(joined[field + '_old'], errors='coerce').to_numpy(float)
            b = pd.to_numeric(joined[field + '_canonical'], errors='coerce').to_numpy(float)
            field_errors[field] = int((~np.isclose(a, b, rtol=0, atol=1e-8, equal_nan=True)).sum())
        for field in ['adjustflag', 'tradestatus', 'isST']:
            field_errors[field] = int(joined[field + '_old'].ne(joined[field + '_canonical']).sum())
        trading = joined.tradestatus_old.eq('1') & joined.tradestatus_canonical.eq('1')
        old_volume = pd.to_numeric(joined.volume_old, errors='coerce').to_numpy(float)
        canonical_volume = pd.to_numeric(joined.volume_canonical, errors='coerce').to_numpy(float)
        trading_volume_errors = int((trading.to_numpy() & ~np.isclose(old_volume, canonical_volume,
                                                                     rtol=0, atol=1e-8, equal_nan=True)).sum())
        # This is identity/factor validation, not permission to replace daily
        # amount, percentage, or suspension-null fields with another source.
        # Retain every discrepancy. A traded OHLC/V disagreement blocks a bridge.
        price_identity_errors = sum(field_errors[k] for k in ['open', 'high', 'low', 'close', 'preclose'])
        eligible = (price_identity_errors == 0 and trading_volume_errors == 0
                    and field_errors['tradestatus'] == 0 and field_errors['adjustflag'] == 0)
        canonical = sorted(primary['factors'], key=lambda r: r[1])
        historical = sorted(old_f['rows'], key=lambda r: r[1])
        first_new_day = canonical[0][1]
        prior = [r for r in historical if r[1] <= first_new_day]
        assert prior
        anchor = prior[-1]
        multiplier = Decimal(canonical[0][2]) / Decimal(anchor[2])
        prefix = [r for r in historical if r[1] < first_new_day]
        assert prefix and prefix[0][1] < '2017-01-01'
        for r in prefix if eligible else []:
            events.append({'symbol': symbol, 'effective_date': r[1], 'observed_code': r[0],
                           'observed_fore_factor': float(r[2]),
                           'effective_fore_factor': float(Decimal(r[2]) * multiplier),
                           'source_kind': 'historical_code_observed_event',
                           'scale_multiplier': float(multiplier),
                           'canonical_anchor_date': first_new_day,
                           'official_code_change_date': change_day})
        for r in canonical:
            events.append({'symbol': symbol, 'effective_date': r[1], 'observed_code': r[0],
                           'observed_fore_factor': float(r[2]), 'effective_fore_factor': float(r[2]),
                           'source_kind': 'canonical_code_observed_event', 'scale_multiplier': 1.0,
                           'canonical_anchor_date': first_new_day, 'official_code_change_date': change_day})
        # Corporate actions on a date observed under both codes must align after
        # the fixed level change. Missing future actions are never invented.
        overlap = []
        by_new_date = {r[1]: r for r in canonical}
        for r in historical:
            if r[1] in by_new_date:
                new = by_new_date[r[1]]
                error = abs(float(Decimal(r[2]) * multiplier) - float(new[2]))
                assert error <= 1e-8
                overlap.append({'date': r[1], 'fore_factor_error': error})
        records.append({'symbol': symbol, 'historical_code': old_f['historical_code'],
                        'official_code_change_date': change_day,
                        'official_statement_url': url, 'official_statement_path': str(statement),
                        'official_statement_sha256': sha(statement),
                        'raw_daily_overlap_rows_checked': len(joined), 'raw_daily_field_mismatches': field_errors,
                        'first_canonical_factor_date': first_new_day,
                        'prefix_observed_factor_events': len(prefix),
                        'prefix_admitted_to_research_bridge': eligible,
                        'research_bridge_pending_reason': None if eligible else 'Raw daily OHLC or trading-volume conflict; original vendor data retained.',
                        'raw_daily_trading_volume_mismatches': trading_volume_errors,
                        'normalization_multiplier': str(multiplier),
                        'shared_observed_event_checks': overlap,
                        'primary_source_sha256': sha(primary_path),
                        'historical_factor_source_sha256': sha(old_factor_path),
                        'historical_raw_daily_source_sha256': sha(old_daily_path)})
    frame = pd.DataFrame(events).sort_values(['symbol', 'effective_date'])
    assert not frame.duplicated(['symbol', 'effective_date']).any()
    assert np.isfinite(frame.effective_fore_factor).all() and frame.effective_fore_factor.gt(0).all()
    path = OUT / 'canonical_observed_fore_factor_events.parquet'
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path, compression='zstd')
    receipt = {'status': 'official_identity_verified_two_research_bridges_prepared_one_daily_price_conflict_pending',
               'checked_at_utc': pd.Timestamp.now(tz='UTC').isoformat(), 'records': records,
               'daily_overlap_rows_checked': sum(r['raw_daily_overlap_rows_checked'] for r in records),
               'research_bridge_symbols': [r['symbol'] for r in records if r['prefix_admitted_to_research_bridge']],
               'pending_bridge_symbols': [r['symbol'] for r in records if not r['prefix_admitted_to_research_bridge']],
               'factor_events_written': len(frame), 'factor_event_parquet_sha256': sha(path),
               'normalization': 'Only foreAdjustFactor is used by the existing candidate. Historical fore-factor levels are multiplied by a single constant to share its current normalization basis. Observed event dates and relative factor changes are preserved. No backward-factor field is used to adjust a price.',
               'point_in_time_semantics': 'Effective historical event date must be <= each requested date. Constant factor normalization is the same retrospective price basis already used by the candidate, not a claim that its current anchor was known historically. Canonical code identity is a retrospective data dictionary, not a stock-selection signal.',
               'original_vendor_responses_modified': False, 'candidate_panel_modified': False,
               'bridge_applied_to_panel': False, 'production_ready': False,
               'source_code_sha256': sha(Path(__file__))}
    (OUT / 'bridge_validation_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
