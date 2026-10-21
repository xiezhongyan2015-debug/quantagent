"""Prequalify the remaining strict-coarse hours without altering raw candidates.

This is a new117-hour scope, independent of the completed87 full-day batch.
Cash bounds expose conflicts only; feasibility never recovers a true quantity.
"""
import csv
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    destination=p.OUT/'historical_remaining_after_v13'
    output=destination/'strict_hour_native_evidence_recheck.json';assert not output.exists()
    priority_path=destination/'receipt.json';priority=p.json.loads(priority_path.read_text())
    file=destination/'remaining_strict_coarse_priority.csv';pin=next(r for r in priority['artifacts'] if r['path']==str(file))
    data=file.read_bytes();assert p.hashlib.sha256(data).hexdigest()==pin['sha256']
    requests=list(csv.DictReader(data.decode().splitlines()));assert len(requests)==117
    source_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';source=p.json.loads(source_path.read_text())
    proof_path=source_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source_path)
    selected={(r['symbol'],r['date']):r for r in source['results']};source_files={r['path']:r for r in source['native_files']}
    results=[];counts=Counter();current=None;bars=None
    for request in requests:
        symbol,date,slot=request['symbol'],request['date'],int(request['slot']);key=symbol,date;record=selected[key]
        assert slot in record['strict_coarse_pass_slots']
        if key!=current:
            leaf=p.Path(record['native_selected_path']);data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==record['native_selected_sha256']
            observed=p.json.loads(data);original=source_files[observed['native_source_path']]
            assert p.identity(original['path'])==original['source_identity'] and observed['native_source_sha256']==original['sha256']
            bars=[{f:D(v) for f,v in row.items()} for row in observed['normalized_bars']];assert len(bars)==240;current=key
        block=bars[slot*60:(slot+1)*60];bad=[]
        for i,b in enumerate(block):
            # One-lot source precision remains a hypothesis here, never a fill.
            low=max(D(0),b['volume']-100)*b['low'];high=(b['volume']+100)*b['high']
            if b['amount']<low-1 or b['amount']>high+1:bad.append(slot*60+i)
        status='known_minute_price_quantity_amount_conflict' if bad else 'native_hour_grid_and_original_reported_precision_contract_pending_formal_stage'
        counts[status]+=1
        results.append(dict(symbol=symbol,date=date,slot=slot,independent12_five_minute_prices_exact=True,independent_each_five_minute_quantity_difference_at_most100=True,
            native_selected_source_path=record['native_selected_path'],native_selected_sha256=record['native_selected_sha256'],
            additional_cash_feasibility_failed_minute_indices=bad,additional_cash_feasibility_not_true_share_certificate=True,
            native_hour_reported_volume_shares=str(sum(b['volume'] for b in block)),status=status))
    p.atomic_json(output,dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),priority_receipt_sha256=p.sha(priority_path),
        historical_source_receipt_sha256=p.sha(source_path),historical_source_independent_proof_sha256=p.sha(proof_path),hours_checked=len(results),counts=dict(counts),
        records=results,source_year_files_not_rescanned_or_rehashed=True,source_values_changed=False,true_share_volume_inferred=False,
        candidate_hours_admitted=0,training_admission_ready=False,production_ready=False))


if __name__=='__main__':main()
