"""Reconcile minute-exact references with one fixed coarse carry-open formula.

Diagnostic only. Never choose a new minute clock or a best model for each bar.
Native minute definitions stay first/max/min/last actual trades. The alternative
coarse formula opens at the observed preceding last price and includes that
price in the coarse high/low; close and actual quantity do not change.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

RAW=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_raw'
DEST=p.OUT/'tail_previous_close_coarse_reconciliation'


def main():
    lock=p.locked('tail_previous_close_diagnostic');DEST.mkdir(exist_ok=True)
    output=DEST/'receipt.json';assert not output.exists()
    raw_audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(raw_audit_path.read_text())
    assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy')
    assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    days={d:i*4 for i,d in enumerate(cal['dates'])};symbols={s:i for i,s in enumerate(cal['union_symbols'])}
    pins=[];leaves=defaultdict(list);seen=set()
    for name in ['sina_tail_minute_expanded','sina_tail_nonmonotonic_references']:
        source=p.OUT/name/'independent_verification.json';proof=p.json.loads(source.read_text())
        assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source.parent/'receipt.json')
        pins.append(dict(path=str(source),sha256=p.sha(source)))
        for h in proof['qualified_minute_diagnostic_hours']:
            key=h['date'],h['symbol'],h['slot'];assert key not in seen;seen.add(key)
            leaves[h['native_minute_path']].append(h)
    ms=[]
    for name,date in [('millisecond_pairing','2026-09-23'),('millisecond_pairing_sep22','2026-09-22')]:
        path=p.OUT/name/'receipt.json';pair=p.json.loads(path.read_text());order_path=path.parent/'intrasecond_order_verification.json';order=p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked']==624
        assert order['paired_source_receipt_sha256']==p.sha(path)
        assert order['intrasecond_event_inversions']==order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==0
        assert p.identity(p.Path(pair['native_field_artifact']['path']))==order['accepted_artifact_identity_guard']
        ms.append(dict(date=date,pairing_path=str(path),pairing_sha256=p.sha(path),order_path=str(order_path),order_sha256=p.sha(order_path)))
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text())
    coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof=p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    refs={}
    for g in coarse['groups']:
        if g['date'] not in days or g['date'] not in ['2026-09-22','2026-09-23']:continue
        data=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest()==g['comparison_sha256']
        for h in p.json.loads(data)['comparisons']:
            if h['model']=='floor_left':refs[h['date'],h['symbol'],h['slot']]=h
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_raw_audit_sha256=p.sha(raw_audit_path),
        source_minute_proof_pins=pins,source_millisecond_proof_pins=ms,coarse_comparison_sha256=p.sha(coarse_path),coarse_independent_proof_sha256=p.sha(coarse_proof_path),
        fixed_formula='Coarse open=last observed trade price strictly before hour start (09:30 uses actual auction open); high/low include that price; close=last actual trade in native fixed hour; quantity=sum actual native fixed-hour quantity. Native minute OHLCV never changed.',
        candidate_hours_admitted=0,source_values_changed=False,source_vendor_implementation_certified=False,production_ready=False,records=[],leaf_pins=[])
    counts=Counter();missing_differences=Counter()
    for path,requests in sorted(leaves.items()):
        source=p.Path(path);before=p.identity(source);data=source.read_bytes();digest=p.hashlib.sha256(data).hexdigest()
        assert all(h['native_minute_sha256']==digest for h in requests)
        frame=p.pq.ParquetFile(p.pa.BufferReader(data)).read(columns=['ticker','left_minute','open','high','low','close','volume','amount_x10000','trades']).to_pandas()
        result['leaf_pins'].append(dict(path=path,sha256=digest,verified_identity=before))
        by_symbol=defaultdict(list)
        for h in requests:by_symbol[h['symbol']].append(h)
        for symbol,requested in sorted(by_symbol.items()):
            stock=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute').sort_index();assert not stock.index.duplicated().any()
            qpath=p.Path(requested[0]['sina_reference_path']);qbefore=p.identity(qpath);qdata=qpath.read_bytes();qdigest=p.hashlib.sha256(qdata).hexdigest()
            assert all(h['sina_reference_sha256']==qdigest for h in requested)
            text=qdata.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);qrefs={r['day']:r for r in rows}
            for h in sorted(requested,key=lambda r:r['slot']):
                date,slot=h['date'],h['slot'];start=[570,630,780,840][slot];block=stock.loc[list(range(start,start+60))]
                assert len(block)==60 and (block.volume>0).all() and (block.trades>0).all()
                for minute,row in block.iterrows():
                    end=int(minute)+1;q=qrefs[f'{date} {end//60:02d}:{end%60:02d}:00']
                    assert all(int(row[f])==D(q[f])*10000 for f in ['open','high','low','close']) and D(int(row.volume))==D(q['volume'])
                native=dict(open=int(block.open.iloc[0]),high=int(block.high.max()),low=int(block.low.min()),close=int(block.close.iloc[-1]),volume=int(block.volume.sum()))
                coarse_hour=refs[date,symbol,slot];assert all(native[f]==coarse_hour['native_hour'][f] for f in native)
                reference={f:native[f]-coarse_hour['differences'][f] for f in native}
                prior=stock.loc[stock.index<start];previous_minute=int(prior.index[-1]) if len(prior) else None
                previous_price=int(prior.close.iloc[-1]) if len(prior) else native['open']
                previous_ref_exact=False
                if previous_minute is not None:
                    end=previous_minute+1;q=qrefs.get(f'{date} {end//60:02d}:{end%60:02d}:00')
                    previous_ref_exact=q is not None and D(q['close'])*10000==previous_price
                model=dict(native,open=previous_price,high=max(native['high'],previous_price),low=min(native['low'],previous_price))
                same=all(model[f]==reference[f] for f in native)
                complete=bool(mask[days[date]+slot,symbols[symbol]])
                counts['minute_exact_diagnostic_hours']+=1;counts['already_admitted_raw_hours']+=complete
                counts['fixed_coarse_formula_exact_all_hours']+=same
                if not complete:
                    counts['remaining_minute_exact_hours']+=1;counts['remaining_fixed_formula_exact']+=same
                    counts['remaining_fixed_formula_and_previous_close_reference_exact']+=same and previous_ref_exact
                    missing_differences[','.join(sorted(f for f,v in coarse_hour['differences'].items() if v))]+=1
                result['records'].append(dict(date=date,symbol=symbol,slot=slot,raw_already_admitted=complete,native=native,coarse_reference=reference,original_coarse_differences=coarse_hour['differences'],
                    previous_observed_minute=previous_minute,previous_observed_close=previous_price,previous_close_independent_minute_exact=previous_ref_exact,
                    fixed_formula=model,fixed_formula_all5_exact=same,independent_minute_path=str(qpath),independent_minute_sha256=qdigest,native_minute_path=path,native_minute_sha256=digest))
            assert p.identity(qpath)==qbefore
        assert p.identity(source)==before
        del data,frame
    assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    result.update(status='complete_fixed_formula_diagnostic_no_admission',finished_utc=p.utc(),counts=dict(counts),remaining_difference_counts=dict(missing_differences),
        no_per_hour_clock_or_model_selection=True,other_coarse_conflicts_preserved=True,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
