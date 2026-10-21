"""P2 补充检验（2026-10-18，用户要求）：S7 对"高减低 beta 价差"（Y2）的预测力，拆成"市场部分"和"剔除市场后的部分"。
Y2 与 p2_signals.beta_spread 同口径：每月末按 60 日 beta 把中证 500 成分分五组，下月每日 高组等权 − 低组等权。
拆分（逐日）：
  事前口径  mkt_ea = (β̄_高 − β̄_低)·R_m，res_ea = Y2 − mkt_ea        β̄ 是组建日的 60 日 beta 组内均值（交易时就知道）
  事后口径  mkt_ep = b̂_月·R_m，res_ep = Y2 − mkt_ep                 b̂_月 是当月 Y2 对 R_m 的回归系数（当月样本内，彻底去掉市场）
  另给 BAB 口径 bab = R_高/β̄_高 − R_低/β̄_低（事前 beta 中性的价差）
每个分量都用 p2_eval.test 做同样的检验（信号非零日的方向统计量、NW t、1000 次循环平移 p、三段），
并按"触发后持有 35 天、权重 0.1"的实盘映射拆出每个分量贡献多少 bp/年。
输出 outputs/timing_p2_20261017/decomp/
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import p2_eval as E  # noqa: E402
import p2_signals as P  # noqa: E402
OUT = E.OUT / 'decomp'; OUT.mkdir(parents=True, exist_ok=True)


def spread_parts(close, M, idx):
    ret = close.pct_change(); ret[(ret > 0.5) | (ret < -0.5)] = np.nan
    ir = idx.close.pct_change().reindex(ret.index)
    me = ret.index.to_series().groupby([ret.index.year, ret.index.month]).max().values
    out = []
    for i in range(3, len(me) - 1):
        t, t1 = pd.Timestamp(me[i]), pd.Timestamp(me[i + 1])
        cols = M.columns[M.loc[t]]
        w = ret.loc[:t].iloc[-60:][cols]; m = ir.loc[:t].iloc[-60:]
        ok = w.notna().sum() >= 40; w = w.loc[:, ok]
        beta = w.apply(lambda s: s.cov(m) / m.var())
        q = pd.qcut(beta.rank(method='first'), 5, labels=False)
        hi, lo = q[q == 4].index, q[q == 0].index
        nxt = ret.loc[(ret.index > t) & (ret.index <= t1)]
        out.append(pd.DataFrame(dict(r_hi=nxt[hi].mean(axis=1), r_lo=nxt[lo].mean(axis=1), b_hi=float(beta[hi].mean()), b_lo=float(beta[lo].mean()),
                                     rm=ir.reindex(nxt.index), month=str(t1.date())[:7])))
    d = pd.concat(out)
    d['y2'] = d.r_hi - d.r_lo; d['db'] = d.b_hi - d.b_lo
    d['mkt_ea'] = d.db * d.rm; d['res_ea'] = d.y2 - d.mkt_ea
    bm = d.groupby('month').apply(lambda g: g.y2.cov(g.rm) / g.rm.var() if g.rm.var() > 0 else np.nan).rename('b_ep')
    d = d.join(bm, on='month'); d['mkt_ep'] = d.b_ep * d.rm; d['res_ep'] = d.y2 - d.mkt_ep
    d['bab'] = d.r_hi / d.b_hi - d.r_lo / d.b_lo
    return d


def hold_map(sig, y, hold=35, w=0.1):
    last = sig.where(sig != 0).ffill(limit=hold - 1).fillna(0)
    pos = w * np.sign(last)
    return (pos * y.reindex(sig.index).shift(-2)).dropna(), pos


def main():
    f = OUT / 'spread_parts.parquet'
    if f.exists() and '--rebuild' not in sys.argv: d = pd.read_parquet(f)
    else:
        idx = pd.read_parquet(E.OUT / 'index_000905.parquet'); close, M = P.load_ifind()
        d = spread_parts(close, M, idx); d.to_parquet(f)
    # consistency with the registered Y2
    y2_reg = pd.read_parquet(E.OUT / 'y2_beta_spread.parquet').y2
    print('Y2 一致性：最大差', float((d.y2 - y2_reg.reindex(d.index)).abs().max()))
    s = pd.read_parquet(E.OUT / 'signals_000905.parquet'); s['S4'] = E.s4_series('000905', s.index)
    s['S5'] = pd.read_parquet(E.OUT / 'breadth.parquet').S5.reindex(s.index)
    s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4))
    idx = pd.read_parquet(E.OUT / 'index_000905.parquet')
    # descriptive: how much of Y2's variance and mean is market
    desc = dict(days=len(d), db_mean=d.db.mean(), b_ep_mean=d.b_ep.mean(), corr_y2_rm=d.y2.corr(d.rm),
                r2_ea=1 - d.res_ea.var() / d.y2.var(), r2_ep=1 - d.res_ep.var() / d.y2.var(),
                y2_ann_bp=d.y2.mean() * 252e4, mkt_ea_ann_bp=d.mkt_ea.mean() * 252e4, res_ea_ann_bp=d.res_ea.mean() * 252e4, res_ep_ann_bp=d.res_ep.mean() * 252e4, bab_ann_bp=d.bab.mean() * 252e4)
    pd.Series(desc).to_csv(OUT / 'describe.csv')
    rows = []
    targets = dict(Y2=d.y2, 市场部分_事前=d.mkt_ea, 剔除市场_事前=d.res_ea, 市场部分_事后=d.mkt_ep, 剔除市场_事后=d.res_ep, BAB=d.bab)
    for h in E.H:
        for tn, y in targets.items():
            fy = E.fwd_daily(y, s.index, h)
            for k in ['S7', 'S3', 'S5', 'S2']:
                r = E.test(k, s[k], fy, h, tn); rows.append(r)
        rows.append(E.test('S7', s['S7'], E.fwd_open(idx, h), h, 'Y1_指数'))
    T = pd.DataFrame(rows); T.to_csv(OUT / 'decomp_tests.csv', index=False)
    # live mapping: S7 held 35 days, weight 0.1 -> contribution of each component (gross), and the net total
    L = []
    for k in ['S7', 'S3', 'S5']:
        for tn, y in targets.items():
            inc, pos = hold_map(s[k], y)
            per = {f'{a}_{b}': inc[a:b].mean() * 252e4 for a, b in E.PERIODS}
            r35 = inc.rolling(35).sum().dropna(); act = pos.reindex(r35.index) != 0
            L.append(dict(signal=k, target=tn, ann_bp=inc.mean() * 252e4, **per, w35_hit_active=float((r35[act] > 0).mean()), w35_p10_active_bp=float(r35[act].quantile(.1) * 1e4)))
    L = pd.DataFrame(L); L.to_csv(OUT / 'decomp_hold35.csv', index=False)
    # S7 episodes split by direction: does +1 (favour high beta) work through the market or the residual?
    inc_y2, pos = hold_map(s['S7'], d.y2); side = []
    for sgn in (1, -1):
        m = np.sign(pos.reindex(inc_y2.index)) == sgn
        side.append(dict(side='偏高beta(+1)' if sgn > 0 else '偏低beta(−1)', days=int(m.sum()),
                         **{tn: float(hold_map(s['S7'], y)[0].reindex(m.index)[m].mean() * 252e4) for tn, y in targets.items()},
                         index_ann_pct=float(idx.close.pct_change().shift(-2).reindex(inc_y2.index)[m].mean() * 252 * 100)))
    Sd = pd.DataFrame(side); Sd.to_csv(OUT / 'decomp_by_side.csv', index=False)
    pd.set_option('display.width', 250)
    print(pd.Series(desc).round(4).to_string())
    k = T[T.h.isin([20, 35]) & T.signal.eq('S7')][['target', 'h', 'n_nonzero', 'stat', 'nw_t', 'p_shuffle', 'periods_positive', 'periods_tested', 'mean_pos', 'mean_neg']]
    print(k.round(4).to_string(index=False))
    k2 = T[T.h.eq(35) & T.signal.isin(['S3', 'S5', 'S2'])][['signal', 'target', 'stat', 'nw_t', 'p_shuffle', 'periods_positive']]
    print(k2.round(4).to_string(index=False))
    print(L.round(1).to_string(index=False)); print(Sd.round(1).to_string(index=False))


if __name__ == '__main__':
    main()
