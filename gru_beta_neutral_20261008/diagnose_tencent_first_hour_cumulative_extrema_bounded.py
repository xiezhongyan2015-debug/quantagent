"""Inspect published first-hour cumulative extrema at the fixed strict cut.

For the first interval only, cumulative daily extrema have the same scope as
the opening-to-endpoint prefix. Later hours cannot reuse daily extrema. Actual
prefix extrema still require separate native-trade proof before admission.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_sep22_quote_witness_minutes_bounded import clock

RAW=p.BASE/'backfilled_panel_candidate_native_strict_endpoint_v20_raw'
PARENT=p.OUT/'tencent_strict_endpoint_quote_diagnostic/receipt.json'
DEST=p.OUT/'tencent_first_hour_cumulative_extrema_diagnostic'


def main():
    lock=p.locked('diagnose_first_hour_cumulative_extrema');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());parent=p.json.loads(PARENT.read_text());assert parent['status']=='complete_canonical_strict_endpoint_quote_diagnostic_no_admission';requests=[]
    for r in parent['records']:
        if r['slot']==0 and not mask[cal['dates'].index(r['date'])*4,cal['union_symbols'].index(r['symbol'])]:requests.append(r)
    result=dict(status='diagnosing_first_hour_only_published_cumulative_extrema',started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_diagnostic_sha256=p.sha(PARENT),parent_raw_audit_sha256=p.sha(audit_path),requested_hours=len(requests),
        definition='First hour only: latest positive actual quote with source UpdateTime strictly before10:30, sorted by(UpdateTime,LocalTime,SeqNo string). OpenPrice/HighPrice/LowPrice/LastPrice and cumulative share volume are observed fields of that exact quote. Cumulative daily scope equals opening-to-endpoint prefix only for first hour; never imported into later hours. No cut is searched or moved. Full actual native prefix extrema and all60Sina minutes must be independently verified before any admission.',
        later_hour_daily_extrema_used=False,canonical_M1_changed=False,candidate_hours_admitted=0,raw_candidate_modified=False,coarse_vendor_implementation_certified=False,records=[],counts={})
    def save():result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    save();counts=Counter()
    for request in sorted(requests,key=lambda r:(r['date'],r['symbol'])):
        symbol=request['symbol'];vf='TradVolume' if symbol.endswith('.SH') else 'Volume';path=p.Path(request['quote_source_path']);before=p.identity(path);assert before==request['quote_source_identity'];quotes=[]
        for batch in p.pq.ParquetFile(path).iter_batches(batch_size=p.BATCH_ROWS,columns=['SecurityID','UpdateTime','LocalTime','SeqNo','OpenPrice','HighPrice','LowPrice','LastPrice',vf,'Turnover'],use_threads=False):
            for q in batch.to_pylist():
                if q['SecurityID']!=symbol[:6] or D(str(q[vf]))<=0 or D(str(q['LastPrice']))<=0:continue
                px={f:int(D(str(q[name]))*10000) for f,name in [('open','OpenPrice'),('high','HighPrice'),('low','LowPrice'),('close','LastPrice')]};assert all(D(str(q[name]))*10000==px[f] for f,name in [('open','OpenPrice'),('high','HighPrice'),('low','LowPrice'),('close','LastPrice')])
                qty=D(str(q[vf]));cash=D(str(q['Turnover']))*10000;assert qty==qty.to_integral_value() and cash==cash.to_integral_value();quotes.append(dict(reported_ms=clock(q['UpdateTime']),received_ms=clock(q['LocalTime']),sequence=str(q['SeqNo']),price_x10000=px['close'],open_price_x10000=px['open'],high_price_x10000=px['high'],low_price_x10000=px['low'],quantity=int(qty),amount_x10000=int(cash)))
        assert len(quotes)<=10000 and p.identity(path)==before;quotes.sort(key=lambda q:(q['reported_ms'],q['received_ms'],q['sequence']));end=next((q for q in reversed(quotes) if q['reported_ms']<37800000),None)
        assert ({f:end[f] for f in request['current_quote']} if end else None)==request['current_quote']
        model=dict(open=end['open_price_x10000'],high=end['high_price_x10000'],low=end['low_price_x10000'],close=end['price_x10000'],volume=end['quantity']) if end else None
        diff={f:model[f]-request['tencent_hour'][f] for f in request['tencent_hour']} if model else None;status='published_first_hour_cumulative_ohlcv_exact_pending_prefix_extrema' if diff and not any(diff.values()) else 'published_first_hour_cumulative_ohlcv_conflict' if diff else 'actual_quote_absent';counts[status]+=1
        result['records'].append(dict(symbol=symbol,date=request['date'],slot=0,status=status,quote_model=model,tencent_hour=request['tencent_hour'],differences=diff,current_quote=end,quote_source_path=str(path),quote_source_sha256=request['quote_source_sha256'],quote_source_identity=before,accepted_fingerprint_reused_behind_identity_guard=True,sina_reference_path=request['sina_reference_path'],sina_reference_sha256=request['sina_reference_sha256'],actual_whole_prefix_extrema_independent_proof_pending=True));result['counts']=dict(counts);save()
    assert len(requests)==371 and p.identity(RAW/'dataset/hour_complete.npy')==identity;result.update(status='complete_first_hour_cumulative_extrema_diagnostic_no_admission',finished_utc=p.utc());save();lock.close()


if __name__=='__main__':main()
