"""Pin the original research donor contract and an evidence-backed next batch.

This is contract reconciliation and prioritization, never raw admission. Source
reporting precision stays explicit; no minute quantity or cash is reconstructed.
"""
import ast
from collections import Counter
from . import bounded_tail_pipeline as p


def main():
    lock=p.locked('reconcile_historical_native_contract')
    directory=p.OUT/'historical_contract_reconciliation';directory.mkdir(exist_ok=True)
    output=directory/'receipt.json';assert not output.exists()
    donor_path=p.ROOT/'gru_beta_neutral_20261008/canonical_ipo_v6/backfilled_hour_panel.py'
    donor_source=donor_path.read_text();tree=ast.parse(donor_source)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='donor_qualification')
    donor_gate=ast.get_source_segment(donor_source,function)
    assert 'same_volume=(joined.raw_volume-joined.volume).abs()<=100' in donor_gate
    assert 'same_open' in donor_gate and 'same_close' in donor_gate and 'in_range' in donor_gate
    plan_path=p.ROOT/'gru_research/PLAN.md';plan=plan_path.read_text()
    assert '首版不使用分钟 VWAP' in plan and '成交量单位和复权一致性必须通过检查' in plan
    verified_path=p.OUT/'historical_quote_candidate_recheck/independent_verification.json'
    verified=p.json.loads(verified_path.read_text());source_receipt=verified_path.parent/'receipt.json'
    assert verified['passed'] and verified['audited_receipt_sha256']==p.sha(source_receipt)
    comparison_path=p.OUT/'historical_cash_precision/original_donor_gate_comparison.json'
    comparison=p.json.loads(comparison_path.read_text())
    assert comparison['source_receipt_sha256']==p.sha(source_receipt) and comparison['original_donor_gate_source_sha256']==p.sha(donor_path)
    records=[]
    for r in verified['results']:
        if set(r.get('strict_coarse_slots',[])) != {0,1,2,3}:continue
        assert r['complete_coarse_reference'] and r['native_reported_lots_all_integral']
        assert abs(p.np.float64(r['primary_daily_volume_difference']))<=100
        records.append(dict(symbol=r['symbol'],date=r['date'],slots=[0,1,2,3],hours=4,
            independent_48_five_minute_prices_exact=True,independent_each_five_minute_quantity_difference_at_most_100_shares=True,
            reported_day_quantity_difference_shares=r['primary_daily_volume_difference'],reported_day_cash_difference_CNY=r['primary_daily_amount_difference'],
            native_source_quantity_is_reported_integer_lots=True,existing_scale_to_reported_shares=100,
            actual_one_share_minute_quantity_recovered=False,actual_receiver_time_certified=False))
    assert len(records)==88 and {(r['symbol'],r['date']) for r in records}=={(r['symbol'],r['date']) for r in comparison['days']+comparison['failed']}
    precision_failures={(r['symbol'],r['date']):r for r in comparison['failed']}
    for r in records:
        q=precision_failures.get((r['symbol'],r['date']))
        r['additional_cash_bound_diagnostic_failed_minutes']=q['badminutes'] if q else []
    p.atomic_json(output,dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),
        original_donor_source_path=str(donor_path),original_donor_source_sha256=p.sha(donor_path),original_donor_gate_lines=[function.lineno,function.end_lineno],
        plan_source_path=str(plan_path),plan_source_sha256=p.sha(plan_path),independent_historical_proof_sha256=p.sha(verified_path),
        original_donor_gate_comparison_sha256=p.sha(comparison_path),
        original_research_contract='240 actual observed valid regular bars; positive observed auction assigned to first minute; primary daily O/C exact, volume difference <=100 shares, prices inside primary daily range, positive dated factor and positive traded hour volume. Historical SH pre2018-08-20 closing exception is separately evidenced and irrelevant to this2020-2023 batch.',
        contract_interpretation='The existing OHLCV research donor contract does not require exact one-share minute volume or every-minute cash equality. Keep vendor integer-lot precision explicit and separately retain all failed cash and rounding diagnostics; never infer true minute shares or certify a publisher rounding implementation from feasibility.',
        original_numeric_clock_factor_coverage_gates_waived=False,additional_exact_minute_cash_not_original_gate_for_this_batch=True,
        independent_minute_reference_not_replaced_by_download_progress=True,source_values_changed=False,
        next_batch_stockdays=len(records),next_batch_hours=sum(r['hours'] for r in records),records=records,
        years=dict(Counter(r['date'][:4] for r in records)),
        required_before_raw_admission=['Reproduce each cached240-minute OHLCV source grid and original donor gate with explicit reporting precision.','Recheck canonical identity/old-code routing and as-of factor compiler against retained original factor hours.','Preserve missing reception clocks; assess historical execution windows under the documented retrospective proxy policy without inventing arrival times.','Write a new version and audit the whole1178-stock history; recalculate historical features, labels,80-hour eligibility and all affected normalization cross-sections before training.'],
        candidate_hours_admitted=0,historical_missing_hours_still=3042,training_admission_ready=False,production_ready=False))
    lock.close()


if __name__=='__main__':main()
