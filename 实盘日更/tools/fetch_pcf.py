"""Download the official 563030 PCF for trading day T (YYYY-MM-DD) and verify it. Public CDN, no credentials.
Usage: fetch_pcf.py T [--out DIR]  -> DIR/563030_<T8>.xml + .sha256 receipt. Exit 2 if the file is not for T or cannot be parsed."""
import sys, json, hashlib, time
from pathlib import Path
import requests, xml.etree.ElementTree as ET
T = sys.argv[1]; t8 = T.replace('-', ''); out = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv else Path(__file__).resolve().parents[1] / 'inputs/pcf'
out.mkdir(parents=True, exist_ok=True); url = f'https://cdn.efunds.com.cn/owch/data/pcf_ims/{t8}/etfd_563030_{t8}.xml'
r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
if r.status_code != 200: print(json.dumps(dict(ok=False, status=r.status_code, url=url))); sys.exit(2)
root = ET.fromstring(r.content); meta = {x.tag: x.text for x in root if x.tag != 'ComponentList'}
n = len(root.findall('./ComponentList/Component'))
if meta.get('TradingDay') != t8 or n < 100: print(json.dumps(dict(ok=False, trading_day=meta.get('TradingDay'), components=n, url=url))); sys.exit(2)
path = out / f'563030_{t8}.xml'; path.write_bytes(r.content); sha = hashlib.sha256(r.content).hexdigest()
(out / f'563030_{t8}.sha256').write_text(json.dumps(dict(url=url, sha256=sha, trading_day=meta['TradingDay'], pre_trading_day=meta['PreTradingDay'], components=n, nav_per_cu=meta['NAVperCU'], fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())), indent=1))
print(json.dumps(dict(ok=True, path=str(path), trading_day=meta['TradingDay'], valuation_day=meta['PreTradingDay'], components=n, sha256=sha[:12])))
