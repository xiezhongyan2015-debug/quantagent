"""Extend the beta-sorted decline table to 2026-10-16 for current CSI500 members using Tencent public daily bars (qfq)."""
import json, time, numpy as np, pandas as pd, requests
P = 'c5_core/data/A_Layer_Live/panel'; OUT = 'outputs/beta_drawdown_20261018'
m500 = pd.read_parquet(f'{P}/member_csi500.parquet'); a = pd.Timestamp('2026-09-17'); syms = m500.columns[m500.loc[a] == 1].tolist()
ex = pd.read_parquet('outputs/barra_cne5_local/exposures.parquet', columns=['date', 'symbol', 'BETA']); ex['symbol'] = ex.symbol.str[-2:] + ex.symbol.str[:6]
beta = ex[ex.date == a].set_index('symbol').BETA.reindex(syms)
s = requests.Session(); px = {}
for i, sym in enumerate(syms):
    tk = sym.lower()
    for attempt in range(3):
        try:
            j = s.get('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get', params={'param': f'{tk},day,2026-06-26,2026-10-17,100,qfq'}, timeout=15).json()
            d = j['data'][tk]; rows = d.get('qfqday') or d.get('day') or []; px[sym] = {r[0]: float(r[2]) for r in rows}; break
        except Exception as e:
            time.sleep(1)
    if i % 100 == 0: print(i, sym, len(px.get(sym, {})), flush=True)
C = pd.DataFrame(px).sort_index(); C.index = pd.to_datetime(C.index); C.to_csv(f'{OUT}/csi500_members_tencent_qfq_close_20261016.csv')
idx = pd.read_csv('outputs/mentor_review_20261017/csi500_price_index_daily_2009_2026.csv', parse_dates=['date']).set_index('date').close
rows = []
for name, (s0, s1) in {'06-30 高点 → 10-16': ('2026-06-30', '2026-10-16'), '09-17 → 10-16 比赛期': ('2026-09-17', '2026-10-16'), '10-09 → 10-16 最近一周': ('2026-10-09', '2026-10-16')}.items():
    r = C.loc[s1] / C.loc[s0] - 1; d = pd.DataFrame({'ret': r, 'beta': beta}).dropna()
    q = pd.qcut(d.beta.rank(method='first'), 5, labels=['Q1', 'Q2', 'Q3', 'Q4', 'Q5']); g = d.groupby(q, observed=True).ret.mean()
    rows.append(dict(window=name, n=len(d), index_ret=idx[s1] / idx[s0] - 1, ew_ret=d.ret.mean(), **{f'{k}_mean': v for k, v in g.items()}, q5_minus_q1=g['Q5'] - g['Q1'], spearman_beta=d.corr(method='spearman').loc['ret', 'beta']))
T = pd.DataFrame(rows); T.to_csv(f'{OUT}/beta_quintiles_csi500_to_1016.csv', index=False); pd.set_option('display.width', 250); print(T.round(4).to_string())
