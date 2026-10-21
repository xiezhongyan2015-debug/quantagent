"""Resumable all-A fundamental extraction with uncapped, adaptive concurrency.

Every network socket is pinned to the physical interface by direct_client.
No token is persisted. Paged endpoints require an empty terminal page;
non-paged endpoints use explicit endpoint-specific completion rules.
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import email.utils
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import sys
import threading
import time
import traceback

import pyarrow as pa
import pyarrow.parquet as pq

from direct_client import DirectClient, ApiHTTPError, CallCancelled
from unpaged_recovery import recover, RecoveryError

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dataset"
CONTROL = ROOT / "control"
CONTROL.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)
NOW = lambda: dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, obj):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def key_for(api, params, fields):
    return hashlib.sha256(canonical({"endpoint": "https://tuaremax.top", "api": api, "params": params, "fields": fields}).encode()).hexdigest()


class RetryJob(Exception):
    pass


class DataQualityError(Exception):
    pass


def schema_for(names, catalog):
    types = {f["field"]: f.get("type", "str").lower() for f in catalog}
    cols = []
    for name in names:
        typ = types.get(name, "str")
        if typ in ("float", "double", "int", "integer"):
            # Vendor numbers occasionally arrive as mixed integer/float values.
            cols.append(pa.field(name, pa.float64()))
        else:
            cols.append(pa.field(name, pa.string()))
    return pa.schema(cols)


def make_table(names, rows, schema):
    arrays = []
    for i, field in enumerate(schema):
        values = []
        for row in rows:
            val = row[i]
            if val is None or val == "":
                values.append(None)
            elif pa.types.is_floating(field.type):
                values.append(float(val))
            else:
                values.append(str(val))
        arrays.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def init_db(catalog, plan):
    db = sqlite3.connect(CONTROL / "manifest.sqlite", timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, api TEXT NOT NULL, params TEXT NOT NULL, fields TEXT NOT NULL, priority INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, rows INTEGER, pages INTEGER, raw_bytes INTEGER, parquet_bytes INTEGER, elapsed REAL, finished_at TEXT, error TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS job_state ON jobs(status,priority,api)")
    db.execute("CREATE INDEX IF NOT EXISTS job_dispatch ON jobs(priority,attempts,id) WHERE status IN ('pending','retry') AND attempts<8")
    db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)")
    # A terminated process can leave jobs marked running. Committed artifacts
    # are checked and reused by extract_job before making another request.
    db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
    seeded = db.execute("SELECT value FROM metadata WHERE key='seeded_v1'").fetchone()
    if not seeded:
        universe = json.loads(Path(plan["universe_file"]).read_text(encoding="utf-8"))
        days = json.loads(Path(plan["trading_days_file"]).read_text(encoding="utf-8"))
        end = plan["formal_end_date"]
        specs = []
        per_stock = [
            ("income", 10), ("balancesheet", 10), ("cashflow", 10), ("fina_indicator", 10),
            ("dividend", 30), ("forecast", 30), ("express", 30), ("fina_audit", 30),
            ("stk_holdernumber", 40), ("top10_holders", 40),
            ("top10_floatholders", 40), ("stk_holdertrade", 40), ("pledge_stat", 40),
            ("repurchase", 40), ("share_float", 40), ("namechange", 45),
        ]
        for stock in universe:
            code = stock["ts_code"]
            for api, priority in per_stock:
                params = {"ts_code": code}
                if api not in ("dividend", "namechange", "disclosure_date"):
                    params.update(start_date="20110101", end_date=end)
                specs.append((api, params, priority))
            for business_type in ("P", "D"):
                specs.append(("fina_mainbz", {"ts_code": code, "start_date": "20110101", "end_date": end, "type": business_type}, 42))
        for day in days:
            for api in ("daily", "daily_basic", "adj_factor"):
                specs.append((api, {"trade_date": day}, 20))
        start = dt.datetime.strptime("20150920", "%Y%m%d").date()
        last = dt.datetime.strptime(end, "%Y%m%d").date()
        while start <= last:
            finish = min(start + dt.timedelta(days=6), last)
            specs.append(("report_rc", {"start_date": start.strftime("%Y%m%d"), "end_date": finish.strftime("%Y%m%d")}, 25))
            start = finish + dt.timedelta(days=1)
        inserts = []
        for api, params, priority in specs:
            names = [f["field"] for f in catalog[api]["fields"]]
            inserts.append((key_for(api, params, names), api, canonical(params), canonical(names), priority))
        db.executemany("INSERT OR IGNORE INTO jobs(id,api,params,fields,priority) VALUES(?,?,?,?,?)", inserts)
        db.execute("INSERT INTO metadata VALUES('seeded_v1',?)", (NOW(),))
        db.commit()
    # Disclosure date's end_date is an exact reporting period, not a range.
    # Fetch whole-market quarters to avoid a current-period default silently
    # replacing historical disclosure schedules.
    if not db.execute("SELECT value FROM metadata WHERE key='disclosure_quarters_v2'").fetchone():
        db.execute("UPDATE jobs SET status='superseded' WHERE api='disclosure_date' AND status='pending'")
        names = [f['field'] for f in catalog['disclosure_date']['fields']]
        for year in range(2010, 2027):
            for suffix in ('0331','0630','0930','1231'):
                period = f'{year}{suffix}'
                if period < '20101231' or period > plan['formal_end_date']:
                    continue
                params = {'end_date': period}
                db.execute("INSERT OR IGNORE INTO jobs(id,api,params,fields,priority) VALUES(?,?,?,?,?)",
                           (key_for('disclosure_date',params,names),'disclosure_date',canonical(params),canonical(names),35))
        db.execute("INSERT INTO metadata VALUES('disclosure_quarters_v2',?)", (NOW(),))
        db.commit()
    return db


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument("--skip-calibration", action="store_true")
    args = parser.parse_args()
    token = sys.stdin.readline().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,80}", token):
        raise RuntimeError("Token must be supplied through stdin")
    lock_path = CONTROL / "download.lock"
    # Exclusive Windows file handle avoids PID reuse or stale lock ambiguity.
    import msvcrt
    lock_file = lock_path.open("a+b")
    lock_file.seek(0)
    if lock_path.stat().st_size == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise RuntimeError("Another downloader currently owns the dataset lock")

    plan = json.loads((ROOT / "download_plan.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "fundamental_catalog.json").read_text(encoding="utf-8"))
    benchmark = Path(json.loads((ROOT / "latest_benchmark.json").read_text(encoding="utf-8"))["run"])
    reference_fields = json.loads((benchmark / "requested_fields.json").read_text(encoding="utf-8"))
    for api in ("daily", "adj_factor"):
        catalog[api] = {"fields": [{"field": name, "type": "str" if name in ("ts_code", "trade_date") else "float"} for name in reference_fields[api]]}
    cfg = json.loads((ROOT / "network_config.json").read_text(encoding="utf-8"))
    client = DirectClient(token, cfg, max_rps=None)
    db = init_db(catalog, plan)
    total_jobs = db.execute("SELECT COUNT(*) FROM jobs WHERE status!='superseded'").fetchone()[0]
    guard = threading.Lock()
    batch_throttled = threading.Event()
    counters = collections.Counter()
    started = time.monotonic()
    request_log = CONTROL / "requests.jsonl"
    stop_path = CONTROL / "stop.request"
    trials = []
    throttle = {"until": 0.0, "last_limit": 0.0, "streak": 0}
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    atomic_json(CONTROL / "process.json", {"pid": os.getpid(), "started_at_utc": NOW(), "run_id": run_id,
                                         "fixed_rps_limit": None, "network": cfg, "ips": client.ips})

    def emit(obj):
        obj = {"at_utc": NOW(), **obj}
        print(json.dumps(obj, ensure_ascii=False), flush=True)
        with (CONTROL / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def metrics():
        with guard:
            return dict(counters)

    def server_backoff(retry_after=None):
        explicit = None
        if retry_after:
            try:
                explicit = max(1.0, float(retry_after))
            except (TypeError, ValueError):
                try:
                    when = email.utils.parsedate_to_datetime(str(retry_after))
                    explicit = max(1.0, when.timestamp() - time.time())
                except (ValueError, TypeError):
                    pass
        with guard:
            now = time.monotonic()
            if explicit is not None:
                delay = explicit
            elif now < throttle["until"]:
                delay = throttle["until"] - now
            else:
                throttle["streak"] = 1 if now - throttle["last_limit"] > 120 else throttle["streak"] + 1
                delay = min(60, 2 ** min(6, throttle["streak"]))
            throttle["last_limit"] = now
            throttle["until"] = max(throttle["until"], now + delay)
        client.cooldown(delay)
        return delay

    def do_request(job, offset, page_size):
        params = {**job["params"], "limit": page_size, "offset": offset}
        for attempt in range(4):
            if stop_path.exists():
                raise RetryJob("User stop request")
            if batch_throttled.is_set():
                raise RetryJob("Batch backoff: defer unsent work")
            record = {"at_utc": NOW(), "run_id": run_id, "job_id": job["id"], "api": job["api"], "offset": offset, "limit": page_size, "attempt": attempt + 1}
            try:
                obj, m = client.call(job["api"], params, job["fields"], cancel_event=batch_throttled)
                record.update(m)
                record["code"] = obj.get("code")
                record["message"] = obj.get("msg")
                if obj.get("code") != 0:
                    message = str(obj.get("msg") or "API error")
                    if re.search(r"每分钟|频次|频率|限流|too many|rate.limit|每秒|上限", message, re.I):
                        if re.search(r"每天|每日|daily|到期|expired", message, re.I):
                            raise DataQualityError("Server daily allowance or expiry requires review: " + message)
                        retry_header = m.get('rate_limit_header')
                        match = re.search(r"(\d+)\s*秒", message)
                        if match:
                            retry_header = int(match.group(1))
                        retry = server_backoff(retry_header)
                        batch_throttled.set()
                        record["rate_limited"] = True
                        raise RetryJob(f"Server rate limit; cooling down {retry}s: {message}")
                    raise DataQualityError(f"API {obj.get('code')}: {message}")
                record["rows"] = len((obj.get("data") or {}).get("items") or [])
                record["ok"] = True
                with guard:
                    counters["successful_requests"] += 1
                    counters["wire_bytes"] += m["wire_bytes"]
                    counters["decoded_bytes"] += m["decoded_bytes"]
                    counters["response_rows"] += record["rows"]
                return obj, record
            except ApiHTTPError as exc:
                record.update(ok=False, error=str(exc).replace(token, "[REDACTED]"), http_status=exc.status)
                if exc.status in (429, 503):
                    retry = server_backoff(exc.retry_after)
                    batch_throttled.set()
                    record["rate_limited"] = True
                    raise RetryJob(f"Server HTTP {exc.status}; cooling down {retry}s")
                if exc.status in (401, 403):
                    raise DataQualityError("Authentication or permission rejected")
                if attempt == 3:
                    raise RetryJob(str(exc))
                time.sleep(2 ** attempt)
            except CallCancelled as exc:
                record.update(ok=False, deferred_without_network=True, error=str(exc))
                raise RetryJob("Batch backoff: defer unsent work")
            except (RetryJob, DataQualityError) as exc:
                record.update(ok=False, error=str(exc).replace(token, "[REDACTED]"))
                raise
            except Exception as exc:
                record.update(ok=False, error=f"{type(exc).__name__}: {exc}".replace(token, "[REDACTED]"))
                if attempt == 3:
                    raise RetryJob(record["error"])
                time.sleep(2 ** attempt)
            finally:
                with guard:
                    if not record.get("deferred_without_network"):
                        counters["request_attempts"] += 1
                    if not record.get("ok") and not record.get("deferred_without_network"):
                        counters["request_errors"] += 1
                    if record.get("rate_limited"):
                        counters["rate_limits"] += 1
                    with request_log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        raise RetryJob("Retry loop exhausted")

    def extract_unpaged_job(job, folder, begin):
        names = job['fields']
        def request_interval(params):
            obj, _ = do_request({**job, 'params': params}, 0, 1000)
            return obj
        try:
            rows, artifacts, recovery = recover(job['api'], job['params'], names,
                                                request_interval, folder, job['id'])
        except RecoveryError as exc:
            raise DataQualityError(str(exc)) from exc
        schema = schema_for(names, catalog[job['api']]['fields'])
        table = make_table(names, rows, schema).replace_schema_metadata({
            b'job_id': job['id'].encode(), b'api': job['api'].encode(),
            b'params': canonical(job['params']).encode(), b'source': b'https://tuaremax.top',
            b'extraction_mode': recovery['mode'].encode(), b'retrieved_at': NOW().encode()})
        temp = folder / 'data.parquet.tmp'
        pq.write_table(table, temp, compression='zstd', use_dictionary=True)
        path = folder / 'data.parquet'
        os.replace(temp, path)
        if pq.ParquetFile(path).metadata.num_rows != len(rows):
            raise DataQualityError('Written Parquet row count mismatch')
        raw_bytes = sum(a['bytes'] for a in artifacts)
        blob = path.read_bytes()
        artifacts.append({'file': 'data.parquet', 'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()})
        result = {'job_id': job['id'], 'api': job['api'], 'params': job['params'],
                  'rows': len(rows), 'data_pages': sum(bool(x['rows']) for x in recovery['leaves']),
                  'terminal_empty_page': False, 'field_count': len(names),
                  'raw_bytes': raw_bytes, 'parquet_bytes': len(blob),
                  'elapsed_seconds': time.monotonic() - begin, 'finished_at_utc': NOW(),
                  'artifacts': artifacts, 'recovery': recovery,
                  'data_status': 'downloaded_nonempty' if rows else 'empty_response_not_proof_of_no_history'}
        for column in ('ann_date', 'start_date', 'end_date'):
            if column in names:
                vals = [str(row[names.index(column)]) for row in rows if row[names.index(column)] not in (None, '')]
                result[column + '_range'] = [min(vals), max(vals)] if vals else []
        atomic_json(folder / 'complete.json', result)
        return result

    def extract_job(job):
        begin = time.monotonic()
        folder = DATA / job["api"] / job["id"][:2] / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
        complete = folder / "complete.json"
        if complete.exists():
            saved = json.loads(complete.read_text(encoding="utf-8"))
            if saved.get("job_id") != job["id"]:
                raise DataQualityError("Cached job identity mismatch")
            for artifact in saved["artifacts"]:
                p = folder / artifact["file"]
                if not p.is_file() or p.stat().st_size != artifact["bytes"] or hashlib.sha256(p.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise DataQualityError("Cached artifact checksum mismatch")
            return {**saved, "elapsed_seconds": time.monotonic() - begin, "reused_complete": True}
        if job['api'] in ('namechange', 'fina_mainbz'):
            return extract_unpaged_job(job, folder, begin)
        names = job["fields"]
        schema = schema_for(names, catalog[job["api"]]["fields"])
        size = 6000 if job["api"] in ("daily", "daily_basic", "adj_factor") else 1000
        if job["api"] in ("fina_indicator", "fina_mainbz"):
            size = 100
        offset, pages, raw_bytes = 0, 0, 0
        all_rows, artifacts, page_signatures = [], [], set()
        for page_no in range(2000):
            filename = f"page_{page_no:04d}.json.gz"
            raw_path = folder / filename
            if raw_path.exists():
                envelope = json.loads(gzip.decompress(raw_path.read_bytes()))
                if envelope["job_id"] != job["id"] or envelope["offset"] != offset:
                    raise DataQualityError("Cached pagination position mismatch")
                obj = envelope["response"]
            else:
                obj, m = do_request(job, offset, size)
                envelope = {"job_id": job["id"], "api": job["api"], "params": job["params"], "requested_fields": names,
                            "offset": offset, "limit": size, "downloaded_at_utc": NOW(), "response": obj}
                packed = gzip.compress(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=1, mtime=0)
                temp = raw_path.with_suffix(".tmp")
                temp.write_bytes(packed)
                os.replace(temp, raw_path)
            packed = raw_path.read_bytes()
            artifacts.append({"file": filename, "bytes": len(packed), "sha256": hashlib.sha256(packed).hexdigest()})
            raw_bytes += len(packed)
            data = obj.get("data") or {}
            got_names, items = data.get("fields") or [], data.get("items") or []
            if obj.get("code") != 0 or set(names) - set(got_names):
                raise DataQualityError("Response failed or omitted requested columns")
            if any(len(row) != len(got_names) for row in items):
                raise DataQualityError("Column count mismatch")
            indices = [got_names.index(n) for n in names]
            rows = [[row[i] for i in indices] for row in items]
            if rows:
                sig = hashlib.sha256(canonical(rows).encode()).hexdigest()
                if sig in page_signatures:
                    raise DataQualityError("Repeated page: endpoint ignored offset or returned a pagination loop")
                page_signatures.add(sig)
                if "ts_code" in names and job["params"].get("ts_code"):
                    if any(row[names.index("ts_code")] != job["params"]["ts_code"] for row in rows):
                        raise DataQualityError("Endpoint ignored the requested stock code")
                if "trade_date" in names and job["params"].get("trade_date"):
                    if any(row[names.index("trade_date")] != job["params"]["trade_date"] for row in rows):
                        raise DataQualityError("Endpoint ignored the requested trade date")
                if job['api']=='disclosure_date' and 'end_date' in names:
                    if any(str(row[names.index('end_date')]) != job['params']['end_date'] for row in rows):
                        raise DataQualityError('Disclosure endpoint ignored the exact reporting period')
                all_rows.extend(rows)
                offset += len(rows)
                pages += 1
            else:
                break
        else:
            raise DataQualityError("Pagination runaway guard reached")
        table = make_table(names, all_rows, schema)
        metadata = {b"job_id": job["id"].encode(), b"api": job["api"].encode(), b"params": canonical(job["params"]).encode(),
                    b"source": b"https://tuaremax.top", b"retrieved_at": NOW().encode()}
        table = table.replace_schema_metadata(metadata)
        temp = folder / "data.parquet.tmp"
        pq.write_table(table, temp, compression="zstd", use_dictionary=True)
        pq_path = folder / "data.parquet"
        os.replace(temp, pq_path)
        if pq.ParquetFile(pq_path).metadata.num_rows != len(all_rows):
            raise DataQualityError("Written Parquet row count mismatch")
        b = pq_path.read_bytes()
        artifacts.append({"file": "data.parquet", "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()})
        result = {"job_id": job["id"], "api": job["api"], "params": job["params"], "rows": len(all_rows), "data_pages": pages,
                  "terminal_empty_page": True, "field_count": len(names), "raw_bytes": raw_bytes, "parquet_bytes": len(b),
                  "elapsed_seconds": time.monotonic() - begin, "finished_at_utc": NOW(), "artifacts": artifacts,
                  "data_status": "downloaded_nonempty" if all_rows else "empty_response_not_proof_of_no_history"}
        for column in ("ann_date", "f_ann_date", "end_date", "trade_date", "report_date"):
            if column in names:
                values = [str(row[names.index(column)]) for row in all_rows if row[names.index(column)] not in (None, "")]
                result[column + "_range"] = [min(values), max(values)] if values else []
        atomic_json(complete, result)
        return result

    def counts():
        return dict(db.execute("SELECT status,COUNT(*) FROM jobs WHERE status!='superseded' GROUP BY status").fetchall())

    def write_status(phase, workers, extra=None):
        state = {"at_utc": NOW(), "pid": os.getpid(), "phase": phase, "workers": workers, "fixed_rps_limit": None,
                 "total_jobs": total_jobs, "jobs": counts(), "runtime_seconds": time.monotonic() - started,
                 "current_run": metrics(), "free_GB": shutil.disk_usage("F:\\").free / 1e9,
                 "dataset": str(DATA), "manifest": str(CONTROL / "manifest.sqlite"), **(extra or {})}
        atomic_json(CONTROL / "status.json", state)
        return state

    def get_jobs(n, calibration=False):
        if calibration:
            rows = db.execute("SELECT id,api,params,fields FROM jobs WHERE status='pending' AND priority=10 ORDER BY id LIMIT ?", (n,)).fetchall()
        else:
            rows = db.execute("SELECT id,api,params,fields FROM jobs INDEXED BY job_dispatch WHERE status IN ('pending','retry') AND attempts<8 ORDER BY priority,attempts,id LIMIT ?", (n,)).fetchall()
        for r in rows:
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (r[0],))
        db.commit()
        return [{"id": r[0], "api": r[1], "params": json.loads(r[2]), "fields": json.loads(r[3])} for r in rows]

    def run_batch(workers, jobs, label):
        if not jobs:
            return None
        before, began = metrics(), time.monotonic()
        batch_throttled.clear()
        results, errors = [], []
        write_status(label, workers)
        last_status = time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(extract_job, job): job for job in jobs}
            while futures:
                done, _ = cf.wait(futures, timeout=2, return_when=cf.FIRST_COMPLETED)
                for future in done:
                    job = futures.pop(future)
                    try:
                        r = future.result()
                        results.append(r)
                        db.execute("UPDATE jobs SET status='complete',rows=?,pages=?,raw_bytes=?,parquet_bytes=?,elapsed=?,finished_at=?,error=NULL WHERE id=?",
                                   (r["rows"], r["data_pages"], r["raw_bytes"], r["parquet_bytes"], r["elapsed_seconds"], r["finished_at_utc"], job["id"]))
                    except Exception as exc:
                        message = f"{type(exc).__name__}: {exc}".replace(token, "[REDACTED]")
                        status = "retry" if isinstance(exc, RetryJob) else "needs_review"
                        if 'Batch backoff:' in message:
                            db.execute("UPDATE jobs SET status='pending',attempts=MAX(0,attempts-1),error=? WHERE id=?",(message,job['id']))
                        else:
                            db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status, message, job["id"]))
                        errors.append({"id": job["id"], "api": job["api"], "status": status, "error": message})
                db.commit()
                if time.monotonic() - last_status >= 10:
                    write_status(label, workers, {"batch_inflight": len(futures), "batch_complete": len(results)})
                    last_status = time.monotonic()
        elapsed, after = time.monotonic() - began, metrics()
        delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after)}
        summary = {"label": label, "workers": workers, "jobs_attempted": len(jobs), "jobs_complete": len(results), "job_errors": len(errors),
                   "seconds": elapsed, "jobs_per_second": len(results) / elapsed,
                   "successful_requests_per_second": delta.get("successful_requests", 0) / elapsed,
                   "wire_MB_per_second": delta.get("wire_bytes", 0) / 1e6 / elapsed,
                   "decoded_MB_per_second": delta.get("decoded_bytes", 0) / 1e6 / elapsed,
                   "row_count": sum(r["rows"] for r in results), "delta": delta, "errors": errors[:12]}
        emit({"event": "batch", **summary})
        write_status(label, workers, {"last_batch": summary})
        return summary

    emit({"event": "started", "pid": os.getpid(), "jobs": total_jobs, "fixed_rps_limit": None, "endpoint_ips": client.ips})
    if args.skip_calibration and (CONTROL / "calibration.json").exists():
        calibration = json.loads((CONTROL / "calibration.json").read_text(encoding="utf-8"))
        workers = calibration["selected_workers"]
        if (CONTROL / 'runtime_workers.json').exists():
            workers = json.loads((CONTROL / 'runtime_workers.json').read_text(encoding='utf-8'))['workers']
    else:
        for workers in (4, 8, 16, 32, 64):
            jobs = get_jobs(max(48, workers * 3), calibration=True)
            result = run_batch(workers, jobs, f"uncapped_calibration_{workers}")
            if result is None:
                break
            trials.append(result)
            atomic_json(CONTROL / "calibration_trials.json", trials)
            if result["delta"].get("rate_limits", 0) or result["delta"].get("request_errors", 0) > 4:
                break
            if len(trials) >= 3 and result["jobs_per_second"] < max(t["jobs_per_second"] for t in trials[:-1]) * 1.03:
                break
        good = [t for t in trials if not t["job_errors"] and not t["delta"].get("rate_limits", 0)]
        best = max(good, key=lambda t: t["jobs_per_second"]) if good else None
        workers = best["workers"] if best else 2
        atomic_json(CONTROL / "calibration.json", {"at_utc": NOW(), "selected_workers": workers, "fixed_rps_limit": None, "trials": trials,
                                                  "note": "Observed throughput on useful financial partitions; not a claim of provider maximum capacity."})
    if args.calibrate_only:
        write_status("CALIBRATION_COMPLETE", workers)
        emit({"event": "calibration_complete", "selected_workers": workers, "jobs": counts()})
        return
    if not db.execute("SELECT value FROM metadata WHERE key='interface_preflight_v1'").fetchone():
        selected = []
        for (api,) in db.execute("SELECT DISTINCT api FROM jobs WHERE status='pending'").fetchall():
            code = {'repurchase':'000333.SZ','share_float':'300750.SZ','stk_holdertrade':'002594.SZ','pledge_stat':'002475.SZ'}.get(api,'600519.SH')
            pattern = '%"ts_code":"' + code + '"%'
            row = db.execute("SELECT id,api,params,fields FROM jobs WHERE api=? AND status='pending' AND params LIKE ? LIMIT 1", (api,pattern)).fetchone()
            if row is None:
                row = db.execute("SELECT id,api,params,fields FROM jobs WHERE api=? AND status='pending' ORDER BY params DESC LIMIT 1", (api,)).fetchone()
            if row:
                selected.append({'id':row[0],'api':row[1],'params':json.loads(row[2]),'fields':json.loads(row[3])})
                db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?",(row[0],))
        db.commit()
        pilot = run_batch(min(workers,8),selected,'all_interface_pagination_preflight')
        if pilot and pilot['job_errors']:
            write_status('STOPPED_INTERFACE_PREFLIGHT_REQUIRES_REVIEW',workers,{'preflight':pilot})
            return
        db.execute("INSERT INTO metadata VALUES('interface_preflight_v1',?)",(NOW(),))
        db.commit()
    best_workers, best_rate = workers, 0.0
    clean_batches, batch_number = 0, 0
    while not stop_path.exists():
        if shutil.disk_usage("F:\\").free < 30e9:
            write_status("STOPPED_LOW_DISK", workers)
            return
        jobs = get_jobs(max(128, workers * 8))
        if not jobs:
            break
        batch_number += 1
        result = run_batch(workers, jobs, f"bulk_{batch_number}")
        rate_limits = result["delta"].get("rate_limits", 0)
        if rate_limits:
            workers = max(1, workers // 2)
            best_workers, best_rate, clean_batches = workers, 0.0, 0
        elif result["delta"].get("request_errors", 0) > max(4, len(jobs) * .05):
            workers = max(1, workers // 2)
            clean_batches = 0
        else:
            rate = result["successful_requests_per_second"]
            if rate > best_rate:
                best_workers, best_rate = workers, rate
            elif best_rate and rate < best_rate * .82 and workers > best_workers:
                workers = best_workers
            clean_batches += 1
            # Probe a higher socket count periodically. The ceiling is a local
            # resource guard, not an account request-rate limit.
            if clean_batches % 4 == 0 and workers < 64:
                workers = min(64, workers + max(1, workers // 4))
        atomic_json(CONTROL/'runtime_workers.json',{'at_utc':NOW(),'workers':workers,'fixed_rps_limit':None})
        if result["job_errors"] == len(jobs) and not rate_limits and result['delta'].get('request_errors', 0):
            write_status("STOPPED_REPEATED_ERRORS", workers)
            return
    final_counts = counts()
    if stop_path.exists():
        phase = "STOPPED_BY_REQUEST"
    elif any(final_counts.get(s, 0) for s in ("pending", "retry", "running", "needs_review")):
        phase = "DOWNLOAD_FINISHED_WITH_REVIEW_REQUIRED"
    else:
        phase = "DOWNLOAD_COMPLETE_COVERAGE_AUDIT_REQUIRED"
    write_status(phase, workers)
    emit({"event": "finished", "phase": phase, "jobs": final_counts})
    db.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        atomic_json(CONTROL / "fatal_error.json", {"at_utc": NOW(), "type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()})
        raise
