"""A15 item 10: add a `raw_open` field (unadjusted open price, the execution price for lot sizing) to a research-format
panel directory, taken from the live daily source (vendor + public-API daily bars with raw_open, 2015-2026).

python scripts/pv2_raw_open.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel [--source /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live/daily_source]
Writes <panel-dir>/raw_open.parquet (date x code, float32, NaN where the source has no bar) and records the field in meta.json.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--panel-dir", required=True); ap.add_argument("--source", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live/daily_source"); ap.add_argument("--field", default="raw_open")
    a = ap.parse_args(argv)
    panel = Panel(a.panel_dir)
    dates = panel.dates; codes = panel.codes; cidx = {c: i for i, c in enumerate(codes)}
    out = np.full((len(dates), len(codes)), np.nan, np.float32)
    t0 = time.time(); n = 0; missing = 0
    for f in sorted(glob.glob(os.path.join(a.source, "*.parquet"))):
        code = os.path.splitext(os.path.basename(f))[0].upper()
        j = cidx.get(code)
        if j is None:
            missing += 1; continue
        t = pq.read_table(f, columns=["date", a.field]).to_pandas()
        s = pd.Series(t[a.field].to_numpy(np.float64), index=pd.DatetimeIndex(pd.to_datetime(t["date"]))).reindex(dates)
        out[:, j] = s.to_numpy(np.float32); n += 1
        if n % 1000 == 0:
            print(f"{n} names ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(out, index=dates, columns=codes)
    df.to_parquet(os.path.join(a.panel_dir, f"{a.field}.parquet"))
    meta_p = os.path.join(a.panel_dir, "meta.json")
    with open(meta_p, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["fields"] = sorted(set(meta.get("fields", [])) | {a.field}); meta[f"{a.field}_source"] = {"source": a.source, "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "names": n}
    with open(meta_p, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    cov = df.notna().sum(axis=1)
    print(json.dumps({"names_written": n, "source_names_not_in_panel": missing, "coverage_mean": float(cov.mean()), "coverage_last": int(cov.iloc[-1]), "panel_codes": len(codes)}))


if __name__ == "__main__":
    main()
