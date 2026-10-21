"""Decode actual provider bytes and reproduce all192 real five-minute comparisons."""
import zlib
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p
from .acquire_late_remaining_native_matrix_columns_bounded import DEST,FIELDS

def main():
    output=DEST/'independent_coarse_verification.json';assert not output.exists();path=DEST/'coarse_reference_diagnostic.json';coarse=p.json.loads(path.read_text());assert coarse['status']=='complete_late_native_day_coarse_diagnostic_no_admission' and len(coarse['results'])==4
    diag_path=DEST/'independent_daily_diagnostic.json';diag=p.json.loads(diag_path.read_text());assert diag['passed'] and p.sha(diag_path)==coarse['independent_daily_diagnostic_sha256'];acq_path=DEST/'receipt.json';acq=p.json.loads(acq_path.read_text());assert p.sha(acq_path)==diag['audited_acquisition_sha256']
    requested={(r['symbol'],r['date']):r for r in diag['records'] if r['daily_and_positive_pass_slots']};assert len(requested)==4
    original={};pins=[]
    for r in acq['results']:
        leaf=p.Path(r['selected_path']);assert p.identity(leaf)==r['selected_verified_identity'];data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['selected_sha256'];original[r['code'],r['field']]=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist()
    records=[];counts=Counter();grid=list(range(571,691))+list(range(781,901))
    for r in coarse['results']:
        symbol,date=r['symbol'],r['date'];assert (symbol,date) in requested;wire=p.Path(r['wire_path']);raw=wire.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['wire_sha256'] and raw.endswith(b'<![CDATA[]]>\n') and len(raw)<1024**2
        header=raw[:21].decode().split('\x01');assert header[1]=='96';decoder=zlib.decompressobj();parts=decoder.decompress(raw[21:21+int(header[2])],1024**2).decode().split('\x01');assert decoder.eof and not decoder.unconsumed_tail
        assert parts[0]=='0' and parts[2]=='query_history_k_data_plus' and parts[7]==symbol[-2:].lower()+'.'+symbol[:6] and parts[9]==parts[10]==date and parts[11]=='5' and parts[12]=='3'
        payload=p.json.loads(parts[6]);rows=payload['record'] if isinstance(payload,dict) else payload;fields=parts[8].split(',');refs=[dict(zip(fields,row)) for row in rows];assert len(refs)==r['reference_rows']==48
        assert [int(x['time'][8:10])*60+int(x['time'][10:12]) for x in refs]==grid[4::5]
        columns={f:[x for x in original[symbol[:6],f] if x['datetime'].date().isoformat()==date] for f in FIELDS};assert all(len(x)==240 for x in columns.values())
        for f in FIELDS:assert [x['datetime'].hour*60+x['datetime'].minute for x in columns[f]]==grid
        good=[];differences=[];fieldmatches=Counter()
        for i,ref in enumerate(refs):
            b={f:[D(str(x['value'])) for x in columns[f][i*5:(i+1)*5]] for f in FIELDS}
            actual=dict(open=b['open'][0],close=b['close'][-1],high=max(b['high']),low=min(b['low']),volume=sum(b['volume']),amount=sum(b['amount']));delta={f:actual[f]-D(ref[f]) for f in FIELDS};reported=r['comparisons'][i]
            assert reported['actual']=={f:str(v) for f,v in actual.items()} and reported['differences']=={f:str(v) for f,v in delta.items()} and reported['reference']=={f:ref[f] for f in FIELDS}
            passed=all(delta[f]==0 for f in ['open','high','low','close']) and abs(delta['volume'])<=100;assert passed==reported['strict_original_ohlcv_gate_pass'];good.append(passed)
            for f in FIELDS:fieldmatches[f+'_exact_points']+=delta[f]==0
            fieldmatches['volume_within100_points']+=abs(delta['volume'])<=100
            differences.append(dict(end_minute=grid[i*5+4],differences={f:str(v) for f,v in delta.items()}))
        passedslots=[s for s in requested[symbol,date]['daily_and_positive_pass_slots'] if all(good[s*12:(s+1)*12])];assert passedslots==r['strict_coarse_pass_slots']
        records.append(dict(symbol=symbol,date=date,original_minutes_compared=240,actual_coarse_rows=48,field_matches=dict(fieldmatches),strict_coarse_pass_slots=passedslots,wire_path=str(wire),wire_sha256=r['wire_sha256'],wire_verified_identity=p.identity(wire),differences=differences));counts['stockdays_checked']+=1;counts['coarse_points_checked']+=48;counts['strict_coarse_pass_hours']+=len(passedslots)
    assert counts['strict_coarse_pass_hours']==coarse['strict_coarse_pass_hours']==0
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_coarse_diagnostic_sha256=p.sha(path),audited_native_daily_diagnostic_sha256=p.sha(diag_path),counts=dict(counts),records=records,original_price_volume_gates_unchanged=True,coarse_bars_not_allocated_to_minutes=True,candidate_hours_admitted=0,whole_remote_field_SHA_verified=False,source_upstream_independence_certified=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));print(p.json.dumps(dict(counts=counts,fieldmatches=[r['field_matches'] for r in records])),flush=True)
if __name__=='__main__':main()
