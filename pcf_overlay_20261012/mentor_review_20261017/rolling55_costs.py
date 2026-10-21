"""Rolling 55-session excess (one competition horizon, 2026-09-18 -> 2026-12-04 is ~55 sessions) and cost sensitivity for the
replayed arms, from their daily ledgers (same calendar, 1000万, base fee 2 bp per side on traded value, T+1 open).
Extra cost k bp is charged on the ledger's traded value (turnover = (buys+sells)/NAV) for the arm AND its reference.
Output: outputs/mentor_review_20261017/rolling55_costs.csv (+ _windows.csv with the non-overlapping windows)."""
import glob, os, numpy as np, pandas as pd
B = 'outputs/pcf_overlay_20261012'; OUT = 'outputs/mentor_review_20261017'
FILES = {'P0': f'{B}/optimizer_overlay_v4/P0_daily.csv', 'P1_blend': f'{B}/optimizer_overlay_v4/P1_blend_daily.csv', 'V5': f'{B}/optimizer_overlay_v4/V5_daily.csv',
         'V5_P1blend(live)': f'{B}/optimizer_overlay_v4/V5_P1blend_daily.csv', 'V5_O2blend': f'{B}/optimizer_overlay_v4/V5_O2blend_daily.csv',
         'I0_official': f'{B}/wide_pool_optimizer_v2b/I0_daily.csv', 'WV0_official_excl': f'{B}/wide_pool_optimizer_v2b/WV0_daily.csv', 'WV1_blend_official': f'{B}/wide_pool_optimizer_v2b/WV1_blend_daily.csv',
         'W_blend_optimizer': f'{B}/wide_pool_optimizer_v2/W_blend_daily.csv', 'V5_P1_all15(2025-)': f'{B}/ensemble_replay_v1/V5_P1_all15_daily.csv', 'V5_P1_ctrl5(2025-)': f'{B}/ensemble_replay_v1/V5_P1_ctrl5_daily.csv'}
for f in glob.glob(f'{B}/industry_refill_v1/*_daily.csv'): FILES['IND:' + os.path.basename(f)[:-10]] = f
D = {}
for k, f in FILES.items():
    if not os.path.exists(f): print('missing', k, f); continue
    d = pd.read_csv(f, parse_dates=['date']).set_index('date'); D[k] = d
h = D['P0'].H00905_nav; idx_ret = h.pct_change().fillna(0.0)
W = 55
def rolling_excess(ra, rb):
    la = np.log1p(ra); lb = np.log1p(rb); s = (la - lb).rolling(W).sum().dropna(); return np.expm1(s)
rows = []; windows = {}
def add(arm, versus, k):
    a = D[arm]; ra = a.nav.pct_change().fillna(0.0) - a.turnover * k / 1e4
    if versus == 'H00905': rb = idx_ret.reindex(ra.index).fillna(0.0)
    else: b = D[versus].reindex(ra.index); rb = b.nav.pct_change().fillna(0.0) - b.turnover * k / 1e4
    ex = rolling_excess(ra, rb); n = len(ra); ann = (np.prod(1 + ra) / np.prod(1 + rb)) ** (252 / n) - 1
    te = (np.log1p(ra) - np.log1p(rb)).std() * np.sqrt(252)
    nonov = ex.iloc[::W]; windows[(arm, versus, k)] = nonov
    rows.append(dict(arm=arm, versus=versus, extra_bp=k, days=n, start=ra.index[0].date(), ann_excess=ann, TE=te, IR=ann / te if te else np.nan, n_windows=len(ex), n_indep=len(nonov),
                     w55_mean=ex.mean(), w55_median=ex.median(), w55_p10=ex.quantile(.1), w55_p25=ex.quantile(.25), w55_min=ex.min(), w55_max=ex.max(), w55_share_pos=(ex > 0).mean(),
                     w55_nonoverlap_share_pos=(nonov > 0).mean(), w55_nonoverlap_min=nonov.min()))
PAIRS = [('V5_P1blend(live)', 'P0'), ('V5_P1blend(live)', 'H00905'), ('V5', 'P0'), ('P1_blend', 'P0'), ('V5_O2blend', 'P0'), ('P0', 'H00905'), ('I0_official', 'H00905'),
         ('WV0_official_excl', 'I0_official'), ('WV1_blend_official', 'I0_official'), ('WV1_blend_official', 'H00905'), ('W_blend_optimizer', 'I0_official'), ('WV1_blend_official', 'V5_P1blend(live)'),
         ('V5_P1_all15(2025-)', 'V5_P1_ctrl5(2025-)'), ('V5_P1_all15(2025-)', 'H00905')] + [(k, 'V5_P1blend(live)') for k in D if k.startswith('IND:')]
for arm, versus in PAIRS:
    if arm not in D or (versus != 'H00905' and versus not in D): continue
    for k in [0, 3, 8, 18]: add(arm, versus, k)
tab = pd.DataFrame(rows); tab.to_csv(f'{OUT}/rolling55_costs.csv', index=False)
pd.DataFrame({f'{a}|{v}|+{k}bp': s.reset_index(drop=True) for (a, v, k), s in windows.items() if k in (0, 8)}).to_csv(f'{OUT}/rolling55_nonoverlap_windows.csv')
pd.set_option('display.width', 260); cols = ['arm', 'versus', 'extra_bp', 'days', 'ann_excess', 'TE', 'IR', 'w55_median', 'w55_p10', 'w55_p25', 'w55_min', 'w55_share_pos', 'n_indep', 'w55_nonoverlap_share_pos']
print(tab[cols].round(4).to_string())
