from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import multiprocessing as mp
import time
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
FIELDS = ["s_info_name", "s_ipo_listeddate", "s_info_delistdate", "s_info_stockclass", "s_info_exchmarket"]
_WORKER_CLIENT = None


def candidate_codes() -> list[str]:
    out = []
    for suffix, start, end in RANGES:
        out.extend(f"{i:06d}.{suffix}" for i in range(start, end + 1))
    return out


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def worker_init() -> None:
    global _WORKER_CLIENT
    _WORKER_CLIENT = WindExcelClient()


def worker_close() -> None:
    global _WORKER_CLIENT
    if _WORKER_CLIENT is not None:
        _WORKER_CLIENT.close()
        _WORKER_CLIENT = None


def query_batch(codes: list[str]) -> list[dict]:
    assert _WORKER_CLIENT is not None
    try:
        df = _WORKER_CLIENT.query_wss(codes, FIELDS, timeout=90)
        out = df.to_dict("records")
        for row, code in zip(out, codes[: len(out)]):
            row["symbol"] = code
        return out
    except Exception as exc:
        if len(codes) <= 1:
            return [{"symbol": codes[0], **{f: None for f in FIELDS}}]
        mid = len(codes) // 2
        return query_batch(codes[:mid]) + query_batch(codes[mid:])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("universe_all.csv"))
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, default=dt.date(2010, 1, 1))
    args = parser.parse_args()

    codes = candidate_codes()
    batches = list(chunks(codes, args.batch))
    print(f"candidates={len(codes)} batches={len(batches)} workers={args.workers}", flush=True)
    rows: list[dict] = []
    start = time.time()
    ctx = mp.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx, initializer=worker_init) as pool:
        futures = [pool.submit(query_batch, b) for b in batches]
        for i, fut in enumerate(cf.as_completed(futures), 1):
            try:
                rows.extend(fut.result())
            except Exception as exc:
                print(f"batch error: {type(exc).__name__}: {exc}", flush=True)
            if i % 100 == 0:
                elapsed = time.time() - start
                print(f"completed {i}/{len(batches)} batches elapsed={elapsed:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("no universe rows returned")
    df["list_date"] = pd.to_datetime(df["s_ipo_listeddate"], errors="coerce").dt.date
    df["delist_date"] = pd.to_datetime(df["s_info_delistdate"], errors="coerce").dt.date
    df = df[df["s_info_stockclass"].astype(str).str.strip().eq(A_SHARE)]
    df = df[df["list_date"].notna()]
    today = dt.date.today()
    df = df[(df["list_date"] <= today) & (df["delist_date"].isna() | (df["delist_date"] >= args.start_date))]
    df = df.rename(columns={"s_info_name": "name", "s_info_exchmarket": "exchange"})
    df = df[["symbol", "name", "list_date", "delist_date", "exchange"]]
    df = df.drop_duplicates("symbol").sort_values(["list_date", "symbol"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"wrote {len(df)} symbols to {args.output.resolve()}", flush=True)
    print(df.head(10).to_string(index=False), flush=True)
    print(df.tail(10).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
