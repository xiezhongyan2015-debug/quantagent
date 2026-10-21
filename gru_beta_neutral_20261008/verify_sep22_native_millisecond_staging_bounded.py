"""Independently aggregate native millisecond rows in bounded SQL and audit leaves."""
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p
from .stage_sep22_native_millisecond_hours_bounded import DEST,DAY,RAW
import duckdb


def main():
    lock=p.locked('verify_sep22_native_ms_staging');path=DEST/'receipt.json';stage=p.json.loads(path.read_text())
    assert stage['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_sep22_native_millisecond_hours_bounded.py')
    assert stage['status']=='staged_actual_millisecond_hours_pending_independent_audit' and not stage['raw_panel_modified']
    pairing_path=p.OUT/'millisecond_pairing_sep22/receipt.json';pairing=p.json.loads(pairing_path.read_text());assert pairing['all_rows_price_quantity_and_floor_clock_exact'] and p.sha(pairing_path)==stage['source_pairing_sha256']
    order_path=pairing_path.parent/'intrasecond_order_verification.json';order=p.json.loads(order_path.read_text());assert p.sha(order_path)==stage['source_intrasecond_proof_sha256'] and order['paired_source_receipt_sha256']==p.sha(pairing_path)
    artifact=p.Path(pairing['native_field_artifact']['path']);assert p.identity(artifact)==stage['native_artifact_identity']==order['accepted_artifact_identity_guard']
    raw_audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(raw_audit_path.read_text());assert audit['passed'] and p.sha(raw_audit_path)==stage['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    rawmask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    offset=calendar['dates'].index(DAY)*4;counts=Counter();records={r['symbol'][:6]:r for r in stage['records']}
    independent_refs={}
    for proof_pin in stage['independent_minute_proofs']:
        proof_path=p.Path(proof_pin['path']);assert p.sha(proof_path)==proof_pin['sha256'];proof=p.json.loads(proof_path.read_text());assert proof['passed']
        for h in proof['qualified_minute_diagnostic_hours']:
            if h['date']==DAY:independent_refs[h['symbol'],h['slot']]=h
    spill=DEST/'independent_sql_spill';spill.mkdir(exist_ok=True);con=duckdb.connect()
    con.execute("SET memory_limit='256MB'");con.execute('SET threads=1');con.execute('SET temp_directory=?',[str(spill)]);con.execute("SET max_temp_directory_size='4GB'")
    con.read_parquet(str(artifact)).create_view('all_native')
    selected=p.pa.table({'ticker':sorted(records)});con.register('selected',selected)
    con.execute('CREATE VIEW native AS SELECT n.*, CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM all_native n JOIN selected s USING(ticker)')
    cursor=con.execute('SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)) AS o,MAX(price_x10000) AS h,MIN(price_x10000) AS l,ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)) AS c,SUM(volume),SUM(price_x10000::HUGEINT*volume::HUGEINT),COUNT(*),MIN(event_ms),MAX(event_ms),MAX(GREATEST(event_ms,local_ms)),ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id)) FROM native GROUP BY ticker,minute ORDER BY ticker,minute')
    current=None;bars={};verified=[]

    def finish(ticker,actual):
        r=records[ticker];symbol=r['symbol'];assert r['date']==DAY and r['stockday_native_primary_six_exact']
        primary_path=p.Path(r['primary_source_path']);raw=primary_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['primary_source_sha256'];primary=p.json.loads(raw)
        day=next(dict(zip(primary['fields'],v)) for v in primary['rows'] if v[0]==DAY);assert day['tradestatus']=='1'
        total=[actual[min(actual)]['open'],max(b['high'] for b in actual.values()),min(b['low'] for b in actual.values()),actual[max(actual)]['close'],sum(b['volume'] for b in actual.values()),sum(b['amount_x10000'] for b in actual.values())]
        assert total==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
        leaf=p.Path(r['minute_path']);assert p.identity(leaf)==r['verified_identity'];raw=leaf.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==r['minute_sha256'];rows=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pylist();byminute={b['left_minute']:b for b in rows};assert len(rows)==len(byminute)==len(r['hours'])*60
        fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','first_transaction_id','last_transaction_id','source_last_event_ms','source_dependency_ready_ms']
        for b in rows:
            a=actual[b['left_minute']];assert b['symbol']==symbol and b['date']==DAY and b['trades']>0 and b['volume']>0
            assert all(b[f]==a[f] for f in fields);counts['actual_minutes_verified']+=1
        grid=list(range(570,690))+list(range(780,900));ref_cache={}
        for h in r['hours']:
            slot=h['slot'];q=independent_refs[symbol,slot];assert h['sina_reference_path']==q['sina_reference_path'] and h['sina_reference_sha256']==q['sina_reference_sha256']
            if h['sina_reference_path'] not in ref_cache:
                text=p.Path(h['sina_reference_path']).read_bytes();assert p.hashlib.sha256(text).hexdigest()==h['sina_reference_sha256'];text=text.decode();data,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);ref_cache[h['sina_reference_path']]={b['day']:b for b in data}
            source_refs=ref_cache[h['sina_reference_path']];block=[actual[m] for m in grid[slot*60:(slot+1)*60]]
            for minute in grid[slot*60:(slot+1)*60]:
                b=actual[minute];end=minute+1;ref=source_refs[f'{DAY} {end//60:02d}:{end%60:02d}:00']
                assert all(b[f]==int(D(ref[f])*10000) for f in ['open','high','low','close']) and D(b['volume'])==D(ref['volume'])
            value=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),source_event_ms_max=max(b['source_last_event_ms'] for b in block),source_ready_ms=max(b['source_dependency_ready_ms'] for b in block))
            assert all(h[f]==v for f,v in value.items())
            exists=bool(rawmask[offset+slot,calendar['union_symbols'].index(symbol)])
            assert exists==(h['purpose']=='recorded_reception_evidence_for_existing_raw_hour')
            counts[h['purpose']]+=1
        assert p.identity(leaf)==r['verified_identity'];verified.append(dict(symbol=symbol,hours=len(r['hours']),all_actual_fields_exact=True,day_primary_six_exact=True))

    while block:=cursor.fetchmany(4096):
        for v in block:
            ticker,minute=v[:2]
            if ticker!=current:
                if current is not None:finish(current,bars)
                current=ticker;bars={}
            assert int(minute) not in bars
            fields=['open','high','low','close','volume','amount_x10000','trades','first_ms','last_ms','source_dependency_ready_ms','first_transaction_id','last_transaction_id']
            b=dict(zip(fields,map(int,v[2:])));b['source_last_event_ms']=b['last_ms'];bars[int(minute)]=b
    if current is not None:finish(current,bars)
    con.close();assert len(verified)==len(records) and counts['actual_minutes_verified']==18420
    assert counts['new_raw_market_hour_pending_admission']==128 and counts['recorded_reception_evidence_for_existing_raw_hour']==179
    assert p.identity(artifact)==stage['native_artifact_identity']
    p.atomic_json(DEST/'independent_verification.json',dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(path),
        counts=dict(counts),stocks_verified=len(verified),hours_minute_truth_verified=307,native_millisecond_fields_aggregated_independently=True,
        all60_minute_independent_ohlcv_exact=True,all_native_daily_six_exact=True,observed_source_reception_dependencies_exact=True,
        raw_candidate_hours_admitted=0,raw_candidate_modified=False,source_live_clock_certificate=False,production_ready=False,
        records=verified,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss));lock.close()


if __name__=='__main__':main()
