"""Compact tables for SUMMARY.md from the part1/part2 result files."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd
import common as C

import numpy as np
ic = pd.read_csv(C.RESULTS / 'part1_rank_ic_long.csv')
p = ic[ic.kind.isin(['raw', 'neu']) & ic.target.isin(['oo1', 'vw5', 'vw20'])].pivot_table(
    index=['arm', 'head', 'target', 'year'], columns='kind', values='rank_ic')
p['chg'] = (p.neu - p.raw) / p.raw.abs()
p['daily_equiv_neu'] = p.neu / np.sqrt(p.index.get_level_values('target').map({'oo1': 1, 'vw5': 5, 'vw20': 20}).astype(float))
p.reset_index().to_csv(C.RESULTS / 'part1_neutralization_change.csv', index=False)

r = pd.read_csv(C.RESULTS / 'part2_comparisons.csv')
r[['arm', 'head', 'usage', 'exec', 'fee']] = r.candidate.str.split('|', expand=True)
r['fee'] = r.fee.astype(float)
fmt = lambda x: f'{100 * x.rel_cagr:+.2f} [{100 * x.ci_lo:+.2f}, {100 * x.ci_hi:+.2f}]'
r['cell'] = r.apply(fmt, axis=1)
out = []
for (arm, head, usage, ex, fee), g in r[r.arm.str.startswith(('VWAP', 'SHORT'))].groupby(['arm', 'head', 'usage', 'exec', 'fee']):
    row = dict(arm=arm, head=head, usage=usage, exec=ex, fee_bp=round(fee * 1e4))
    for comp in ('vs_P0', 'vs_P0_tranche', 'vs_SHORT_same_usage', 'vs_SHORT_head4_current'):
        for w in ('continuous2025_2026', '2025', '2026'):
            x = g[(g.comparison == comp) & (g.window == w)]
            if len(x):
                row[f'{comp}|{w}'] = x.cell.iloc[0]
    x = g[(g.comparison == 'vs_P0') & (g.window == 'continuous2025_2026')].iloc[0]
    row['turnover_two_way_daily'] = round(x.cand_turnover, 4)
    out.append(row)
t = pd.DataFrame(out)
t.to_csv(C.RESULTS / 'part2_main_table.csv', index=False)

base = r[r.comparison.isin(['P0_tranche_vs_P0', 'P0_vwap_vs_open'])][['comparison', 'candidate', 'window', 'cell', 'cand_turnover', 'base_turnover']]
base.to_csv(C.RESULTS / 'part2_structure_baselines.csv', index=False)


def md(df):
    cols = list(df.columns)
    lines = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    lines += ['| ' + ' | '.join(str(v) for v in row) + ' |' for row in df.itertuples(index=False)]
    return '\n'.join(lines)


sel = t[t.arm.str.startswith('VWAP') & (t.exec == 'open') & (t.fee_bp == 2)]
cols = ['arm', 'head', 'usage', 'vs_P0|continuous2025_2026', 'vs_P0|2025', 'vs_P0|2026', 'vs_SHORT_same_usage|continuous2025_2026',
        'vs_P0_tranche|continuous2025_2026', 'turnover_two_way_daily']
Path(C.RESULTS / 'part2_tables.md').write_text(
    '## open execution, 2bp\n\n' + md(sel[cols].fillna('—')) + '\n\n' +
    '## vwap execution, 2bp\n\n' + md(t[t.arm.str.startswith('VWAP') & (t.exec == 'vwap') & (t.fee_bp == 2)][cols].fillna('—')) + '\n\n' +
    '## open execution, 4bp stress\n\n' + md(t[t.arm.str.startswith('VWAP') & (t.exec == 'open') & (t.fee_bp == 4)][cols].fillna('—')) + '\n\n' +
    '## vwap execution, 4bp stress\n\n' + md(t[t.arm.str.startswith('VWAP') & (t.exec == 'vwap') & (t.fee_bp == 4)][cols].fillna('—')) + '\n\n' +
    '## SHORT arms (reference), open, 2bp\n\n' + md(t[t.arm.str.startswith('SHORT') & (t.exec == 'open') & (t.fee_bp == 2)][
        ['arm', 'head', 'usage', 'vs_P0|continuous2025_2026', 'vs_P0|2025', 'vs_P0|2026', 'vs_P0_tranche|continuous2025_2026', 'turnover_two_way_daily']].fillna('—')) + '\n\n' +
    '## structure baselines\n\n' + md(base.round(4)) + '\n', encoding='utf-8')
print(open(C.RESULTS / 'part2_tables.md', encoding='utf-8').read())
