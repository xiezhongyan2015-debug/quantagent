"""Owned heavy-process jobs with deadlines, retained failures and cancellation.

Results describe process execution, never account returns or research validity.
Workers may persist their own checkpoints anywhere under ``output_dir``. This
supervisor preserves them and writes separate job_* records. A stopped job is
never restarted by calling this function again with the same directory.

Windows uses an OS Job Object assigned before the first instruction: children
are contained too, including when the supervisor dies. POSIX uses an owned
process group for explicit cancellation; it has no kill-on-supervisor-death
guarantee. Output limits are polling stop thresholds, not exact storage quotas.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Callable
from uuid import uuid4

VERSION = "v6_owned_heavy_job_v1"
TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled",
                             "output_limit", "not_started", "unknown"})


def _serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_serial(value).encode("utf-8")).hexdigest()


def _write(path, value, *, exclusive=False):
    path = Path(path)
    destination = path if exclusive else path.with_name(path.name + ".tmp")
    with destination.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        stream.write(_serial(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        destination.replace(path)


def _files(root):
    rows = []
    for path in sorted(root.rglob("*")):
        try:
            if path.is_file():
                rows.append({"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size})
        except FileNotFoundError:
            pass  # A worker may atomically replace its own checkpoint.
    return rows


class _WindowsProcess:
    def __init__(self, command, cwd, env, log, on_started):
        # Reuse this already tested OS primitive, not the V4 research controller.
        from quanta_agents.meta_v3.windows_job import Job
        self.job = Job("quanta-v6-" + uuid4().hex)
        try:
            self.job.start(command, cwd=cwd, env=env, log_path=log, on_suspended=on_started)
        except BaseException:
            self.job.close()
            raise

    def state(self):
        metrics = self.job.measurement()
        return self.job.poll(), metrics["active_processes"], metrics

    def stop(self):
        self.job.terminate()

    def close(self):
        self.job.close()


class _PosixProcess:
    def __init__(self, command, cwd, env, log, on_started):
        with log.open("xb") as stream:
            self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        self.group = self.process.pid
        on_started({"pid": self.process.pid, "process_group": self.group,
                    "assigned_before_resume": False, "started_at": time.time()})

    def state(self):
        code = self.process.poll()
        try:
            os.killpg(self.group, 0)
            active = 1  # Group existence, not an exact descendant count.
        except ProcessLookupError:
            active = 0
        return code, active, {"active_processes": active, "active_process_count_exact": False,
                              "cpu_ms": None, "cpu_complete": False}

    def stop(self):
        try:
            os.killpg(self.group, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self):
        self.stop()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def run_job(command: list[str], *, cwd, output_dir, timeout_seconds: float,
            deadline_epoch: float | None = None, closing_seconds: float = 0,
            max_output_bytes: int = 256 * 1024**2, env: dict[str, str] | None = None,
            cancelled: Callable[[], bool] | None = None, cancel_file=None,
            poll_seconds: float = .05, termination_grace_seconds: float = 2) -> dict:
    """Run exactly one owned command without a shell or implicit retry.

    ``env`` overrides inherited environment; its values are never recorded.
    Cancellation is observed through ``cancelled()`` or ``cancel_file`` (default
    output_dir/cancel.request). The absolute deadline reserves both the caller's
    closing window and termination_grace_seconds for process cleanup. The local
    timeout bounds worker running time; cleanup can take the stated grace.
    A completed status means exit0 and no active owned children, not a validated
    worker result. An unresolved process stop has status unknown, not success.
    """
    if not isinstance(command, list) or not command or not all(isinstance(s, str) and s and "\0" not in s for s in command):
        raise ValueError("nonempty argv list required; shell commands are not accepted")
    for name, value in (("timeout_seconds", timeout_seconds), ("poll_seconds", poll_seconds),
                        ("termination_grace_seconds", termination_grace_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be positive and finite")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be a positive integer")
    if not isinstance(closing_seconds, (int, float)) or not math.isfinite(closing_seconds) or closing_seconds < 0:
        raise ValueError("closing_seconds must be finite and nonnegative")
    if deadline_epoch is not None and (not isinstance(deadline_epoch, (int, float)) or not math.isfinite(deadline_epoch)):
        raise ValueError("deadline_epoch must be finite")
    if env is not None and (not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and
            k and "=" not in k and "\0" not in k + v for k, v in env.items())):
        raise ValueError("environment overrides must be strings")
    if cancelled is not None and not callable(cancelled):
        raise ValueError("cancelled must be callable")
    cwd, root = Path(cwd).resolve(), Path(output_dir).resolve()
    if not cwd.is_dir():
        raise ValueError("job cwd does not exist")
    inherited = {**os.environ, **(env or {})}
    executable = shutil.which(command[0], path=inherited.get("PATH"))
    if executable is None:
        executable = str((cwd / command[0]).resolve())
    argv = [str(Path(executable).resolve()), *command[1:]]
    root.mkdir(parents=True, exist_ok=True)
    cancellation = Path(cancel_file).resolve() if cancel_file is not None else root / "cancel.request"
    epoch, clock = time.time(), time.perf_counter()
    cutoff = None if deadline_epoch is None else deadline_epoch - closing_seconds - termination_grace_seconds
    intent = {"version": VERSION, "command": argv, "command_hash": _hash(argv), "cwd": str(cwd),
        "started_epoch": epoch, "created_at": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": timeout_seconds, "deadline_epoch": deadline_epoch, "closing_seconds": closing_seconds,
        "worker_absolute_cutoff_epoch": cutoff, "termination_grace_seconds": termination_grace_seconds,
        "max_output_bytes": max_output_bytes, "output_limit_is_polling_threshold": True,
        "environment_override_keys": sorted(env or {}), "cancel_file": str(cancellation),
        "supervisor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "platform_primitive_source_sha256": hashlib.sha256(
            (Path(__file__).parents[1] / "meta_v3/windows_job.py").read_bytes()).hexdigest() if os.name == "nt" else None,
        "platform_containment": "windows_job_from_birth" if os.name == "nt" else "posix_owned_process_group",
        "model_calls_by_supervisor": 0, "implicit_retry": False}
    # This durable claim excludes a second supervisor even if no worker started.
    _write(root / "job_intent.json", intent, exclusive=True)
    events = root / "job_events.jsonl"

    def event(kind, details):
        with events.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(_serial({"at": time.time(), "kind": kind, **details}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def is_cancelled():
        return cancellation.exists() or (cancelled is not None and bool(cancelled()))

    worker = None
    identity = None
    status, reason, code, active, metrics = "unknown", None, None, None, None
    error = None
    stop_requested = False
    last_checkpoint = 0.0

    def checkpoint(phase):
        _write(root / "job_checkpoint.json", {"phase": phase, "observed_epoch": time.time(),
            "elapsed_seconds": time.perf_counter() - clock, "process_identity": identity,
            "primary_exit_code": code, "owned_active_processes": active,
            "metrics": metrics, "stop_reason": reason, "process_outcome_only": True})

    def on_started(value):
        nonlocal identity
        identity = value
        event("process_created", value)
        checkpoint("dispatched")

    try:
        if is_cancelled():
            status, reason = "not_started", "cancelled_before_dispatch"
        elif cutoff is not None and time.time() >= cutoff:
            status, reason = "not_started", "closing_window_reserved"
        elif sum(r["bytes"] for r in _files(root)) >= max_output_bytes:
            status, reason = "not_started", "output_limit_before_dispatch"
        else:
            kind = _WindowsProcess if os.name == "nt" else _PosixProcess
            worker = kind(argv, cwd, inherited, root / "worker.log", on_started)
            while True:
                code, active, metrics = worker.state()
                elapsed = time.perf_counter() - clock
                if code is not None and active == 0:
                    if sum(r["bytes"] for r in _files(root)) >= max_output_bytes:
                        status, reason = "output_limit", "retained_output_threshold"
                    elif cutoff is not None and time.time() >= cutoff:
                        status, reason = "timed_out", "closing_window_reserved"
                    elif elapsed >= timeout_seconds:
                        status, reason = "timed_out", "worker_wall_limit"
                    else:
                        status, reason = ("completed", "exit_zero") if code == 0 else ("failed", "exit_nonzero")
                    break
                if is_cancelled():
                    status, reason = "cancelled", "cancellation_requested"
                elif cutoff is not None and time.time() >= cutoff:
                    status, reason = "timed_out", "closing_window_reserved"
                elif elapsed >= timeout_seconds:
                    status, reason = "timed_out", "worker_wall_limit"
                elif sum(r["bytes"] for r in _files(root)) >= max_output_bytes:
                    status, reason = "output_limit", "retained_output_threshold"
                else:
                    if elapsed - last_checkpoint >= 1:
                        checkpoint("running")
                        last_checkpoint = elapsed
                    time.sleep(min(poll_seconds, max(.001, timeout_seconds - elapsed)))
                    continue
                event("stop_requested", {"status": status, "reason": reason})
                stop_requested = True
                worker.stop()
                cleanup_end = time.perf_counter() + termination_grace_seconds
                while time.perf_counter() < cleanup_end:
                    code, active, metrics = worker.state()
                    if code is not None and active == 0:
                        break
                    time.sleep(min(poll_seconds, .05))
                if code is None or active != 0:
                    status = "unknown"
                    error = {"type": "TerminationUnconfirmed", "message": "Owned processes have not all exited within cleanup grace."}
                break
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        reason = "supervisor_interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "supervisor_or_spawn_error"
        status = "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
        if worker is not None:
            stop_requested = True
            try:
                worker.stop()
                cleanup_end = time.perf_counter() + termination_grace_seconds
                while time.perf_counter() < cleanup_end:
                    code, active, metrics = worker.state()
                    if code is not None and active == 0:
                        break
                    time.sleep(min(poll_seconds, .05))
                if code is None or active != 0:
                    status = "unknown"
            except Exception as cleanup_error:
                status = "unknown"
                error["cleanup_error"] = str(cleanup_error)[:1000]
        elif identity is not None:
            # Spawn callback may fail after native creation but before the
            # process wrapper returns. Its native close kills the owned job,
            # but this controller has no independent exit observation.
            status = "unknown"
    finally:
        if worker is not None:
            try:
                worker.close()
            except Exception as cleanup_error:
                status = "unknown"
                error = {**(error or {}), "close_error": str(cleanup_error)[:1000]}
    retained = _files(root)
    result = {"version": VERSION, "status": status, "reason": reason, "error": error,
        "intent_hash": _hash(intent), "process_identity": identity, "primary_exit_code": code,
        "owned_active_processes_at_last_observation": active, "all_owned_processes_exited": active == 0 if worker else identity is None,
        "stop_requested": stop_requested, "spawned": identity is not None,
        "wall_seconds": time.perf_counter() - clock, "finished_epoch": time.time(), "metrics": metrics,
        "retained_files_before_final_receipt": retained, "retained_bytes_before_final_receipt": sum(r["bytes"] for r in retained),
        "worker_log_path": str(root / "worker.log") if (root / "worker.log").exists() else None,
        "worker_checkpoints_preserved": True, "process_outcome_only": True,
        "model_calls_by_supervisor": 0, "automatic_retry": False}
    checkpoint("terminal")
    event("job_terminal", {"status": status, "reason": reason, "result_hash": _hash(result)})
    _write(root / "job_result.json", result, exclusive=True)
    return result
