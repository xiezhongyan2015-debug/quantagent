"""Z4: hourly GRU (walk-forward control, 5 seeds, head y4, 15:00) versus pure daily models.

Daily models (outputs/gru_dualplatform_20261011/B): LightGBM T2021–T2026 (5 seeds) and daily GRU T2021–T2023 (seed 11).
Daily-panel row d is stamped d 15:00, but every daily feature is lagged one session (gru_research/slow_features.daily_state
and the gp factors use .shift(1); PIT market fields become available the session after their query date), so the row-d score only
uses information through the close of d-1. Its label is log(open[d+1+h]/open[d+1]); head 0 (h=1) is open d+1 -> open d+2,
the same holding interval as hourly 15:00 y4 on day d (execution d+1 09:31, label end d+2 09:36).
  primary   'label_aligned' : daily row d  <-> hourly 15:00 of d (same target; daily info one day older)
  sensitivity 'info_aligned': daily row d+1 <-> hourly 15:00 of d (both use information through close d; daily target shifted)
Per-seed z within CSI500 members at hourly 15:00 d, seed mean. Neutralisation = production neutralize (vol/turn ranks, log cap, beta60).
Incremental IC: cross-sectional OLS of hourly signal on [1, daily, 4 exposures] (production design), residual vs y4 Spearman.
Outputs: z4_hourly_vs_daily.csv (yearly), z4_daily.csv (daily rows).
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import *

B = LOCAL_DUAL / 'B'
PANEL = LOCAL_DUAL / 'daily_panel'


def spearman(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 100:
        return np.nan
    return float(np.corrcoef(rankdata(a[ok]), rankdata(b[ok]))[0, 1])


def daily_scores(kind, head):
    """date -> [U_hourly] seed-mean of per-seed scores mapped onto the hourly symbol axis (raw, not z)."""
    dsym = json.loads((PANEL / 'symbols.json').read_text())
    hsym = json.loads((DATA / 'symbols.json').read_text())
    pos = {s: k for k, s in enumerate(dsym)}
    take = np.array([pos[s] for s in hsym])
    dtimes = np.load(PANEL / 'times.npy')
    out = {}
    years = range(2021, 2027) if kind == 'LightGBM' else range(2021, 2024)
    for y in years:
        seeds = sorted((B / kind / f'T{y}').glob('seed*'))
        per = []
        ref = None
        for sd in seeds:
            a = np.load(sd / 'test_scores.npz')
            assert np.array_equal(dtimes[a['indices']], a['times'])
            if ref is None: ref = a['times']
            assert np.array_equal(ref, a['times'])
            per.append(a['scores'][:, take, head].astype(float))
        dates = pd.to_datetime(ref, utc=True).tz_convert(TZ).strftime('%Y-%m-%d')
        for k, d in enumerate(dates):
            out[d] = [p[k] for p in per]
    return out, len(seeds)


def hourly_scores(ctx):
    out = {}
    for y in range(2021, 2027):
        per = []; ref = None
        for seed in [11, 22, 33, 44, 55]:
            ix, s = load_head4_15(WALK / f'T{y}' / f'control_seed{seed}' / 'test_scores.npz', ctx)
            if ref is None: ref = ix
            assert np.array_equal(ref, ix)
            per.append(seed_z(s))
        raw = np.mean(per, axis=0)
        for k, i in enumerate(ref):
            out[ctx.dates[i]] = (i, raw[k])
    return out


def main():
    ctx = Context()
    hourly = hourly_scores(ctx)
    hdates = sorted(hourly)
    hnext = {d: hdates[k + 1] for k, d in enumerate(hdates[:-1])}
    rows = []
    for kind in ['LightGBM', 'GRU']:
        for head in [0, 3]:
            dmap, nseed = daily_scores(kind, head)
            ddates = sorted(dmap)
            nxt = {d: ddates[k + 1] for k, d in enumerate(ddates[:-1])}
            for align in ['label_aligned', 'info_aligned']:
                for d in hdates:
                    dd = d if align == 'label_aligned' else nxt.get(d)
                    if dd is None or dd not in dmap:
                        continue
                    if align == 'info_aligned' and hnext.get(d) != dd:
                        continue  # the next daily session must be the next hourly session
                    i, h = hourly[d]
                    m = np.asarray(ctx.members[i])
                    dz = np.mean([zscore(np.where(m, s, np.nan)) for s in dmap[dd]], axis=0)
                    hz = zscore(np.where(m, h, np.nan))
                    e = ctx.exposure(i); y = ctx.y4(i)
                    ok = m & np.isfinite(hz) & np.isfinite(dz) & np.isfinite(e).all(1)
                    if ok.sum() < 100:
                        continue
                    X = design(e, ok)
                    Q = np.linalg.qr(X)[0]
                    hn = np.full(ctx.U, np.nan); dn = np.full(ctx.U, np.nan)
                    hn[ok] = fast_residual(hz[ok], Q); dn[ok] = fast_residual(dz[ok], Q)
                    X2 = np.c_[X, zscore(dz[ok])]
                    Q2 = np.linalg.qr(X2)[0]
                    hinc = np.full(ctx.U, np.nan); dinc = np.full(ctx.U, np.nan)
                    hinc[ok] = fast_residual(hz[ok], Q2)
                    Q3 = np.linalg.qr(np.c_[X, hz[ok]])[0]
                    dinc[ok] = fast_residual(dz[ok], Q3)
                    hh = np.where(ok, hz, np.nan); ddv = np.where(ok, dz, np.nan)
                    rows.append(dict(daily_model=kind, daily_head=f'h{[1,2,3,5][head]}', align=align, daily_seeds=nseed,
                                     date=d, daily_row_date=dd, year=int(d[:4]), n=int(ok.sum()),
                                     corr_raw=spearman(hh, ddv), corr_neutral=spearman(hn, dn),
                                     ic_hourly_raw=spearman(hh, y), ic_hourly_neutral=spearman(hn, y),
                                     ic_daily_raw=spearman(ddv, y), ic_daily_neutral=spearman(dn, y),
                                     ic_hourly_incremental=spearman(hinc, y), ic_daily_incremental=spearman(dinc, y)))
                print(kind, head, align, len(rows), flush=True)
    daily = pd.DataFrame(rows)
    daily.to_csv(OUT / 'z4_daily.csv', index=False)
    cols = ['corr_raw', 'corr_neutral', 'ic_hourly_raw', 'ic_hourly_neutral', 'ic_daily_raw', 'ic_daily_neutral', 'ic_hourly_incremental', 'ic_daily_incremental']
    summ = []
    for key, g in daily.groupby(['daily_model', 'daily_head', 'align']):
        for yr, gg in list(g.groupby('year')) + [('all', g)]:
            r = dict(daily_model=key[0], daily_head=key[1], align=key[2], year=yr, days=len(gg), mean_n=float(gg.n.mean()))
            for c in cols:
                r[c] = float(gg[c].mean())
            for c in ['ic_hourly_neutral', 'ic_hourly_incremental', 'ic_daily_incremental']:
                t, lag, n = newey_west_t(gg[c].to_numpy())
                r[c + '_nw_t'] = t
            r['nw_lag'] = lag
            summ.append(r)
    pd.DataFrame(summ).to_csv(OUT / 'z4_hourly_vs_daily.csv', index=False)
    print(pd.DataFrame(summ)[['daily_model', 'daily_head', 'align', 'year', 'days', 'corr_raw', 'corr_neutral', 'ic_hourly_neutral', 'ic_hourly_incremental', 'ic_hourly_incremental_nw_t', 'ic_daily_neutral', 'ic_daily_incremental']].round(4).to_string())


if __name__ == '__main__':
    main()
