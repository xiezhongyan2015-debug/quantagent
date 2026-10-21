#!/usr/bin/env python3
"""Bounded, resumable background acquisition using the approved TuShare relay.

No trading, model training, data replacement, or messages to teammates.
Progress is saved to SQLite, status.json, and SYNC_STATUS.md after each partition.
"""
import argparse
import collections
import csv
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import time

from tushare_sync import (Archive, Client, DataError, INDEX_CODES, KEYS, ROOT, atomic_json,
                          atomic_bytes, canonical, date_arg, month_ranges, now,
                          read_credentials, validate_scope)

STOP = False


def request_stop(*_):
    global STOP
    STOP = True


def job(api, params, phase, empty=False):
    return {"api": api, "params": params, "phase": phase, "empty": empty}


def bootstrap_jobs(start, end):
    for year in range(end.year, start.year - 1, -1):
        a, b = max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31))
        yield job("trade_cal", {"exchange": "SSE", "start_date": a.strftime("%Y%m%d"),
                               "end_date": b.strftime("%Y%m%d")}, "bootstrap")
    for state in ("L", "D", "P"):
        for exchange in ("SSE", "SZSE", "BSE"):
            yield job("stock_basic", {"list_status": state, "exchange": exchange}, "bootstrap", state != "L")


class BoundedClient(Client):
    """The relay ignores limit/offset. Use bounded natural queries, never repeated pages.

    A non-saturated response is an archive of the provider result, not proof of
    economic completeness. Later cross-table coverage checks remain necessary.
    """
    validation_method = "natural date/exchange/symbol partition, unique keys, query scope, conservative row-cap guard; provider coverage not independently reconciled"
    CAPS = {"daily": 6000, "daily_basic": 6000, "adj_factor": 6000, "stk_limit": 5800,
            "stock_basic": 6000, "trade_cal": 6000, "index_daily": 6000, "index_weight": 4000,
            "suspend_d": 5000, "namechange": 1000, "dividend": 2000, "stk_mins": 8000}

    def fetch_all(self, api, params, page_size=None):
        if api == "stock_basic" and params.get("exchange") not in ("SSE", "SZSE", "BSE"):
            raise DataError("Stock list must be partitioned by exchange")
        fields, rows = self.query(api, params)
        validate_scope(api, params, fields, rows)
        key_indices = [fields.index(k) for k in KEYS[api]] if rows else []
        keys = [tuple(row[i] for i in key_indices) for row in rows]
        if len(set(keys)) != len(keys):
            raise DataError("Duplicate primary keys in bounded response")
        if api == "trade_cal":
            start, end = (dt.datetime.strptime(params[k], "%Y%m%d").date() for k in ("start_date", "end_date"))
            expected = {(start+dt.timedelta(days=i)).strftime("%Y%m%d") for i in range((end-start).days+1)}
            actual = {row[fields.index("cal_date")] for row in rows} if rows else set()
            if actual != expected:
                raise DataError("Incomplete calendar partition")
        if len(rows) >= self.CAPS[api]:
            # Split long date queries until each result stays below the row cap.
            if api in ("index_weight", "index_daily") and params.get("start_date") < params.get("end_date"):
                start, end = (dt.datetime.strptime(params[k], "%Y%m%d").date() for k in ("start_date", "end_date"))
                middle = start + (end-start)//2
                f1, r1 = self.fetch_all(api, dict(params, end_date=middle.strftime("%Y%m%d")))
                f2, r2 = self.fetch_all(api, dict(params, start_date=(middle+dt.timedelta(days=1)).strftime("%Y%m%d")))
                if f1 != f2:
                    raise DataError("Schema changed across date subpartitions")
                return f1, r1+r2
            raise DataError("Response reached row cap; completeness unknown, partition not saved")
        return fields, rows


def research_jobs(start, end, dates, stocks, minute_stocks):
    """Current gaps first; preserve old sources for reconciliation, not silent substitution."""
    recent_cutoff = "20260612"  # Existing broad local market archive stops on 2026-06-11.
    recent = sorted((d for d in dates if d >= recent_cutoff), reverse=True)
    older = sorted((d for d in dates if d < recent_cutoff), reverse=True)
    for date in recent:
        for api in ("daily_basic", "adj_factor", "daily", "stk_limit"):
            yield job(api, {"trade_date": date}, "recent_daily")
    for code in INDEX_CODES:
        for year in range(end.year, start.year - 1, -1):
            a, b = max(start, dt.date(year, 1, 1)), min(end, dt.date(year, 12, 31))
            yield job("index_daily", {"ts_code": code, "start_date": a.strftime("%Y%m%d"), "end_date": b.strftime("%Y%m%d")}, "indices")
        for a, b in reversed(list(month_ranges(start, end))):
            yield job("index_weight", {"index_code": code, "start_date": a, "end_date": b}, "indices", True)
    for date in older:
        for api in ("daily_basic", "adj_factor", "daily"):
            yield job(api, {"trade_date": date}, "historical_daily")
    for date in sorted(dates, reverse=True):
        yield job("suspend_d", {"trade_date": date}, "trading_status", True)
        if date < recent_cutoff:
            yield job("stk_limit", {"trade_date": date}, "trading_status")
    for code in stocks:
        # Retain all provider history and announcement dates. Do not apply today's status to the past.
        yield job("namechange", {"ts_code": code}, "historical_reference", True)
        yield job("dividend", {"ts_code": code}, "historical_reference", True)
    # Only fill the current 500's recent gap, not all-A minute history already on disk.
    minute_start = max(start, dt.date(2026, 6, 12))
    if minute_start <= end:
        for a, b in reversed(list(month_ranges(minute_start, end))):
            for code in minute_stocks:
                yield job("stk_mins", {"ts_code": code, "freq": "60min",
                          "start_date": dt.datetime.strptime(a, "%Y%m%d").strftime("%Y-%m-%d 09:00:00"),
                          "end_date": dt.datetime.strptime(b, "%Y%m%d").strftime("%Y-%m-%d 15:30:00")}, "recent_minutes", True)


def failure_kind(message):
    text = message.lower()
    if any(s in text for s in ("http 429", "http 500", "http 502", "http 503", "http 504", "network", "timeout", "频率", "频次", "每分钟", "每小时", "每天最多", "每个小时", "每个自然日")):
        return "transient"
    if any(s in text for s in ("tls", "token", "过期", "失效", "http 401", "http 403")):
        return "credential_or_transport"
    if any(s in text for s in ("权限", "没有访问", "接口不存在", "无效的接口")):
        return "api_unavailable"
    return "data_validation"


class Runner:
    def __init__(self, root, client, start, end, max_hours):
        self.root, self.client, self.start, self.end = root, client, start, end
        self.started = time.monotonic()
        self.deadline = self.started + max_hours * 3600
        self.db = sqlite3.connect(root / "sync_queue.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, api TEXT, params TEXT, phase TEXT, allow_empty INTEGER, status TEXT DEFAULT 'pending', rows INTEGER DEFAULT 0, path TEXT, error TEXT, attempts INTEGER DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        previous = self.db.execute("SELECT value FROM meta WHERE key='range'").fetchone()
        scope = canonical([str(start), str(end)])
        if previous and previous[0] != scope:
            raise DataError("Queue uses another date range; create a separate run directory")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('range',?)", (scope,))
        self.db.commit()
        self.archive = Archive(root)
        self.current = None

    def add(self, jobs):
        for j in jobs:
            identity = canonical({"api": j["api"], "params": j["params"]})
            key = hashlib.sha256(identity.encode()).hexdigest()
            self.db.execute("INSERT OR IGNORE INTO jobs(id,api,params,phase,allow_empty) VALUES (?,?,?,?,?)",
                            (key, j["api"], canonical(j["params"]), j["phase"], j["empty"]))
        self.db.commit()

    def state(self, status, error=None, retry_seconds=None):
        counts = dict(self.db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status"))
        done_rows = self.db.execute("SELECT COALESCE(SUM(rows),0) FROM jobs WHERE status='done'").fetchone()[0]
        per_api = [dict(zip(("api", "status", "partitions", "rows"), row)) for row in self.db.execute(
            "SELECT api,status,COUNT(*),SUM(rows) FROM jobs GROUP BY api,status")]
        result = {"updated_at": now(), "pid": os.getpid(), "status": status,
                  "range": {"start": str(self.start), "end": str(self.end)},
                  "queue_counts": counts, "downloaded_rows": done_rows,
                  "requests_this_process": self.client.requests, "per_api": per_api,
                  "current": self.current, "last_error": error, "retry_in_seconds": retry_seconds,
                  "deadline_utc": dt.datetime.fromtimestamp(time.time()+max(0,self.deadline-time.monotonic()), dt.timezone.utc).isoformat(),
                  "full_queue_built": bool(self.db.execute("SELECT value FROM meta WHERE key='expanded'").fetchone()),
                  "baseline_ready": False}
        atomic_json(self.root / "status.json", result)
        title = {"running": "正在同步", "waiting_service": "服务暂不可用，等待重试", "stopped": "已停止，可断点恢复",
                 "finished": "下载队列完成", "finished_with_gaps": "队列处理结束，仍有缺口", "blocked": "遇到需要处理的问题"}.get(status, status)
        text = ("# 数据同步进度\n\n状态：**%s**\n\n更新（UTC）：%s\n\n范围：%s 至 %s\n\n"
                "已完成分区：%s；已保存记录：%s；待处理分区：%s；失败/不可用分区：%s。\n\n"
                "全量队列已生成：%s（此前仅列出基础资料任务）。\n\n" %
                (title, result["updated_at"], self.start, self.end, counts.get("done",0), done_rows,
                 counts.get("pending",0), counts.get("failed",0)+counts.get("unavailable",0), result["full_queue_built"]))
        if error:
            text += "最近问题：%s\n\n" % error
        if retry_seconds:
            text += "计划约%s秒后重试；失败请求没有标为完成。\n\n" % retry_seconds
        text += "原始数据不覆盖。每个成功分区保留来源、参数、记录数和SHA256。下载完成仍需跨表覆盖和价格口径验收，不能直接等同baseline复现完成。\n"
        atomic_bytes(self.root / "SYNC_STATUS.md", text.encode())
        return result

    def wait_retry(self, seconds, error):
        until = min(self.deadline, time.monotonic() + seconds)
        while not STOP and time.monotonic() < until:
            self.state("waiting_service", error, max(1, int(until - time.monotonic())))
            time.sleep(min(15, max(0, until-time.monotonic())))

    def process_pending(self):
        failures = 0
        while not STOP and time.monotonic() < self.deadline:
            row = self.db.execute("SELECT id,api,params,phase,allow_empty FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return True
            key, api, packed, phase, empty = row
            params = json.loads(packed)
            self.current = {"api": api, "params": params, "phase": phase}
            if shutil.disk_usage(self.root).free < 20*1024**3:
                self.state("blocked", "Less than 20 GiB free; existing data retained")
                return False
            self.state("running")
            self.db.execute("UPDATE jobs SET attempts=attempts+1 WHERE id=?", (key,)); self.db.commit()
            try:
                rec, path = self.archive.partition(self.client, api, params, bool(empty))
                self.db.execute("UPDATE jobs SET status='done',rows=?,path=?,error=NULL WHERE id=?", (rec["rows"], str(path), key))
                self.db.commit()
                failures = 0
                print(canonical({"at": now(), "api": api, "params": params, "rows": rec["rows"], "status": rec["status"]}), flush=True)
                self.state("running")
            except DataError as exc:
                error = str(exc).replace(self.client.token, "[REDACTED]")
                kind = failure_kind(error)
                self.db.execute("UPDATE jobs SET error=? WHERE id=?", (error, key)); self.db.commit()
                print(canonical({"at": now(), "api": api, "kind": kind, "error": error}), flush=True)
                if kind == "transient":
                    failures += 1
                    self.wait_retry(min(1800, 60*2**min(failures-1,5)), error)
                elif kind == "credential_or_transport" or phase == "bootstrap":
                    self.state("blocked", error)
                    return False
                elif kind == "api_unavailable":
                    self.db.execute("UPDATE jobs SET status='unavailable',error=? WHERE api=? AND status='pending'", (error, api)); self.db.commit()
                else:
                    self.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (key,)); self.db.commit()
                    # Avoid repeating an incompatible schema thousands of times.
                    count = self.db.execute("SELECT COUNT(*) FROM jobs WHERE api=? AND status='failed'", (api,)).fetchone()[0]
                    if count >= 3:
                        self.db.execute("UPDATE jobs SET status='unavailable',error=? WHERE api=? AND status='pending'", ("Repeated validation errors; inspect failed partitions", api)); self.db.commit()
        self.state("stopped", "Stopped by user or reached the 48-hour process limit; resume is available")
        return False

    def expand(self):
        dates, stocks = set(), set()
        for api, path in self.db.execute("SELECT api,path FROM jobs WHERE phase='bootstrap' AND status='done'"):
            with gzip.open(path, "rt", newline="") as fh:
                records = list(csv.DictReader(fh))
            if api == "trade_cal":
                if any(r.get("is_open") not in ("0", "1") for r in records):
                    raise DataError("Invalid trading-calendar status")
                dates.update(r["cal_date"] for r in records if r["is_open"] == "1")
            elif api == "stock_basic":
                stocks.update(r["ts_code"] for r in records)
        calendar_days = set()
        for (path,) in self.db.execute("SELECT path FROM jobs WHERE api='trade_cal' AND status='done'"):
            with gzip.open(path, "rt", newline="") as fh:
                calendar_days.update(r["cal_date"] for r in csv.DictReader(fh))
        expected = {(self.start+dt.timedelta(days=i)).strftime("%Y%m%d") for i in range((self.end-self.start).days+1)}
        if calendar_days != expected or not dates or not stocks:
            raise DataError("Incomplete calendar or stock universe; full queue not generated")
        members_path = ROOT.parent / "acquired_data/current_prediction_20260920/normalized_v3/coverage.csv"
        minute_stocks = []
        if members_path.exists():
            with members_path.open() as fh:
                symbols = [r["symbol"] for r in csv.DictReader(fh)]
            if len(symbols) != 500 or len(set(symbols)) != 500:
                raise DataError("Current minute stock list is not 500 unique symbols")
            minute_stocks = [s[2:]+"."+s[:2].upper() for s in symbols]
        self.add(research_jobs(self.start, self.end, sorted(dates), sorted(stocks), minute_stocks))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('expanded',?)", (now(),)); self.db.commit()
        atomic_json(self.root / "run_scope.json", {"start": str(self.start), "end": str(self.end),
                    "trading_dates": len(dates), "listed_delisted_suspended_stocks": len(stocks),
                    "recent_minute_stocks": len(minute_stocks), "priority": ["recent_daily", "indices", "historical_daily", "trading_status", "historical_reference", "recent_minutes"],
                    "old_data_policy": "Preserve local files; unknown-adjustment legacy prices are not treated as authoritative raw prices",
                    "monthly_weight_warning": "Monthly weights are snapshots, not proven historical effective-date membership"})

    def run(self):
        for key, packed in self.db.execute("SELECT id,params FROM jobs WHERE api='stock_basic' AND phase='bootstrap' AND status='pending'").fetchall():
            if "exchange" not in json.loads(packed):
                self.db.execute("UPDATE jobs SET status='superseded',error='Replaced by exchange-partitioned stock lists' WHERE id=?", (key,))
        self.db.commit()
        self.add(bootstrap_jobs(self.start, self.end))
        self.state("running")
        if not self.process_pending():
            return 2
        if not self.db.execute("SELECT value FROM meta WHERE key='expanded'").fetchone():
            self.expand()
        if not self.process_pending():
            return 2
        bad = self.db.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('done','superseded')").fetchone()[0]
        self.current = None
        self.state("finished_with_gaps" if bad else "finished")
        return int(bool(bad))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date_arg, required=True)
    parser.add_argument("--end", type=date_arg, required=True)
    parser.add_argument("--max-hours", type=float, default=48)
    args = parser.parse_args()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    if not args.start <= args.end < today or not 0 < args.max_hours <= 48:
        parser.error("Require start <= end < today and 0 < max-hours <= 48")
    root = ROOT / "data" / "tushare"
    root.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with (root / ".sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError("Another data sync is already running") from None
        token, url = read_credentials(ROOT / ".env.tushare")
        runner = Runner(root, BoundedClient(token, url, interval=1.0, timeout=25), args.start, args.end, args.max_hours)
        try:
            return runner.run()
        except Exception as exc:
            error = str(exc).replace(token, "[REDACTED]")
            runner.state("blocked", error)
            print("STOP: " + error, file=sys.stderr, flush=True)
            return 2


if __name__ == "__main__":
    sys.exit(main())
