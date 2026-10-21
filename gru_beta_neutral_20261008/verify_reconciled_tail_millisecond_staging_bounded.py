"""Independent bounded SQL audit of104 reconciled native millisecond hours."""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stage_reconciled_tail_millisecond_minutes_bounded import DEST,RAW
import duckdb


def main():
    lock=p.locked('verify_reconciled_tail_ms_staging');path=DEST/'receipt.json';stage=p.json.loads(path.read_text())
    output=DEST/'independent_verification.json';assert not output.exists()
    assert stage['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_reconciled_tail_millisecond_minutes_bounded.py')
    assert stage['status']=='staged_actual_millisecond_hours_pending_independent_audit' and not stage['raw_panel_modified']
    diagnostic_path=p.OUT/'tail_previous_close_coarse_reconciliation/receipt.json';assert p.sha(diagnostic_path)==stage['coarse_reconciliation_receipt_sha256']
    proof_path=diagnostic_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and p.sha(proof_path)==stage['coarse_reconciliation_independent_proof_sha256'] and proof['audited_receipt_sha256']==p.sha(diagnostic_path)
    selected={(h['date'],h['symbol'],h['slot']) for h in proof['qualified_fixed_formula_diagnostic_hours']}
    diagnostic=p.json.loads(diagnostic_path.read_text());source_checks={(h['date'],h['symbol'],h['slot']):h for h in diagnostic['records']}
    assert {(r['date'],r['symbol'],h['slot']) for r in stage['records'] for h in r['hours']}==selected
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and p.sha(audit_path)==stage['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    counts=Counter();verified=[]
    spill=DEST/'independent_sql_spill';spill.mkdir(exist_ok=True);con=duckdb.connect()
    con.execute("SET memory_limit='192MB'");con.execute('SET threads=1');con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='2GB'")
    for pin in stage['native_source_pins']:
        date=pin['date'];pairing_path=p.Path(pin['pairing_path']);assert p.sha(pairing_path)==pin['pairing_sha256'];pairing=p.json.loads(pairing_path.read_text())
        order_path=p.Path(pin['order_path']);assert p.sha(order_path)==pin['order_sha256'];order=p.json.loads(order_path.read_text())
        assert order['paired_source_receipt_sha256']==p.sha(pairing_path) and pairing['all_rows_price_quantity_and_floor_clock_exact']
        assert order['intrasecond_event_inversions']==order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==0
        artifact=p.Path(pairing['native_field_artifact']['path']);assert p.identity(artifact)==order['accepted_artifact_identity_guard']
        records={r['symbol'][:6]:r for r in stage['records'] if r['date']==date};assert records
        con.read_parquet(str(artifact)).create_view('all_native',replace=True);con.register('selected',p.pa.table({'ticker':sorted(records)}))
        con.execute('CREATE OR REPLACE VIEW native AS SELECT n.*,CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native n JOIN selected s USING(ticker)')
        cursor=con.execute('SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),COUNT(*),MIN(event_ms),MAX(event_ms),MAX(GREATEST(event_ms,local_ms)),ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id)) FROM native GROUP BY ticker,minute ORDER BY ticker,minute')
        current=None;bars={}

        def finish(ticker,actual):
            r=records[ticker];symbol=r['symbol'];source=p.Path(r['primary_source_path']);data=source.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['primary_source_sha256'];primary=p.json.loads(data)
            day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==date);assert day['tradestatus']=='1'
            total=[actual[min(actual)]['open'],max(b['high'] for b in actual.values()),min(b['low'] for b in actual.values()),actual[max(actual)]['close'],sum(b['volume'] for b in actual.values()),sum(b['amount_x10000'] for b in actual.values())]
            assert total==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
            leaf=p.Path(r['minute_path']);assert p.identity(leaf)==r['verified_identity'];data=leaf.read_bytes();assert p.hashlib.sha256(data).hexdigest()==r['minute_sha256'];rows=p.pq.ParquetFile(p.pa.BufferReader(data)).read().to_pylist()
            byminute={b['left_minute']:b for b in rows};assert len(rows)==len(byminute)==len(r['hours'])*60
            fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_last_event_ms','source_dependency_ready_ms']
            for b in rows:
                assert b['symbol']==symbol and b['date']==date and b['source_kind']=='actual_millisecond_trade' and b['volume']>0 and b['trades']>0
                assert all(b[f]==actual[b['left_minute']][f] for f in fields);counts['actual_minutes_verified']+=1
            ref_cache={}
            for h in r['hours']:
                slot=h['slot'];request=source_checks[date,symbol,slot];assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
                assert h['sina_reference_path']==request['independent_minute_path'] and h['sina_reference_sha256']==request['independent_minute_sha256']
                qpath=h['sina_reference_path']
                if qpath not in ref_cache:
                    data=p.Path(qpath).read_bytes();assert p.hashlib.sha256(data).hexdigest()==h['sina_reference_sha256'];text=data.decode();qrows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);ref_cache[qpath]={q['day']:q for q in qrows}
                refs=ref_cache[qpath];start=[570,630,780,840][slot];block=[actual[m] for m in range(start,start+60)]
                for m in range(start,start+60):
                    end=m+1;q=refs[f'{date} {end//60:02d}:{end%60:02d}:00'];b=actual[m]
                    assert all(b[f]==D(q[f])*10000 for f in ['open','high','low','close']) and D(b['volume'])==D(q['volume'])
                values=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),source_event_ms_max=max(b['source_last_event_ms'] for b in block),source_ready_ms=max(b['source_dependency_ready_ms'] for b in block))
                assert all(h[f]==v for f,v in values.items())
                previous=max(m for m in actual if m<start);carry=actual[previous]['close'];end=previous+1;q=refs[f'{date} {end//60:02d}:{end%60:02d}:00']
                assert D(q['close'])*10000==carry and h['previous_observed_minute']==previous and h['previous_observed_close']==carry
                coarse=dict(open=carry,high=max(carry,values['high']),low=min(carry,values['low']),close=values['close'],volume=values['volume'])
                assert coarse==h['coarse_reference']==request['coarse_reference'] and coarse==request['fixed_formula']
                assert {f:values[f] for f in request['native']}==request['native'] and h['coarse_definition_reconciliation_exact'] and h['independent_minute60_ohlcv_exact']
                counts['qualified_hours']+=1
            assert p.identity(leaf)==r['verified_identity'];verified.append(dict(date=date,symbol=symbol,hours=len(r['hours']),all_actual_millisecond_fields_exact=True,primary_daily_six_exact=True))

        while rows:=cursor.fetchmany(4096):
            for values in rows:
                ticker,minute=values[:2]
                if ticker!=current:
                    if current is not None:finish(current,bars)
                    current=ticker;bars={}
                fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','source_dependency_ready_ms','first_transaction_id','last_transaction_id']
                assert int(minute) not in bars;b=dict(zip(fields,map(int,values[2:])));b['source_last_event_ms']=b['last_ms'];bars[int(minute)]=b
        if current is not None:finish(current,bars)
        assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    con.close();assert len(verified)==97 and counts['qualified_hours']==104 and counts['actual_minutes_verified']==6240
    assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    p.atomic_json(output,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),counts=dict(counts),
        stockdays_verified=len(verified),hours_verified=104,minutes_verified=6240,native_millisecond_fields_aggregated_independently=True,all60_minute_independent_ohlcv_exact=True,
        all_native_daily_six_exact=True,observed_source_reception_dependencies_exact=True,coarse_previous_close_formula_reproduced=True,
        native_first_trade_price_preserved=True,other_coarse_conflicts_preserved=True,source_vendor_implementation_certified=False,
        raw_candidate_hours_admitted=0,raw_candidate_modified=False,source_live_clock_certificate=False,production_ready=False,
        records=verified,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
