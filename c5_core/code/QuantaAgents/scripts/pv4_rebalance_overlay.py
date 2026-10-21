"""A17 item 3a step 4: build the date x code overlay score files for the CSI500 review prediction.

Two files are produced from a baseline score (values are kept untouched except in the overlay cells, so the
book behaves exactly like the baseline outside the review windows):

  veto  <- exclusion score. Predicted DROPS are pushed below that date's union minimum from the prediction date
           up to (but not including) the date the panel's member_csi500 field flips. Passed through the rule JSON
           as "exclusion_pred": the enhanced book then puts them in the exclusion state (weight 0).
  boost <- main score.      Predicted ADDS are pushed above that date's union maximum over the same window, so
           that the ones already inside the other-union bucket (CSI300 / CSI1000 names) enter its top-N.

Information set: the list for a review is written only on dates >= its prediction date (the cut-off date, or the
session before the announcement), so a score on day t only uses lists predicted on or before t.

python scripts/pv4_rebalance_overlay.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel \
    --base F:/.../scores_research.parquet --out-dir F:/.../pv4/rebalance --tag research --from-year 2019 --to-year 2024
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pv4_rebalance_lib import (Panel, event_calendar, predict_event, actual_changes, _shift_td)  # noqa: E402

FIELDS = ["total_market_cap", "amount", "is_st", "is_delisting", "listed_days", "close",
          "member_csi300", "member_csi500", "member_csi1000"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="research")
    ap.add_argument("--from-year", type=int, default=2019)
    ap.add_argument("--to-year", type=int, default=2024)
    ap.add_argument("--asof", default="cutoff", choices=["cutoff", "pred_ann"])
    ap.add_argument("--csi300-mode", default="predicted")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    for f in FIELDS:
        panel[f]
    dates = panel.dates
    m500 = panel["member_csi500"] > 0
    mem_union = (panel["member_csi300"] > 0) | m500 | (panel["member_csi1000"] > 0)
    cal = [e for e in event_calendar(dates, m500, [(y, m) for y in range(a.from_year, a.to_year + 1) for m in (6, 12)])
           if e["n_change_panel"] >= 6]

    base = pd.read_parquet(a.base); base.index = pd.DatetimeIndex(base.index)
    base = base.reindex(index=dates, columns=panel.codes)
    lo = base.where(mem_union).min(axis=1)
    hi = base.where(mem_union).max(axis=1)
    veto = base.copy(); boost = base.copy()
    cidx = {c: i for i, c in enumerate(panel.codes)}
    vnp = veto.to_numpy(); bnp = boost.to_numpy()
    lonp = (lo - 1.0).to_numpy(); hinp = (hi + 1.0).to_numpy()
    meta = []
    for ev in cal:
        asof = ev[a.asof]
        r = predict_event(panel, ev, asof, csi300_mode=a.csi300_mode)["csi500"]
        i0 = int(dates.get_indexer([asof])[0])
        i1 = int(dates.get_indexer([ev["eff_panel"]])[0])       # exclusive: the membership flips here
        if i0 < 0 or i1 <= i0:
            continue
        rows = np.arange(i0, i1)
        jd = [cidx[c] for c in r["drops"] if c in cidx]
        ja = [cidx[c] for c in r["adds"] if c in cidx]
        vnp[np.ix_(rows, jd)] = lonp[rows][:, None]
        bnp[np.ix_(rows, ja)] = hinp[rows][:, None]
        act = actual_changes(m500, dates, ev["eff_panel"])
        in_oth = float(np.mean([bool(mem_union.iloc[i0, cidx[c]] and not m500.iloc[i0, cidx[c]]) for c in r["adds"]]))
        meta.append({"event": ev["label"], "asof": str(asof.date()), "eff_panel": str(ev["eff_panel"].date()),
                     "sessions": int(len(rows)), "n_drops": len(jd), "n_adds": len(ja),
                     "adds_in_other_bucket": in_oth,
                     "drop_hit": len(set(r["drops"]) & set(act["drops"])), "add_hit": len(set(r["adds"]) & set(act["adds"]))})
    veto = pd.DataFrame(vnp, index=dates, columns=panel.codes).astype(np.float32)
    boost = pd.DataFrame(bnp, index=dates, columns=panel.codes).astype(np.float32)
    pv = os.path.join(a.out_dir, f"score_veto_{a.tag}_{a.asof}.parquet")
    pb = os.path.join(a.out_dir, f"score_boost_{a.tag}_{a.asof}.parquet")
    veto.to_parquet(pv); boost.to_parquet(pb)
    with open(os.path.join(a.out_dir, f"overlay_meta_{a.tag}_{a.asof}.json"), "w", encoding="utf-8") as fh:
        json.dump({"base": a.base, "panel_dir": a.panel_dir, "asof": a.asof, "csi300_mode": a.csi300_mode,
                   "veto": pv, "boost": pb, "events": meta}, fh, ensure_ascii=False, indent=1, default=float)
    print(pd.DataFrame(meta).to_string(index=False))
    print(pv); print(pb)
    panel.release()


if __name__ == "__main__":
    main()
