from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

import pandas as pd

from wind_excel_client import WindExcelClient


CODE_RE = re.compile(r"^\d{6}\.(?:SZ|SH|BJ)$", re.I)


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("universe_index.csv"))
    parser.add_argument("--indices", nargs="+", default=["000300.SH", "000905.SH", "000852.SH"])
    args = parser.parse_args()

    frames = []
    for idx in args.indices:
        client = WindExcelClient()
        try:
            expected = {"000300.SH": 300, "000905.SH": 500, "000852.SH": 1000}.get(idx)
            raw = client.query_wset("indexconstituent", f"windcode={idx}", timeout=120, expected_rows=expected)
        finally:
            client.close()
        if raw.empty or raw.shape[1] < 3:
            raise RuntimeError(f"invalid constituents response for {idx}: shape={raw.shape}")
        raw = raw.iloc[:, :5].copy()
        raw.columns = ["asof", "symbol", "name", "industry", "weight"][: raw.shape[1]]
        raw = raw[raw["symbol"].astype(str).str.upper().str.match(CODE_RE)]
        raw["symbol"] = raw["symbol"].astype(str).str.upper().str.strip()
        raw["index_code"] = idx
        frames.append(raw[["symbol", "name", "industry", "weight", "index_code"]])
        print(f"{idx}: {len(raw)} constituents", flush=True)
    client = WindExcelClient()
    try:
        uni = pd.concat(frames, ignore_index=True)
        uni = uni.drop_duplicates(["symbol", "index_code"]).sort_values(["symbol", "index_code"])
        symbols = sorted(uni["symbol"].unique())
        static_rows = []
        fields = ["s_info_name", "s_ipo_listeddate", "s_info_delistdate", "s_info_exchmarket", "s_info_stockclass"]
        for i, batch in enumerate(chunks(symbols, 20), 1):
            try:
                sdf = client.query_wss(batch, fields, timeout=180)
            except Exception:
                mid = len(batch) // 2
                sdf = pd.concat([client.query_wss(batch[:mid], fields, timeout=180), client.query_wss(batch[mid:], fields, timeout=180)], ignore_index=True)
            sdf.insert(0, "symbol", batch[: len(sdf)])
            static_rows.append(sdf)
            if i % 20 == 0:
                print(f"static {i*20}/{len(symbols)}", flush=True)
        static = pd.concat(static_rows, ignore_index=True)
        static["list_date"] = pd.to_datetime(static["s_ipo_listeddate"], errors="coerce").dt.date
        static["delist_date"] = pd.to_datetime(static["s_info_delistdate"], errors="coerce").dt.date
        static = static.rename(columns={"s_info_exchmarket": "exchange", "s_info_stockclass": "stockclass"})
        static = static[["symbol", "list_date", "delist_date", "exchange"]]
        out = uni.merge(static, on="symbol", how="left")
        out = out.drop_duplicates("symbol").sort_values("symbol")
        out.to_csv(args.output, index=False, encoding="utf-8-sig")
        print(f"wrote {len(out)} unique symbols to {args.output.resolve()}", flush=True)
        print(out.head(10).to_string(index=False), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
