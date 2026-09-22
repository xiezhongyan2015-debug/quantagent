"""PCF shadow-book adapter for the existing target CSV/JSON handoff. Never sends orders."""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from .competition import precheck
import re

def normalize_code(value):
    value = value.strip().upper()
    if re.fullmatch(r'[036]\d{5}', value):
        value = ('SH' if value.startswith('6') else 'SZ') + value
    elif re.fullmatch(r'[036]\d{5}\.(SH|SZ)', value):
        value = value[-2:] + value[:6]
    if not re.fullmatch(r'(SH6\d{5}|SZ[03]\d{5})', value):
        raise ValueError('Unsupported A-share code: ' + value)
    return value

def download_pcf(fund: str, date: str, out: str) -> dict:
    if fund!='563030':raise ValueError('Only the verified E Fund 563030 provider is supported')
    base='https://api.efunds.com.cn/xcowch/front/etffund/'
    record={'date':date,'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'historical_publication_timestamp_verified':False,'source':base}
    for ep in ['baseinfo','stocklist']:
        r=requests.get(base+ep,params={'fundCode':fund,'tDate':date,'listType':'1'},timeout=30)
        r.raise_for_status();obj=r.json()
        if obj.get('status')!=1:raise ValueError('PCF provider returned failure')
        record[ep]=obj['data']
    validate_pcf(record)
    path=Path(out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record

def validate_pcf(record):
    day=pd.Timestamp(record['date']).normalize();b=record['baseinfo'];stocks=record['stocklist']
    if pd.Timestamp(b['tDate'])!=day or not b.get('isMapShow'):raise ValueError('PCF date mismatch or unavailable')
    if not stocks:raise ValueError('Empty PCF')
    if len(stocks)!=int(b['map']['RECORDNUM']):raise ValueError('PCF record count mismatch')
    codes=[normalize_code(str(x['C_STOCKCODE'])) for x in stocks]
    if len(set(codes))!=len(codes):raise ValueError('Duplicate PCF security')
    if any(str(x['BUSI_DATE'])!=day.strftime('%Y%m%d') for x in stocks):raise ValueError('Component date mismatch')
    qty=np.array([float(x['L_NUMBER']) for x in stocks])
    if not np.isfinite(qty).all() or (qty<0).any():raise ValueError('Invalid PCF quantity')
    if not np.isfinite(float(b['map']['NAVPERCU'])) or float(b['map']['NAVPERCU'])<=0:raise ValueError('Invalid PCF NAV')
    return day,codes,qty

def build_target(record,quotes,holdings,nav,calendar,execution_date,asof,cost=.001,lag=1):
    """Quotes contain current raw close and prior-PCF-date reference raw close.

    This is a separate shadow subaccount. Holdings must contain only that subaccount.
    Final broker validation, current prices, lot rules and order transmission remain external.
    """
    if not np.isfinite(nav) or nav<=0 or cost!=.001 or lag not in [1,3,5]:raise ValueError('Invalid NAV, fee, or lag')
    day,codes,qty=validate_pcf(record);asof=pd.Timestamp(asof).normalize();execution=pd.Timestamp(execution_date).normalize()
    cal=pd.DatetimeIndex(pd.to_datetime(calendar)).normalize()
    if cal.has_duplicates or not cal.is_monotonic_increasing:raise ValueError('Invalid exchange calendar')
    if day not in cal or execution not in cal or asof not in cal:raise ValueError('Dates absent from supplied exchange calendar')
    if cal.get_loc(execution)-cal.get_loc(day)!=lag or cal.get_loc(execution)-cal.get_loc(asof)!=1:raise ValueError('Stale PCF or incorrect execution lag/asof')
    previous=pd.Timestamp(record['baseinfo']['map']['PRETRADINGDAY']).normalize()
    if previous not in cal or cal.get_loc(day)-cal.get_loc(previous)!=1:raise ValueError('PCF previous trading date mismatch')
    required={'code','date','close','reference_date','reference_close','member_csi500','member_union','is_st','tradable'}
    if not required.issubset(quotes):raise ValueError('Missing quote or point-in-time membership fields: '+str(required-set(quotes)))
    q=quotes.copy();q['code']=q.code.astype(str).map(normalize_code)
    if q.code.duplicated().any():raise ValueError('Duplicate quote code')
    if not pd.to_datetime(q.date).eq(asof).all() or not pd.to_datetime(q.reference_date).eq(previous).all():raise ValueError('Quote dates are not the required historical snapshots')
    q=q.set_index('code')
    for field in ['member_csi500','member_union','is_st','tradable']:
        if not q[field].isin([0,1]).all():raise ValueError('Flags must be explicit 0/1')
    if (q.member_csi500.astype(bool)&~q.member_union.astype(bool)).any():raise ValueError('CSI500 membership must imply union membership')
    h=holdings.copy()
    if not {'code','shares'}.issubset(h):raise ValueError('Holdings require code and shares; supply an explicit empty file for a new book')
    h['code']=h.code.astype(str).map(normalize_code);h['shares']=pd.to_numeric(h.shares,errors='raise')
    if h.code.duplicated().any() or not np.isfinite(h.shares).all() or (h.shares<0).any() or (h.shares%1!=0).any():raise ValueError('Invalid holdings')
    held=h.set_index('code').shares
    allcodes=sorted(set(codes)|set(held.index))
    if set(allcodes)-set(q.index):raise ValueError('Missing quote: refusing to drop stock or infer liquidation')
    q=q.loc[allcodes]
    if not np.isfinite(q[['close','reference_close']].to_numpy(float)).all() or (q[['close','reference_close']]<=0).any().any():raise ValueError('Raw prices must be positive and finite')
    basket=pd.Series(qty,index=codes).reindex(allcodes,fill_value=0)
    weights=basket*q.reference_close/float(record['baseinfo']['map']['NAVPERCU'])
    if weights.sum()>.98:weights*=.98/weights.sum()
    held=held.reindex(allcodes,fill_value=0).astype(float);held_mv=held*q.close
    if held_mv.sum()>nav+1e-6:raise ValueError('Marked holdings exceed subaccount NAV')
    target_mv=weights*nav;want=pd.Series(0.,index=allcodes)
    frozen=q.tradable.eq(0)|q.is_st.eq(1)
    for code in allcodes:
        minimum=200 if code.startswith('SH688') else 100
        lots=np.floor(target_mv[code]/q.loc[code,'close']/100)*100
        if 0<lots<minimum:lots=0
        if frozen[code]:lots=held[code]
        # Conservative partial sales: retain odd lots; sell the whole residual on full exit.
        if 0<lots<held[code]:
            reduction=np.floor((held[code]-lots)/100)*100
            if reduction<minimum:reduction=0
            lots=held[code]-reduction
        if lots>held[code]:
            addition=np.floor((lots-held[code])/100)*100
            if addition<minimum:addition=0
            lots=held[code]+addition
        want[code]=lots
    delta=want-held
    sell=(-delta.clip(upper=0))*q.close
    cash=nav-held_mv.sum()+sell.sum()*(1-cost)
    buys=delta.clip(lower=0);required_cash=float((buys*q.close).sum()*(1+cost))
    if required_cash>cash and required_cash>0:
        scale=max(0,cash)/required_cash
        buys=np.floor(buys*scale/100)*100
        for code in allcodes:
            if code.startswith('SH688') and 0<buys[code]<200:buys[code]=0
        want=held+delta.clip(upper=0)+buys
    delta=want-held;fees=float((delta.abs()*q.close).sum()*cost)
    remaining=float(nav-(want*q.close).sum()-fees)
    if remaining< -1e-6:raise ValueError('Cash budget exceeded')
    df=pd.DataFrame({'code':allcodes,'bucket':np.where(q.member_csi500,'csi500',np.where(q.member_union,'other','outside')),
        'score_pct':np.nan,'state':'pcf_shadow','held_mv':held_mv.values,'target_mv':target_mv.values,
        'exec_shares':want.values,'exec_mv':(want*q.close).values,'delta_shares':delta.values,
        'delta_mv':(delta*q.close).values,'close':q.close.values,'is_st':q.is_st.values,'pcf_weight':weights.values})
    df['action']=np.where(delta.values>0,'buy',np.where(delta.values<0,np.where(want.values==0,'sell','trim'),'hold'))
    df.loc[(want.values==0)&(held.values==0)&(target_mv.values>0),'action']='skip_lot'
    df['blocked']=frozen.values
    is500=q.member_csi500.astype(bool).to_dict()
    chk=precheck(held_mv[held_mv>0].to_dict(),(want*q.close)[want>0].to_dict(),nav,is500)
    summary={'asof':str(asof.date()),'execution_date':str(execution.date()),'pcf_date':str(day.date()),'lag':lag,'nav':nav,
        'strategy':'pcf_563030_shadow','schema':'target_csv_compatible_v1','broker_orders_submitted':False,
        'requires_external_execution_validation':True,'historical_publication_timestamp_verified':False,
        'target_invested':float((want*q.close).sum()/nav),'target_share500':float((want*q.close*q.member_csi500).sum()/nav),
        'target_max_single':float((want*q.close).max()/nav),'n_orders':int((delta!=0).sum()),
        'estimated_fee':fees,'cash_after_estimated_trades':remaining,'cost_per_side':cost,'precheck':chk,
        'membership':'caller-supplied asof snapshot; missing membership is rejected',
        'price_basis':'unadjusted closes; reference prices strictly before PCF date; execution is a plan, not a fill'}
    priority=df.action.map({'sell':0,'trim':0,'buy':1,'hold':2,'skip_lot':3})
    df=df.assign(_order=priority).sort_values(['_order','bucket','code']).drop(columns='_order')
    return df,summary

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='cmd',required=True)
    fetch=sub.add_parser('fetch');fetch.add_argument('--fund',default='563030');fetch.add_argument('--date',required=True);fetch.add_argument('--out',required=True)
    plan=sub.add_parser('target')
    for key in ['pcf','quotes','holdings','calendar','execution-date','asof','out']:plan.add_argument('--'+key,required=True)
    plan.add_argument('--nav',required=True,type=float);plan.add_argument('--lag',type=int,default=1,choices=[1,3,5])
    a=ap.parse_args()
    if a.cmd=='fetch':download_pcf(a.fund,a.date,a.out);return
    record=json.loads(Path(a.pcf).read_text(encoding='utf-8'))
    quotes=pd.read_csv(a.quotes,dtype={'code':str});held=pd.read_csv(a.holdings,dtype={'code':str})
    calendar=json.loads(Path(a.calendar).read_text(encoding='utf-8'))
    frame,summary=build_target(record,quotes,held,a.nav,calendar,a.execution_date,a.asof,lag=a.lag)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);tag=pd.Timestamp(a.asof).strftime('%Y%m%d')
    frame.to_csv(out/f'target_{tag}.csv',index=False,encoding='utf-8-sig')
    (out/f'target_{tag}.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=float),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2,default=float))

if __name__=='__main__':main()
