"""兴证拓扑择时信号，用申万宏源官网申万二级（SW2021，124 个）指数日线复刻（2026-10-17）。
原文：每日过去 30 日申万二级行业收益率 → 相关矩阵 → d=sqrt(2(1−ρ)) → Vietoris-Rips → 2 维孔洞数 N；N≤1 → 做多并延续 4 个交易日。
输出 outputs/timing_v3_20261017/tda_sw2.csv（逐日 H1/H2）与 tda_sw2_signal.csv（E4）。用 tdaenv（ripser）运行。"""
import glob
from pathlib import Path
from multiprocessing import Pool
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / 'outputs/timing_v3_20261017'


def h(args):
    d, X = args
    from ripser import ripser
    C = np.clip(np.corrcoef(X.T), -1, 1); D = np.sqrt(np.maximum(2 * (1 - C), 0)); np.fill_diagonal(D, 0)
    dg = ripser(D, distance_matrix=True, maxdim=2)['dgms']
    return d, len(dg[2]), len(dg[1]), float(np.sum(dg[2][:, 1] - dg[2][:, 0])) if len(dg[2]) else 0.0, X.shape[1]


if __name__ == '__main__':
    px = {}
    for f in glob.glob(str(OUT / 'sw/L2_*.csv')):
        x = pd.read_csv(f, parse_dates=['bargaindate']).set_index('bargaindate').closeindex
        px[Path(f).stem[3:]] = x[~x.index.duplicated()]
    P = pd.DataFrame(px).sort_index(); R = P.pct_change(fill_method=None).loc['2014-12-01':]
    print('SW L2 indices', P.shape[1], R.index.min().date(), R.index.max().date(), flush=True)
    jobs = []
    for k in range(30, len(R) + 1):
        W = R.iloc[k - 30:k]; W = W.loc[:, W.notna().all()]
        if W.shape[1] >= 60: jobs.append((R.index[k - 1], W.values))
    with Pool(10) as p: res = p.map(h, jobs, chunksize=10)
    df = pd.DataFrame(res, columns=['date', 'h2', 'h1', 'h2_life', 'nodes']).set_index('date').sort_index(); df.to_csv(OUT / 'tda_sw2.csv')
    raw = (df.h2 <= 1).astype(float); E4 = raw.rolling(4, min_periods=1).max()  # signal day + following 3 days = 4 trading days
    E4 = E4.where(df.nodes >= 115)  # before 2022-01 many SW2021 L2 indices have no history (65 nodes): hole counts not comparable -> not used
    pd.DataFrame(dict(E4=E4, h2=df.h2)).to_csv(OUT / 'tda_sw2_signal.csv')
    print(df.describe().round(2).to_string()); print('E4 share', round(E4.mean(), 3), 'trigger days', int(raw.sum())); print(df.tail(8).to_string())
