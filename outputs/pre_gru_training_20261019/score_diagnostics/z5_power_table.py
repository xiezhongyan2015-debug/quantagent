"""Z5: power table. Minimum detectable paired difference (two-sided 5%, power 80%) for 34 / 60 / 250 trading days.

For a daily paired-difference series d_t with sd s and long-run sd s_LR:
  MDE(n) = (t_{0.975,n-1} + t_{0.80,n-1}) * s_LR / sqrt(n)
reported with three s_LR choices: iid (s), AR(1) (s*sqrt((1+rho1)/(1-rho1))), Newey-West (Bartlett, lag 5).
Daily RankIC sources: gru_deep_research_20261019_v2/daily_prediction_diagnostics.csv (RANK_NULL vs RANK_STATS),
z1_daily_full_ensemble.csv (A1 vs A4 all-seed ensembles), ensemble_replay_v1/daily_rankic.csv and
size_rank_replay_v1/live_construction/daily_rankic_y4.csv (live-construction signal sets).
Daily account sources: nav.pct_change (first build day dropped) of matched ledgers; the paired difference of two
accounts on the same skeleton equals the difference of their active returns. MDE in annualised percentage points (x250).
Output: z5_power_table.csv
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import t as tdist
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, OUT

HORIZONS = [34, 60, 250]
NW_LAG = 5
DIAG = ROOT / 'outputs/gru_deep_research_20261019_v2/daily_prediction_diagnostics.csv'
ENS = ROOT / 'outputs/pcf_overlay_20261012/ensemble_replay_v1'
SIZE = ROOT / 'outputs/pcf_overlay_20261012/size_rank_replay_v1'
PAIRED = ROOT / 'outputs/cluster_results_20261012_16_sync_20261017/snapshot/DATA/gru_rank_minute_20261015/accounts/paired_v1'


def stats(d):
    d = np.asarray(d, float); d = d[np.isfinite(d)]; n = len(d)
    e = d - d.mean(); s = d.std(ddof=1)
    rho1 = float((e[1:] @ e[:-1]) / (e @ e))
    rho5 = float(np.mean([(e[k:] @ e[:-k]) / (e @ e) for k in range(1, 6)]))
    lr = e @ e / n
    for k in range(1, NW_LAG + 1):
        lr += 2 * (1 - k / (NW_LAG + 1)) * (e[k:] @ e[:-k]) / n
    s_nw = np.sqrt(max(lr, 0) * n / (n - 1))
    s_ar = s * np.sqrt((1 + rho1) / (1 - rho1)) if rho1 < .99 else np.nan
    t_obs = d.mean() / (s_nw / np.sqrt(n))
    out = dict(n=n, mean=float(d.mean()), sd=float(s), rho1=rho1, rho_mean_lag1to5=rho5, sd_lr_ar1=float(s_ar), sd_lr_nw5=float(s_nw),
               lr_inflation_nw=float(s_nw / s), observed_t_nw=float(t_obs))
    for h in HORIZONS:
        c = tdist.ppf(.975, h - 1) + tdist.ppf(.80, h - 1)
        out[f'mde_{h}d_iid'] = c * s / np.sqrt(h)
        out[f'mde_{h}d_ar1'] = c * s_ar / np.sqrt(h)
        out[f'mde_{h}d_nw'] = c * s_nw / np.sqrt(h)
    return out


def main():
    rows = []
    def add(kind, source, pair, metric, period, d, scale=1.):
        r = dict(kind=kind, source=source, pair=pair, metric=metric, period=period)
        st = stats(d)
        for k, v in st.items():
            if k.startswith('mde') or k == 'mean':
                v = v * scale
            r[k] = v
        r['unit'] = 'RankIC / Cov (mean, sd, mde)' if scale == 1 else 'mean & mde: annualised pp (daily x250 x100); sd: daily decimal'
        if scale != 1:
            r['sd_daily_bp'] = st['sd'] * 1e4
            r['tracking_error_ann_pct'] = st['sd'] * np.sqrt(250) * 100
        rows.append(r)

    # ---- daily RankIC differences ----
    f = pd.read_csv(DIAG)
    f['year'] = f.date.str[:4]
    for m in ['raw_rankic', 'neutral_pre_ema_rankic', 'final_rankic']:
        d = f[f'RANK_STATS_{m}'] - f[f'RANK_NULL_{m}']
        for per, g in [('2024-2026', f)] + [(y, f[f.year == y]) for y in ['2024', '2025', '2026']]:
            add('rankic', 'deep_research_v2', 'RANK_STATS-RANK_NULL', m, per, d[g.index])
    z1 = pd.read_csv(OUT / 'z1_daily_full_ensemble.csv')
    pv = {k: g.set_index('date') for k, g in z1.groupby(['model', 'variant'])}
    for (a, b, lab) in [(('A1', 'best'), ('A4', 'best'), 'A1best(all)-A4(5)'), (('A1', 'swa'), ('A4', 'best'), 'A1swa(all)-A4(5)'),
                        (('A1', 'swa'), ('A1', 'best'), 'A1swa-A1best'), (('WF', 'best'), ('A4', 'best'), 'WF(5)-A4(5) [4 vs 2 train slots]'),
                        (('A1', 'best'), ('WF', 'best'), 'A1best(all)-WF(5) [same recipe replicate]')]:
        x, y = pv[a], pv[b]
        common = x.index.intersection(y.index)
        for m in ['raw_rankic', 'neu_rankic', 'covb', 'ema_rankic', 'ema_covb']:
            d = (x.loc[common, m] - y.loc[common, m])
            add('rankic' if 'rankic' in m else 'covb', 'z1', lab, m, 'pooled common windows', d)
            for yr in ['2024', '2025', '2026']:
                add('rankic' if 'rankic' in m else 'covb', 'z1', lab, m, yr, d[[c.startswith(yr) for c in d.index]])
    e = pd.read_csv(ENS / 'daily_rankic.csv')
    for a, b in [('his5', 'ctrl5'), ('all15', 'ctrl5'), ('wf10', 'ctrl5'), ('ten', 'ctrl5')]:
        add('rankic', 'ensemble_replay_v1 (final signal, y4)', f'{a}-{b}', 'final_rankic', '2025-2026', e[a] - e[b])
    s = pd.read_csv(SIZE / 'live_construction/daily_rankic_y4.csv')
    add('rankic', 'size_rank live_construction (y4)', 'SIZENPZ-OLDNPZ', 'final_rankic', '2025-2026', s.SIZENPZ - s.OLDNPZ)

    # ---- daily account return differences ----
    def ret(path):
        a = pd.read_csv(path).set_index('date')
        return a.nav.pct_change().iloc[1:]
    acct = [
        ('PCF skeleton 1000万 (paired_v1)', PAIRED, [('RANK_STATS', 'RANK_NULL'), ('RANK_NULL', 'OLD_RANK'), ('OLD_RANK', 'P0')]),
        ('PCF skeleton 1000万 (size_rank account)', SIZE / 'account', [('SIZE_RANK', 'OLD_RANK')]),
        ('live construction c5 blend (ensemble_replay_v1)', ENS, [('V5_P1_his5', 'V5_P1_ctrl5'), ('V5_P1_all15', 'V5_P1_ctrl5'), ('V5_P1_wf10', 'V5_P1_ctrl5'), ('V5_P1_ten', 'V5_P1_ctrl5'), ('V5_P1_all15', 'V5')]),
        ('live construction c5 blend (size_rank)', SIZE / 'live_construction', [('SIZENPZ', 'OLDNPZ'), ('tenA_SIZE', 'tenA_OLD')]),
    ]
    for src, base, pairs in acct:
        for a, b in pairs:
            x, y = ret(base / f'{a}_daily.csv'), ret(base / f'{b}_daily.csv')
            common = x.index.intersection(y.index)
            d = x[common] - y[common]
            add('account', src, f'{a}-{b}', 'daily return diff', f'{common[0]}..{common[-1]}', d.to_numpy(), scale=250 * 100)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / 'z5_power_table.csv', index=False)
    show = ['kind', 'source', 'pair', 'metric', 'period', 'n', 'mean', 'sd', 'rho1', 'lr_inflation_nw', 'observed_t_nw', 'mde_34d_nw', 'mde_60d_nw', 'mde_250d_nw', 'mde_34d_iid', 'mde_250d_iid', 'tracking_error_ann_pct']
    pd.set_option('display.width', 250)
    print(out[show].round(4).to_string())


if __name__ == '__main__':
    main()
