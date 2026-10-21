"""Retrain a saved member set inside the competition universe (CSI300 U CSI500 U CSI1000).

Same LightGBM rank-target expanding-window model as the frozen all-A combination, but training rows, training
label ranks and (optionally) the member percentile features are restricted to / re-ranked within the union.

python scripts/competition_union_combine.py --members-file /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/batches/combinations_v11_396_blend51020.json \
    --universe union --rerank none --label-blend 5 10 20 --tag u396_union_norerank
Options:
  --universe all_a|union      training/test universe (all_a reproduces the frozen model up to seed noise)
  --rerank none|union         none: member features are the stored all-A percentiles; union: percentiles re-ranked within
                              the union each date (arrays cached under <root>/competition/union_ranks)
  --panel-dir / --ranks-dir   research panel + pool_ranks by default; pass the OOS panel and its ranks dir for 2025/2026
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
from quanta_agents.factor_lab_a.evaluate import Evaluator, rank_ic, annual_table  # noqa: E402
from quanta_agents.factor_lab_a.ledger import Store  # noqa: E402
from quanta_agents.factor_lab_a.combine import LazyRanks, rolling_combination_lowmem  # noqa: E402
from quanta_agents.factor_lab_a.credibility import rank_path_any  # noqa: E402
from quanta_agents.factor_lab_a.trading import blended_label  # noqa: E402


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def rerank_within(src_path: str, dst_path: str, mask_np: np.ndarray):
    with open(src_path, "rb") as fh:
        arr = np.load(fh).astype(np.float32)
    arr = np.where(mask_np, arr, np.nan)
    out = pd.DataFrame(arr).rank(axis=1, pct=True).to_numpy(np.float16)
    tmp = dst_path + ".tmp.npy"
    np.save(tmp, out)
    os.replace(tmp, dst_path)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research")
    ap.add_argument("--panel-dir", default=None)
    ap.add_argument("--ranks-dir", nargs="*", default=None, help="directories of <id>.npy percentile arrays, searched in order (default: research pool_ranks/cand_ranks)")
    ap.add_argument("--members-file", required=True)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--universe", default="union", choices=["all_a", "union", "csi300", "csi500", "csi1000"])
    ap.add_argument("--rerank", default="none", choices=["none", "union"])
    ap.add_argument("--label-blend", nargs="*", type=int, default=[5, 10, 20])
    ap.add_argument("--label-capneutral", type=int, default=0, help=">0: alias of --label-neutral-field log_cap --label-neutral-q N (size-neutral label)")
    ap.add_argument("--label-neutral-field", default=None, choices=["log_cap", "float_market_cap", "total_market_cap", "turnover20", "vol20", "mom20"],
                    help="the blended training label is re-ranked inside --label-neutral-q groups of this style field per date (style-neutral label; A15 item 1)")
    ap.add_argument("--label-neutral-q", type=int, default=5)
    ap.add_argument("--crash-label", nargs=2, type=float, default=None, metavar=("HORIZON", "DROP"),
                    help="binary training label: 1 if label_<HORIZON> < -DROP (e.g. 10 0.15); overrides --label-blend; sets target=binary and objective=binary")
    ap.add_argument("--weight-universe", nargs="*", default=None, metavar="UNIVERSE=W", help="A17 item 2: training-row weight multipliers, e.g. union=3 csi500=5 (max over matching universes; others 1)")
    ap.add_argument("--top-weight", type=float, default=0.0, help="A17 item 2: multiply row weights by 1 + A * pct^2 of the training label (top-heavy fit)")
    ap.add_argument("--binary-label", nargs=2, default=None, metavar=("SIDE", "FRAC"), help="A17 item 2: specialist model. SIDE top|bottom, FRAC e.g. 0.2: label = 1 if the blended label percentile (inside --label-universe) is in that tail; bottom models are stored negated (higher = safer)")
    ap.add_argument("--label-universe", default=None, help="universe for the binary-label percentile (default: --universe)")
    ap.add_argument("--train-years", nargs="*", type=int, default=[2016, 2017, 2018])
    ap.add_argument("--target-years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--day-step", type=int, default=4)
    ap.add_argument("--params", default=None)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)

    store = Store(a.root); proto = store.protocol()
    panel_dir = a.panel_dir or proto["panel_dir"]
    panel = Panel(panel_dir); panel._dtype = np.float32
    out_dir = a.out_dir or os.path.join(a.root, "competition", "combos")
    os.makedirs(out_dir, exist_ok=True)
    with open(a.members_file, encoding="utf-8") as fh:
        d = json.load(fh)
    if isinstance(d, dict):
        members = d["members"]
    elif d and isinstance(d[0], dict) and "features" in d[0]:
        members = d[a.index]["features"]
    else:
        members = d
    members = list(members)
    _log(f"{len(members)} members; universe={a.universe} rerank={a.rerank} panel={panel_dir}")

    def src_path(cid):
        if a.ranks_dir:
            for d in a.ranks_dir:
                p = os.path.join(d, f"{cid}.npy")
                if os.path.exists(p):
                    return p
            return None
        return rank_path_any(store, cid)

    missing = [m for m in members if src_path(m) is None]
    if missing:
        raise SystemExit(f"missing rank arrays for {len(missing)} members, e.g. {missing[:3]}")
    ranks = {}
    if a.rerank == "union":
        umask = panel.mask("union").to_numpy(bool)
        panel_tag = os.path.basename(os.path.normpath(panel_dir)) + "_" + os.path.basename(os.path.normpath(os.path.dirname(panel_dir)))
        panel_tag += "_" + ("+".join(os.path.basename(os.path.normpath(d)) for d in a.ranks_dir) if a.ranks_dir else "poolranks")   # cache key includes the rank source (review fix M7)
        rr_dir = os.path.join(a.root, "competition", "union_ranks", panel_tag)
        os.makedirs(rr_dir, exist_ok=True)
        t0 = time.time()
        for i, m in enumerate(members):
            dst = os.path.join(rr_dir, f"{m}.npy")
            if not os.path.exists(dst):
                rerank_within(src_path(m), dst, umask)
            ranks[m] = LazyRanks(dst)
            if (i + 1) % 50 == 0:
                _log(f"  re-ranked {i + 1}/{len(members)} ({time.time() - t0:.0f}s)")
    else:
        for m in members:
            ranks[m] = LazyRanks(src_path(m))

    ev = Evaluator(panel, universe=a.universe, label="label_5", min_stocks=50, train_years=tuple(a.train_years), target_years=tuple(a.target_years))
    params = json.loads(a.params) if a.params else None
    target = "rank"
    if a.crash_label:
        h, drop = int(a.crash_label[0]), float(a.crash_label[1])
        lab = panel[f"label_{h}"]
        label_override = (lab < -drop).astype(np.float32).where(lab.notna() & panel.mask(a.universe))
        # the last h+1 signal dates of each year are blanked like blended_label does, so the label never reaches the test year
        year = pd.Series(panel.dates.year, index=panel.dates)
        for y, idx in year.groupby(year).groups.items():
            label_override.loc[idx[-(h + 1):]] = np.nan
        target = "binary"; params = dict(params or {}, objective="binary")
        _log(f"crash label: label_{h} < -{drop:.2f}; positive rate {float(np.nanmean(label_override.to_numpy())):.3f}")
    else:
        label_override = blended_label(panel, horizons=tuple(a.label_blend), mask=panel.mask(a.universe)) if a.label_blend else None
        nfield = a.label_neutral_field or ("log_cap" if a.label_capneutral else None)
        nq = int(a.label_capneutral or a.label_neutral_q)
        if nfield and label_override is not None:
            from quanta_agents.factor_lab_a.competition import cap_neutral_within, style_field
            umask = panel.mask(a.universe)
            label_override = cap_neutral_within(label_override, style_field(panel, nfield), umask & label_override.notna(), nq)
            _log(f"training label re-ranked inside {nq} groups of {nfield} (style-neutral label)")
        a.label_neutral_field = nfield; a.label_neutral_q = nq if nfield else 0
    row_weight = None
    if a.binary_label:
        side, frac = a.binary_label[0], float(a.binary_label[1])
        lu = panel.mask(a.label_universe or a.universe)
        pct = label_override.where(lu).rank(axis=1, pct=True)
        hit = (pct >= 1 - frac) if side == "top" else (pct <= frac)
        label_override = hit.astype(np.float32).where(pct.notna())
        target = "binary"; params = dict(params or {}, objective="binary")
        _log(f"binary label: {side} {frac:.2f} inside {a.label_universe or a.universe}; positive rate {float(np.nanmean(label_override.to_numpy())):.3f}")
    if a.weight_universe or a.top_weight > 0:
        rw = pd.DataFrame(1.0, index=panel.dates, columns=panel.codes, dtype=np.float32)
        for spec in (a.weight_universe or []):
            uni, w = spec.split("="); m = panel.mask(uni)
            rw = rw.where(~m, np.maximum(rw, float(w)))
        if a.top_weight > 0 and label_override is not None and target != "binary":
            pct = label_override.where(panel.mask(a.universe)).rank(axis=1, pct=True)
            rw = rw * (1.0 + a.top_weight * (pct.fillna(0.5) ** 2)).astype(np.float32)
        row_weight = rw
        _log(f"row weights: universes {a.weight_universe} top_weight {a.top_weight}; mean {float(rw.to_numpy().mean()):.2f}")
    workdir = os.path.join(a.root, "batches", f"tmp_lowmem_{a.tag}")
    res, pred = rolling_combination_lowmem(panel, ev, ranks, members, target=target, train_years=tuple(a.train_years), target_years=tuple(a.target_years),
                                           day_step=a.day_step, params=params, seed=a.seed, n_seeds=a.seeds, workdir=workdir, log=_log, label_override=label_override, row_weight=row_weight)
    if a.crash_label or (a.binary_label and a.binary_label[0] == "bottom"):
        pred = -pred   # store as a score: higher = safer (lower crash / bottom-tail probability)
    ppath = os.path.join(out_dir, f"pred_{a.tag}.parquet")
    pred.astype(np.float32).to_parquet(ppath)
    # RankIC inside each competition universe (label_5, eval_ok purge, min 50 names)
    ics = {}
    for name in ("all_a", "union", "csi300", "csi500", "csi1000"):
        try:
            msk = panel.mask(name) & (panel["eval_ok"] > 0)
        except KeyError:
            continue
        r, n = rank_ic(pred, panel["label_5"], msk, min_stocks=50)
        ics[name] = {str(y): v["mean"] for y, v in annual_table(r, tuple(a.target_years)).items()}
    out = {"tag": a.tag, "members_file": a.members_file, "index": a.index, "n_members": len(members), "universe": a.universe, "rerank": a.rerank,
           "label_blend": a.label_blend, "crash_label": a.crash_label, "label_capneutral": a.label_capneutral, "label_neutral_field": a.label_neutral_field, "label_neutral_q": a.label_neutral_q, "weight_universe": a.weight_universe, "top_weight": a.top_weight, "binary_label": a.binary_label, "label_universe": a.label_universe, "target": target, "seed": a.seed, "seeds": a.seeds, "day_step": a.day_step, "params": params, "panel_dir": panel_dir, "ranks_dir": a.ranks_dir,
           "train_years": a.train_years, "target_years": a.target_years, "prediction_path": ppath, "features": members,
           "model": "lgbm", "target": "rank", "fits": res.get("fits"), "annual_rank_ic_by_universe": ics,
           "target_mean_rank_ic": res.get("target_mean_rank_ic"), "target_worst_rank_ic": res.get("target_worst_rank_ic"), "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(out_dir, f"combo_{a.tag}.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
    for k, v in ics.items():
        vals = [x for x in v.values() if x is not None]
        if not vals:
            _log(f"  IC {k:<8} n/a (fewer than 50 scored names)"); continue
        _log("  IC %-8s mean %.4f worst %.4f | %s" % (k, np.mean(vals), np.min(vals), " ".join(f"{y}:{x:+.3f}" for y, x in v.items() if x is not None)))
    _log(f"saved {ppath}")


if __name__ == "__main__":
    main()
