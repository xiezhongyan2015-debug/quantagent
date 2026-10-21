"""Read archived PCFs; preserve cash substitutions and source provenance."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/pcf_overlay_20261012'
MATERIAL = ROOT / 'outputs/cluster_sync_20261011/teammate_latest_uploads/unpacked/pcf_alpha_materials_20261011'
BS = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/baostock/union_daily_factors/normalized'
END = '2026-09-23'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))

def symbol(code):
    code = str(code).zfill(6)
    if len(code) != 6 or not code.isdigit(): raise ValueError('Invalid security code')
    return code + ('.SH' if code.startswith(('5', '6')) else '.SZ')

def parse_xml(path):
    root = ET.fromstring(Path(path).read_bytes())
    meta = {x.tag: x.text for x in root if x.tag != 'ComponentList'}
    day = pd.Timestamp(meta['TradingDay']).strftime('%Y-%m-%d')
    rows = []
    for x in root.findall('./ComponentList/Component'):
        r = {a.tag:a.text for a in x}
        rows.append(dict(date=day, code=r['InstrumentID'].zfill(6),
                         quantity=float(r['Quantity']), substitution=str(r['SubstitutionFlag']),
                         substitution_cash=float(r.get('SubstitutionCashAmount', 0)),
                         name=r.get('InstrumentName'), source='official_xml', source_path=str(path)))
    return rows, dict(date=day, valuation_date=pd.Timestamp(meta['PreTradingDay']).strftime('%Y-%m-%d'),
                     nav=float(meta['NAV']), nav_per_cu=float(meta['NAVperCU']),
                     creation_unit=float(meta['CreationRedemptionUnit']),
                     estimated_cash=float(meta['EstimatedCashComponent']), source='official_xml',
                     historical_publication_verified=False, source_sha256=sha(path))

def parse_all():
    rows=[]; metas=[]; fingerprints=[]
    for path in sorted((MATERIAL/'pcf/historical_sina_mirror/raw').glob('*.json.gz')):
        with gzip.open(path, 'rt') as f: j=json.load(f)
        day=j['date']; m=j['baseinfo']['map']
        for r in j['stocklist']:
            rows.append(dict(date=day, code=str(r['C_STOCKCODE']).zfill(6), quantity=float(r['L_NUMBER']),
                             substitution=str(r['C_TDBZ']), substitution_cash=float(r.get('F_TDJE') or 0),
                             name=r.get('C_STOCKSHORT'), source='archived_mirror',source_path=str(path)))
        metas.append(dict(date=day,valuation_date=str(m['PRETRADINGDAY'])[:10], nav=float(m['NAV']),
                          nav_per_cu=float(m['NAVPERCU']), creation_unit=float(m['CREATIONREDEMPTIONUNIT']),
                          estimated_cash=float(m['ESTIMATECASHCOMPONENT']),source='archived_mirror',
                          historical_publication_verified=False, source_sha256=sha(path)))
        fingerprints.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
    official=list((MATERIAL/'pcf/official_xml_recent').glob('*.xml'))
    official += list((MATERIAL/'pcf/official_original_20260918').glob('*.pcf'))
    for path in sorted(official):
        a,b=parse_xml(path);rows.extend(a);metas.append(b)
        fingerprints.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
    components=pd.DataFrame(rows).sort_values(['date','code']);meta=pd.DataFrame(metas).sort_values('date')
    assert not components.duplicated(['date','code']).any()
    assert not meta.duplicated('date').any()
    assert (meta.valuation_date<meta.date).all()
    # 2/4 are mandatory cash substitution. Positive quantities on these rows
    # remain in the audit, never become a stock purchase.
    components['stock_replicable']=(components.quantity>0)&~components.substitution.isin(['2','4'])
    OUT.mkdir(parents=True,exist_ok=True)
    components.to_parquet(OUT/'pcf_components.parquet',index=False)
    meta.to_csv(OUT/'pcf_metadata.csv',index=False)
    report=dict(status='parsed',days=len(meta),official_days=int((meta.source=='official_xml').sum()),
                mirror_days=int((meta.source=='archived_mirror').sum()),rows=len(components),
                unique_codes=int(components.code.nunique()),stock_codes=int(components.loc[components.stock_replicable,'code'].nunique()),
                positive_mandatory_cash_rows=int(((components.quantity>0)&~components.stock_replicable).sum()),
                publication_timestamps_verified=False,sources=fingerprints)
    write(OUT/'pcf_parse_audit.json',report)
    return components,meta

def fetch_tencent(sym):
    """Four bounded HTTPS queries; no credentials; original bytes cached."""
    result=[]; ticker=sym[-2:].lower()+sym[:6]
    cache=OUT/'public_daily_raw';cache.mkdir(exist_ok=True)
    for start,end in [('2023-03-01','2024-12-31'),('2025-01-01',END)]:
        pair={}
        for adjustment in ['', 'qfq']:
            name=f'{sym}_{start}_{end}_{adjustment or "raw"}.json'
            path=cache/name
            if path.exists(): j=json.loads(path.read_text())
            else:
                r=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                               params={'param':f'{ticker},day,{start},{end},640,{adjustment}'},timeout=30)
                r.raise_for_status();j=r.json()
                if j.get('code')!=0:raise ValueError(f'Tencent failure: {sym}')
                path.write_bytes(r.content)
            a=j.get('data',{}).get(ticker,{})
            values=a.get('qfqday' if adjustment else 'day',a.get('day',[]))
            frame=pd.DataFrame([r[:6] for r in values],columns=['date','open','close','high','low','volume'])
            frame=frame[(frame.date>=start)&(frame.date<=end)].set_index('date')
            frame=frame.apply(pd.to_numeric,errors='coerce')
            pair[adjustment]=frame
        raw=pair[''];adj=pair['qfq']
        if not len(raw):continue  # IPO may occur after the first requested period.
        joined=raw.join(adj[['open','close']],rsuffix='_adjusted',how='left')
        joined['factor']=joined.close_adjusted/joined.close
        joined['tradestatus']=(joined.volume>0).astype(int)
        joined['isST']=np.nan # Never manufacture historical ST identity.
        joined['symbol']=sym;joined['source']='tencent_public_daily'
        result.append(joined.reset_index())
    if not result:raise ValueError(f'No raw daily prices: {sym}')
    return pd.concat(result,ignore_index=True)

def prices(components):
    model_symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    wanted=set(model_symbols)|{symbol(x) for x in components.loc[components.stock_replicable,'code']}
    write(OUT/'price_symbols.json',sorted(wanted))
    frames=[];provenance=[];missing=[]
    for sym in sorted(wanted):
        path=BS/(sym+'.json')
        if not path.exists():missing.append(sym);continue
        j=json.loads(path.read_text());assert j['symbol']==sym
        a=pd.DataFrame(j['rows'],columns=j['fields'])
        a=a[(a.date>='2023-03-01')&(a.date<=END)].copy()
        for k in ['open','high','low','close','preclose','volume','tradestatus','isST']:a[k]=pd.to_numeric(a[k],errors='coerce')
        # Use a single per-stock adjustment basis throughout the account.
        factor=pd.DataFrame(j['factors'],columns=j['factor_fields'])
        factor=factor.rename(columns={'dividOperateDate':'factor_date','backAdjustFactor':'factor'})
        factor['factor']=pd.to_numeric(factor.factor,errors='coerce')
        if len(factor):
            factor=factor.sort_values('factor_date')
            ix=np.searchsorted(factor.factor_date.to_numpy(),a.date.to_numpy(),side='right')-1
            a['factor']=np.where(ix>=0,factor.factor.to_numpy()[np.maximum(ix,0)],1.)
        else:a['factor']=np.nan
        a['factor']=a.factor.fillna(1.) # No action before first recorded event.
        a['symbol']=sym;a['source']='baostock_archived_raw_and_factors'
        a['open_adjusted']=a.open*a.factor;a['close_adjusted']=a.close*a.factor
        frames.append(a);provenance.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
    errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch_tencent,s):s for s in missing}
        for i,f in enumerate(as_completed(futures),1):
            try:frames.append(f.result())
            except Exception as e:errors.append(dict(symbol=futures[f],error=str(e)))
            if i%20==0:print('PUBLIC_PRICE_PROGRESS',i,len(missing),len(errors),flush=True)
    a=pd.concat(frames,ignore_index=True)
    assert not a.duplicated(['date','symbol']).any()
    # Do not make incomplete rows tradeable; valuation carries are ledger-only.
    valid=(a[['open','close','high','low','factor']]>0).all(axis=1)
    valid&=(a.high>=a[['open','close']].max(axis=1))&(a.low<=a[['open','close']].min(axis=1))
    a['valid']=valid
    a.to_parquet(OUT/'daily_prices.parquet',index=False)
    write(OUT/'daily_price_audit.json',dict(symbols_required=len(wanted),symbols_present=int(a.symbol.nunique()),
          downloaded_symbols=len(missing),errors=errors,rows=len(a),source_fingerprints=provenance,
          corporate_actions='adjusted-unit diagnostic, not an actual cash-dividend and share-settlement ledger',
          tencent_historical_ST_unavailable=True))
    return a

def main():
    c,m=parse_all();print('PCF_PARSED',len(m),flush=True)
    prices(c);print('PRICE_ASSEMBLY_COMPLETE',flush=True)

if __name__=='__main__':main()
