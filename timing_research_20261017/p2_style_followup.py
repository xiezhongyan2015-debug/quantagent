"""风格杠杆检验 第二步（2026-10-18）。p2_style_levers.py 的结果引出的问题：
 1 成交额信号 ACT（20 日 vs 120 日成交额）对 beta 价差是"反向"预测（放量后高 beta 跑输、缩量后高 beta 跑赢）。这是看了结果才翻的方向，
   必须当探索看待。拆：它对指数方向、对 beta 价差的市场部分 / 剔除市场部分各有多少；和 S7 重叠多少；S7 之外还剩多少
 2 换手、特质波动在 beta 之外的静态溢价逐年是多少（"永远偏低换手"值不值）
 3 当前读数：ACT 现在是多少（用 c5 面板成交额补到最新）
输出 outputs/timing_p2_20261017/style/followup_*.csv
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import p2_eval as E  # noqa: E402
import p2_style_levers as SL  # noqa: E402
OUT = SL.OUT


def main():
    S = pd.read_parquet(OUT / 'spreads.parquet'); R = SL.ortho(S); A = pd.read_parquet(OUT / 'activity.parquet')
    d = pd.read_parquet(E.OUT / 'decomp/spread_parts.parquet')   # beta spread market / residual parts (ex-post, p2_beta_decomp.py)
    idx = pd.read_parquet(E.OUT / 'index_000905.parquet')
    s = pd.read_parquet(E.OUT / 'signals_000905.parquet'); s['S4'] = E.s4_series('000905', s.index)
    s['S5'] = pd.read_parquet(E.OUT / 'breadth.parquet').S5.reindex(s.index)
    s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4))
    s['ACTc'] = -A.act.reindex(s.index).fillna(0)          # contrarian: +1 after 缩量 (favour high beta), -1 after 放量
    s['S7_or_ACTc'] = np.where(s.S7 != 0, s.S7, s.ACTc)
    s['S7_and_ACTc'] = np.where(np.sign(s.S7) == np.sign(s.ACTc), s.S7, 0)
    # 1 ACT: what does it predict
    rows = []
    tg = dict(beta=d.y2, 市场部分=d.mkt_ep, 剔除市场=d.res_ep, 指数=None)
    for h in (20, 35):
        for tn, y in tg.items():
            fy = E.fwd_open(idx, h) if y is None else E.fwd_daily(y, s.index, h)
            for k in ['ACTc', 'S7', 'S7_or_ACTc', 'S7_and_ACTc']: rows.append(E.test(k, s[k], fy, h, tn))
    T = pd.DataFrame(rows); T.to_csv(OUT / 'followup_act_tests.csv', index=False)
    # ACT beyond S7: on days S7 == 0 only
    rows = []
    for h in (20, 35):
        fy = E.fwd_daily(d.y2, s.index, h); m = s.S7 == 0
        rows.append(E.test('ACTc|S7=0', s.ACTc.where(m, 0), fy, h, 'beta')); rows.append(E.test('ACTc|S7≠0', s.ACTc.where(~m, 0), fy, h, 'beta'))
    T2 = pd.DataFrame(rows); T2.to_csv(OUT / 'followup_act_vs_s7.csv', index=False)
    ov = pd.crosstab(s.S7, s.ACTc, normalize=False); ov.to_csv(OUT / 'followup_overlap.csv')
    # lever mapping on the beta spread (weight 0.1, cost)
    L = []
    for k in ['ACTc', 'S7', 'S7_or_ACTc', 'S7_and_ACTc']:
        for hold in (None, 35):
            v = s[k]; p = v.where(v != 0).ffill(limit=hold - 1).fillna(0) if hold else v
            pos = 0.1 * np.sign(p)
            for tn, y in (('beta', d.y2), ('市场部分', d.mkt_ep), ('剔除市场', d.res_ep)):
                inc = pos * y.reindex(s.index).shift(-2); inc = (inc - (pos.diff().abs() * 2 * 0.0004 if tn == 'beta' else 0)).dropna()
                L.append(dict(signal=k + (f'_hold{hold}' if hold else ''), target=tn, ann_bp=inc.mean() * 252e4, active=float((pos != 0).mean()),
                              **{f'{a}_{b}': inc[a:b].mean() * 252e4 for a, b in E.PERIODS}))
    L = pd.DataFrame(L); L.to_csv(OUT / 'followup_act_levers.csv', index=False)
    # 2 static premia beyond beta, by year
    Y = pd.DataFrame({c: R[c].groupby(R.index.year).mean() * 252e4 for c in R.columns}); Y['beta_raw'] = S.beta.groupby(S.index.year).mean() * 252e4
    Y.to_csv(OUT / 'followup_static_by_year.csv')
    # 3 current ACT from the c5 panel (amount of CSI500 members), overlap check with the iFinD version
    try:
        panel = SL.P.PANEL if hasattr(SL.P, 'PANEL') else None
        amt = pd.read_parquet(HERE.parent / 'c5_core/data/A_Layer_Live/panel/amount.parquet')
        mem = pd.read_parquet(HERE.parent / 'c5_core/data/A_Layer_Live/panel/member_csi500.parquet').reindex_like(amt).fillna(0).astype(bool)
        tot = (amt * mem).sum(axis=1).replace(0, np.nan); z = np.log(tot.rolling(20).mean() / tot.rolling(120).mean())
        both = pd.concat([z.rename('c5'), A.z.rename('ifind')], axis=1).dropna()
        cur = dict(last_date=str(z.dropna().index[-1].date()), z=float(z.dropna().iloc[-1]), corr_with_ifind=float(both.corr().iloc[0, 1]), z_tail=z.dropna().tail(10).round(3).to_dict())
    except Exception as e:
        cur = dict(error=f'{type(e).__name__}: {e}')
    pd.Series(cur).to_csv(OUT / 'followup_current.csv')
    pd.set_option('display.width', 250)
    print(T[T.h == 35][['target', 'signal', 'n_nonzero', 'stat', 'nw_t', 'p_shuffle', 'periods_positive', 'mean_pos', 'mean_neg']].round(4).to_string(index=False))
    print(T2[['target', 'signal', 'h', 'n_nonzero', 'stat', 'nw_t', 'p_shuffle', 'periods_positive']].round(4).to_string(index=False))
    print(ov.to_string()); print(L.round(1).to_string(index=False)); print(Y.round(0).to_string()); print(cur)


if __name__ == '__main__':
    main()
