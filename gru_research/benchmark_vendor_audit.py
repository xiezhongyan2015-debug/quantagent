"""CSI500 PRICE vendor cross-check against saved actual official CSI quotes.

Collection is sequential and separate from the stock collector. This module
never fits models or computes strategy/ETF/total-return performance.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pandas as pd
from acquire_trading_evidence import key,atomic_json

OUT=ROOT/'gru_research/trading_data/vendor_benchmark_2025_2026'
FIELDS='date,code,open,high,low,close,preclose,volume,amount,pctChg'
PROBE={'api':'query_history_k_data_plus','params':{'code':'sh.000905','fields':FIELDS,
    'start_date':'2024-12-31','end_date':'2025-01-03','frequency':'d','adjustflag':'3'}}
FULL={'api':'query_history_k_data_plus','params':{'code':'sh.000905','fields':FIELDS,
    'start_date':'2024-12-31','end_date':'2026-09-23','frequency':'d','adjustflag':'3'}}


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    atomic_json(OUT/'probe_specs.json',[PROBE])
    atomic_json(OUT/'full_specs.json',[PROBE,FULL])


def require_probe():
    path=OUT/(key(PROBE)+'.json');q=json.loads(path.read_text())
    if q['error_code']!='0' or not q['rows'] or q['fields']!=FIELDS.split(','):
        raise ValueError('successful real PRICE index probe with exact schema required')
    if any(r[q['fields'].index('code')]!='sh.000905' for r in q['rows']):
        raise ValueError('PRICE index identity mismatch')
    return q


def audit(official_csv):
    require_probe()
    path=OUT/(key(FULL)+'.json');data=path.read_bytes();q=json.loads(data)
    if q['error_code']!='0' or not q['rows'] or q['fields']!=FIELDS.split(','):
        raise ValueError('successful real full PRICE index response required')
    source=pd.DataFrame(q['rows'],columns=q['fields'])
    source.to_csv(OUT/'source_strings.csv',index=False)
    d=source.copy();d['date']=pd.to_datetime(d.date)
    if d.duplicated('date').any() or not d.code.eq('sh.000905').all():
        raise ValueError('unresolved PRICE index date/identity conflict')
    for field in FIELDS.split(',')[2:]:d[field]=pd.to_numeric(d[field],errors='coerce')
    if d[['open','high','low','close','preclose']].isna().any().any():
        raise ValueError('missing actual PRICE index quote cannot be repaired')
    if not d[['open','high','low','close','preclose']].gt(0).all().all() or not (
            d.high.ge(d[['open','close']].max(axis=1)) & d.low.le(d[['open','close']].min(axis=1))
            & d.high.ge(d.low)).all():
        raise ValueError('invalid actual PRICE index OHLC cannot be repaired')
    official_csv=Path(official_csv);official=pd.read_csv(official_csv)
    if 'date' not in official:raise ValueError('official CSI schema date required')
    official['date']=pd.to_datetime(official.date)
    # Caller/root must provide explicit official OHLC mapping if schema changes.
    fields=[c for c in ['open','high','low','close'] if c in official]
    if len(fields)!=4:raise ValueError('official CSI raw OHLC mapping changed')
    joined=d.merge(official,on='date',suffixes=('_vendor','_official'),validate='one_to_one',how='outer',indicator=True)
    maxdifferences={f:float((joined[f+'_vendor']-joined[f+'_official']).abs().max()) for f in fields}
    checks=joined[['date','_merge']].copy()
    for f in fields:checks[f+'_vendor_minus_official']=joined[f+'_vendor']-joined[f+'_official']
    checks.to_csv(OUT/'vendor_official_cross_checks.csv',index=False)
    report={'code':'000905','index_variant':'CSI500_PRICE_INDEX_NOT_ETF_OR_TOTAL_RETURN',
        'vendor_response_sha256':hashlib.sha256(data).hexdigest(),
        'official_csv_sha256':hashlib.sha256(official_csv.read_bytes()).hexdigest(),
        'vendor_rows':len(d),'official_rows':len(official),'joined_states':joined['_merge'].value_counts().to_dict(),
        'max_absolute_OHLC_difference':maxdifferences,'raw_daily_first':str(d.date.min()),
        'independent_price_precision_check_passed':bool(joined['_merge'].eq('both').all()
            and all(v <= .00500001 for v in maxdifferences.values())),
        'raw_daily_last':str(d.date.max()),'source_class':'ANONYMOUS_TCP_VENDOR_NOT_EXCHANGE_CERTIFIED',
        'canonical_official_bytes_unchanged':True,'strategy_performance_computed':False,
        'fetched_utc':datetime.now(timezone.utc).isoformat()}
    atomic_json(OUT/'cross_source_audit.json',report)
    d.to_parquet(OUT/'raw_csi500_price.parquet',index=False)
    return report


if __name__=='__main__':
    if len(sys.argv)==2 and sys.argv[1]=='prepare':prepare()
    elif len(sys.argv)==2 and sys.argv[1]=='require_probe':require_probe()
    elif len(sys.argv)==3 and sys.argv[1]=='audit':print(json.dumps(audit(sys.argv[2]),indent=2))
    else:raise SystemExit('prepare | require_probe | audit <official_csv>')
