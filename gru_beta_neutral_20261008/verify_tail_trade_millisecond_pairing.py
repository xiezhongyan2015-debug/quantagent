"""Pair all available Sep23 target trades by native exchange sequence number.

One parquet scan per source; disk-backed join capped at256MiB. Matching two
published representations does not establish independent upstream provenance.
"""
from . import bounded_tail_pipeline as p
import duckdb

WHOLE=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive'


def main():
    process_lock=p.locked('millisecond_pairing')
    destination=p.OUT/'millisecond_pairing'
    destination.mkdir(exist_ok=True)
    spill=destination/'spill'
    spill.mkdir(exist_ok=True)
    source=p.json.loads((WHOLE/'full_union_day_native_members/complete_native_member_acquisition.json').read_text())
    proof_path=WHOLE/'full_union_day_native_members/independent_complete_native_member_verification.json'
    proof=p.json.loads(proof_path.read_text())
    assert proof['passed']
    leaves={(r['symbol'],r['kind']):r for r in source['records']}
    targets=p.targets()
    symbols=sorted(t['symbol'] for key,group in targets.items() if key[0]=='2026-09-23' for t in group.values())
    assert len(symbols)==624 and set(symbols)==set(source['actual_missing_target_symbols'])
    schema=p.pa.schema([('ticker',p.pa.string()),('tran_id',p.pa.int64()),('channel',p.pa.int32()),
        ('event_ms',p.pa.int32()),('local_ms',p.pa.int32()),('price_x10000',p.pa.int64()),('volume',p.pa.int64())])
    raw_file=destination/'native_target_trade_fields.parquet'
    temp=raw_file.with_suffix('.parquet.tmp')
    rows=0
    leaf_records=[]
    with p.pq.ParquetWriter(temp,schema,compression='zstd') as writer:
        for symbol in symbols:
            leaf=leaves[symbol,'events']
            pf=p.pq.ParquetFile(leaf['path'])
            assert pf.schema_arrow.metadata[b'price_scale']==b'10000' and pf.schema_arrow.metadata[b'qty_scale']==b'1'
            stock_rows=0
            for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=['RecordType','EventTime','LocalTime','EventSeqNo','ChannelNo','Price','Qty'],use_threads=False):
                table=p.pa.Table.from_batches([batch]);table=table.filter(p.pc.equal(table['RecordType'],2))
                if not len(table):continue
                event=table['EventTime'].cast(p.pa.int32())
                regular=p.pc.or_(p.pc.equal(event,33900000),p.pc.or_(p.pc.and_(p.pc.greater_equal(event,34200000),p.pc.less_equal(event,41400000)),p.pc.and_(p.pc.greater_equal(event,46800000),p.pc.less_equal(event,54000000))))
                table=table.filter(regular)
                result=p.pa.Table.from_arrays([p.pa.array([symbol[:6]]*len(table)),table['EventSeqNo'].cast(p.pa.int64()),
                    table['ChannelNo'].cast(p.pa.int32()),table['EventTime'].cast(p.pa.int32()),table['LocalTime'].cast(p.pa.int32()),
                    table['Price'].cast(p.pa.int64()),table['Qty'].cast(p.pa.int64())],schema=schema)
                writer.write_table(result,row_group_size=p.BATCH_ROWS)
                stock_rows+=len(result)
            leaf_records.append(dict(symbol=symbol,regular_trade_rows=stock_rows,source_path=leaf['path'],
                previous_certified_sha256=leaf['sha256'],verified_identity=p.identity(leaf['path'])))
            rows+=stock_rows
    temp.replace(raw_file)
    subset=p.json.loads((p.OUT/'extraction.json').read_text())
    records=[r for r in subset['partitions'] if r['date']=='2026-09-23']
    for r in records:assert p.identity(r['selected_native_column_subset_path'])==r['verified_identity']
    paths=[r['selected_native_column_subset_path'] for r in records]
    database=destination/'pairing.duckdb'
    con=duckdb.connect(str(database))
    con.execute("SET memory_limit='256MB'")
    con.execute('SET threads=1')
    con.execute('SET temp_directory=?',[str(spill)])
    con.execute("SET max_temp_directory_size='4GB'")
    con.execute('DROP TABLE IF EXISTS native')
    con.execute('DROP TABLE IF EXISTS seconds')
    con.execute('CREATE TABLE native AS SELECT * FROM read_parquet(?)',[str(raw_file)])
    con.execute('CREATE TABLE seconds AS SELECT * FROM read_parquet(?, hive_partitioning=false) WHERE (time_s BETWEEN 33900 AND 33902) OR (time_s BETWEEN 34200 AND 41402) OR (time_s BETWEEN 46800 AND 54002)',[paths])
    native_duplicates=con.execute('SELECT ticker,tran_id,COUNT(*) AS n FROM native GROUP BY ticker,tran_id HAVING COUNT(*)>1').fetchall()
    seconds_duplicates=con.execute('SELECT ticker,tran_id,COUNT(*) AS n FROM seconds GROUP BY ticker,tran_id HAVING COUNT(*)>1').fetchall()
    assert not native_duplicates and not seconds_duplicates,'Exchange sequence mapping is ambiguous'
    con.execute('CREATE OR REPLACE TABLE paired AS SELECT COALESCE(s.ticker,n.ticker) AS ticker,s.tran_id AS seconds_id,n.tran_id AS native_id,s.time_s,n.event_ms,n.local_ms,s.price_x10000 AS seconds_price,n.price_x10000 AS native_price,s.volume AS seconds_volume,n.volume AS native_volume FROM seconds s FULL OUTER JOIN native n ON s.ticker=n.ticker AND s.tran_id=n.tran_id')
    cursor=con.execute('SELECT ticker,COUNT(seconds_id),COUNT(native_id),COUNT(*) FILTER(WHERE seconds_id IS NULL),COUNT(*) FILTER(WHERE native_id IS NULL),COUNT(*) FILTER(WHERE seconds_price!=native_price),COUNT(*) FILTER(WHERE seconds_volume!=native_volume),COUNT(*) FILTER(WHERE time_s!=event_ms//1000),SUM(seconds_volume),SUM(native_volume) FROM paired GROUP BY ticker ORDER BY ticker')
    fields=['ticker','seconds_rows','native_rows','missing_seconds_rows','missing_native_rows','price_different_rows','volume_different_rows','floor_event_second_different_rows','seconds_total_quantity','native_total_quantity']
    stockdays=[dict(zip(fields,row)) for row in cursor.fetchall()]
    shifts=[dict(delta_seconds=int(row[0]),rows=int(row[1])) for row in con.execute('SELECT time_s-event_ms//1000 AS delta,COUNT(*) FROM paired WHERE seconds_id IS NOT NULL AND native_id IS NOT NULL GROUP BY delta ORDER BY delta').fetchall()]
    timing=con.execute('SELECT MIN(local_ms-event_ms),MAX(local_ms-event_ms),COUNT(*) FILTER(WHERE local_ms<event_ms) FROM native').fetchone()
    result=dict(started_utc=p.utc(),finished_utc=p.utc(),source_code_sha256=p.sha(__file__),native_member_receipt_sha256=p.sha(WHOLE/'full_union_day_native_members/complete_native_member_acquisition.json'),
        prior_native_verification_sha256=p.sha(proof_path),requested_stockdays=624,stockdays_checked=len(stockdays),
        native_regular_trade_rows=rows,stockdays=stockdays,source_clock_difference_histogram=shifts,
        source_local_minus_event_ms=dict(minimum=int(timing[0]),maximum=int(timing[1]),negative_rows=int(timing[2])),
        all_rows_price_quantity_and_floor_clock_exact=all(r['missing_seconds_rows']==r['missing_native_rows']==r['price_different_rows']==r['volume_different_rows']==r['floor_event_second_different_rows']==0 for r in stockdays),
        join_memory_limit_bytes=256*1024**2,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss,
        native_source_leaves=leaf_records,native_field_artifact=dict(path=str(raw_file),sha256=p.sha(raw_file),rows=rows),
        upstream_source_independence_certified=False,candidate_hours_admitted=0)
    p.atomic_json(destination/'receipt.json',result)
    con.close()
    # Rebuildable join tables are unnecessary after the compact proof is saved.
    database.unlink()
    wal=database.with_suffix('.duckdb.wal')
    if wal.exists():wal.unlink()
    process_lock.close()


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        p.atomic_json(p.OUT/'millisecond_pairing/failure.json',dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),
            error_type=type(error).__name__,error=str(error),traceback=p.traceback.format_exc(),candidate_hours_admitted=0))
        raise
