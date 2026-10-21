"""Independent replay of a saved combination result.

Recomputes every member expression from the frozen panel via the DSL (not from cached pool frames),
re-derives each member's direction from the training years, refits the combination model per year and
recomputes annual RankIC. Compares against the saved record. Usage:

  python -m quanta_agents.factor_lab_a.replay --root F:/A_Layer_Research --file batches/combinations_pool_v3_nanfix.json --index 0
"""
from __future__ import annotations
import argparse
import json
import os
import time
import numpy as np
import pandas as pd

from .cli import get_ctx, _log, compute_candidate, pct_ranks
from .combine import rolling_combination
from .evaluate import rank_ic


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True); p.add_argument("--file", required=True); p.add_argument("--index", type=int, default=0)
    p.add_argument("--day-step", type=int, default=None); p.add_argument("--seed", type=int, default=0)
    a = p.parse_args(argv)
    store, proto, panel, compiler, ev = get_ctx(a.root)
    path = a.file if os.path.isabs(a.file) else os.path.join(a.root, a.file)
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)[a.index]
    members = saved["features"]
    cands = {c["id"]: c for c in store.candidates()}
    frames, directions = {}, {}
    yrs = ev.years
    t0 = time.time()
    for i, cid in enumerate(members):
        c = cands[cid]
        f, _meta = compute_candidate(c, panel, compiler, ev)
        ric, n = rank_ic(f, ev.label, ev.mask, proto["min_stocks"])
        train = ric[np.isin(yrs, proto["train_years"])].dropna()
        d = int(np.sign(train.mean())) if len(train) else 0
        saved_d = (store.load_result(cid) or {}).get("direction")
        directions[cid] = {"replay": d, "saved": saved_d}
        frames[cid] = pct_ranks(f * d, ev)
        del f
        if (i + 1) % 10 == 0:
            _log(f"recomputed {i + 1}/{len(members)} members ({time.time() - t0:.0f}s)")
    mismatch = [k for k, v in directions.items() if v["replay"] != v["saved"]]
    _log(f"direction mismatches: {len(mismatch)}")
    panel.release(keep=())
    res, pred = rolling_combination(panel, ev, frames, members, model=saved["model"], target=saved["target"], update=saved["update"],
                                    train_years=tuple(proto["train_years"]), target_years=tuple(proto["target_years"]),
                                    day_step=a.day_step or saved.get("day_step", 2), params=saved.get("params") or None, seed=a.seed)
    ar = res["annual_rank_ic"]; sa = saved["annual_rank_ic"]
    rows = []
    for y in proto["target_years"]:
        rows.append({"year": y, "saved": sa[str(y)]["mean"], "replay": ar[str(y)]["mean"], "diff": ar[str(y)]["mean"] - sa[str(y)]["mean"]})
    out = {"file": path, "index": a.index, "model": saved["model"], "target": saved["target"], "update": saved["update"], "n_features": len(members),
           "seed": a.seed, "day_step": a.day_step or saved.get("day_step", 2), "direction_mismatches": mismatch,
           "saved_mean": saved["target_mean_rank_ic"], "replay_mean": res["target_mean_rank_ic"],
           "saved_worst": saved["target_worst_rank_ic"], "replay_worst": res["target_worst_rank_ic"], "annual": rows,
           "replay_icir": res["target_icir"], "elapsed_s": time.time() - t0, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    os.makedirs(os.path.join(a.root, "acceptance"), exist_ok=True)
    name = f"replay_{os.path.basename(path)[:-5]}_{a.index}_seed{a.seed}_ds{out['day_step']}.json"
    with open(os.path.join(a.root, "acceptance", name), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
