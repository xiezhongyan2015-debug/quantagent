"""Check whether second-time/sequence ordering preserves native minute O/C.

Global sequence resets do not by themselves make intrasecond ordering invalid.
Use the already paired derived fields, bounded disk spill and no new downloads.
"""
from datetime import datetime
from . import bounded_tail_pipeline as p
import duckdb


def main():
    lock=p.locked('sep24_intrasecond_trade_order')
    directory=p.OUT/'millisecond_pairing_sep24'
    receipt_path=directory/'receipt.json'
    receipt=p.json.loads(receipt_path.read_text())
    assert receipt['all_rows_price_quantity_and_floor_clock_exact']
    artifact=receipt['native_field_artifact']
    path=p.Path(artifact['path'])
    identity=p.identity(path)
    receipt_time=int(datetime.fromisoformat(receipt['finished_utc']).timestamp()*1e9)
    assert identity['ctime_ns']<=receipt_time and identity['mtime_ns']<=receipt_time
    assert p.pq.ParquetFile(path).metadata.num_rows==artifact['rows']
    spill=directory/'order_spill'
    spill.mkdir(exist_ok=True)
    con=duckdb.connect()
    con.execute("SET memory_limit='256MB'")
    con.execute('SET threads=1')
    con.execute('SET temp_directory=?',[str(spill)])
    con.execute("SET max_temp_directory_size='4GB'")
    con.read_parquet(str(path)).create_view('native_source')
    con.execute('CREATE VIEW native AS SELECT *,event_ms//1000 AS sec,CASE WHEN event_ms BETWEEN 33900000 AND 34200999 THEN 570 WHEN event_ms BETWEEN 41400000 AND 41402999 THEN 689 WHEN event_ms BETWEEN 54000000 AND 54002999 THEN 899 ELSE event_ms//60000 END AS minute FROM native_source')
    cursor=con.execute('SELECT ticker,minute,COUNT(*) AS trades,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)) AS millisecond_open,ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)) AS millisecond_close,ARG_MIN(price_x10000,struct_pack(t:=sec,id:=tran_id)) AS seconds_open,ARG_MAX(price_x10000,struct_pack(t:=sec,id:=tran_id)) AS seconds_close FROM native GROUP BY ticker,minute ORDER BY ticker,minute')
    fields=['ticker','left_minute','trades','millisecond_open','millisecond_close','seconds_open','seconds_close']
    bars=[dict(zip(fields,row)) for row in cursor.fetchall()]
    inversions=con.execute('SELECT ticker,COUNT(*) AS rows,COUNT(*) FILTER(WHERE event_ms<previous_ms) AS intrasecond_event_inversions FROM (SELECT ticker,event_ms,LAG(event_ms) OVER(PARTITION BY ticker,sec ORDER BY tran_id) AS previous_ms FROM native) GROUP BY ticker ORDER BY ticker').fetchall()
    records=[dict(ticker=row[0],rows=int(row[1]),intrasecond_event_inversions=int(row[2])) for row in inversions]
    assert sum(r['rows'] for r in records)==artifact['rows'] and len(records)==receipt['available_native_source_stockdays']
    assert p.identity(path)==identity
    result=dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),paired_source_receipt_sha256=p.sha(receipt_path),
        accepted_derived_field_sha256_reused=artifact['sha256'],accepted_artifact_identity_guard=identity,
        artifact_ctime_and_mtime_not_later_than_accepted_receipt=True,stockdays_checked=receipt['available_native_source_stockdays'],requested_stockdays=624,absent_native_source_stockdays=receipt['absent_native_source_stockdays'],native_rows_checked=artifact['rows'],
        observed_trade_minutes_checked=len(bars),minute_open_changed_by_intrasecond_order=sum(r['millisecond_open']!=r['seconds_open'] for r in bars),
        minute_close_changed_by_intrasecond_order=sum(r['millisecond_close']!=r['seconds_close'] for r in bars),
        intrasecond_event_inversions=sum(r['intrasecond_event_inversions'] for r in records),records=records,
        changed_minute_examples=[r for r in bars if r['millisecond_open']!=r['seconds_open'] or r['millisecond_close']!=r['seconds_close']][:50],
        source_rows_not_rehashed=True,other_dates_not_certified=True,candidate_hours_admitted=0,
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(directory/'intrasecond_order_verification.json',result)
    con.close()
    lock.close()


if __name__=='__main__':
    main()
