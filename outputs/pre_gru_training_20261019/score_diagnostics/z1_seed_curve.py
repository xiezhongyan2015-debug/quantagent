"""Z1: seed-count curve for A1 (15 seeds, SWA, four training slots) and A4 (5 seeds).

For each window and N, sample up to 200 seed subsets (all subsets when C(K,N) <= 200),
combine like production (per-seed head4 z over all scored names -> seed mean), and compute at 15:00
within CSI500 members:
  raw_rankic      Spearman(seed-mean, y4)
  neu_rankic      Spearman(neutralize(seed-mean), y4)            [no EMA]
  covb            Cov_b(z(neutral), ranknormal(y4)), b = PIT month-end CSI500 weight, member-normalised
  ema_rankic / ema_covb   same after EMA 0.2 over the window's 15:00 sequence and member z (production final signal;
                  EMA restarts at the first test day of each window)
Outputs: z1_seed_curve.csv (subset distribution of window means), z1_a1_minus_a4.csv,
         z1_daily_full_ensemble.csv (daily metrics of the all-seed ensembles, used by Z5).
"""
import itertools, math, sys, time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import rankdata
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import *

NS = [1, 2, 3, 5, 8, 10, 15]
NSUB = 200
METRICS = ['raw_rankic', 'neu_rankic', 'covb', 'ema_rankic', 'ema_covb']


def runs():
    out = []
    for w in ['T2021', 'T2022']:
        out.append(('A1', w, sorted((LOCAL_DUAL / 'A1' / w).glob('control_seed*'))))
    for w in ['T2024', 'T2025', 'T2026']:
        out.append(('A1', w, sorted((SNAP / 'A1' / w).glob('control_seed*'))))
    for w in ['T2021', 'T2022', 'T2023', 'T2024', 'T2025', 'T2026']:
        out.append(('A4', w, sorted((SNAP / 'A4' / w).glob('control_seed*'))))
    # WF = production walk-forward control (gru_walkforward_20261010): four training slots, best checkpoint, no SWA,
    # i.e. the A1 'best' recipe with 5 different seeds -> a same-recipe replicate of A1-best.
    for w in ['T2021', 'T2022', 'T2023', 'T2024', 'T2025', 'T2026']:
        out.append(('WF', w, sorted((WALK / w).glob('control_seed*'))))
    return out


def subsets(K, N, rng):
    if math.comb(K, N) <= NSUB:
        return [list(c) for c in itertools.combinations(range(K), N)]
    seen = set(); out = []
    while len(out) < NSUB:
        c = tuple(sorted(rng.choice(K, N, replace=False)))
        if c not in seen:
            seen.add(c); out.append(list(c))
    return out


def evaluate(ctx, ix, Z, subs):
    """Z: [K, D, U] per-seed z. subs: list of index lists. Returns metrics [M, nsub, D] and day info."""
    K, D, U = Z.shape
    W = np.zeros((len(subs), K))
    for r, c in enumerate(subs):
        W[r, c] = 1. / len(c)
    fin = np.isfinite(Z)
    assert (fin == fin[:1]).all(), 'seed finiteness differs'
    fin = fin[0]
    res = np.full((len(METRICS), len(subs), D), np.nan)
    last = None  # EMA state per seed [K, U]
    for d in range(D):
        i = ix[d]
        m = np.asarray(ctx.members[i])
        e = ctx.exposure(i)
        y = ctx.y4(i)
        wraw, _ = ctx.weights(ctx.dates[i])
        okn = m & fin[d] & np.isfinite(e).all(1)
        # per-seed neutral residual (linear => subset mean of residuals == residual of subset mean)
        R = np.full((K, U), np.nan)
        if okn.sum() >= 100:
            Q = basis(e, okn)
            R[:, okn] = fast_residual(Z[:, d, okn], Q)
        # EMA (production rule, per seed; linear and NaN pattern shared across seeds)
        E = R.copy()
        if last is not None:
            both = np.isfinite(E) & np.isfinite(last)
            E = np.where(both, .2 * E + .8 * last, E)
        last = E.copy()
        fy = np.isfinite(y)
        # raw
        s_raw = m & fin[d] & fy
        if s_raw.sum() >= 100:
            A = W @ Z[:, d, s_raw]
            res[0, :, d] = row_spearman(A, rankdata(y[s_raw]))
        g_all = ranknormal(y)  # y already member-masked
        for k0, X in [(1, R), (3, E)]:
            okx = np.isfinite(X[0]) & m
            sel = okx & fy
            if sel.sum() < 100:
                continue
            A = W @ X[:, sel]
            res[k0, :, d] = row_spearman(A, rankdata(y[sel]))
            # covb: z over all members with finite signal (production final z), then weights on sel
            Aall = W @ X[:, okx]
            Aall = (Aall - Aall.mean(1, keepdims=True)) / Aall.std(1, keepdims=True)
            pos = np.flatnonzero(okx)
            inner = np.isin(pos, np.flatnonzero(sel))
            b = wraw[sel]
            if b.sum() <= 0:
                continue
            b = b / b.sum()
            res[k0 + 1, :, d] = row_cov_b(Aall[:, inner], g_all[sel], b)
    return res


def main():
    t0 = time.time()
    ctx = Context()
    print('self_check max abs diff', self_check(ctx), flush=True)
    rng = np.random.default_rng(20261019)
    rows, daily_rows, subset_store = [], [], {}
    for model, win, dirs in runs():
        variants = ['best', 'swa'] if model == 'A1' else ['best']
        for var in variants:
            fname = 'test_scores_swa.npz' if var == 'swa' else 'test_scores.npz'
            paths = [p / fname for p in dirs if (p / fname).exists()]
            seeds = [p.parent.name.replace('control_seed', '') for p in paths]
            ref = None; Z = []
            for p in paths:
                ix, s = load_head4_15(p, ctx)
                if ref is None: ref = ix
                assert np.array_equal(ref, ix)
                Z.append(seed_z(s))
            Z = np.asarray(Z); K = len(Z)
            Ns = [n for n in NS if n <= K]
            if K not in Ns: Ns.append(K)
            allsubs, tags = [], []
            for N in Ns:
                ss = subsets(K, N, rng); allsubs += ss; tags += [N] * len(ss)
            tags = np.asarray(tags)
            res = evaluate(ctx, ref, Z, allsubs)
            means = np.nanmean(res, axis=2)  # [M, nsub]
            dates = ctx.dates[ref]
            for N in Ns:
                sel = tags == N
                row = dict(model=model, window=win, variant=var, K_available=K, N=N, n_subsets=int(sel.sum()), days=len(ref),
                           first=dates[0], last=dates[-1], seeds=' '.join(seeds))
                for k, name in enumerate(METRICS):
                    v = means[k, sel]
                    row[f'{name}_mean'] = float(np.mean(v)); row[f'{name}_p05'] = float(np.quantile(v, .05)); row[f'{name}_p95'] = float(np.quantile(v, .95))
                    row[f'{name}_sd'] = float(np.std(v))
                rows.append(row)
                subset_store[(model, win, var, N)] = means[:, sel]
            full = np.flatnonzero(tags == K)[0]
            for d in range(len(ref)):
                daily_rows.append(dict(model=model, window=win, variant=var, K=K, date=dates[d], **{m: res[k, full, d] for k, m in enumerate(METRICS)}))
            print(model, win, var, K, 'done', round(time.time() - t0), 's', {m: round(rows[-1][f'{m}_mean'], 4) for m in METRICS}, flush=True)
    curve = pd.DataFrame(rows)
    curve.to_csv(OUT / 'z1_seed_curve.csv', index=False)
    daily = pd.DataFrame(daily_rows)
    daily.to_csv(OUT / 'z1_daily_full_ensemble.csv', index=False)
    # A1 vs A4 decomposition per window
    dec = []
    for win in ['T2021', 'T2022', 'T2024', 'T2025', 'T2026']:
        a4 = subset_store[('A4', win, 'best', 5)][:, 0]
        for var in ['best', 'swa']:
            K = int(curve[(curve.model == 'A1') & (curve.window == win) & (curve.variant == var)].K_available.iloc[0])
            a1_5 = subset_store[('A1', win, var, 5)]
            a1_full = subset_store[('A1', win, var, K)][:, 0]
            a1_1 = subset_store[('A1', win, var, 1)]; a4_1 = subset_store[('A4', win, 'best', 1)]
            wf = subset_store[('WF', win, 'best', 5)][:, 0]
            for k, mname in enumerate(METRICS):
                d5 = a1_5[k] - a4[k]
                dwf = a1_5[k] - wf[k]
                dec.append(dict(window=win, a1_variant=var, metric=mname, A1_K=K, A1_full=a1_full[k], A4_5seed=a4[k],
                                total_gap_full_minus_A4=a1_full[k] - a4[k],
                                A1_5subset_mean=float(a1_5[k].mean()), A1_5subset_p05=float(np.quantile(a1_5[k], .05)), A1_5subset_p95=float(np.quantile(a1_5[k], .95)),
                                gap_5v5_mean=float(d5.mean()), gap_5v5_p05=float(np.quantile(d5, .05)), gap_5v5_p95=float(np.quantile(d5, .95)),
                                share_5v5_below_0=float((d5 <= 0).mean()),
                                seed_count_part=a1_full[k] - float(a1_5[k].mean()),
                                seed_count_share_of_gap=(a1_full[k] - float(a1_5[k].mean())) / (a1_full[k] - a4[k]) if abs(a1_full[k] - a4[k]) > 1e-12 else np.nan,
                                A1_single_seed_mean=float(a1_1[k].mean()), A4_single_seed_mean=float(a4_1[k].mean()),
                                ensemble_gain_A1_5=float(a1_5[k].mean() - a1_1[k].mean()), ensemble_gain_A4_5=float(a4[k] - a4_1[k].mean()),
                                WF_5seed=wf[k], WF_minus_A4=wf[k] - a4[k],
                                A1_5subset_minus_WF_mean=float(dwf.mean()), A1_5subset_minus_WF_p05=float(np.quantile(dwf, .05)), A1_5subset_minus_WF_p95=float(np.quantile(dwf, .95))))
    pd.DataFrame(dec).to_csv(OUT / 'z1_a1_minus_a4.csv', index=False)
    print('total', round(time.time() - t0), 's')


if __name__ == '__main__':
    main()
