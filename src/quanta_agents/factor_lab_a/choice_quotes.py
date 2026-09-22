"""Public Choice quote adapter. No account credentials, trading or fabricated timestamps."""
from __future__ import annotations
import argparse
import datetime as dt
from pathlib import Path
import requests
from .choice_pcf import TradeError, number
from .pcf import normalize_code
from .pcf_automation import CN, read_json, write_json

URL='https://choicelab.eastmoney.com/api/qt/stock/get'


def quote(code,session=None):
    code=normalize_code(code)
    client=session or requests
    r=client.get(URL,params={'id':code,'tt':'','tv':''},timeout=15,allow_redirects=False)
    if r.status_code!=200: raise TradeError('Quote HTTP failure')
    obj=r.json()
    if obj.get('rc')!=0 or obj.get('rt')!=4: raise TradeError('Quote provider failure')
    row=obj.get('data') or {}
    if str(row.get('f57'))!=code[2:]: raise TradeError('Quote security mismatch')
    scale=10**(-number(row['f59'],True))
    if not 0<scale<=1: raise TradeError('Invalid quote precision')
    stamp=dt.datetime.fromtimestamp(number(row['f86']),CN)
    out={'timestamp':stamp.isoformat(),'code':code,'name':str(row['f58']),
         'source':URL,'last':number(row['f43'])*scale,'previous_close':number(row['f60'])*scale,
         'bid':number(row['f19'])*scale,'ask':number(row['f39'])*scale,
         'upper_limit':number(row['f51'])*scale,'lower_limit':number(row['f52'])*scale,
         'is_st':int('ST' in str(row['f58']).upper() or '退' in str(row['f58']))}
    for k in ('last','previous_close','bid','ask','upper_limit','lower_limit'):
        out[k]=round(out[k],int(row['f59']))
    out['tradable']=int(out['last']>0 and out['bid']>0 and out['ask']>0 and
        0<out['lower_limit']<=out['bid']<=out['ask']<=out['upper_limit'])
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--code',required=True); ap.add_argument('--out',required=True)
    args=ap.parse_args(); code=normalize_code(args.code)
    data=read_json(args.out) if Path(args.out).exists() else {}
    data[code]=quote(code)
    write_json(args.out,data)


if __name__=='__main__': main()
