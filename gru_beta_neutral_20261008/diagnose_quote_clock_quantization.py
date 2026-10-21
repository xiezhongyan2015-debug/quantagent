"""Predeclared global clock quantizers against every 499 original control day.

No rule is selected per stock. This is source-definition diagnosis, not model
selection, historical precision proof, or an admission certificate.
"""
from . import bounded_tail_pipeline as p

WHOLE=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
GRID=list(range(570,690))+list(range(780,900))
FIELDS=['open','high','low','close','volume']
CLOCKS=['UpdateTime','LocalTime']
QUANTIZERS=['native_ms','floor_second','ceil_second','nearest_second']
EDGES=['venue','all_left','all_right']


def milliseconds(text):
    hour,minute,second=text.split(':')
    return int(hour)*3600000+int(minute)*60000+int(p.scale(second))//10


def clock_ms(text):
    hour,minute,second=text.split(':')
    from decimal import Decimal
    return int(hour)*3600000+int(minute)*60000+int(Decimal(second)*1000)


def quantize(values,name):
    if name=='native_ms':return values
    if name=='floor_second':return values//1000*1000
    if name=='ceil_second':return (values+999)//1000*1000
    return (values+500)//1000*1000


def candle(qt,values,indices,half_open,session_ends):
    """All clocks use the same quote-price and cumulative-quantity definition."""
    v=values[indices]
    t=qt[indices]
    left=p.np.array(GRID)*60000
    side='left' if half_open else 'right'
    first=p.np.searchsorted(t,left,side=side)
    stop=p.np.searchsorted(t,left+60000,side=side)
    output=p.np.zeros((240,5),dtype=p.np.int64)
    cumulative_before=0
    for k,minute in enumerate(GRID):
        final=stop[k]-1
        # Corroborated session totals are source-phase facts, not tuned clocks.
        if minute in session_ends:
            final=int(p.np.flatnonzero(indices==session_ends[minute])[0])
        baseline=first[k]-1
        selected=p.np.arange(first[k],stop[k])
        selected=selected[(v[selected,4]>0)&(v[selected,0]>0)]
        if minute in session_ends and final not in selected:
            selected=p.np.r_[selected,final]
        if baseline<0 or final<0 or not len(selected):
            raise ValueError('No observed quote interval')
        opening=v[final,1] if minute==570 else v[baseline,0]
        if minute==780:
            at_open=int(p.np.searchsorted(t,46800000,side='left'))
            candidates=p.np.arange(at_open,stop[k])
            positive=candidates[(v[candidates,4]>0)&(v[candidates,0]>0)]
            if not len(positive):raise ValueError('No observed afternoon open')
            opening=v[positive[0],0]
            baseline=at_open-1
        high=max(int(opening),int(v[selected,0].max()))
        low=min(int(opening),int(v[selected,0].min()))
        if minute==570 or v[final,2]>v[baseline,2]:high=max(high,int(v[final,2]))
        if minute==570 or v[final,3]<v[baseline,3]:low=min(low,int(v[final,3]))
        quantity=int(v[final,4])-cumulative_before
        if quantity<0:raise ValueError('Clock reorders cumulative counter')
        cumulative_before=int(v[final,4])
        output[k]=[opening,high,low,v[final,0],quantity]
    return output


def main():
    process_lock=p.locked('quote_clock_quantization')
    destination=p.OUT/'quote_clock_quantization'
    destination.mkdir(exist_ok=True)
    op=destination/'receipt.json'
    native_path=WHOLE/'full_union_day_native_members/complete_native_member_acquisition.json'
    original_path=WHOLE/'all_original_day_controls/all_original_control_reference_audit.json'
    ends_path=WHOLE/'known_control_quote_range_and_session_end/known_original_quote_ranges_and_fixed_session_end_diagnostics.json'
    leaves={(r['symbol'],r['kind']):r for r in p.json.loads(native_path.read_text())['records']}
    originals=p.json.loads(original_path.read_text())['records']
    ends={r['symbol']:r for r in p.json.loads(ends_path.read_text())['records']}
    observed=p.json.loads((WHOLE/'full_union_observed_quote_minute_layer_v2/full_union_observed_quote_minute_layer_receipt.json').read_text())
    observed_by_symbol={r['symbol']:r for r in observed['records']}
    models=[(clock,quantizer,edge) for clock in CLOCKS for quantizer in QUANTIZERS for edge in EDGES]
    keys=['__'.join(model) for model in models]
    d=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),original_control_stockdays_requested=499,
        clock_models_predeclared=keys,source_receipt_sha256=p.sha(native_path),original_receipt_sha256=p.sha(original_path),
        native_session_end_proof_sha256=p.sha(ends_path),records=[],totals={k:dict(stockdays=0,full_exact_stockdays=0,exact_minutes=0,
        exact_by_field={f:0 for f in FIELDS},invalid_clock_stockdays=0) for k in keys},candidate_hours_admitted=0,status='diagnosing')
    for ordinal,record in enumerate(sorted(originals,key=lambda r:r['symbol'])):
        symbol=record['symbol']
        ref=p.pq.ParquetFile(record['path']).read(columns=FIELDS).to_pandas()
        actual=ref.to_numpy(float)
        actual[:,:4]*=10000
        assert p.np.max(abs(actual-p.np.rint(actual)))<1e-6
        original=p.np.rint(actual).astype(p.np.int64)
        volume_field='TradVolume' if symbol.endswith('.SH') else 'Volume'
        phase='InstruStatus' if symbol.endswith('.SH') else 'TradingPhaseCode'
        file=leaves[symbol,'snapshot']['path']
        q=p.pq.ParquetFile(file).read(columns=['UpdateTime','LocalTime','SeqNo','LastPrice','OpenPrice','HighPrice','LowPrice',volume_field,phase]).to_pandas()
        clocks={name:p.np.array([clock_ms(t) for t in q[name]],dtype=p.np.int64) for name in CLOCKS}
        base=q[['LastPrice','OpenPrice','HighPrice','LowPrice',volume_field]].to_numpy(float)
        base[:,:4]*=10000
        assert p.np.isfinite(base).all() and p.np.max(abs(base-p.np.rint(base)))<1e-6
        values=p.np.rint(base).astype(p.np.int64)
        qtime=clocks['UpdateTime']
        # Resolve producer's independently certified original source quote index.
        producer_order=p.np.lexsort((q.SeqNo.to_numpy(),clocks['LocalTime'],qtime))
        minutes=p.pq.ParquetFile(observed_by_symbol[symbol]['native_minute_artifact']['path']).read(columns=['left_minute','source_end_quote_sorted_index']).to_pylist()
        session_ends={r['left_minute']:int(producer_order[r['source_end_quote_sorted_index']]) for r in minutes if r['left_minute'] in [689,899]}
        summary=dict(symbol=symbol,source_snapshot_identity=p.identity(file),source_snapshot_prior_verified_sha256=leaves[symbol,'snapshot']['sha256'],models={})
        for (clock,quantizer,edge),key in zip(models,keys):
            t=quantize(clocks[clock],quantizer)
            # Preserve original precise-clock/sequence ordering within a quantized second.
            indices=p.np.lexsort((q.SeqNo.to_numpy(),clocks['LocalTime'],clocks[clock],t))
            half_open=symbol.endswith('.SH') if edge=='venue' else edge=='all_left'
            total=d['totals'][key]
            total['stockdays']+=1
            try:
                bars=candle(t,values,indices,half_open,session_ends)
                equal=bars==original
                exact=equal.all(axis=1)
                item=dict(exact_minutes=int(exact.sum()),exact_by_field={f:int(equal[:,i].sum()) for i,f in enumerate(FIELDS)},
                    failed_minutes=[GRID[i] for i in p.np.flatnonzero(~exact)])
                total['full_exact_stockdays']+=bool(exact.all())
                total['exact_minutes']+=int(exact.sum())
                for f,n in item['exact_by_field'].items():total['exact_by_field'][f]+=n
            except ValueError as e:
                total['invalid_clock_stockdays']+=1
                item=dict(status='source_clock_cannot_form_all_intervals',error=str(e))
            summary['models'][key]=item
        d['records'].append(summary)
        if (ordinal+1)%25==0:
            d.update(stockdays_checked=ordinal+1,updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
            p.atomic_json(op,d)
    d.update(status='complete_global_diagnostics',stockdays_checked=499,finished_utc=p.utc(),
        globally_exact_models=[key for key,total in d['totals'].items() if total['full_exact_stockdays']==499],
        candidate_changed=False,minute_definition_admitted=False)
    p.atomic_json(op,d)
    process_lock.close()


if __name__=='__main__':main()
