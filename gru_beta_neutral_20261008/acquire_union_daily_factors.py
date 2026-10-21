"""Resumable public raw daily/status and corporate-action acquisition.

Uses inspected anonymous vendor protocol, one connection at a time. Old caches
are preserved and fingerprinted; no minute price or suspension is synthesized.
"""
import argparse,fcntl,hashlib,json,time
from datetime import datetime,timezone
from pathlib import Path
from public_baostock_probe import PublicClient,ROOT

OUT=ROOT/'outputs/gru_beta_neutral_20261008/public_source_probes/baostock/union_daily_factors'
FIELDS=['date','code','open','high','low','close','preclose','volume','amount','adjustflag','turn','tradestatus','pctChg','isST']


def rows(result):
    data=result.get('data',[])
    return data.get('record',[]) if isinstance(data,dict) else data


def verified_cache(path,code):
    if not path.exists():return None
    obj=json.loads(path.read_text())
    if obj.get('error_code')!='0' or obj.get('fields')!=FIELDS:return None
    p=obj.get('params',{})
    if p.get('code')!=code or p.get('start_date')!='2017-01-01' or p.get('end_date')!='2023-12-31' or p.get('adjustflag')!='3':return None
    if any(r[1]!=code or r[9]!='3' for r in obj['rows']):raise ValueError('Cached raw code/adjustment mismatch')
    return obj


def request_pages(client,code,start,end,kind):
    result_rows=[];evidence=[];fields=None
    for page in range(1,5):
        r=client.factors(code,start,end,page) if kind=='factors' else client.query(code,start,end,'d',page)
        if r['error_code']!='0':raise ValueError(f"{code} {kind}: {r['error_code']} {r['error_msg']}")
        if r['code']!=code:raise ValueError('Response identity mismatch')
        if fields is None:fields=r['fields']
        if r['fields']!=fields:raise ValueError('Response schema changed between pages')
        records=rows(r);result_rows.extend(records)
        evidence.append({k:v for k,v in r.items() if k!='data'})
        time.sleep(.35)
        if len(records)<int(r['per_page']):return result_rows,fields,evidence
    raise ValueError('Public response exceeded four-page bound')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'normalized').mkdir(exist_ok=True)
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    if args.limit:symbols=symbols[:args.limit]
    lock=(ROOT/'acquired_data/data_round2/baostock_session.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    client=None;errors=[];counts={'daily_rows':0,'factor_rows':0,'reused_2017_2023_files':0,'symbols_complete':0}
    started=datetime.now(timezone.utc).isoformat()
    run_dir=OUT/'responses'/datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S%fZ')
    connection_number=0;transient_retries=[]
    def connect():
        nonlocal connection_number
        connection_number+=1
        new=PublicClient(run_dir/f'connection_{connection_number:03d}')
        new.socket.settimeout(45)
        try:new.login()
        except Exception:
            new.socket.close()
            raise
        return new
    try:
        for symbol in symbols:
            code=symbol[-2:].lower()+'.'+symbol[:6];destination=OUT/'normalized'/(symbol+'.json')
            if destination.exists():
                obj=json.loads(destination.read_text());assert obj['symbol']==symbol and obj['end']=='2026-09-23'
            else:
                cached_path=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'/('raw_DEV_'+code.replace('.','')+'.json')
                cached=verified_cache(cached_path,code)
                start='2024-01-01' if cached is not None else '2017-01-01'
                for attempt in range(3):
                    try:
                        if client is None:client=connect()
                        newer,fields,evidence=request_pages(client,code,start,'2026-09-23','daily')
                        assert fields==FIELDS
                        daily=(cached['rows'] if cached is not None else [])+newer
                        if len({r[0] for r in daily})!=len(daily):raise ValueError('Duplicate raw daily date')
                        if any(r[1]!=code or r[9]!='3' for r in daily):raise ValueError('Unexpected raw daily code/factor flag')
                        factors,factor_fields,factor_evidence=request_pages(client,code,'1990-01-01','2026-09-23','factors')
                        break
                    except (TimeoutError,ConnectionError,OSError) as e:
                        # A timeout can leave a late reply on the stream. Discard
                        # that connection; never send a new query or logout on it.
                        if client is not None:client.socket.close()
                        client=None
                        transient_retries.append({'symbol':symbol,'attempt':attempt+1,'error':f'{type(e).__name__}: {e}',
                                                  'utc':datetime.now(timezone.utc).isoformat()})
                        if attempt==2:raise
                        time.sleep(2*(attempt+1))
                assert factor_fields==['code','dividOperateDate','foreAdjustFactor','backAdjustFactor','adjustFactor']
                if any(r[0]!=code for r in factors):raise ValueError('Unexpected factor code')
                obj={'symbol':symbol,'code':code,'start':'2017-01-01','end':'2026-09-23','fields':fields,'rows':daily,
                     'factor_fields':factor_fields,'factors':factors,'daily_response_evidence':evidence,'factor_response_evidence':factor_evidence,
                     'cached_2017_2023_source':str(cached_path) if cached is not None else None,
                     'cached_2017_2023_sha256':hashlib.sha256(cached_path.read_bytes()).hexdigest() if cached is not None else None,
                     'provider':'Baostock public anonymous API; vendor data, not exchange-certified',
                     'finished_utc':datetime.now(timezone.utc).isoformat(),'production_admitted':False}
                temp=destination.with_suffix('.json.tmp');temp.write_text(json.dumps(obj,ensure_ascii=False));temp.replace(destination)
            counts['symbols_complete']+=1;counts['daily_rows']+=len(obj['rows']);counts['factor_rows']+=len(obj['factors'])
            counts['reused_2017_2023_files']+=int(obj['cached_2017_2023_source'] is not None)
            progress={'status':'acquiring','started_utc':started,'checked_utc':datetime.now(timezone.utc).isoformat(),
                      'symbols_requested':len(symbols),'last_symbol':symbol,**counts,'errors':errors,
                      'transient_retries':transient_retries,'production_admitted':False}
            (OUT/'acquisition_progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2))
            if counts['symbols_complete']%25==0:print(json.dumps(progress,ensure_ascii=False),flush=True)
        progress.update(status='source_acquisition_complete_pending_audit',finished_utc=datetime.now(timezone.utc).isoformat())
    except Exception as e:
        progress={'status':'acquisition_interrupted_resumable','started_utc':started,'checked_utc':datetime.now(timezone.utc).isoformat(),
                  'symbols_requested':len(symbols),**counts,'errors':[f'{type(e).__name__}: {e}'],
                  'transient_retries':transient_retries,'production_admitted':False}
    finally:
        try:
            if client is not None:client.close()
        except Exception as e:progress['logout_error']=str(e)
        (OUT/'acquisition_progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2))
        print(json.dumps(progress,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
