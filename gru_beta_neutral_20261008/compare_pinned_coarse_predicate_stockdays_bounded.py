"""Compare two actually retrieved coarse days; never expand them to minutes.

Repeat entry only to finish the second pending acquisition. Selected leaves
are small, fingerprinted and source-version pinned. Whole publisher file SHA
and upstream independence remain explicitly uncertified for range retrieval.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .probe_pinned_coarse_predicate_ranges_bounded import DEST as ACQUIRED,SOURCE

DEST=SOURCE/'pinned_predicate_reference_comparison'


def main():
    lock=p.locked('compare_pinned_coarse_predicate_stockdays');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json'
    acquisition_path=ACQUIRED/'receipt.json';acquisition=p.json.loads(acquisition_path.read_text());assert acquisition['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_pinned_coarse_predicate_ranges_bounded.py')
    cached_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';cached=p.json.loads(cached_path.read_text());cached_proof_path=cached_path.parent/'independent_verification.json';cached_proof=p.json.loads(cached_proof_path.read_text());assert cached_proof['passed'] and cached_proof['audited_receipt_sha256']==p.sha(cached_path)
    result=p.json.loads(output.read_text()) if output.exists() else dict(status='waiting_for_both_observed_coarse_days',started_utc=p.utc(),source_code_sha256=p.sha(__file__),pinned_revision=acquisition['pinned_revision'],cached_reference_receipt_sha256=p.sha(cached_path),cached_reference_proof_sha256=p.sha(cached_proof_path),records=[],candidate_hours_admitted=0,raw_candidate_modified=False,complete_publisher_file_SHA_verified=False,independent_upstream_certified=False,coarse_rows_not_expanded_to_minutes=True)
    assert result['source_code_sha256']==p.sha(__file__) and result['cached_reference_receipt_sha256']==p.sha(cached_path)
    done={(r['symbol'],r['date']) for r in result['records']};price=['open','high','low','close'];fields=price+['volume','amount']
    for r in acquisition['records']:
        key=r['symbol'],r['date']
        if key in done or r['status']!='complete_observed_stockday_diagnostic_only':continue
        path=p.Path(r['parquet_path']);before=p.identity(path);assert p.sha(path)==r['parquet_sha256'];rows=p.pq.read_table(path).to_pylist();assert p.identity(path)==before and len(rows)==48
        old=next(q for q in cached['results'] if (q['symbol'],q['date'])==key);assert old['reference_rows']==48;checks=[]
        for i,b in enumerate(rows):
            q=old['comparisons'][i];minute=b['datetime'].hour*60+b['datetime'].minute;assert minute==q['end_minute'] and b['date'].isoformat()==r['date'] and b['stock_code']==r['symbol'][:6]
            values={f:D(str(b[f])) for f in fields};native={f:D(q['actual'][f]) for f in fields};prior={f:D(q['reference'][f]) for f in fields}
            assert all(v.is_finite() for v in values.values()) and values['volume']>=0 and values['volume']==values['volume'].to_integral_value()
            native_delta={f:values[f]-native[f] for f in fields};prior_delta={f:values[f]-prior[f] for f in fields}
            checks.append(dict(end_minute=minute,observed_fields={f:str(v) for f,v in values.items()},old_Baostock_fields={f:str(v) for f,v in prior.items()},original_native_aggregate_fields={f:str(v) for f,v in native.items()},delta_from_old_Baostock={f:str(v) for f,v in prior_delta.items()},delta_from_original_native={f:str(v) for f,v in native_delta.items()},all6_fields_exact_old_Baostock=all(v==0 for v in prior_delta.values()),original_price_and_quantity_gate_pass=all(native_delta[f]==0 for f in price) and abs(native_delta['volume'])<=100))
        result['records'].append(dict(symbol=r['symbol'],date=r['date'],observed_5min_rows=48,observed_path=str(path),observed_sha256=r['parquet_sha256'],observed_verified_identity=before,published_source_file=r['published_file'],pinned_source_revision=acquisition['pinned_revision'],cached_Baostock_reference_path=old['reference_path'],cached_Baostock_reference_sha256=old['reference_sha256'],original_native_selected_path=old['native_selected_path'],original_native_selected_sha256=old['native_selected_sha256'],
            all48_six_fields_exact_existing_Baostock=all(c['all6_fields_exact_old_Baostock'] for c in checks),strict_coarse_pass_slots=[s for s in range(4) if all(c['original_price_and_quantity_gate_pass'] for c in checks[s*12:(s+1)*12])],comparisons=checks,independent_new_upstream_not_demonstrated=True,candidate_hours_admitted=0));done.add(key)
    result.update(updated_utc=p.utc(),source_range_progress_sha256_at_comparison=p.sha(acquisition_path),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    if len(result['records'])==2:result.update(status='complete_two_observed_coarse_day_comparisons_no_admission',finished_utc=p.utc())
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
