"""Test the canonical left-closed/right-open quote boundary, once.

The prior diagnostic included an endpoint quote. Here a quote reported exactly
at the end belongs to the next interval, as for the actual trade minute grid.
No clock offsets or best-match selection are searched.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_sep22_quote_witness_minutes_bounded import clock
from .diagnose_tencent_sampled_quote_hours_bounded import START,END

RAW=p.BASE/'backfilled_panel_candidate_native_sampled_quote_v19_raw'
DEST=p.OUT/'tencent_strict_endpoint_quote_diagnostic'
PARENT=p.OUT/'tencent_sampled_quote_hour_diagnostic/receipt.json'


def main():
    lock=p.locked('diagnose_tencent_strict_endpoint_quotes');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());parent=p.json.loads(PARENT.read_text());assert parent['status']=='complete_one_fixed_sampled_quote_diagnostic_no_admission'
    groups=defaultdict(list)
    for r in parent['records']:
        if not mask[cal['dates'].index(r['date'])*4+r['slot'],cal['union_symbols'].index(r['symbol'])]:groups[r['quote_source_path']].append(r)
    result=dict(status='diagnosing_canonical_strict_quote_endpoint',started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_diagnostic_sha256=p.sha(PARENT),parent_raw_audit_sha256=p.sha(audit_path),
        requested_hours=sum(len(v) for v in groups.values()),source_quote_files=len(groups),definition='Positive actual quotes sorted by (reported UpdateTime,received LocalTime,SeqNo string). Latest end quote is strictly before10:30/11:30/14:00/15:00. Samples include left boundary and exclude right boundary. Open uses dayOpenPrice in slot0, latest quote strictly before left boundary for slots1/3, first positive actual quote at or after13:00 in slot2. High/Low include Open and fixed interval LastPrice samples. Volume is end cumulative quantity minus previous strict endpoint quantity (zero in slot0).',
        fixed_interval_is_canonical_trade_grid=True,clock_offsets_fitted=False,canonical_M1_fields_changed=False,coarse_vendor_implementation_certified=False,candidate_hours_admitted=0,raw_candidate_modified=False,records=[],counts={})
    def save():result.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    save();counts=Counter()
    for filename,requests in sorted(groups.items()):
        first=requests[0];symbol=first['symbol'];vf='TradVolume' if symbol.endswith('.SH') else 'Volume';path=p.Path(filename);before=p.identity(path);assert before==first['quote_source_identity'];assert all(r['quote_source_sha256']==first['quote_source_sha256'] and r['quote_source_identity']==before for r in requests)
        pf=p.pq.ParquetFile(path);quotes=[]
        for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','OpenPrice',vf,'Turnover'],use_threads=False):
            for q in batch.to_pylist():
                if q['SecurityID']!=symbol[:6]:continue
                quantity=D(str(q[vf]));price=D(str(q['LastPrice']))*10000;opening=D(str(q['OpenPrice']))*10000;cash=D(str(q['Turnover']))*10000
                if quantity<=0 or price<=0:continue
                assert all(v==v.to_integral_value() for v in [quantity,price,opening,cash])
                quotes.append(dict(reported_ms=clock(q['UpdateTime']),received_ms=clock(q['LocalTime']),sequence=str(q['SeqNo']),price_x10000=int(price),open_price_x10000=int(opening),quantity=int(quantity),amount_x10000=int(cash)))
        assert len(quotes)<=10000 and p.identity(path)==before;quotes.sort(key=lambda q:(q['reported_ms'],q['received_ms'],q['sequence']));endpoints={s:next((q for q in reversed(quotes) if q['reported_ms']<END[s]),None) for s in range(4)}
        for request in sorted(requests,key=lambda r:r['slot']):
            slot=request['slot'];end=endpoints[slot];previous=endpoints.get(slot-1);sample=[q for q in quotes if START[slot]<=q['reported_ms']<END[slot]]
            opening=end if slot==0 else next((q for q in quotes if START[slot]<=q['reported_ms']<END[slot]),None) if slot==2 else next((q for q in reversed(quotes) if q['reported_ms']<START[slot]),None)
            model=None
            if end and opening and sample and (slot==0 or previous):
                op=end['open_price_x10000'] if slot==0 else opening['price_x10000'];prices=[op]+[q['price_x10000'] for q in sample];model=dict(open=op,high=max(prices),low=min(prices),close=end['price_x10000'],volume=end['quantity']-(previous['quantity'] if previous else 0))
            diff={f:model[f]-request['tencent_hour'][f] for f in request['tencent_hour']} if model else None;status='canonical_strict_endpoint_ohlcv_exact' if diff and not any(diff.values()) else 'canonical_strict_endpoint_ohlcv_conflict' if diff else 'actual_endpoint_or_samples_absent'
            counts[status]+=1;result['records'].append(dict(date=request['date'],symbol=symbol,slot=slot,status=status,quote_model=model,tencent_hour=request['tencent_hour'],differences=diff,opening_quote=opening,current_quote=end,previous_quote=previous,observed_price_sample_count=len(sample),quote_source_path=filename,quote_source_sha256=first['quote_source_sha256'],quote_source_identity=before,accepted_fingerprint_reused_behind_identity_guard=True,sina_reference_path=request['sina_reference_path'],sina_reference_sha256=request['sina_reference_sha256']))
        result['counts']=dict(counts);save()
    assert len(result['records'])==1447==result['requested_hours'] and p.identity(RAW/'dataset/hour_complete.npy')==identity
    result.update(status='complete_canonical_strict_endpoint_quote_diagnostic_no_admission',finished_utc=p.utc());save();lock.close()


if __name__=='__main__':main()
