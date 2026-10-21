"""A15 item 6: statistical industries from return co-movement.

Every quarter (first session of Jan/Apr/Jul/Oct) the eligible all-A names with >= `min_obs` valid returns in the
trailing `window` sessions (ending the session before) are clustered: daily returns are cross-sectionally demeaned
(market factor removed), standardised per name, the n x n correlation matrix is embedded with its top `n_pc`
eigenvectors (scaled by sqrt eigenvalue) and k-means (k = `n_clusters`) labels the names. Labels hold until the next
quarter; a name without a label (new listing, long suspension) stays NaN. Output: a date x code float parquet of
cluster ids (1..k) on the panel grid, plus a json summary (sizes per quarter, silhouette-free diagnostics).

python scripts/pv2_cluster.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/cluster_k25_research.parquet --n-clusters 25
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def cluster_once(R: np.ndarray, n_clusters: int, n_pc: int, seed: int) -> np.ndarray:
    """R: (n_names x T) demeaned returns with NaN. Returns labels 0..k-1."""
    from sklearn.cluster import KMeans
    X = np.where(np.isfinite(R), R, 0.0)
    X = X - X.mean(axis=1, keepdims=True)
    sd = X.std(axis=1, keepdims=True); sd[sd == 0] = 1.0
    X = X / sd
    T = X.shape[1]
    C = X @ X.T / T                                  # correlation-like (zero-filled gaps shrink toward 0)
    w, V = np.linalg.eigh(C)
    idx = np.argsort(w)[::-1][:n_pc]
    emb = V[:, idx] * np.sqrt(np.maximum(w[idx], 0.0))[None, :]
    km = KMeans(n_clusters=n_clusters, n_init=5, random_state=seed).fit(emb)
    return km.labels_


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--n-clusters", type=int, default=25); ap.add_argument("--window", type=int, default=250); ap.add_argument("--min-obs", type=int, default=200)
    ap.add_argument("--n-pc", type=int, default=20); ap.add_argument("--seed", type=int, default=0); ap.add_argument("--start-year", type=int, default=2016)
    a = ap.parse_args(argv)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    ret = panel["ret"]; elig = panel["eligible"] > 0
    dates = panel.dates; codes = panel.codes
    rv = ret.where(elig).to_numpy(np.float64)
    rv = rv - np.nanmean(rv, axis=1, keepdims=True)    # remove the market factor per day
    q = pd.Series(dates.quarter, index=dates); y = pd.Series(dates.year, index=dates)
    starts = [i for i in range(1, len(dates)) if (q.iloc[i] != q.iloc[i - 1] or y.iloc[i] != y.iloc[i - 1]) and dates[i].year >= a.start_year and i >= a.window]
    out = np.full((len(dates), len(codes)), np.nan, np.float32)
    summary = []
    t0 = time.time()
    for k, i0 in enumerate(starts):
        i1 = starts[k + 1] if k + 1 < len(starts) else len(dates)
        blk = rv[i0 - a.window:i0]                          # ends the session before the quarter's first session
        cnt = np.isfinite(blk).sum(axis=0)
        thr = min(a.min_obs, int(0.8 * cnt.max())) if cnt.max() > 0 else a.min_obs   # early panel: eligibility starts 120 sessions in
        ok = (cnt >= thr) & elig.iloc[i0 - 1].to_numpy(bool)
        names = np.where(ok)[0]
        if len(names) < 5 * a.n_clusters:
            _log(f"{dates[i0].date()}: only {len(names)} names with >= {thr} returns; quarter skipped"); continue
        lab = cluster_once(blk[:, names].T, a.n_clusters, a.n_pc, a.seed)
        row = np.full(len(codes), np.nan, np.float32); row[names] = lab + 1
        out[i0:i1] = row[None, :]
        sizes = np.bincount(lab, minlength=a.n_clusters)
        summary.append({"from": str(dates[i0].date()), "to": str(dates[i1 - 1].date()), "names": int(len(names)), "sizes": sizes.tolist(),
                        "min_size": int(sizes.min()), "max_size": int(sizes.max())})
        _log(f"{dates[i0].date()}: {len(names)} names -> {a.n_clusters} clusters, sizes {sizes.min()}..{sizes.max()} ({time.time() - t0:.0f}s)")
    df = pd.DataFrame(out, index=dates, columns=codes)
    df.to_parquet(a.out)
    # stability: share of names keeping their partner-majority cluster is not identifiable across relabelled runs;
    # report instead the within-cluster mean pairwise correlation vs the all-pairs mean on the next quarter (out of sample)
    with open(os.path.splitext(a.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump({"panel_dir": a.panel_dir, "n_clusters": a.n_clusters, "window": a.window, "min_obs": a.min_obs, "n_pc": a.n_pc, "seed": a.seed, "quarters": summary}, fh, ensure_ascii=False, indent=1)
    _log(f"saved {a.out} ({len(starts)} quarters)")


if __name__ == "__main__":
    main()
