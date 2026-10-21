"""Decile diagnostic inside the competition buckets: per year, mean label_5 (5-session open-to-open) of each score
decile within CSI500 members (and within the other-union names) relative to the bucket equal-weight mean; top-55 by
count; cap tilt of the top names; monthly RankIC inside CSI500. Read-only.
python scripts/competition_decile_diag.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/panel --prediction ... --years 2025 --label ...
"""
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel
from quanta_agents.factor_lab_a import competition as cp
from quanta_agents.factor_lab_a.trading import trading_mask
from quanta_agents.factor_lab_a.evaluate import rank_ic


def decile_table(pred, lab, mask, years, q=10, topn=55):
    rows = {}
    m = mask & pred.notna() & lab.notna()
    pct = pred.where(m).rank(axis=1, pct=True)
    dec = np.ceil(pct * q).clip(1, q)
    labm = lab.where(m)
    ew = labm.mean(axis=1)
    cnt = m.sum(axis=1)
    rk = pred.where(m).rank(axis=1, ascending=False)
    yrs = pred.index.year
    for y in years:
        sel = yrs == y
        out = {}
        for k in range(1, q + 1):
            out[f"d{k}"] = float(((labm.where(dec == k).mean(axis=1) - ew)[sel]).mean() * 252 / 5)
        out[f"top{topn}"] = float(((labm.where(rk <= topn).mean(axis=1) - ew)[sel]).mean() * 252 / 5)
        out["names"] = float(cnt[sel].mean())
        rows[str(y)] = out
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--prediction", required=True)
    ap.add_argument("--years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); ap.add_argument("--label", default=""); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    pred = pd.read_parquet(a.prediction); pred.index = pd.DatetimeIndex(pred.index); pred = pred.reindex(index=panel.dates, columns=panel.codes)
    mem, note = cp.membership(panel)
    base = trading_mask(panel) & (panel["eval_ok"] > 0)
    lab5 = panel["label_5"]
    res = {"label": a.label, "membership": note}
    for name, msk in (("csi500", base & mem["csi500"]), ("other_union", base & mem["union"] & ~mem["csi500"]), ("csi300", base & mem["csi300"]), ("csi1000", base & mem["csi1000"])):
        res[name] = decile_table(pred, lab5, msk, a.years, topn=55 if name == "csi500" else 25)
    # cap tilt of the top-55 inside CSI500: mean cap percentile (within CSI500) of the top names, per year
    m5 = base & mem["csi500"]
    cap_pct = panel["log_cap"].where(m5).rank(axis=1, pct=True)
    rk = pred.where(m5).rank(axis=1, ascending=False)
    tilt = cap_pct.where(rk <= 55).mean(axis=1)
    res["csi500_top55_cap_pct_by_year"] = {str(y): float(tilt[tilt.index.year == y].mean()) for y in a.years}
    # monthly RankIC inside CSI500 and union
    for name, msk in (("csi500", m5), ("union", base & mem["union"])):
        r, n = rank_ic(pred, lab5, msk, min_stocks=50)
        res[f"monthly_ic_{name}"] = {str(k): round(float(v), 4) for k, v in r.groupby(r.index.to_period("M")).mean().items() if k.year in a.years}
    print(f"== {a.label}  ({note})")
    for name in ("csi500", "other_union", "csi300", "csi1000"):
        print(f"  {name}: annualised 5-day excess vs bucket EW by decile (d10 = best) and top-N")
        for y, v in res[name].items():
            print("   %s  %s  top %+5.1f%%  n %.0f" % (y, " ".join(f"{v[f'd{k}'] * 100:+5.1f}" for k in range(1, 11)), v[[k for k in v if k.startswith("top")][0]] * 100, v["names"]))
    print("  csi500 top-55 cap percentile by year:", {k: round(v, 2) for k, v in res["csi500_top55_cap_pct_by_year"].items()})
    print("  monthly IC csi500:", res["monthly_ic_csi500"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
