"""Diagnose one previous-close price convention using already selected leaves.

Factor inversion/cent rounding remains a hypothesis, not a raw price repair.
Only comparison values are derived; source bars, quantities and panel inputs
stay unchanged. No year CSV or entire published Parquet is reread.
"""
from collections import Counter
from decimal import Decimal as D,ROUND_HALF_EVEN
from . import bounded_tail_pipeline as p

DEST=p.OUT/'kit_wind_fixed_carry_price_diagnostic'


def main():
    lock=p.locked('diagnose_kit_wind_fixed_carry_prices');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    qty_path=p.OUT/'kit_wind_minute_quantity_diagnostic/receipt.json';qty=p.json.loads(qty_path.read_text());assert qty['status']=='complete18day_quantity_diagnostic_no_admission'
    kit_path=p.BASE/'public_source_probes/new_public_history_and_tail_inventory_20261009/Lucifer744__KiT_data_demo/historical_minute_overlap_diagnostic/receipt.json';kit=p.json.loads(kit_path.read_text());byday={(r['symbol'],r['date']):r for r in kit['records']};fields=['open','high','low','close'];counts=Counter();records=[]
    for r in qty['records']:
        key=r['symbol'],r['date'];k=byday[key];comparison_path=p.Path(r['minute_comparison_path']);assert p.sha(comparison_path)==r['minute_comparison_sha256'];rows=p.json.loads(comparison_path.read_text())['minutes'];assert len(rows)==240
        primary_path=p.Path(k['primary_path']);assert p.sha(primary_path)==k['primary_sha256'];primary=p.json.loads(primary_path.read_text());day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==r['date']);events=[dict(zip(primary['factor_fields'],v)) for v in primary['factors']];eligible=[e for e in events if e['dividOperateDate']<=r['date']];factor=D(max(eligible,key=lambda e:e['dividOperateDate'])['backAdjustFactor']) if eligible else D(1)
        previous=D(day['preclose']);matched=Counter();minute_checks=[]
        for b in rows:
            raw={f:D(b['original_raw_prices'][f]) for f in fields};reference={f:(D(str(b['published_adjusted_prices'][f]))/factor).quantize(D('0.01'),rounding=ROUND_HALF_EVEN) for f in fields};predicted=dict(open=previous,high=max(previous,raw['high']),low=min(previous,raw['low']),close=raw['close']);same={f:predicted[f]==reference[f] for f in fields}
            for f,v in same.items():matched[f]+=int(v)
            matched['all4']+=int(all(same.values()));minute_checks.append(dict(end_minute=b['end_minute'],fixed_price_fields_equal=same,reference_inverse_cent_rounded_diagnostic={f:str(v) for f,v in reference.items()},fixed_carry_reference_prediction={f:str(v) for f,v in predicted.items()},original_source_prices={f:str(v) for f,v in raw.items()}));previous=raw['close']
        leaf=DEST/(r['symbol']+'_'+r['date']+'_price_comparison.json');p.atomic_json(leaf,dict(symbol=r['symbol'],date=r['date'],minutes=minute_checks,source_price_adjustment_and_rounding_not_certified=True,derived_comparison_prices_not_admitted=True))
        record=dict(symbol=r['symbol'],date=r['date'],slots=r['slots'],matched_minute_fields=dict(matched),all240_fixed_carry_all4_exact=matched['all4']==240,fixed_quantity_floor_all240_exact='floor_published_quantity_to100_shares' in r['fixed_model_all240_exact'],primary_preclose=day['preclose'],dated_published_back_factor=str(factor),minute_comparison_path=str(leaf),minute_comparison_sha256=p.sha(leaf),original_quantity_comparison_path=str(comparison_path),original_quantity_comparison_sha256=r['minute_comparison_sha256'],candidate_hours_admitted=0);records.append(record)
        counts['days_checked']+=1;counts['minutes_checked']+=240;counts['all240_carry_all4_exact_days']+=int(record['all240_fixed_carry_all4_exact'])
        counts['all240_carry_price_and_floor_quantity_exact_days']+=int(record['all240_fixed_carry_all4_exact'] and record['fixed_quantity_floor_all240_exact'])
    p.atomic_json(output,dict(status='complete18day_fixed_carry_diagnostic_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),quantity_diagnostic_sha256=p.sha(qty_path),published_price_diagnostic_sha256=p.sha(kit_path),fixed_formula='One comparison convention: previous original minuteClose (primarypreclose for first regular minute) as Open, max/min of carryOpen and unchanged originalHigh/Low, unchanged originalClose. Compare only with published prices inversed by already published dated backfactor and cent-rounded half-even. No fitted factors, clocks or source substitutions.',counts=dict(counts),records=records,source_year_CSV_files_reopened=0,whole_published_minute_files_reopened=0,source_adjustment_and_rounding_implementation_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,quantity_or_price_gate_relaxed=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
