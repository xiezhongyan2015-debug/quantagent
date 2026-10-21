"""Where does the book's excess come from? Per year: CSI500 bucket vs index, other bucket vs index, cash drag, costs.
python scripts/pv4_decompose.py --panel-dir P --pred path --rule-json file --rules c3A c3A_x00 [--years 2019 2026]"""
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp
ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--pred", required=True); ap.add_argument("--rule-json", nargs="+", required=True); ap.add_argument("--rules", nargs="+", required=True)
ap.add_argument("--years", nargs=2, type=int, default=[2019, 2026])
a = ap.parse_args()
allr = {}
for f in a.rule_json:
    allr.update(json.load(open(f, encoding="utf-8")))
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel); bench1, _ = cp.benchmark_label1(panel.dates, "csi500")
pred = pd.read_parquet(a.pred); pred.index = pd.DatetimeIndex(pred.index)
for r in a.rules:
    cfg = dict(cp.DEFAULT_CFG, **allr[r]); b = cp.two_bucket_book(pred, panel, mem, bench1, cfg)
    live = b["live"]; w5, wo = float(cfg["w500"]), float(cfg["woth"]); bench = b["benchmark"]
    parts = pd.DataFrame({"b500_vs_index": w5 * (b["bucket500"]["portfolio"] - bench), "other_vs_index": wo * (b["bucketoth"]["portfolio"] - bench), "cash_drag": -(1 - w5 - wo) * bench,
                          "cost": -cfg["cost"] * b["turnover"], "ew500_vs_index": b["ew500_vs_index"], "index": bench})[live]
    parts["net"] = parts[["b500_vs_index", "other_vs_index", "cash_drag", "cost"]].sum(axis=1)
    y = parts.index.year; sel = (y >= a.years[0]) & (y <= a.years[1])
    print(f"== {r}"); print((parts[sel].groupby(y[sel]).mean() * 252).round(4).to_string())
