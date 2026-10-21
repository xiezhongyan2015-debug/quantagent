"""Validate actual IPO seed events and every affected observed minute stockday."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
SOURCE = BASE / 'sina_ipo_factor_prefix_20261009'
PRIMARY = BASE / 'baostock/union_daily_factors/normalized'
HF = BASE / 'huggingface'


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    comparison = json.loads((SOURCE / 'prefix_concordance_audit.json').read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_sina_ipo_factor_prefix.py') == comparison['source_code_sha256']
    listings = json.loads((SOURCE / 'listing_sources/receipt.json').read_text())
    assert listings['passed']
    assert sha(ROOT / 'gru_beta_neutral_20261008/pin_ipo_listing_sources.py') == listings['source_code_sha256']
    references = {r['symbol']: r for r in listings['records']}
    inventory = pd.read_csv(HF / 'verified_union_file_inventory.csv').set_index('symbol')
    admitted, checks, events = [], [], []
    for record in comparison['records']:
        symbol = record['symbol']
        if not record['all_canonical_events_concordant_with_anchor_aligned_sina']:
            continue
        listing = references[symbol]
        for name, digest in [('source_path', 'source_sha256'), ('extracted_text_path', 'extracted_text_sha256')]:
            assert sha(Path(listing[name])) == listing[digest]
        ipo = record['observed_ipo_event_date']
        assert ipo == listing['listing_date']
        source_path = SOURCE / (symbol[:6]+'.js')
        assert sha(source_path) == record['source_factor_sha256']
        raw = source_path.read_text(); parsed, _ = json.JSONDecoder().raw_decode(raw[raw.index('{'):])
        observed = [r for r in parsed['data'] if r['d'] != '1900-01-01']
        assert len(observed) == 1 and observed[0]['d'] == ipo and float(observed[0]['f']) == 1.
        primary_path = PRIMARY / (symbol+'.json')
        assert sha(primary_path) == record['canonical_source_sha256']
        obj = json.loads(primary_path.read_text())
        assert len(obj['factors']) == 1 and float(obj['factors'][0][2]) == 1.
        first = obj['factors'][0][1]; assert first == record['first_canonical_factor_date'] and ipo < first
        daily = pd.DataFrame(obj['rows'], columns=obj['fields']).set_index('date')
        minutes_path = HF / 'snapshot' / inventory.loc[symbol, 'file']
        assert sha(minutes_path) == inventory.loc[symbol, 'expected_lfs_sha256']
        days = record['factorless_trading_dates']
        quotes = pq.read_table(minutes_path, filters=[('timestamp', '>=', pd.Timestamp(min(days))),
                                                       ('timestamp', '<', pd.Timestamp(max(days))+pd.Timedelta(days=1))]).to_pandas()
        assert quotes.symbol.astype(str).eq(symbol[:6]).all() and quotes.exchange.eq('SH').all()
        quotes['date'] = quotes.timestamp.dt.strftime('%Y-%m-%d')
        day_checks = []
        for day in days:
            frame = quotes[quotes.date.eq(day)].sort_values('timestamp')
            clock = frame.timestamp.dt.hour.to_numpy()*60 + frame.timestamp.dt.minute.to_numpy()
            expected_clock = np.r_[np.arange(571,691), np.arange(781,901)]
            regular = frame.loc[np.isin(clock,expected_clock)]
            valid_grid = len(regular)==240 and np.array_equal(regular.timestamp.dt.hour.to_numpy()*60+regular.timestamp.dt.minute.to_numpy(), expected_clock)
            valid_grid &= bool(frame.timestamp.dt.second.eq(0).all() and not frame.timestamp.duplicated().any())
            valid_grid &= not bool(((clock==780)&frame.volume.gt(0)).any())
            assert valid_grid, (symbol,day)
            raw_prices = regular[['open','high','low','close']].to_numpy(float).round(2)
            volume = regular.volume.to_numpy(float).copy()
            assert np.isfinite(raw_prices).all() and (raw_prices>0).all()
            assert np.isfinite(volume).all() and (volume>=0).all()
            assert (raw_prices[:,1]>=raw_prices.max(axis=1)).all() and (raw_prices[:,2]<=raw_prices.min(axis=1)).all()
            boundary = frame.loc[(clock==570)&frame.volume.gt(0)]
            assert len(boundary)<=1
            if len(boundary):
                b=boundary.iloc[0]; raw_prices[0,0]=round(float(b.open),2)
                raw_prices[0,1]=max(raw_prices[0,1],round(float(b.high),2));raw_prices[0,2]=min(raw_prices[0,2],round(float(b.low),2))
                volume[0]+=float(b.volume)
            p=daily.loc[day];assert p.tradestatus=='1' and p.adjustflag=='3'
            tests={'daily_open_exact': abs(raw_prices[0,0]-float(p.open))<=1e-8,
                   'daily_close_exact': abs(raw_prices[-1,3]-float(p.close))<=1e-8,
                   'daily_volume_within_100_shares': abs(volume.sum()-float(p.volume))<=100,
                   'all_observed_quotes_inside_daily_range': bool(raw_prices[:,1].max()<=float(p.high)+1e-8 and raw_prices[:,2].min()>=float(p.low)-1e-8)}
            tests={k:bool(v) for k,v in tests.items()}
            valid=all(tests.values())
            positive = volume.reshape(4,60).sum(axis=1)>0
            day_checks.append({'symbol':symbol,'date':day,'minute_grid_valid':bool(valid_grid),**tests,
                               'qualified_hour_count':int(positive.sum()) if valid else 0,
                               'source_raw_open':float(raw_prices[0,0]),'source_raw_close':float(raw_prices[-1,3]),
                               'source_day_volume':float(volume.sum()),'positive_volume_boundary':bool(len(boundary))})
        admitted.append({**record,'listing_primary_reference_byte_sha256':listing['source_sha256'],
                         'listing_primary_reference_bytes_pinned':True,'minute_source_sha256':sha(minutes_path),
                         'qualified_prefix_hours':sum(r['qualified_hour_count'] for r in day_checks),
                         'prefix_seed_date':ipo,'prefix_observed_fore_multiplier':1.,
                         'prefix_asof_event_semantics':'Actual IPO-date source seed, reciprocal of observed divisor 1; canonical seed 1 agrees. No extension before IPO and no 1900 sentinel.',
                         'admitted_to_candidate':False})
        events += [{'symbol':symbol,'effective_date':ipo,'effective_fore_factor':1.,'source_kind':'observed_sina_ipo_seed'},
                   {'symbol':symbol,'effective_date':first,'effective_fore_factor':1.,'source_kind':'canonical_observed_factor_event'}]
        checks += day_checks
    assert {r['symbol'] for r in admitted}=={'688220.SH','688234.SH','688331.SH'}
    pd.DataFrame(events).to_parquet(SOURCE/'validated_observed_ipo_factor_events.parquet',index=False,compression='zstd')
    result={'checked_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'source_code_sha256':sha(Path(__file__)),
            'concordance_receipt_sha256':sha(SOURCE/'prefix_concordance_audit.json'),
            'listing_source_receipt_sha256':sha(SOURCE/'listing_sources/receipt.json'),
            'validated_event_parquet_sha256':sha(SOURCE/'validated_observed_ipo_factor_events.parquet'),
            'records':admitted,'minute_stockday_checks':checks,'research_prefix_symbols':[r['symbol'] for r in admitted],
            'pending_factor_method_conflict_symbols':comparison['adjustment_method_conflict_symbols'],
            'qualified_prefix_hours':sum(r['qualified_hour_count'] for r in checks),
            'all_known_canonical_factor_values_preserved_exactly':True,'no_value_assumed_before_observed_IPO_seed':True,
            'source_1900_placeholder_ignored':True,'factor_level_retrospective_not_historically_published_basis_certified':True,
            'applied_to_any_candidate':False,'production_ready':False}
    (SOURCE/'prefix_validation_receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ['records','minute_stockday_checks']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
