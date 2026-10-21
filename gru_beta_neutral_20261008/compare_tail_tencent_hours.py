"""Fetch one small public 60-minute response per target stock, then stream bars.

Independent coarse corroboration, with both existing fixed minute clocks kept.
The response's extra turnover-rate field is never used as cash. Quantity times
100 remains a hypothesis; no source minute, trade or candidate is manufactured.
"""
import concurrent.futures
import threading
import traceback
from decimal import Decimal as D
from . import bounded_tail_pipeline as p

DEST = p.OUT / 'tencent_minute_references'
FIELDS = ['open','high','low','close']


def fetch(wanted):
    output=DEST/'all_target_stock_acquisition.json'
    directory=DEST/'hourly_responses'
    directory.mkdir(exist_ok=True)
    symbols=sorted({r['symbol'] for g in wanted.values() for r in g.values()})
    assert len(symbols)==624
    receipt=p.json.loads(output.read_text()) if output.exists() else dict(started_utc=p.utc(),
        source_code_sha256=p.sha(__file__),requested_stocks=624,maximum_workers=2,
        maximum_response_bytes=2**21,maximum_attempts_per_stock=2,records=[],status='acquiring',
        public_endpoint='https://ifzq.gtimg.cn/appstock/app/kline/mkline',
        candidate_hours_admitted=0,personal_credentials_used=False)
    completed={r['symbol'] for r in receipt['records'] if r['status']=='response_retained'}
    initial_path=DEST/'initial_probe_receipt.json'
    initial=p.json.loads(initial_path.read_text())
    cache={r['code']:r for r in initial['queries'] if r.get('rows')==320 and r['period']=='m60'}
    stop=threading.Event()

    def worker(symbol):
        code=symbol[-2:].lower()+symbol[:6]
        rec=dict(symbol=symbol,code=code,attempts=[],status='failed_preserved')
        if code in cache:
            old=cache[code]
            path=p.Path(old['path'])
            assert p.sha(path)==old['sha256']
            rec.update(status='response_retained',path=str(path),sha256=old['sha256'],
                       verified_identity=p.identity(path),rows=old['rows'],bytes=old['bytes'],
                       min_date=old['min_date'],max_date=old['max_date'],initial_probe_reused=True)
            return rec
        for i in range(2):
            if stop.is_set():
                break
            attempt=dict(started_utc=p.utc(),stage='public_https_request')
            rec['attempts'].append(attempt)
            try:
                url=f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m60,,320'
                request=p.urllib.request.Request(url,headers={'Referer':'https://gu.qq.com/','User-Agent':'QuantResearch-public-data-audit/2.0'})
                with p.urllib.request.urlopen(request,timeout=12,context=p.ssl.create_default_context()) as response:
                    assert response.status==200 and response.url.startswith('https://')
                    raw=response.read(2**21+1)
                    assert len(raw)<=2**21
                attempt['stage']='response_schema_check'
                obj=p.json.loads(raw)
                assert obj['code']==0
                bars=obj['data'][code]['m60']
                assert 0<len(bars)<=320
                assert all(len(row)>=6 and len(row[0])==12 and row[0].isdigit() for row in bars)
                assert [r[0] for r in bars]==sorted({r[0] for r in bars})
                assert all(r[0][-4:] in ['1030','1130','1400','1500'] for r in bars)
                path=directory/(code+'_m60_latest_320.json')
                temp=path.with_suffix('.json.tmp')
                temp.write_bytes(raw)
                temp.replace(path)
                rec.update(status='response_retained',path=str(path),sha256=p.hashlib.sha256(raw).hexdigest(),
                    verified_identity=p.identity(path),bytes=len(raw),rows=len(bars),
                    min_date=bars[0][0][:8],max_date=bars[-1][0][:8],url=url)
                attempt.update(stage='complete',finished_utc=p.utc())
                p.time.sleep(.75)
                return rec
            except Exception as error:
                attempt.update(error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc(),finished_utc=p.utc())
                if isinstance(error,p.urllib.error.HTTPError) and error.code in [401,403,429]:
                    rec['status']='access_or_rate_denial_preserved'
                    stop.set()
                    break
                p.time.sleep(1+i)
        return rec

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending=[s for s in symbols if s not in completed]
        # Submit only a small batch; denial stops further dispatch promptly.
        for start in range(0,len(pending),16):
            if stop.is_set():
                break
            futures=[pool.submit(worker,s) for s in pending[start:start+16]]
            for future in concurrent.futures.as_completed(futures):
                record=future.result()
                receipt['records']=[r for r in receipt['records'] if r['symbol']!=record['symbol']]+[record]
                receipt.update(updated_utc=p.utc(),stocks_complete=sum(r['status']=='response_retained' for r in receipt['records']),
                               peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
                p.atomic_json(output,receipt)
    receipt.update(status='complete' if sum(r['status']=='response_retained' for r in receipt['records'])==624 else 'incomplete_preserved',
                   finished_utc=p.utc())
    p.atomic_json(output,receipt)
    return receipt


def aggregate(path):
    bars={}
    for batch in p.pq.ParquetFile(path).iter_batches(batch_size=p.BATCH_ROWS,
            columns=['ticker','left_minute',*FIELDS,'volume','trades'],use_threads=False):
        frame=batch.to_pandas()
        frame['slot']=p.np.where(frame.left_minute<690,(frame.left_minute-570)//60,(frame.left_minute-780)//60+2)
        small=frame.groupby(['ticker','slot'],sort=False).agg(
            open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'),
            first_minute=('left_minute','first'),last_minute=('left_minute','last'),observed_minutes=('left_minute','size'))
        for key,row in small.iterrows():
            new={f:int(v) for f,v in row.items()}
            old=bars.get(key)
            if old is None:
                bars[key]=new
                continue
            if new['first_minute']<old['first_minute']:
                old.update(open=new['open'],first_minute=new['first_minute'])
            if new['last_minute']>old['last_minute']:
                old.update(close=new['close'],last_minute=new['last_minute'])
            old['high']=max(old['high'],new['high'])
            old['low']=min(old['low'],new['low'])
            old['volume']+=new['volume']
            old['observed_minutes']+=new['observed_minutes']
    return bars


def compare(wanted,acquisition):
    output=DEST/'all_tail_hour_comparison.json'
    minute=p.json.loads((p.OUT/'minute_observation_progress.json').read_text())
    record_by_symbol={r['symbol']:r for r in acquisition['records'] if r['status']=='response_retained'}
    refs={}
    for symbol,record in record_by_symbol.items():
        path=p.Path(record['path'])
        assert p.identity(path)==record['verified_identity']
        obj=p.json.loads(path.read_text())
        rows=obj['data'][record['code']]['m60']
        refs[symbol]={}
        for row in rows:
            date=f'{row[0][:4]}-{row[0][4:6]}-{row[0][6:8]}'
            slot=['1030','1130','1400','1500'].index(row[0][-4:])
            bar={f:int(D(row[i])*10000) for f,i in [('open',1),('close',2),('high',3),('low',4)]}
            volume=D(row[5])*100
            assert volume==volume.to_integral_value() and volume>=0
            bar.update(volume=int(volume),native_row=row)
            refs[symbol][date,slot]=bar
    receipt=p.json.loads(output.read_text()) if output.exists() else dict(started_utc=p.utc(),
        source_code_sha256=p.sha(__file__),source_acquisition_sha256=p.sha(DEST/'all_target_stock_acquisition.json'),
        requested_stockdays=21215,requested_hours=84860,groups=[],status='comparing',candidate_hours_admitted=0,
        coarse_rows_never_expanded_to_minutes=True,extra_turnover_rate_not_used_as_cash=True,
        volume_multiplier_100_is_diagnostic_hypothesis=True,all_existing_fixed_clocks_preserved=True)
    done={(g['date'],g['prefix']) for g in receipt['groups']}
    for group in minute['groups']:
        date,prefix=group['date'],group['prefix']
        if (date,prefix) in done:
            continue
        totals={}
        comparisons=[]
        for file in group['minute_files']:
            path=p.Path(file['path'])
            bars=aggregate(path)
            counts=dict(reference_hours=0,observed_trade_hours=0,all_60_trade_minutes_observed_hours=0,
                        all_four_prices_exact_hours=0,all_four_prices_and_volume_exact_hours=0,
                        all_four_prices_and_volume_within_100_hours=0)
            for ticker,target in wanted[date,prefix].items():
                for slot in range(4):
                    native=bars.get((ticker,slot))
                    ref=refs.get(target['symbol'],{}).get((date,slot))
                    if ref:
                        counts['reference_hours']+=1
                    if native:
                        counts['observed_trade_hours']+=1
                        counts['all_60_trade_minutes_observed_hours']+=native['observed_minutes']==60
                    row=dict(symbol=target['symbol'],date=date,slot=slot,model=file['model'],
                             source_trade_hour_present=native is not None,coarse_reference_present=ref is not None)
                    if native and ref:
                        differences={f:native[f]-ref[f] for f in [*FIELDS,'volume']}
                        price_exact=all(differences[f]==0 for f in FIELDS)
                        counts['all_four_prices_exact_hours']+=price_exact
                        counts['all_four_prices_and_volume_exact_hours']+=price_exact and differences['volume']==0
                        counts['all_four_prices_and_volume_within_100_hours']+=price_exact and abs(differences['volume'])<=100
                        row.update(differences=differences,observed_minutes=native['observed_minutes'],native_hour=native,
                                   reference_native_row=ref['native_row'],four_prices_exact=price_exact)
                    comparisons.append(row)
            totals[file['model']]=counts
        leaf=DEST/('hour_comparison_'+date+'_'+prefix+'.json')
        p.atomic_json(leaf,dict(date=date,prefix=prefix,comparisons=comparisons,candidate_hours_admitted=0))
        receipt['groups'].append(dict(date=date,prefix=prefix,stockdays=len(wanted[date,prefix]),totals=totals,
                                    comparison_path=str(leaf),comparison_sha256=p.sha(leaf)))
        receipt.update(updated_utc=p.utc(),processed_stockdays=sum(g['stockdays'] for g in receipt['groups']),
                       peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
        p.atomic_json(output,receipt)
    receipt['totals_by_model']={model:{field:sum(g['totals'][model][field] for g in receipt['groups'])
        for field in receipt['groups'][0]['totals'][model]} for model in ['closed_right','floor_left']}
    receipt.update(status='complete',finished_utc=p.utc())
    assert receipt['processed_stockdays']==21215
    p.atomic_json(output,receipt)


def main():
    lock=p.locked('tail_tencent_hours')
    DEST.mkdir(exist_ok=True)
    wanted=p.targets()
    acquisition=fetch(wanted)
    compare(wanted,acquisition)
    lock.close()


if __name__=='__main__':
    main()
