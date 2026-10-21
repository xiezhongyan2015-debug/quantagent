"""A16: statistical-industry (cluster) based scores as candidate long-side signals inside CSI500.
  cluster_mom_W : the equal-weight W-session cumulative return of the stock's cluster (sector momentum)
  resid_mom_W   : the stock's W-session return minus its cluster's (residual momentum, sector effect removed)
Direction is fixed on 2016-2018 inside the chosen universe (daily RankIC vs label_5); the sign-applied value is written
as a score parquet (NaN outside the all-A eligibility mask), one file per score.

python scripts/pv3_cluster_scores.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --clusters /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/cluster_k25_research.parquet --out-dir <dir> [--windows 20 60 120]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a.evaluate import rank_ic, annual_table  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--clusters", required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--windows", nargs="*", type=int, default=[20, 60, 120]); ap.add_argument("--universe", default="csi500"); ap.add_argument("--train-years", nargs="*", type=int, default=[2016, 2017, 2018])
    ap.add_argument("--eval-years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024])
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    cl = pd.read_parquet(a.clusters); cl.index = pd.DatetimeIndex(cl.index); cl = cl.reindex(index=panel.dates, columns=panel.codes)
    elig = panel.mask("all_a"); ret = panel["ret"].where(elig)
    logc = np.log1p(ret.astype(np.float64))
    mask_eval = panel.mask(a.universe) & (panel["eval_ok"] > 0)
    summary = {}
    for W in a.windows:
        cum = logc.rolling(W, min_periods=int(0.8 * W)).sum()          # stock W-session log return
        # cluster mean per date: group columns by cluster id (ids change quarterly -> compute per date with groupby on the transposed frame is slow;
        # instead loop over cluster ids: mean over members)
        ids = np.unique(cl.to_numpy()[np.isfinite(cl.to_numpy())])
        cmean = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes, dtype=np.float64)
        cv = cl.to_numpy(); cumv = cum.to_numpy(np.float64)
        out = np.full(cumv.shape, np.nan)
        for g in ids:
            m = (cv == g)
            vals = np.where(m, cumv, np.nan)
            mu = np.nanmean(vals, axis=1)                                # per-date cluster mean
            out = np.where(m, mu[:, None], out)
        cmean = pd.DataFrame(out, index=panel.dates, columns=panel.codes)
        scores = {f"cluster_mom_{W}": cmean, f"resid_mom_{W}": cum - cmean}
        for name, f in scores.items():
            r, n = rank_ic(f, panel["label_5"], mask_eval, min_stocks=50)
            tr = r[np.isin(r.index.year, a.train_years)].dropna()
            d = int(np.sign(tr.mean())) if len(tr) > 20 else 0; t = float(tr.mean() / tr.std(ddof=1) * np.sqrt(len(tr))) if len(tr) > 20 else None
            ev = (r * d)[np.isin(r.index.year, a.eval_years)].dropna()
            ann = annual_table(r * d, tuple(a.eval_years))
            s = (f * (d or 1)).where(elig).astype(np.float32)
            s.to_parquet(os.path.join(a.out_dir, f"{name}_{a.universe}.parquet"))
            summary[name] = {"direction": d, "train_t": t, "eval_ic_mean": float(ev.mean()) if len(ev) else None, "eval_ic_worst": min((v["mean"] for v in ann.values() if v["mean"] is not None), default=None),
                             "annual": {str(y): (round(v["mean"], 4) if v["mean"] is not None else None) for y, v in ann.items()}}
            print(name, json.dumps(summary[name]))
    with open(os.path.join(a.out_dir, f"cluster_scores_{a.universe}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
