"""Resumable expanded Sina minute reference comparison, one stock at a time."""
from decimal import Decimal as D
from collections import Counter
from . import bounded_tail_pipeline as p

DEST=p.OUT/'sina_tail_minute_expanded'
PRIMARY_URL='https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_zh_a_sina.py'


def main():
    lock=p.locked('sina_tail_expanded');DEST.mkdir(exist_ok=True)
    out=DEST/'receipt.json'
    coarse_path=p.OUT/'tencent_minute_references/all_tail_hour_comparison.json'
    coarse=p.json.loads(coarse_path.read_text());minute_progress=p.json.loads((p.OUT/'minute_observation_progress.json').read_text())
    minute_groups={(r['date'],r['prefix']):r for r in minute_progress['groups']}
    selected=[]
    for group in coarse['groups']:
        if group['date']!='2026-09-22':continue
        raw=p.Path(group['comparison_path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==group['comparison_sha256']
        mg=minute_groups[group['date'],group['prefix']]
        validation_raw=p.Path(mg['validation_path']).read_bytes();assert p.hashlib.sha256(validation_raw).hexdigest()==mg['validation_sha256']
        validation={r['symbol']:r for r in p.json.loads(validation_raw)['stockdays']}
        candidates={r['symbol'] for r in p.json.loads(raw)['comparisons'] if r['model']=='floor_left' and r['slot']<3 and r.get('observed_minutes')==60 and r.get('differences') and all(v==0 for v in r['differences'].values())}
        qualified=sorted(s for s in candidates if validation[s]['unique_transaction_order_certified'] and validation[s]['source_inventory_exact'] and validation[s]['raw_positive'] and validation[s]['clocks']['floor_left']['daily_six_fields_exact'])
        for symbol in qualified:selected.append(dict(symbol=symbol,source_group=mg))
    assert len(selected)==134
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),primary_interface_source=PRIMARY_URL,selection='all sorted qualifying symbols per prefix; Sep22 all60 true-trade minutes, daily6 exact, globalID strictly increasing and Tencenthour5 exact',symbols_requested=len(selected),records=[],candidate_hours_admitted=0,credentials_used=False,minute_missing_values_filled=False,status='pilot_running')
    if out.exists():
        receipt=p.json.loads(out.read_text());assert receipt['source_code_sha256']==p.sha(__file__)
    pilot_path=p.OUT/'sina_tail_minute_pilot/receipt.json'
    pilot=p.json.loads(pilot_path.read_text())
    receipt['initial_pilot_receipt_sha256']=p.sha(pilot_path)
    reusable={r['symbol']:r for r in pilot['records'] if r['status']=='compared_without_admission'}
    done={r['symbol'] for r in receipt['records']}
    counts=Counter(receipt.get('counts',{}))
    for request in selected:
        symbol=request['symbol']
        if symbol in done:continue
        if symbol in reusable:
            old=reusable[symbol];assert p.identity(p.Path(old['path']))==old['verified_identity']
            receipt['records'].append({**old,'reused_pilot_observation':True})
            counts['all60_price_volume_exact_hours']+=sum(h['all60_price_and_volume_exact'] for c in old['comparisons'] for h in c['hours'])
            receipt.update(counts=dict(counts),updated_utc=p.utc());p.atomic_json(out,receipt);continue
        code=('sh' if symbol.endswith('.SH') else 'sz')+symbol[:6]
        url=f'https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol={code}&scale=1&ma=no&datalen=1970'
        path=DEST/(code+'_m1_1970_raw.txt')
        record=dict(symbol=symbol,url=url)
        try:
            query=p.urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
            with p.urllib.request.urlopen(query,timeout=25,context=p.ssl.create_default_context()) as response:
                data=response.read(2*1024**2+1);assert len(data)<=2*1024**2
                status=response.status
            path.write_bytes(data);record.update(http_status=status,path=str(path),sha256=p.hashlib.sha256(data).hexdigest(),verified_identity=p.identity(path),bytes=len(data))
            text=data.decode('utf8');rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):]);assert isinstance(rows,list)
            observed={}
            for r in rows:
                stamp=p.datetime.fromisoformat(r['day']);assert stamp.second==0
                key=stamp.date().isoformat(),stamp.hour*60+stamp.minute
                assert key not in observed;observed[key]=r
            record.update(rows=len(rows),first_observed=rows[0]['day'] if rows else None,last_observed=rows[-1]['day'] if rows else None,comparisons=[])
            for date in ['2026-09-22','2026-09-23']:
                mg=minute_groups.get((date,symbol[:2]));assert mg
                leaf=next(f for f in mg['minute_files'] if f['model']=='floor_left')
                raw=p.Path(leaf['path']).read_bytes();assert p.hashlib.sha256(raw).hexdigest()==leaf['sha256']
                native=p.pq.ParquetFile(p.pa.BufferReader(raw)).read().to_pandas();native=native.loc[native.ticker.eq(symbol[:6])].set_index('left_minute')
                checks=[]
                for minute,r in native.iterrows():
                    q=observed.get((date,int(minute)+1))
                    if q is None:continue
                    exact={f:int(r[f])==int(D(q[f])*10000) for f in ['open','high','low','close']}
                    exact['volume']=D(int(r['volume']))==D(q['volume'])
                    cash=D(int(r['amount_x10000']))/10000==D(q['amount'])
                    checks.append(dict(left_minute=int(minute),exact=exact,calculated_trade_cash_equals_source_amount=cash))
                byminute={r['left_minute']:r for r in checks};grid=list(range(570,690))+list(range(780,900))
                hours=[]
                for slot in range(4):
                    expected=grid[slot*60:(slot+1)*60];present=[m for m in expected if m in byminute]
                    all_exact=len(present)==60 and all(all(byminute[m]['exact'].values()) for m in present)
                    hours.append(dict(slot=slot,paired_real_minutes=len(present),all60_price_and_volume_exact=all_exact))
                    counts['all60_price_volume_exact_hours']+=all_exact
                record['comparisons'].append(dict(date=date,native_observed_minutes=len(native),independent_observed_minutes=sum(d==date for d,m in observed),paired_real_minutes=len(checks),minute_all5_exact=sum(all(r['exact'].values()) for r in checks),minute_all6_exact=sum(all(r['exact'].values()) and r['calculated_trade_cash_equals_source_amount'] for r in checks),field_exact_counts={f:sum(r['exact'][f] for r in checks) for f in ['open','high','low','close','volume']},hours=hours,native_source_path=leaf['path'],native_source_sha256=leaf['sha256']))
                del native,raw
            record['status']='compared_without_admission'
        except p.urllib.error.HTTPError as exc:
            record.update(status='http_error',http_status=exc.code,error_type=type(exc).__name__)
            receipt['records'].append(record);p.atomic_json(out,receipt)
            if exc.code in [401,403,429]:break
            continue
        except Exception as exc:
            record.update(status='failed',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc())
        receipt['records'].append(record);receipt.update(updated_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss);p.atomic_json(out,receipt)
        p.time.sleep(.75)
    receipt.update(status='expanded_references_complete_no_admission',finished_utc=p.utc(),counts=dict(counts),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
    p.atomic_json(out,receipt);lock.close()


if __name__=='__main__':main()
