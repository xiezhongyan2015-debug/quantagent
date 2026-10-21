"""Part 1: RankIC decomposition of every arm/head, raw vs neutralized vs account score.

Signal T = 15:00 of signal date (calendar index c).  Forward returns:
  oo1 = O[c+2]/O[c+1]-1, oo5 = O[c+6]/O[c+1]-1, oo20 = O[c+21]/O[c+1]-1   (open-to-open from T+1 open)
  vw1 = V[c+2]/V[c+1]-1, vw5 = V[c+5]/V[c+1]-1, vw20 = V[c+20]/V[c+1]-1   (VWAP; 5/20 = exact training label span)
Each return uses iFinD hfq when both endpoints exist there, else parquet hfq
(post-exit prices); '_if' variants are iFinD-only (= what training labels see).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
import common as C

scores, axes = C.scores_cached()
px = C.price_panels()
cal = pd.Index(px['calendar'].astype(str))
dates = axes['dates']; members = axes['members']; ix = axes['risk_index']
crow = cal.get_indexer(dates); assert (crow >= 0).all()
years = np.array([int(d[:4]) for d in dates])
risk = C.risk_context()
K = len(cal)


def fwd(field, a, b, ifind_only=False):
    out = np.full((len(dates), len(axes['symbols'])), np.nan)
    for d, c in enumerate(crow):
        if c + b >= K:
            continue
        ra = px['if_' + field][c + b] / px['if_' + field][c + a] - 1
        if not ifind_only:
            rb = px['pq_' + field][c + b] / px['pq_' + field][c + a] - 1
            ra = np.where(np.isfinite(ra), ra, rb)
        out[d] = ra
    return out


TARGETS = {'oo1': ('open', 1, 2), 'oo5': ('open', 1, 6), 'oo20': ('open', 1, 21),
           'vw1': ('vwap', 1, 2), 'vw5': ('vwap', 1, 5), 'vw20': ('vwap', 1, 20)}
R = {k: fwd(*v) for k, v in TARGETS.items()}
R_if = {k: fwd(*v, ifind_only=True) for k, v in TARGETS.items() if k in ('vw5', 'vw20', 'oo1')}
for k in R:
    R[k] = np.where(members, R[k], np.nan)

# PCF reference weights (P0 plans: execution row t uses signal date t-1) on the 1178 axis
p0 = np.load(C.PILOT / 'P0_plans.npz', allow_pickle=False)
ref = np.full(members.shape, np.nan)
srow = pd.Index(dates).get_indexer(p0['signal_dates'].astype(str))
col = pd.Index(axes['symbols']).get_indexer(p0['symbols'])
assert (srow >= 0).all()
cov = col >= 0
ref[np.ix_(srow, col[cov])] = p0['target'][:, cov]   # weighted IC: covered names only


def summarize(ic, mask):
    v = ic[mask & np.isfinite(ic)]
    if len(v) < 5:
        return np.nan, np.nan, len(v)
    return float(v.mean()), float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))), len(v)


rows = []
KINDS = ('raw', 'neu', 'ema0.2')
for (arm, head), d in scores.items():
    for kind in KINDS:
        s = d[kind]
        for tgt, r in R.items():
            ic = C.rank_ic(s, r)
            icw = C.rank_ic(s, r, ref) if tgt in ('oo1', 'oo20', 'vw20') else None
            for y in (2024, 2025, 2026):
                m, t, n = summarize(ic, years == y)
                row = dict(arm=arm, head=head, kind=kind, target=tgt, year=y, rank_ic=m, t=t, days=n)
                if icw is not None and y >= 2025:
                    row['pcf_weighted_rank_ic'] = summarize(icw, years == y)[0]
                rows.append(row)
        if kind == 'raw':
            for tgt, r in R_if.items():
                ic = C.rank_ic(s, np.where(members, r, np.nan))
                for y in (2024, 2025, 2026):
                    m, t, n = summarize(ic, years == y)
                    rows.append(dict(arm=arm, head=head, kind=kind, target=tgt + '_ifind_only', year=y, rank_ic=m, t=t, days=n))
    print('ic done', arm, head, flush=True)
ic_tab = pd.DataFrame(rows)
ic_tab.to_csv(C.RESULTS / 'part1_rank_ic_long.csv', index=False)

# IC decay: account score at T vs single-day open-to-open slice k days later
decay = []
slices = [fwd('open', 1 + k, 2 + k) for k in range(20)]
for (arm, head), d in scores.items():
    for kind in ('neu', 'ema0.2'):
        for k, r in enumerate(slices):
            ic = C.rank_ic(d[kind], np.where(members, r, np.nan))
            for y in (2025, 2026):
                decay.append(dict(arm=arm, head=head, kind=kind, lag=k, year=y, rank_ic=summarize(ic, years == y)[0]))
pd.DataFrame(decay).to_csv(C.RESULTS / 'part1_ic_decay_daily_slices.csv', index=False)
print('decay done', flush=True)

# exposures, industry share, SHORT correlation, autocorrelation
expo_rows = []
for (arm, head), d in scores.items():
    for kind in KINDS:
        s = d[kind]
        for y in (2025, 2026):
            sel = np.flatnonzero(years == y)
            row = dict(arm=arm, head=head, kind=kind, year=y)
            for e in C.EXPOSURES:
                row['corr_' + e] = float(np.nanmean(C.rank_ic(s[sel], risk[e][ix[sel]])))
            r2 = []
            for i in sel:
                ok = np.isfinite(s[i]); ind = risk['industry'][ix[i]][ok]; v = s[i][ok]
                g = pd.Series(v).groupby(ind).transform('mean').to_numpy()
                r2.append(np.var(g) / np.var(v))
            row['industry_r2'] = float(np.mean(r2))
            suffix = arm.split('_')[1]
            for sh in ('head3', 'head4'):
                row['corr_SHORT_' + sh] = float(np.nanmean(C.rank_ic(s[sel], scores[('SHORT_' + suffix, sh)][kind][sel])))
            for lag in (1, 5, 20):
                row[f'autocorr_lag{lag}'] = float(np.nanmean(C.rank_ic(s[sel], s[sel - lag])))
            expo_rows.append(row)
pd.DataFrame(expo_rows).to_csv(C.RESULTS / 'part1_exposures_corr.csv', index=False)
print('exposures done', flush=True)

# Bridge IC -> money: ideal gross tilt alpha (no cost, perfect fill, k=0.5 PCF tilt)
def tilt_active(w, z):
    m = np.where(np.isfinite(z), 1 + .5 * np.clip(z, -2, 2), 1.)
    w0 = np.nan_to_num(w); t = w0 * m
    return t / t.sum() * w0.sum() - w0 if t.sum() > 0 else np.zeros_like(w0)


alpha_rows = []
# 798-axis: reference rows by signal date, parquet hfq returns (covers names outside the model axis)
pq = pd.read_parquet(C.PARQUET, columns=['date', 'symbol', 'open_adjusted', 'volume', 'amount', 'factor'])
pq = pq[pq.date >= '2024-01-02']; pq['amount'] = pd.to_numeric(pq['amount'], errors='coerce')
pq['vwap'] = np.where((pq.volume > 0) & (pq.amount > 0), pq.amount / pq.volume * pq.factor, np.nan)
sym798 = pd.Index(p0['symbols'])
def pq_panel(c):
    a = np.full((K, len(sym798)), np.nan); r = cal.get_indexer(pq.date); q = sym798.get_indexer(pq.symbol); ok = (r >= 0) & (q >= 0)
    a[r[ok], q[ok]] = pq[c].to_numpy(float)[ok]; a[~(a > 0)] = np.nan; return a
O8, V8 = pq_panel('open_adjusted'), pq_panel('vwap')
V8 = np.where(np.isfinite(V8), V8, O8)   # names without daily VWAP (outside model axis): open fallback, as in the replay
def fwd8(P, a, b):
    out = np.full((len(dates), len(sym798)), np.nan)
    for d_, c in enumerate(crow):
        if c + b < K: out[d_] = P[c + b] / P[c + a] - 1
    return out
hold = {'oo_hold1': (fwd8(O8, 1, 2), 1), 'oo_hold5': (fwd8(O8, 1, 6), 5), 'oo_hold20': (fwd8(O8, 1, 21), 20),
        'vw_hold1': (fwd8(V8, 1, 2), 1), 'vw_hold5': (fwd8(V8, 1, 6), 5), 'vw_hold20': (fwd8(V8, 1, 21), 20)}
ref8 = np.full((len(dates), len(sym798)), np.nan); ref8[srow] = p0['target']
valid = np.isfinite(ref8).all(axis=1)
for (arm, head), d in scores.items():
    for kind in ('raw', 'neu', 'ema0.2'):
        z8 = np.full(ref8.shape, np.nan); z8[:, cov] = d[kind][:, col[cov]]
        act = np.zeros(ref8.shape)
        for i in np.flatnonzero(valid):
            act[i] = tilt_active(ref8[i], z8[i])
        for name, (r, n) in hold.items():
            per_day = np.nansum(act * np.nan_to_num(r), axis=1) / n
            for y in (2025, 2026):
                sel = (years == y) & valid & np.isfinite(r).any(axis=1)
                # last rows lack full-horizon returns; require >=90% of reference weight priced
                priced = np.array([np.nansum(np.where(np.isfinite(r[i]), ref8[i], 0)) / np.nansum(ref8[i]) if valid[i] else 0 for i in range(len(r))])
                sel &= priced > .9
                alpha_rows.append(dict(arm=arm, head=head, kind=kind, horizon=name, year=y,
                                       ideal_gross_alpha_annual=float(per_day[sel].mean() * 252), days=int(sel.sum())))
        # turnover of the active book (one-way, fraction of NAV per day)
        dact = np.abs(np.diff(act[valid], axis=0)).sum(axis=1) / 2
        alpha_rows.append(dict(arm=arm, head=head, kind=kind, horizon='active_oneway_turnover_per_day', year=0,
                               ideal_gross_alpha_annual=float(dact.mean()), days=int(len(dact))))
pd.DataFrame(alpha_rows).to_csv(C.RESULTS / 'part1_ideal_tilt_alpha.csv', index=False)
print('alpha done', flush=True)
