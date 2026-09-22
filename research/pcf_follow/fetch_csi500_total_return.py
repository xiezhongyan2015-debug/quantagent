"""Official CSI500 total-return benchmark; never substitute a price index."""
import datetime as dt
import hashlib
import json
import pandas as pd
import requests
from pcf_research import ROOT,OUT

def main():
    url='https://www.csindex.com.cn/csindex-home/perf/index-perf'
    params={'indexCode':'H00905','startDate':'20230301','endDate':'20260917'}
    response=requests.get(url,params=params,timeout=45)
    response.raise_for_status()
    data=pd.DataFrame(response.json()['data'])
    assert data.indexCode.eq('H00905').all() and not data.tradeDate.duplicated().any()
    data['date']=pd.to_datetime(data.tradeDate,format='%Y%m%d')
    calendar=pd.read_parquet(ROOT/'data_expansion/normalized/benchmark_index_daily.parquet')
    calendar=calendar.loc[(calendar.symbol=='000905.SH')&(calendar.date>='2023-03-01')&(calendar.date<='2026-09-17'),'date']
    assert set(calendar)==set(data.date), 'Official TRI trading dates do not match local calendar'
    assert data.close.notna().all() and (data.close>0).all()
    path=OUT/'H00905_csindex_raw.json';path.write_text(response.text,encoding='utf-8')
    data.to_parquet(OUT/'H00905_daily.parquet',index=False)
    audit={'source':url,'parameters':params,'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(data),'calendar_match':True,
        'has_open':bool(data.open.notna().any()),'return_type':'gross total return with dividends reinvested'}
    (OUT/'H00905_provenance.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2))

if __name__=='__main__':main()
