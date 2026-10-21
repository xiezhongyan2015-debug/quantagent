"""S3 的"研报口径"版本（2026-10-18）。
兴证《相似几何系列二》4.2 节：上涨概率 = 历史样本点（同样 H-L-P / L-H-P 顺序的每一个交易日）里价格效率高于当前点的比例；
"叠加时间效率"= 只看时间效率 ≥ 当前点的样本点。用沪深 300 2021-09-15 校验：本地（2010 起）84.3/82.1/77.7%、时间版 96.5/95.4/93.4%，
研报（2005 起）87.67/86.11/82.86%、97.74/97.39/96.78%，同向且接近；我们原来的"已完成波段比例"口径在该点为 100%。
因果处理：t 日只用"所在波段的终点端点在 t 日或之前已确认"的历史样本点；端点按事后日期（极值日）分段，这些端点在 t 日都已确认。
信号：P(本段继续) < 0.30 时，下跌段 → +1（偏高 beta），上涨段 → −1，与原 S3 同一阈值、同一方向约定。
输出 outputs/timing_p2_20261017/s3x/
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import xy_swing  # noqa: E402
import p2_eval as E  # noqa: E402
OUT = E.OUT / 's3x'; OUT.mkdir(parents=True, exist_ok=True)


def s3x_history(df, min_points=250, thr=0.30):
    out, piv, _ = xy_swing.swing(df)
    n = len(df); c = df.close.values; dates = df.index
    pv = piv.sort_values('i').reset_index(drop=True); pi, pc, pk, pp = pv.i.values, pv.confirm_i.values, pv.kind.values, pv.price.values
    # ex-post sample points: for each day t', its two previous pivots by date, and the confirmation bar of the pivot that ends its leg
    order = np.full(n, '', object); pe = np.full(n, np.nan); te = np.full(n, np.nan); avail = np.full(n, np.iinfo(np.int64).max)
    for t in range(n):
        k = np.searchsorted(pi, t, side='left')        # pivots strictly before t: indices < k
        if k < 2 or k >= len(pi): continue             # needs a following pivot to be a completed-leg sample
        a, b = k - 2, k - 1
        order[t] = pk[a] + pk[b]; pe[t] = abs(c[t] - pp[b]) / abs(pp[b] - pp[a])
        te[t] = (dates[t] - dates[pi[b]]).days / max((dates[pi[b]] - dates[pi[a]]).days, 1); avail[t] = pc[k]
    # current point: last two pivots confirmed by t (as in S3)
    ci_sorted = np.sort(pc); pv_c = piv.sort_values('confirm_i').reset_index(drop=True)
    P = np.full(n, np.nan); PT = np.full(n, np.nan); S = np.zeros(n); ST = np.zeros(n); eff = np.full(n, np.nan); tef = np.full(n, np.nan)
    for t in range(n):
        m = np.searchsorted(ci_sorted, t, side='right')
        if m < 2: continue
        a, b = pv_c.iloc[m - 2], pv_c.iloc[m - 1]
        cur_pe = abs(c[t] - b.price) / abs(b.price - a.price); cur_te = (dates[t] - dates[int(b.i)]).days / max((dates[int(b.i)] - dates[int(a.i)]).days, 1)
        eff[t], tef[t] = cur_pe, cur_te
        ok = (avail <= t) & (order == a.kind + b.kind)
        if ok.sum() < min_points: continue
        p = float((pe[ok] > cur_pe).mean()); P[t] = p
        okt = ok & (te >= cur_te); PT[t] = float((pe[okt] > cur_pe).mean()) if okt.sum() >= 30 else np.nan
        falling = b.kind == 'H'
        if p < thr: S[t] = 1 if falling else -1
        if np.isfinite(PT[t]) and PT[t] < thr: ST[t] = 1 if falling else -1
    return pd.DataFrame(dict(S3x=S, S3xt=ST, p_x=P, p_xt=PT, eff=eff, teff=tef), index=df.index)


def main():
    idx = pd.read_parquet(E.OUT / 'index_000905.parquet')
    f = OUT / 's3x_000905.parquet'
    if f.exists() and '--rebuild' not in sys.argv: X = pd.read_parquet(f)
    else: X = s3x_history(idx); X.to_parquet(f)
    s = pd.read_parquet(E.OUT / 'signals_000905.parquet'); s['S4'] = E.s4_series('000905', s.index)
    s['S5'] = pd.read_parquet(E.OUT / 'breadth.parquet').S5.reindex(s.index)
    s = s.join(X[['S3x', 'S3xt', 'p_x', 'p_xt']])
    s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4))
    s['S7x'] = np.sign(s[['S2', 'S3x', 'S4', 'S5']].sum(axis=1, min_count=4))
    d = pd.read_parquet(E.OUT / 'decomp/spread_parts.parquet')
    rows, lev = [], []
    for h in (20, 35):
        for tn, y in (('Y2', d.y2), ('市场部分', d.mkt_ep), ('剔除市场', d.res_ep)):
            fy = E.fwd_daily(y, s.index, h)
            for k in ['S3', 'S3x', 'S3xt', 'S7', 'S7x']: rows.append(E.test(k, s[k], fy, h, tn))
        for k in ['S3', 'S3x', 'S3xt', 'S7', 'S7x']: rows.append(E.test(k, s[k], E.fwd_open(idx, h), h, '指数'))
    for k in ['S3', 'S3x', 'S3xt', 'S7', 'S7x']:
        last = s[k].where(s[k] != 0).ffill(limit=34).fillna(0); pos = 0.1 * np.sign(last)
        inc = (pos * d.y2.reindex(s.index).shift(-2) - pos.diff().abs().fillna(0) * 2 * 0.0004).dropna()
        lev.append(dict(signal=k + '_hold35', ann_bp=inc.mean() * 252e4, active=float((pos != 0).mean()), episodes=int(((s[k] != 0) & (s[k] != s[k].shift())).sum()),
                        **{f'{a}_{b}': inc[a:b].mean() * 252e4 for a, b in E.PERIODS}))
    T = pd.DataFrame(rows); T.to_csv(OUT / 'tests.csv', index=False); L = pd.DataFrame(lev); L.to_csv(OUT / 'levers.csv', index=False)
    agree = pd.crosstab(s.S3, s.S3x); agree.to_csv(OUT / 'agree_s3_s3x.csv')
    pd.set_option('display.width', 250)
    print(T[T.h == 35][['target', 'signal', 'n_nonzero', 'episodes', 'stat', 'nw_t', 'p_shuffle', 'periods_positive', 'mean_pos', 'mean_neg']].round(4).to_string(index=False))
    print(T[T.h == 20][['target', 'signal', 'stat', 'nw_t', 'p_shuffle', 'periods_positive']].round(4).to_string(index=False))
    print(L.round(1).to_string(index=False)); print(agree.to_string())
    print('latest', s[['S3', 'p_extend', 'S3x', 'p_x', 'S3xt', 'p_xt', 'S7', 'S7x']].dropna(subset=['p_x']).tail(3).round(3).to_string())


if __name__ == '__main__':
    main()
