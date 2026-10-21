"""Record how the shadow targets differ from the released target for one execution day (2026-10-15).
Appends one row per shadow variant to 实盘日更/outputs/shadow/shadow_log.csv: names, two-way weight distance (L1/2),
CSI500 share, stock fraction, and the five largest weight differences. Usage: shadow_compare.py <exec_date>"""
import json, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]; L = ROOT / '实盘日更'; day = sys.argv[1]
def weights(path):
    j = json.loads(Path(path).read_text(encoding='utf-8')); rows = j.get('targets') or j.get('positions') or []
    if isinstance(rows, dict): rows = [dict(code=k, **(v if isinstance(v, dict) else {'weight': v})) for k, v in rows.items()]
    w = {}
    for r in rows:
        code = r.get('code') or r.get('symbol'); val = r.get('weight', r.get('target_weight'))
        if code is not None and val is not None: w[code] = float(val)
    s = pd.Series(w, dtype=float); return s / s.sum() if s.sum() > 0 else s
live = L / f'outputs/targets/{day}/target_{day}.json'
if not live.exists(): print('no released target for', day); sys.exit(0)
a = weights(live); out = []
for v in sorted((L / f'outputs/shadow/{day}').glob('*/target_*.json')):
    b = weights(v); u = a.index.union(b.index); d = (a.reindex(u).fillna(0) - b.reindex(u).fillna(0))
    pre = json.loads((v.parent / f'precheck_{day}.json').read_text(encoding='utf-8')) if (v.parent / f'precheck_{day}.json').exists() else {}
    out.append(dict(exec_date=day, variant=v.parent.name, names_live=int((a > 0).sum()), names_shadow=int((b > 0).sum()), l1_half=round(float(d.abs().sum() / 2), 4), only_live=int(((a.reindex(u).fillna(0) > 0) & (b.reindex(u).fillna(0) == 0)).sum()), only_shadow=int(((b.reindex(u).fillna(0) > 0) & (a.reindex(u).fillna(0) == 0)).sum()),
                    csi500_share=pre.get('csi500_share_of_target'), top_diffs=';'.join(f'{k}:{x:+.4f}' for k, x in d.abs().sort_values(ascending=False).head(5).items())))
if out:
    f = L / 'outputs/shadow/shadow_log.csv'; df = pd.DataFrame(out); df.to_csv(f, mode='a', header=not f.exists(), index=False); print(df.to_string(index=False))
