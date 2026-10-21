"""Aggregate the qfq minute parquet library into per-stock daily bars (2015-01-01 .. latest).

Source: /Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data/<code>.parquet
  columns: datetime, open, high, low, close, volume, amount, float_shares, total_shares, qfq_ratio
  price basis: qfq as of 2026-05-29 (single consistent basis across the whole history)
Output: <out_dir>/<CODE>.parquet with columns
  date, code, open, high, low, close, volume, amount, float_shares, total_shares, qfq_ratio,
  raw_close (= close / qfq_ratio), float_market_cap, total_market_cap
Only SH/SZ main-board, ChiNext and STAR codes are kept (same filter as the frozen daily source).
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PATTERN = re.compile(r"^(?:sh(?:600|601|603|605|688|689)|sz(?:000|001|002|003|300|301|302))\d{3}$")


def aggregate(path: str, out_dir: str, start: str) -> tuple[str, int, str]:
    code = os.path.basename(path)[:-8]
    if not PATTERN.match(code):
        return code, 0, "skipped"
    out = os.path.join(out_dir, f"{code.upper()}.parquet")
    if os.path.exists(out):
        return code, -1, "exists"
    try:
        t = pq.read_table(path, columns=["datetime", "open", "high", "low", "close", "volume", "amount", "float_shares", "total_shares", "qfq_ratio"]).to_pandas()
        t = t[t["datetime"] >= pd.Timestamp(start)]
        if t.empty:
            return code, 0, "empty"
        t["date"] = t["datetime"].dt.normalize()
        g = t.groupby("date", sort=True)
        d = pd.DataFrame({
            "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
            "volume": g["volume"].sum().astype(float), "amount": g["amount"].sum(),
            "float_shares": g["float_shares"].last().astype(float), "total_shares": g["total_shares"].last().astype(float),
            "qfq_ratio": g["qfq_ratio"].last(),
        })
        # bars with zero volume all day are suspensions: keep the row (calendar alignment) but flag via amount==0
        d["raw_close"] = d["close"] / d["qfq_ratio"].replace(0, np.nan)
        d["float_market_cap"] = d["raw_close"] * d["float_shares"]
        d["total_market_cap"] = d["raw_close"] * d["total_shares"]
        d = d.reset_index()
        d["code"] = code.upper()
        d.to_parquet(out, index=False)
        return code, len(d), "ok"
    except Exception as exc:  # noqa: BLE001
        return code, 0, f"error: {exc}"[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2014-06-01")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    files = sorted(os.path.join(a.source, f) for f in os.listdir(a.source) if f.endswith(".parquet"))
    print(f"{len(files)} files", flush=True)
    done = 0; rows = 0; errors = 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(aggregate, f, a.out, a.start) for f in files]
        for fut in as_completed(futs):
            code, n, status = fut.result()
            done += 1
            if status.startswith("error"):
                errors += 1; print(code, status, flush=True)
            elif n > 0:
                rows += n
            if done % 250 == 0:
                print(f"{done}/{len(files)} rows={rows} errors={errors}", flush=True)
    print(f"finished {done} files rows={rows} errors={errors}", flush=True)


if __name__ == "__main__":
    main()
