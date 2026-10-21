"""Predeclared historical cumulative precision tests; no values corrected/admitted."""
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_EVEN,ROUND_FLOOR,ROUND_CEILING
from collections import Counter
from . import bounded_tail_pipeline as p


def main():
    lock=p.locked('historical_cash_precision')
    root=p.OUT/'historical_quote_candidate_recheck';source_path=root/'receipt.json';receipt=p.json.loads(source_path.read_text());prior_path=root/'independent_verification.json';prior=p.json.loads(prior_path.read_text());assert prior['passed'] and prior['audited_receipt_sha256']==p.sha(source_path)
    names=['decimal_nearest_yuan_half_up','decimal_nearest_yuan_half_even','decimal_floor_yuan','decimal_ceil_yuan','float32_cumulative_currency','float32_then_nearest_yuan']
    rounds=[ROUND_HALF_UP,ROUND_HALF_EVEN,ROUND_FLOOR,ROUND_CEILING]
    counts=Counter();records=[]
    for r in receipt['results']:
        if r['reference_rows']!=48:continue
        native=reference=D(0);points=[];matches={name:0 for name in names};errors={name:D(0) for name in names}
        for row in r['comparisons']:
            native+=D(row['actual']['amount']);reference+=D(row['reference']['amount'])
            f32=D(str(float(p.np.float32(float(reference)))))
            estimates=[reference.quantize(D(1),rounding=m) for m in rounds]+[f32,f32.quantize(D(1),rounding=ROUND_HALF_UP)]
            checks={}
            for name,estimate in zip(names,estimates):
                diff=native-estimate;checks[name]=diff==0;matches[name]+=diff==0;errors[name]=max(errors[name],abs(diff))
            points.append(dict(end_minute=row['end_minute'],native_reported_cumulative_amount=str(native),reference_cumulative_amount=str(reference),model_exact=checks))
        all48=[name for name in names if matches[name]==48]
        counts['complete_reference_stockdays']+=1;counts['any_predeclared_cash_model_all48_exact_days']+=bool(all48)
        for name in names:counts[name+'_all48_exact_days']+=matches[name]==48
        record=dict(symbol=r['symbol'],date=r['date'],strict_price_volume_diagnostic_slots=r['strict_coarse_pass_slots'],all48_cash_exact_models=all48,exact_prefix_counts=matches,max_absolute_prefix_errors={k:str(v) for k,v in errors.items()},points=points)
        records.append(record)
    dest=p.OUT/'historical_cash_precision';dest.mkdir(exist_ok=True)
    p.atomic_json(dest/'receipt.json',dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_historical_receipt_sha256=p.sha(source_path),inherited_historical_proof_sha256=p.sha(prior_path),models_predeclared=names,counts=dict(counts),records=records,source_values_modified=False,true_minute_shares_inferred=False,minute_bars_created=False,candidate_hours_admitted=0,cash_gate_relaxed=False,source_rounding_implementation_not_certified=True,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(dict(counts));lock.close()


if __name__=='__main__':main()
