"""v3 检验：趋势型 / 拐点型分族检验，再合成（2026-10-17）。沿用 P2 的统计口径（stat、循环平移打乱 p、分段），新增：
  (a) 复刻对照：按兴证原文的纯多头日度择时回测（中证全指代理万得全A，双边 0.1% 费用，t 日收盘信号 t+1 收盘起持有）
  (b) 三维框架：W = 性价比3.0 分位（月末更新）+ 0.1·战术合计 + 0.1·LPPLS 尾部 → 截断 [0,1] → 标准化 Sigmoid(α=10)；股债回测（债券用 10 年国债收益率近似：票息/252 − 久期5×Δy）
输出 outputs/timing_v3_20261017/v3_tests.csv、v3_backtests.csv、v3_today.csv"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent; sys.path.insert(0, str(HERE))
import p2_eval as E  # noqa: E402
OUT = ROOT / 'outputs/timing_v3_20261017'; P2 = ROOT / 'outputs/timing_p2_20261017'
PERIODS = [('2012', '2017'), ('2018', '2022'), ('2023', '2026')]
E.PERIODS = PERIODS


def g_sigmoid(q, a=10):
    s = lambda x: 1 / (1 + np.exp(-a * (x - 0.5))); return (s(q) - s(0)) / (s(1) - s(0))


def load():
    S = pd.read_parquet(OUT / 'signals_v3.parquet')
    tda = OUT / 'tda_sw2_signal.csv'
    if tda.exists(): S['E4'] = pd.read_csv(tda, parse_dates=['date']).set_index('date').E4.reindex(S.index)
    for k in ['E2', 'E4']:
        if k not in S: S[k] = np.nan
    z = lambda c: S[c].fillna(0)
    S['TREND_xy'] = np.sign(S[['T1', 'T2', 'T4', 'T5', 'T6']].sum(axis=1, min_count=3))
    S['TREND_3p0'] = np.sign(S[['T4', 'T5', 'T6']].sum(axis=1, min_count=3))
    S['EXTREME_xy'] = np.sign(z('E1') + z('E2') + z('E3') + z('E4'))
    S['EXTREME_plus'] = np.sign(z('E1') + z('E2') + z('E3') + z('E4') + z('X2') + z('X3') + z('X5'))
    S['ART'] = np.sign(1 + z('T1') + z('T2') + z('E1') + z('E2') + z('E3') + z('E4')).where(S.T1.notna() & S.T2.notna())
    S['ART_plus_trend3'] = np.sign(1 + z('T1') + z('T2') + z('E1') + z('E2') + z('E3') + z('E4') + np.sign(z('T4') + z('T5') + z('T6')))
    # 3.0 three-dimension weight (anchor updated at month end)
    me = S.index.to_series().groupby([S.index.year, S.index.month]).transform('max') == S.index.to_series()
    anchor = S.Q3.where(me).ffill()
    W = (anchor + 0.1 * (z('T4') + z('T5') + z('T6')) + 0.1 * z('E2')).clip(0, 1)
    S['W3D'] = g_sigmoid(W).where(anchor.notna() & S.T4.notna()); S['W3D_lin'] = W.where(anchor.notna() & S.T4.notna()); S['anchor_Q3'] = anchor
    return S


def backtest(pos, px, fee_round=0.001):
    r = px.pct_change().reindex(pos.index)
    p = pos.shift(1).fillna(0)  # signal at t close, held from t+1
    turn = p.diff().abs().fillna(0); net = p * r - turn * fee_round / 2
    net = net.dropna(); nav = (1 + net).cumprod()
    yrs = len(net) / 252; ann = nav.iloc[-1] ** (1 / yrs) - 1; vol = net.std() * np.sqrt(252)
    mdd = (nav / nav.cummax() - 1).min()
    bh = (1 + r.reindex(net.index).fillna(0)).cumprod(); bh_ann = bh.iloc[-1] ** (1 / yrs) - 1; bh_mdd = (bh / bh.cummax() - 1).min()
    return dict(start=str(net.index[0].date()), end=str(net.index[-1].date()), ann=ann, vol=vol, mdd=mdd, in_mkt=float((p > 0).mean()), turnover_yr=float(turn.sum() / yrs), bh_ann=bh_ann, bh_mdd=bh_mdd)


def stockbond(w, px, y10, fee_round=0.001):
    r = px.pct_change().reindex(w.index); y = y10.reindex(w.index).ffill() / 100
    rb = y.shift(1) / 252 - 5 * y.diff(); p = w.shift(1)
    turn = p.diff().abs().fillna(0); net = (p * r + (1 - p) * rb - turn * fee_round / 2).dropna(); nav = (1 + net).cumprod(); yrs = len(net) / 252
    eq = (0.5 * r + 0.5 * rb).reindex(net.index).dropna(); eqn = (1 + eq).cumprod()
    return dict(start=str(net.index[0].date()), ann=nav.iloc[-1] ** (1 / yrs) - 1, mdd=(nav / nav.cummax() - 1).min(), avg_w=float(p.mean()), eq5050_ann=eqn.iloc[-1] ** (1 / yrs) - 1, eq5050_mdd=(eqn / eqn.cummax() - 1).min())


if __name__ == '__main__':
    S = load(); allA = E.pd.read_csv(HERE / 'cache/index_000985.csv', parse_dates=['date']).set_index('date').sort_index()
    i500 = pd.read_parquet(P2 / 'index_000905.parquet'); y2 = pd.read_parquet(P2 / 'y2_beta_spread.parquet').y2
    yl = pd.read_csv(OUT / 'public/yields.csv', parse_dates=['date']).set_index('date').cn10y
    TREND = ['T1', 'T2', 'T4', 'T5', 'T6', 'T7_allA', 'TREND_xy', 'TREND_3p0']
    EXTREME = ['E1', 'E2', 'E3', 'E4', 'X2', 'X3', 'X5', 'EXTREME_xy', 'EXTREME_plus']
    COMBO = ['ART', 'ART_plus_trend3']
    rows = []
    for h in [5, 10, 20, 35]:
        fA = E.fwd_open(allA, h).reindex(S.index); f5 = E.fwd_open(i500, h).reindex(S.index); f2 = E.fwd_daily(y2, S.index, h)
        for fam, ks in (('趋势型', TREND), ('拐点型', EXTREME), ('合成', COMBO)):
            for k in ks:
                s = S[k] if k != 'T7_allA' else S['T7_allA']
                for tgt, f in (('Y1_全A', fA), ('Y1_中证500', f5 if k != 'T7_allA' else f5), ('Y2_beta价差', f2)):
                    sk = S['T7_500'] if (k == 'T7_allA' and tgt != 'Y1_全A') else s
                    r = E.test(k, sk, f, h, tgt); r['family'] = fam; rows.append(r)
    T = pd.DataFrame(rows); T.to_csv(OUT / 'v3_tests.csv', index=False)
    # (a) replication backtests on the all-A proxy, long-only (position 1 if signal>0)
    B = []
    for k in TREND + EXTREME + COMBO:
        s = S[k].dropna()
        if len(s) < 250: continue
        pos = (s > 0).astype(float)
        if k in ('E3',): pos = (s >= 0).astype(float)  # risk-off signal: hold unless -1
        if k in ('E1', 'E2', 'E4', 'X2', 'X3', 'X5', 'EXTREME_xy', 'EXTREME_plus'): pos = (s >= 0).astype(float)  # sparse signals as overlays on a long base: out only when -1
        B.append(dict(signal=k, kind='纯多头择时（全A代理）', **backtest(pos, allA.close)))
    for k in ['W3D', 'W3D_lin', 'anchor_Q3']:
        w = S[k].dropna()
        if len(w) > 250: B.append(dict(signal=k, kind='股债轮动（全A代理 + 10Y 近似债券）', **stockbond(w, allA.close, yl)))
    Bt = pd.DataFrame(B); Bt.to_csv(OUT / 'v3_backtests.csv', index=False)
    today = S.ffill().iloc[-1]; today.to_frame(str(S.index[-1].date())).to_csv(OUT / 'v3_today.csv')
    pd.set_option('display.width', 260)
    key = T[T.h.isin([20, 35])][['family', 'signal', 'target', 'h', 'n_nonzero', 'episodes', 'stat', 'nw_t', 'p_shuffle', 'periods_positive', 'periods_tested']]
    print(key.round(4).to_string(index=False)); print(Bt.round(3).to_string(index=False)); print(today.round(3).to_string())
