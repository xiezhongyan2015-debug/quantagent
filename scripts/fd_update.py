"""A18 step 6: incremental fundamental update for the daily chain (Tushare-compatible endpoint, same client and routing as
the bulk download).  UNTESTED against the endpoint until a token is supplied — run `probe` first.

Token: environment variable FD_TUSHARE_TOKEN, else read from stdin (never written to disk or logs).

python scripts/fd_update.py probe  --date 20260918                 # one small request per api: rows, date range, whether the filter is honoured
python scripts/fd_update.py update --days 5 [--apis daily daily_basic ...]   # fetch the last K trading days per api, merge into raw/<api>.parquet
python scripts/fd_update.py refresh-live                            # rebuild the live-window grid and assemble onto F:/A_Layer_Live/panel

Fetch plan (official parameter semantics; the probe verifies the third-party endpoint honours them):
  daily / daily_basic / adj_factor       trade_date=<day>                       one call per day
  forecast / express / dividend / repurchase / stk_holdertrade / share_float / fina_audit / stk_holdernumber / top10_floatholders
                                          ann_date=<day>                         one call per day per api
  pledge_stat                             end_date=<last Friday>                one call per week
  report_rc                               start_date/end_date = window          one call
  income / balancesheet / cashflow / fina_indicator
                                          ann_date=<day> if honoured, else per-stock refetch for stocks whose disclosure_date.actual_date is in the window
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DL = "F:/TushareData/Ashare_10Y_20260920"
RAW = "F:/A_Layer_Research/fundamental/raw"
LIVE_GRID = "F:/A_Layer_Live/fundamental_grid"
sys.path.insert(0, DL)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

DAILY_APIS = ["daily", "daily_basic", "adj_factor"]
ANN_APIS = ["forecast", "express", "dividend", "repurchase", "stk_holdertrade", "share_float", "fina_audit", "stk_holdernumber", "top10_floatholders"]
STMT_APIS = ["income", "balancesheet", "cashflow", "fina_indicator"]
KEYS = {"daily": ["ts_code", "trade_date"], "daily_basic": ["ts_code", "trade_date"], "adj_factor": ["ts_code", "trade_date"]}


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def fields_for(api: str) -> list[str]:
    plan = json.load(open(os.path.join(DL, "download_plan.json"), encoding="utf-8"))
    for grp in ("main_fundamental_apis", "extended_fundamental_apis"):
        if api in plan[grp]:
            return plan[grp][api]
    cat = json.load(open(os.path.join(DL, "fundamental_catalog.json"), encoding="utf-8"))
    return [f["field"] for f in cat[api]["fields"]]


def get_token() -> str:
    tok = os.environ.get("FD_TUSHARE_TOKEN", "").strip()
    if not tok:
        tok = sys.stdin.readline().strip()
    if len(tok) < 40:
        raise SystemExit("token missing: set FD_TUSHARE_TOKEN or pipe it on stdin")
    return tok


def client():
    from direct_client import DirectClient  # noqa: E402  (bulk-download client, interface-pinned, no proxy fallback)
    cfg = json.load(open(os.path.join(DL, "network_config.json"), encoding="utf-8"))
    return DirectClient(get_token(), cfg, max_rps=5)


def fetch(cl, api: str, params: dict, fields: list[str]) -> pd.DataFrame:
    """All pages of one query; tolerant to the client's return shape (DataFrame or list of rows + fields)."""
    frames = []
    offset = 0
    while True:
        p = dict(params, limit=5000, offset=offset)
        obj, _meta = cl.call(api, p, fields)          # DirectClient.call -> (tushare json, transport meta)
        if obj.get("code") not in (0, None):
            raise RuntimeError(f"{api}: {obj.get('msg')}")
        data = obj.get("data") or {}
        df = pd.DataFrame(data.get("items", []), columns=data.get("fields", fields))
        if df.empty:
            break
        frames.append(df); offset += len(df)
        if len(df) < 5000:
            break
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=fields)


def add_code(df: pd.DataFrame) -> pd.DataFrame:
    if "ts_code" in df.columns and "code" not in df.columns:
        df.insert(df.columns.get_loc("ts_code") + 1, "code", df["ts_code"].str[-2:] + df["ts_code"].str[:6])
    return df


def merge_raw(api: str, new: pd.DataFrame) -> int:
    path = os.path.join(RAW, f"{api}.parquet")
    old = pd.read_parquet(path)
    new = add_code(new.copy())
    for c in old.columns:
        if c not in new.columns:
            new[c] = None
    new = new[old.columns]
    for c in old.columns:   # align dtypes (strings stay strings, numbers to float)
        if old[c].dtype.kind in "f":
            new[c] = pd.to_numeric(new[c], errors="coerce")
        elif old[c].dtype == object:
            new[c] = new[c].astype(object).where(new[c].notna(), None)
    both = pd.concat([old, new], ignore_index=True)
    before = len(both)
    both = both.drop_duplicates()
    added = len(both) - len(old)
    tmp = path + ".tmp"
    pq.write_table(pa.Table.from_pandas(both, preserve_index=False), tmp, compression="zstd")
    os.replace(tmp, path)
    log(f"[{api}] +{added} rows (fetched {len(new)}, dupes dropped {before - len(both)})")
    return added


def trading_days(last_k: int) -> list[str]:
    cal = json.load(open(os.path.join(DL, "benchmarks", "20260920T055927Z", "trading_days.json"), encoding="utf-8"))
    today = time.strftime("%Y%m%d")
    days = [d for d in cal if d <= today]
    # the calendar file ends at the download date; extend with weekdays after it (holidays are filtered by empty responses)
    if days and days[-1] < today:
        extra = pd.bdate_range(pd.Timestamp(days[-1]) + pd.Timedelta(days=1), pd.Timestamp(today)).strftime("%Y%m%d").tolist()
        days += extra
    return days[-last_k:]


def cmd_probe(a):
    cl = client()
    day = a.date
    for api in DAILY_APIS:
        df = fetch(cl, api, {"trade_date": day}, fields_for(api)); log(f"probe {api} trade_date={day}: {len(df)} rows")
    for api in ANN_APIS:
        df = fetch(cl, api, {"ann_date": day}, fields_for(api))
        col = "ann_date" if "ann_date" in df.columns else None
        rng = (df[col].min(), df[col].max()) if col and len(df) else None
        log(f"probe {api} ann_date={day}: {len(df)} rows, ann_date range {rng}  {'OK' if not rng or rng == (day, day) else 'FILTER NOT HONOURED'}")
    for api in STMT_APIS:
        df = fetch(cl, api, {"ann_date": day}, fields_for(api))
        rng = (df["ann_date"].min(), df["ann_date"].max()) if len(df) else None
        log(f"probe {api} ann_date={day}: {len(df)} rows, range {rng}  {'OK' if not rng or rng == (day, day) else 'FILTER NOT HONOURED -> use per-stock refetch'}")
    df = fetch(cl, "report_rc", {"start_date": day, "end_date": day}, fields_for("report_rc")); log(f"probe report_rc {day}: {len(df)} rows")
    df = fetch(cl, "disclosure_date", {"end_date": "20260630"}, fields_for("disclosure_date")); log(f"probe disclosure_date 20260630: {len(df)} rows")


def cmd_update(a):
    cl = client()
    days = trading_days(a.days)
    apis = a.apis or (DAILY_APIS + ANN_APIS + STMT_APIS + ["report_rc", "pledge_stat"])
    for api in apis:
        try:
            if api in DAILY_APIS:
                new = pd.concat([fetch(cl, api, {"trade_date": d}, fields_for(api)) for d in days], ignore_index=True)
            elif api in ANN_APIS:
                new = pd.concat([fetch(cl, api, {"ann_date": d}, fields_for(api)) for d in days], ignore_index=True)
            elif api in STMT_APIS:
                if a.stmt_mode == "ann_date":
                    new = pd.concat([fetch(cl, api, {"ann_date": d}, fields_for(api)) for d in days], ignore_index=True)
                else:   # per-stock refetch for stocks that disclosed inside the window (disclosure_date.actual_date)
                    dd = pd.read_parquet(os.path.join(RAW, "disclosure_date.parquet"), columns=["ts_code", "actual_date"])
                    codes = sorted(dd.loc[dd["actual_date"].isin(days), "ts_code"].dropna().unique())
                    log(f"[{api}] refetching {len(codes)} stocks with disclosures in the window")
                    new = pd.concat([fetch(cl, api, {"ts_code": c, "start_date": "20240101", "end_date": days[-1]}, fields_for(api)) for c in codes], ignore_index=True) if codes else pd.DataFrame(columns=fields_for(api))
            elif api == "report_rc":
                new = fetch(cl, api, {"start_date": days[0], "end_date": days[-1]}, fields_for(api))
            elif api == "pledge_stat":
                last_fri = (pd.Timestamp(days[-1]) - pd.Timedelta(days=(pd.Timestamp(days[-1]).dayofweek - 4) % 7)).strftime("%Y%m%d")
                new = fetch(cl, api, {"end_date": last_fri}, fields_for(api))
            else:
                log(f"[{api}] no fetch rule"); continue
            if len(new):
                merge_raw(api, new)
            else:
                log(f"[{api}] nothing new")
        except Exception as e:
            log(f"[{api}] FAILED: {type(e).__name__}: {str(e)[:200]}")
    json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "days": days, "apis": apis}, open(os.path.join(RAW, "_update_state.json"), "w"), indent=1)


def cmd_refresh_live(a):
    """Rebuild the live-window grid (dates/codes of F:/A_Layer_Live/panel only) and assemble onto the live panel."""
    import subprocess
    py = sys.executable
    parts = ["income", "balancesheet", "cashflow", "fina_indicator", "ratios", "events", "analyst", "daily"]
    for part in parts:
        rc = subprocess.call([py, "scripts/fd_features.py", "build", "--parts", part, "--grid-dir", LIVE_GRID, "--grid-panels", "live"])
        if rc:
            raise SystemExit(f"build {part} failed rc={rc}")
    rc = subprocess.call([py, "scripts/fd_features.py", "assemble", "--panels", "live", "--grid-dir", LIVE_GRID])
    raise SystemExit(rc)


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("probe"); s.add_argument("--date", required=True)
    s = sub.add_parser("update"); s.add_argument("--days", type=int, default=5); s.add_argument("--apis", nargs="*", default=None); s.add_argument("--stmt-mode", choices=["ann_date", "refetch"], default="refetch")
    sub.add_parser("refresh-live")
    a = ap.parse_args()
    {"probe": cmd_probe, "update": cmd_update, "refresh-live": cmd_refresh_live}[a.cmd](a)


if __name__ == "__main__":
    main()
