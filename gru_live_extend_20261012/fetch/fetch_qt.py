"""Tencent batch quote (qt.gtimg.cn) for float shares / turnover cross-check; today's snapshot only, public, no credentials."""
import json, time, requests, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'gru_research/trading_data/future_minute_probe/tencent_qt_20261012'; OUT.mkdir(parents=True,exist_ok=True)
syms=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text(encoding='utf-8')); s=requests.Session(); s.headers['User-Agent']='Mozilla/5.0'
rows=[]
for i in range(0,len(syms),50):
    chunk=syms[i:i+50]; q=','.join(x[-2:].lower()+x[:6] for x in chunk)
    for attempt in range(3):
        try:
            t=s.get('https://qt.gtimg.cn/q='+q,timeout=20); t.encoding='gbk'; txt=t.text; break
        except Exception as e: time.sleep(1); txt=''
    for line in txt.strip().split(';'):
        if '=' not in line: continue
        f=line.split('=')[1].strip('"').split('~')
        if len(f)<50: continue
        rows.append(dict(symbol=f[2]+('.SH' if line.split('=')[0].strip().startswith('v_sh') else '.SZ'),name=f[1],date=f[30][:8],last=f[3],prev_close=f[4],open=f[5],volume_lots=f[6],amount_wan=f[37],turnover_pct=f[38],float_cap_yi=f[44],total_cap_yi=f[45],raw=line.strip()))
    time.sleep(0.2)
(OUT/'qt_snapshot.json').write_text(json.dumps(dict(fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),rows=rows),ensure_ascii=False), encoding='utf-8')
print('qt rows',len(rows))
