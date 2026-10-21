"""A18: after the fd_rc_n90 fix, drop the single-factor results of every candidate whose expression uses an analyst field so
that `fd_factors.py evaluate` re-evaluates them (none of them was admitted, so no pool bookkeeping to undo)."""
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.ledger import Store  # noqa: E402

store = Store("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research")
pat = re.compile(r"fd_rc_|fd_est_|fd_tp_ret")
n = 0
for c in store.candidates():
    if not str(c.get("source", "")).startswith("fd_item"):
        continue
    if pat.search(c.get("canonical", "")):
        r = store.load_result(c["id"]) or {}
        if r.get("in_pool"):
            print("admitted, kept:", c["name"]); continue
        p = os.path.join(store.root, "results", f"{c['id']}.json")
        if os.path.exists(p):
            os.remove(p); n += 1; print("reset", c["name"])
print("reset", n, "results")
