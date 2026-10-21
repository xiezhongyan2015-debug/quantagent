"""Stage real second-clock minutes; do not fabricate arrival timestamps or admit."""
from collections import defaultdict,Counter
from . import bounded_tail_pipeline as p


def main():
    lock=p.locked('stage_cached_sept21_source_second');dest=p.OUT/'cached_sept21_source_second_v26_staging';dest.mkdir(exist_ok=True)
    output=dest/'receipt.json';assert not output.exists()
    proof_path=p.OUT/'cached_sina_sept21_actual_hour_comparison/receipt.json';comparison=p.json.loads(proof_path.read_text());assert comparison['status']=='complete_cached_sept21_minute_comparison_no_admission'
    qualified=[dict(h,sina_reference_path=h['source_sina_path'],sina_reference_sha256=h['source_sina_sha256']) for h in comparison['records'] if h['status']=='direct_coarse_exact_minutes_pending_source_arrival_policy'];assert len(qualified)==3
    progress_path=p.OUT/'minute_observation_progress.json';progress=p.json.loads(progress_path.read_text());groups={(r['date'],r['prefix']):r for r in progress['groups']}
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text())
    coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof=p.json.loads(coarse_proof_path.read_text());assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    refs={}
    for g in coarse['groups']:
        if g['date'] not in ['2026-09-21']:continue
        raw=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==g['comparison_sha256']
        for r in p.json.loads(raw)['comparisons']:
            if r['model']=='floor_left':refs[r['date'],r['symbol'],r['slot']]=r
    raw_candidate=p.BASE/'backfilled_panel_candidate_native_actual_quantity_v25_raw';audit_path=raw_candidate/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(raw_candidate/'dataset/meta.json')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(raw_candidate/'dataset/hour_complete.npy',mmap_mode='r')
    incoming=defaultdict(list);excluded=Counter()
    for h in qualified:
        row=calendar['dates'].index(h['date'])*4+h['slot'];column=calendar['union_symbols'].index(h['symbol'])
        if mask[row,column]:excluded['already_admitted_raw_hour']+=1;continue
        ref=refs.get((h['date'],h['symbol'],h['slot']))
        if not ref or not ref.get('differences') or not all(v==0 for v in ref['differences'].values()):excluded['independent_hour_conflict']+=1;continue
        incoming[h['date'],h['symbol']].append(h)
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),minute_reference_proof_sha256=p.sha(proof_path),native_observation_progress_sha256=p.sha(progress_path),independent_hour_proof_sha256=p.sha(coarse_proof_path),latest_raw_audit_sha256=p.sha(audit_path),status='staging_real_minutes_pending_arrival_time_qualification',definition='same globallyfixed floor_left source-second intervals; first/last actual transactionID price; auction andsession 2sgrace unchanged; exactly60genuine positive-trade observations perhour; nozero-interval fill',source_arrival_or_live_clock_certificate=False,arrival_times_not_invented=True,candidate_hours_admitted=0,raw_panel_modified=False,features_labels_training_not_started=True,excluded_diagnostic_hours=dict(excluded),records=[])
    grid=list(range(570,690))+list(range(780,900))
    for (date,symbol),requested in sorted(incoming.items()):
        group=groups[date,symbol[:2]];validation_path=p.Path(group['validation_path']);data=validation_path.read_bytes();assert p.hashlib.sha256(data).hexdigest()==group['validation_sha256'];validation=next(r for r in p.json.loads(data)['stockdays'] if r['symbol']==symbol)
        assert validation['source_inventory_exact'] and validation['raw_positive'] and validation['clocks']['floor_left']['daily_six_fields_exact']
        if not validation['unique_transaction_order_certified']:
            excluded['global_transaction_order_not_certified']+=len(requested);receipt.setdefault('excluded_stockdays',[]).append(dict(symbol=symbol,date=date,hours=[h['slot'] for h in requested],reason='source_global_transaction_order_not_certified',minute_all60_and_coarse_match_retained=True));continue
        minute_path=p.Path(requested[0]['native_minute_path']);raw=minute_path.read_bytes();assert p.hashlib.sha256(raw).hexdigest()==requested[0]['native_minute_sha256']
        frame=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pandas();frame=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute')
        rows=[];hours=[]
        for h in sorted(requested,key=lambda r:r['slot']):
            clocks=grid[h['slot']*60:(h['slot']+1)*60];block=frame.loc[clocks];assert len(block)==60 and (block.trades>0).all() and (block.volume>0).all()
            values=dict(open=int(block.iloc[0].open),high=int(block.high.max()),low=int(block.low.min()),close=int(block.iloc[-1].close),volume=int(block.volume.sum()),amount_x10000=int(block.amount_x10000.sum()))
            ref=refs[date,symbol,h['slot']];assert all(values[f]==ref['native_hour'][f] for f in ['open','high','low','close','volume'])
            hours.append(dict(slot=h['slot'],date=date,**values,source_event_seconds_max=int(block.last_ts.max()),source_local_arrival_ms=None,sina_reference_path=h['sina_reference_path'],sina_reference_sha256=h['sina_reference_sha256'],independent_minute60_ohlcv_exact=True,independent_hour_ohlcv_exact=True))
            for minute,row in block.iterrows():rows.append(dict(symbol=symbol,date=date,left_minute=int(minute),**{k:int(v) for k,v in row.items() if k not in ['ticker','date']}))
        target=dest/('date='+date)/('symbol='+symbol)/'native_candidate_minutes.parquet';target.parent.mkdir(parents=True,exist_ok=True);p.pq.write_table(p.pa.Table.from_pylist(rows),target,compression='zstd')
        receipt['records'].append(dict(symbol=symbol,date=date,hours=hours,minute_path=str(target),minute_sha256=p.sha(target),verified_identity=p.identity(target),native_source_path=str(minute_path),native_source_sha256=requested[0]['native_minute_sha256'],validation_path=str(validation_path),validation_sha256=group['validation_sha256'],primary_daily_reference=validation['primary_daily_reference'],stockday_native_primary_six_exact=True,global_transaction_order_certified=True,source_arrival_time_qualification_pending=True))
    receipt.update(finished_utc=p.utc(),status='real_minute_staging_complete_pending_arrival_time_qualification',stockdays_staged=len(receipt['records']),hours_staged=sum(len(r['hours']) for r in receipt['records']),minute_observations_staged=sum(len(r['hours'])*60 for r in receipt['records']),excluded_diagnostic_hours=dict(excluded),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
