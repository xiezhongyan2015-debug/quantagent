"""Fetch only catalogued files for unresolved candidates; stream CSV filters.

Keep adjusted fields and factors verbatim. Arithmetic inversion is a diagnostic,
not a unique reconstruction or an admission certificate.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p


def main():
    process_lock = p.locked('historical_candidate_files')
    metadata = p.OUT / 'source_documentation/jobs-git__StockChina-Minute.json'
    info = p.json.loads(metadata.read_text())
    assert info['id']=='jobs-git/StockChina-Minute' and not info['gated'] and not info['private']
    published = {r['rfilename']:r for r in info['siblings']}
    ref = p.json.loads((p.OUT/'historical_gap_references/receipt.json').read_text())
    groups = p.defaultdict(list)
    unavailable=[]
    for r in ref['results']:
        filename = r['symbol'][:6]+('.XSHG.csv' if r['symbol'].endswith('SH') else '.XSHE.csv')
        if filename in published and r['status']=='no_complete_reference':
            groups[r['symbol']].append(r)
        elif r['status']=='no_complete_reference':
            unavailable.append(dict(symbol=r['symbol'],date=r['date']))
    destination=p.OUT/'historical_candidate_files'
    destination.mkdir(exist_ok=True)
    op=destination/'receipt.json'
    d=p.json.loads(op.read_text()) if op.exists() else dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),
        repository=info['id'],pinned_revision=info['sha'],metadata_sha256=p.sha(metadata),
        requested_available_stocks=len(groups),stockdays_not_in_published_catalogue=unavailable,
        adjusted_source_fields_are_not_assumed_raw=True,records=[],candidate_hours_admitted=0,status='acquiring_and_comparing')
    done={r['symbol'] for r in d['records'] if r['status']=='complete_comparison'}
    olddir=p.BASE/'public_source_probes/huggingface/joinquant_targeted_concordance_20261009'
    oldproof=olddir/'source_acquisition_receipt.json'
    accepted={p.Path(r['path']).name:r for r in p.json.loads(oldproof.read_text())['records']} if oldproof.exists() else {}
    for symbol,requests in sorted(groups.items()):
        if symbol in done:
            continue
        filename=symbol[:6]+'.XSHE.csv'
        item=published[filename]
        assert item['size']<=128*1024**2 and p.Path(filename).name==filename
        path=destination/filename
        cached=olddir/filename
        record=dict(symbol=symbol,filename=filename,published_bytes=item['size'],published_sha256=item['lfs']['sha256'],requests=[dict(date=r['date'],slots=r['slots']) for r in requests])
        try:
            if cached.exists() and filename in accepted and accepted[filename]['sha256']==item['lfs']['sha256'] and cached.stat().st_size==item['size']:
                path=cached
                record['certification']='previous_full_digest_receipt_reused_with_size_guard'
            elif path.exists():
                assert path.stat().st_size==item['size'] and p.sha(path)==item['lfs']['sha256']
                record['certification']='recovered_download_digest_verified_once'
            else:
                url='https://huggingface.co/datasets/'+info['id']+'/resolve/'+info['sha']+'/'+filename
                temp=path.with_suffix('.csv.part')
                digest=p.hashlib.sha256()
                count=0
                with p.urllib.request.urlopen(p.urllib.request.Request(url,headers={'User-Agent':'QuantResearch-public-data-audit/2.0'}),timeout=45,context=p.ssl.create_default_context()) as response,temp.open('wb') as f:
                    assert response.status==200 and response.url.startswith('https://')
                    while block:=response.read(1024**2):
                        count+=len(block)
                        assert count<=item['size']
                        f.write(block)
                        digest.update(block)
                assert count==item['size'] and digest.hexdigest()==item['lfs']['sha256']
                temp.replace(path)
                record['certification']='streamed_published_whole_SHA256_verified'
            days={r['date'] for r in requests}
            subset=destination/(symbol[:6]+'_requested_stockdays.csv')
            temporary=subset.with_suffix('.csv.tmp')
            first=True
            selected=0
            source_rows=0
            for chunk in p.pd.read_csv(path,chunksize=32768,dtype=str):
                date=chunk.iloc[:,0].str[:10]
                picked=chunk.loc[date.isin(days)]
                source_rows+=len(chunk)
                if len(picked):
                    picked.to_csv(temporary,index=False,mode='w' if first else 'a',header=first)
                    first=False
                    selected+=len(picked)
            if first:
                p.pd.read_csv(path,nrows=0).to_csv(temporary,index=False)
            temporary.replace(subset)
            frame=p.pd.read_csv(subset,dtype=str)
            date=frame.iloc[:,0].str[:10] if len(frame) else p.pd.Series(dtype=str)
            diagnostics=[]
            for request in requests:
                one=frame.loc[date.eq(request['date'])].copy()
                controls=dict(symbol=symbol,date=request['date'],requested_slots=request['slots'],reference_rows=len(one),
                    source_factor_inversion_is_not_certified=True,strict_candidate_hours_admitted=0)
                if len(one):
                    ts=p.pd.to_datetime(one.iloc[:,0])
                    minute=ts.dt.hour*60+ts.dt.minute
                    controls.update(complete_240_end_minute_grid=minute.tolist()==list(range(571,691))+list(range(781,901)),
                        paused_flags=sorted(one.paused.dropna().unique().tolist()),factor_values=sorted(one.factor.dropna().unique().tolist()))
                    candidate=native_for_request(request)
                    if len(one)==240 and controls['complete_240_end_minute_grid'] and len(controls['factor_values'])==1:
                        factor=D(controls['factor_values'][0])
                        exact={f:0 for f in ['open','high','low','close','volume','money']}
                        inversion={f:0 for f in ['open','high','low','close']}
                        for slot in request['slots']:
                            for j in range(slot*60,(slot+1)*60):
                                row=one.iloc[j]
                                for f in ['open','high','low','close']:
                                    exact[f]+=D(row[f])==candidate[j][f]
                                    inversion[f]+=factor>0 and D(row[f])/factor==candidate[j][f]
                                exact['volume']+=D(row['volume'])==candidate[j]['volume']
                                exact['money']+=D(row['money'])==candidate[j]['amount']
                        controls.update(requested_observed_minutes=60*len(request['slots']),direct_fields_exact_counts=exact,
                            divide_factor_price_exact_counts=inversion)
                diagnostics.append(controls)
            record.update(status='complete_comparison',source_path=str(path),verified_identity=p.identity(path),subset_path=str(subset),
                subset_sha256=p.sha(subset),source_rows_scanned=source_rows,selected_rows=selected,stockdays=diagnostics)
        except Exception as e:
            import traceback
            record.update(status='failed_preserved',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc())
        d['records'].append(record)
        d.update(updated_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
        p.atomic_json(op,d)
    d.update(status='complete_available_catalogue_comparisons' if all(r['status']=='complete_comparison' for r in d['records']) else 'incomplete_preserved',finished_utc=p.utc())
    p.atomic_json(op,d)
    process_lock.close()


def native_for_request(request):
    from .resume_gap_references import load
    load('public_baostock_probe')
    native=load('query_all_unchecked_minimum_coarse_references')
    path=p.Path(request['native_source_path'])
    assert p.sha(path)==request['native_source_sha256']
    return native.normalize(path,request['date'])


if __name__=='__main__':
    main()
