"""Tencent public daily klines, raw and qfq, 2026-09-10..2026-10-10, for every frozen symbol plus the CSI500 index; cache raw bytes."""
import json, time, requests, hashlib, sys, datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'gru_research/trading_data/future_minute_probe/tencent_daily_20261012/raw'; OUT.mkdir(parents=True,exist_ok=True); END=sys.argv[2] if len(sys.argv)>2 else '2026-10-10'
syms=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text(encoding='utf-8'))+['000905.SH','000300.SH']
# 2026-10-20: proxy.finance.qq.com started answering the cloud IP with a 501 WAF page at 20:07; the same API on web.ifzq.gtimg.cn
# returns the same JSON, so a blocked host is skipped for the rest of the run
HOSTS=['https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get','https://web.ifzq.gtimg.cn/appstock/app/fqkline/get','https://ifzq.gtimg.cn/appstock/app/fqkline/get']; DEAD=set()   # 21:23 web.ifzq.gtimg.cn was blocked too; ifzq.gtimg.cn is the third copy
s=requests.Session(); s.headers['User-Agent']='Mozilla/5.0'; log=open(OUT.parent/'fetch_log.jsonl','a',encoding='utf-8'); bad=[]
for i,sym in enumerate(syms):
    tk=sym[-2:].lower()+sym[:6]
    for adj in ['','qfq']:
        path=OUT/f'{sym}_{adj or "raw"}.json'
        if path.exists(): continue
        for attempt in range(4):
            try:
                live=[h for h in HOSTS if h not in DEAD]
                if not live: raise RuntimeError('all Tencent hosts blocked')
                host=live[0]
                r=s.get(host,params={'param':f'{tk},day,2026-09-10,{END},60,{adj}'},timeout=20)
                if r.status_code==501 or not r.text.lstrip().startswith('{'): DEAD.add(host); raise ValueError(f'blocked {r.status_code}')
                j=r.json()
                if j.get('code')!=0: raise ValueError('code!=0')
                path.write_bytes(r.content); log.write(json.dumps(dict(symbol=sym,adj=adj or 'raw',sha256=hashlib.sha256(r.content).hexdigest(),fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))+'\n'); log.flush(); break
            except Exception as e:
                time.sleep(0.5)
                if attempt==3: bad.append((sym,adj,str(e)[:60]))
        time.sleep(0.15)
    if i%200==0: print(i,len(bad),flush=True)
print('DONE bad',len(bad),bad[:5],flush=True)
