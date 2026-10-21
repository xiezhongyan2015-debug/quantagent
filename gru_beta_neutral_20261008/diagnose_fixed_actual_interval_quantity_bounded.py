"""Test actual fixed-hour trade quantity with unchanged observed quote prices."""
from . import bounded_tail_pipeline as p
from collections import Counter
RAW=p.BASE/'backfilled_panel_candidate_native_session_quantity_v24_raw'
DEST=p.OUT/'actual_interval_quantity_diagnostic_v24'
PARENT=p.OUT/'afternoon_closed_morning_quantity_diagnostic_v23/receipt.json'
def main():
    DEST.mkdir(exist_ok=False);parent=p.json.loads(PARENT.read_text());auditpath=RAW/'raw_full_history_audit.json';audit=p.json.loads(auditpath.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json');pin=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==pin
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());requests=[r for r in parent['records'] if not mask[cal['dates'].index(r['date'])*4+r['slot'],cal['union_symbols'].index(r['symbol'])]];assert len(requests)==492
    comparisonpath=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';comparison=p.json.loads(comparisonpath.read_text());proofpath=comparisonpath.parent/'independent_hour_comparison_verification.json';proof=p.json.loads(proofpath.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(comparisonpath)
    wanted={(r['date'],r['symbol'],r['slot']) for r in requests};actual={};pins=[]
    for g in comparison['groups']:
        if g['date'] not in {r['date'] for r in requests}:continue
        path=p.Path(g['comparison_path']);data=path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==g['comparison_sha256'];rows=p.json.loads(data)['comparisons']
        used=False
        for r in rows:
            key=r['date'],r['symbol'],r['slot']
            if r['model']=='floor_left' and key in wanted:assert key not in actual;actual[key]=r['native_hour'];used=True
        if used:pins.append(dict(path=str(path),sha256=g['comparison_sha256']))
    counts=Counter();records=[]
    for r in requests:
        key=r['date'],r['symbol'],r['slot'];native=actual[key];model=dict(r['quote_model'],volume=native['volume']);differences={f:model[f]-r['tencent_hour'][f] for f in model};exact=not any(differences.values());status='fixed_actual_interval_quantity_ohlcv_exact' if exact else 'fixed_actual_interval_quantity_ohlcv_conflict';counts[status]+=1;records.append(dict(r,status=status,quote_model=model,differences=differences,native_fixed_hour=native,source_quote_difference_quantity=r['quote_model']['volume']))
    assert p.identity(RAW/'dataset/hour_complete.npy')==pin
    result=dict(status='complete_fixed_actual_interval_quantity_diagnostic_no_admission',checked_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_diagnostic_sha256=p.sha(PARENT),parent_raw_audit_sha256=p.sha(auditpath),original_sampled_diagnostic_sha256=parent['parent_diagnostic_sha256'],comparison_receipt_sha256=p.sha(comparisonpath),comparison_independent_proof_sha256=p.sha(proofpath),comparison_leaf_pins=pins,definition='Same fixed canonical hour interval and the existing strict-cut actual LastPrice snapshot OHLC rule (Open first actual quote in each later interval, dayOpenPrice for slot0). Quantity is independently summed from all actual trades in the canonical fixed hour including existing phase/grace mapping, rather than differencing snapshot cumulative quantities. No time offset, cut search, rounded quantity or invented minute. All source price snapshots must bind to actual prefixes; all60trueM1 must match Sina and day6 must match primary before any admission. True M1 stays first/max/min/last actual trades, separate from coarse snapshot prices.',records=records,counts=dict(counts),requested_hours=492,candidate_hours_admitted=0,raw_candidate_modified=False,provider_implementation_certified=False,actual_source_archives_reopened=False)
    p.atomic_json(DEST/'receipt.json',result);print(p.json.dumps({k:v for k,v in result.items() if k not in ['records','comparison_leaf_pins']},ensure_ascii=False))
if __name__=='__main__':main()
