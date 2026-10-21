"""Label censoring: stocks that leave CSI500 lose their iFinD (members-only) VWAP label.

Checks (2024-2026 signal days):
  * how much of the member cross-section has a censored 5/20-day VWAP label, and why (exit vs suspension);
  * whether model scores on soon-to-exit stocks are systematically biased (event level, one value per exit);
  * what those stocks actually earned afterwards (parquet post-exit prices) and what the account's tilt on them earned.
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
dates = axes['dates']; M = axes['members']; ix = axes['risk_index']; sym = axes['symbols']
crow = cal.get_indexer(dates); K = len(cal); D = len(dates)
years = np.array([int(d[:4]) for d in dates])
risk = C.risk_context()


def fwd(field, a, b, ifind_only):
    out = np.full(M.shape, np.nan)
    for d, c in enumerate(crow):
        if c + b >= K:
            continue
        r = px['if_' + field][c + b] / px['if_' + field][c + a] - 1
        if not ifind_only:
            r = np.where(np.isfinite(r), r, px['pq_' + field][c + b] / px['pq_' + field][c + a] - 1)
        out[d] = r
    return out


rows = []
for h, (a, b) in {'vw5': (1, 5), 'vw20': (1, 20)}.items():
    lab_if = fwd('vwap', a, b, True); lab_all = fwd('vwap', a, b, False)
    n = b  # look-ahead sessions
    exit_h = np.zeros(M.shape, bool)
    for d in range(D - n):
        exit_h[d] = M[d] & (~M[d + 1:d + n + 1]).any(axis=0)
    usable = np.arange(D) < D - n
    for y in (2024, 2025, 2026):
        sel = usable & (years == y)
        mm = M[sel]; cen = mm & ~np.isfinite(lab_if[sel]); ex = exit_h[sel] & mm
        rows.append(dict(label=h, year=y, member_stock_days=int(mm.sum()),
                         censored_share=float(cen.sum() / mm.sum()),
                         censored_and_exiting_share_of_censored=float((cen & ex).sum() / max(cen.sum(), 1)),
                         exiting_share=float(ex.sum() / mm.sum()),
                         exiting_censored_share=float((cen & ex).sum() / max(ex.sum(), 1)),
                         censored_recovered_by_parquet=float((cen & np.isfinite(lab_all[sel])).sum() / max(cen.sum(), 1))))
cens_tab = pd.DataFrame(rows)
cens_tab.to_csv(C.RESULTS / 'part1_censoring_coverage.csv', index=False)

# Exit events: (stock, last member signal day e) followed by a non-member day
events = []
for i in range(M.shape[1]):
    m = M[:, i]
    for e in np.flatnonzero(m[:-1] & ~m[1:]):
        if e >= 20:
            events.append((i, e))
print('exit events', len(events))
lab20_all = fwd('vwap', 1, 20, False)   # post-exit prices from parquet
oo1 = fwd('open', 1, 2, False)
cap_rank = np.full(M.shape, np.nan)
for d in range(D):
    v = np.where(M[d], risk['log_cap'][ix[d]], np.nan); ok = np.isfinite(v)
    cap_rank[d, ok] = pd.Series(v[ok]).rank(pct=True).to_numpy()

ev_rows = []
for (arm, head), dd in scores.items():
    for kind in ('raw', 'neu', 'ema0.2'):
        s = dd[kind]
        # cross-sectional z of the score so different kinds are comparable
        z = (s - np.nanmean(s, axis=1, keepdims=True)) / np.nanstd(s, axis=1, keepdims=True)
        rel20 = lab20_all - np.nanmean(np.where(M, lab20_all, np.nan), axis=1, keepdims=True)
        for i, e in events:
            w = np.arange(e - 19, e + 1)              # the 20 signal days whose 20d VWAP label crosses the exit
            ev_rows.append(dict(arm=arm, head=head, kind=kind, symbol=sym[i], exit_signal_date=dates[e], year=int(dates[e][:4]),
                                mean_z_pre_exit=float(np.nanmean(z[w, i])),
                                mean_z_d_minus_40_to_21=float(np.nanmean(z[max(e - 39, 0):e - 19, i])),
                                cap_pct_at_exit=float(cap_rank[e, i]),
                                realized_rel_vw20_pre_exit=float(np.nanmean(rel20[w, i]))))
ev = pd.DataFrame(ev_rows)
ev['exit_type'] = np.where(ev.cap_pct_at_exit >= .7, 'large_cap(promotion-like)', np.where(ev.cap_pct_at_exit <= .3, 'small_cap(deletion-like)', 'mid'))
ev.to_csv(C.RESULTS / 'part1_censoring_exit_events.csv', index=False)


def tstat(v):
    v = v[np.isfinite(v)]
    return float(v.mean() / v.std(ddof=1) * np.sqrt(len(v))) if len(v) > 2 else np.nan


summ = []
groups = [((a, h, k, 'all'), g) for (a, h, k), g in ev.groupby(['arm', 'head', 'kind'])] + \
         [((a, h, k, t), g) for (a, h, k, t), g in ev.groupby(['arm', 'head', 'kind', 'exit_type'])]
for (arm, head, kind, typ), g in groups:
    summ.append(dict(arm=arm, head=head, kind=kind, exit_type=typ, events=len(g),
                     mean_z_pre_exit=g.mean_z_pre_exit.mean(), t_z=tstat(g.mean_z_pre_exit.to_numpy()),
                     mean_z_earlier_window=g.mean_z_d_minus_40_to_21.mean(),
                     realized_rel_vw20=g.realized_rel_vw20_pre_exit.mean(), t_realized=tstat(g.realized_rel_vw20_pre_exit.to_numpy()),
                     corr_z_realized=float(g[['mean_z_pre_exit', 'realized_rel_vw20_pre_exit']].corr().iloc[0, 1])))
pd.DataFrame(summ).to_csv(C.RESULTS / 'part1_censoring_bias_summary.csv', index=False)

# Account impact: active weight the k=0.5 PCF tilt places on to-be-exited names and its 1-day earnings
p0 = np.load(C.PILOT / 'P0_plans.npz', allow_pickle=False)
srow = pd.Index(dates).get_indexer(p0['signal_dates'].astype(str))
col = pd.Index(sym).get_indexer(p0['symbols']); cov = col >= 0
exit20 = np.zeros(M.shape, bool)
for d in range(D - 20):
    exit20[d] = M[d] & (~M[d + 1:d + 21]).any(axis=0)
imp = []
for (arm, head), dd in scores.items():
    s = dd['ema0.2']
    act_exit, contrib = [], []
    for r, d in enumerate(srow):
        w = p0['target'][r]; z = np.full(len(w), np.nan); z[cov] = s[d, col[cov]]
        m = np.where(np.isfinite(z), 1 + .5 * np.clip(z, -2, 2), 1.); t = w * m; t = t / t.sum() * w.sum()
        act = t - w; ex = np.zeros(len(w), bool); ex[cov] = exit20[d, col[cov]]
        rr = np.zeros(len(w)); rr[cov] = np.nan_to_num(oo1[d, col[cov]])
        act_exit.append(act[ex].sum()); contrib.append((act[ex] * rr[ex]).sum())
    act_exit = np.array(act_exit); contrib = np.array(contrib); yr = np.array([int(str(x)[:4]) for x in p0['signal_dates']])
    for y in (2025, 2026):
        imp.append(dict(arm=arm, head=head, year=y, mean_net_active_weight_on_exit20_names=float(act_exit[yr == y].mean()),
                        annual_alpha_from_exit20_names=float(contrib[yr == y].mean() * 252)))
pd.DataFrame(imp).to_csv(C.RESULTS / 'part1_censoring_account_impact.csv', index=False)
print(cens_tab.round(4).to_string())
print(pd.DataFrame(summ).query("kind=='neu'").round(3).to_string())
print(pd.DataFrame(imp).round(5).to_string())
