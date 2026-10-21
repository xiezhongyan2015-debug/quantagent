"""Cross-retrieval and composite construction over the factor pool (A12.1).

Borrowed practices:
- AlphaPROBE: program picks parents by an explicit score (quality, novelty, under-exploration, depth penalty)
  and admits children through a quality route or a complementarity route.
- QuantaAlpha: every child records its parents, the modification intent (operator) and the outcome delta,
  forming a searchable trajectory ledger.
- AlphaAgent: composites are expressed as a contract (operator + parents) that is re-computable and
  checked for numeric similarity before admission.

Composite operators act on per-date percentile ranks of the (direction-applied) parents:
  sum      rx + ry                      (equal-weight rank blend)
  prod     (rx-0.5)*(ry-0.5)            (agreement / interaction)
  gate_hi  rx * (ry > 0.5)              (x only where y is high)
  gate_lo  rx * (ry <= 0.5)             (x only where y is low)
  resid    rx - beta*ry (per date OLS)  (x orthogonalised to y)
  min      min(rx, ry)                  (both must be high)
  diff     rx - ry                      (x against y)
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd

OPS = ("sum", "prod", "gate_hi", "gate_lo", "resid", "min", "diff")


def composite(rx: np.ndarray, ry: np.ndarray, op: str) -> np.ndarray:
    with np.errstate(all="ignore"):
        if op == "sum":
            return rx + ry
        if op == "prod":
            return (rx - 0.5) * (ry - 0.5)
        if op == "gate_hi":
            return np.where(ry > 0.5, rx, np.nan)
        if op == "gate_lo":
            return np.where(ry <= 0.5, rx, np.nan)
        if op == "min":
            return np.minimum(rx, ry)
        if op == "diff":
            return rx - ry
        if op == "resid":
            m = np.isfinite(rx) & np.isfinite(ry)
            x = np.where(m, rx, 0.0); y = np.where(m, ry, 0.0); n = np.maximum(m.sum(axis=1), 1)
            xm = x.sum(axis=1) / n; ym = y.sum(axis=1) / n
            xc = np.where(m, rx - xm[:, None], 0.0); yc = np.where(m, ry - ym[:, None], 0.0)
            beta = (xc * yc).sum(axis=1) / np.maximum((yc ** 2).sum(axis=1), 1e-12)
            return np.where(m, xc - beta[:, None] * yc, np.nan)
    raise KeyError(op)


def parent_scores(table: pd.DataFrame, results: dict, children_count: dict, depth: dict) -> pd.DataFrame:
    """AlphaPROBE-style parent score: quality + novelty + under-exploration - depth."""
    t = table[table["in_pool"] == True].dropna(subset=["mean_ric"]).copy()
    q_ic = t["mean_ric"].clip(lower=0) / max(t["mean_ric"].max(), 1e-9)
    if "long_net_sharpe" in t and t["long_net_sharpe"].notna().any():
        ls = t["long_net_sharpe"].fillna(0).clip(lower=0)
        q_long = ls / max(ls.max(), 1e-9)
        q = 0.5 * q_ic + 0.5 * q_long
    else:
        q = q_ic
    nov = 1.0 - t["max_pool_corr"].fillna(1.0).clip(0, 1)
    explore = 1.0 / (1.0 + t["id"].map(lambda i: children_count.get(i, 0)))
    dep = t["id"].map(lambda i: depth.get(i, 0))
    t["parent_score"] = 0.5 * q + 0.25 * nov + 0.25 * explore - 0.1 * dep
    return t.sort_values("parent_score", ascending=False)


def cluster_pairs(parents: pd.DataFrame, sig_loader, max_corr=0.5, max_pairs=60) -> list[tuple[str, str, float]]:
    """Pairs of parents whose sampled-rank correlation is below max_corr (different clusters)."""
    ids = list(parents["id"])
    sigs = {i: sig_loader(i) for i in ids}
    sigs = {k: v for k, v in sigs.items() if v is not None}
    pairs = []
    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1:]:
            if a not in sigs or b not in sigs:
                continue
            sa, sb = sigs[a], sigs[b]
            m = np.isfinite(sa) & np.isfinite(sb)
            n = m.sum(axis=1)
            x = np.where(m, sa, 0); y = np.where(m, sb, 0)
            with np.errstate(all="ignore"):
                xm = x.sum(axis=1) / np.maximum(n, 1); ym = y.sum(axis=1) / np.maximum(n, 1)
                xc = np.where(m, sa - xm[:, None], 0); yc = np.where(m, sb - ym[:, None], 0)
                r = (xc * yc).sum(axis=1) / np.sqrt((xc ** 2).sum(axis=1) * (yc ** 2).sum(axis=1))
            c = float(np.nanmean(np.abs(r[n >= 100])))
            if np.isfinite(c) and c < max_corr:
                pairs.append((a, b, c))
    pairs.sort(key=lambda p: p[2])
    return pairs[:max_pairs]


def append_lineage(root: str, record: dict):
    with open(os.path.join(root, "lineage.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_lineage(root: str) -> list[dict]:
    p = os.path.join(root, "lineage.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]
