"""Disk-first M1 reference queries for every still-missing Sep22/23 diagnostic.

One native minute leaf is held at a time, never the full trade archive. Every
request is guarded by the current calendar and remaining raw mask. Reusing a
completed stock-day requires unchanged identity plus its retained small digest.
This is only diagnostics, not panel admission or training authorization.
"""
from collections import defaultdict, Counter
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .diagnose_tencent_explicit_end_m1_bounded import normalize_actual_rows

DEST=p.OUT/'remaining_tencent_actual_m1_hour_reference_diagnostic'
RAW=p.BASE/'backfilled_panel_candidate_native_cached_second_v26_raw'
FIELDS=['open','high','low','close','volume']

def main():
    lock=p.locked('remaining_tencent_actual_m1_hour_reference');DEST.mkdir(exist_ok=True)
    output=DEST/'receipt.json';assert not output.exists()
    dp=p.OUT/'actual_interval_quantity_diagnostic_v24/receipt.json';diagnostic=p.json.loads(dp.read_text())
    cal=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text());mask=p.np.load(RAW/'dataset/hour_complete.npy',mmap_mode='r')
    scope=defaultdict(list)
    for r in diagnostic['records']:
        assert r['date'] in ['2026-09-22','2026-09-23'] and r['date'] in cal['dates']
        row=cal['dates'].index(r['date'])*4+r['slot'];col=cal['union_symbols'].index(r['symbol'])
        if not mask[row,col]:scope[r['date'],r['symbol']].append(r)
    assert sum(map(len,scope.values()))==486 and len(scope)<=395
    existing={}
    pilot=p.OUT/'tencent_actual_m1_aggregated_hour_pilot/receipt.json'
    for r in p.json.loads(pilot.read_text())['results']:
        existing[r['date'],r['symbol']]=dict(path=r['source_reference_path'],sha256=r['source_reference_sha256'])
    progresspath=p.OUT/'minute_observation_progress.json';progress=p.json.loads(progresspath.read_text());groups={(r['date'],r['prefix']):r for r in progress['groups']}
    receipt=dict(status='running_bounded_actual_m1_reference_queries',started_utc=p.utc(),source_code_sha256=p.sha(__file__),normalizer_source_code_sha256=p.sha(p.ROOT/'gru_beta_neutral_20261008/diagnose_tencent_explicit_end_m1_bounded.py'),parent_diagnostic_sha256=p.sha(dp),raw_meta_sha256=p.sha(RAW/'dataset/meta.json'),actual_missing_hours=486,actual_missing_stockdays=len(scope),scoped_stockdays=[dict(date=d,symbol=s,slots=[r['slot'] for r in rs]) for (d,s),rs in sorted(scope.items())],max_attempts_per_stockday=3,max_response_bytes=512*1024,max_total_new_bytes=32*1024**2,max_session_seconds=1800,new_network_bytes=0,network_attempts=0,source_leaf_pins=[],records=[],counts={},candidate_hours_admitted=0,raw_panel_modified=False,canonical_native_clock_changed=False,source_live_or_provider_implementation_certified=False)
    def save():
        receipt.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(output,receipt)
    save();deadline=p.time.monotonic()+1800;current=None;frame=None;sourcepin=None;counts=Counter()
    try:
        for (date,symbol),requests in sorted(scope.items(),key=lambda x:(x[0][0],x[0][1][:2],x[0][1])):
            assert p.time.monotonic()<deadline
            key=date,symbol[:2]
            if current!=key:
                frame=None;group=groups[key];leaf=next(r for r in group['minute_files'] if r['model']=='floor_left');mp=p.Path(leaf['path']);assert p.sha(mp)==leaf['sha256']
                frame=p.pq.ParquetFile(mp).read(use_threads=False).to_pandas();current=key;sourcepin=dict(date=date,prefix=symbol[:2],path=str(mp),sha256=leaf['sha256'],verified_identity=p.identity(mp));receipt['source_leaf_pins'].append(sourcepin)
            request_leaf=DEST/(symbol+'_'+date+'_actual_response.json');old=existing.get((date,symbol));attempts=[];cached=False;body=None
            code=symbol[-2:].lower()+symbol[:6];end=date.replace('-','')+'1501';url=f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m1,{end},241'
            if old:
                request_leaf=p.Path(old['path']);body=request_leaf.read_bytes();assert p.hashlib.sha256(body).hexdigest()==old['sha256'];cached=True
            else:
                for attempt in range(3):
                    assert p.time.monotonic()<deadline
                    try:
                        receipt['network_attempts']+=1
                        req=p.urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://gu.qq.com/'})
                        with p.urllib.request.urlopen(req,timeout=15,context=p.ssl.create_default_context()) as response:body=response.read(512*1024+1)
                        assert len(body)<=512*1024 and receipt['new_network_bytes']+len(body)<=receipt['max_total_new_bytes']
                        data=p.json.loads(body);assert data.get('code')==0
                        temp=request_leaf.with_suffix('.json.tmp');temp.write_bytes(body);temp.replace(request_leaf);receipt['new_network_bytes']+=len(body);attempts.append(dict(attempt=attempt+1,status='actual_response_retained',bytes=len(body)));break
                    except Exception as e:
                        body=None;attempts.append(dict(attempt=attempt+1,status='failed',error_type=type(e).__name__,error=str(e),error_stack=p.traceback.format_exc()));save()
                        if attempt<2:p.time.sleep(1+attempt)
            record=dict(date=date,symbol=symbol,requested_slots=[r['slot'] for r in requests],url=url,reused_existing_response=cached,attempts=attempts,hours=[],source_native_leaf_sha256=sourcepin['sha256'],source_native_leaf_path=sourcepin['path'])
            if body is None:
                record['status']='stockday_request_failed_terminal';counts['failed_stockdays']+=1
            else:
                record.update(source_reference_path=str(request_leaf),source_reference_sha256=p.hashlib.sha256(body).hexdigest(),source_reference_identity=p.identity(request_leaf))
                data=p.json.loads(body);rows=data.get('data',{}).get(code,{}).get('m1',[])
                record['actual_response_rows']=len(rows)
                try:
                    quotes,auction=normalize_actual_rows(rows,date);record.update(status='actual240_minutes_compared',observed_auction=auction,actual_regular_minutes=240)
                    pp=p.BASE/'public_source_probes/baostock/union_daily_factors/normalized'/(symbol+'.json');primary=p.json.loads(pp.read_text());day=next(dict(zip(primary['fields'],r)) for r in primary['rows'] if r[0]==date)
                    actual_day=dict(open=quotes[570]['open'],close=quotes[899]['close'],high=max(q['high'] for q in quotes.values()),low=min(q['low'] for q in quotes.values()),volume=sum(q['volume'] for q in quotes.values()))
                    differences={f:actual_day[f]-int(D(day[f])*10000) for f in FIELDS[:4]};differences['volume']=actual_day['volume']-int(day['volume'])
                    record.update(primary_daily_path=str(pp),primary_daily_sha256=p.sha(pp),reference_day=actual_day,primary_day_differences=differences,all_five_day_fields_exact=not any(differences.values()))
                    native=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute')
                    for r in requests:
                        slot=r['slot'];start=[570,630,780,840][slot];grid=list(range(start,start+60));qb=[quotes[m] for m in grid]
                        qhour=dict(open=qb[0]['open'],close=qb[-1]['close'],high=max(q['high'] for q in qb),low=min(q['low'] for q in qb),volume=sum(q['volume'] for q in qb))
                        absent=[m for m in grid if m not in native.index];minute_differences=[];positive=0;nhour=None
                        if not absent:
                            nb=native.loc[grid];nhour=dict(open=int(nb.open.iloc[0]),close=int(nb.close.iloc[-1]),high=int(nb.high.max()),low=int(nb.low.min()),volume=int(nb.volume.sum()));assert all(nhour[f]==r['native_fixed_hour'][f] for f in FIELDS)
                        for minute in grid:
                            if minute not in native.index:continue
                            n=native.loc[minute];delta={f:int(n[f])-quotes[minute][f] for f in FIELDS};positive+=int(n.trades)>0 and int(n.volume)>0
                            if any(delta.values()):minute_differences.append(dict(left_minute=minute,differences=delta))
                        delta={f:qhour[f]-nhour[f] for f in FIELDS} if nhour else None
                        hour=dict(slot=slot,reference_hour=qhour,native_hour=nhour,missing_native_minutes=absent,native_positive_minutes=positive,minute_conflicts=minute_differences,all60_minute_fields_exact=not absent and not minute_differences,hour_differences=delta,all_five_hour_fields_exact=delta is not None and not any(delta.values()))
                        record['hours'].append(hour);counts['checked_hours']+=1;counts['hour_all_five_exact']+=hour['all_five_hour_fields_exact'];counts['all60_minute_fields_exact_hours']+=hour['all60_minute_fields_exact']
                    counts['actual_complete_stockdays']+=1;counts['actual_regular_reference_minutes']+=240
                except Exception as e:
                    record.update(status='source_grid_or_comparison_failed_terminal',error_type=type(e).__name__,error=str(e),error_stack=p.traceback.format_exc());counts['source_or_comparison_failed_stockdays']+=1
            receipt['records'].append(record);receipt['counts']=dict(counts);save()
            if len(receipt['records'])%20==0:print(p.json.dumps(dict(processed_stockdays=len(receipt['records']),counts=counts,new_bytes=receipt['new_network_bytes'],peak_rss_bytes=receipt['peak_rss_bytes'])),flush=True)
            p.time.sleep(.35)
        receipt.update(status='complete_all_remaining_scoped_actual_m1_reference_diagnostic_no_admission',finished_utc=p.utc())
    except Exception as e:receipt.update(status='failed_terminal',error_type=type(e).__name__,error=str(e),error_stack=p.traceback.format_exc())
    finally:save();lock.close()

if __name__=='__main__':main()
