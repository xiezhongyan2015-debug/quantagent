"""A17 item 3b: limit-up / limit-down / resumption events inside CSI500 - is there return left after the model score?
For each event type: count, mean in-bucket score percentile, mean forward excess (vs CSI500 members equal weight) over
1 / 5 / 10 days (open-to-open labels), and the same excess measured against the score-decile peers of the same day
(residual = what the model does not already know). Research years only by default.

python scripts/pv4_limit_events.py --panel-dir P --pred pred.parquet [--years 2019 2024] --out out.json
"""
import argparse
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--panel-dir", required=True); ap.add_argument("--pred", required=True); ap.add_argument("--years", nargs=2, type=int, default=[2019, 2024]); ap.add_argument("--out", required=True)
a = ap.parse_args()
panel = Panel(a.panel_dir); panel._dtype = np.float32
mem, _ = cp.membership(panel); m5 = mem["csi500"]
dates = panel.dates; codes = pd.Index(panel.codes)
close, high, low, prev, vol = panel["close"], panel["high"], panel["low"], panel["prev_close"], panel["volume"]
ret = close / prev - 1
# limit width by board: 20% for STAR (688) always, ChiNext (300/301) from 2020-08-24, else 10% (ST is not in CSI500)
wide = pd.Series([c[-6:].startswith("688") for c in codes], index=codes)
chinext = pd.Series([c[-6:].startswith(("300", "301")) for c in codes], index=codes)
lim = pd.DataFrame(0.10, index=dates, columns=codes)
lim.loc[:, wide[wide].index] = 0.20
lim.loc[dates >= pd.Timestamp("2020-08-24"), chinext[chinext].index] = 0.20
up_close = (ret >= lim - 0.003) & (close >= high - 1e-6)
dn_close = (ret <= -lim + 0.003) & (close <= low + 1e-6)
one_word_up = up_close & (high <= low + 1e-6)
touched_up = (high / prev - 1 >= lim - 0.003) & ~up_close                     # hit the limit intraday, closed below it
touched_dn = (low / prev - 1 <= -lim + 0.003) & ~dn_close
traded = (vol > 0)
# resumption: first traded day after >= 5 consecutive non-traded days
nt = (~traded).to_numpy().astype(np.int32); run = np.zeros_like(nt)
for i in range(1, len(dates)):
    run[i] = np.where(nt[i] > 0, run[i - 1] + 1, 0)
run = pd.DataFrame(run, index=dates, columns=codes)
resume = traded & (run.shift(1) >= 5)
events = {"limit_up_close": up_close & ~one_word_up, "one_word_limit_up": one_word_up, "limit_up_opened": touched_up, "limit_down_close": dn_close, "limit_down_opened": touched_dn,
          "second_limit_up": up_close & up_close.shift(1).fillna(False), "resumption": resume}
pred = pd.read_parquet(a.pred); pred.index = pd.DatetimeIndex(pred.index); pred = pred.reindex(index=dates, columns=codes)
pct = pred.where(m5).rank(axis=1, pct=True)
dec = np.ceil(pct * 10).clip(1, 10)
ysel = (dates.year >= a.years[0]) & (dates.year <= a.years[1])
rep = {}
for h in (1, 5, 10):
    lab = panel[f"label_{h}"].where(m5)
    ex = lab.sub(lab.mean(axis=1), axis=0)                                    # vs CSI500 members equal weight
    # peers: same day, same score decile
    peer = pd.DataFrame(np.nan, index=dates, columns=codes)
    for d in range(1, 11):
        md = dec == d
        peer = peer.where(~md, lab.where(md).mean(axis=1).to_numpy()[:, None] * np.ones((1, len(codes))))
    resid = lab - peer
    for name, ev in events.items():
        e = (ev & m5 & pct.notna()).to_numpy() & ysel[:, None]
        x = ex.to_numpy()[e]; r = resid.to_numpy()[e]; x = x[np.isfinite(x)]; r = r[np.isfinite(r)]
        # event-day clustered t: mean of daily means
        rr = np.where(e, resid.to_numpy(), np.nan); has = np.isfinite(rr).any(axis=1)
        dm = pd.Series(np.nanmean(rr[has], axis=1)) if has.any() else pd.Series(dtype=float)
        rep.setdefault(name, {"n": int(e.sum()), "mean_score_pct": float(np.nanmean(pct.to_numpy()[e])) if e.any() else float("nan")})
        rep[name][f"h{h}"] = {"excess": float(x.mean()) if len(x) else float("nan"), "resid_vs_score_peers": float(r.mean()) if len(r) else float("nan"),
                              "t_resid_daycluster": float(dm.mean() / dm.std(ddof=1) * np.sqrt(len(dm))) if len(dm) > 10 and dm.std() > 0 else float("nan"), "event_days": int(len(dm))}
for name, v in rep.items():
    print(f"{name:20s} n {v['n']:6d} score pct {v['mean_score_pct']:.3f} | " + " | ".join(f"h{h}: ex {v[f'h{h}']['excess'] * 100:+.2f}% resid {v[f'h{h}']['resid_vs_score_peers'] * 100:+.2f}% (t {v[f'h{h}']['t_resid_daycluster']:+.1f})" for h in (1, 5, 10)))
json.dump(rep, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
