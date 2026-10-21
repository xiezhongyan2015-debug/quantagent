from __future__ import annotations

import argparse
import atexit
import concurrent.futures as cf
import datetime as dt
import multiprocessing as mp
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from wind_excel_client import WindExcelClient


FIELDS = [
    ("open_b", "open", "PriceAdj=B"),
    ("high_b", "high", "PriceAdj=B"),
    ("low_b", "low", "PriceAdj=B"),
    ("close_b", "close", "PriceAdj=B"),
    ("pre_close_b", "pre_close", "PriceAdj=B"),
    ("volume", "volume", None),
    ("amt", "amt", None),
    ("pct_chg", "pct_chg", None),
    ("turn", "turn", None),
    ("free_turn", "free_turn", None),
    ("vwap_b", "vwap", "PriceAdj=B"),
    ("adjfactor", "adjfactor", None),
]
_WORKER_CLIENT = None


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def worker_init() -> None:
    global _WORKER_CLIENT
    _WORKER_CLIENT = WindExcelClient()
    atexit.register(worker_close)


def worker_close() -> None:
    global _WORKER_CLIENT
    if _WORKER_CLIENT is not None:
        _WORKER_CLIENT.close()
        _WORKER_CLIENT = None


def get_calendar(start: dt.date, end: dt.date, calendar_path: str) -> list[dt.date]:
    cal = pd.read_csv(calendar_path)
    dates = list(pd.to_datetime(cal["date"]).dt.date)
    return [d for d in dates if start <= d <= end]


def safe_wsd_codes(codes: list[str], field: str, options: str | None, start: dt.date, end: dt.date) -> pd.DataFrame:
    assert _WORKER_CLIENT is not None
    attempts = 3
    last_exc = None
    for _ in range(attempts):
        try:
            return _WORKER_CLIENT.query_wsd_codes(codes, field, start, end, options=options, timeout=60)
        except Exception as exc:
            last_exc = exc
            try:
                _WORKER_CLIENT.restart()
            except Exception:
                pass
            time.sleep(2.0)
    raise RuntimeError(f"WSD failed for {field}: {last_exc}")


def process_batch(batch: list[dict], start: dt.date, end: dt.date, output_dir: str, batch_no: int, calendar_path: str) -> dict:
    assert _WORKER_CLIENT is not None
    calendars = get_calendar(start, end, calendar_path)
    codes = [x["symbol"] for x in batch]
    n = len(calendars)
    columns: dict[str, pd.Series] = {"date": pd.Series(calendars)}

    for out_name, field, option in FIELDS:
        raw = safe_wsd_codes(codes, field, option, start, end)
        if raw.shape[0] != n:
            raise RuntimeError(f"batch {batch_no} field {field} row mismatch: got {raw.shape[0]} expected {n}")
        time.sleep(0.15)
        for idx, code in enumerate(codes):
            if idx < raw.shape[1]:
                columns[f"{out_name}:{code}"] = raw.iloc[:, idx].reset_index(drop=True)

    frame = pd.DataFrame(columns)
    records = []
    for meta in batch:
        symbol = meta["symbol"]
        list_date = pd.to_datetime(meta.get("list_date"), errors="coerce")
        delist_date = pd.to_datetime(meta.get("delist_date"), errors="coerce")
        mask = frame["date"].map(lambda d: (pd.isna(list_date) or d >= list_date.date()) and (pd.isna(delist_date) or d <= delist_date.date()))
        sub = frame.loc[mask, ["date"]].copy()
        sub["symbol"] = symbol
        for out_name, _, _ in FIELDS:
            col = f"{out_name}:{symbol}"
            if col in frame.columns:
                sub[out_name] = frame.loc[mask, col].values
            else:
                sub[out_name] = None
        records.append(sub)
    result = pd.concat(records, ignore_index=True)
    result = result.sort_values(["date", "symbol"]).reset_index(drop=True)
    out_path = Path(output_dir) / f"part-{batch_no:05d}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(result, preserve_index=False)
    pq.write_table(table, out_path, compression="zstd")
    return {"batch": batch_no, "symbols": len(batch), "rows": len(result), "path": str(out_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("universe_all.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/daily/parts"))
    parser.add_argument("--start-date", type=dt.date.fromisoformat, default=dt.date(2010, 1, 1))
    parser.add_argument("--end-date", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    uni = pd.read_csv(args.universe)
    uni = uni[uni["symbol"].notna()].copy()
    uni["symbol"] = uni["symbol"].astype(str).str.strip().str.upper()
    uni = uni[uni["symbol"].str.match(r"^\d{6}\.(SZ|SH|BJ)$", na=False)]
    uni = uni.drop_duplicates("symbol").sort_values("symbol")
    indexed = list(enumerate(uni.to_dict("records")))
    grouped = {}
    for idx, row in indexed:
        grouped.setdefault(idx // args.batch, []).append(row)
    batch = [grouped[k] for k in sorted(grouped)]
    args.output.mkdir(parents=True, exist_ok=True)
    print(f"symbols={len(uni)} batches={len(batch)} workers={args.workers}", flush=True)
    calendar_path = args.output.parent / "trading_calendar.csv"
    cal_client = WindExcelClient()
    try:
        cal = cal_client.query_wsd_fields("000001.SZ", ["close"], args.start_date, args.end_date, options="PriceAdj=B", timeout=180)
    finally:
        cal_client.close()
    if cal.empty:
        raise RuntimeError("unable to retrieve trading calendar")
    cal[["date"]].to_csv(calendar_path, index=False, encoding="utf-8-sig")
    print(f"calendar dates={len(cal)} path={calendar_path}", flush=True)
    required_cols = {name for name, _, _ in FIELDS}
    pending = []
    for i, b in enumerate(batch, 1):
        part = args.output / f"part-{i:05d}.parquet"
        done = args.output / f"part-{i:05d}.done"
        if part.exists() and done.exists():
            try:
                cols = set(pq.read_schema(part).names)
                if required_cols.issubset(cols):
                    print(f"skip completed batch {i}", flush=True)
                    continue
            except Exception:
                pass
        pending.append((i, b))
    print(f"pending={len(pending)} completed={len(batch)-len(pending)}", flush=True)
    start = time.time()
    ctx = mp.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx, initializer=worker_init) as pool:
        futures = [pool.submit(process_batch, b, args.start_date, args.end_date, str(args.output), i, str(calendar_path)) for i, b in pending]
        for i, fut in enumerate(cf.as_completed(futures), 1):
            try:
                print(fut.result(), flush=True)
            except Exception as exc:
                print(f"batch failed: {type(exc).__name__}: {exc}", flush=True)
            if i % 20 == 0:
                print(f"completed {i}/{len(batch)} elapsed={time.time()-start:.0f}s", flush=True)

    parts = sorted(args.output.glob("part-*.parquet"))
    total_rows = 0
    for p in parts:
        total_rows += pq.ParquetFile(p).metadata.num_rows
    print(f"done parts={len(parts)} rows={total_rows} elapsed={time.time()-start:.0f}s", flush=True)


if __name__ == "__main__":
    main()
