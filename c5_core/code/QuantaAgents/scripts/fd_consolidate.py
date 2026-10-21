"""A18 step 0: consolidate the per-job Tushare-format partitions into one parquet per table.

Input : /Users/leslie/Downloads/中证500量化60min/c5_core/data/TushareData/Ashare_10Y_20260920/dataset/<api>/<id[:2]>/<id>/data.parquet
        /Users/leslie/Downloads/中证500量化60min/c5_core/data/TushareData/Ashare_10Y_20260920/control/manifest.sqlite  (jobs: id, api, params, status, rows)
Output: /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/raw/<api>.parquet  (zstd, all returned versions kept, no dedupe)
        /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/raw/_consolidate_summary.json

Adds a framework-style ``code`` column (SH600000 / SZ000001 / BJ430047) next to ``ts_code``; keeps every
other column verbatim (dates stay YYYYMMDD strings).  ``fina_mainbz`` gets ``bz_type`` (D/P) from the job
params.  Resumable: an api whose output file exists is skipped unless --force.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/TushareData/Ashare_10Y_20260920"
OUT = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/fundamental/raw"
TABLES = [
    "daily", "daily_basic", "adj_factor",
    "income", "balancesheet", "cashflow", "fina_indicator",
    "forecast", "express", "dividend", "fina_audit", "disclosure_date",
    "report_rc",
    "stk_holdernumber", "top10_holders", "top10_floatholders", "stk_holdertrade",
    "pledge_stat", "repurchase", "share_float", "namechange", "fina_mainbz",
]


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_jobs() -> dict[str, list]:
    """All complete jobs grouped by api, read once from an immutable snapshot of the manifest (no WAL/lock interaction)."""
    src = f"{ROOT}/control/manifest.sqlite"
    con = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True)
    try:
        rows = con.execute("select api, id, params, rows from jobs where status='complete'").fetchall()
    finally:
        con.close()
    out: dict[str, list] = {}
    for api, i, p, r in rows:
        out.setdefault(api, []).append((i, json.loads(p), r or 0))
    return out


def jobs_for(jobs: dict, api: str):
    return jobs.get(api, [])


def read_one(job):
    jid, params, nrows = job
    path = os.path.join(ROOT, "dataset", jid[:2], jid, "data.parquet")
    # layout is dataset/<api>/<2hex>/<id>/data.parquet — api is the parent of the 2hex dir
    return jid, params, nrows, path


def read_partition(api: str, job):
    jid, params, nrows = job
    path = os.path.join(ROOT, "dataset", api, jid[:2], jid, "data.parquet")
    if not os.path.exists(path):
        return jid, params, nrows, None
    t = pq.read_table(path)
    if t.num_rows == 0:
        return jid, params, nrows, None
    return jid, params, nrows, t


def unify(tables: list[pa.Table]) -> pa.Table:
    """Cast every table to a common schema (first non-null type per column, in first-seen column order)."""
    types: dict[str, pa.DataType] = {}
    order: list[str] = []
    for t in tables:
        for f in t.schema:
            if f.name not in order:
                order.append(f.name)
            cur = types.get(f.name)
            if cur is None or pa.types.is_null(cur):
                types[f.name] = f.type
    for name in order:
        if pa.types.is_null(types[name]):
            types[name] = pa.string()
    schema = pa.schema([pa.field(n, types[n]) for n in order])
    out = []
    for t in tables:
        cols = []
        for n in order:
            if n in t.column_names:
                col = t[n]
                if col.type != types[n]:
                    col = pc.cast(col, types[n], safe=False)
            else:
                col = pa.nulls(t.num_rows, type=types[n])
            cols.append(col)
        out.append(pa.table(cols, schema=schema))
    return pa.concat_tables(out)


def add_code(t: pa.Table) -> pa.Table:
    if "ts_code" not in t.column_names:
        return t
    ts = pc.cast(t["ts_code"], pa.string())
    sym = pc.utf8_slice_codeunits(ts, 0, 6)
    exch = pc.utf8_slice_codeunits(ts, 7, 9)
    code = pc.binary_join_element_wise(exch, sym, "")
    idx = t.column_names.index("ts_code") + 1
    return t.add_column(idx, "code", code)


def consolidate(api: str, con: dict, workers: int, force: bool) -> dict:
    out_path = os.path.join(OUT, f"{api}.parquet")
    if os.path.exists(out_path) and not force:
        meta = pq.read_metadata(out_path)
        log(f"[{api}] exists ({meta.num_rows} rows) — skip")
        return {"api": api, "skipped": True, "rows": meta.num_rows}
    jobs = jobs_for(con, api)
    expected = sum(r for _, _, r in jobs)
    log(f"[{api}] {len(jobs)} partitions, manifest rows {expected}")
    t0 = time.time()
    tables: list[pa.Table] = []
    missing = 0
    empty = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (jid, params, nrows, t) in enumerate(ex.map(lambda j: read_partition(api, j), jobs)):
            if t is None:
                if nrows:
                    missing += 1
                else:
                    empty += 1
                continue
            if api == "fina_mainbz":
                t = t.append_column("bz_type", pa.array([params.get("type")] * t.num_rows, pa.string()))
            tables.append(t)
            if (i + 1) % 500 == 0:
                log(f"[{api}] {i + 1}/{len(jobs)} read, {time.time() - t0:.0f}s")
    if not tables:
        log(f"[{api}] no data")
        return {"api": api, "rows": 0, "expected": expected, "missing": missing, "empty": empty}
    big = unify(tables)
    del tables
    big = add_code(big)
    tmp = out_path + ".tmp"
    pq.write_table(big, tmp, compression="zstd", compression_level=3, row_group_size=1_000_000)
    os.replace(tmp, out_path)
    dt = time.time() - t0
    res = {
        "api": api, "rows": big.num_rows, "expected": expected, "cols": big.num_columns,
        "partitions": len(jobs), "missing_files": missing, "empty_partitions": empty,
        "bytes": os.path.getsize(out_path), "seconds": round(dt, 1),
        "rows_match": big.num_rows == expected,
    }
    log(f"[{api}] wrote {big.num_rows} rows ({'OK' if res['rows_match'] else 'MISMATCH vs ' + str(expected)}), "
        f"{res['bytes'] / 1e6:.0f} MB, {dt:.0f}s")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="*", default=TABLES)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    con = load_jobs()
    log(f"manifest: {sum(len(v) for v in con.values())} complete jobs in {len(con)} apis")
    summary_path = os.path.join(OUT, "_consolidate_summary.json")
    summary = {}
    if os.path.exists(summary_path):
        summary = json.load(open(summary_path, encoding="utf-8"))
    for api in args.tables:
        try:
            res = consolidate(api, con, args.workers, args.force)
        except Exception as e:  # keep going, report at the end
            log(f"[{api}] FAILED: {e!r}")
            res = {"api": api, "error": repr(e)}
        if not res.get("skipped"):
            # several consolidation processes may run at once: merge on write
            try:
                summary = json.load(open(summary_path, encoding="utf-8")) if os.path.exists(summary_path) else {}
            except Exception:
                pass
            summary[api] = res
            tmp = summary_path + f".{os.getpid()}"
            json.dump(summary, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, summary_path)
    bad = [k for k, v in summary.items() if v.get("error") or v.get("rows_match") is False]
    log(f"done; problems: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
