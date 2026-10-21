"""Classify all947 historical coarse conflicts; test one fixed carry-open rule.

Diagnostics only. Neither volume tolerances nor minute definitions are changed.
One240-minute observed stockday is resident, without rescanning year CSVs.
"""
import csv
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    dest=p.OUT/'historical_remaining_coarse_conflict_definition_diagnostic';dest.mkdir(exist_ok=True)
    output=dest/'receipt.json';assert not output.exists()
    scope_path=p.OUT/'historical_remaining_after_v14/receipt.json';scope=p.json.loads(scope_path.read_text())
    rows_path=scope_path.parent/'remaining_hours.csv';pin=next(r for r in scope['artifacts'] if r['path']==str(rows_path));data=rows_path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==pin['sha256']
    wanted=[r for r in csv.DictReader(data.decode().splitlines()) if r['current_remaining_reason']=='coarse_price_or_quantity_conflict'];assert len(wanted)==947
    source_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';source=p.json.loads(source_path.read_text());proof_path=source_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source_path)
    by_day={(r['symbol'],r['date']):r for r in source['results']};source_files={r['path']:r for r in source['native_files']}
    raw=p.BASE/'backfilled_panel_candidate_native_reconciled_v15_raw';audit_path=raw/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(raw/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(raw/'dataset/hour_complete.npy')==identity
    mask=p.np.load(raw/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    counts=Counter();patterns=Counter();records=[];current=None;bars=None;sidecar_pins=[]
    for requested in sorted(wanted,key=lambda r:(r['symbol'],r['date'],int(r['slot']))):
        key=requested['symbol'],requested['date'];slot=int(requested['slot']);original=by_day[key]
        assert not mask[cal['dates'].index(key[1])*4+slot,cal['union_symbols'].index(key[0])]
        if key!=current:
            path=p.Path(original['native_selected_path']);before=p.identity(path);data=path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==original['native_selected_sha256']
            selected=p.json.loads(data);accepted=source_files[selected['native_source_path']];assert p.identity(p.Path(accepted['path']))==accepted['source_identity'] and selected['native_source_sha256']==accepted['sha256']
            bars=[{f:D(v) for f,v in b.items()} for b in selected['normalized_bars']];assert len(bars)==240
            sidecar_pins.append(dict(path=str(path),sha256=original['native_selected_sha256'],verified_identity=before));assert p.identity(path)==before;current=key
        block=original['comparisons'][slot*12:(slot+1)*12];assert len(block)==12
        price_fields=set();max_qty=D(0);fixed_matches=0;native_matches=0
        for i,q in enumerate(block,slot*12):
            observed=bars[i*5:(i+1)*5];reference={f:D(v) for f,v in q['reference'].items()}
            native=dict(open=observed[0]['open'],high=max(b['high'] for b in observed),low=min(b['low'] for b in observed),close=observed[-1]['close'],volume=sum(b['volume'] for b in observed),amount=sum(b['amount'] for b in observed))
            assert all(native[f]==D(q['actual'][f]) and native[f]-reference[f]==D(q['differences'][f]) for f in native)
            price_fields|={f for f in ['open','high','low','close'] if native[f]!=reference[f]};max_qty=max(max_qty,abs(native['volume']-reference['volume']))
            carry=bars[i*5-1]['close'] if i else observed[0]['open'];model=dict(native,open=carry,high=max(native['high'],carry),low=min(native['low'],carry))
            volume_pass=abs(native['volume']-reference['volume'])<=100
            native_matches+=all(native[f]==reference[f] for f in ['open','high','low','close']) and volume_pass
            fixed_matches+=all(model[f]==reference[f] for f in ['open','high','low','close']) and volume_pass
        assert native_matches<12
        counts['price_conflict' if price_fields else 'quantity_only_conflict']+=1;counts['all12_fixed_carry_open_and_original_quantity_gate_pass']+=fixed_matches==12
        patterns[','.join(sorted(price_fields))]+=1
        records.append(dict(symbol=key[0],date=key[1],slot=slot,conflicting_price_fields=sorted(price_fields),max_abs_five_minute_quantity_difference_shares=str(max_qty),
            native_definition_exact_five_minute_blocks=native_matches,fixed_previous_observed_close_exact_blocks=fixed_matches,
            all12_fixed_formula_and_original_quantity_gate_pass=fixed_matches==12,status='source_conflict_retained',native_selected_path=original['native_selected_path'],native_selected_sha256=original['native_selected_sha256']))
    assert len(records)==947 and p.identity(raw/'dataset/hour_complete.npy')==identity
    p.atomic_json(output,dict(status='complete_all947_coarse_conflicts_diagnosed_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),
        original_scope_receipt_sha256=p.sha(scope_path),source_receipt_sha256=p.sha(source_path),source_independent_verification_sha256=p.sha(proof_path),raw_full_history_audit_sha256=p.sha(audit_path),
        fixed_formula='Preceding observed native minute close is coarse open, included in high/low; first session block retains observed auction open; actual last close and sum reported volume unchanged; original <=100-share five-minute quantity gate retained.',
        hours_checked=947,counts=dict(counts),price_conflict_field_pattern_counts=dict(patterns),source_selected_sidecars_read_once=sidecar_pins,records=records,
        source_year_files_not_rescanned_or_rehashed=True,minute_definitions_and_quantity_thresholds_unchanged=True,source_vendor_implementation_not_certified=True,
        candidate_hours_admitted=0,source_values_changed=False,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))


if __name__=='__main__':main()
