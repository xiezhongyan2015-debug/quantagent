"""A17 item 3a step 3: event study around the CSI500 periodic review.

Open-to-open cumulative abnormal return of actual / predicted adds and drops over three windows
  W1 cut-off  -> announcement   (open of the day after the cut-off      -> open of the day after the announcement)
  W2 announcement -> effective  (open of the day after the announcement -> open of the effective date)
  W3 effective -> +20 sessions  (open of the effective date             -> open 20 sessions later)
Abnormal = stock window return - equal-weight window return of the pre-review CSI500 members (and of CSI1000).

python scripts/pv4_rebalance_event.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out-dir .../pv4/rebalance --tag research
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pv4_rebalance_lib import (Panel, event_calendar, predict_event, actual_changes, _shift_td, _next_td)  # noqa: E402

FIELDS = ["total_market_cap", "amount", "is_st", "is_delisting", "listed_days", "close", "open",
          "member_csi300", "member_csi500", "member_csi1000"]


def win_ret(opn: pd.DataFrame, d0: pd.Timestamp, d1: pd.Timestamp) -> pd.Series:
    """open[d1]/open[d0]-1 per code; NaN when either open is missing (suspended)."""
    if d0 is None or d1 is None:
        return pd.Series(np.nan, index=opn.columns)
    return opn.loc[d1] / opn.loc[d0] - 1.0


def tstat(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan")
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(len(x))) if sd > 0 else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--from-year", type=int, default=2019)
    ap.add_argument("--to-year", type=int, default=2024)
    ap.add_argument("--tag", default="research")
    ap.add_argument("--post", type=int, default=20)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    for f in FIELDS:
        panel[f]
    dates = panel.dates
    opn = panel["open"]
    m500 = panel["member_csi500"] > 0; m300 = panel["member_csi300"] > 0; m1000 = panel["member_csi1000"] > 0
    cal = event_calendar(dates, m500, [(y, m) for y in range(a.from_year, a.to_year + 1) for m in (6, 12)])
    cal = [e for e in cal if e["n_change_panel"] >= 6]

    rows = []
    for ev in cal:
        act = actual_changes(m500, dates, ev["eff_panel"])
        pr = predict_event(panel, ev, ev["cutoff"])["csi500"]
        pr2 = predict_event(panel, ev, ev["pred_ann"])["csi500"]
        c1 = _next_td(dates, ev["cutoff"]); a1 = _next_td(dates, ev["ann"])
        e0 = ev["eff_true"]; e20 = _shift_td(dates, e0, a.post)
        wins = {"W1_cut_to_ann": (c1, a1), "W2_ann_to_eff": (a1, e0), "W3_eff_to_p20": (e0, e20)}
        bench500 = m500.loc[ev["cutoff"]]; bench1000 = m1000.loc[ev["cutoff"]]
        groups = {"actual_add": act["adds"], "actual_drop": act["drops"],
                  "pred_add": pr["adds"], "pred_drop": pr["drops"],
                  "pred_add_ann": pr2["adds"], "pred_drop_ann": pr2["drops"],
                  "pred_add_hit": [c for c in pr["adds"] if c in set(act["adds"])],
                  "pred_drop_hit": [c for c in pr["drops"] if c in set(act["drops"])]}
        for wname, (d0, d1) in wins.items():
            if d0 is None or d1 is None:      # window runs past the end of the panel
                continue
            r = win_ret(opn, d0, d1)
            b500 = float(r[bench500[bench500].index].mean())
            b1000 = float(r[bench1000[bench1000].index].mean())
            for gname, codes in groups.items():
                v = r[[c for c in codes if c in r.index]].astype(float)
                rows.append({"event": ev["label"], "window": wname, "group": gname, "n": int(v.notna().sum()),
                             "d0": str(d0.date()), "d1": str(d1.date()),
                             "raw": float(v.mean()), "bench500": b500, "bench1000": b1000,
                             "ar500_mean": float(v.mean()) - b500, "ar1000_mean": float(v.mean()) - b1000,
                             "ar500_t_names": tstat((v - b500).to_numpy()),
                             "in_union_share": float(np.mean([bool(m300.loc[ev["cutoff"], c] or m1000.loc[ev["cutoff"], c] or m500.loc[ev["cutoff"], c]) for c in codes])) if codes else np.nan,
                             "_vals": (v - b500).dropna().tolist(), "_vals1000": (v - b1000).dropna().tolist()})
    df = pd.DataFrame(rows)
    per_event = df.drop(columns=["_vals", "_vals1000"])
    per_event.to_csv(os.path.join(a.out_dir, f"event_study_per_event_{a.tag}.csv"), index=False, encoding="utf-8-sig")

    summ = []
    for (w, g), sub in df.groupby(["window", "group"]):
        pooled = np.concatenate([np.array(x) for x in sub["_vals"]]) if len(sub) else np.array([])
        pooled1000 = np.concatenate([np.array(x) for x in sub["_vals1000"]]) if len(sub) else np.array([])
        ev_means = sub["ar500_mean"].to_numpy(float)
        ev_means1000 = sub["ar1000_mean"].to_numpy(float)
        summ.append({"window": w, "group": g, "events": len(sub), "names": int(len(pooled)),
                     "ar500_mean": float(np.mean(pooled)) if len(pooled) else np.nan, "ar500_t_names": tstat(pooled),
                     "ar500_t_events": tstat(ev_means), "ar500_pos_events": int((ev_means > 0).sum()),
                     "ar1000_mean": float(np.mean(pooled1000)) if len(pooled1000) else np.nan,
                     "ar1000_t_names": tstat(pooled1000), "ar1000_t_events": tstat(ev_means1000),
                     "in_union_share": float(sub["in_union_share"].mean())})
    sdf = pd.DataFrame(summ).sort_values(["window", "group"])
    sdf.to_csv(os.path.join(a.out_dir, f"event_study_summary_{a.tag}.csv"), index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 220)
    print(sdf.round(4).to_string(index=False))
    with open(os.path.join(a.out_dir, f"event_study_{a.tag}.json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": sdf.to_dict("records"), "per_event": per_event.to_dict("records")}, fh, ensure_ascii=False, indent=1, default=float)
    panel.release()


if __name__ == "__main__":
    main()
