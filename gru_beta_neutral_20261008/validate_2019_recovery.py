"""Validate boundary inference against intact originals hidden from the method.

The first open and low are masked only in memory. Source parquet files and the
frozen dataset are unchanged. All admitted OHLC must equal the withheld truth.
"""
import hashlib
import json
from pathlib import Path
from reconstruct_2019_boundaries import OUT, ROOT, pq, pd, np, recover_boundary


def main():
    legacy=pq.read_table(OUT/'legacy_2019_selected_bars.parquet').to_pandas()
    groups={(d,s):g.set_index('legacy_time') for (d,s),g in legacy.groupby(['date','symbol'])}
    daily_cache={};sources={};results=[]
    for path in sorted((OUT/'minute_original_selected').glob('*/*.parquet')):
        day,symbol=path.parent.name,path.stem
        # Defective originals are never a validation oracle.
        x=pq.read_table(path).to_pandas()
        if len(x)!=240 or not (x[['open','high','low','close']].to_numpy()>0).all() or x.volume.iloc[:60].sum()<=0:
            continue
        key=(day,symbol);result={'date':day,'symbol':symbol,'status':'not_admitted'}
        if symbol not in daily_cache:
            src=ROOT/'acquired_data/data_round2/baostock_raw500_DEV'/f'raw_DEV_{symbol[-2:].lower()+symbol[:6]}.json'
            if src.exists():
                obj=json.loads(src.read_text())
                assert obj['error_code']=='0' and obj['params']['adjustflag']=='3'
                assert obj['params']['code']==symbol[-2:].lower()+'.'+symbol[:6]
                daily_cache[symbol]={row[0]:dict(zip(obj['fields'],row)) for row in obj['rows']}
                sources[symbol]={'path':str(src),'sha256':hashlib.sha256(src.read_bytes()).hexdigest()}
            else:daily_cache[symbol]={}
        dr=daily_cache[symbol].get(day)
        if dr is None or key not in groups:
            result['reason']='independent_control_sources_missing';results.append(result);continue
        truth=np.array([x.open.iloc[0],x.high.iloc[:60].max(),x.low.iloc[:60].min(),x.close.iloc[59]])
        masked=x.copy();masked.loc[masked.index[0],['open','low']]=0.
        recovered=recover_boundary(masked,dr,groups[key]);result.update(recovered)
        result['withheld_open'],result['withheld_high'],result['withheld_low'],result['withheld_close']=truth
        if recovered['status']=='verified_hour_boundary_recovery':
            inferred=np.array([recovered['raw_open'],recovered['raw_high'],recovered['raw_low'],recovered['raw_close']])
            result['max_absolute_error']=float(np.abs(truth-inferred).max())
            result['all_ohlc_match']=bool(np.allclose(truth,inferred,rtol=0,atol=1e-8))
        result['original_payload_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        results.append(result)
    frame=pd.DataFrame(results);frame.to_csv(OUT/'2019_boundary_withheld_validation.csv',index=False)
    admitted=frame[frame.status.eq('verified_hour_boundary_recovery')]
    mismatches=admitted[~admitted.all_ohlc_match.astype(bool)]
    receipt={'control_stockdays':len(frame),'admitted_controls':len(admitted),
             'ohlc_mismatches':len(mismatches),'maximum_admitted_absolute_error':float(admitted.max_absolute_error.max()) if len(admitted) else None,
             'rejected_reasons':frame.loc[~frame.status.eq('verified_hour_boundary_recovery'),'reason'].value_counts().to_dict(),
             'validation_passed':len(admitted)>=50 and len(mismatches)==0,
             'method':'Withhold intact first-minute open and low in memory; infer hourly boundaries using only independent daily and legacy bars plus the remaining minute observations; compare all four inferred hourly prices to original source truth.',
             'scope_limit':'A successful control supports the adjustment/grid model; it does not provide independent original minute lows for corrupt records.',
             'source_files_modified':False,'daily_sources':sources,
             'recovery_source_sha256':hashlib.sha256((Path(__file__).parent/'reconstruct_2019_boundaries.py').read_bytes()).hexdigest()}
    (OUT/'2019_boundary_withheld_validation_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k!='daily_sources'},ensure_ascii=False))
    if len(mismatches):print(mismatches[['date','symbol','raw_open','withheld_open','raw_low','withheld_low','max_absolute_error']].head(15).to_string(index=False))


if __name__=='__main__':main()
