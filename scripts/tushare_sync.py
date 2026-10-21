#!/usr/bin/env python3
"""Local TuShare-compatible raw-data archive. Python 3.9+, standard library only.

No model training, portfolio changes or trading. `plan` is offline; only `smoke`
and `sync --execute` send credentials to the user-specified HTTPS endpoint.
"""
import argparse
import calendar
import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://tuaremax.top"
INDEX_CODES = ("000300.SH", "000905.SH", "000852.SH")
KEYS = {
    "daily": ("ts_code", "trade_date"),
    "daily_basic": ("ts_code", "trade_date"),
    "adj_factor": ("ts_code", "trade_date"),
    "stk_limit": ("ts_code", "trade_date"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "trade_cal": ("exchange", "cal_date"),
    "stock_basic": ("ts_code",),
    "index_daily": ("ts_code", "trade_date"),
    "index_weight": ("index_code", "con_code", "trade_date"),
    "stk_mins": ("ts_code", "trade_time"),
    "namechange": ("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"),
    "dividend": ("ts_code", "end_date", "ann_date", "div_proc"),
}
DAILY_APIS = ("daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d")
API_FIELDS = {
    "stock_basic": "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs",
    "stk_limit": "trade_date,ts_code,pre_close,up_limit,down_limit",
}


class DataError(RuntimeError):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_bytes(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as fh:
        fh.write(value)
        fh.flush()
        os.fsync(fh.fileno())
    temp.replace(path)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def read_credentials(path):
    values = {}
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise DataError("Credential file must have permissions 600")
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip("\"'")
    token = os.environ.get("TUSHARE_TOKEN") or values.get("TUSHARE_TOKEN")
    url = os.environ.get("TUSHARE_HTTP_URL") or values.get("TUSHARE_HTTP_URL", ENDPOINT)
    if not token:
        raise DataError("TUSHARE_TOKEN is missing (use private .env.tushare or environment)")
    if url.rstrip("/") != ENDPOINT:
        raise DataError("This prepared client only sends credentials to https://tuaremax.top")
    return token, ENDPOINT


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward this credential to a redirect destination.


class Client:
    def __init__(self, token, url=ENDPOINT, interval=1.0, timeout=25, max_requests=None):
        if url != ENDPOINT:
            raise DataError("Unapproved credential destination")
        self.token, self.url = token, url
        self.interval, self.timeout = interval, timeout
        self.max_requests, self.requests, self.last = max_requests, 0, 0.0
        self.opener = urllib.request.build_opener(
            NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def query(self, api, params):
        if api not in KEYS:
            raise DataError("API is outside the prepared read-only catalog")
        body = canonical({"api_name": api, "token": self.token,
                          "params": params, "fields": API_FIELDS.get(api, "")}).encode()
        for attempt in range(3):
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise DataError("Request budget reached; rerun the same command to resume")
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            self.requests += 1
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "User-Agent": "python-requests/2.32.3"})
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    raw = response.read(64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024:
                    raise DataError("Response exceeds 64 MiB; use smaller partitions")
                payload = json.loads(raw)
                if payload.get("code") != 0:
                    msg = str(payload.get("msg", "")).replace(self.token, "[REDACTED]")
                    raise DataError("API %s: code=%s %s" % (api, payload.get("code"), msg[:300]))
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise DataError("Missing API data object")
                fields, items = data.get("fields"), data.get("items")
                if not isinstance(fields, list) or not isinstance(items, list):
                    raise DataError("Malformed API table")
                if not all(isinstance(f, str) for f in fields) or len(fields) != len(set(fields)):
                    raise DataError("Invalid or duplicate field names")
                if any(not isinstance(row, list) or len(row) != len(fields) for row in items):
                    raise DataError("Row width does not match fields")
                if items and not set(KEYS[api]).issubset(fields):
                    raise DataError("API result lacks expected primary-key fields")
                return fields, items
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise DataError("HTTP %s from configured provider" % exc.code) from None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, ssl.SSLError):
                    raise DataError("TLS validation failed; request stopped") from None
                if attempt == 2:
                    raise DataError("Network request failed; partition is not complete") from None
            except (TimeoutError, OSError):
                if attempt == 2:
                    raise DataError("Network timeout; partition is not complete") from None
            except (ValueError, AttributeError):
                raise DataError("Provider returned invalid JSON/schema") from None
            time.sleep(2 ** (attempt + 1))

    def fetch_all(self, api, params, page_size=6000):
        columns, rows, seen = None, [], set()
        for page in range(200):
            fields, items = self.query(api, dict(params, limit=page_size, offset=len(rows)))
            if columns is None:
                columns = fields
            if not items:
                return columns, rows
            if fields != columns:
                raise DataError("Schema changed during pagination")
            key_indices = [fields.index(k) for k in KEYS[api]]
            for row in items:
                key = tuple(row[i] for i in key_indices)
                if key in seen:
                    raise DataError("Duplicate key/repeated page: provider may ignore offset")
                seen.add(key)
                rows.append(row)
            # A short page can reflect a provider-specific cap. Confirm with an empty page.
        raise DataError("Pagination limit reached; partition was not saved")


def validate_scope(api, params, fields, rows):
    for row in rows:
        rec = dict(zip(fields, row))
        for key in ("ts_code", "index_code", "trade_date", "exchange", "list_status"):
            if key in params and key in rec and str(rec[key]) != str(params[key]):
                raise DataError("Provider returned rows outside requested %s" % key)
        date_key = "trade_time" if api == "stk_mins" else ("cal_date" if api == "trade_cal" else "trade_date")
        value = rec.get(date_key)
        if value is not None:
            if params.get("start_date") and str(value) < params["start_date"]:
                raise DataError("Provider returned rows before requested range")
            if params.get("end_date") and str(value) > params["end_date"]:
                raise DataError("Provider returned rows after requested range")


class Archive:
    def __init__(self, root):
        self.root = root

    def paths(self, api, params):
        identity = {"api": api, "params": params, "source": ENDPOINT, "version": 1}
        stem = hashlib.sha256(canonical(identity).encode()).hexdigest()[:24]
        base = self.root / "raw" / api / stem
        return identity, base.with_suffix(".csv.gz"), base.with_suffix(".json")

    def partition(self, client, api, params, allow_empty=False):
        identity, path, receipt_path = self.paths(api, params)
        if receipt_path.exists() and path.exists():
            rec = json.loads(receipt_path.read_text())
            if rec.get("identity") == identity and hashlib.sha256(path.read_bytes()).hexdigest() == rec.get("sha256"):
                if rec["rows"] == 0 and not allow_empty:
                    raise DataError("Empty required partition in cache")
                return rec, path
        fields, rows = client.fetch_all(api, params)
        validate_scope(api, params, fields, rows)
        if not rows and not allow_empty:
            raise DataError("Required %s partition is empty; not marked complete" % api)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)
        blob = gzip.compress(stream.getvalue().encode(), mtime=0)
        rec = {"identity": identity, "retrieved_at": now(), "rows": len(rows), "fields": fields,
               "status": "downloaded" if rows else "empty_response",
               "sha256": hashlib.sha256(blob).hexdigest(),
               "validation": getattr(type(client), "validation_method", "schema, primary key, query scope, pagination; not independently reconciled")}
        atomic_bytes(path, blob)
        atomic_json(receipt_path, rec)
        return rec, path


def date_arg(value):
    try:
        return dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("Use YYYYMMDD")


def month_ranges(start, end):
    current = start
    while current <= end:
        last = min(end, current.replace(day=calendar.monthrange(current.year, current.month)[1]))
        yield current.strftime("%Y%m%d"), last.strftime("%Y%m%d")
        current = last + dt.timedelta(days=1)


def prepare_plan(start, end):
    weekdays = sum((start + dt.timedelta(days=i)).weekday() < 5 for i in range((end-start).days+1))
    return {"created_at": now(), "start": str(start), "end": str(end), "source": ENDPOINT,
            "universe": "all A (including delisted stocks), not today's index members only",
            "priority_0": ["stock_basic L/D/P", "trade_cal SSE"],
            "priority_1": list(DAILY_APIS),
            "priority_2": ["index_daily + index_weight: " + ", ".join(INDEX_CODES)],
            "estimated_partitions_upper_bound": 4 + 5*weekdays + 3*len(list(month_ranges(start, end))) + 3*(end.year-start.year+1),
            "request_policy": "serial, >=1 second interval; pagination until empty; retries up to 3",
            "storage": "raw CSV.gz + per-partition JSON receipt and SHA256; resumable",
            "not_yet_covered": ["historical ST/name changes for every stock", "corporate actions and cash dividends",
                                "verified index change effective dates (monthly weights are insufficient)",
                                "parquet adapter and vendor-data parity", "models and external factor modules"],
            "minute_policy": "smoke-test 60min only; bulk minute data needs an explicit symbol list and time range",
            "expiry": "three-day access as reported by user; exact activation/expiry is unknown"}


def baseline_jobs(start, end, dates):
    for date in dates:
        for api in DAILY_APIS:
            yield api, {"trade_date": date}, api == "suspend_d"
    for code in INDEX_CODES:
        for year in range(start.year, end.year+1):
            a, b = max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31))
            yield "index_daily", {"ts_code": code, "start_date": a.strftime("%Y%m%d"), "end_date": b.strftime("%Y%m%d")}, False
        for a, b in month_ranges(start, end):
            yield "index_weight", {"index_code": code, "start_date": a, "end_date": b}, True


def smoke(client, archive):
    month = {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20240131"}
    jobs = [("daily", month, False), ("daily_basic", month, False), ("adj_factor", month, False),
            ("stock_basic", {"ts_code": "000001.SZ", "list_status": "L"}, False),
            ("trade_cal", {"exchange": "SSE", "start_date": "20240101", "end_date": "20240131"}, False),
            ("index_daily", dict(month, ts_code="000905.SH"), False),
            ("index_weight", {"index_code": "000905.SH", "start_date": "20240101", "end_date": "20240131"}, False),
            ("stk_limit", {"ts_code": "000001.SZ", "trade_date": "20240131"}, False),
            ("suspend_d", {"ts_code": "000001.SZ", "trade_date": "20240131"}, True),
            ("namechange", {"ts_code": "000001.SZ"}, False),
            ("dividend", {"ts_code": "000001.SZ", "end_date": "20231231"}, True),
            ("stk_mins", {"ts_code": "000001.SZ", "freq": "60min", "start_date": "2024-01-31 09:00:00", "end_date": "2024-01-31 15:30:00"}, False)]
    report = {"checked_at": now(), "source": ENDPOINT, "results": []}
    for api, params, empty in jobs:
        try:
            rec, path = archive.partition(client, api, params, empty)
            result = {"api": api, "status": rec["status"], "rows": rec["rows"], "file": str(path.relative_to(ROOT))}
        except DataError as exc:
            result = {"api": api, "status": "failed", "error": str(exc)}
        report["results"].append(result)
        atomic_json(archive.root / "smoke_report.json", report)
        print(canonical(result), flush=True)
    return int(any(r["status"] == "failed" for r in report["results"]))


def sync(client, archive, start, end):
    # Metadata snapshots include retrieval date so a later run refreshes listings.
    metadata = Archive(archive.root / "metadata" / dt.date.today().isoformat())
    for state in ("L", "D", "P"):
        metadata.partition(client, "stock_basic", {"list_status": state}, allow_empty=state == "P")
    _, path = metadata.partition(client, "trade_cal", {
        "exchange": "SSE", "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")})
    with gzip.open(path, "rt", newline="") as fh:
        calendar_rows = list(csv.DictReader(fh))
    expected = {(start + dt.timedelta(days=i)).strftime("%Y%m%d") for i in range((end-start).days+1)}
    if {r["cal_date"] for r in calendar_rows} != expected:
        raise DataError("Incomplete calendar; refusing to infer missing trading dates")
    dates = sorted(r["cal_date"] for r in calendar_rows if r["is_open"] == "1")
    completed, empty_count = 0, 0
    for api, params, empty in baseline_jobs(start, end, dates):
        rec, _ = archive.partition(client, api, params, empty)
        completed += 1
        empty_count += rec["rows"] == 0
        print(canonical({"api": api, "params": params, "rows": rec["rows"], "partitions": completed}), flush=True)
    atomic_json(archive.root / "last_sync.json", {"finished_at": now(), "start": str(start), "end": str(end),
                "partitions": completed, "empty_partitions": empty_count,
                "status": "raw_download_finished", "baseline_ready": False})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "smoke", "sync"))
    parser.add_argument("--start", type=date_arg, default=dt.date(2015, 1, 1))
    yesterday = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() - dt.timedelta(days=1)
    parser.add_argument("--end", type=date_arg, default=yesterday)
    parser.add_argument("--execute", action="store_true", help="explicitly execute bulk sync")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-requests", type=int, default=None)
    args = parser.parse_args(argv)
    if args.start > args.end or args.end > yesterday:
        parser.error("Require start <= end <= yesterday in Asia/Shanghai")
    if args.interval < 1 or (args.max_requests is not None and args.max_requests < 1):
        parser.error("interval must be >=1 and max-requests must be positive")
    root = ROOT / "data" / "tushare"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError("Another local data preparation process is running") from None
        if args.command == "plan" or (args.command == "sync" and not args.execute):
            plan = prepare_plan(args.start, args.end)
            atomic_json(root / "download_plan.json", plan)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        token, url = read_credentials(ROOT / ".env.tushare")
        client = Client(token, url, interval=args.interval, max_requests=args.max_requests)
        archive = Archive(root / "smoke" if args.command == "smoke" else root)
        if args.command == "smoke":
            return smoke(client, archive)
        return sync(client, archive, args.start, args.end)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DataError as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("Interrupted; completed partitions remain resumable.", file=sys.stderr)
        sys.exit(130)
