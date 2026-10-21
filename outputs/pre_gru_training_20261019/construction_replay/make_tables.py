"""Collect the Z2 / H85 tables for SUMMARY.md from construction_replay.py outputs (this directory only)."""
from pathlib import Path
import numpy as np
import pandas as pd

H = Path(__file__).resolve().parent
P = pd.read_csv(H / 'paired_comparisons.csv'); S = pd.read_csv(H / 'arm_summary.csv').set_index('arm'); DG = pd.read_csv(H / 'z2_signal_diagnostics_by_year.csv', index_col=0)
pct = lambda x: round(100 * float(x), 2)


def pair(x, y):
    r = P[(P.arm == x) & (P.versus == y)].iloc[0]
    return dict(arm=x, versus=y, ann_diff_pct=pct(r.annualized_relative_cagr), ci95=f'[{pct(r.boot95_low)}, {pct(r.boot95_high)}]',
                y2025_pct=pct(r.y2025_cagr), y2025_ci=f'[{pct(r.y2025_lo)}, {pct(r.y2025_hi)}]', y2026_pct=pct(r.y2026_cagr), y2026_ci=f'[{pct(r.y2026_lo)}, {pct(r.y2026_hi)}]',
                rel_te_pct=pct(r.tracking_error), ir=round(float(r.information_ratio), 2), w50_win=round(float(r.w50_win), 2))


rows = []
for x, y in [('B', 'A'), ('C', 'A'), ('B_nt', 'A_nt'), ('C_nt', 'A_nt'), ('A', 'A_nt')]:
    d = pair(x, y)
    for a, tag in ((x, 'arm'), (y, 'versus')):
        s = S.loc[a]; d.update({f'{tag}_turnover_pct': pct(s.turnover_two_way_mean), f'{tag}_target_beta': round(float(s.beta_final_mean), 3), f'{tag}_realised_beta': round(float(s.realised_beta_vs_H00905), 3)})
    v = x.split('_')[0]
    d.update(arm_rankic_y4=round(float(DG.loc['all', f'{v}_rankic_y4']), 4), arm_corr_vol=round(float(DG.loc['all', f'{v}_corr_vol_rank']), 3), arm_corr_turn=round(float(DG.loc['all', f'{v}_corr_turn_rank']), 3))
    for c in ('act_vol', 'act_turn', 'act_logcap', 'act_beta60', 'gru_bcov_vol', 'gru_bcov_turn', 'gru_bcov_logcap', 'gru_bcov_beta60'):
        d[f'arm_{c}'] = round(float(S.loc[x, c + '_mean']), 4)
    rows.append(d)
pd.DataFrame(rows).to_csv(H / 'z2_table.csv', index=False)

rows = []
for x, y in [('A_H85', 'A'), ('A_H85x', 'A'), ('A_H87x', 'A'), ('A_H86t86x', 'A'), ('A_nt_H85', 'A_nt')]:
    d = pair(x, y)
    for a, tag in ((x, 'arm'), (y, 'versus')):
        s = S.loc[a]
        d.update({f'{tag}_turnover_pct': pct(s.turnover_two_way_mean), f'{tag}_te_vs_H00905_pct': pct(s.te_vs_H00905), f'{tag}_te_vs_P0_pct': pct(s.te_vs_P0),
                  f'{tag}_active_vs_H00905_pct': pct(s.ann_active_vs_H00905), f'{tag}_c500_mean_pct': pct(s.c500_nav_exec_mean), f'{tag}_c500_min_pct': pct(s.c500_nav_exec_min),
                  f'{tag}_days_lt85': int(s.c500_nav_exec_days_lt85), f'{tag}_days_lt80': int(s.c500_nav_exec_days_lt80),
                  f'{tag}_c500_mean_sigmem_pct': pct(s.c500_nav_sig_mean), f'{tag}_c500_min_sigmem_pct': pct(s.c500_nav_sig_min), f'{tag}_days_lt85_sigmem': int(s.c500_nav_sig_days_lt85)})
    d.update(binding_days=int(S.loc[x, 'h85_binding_days']), moved_mean_pct=pct(S.loc[x, 'h85_moved_mean_when_binding']), same_industry_share=round(float(S.loc[x, 'h85_same_industry_share']), 3))
    rows.append(d)
pd.DataFrame(rows).to_csv(H / 'h85_table.csv', index=False)

# CSI500 share distribution by year (realised at close, execution-day membership)
out = []
for a in ['P0', 'A', 'A_nt', 'A_H85', 'A_H85x', 'A_H87x', 'A_H86t86x', 'A_nt_H85']:
    d = pd.read_csv(H / f'arm_{a}_daily.csv').iloc[1:]; d['year'] = d.date.str[:4]
    for yy, g in list(d.groupby('year')) + [('all', d)]:
        x = g.member_nav_weight_exec
        out.append(dict(arm=a, year=yy, days=len(g), mean=round(x.mean(), 4), p5=round(x.quantile(.05), 4), min=round(x.min(), 4), min_date=g.date[x.idxmin()], days_lt85=int((x < .85).sum()), days_lt80=int((x < .80).sum()),
                        target_mean=round(g.target_member_weight_exec.mean(), 4)))
cal = pd.read_csv(H / 'calibration_V5_P1_all15_daily.csv').iloc[1:]
out.append(dict(arm='calibration_old_framework(.98,prorata,no tilt)', year='all', days=len(cal), mean=round(cal.member_nav_weight.mean(), 4), p5=round(cal.member_nav_weight.quantile(.05), 4), min=round(cal.member_nav_weight.min(), 4),
                min_date=cal.date[cal.member_nav_weight.idxmin()], days_lt85=int((cal.member_nav_weight < .85).sum()), days_lt80=int((cal.member_nav_weight < .80).sum()), target_mean=np.nan))
pd.DataFrame(out).to_csv(H / 'c500_share_distribution.csv', index=False)
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 60)
for f in ('z2_table.csv', 'h85_table.csv', 'c500_share_distribution.csv'): print(f); print(pd.read_csv(H / f).T.to_string() if f != 'c500_share_distribution.csv' else pd.read_csv(H / f).to_string())
