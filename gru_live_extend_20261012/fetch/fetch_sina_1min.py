"""Fetch Sina 1-minute klines (datalen 1970, ~8 sessions) for every frozen symbol; cache raw response bytes. Public endpoint, no credentials."""
import json, time, sys, re, requests, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'gru_research/trading_data/future_minute_probe/sina_20261012/raw'; OUT.mkdir(parents=True,exist_ok=True)
syms=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text(encoding='utf-8'))
s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 (Macintosh)','Referer':'https://finance.sina.com.cn/'})
log=open(OUT.parent/'fetch_log.jsonl','a',encoding='utf-8'); done=0; bad=[]
for i,sym in enumerate(syms):
    tk=sym[-2:].lower()+sym[:6]; path=OUT/f'{sym}.json'
    if path.exists(): done+=1; continue
    for attempt in range(4):
        try:
            r=s.get('https://quotes.sina.cn/cn/api/jsonp_v2.php/x/CN_MarketDataService.getKLineData',params={'symbol':tk,'scale':'1','datalen':'1970'},timeout=20)
            m=re.search(r'\((\[.*\]|null)\)',r.text,re.S)
            if not m: raise ValueError('no json body')
            arr=json.loads(m.group(1)) or []
            path.write_bytes(r.content); log.write(json.dumps(dict(symbol=sym,rows=len(arr),first=arr[0]['day'] if arr else None,last=arr[-1]['day'] if arr else None,fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),sha256=hashlib.sha256(r.content).hexdigest()))+'\n'); log.flush(); done+=1; break
        except Exception as e:
            time.sleep(1.5*(attempt+1))
            if attempt==3: bad.append((sym,str(e)[:80]))
    time.sleep(0.12)
    if i%100==0: print(i,done,len(bad),flush=True)
print('DONE',done,'bad',len(bad),bad[:10],flush=True)
