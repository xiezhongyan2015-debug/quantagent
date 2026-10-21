"""A17 item 3a step 1-2: replicate the CSI500 (and CSI300) periodic review and score the prediction accuracy.

python scripts/pv4_rebalance_predict.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --events 2019-06 ... \
       --out-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv4/rebalance [--modes predicted pre actual] [--windows 252]

Writes  <out-dir>/lists/pred_<event>_<asof>_<mode>.csv   (one row per predicted add / drop)
        <out-dir>/lists/actual_<event>.csv
        <out-dir>/accuracy_<tag>.csv                     (precision / recall per event)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pv4_rebalance_lib import (Panel, event_calendar, predict_event, actual_changes, prf, _shift_td)  # noqa: E402

FIELDS = ["total_market_cap", "amount", "is_st", "is_delisting", "listed_days", "close",
          "member_csi300", "member_csi500", "member_csi1000"]


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True)
    ap.add_argument("--events", nargs="*", default=None, help="e.g. 2019-06 2019-12 ... (default: all June/Dec in the panel from --from-year)")
    ap.add_argument("--from-year", type=int, default=2019)
    ap.add_argument("--to-year", type=int, default=2024)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--modes", nargs="*", default=["predicted", "pre", "actual"])
    ap.add_argument("--asofs", nargs="*", default=["cutoff", "pred_ann"])
    ap.add_argument("--windows", nargs="*", type=int, default=[252])
    ap.add_argument("--tag", default="research")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out_dir, "lists"), exist_ok=True)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    for f in FIELDS:
        panel[f]
    dates = panel.dates
    m500 = panel["member_csi500"] > 0
    evs = [(y, m) for y in range(a.from_year, a.to_year + 1) for m in (6, 12)]
    cal = event_calendar(dates, m500, evs)
    if a.events:
        cal = [e for e in cal if e["label"] in set(a.events)]
    cal = [e for e in cal if e["eff_panel"] in dates and _shift_td(dates, e["eff_panel"], -1) is not None
           and e["n_change_panel"] >= 6]
    log(f"events: {[(e['label'], str(e['cutoff'].date()), str(e['ann'].date()), str(e['eff_true'].date()), str(e['eff_panel'].date()), e['n_change_panel']) for e in cal]}")

    rows = []
    for ev in cal:
        act = actual_changes(m500, dates, ev["eff_panel"])
        pd.DataFrame({"code": act["adds"] + act["drops"],
                      "side": ["add"] * len(act["adds"]) + ["drop"] * len(act["drops"])}).to_csv(
            os.path.join(a.out_dir, "lists", f"actual_{ev['label']}.csv"), index=False, encoding="utf-8-sig")
        for win in a.windows:
            for asof_key in a.asofs:
                asof = ev[asof_key]
                for mode in a.modes:
                    r = predict_event(panel, ev, asof, window=win, csi300_mode=mode)
                    r5 = r["csi500"]
                    pa, pdp = prf(r5["adds"], act["adds"]), prf(r5["drops"], act["drops"])
                    fn = os.path.join(a.out_dir, "lists", f"pred_{ev['label']}_{asof_key}_{mode}_w{win}.csv")
                    pd.DataFrame({"code": r5["adds"] + r5["drops"],
                                  "side": ["add"] * len(r5["adds"]) + ["drop"] * len(r5["drops"]),
                                  "cap_rank": [float(r5["rank"].get(c, np.nan)) for c in r5["adds"] + r5["drops"]],
                                  "hit": [c in set(act["adds"]) for c in r5["adds"]] + [c in set(act["drops"]) for c in r5["drops"]],
                                  }).to_csv(fn, index=False, encoding="utf-8-sig")
                    # CSI300 accuracy as a side diagnostic (only meaningful in 'predicted' mode)
                    act3 = actual_changes(panel["member_csi300"] > 0, dates, ev["eff_panel"])
                    p3 = prf(r["csi300"]["adds"], act3["adds"])
                    rows.append({"event": ev["label"], "asof": asof_key, "asof_date": str(asof.date()), "mode": mode, "window": win,
                                 "cutoff": str(ev["cutoff"].date()), "ann": str(ev["ann"].date()),
                                 "eff_true": str(ev["eff_true"].date()), "eff_panel": str(ev["eff_panel"].date()),
                                 "add_pred": pa["n_pred"], "add_actual": pa["n_actual"], "add_hit": pa["hit"],
                                 "add_precision": pa["precision"], "add_recall": pa["recall"],
                                 "drop_pred": pdp["n_pred"], "drop_actual": pdp["n_actual"], "drop_hit": pdp["hit"],
                                 "drop_precision": pdp["precision"], "drop_recall": pdp["recall"],
                                 "csi300_add_precision": p3["precision"], "csi300_add_hit": p3["hit"], "csi300_add_actual": p3["n_actual"],
                                 "ideal_adds": r5["n_ideal_adds"], "forced_drops": r5["n_forced"]})
                    log(f"  {ev['label']} {asof_key} {mode} w{win}: add P {pa['precision']:.2f} R {pa['recall']:.2f} ({pa['hit']}/{pa['n_actual']})"
                        f" | drop P {pdp['precision']:.2f} R {pdp['recall']:.2f} ({pdp['hit']}/{pdp['n_actual']}) | csi300 add P {p3['precision']:.2f}")
    df = pd.DataFrame(rows)
    out = os.path.join(a.out_dir, f"accuracy_{a.tag}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    summ = df.groupby(["asof", "mode", "window"])[["add_precision", "add_recall", "drop_precision", "drop_recall", "csi300_add_precision"]].mean().round(4)
    print(summ.to_string())
    with open(os.path.join(a.out_dir, f"accuracy_{a.tag}.json"), "w", encoding="utf-8") as fh:
        json.dump({"events": [{k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in e.items()} for e in cal],
                   "rows": df.to_dict("records"), "summary": summ.reset_index().to_dict("records")}, fh, ensure_ascii=False, indent=1, default=float)
    print(out)
    panel.release()


if __name__ == "__main__":
    main()
