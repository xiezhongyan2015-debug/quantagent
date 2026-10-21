"""Small, scope-guarded independent M5 diagnostic of actual tail trades.

No M5 bar becomes an M1 bar. Prices and quantities retain their actual source
values; the existing minute clock and historical M5 comparison gate are fixed.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .resume_gap_references import load

DEST=p.OUT/'tail_actual_baostock_five_minute_scope_probe'
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
SPECS=[('000031.SZ','2026-08-07'),('000099.SZ','2026-09-18'),
       ('000031.SZ','2026-09-22'),('000031.SZ','2026-09-23')]
GRID=list(range(570,690))+list(range(780,900))
FIELDS=['open','high','low','close','volume']

def main():
    lock=p.locked('tail_actual_baostock_five_minute_probe');DEST.mkdir(exist_ok=True)
    op=DEST/'receipt.json';assert not op.exists()
    cp=p.ROOT/'gru_research/artifacts/minute_calendar.json';cal=p.json.loads(cp.read_text())
    mp=RAW/'dataset/hour_complete.npy';mask=p.np.load(mp,mmap_mode='r')
    pp=p.OUT/'minute_observation_progress.json';progress=p.json.loads(pp.read_text())
    groups={(g['date'],g['prefix']):g for g in progress['groups']}
    scope=[]
    for symbol,date in SPECS:
        assert date in cal['dates'] and symbol in cal['union_symbols']
        slots=[s for s in range(4) if not mask[cal['dates'].index(date)*4+s,cal['union_symbols'].index(symbol)]]
        assert slots and (date,symbol[:2]) in groups
        scope.append(dict(symbol=symbol,date=date,slots=slots))
    receipt=dict(status='running_scope_guarded_actual_five_minute_probe',started_utc=p.utc(),source_code_sha256=p.sha(__file__),
        calendar_sha256=p.sha(cp),raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),raw_mask_identity=p.identity(mp),
        scope=scope,maximum_queries=4,maximum_session_seconds=180,maximum_response_wire_and_decoded_bytes=512*1024,
        original_trade_archives_opened=False,minute_clock_changed=False,candidate_hours_admitted=0,raw_panel_modified=False,
        five_minute_rows_allocated_to_minutes=False,personal_credentials_used=False,records=[],queries=[])
    def save():
        receipt.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
        p.atomic_json(op,receipt)
    save();public=load('public_baostock_probe');client=None
    source_lock=(p.ROOT/'acquired_data/data_round2/baostock_session.lock').open('a');p.fcntl.flock(source_lock,p.fcntl.LOCK_EX|p.fcntl.LOCK_NB)
    start=p.time.monotonic()
    try:
        for request in scope:
            assert p.time.monotonic()-start<180
            symbol,date=request['symbol'],request['date'];g=groups[date,symbol[:2]]
            leaf=next(r for r in g['minute_files'] if r['model']=='floor_left');path=p.Path(leaf['path']);assert p.sha(path)==leaf['sha256']
            rows=[]
            for batch in p.pq.ParquetFile(path).iter_batches(batch_size=65536,use_threads=False):
                f=batch.to_pandas();rows.extend(f.loc[f.ticker.eq(symbol[:6])].to_dict('records'))
            native={int(r['left_minute']):r for r in rows};assert len(native)==len(rows) and set(native)<=set(GRID)
            vp=p.Path(g['validation_path']);assert p.sha(vp)==g['validation_sha256']
            validation=next(r for r in p.json.loads(vp.read_text())['stockdays'] if r['symbol']==symbol)
            if client is None:
                client=public.PublicClient(DEST/'coarse_wire',max_response_bytes=512*1024);client.socket.settimeout(8);client.login()
            q=client.query(symbol[-2:].lower()+'.'+symbol[:6],date,date,'5')
            assert q['bytes']<=512*1024
            receipt['queries'].append({k:v for k,v in q.items() if k!='data'})
            assert q['start']==q['end']==date and q['frequency']=='5' and q['adjustflag']=='3'
            payload=q.get('data',[]);wire_rows=payload.get('record',[]) if isinstance(payload,dict) else payload
            refs=[dict(zip(q['fields'],r)) for r in wire_rows] if q['error_code']=='0' else []
            result=dict(**request,reference_rows=len(refs),reference_error_code=q['error_code'],wire_path=q['wire_path'],wire_sha256=q['sha256'],
                native_minute_path=str(path),native_minute_sha256=leaf['sha256'],native_minute_identity=p.identity(path),
                validation_path=str(vp),validation_sha256=g['validation_sha256'],native_observed_minutes=len(native),
                source_primary_daily_six_exact=validation['clocks']['floor_left']['daily_six_fields_exact'],
                source_transaction_order_flag=validation['unique_transaction_order_certified'],comparisons=[],strict_coarse_pass_slots=[])
            if len(refs)==48:
                assert [int(r['time'][8:10])*60+int(r['time'][10:12]) for r in refs]==[m+1 for m in GRID[4::5]]
                for i,row in enumerate(refs):
                    minutes=GRID[i*5:(i+1)*5];missing=[m for m in minutes if m not in native]
                    c=dict(end_minute=minutes[-1]+1,missing_native_minutes=missing,strict_original_ohlcv_gate_pass=False)
                    if not missing:
                        block=[native[m] for m in minutes]
                        actual=dict(open=int(block[0]['open']),close=int(block[-1]['close']),high=max(int(b['high']) for b in block),low=min(int(b['low']) for b in block),volume=sum(int(b['volume']) for b in block))
                        ref={f:D(row[f])*(10000 if f!='volume' else 1) for f in FIELDS}
                        assert all(v.is_finite() and v==v.to_integral_value() for v in ref.values())
                        delta={f:actual[f]-int(ref[f]) for f in FIELDS}
                        c.update(actual=actual,reference={f:int(v) for f,v in ref.items()},differences=delta,
                            strict_original_ohlcv_gate_pass=all(delta[f]==0 for f in FIELDS[:4]) and abs(delta['volume'])<=100)
                    result['comparisons'].append(c)
                result['strict_coarse_pass_slots']=[s for s in request['slots'] if all(c['strict_original_ohlcv_gate_pass'] for c in result['comparisons'][s*12:(s+1)*12])]
                result['status']='complete_actual48_reference_compared_without_admission'
            else:result['status']='no_complete_actual_reference'
            receipt['records'].append(result);save();print(p.json.dumps({k:result[k] for k in ['symbol','date','reference_rows','strict_coarse_pass_slots']}),flush=True)
        receipt.update(status='complete_scoped_actual_five_minute_diagnostic_no_admission',finished_utc=p.utc(),
            strict_coarse_diagnostic_hours=sum(len(r['strict_coarse_pass_slots']) for r in receipt['records']))
    except Exception as e:
        receipt.update(status='failed_terminal',error_type=type(e).__name__,error=str(e),error_stack=p.traceback.format_exc())
    finally:
        if client is not None:
            try:client.socket.settimeout(3);client.close()
            except Exception as e:receipt['logout_error']=str(e)
        save();source_lock.close();lock.close()

if __name__=='__main__':main()
