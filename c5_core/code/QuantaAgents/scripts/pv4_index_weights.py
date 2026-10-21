"""A17 structural item: implied CSI500 index weights from prices only.
The index is weighted by free-float-ADJUSTED capitalisation (tiered inclusion factors); the panel only has the tradable
float capitalisation, so a float-cap-weighted bucket carries an unintended bet (low-free-float names overweighted).
Model: true weight_i ∝ floatcap_i x g_i with a slow-moving multiplier g_i. With h = g - 1,
    index return - floatcap-weighted return  =  sum_i h_i * w_i * (r_i - r_fc)  + noise          (open-to-open, daily)
so h is a ridge regression on a trailing window (walk-forward: fitted on days whose returns are fully known before the
decision day, re-fitted every `step` days). Output: multiplier panel g (date x code, float32) + tracking-error report
(float-cap vs implied weights, out of sample, per year).

python scripts/pv4_index_weights.py --panel-dir P --out-dir O [--window 500] [--step 20] [--alphas 0.3 1 3 10] [--write-alpha 1]
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
ap.add_argument("--panel-dir", required=True); ap.add_argument("--out-dir", required=True); ap.add_argument("--window", type=int, default=500); ap.add_argument("--step", type=int, default=20)
ap.add_argument("--alphas", nargs="+", type=float, default=[0.3, 1.0, 3.0, 10.0]); ap.add_argument("--write-alpha", type=float, default=None); ap.add_argument("--start", default="2017-01-01")
ap.add_argument("--cap-field", default="float_market_cap"); ap.add_argument("--tag", default=""); ap.add_argument("--index", default="csi500", help="csi500 (default) / csi300 / csi1000: the same method on another index is a mechanism check")
a = ap.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel); m5 = mem[a.index]
dates = panel.dates
ever = m5.loc[dates >= pd.Timestamp(a.start)].any(axis=0); codes = list(m5.columns[ever.to_numpy()])
m = m5[codes].to_numpy(bool)
cap = panel[a.cap_field][codes].to_numpy(np.float64)
ovn = (panel["open"].shift(-1) / panel["close"] - 1)[codes].to_numpy(np.float64)
lab = panel["label_1"][codes].to_numpy(np.float64)
bench1, _ = cp.benchmark_label1(dates, a.index)
# weights at the open of t+1 of a float-cap-weighted CSI500 bucket decided on the close of t
wfc = np.where(m & np.isfinite(cap) & (cap > 0), cap * (1 + np.nan_to_num(ovn)), 0.0)
wfc = wfc / np.maximum(wfc.sum(axis=1, keepdims=True), 1e-12)
r = np.nan_to_num(lab)
rfc = (wfc * r).sum(axis=1)
y = bench1 - rfc                                              # what the float-cap bucket misses, per day
X = wfc * (r - rfc[:, None])
ok = np.isfinite(bench1) & (wfc.sum(axis=1) > 0.5)
T = len(dates); start_i = int(np.searchsorted(dates, pd.Timestamp(a.start)))
G = {al: np.ones((T, len(codes)), np.float32) for al in a.alphas}
for t0 in range(start_i, T, a.step):
    # rows usable on decision day t0: label_1 of row s is known at the open of s+2 -> s <= t0 - 2
    hi = t0 - 1; lo = max(0, hi - a.window)
    if hi - lo < 120:
        continue
    rows = np.arange(lo, hi)[ok[lo:hi]]
    if len(rows) < 120:
        continue
    Xw = X[rows]; yw = y[rows]
    act = m[t0] | (np.abs(Xw).sum(axis=0) > 0)
    Xa = Xw[:, act]; K = Xa @ Xa.T                           # dual ridge: n_obs x n_obs
    scale = np.trace(Xa.T @ Xa) / max(act.sum(), 1)
    for al in a.alphas:
        lam = al * scale
        h = Xa.T @ np.linalg.solve(K + lam * np.eye(len(rows)), yw)
        g = np.ones(len(codes)); g[act] = np.clip(1 + h, 0.1, 3.0)
        G[al][t0:min(t0 + a.step, T)] = g.astype(np.float32)
rep = {}
yrs = dates.year
for al in a.alphas:
    wg = wfc * G[al]; wg = wg / np.maximum(wg.sum(axis=1, keepdims=True), 1e-12)
    yg = bench1 - (wg * r).sum(axis=1)
    rep[str(al)] = {}
    for Y in sorted(set(yrs[start_i:])):
        s = ok & (yrs == Y) & (np.arange(T) >= start_i + a.window // 2)
        if s.sum() < 50:
            continue
        rep[str(al)][str(Y)] = {"te_floatcap": float(np.std(y[s]) * np.sqrt(252)), "te_implied": float(np.std(yg[s]) * np.sqrt(252)),
                                "miss_floatcap_ann": float(np.mean(y[s]) * 252), "miss_implied_ann": float(np.mean(yg[s]) * 252),
                                "g_p10": float(np.percentile(G[al][s][m[s]], 10)), "g_p90": float(np.percentile(G[al][s][m[s]], 90))}
    print(f"alpha {al}")
    print(pd.DataFrame(rep[str(al)]).T.round(4).to_string())
json.dump(rep, open(os.path.join(a.out_dir, f"index_weights_report{a.tag}.json"), "w", encoding="utf-8"), indent=1)
if a.write_alpha is not None:
    out = pd.DataFrame(G[a.write_alpha], index=dates, columns=codes)
    out.to_parquet(os.path.join(a.out_dir, f"index_weight_mult{a.tag}.parquet"))
    print("written", os.path.join(a.out_dir, f"index_weight_mult{a.tag}.parquet"))
