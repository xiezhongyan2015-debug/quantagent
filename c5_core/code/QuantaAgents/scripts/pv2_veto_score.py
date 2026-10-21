"""Combined-veto exclusion score: per-date elementwise min of the union percentiles of a main score and a second score
(a name is excluded when either score puts it in the bottom). python scripts/pv2_veto_score.py --panel-dir P --main <parquet> --second <parquet> --out <parquet>"""
import argparse, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--main", required=True); ap.add_argument("--second", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
panel = Panel(a.panel_dir); panel._dtype = np.float32
union = panel.mask("union")
def pct(path):
    d = pd.read_parquet(path); d.index = pd.DatetimeIndex(d.index)
    return d.reindex(index=panel.dates, columns=panel.codes).where(union).rank(axis=1, pct=True)
pm, ps = pct(a.main), pct(a.second)
out = pd.concat([pm, ps]).groupby(level=0).min().where(pm.notna())
out.astype(np.float32).to_parquet(a.out); print(a.out, float(out.notna().sum(axis=1).mean()))
