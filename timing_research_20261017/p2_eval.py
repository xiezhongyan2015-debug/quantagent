"""P2 检验：按 reports/P2_择时信号检验协议_2026-10-17.md 第 4–5 节。
主统计量 stat = 信号非零日上 s_t·(fwd_t − μ) 的均值（μ = 同样本无条件均值）；>0 表示方向正确。
打乱对照：信号整段循环平移 1000 次（平移量 60..N−60），p = 平移统计量 ≥ 实际值的比例。
输出 outputs/timing_p2_20261017/p2_tests.csv、p2_levers.csv、p2_summary.md
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent; sys.path.insert(0, str(HERE))
import lppls  # noqa: E402
OUT = ROOT / 'outputs/timing_p2_20261017'
CODES = {'000905': '中证500', '000300': '沪深300', '000852': '中证1000', '399006': '创业板指'}
H = [5, 10, 20, 35]
RNG = np.random.default_rng(20261017)
PERIODS = [('2010', '2017'), ('2018', '2023'), ('2024', '2026')]
FEE = 0.0002


def s4_series(code, index):
    f = OUT / f'lppls_hist_{code}.csv'
    if not f.exists(): return pd.Series(np.nan, index=index)
    h = pd.read_csv(f, parse_dates=['date']).drop_duplicates('date').set_index('date').sort_index()
    h = h.reindex(index.union(h.index)).ffill(limit=5).reindex(index).dropna()  # 5-day sampled history -> daily (protocol supplement 1)
    out = pd.Series(0.0, index=h.index)
    pos, neg = h.pos.astype(float), h.neg.astype(float)
    pk_p, pk_n = pos.rolling(40, min_periods=1).max(), neg.rolling(40, min_periods=1).max()
    out[(pk_n > 0.3) & (pk_n - neg > 0.2)] = 1
    out[(pk_p > 0.3) & (pk_p - pos > 0.2)] = -1  # if both fire, bearish wins (rare)
    return out.reindex(index)


def fwd_open(df, h):
    o = df.open
    return o.shift(-(1 + h)) / o.shift(-1) - 1


def fwd_daily(y, index, h):
    """cumulative return of a daily return series over days t+2 .. t+1+h (one-day skip, conservative)"""
    ly = np.log1p(y.reindex(index))
    c = ly.cumsum()
    return np.expm1(c.shift(-(1 + h)) - c.shift(-1))


def nw_t(x, lag):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = len(x)
    if n < 30: return np.nan
    m = x.mean(); e = x - m; v = e @ e / n
    for L in range(1, min(lag, n - 1) + 1):
        w = 1 - L / (lag + 1); v += 2 * w * (e[L:] @ e[:-L]) / n
    return m / np.sqrt(v / n) if v > 0 else np.nan


def stat(s, f):
    ok = s.notna() & f.notna(); s, f = s[ok], f[ok]
    if len(f) == 0: return np.nan, 0, s, f
    mu = f.mean(); nz = s != 0
    if nz.sum() == 0: return np.nan, 0, s, f
    return float((s[nz] * (f[nz] - mu)).mean()), int(nz.sum()), s, f


def test(name, s, f, h, target):
    st, n_nz, s_ok, f_ok = stat(s, f)
    row = dict(signal=name, target=target, h=h, n=len(f_ok), n_nonzero=n_nz, share_nonzero=n_nz / max(len(f_ok), 1), stat=st)
    if not np.isfinite(st) or n_nz < 20: return row
    mu = f_ok.mean(); nz = s_ok != 0
    x = (s_ok * (f_ok - mu)).where(nz)
    row['nw_t'] = nw_t(x.dropna().values, h)
    N = len(s_ok); sv = s_ok.values; fv = f_ok.values - mu
    sims = []
    for _ in range(1000):
        k = int(RNG.integers(60, max(61, N - 60))); ss = np.roll(sv, k); m = ss != 0
        sims.append((ss[m] * fv[m]).mean() if m.any() else 0.0)
    row['p_shuffle'] = float((np.array(sims) >= st).mean())
    # non-overlapping raw-direction hit rate and base rate
    idx = np.arange(0, N, h); si, fi = sv[idx], f_ok.values[idx]; m = si != 0
    row['hit_nonoverlap'] = float((np.sign(si[m]) == np.sign(fi[m])).mean()) if m.sum() >= 5 else np.nan
    row['n_nonoverlap'] = int(m.sum())
    row['base_up'] = float((f_ok.values[idx] > 0).mean())
    # +1 / 0 / -1 conditional means
    for v, lab in [(1, 'mean_pos'), (0, 'mean_zero'), (-1, 'mean_neg')]:
        mm = s_ok == v; row[lab] = float(f_ok[mm].mean()) if mm.sum() >= 5 else np.nan
    # subperiods
    signs = []
    for a, b in PERIODS:
        sp = s_ok[a:b]; fp = f_ok[a:b]
        if (sp != 0).sum() >= 10:
            mu_p = fp.mean(); v = float((sp[sp != 0] * (fp[sp != 0] - mu_p)).mean()); row[f'stat_{a}_{b}'] = v; signs.append(np.sign(v))
    row['periods_positive'] = int(sum(1 for z in signs if z > 0)); row['periods_tested'] = len(signs)
    # episodes = runs of the same nonzero value
    runs = (s_ok != s_ok.shift()).cumsum(); row['episodes'] = int(s_ok[s_ok != 0].groupby(runs[s_ok != 0]).ngroups)
    return row


def lever(name, s, kind, y, cost_mult):
    """daily incremental return of the mapped lever; s known at t close, applied to y(t+1) (A: open-to-open) or y(t+2) (B, C)"""
    s = s.fillna(0)
    if kind == 'A':
        pos = 0.035 * np.sign(s); inc = pos * y.shift(-1).reindex(s.index); turn = pos.diff().abs()
    elif kind == 'B':
        pos = 0.2 * np.sign(s); inc = pos * y.reindex(s.index).shift(-2); turn = pos.diff().abs() * 2
    else:  # C: halve the overlay when bullish
        pos = -0.5 * (s > 0).astype(float); inc = pos * y.reindex(s.index).shift(-2); turn = pos.diff().abs() * 0.30 * 2
    inc = (inc - turn.fillna(0) * FEE * cost_mult).dropna()
    if len(inc) < 60: return None
    r35 = inc.rolling(35).sum().dropna(); act = (pos.reindex(inc.index) != 0).astype(float).rolling(35).max().reindex(r35.index) > 0
    extra = dict(w35_active_share=float(act.mean()), w35_hit_active=float((r35[act] > 0).mean()) if act.any() else np.nan,
                 w35_mean_active_bp=float(r35[act].mean() * 1e4) if act.any() else np.nan, w35_p10_active_bp=float(r35[act].quantile(0.1) * 1e4) if act.any() else np.nan)
    return extra | dict(signal=name, lever=kind, cost_mult=cost_mult, start=str(inc.index[0].date()), days=len(inc), ann_bp=float(inc.mean() * 252 * 1e4),
                w35_mean_bp=float(r35.mean() * 1e4), w35_p10_bp=float(r35.quantile(0.1) * 1e4), w35_hit=float((r35 > 0).mean()),
                switches=int((np.sign(s).diff().abs() > 0).sum()))


def main():
    idx = {c: pd.read_parquet(OUT / f'index_{c}.parquet') for c in CODES}
    sig = {c: pd.read_parquet(OUT / f'signals_{c}.parquet') for c in CODES}
    for c in CODES: sig[c]['S4'] = s4_series(c, sig[c].index)
    br = pd.read_parquet(OUT / 'breadth.parquet'); tac = pd.read_parquet(OUT / 'tactical.parquet')
    y2 = pd.read_parquet(OUT / 'y2_beta_spread.parquet').y2; y3 = pd.read_parquet(OUT / 'y3_overlay_excess.parquet').y3
    s500 = sig['000905'].copy()
    s500['S5'] = br.S5.reindex(s500.index)
    for k in ['S6a', 'S6b', 'S6c', 'S6', 'S6a_long']: s500[k] = tac[k].reindex(s500.index)
    s500['S6'] = np.sign(s500['S6'])
    comp = s500[['S2', 'S3', 'S4', 'S5']]
    s500['S7'] = np.sign(comp.sum(axis=1, min_count=4))
    rows = []
    # Y1: index-specific signals S1-S4 on each index; CSI500-only signals on CSI500
    for c, name in CODES.items():
        for h in H:
            f = fwd_open(idx[c], h)
            for k in ['S1', 'S2', 'S3', 'S4']:
                rows.append(test(f'{k}@{name}', sig[c][k], f, h, 'Y1'))
    for h in H:
        f = fwd_open(idx['000905'], h)
        for k in ['S5', 'S6a_long', 'S6a', 'S6b', 'S6c', 'S6', 'S7']:
            rows.append(test(f'{k}@中证500', s500[k], f, h, 'Y1'))
        f2 = fwd_daily(y2, s500.index, h); f3 = fwd_daily(y3, s500.index, h)
        for k in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6a_long', 'S6', 'S7']:
            rows.append(test(f'{k}@中证500', s500[k], f2, h, 'Y2'))
            rows.append(test(f'{k}@中证500', s500[k], f3, h, 'Y3'))
    T = pd.DataFrame(rows); T.to_csv(OUT / 'p2_tests.csv', index=False)
    # levers on CSI500 signals
    r_oo = idx['000905'].open.shift(-1) / idx['000905'].open - 1  # open(t+1)->open(t+2) attributed to t+1; lever() shifts by 1
    r_oo = (idx['000905'].open.shift(-1) / idx['000905'].open - 1)
    Lrows = []
    for k in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6a_long', 'S6', 'S7']:
        for cm in (1, 2):
            for kind, y in (('A', r_oo), ('B', y2), ('C', y3)):
                r = lever(k, s500[k], kind, y, cm)
                if r: Lrows.append(r)
    L = pd.DataFrame(Lrows); L.to_csv(OUT / 'p2_levers.csv', index=False)
    # gates (protocol section 5), CSI500 signals; G1 on Y1 (lever A) and Y2 (lever B) separately
    G = []
    for k in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6a_long', 'S6', 'S7']:
        for tgt, kind, p10_floor in (('Y1', 'A', -30), ('Y2', 'B', -60)):
            t = T[(T.signal == f'{k}@中证500') & (T.target == tgt) & (T.h.isin([20, 35]))].dropna(subset=['p_shuffle'])
            best = t.sort_values('p_shuffle').head(1)
            g1 = bool(len(best) and best.p_shuffle.iloc[0] < 0.10 and best.stat.iloc[0] > 0)
            g2 = bool(len(best) and best.periods_positive.iloc[0] >= 2)
            lv = L[(L.signal == k) & (L.lever == kind) & (L.cost_mult == 2)]
            g3 = bool(len(lv) and lv.ann_bp.iloc[0] > 0)
            g4 = bool(len(lv) and lv.w35_hit_active.iloc[0] >= 0.55 and lv.w35_p10_active_bp.iloc[0] >= p10_floor)
            G.append(dict(signal=k, target=tgt, lever=kind, best_h=int(best.h.iloc[0]) if len(best) else None, best_p=float(best.p_shuffle.iloc[0]) if len(best) else None,
                          G1_p_lt_0p10=g1, G2_periods=g2, G3_net_positive=g3, G4_w35=g4, PASS=g1 and g2 and g3 and g4))
    G = pd.DataFrame(G); G.to_csv(OUT / 'p2_gates.csv', index=False)
    # multiple-testing tally
    m = T[T.h.isin([20, 35])].dropna(subset=['p_shuffle'])
    tally = m.groupby('target').apply(lambda x: pd.Series(dict(tests=len(x), p_lt_0p10=int((x.p_shuffle < 0.10).sum()), expected=round(0.10 * len(x), 1), p_lt_0p01=int((x.p_shuffle < 0.01).sum()))))
    tally.to_csv(OUT / 'p2_tally.csv')
    # current values (latest bar) of every CSI500 signal
    now = s500.ffill().iloc[-1][['S1', 'S2', 'S3', 'S4', 'S5', 'S6a_long', 'S6', 'S7', 'p_extend', 'eff']].to_dict()
    pd.Series(now, name=str(s500.index[-1].date())).to_csv(OUT / 'p2_current.csv')
    return T, L


if __name__ == '__main__':
    T, L = main()
    pd.set_option('display.width', 250)
    key = T[(T.h.isin([20, 35]))].copy()
    print(key[['signal', 'target', 'h', 'n_nonzero', 'episodes', 'stat', 'nw_t', 'p_shuffle', 'hit_nonoverlap', 'base_up', 'periods_positive', 'periods_tested']].round(4).to_string(index=False))
    print(L[L.cost_mult == 2][['signal', 'lever', 'ann_bp', 'w35_mean_bp', 'w35_active_share', 'w35_hit_active', 'w35_mean_active_bp', 'w35_p10_active_bp', 'switches']].round(2).to_string(index=False))
    print(pd.read_csv(OUT / 'p2_gates.csv').to_string(index=False)); print(pd.read_csv(OUT / 'p2_tally.csv').to_string(index=False)); print(pd.read_csv(OUT / 'p2_current.csv').to_string(index=False))
