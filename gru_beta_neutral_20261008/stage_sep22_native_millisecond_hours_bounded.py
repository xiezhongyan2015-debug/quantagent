"""Stage Sep22 native real minutes after actual millisecond and order proof.

Only predeclared exact independent minute/hour matches are considered. Keep
received times as observed and keep raw-market and execution admission separate.
One stock's minute summaries and one65536-row trade batch are resident.
"""
from collections import defaultdict,Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID,labels

DAY='2026-09-22'
DEST=p.OUT/'sep22_native_millisecond_staging'
RAW=p.BASE/'backfilled_panel_candidate_native_second_v10_raw'


def main():
    lock=p.locked('stage_sep22_native_ms');DEST.mkdir(exist_ok=True)
    output=DEST/'receipt.json';assert not output.exists()
    pairing_path=p.OUT/'millisecond_pairing_sep22/receipt.json';pairing=p.json.loads(pairing_path.read_text())
    order_path=pairing_path.parent/'intrasecond_order_verification.json';order=p.json.loads(order_path.read_text())
    assert pairing['all_rows_price_quantity_and_floor_clock_exact'] and pairing['stockdays_checked']==624
    assert order['paired_source_receipt_sha256']==p.sha(pairing_path)
    assert order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==order['intrasecond_event_inversions']==0
    artifact=p.Path(pairing['native_field_artifact']['path']);assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    raw_audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(raw_audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    refs={};source_proofs=[]
    for name in ['sina_tail_minute_expanded','sina_tail_nonmonotonic_references']:
        proof_path=p.OUT/name/'independent_verification.json';proof=p.json.loads(proof_path.read_text());assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(proof_path.parent/'receipt.json')
        source_proofs.append(dict(path=str(proof_path),sha256=p.sha(proof_path)))
        for h in proof['qualified_minute_diagnostic_hours']:
            if h['date']==DAY:
                key=h['symbol'],h['slot'];assert key not in refs;refs[key]=h
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text())
    coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof=p.json.loads(coarse_proof_path.read_text());assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    expected={}
    for g in coarse['groups']:
        if g['date']!=DAY:continue
        raw=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==g['comparison_sha256']
        for h in p.json.loads(raw)['comparisons']:
            if h['model']=='floor_left' and h.get('differences') and all(v==0 for v in h['differences'].values()):expected[h['symbol'],h['slot']]=h['native_hour']
    # The179alreadyadmitted second-time hours are revisited only for actual
    # reception evidence. Previously qualified market values are never replaced.
    prior=p.json.loads((RAW/'raw_panel_merge_receipt.json').read_text())
    previous={(r['symbol'],h['slot']) for r in prior['records'] for h in r['hours']}
    assert len(previous)==179
    wanted=defaultdict(dict);exclusions=Counter()
    offset=calendar['dates'].index(DAY)*4
    for (symbol,slot),ref in refs.items():
        if (symbol,slot) not in expected:exclusions['hour_reference_conflict']+=1;continue
        complete=bool(mask[offset+slot,calendar['union_symbols'].index(symbol)])
        if complete and (symbol,slot) not in previous:exclusions['already_admitted_other_source']+=1;continue
        wanted[symbol][slot]=dict(reference=ref,expected=expected[symbol,slot],purpose='recorded_reception_evidence_for_existing_raw_hour' if complete else 'new_raw_market_hour_pending_admission')
    assert sum(len(v) for v in wanted.values())==307 and previous<=set((s,k) for s,slots in wanted.items() for k in slots)
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),status='staging_actual_millisecond_minutes',date=DAY,
        source_pairing_sha256=p.sha(pairing_path),source_intrasecond_proof_sha256=p.sha(order_path),native_artifact_identity=order['accepted_artifact_identity_guard'],
        independent_minute_proofs=source_proofs,independent_hour_proof_sha256=p.sha(coarse_proof_path),parent_raw_audit_sha256=p.sha(raw_audit_path),
        definition='actual millisecond chronological first/open, max/high,min/low,last/close; samefloor_left secondminuteclock andfixed2sphasegrace; nozerointerval fill; all60OHLCV exactindependentSina andhourOHLCV exactTencent; wholedayprimary6exact',
        records=[],excluded=dict(exclusions),candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,
        source_live_clock_certified=False,receiver_timestamps_are_observed_not_invented=True,previous_quote_clock_failures_preserved=True)

    def finish(ticker,bars):
        symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
        if symbol not in wanted:return
        source=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(source.read_text())
        daily=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==DAY)
        actual=[bars[min(bars)]['open'],max(r['high'] for r in bars.values()),min(r['low'] for r in bars.values()),bars[max(bars)]['close'],sum(r['volume'] for r in bars.values()),sum(r['amount_x10000'] for r in bars.values())]
        expected_daily=[int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
        assert daily['tradestatus']=='1' and actual==expected_daily
        rows=[];hours=[]
        for slot,request in sorted(wanted[symbol].items()):
            block=[bars[m] for m in GRID[slot*60:(slot+1)*60]];assert all(r['trades']>0 and r['volume']>0 for r in block)
            h=dict(slot=slot,date=DAY,purpose=request['purpose'],open=block[0]['open'],high=max(r['high'] for r in block),low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),amount_x10000=sum(r['amount_x10000'] for r in block),source_event_ms_max=max(r['source_last_event_ms'] for r in block),source_ready_ms=max(r['source_dependency_ready_ms'] for r in block),sina_reference_path=request['reference']['sina_reference_path'],sina_reference_sha256=request['reference']['sina_reference_sha256'])
            assert all(h[f]==request['expected'][f] for f in ['open','high','low','close','volume'])
            data=p.Path(h['sina_reference_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest()==h['sina_reference_sha256'];text=data.decode();native_refs,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);ref_bytime={r['day']:r for r in native_refs}
            for minute in GRID[slot*60:(slot+1)*60]:
                r=bars[minute];end=minute+1;q=ref_bytime[f'{DAY} {end//60:02d}:{end%60:02d}:00']
                assert all(r[f]==int(D(q[f])*10000) for f in ['open','high','low','close']) and D(r['volume'])==D(q['volume'])
                rows.append(dict(symbol=symbol,date=DAY,left_minute=minute,**{k:v for k,v in r.items() if k not in ['first_key','last_key']}))
            hours.append(h)
        folder=DEST/('symbol='+symbol);folder.mkdir(exist_ok=True);path=folder/'native_candidate_minutes.parquet'
        p.pq.write_table(p.pa.Table.from_pylist(rows),path,compression='zstd')
        result['records'].append(dict(symbol=symbol,date=DAY,hours=hours,minute_path=str(path),minute_sha256=p.sha(path),verified_identity=p.identity(path),primary_source_path=str(source),primary_source_sha256=p.sha(source),stockday_native_primary_six_exact=True))
        result.update(updated_utc=p.utc(),stockdays_staged=len(result['records']));p.atomic_json(output,result)

    current=None;bars={};completed=set()
    for batch in p.pq.ParquetFile(artifact).iter_batches(batch_size=p.BATCH_ROWS,use_threads=False):
        frame=batch.to_pandas()
        for ticker,part in frame.groupby('ticker',sort=False):
            if ticker!=current:
                if current is not None:finish(current,bars);completed.add(current)
                assert ticker not in completed;current=ticker;bars={}
            symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ')
            if symbol not in wanted:continue
            part=part.sort_values(['event_ms','tran_id'],kind='stable').copy()
            regular,minute=labels(part.event_ms.to_numpy()//1000,'floor_left');assert regular.all();part['minute']=minute
            assert (part.price_x10000>0).all() and (part.volume>0).all()
            assert int(part.price_x10000.max())*int(part.volume.max())*len(part)<2**63
            part['amount']=part.price_x10000*part.volume;part['ready']=p.np.maximum(part.event_ms,part.local_ms)
            summarized=part.groupby('minute',sort=False).agg(open=('price_x10000','first'),high=('price_x10000','max'),low=('price_x10000','min'),close=('price_x10000','last'),volume=('volume','sum'),amount_x10000=('amount','sum'),trades=('tran_id','size'),first_ms=('event_ms','first'),first_transaction_id=('tran_id','first'),last_ms=('event_ms','last'),last_transaction_id=('tran_id','last'),source_last_event_ms=('event_ms','max'),source_dependency_ready_ms=('ready','max'))
            for minute,row in summarized.iterrows():
                new={k:int(v) for k,v in row.items()};new['first_key']=(new['first_ms'],new['first_transaction_id']);new['last_key']=(new['last_ms'],new['last_transaction_id']);old=bars.get(int(minute))
                if old is None:bars[int(minute)]=new;continue
                if new['first_key']<old['first_key']:
                    for f in ['first_key','first_ms','first_transaction_id','open']:old[f]=new[f]
                if new['last_key']>old['last_key']:
                    for f in ['last_key','last_ms','last_transaction_id','close']:old[f]=new[f]
                old['high']=max(old['high'],new['high']);old['low']=min(old['low'],new['low'])
                for f in ['volume','amount_x10000','trades']:old[f]+=new[f]
                for f in ['source_last_event_ms','source_dependency_ready_ms']:old[f]=max(old[f],new[f])
    if current is not None:finish(current,bars)
    assert p.identity(artifact)==order['accepted_artifact_identity_guard'] and len(result['records'])==len(wanted)
    hours=[h for r in result['records'] for h in r['hours']]
    counts=Counter(h['purpose'] for h in hours);assert counts['new_raw_market_hour_pending_admission']==128 and counts['recorded_reception_evidence_for_existing_raw_hour']==179
    result.update(status='staged_actual_millisecond_hours_pending_independent_audit',finished_utc=p.utc(),hours_staged=len(hours),counts=dict(counts),actual_minutes_staged=len(hours)*60,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
