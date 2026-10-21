"""Fetch public market data for the live-period study (10-12..10-20): Sina 1-minute (datalen 1970, ~8 sessions)
and Tencent raw daily (2026-08-01..2026-10-20) for every symbol with a target gap in the executor ledgers, plus CSI500 index.
Public endpoints, no credentials. Writes only under raw/. Usage: python -I 01_fetch_live_period.py [--force-sina]"""
import json, re, sys, time, requests
from pathlib import Path
HERE = Path(__file__).resolve().parent; RAW = HERE / 'raw'
syms = (RAW / 'gap_symbols.txt').read_text().split()
force = '--force-sina' in sys.argv
s = requests.Session(); s.headers.update({'User-Agent': 'Mozilla/5.0 (Macintosh)', 'Referer': 'https://finance.sina.com.cn/'})
bad = []
for sym in syms:
    tk = sym[-2:].lower() + sym[:6]; p = RAW / 'sina1m' / f'{sym}.json'
    if p.exists() and not force: continue
    for attempt in range(4):
        try:
            r = s.get('https://quotes.sina.cn/cn/api/jsonp_v2.php/x/CN_MarketDataService.getKLineData', params={'symbol': tk, 'scale': '1', 'datalen': '1970'}, timeout=20)
            m = re.search(r'\((\[.*\]|null)\)', r.text, re.S); arr = json.loads(m.group(1)) or []
            p.write_text(json.dumps(arr)); break
        except Exception as e:
            time.sleep(1.5 * (attempt + 1))
            if attempt == 3: bad.append(('sina', sym, str(e)[:60]))
    time.sleep(0.12)
t = requests.Session(); t.headers['User-Agent'] = 'Mozilla/5.0'
for sym in syms + ['000905.SH']:
    tk = sym[-2:].lower() + sym[:6]; p = RAW / 'tencent_daily' / f'{sym}.json'
    for attempt in range(4):
        try:
            j = t.get('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get', params={'param': f'{tk},day,2026-08-01,2026-10-20,80,'}, timeout=20).json()
            rows = j['data'][tk].get('day') or j['data'][tk].get('qfqday') or []
            p.write_text(json.dumps(rows)); break
        except Exception as e:
            time.sleep(0.6)
            if attempt == 3: bad.append(('tencent', sym, str(e)[:60]))
    time.sleep(0.05)
print('DONE', len(syms), 'bad', bad)
