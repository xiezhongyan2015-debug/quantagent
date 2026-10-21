from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from wind_excel_client import WindExcelClient


A_SHARE = "A" + chr(0x80A1)  # A?, kept as a code point for Windows encoding safety


RANGES = [
    ("SZ", 1, 4999),
    ("SZ", 300000, 302999),
    ("SH", 600000, 609999),
    ("SH", 688000, 689999),
    ("BJ", 430000, 439999),
    ("BJ", 830000, 839999),
    ("BJ", 870000, 879999),
    ("BJ", 920000, 929999),
]


def candidate_codes() -> list[str]:
    out = []
    for suffix, start, end in RANGES:
        out.extend(f"{i:06d}.{suffix}" for i in range(start, end + 1))
    return out


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def query_batch(client: WindExcelClient, codes: list[str]) -> pd.DataFrame:
    fields = ["s_info_name", "s_ipo_listeddate", "s_info_delistdate", "s_info_stockclass", "s_info_exchmarket"]
    try:
        return client.query_wss(codes, fields, timeout=180)
    except Exception:
        if len(codes) <= 1:
            return pd.DataFrame([{f: None for f in fields}])
        mid = len(codes) // 2
        return pd.concat([query_batch(client, codes[:mid]), query_batch(client, codes[mid:])], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("universe_all.csv"))
    parser.add_argument("--batch", type=int, default=400)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, default=dt.date(2010, 1, 1))
    args = parser.parse_args()

    codes = candidate_codes()
    client = WindExcelClient()
    frames = []
    try:
        for i, batch in enumerate(chunks(codes, args.batch), 1):
            df = query_batch(client, batch)
            df.insert(0, "symbol", batch[: len(df)])
            frames.append(df)
            if i % 10 == 0:
                print(f"queried {i} batches / {i * args.batch} candidate codes", flush=True)
    finally:
        client.close()

    all_df = pd.concat(frames, ignore_index=True)
    all_df["list_date"] = pd.to_datetime(all_df["s_ipo_listeddate"], errors="coerce").dt.date
    all_df["delist_date"] = pd.to_datetime(all_df["s_info_delistdate"], errors="coerce").dt.date
    all_df = all_df[all_df["s_info_stockclass"].astype(str).str.strip().eq(A_SHARE)]
    all_df = all_df[all_df["list_date"].notna()]
    today = dt.date.today()
    all_df = all_df[
        (all_df["list_date"] <= today)
        & (all_df["delist_date"].isna() | (all_df["delist_date"] >= args.start_date))
    ]
    all_df = all_df.rename(
        columns={
            "s_info_name": "name",
            "s_info_delistdate": "delist_date_raw",
            "s_info_stockclass": "stockclass",
            "s_info_exchmarket": "exchange",
        }
    )
    all_df = all_df[["symbol", "name", "list_date", "delist_date", "exchange"]]
    all_df = all_df.drop_duplicates("symbol").sort_values(["list_date", "symbol"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"wrote {len(all_df)} A-share symbols to {args.output.resolve()}")
    print(all_df.head(10).to_string(index=False))
    print(all_df.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
