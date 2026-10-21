"""Fetch only the remaining376 public minute references, one prefix at a time.

Each shared native minute leaf is read/hash-certified once during comparison.
No trade archive is opened; no quotes or minutes are imputed or raw-admitted.
"""
from collections import Counter,defaultdict
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

DEST=p.OUT/'sina_remaining376_tail_references'


def main():
    lock=p.locked('sina_remaining376');DEST.mkdir(exist_ok=True);output=DEST/'receipt.json';assert not output.exists()
    prior_symbols=set();pins=[]
    for name in ['sina_tail_minute_expanded','sina_tail_nonmonotonic_references']:
        path=p.OUT/name/'receipt.json';receipt=p.json.loads(path.read_text());proof_path=path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
        assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(path)
        pins.append(dict(receipt_path=str(path),receipt_sha256=p.sha(path),proof_path=str(proof_path),proof_sha256=p.sha(proof_path)))
        prior_symbols|={r['symbol'] for r in receipt['records']}
    targets=sorted({t['symbol'] for (date,prefix),group in p.targets().items() if date=='2026-09-23' for t in group.values()})
    wanted=sorted(set(targets)-prior_symbols);assert len(targets)==624 and len(prior_symbols)==248 and len(wanted)==376
    selected=defaultdict(list)
    for symbol in wanted:selected[symbol[:2]].append(symbol)
    progress_path=p.OUT/'minute_observation_progress.json';progress=p.json.loads(progress_path.read_text());assert progress['status']=='observations_complete_pending_independent_concordance'
    groups={(g['date'],g['prefix']):g for g in progress['groups']}
    result=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),primary_interface_source='https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py',
        prior_query_pins=pins,native_progress_sha256=p.sha(progress_path),selection='All376 unqueried symbols in the fixed624 Sep23 missing-target universe, sorted by prefix/symbol; same end-label-minus1 interval mapping; directly reported shares.',
        symbols_requested=len(wanted),status='querying_remaining_public_references',records=[],native_source_leaf_pins=[],candidate_hours_admitted=0,
        credentials_used=False,missing_minutes_imputed=False,volume_units_changed=False,clock_rule_changed=False,nighttime_popups=False)
    counts=Counter();p.atomic_json(output,result);stopped=False
    for prefix,symbols in sorted(selected.items()):
        frames={};sources={}
        for date in ['2026-09-22','2026-09-23']:
            group=groups[date,prefix];leaf=next(x for x in group['minute_files'] if x['model']=='floor_left');source=p.Path(leaf['path']);before=p.identity(source)
            data=source.read_bytes();assert p.hashlib.sha256(data).hexdigest()==leaf['sha256']
            columns=['ticker','left_minute','open','high','low','close','volume','amount_x10000','trades']
            frame=p.pq.ParquetFile(p.pa.BufferReader(data)).read(columns=columns).to_pandas();assert len(frame)==leaf['observed_minutes']
            assert p.identity(source)==before;del data
            frames[date]=frame;sources[date]=dict(path=str(source),sha256=leaf['sha256'],verified_identity=before,rows=len(frame))
            result['native_source_leaf_pins'].append(sources[date]);p.atomic_json(output,result)
        for symbol in symbols:
            code=('sh' if symbol.endswith('.SH') else 'sz')+symbol[:6]
            url=f'https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol={code}&scale=1&ma=no&datalen=1970'
            leaf=DEST/(code+'_m1_1970_raw.txt');record=dict(symbol=symbol,url=url)
            try:
                request=p.urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
                with p.urllib.request.urlopen(request,timeout=25,context=p.ssl.create_default_context()) as response:
                    data=response.read(2*1024**2+1);assert len(data)<=2*1024**2;status=response.status
                leaf.write_bytes(data);record.update(http_status=status,path=str(leaf),sha256=p.hashlib.sha256(data).hexdigest(),verified_identity=p.identity(leaf),bytes=len(data))
                text=data.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);assert isinstance(rows,list)
                independent={}
                for q in rows:
                    stamp=p.datetime.fromisoformat(q['day']);assert stamp.second==0
                    key=stamp.date().isoformat(),stamp.hour*60+stamp.minute-1;assert key not in independent;independent[key]=q
                record.update(rows=len(rows),first_observed=rows[0]['day'] if rows else None,last_observed=rows[-1]['day'] if rows else None,comparisons=[])
                for date,frame in frames.items():
                    native=frame.loc[frame.ticker.eq(symbol[:6])].set_index('left_minute');assert not native.index.duplicated().any()
                    paired={};exact_counts=Counter();all5=all6=0
                    for minute,r in native.iterrows():
                        q=independent.get((date,int(minute)))
                        if q is None:continue
                        exact={f:D(int(r[f]))==D(q[f])*10000 for f in ['open','high','low','close']};exact['volume']=D(int(r.volume))==D(q['volume'])
                        cash=D(int(r.amount_x10000))/10000==D(q['amount']);good=all(exact.values());paired[int(minute)]=good;all5+=good;all6+=good and cash
                        for f,value in exact.items():exact_counts[f]+=value
                    grid=list(range(570,690))+list(range(780,900));hours=[]
                    for slot in range(4):
                        minutes=grid[slot*60:(slot+1)*60];present=sum(m in paired for m in minutes);good=present==60 and all(paired[m] for m in minutes)
                        hours.append(dict(slot=slot,paired_real_minutes=present,all60_price_and_volume_exact=good));counts['all60_price_volume_exact_hours']+=good
                    record['comparisons'].append(dict(date=date,native_observed_minutes=len(native),independent_observed_minutes=sum(d==date for d,m in independent),paired_real_minutes=len(paired),minute_all5_exact=all5,minute_all6_exact=all6,field_exact_counts=dict(exact_counts),hours=hours,native_source_path=sources[date]['path'],native_source_sha256=sources[date]['sha256']))
                record['status']='compared_without_admission'
            except p.urllib.error.HTTPError as exc:
                record.update(status='http_error',http_status=exc.code,error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc());stopped=exc.code in [401,403,429]
            except Exception as exc:
                record.update(status='failed',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc())
            result['records'].append(record);result.update(counts=dict(counts),updated_utc=p.utc(),symbols_processed=len(result['records']),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
            p.atomic_json(output,result)
            if stopped:break
            p.time.sleep(.75)
        for source in sources.values():assert p.identity(p.Path(source['path']))==source['verified_identity']
        del frames
        if stopped:break
    result.update(status='bounded_queries_stopped_on_public_permission_or_rate_response' if stopped else 'expanded_references_complete_no_admission',finished_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(output,result);lock.close()


if __name__=='__main__':main()
