"""Bounded direct-network calibration. Token arrives only on stdin."""
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import threading
import time

import pyarrow as pa
import pyarrow.parquet as pq

from direct_client import DirectClient

ROOT = Path(__file__).resolve().parent
CATALOG = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/ChoiceData/TushareAccessAudit_20260920/document_fields.json")
START, END = "20160920", "20260918"


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def percentile(values, p):
    a = sorted(values)
    return a[max(0, math.ceil(len(a) * p) - 1)] if a else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r"[a-fA-F0-9]{40,80}", token):
        raise RuntimeError("Missing or malformed token")
    run = ROOT / "benchmarks" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (run / "raw").mkdir(parents=True)
    (run / "parquet").mkdir()
    fields = {k: [f["field"] for f in v["fields"]] for k, v in json.loads(CATALOG.read_text(encoding="utf-8")).items()}
    fields.update({
        "stock_basic": "ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs,act_name,act_ent_type".split(","),
        "trade_cal": "exchange,cal_date,is_open,pretrade_date".split(","),
        "daily": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount".split(","),
        "adj_factor": ["ts_code", "trade_date", "adj_factor"],
    })
    dump(run / "requested_fields.json", fields)
    config = json.loads((ROOT / "network_config.json").read_text(encoding="utf-8"))
    client = DirectClient(token, config, max_rps=5)
    dump(run / "network_evidence.json", {"at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "pid": os.getpid(), "config": config, "dns_results": client.ips, "dns_attempts": client.dns_attempts})
    records = []
    records_lock = threading.Lock()
    seq = 0

    def call(label, api, params):
        nonlocal seq
        with records_lock:
            seq += 1
            number = seq
        requested = fields.get(api, [])
        rec = {"sequence": number, "label": label, "api": api, "params": params,
               "at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "requested_field_count": len(requested)}
        started = time.monotonic()
        obj = None
        try:
            obj, metrics = client.call(api, params, requested)
            rec.update(metrics)
            data = obj.get("data") or {}
            names, items = data.get("fields") or [], data.get("items") or []
            rec.update({"code": obj.get("code"), "message": obj.get("msg"), "rows": len(items),
                        "fields": names, "field_count": len(names), "missing_fields": sorted(set(requested) - set(names)),
                        "ok": metrics["http_status"] == 200 and obj.get("code") == 0})
            rec["limited"] = metrics["http_status"] == 429 or bool(re.search(r"每分钟|频次|频率|限流|too many|rate.limit", str(rec.get("message")), re.I))
            if any(len(row) != len(names) for row in items):
                raise ValueError("Response row width does not match returned fields")
            key = f"{number:04d}_{label}"
            blob = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
            packed = gzip.compress(blob, compresslevel=3, mtime=0)
            raw_path = run / "raw" / (key + ".json.gz")
            raw_path.write_bytes(packed)
            rec.update({"raw_file": str(raw_path.relative_to(run)), "raw_gzip_bytes": len(packed), "raw_sha256": hashlib.sha256(packed).hexdigest()})
            if items and rec["ok"]:
                rec["nonnull_cells"] = sum(x is not None and x != "" for row in items for x in row)
                rec["total_cells"] = len(items) * len(names)
                for col in ("ts_code", "ann_date", "f_ann_date", "end_date", "trade_date", "report_date"):
                    if col in names:
                        values = [str(row[names.index(col)]) for row in items if row[names.index(col)] not in (None, "")]
                        rec[col + "_range"] = [min(values), max(values)] if values else []
                        if col == "ts_code":
                            rec["unique_stocks"] = len(set(values))
                            if params.get("ts_code") and set(values) != {params["ts_code"]}:
                                rec["stock_filter_violation"] = True
                try:
                    table = pa.Table.from_pydict({name: [row[i] for row in items] for i, name in enumerate(names)})
                    pq_path = run / "parquet" / (key + ".parquet")
                    pq.write_table(table, pq_path, compression="zstd")
                    rec["parquet_file"] = str(pq_path.relative_to(run))
                    rec["parquet_bytes"] = pq_path.stat().st_size
                except Exception as exc:
                    rec["parquet_error"] = str(exc).replace(token, "[REDACTED]")
        except Exception as exc:
            rec.update({"ok": False, "error": f"{type(exc).__name__}: {exc}".replace(token, "[REDACTED]")})
        rec["elapsed_seconds"] = time.monotonic() - started
        with records_lock:
            records.append(rec)
            with (run / "requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec, obj

    print(json.dumps({"event": "started", "run": str(run), "pid": os.getpid(), "endpoint_ip": client.ips, "max_rps": 5}), flush=True)
    stocks = []
    for status in ["L", "D", "P"]:
        rec, obj = call("universe_" + status, "stock_basic", {"list_status": status})
        print(json.dumps({"event": "universe", "status": status, "rows": rec.get("rows"), "ok": rec["ok"], "message": rec.get("message")}, ensure_ascii=False), flush=True)
        if not rec["ok"]:
            raise RuntimeError("Universe preflight failed; see saved request record")
        data = obj.get("data") or {}
        stocks.extend(dict(zip(data.get("fields", []), row)) for row in data.get("items", []))
    by_code = {}
    excluded = []
    for s in stocks:
        code = s.get("ts_code", "")
        listed, delisted = str(s.get("list_date") or ""), str(s.get("delist_date") or "")
        if (re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) and s.get("exchange") in ("SSE", "SZSE", "BSE")
                and len(listed) == 8 and listed <= END and (not delisted or delisted >= START)):
            by_code[code] = s
        else:
            excluded.append(s)
    universe = sorted(by_code.values(), key=lambda s: s["ts_code"])
    dump(run / "universe.json", universe)
    dump(run / "excluded_universe_rows.json", excluded)
    rec, obj = call("calendar", "trade_cal", {"exchange": "SSE", "start_date": START, "end_date": END})
    if not rec["ok"]:
        raise RuntimeError("Trade calendar preflight failed")
    data = obj.get("data") or {}
    days = sorted({r["cal_date"] for row in data.get("items", []) if (r := dict(zip(data["fields"], row))).get("is_open") in (1, "1")})
    if len(days) < 2000:
        raise RuntimeError("Trading calendar unexpectedly short")
    dump(run / "trading_days.json", days)
    print(json.dumps({"event": "scope", "stocks": len(universe), "excluded_rows": len(excluded), "trading_days": len(days), "first": days[0], "last": days[-1]}), flush=True)

    finance_apis = ["income", "balancesheet", "cashflow", "fina_indicator"]
    candidates = [s["ts_code"] for s in universe if s["ts_code"].endswith((".SH", ".SZ")) and str(s.get("list_date")) < "20140101"]
    # Spread across exchanges and codes; disjoint cohorts avoid query-cache repeats.
    chosen = [candidates[round(i * (len(candidates) - 1) / 95)] for i in range(96)]
    stages = []

    def specs_for_stage(stage_index, count=24):
        jobs = []
        for i in range(16):
            jobs.append((f"c{stage_index}_financial_{i}", finance_apis[i % 4],
                         {"ts_code": chosen[stage_index * 16 + i], "start_date": START, "end_date": END}))
        for i in range(6):
            day = days[min(len(days) - 1, (stage_index * 6 + i) * (len(days) - 1) // 25)]
            api = ["daily", "daily_basic", "adj_factor"][i % 3]
            jobs.append((f"c{stage_index}_{api}_{i}", api, {"trade_date": day}))
        for i in range(2):
            year = 2017 + stage_index * 2 + i
            jobs.append((f"c{stage_index}_report_{i}", "report_rc", {"start_date": f"{year}0401", "end_date": f"{year}0407"}))
        return jobs

    def run_stage(label, workers, jobs):
        start = time.monotonic()
        results = []
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(call, *spec) for spec in jobs]
            for future in cf.as_completed(futures):
                results.append(future.result()[0])
        elapsed = time.monotonic() - start
        good = [r for r in results if r["ok"]]
        summary = {"label": label, "workers": workers, "requests": len(results), "successes": len(good),
                   "errors": len(results) - len(good), "limited": sum(bool(r.get("limited")) for r in results),
                   "wall_seconds": elapsed, "requests_per_second": len(good) / elapsed,
                   "rows": sum(r.get("rows", 0) for r in good),
                   "rows_per_second": sum(r.get("rows", 0) for r in good) / elapsed,
                   "wire_MB_per_second": sum(r.get("wire_bytes", 0) for r in good) / 1e6 / elapsed,
                   "decoded_MB_per_second": sum(r.get("decoded_bytes", 0) for r in good) / 1e6 / elapsed,
                   "latency_median_seconds": statistics.median([r["network_seconds"] for r in good]) if good else None,
                   "latency_p95_seconds": percentile([r["network_seconds"] for r in good], .95),
                   "field_failures": sum(bool(r.get("missing_fields")) for r in good),
                   "stock_filter_failures": sum(bool(r.get("stock_filter_violation")) for r in good),
                   "parquet_errors": sum(bool(r.get("parquet_error")) for r in good),
                   "request_sequences": sorted(r["sequence"] for r in results)}
        stages.append(summary)
        dump(run / "stages.json", stages)
        print(json.dumps({"event": "stage", **summary}, ensure_ascii=False), flush=True)
        return summary

    for stage_index, workers in enumerate([1, 2, 4, 8]):
        result = run_stage(f"concurrency_{workers}", workers, specs_for_stage(stage_index))
        if result["limited"] or result["errors"] > 2:
            print(json.dumps({"event": "stopped_escalation", "reason": "rate limit or repeated errors"}), flush=True)
            break
    clean = [s for s in stages if s["errors"] == 0 and s["limited"] == 0]
    best = max(clean, key=lambda s: s["requests_per_second"]) if clean else None
    if best:
        # Prefer fewer sockets when throughput is within 10% of the best stage.
        recommended = min(s["workers"] for s in clean if s["requests_per_second"] >= best["requests_per_second"] * .9)
        jobs = []
        for i in range(64):
            jobs.append((f"sustained_financial_{i}", finance_apis[i % 4],
                         {"ts_code": chosen[32 + i], "start_date": "20130101", "end_date": END}))
        run_stage("sustained_financial", recommended, jobs)
    else:
        recommended = None
    summary = {"run": str(run), "at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "phase": "benchmark_complete_bulk_not_started",
               "formal_start": START, "formal_end": END, "universe_count": len(universe), "trading_days": len(days),
               "universe_status_counts": {x: sum(s.get("list_status") == x for s in universe) for x in ["L", "D", "P"]},
               "requests": len(records), "recommended_workers_preliminary": recommended,
               "rate_ceiling_requests_per_minute": 300, "stages": stages,
               "bytes_saved": sum(p.stat().st_size for p in run.rglob("*") if p.is_file()),
               "all_routes_physical": all(r.get("route", {}).get("interface_index") == config["interface_index"] for r in records if r.get("ok")),
               "quality_followup_required": "Check per-interface row caps, parameter semantics, historical universe, and optional-endpoint availability before full extraction."}
    dump(run / "summary.json", summary)
    dump(ROOT / "latest_benchmark.json", {"run": str(run)})
    print(json.dumps({"event": "finished", "summary": str(run / "summary.json"), "requests": len(records), "recommended_workers_preliminary": recommended, "bytes_saved": summary["bytes_saved"]}), flush=True)


if __name__ == "__main__":
    main()
