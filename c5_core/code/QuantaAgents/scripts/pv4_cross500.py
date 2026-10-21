"""A17 item 6: targeted composites - parents and fitness judged by the LONG side inside CSI500 (training years only).
Fitness of a rank array = mean 5-day excess (vs the CSI500 members' equal-weight mean) of its top decile inside CSI500,
on non-overlapping training dates, with its t-value. Parents = the members with the best long-side t; pairs need a low
in-bucket correlation; a composite is kept when its long-side t >= --min-t and its mean beats both parents by --gain.
Kept composites are written as full-panel percentile arrays (<id>.npy, same layout as the pool rank arrays) plus a
members file (base members + composites) for a retrain. The research ledger is not touched.

python scripts/pv4_cross500.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --members-file <json> --ranks-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/pool_ranks \
    --out-ranks <dir> --out-members <json> [--train-end 2018-12-31] [--parents 40] [--pairs 120] [--keep 30]
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402
from quanta_agents.factor_lab_a import crossover as xo  # noqa: E402

OPS = ("sum", "min", "prod", "gate_hi", "gate_lo", "diff")


def _log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True); ap.add_argument("--members-file", required=True); ap.add_argument("--ranks-dir", nargs="+", required=True)
    ap.add_argument("--out-ranks", required=True); ap.add_argument("--out-members", required=True); ap.add_argument("--train-start", default="2016-01-01"); ap.add_argument("--train-end", default="2018-12-31")
    ap.add_argument("--parents", type=int, default=40); ap.add_argument("--pairs", type=int, default=120); ap.add_argument("--keep", type=int, default=30)
    ap.add_argument("--min-t", type=float, default=3.0); ap.add_argument("--gain", type=float, default=1.15); ap.add_argument("--max-pair-corr", type=float, default=0.5); ap.add_argument("--max-kept-corr", type=float, default=0.8)
    ap.add_argument("--extra-panel", nargs=2, action="append", default=[], metavar=("PANEL_DIR", "RANKS_DIR..OUT"), help="also write the kept composites on another panel: PANEL_DIR 'ranksdir1;ranksdir2;outdir'")
    a = ap.parse_args(argv)
    d = json.load(open(a.members_file, encoding="utf-8"))
    members = list(d["members"] if isinstance(d, dict) else (d[0]["features"] if d and isinstance(d[0], dict) else d))
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    mem, _ = cp.membership(panel); m5 = mem["csi500"].to_numpy(bool)
    dates = panel.dates
    tr = np.where((dates >= pd.Timestamp(a.train_start)) & (dates <= pd.Timestamp(a.train_end)))[0]
    lab = panel["label_5"].to_numpy(np.float32)
    rows = [i for i in tr[::5] if np.isfinite(lab[i][m5[i]]).sum() > 300][:-2]           # non-overlapping 5-day labels, last ones purged
    rows = np.array(rows); _log(f"{len(members)} members, {len(rows)} training dates ({dates[rows[0]].date()}..{dates[rows[-1]].date()})")
    M = m5[rows]; L = np.where(M, lab[rows], np.nan); ex = L - np.nanmean(L, axis=1, keepdims=True)

    def path(cid):
        for rd in a.ranks_dir:
            p = os.path.join(rd, f"{cid}.npy")
            if os.path.exists(p):
                return p
        raise FileNotFoundError(cid)

    def fitness(R):
        """R: rows x codes scores (NaN allowed). Top decile inside CSI500 each date -> mean excess series."""
        X = np.where(M & np.isfinite(ex), R, np.nan)
        thr = np.nanquantile(X, 0.9, axis=1, keepdims=True)
        top = X >= thr
        n = top.sum(axis=1)
        s = np.where(n >= 20, np.nansum(np.where(top, ex, 0.0), axis=1) / np.maximum(n, 1), np.nan)
        s = s[np.isfinite(s)]
        return (float(s.mean()), float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))) if len(s) > 30 and s.std() > 0 else (float("nan"), float("nan"))

    S = {}
    stats = []
    for k, cid in enumerate(members):
        r = np.asarray(np.load(path(cid), mmap_mode="r")[rows], dtype=np.float32); S[cid] = r
        mu, t = fitness(r); stats.append({"id": cid, "long500_mean": mu, "long500_t": t})
    st = pd.DataFrame(stats).sort_values("long500_t", ascending=False)
    _log("long-side inside CSI500 (5-day top-decile excess): members with t>=2: %d, t>=3: %d; median mean %.4f" % ((st.long500_t >= 2).sum(), (st.long500_t >= 3).sum(), st.long500_mean.median()))
    par = st[(st.long500_mean > 0)].head(a.parents)
    _log("parents: %d, t range %.1f..%.1f" % (len(par), par.long500_t.min(), par.long500_t.max()))

    def flat(r):
        x = np.where(M, r, np.nan).ravel(); return x

    F = {cid: flat(S[cid]) for cid in par.id}

    def corr(x, y):
        m = np.isfinite(x) & np.isfinite(y)
        return float(np.corrcoef(x[m], y[m])[0, 1]) if m.sum() > 1000 else 1.0

    pairs = []
    ids = list(par.id); tmap = dict(zip(par.id, par.long500_t)); mmap_ = dict(zip(par.id, par.long500_mean))
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            c = corr(F[ids[i]], F[ids[j]])
            if abs(c) < a.max_pair_corr:
                pairs.append((ids[i], ids[j], c, tmap[ids[i]] + tmap[ids[j]]))
    pairs = sorted(pairs, key=lambda p: -p[3])[:a.pairs]
    _log(f"{len(pairs)} low-correlation pairs")
    cands = []; screened = 0
    for x, y, c, _ in pairs:
        for op in OPS:
            for p, q in ((x, y), (y, x)):
                if op in ("sum", "min", "prod") and p > q:
                    continue
                arr = xo.composite(S[p], S[q], op); screened += 1
                mu, t = fitness(arr)
                if np.isfinite(t) and t >= a.min_t and mu >= a.gain * max(mmap_[p], mmap_[q]):
                    cands.append({"op": op, "parents": [p, q], "pair_corr": c, "long500_mean": mu, "long500_t": t, "parent_means": [mmap_[p], mmap_[q]], "parent_ts": [tmap[p], tmap[q]]})
    _log(f"screened {screened} composites; {len(cands)} pass (t >= {a.min_t}, mean >= {a.gain} x best parent)")
    cands = sorted(cands, key=lambda r: -r["long500_t"])
    kept = []; keptF = []
    for r in cands:
        f = flat(xo.composite(S[r["parents"][0]], S[r["parents"][1]], r["op"]))
        if all(abs(corr(f, g)) < a.max_kept_corr for g in keptF):
            r["id"] = "x5" + hashlib.md5(f"{r['op']}({r['parents'][0]},{r['parents'][1]})".encode()).hexdigest()[:14]
            kept.append(r); keptF.append(f)
        if len(kept) >= a.keep:
            break
    _log(f"kept {len(kept)} composites after the mutual-correlation filter")
    for r in kept[:12]:
        _log("  %s %-7s t %.1f mean %.4f (parents %.4f / %.4f, pair corr %.2f)" % (r["id"], r["op"], r["long500_t"], r["long500_mean"], r["parent_means"][0], r["parent_means"][1], r["pair_corr"]))

    def write_full(panel_dir, ranks_dirs, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        for r in kept:
            ps = []
            for cid in r["parents"]:
                p = next((os.path.join(rd, f"{cid}.npy") for rd in ranks_dirs if os.path.exists(os.path.join(rd, f"{cid}.npy"))), None)
                if p is None:
                    raise FileNotFoundError(f"{cid} in {ranks_dirs}")
                ps.append(np.asarray(np.load(p, mmap_mode="r"), dtype=np.float32))
            arr = xo.composite(ps[0], ps[1], r["op"])
            out = pd.DataFrame(arr).rank(axis=1, pct=True).to_numpy(np.float32)             # per-date percentile, NaN kept
            np.save(os.path.join(out_dir, f"{r['id']}.npy"), out)
        _log(f"wrote {len(kept)} arrays to {out_dir}")

    write_full(a.panel_dir, a.ranks_dir, a.out_ranks)
    for pdir, spec in a.extra_panel:
        parts = spec.split(";"); write_full(pdir, parts[:-1], parts[-1])
    os.makedirs(os.path.dirname(a.out_members), exist_ok=True)
    json.dump({"tag": os.path.splitext(os.path.basename(a.out_members))[0], "base": os.path.basename(a.members_file), "n": len(members) + len(kept), "new_members": [r["id"] for r in kept],
               "members": members + [r["id"] for r in kept], "composites": kept, "member_long500": st.to_dict("records")}, open(a.out_members, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _log(f"members file {a.out_members}: {len(members)} + {len(kept)}")
    print("CROSS500_DONE", flush=True)


if __name__ == "__main__":
    main()
