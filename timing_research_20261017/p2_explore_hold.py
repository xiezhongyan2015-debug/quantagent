"""P2 探索（未登记，不计入门槛）：事件触发后持有 hold 个交易日的杠杆映射。pos_t = 最近 hold 日内最后一个非零信号的符号。"""
import sys, warnings; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import p2_eval as E
OUT = E.OUT
idx = pd.read_parquet(OUT / 'index_000905.parquet'); s = pd.read_parquet(OUT / 'signals_000905.parquet')
s['S4'] = E.s4_series('000905', s.index); br = pd.read_parquet(OUT / 'breadth.parquet'); s['S5'] = br.S5.reindex(s.index)
s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4))
y2 = pd.read_parquet(OUT / 'y2_beta_spread.parquet').y2; y3 = pd.read_parquet(OUT / 'y3_overlay_excess.parquet').y3
r_oo = idx.open.shift(-1) / idx.open - 1
rows = []
for k in ['S2', 'S3', 'S5', 'S7']:
    for hold in (20, 35):
        last = s[k].where(s[k] != 0).ffill(limit=hold - 1).fillna(0)
        for kind, y, w in (('A', r_oo, 0.035), ('B', y2, 0.2), ('B_half', y2, 0.1)):
            pos = w * np.sign(last)
            inc = (pos * (y.shift(-1).reindex(s.index) if kind == 'A' else y.reindex(s.index).shift(-2)))
            turn = pos.diff().abs() * (1 if kind == 'A' else 2)
            inc = (inc - turn.fillna(0) * 0.0004).dropna()
            r35 = inc.rolling(35).sum().dropna(); act = (pos.reindex(r35.index) != 0)
            per = {f'ann_{a}_{b}': inc[a:b].mean() * 252 * 1e4 for a, b in E.PERIODS}
            rows.append(dict(signal=k, hold=hold, lever=kind, ann_bp=inc.mean() * 252 * 1e4, active_share=float((pos != 0).mean()),
                             w35_hit_active=float((r35[act] > 0).mean()), w35_mean_active_bp=float(r35[act].mean() * 1e4), w35_p10_active_bp=float(r35[act].quantile(.1) * 1e4), **per))
R = pd.DataFrame(rows); R.to_csv(OUT / 'p2_explore_hold.csv', index=False)
pd.set_option('display.width', 250); print(R.round(1).to_string(index=False))
