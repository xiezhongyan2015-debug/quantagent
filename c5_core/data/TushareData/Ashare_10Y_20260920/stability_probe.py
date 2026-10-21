"""One-minute capped throughput validation plus bounded interface edge probes."""
import concurrent.futures as cf
import gzip
import json
from pathlib import Path
import re
import statistics
import sys
import threading
import time

from direct_client import DirectClient

ROOT = Path(__file__).resolve().parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r"[a-fA-F0-9]{40,80}", token):
        raise RuntimeError("Missing token")
    run = Path(json.loads((ROOT / "latest_benchmark.json").read_text(encoding="utf-8"))["run"])
    out = run / "stability"
    out.mkdir(exist_ok=True)
    catalog = json.loads((ROOT / "fundamental_catalog.json").read_text(encoding="utf-8"))
    fields = {k: [f["field"] for f in v.get("fields", [])] for k, v in catalog.items()}
    cfg = json.loads((ROOT / "network_config.json").read_text(encoding="utf-8"))
    client = DirectClient(token, cfg, max_rps=5)
    lock, stop = threading.Lock(), threading.Event()
    records = []

    def call(spec):
        label, api, params = spec
        if stop.is_set():
            return None
        started = time.monotonic()
        rec = {"label": label, "api": api, "params": params}
        try:
            obj, metrics = client.call(api, params, fields.get(api, []))
            rec.update(metrics)
            data = obj.get("data") or {}
            names, rows = data.get("fields") or [], data.get("items") or []
            rec.update({"code": obj.get("code"), "message": obj.get("msg"), "rows": len(rows), "fields": names,
                        "missing_fields": sorted(set(fields.get(api, [])) - set(names)),
                        "ok": metrics["http_status"] == 200 and obj.get("code") == 0})
            rec["limited"] = metrics["http_status"] == 429 or bool(re.search(r"每分钟|频次|频率|限流|too many|rate.limit", str(rec.get("message")), re.I))
            if rec["limited"]:
                stop.set()
            packed = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=3, mtime=0)
            (out / (label + ".json.gz")).write_bytes(packed)
            rec["raw_gzip_bytes"] = len(packed)
            rec["row_signatures"] = None
            for column in ["ann_date", "end_date"]:
                if column in names:
                    values = [str(row[names.index(column)]) for row in rows if row[names.index(column)] not in (None, "")]
                    rec[column + "_range"] = [min(values), max(values)] if values else []
        except Exception as exc:
            rec.update({"ok": False, "error": str(exc).replace(token, "[REDACTED]")})
        rec["elapsed_seconds"] = time.monotonic() - started
        with lock:
            records.append(rec)
            with (out / "requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    edge_specs = [
        ("mainbz_range_P", "fina_mainbz", {"ts_code": "600519.SH", "start_date": "20130101", "end_date": "20260918", "type": "P"}),
        ("mainbz_range_D", "fina_mainbz", {"ts_code": "600519.SH", "start_date": "20130101", "end_date": "20260918", "type": "D"}),
        ("mainbz_other", "fina_mainbz", {"ts_code": "000333.SZ", "start_date": "20160101", "end_date": "20260918", "type": "P"}),
        ("indicator_long", "fina_indicator", {"ts_code": "600519.SH", "start_date": "19900101", "end_date": "20260918"}),
        ("indicator_page0", "fina_indicator", {"ts_code": "600519.SH", "start_date": "19900101", "end_date": "20260918", "limit": 10, "offset": 0}),
        ("indicator_page1", "fina_indicator", {"ts_code": "600519.SH", "start_date": "19900101", "end_date": "20260918", "limit": 10, "offset": 10}),
        ("holder_page0", "top10_holders", {"ts_code": "600519.SH", "start_date": "20130101", "end_date": "20260918", "limit": 10, "offset": 0}),
        ("holder_page1", "top10_holders", {"ts_code": "600519.SH", "start_date": "20130101", "end_date": "20260918", "limit": 10, "offset": 10}),
    ]
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for rec in pool.map(call, edge_specs):
            print(json.dumps({"event": "edge", **{k: rec.get(k) for k in ["label", "rows", "code", "missing_fields", "ann_date_range", "end_date_range", "error"]}}, ensure_ascii=False), flush=True)

    universe = json.loads((run / "universe.json").read_text(encoding="utf-8"))
    days = json.loads((run / "trading_days.json").read_text(encoding="utf-8"))
    codes = [s["ts_code"] for s in universe if str(s.get("list_date") or "") < "20160101"]
    specs = []
    for i in range(300):
        if i % 10 == 0:
            specs.append((f"steady_{i:03d}", "daily_basic", {"trade_date": days[31 + i * 7]}))
        else:
            code = codes[(i * 11 + 7) % len(codes)]
            api = ["income", "balancesheet", "cashflow", "fina_indicator"][i % 4]
            specs.append((f"steady_{i:03d}", api, {"ts_code": code, "start_date": "20130101", "end_date": "20260918"}))
    started = time.monotonic()
    completed = []
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call, spec) for spec in specs]
        for future in cf.as_completed(futures):
            rec = future.result()
            if rec:
                completed.append(rec)
            if len(completed) and len(completed) % 100 == 0:
                print(json.dumps({"event": "progress", "completed": len(completed), "elapsed_seconds": time.monotonic() - started}), flush=True)
    elapsed = time.monotonic() - started
    good = [x for x in completed if x["ok"]]
    latencies = sorted(x["network_seconds"] for x in good)
    summary = {"workers": 4, "planned_requests": len(specs), "requests": len(completed), "successes": len(good),
               "errors": len(completed) - len(good), "limited": sum(bool(r.get("limited")) for r in completed),
               "wall_seconds": elapsed, "requests_per_second": len(good) / elapsed,
               "rows": sum(r["rows"] for r in good), "wire_MB_per_second": sum(r["wire_bytes"] for r in good) / 1e6 / elapsed,
               "decoded_MB_per_second": sum(r["decoded_bytes"] for r in good) / 1e6 / elapsed,
               "latency_median_seconds": statistics.median(latencies) if latencies else None,
               "latency_p95_seconds": latencies[int((len(latencies) - 1) * .95)] if latencies else None,
               "missing_fields_requests": sum(bool(r.get("missing_fields")) for r in good),
               "all_routes_physical": all(r.get("route", {}).get("interface_index") == 18 for r in good),
               "max_rps": 5, "edge_requests": len(edge_specs)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "finished", **summary}), flush=True)


if __name__ == "__main__":
    main()
