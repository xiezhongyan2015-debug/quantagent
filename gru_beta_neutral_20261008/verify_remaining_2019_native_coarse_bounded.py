"""Reproduce actual provider wire and native comparisons, including empty replies."""
import zlib
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p
from .acquire_remaining_2019_native_columns_bounded import DEST, FIELDS
from .recheck_historical_quote_candidates import GRID

def main():
    output=DEST/'independent_coarse_verification.json';assert not output.exists()
    path=DEST/'coarse_reference_diagnostic.json';coarse=p.json.loads(path.read_text())
    assert coarse['status']=='complete_2019_native_day_coarse_diagnostic_no_admission'
    dp=DEST/'independent_daily_diagnostic.json';daily=p.json.loads(dp.read_text())
    assert daily['passed'] and p.sha(dp)==coarse['independent_daily_diagnostic_sha256']
    ap=DEST/'receipt.json';acq=p.json.loads(ap.read_text());assert p.sha(ap)==daily['audited_acquisition_sha256']
    wanted={(r['symbol'],r['date']):r for r in daily['records'] if r.get('daily_and_positive_pass_slots')}
    assert len(coarse['results'])==len(wanted)<=24
    original={}
    for r in acq['results']:
        leaf=p.Path(r['selected_path']);assert p.identity(leaf)==r['selected_verified_identity'] and p.sha(leaf)==r['selected_sha256']
        original[r['code'],r['field']]=p.pq.ParquetFile(leaf).read(use_threads=False).to_pylist()
    counts=Counter();records=[];seen=set()
    for r in coarse['results']:
        symbol,date=r['symbol'],r['date'];key=(symbol,date);assert key in wanted and key not in seen and date.startswith('2019-');seen.add(key)
        wire=p.Path(r['wire_path']);raw=wire.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['wire_sha256'] and len(raw)<1024**2 and raw.endswith(b'<![CDATA[]]>\n')
        header=raw[:21].decode().split('\x01');assert header[1]=='96'
        decoder=zlib.decompressobj();parts=decoder.decompress(raw[21:21+int(header[2])],1024**2).decode().split('\x01')
        assert decoder.eof and not decoder.unconsumed_tail
        assert parts[0]==r['reference_error_code']=='0' and parts[2]=='query_history_k_data_plus' and parts[7]==symbol[-2:].lower()+'.'+symbol[:6]
        assert parts[9]==parts[10]==date and parts[11]=='5' and parts[12]=='3'
        payload=p.json.loads(parts[6]);rows=payload['record'] if isinstance(payload,dict) else payload
        refs=[dict(zip(parts[8].split(','),row)) for row in rows];assert len(refs)==r['reference_rows']
        assert len(refs) in [0,48]
        qualified=[];matches=Counter()
        if refs:
            assert [int(x['time'][8:10])*60+int(x['time'][10:12]) for x in refs]==GRID[4::5]
            columns={f:[x for x in original[symbol[:6],f] if x['datetime'].date().isoformat()==date] for f in FIELDS}
            assert all(len(x)==240 for x in columns.values())
            for f in FIELDS:assert [x['datetime'].hour*60+x['datetime'].minute for x in columns[f]]==GRID
            good=[]
            for i,ref in enumerate(refs):
                b={f:[D(str(x['value'])) for x in columns[f][i*5:(i+1)*5]] for f in FIELDS}
                actual=dict(open=b['open'][0],close=b['close'][-1],high=max(b['high']),low=min(b['low']),volume=sum(b['volume']),amount=sum(b['amount']))
                delta={f:actual[f]-D(ref[f]) for f in FIELDS};reported=r['comparisons'][i]
                assert reported['actual']=={f:str(v) for f,v in actual.items()} and reported['differences']=={f:str(v) for f,v in delta.items()} and reported['reference']=={f:ref[f] for f in FIELDS}
                ok=all(delta[f]==0 for f in ['open','high','low','close']) and abs(delta['volume'])<=100
                assert ok==reported['strict_original_ohlcv_gate_pass'];good.append(ok)
                for f in FIELDS:matches[f+'_exact_points']+=delta[f]==0
            qualified=[s for s in wanted[key]['daily_and_positive_pass_slots'] if all(good[s*12:(s+1)*12])]
        else:
            assert r['status']=='no_complete_actual_reference';counts['actual_empty_reference_days']+=1
            counts['hours_without_complete_reference']+=len(r['slots'])
        assert qualified==r['strict_coarse_pass_slots'];counts['strict_coarse_pass_hours']+=len(qualified);counts['stockdays_checked']+=1;counts['actual_coarse_points']+=len(refs)
        records.append(dict(symbol=symbol,date=date,reference_rows=len(refs),strict_coarse_pass_slots=qualified,field_matches=dict(matches),wire_path=str(wire),wire_sha256=r['wire_sha256'],wire_verified_identity=p.identity(wire)))
    assert set(wanted)==seen and counts['strict_coarse_pass_hours']==coarse['strict_coarse_pass_hours']
    assert counts['hours_without_complete_reference']==coarse['hours_without_complete_references']
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_coarse_receipt_sha256=p.sha(path),audited_daily_receipt_sha256=p.sha(dp),counts=dict(counts),records=records,candidate_hours_admitted=0,empty_reference_not_interpreted_as_halt=True,original_price_volume_gates_unchanged=True,coarse_bars_not_allocated_to_minutes=True,source_upstream_independence_certified=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(p.json.dumps(dict(counts=counts)),flush=True)

if __name__=='__main__':main()
