"""Stage native event-time candles plus genuinely observed zero-trade quotes.

The immutable 499 sampled-quote controls are a different candle definition;
their failed exact-clock diagnostics stay unchanged. This stage cannot train a
model until its new parent panel, features, labels and full scope are audited.
"""
from collections import defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import Aggregate, labels, GRID

DAY='2026-09-23'
WHOLE=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive'
DEST=p.OUT/'new_candidate_v7_staging'


def main():
    lock=p.locked('native_tail_candidate_staging')
    DEST.mkdir(exist_ok=True)
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    coarse=p.json.loads(coarse_path.read_text())
    proof=p.json.loads((coarse_path.parent/'independent_hour_comparison_verification.json').read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(coarse_path)
    grouped=defaultdict(list)
    for group in coarse['groups']:
        if group['date']!=DAY:
            continue
        raw=p.Path(group['comparison_path']).read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==group['comparison_sha256']
        for row in p.json.loads(raw)['comparisons']:
            if row['model']=='floor_left' and row.get('differences'):
                grouped[row['symbol']].append(row)
    selected=sorted(s for s,rs in grouped.items() if len(rs)==4 and all(all(v==0 for v in r['differences'].values()) for r in rs))
    assert len(selected)==22
    pairing_path=p.OUT/'millisecond_pairing/receipt.json'
    pairing=p.json.loads(pairing_path.read_text())
    order_path=pairing_path.parent/'intrasecond_order_verification.json'
    order=p.json.loads(order_path.read_text())
    assert order['paired_source_receipt_sha256']==p.sha(pairing_path)
    assert pairing['all_rows_price_quantity_and_floor_clock_exact']
    assert order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==order['intrasecond_event_inversions']==0
    event_path=p.Path(pairing['native_field_artifact']['path'])
    assert p.identity(event_path)==order['accepted_artifact_identity_guard']
    aggregate=Aggregate()
    timing={}
    selected_tickers=p.pa.array([s[:6] for s in selected])
    for batch in p.pq.ParquetFile(event_path).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
        table=p.pa.Table.from_batches([batch])
        table=table.filter(p.pc.is_in(table['ticker'],value_set=selected_tickers))
        if not len(table):
            continue
        second=p.pc.divide(table['event_ms'],p.pa.scalar(1000,p.pa.int32())).cast(p.pa.int32())
        fields=p.pa.Table.from_arrays([table['ticker'],second,table['tran_id'],table['price_x10000'],table['volume']],names=p.CORE)
        for leaf in fields.to_batches():
            aggregate.consume(leaf)
        frame=table.select(['ticker','event_ms','local_ms']).to_pandas()
        _,minute=labels(frame.event_ms.to_numpy()//1000,'floor_left')
        frame['left_minute']=minute
        summary=frame.groupby(['ticker','left_minute']).agg(event_ms=('event_ms','max'),local_ms=('local_ms','max'))
        for key,row in summary.iterrows():
            old=timing.setdefault(key,dict(event_ms=0,local_ms=0))
            for f in old:
                old[f]=max(old[f],int(row[f]))
    assert p.identity(event_path)==order['accepted_artifact_identity_guard']
    prefix_path=WHOLE/'full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json'
    prefix=p.json.loads(prefix_path.read_text())
    prefix_index={r['symbol']:r for r in prefix['records']}
    output=DEST/'receipt.json'
    assert not output.exists()
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),parent_candidate='backfilled_panel_candidate_canonical_ipo_v6',
        parent_meta_sha256='6f32416294f607b950174a8723c9ad364d6b7025a7a15f4db0911fe89e9c3034',
        source_millisecond_pairing_sha256=p.sha(pairing_path),source_intrasecond_verification_sha256=p.sha(order_path),
        independent_coarse_verification_sha256=p.sha(coarse_path.parent/'independent_hour_comparison_verification.json'),
        requested_stockdays=22,records=[],status='staging',formal_parent_panel_hours_admitted=0,
        new_features_or_labels_recalculated=False,new_model_training_started=False,production_ready=False,
        definition='Actual native trade first/open, maximum/high, minimum/low, last/close with floor EventTime minute labels and fixed two-second session-end grace. An empty trade interval is retained only when a real positive quote in that interval matches the exact native quantity and calculated amount cut; its observed LastPrice is retained and actual trade count/volume/amount are zero.',
        original_sampled_quote_clock_failures_preserved=True,missing_quotes_never_filled=True,
        true_share_volume_from_native_qty_scale_1=True,coarse_quantity_times_100_remains_corroboration_hypothesis=True,
        source_availability_policy='Record max native event and local times, quote receipt and full trade-prefix receipt dependencies; require each complete hour strictly before its existing execution. This is a recorded-time research check, not a live clock or latency certificate.')
    execution=p.np.load(p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset/earliest_execution_times.npy',mmap_mode='r')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    index=calendar['dates'].index(DAY)*4
    midnight=int(p.datetime.fromisoformat(DAY).replace(tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).timestamp()*1e9)
    for symbol in selected:
        ticker=symbol[:6]
        native=prefix_index[symbol]
        assert native['regular_trade_identity_unique'] and native['regular_trade_fields_valid']
        witness=native['quote_prefix_witnesses']
        quote_file=p.Path(witness['path'])
        # Small selected evidence leaf; decode only its bounded day, not raw archives.
        assert p.sha(quote_file)==witness['sha256']
        quotes=p.pq.ParquetFile(quote_file).read().to_pylist()
        quotes.sort(key=lambda r:(r['quote_reported_ms'],r['quote_received_ms'],r['quote_sorted_index']))
        minutes=[]
        cumulative_qty=cumulative_cash=0
        error=None
        for left in GRID:
            trade=aggregate.bars['floor_left'].get((ticker,left))
            if trade:
                row={f:trade[f] for f in ['open','high','low','close','volume','amount_x10000','trades']}
                time=timing[ticker,left]
                row.update(source_kind='native_actual_trades',source_last_event_ms=time['event_ms'],
                           source_dependency_ready_ms=max(time['event_ms'],time['local_ms']),
                           first_transaction_id=trade['first_id'],last_transaction_id=trade['last_id'])
            else:
                eligible=[q for q in quotes if left*60000<=q['quote_reported_ms']<(left+1)*60000
                    and D(q['quote_quantity'])==cumulative_qty and D(q['quote_Turnover'])*10000==cumulative_cash
                    and q['last_actual_trade_EventTime_ms']<=(left+1)*60000]
                if not eligible:
                    error=dict(left_minute=left,reason='no_genuine_zero_trade_quote_at_exact_native_cut')
                    break
                q=eligible[0]
                price=D(q['quote_LastPrice'])*10000
                assert price==price.to_integral_value() and price>0
                row=dict.fromkeys(['open','high','low','close'],int(price))
                row.update(volume=0,amount_x10000=0,trades=0,source_kind='genuine_quote_in_verified_zero_trade_interval',
                    source_quote_sorted_index=q['quote_sorted_index'],source_quote_sequence=q['quote_seq_no'],
                    source_quote_reported_ms=q['quote_reported_ms'],source_quote_received_ms=q['quote_received_ms'],
                    source_last_event_ms=q['last_actual_trade_EventTime_ms'],
                    source_dependency_ready_ms=max(q['quote_received_ms'],q['all_prefix_trades_latest_LocalTime_ms'],q['last_actual_trade_EventTime_ms']))
            cumulative_qty+=row['volume']
            cumulative_cash+=row['amount_x10000']
            row.update(symbol=symbol,date=DAY,left_minute=left,cumulative_actual_quantity=cumulative_qty,
                       cumulative_actual_calculated_amount_x10000=cumulative_cash)
            minutes.append(row)
        if error:
            result['records'].append(dict(symbol=symbol,status='quarantined',error=error))
            p.atomic_json(output,result)
            continue
        assert len(minutes)==240 and cumulative_qty==native['regular_actual_trade_total_quantity']
        assert D(cumulative_cash)/10000==D(native['regular_calculated_actual_trade_total_amount'])
        daily_path=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        primary=p.json.loads(daily_path.read_text())
        daily=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==DAY)
        assert daily['tradestatus']=='1'
        assert [minutes[0]['open'],max(r['high'] for r in minutes),min(r['low'] for r in minutes),minutes[-1]['close'],cumulative_qty,cumulative_cash]==[int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
        hours=[]
        for slot in range(4):
            block=minutes[slot*60:(slot+1)*60]
            hour=dict(symbol=symbol,date=DAY,slot=slot,open=block[0]['open'],high=max(r['high'] for r in block),
                      low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),
                      amount_x10000=sum(r['amount_x10000'] for r in block),actual_trade_minutes=sum(r['trades']>0 for r in block),
                      genuine_zero_trade_quote_minutes=sum(r['trades']==0 for r in block),
                      stored_dependency_ready_ms=max(r['source_dependency_ready_ms'] for r in block),
                      existing_earliest_execution_utc_ns=int(execution[index+slot]))
            expected=next(r['native_hour'] for r in grouped[symbol] if r['slot']==slot)
            assert all(hour[f]==expected[f] for f in ['open','high','low','close','volume'])
            assert midnight+hour['stored_dependency_ready_ms']*1000000<hour['existing_earliest_execution_utc_ns']
            hours.append(hour)
        folder=DEST/('symbol='+symbol)
        folder.mkdir(exist_ok=True)
        minute_path=folder/'native_candidate_minutes.parquet'
        keys=sorted({key for row in minutes for key in row})
        normalized=[{key:row.get(key) for key in keys} for row in minutes]
        p.pq.write_table(p.pa.Table.from_pylist(normalized),minute_path,compression='zstd')
        result['records'].append(dict(symbol=symbol,status='native_stockday_staged',minute_path=str(minute_path),
            minute_sha256=p.sha(minute_path),verified_identity=p.identity(minute_path),
            original_primary_daily_six_fields_exact=True,independent_four_hour_ohlcv_exact=True,
            all_stored_dependencies_before_execution=True,hours=hours,
            observed_actual_trade_minutes=sum(r['trades']>0 for r in minutes),
            observed_genuine_zero_trade_quote_minutes=sum(r['trades']==0 for r in minutes)))
        result.update(updated_utc=p.utc(),staged_stockdays=sum(r['status']=='native_stockday_staged' for r in result['records']))
        p.atomic_json(output,result)
    result.update(status='source_defined_minutes_staged_pending_independent_verification_and_parent_panel_merge',finished_utc=p.utc(),
        staged_hours=sum(len(r.get('hours',[])) for r in result['records']),
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result)
    lock.close()


if __name__=='__main__':
    main()
