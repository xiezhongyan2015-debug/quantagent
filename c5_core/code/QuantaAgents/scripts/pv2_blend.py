"""Equal-weight blend of two score files: per-date percentile ranks inside the eligible universe, averaged.
python scripts/pv2_blend.py --panel-dir P --a <parquet> --b <parquet> --out <parquet> [--wa 0.5]"""
import argparse, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--a", required=True); ap.add_argument("--b", required=True); ap.add_argument("--out", required=True); ap.add_argument("--wa", type=float, default=0.5)
a = ap.parse_args()
panel = Panel(a.panel_dir); panel._dtype = np.float32; m = panel.mask("all_a")
def pct(p):
    d = pd.read_parquet(p); d.index = pd.DatetimeIndex(d.index)
    return d.reindex(index=panel.dates, columns=panel.codes).where(m).rank(axis=1, pct=True)
pa, pb = pct(a.a), pct(a.b)
out = (a.wa * pa + (1 - a.wa) * pb).where(pa.notna() & pb.notna())
out.astype(np.float32).to_parquet(a.out); print(a.out, float(out.notna().sum(axis=1).mean()))
