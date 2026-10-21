"""Reproduce every staged minute directly from native event order or quote rows."""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
import duckdb


def main():
    lock=p.locked('verify_native_candidate_staging')
    directory=p.OUT/'new_candidate_v7_staging'
    receipt_path=directory/'receipt.json'
    receipt=p.json.loads(receipt_path.read_text())
    assert receipt['staged_stockdays']==22 and receipt['staged_hours']==88
    assert p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_native_tail_candidate_minutes.py')==receipt['source_code_sha256']
    pairing=p.json.loads((p.OUT/'millisecond_pairing/receipt.json').read_text())
    order=p.json.loads((p.OUT/'millisecond_pairing/intrasecond_order_verification.json').read_text())
    artifact=p.Path(pairing['native_field_artifact']['path'])
    assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    tickers=[r['symbol'][:6] for r in receipt['records']]
    con=duckdb.connect()
    con.execute("SET memory_limit='128MB'")
    con.execute('SET threads=1')
    rows=con.execute('WITH selected AS (SELECT *,CASE WHEN event_ms=33900000 THEN 570 WHEN event_ms=41400000 THEN 689 WHEN event_ms=54000000 THEN 899 ELSE event_ms//60000 END AS minute FROM read_parquet(?) WHERE ticker=ANY(?)) SELECT ticker,minute,ARG_MIN(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),MAX(price_x10000),MIN(price_x10000),ARG_MAX(price_x10000,struct_pack(t:=event_ms,id:=tran_id)),SUM(volume),SUM(price_x10000*volume),COUNT(*),GREATEST(MAX(event_ms),MAX(local_ms)),MAX(event_ms) FROM selected GROUP BY ticker,minute',[str(artifact),tickers]).fetchall()
    fields=['open','high','low','close','volume','amount_x10000','trades','source_dependency_ready_ms','source_last_event_ms']
    actual={(r[0],int(r[1])):dict(zip(fields,r[2:])) for r in rows}
    assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    whole=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
    prefix=p.json.loads((whole/'full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json').read_text())
    index={r['symbol']:r for r in prefix['records']}
    reference_receipt=p.json.loads((p.OUT/'tencent_minute_references/all_target_stock_acquisition.json').read_text())
    references={r['symbol']:r for r in reference_receipt['records']}
    parent=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    assert p.sha(parent/'meta.json')==receipt['parent_meta_sha256']
    execution=p.np.load(parent/'earliest_execution_times.npy',mmap_mode='r')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    date='2026-09-23'
    hour_index=calendar['dates'].index(date)*4
    midnight=int(p.datetime.fromisoformat(date).replace(tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).timestamp())*1000000000
    total=zero=events=0
    stockdays=[]
    for record in receipt['records']:
        symbol=record['symbol']
        path=p.Path(record['minute_path'])
        assert p.identity(path)==record['verified_identity'] and p.sha(path)==record['minute_sha256']
        minutes=p.pq.ParquetFile(path).read().to_pylist()
        assert [r['left_minute'] for r in minutes]==list(range(570,690))+list(range(780,900))
        witness=index[symbol]['quote_prefix_witnesses']
        quote_path=p.Path(witness['path'])
        assert p.sha(quote_path)==witness['sha256']
        quotes={r['quote_sorted_index']:r for r in p.pq.ParquetFile(quote_path).read().to_pylist()}
        qty=cash=0
        for minute in minutes:
            key=symbol[:6],minute['left_minute']
            native=actual.get(key)
            if native is not None:
                assert minute['source_kind']=='native_actual_trades'
                assert all(int(minute[f])==int(v) for f,v in native.items())
                events+=minute['trades']
            else:
                assert minute['source_kind']=='genuine_quote_in_verified_zero_trade_interval'
                assert minute['volume']==minute['amount_x10000']==minute['trades']==0
                quote=quotes[minute['source_quote_sorted_index']]
                begin,end=minute['left_minute']*60000,(minute['left_minute']+1)*60000
                assert begin<=quote['quote_reported_ms']<end
                assert quote['whole_trade_quantity_prefix_exact'] and quote['calculated_trade_prefix_amount_exact'] and quote['last_trade_price_exact']
                assert D(quote['quote_quantity'])==qty and D(quote['quote_Turnover'])*10000==cash
                assert quote['last_actual_trade_EventTime_ms']<=end
                price=D(quote['quote_LastPrice'])*10000
                assert all(D(minute[f])==price for f in ['open','high','low','close'])
                ready=max(quote['quote_received_ms'],quote['all_prefix_trades_latest_LocalTime_ms'],quote['last_actual_trade_EventTime_ms'])
                assert ready==minute['source_dependency_ready_ms']
                assert minute['source_quote_sequence']==quote['quote_seq_no']
                zero+=1
            assert minute['high']>=max(minute['open'],minute['close'])>=min(minute['open'],minute['close'])>=minute['low']>0
            qty+=minute['volume']
            cash+=minute['amount_x10000']
            assert qty==minute['cumulative_actual_quantity'] and cash==minute['cumulative_actual_calculated_amount_x10000']
            total+=1
        assert qty==index[symbol]['regular_actual_trade_total_quantity']
        assert D(cash)/10000==D(index[symbol]['regular_calculated_actual_trade_total_amount'])
        daily_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        daily=p.json.loads(daily_path.read_text())
        primary=next(dict(zip(daily['fields'],r)) for r in daily['rows'] if r[0]==date)
        assert primary['tradestatus']=='1' and D(primary['volume'])>0
        assert [minutes[0]['open'],max(r['high'] for r in minutes),min(r['low'] for r in minutes),minutes[-1]['close'],qty,cash]==[int(D(primary[f])*10000) for f in ['open','high','low','close']]+[int(primary['volume']),int(D(primary['amount'])*10000)]
        ref=references[symbol]
        ref_path=p.Path(ref['path'])
        raw=ref_path.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==ref['sha256']
        published=p.json.loads(raw)['data'][ref['code']]['m60']
        for hour in record['hours']:
            block=minutes[hour['slot']*60:(hour['slot']+1)*60]
            check=dict(open=block[0]['open'],high=max(r['high'] for r in block),low=min(r['low'] for r in block),
                       close=block[-1]['close'],volume=sum(r['volume'] for r in block),amount_x10000=sum(r['amount_x10000'] for r in block),
                       stored_dependency_ready_ms=max(r['source_dependency_ready_ms'] for r in block))
            assert all(hour[f]==v for f,v in check.items())
            reference=next(r for r in published if r[0]=='20260923'+['1030','1130','1400','1500'][hour['slot']])
            exact={f:int(D(reference[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            exact['volume']=int(D(reference[5])*100)
            assert all(hour[f]==v for f,v in exact.items())
            assert hour['existing_earliest_execution_utc_ns']==int(execution[hour_index+hour['slot']])
            assert midnight+hour['stored_dependency_ready_ms']*1000000<hour['existing_earliest_execution_utc_ns']
        stockdays.append(dict(symbol=symbol,minutes_verified=240,actual_trade_quantity=qty,calculated_amount_x10000=cash))
    assert total==5280 and zero==139
    proof=dict(checked_utc=p.utc(),passed=True,source_code_sha256=p.sha(__file__),audited_receipt_sha256=p.sha(receipt_path),
        staged_stockdays_verified=22,staged_hours_verified=88,real_source_minutes_verified=total,
        native_transaction_records_contributing=events,genuine_zero_trade_quote_minutes_verified=zero,
        source_order='Independent SQL over native millisecond EventTime and sequence; all staged trade OHLC/V/amount and native timing are exact.',
        zero_trade_verification='No event record exists in the complete paired native interval. A real same-interval quote has exact quantity/amount at that cut and its own literal LastPrice; no absent quote is carried forward.',
        all_22_primary_daily_ohlc_volume_and_amount_exact=True,all_88_independent_coarse_ohlcv_exact=True,
        all_88_stored_source_dependencies_before_unchanged_execution=True,
        parent_panel_not_yet_merged=True,features_labels_training_not_started=True,formal_parent_panel_hours_admitted=0,
        stockdays=stockdays,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(directory/'independent_verification.json',proof)
    con.close()
    lock.close()


if __name__=='__main__':
    main()
