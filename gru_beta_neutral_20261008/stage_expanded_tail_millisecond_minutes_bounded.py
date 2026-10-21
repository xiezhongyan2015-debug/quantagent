"""Stage missing hours from the remaining376 independent public references.

Native minutes always use actual millisecond chronological first/max/min/last.
The coarse reference must match directly or be fully reproduced by the already
declared preceding-observed-close formula, with that close independently seen.
Both forms are recorded explicitly; no unexplained coarse conflict is admitted.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import GRID
from .stage_reconciled_tail_millisecond_minutes_bounded import stock_bars

DEST=p.OUT/'expanded_tail_millisecond_v16_staging'
RAW=p.BASE/'backfilled_panel_candidate_native_reconciled_v15_raw'


def main():
    lock=p.locked('stage_expanded_tail_ms');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    reference_path=p.OUT/'sina_remaining376_tail_references/independent_verification.json';reference=p.json.loads(reference_path.read_text())
    assert reference['passed'] and reference['audited_receipt_sha256']==p.sha(reference_path.parent/'receipt.json')
    assert reference['all_requested_queries_complete'] and reference['requested_symbols']==376
    audit_path=RAW/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['audited_raw_meta_sha256']==p.sha(RAW/'dataset/meta.json')
    identity=next(r['verified_identity'] for r in audit['arrays'] if r['name']=='hour_complete.npy');assert p.identity(RAW/'dataset/hour_complete.npy')==identity
    cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    by_date=defaultdict(lambda:defaultdict(list));counts=Counter();seen=set()
    for h in reference['qualified_minute_diagnostic_hours']:
        key=h['date'],h['symbol'],h['slot'];assert key not in seen;seen.add(key)
        if mask[cal['dates'].index(h['date'])*4+h['slot'],cal['union_symbols'].index(h['symbol'])]:counts['already_admitted_raw_hours']+=1;continue
        by_date[h['date']][h['symbol'][:6]].append(h)
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json';coarse=p.json.loads(coarse_path.read_text());coarse_proof_path=coarse_path.parent/'independent_hour_comparison_verification.json';coarse_proof=p.json.loads(coarse_proof_path.read_text())
    assert coarse_proof['passed'] and coarse_proof['audited_receipt_sha256']==p.sha(coarse_path)
    refs={}
    for g in coarse['groups']:
        if g['date'] not in by_date:continue
        data=p.Path(g['comparison_path']).read_bytes();assert p.hashlib.sha256(data).hexdigest()==g['comparison_sha256']
        for r in p.json.loads(data)['comparisons']:
            if r['model']=='floor_left':refs[r['date'],r['symbol'],r['slot']]=r
    native_pins=[]
    for name,date in [('millisecond_pairing','2026-09-23'),('millisecond_pairing_sep22','2026-09-22')]:
        pair_path=p.OUT/name/'receipt.json';pair=p.json.loads(pair_path.read_text());order_path=pair_path.parent/'intrasecond_order_verification.json';order=p.json.loads(order_path.read_text())
        assert pair['all_rows_price_quantity_and_floor_clock_exact'] and pair['stockdays_checked']==624 and order['paired_source_receipt_sha256']==p.sha(pair_path)
        assert order['intrasecond_event_inversions']==order['minute_open_changed_by_intrasecond_order']==order['minute_close_changed_by_intrasecond_order']==0
        assert p.identity(p.Path(pair['native_field_artifact']['path']))==order['accepted_artifact_identity_guard']
        native_pins.append(dict(date=date,pairing_path=str(pair_path),pairing_sha256=p.sha(pair_path),order_path=str(order_path),order_sha256=p.sha(order_path)))
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),source_stock_bars_helper_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/stage_reconciled_tail_millisecond_minutes_bounded.py'),
        status='staging_actual_millisecond_minutes',parent_raw_audit_sha256=p.sha(audit_path),reference_proof_path=str(reference_path),reference_proof_sha256=p.sha(reference_path),
        coarse_comparison_receipt_sha256=p.sha(coarse_path),coarse_independent_proof_sha256=p.sha(coarse_proof_path),native_source_pins=native_pins,
        definition='Actual chronological first/max/min/last native millisecond trades; unchanged floor-left phase clock and2s grace; all60 positive actual minutes match independent SinaOHLCV; primary whole-day6exact. Tencenthour5 either directly matches, or fixed preceding-observed-close coarseopen/high/low formula exactly reproduces everyfield with precedingclose independentlySinaobserved. Nativefirsttradeprices neverreplaced; unexplainedconflicts rejected.',
        source_vendor_coarse_implementation_certified=False,source_live_clock_certified=False,receiver_timestamps_are_observed_not_invented=True,
        candidate_hours_admitted=0,raw_panel_modified=False,production_ready=False,records=[],rejected_hours=[],counts=dict(counts))
    p.atomic_json(output,result)
    for pin in native_pins:
        date=pin['date'];pair=p.json.loads(p.Path(pin['pairing_path']).read_text());order=p.json.loads(p.Path(pin['order_path']).read_text());artifact=p.Path(pair['native_field_artifact']['path'])
        for ticker,bars in stock_bars(artifact,by_date[date]):
            symbol=ticker+('.SH' if ticker.startswith(('6','9')) else '.SZ');source=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(source.read_text())
            daily=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
            native_day=[bars[min(bars)]['open'],max(b['high'] for b in bars.values()),min(b['low'] for b in bars.values()),bars[max(bars)]['close'],sum(b['volume'] for b in bars.values()),sum(b['amount_x10000'] for b in bars.values())]
            expected=[int(D(daily[f])*10000) for f in ['open','high','low','close']]+[int(daily['volume']),int(D(daily['amount'])*10000)]
            day_exact=daily['tradestatus']=='1' and native_day==expected
            requested=by_date[date][ticker];qpath=p.Path(requested[0]['sina_reference_path']);data=qpath.read_bytes();qdigest=p.hashlib.sha256(data).hexdigest();assert all(h['sina_reference_sha256']==qdigest for h in requested)
            text=data.decode();qrows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);qrefs={q['day']:q for q in qrows}
            rows=[];hours=[]
            for h in sorted(requested,key=lambda r:r['slot']):
                slot=h['slot'];minutes=GRID[slot*60:(slot+1)*60];block=[bars[m] for m in minutes];assert all(b['trades']>0 and b['volume']>0 for b in block)
                for m in minutes:
                    end=m+1;q=qrefs[f'{date} {end//60:02d}:{end%60:02d}:00'];b=bars[m]
                    assert all(b[f]==D(q[f])*10000 for f in ['open','high','low','close']) and D(b['volume'])==D(q['volume'])
                values=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount_x10000=sum(b['amount_x10000'] for b in block),source_event_ms_max=max(b['source_last_event_ms'] for b in block),source_ready_ms=max(b['source_dependency_ready_ms'] for b in block))
                coarse=refs[date,symbol,slot];assert all(values[f]==coarse['native_hour'][f] for f in ['open','high','low','close','volume'])
                ref={f:values[f]-coarse['differences'][f] for f in ['open','high','low','close','volume']}
                exact=all(values[f]==ref[f] for f in ref);prior=max((m for m in bars if m<minutes[0]),default=None);carry=bars[prior]['close'] if prior is not None else values['open']
                previous_exact=False
                if prior is not None:
                    end=prior+1;q=qrefs.get(f'{date} {end//60:02d}:{end%60:02d}:00');previous_exact=q is not None and D(q['close'])*10000==carry
                formula=dict(open=carry,high=max(carry,values['high']),low=min(carry,values['low']),close=values['close'],volume=values['volume'])
                kind='direct_coarse_ohlcv_exact' if exact else 'fixed_previous_close_coarse_reconciled' if previous_exact and formula==ref else None
                if not day_exact or kind is None:
                    reason='primary_whole_day_six_conflict' if not day_exact else 'unexplained_coarse_conflict';counts[reason]+=1
                    result['rejected_hours'].append(dict(symbol=symbol,date=date,slot=slot,reason=reason,native_hour={f:values[f] for f in ref},coarse_reference=ref,coarse_differences=coarse['differences'],previous_observed_minute=prior,previous_observed_close=carry,previous_close_independent_exact=previous_exact));continue
                counts[kind]+=1
                hours.append(dict(slot=slot,date=date,purpose='new_raw_market_hour_pending_admission',**values,sina_reference_path=str(qpath),sina_reference_sha256=qdigest,
                    previous_observed_minute=prior,previous_observed_close=carry,previous_close_independent_exact=previous_exact,coarse_reference=ref,coarse_reference_kind=kind,
                    coarse_definition_reconciliation_exact=True,independent_minute60_ohlcv_exact=True))
                for m in minutes:rows.append(dict(symbol=symbol,date=date,left_minute=m,source_kind='actual_millisecond_trade',**{k:v for k,v in bars[m].items() if k not in ['first_key','last_key']}))
            if hours:
                folder=DEST/('date='+date)/('symbol='+symbol);folder.mkdir(parents=True,exist_ok=True);leaf=folder/'native_candidate_minutes.parquet';p.pq.write_table(p.pa.Table.from_pylist(rows),leaf,compression='zstd')
                result['records'].append(dict(symbol=symbol,date=date,hours=hours,minute_path=str(leaf),minute_sha256=p.sha(leaf),verified_identity=p.identity(leaf),primary_source_path=str(source),primary_source_sha256=p.sha(source),stockday_native_primary_six_exact=True))
            result.update(stockdays_staged=len(result['records']),updated_utc=p.utc(),counts=dict(counts));p.atomic_json(output,result)
        assert p.identity(artifact)==order['accepted_artifact_identity_guard']
    qualified=sum(len(r['hours']) for r in result['records']);assert qualified==counts['direct_coarse_ohlcv_exact']+counts['fixed_previous_close_coarse_reconciled']
    assert qualified+len(result['rejected_hours'])+counts['already_admitted_raw_hours']==len(reference['qualified_minute_diagnostic_hours']) and p.identity(RAW/'dataset/hour_complete.npy')==identity
    result.update(status='staged_actual_millisecond_hours_pending_independent_audit',finished_utc=p.utc(),hours_staged=qualified,actual_minutes_staged=qualified*60,counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
