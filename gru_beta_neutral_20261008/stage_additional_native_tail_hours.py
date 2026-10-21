"""Stage individually qualified Sep23 hours without promoting conflicted hours.

The event-time rule is exactly the existing V7 rule. A single sorted native
field scan holds one stock's minute summaries, plus <=65536 input rows.
"""
from decimal import Decimal as D
from collections import defaultdict,Counter
from zoneinfo import ZoneInfo
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID,labels

DAY='2026-09-23'
DEST=p.OUT/'additional_native_hour_staging'
WHOLE=p.BASE/'public_source_probes/additional_native_inventory_20261009/whole_event_archive'


def main():
    lock=p.locked('additional_native_hours')
    DEST.mkdir(exist_ok=True)
    output=DEST/'receipt.json'
    assert not output.exists()
    old_dir=p.BASE/'backfilled_panel_candidate_native_event_v7_raw'
    old=p.json.loads((old_dir/'raw_panel_merge_receipt.json').read_text())
    old_audit=p.json.loads((old_dir/'raw_full_history_audit.json').read_text())
    assert old_audit['passed'] and old_audit['audited_merge_receipt_sha256']==p.sha(old_dir/'raw_panel_merge_receipt.json')
    prior={(r['symbol'],h['slot']) for r in old['records'] for h in r['hours']}
    original=p.BASE/'backfilled_panel_candidate_canonical_ipo_v6/dataset'
    meta=p.json.loads((original/'meta.json').read_text())
    special=set(meta['factor_bridge_provenance']['applied_symbols'])|set(meta['IPO_factor_prefix_provenance']['applied_symbols'])
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    coarse=p.json.loads(coarse_path.read_text())
    coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json'
    coarse_proof=p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    wanted=defaultdict(dict)
    excluded=[]
    for group in coarse['groups']:
        if group['date']!=DAY:continue
        raw=p.Path(group['comparison_path']).read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==group['comparison_sha256']
        for row in p.json.loads(raw)['comparisons']:
            if row['model']!='floor_left' or not row.get('differences') or not all(v==0 for v in row['differences'].values()):continue
            symbol,slot=row['symbol'],row['slot']
            if (symbol,slot) in prior:continue
            if symbol in special:
                excluded.append(dict(symbol=symbol,slot=slot,reason='existing_special_factor_compiler_requires_separate_verified_route'))
                continue
            wanted[symbol][slot]=row['native_hour']
    pairing_path=p.OUT/'millisecond_pairing/receipt.json'
    pairing=p.json.loads(pairing_path.read_text())
    order_path=pairing_path.parent/'intrasecond_order_verification.json'
    order=p.json.loads(order_path.read_text())
    assert order['paired_source_receipt_sha256']==p.sha(pairing_path)
    assert pairing['all_rows_price_quantity_and_floor_clock_exact']
    assert order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==order['intrasecond_event_inversions']==0
    artifact=p.Path(pairing['native_field_artifact']['path'])
    artifact_identity=order['accepted_artifact_identity_guard']
    assert p.identity(artifact)==artifact_identity
    prefix_path=WHOLE/'full_union_trade_prefix_audit/full_union_native_trade_prefix_diagnostics.json'
    prefix={r['symbol']:r for r in p.json.loads(prefix_path.read_text())['records']}
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    offset=calendar['dates'].index(DAY)*4
    execution=p.np.load(original/'earliest_execution_times.npy')
    midnight=int(p.datetime.fromisoformat(DAY).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())*1000000000
    previous=p.json.loads((p.OUT/'new_candidate_v7_staging/receipt.json').read_text())
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='staging_source_defined_individual_hours',
        definition=previous['definition'],source_availability_policy=previous['source_availability_policy'],
        source_pairing_sha256=p.sha(pairing_path),source_intrasecond_proof_sha256=p.sha(order_path),
        native_artifact_identity=artifact_identity,source_coarse_proof_sha256=p.sha(coarse_proof_path),
        requested_stockdays=len(wanted),requested_additional_hours=sum(len(v) for v in wanted.values()),
        previously_admitted_hours_excluded=88,special_factor_hours_excluded=excluded,records=[],
        candidate_hours_admitted=0,model_input_complete=False,production_ready=False,
        original_sampled_quote_clock_failures_preserved=True,missing_minutes_never_filled=True,
        resident_scope='one stock minute dictionary; <=65536 actual events per input batch')

    def finish(ticker,bars):
        symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
        if symbol not in wanted:return
        native=prefix[symbol]
        assert native['regular_trade_identity_unique'] and native['regular_trade_fields_valid']
        source=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json')
        primary=p.json.loads(source.read_text())
        day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==DAY)
        actual=[bars[min(bars)]['open'],max(r['high'] for r in bars.values()),min(r['low'] for r in bars.values()),bars[max(bars)]['close'],sum(r['volume'] for r in bars.values()),sum(r['amount_x10000'] for r in bars.values())]
        daily=[int(D(day[f])*10000) for f in ['open','high','low','close']]+[int(day['volume']),int(D(day['amount'])*10000)]
        record=dict(symbol=symbol,requested_slots=sorted(wanted[symbol]),hours=[],quarantined_hours=[],primary_source_path=str(source),primary_source_sha256=p.sha(source))
        if day['tradestatus']!='1' or actual!=daily:
            record.update(status='stockday_primary_six_fields_failed',daily_actual=actual,daily_primary=daily)
            result['records'].append(record);return
        assert actual[-2]==native['regular_actual_trade_total_quantity'] and D(actual[-1])/10000==D(native['regular_calculated_actual_trade_total_amount'])
        witness=native['quote_prefix_witnesses']
        quote_path=p.Path(witness['path']);raw=quote_path.read_bytes()
        assert p.hashlib.sha256(raw).hexdigest()==witness['sha256']
        quotes=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pylist()
        quotes.sort(key=lambda r:(r['quote_reported_ms'],r['quote_received_ms'],r['quote_sorted_index']))
        minutes=[];qty=cash=0;missing=[]
        for minute in GRID:
            trade=bars.get(minute)
            if trade:
                row={k:v for k,v in trade.items() if k not in ['first_key','last_key']}
                row['source_kind']='native_actual_trades'
                qty+=row['volume'];cash+=row['amount_x10000']
            else:
                eligible=[q for q in quotes if minute*60000<=q['quote_reported_ms']<(minute+1)*60000
                    and q['whole_trade_quantity_prefix_exact'] and q['calculated_trade_prefix_amount_exact'] and q['last_trade_price_exact']
                    and D(q['quote_quantity'])==qty and D(q['quote_Turnover'])*10000==cash
                    and q['last_actual_trade_EventTime_ms']<=(minute+1)*60000]
                if not eligible:
                    missing.append(minute);continue
                q=eligible[0];price=D(q['quote_LastPrice'])*10000
                assert price==price.to_integral_value() and price>0
                row={f:int(price) for f in ['open','high','low','close']}
                row.update(volume=0,amount_x10000=0,trades=0,source_kind='genuine_quote_in_verified_zero_trade_interval',
                    source_quote_sorted_index=q['quote_sorted_index'],source_quote_sequence=q['quote_seq_no'],
                    source_quote_reported_ms=q['quote_reported_ms'],source_quote_received_ms=q['quote_received_ms'],
                    source_last_event_ms=q['last_actual_trade_EventTime_ms'],
                    source_dependency_ready_ms=max(q['quote_received_ms'],q['all_prefix_trades_latest_LocalTime_ms'],q['last_actual_trade_EventTime_ms']))
            row.update(symbol=symbol,date=DAY,left_minute=minute,cumulative_actual_quantity=qty,cumulative_actual_calculated_amount_x10000=cash)
            minutes.append(row)
        assert qty==actual[-2] and cash==actual[-1]
        index={r['left_minute']:r for r in minutes}
        for slot in sorted(wanted[symbol]):
            grid=GRID[slot*60:(slot+1)*60]
            absent=[minute for minute in grid if minute not in index]
            if absent:
                record['quarantined_hours'].append(dict(slot=slot,reason='no_genuine_quote_for_actual_zero_trade_interval',missing_minutes=absent));continue
            block=[index[minute] for minute in grid]
            h=dict(slot=slot,open=block[0]['open'],high=max(r['high'] for r in block),low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),amount_x10000=sum(r['amount_x10000'] for r in block),
                stored_dependency_ready_ms=max(r['source_dependency_ready_ms'] for r in block),existing_earliest_execution_utc_ns=int(execution[offset+slot]),
                actual_trade_minutes=sum(r['trades']>0 for r in block),genuine_zero_trade_quote_minutes=sum(r['trades']==0 for r in block))
            assert all(h[f]==wanted[symbol][slot][f] for f in ['open','high','low','close','volume'])
            if midnight+h['stored_dependency_ready_ms']*1000000>=h['existing_earliest_execution_utc_ns']:
                record['quarantined_hours'].append(dict(slot=slot,reason='source_record_dependencies_not_strictly_before_existing_execution'));continue
            record['hours'].append(h)
        if record['hours']:
            folder=DEST/('symbol='+symbol);folder.mkdir(exist_ok=True)
            path=folder/'native_candidate_minutes.parquet'
            keys=sorted({k for r in minutes for k in r})
            p.pq.write_table(p.pa.Table.from_pylist([{k:r.get(k) for k in keys} for r in minutes]),path,compression='zstd')
            record.update(status='additional_native_hours_staged',minute_path=str(path),minute_sha256=p.sha(path),verified_identity=p.identity(path),
                observed_minutes=len(minutes),missing_source_quote_minutes=missing,quote_source_path=str(quote_path),quote_source_sha256=witness['sha256'],
                primary_daily_six_fields_exact=True,source_actual_quantity=qty,source_actual_amount_x10000=cash)
        else:record['status']='no_requested_hour_passed'
        result['records'].append(record)
        result.update(updated_utc=p.utc(),processed_stockdays=len(result['records']),staged_additional_hours=sum(len(r['hours']) for r in result['records']))
        p.atomic_json(output,result)

    current=None;bars={};completed=set()
    for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
        frame=batch.to_pandas()
        for ticker,part in frame.groupby('ticker',sort=False):
            if ticker!=current:
                if current is not None:finish(current,bars);completed.add(current)
                assert ticker not in completed,'Native artifact is not stock-contiguous'
                current=ticker;bars={}
            symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
            if symbol not in wanted:continue
            part=part.sort_values(['event_ms','tran_id'],kind='stable').copy()
            _,minute=labels(part.event_ms.to_numpy()//1000,'floor_left')
            part['minute']=minute
            assert (part.price_x10000>0).all() and (part.volume>0).all()
            assert int(part.price_x10000.max())*int(part.volume.max())*len(part)<2**63
            part['amount']=part.price_x10000*part.volume
            part['ready']=p.np.maximum(part.event_ms,part.local_ms)
            summarized=part.groupby('minute',sort=False).agg(open=('price_x10000','first'),high=('price_x10000','max'),low=('price_x10000','min'),close=('price_x10000','last'),volume=('volume','sum'),amount_x10000=('amount','sum'),trades=('tran_id','size'),first_ms=('event_ms','first'),first_transaction_id=('tran_id','first'),last_ms=('event_ms','last'),last_transaction_id=('tran_id','last'),source_last_event_ms=('event_ms','max'),source_dependency_ready_ms=('ready','max'))
            for minute,r in summarized.iterrows():
                new={k:int(v) for k,v in r.items()}
                new['first_key']=(new.pop('first_ms'),new['first_transaction_id']);new['last_key']=(new.pop('last_ms'),new['last_transaction_id'])
                old=bars.get(int(minute))
                if old is None:bars[int(minute)]=new;continue
                if new['first_key']<old['first_key']:
                    for k in ['first_key','first_transaction_id','open']:old[k]=new[k]
                if new['last_key']>old['last_key']:
                    for k in ['last_key','last_transaction_id','close']:old[k]=new[k]
                old['high']=max(old['high'],new['high']);old['low']=min(old['low'],new['low'])
                for k in ['volume','amount_x10000','trades']:old[k]+=new[k]
                for k in ['source_last_event_ms','source_dependency_ready_ms']:old[k]=max(old[k],new[k])
    if current is not None:finish(current,bars)
    assert p.identity(artifact)==artifact_identity
    assert len(result['records'])==len(wanted)
    result.update(status='staged_pending_independent_verification',finished_utc=p.utc(),processed_stockdays=len(result['records']),
        staged_stockdays=sum(bool(r['hours']) for r in result['records']),staged_additional_hours=sum(len(r['hours']) for r in result['records']),
        quarantine_reason_counts=dict(Counter(h['reason'] for r in result['records'] for h in r['quarantined_hours'])),
        peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':
    main()
