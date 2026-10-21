"""Classify actual unresolved fixed-model conflicts; no source/model fitting."""
from collections import Counter,defaultdict
from . import bounded_tail_pipeline as p
RAW=p.BASE/'backfilled_panel_candidate_native_first_hour_extrema_v21_raw'
DEST=p.OUT/'remaining_quote_conflict_classification_v21'
def main():
    DEST.mkdir(exist_ok=False)
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    pin=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==pin
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());dates={d:i*4 for i,d in enumerate(cal['dates'])};symbols={s:i for i,s in enumerate(cal['union_symbols'])}
    strict=p.OUT/'tencent_strict_endpoint_quote_diagnostic/receipt.json';first=p.OUT/'tencent_first_hour_cumulative_extrema_diagnostic/receipt.json';a=p.json.loads(strict.read_text());b=p.json.loads(first.read_text());cumulative={(r['date'],r['symbol'],0):r for r in b['records']}
    records=[];fields=Counter();slots=Counter();qty=Counter();prices_only=[]
    for r in a['records']:
        key=r['date'],r['symbol'],r['slot']
        if mask[dates[key[0]]+key[2],symbols[key[1]]]:continue
        source=cumulative[key] if key[2]==0 else r
        differences=source['differences'];assert differences is not None
        fs=','.join(sorted(f for f,v in differences.items() if v));fields[fs]+=1;slots[str(key[2])+':'+fs]+=1
        if differences['volume']:qty['quantity_difference_within100' if abs(differences['volume'])<=100 else 'quantity_difference_over100']+=1
        if not differences['volume']:prices_only.append({'date':key[0],'symbol':key[1],'slot':key[2],'fields':fs})
        records.append(dict(date=key[0],symbol=key[1],slot=key[2],fixed_comparison='first_hour_cumulative_source_extrema' if key[2]==0 else 'strict_interval_sampled_source_quotes',fields_in_conflict=fs,differences=differences,tencent_hour=source['tencent_hour'],actual_quote_model=source['quote_model'],quote_source_path=source['quote_source_path'],quote_source_identity=source['quote_source_identity'],quote_source_sha256=source['quote_source_sha256'],sina_reference_path=source['sina_reference_path'],sina_reference_sha256=source['sina_reference_sha256']))
    assert len(records)==956 and p.identity(RAW/'dataset/hour_complete.npy')==pin
    result=dict(status='complete_remaining_quote_conflict_classification_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),raw_audit_sha256=p.sha(audit_path),strict_diagnostic_sha256=p.sha(strict),first_diagnostic_sha256=p.sha(first),records=records,field_conflict_counts=dict(fields),slot_field_conflict_counts=dict(slots),quantity_counts=dict(qty),quantity_exact_price_conflict_hours=prices_only,actual_source_payloads_reopened=0,archive_fingerprints_repeated=False,clock_offsets_fitted=False,candidate_hours_admitted=0,production_ready=False)
    p.atomic_json(DEST/'receipt.json',result);print(p.json.dumps({k:v for k,v in result.items() if k not in ['records','quantity_exact_price_conflict_hours']},ensure_ascii=False))
if __name__=='__main__':main()
