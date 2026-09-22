"""A18: a second, pre-registered member set for the fundamental families — relaxed all-A admission
(|train t| >= 2, target mean RankIC >= 0.008, stock coverage >= 0.8, not flagged redundant), because fundamental signals carry
less short-horizon IC per factor than price-volume ones and the tree model is meant to combine them.
Writes fd_ids_relaxed.json and m396_plus_fd_relaxed.json next to the strict versions."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.ledger import Store  # noqa: E402

R = "F:/A_Layer_Research"; M = os.path.join(R, "competition", "fd", "members")
store = Store(R)
base = json.load(open(os.path.join(R, "batches", "combinations_v11_396_blend51020.json"), encoding="utf-8"))[0]["features"]
rows = []
for c in store.candidates():
    if not str(c.get("source", "")).startswith("fd_item"):
        continue
    r = store.load_result(c["id"]) or {}
    if not r.get("direction"):
        continue
    ok = abs(r.get("train_t") or 0) >= 2.0 and (r.get("target_mean_rank_ic") or 0) >= 0.008 and (r.get("stock_coverage") or 0) >= 0.8 and r.get("failure_class") != "redundant"
    rows.append((c["name"], c["id"], ok, r.get("target_mean_rank_ic"), r.get("in_pool")))
sel = [i for n, i, ok, ic, p in rows if ok]
for n, i, ok, ic, p in sorted(rows, key=lambda x: -(x[3] or 0)):
    print(f"{'*' if ok else ' '} {n:22s} ic={ic:+.4f} pool={p}")
json.dump({"members": sel}, open(os.path.join(M, "fd_ids_relaxed.json"), "w"), indent=1)
json.dump({"tag": "m396_plus_fd_relaxed", "base": "combinations_v11_396_blend51020", "new_members": sel, "n": len(base) + len(sel), "members": list(base) + sel},
          open(os.path.join(M, "m396_plus_fd_relaxed.json"), "w"), indent=1)
print("relaxed set:", len(sel), "of", len(rows))
