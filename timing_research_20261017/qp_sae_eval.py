"""评估 qp_sae_reimpl.py 产出的 SAE 样本外信号（2026-10-18）。
1 研报口径回测（T 收盘信号、T+1 收盘成交、T+2 起计收益，空头倒数收益，零费用），对照研报与 QuantsPlaybook 复现的数字
2 日度预测力：score 对 5 日标签的秩相关（逐年）
3 放进 P2 框架（p2_eval.test：信号非零日方向统计量、NW t、1000 次循环平移 p、三段→这里只有 2020 起，分段按年）：
  目标 Y1 指数（开盘到开盘）、Y2 beta 价差、Y2 市场部分 / 剔除市场部分（事后口径），h = 5/10/20/35；同窗口对照 S3/S5/S7/ACTc
4 与现有信号的重叠和增量：相关、列联、Y2 前向收益对 S7 和 SAE 的联合回归（NW t）
5 杠杆映射：A（现金 ±3.5%）、B（beta 倾斜权重 0.1，信号在时持仓），毛/净 bp/年
输出 outputs/timing_p2_20261017/qp/sae_eval_*.csv
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import p2_eval as E  # noqa: E402

QP = E.OUT / 'qp'
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)


def report_bt(close, sig, mode):
    pos = sig.shift(2, fill_value=0)
    if mode == 'long_only': pos = pos.clip(lower=0)
    if mode == 'short_only': pos = pos.clip(upper=0)
    r_long = close / close.shift(1) - 1; r_short = close.shift(1) / close - 1
    r = pd.Series(np.select([pos.eq(1), pos.eq(-1)], [r_long, r_short], 0.0), index=close.index).iloc[1:]
    ann = float(np.expm1(np.log1p(r).mean() * 252)); vol = float(r.std(ddof=1) * np.sqrt(252))
    wealth = np.r_[1, (1 + r).cumprod()]; mdd = float((wealth / np.maximum.accumulate(wealth) - 1).min())
    ex = sig.shift(1, fill_value=0)
    if mode == 'long_only': ex = ex.clip(lower=0)
    if mode == 'short_only': ex = ex.clip(upper=0)
    trades = int((ex.ne(0) & ex.ne(ex.shift(1, fill_value=0))).sum())
    return dict(ann=ann, vol=vol, sharpe=ann / vol if vol > 0 else float("nan"), mdd=mdd, win=float((r > 0).mean()), trades=trades)


def main():
    S = pd.read_csv(QP / 'sae_000905.csv', parse_dates=['date'], index_col='date')
    idx = pd.read_parquet(E.OUT / 'index_000905.parquet')
    close = idx.close
    out = {}
    # ---------- 1 report-style backtests
    rows = []
    for a, b, lab in [('2020-01-01', '2025-12-31', '2020-2025'), ('2026-01-01', '2026-10-16', '2026YTD'), ('2020-01-01', '2026-10-16', '2020-2026')]:
        c = close[a:b]
        bh = float(np.expm1(np.log1p(c.pct_change().dropna()).mean() * 252))
        for sname in ['signal', 'signal10', 'signal_k0']:
            sg = S[sname].reindex(c.index).fillna(0).astype(int)
            for mode in ['long_short', 'long_only', 'short_only']:
                rows.append(dict(period=lab, signal=sname, mode=mode, buyhold_ann=bh, **report_bt(c, sg, mode)))
        for k in (0.0, 0.004):
            from qp_sae_reimpl import threshold_signal
            sg = threshold_signal(S.score5, k).reindex(c.index).fillna(0).astype(int)
            rows.append(dict(period=lab, signal=f'score5_k{k}', mode='long_short', buyhold_ann=bh, **report_bt(c, sg, 'long_short')))
    BT = pd.DataFrame(rows); BT.to_csv(QP / 'sae_eval_backtest.csv', index=False)
    print(BT.round(4).to_string(index=False))
    # per-year long-short (signal, 5 seeds)
    yr = []
    for y in range(2020, 2027):
        c = close[str(y)]; sg = S.signal.reindex(c.index).fillna(0).astype(int)
        yr.append(dict(year=y, ls=report_bt(c, sg, 'long_short')['ann'], lo=report_bt(c, sg, 'long_only')['ann'],
                       bh=float(np.expm1(np.log1p(c.pct_change().dropna()).mean() * 252)), long_share=float((sg > 0).mean())))
    YR = pd.DataFrame(yr); YR.to_csv(QP / 'sae_eval_by_year.csv', index=False); print(YR.round(4).to_string(index=False))
    # ---------- 2 rank IC on the training label
    lab = close.shift(-6) / close.shift(-1) - 1
    ic = pd.DataFrame({'score5': S.score5, 'lab': lab.reindex(S.index)}).dropna()
    icy = ic.groupby(ic.index.year).apply(lambda g: g.score5.corr(g.lab, method='spearman'))
    print('rank IC by year', icy.round(3).to_dict(), 'all', round(ic.score5.corr(ic.lab, method='spearman'), 4))
    out['ic_all'] = ic.score5.corr(ic.lab, method='spearman')
    # IC non-overlapping (every 5th day) NW-free t
    nz = ic.iloc[::5]; r = nz.score5.corr(nz.lab, method='spearman'); out['ic_nonoverlap'] = r; out['ic_nonoverlap_t'] = r * np.sqrt(len(nz) - 2) / np.sqrt(1 - r * r)
    # ---------- 3 P2-framework tests
    s = pd.read_parquet(E.OUT / 'signals_000905.parquet'); s['S4'] = E.s4_series('000905', s.index)
    s['S5'] = pd.read_parquet(E.OUT / 'breadth.parquet').S5.reindex(s.index)
    s['S7'] = np.sign(s[['S2', 'S3', 'S4', 'S5']].sum(axis=1, min_count=4))
    A = pd.read_parquet(E.OUT / 'style/activity.parquet'); s['ACTc'] = -A.act.reindex(s.index).fillna(0)
    s['SAE'] = S.signal.reindex(s.index); s['SAE10'] = S.signal10.reindex(s.index); s['SAE_k0'] = S.signal_k0.reindex(s.index)
    s['SAE_strong'] = np.sign(S.score5).where(S.score5.abs() > S.score5.abs().expanding(60).median()).fillna(0).reindex(s.index)  # top-half |score| by expanding median (causal)
    s = s.loc['2020-01-01':]
    E.PERIODS[:] = [('2020', '2021'), ('2022', '2023'), ('2024', '2026')]
    d = pd.read_parquet(E.OUT / 'decomp/spread_parts.parquet')
    targets = dict(Y1_指数=None, Y2=d.y2, Y2市场部分=d.mkt_ep, Y2剔除市场=d.res_ep)
    rows = []
    for h in E.H:
        for tn, y in targets.items():
            fy = E.fwd_open(idx, h) if y is None else E.fwd_daily(y, s.index, h)
            fy = fy.reindex(s.index)
            for k in ['SAE', 'SAE10', 'SAE_k0', 'SAE_strong', 'S1', 'S3', 'S5', 'S7', 'ACTc']:
                rows.append(E.test(k, s[k], fy, h, tn))
    T = pd.DataFrame(rows); T.to_csv(QP / 'sae_eval_p2tests.csv', index=False)
    cols = ['signal', 'target', 'h', 'n', 'n_nonzero', 'stat', 'nw_t', 'p_shuffle', 'hit_nonoverlap', 'base_up', 'mean_pos', 'mean_neg', 'periods_positive', 'periods_tested', 'episodes']
    print(T[cols].round(4).to_string(index=False))
    # ---------- 4 overlap / incremental
    sub = s[['SAE', 'SAE_k0', 'S1', 'S3', 'S5', 'S7', 'ACTc']].dropna()
    C = sub.corr(); C.to_csv(QP / 'sae_eval_corr.csv'); print(C.round(3).to_string())
    print(pd.crosstab(sub.SAE, sub.S7)); print(pd.crosstab(sub.SAE, sub.S1))
    reg = []
    for h in (20, 35):
        for tn in ['Y2', 'Y2剔除市场', 'Y1_指数']:
            y = targets[tn]; fy = (E.fwd_open(idx, h) if y is None else E.fwd_daily(y, s.index, h)).reindex(s.index)
            D = pd.DataFrame({'f': fy, 'S7': s.S7, 'SAE': s.SAE, 'ACTc': s.ACTc}).dropna()
            X = np.c_[np.ones(len(D)), D[['S7', 'SAE', 'ACTc']].values]; b = np.linalg.lstsq(X, D.f.values, rcond=None)[0]; e = D.f.values - X @ b
            # NW covariance
            n = len(D); XtX = np.linalg.inv(X.T @ X); u = X * e[:, None]; Sg = u.T @ u
            for L in range(1, h + 1):
                wgt = 1 - L / (h + 1); G = u[L:].T @ u[:-L]; Sg += wgt * (G + G.T)
            se = np.sqrt(np.diag(XtX @ Sg @ XtX))
            reg.append(dict(h=h, target=tn, n=n, b_S7=b[1], t_S7=b[1] / se[1], b_SAE=b[2], t_SAE=b[2] / se[2], b_ACTc=b[3], t_ACTc=b[3] / se[3]))
    R = pd.DataFrame(reg); R.to_csv(QP / 'sae_eval_joint_reg.csv', index=False); print(R.round(4).to_string(index=False))
    # ---------- 5 lever mapping (signal-in-time; A uses index open-to-open, B uses y2)
    L = []
    for k in ['SAE', 'SAE10', 'S7', 'S3', 'ACTc']:
        for cm in (1, 2):
            a = E.lever(k, s[k], 'A', idx.open.pct_change().reindex(s.index), cm)
            bb = E.lever(k, s[k], 'B', d.y2, cm)
            for z in (a, bb):
                if z: L.append(z)
    LV = pd.DataFrame(L); LV.to_csv(QP / 'sae_eval_levers.csv', index=False)
    print(LV[['signal', 'lever', 'cost_mult', 'days', 'ann_bp', 'w35_mean_bp', 'w35_p10_bp', 'w35_hit', 'switches']].round(1).to_string(index=False))
    # current reading
    print('latest', S.tail(8).round(5).to_string())
    print(pd.Series(out).round(4).to_string())


if __name__ == '__main__':
    main()
