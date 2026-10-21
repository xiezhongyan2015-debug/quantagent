"""兴证《捕捉市场的形状》拓扑择时信号的复刻（2026-10-17）。
原文：每日取过去 30 日申万二级行业收益率 → 相关矩阵 ρ → 距离 d=sqrt(2(1−ρ)) → Vietoris-Rips 持久同调 → 2 维孔洞数 N；N≤1 发出做多信号并延续 4 个交易日。
偏离（数据所限）：申万二级行业指数拿不到完整历史（转售接口按日 15–25 秒/次），改用证监会二位行业（baostock 2026-10 快照）对 c5 全 A 日线
（daily_source，2015-01 起）做等权行业收益；剔除成分 < 5 只的行业。节点数约 70–80（原文约 124–131），N 的绝对水平不可直接比较，另报历史分位口径。
运行：tdaenv（含 ripser）。输出 outputs/timing_v3_20261017/tda_h2.csv、industry_returns.parquet
"""
import sys, glob
from pathlib import Path
from multiprocessing import Pool
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / 'outputs/timing_v3_20261017'; OUT.mkdir(parents=True, exist_ok=True)
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'c5_core/data/A_Layer_Live/daily_source'


def industry_returns():
    ind = pd.read_csv(ROOT / 'outputs/barra_cne5_local_sw_industry_baostock_20261015.csv', dtype=str).fillna('')
    ind['sym'] = ind.code.str.replace('.', '', regex=False); ind['cls'] = ind.industry.str[:3]
    g = dict(zip(ind.sym, ind.cls)); rets = {}
    for f in glob.glob(str(SRC / '*.parquet')):
        s = Path(f).stem; c = g.get(s)
        if not c: continue
        x = pd.read_parquet(f, columns=['date', 'close', 'prev_close', 'is_st'])
        x = x[~x.is_st.fillna(False).astype(bool)]
        r = (x.close / x.prev_close - 1).where((x.prev_close > 0)); r[(r.abs() > 0.21)] = np.nan
        rets[s] = pd.Series(r.values, index=pd.to_datetime(x.date))
    R = pd.DataFrame(rets)
    cls = pd.Series({s: g[s] for s in R.columns})
    cnt = R.notna().T.groupby(cls).sum().T
    IR = R.T.groupby(cls).mean().T.where(cnt >= 5)
    IR.to_parquet(OUT / 'industry_returns.parquet'); return IR


def h2_count(args):
    d, X = args
    from ripser import ripser
    C = np.corrcoef(X.T); C = np.clip(C, -1, 1); D = np.sqrt(np.maximum(2 * (1 - C), 0)); np.fill_diagonal(D, 0)
    dg = ripser(D, distance_matrix=True, maxdim=2)['dgms']
    h1 = dg[1]; h2 = dg[2]
    life = lambda g: float(np.sum(g[:, 1] - g[:, 0])) if len(g) else 0.0
    return d, len(h2), len(h1), life(h1), life(h2), X.shape[1]


if __name__ == '__main__':
    IR = industry_returns(); print('industry returns', IR.shape, IR.index.min().date(), IR.index.max().date(), flush=True)
    jobs = []
    for k in range(30, len(IR) + 1):
        W = IR.iloc[k - 30:k]; W = W.loc[:, W.notna().all()]
        if W.shape[1] >= 30: jobs.append((IR.index[k - 1], W.values))
    with Pool(10) as p: res = p.map(h2_count, jobs, chunksize=20)
    df = pd.DataFrame(res, columns=['date', 'h2', 'h1', 'h1_life', 'h2_life', 'nodes']).set_index('date').sort_index()
    df.to_csv(OUT / 'tda_h2.csv'); print(df.describe().round(2).to_string()); print(df.tail(10).to_string())
