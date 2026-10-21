"""Summary tables from 04_replay_guard.py outputs (both timing models) for the write-up.
Writes: replay_event_summary.csv (blocked-trade statistics under the live 3% rule), replay_rule_risk.csv (tail risk of each rule vs A3),
        replay_rule_table.csv (main comparison table, both timings side by side)."""
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
rows = []
for tm, suf in (('open', ''), ('h1', '_h1')):
    e = pd.read_csv(HERE / f'replay_events_A3{suf}.csv'); first = e.date.min(); e = e[e.date != first].copy()
    ndays = e.date.nunique(); e['blocked'] = e['where'] != 'first'; e['sgn'] = np.where(e.side == 'buy', 1, -1)
    for k in (1, 3, 5): e[f's{k}'] = e.sgn * e[f'fwd{k}c_x']
    e['s0'] = e.sgn * e.d0_x
    daily_blk = e[e.blocked].groupby('date').weight_nav.sum().reindex(sorted(e.date.unique())).fillna(0)
    daily_nf = e[e.blocked & ~e.filled].groupby('date').weight_nav.sum().reindex(sorted(e.date.unique())).fillna(0)
    for side in ('all', 'buy', 'sell'):
        s = e if side == 'all' else e[e.side == side]; b = s[s.blocked]; nf = b[~b.filled]
        r = dict(timing=tm, side=side, days=ndays, intents=len(s), intent_pct_nav_per_day=s.weight_nav.sum() / ndays * 100,
                 blocked_n=len(b), blocked_share_of_intent_amt=b.weight_nav.sum() / s.weight_nav.sum(), blocked_pct_nav_per_day=b.weight_nav.sum() / ndays * 100,
                 never_back_share_amt=nf.weight_nav.sum() / b.weight_nav.sum() if len(b) else np.nan, never_back_pct_nav_per_day=nf.weight_nav.sum() / ndays * 100)
        if side == 'all':
            r.update(days_with_block=int((daily_blk > 0).sum()), blocked_pct_nav_median_day=daily_blk.median() * 100, blocked_pct_nav_p90_day=daily_blk.quantile(.9) * 100,
                     days_with_never_back=int((daily_nf > 0).sum()), never_back_pct_nav_p90_day=daily_nf.quantile(.9) * 100)
        for grp, g in (('blocked', b), ('never_back', nf)):
            for k in ('0', '1', '3', '5'):
                x = g[f's{k}'].dropna(); w = g.loc[x.index, 'weight_nav']
                r[f'{grp}_signed_fwd{k}'] = float(np.average(x, weights=w)) if len(x) else np.nan
                r[f'{grp}_signed_fwd{k}_t'] = float(x.mean() / (x.std() / np.sqrt(len(x)))) if len(x) > 2 else np.nan
        rows.append(r)
E = pd.DataFrame(rows); E.to_csv(HERE / 'replay_event_summary.csv', index=False, float_format='%.6g')

risk = []; tab = []
for tm, suf in (('open', ''), ('h1', '_h1')):
    S = pd.read_csv(HERE / f'replay_rule_summary{suf}.csv'); Y = pd.read_csv(HERE / f'replay_rule_by_year{suf}.csv')
    base = pd.read_csv(HERE / 'replay_daily' / f'replay_daily_A3{suf}.csv'); rb = base.nav.pct_change().to_numpy()
    for rule in S.rule:
        d = pd.read_csv(HERE / 'replay_daily' / f'replay_daily_{rule}{suf}.csv'); r = d.nav.pct_change().to_numpy(); diff = (r - rb)[1:]
        rel = np.cumprod(1 + diff); r20 = rel[20:] / rel[:-20] - 1
        risk.append(dict(timing=tm, rule=rule, worst_day_vs_A3_bp=diff.min() * 1e4, best_day_vs_A3_bp=diff.max() * 1e4,
                         worst20d_vs_A3_bp=r20.min() * 1e4 if len(r20) else np.nan, hit_rate_days=float((diff > 0).sum() / max((diff != 0).sum(), 1)),
                         days_different=int((np.abs(diff) > 1e-12).sum())))
    S = S.merge(Y, on='rule'); S.insert(0, 'timing', tm); tab.append(S)
R = pd.DataFrame(risk); R.to_csv(HERE / 'replay_rule_risk.csv', index=False, float_format='%.6g')
T = pd.concat(tab).merge(R, on=['timing', 'rule'])
keep = ['timing', 'rule', 'ann_vs_A3', 'ci_lo', 'ci_hi', '2025', '2026', 'te_vs_A3', 'turnover_daily', 'unfilled_share_of_intent', 'unfilled_pct_nav', 'days_with_unfilled',
        'late_fill_share', 'fill_adv_bp', 'beyond3_pct_nav_per_day', 'beyond5_pct_nav_per_day', 'beyond7_pct_nav_per_day', 'worst_day_vs_A3_bp', 'worst20d_vs_A3_bp', 'mean_target_gap_l1', 'min_member_weight']
T[keep].to_csv(HERE / 'replay_rule_table.csv', index=False, float_format='%.6g')
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 60)
print(E.T.to_string()); print(T[keep].round(4).to_string())
