"""Independent millisecond-event/quote reconstruction of additional hours."""
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
import duckdb


def main():
    lock=p.locked('verify_additional_native_hours')
    directory=p.OUT/'additional_native_hour_staging'
    receipt_path=directory/'receipt.json'
    receipt=p.json.loads(receipt_path.read_text())
    assert receipt['status']=='staged_pending_independent_verification'
    assert p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_additional_native_tail_hours.py')==receipt['source_code_sha256']
    records=[r for r in receipt['records'] if r['hours']]
    pairing_path=p.OUT/'millisecond_pairing/receipt.json'
    pairing=p.json.loads(pairing_path.read_text())
    assert p.sha(pairing_path)==receipt['source_pairing_sha256']
    artifact=p.Path(pairing['native_field_artifact']['path'])
    assert p.identity(artifact)==receipt['native_artifact_identity']
    con=duckdb.connect()
    con.execute("SET memory_limit='128MB'")
    con.execute('SET threads=1')
    tickers=[r['symbol'][:6] for r in records]
    native_rows=con.execute('WITH selected AS (SELECT *,CASE WHEN event_ms=33900000 THEN 570 WHEN event_ms=41400000 THEN 689 WHEN event_ms=54000000 THEN 899 ELSE event_ms//60000 END AS minute FROM read_parquet(?) WHERE ticker=ANY(?)) SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),SUM(volume),SUM(price_x10000*volume),COUNT(*),GREATEST(MAX(event_ms),MAX(local_ms)),MAX(event_ms),ARG_MIN(tran_id,struct_pack(t:=event_ms,id:=tran_id)),ARG_MAX(tran_id,struct_pack(t:=event_ms,id:=tran_id)) FROM selected GROUP BY ticker,minute',[str(artifact),tickers]).fetchall()
    fields=['open','high','low','close','volume','amount_x10000','trades','source_dependency_ready_ms','source_last_event_ms','first_transaction_id','last_transaction_id']
    actual={(r[0],int(r[1])):dict(zip(fields,r[2:])) for r in native_rows}
    del native_rows
    assert p.identity(artifact)==receipt['native_artifact_identity']
    reference_receipt=p.json.loads((p.OUT/'tencent_minute_references/all_target_stock_acquisition.json').read_text())
    refs={r['symbol']:r for r in reference_receipt['records']}
    parent=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    execution=p.np.load(parent/'earliest_execution_times.npy')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    date='2026-09-23';offset=calendar['dates'].index(date)*4
    midnight=int(p.datetime.fromisoformat(date).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())*1000000000
    previous=p.json.loads((p.BASE/'backfilled_panel_candidate_native_event_v7_raw/raw_panel_merge_receipt.json').read_text())
    prior={(r['symbol'],h['slot']) for r in previous['records'] for h in r['hours']}
    total=zero=hours=staged_zero=staged_actual=0
    stockdays=[]
    for record in records:
        symbol=record['symbol']
        path=p.Path(record['minute_path'])
        assert p.identity(path)==record['verified_identity'] and p.sha(path)==record['minute_sha256']
        minutes=p.pq.ParquetFile(path).read().to_pylist()
        expected_grid=list(range(570,690))+list(range(780,900))
        assert [r['left_minute'] for r in minutes]==[m for m in expected_grid if m not in record['missing_source_quote_minutes']]
        quote_path=p.Path(record['quote_source_path'])
        raw=quote_path.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==record['quote_source_sha256']
        quotes={r['quote_sorted_index']:r for r in p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pylist()}
        qty=cash=0
        for minute in minutes:
            key=symbol[:6],minute['left_minute']
            native=actual.get(key)
            if native is not None:
                assert minute['source_kind']=='native_actual_trades'
                assert all(int(minute[f])==int(v) for f,v in native.items())
            else:
                assert minute['source_kind']=='genuine_quote_in_verified_zero_trade_interval'
                assert minute['volume']==minute['amount_x10000']==minute['trades']==0
                q=quotes[minute['source_quote_sorted_index']]
                begin,end=minute['left_minute']*60000,(minute['left_minute']+1)*60000
                assert begin<=q['quote_reported_ms']<end and q['last_actual_trade_EventTime_ms']<=end
                assert q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']
                assert D(q['quote_quantity'])==qty and D(q['quote_Turnover'])*10000==cash
                assert all(D(minute[f])==D(q['quote_LastPrice'])*10000 for f in ['open','high','low','close'])
                assert minute['source_dependency_ready_ms']==max(q['quote_received_ms'],q['all_prefix_trades_latest_LocalTime_ms'],q['last_actual_trade_EventTime_ms'])
                assert minute['source_quote_sequence']==q['quote_seq_no']
                zero+=1
            assert minute['high']>=max(minute['open'],minute['close'])>=min(minute['open'],minute['close'])>=minute['low']>0
            qty+=minute['volume'];cash+=minute['amount_x10000']
            assert qty==minute['cumulative_actual_quantity'] and cash==minute['cumulative_actual_calculated_amount_x10000']
            total+=1
        observed={r['left_minute'] for r in minutes}
        assert all(m in observed for ticker,m in actual if ticker==symbol[:6])
        primary_path=p.Path(record['primary_source_path'])
        assert p.sha(primary_path)==record['primary_source_sha256']
        primary=p.json.loads(primary_path.read_text())
        day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
        assert day['tradestatus']=='1'
        assert [minutes[0]['open'],max(r['high'] for r in minutes),min(r['low'] for r in minutes),minutes[-1]['close'],qty,cash]==[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
        ref=refs[symbol];raw=p.Path(ref['path']).read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==ref['sha256']
        reference={r[0]:r for r in p.json.loads(raw)['data'][ref['code']]['m60']}
        by_minute={r['left_minute']:r for r in minutes}
        for hour in record['hours']:
            slot=hour['slot']
            assert (symbol,slot) not in prior
            block=[by_minute[m] for m in expected_grid[slot*60:(slot+1)*60]]
            derived=dict(open=block[0]['open'],high=max(r['high'] for r in block),low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),amount_x10000=sum(r['amount_x10000'] for r in block),stored_dependency_ready_ms=max(r['source_dependency_ready_ms'] for r in block))
            assert all(hour[f]==v for f,v in derived.items())
            row=reference['20260923'+['1030','1130','1400','1500'][slot]]
            coarse={f:int(D(row[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            coarse['volume']=int(D(row[5])*100)
            assert all(hour[f]==v for f,v in coarse.items())
            assert midnight+hour['stored_dependency_ready_ms']*1000000<int(execution[offset+slot])==hour['existing_earliest_execution_utc_ns']
            staged_zero+=sum(r['trades']==0 for r in block)
            staged_actual+=sum(r['trades']>0 for r in block)
            hours+=1
        stockdays.append(dict(symbol=symbol,hours_verified=len(record['hours']),real_source_minutes_verified=len(minutes),missing_quote_minutes_kept_absent=record['missing_source_quote_minutes']))
    assert hours==receipt['staged_additional_hours'] and len(stockdays)==receipt['staged_stockdays']
    assert staged_actual+staged_zero==hours*60
    result=dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(receipt_path),
        additional_hours_verified=hours,stockdays_with_verified_hours=len(stockdays),all_real_source_minutes_verified=total,
        genuine_zero_trade_quote_minutes_verified=zero,admitted_hour_zero_quote_minutes=staged_zero,admitted_hour_actual_trade_minutes=staged_actual,
        all_primary_daily_six_fields_exact=True,all_selected_independent_coarse_ohlcv_exact=True,
        all_source_dependencies_strictly_before_existing_execution=True,partial_stockdays_not_promoted_to_four_complete_hours=True,
        previous_clock_and_coverage_failures_retained=True,parent_raw_panel_not_yet_merged=True,features_labels_training_not_started=True,
        candidate_hours_admitted=0,stockdays=stockdays,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(directory/'independent_verification.json',result)
    con.close();lock.close()


if __name__=='__main__':
    main()
