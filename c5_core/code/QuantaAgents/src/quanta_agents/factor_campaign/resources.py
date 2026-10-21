"""Campaign resource admission and cumulative, observation-backed actual costs."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from uuid import uuid4

from quanta_agents.research_kernel.store import digest, serial

GIB = 1024 ** 3


def resource_snapshot(root):
    """Read live availability. Unknown measurements block admission."""
    root = Path(root).resolve()
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    result = {"observed_unix": time.time(), "disk_path": str(probe),
              "available_memory_bytes": None, "process_memory_bytes": None,
              "disk_free_bytes": None}
    try:
        import psutil
        result["available_memory_bytes"] = psutil.virtual_memory().available
        result["process_memory_bytes"] = psutil.Process().memory_info().rss
    except (ImportError, OSError):
        if os.name == "nt":
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in
                    ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
            value = MemoryStatus()
            value.length = ctypes.sizeof(value)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
                result["available_memory_bytes"] = value.available
        elif hasattr(os, "sysconf"):
            try:
                result["available_memory_bytes"] = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            except (ValueError, OSError):
                pass
    try:
        result["disk_free_bytes"] = shutil.disk_usage(probe).free
    except OSError:
        pass
    return result


class ResourceGuard:
    def __init__(self, root, *, snapshotter=resource_snapshot):
        self.root, self.snapshotter = Path(root).resolve(), snapshotter

    def check(self, *, required_memory_bytes=0, required_disk_bytes=0):
        if min(required_memory_bytes, required_disk_bytes) < 0:
            raise ValueError("Resource reservations cannot be negative")
        snapshot = self.snapshotter(self.root)
        blockers = []
        for key, floor, extra, reason in (
            ("available_memory_bytes", 6 * GIB, required_memory_bytes, "memory_reserve_below_6GiB"),
            ("disk_free_bytes", 10 * GIB, required_disk_bytes, "disk_reserve_below_10GiB"),
        ):
            if snapshot.get(key) is None:
                blockers.append(key + "_unknown")
            elif snapshot[key] - extra < floor:
                blockers.append(reason)
        return {**snapshot, "allowed": not blockers, "blockers": blockers,
                "memory_reserve_bytes": 6 * GIB, "disk_reserve_bytes": 10 * GIB}

    def workers_after_calibration(self, calibration=None):
        """Always begin with one worker; two need measured private peak headroom."""
        snapshot = self.check()
        peak = (calibration or {}).get("worker_peak_private_bytes")
        calibrated = (calibration or {}).get("single_process_completed") is True
        pair = calibrated and isinstance(peak, (int, float)) and peak > 0
        enough = snapshot.get("available_memory_bytes", 0) or 0
        workers = 2 if snapshot["allowed"] and pair and enough >= 6 * GIB + 2 * peak * 1.2 else 1
        return {"workers": workers if snapshot["allowed"] else 0, "snapshot": snapshot,
                "calibrated": calibrated, "worker_peak_private_bytes": peak}


class CostLedger:
    """Each logical event has immutable observations and one latest known total.

    Recovering the same model receipt improves its cost evidence without charging
    a second call. Reasoning is a subset of output, never added to token totals.
    """
    def __init__(self, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "campaign_cost.sqlite"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,UNIQUE(event_id,digest))")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, event_id, **actuals):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Stable event_id required")
        for key in ("cpu_seconds", "wall_seconds", "peak_memory_bytes", "peak_private_bytes", "peak_rss_bytes", "available_memory_min_bytes", "artifact_bytes"):
            value = actuals.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError("Actual measurements must be finite and nonnegative")
        usage = actuals.get("usage", {})
        if not isinstance(usage, dict) or any(v is not None and (type(v) is not int or v < 0) for v in usage.values()):
            raise ValueError("Usage must contain actual nonnegative integer counts or unknown null")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM observations WHERE event_id=? ORDER BY seq DESC LIMIT 1", (event_id,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            merged = {**previous, **actuals}
            # Missing/newly unknown evidence never erases earlier known usage.
            merged["usage"] = {**previous.get("usage", {}), **{k: v for k, v in usage.items() if v is not None}}
            merged["failed"] = bool(previous.get("failed") or actuals.get("failed"))
            merged["currency_cost"] = None
            merged["currency_cost_status"] = "unknown_no_billing_receipt"
            db.execute("INSERT OR IGNORE INTO observations(event_id,digest,payload,created) VALUES (?,?,?,?)",
                       (event_id, digest(merged), serial(merged), time.time()))
        return merged

    @contextmanager
    def measure(self, event_id, **metadata):
        # A repeated numerical/test execution is new work. In contrast, record()
        # uses a stable ID to reconcile several observations of one model call.
        attempt_id = event_id + ".attempt." + uuid4().hex
        wall, cpu = time.perf_counter(), time.process_time()
        self.record(attempt_id, **metadata, logical_event_id=event_id, status="started",
                    cpu_seconds=None, wall_seconds=None, peak_memory_bytes=None,
                    peak_private_bytes=None, peak_rss_bytes=None, available_memory_min_bytes=None)
        telemetry = {"peak_private_bytes": None, "peak_rss_bytes": None,
                     "available_memory_min_bytes": None, "memory_samples": 0}
        stopped = threading.Event()
        def sample():
            try:
                import psutil
                process = psutil.Process()
            except (ImportError, OSError):
                process = None
            last_save = time.perf_counter()
            while not stopped.is_set():
                try:
                    if process is not None:
                        memory = process.memory_info()
                        observed = {"peak_rss_bytes": memory.rss,
                                    "peak_private_bytes": getattr(memory, "private", None),
                                    "available_memory_min_bytes": psutil.virtual_memory().available}
                        for key in ("peak_rss_bytes", "peak_private_bytes"):
                            value = observed[key]
                            if value is not None:
                                telemetry[key] = max(telemetry[key] or 0, value)
                        value = observed["available_memory_min_bytes"]
                        telemetry["available_memory_min_bytes"] = min(telemetry["available_memory_min_bytes"] or value, value)
                        telemetry["memory_samples"] += 1
                    now = time.perf_counter()
                    if now - last_save >= 5:
                        self.record(attempt_id, **metadata, logical_event_id=event_id, status="running",
                                    **telemetry, peak_memory_bytes=telemetry["peak_private_bytes"] or telemetry["peak_rss_bytes"],
                                    observed_wall_lower_bound=now - wall,
                                    cpu_seconds=None, wall_seconds=None,
                                    memory_sampling_seconds=.25)
                        last_save = now
                except Exception:
                    # Missing telemetry must not kill research or invent zeros.
                    pass
                stopped.wait(.25)
        sampler = threading.Thread(target=sample, daemon=True, name="campaign-cost-sampler")
        sampler.start()
        failure = False
        try:
            yield telemetry
        except BaseException:
            failure = True
            raise
        finally:
            stopped.set()
            sampler.join(timeout=1)
            self.record(attempt_id, **metadata, logical_event_id=event_id,
                        status="failed" if failure else "completed", cpu_seconds=time.process_time() - cpu,
                        wall_seconds=time.perf_counter() - wall, failed=failure, **telemetry,
                        peak_memory_bytes=telemetry["peak_private_bytes"] or telemetry["peak_rss_bytes"],
                        memory_sampling_seconds=.25,
                        memory_measurement_scope="sampled current process; peaks between samples may be missed")

    def summary(self):
        with self._db() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM observations WHERE seq IN (SELECT MAX(seq) FROM observations GROUP BY event_id)")]
        calls = [r for r in rows if r.get("kind") == "model_call"]
        result = {"events": len(rows), "model_calls": len(calls),
                  "failed_model_calls": sum(bool(r.get("failed")) for r in calls),
                  "unknown_usage_calls": sum(any(r.get("usage", {}).get(k) is None for k in ("input_tokens", "output_tokens")) for r in calls),
                  "currency_cost": None, "currency_cost_status": "unknown_no_billing_receipt",
                  "cpu_measurement_scope": "recorded Python processes; child CPU only when explicitly supplied"}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
            result[key] = sum(r.get("usage", {}).get(key) or 0 for r in calls)
        result["known_total_tokens"] = result["input_tokens"] + result["output_tokens"]
        for key in ("cpu_seconds", "wall_seconds", "artifact_bytes"):
            result[key] = sum(r.get(key) or 0 for r in rows)
        measured_peaks = [r["peak_memory_bytes"] for r in rows if r.get("peak_memory_bytes") is not None]
        result["peak_memory_bytes"] = max(measured_peaks) if measured_peaks else None
        result["events_without_cpu_measurement"] = sum(r.get("cpu_seconds") is None for r in rows)
        result["events_without_wall_measurement"] = sum(r.get("wall_seconds") is None for r in rows)
        result["events_without_memory_measurement"] = sum(r.get("peak_memory_bytes") is None for r in rows)
        result["unfinished_measurements"] = sum(r.get("status") in {"started", "running"} for r in rows)
        available = [r["available_memory_min_bytes"] for r in rows if r.get("available_memory_min_bytes") is not None]
        result["available_memory_min_bytes"] = min(available) if available else None
        return result
