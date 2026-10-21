"""Recent-window replay of the live versions on the LIVE panel (what the daily chain actually scores): c2 mainline (base model),
c4 and c5 (turnover-label model), open-to-open, net of 0.2% per side.  Books are simulated over the whole live window so the
hysteresis states are warm; only the requested window is reported.

python scripts/live_recent_pnl.py --start 2026-09-15 --end 2026-09-30
"""
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402

LIVE = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live"
BASE = {"book": "enhanced", "gamma": 1.0, "x_out": 0.2, "x_in": 0.35, "y_in": 0.9, "y_out": 0.75, "tau": 0.0, "smooth": 5, "neutral_q": 5, "neutral_field": "turnover20", "cost": 0.004}
RULES = {
    "c2": (LIVE + "/pred_live.parquet", dict(BASE, cap_field="float_market_cap", w500=0.88, woth=0.08, noth=25, keep_mult=3.0)),
    "c4": (LIVE + "/pred_live_turnlabel.parquet", dict(BASE, cap_field="float_market_cap", w500=0.85, woth=0.13, noth=110, keep_mult=4.0, oth_universe="csi1000", cap_mult=LIVE + "/indexw/index_weight_mult_a1.parquet")),
    "c5": (LIVE + "/pred_live_turnlabel.parquet", dict(BASE, cap_field="fd_free_cap", w500=0.85, woth=0.13, noth=110, keep_mult=4.0, oth_universe="csi1000", veto_mask=LIVE + "/fd_veto_distress10_v2.parquet")),
}

ap = argparse.ArgumentParser(); ap.add_argument("--start", default="2026-09-15"); ap.add_argument("--end", default="2026-12-31"); ap.add_argument("--out", default=None)
a = ap.parse_args()
panel = Panel(LIVE + "/panel"); panel._dtype = np.float32
mem, _ = cp.membership(panel); bench1, _ = cp.benchmark_label1(panel.dates, "csi500")
res = {}
for name, (pred_path, cfg) in RULES.items():
    if not os.path.exists(pred_path) or (cfg.get("cap_mult") and not os.path.exists(cfg["cap_mult"])) or (cfg.get("veto_mask") and not os.path.exists(cfg["veto_mask"])):
        print(name, "skipped: missing inputs"); continue
    pred = pd.read_parquet(pred_path); pred.index = pd.DatetimeIndex(pred.index)
    full = dict(cp.DEFAULT_CFG, **cfg); b = cp.two_bucket_book(pred, panel, mem, bench1, full)
    live = b["live"]; w5, wo = float(full["w500"]), float(full["woth"]); bench = b["benchmark"]
    net = (w5 * (b["bucket500"]["portfolio"] - bench) + wo * (b["bucketoth"]["portfolio"] - bench) - (1 - w5 - wo) * bench - full["cost"] * b["turnover"])[live]
    df = pd.DataFrame({"index": bench[live], "net_excess": net, "portfolio": net + bench[live], "turnover": b["turnover"][live]}).loc[a.start:a.end]
    res[name] = df
    cum = lambda s: ((1 + s).prod() - 1) * 100
    print(f"== {name}: {df.index[0].date()}..{df.index[-1].date()} ({len(df)} signal days) | index {cum(df['index']):+.2f}%  portfolio {cum(df['portfolio']):+.2f}%  net excess {cum(df['net_excess']):+.2f}%  "
          f"| days>0 {int((df['net_excess'] > 0).sum())}/{len(df)}  mean {df['net_excess'].mean() * 1e4:+.1f} bp/d  turnover {df['turnover'].mean() * 100:.2f}%/d")
    print((df[["index", "net_excess"]] * 100).round(2).to_string())
if a.out:
    json.dump({k: (v * 100).round(4).reset_index().astype(str).to_dict(orient="records") for k, v in res.items()}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
