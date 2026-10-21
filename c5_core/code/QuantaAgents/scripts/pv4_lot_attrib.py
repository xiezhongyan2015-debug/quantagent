"""Where does the whole-lot book lose against the frictionless one? Attribution of (w_lot - w_free) x next return by
price group and by cash, per year. python scripts/pv4_lot_attrib.py --panel-dir P --pred path --rule-json file --rule c3A --years 2025 2026 [--group 1] [--nav 2e6]"""
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp
ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--pred", required=True); ap.add_argument("--rule-json", required=True); ap.add_argument("--rule", required=True)
ap.add_argument("--years", nargs=2, type=int, required=True); ap.add_argument("--group", type=int, default=0); ap.add_argument("--nav", type=float, default=2e6); ap.add_argument("--band", type=float, default=0.001)
a = ap.parse_args()
rule = json.load(open(a.rule_json, encoding="utf-8"))[a.rule]
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel); bench1, _ = cp.benchmark_label1(panel.dates, "csi500")
pred = pd.read_parquet(a.pred); pred.index = pd.DatetimeIndex(pred.index)
free = cp.two_bucket_book(pred, panel, mem, bench1, dict(cp.DEFAULT_CFG, **rule, keep_weights=True))
lot = cp.two_bucket_book(pred, panel, mem, bench1, dict(cp.DEFAULT_CFG, **rule, keep_weights=True, lot_nav=a.nav, lot_min_trade=a.band, lot_group_below=bool(a.group)))
lab = np.nan_to_num(panel["label_1"].to_numpy(np.float64)); px = panel["raw_open"].shift(-1).to_numpy(np.float64)
ys = (panel.dates.year >= a.years[0]) & (panel.dates.year <= a.years[1]) & free["live"].to_numpy(bool)
bins = [0, 10, 20, 40, 80, 160, 1e9]
for bk, wt in (("bucket500", float(rule.get("w500", cp.DEFAULT_CFG["w500"]))), ("bucketoth", float(rule.get("woth", cp.DEFAULT_CFG["woth"])))):
    d = wt * (lot[bk]["weights"].astype(np.float64) - free[bk]["weights"].astype(np.float64))
    contrib = d * lab
    print(f"== {bk}: total active-vs-frictionless contribution {contrib[ys].sum(axis=1).mean() * 252:+.4f}/yr; mean invested lot {wt * lot[bk]['invested'][ys].mean():.4f} vs free {wt * free[bk]['invested'][ys].mean():.4f}; names lot {lot[bk]['count'][ys].mean():.0f} vs free {free[bk]['count'][ys].mean():.0f}")
    for lo, hi in zip(bins[:-1], bins[1:]):
        g = (px >= lo) & (px < hi)
        c = np.where(g, contrib, 0.0)[ys].sum(axis=1).mean() * 252; dw = np.where(g, d, 0.0)[ys].sum(axis=1).mean(); wf = np.where(g, wt * free[bk]["weights"], 0.0)[ys].sum(axis=1).mean()
        print(f"   price {lo:>4}-{hi:<6.0f}: free weight {wf:.4f}  lot-minus-free weight {dw:+.4f}  contribution {c:+.4f}/yr")
net = lambda b: (b["excess"] - 0.004 * b["turnover"])[ys]
print("net free %+.4f lot %+.4f | turnover free %.4f lot %.4f | gross free %+.4f lot %+.4f" % (net(free).mean() * 252, net(lot).mean() * 252, free["turnover"][ys].mean(), lot["turnover"][ys].mean(), free["excess"][ys].mean() * 252, lot["excess"][ys].mean() * 252))
