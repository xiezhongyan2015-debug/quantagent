"""A17 item 5: environment switch between a low-tracking-error book A and a concentrated book B.
The portfolio holds (1 - x) of A and x of B (a mix of two valid books is a valid book); x is reviewed on the first
trading day of each month from an indicator known at that close, moving x costs `switch_cost` per unit moved.
Policies = indicator x direction x threshold (expanding quantile of the indicator's own past) x (lo, hi) pair. For every
year Y the policy is chosen on the years before Y only (net Sharpe), then applied in Y: that stitched series is the only
honest number; it is compared with the fixed mixes and with a walk-forward choice among fixed mixes.

python scripts/pv4_regime.py --panel-dir P --series a.parquet [b.parquet] --a "base|c3A" --b "turn|c3B" [--first-year 2021] --out out.json
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
ap.add_argument("--panel-dir", required=True); ap.add_argument("--series", nargs="+", required=True); ap.add_argument("--a", required=True); ap.add_argument("--b", required=True)
ap.add_argument("--first-year", type=int, default=2021); ap.add_argument("--switch-cost", type=float, default=0.0025); ap.add_argument("--out", default=None)
a = ap.parse_args()
frames = []
for p in a.series:
    d = pd.read_parquet(p); d.index = pd.DatetimeIndex(d.index); frames.append(d[[a.a, a.b]].dropna(how="all"))
S = pd.concat(frames).sort_index(); S = S[~S.index.duplicated(keep="last")].dropna()
A, B = S[a.a], S[a.b]
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel)
m5 = mem["csi500"]
close = panel["close"]; r20 = (close / close.shift(20) - 1).where(m5)
disp = r20.std(axis=1)
ret1 = panel["ret"].where(m5); mkt = ret1.mean(axis=1)
ind = pd.DataFrame({
    "disp20": disp, "mkt20": mkt.rolling(20).sum(), "mkt60": mkt.rolling(60).sum(), "vol20": mkt.rolling(20).std(),
    "xvol20": ret1.std(axis=1).rolling(20).mean(),
}).reindex(S.index)
# realised strategy series are known two rows later (row t is earned between the opens of t+1 and t+2)
ind["b60"] = B.shift(2).rolling(60).mean(); ind["a60"] = A.shift(2).rolling(60).mean(); ind["ba60"] = (B - A).shift(2).rolling(60).mean()
ind["b120"] = B.shift(2).rolling(120).mean(); ind["ba120"] = (B - A).shift(2).rolling(120).mean()
month_first = pd.Series(S.index.to_period("M"), index=S.index).ne(pd.Series(S.index.to_period("M"), index=S.index).shift(1)).to_numpy()


def sh(x):
    x = x.dropna()
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252)) if len(x) > 20 and x.std() > 0 else float("nan")


def run(x_target: pd.Series) -> pd.Series:
    x = np.zeros(len(S)); cur = float(x_target.iloc[0]) if np.isfinite(x_target.iloc[0]) else 0.5; cost = np.zeros(len(S))
    xt = x_target.to_numpy()
    for t in range(len(S)):
        if month_first[t] and np.isfinite(xt[t]) and xt[t] != cur:
            cost[t] = a.switch_cost * abs(xt[t] - cur); cur = xt[t]
        x[t] = cur
    return pd.Series((1 - x) * A.to_numpy() + x * B.to_numpy() - cost, index=S.index), pd.Series(x, index=S.index)


policies = {}
for name in ind.columns:
    v = ind[name]
    for qn in (0.3, 0.5, 0.7):
        thr = v.expanding(min_periods=250).quantile(qn).shift(1)
        for direction in (1, -1):
            for lo, hi in ((0.0, 1.0), (0.0, 0.5), (0.5, 1.0)):
                on = (v > thr) if direction == 1 else (v < thr)
                xt = pd.Series(np.where(on, hi, lo), index=S.index).where(v.notna() & thr.notna())
                policies[f"{name}{'>' if direction == 1 else '<'}q{qn}|{lo}-{hi}"] = xt
fixed = {f"fixed_{x}": pd.Series(x, index=S.index) for x in (0.0, 0.25, 0.5, 0.75, 1.0)}
series = {k: run(v)[0] for k, v in {**policies, **fixed}.items()}
yrs = S.index.year


def walk(keys):
    chosen = {}; parts = []
    for Y in sorted(set(yrs)):
        if Y < a.first_year:
            continue
        sc = {k: sh(series[k][yrs < Y]) for k in keys}
        best = max(sc, key=lambda k: sc[k] if np.isfinite(sc[k]) else -9)
        chosen[int(Y)] = best; parts.append(series[best][yrs == Y])
    return pd.concat(parts), chosen


def stats(x):
    y = x.index.year
    return {"research": {"net": float(x[y <= 2024].mean() * 252), "sharpe": sh(x[y <= 2024])},
            **{str(k): {"net": float(x[y == k].mean() * 252), "sharpe": sh(x[y == k])} for k in sorted(set(y)) if k >= 2025}}


wf_pol, ch_pol = walk(list(policies)); wf_fix, ch_fix = walk(list(fixed))
rep = {"a": a.a, "b": a.b, "walkforward_policy": stats(wf_pol), "chosen_policy": ch_pol, "walkforward_fixed": stats(wf_fix), "chosen_fixed": ch_fix,
       "fixed": {k: stats(series[k][yrs >= a.first_year]) for k in fixed}}
# how many policies beat the best fixed mix in-sample on the research years (selection-bias context)
best_fix = max(sh(series[k][(yrs >= a.first_year) & (yrs <= 2024)]) for k in fixed)
ins = {k: sh(series[k][(yrs >= a.first_year) & (yrs <= 2024)]) for k in policies}
rep["in_sample_share_beating_best_fixed"] = float(np.mean([v > best_fix for v in ins.values()]))
rep["in_sample_top5"] = {k: {"sharpe": v, **stats(series[k][yrs >= a.first_year])} for k, v in sorted(ins.items(), key=lambda kv: -kv[1])[:5]}
fmt = lambda st: " ".join(f"{k}: {v['net']:+.3f}/{v['sharpe']:.2f}" for k, v in st.items())
print("walk-forward policy :", fmt(rep["walkforward_policy"])); print("   chosen:", ch_pol)
print("walk-forward fixed  :", fmt(rep["walkforward_fixed"])); print("   chosen:", ch_fix)
for k in fixed:
    print(f"  {k:11s}:", fmt(rep["fixed"][k]))
print("share of policies beating the best fixed mix in-sample: %.2f" % rep["in_sample_share_beating_best_fixed"])
for k, v in rep["in_sample_top5"].items():
    print(f"  in-sample top {k}: " + " ".join(f"{kk}: {vv['net']:+.3f}/{vv['sharpe']:.2f}" for kk, vv in v.items() if kk != "sharpe"))
if a.out:
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
