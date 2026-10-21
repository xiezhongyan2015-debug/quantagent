"""Isolate the skeleton-weighting effect: same k=0.5 tilt on an equal-weight member skeleton,
and RankIC inside PCF-heavy vs PCF-light names (open-to-open, merged prices)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
import common as C

scores, axes = C.scores_cached()
px = C.price_panels()
cal = pd.Index(px['calendar'].astype(str)); K = len(cal)
dates = axes['dates']; M = axes['members']; crow = cal.get_indexer(dates)
years = np.array([int(d[:4]) for d in dates])


def fwd(a, b):
    out = np.full(M.shape, np.nan)
    for d, c in enumerate(crow):
        if c + b < K:
            r = px['if_open'][c + b] / px['if_open'][c + a] - 1
            out[d] = np.where(np.isfinite(r), r, px['pq_open'][c + b] / px['pq_open'][c + a] - 1)
    return np.where(M, out, np.nan)


R1, R20 = fwd(1, 2), fwd(1, 21)
p0 = np.load(C.PILOT / 'P0_plans.npz', allow_pickle=False)
srow = pd.Index(dates).get_indexer(p0['signal_dates'].astype(str))
col = pd.Index(axes['symbols']).get_indexer(p0['symbols']); cov = col >= 0
pcf = np.full(M.shape, np.nan); pcf[np.ix_(srow, col[cov])] = p0['target'][:, cov]

rows = []
for (arm, head), d in scores.items():
    for kind in ('neu', 'ema0.2'):
        s = d[kind]
        for name, r, n in (('oo_hold1', R1, 1), ('oo_hold20', R20, 20)):
            ew = []
            for i in srow:
                ok = M[i] & np.isfinite(s[i]) & np.isfinite(r[i])
                if ok.sum() < 100 or (n == 20 and np.isfinite(r[i]).sum() < .9 * M[i].sum()):
                    ew.append(np.nan); continue
                w = np.full(ok.sum(), .98 / ok.sum()); m = 1 + .5 * np.clip(s[i, ok], -2, 2)
                t = w * m; t = t / t.sum() * w.sum()
                ew.append(((t - w) * r[i, ok]).sum() / n)
            ew = np.array(ew); yr = years[srow]
            for y in (2025, 2026):
                rows.append(dict(arm=arm, head=head, kind=kind, horizon=name, year=y, skeleton='equal_weight_members',
                                 ideal_gross_alpha_annual=float(np.nanmean(ew[yr == y]) * 252)))
        # IC within PCF-heavy (top third of covered PCF weight) vs light names
        for name, r in (('oo1', R1), ('oo20', R20)):
            heavy, light = [], []
            for i in srow:
                w = pcf[i]; ok = np.isfinite(w) & (w > 0) & np.isfinite(s[i]) & np.isfinite(r[i])
                if ok.sum() < 60:
                    heavy.append(np.nan); light.append(np.nan); continue
                cut = np.quantile(w[ok], 2 / 3)
                for bucket, sel in ((heavy, ok & (w >= cut)), (light, ok & (w < cut))):
                    bucket.append(pd.Series(s[i, sel]).corr(pd.Series(r[i, sel]), method='spearman'))
            heavy, light = np.array(heavy), np.array(light); yr = years[srow]
            for y in (2025, 2026):
                rows.append(dict(arm=arm, head=head, kind=kind, horizon=name, year=y, skeleton='ic_pcf_heavy_top_third',
                                 ideal_gross_alpha_annual=float(np.nanmean(heavy[yr == y]))))
                rows.append(dict(arm=arm, head=head, kind=kind, horizon=name, year=y, skeleton='ic_pcf_light_rest',
                                 ideal_gross_alpha_annual=float(np.nanmean(light[yr == y]))))
out = pd.DataFrame(rows).rename(columns={'ideal_gross_alpha_annual': 'value'})
out.to_csv(C.RESULTS / 'part1_weighting_effect.csv', index=False)
print(out.pivot_table(index=['arm', 'head', 'kind', 'year'], columns=['skeleton', 'horizon'], values='value').round(4).to_string())
