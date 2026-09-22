"""A18 step 4a: tracking error of the pure CSI500 basket under different weight sources, per year, walk-forward-free
(no fitted parameters): float cap (panel), free-float cap (vendor free_share x price, fd_free_cap), float cap x implied
multiplier (A17 ridge), free-float cap x implied multiplier.  Same open-to-open convention as pv4_index_weights.py.

python scripts/fd_freecap_te.py --panel-dir F:/A_Layer_Research/panel --cap-mult F:/A_Layer_Research/competition/pv4/indexw/index_weight_mult_a1.parquet --out F:/A_Layer_Research/competition/fd/eval/freecap_te_research.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--panel-dir", required=True); ap.add_argument("--cap-mult", default=None); ap.add_argument("--index", default="csi500")
ap.add_argument("--start", default="2017-01-01"); ap.add_argument("--out", required=True)
a = ap.parse_args()
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel); m5 = mem[a.index]
dates = panel.dates
ever = m5.loc[dates >= pd.Timestamp(a.start)].any(axis=0); codes = list(m5.columns[ever.to_numpy()])
m = m5[codes].to_numpy(bool)
ovn = (panel["open"].shift(-1) / panel["close"] - 1)[codes].to_numpy(np.float64)
lab = panel["label_1"][codes].to_numpy(np.float64)           # open[t+1] -> open[t+2]
bench1, _ = cp.benchmark_label1(dates, a.index)
bench1 = np.asarray(bench1, np.float64)

caps = {"float_cap": panel["float_market_cap"][codes].to_numpy(np.float64)}
if panel.has("fd_free_cap"):
    caps["free_cap"] = panel["fd_free_cap"][codes].to_numpy(np.float64)
if a.cap_mult and os.path.exists(a.cap_mult):
    cm = pd.read_parquet(a.cap_mult); cm.index = pd.DatetimeIndex(cm.index)
    g = cm.reindex(index=dates, columns=codes).ffill().fillna(1.0).to_numpy(np.float64)
    caps["float_x_implied"] = caps["float_cap"] * g
    if "free_cap" in caps:
        caps["free_x_implied"] = caps["free_cap"] * g
    if "free_cap" in caps:   # half-way: geometric mean of the two direct sources
        caps["float_free_geo"] = np.sqrt(caps["float_cap"] * caps["free_cap"])

years = np.asarray(dates.year)
res = {}
for name, cap in caps.items():
    # a free-cap cell missing (pre-2016-09 or vendor gap) falls back to float cap so the basket stays complete
    capf = np.where(np.isfinite(cap) & (cap > 0), cap, caps["float_cap"])
    w = np.where(m & np.isfinite(capf) & (capf > 0), capf * (1 + np.nan_to_num(ovn)), 0.0)
    w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
    r = np.nansum(w * np.nan_to_num(lab), axis=1)
    diff = r - bench1
    ok = np.isfinite(diff) & np.asarray(dates >= pd.Timestamp(a.start)) & (m.sum(axis=1) > 400)
    per_year = {}
    for y in sorted(set(years[ok])):
        s = ok & (years == y)
        d = diff[s]
        per_year[int(y)] = {"te_ann": float(np.std(d, ddof=1) * np.sqrt(252)), "mean_ann": float(np.mean(d) * 252), "days": int(s.sum())}
    res[name] = {"per_year": per_year, "te_ann_all": float(np.std(diff[ok], ddof=1) * np.sqrt(252)), "coverage_cells": float(np.isfinite(cap[m]).mean())}
os.makedirs(os.path.dirname(a.out), exist_ok=True)
json.dump(res, open(a.out, "w", encoding="utf-8"), indent=1)
rows = []
for name, r in res.items():
    row = {"weights": name, "TE_all": round(r["te_ann_all"], 4)}
    row.update({str(y): round(v["te_ann"], 4) for y, v in r["per_year"].items()})
    rows.append(row)
print(pd.DataFrame(rows).to_string(index=False))
