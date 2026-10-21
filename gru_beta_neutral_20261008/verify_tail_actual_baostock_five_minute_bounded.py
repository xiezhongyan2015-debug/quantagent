"""Independent wire decode and actual-minute aggregation of the small M5 probe."""
from collections import Counter
from decimal import Decimal as D
import zlib
from . import bounded_tail_pipeline as p

DEST=p.OUT/'tail_actual_baostock_five_minute_scope_probe'
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'

def main():
    output=DEST/'independent_verification.json';assert not output.exists()
    path=DEST/'receipt.json';probe=p.json.loads(path.read_text())
    assert probe['status']=='complete_scoped_actual_five_minute_diagnostic_no_admission'
    assert p.sha(DEST/'executed_initial_probe_source.py.txt')==probe['source_code_sha256']
    assert probe['raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    cp=p.ROOT/'gru_research/artifacts/minute_calendar.json';assert p.sha(cp)==probe['calendar_sha256']
    cal=p.json.loads(cp.read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    assert p.identity(RAW/'dataset/hour_complete.npy')==probe['raw_mask_identity']
    assert len(probe['scope'])==len(probe['records'])==4
    fields=['open','high','low','close','volume'];grid=list(range(570,690))+list(range(780,900))
    records=[];counts=Counter()
    for expected,r in zip(probe['scope'],probe['records']):
        symbol,date=r['symbol'],r['date'];assert expected=={k:r[k] for k in ['symbol','date','slots']}
        slots=[s for s in range(4) if not mask[cal['dates'].index(date)*4+s,cal['union_symbols'].index(symbol)]];assert slots==r['slots']
        wp=p.Path(r['wire_path']);body=wp.read_bytes();assert len(body)<=512*1024 and p.hashlib.sha256(body).hexdigest()==r['wire_sha256'] and body.endswith(b'<![CDATA[]]>\n')
        header=body[:21].decode().split('\x01');assert header[1]=='96'
        decoder=zlib.decompressobj();decoded=decoder.decompress(body[21:21+int(header[2])],512*1024+1)
        assert len(decoded)<=512*1024 and decoder.eof and not decoder.unconsumed_tail
        parts=decoded.decode().split('\x01')
        assert parts[0]=='0' and parts[2]=='query_history_k_data_plus' and parts[7]==symbol[-2:].lower()+'.'+symbol[:6]
        assert parts[9]==parts[10]==date and parts[11]=='5' and parts[12]=='3'
        payload=p.json.loads(parts[6]);rows=payload['record'] if isinstance(payload,dict) else payload
        refs=[dict(zip(parts[8].split(','),row)) for row in rows];assert len(refs)==r['reference_rows']==48
        assert [int(x['time'][8:10])*60+int(x['time'][10:12]) for x in refs]==[m+1 for m in grid[4::5]]
        assert all(x['date']==date and x['code']==parts[7] and x['adjustflag']=='3' for x in refs)
        mp=p.Path(r['native_minute_path']);assert p.identity(mp)==r['native_minute_identity'] and p.sha(mp)==r['native_minute_sha256']
        native={}
        for batch in p.pq.ParquetFile(mp).iter_batches(batch_size=65536,use_threads=False):
            f=batch.to_pandas()
            for x in f.loc[f.ticker.eq(symbol[:6])].to_dict('records'):
                minute=int(x['left_minute']);assert minute not in native and minute in grid;native[minute]=x
        assert len(native)==r['native_observed_minutes']
        vp=p.Path(r['validation_path']);assert p.sha(vp)==r['validation_sha256']
        v=next(x for x in p.json.loads(vp.read_text())['stockdays'] if x['symbol']==symbol)
        assert v['clocks']['floor_left']['daily_six_fields_exact']==r['source_primary_daily_six_exact']
        assert v['unique_transaction_order_certified']==r['source_transaction_order_flag']
        good=[];matches=Counter();conflicts=[]
        for i,ref in enumerate(refs):
            minutes=grid[i*5:(i+1)*5];missing=[m for m in minutes if m not in native];reported=r['comparisons'][i]
            assert reported['end_minute']==minutes[-1]+1 and reported['missing_native_minutes']==missing
            passed=False
            if not missing:
                bs=[native[m] for m in minutes]
                actual=dict(open=int(bs[0]['open']),close=int(bs[-1]['close']),high=max(int(b['high']) for b in bs),low=min(int(b['low']) for b in bs),volume=sum(int(b['volume']) for b in bs))
                q={f:D(ref[f])*(10000 if f!='volume' else 1) for f in fields};assert all(x.is_finite() and x==x.to_integral_value() for x in q.values())
                delta={f:actual[f]-int(q[f]) for f in fields}
                assert actual==reported['actual'] and delta==reported['differences'] and {f:int(x) for f,x in q.items()}==reported['reference']
                passed=all(delta[f]==0 for f in fields[:4]) and abs(delta['volume'])<=100
                for f in fields:matches[f+'_exact_5m_points']+=delta[f]==0
                matches['volume_within100_5m_points']+=abs(delta['volume'])<=100
                if not passed:conflicts.append(dict(end_minute=minutes[-1]+1,differences=delta))
            assert passed==reported['strict_original_ohlcv_gate_pass'];good.append(passed)
        pass_slots=[s for s in slots if all(good[s*12:(s+1)*12])];assert pass_slots==r['strict_coarse_pass_slots']
        records.append(dict(symbol=symbol,date=date,scoped_actual_missing_slots=slots,observed_native_minutes=len(native),actual_5m_reference_rows=48,
            actual_wire_bytes=len(body),actual_decoded_bytes=len(decoded),wire_verified_identity=p.identity(wp),field_matches=dict(matches),
            strict_coarse_pass_slots=pass_slots,conflicts=conflicts))
        counts['stockdays_checked']+=1;counts['actual_missing_hours_checked']+=len(slots);counts['actual_5m_reference_rows']+=48
        counts['strict_coarse_pass_hours']+=len(pass_slots);counts['native_minutes_read']+=len(native)
        counts['retained_reference_wire_bytes']+=len(body)
    assert counts['strict_coarse_pass_hours']==probe['strict_coarse_diagnostic_hours']==0
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),source_receipt_sha256=p.sha(path),
        counts=dict(counts),records=records,candidate_hours_admitted=0,canonical_minute_clock_changed=False,
        price_volume_fields_rewritten=False,missing_actual_minutes_manufactured=False,original_trade_archives_opened=False,
        initial_probe_protocol_cap_bytes=32*1024**2,initial_probe_acceptance_wire_cap_bytes=512*1024,
        initial_receipt_cap_clarification='Initial generic client used32MiB wire/decoded protocol guard and512KiB post-response acceptance; all four actual wires AND independent decodes were below512KiB. Future scoped client enforces512KiB before acceptance.',
        source_provider_implementation_or_upstream_independence_certified=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(p.json.dumps(dict(counts=counts,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)),flush=True)

if __name__=='__main__':main()
