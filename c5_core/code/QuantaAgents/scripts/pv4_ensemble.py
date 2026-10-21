"""Seed / model ensemble: per-date percentile ranks inside the all-A eligibility mask, averaged over N prediction files.
python scripts/pv4_ensemble.py --panel-dir P --preds a.parquet b.parquet ... --out out.parquet [--weights 1 1 ...]"""
import argparse
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--panel-dir", required=True); ap.add_argument("--preds", nargs="+", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--weights", nargs="*", type=float, default=None)
a = ap.parse_args()
panel = Panel(a.panel_dir); panel._dtype = np.float32
m = panel.mask("all_a")
w = a.weights or [1.0] * len(a.preds)
acc = None; cnt = None
for p, wi in zip(a.preds, w):
    d = pd.read_parquet(p); d.index = pd.DatetimeIndex(d.index)
    r = d.reindex(index=panel.dates, columns=panel.codes).where(m).rank(axis=1, pct=True)
    acc = r.fillna(0) * wi if acc is None else acc + r.fillna(0) * wi
    cnt = r.notna() * wi if cnt is None else cnt + r.notna() * wi
out = (acc / cnt.where(cnt > 0)).where(cnt >= 0.999 * sum(w))      # a cell needs every member
out = out.dropna(how="all")
out.astype(np.float32).to_parquet(a.out)
print(a.out, out.shape, float(out.notna().sum(axis=1).mean()))
