"""Closes of the PCF valuation day for every PCF constituent, from Tencent public daily bars.
Usage: make_prices.py <pcf.xml> [--out DIR] -> DIR/close_<valuation8>.csv (code,close) and a receipt. Exit 2 if any replicable name lacks a close."""
import sys, json, time
from pathlib import Path
import requests, pandas as pd, xml.etree.ElementTree as ET
pcf = Path(sys.argv[1]); out = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv else Path(__file__).resolve().parents[1] / 'inputs/prices'; out.mkdir(parents=True, exist_ok=True)
root = ET.fromstring(pcf.read_bytes()); meta = {x.tag: x.text for x in root if x.tag != 'ComponentList'}; v8 = meta['PreTradingDay']; vday = f'{v8[:4]}-{v8[4:6]}-{v8[6:]}'
codes = []; optional = set()
for x in root.findall('./ComponentList/Component'):
    r = {a.tag: a.text for a in x}; c = r['InstrumentID'].zfill(6); dot = c + ('.SH' if c[0] in '569' else '.SZ')
    if str(r['SubstitutionFlag']) in ('1', '3'): codes.append(dot)
    elif float(r['Quantity']) == 0: codes.append(dot); optional.add(dot)   # 2026-10-19 K1: zero-quantity rows may be backfilled; their price is optional
# 2026-10-20: Tencent's WAF answers a busy IP with a 501 page, one host at a time (proxy.finance.qq.com from 20:07, web.ifzq.gtimg.cn
# from ~21:23); the same JSON is served by three hosts, so a blocked host is skipped for the rest of the run
HOSTS = ['https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get', 'https://ifzq.gtimg.cn/appstock/app/fqkline/get']; DEAD = set()
def host():
    live = [h for h in HOSTS if h not in DEAD]
    if not live: raise RuntimeError('all Tencent hosts blocked')
    return live[0]
s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'; rows = []; missing = []
# 2026-10-20: a rerun keeps the closes this valuation day already has (same day, same source) and fetches only the rest
prev = out / f'close_{v8}.csv'; have = {}
if prev.exists() and prev.stat().st_size > 0:
    try: have = {r.code: float(r.close) for r in pd.read_csv(prev).itertuples() if float(r.close) > 0}
    except Exception: have = {}
for c in codes:
    if c in have: rows.append(dict(code=c, close=have[c])); continue
    tk = c[-2:].lower() + c[:6]; close = None
    for attempt in range(4):
        try:
            h = host(); r = s.get(h, params={'param': f'{tk},day,{vday},{vday},5,'}, timeout=20)
            if r.status_code == 501 or not r.text.lstrip().startswith('{'): DEAD.add(h); raise ValueError(f'blocked {r.status_code}')
            j = r.json()
            day = {r[0]: r for r in j['data'][tk].get('day', [])}
            if vday in day: close = float(day[vday][2])
            break
        except Exception: time.sleep(1)
    if close and close > 0: rows.append(dict(code=c, close=close))
    else: missing.append(c)
    time.sleep(0.15)
df = pd.DataFrame(rows); path = out / f'close_{v8}.csv'; df.to_csv(path, index=False)
optional_missing = [c for c in missing if c in optional]; missing = [c for c in missing if c not in optional]
(out / f'close_{v8}.receipt.json').write_text(json.dumps(dict(pcf=str(pcf), valuation_day=vday, names=len(df), missing=missing, optional_missing=optional_missing, source='tencent public daily (raw close)', fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), indent=1))
print(json.dumps(dict(ok=not missing, path=str(path), valuation_day=vday, names=len(df), missing=missing, optional_missing=optional_missing)))
sys.exit(0 if not missing else 2)
