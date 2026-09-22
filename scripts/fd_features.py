"""A18 step 1: build point-in-time fundamental fields on the master grid and assemble them onto the panels.

python scripts/fd_features.py build [--parts income balancesheet cashflow fina_indicator ratios events analyst daily]   # -> F:/A_Layer_Research/fundamental/grid/fd_*.parquet
python scripts/fd_features.py assemble [--panels research oos p2026 live]        # -> <panel>/fd_*.parquet + meta.fields
python scripts/fd_features.py validate --panel-dir F:/A_Layer_OOS/p2026/panel    # self-built vs vendor vs Choice cross-checks
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
from quanta_agents.factor_lab_a import fundamental as fd  # noqa: E402

CHOICE = "F:/ChoiceData/CSI500_41Factors_20260819_20260918/CSI500_41Factors_daily_20260819_20260918.parquet"


def _apply_grid(a):
    if getattr(a, "grid_dir", None):
        fd.GRID = a.grid_dir
    panels = {k: fd.PANELS[k] for k in (getattr(a, "grid_panels", None) or list(fd.PANELS))}
    return panels


def cmd_build(a):
    t0 = time.time()
    panels = _apply_grid(a)
    dates, codes = fd.master_grid(panels)
    fd.log(f"master grid: {len(dates)} dates {dates[0].date()}..{dates[-1].date()}, {len(codes)} codes")
    os.makedirs(fd.GRID, exist_ok=True)
    summary_path = os.path.join(fd.GRID, "_build_summary.json")
    summary = json.load(open(summary_path, encoding="utf-8")) if os.path.exists(summary_path) else {}
    builders = {"statements": fd.build_statements, "income": fd.build_income, "balancesheet": fd.build_balancesheet, "cashflow": fd.build_cashflow,
                "fina_indicator": fd.build_fina_indicator, "ratios": fd.build_ratios, "events": fd.build_events, "analyst": fd.build_analyst, "daily": fd.build_daily_basic}
    for part in a.parts:
        t1 = time.time()
        fields = builders[part](dates, codes)
        cov = {k: fd.save_grid_field(k, v, dates, codes) for k, v in fields.items()}
        summary = json.load(open(summary_path, encoding="utf-8")) if os.path.exists(summary_path) else {}   # parallel builders: merge on write
        summary[part] = {"fields": cov, "seconds": round(time.time() - t1), "built": time.strftime("%Y-%m-%dT%H:%M:%S")}
        tmp = summary_path + f".{os.getpid()}"; json.dump(summary, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1); os.replace(tmp, summary_path)
        fd.log(f"{part}: {len(cov)} fields in {time.time() - t1:.0f}s; coverage " + ", ".join(f"{k[3:]}={v:.2f}" for k, v in cov.items()))
        del fields
    json.dump({"dates": [d.strftime("%Y-%m-%d") for d in dates], "codes": codes}, open(os.path.join(fd.GRID, "grid.json"), "w"))
    fd.log(f"build done in {time.time() - t0:.0f}s")


def cmd_assemble(a):
    t1 = time.time()
    _apply_grid(a)
    cov = fd.assemble_all([fd.PANELS[n] for n in a.panels], ffill_limit=a.ffill_limit)
    for p, c in cov.items():
        fd.log(f"{p}: {len(c)} fields; " + ", ".join(f"{k[3:]}={v:.2f}" for k, v in list(c.items())[:8]) + " ...")
    fd.log(f"assemble done in {time.time() - t1:.0f}s")


def _rank_corr_by_date(x: pd.DataFrame, y: pd.DataFrame, min_n=100):
    r = []
    for d in x.index:
        a, b = x.loc[d], y.loc[d]
        m = a.notna() & b.notna()
        if m.sum() >= min_n:
            r.append(a[m].rank().corr(b[m].rank()))
    return float(np.nanmean(r)) if r else np.nan, len(r)


def cmd_validate(a):
    pdir = a.panel_dir
    rd = lambda f: pd.read_parquet(os.path.join(pdir, f + ".parquet"))
    out = {}
    # 1) self-built valuation vs vendor daily_basic (same dates, all codes)
    pairs = [("fd_ep_ttm", "fd_v_pe_ttm", True), ("fd_bp", "fd_v_pb", True), ("fd_dp_ttm", "fd_v_dv_ttm", False)]
    for mine, vendor, inverse in pairs:
        x, y = rd(mine), rd(vendor)
        x.index = pd.DatetimeIndex(x.index); y.index = pd.DatetimeIndex(y.index)
        idx = x.index[x.index >= "2017-01-01"][::20]
        yy = 1.0 / y.loc[idx] if inverse else y.loc[idx] / 100.0
        rc, n = _rank_corr_by_date(x.loc[idx], yy)
        both = x.loc[idx].notna() & yy.notna()
        rel = ((x.loc[idx] - yy).abs() / yy.abs()).where(both)
        out[f"{mine}_vs_vendor"] = {"rank_corr_mean": rc, "dates": n, "median_rel_diff": float(np.nanmedian(rel.to_numpy())),
                                    "share_within_5pct": float((rel < 0.05).sum().sum() / both.sum().sum())}
    # 2) vs Choice 41 factors on the 23-day CSI500 sample
    if os.path.exists(CHOICE):
        ch = pd.read_parquet(CHOICE)
        dcol = [c for c in ch.columns if "date" in c.lower() or "日期" in c][0]
        ccol = [c for c in ch.columns if "code" in c.lower() or "代码" in c][0]
        ch["date"] = pd.to_datetime(ch[dcol]); ch["code"] = ch[ccol].astype(str).str.replace(r"^(\d{6})\.(SH|SZ)$", r"\2\1", regex=True)
        cmap = {"fd_ep_ttm": "EP比_PIT", "fd_bp": "账面市值比_PIT", "fd_dp_ttm": "股息率(DTOP)_PIT", "fd_est_ep_fy1": "分析师预测EP比_PIT",
                "fd_rc_disp": "分析师预测EP比标准差_PIT", "fd_rc_up90": "评级调整比率_PIT", "fd_sp_ttm": "销售市值比_PIT"}
        cols = {c: c for c in ch.columns}
        for mine, chname in cmap.items():
            target = [c for c in ch.columns if chname.split("_")[0] in c]
            if not target or not os.path.exists(os.path.join(pdir, mine + ".parquet")):
                continue
            x = rd(mine); x.index = pd.DatetimeIndex(x.index)
            piv = ch.pivot_table(index="date", columns="code", values=target[0])
            common_d = piv.index.intersection(x.index); common_c = piv.columns.intersection(x.columns)
            if len(common_d) == 0:
                out[f"{mine}_vs_choice"] = "no overlapping dates in this panel"; continue
            rc, n = _rank_corr_by_date(x.loc[common_d, common_c], piv.loc[common_d, common_c], min_n=200)
            out[f"{mine}_vs_choice[{target[0]}]"] = {"rank_corr_mean": rc, "dates": n, "codes": len(common_c)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    with open(os.path.join(fd.GRID, "_validate_" + os.path.basename(os.path.dirname(pdir)) + ".json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build"); s.add_argument("--parts", nargs="*", default=["statements", "events", "analyst", "daily"])
    s.add_argument("--grid-dir", default=None, help="alternative grid directory (e.g. F:/A_Layer_Live/fundamental_grid for the daily chain)")
    s.add_argument("--grid-panels", nargs="*", default=None, help="panels whose dates/codes define the grid (default: all four)")
    s = sub.add_parser("assemble"); s.add_argument("--panels", nargs="*", default=list(fd.PANELS)); s.add_argument("--grid-dir", default=None)
    s.add_argument("--ffill-limit", type=int, default=None, help="live chain: forward-fill up to N trading days past the grid's last date")
    s = sub.add_parser("validate"); s.add_argument("--panel-dir", default=fd.PANELS["p2026"])
    a = ap.parse_args()
    {"build": cmd_build, "assemble": cmd_assemble, "validate": cmd_validate}[a.cmd](a)


if __name__ == "__main__":
    main()
