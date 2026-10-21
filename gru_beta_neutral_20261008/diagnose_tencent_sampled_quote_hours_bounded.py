"""Test one fixed sampled-LastPrice coarse rule on remaining minute-exact hours.

All four same-day quotes are reduced from one small stock snapshot leaf. No
clock search, reconstructed prices, or candidate admission is performed.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_sep22_quote_witness_minutes_bounded import clock

RAW=p.BASE/'backfilled_panel_candidate_native_nominal_cut_v18_raw'
DEST=p.OUT/'tencent_sampled_quote_hour_diagnostic'
PARENT_DIAGNOSTIC=p.OUT/'tencent_nominal_quote_cut_diagnostic/receipt.json'
WHOLE=p.BASE/'public_source_probes/additional_native_inventory_20261009'
START=[33900000,37800000,46800000,50400000]
END=[37800000,41400000,50400000,54000000]


def main():
    lock=p.locked('diagnose_tencent_sampled_quote_hours');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    mask_identity=next(a['verified_identity'] for a in audit['arrays'] if a['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==mask_identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    parent=p.json.loads(PARENT_DIAGNOSTIC.read_text());assert parent['status']=='complete_nominal_endpoint_diagnostic_no_admission';wanted=defaultdict(lambda:defaultdict(list))
    for h in parent['records']:
        if not mask[cal['dates'].index(h['date'])*4+h['slot'],cal['union_symbols'].index(h['symbol'])]:wanted[h['date']][h['symbol']].append(h)
    result=dict(status='diagnosing_one_fixed_sampled_quote_rule',started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_raw_audit_sha256=p.sha(audit_path),parent_diagnostic_sha256=p.sha(PARENT_DIAGNOSTIC),
        definition='Use source-reported UpdateTime, sort by (UpdateTime,LocalTime,SeqNo), and keep positive actual quote LastPrice/quantity. Coarse end is latest quote reported at or before the fixed10:30,11:30,14:00,15:00 endpoint. Interval LastPrice samples are left-open/right-closed except first auction-inclusive hour. Open is end quote OpenPrice for first hour, last quote at/before left boundary for slots1/3, first observed quote at/after13:00 for slot2. High/Low include this Open and observed LastPrices in the fixed interval. Close is endpoint quote LastPrice, Volume is cumulative quantity minus the previous hour endpoint (zero for slot0). No source cumulative daily HighPrice/LowPrice extrema are imported into later hours. This is an evidence diagnostic, not supplier implementation certification or a replacement for actualM1 bars.',
        requested_hours=sum(len(hs) for stocks in wanted.values() for hs in stocks.values()),source_pins=[],records=[],counts={},candidate_hours_admitted=0,canonical_minute_clock_changed=False,raw_candidate_modified=False,coarse_vendor_implementation_certified=False)
    p.atomic_json(output,result);counts=Counter()
    for date,stocks in sorted(wanted.items()):
        if date=='2026-09-22':
            source_path=WHOLE/'whole_event_archive_sep22/acquisition_and_extraction.json';proof_path=source_path.parent/'independent_member_verification.json'
            source=p.json.loads(source_path.read_text());proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source_path)
            leaves={r['symbol']:r for r in source['records'] if r['kind']=='snapshot'}
        else:
            source_path=WHOLE/'whole_event_archive/full_union_day_native_members/complete_native_member_acquisition.json';proof_path=source_path.parent/'independent_complete_native_member_verification.json'
            source=p.json.loads(source_path.read_text());proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['native_member_acquisition_receipt_sha256']==p.sha(source_path)
            leaves={r['symbol']:r for r in source['records'] if r['kind']=='snapshot'}
        result['source_pins'].append(dict(date=date,receipt_path=str(source_path),receipt_sha256=p.sha(source_path),proof_path=str(proof_path),proof_sha256=p.sha(proof_path)))
        for symbol,hs in sorted(stocks.items()):
            leaf=leaves[symbol];path=p.Path(leaf['path']);before=p.identity(path);vf='TradVolume' if symbol.endswith('.SH') else 'Volume'
            if leaf.get('verified_identity'):assert before==leaf['verified_identity'];pf=p.pq.ParquetFile(path);reused=True
            else:
                # A small quote leaf, not the archive/events; hash during the
                # required read when the older receipt lacks an identity guard.
                data=path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==leaf['sha256'];pf=p.pq.ParquetFile(p.pa.BufferReader(data));reused=False
            cols=['SecurityID','UpdateTime','LocalTime','SeqNo','LastPrice','OpenPrice',vf,'Turnover'];quotes=[]
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=cols,use_threads=False):
                for q in batch.to_pylist():
                    if q['SecurityID']!=symbol[:6]:continue
                    quantity=D(str(q[vf]));price=D(str(q['LastPrice']))*10000;opening=D(str(q['OpenPrice']))*10000;cash=D(str(q['Turnover']))*10000
                    if quantity<=0 or price<=0:continue
                    assert quantity==quantity.to_integral_value() and price==price.to_integral_value() and opening==opening.to_integral_value() and cash==cash.to_integral_value()
                    quotes.append(dict(reported_ms=clock(q['UpdateTime']),received_ms=clock(q['LocalTime']),sequence=str(q['SeqNo']),price_x10000=int(price),open_price_x10000=int(opening),quantity=int(quantity),amount_x10000=int(cash)))
            assert p.identity(path)==before;assert len(quotes)<=10000;quotes.sort(key=lambda q:(q['reported_ms'],q['received_ms'],q['sequence']))
            endpoints={s:next((q for q in reversed(quotes) if q['reported_ms']<=END[s]),None) for s in range(4)}
            for request in sorted(hs,key=lambda h:h['slot']):
                slot=request['slot'];end=endpoints[slot];previous=endpoints.get(slot-1);sample=[q for q in quotes if (START[slot]<=q['reported_ms'] if slot==0 else START[slot]<q['reported_ms']) and q['reported_ms']<=END[slot]]
                opening=end if slot==0 else next((q for q in quotes if START[slot]<=q['reported_ms']<=END[slot]),None) if slot==2 else next((q for q in reversed(quotes) if q['reported_ms']<=START[slot]),None)
                model=None
                if end and opening and sample and (slot==0 or previous):
                    op=end['open_price_x10000'] if slot==0 else opening['price_x10000'];prices=[op]+[q['price_x10000'] for q in sample]
                    model=dict(open=op,high=max(prices),low=min(prices),close=end['price_x10000'],volume=end['quantity']-(previous['quantity'] if previous else 0))
                differences={f:model[f]-request['tencent_hour'][f] for f in request['tencent_hour']} if model else None
                status='fixed_sampled_quote_ohlcv_exact' if differences and not any(differences.values()) else 'fixed_sampled_quote_ohlcv_conflict' if differences else 'actual_quote_endpoint_or_samples_absent'
                counts[status]+=1;result['records'].append(dict(date=date,symbol=symbol,slot=slot,status=status,quote_model=model,tencent_hour=request['tencent_hour'],differences=differences,opening_quote=opening,current_quote=end,previous_quote=previous,observed_price_sample_count=len(sample),quote_source_path=str(path),quote_source_sha256=leaf['sha256'],quote_source_identity=before,accepted_fingerprint_reused_behind_identity_guard=reused,sina_reference_path=request['sina_reference_path'],sina_reference_sha256=request['sina_reference_sha256']))
            result.update(updated_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result)
    assert len(result['records'])==result['requested_hours'] and p.identity(RAW/'dataset/hour_complete.npy')==mask_identity
    result.update(status='complete_one_fixed_sampled_quote_diagnostic_no_admission',finished_utc=p.utc());p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
