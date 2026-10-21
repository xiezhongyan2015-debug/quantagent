"""Independently reproduce all reference comparisons, including trade-empty cuts.

Missing pure-trade minute rows are retained as missing. In addition, compare
each actual M1 aggregate with the pre-existing, explicitly pinned complete-hour
diagnostic so zero-trade intervals do not silently shrink the486-hour audit.
This supplemental comparison cannot certify those zero intervals or admit bars.
"""
from collections import Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .probe_remaining_tencent_actual_minute_hours_bounded import DEST, RAW, FIELDS

def reconstruct(rows,date):
    observed={}
    for row in rows:
        if row[0][:8]!=date.replace('-',''):continue
        clock=int(row[0][8:10])*60+int(row[0][10:12]);assert clock not in observed
        values={f:D(row[k])*10000 for f,k in [('open',1),('close',2),('high',3),('low',4)]};values['volume']=D(row[5])*100
        assert all(v.is_finite() and v==int(v) for v in values.values())
        assert min(values[f] for f in FIELDS[:4])>0 and values['volume']>=0
        observed[clock]={k:int(v) for k,v in values.items()}
    grid=list(range(571,691))+list(range(781,901));assert all(m in observed for m in grid) and set(observed)<=set(grid+[570])
    auction=observed.get(570)
    if auction and auction['volume']>0:
        observed[571]['open']=auction['open'];observed[571]['high']=max(observed[571]['high'],auction['high']);observed[571]['low']=min(observed[571]['low'],auction['low']);observed[571]['volume']+=auction['volume']
    return {m-1:observed[m] for m in grid}

def hour(values):
    return dict(open=values[0]['open'],high=max(x['high'] for x in values),low=min(x['low'] for x in values),close=values[-1]['close'],volume=sum(x['volume'] for x in values))

def main():
    op=DEST/'independent_verification.json';assert not op.exists();rp=DEST/'receipt.json';source=p.json.loads(rp.read_text())
    assert source['status']=='complete_all_remaining_scoped_actual_m1_reference_diagnostic_no_admission'
    parent_path=p.OUT/'actual_interval_quantity_diagnostic_v24/receipt.json';assert p.sha(parent_path)==source['parent_diagnostic_sha256'];parent=p.json.loads(parent_path.read_text());base={(r['date'],r['symbol'],r['slot']):r for r in parent['records']}
    cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r');assert p.sha(RAW/'dataset/meta.json')==source['raw_meta_sha256']
    requested={(r['date'],r['symbol']):r['slots'] for r in source['scoped_stockdays']};assert len(requested)==source['actual_missing_stockdays']==389 and sum(map(len,requested.values()))==486
    pins={(r['date'],r['prefix']):r for r in source['source_leaf_pins']};current=None;frame=None;seen=set();counts=Counter();records=[];qualified=[]
    for record in source['records']:
        date,symbol=record['date'],record['symbol'];key=date,symbol;assert key in requested and key not in seen and date in cal['dates'];seen.add(key)
        for slot in requested[key]:assert not mask[cal['dates'].index(date)*4+slot,cal['union_symbols'].index(symbol)]
        assert record['requested_slots']==requested[key]
        if record['status']!='actual240_minutes_compared':
            counts['unresolved_stockdays']+=1;counts['unresolved_hours']+=len(requested[key]);continue
        leaf=p.Path(record['source_reference_path']);assert p.identity(leaf)==record['source_reference_identity'] and p.sha(leaf)==record['source_reference_sha256']
        data=p.json.loads(leaf.read_text());assert data['code']==0;code=symbol[-2:].lower()+symbol[:6];actual=data['data'][code]['m1'];assert len(actual)==record['actual_response_rows']<=241
        quotes=reconstruct(actual,date);assert len(quotes)==240
        day=hour(list(quotes.values()));assert day==record['reference_day'];pp=p.Path(record['primary_daily_path']);assert p.sha(pp)==record['primary_daily_sha256'];primary=p.json.loads(pp.read_text());pr=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
        delta={f:day[f]-int(D(pr[f])*10000) for f in FIELDS[:4]};delta['volume']=day['volume']-int(pr['volume']);assert delta==record['primary_day_differences'] and (not any(delta.values()))==record['all_five_day_fields_exact']
        group=date,symbol[:2]
        if current!=group:
            frame=None;pin=pins[group];mp=p.Path(pin['path']);assert p.identity(mp)==pin['verified_identity'] and p.sha(mp)==pin['sha256'];frame=p.pq.ParquetFile(mp).read(use_threads=False).to_pandas();current=group
        stock=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute')
        assert {h['slot'] for h in record['hours']}==set(requested[key])
        for h in record['hours']:
            slot=h['slot'];start=[570,630,780,840][slot];grid=list(range(start,start+60));qhour=hour([quotes[m] for m in grid]);assert qhour==h['reference_hour']
            missing=[m for m in grid if m not in stock.index];assert missing==h['missing_native_minutes'];diffs=[];positive=0
            for m in grid:
                if m not in stock.index:continue
                n=stock.loc[m];d={f:int(n[f])-quotes[m][f] for f in FIELDS};positive+=int(n.trades)>0 and int(n.volume)>0
                if any(d.values()):diffs.append(dict(left_minute=m,differences=d))
            assert diffs==h['minute_conflicts'] and positive==h['native_positive_minutes'];assert (not diffs and not missing)==h['all60_minute_fields_exact']
            if not missing:
                nhour=hour([{f:int(stock.loc[m,f]) for f in FIELDS} for m in grid]);assert nhour==h['native_hour'] and nhour=={f:base[date,symbol,slot]['native_fixed_hour'][f] for f in FIELDS}
                dh={f:qhour[f]-nhour[f] for f in FIELDS};assert dh==h['hour_differences'] and (not any(dh.values()))==h['all_five_hour_fields_exact']
            else:
                assert h['native_hour'] is h['hour_differences'] is None and not h['all_five_hour_fields_exact'];counts['pure_trade_minute_incomplete_hours']+=1
            previous={f:base[date,symbol,slot]['native_fixed_hour'][f] for f in FIELDS};pdiff={f:qhour[f]-previous[f] for f in FIELDS};exact=not any(pdiff.values())
            row=dict(date=date,symbol=symbol,slot=slot,reference_hour=qhour,pinned_prior_hour=previous,all_prior_hour_five_fields_exact=exact,prior_hour_differences=pdiff,pure_trade_minute_complete=not missing,missing_pure_trade_minutes=missing,actual_all60_minute_fields_exact=not missing and not diffs,reference_day_differences=delta,source_reference_path=str(leaf),source_reference_sha256=record['source_reference_sha256'],requires_full_native_minute_quote_and_arrival_proof_before_admission=True)
            records.append(row);counts['all_scoped_hour_aggregates_checked']+=1;counts['pure_trade_hour_five_exact']+=h['all_five_hour_fields_exact'];counts['prior_hour_five_exact']+=exact;counts['all60_actual_minute_fields_exact_hours']+=not missing and not diffs
            for f,v in pdiff.items():counts[f+'_exact_hour_aggregates']+=v==0
            if exact:qualified.append(row)
        counts['actual_complete_stockdays']+=1;counts['actual_regular_reference_minutes']+=240
    assert seen==set(requested) and counts['all_scoped_hour_aggregates_checked']+counts['unresolved_hours']==486
    assert counts['actual_complete_stockdays']==source['counts'].get('actual_complete_stockdays',0) and counts['pure_trade_hour_five_exact']==source['counts'].get('hour_all_five_exact',0)
    assert counts['all60_actual_minute_fields_exact_hours']==source['counts'].get('all60_minute_fields_exact_hours',0)
    p.atomic_json(op,dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_source_receipt_sha256=p.sha(rp),pinned_prior_complete_hour_diagnostic_sha256=p.sha(parent_path),counts=dict(counts),records=records,additional_reference_exact_hours_pending_native_truth_proof=qualified,candidate_hours_admitted=0,missing_pure_trade_intervals_not_filled=True,prior_zero_interval_diagnostics_not_promoted_to_admission=True,all486_actual_missing_hours_accounted_for=True,source_price_volume_and_clock_unchanged=True,no_trade_archives_reopened=True,network_requests=0,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(p.json.dumps(dict(counts=counts,additional_exact_hour_keys=[(r['date'],r['symbol'],r['slot']) for r in qualified])),flush=True)

if __name__=='__main__':main()
