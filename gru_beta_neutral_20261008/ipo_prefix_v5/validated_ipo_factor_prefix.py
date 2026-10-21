"""Three pinned, source-observed IPO seeds; no generic pre-first-event fill."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/sina_ipo_factor_prefix_20261009'
PRIMARY = SOURCE.parent / 'baostock/union_daily_factors/normalized'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def verified_inputs():
    path = SOURCE / 'prefix_validation_receipt.json'
    receipt = json.loads(path.read_text())
    assert receipt['passed'] and receipt['qualified_prefix_hours']==24 and not receipt['applied_to_any_candidate']
    assert sha(ROOT / 'gru_beta_neutral_20261008/validate_sina_ipo_factor_prefix.py')==receipt['source_code_sha256']
    assert sha(SOURCE/'prefix_concordance_audit.json')==receipt['concordance_receipt_sha256']
    assert sha(SOURCE/'listing_sources/receipt.json')==receipt['listing_source_receipt_sha256']
    table = SOURCE/'validated_observed_ipo_factor_events.parquet'
    assert sha(table)==receipt['validated_event_parquet_sha256']
    frame=pq.read_table(table).to_pandas();events={};records={}
    listings=json.loads((SOURCE/'listing_sources/receipt.json').read_text())
    for listing in listings['records']:
        assert sha(Path(listing['source_path']))==listing['source_sha256']
        assert sha(Path(listing['extracted_text_path']))==listing['extracted_text_sha256']
    for record in receipt['records']:
        symbol=record['symbol'];primary=PRIMARY/(symbol+'.json')
        assert sha(primary)==record['canonical_source_sha256']
        assert sha(SOURCE/(symbol[:6]+'.js'))==record['source_factor_sha256']
        selected=frame[frame.symbol.eq(symbol)].sort_values('effective_date')
        assert len(selected)==2 and not selected.effective_date.duplicated().any()
        days=selected.effective_date.to_numpy(str);values=selected.effective_fore_factor.to_numpy(float)
        assert days[0]==record['observed_ipo_event_date'] and (values==1.).all()
        obj=json.loads(primary.read_text());known=dict(zip(days,values))
        for row in obj['factors']:assert known[row[1]]==float(row[2])
        events[symbol]=(days,values);records[symbol]=record
    assert set(events)=={'688220.SH','688234.SH','688331.SH'}
    return events,records,{'prefix_validation_receipt':str(path),'prefix_validation_receipt_sha256':sha(path),
        'observed_event_parquet_sha256':sha(table),'module_sha256':sha(Path(__file__)),
        'applied_symbols':sorted(events),'qualified_observed_minute_prefix_hours':24,
        'pending_factor_method_conflict_symbols':receipt['pending_factor_method_conflict_symbols'],
        'source_1900_placeholder_ignored':True,'canonical_factors_overwritten':False,
        'point_in_time_semantics':'Actual source IPO-date events <= the session. Observed factors of 1 agree with canonical seeds; no 1900 or generic backward extension.',
        'factor_level_is_retrospective_not_historically_published_basis_certified':True,'production_ready':False}


def override_factor_state(obj,dates):
    events,_,_=verified_inputs()
    if obj['symbol'] not in events:return None
    event_dates,event_values=events[obj['symbol']];queries=np.asarray(dates,dtype=str)
    positions=np.searchsorted(event_dates,queries,side='right')-1
    output=np.full(len(queries),np.nan);known=positions>=0
    output[known]=event_values[positions[known]]
    assert (event_dates[positions[known]]<=queries[known]).all()
    return output


def prefix_dates(symbol):
    _,records,_=verified_inputs()
    return records.get(symbol,{}).get('factorless_trading_dates',[])


def provenance(symbol=None):
    _,records,contract=verified_inputs()
    if symbol is None:return contract
    return {**contract,'source_record':records[symbol]} if symbol in records else None


def validate_asof():
    events,records,contract=verified_inputs()
    for symbol,(days,values) in events.items():
        obj=json.loads((PRIMARY/(symbol+'.json')).read_text())
        np.testing.assert_array_equal(override_factor_state(obj,days),values)
        assert np.isnan(override_factor_state(obj,['1900-01-01','2020-01-01'])).all()
        assert (override_factor_state(obj,records[symbol]['factorless_trading_dates'])==1.).all()
        queries=np.array(records[symbol]['factorless_trading_dates'])
        appended_days=np.concatenate([days,np.array(['2027-01-01'])]);appended_values=np.r_[values,2.]
        positions=np.searchsorted(appended_days,queries,side='right')-1
        np.testing.assert_array_equal(override_factor_state(obj,queries),appended_values[positions])
    return {'passed':True,'only_observed_IPO_seed_prefixes_admitted':True,'unknown_before_IPO_stays_missing':True,
            'future_event_append_invariance':True,'all_known_canonical_values_exactly_preserved':True,'source_contract':contract}


if __name__=='__main__':print(json.dumps(validate_asof(),ensure_ascii=False))
