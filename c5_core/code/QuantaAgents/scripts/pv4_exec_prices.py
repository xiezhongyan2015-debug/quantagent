"""A17 item 4: daily execution-price alternatives from the local 1-minute library (qfq units, consistent within a day).
For every name that was ever in CSI300 U CSI500 U CSI1000 during the window: open (09:31 bar open = auction price),
vwap of the first 5 / 30 minutes, of the morning session, of the whole day, and the close. Output: one date x code
parquet per field under --out-dir. Single process, a few hundred MB of memory.

python scripts/pv4_exec_prices.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv4/exec --start 2019-01-01
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402

MINUTE_ROOT = r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data"
FIELDS = ("x_open", "x_vwap5", "x_vwap30", "x_vwap_am", "x_vwap_day", "x_close")


def one(code: str, start: pd.Timestamp) -> pd.DataFrame | None:
    path = os.path.join(MINUTE_ROOT, code.lower() + ".parquet")
    if not os.path.exists(path):
        return None
    t = ds.dataset(path).to_table(columns=["datetime", "open", "close", "volume", "amount", "qfq_ratio"], filter=ds.field("datetime") >= start.to_datetime64()).to_pandas()
    if t.empty:
        return None
    t["date"] = t["datetime"].dt.normalize(); t["hm"] = t["datetime"].dt.hour * 100 + t["datetime"].dt.minute
    g = t.groupby("date", sort=True)
    out = pd.DataFrame({"x_open": g["open"].first(), "x_close": g["close"].last()})
    ratio = g["qfq_ratio"].first()

    def vwap(mask):
        s = t[mask].groupby("date")[["amount", "volume"]].sum()
        return (s["amount"] / s["volume"].where(s["volume"] > 0)) * ratio.reindex(s.index)

    out["x_vwap5"] = vwap(t["hm"] <= 935); out["x_vwap30"] = vwap(t["hm"] <= 1000)
    out["x_vwap_am"] = vwap(t["hm"] <= 1130); out["x_vwap_day"] = vwap(t["hm"] <= 1500)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--out-dir", required=True); ap.add_argument("--start", default="2019-01-01")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    start = pd.Timestamp(a.start); dates = panel.dates[panel.dates >= start]
    union = ((panel["member_csi300"] > 0) | (panel["member_csi500"] > 0) | (panel["member_csi1000"] > 0)).loc[dates]
    codes = [c for c in panel.codes if union[c].any()]
    print(f"{len(codes)} union names since {a.start}", flush=True)
    data = {f: np.full((len(dates), len(codes)), np.nan, np.float32) for f in FIELDS}
    t0 = time.time(); miss = 0
    for j, c in enumerate(codes):
        try:
            d = one(c, start)
        except Exception as exc:  # noqa: BLE001
            print(f"  {c}: {str(exc)[:80]}", flush=True); d = None
        if d is None:
            miss += 1
        else:
            d = d.reindex(dates)
            for f in FIELDS:
                data[f][:, j] = d[f].to_numpy(np.float32)
        if (j + 1) % 200 == 0:
            print(f"  {j + 1}/{len(codes)} ({time.time() - t0:.0f}s), missing {miss}", flush=True)
    for f in FIELDS:
        pd.DataFrame(data[f], index=dates, columns=codes).to_parquet(os.path.join(a.out_dir, f"{f}.parquet"))
    print(f"EXEC_PRICES_DONE names {len(codes)} missing {miss} ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
