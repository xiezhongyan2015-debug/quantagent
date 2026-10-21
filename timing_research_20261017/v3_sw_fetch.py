"""申万宏源官网指数行情（https://www.swsresearch.com/institute-sw/api/index_publish/trend/）：一级 31 个 + 二级 124 个（SW2021），全历史日线。"""
import sys, time, json
from pathlib import Path
import pandas as pd, requests
ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / 'outputs/timing_v3_20261017/sw'; OUT.mkdir(parents=True, exist_ok=True)
L1 = [801010,801030,801040,801050,801080,801110,801120,801130,801140,801150,801160,801170,801180,801200,801210,801230,801710,801720,801730,801740,801750,801760,801770,801780,801790,801880,801890,801950,801960,801970,801980]
L2 = pd.read_csv(sys.argv[1]).ts_code.str[:6].astype(int).tolist()
from concurrent.futures import ThreadPoolExecutor
def get(job):
    lvl, c = job; f = OUT / f'{lvl}_{c}.csv'
    if f.exists(): return 1
    try:
        j = requests.get('https://www.swsresearch.com/institute-sw/api/index_publish/trend/', params=dict(swindexcode=str(c), period='DAY'), headers={'User-Agent': 'Mozilla/5.0'}, timeout=120).json()
        pd.DataFrame(j['data'])[['bargaindate', 'closeindex', 'markup', 'bargainsum']].to_csv(f, index=False); return 1
    except Exception:
        return 0
jobs = [('L1', c) for c in L1] + [('L2', c) for c in L2]
for rnd in range(int(__import__("os").environ.get("SW_ROUNDS", "6"))):
    todo = [j for j in jobs if not (OUT / f'{j[0]}_{j[1]}.csv').exists()]
    if not todo: break
    with ThreadPoolExecutor(int(__import__("os").environ.get("SW_THREADS", "6"))) as ex: ok = sum(ex.map(get, todo))
    print("round", rnd, "todo", len(todo), "ok", ok, flush=True); time.sleep(int(__import__("os").environ.get("SW_SLEEP", "5")))
print(len(list(OUT.glob('L1_*.csv'))), len(list(OUT.glob('L2_*.csv'))))
