"""Stage104 minute-exact hours with a reproduced coarse carry-open discrepancy.

Only actual milliseconds and shares are written; the coarse carry-open price
never replaces the actual first-trade minute or raw hour price.
"""
from collections import defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID,labels

DEST=p.OUT/'reconciled_tail_millisecond_staging'
RAW=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_raw'


def stock_bars(artifact,wanted):
    current=None;bars={};finished=set()
    for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
        frame=batch.to_pandas()
        for ticker,part in frame.groupby('ticker',sort=False):
            if ticker!=current:
                if current in wanted:yield current,bars
                if current is not None:finished.add(current)
                assert ticker not in finished;current=ticker;bars={}
            if ticker not in wanted:continue
            part=part.sort_values(['event_ms','tran_id'],kind='stable').copy()
            regular,minute=labels(part.event_ms.to_numpy()//1000,'floor_left');assert regular.all();part['minute']=minute
            assert (part.price_x10000>0).all() and (part.volume>0).all()
            assert int(part.price_x10000.max())*int(part.volume.max())*len(part)<2**63
            part['amount']=part.price_x10000*part.volume;part['ready']=p.np.maximum(part.event_ms,part.local_ms)
            summary=part.groupby('minute',sort=False).agg(open=('price_x10000','first'),high=('price_x10000','max'),low=('price_x10000','min'),close=('price_x10000','last'),volume=('volume','sum'),amount_x10000=('amount','sum'),trades=('tran_id','size'),first_ms=('event_ms','first'),first_transaction_id=('tran_id','first'),last_ms=('event_ms','last'),last_transaction_id=('tran_id','last'),source_last_event_ms=('event_ms','max'),source_dependency_ready_ms=('ready','max'))
            for minute,row in summary.iterrows():
                new={k:int(v) for k,v in row.items()};new['first_key']=(new['first_ms'],new['first_transaction_id']);new['last_key']=(new['last_ms'],new['last_transaction_id']);old=bars.get(int(minute))
                if old is None:bars[int(minute)]=new;continue
                if new['first_key']<old['first_key']:
                    for f in ['first_key','first_ms','first_transaction_id','open']:old[f]=new[f]
                if new['last_key']>old['last_key']:
                    for f in ['last_key','last_ms','last_transaction_id','close']:old[f]=new[f]
                old['high']=max(old['high'],new['high']);old['low']=min(old['low'],new['low'])
                for f in ['volume','amount_x10000','trades']:old[f]+=new[f]
                for f in ['source_last_event_ms','source_dependency_ready_ms']:old[f]=max(old[f],new[f])
    if current in wanted:yield current,bars


def main():
    lock=p.locked('stage_reconciled_tail_ms');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    diagnostic_path=p.OUT/'tail_previous_close_coarse_reconciliation/receipt.json';diagnostic=p.json.loads(diagnostic_path.read_text())
    proof_path=diagnostic_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(diagnostic_path)
    selected={(h['date'],h['symbol'],h['slot']) for h in proof['qualified_fixed_formula_diagnostic_hours']};assert len(selected)==104
    desired=defaultdict(lambda:defaultdict(list))
    for h in diagnostic['records']:
        if (h['date'],h['symbol'],h['slot']) in selected:
            assert not h['raw_already_admitted'] and h['fixed_formula_all5_exact'] and h['previous_close_independent_minute_exact']
            desired[h['date']][h['symbol'][:6]].append(h)
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and p.sha(audit_path)==diagnostic['parent_raw_audit_sha256']
    assert audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='staging_actual_millisecond_minutes',parent_raw_audit_sha256=p.sha(audit_path),
        coarse_reconciliation_receipt_sha256=p.sha(diagnostic_path),coarse_reconciliation_independent_proof_sha256=p.sha(proof_path),native_source_pins=diagnostic['source_millisecond_proof_pins'],
        definition='Actual chronological first/max/min/last native millisecond trades; fixed floor-left phase clock and2s grace unchanged; positive actual trade in every selectedminute; all60 independent minuteOHLCV exact; primary whole-day6exact. Tencent coarse carry-open discrepancy reproduced under one fixed formula with independent preceding-minute close. Coarse carry-open never substitutes native minute or hour values.',
        source_vendor_coarse_implementation_certified=False,source_live_clock_certified=False,receiver_timestamps_are_observed_not_invented=True,
        candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,records=[])
    p.atomic_json(output,result)
    for pin in result['native_source_pins']:
        date=pin['date'];pairing_path=p.Path(pin['pairing_path']);assert p.sha(pairing_path)==pin['pairing_sha256'];pair=p.json.loads(pairing_path.read_text())
        order_path=p.Path(pin['order_path']);assert p.sha(order_path)==pin['order_sha256'];order=p.json.loads(order_path.read_text())
        artifact=p.Path(pair['native_field_artifact']['path']);assert p.identity(artifact)==order['accepted_artifact_identity_guard']
        for ticker,bars in stock_bars(artifact,desired[date]):
            symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ');source=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(source.read_text())
            daily=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
            actual=[bars[min(bars)]['open'],max(r['high'] for r in bars.values()),min(r['low'] for r in bars.values()),bars[max(bars)]['close'],sum(r['volume'] for r in bars.values()),sum(r['amount_x10000'] for r in bars.values())]
            expected=[int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
            assert daily['tradestatus']=='1' and actual==expected
            rows=[];hours=[];offset=cal['dates'].index(date)*4;column=cal['union_symbols'].index(symbol)
            for requested in sorted(desired[date][ticker],key=lambda h:h['slot']):
                slot=requested['slot'];assert not mask[offset+slot,column];minutes=GRID[slot*60:(slot+1)*60];block=[bars[m] for m in minutes]
                values=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),source_event_ms_max=max(b['source_last_event_ms'] for b in block),source_ready_ms=max(b['source_dependency_ready_ms'] for b in block))
                assert all(values[f]==requested['native'][f] for f in requested['native'])
                prior=max(m for m in bars if m<minutes[0]);carry=bars[prior]['close']
                assert prior==requested['previous_observed_minute'] and carry==requested['previous_observed_close']
                coarse=dict(open=carry,high=max(carry,values['high']),low=min(carry,values['low']),close=values['close'],volume=values['volume']);assert coarse==requested['coarse_reference']
                qpath=p.Path(requested['independent_minute_path']);data=qpath.read_bytes();assert p.hashlib.sha256(data).hexdigest()==requested['independent_minute_sha256']
                text=data.decode();qrows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);refs={q['day']:q for q in qrows}
                for m in minutes:
                    b=bars[m];end=m+1;q=refs[f'{date} {end//60:02d}:{end%60:02d}:00'];assert b['volume']>0 and b['trades']>0
                    assert all(b[f]==D(q[f])*10000 for f in ['open','high','low','close']) and D(b['volume'])==D(q['volume'])
                    rows.append(dict(symbol=symbol,date=date,left_minute=m,source_kind='actual_millisecond_trade',**{k:v for k,v in b.items() if k not in ['first_key','last_key']}))
                hours.append(dict(slot=slot,date=date,purpose='new_raw_market_hour_pending_admission',**values,sina_reference_path=str(qpath),sina_reference_sha256=requested['independent_minute_sha256'],
                    previous_observed_minute=prior,previous_observed_close=carry,coarse_reference=coarse,coarse_definition_reconciliation_exact=True,independent_minute60_ohlcv_exact=True))
            folder=DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True,exist_ok=True);leaf=folder/'native_candidate_minutes.parquet';p.pq.write_table(p.pa.Table.from_pylist(rows),leaf,compression='zstd')
            result['records'].append(dict(symbol=symbol,date=date,hours=hours,minute_path=str(leaf),minute_sha256=p.sha(leaf),verified_identity=p.identity(leaf),primary_source_path=str(source),primary_source_sha256=p.sha(source),stockday_native_primary_six_exact=True))
            result.update(stockdays_staged=len(result['records']),updated_utc=p.utc());p.atomic_json(output,result)
        assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    assert sum(len(r['hours']) for r in result['records'])==104 and len(result['records'])==97
    result.update(status='staged_actual_millisecond_hours_pending_independent_audit',finished_utc=p.utc(),hours_staged=104,actual_minutes_staged=6240,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
