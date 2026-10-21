"""A17 item 4: what would the same trade list earn if it were executed at another time of the execution day?
The book trades d(t, i) (fraction of NAV, + buy / - sell) are decided on the close of t and executed at the open of t+1
in the backtest. Executing at price P_alt instead of the open changes the daily PnL by  -sum_i d_i * (P_alt / P_open - 1)
(a buy that gets cheaper or a sale that gets dearer is a gain). Reports the annualised gain per alternative, split by
buys / sells and by bucket, per year, with the t-value of the daily series.

python scripts/pv4_exec_study.py --panel-dir P --exec-dir E --pred label=path [...] --rule-json rules.json --out out.json [--years 2019 2024]
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

ALTS = ("x_vwap5", "x_vwap30", "x_vwap_am", "x_vwap_day", "x_close")

ap = argparse.ArgumentParser()
ap.add_argument("--panel-dir", required=True); ap.add_argument("--exec-dir", required=True); ap.add_argument("--pred", action="append", required=True)
ap.add_argument("--rule-json", required=True); ap.add_argument("--out", required=True); ap.add_argument("--years", nargs=2, type=int, default=[2019, 2024])
a = ap.parse_args()
rules = json.loads(open(a.rule_json, encoding="utf-8").read() if os.path.exists(a.rule_json) else a.rule_json)
panel = Panel(a.panel_dir); panel._dtype = np.float32
dates = panel.dates; codes = list(panel.codes)
mem, _ = cp.membership(panel)
bench1, _ = cp.benchmark_label1(dates, "csi500")
xo = pd.read_parquet(os.path.join(a.exec_dir, "x_open.parquet")).reindex(index=dates, columns=codes)
# sanity: the minute-library open must agree with the panel open (both qfq; compare the overnight-free ratio open/close)
chk = (xo / pd.read_parquet(os.path.join(a.exec_dir, "x_close.parquet")).reindex(index=dates, columns=codes)) / (panel["open"] / panel["close"]) - 1
_c = chk.abs().stack()
print("open/close ratio vs panel: median abs diff %.5f, 99%% %.5f, cells %d" % (_c.median(), _c.quantile(0.99), len(_c)), flush=True)
rel = {}
for f in ALTS:
    r = (pd.read_parquet(os.path.join(a.exec_dir, f + ".parquet")).reindex(index=dates, columns=codes) / xo - 1)
    rel[f] = r.clip(-0.21, 0.21).shift(-1).to_numpy(np.float64)      # row t = execution day t+1
# what-if: the same trades done at the close of the signal day itself (needs a signal computed just before the close)
rel["p_prev_close"] = (panel["close"] / panel["open"].shift(-1) - 1).clip(-0.21, 0.21).to_numpy(np.float64)
ALTS = ALTS + ("p_prev_close",)
ysel = (dates.year >= a.years[0]) & (dates.year <= a.years[1])
rep = {}
for spec in a.pred:
    label, path = spec.split("=", 1)
    pred = pd.read_parquet(path); pred.index = pd.DatetimeIndex(pred.index)
    for rname, over in rules.items():
        cfg = dict(cp.DEFAULT_CFG, **over, keep_weights=True)
        book = cp.two_bucket_book(pred, panel, mem, bench1, cfg)
        live = book["live"].to_numpy(bool) & ysel
        d5 = float(cfg["w500"]) * (book["bucket500"]["weights"] - book["bucket500"]["weights_pre"]).astype(np.float64)
        do = float(cfg["woth"]) * (book["bucketoth"]["weights"] - book["bucketoth"]["weights_pre"]).astype(np.float64)
        net = (book["excess"] - cfg["cost"] * book["turnover"]).where(book["live"])[ysel]
        out = {"net_open": float(net.mean() * 252), "sharpe_open": float(net.mean() / net.std() * np.sqrt(252)), "turnover": float(book["turnover"][live].mean()), "alts": {}}
        for f in ALTS:
            r = rel[f]; ok = np.isfinite(r)
            parts = {}
            for nm, d in (("all", d5 + do), ("b500", d5), ("both", do)):
                g = -(np.where(ok, d * r, 0.0))
                day = pd.Series(g.sum(axis=1), index=dates)[live]
                buys = pd.Series(np.where(d > 0, g, 0.0).sum(axis=1), index=dates)[live]; sells = pd.Series(np.where(d < 0, g, 0.0).sum(axis=1), index=dates)[live]
                parts[nm] = {"gain_ann": float(day.mean() * 252), "t": float(day.mean() / day.std(ddof=1) * np.sqrt(len(day))) if day.std() > 0 else 0.0,
                             "buys_ann": float(buys.mean() * 252), "sells_ann": float(sells.mean() * 252),
                             "by_year": {str(y): float(day[day.index.year == y].mean() * 252) for y in sorted(set(day.index.year))}}
            tot = net + pd.Series(-(np.where(ok, (d5 + do) * r, 0.0)).sum(axis=1), index=dates)[ysel].where(net.notna())
            parts["book_net"] = float(tot.mean() * 252); parts["book_sharpe"] = float(tot.mean() / tot.std() * np.sqrt(252))
            cover = float((np.abs(d5 + do) * ok)[live].sum() / max(np.abs(d5 + do)[live].sum(), 1e-12)); parts["trade_coverage"] = cover
            out["alts"][f] = parts
            print(f"{label}|{rname} {f:11s}: gain {parts['all']['gain_ann']:+.4f} (t {parts['all']['t']:+.1f}; buys {parts['all']['buys_ann']:+.4f} sells {parts['all']['sells_ann']:+.4f}; 500 {parts['b500']['gain_ann']:+.4f} other {parts['both']['gain_ann']:+.4f}) "
                  f"book {out['net_open']:+.3f}/{out['sharpe_open']:.2f} -> {parts['book_net']:+.3f}/{parts['book_sharpe']:.2f} cover {cover:.3f}", flush=True)
        rep[f"{label}|{rname}"] = out
json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(a.out)
