"""Independently decode the bounded diagnostic's saved public wire replies."""
import zlib
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    dest=p.OUT/'older_coarse_frequency_and_alias_probe'
    output=dest/'independent_verification.json';assert not output.exists()
    receipt_path=dest/'receipt.json';receipt=p.json.loads(receipt_path.read_text())
    assert receipt['status']=='complete_bounded_reference_diagnostic'
    assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/probe_older_coarse_windows_and_official_alias_bounded.py')
    assert receipt['candidate_hours_admitted']==0 and not receipt['authenticated_credentials_used']
    checks=[]
    for query,r in zip(receipt['requested_queries'],receipt['results'],strict=True):
        source=p.Path(r['wire_path']);before=p.identity(source);raw=source.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==r['wire_sha256'] and raw.endswith(b'<![CDATA[]]>\n')
        header=raw[:21].decode().split('\x01');body=raw[21:21+int(header[2])]
        if header[1]=='96':body=zlib.decompress(body)
        fields=body.decode().split('\x01');assert fields[0]==r['error_code']=='0' and fields[1]==r['error_msg']=='success'
        assert fields[7]==query['code']==r['query_code'] and fields[9]==fields[10]==query['date']
        assert fields[11]==query['frequency']==r['frequency'] and fields[12].split('\n')[0]=='3'
        records=p.json.loads(fields[6]) if fields[6] else []
        if isinstance(records,dict):records=records.get('record',[])
        assert len(records)==r['rows']
        columns=fields[8].split(',');strict=[];deltas=[]
        if records:
            source_minutes=p.Path(r['native_selected_path']);data=source_minutes.read_bytes()
            assert p.hashlib.sha256(data).hexdigest()==r['native_selected_sha256']
            selected=p.json.loads(data);minutes=selected['normalized_bars'];width=int(query['frequency'])
            assert len(minutes)==240 and len(records)==240//width
            for i,row in enumerate(records):
                q=dict(zip(columns,row,strict=True));block=minutes[i*width:(i+1)*width]
                assert q['date']==query['date'] and q['code']==query['code'] and q['adjustflag']=='3'
                end=int(q['time'][8:10])*60+int(q['time'][10:12])
                assert end==(list(range(571,691))+list(range(781,901)))[(i+1)*width-1]
                values=dict(open=D(block[0]['open']),high=max(D(b['high']) for b in block),low=min(D(b['low']) for b in block),close=D(block[-1]['close']),volume=sum(D(b['volume']) for b in block),amount=sum(D(b['amount']) for b in block))
                difference={f:values[f]-D(q[f]) for f in values}
                passed=all(difference[f]==0 for f in ['open','high','low','close']) and abs(difference['volume'])<=100
                deltas.append(dict(end_minute=end,strict_price_volume_gate=passed,differences={f:str(v) for f,v in difference.items()}))
            assert deltas==r['comparisons']
            count=60//width;strict=[s for s in range(4) if all(x['strict_price_volume_gate'] for x in deltas[s*count:(s+1)*count])]
        assert strict==r['strict_reference_slots'] and r['complete_reference']==bool(records)
        assert p.identity(source)==before
        checks.append(dict(symbol=r['symbol'],date=query['date'],frequency=query['frequency'],rows=len(records),strict_reference_slots=strict,wire_identity=before))
    assert len(checks)==6 and sum(c['rows']==0 for c in checks)==5 and checks[-1]['rows']==4
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(receipt_path),queries_verified=checks,
        empty_queries_do_not_establish_all_older_dates_unavailable=True,positive_2021_hour_control_verified=True,coarse_bars_not_synthesized_into_minutes=True,
        candidate_hours_admitted=0,production_ready=False,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))


if __name__=='__main__':main()
