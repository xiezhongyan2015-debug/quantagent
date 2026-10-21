"""Daily net-excess series of every (prediction, rule) pair, for walk-forward rule selection (A17 item 1).
python scripts/pv4_rule_series.py --panel-dir P --pred label=path [--pred ...] --rule-json <json or file> --out series.parquet"""
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--panel-dir", required=True); ap.add_argument("--pred", action="append", required=True); ap.add_argument("--rule-json", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
rules = json.loads(open(a.rule_json, encoding="utf-8").read() if os.path.exists(a.rule_json) else a.rule_json)
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel)
bench1, _ = cp.benchmark_label1(panel.dates, "csi500")
cols = {}
for spec in a.pred:
    label, path = spec.split("=", 1)
    pred = pd.read_parquet(path); pred.index = pd.DatetimeIndex(pred.index)
    covered = pred.reindex(panel.dates).notna().any(axis=1).to_numpy()
    for rname, over in rules.items():
        cfg = dict(cp.DEFAULT_CFG, **over); t0 = time.time()
        book = cp.two_bucket_book(pred, panel, mem, bench1, cfg)
        net = (book["excess"] - cfg["cost"] * book["turnover"]).where(book["live"])
        net = net[covered]                                   # only dates the prediction covers
        cols[f"{label}|{rname}"] = net
        print(f"{label}|{rname}: ann {net.mean() * 252:+.3f} ({time.time() - t0:.0f}s)", flush=True)
pd.DataFrame(cols).to_parquet(a.out)
print(a.out)
