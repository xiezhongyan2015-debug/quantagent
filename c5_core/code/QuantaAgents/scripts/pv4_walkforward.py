"""Walk-forward rule selection (A17 item 1): for every year Y the rule is chosen on all earlier years only (criterion:
net Sharpe, or net mean subject to a Sharpe floor), then applied in Y. Reports the honest stitched performance next to
every fixed rule. Several series files (research years, forward years) are concatenated by column name.
python scripts/pv4_walkforward.py --series a.parquet [b.parquet ...] --pred-label base396 --first-year 2021 [--criterion sharpe|mean_sh1.5] [--out json]"""
import argparse
import json
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--series", nargs="+", required=True); ap.add_argument("--pred-label", required=True); ap.add_argument("--first-year", type=int, default=2021)
ap.add_argument("--criterion", default="sharpe"); ap.add_argument("--out", default=None)
a = ap.parse_args()
frames = []
for p in a.series:
    d = pd.read_parquet(p); d.index = pd.DatetimeIndex(d.index)
    d = d[[c for c in d.columns if c.split("|")[0].startswith(a.pred_label)]]
    d.columns = [c.split("|", 1)[1] for c in d.columns]
    frames.append(d.dropna(how="all"))
S = pd.concat(frames).sort_index()
S = S[~S.index.duplicated(keep="last")]
yrs = S.index.year


def sh(x):
    x = x.dropna()
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252)) if len(x) > 20 and x.std() > 0 else float("nan")


def score(x):
    if a.criterion == "sharpe":
        return sh(x)
    floor = float(a.criterion.split("sh")[1])
    return float(x.mean() * 252) if sh(x) >= floor else float("-inf")


chosen = {}; stitched = []
for Y in sorted(set(yrs)):
    if Y < a.first_year:
        continue
    past = S[yrs < Y]
    sc = {r: score(past[r]) for r in S.columns if past[r].notna().sum() > 200}
    if not sc:
        continue
    best = max(sc, key=lambda r: (sc[r] if np.isfinite(sc[r]) else -1e9))
    chosen[int(Y)] = best
    stitched.append(S.loc[yrs == Y, best])
W = pd.concat(stitched); wy = W.index.year
rep = {"criterion": a.criterion, "chosen": chosen,
       "walkforward": {str(y): {"net": float(W[wy == y].mean() * 252), "sharpe": sh(W[wy == y])} for y in sorted(set(wy))},
       "walkforward_research": {"net": float(W[wy <= 2024].mean() * 252), "sharpe": sh(W[wy <= 2024])}, "fixed": {}}
for r in S.columns:
    x = S[r]; sel = (yrs >= a.first_year) & (yrs <= 2024)
    rep["fixed"][r] = {"research_from_first": {"net": float(x[sel].mean() * 252), "sharpe": sh(x[sel])}}
    for y in sorted(set(yrs)):
        if y >= 2025:
            rep["fixed"][r][str(y)] = {"net": float(x[yrs == y].mean() * 252), "sharpe": sh(x[yrs == y])}
print("chosen per year:", chosen)
print("walk-forward: research(%d-2024) net %+.3f sharpe %.2f | " % (a.first_year, rep["walkforward_research"]["net"], rep["walkforward_research"]["sharpe"])
      + " ".join(f"{y}: {v['net']:+.3f}/{v['sharpe']:.2f}" for y, v in rep["walkforward"].items()))
for r, v in sorted(rep["fixed"].items(), key=lambda kv: -(kv[1]["research_from_first"]["sharpe"] if np.isfinite(kv[1]["research_from_first"]["sharpe"]) else -9))[:14]:
    print("  fixed %-30s research net %+.3f sharpe %.2f | %s" % (r, v["research_from_first"]["net"], v["research_from_first"]["sharpe"],
          " ".join(f"{y}: {v[y]['net']:+.3f}/{v[y]['sharpe']:.2f}" for y in v if y != "research_from_first")))
if a.out:
    json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
