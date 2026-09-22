"""A18 step 0 audit: coverage of the consolidated fundamental tables for the competition universe (CSI300 ∪ 500 ∪ 1000
members at any time in 2015-2026) and the consistency of vendor daily_basic with the research panel.

python scripts/fd_audit.py --out F:/A_Layer_Research/fundamental/audit
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a import fundamental as fd  # noqa: E402

INSTR = "F:/A_Layer_Live/instruments"


def union_codes(start="2015-01-01") -> set[str]:
    codes = set()
    for idx in ("csi300", "csi500", "csi1000"):
        t = pd.read_csv(os.path.join(INSTR, f"{idx}.txt"), sep="\t", header=None, names=["code", "start", "end"])
        t = t[t["end"] >= start]
        codes |= set(t["code"])
    return codes


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="F:/A_Layer_Research/fundamental/audit")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    uni = union_codes(); rep = {"union_codes": len(uni)}
    dates, codes = fd.master_grid(); rep["grid"] = {"dates": len(dates), "codes": len(codes), "union_in_grid": len(uni & set(codes))}
    # statement coverage per union stock: number of distinct report periods 2015Q1.. and last end_date
    per_code = pd.DataFrame(index=sorted(uni))
    for api, avail_col in [("income", "f_ann_date"), ("balancesheet", "f_ann_date"), ("cashflow", "f_ann_date"), ("fina_indicator", "ann_date")]:
        t = fd.load_raw(api, ["code", "ann_date", "end_date"] + ([avail_col] if avail_col != "ann_date" else []))
        t = t[t["code"].isin(uni)]
        t["end"] = fd._dt(t["end_date"]); t = t[t["end"] >= "2015-03-31"]
        g = t.groupby("code")
        per_code[f"{api}_periods"] = g["end"].nunique()
        per_code[f"{api}_last"] = g["end"].max().dt.strftime("%Y-%m-%d")
        per_code[f"{api}_null_ann"] = g["ann_date"].apply(lambda s: float(s.isna().mean()))
        expected = (t["end"].max().year - 2015) * 4 + t["end"].max().month // 3
        rep[api] = {"union_codes_present": int(t["code"].nunique()), "expected_periods_full": int(expected),
                    "codes_with_lt_half_expected": int((per_code[f"{api}_periods"].fillna(0) < expected / 2).sum()),
                    "null_ann_date_share": float(t["ann_date"].isna().mean())}
    for api in ["forecast", "express", "report_rc", "dividend", "share_float", "stk_holdertrade", "pledge_stat", "top10_floatholders", "stk_holdernumber", "fina_audit", "repurchase"]:
        t = fd.load_raw(api, ["code"]); n = t[t["code"].isin(uni)]["code"].nunique()
        rep[api] = {"union_codes_with_records": int(n), "share": round(n / len(uni), 3)}
    # report_rc rows per year (coverage of analyst history)
    rc = fd.load_raw("report_rc", ["code", "report_date", "quarter"]); rc = rc[rc["code"].isin(uni)]
    rep["report_rc_rows_by_year"] = rc["report_date"].str[:4].value_counts().sort_index().to_dict()
    rep["report_rc_q4_share"] = float(rc["quarter"].astype(str).str.endswith("Q4").mean())
    # daily_basic vs panel caps on 12 sampled dates
    db = fd.load_raw("daily_basic", ["code", "trade_date", "total_mv", "circ_mv", "total_share", "float_share", "free_share"])
    db["date"] = fd._dt(db["trade_date"])
    pdir = fd.PANELS["p2026"]
    cap = pd.read_parquet(os.path.join(pdir, "total_market_cap.parquet")); cap.index = pd.DatetimeIndex(cap.index)
    fcap = pd.read_parquet(os.path.join(pdir, "float_market_cap.parquet")); fcap.index = pd.DatetimeIndex(fcap.index)
    sample_dates = [d for d in cap.index[cap.index >= "2016-10-01"][::240]]
    rows = []
    for d in sample_dates:
        s = db[db["date"] == d].set_index("code")
        c = cap.loc[d].dropna(); common = c.index.intersection(s.index)
        r_total = (c[common] / (s.loc[common, "total_mv"] * 1e4)); r_float = fcap.loc[d, common] / (s.loc[common, "circ_mv"] * 1e4)
        rows.append({"date": d.strftime("%Y-%m-%d"), "panel_codes": int(c.notna().sum()), "vendor_codes": int(len(s)), "common": int(len(common)),
                     "total_cap_within_1pct": float(((r_total - 1).abs() < 0.01).mean()), "float_cap_within_1pct": float(((r_float - 1).abs() < 0.01).mean()),
                     "free_over_float_median": float((s.loc[common, "free_share"] / s.loc[common, "float_share"]).median())})
    rep["daily_basic_vs_panel"] = rows
    per_code.to_csv(os.path.join(a.out, "union_statement_coverage.csv"), encoding="utf-8-sig")
    with open(os.path.join(a.out, "audit_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
