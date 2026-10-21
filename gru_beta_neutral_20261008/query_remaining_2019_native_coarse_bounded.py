"""Query exact already observed missing days, then compare real M1 to M5 refs."""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .acquire_remaining_2019_native_columns_bounded import DEST,FIELDS,CODES
from .recheck_historical_quote_candidates import GRID
from .resume_gap_references import load

def main():
    lock=p.locked('query_2019_matrix_coarse');out=DEST/'coarse_reference_diagnostic.json';assert not out.exists()
    diag_path=DEST/'independent_daily_diagnostic.json';diag=p.json.loads(diag_path.read_text());assert diag['passed'];acq_path=DEST/'receipt.json';assert p.sha(acq_path)==diag['audited_acquisition_sha256'];acq=p.json.loads(acq_path.read_text())
    requests=[r for r in diag['records'] if r.get('daily_and_positive_pass_slots')];assert len(requests)<=24
    old_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';old=p.json.loads(old_path.read_text());cached={(r['symbol'],r['date']):r for r in old['results']};receipt=dict(status='querying_only_daily_qualified_actual_2019_gap_days',started_utc=p.utc(),source_code_sha256=p.sha(__file__),independent_daily_diagnostic_sha256=p.sha(diag_path),cached_reference_receipt_sha256=p.sha(old_path),queries=[],results=[],candidate_hours_admitted=0,maximum_stockday_queries=len(requests),maximum_session_seconds=300,source_upstream_independence_certified=False,whole_remote_matrix_SHA_verified=False)
    def save():receipt.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(out,receipt)
    save();public=load('public_baostock_probe');client=None;source_lock=(p.ROOT/'acquired_data/data_round2/baostock_session.lock').open('a');p.fcntl.flock(source_lock,p.fcntl.LOCK_EX|p.fcntl.LOCK_NB);start=p.time.monotonic()
    tables={};pins=[]
    try:
        for r in acq['results']:
            assert r['code'] in CODES
            path=p.Path(r['selected_path']);assert p.identity(path)==r['selected_verified_identity'];data=path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['selected_sha256'];tables[r['code'],r['field']]=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist()
        assert set(tables)=={(c,f) for c in CODES for f in FIELDS}
        for request in requests:
            symbol,date=request['symbol'],request['date'];assert symbol[:6] in CODES and p.time.monotonic()-start<300
            prior=cached.get((symbol,date))
            if prior:
                path=p.Path(prior['reference_path']);raw=path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==prior['reference_sha256'];q=p.json.loads(path.with_suffix('.json').read_text());assert q['sha256']==prior['reference_sha256'];reused=True
            else:
                if client is None:
                    client=public.PublicClient(DEST/'coarse_wire');client.socket.settimeout(8);client.login()
                q=client.query(symbol[-2:].lower()+'.'+symbol[:6],date,date,'5');receipt['queries'].append({k:v for k,v in q.items() if k!='data'});reused=False
            assert q['start']==q['end']==date and q['code']==symbol[-2:].lower()+'.'+symbol[:6] and q['frequency']=='5' and q['adjustflag']=='3'
            payload=q.get('data',[]);rows=payload.get('record',[]) if isinstance(payload,dict) else payload;refs=[dict(zip(q['fields'],r)) for r in rows] if q['error_code']=='0' else []
            result=dict(symbol=symbol,date=date,slots=request['daily_and_positive_pass_slots'],wire_path=q['wire_path'],wire_sha256=q['sha256'],reused_previous_actual_wire=reused,reference_rows=len(refs),reference_error_code=q['error_code'],strict_coarse_pass_slots=[])
            if len(refs)==48:
                assert [int(r['time'][8:10])*60+int(r['time'][10:12]) for r in refs]==GRID[4::5]
                selected={f:[r for r in tables[symbol[:6],f] if r['datetime'].date().isoformat()==date] for f in FIELDS};assert all(len(v)==240 for v in selected.values())
                bars=[{f:D(str(selected[f][i]['value'])) for f in FIELDS} for i in range(240)];comparisons=[]
                for i,row in enumerate(refs):
                    block=bars[i*5:(i+1)*5];actual=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount=sum(b['amount'] for b in block));diff={f:actual[f]-D(row[f]) for f in FIELDS};passed=all(diff[f]==0 for f in ['open','high','low','close']) and abs(diff['volume'])<=100
                    comparisons.append(dict(end_minute=GRID[i*5+4],actual={f:str(v) for f,v in actual.items()},reference={f:row[f] for f in FIELDS},differences={f:str(v) for f,v in diff.items()},strict_original_ohlcv_gate_pass=passed))
                result.update(status='complete48_reference_compared',comparisons=comparisons,strict_coarse_pass_slots=[s for s in request['daily_and_positive_pass_slots'] if all(b['strict_original_ohlcv_gate_pass'] for b in comparisons[s*12:(s+1)*12])])
            else:result['status']='no_complete_actual_reference'
            receipt['results'].append(result);save();print(p.json.dumps(dict(date=date,reference_rows=len(refs),strict_slots=result['strict_coarse_pass_slots'])),flush=True)
        receipt.update(status='complete_2019_native_day_coarse_diagnostic_no_admission',finished_utc=p.utc(),strict_coarse_pass_hours=sum(len(r['strict_coarse_pass_slots']) for r in receipt['results']),hours_without_complete_references=sum(len(r['slots']) for r in receipt['results'] if r['reference_rows']!=48))
    except Exception as e:receipt.update(status='failed_terminal',error_type=type(e).__name__,error_detail=str(e),error_stack=p.traceback.format_exc())
    finally:
        if client is not None:
            try:client.socket.settimeout(3);client.close()
            except Exception as e:receipt['logout_error']=str(e)
        save();source_lock.close();lock.close()

if __name__=='__main__':main()
