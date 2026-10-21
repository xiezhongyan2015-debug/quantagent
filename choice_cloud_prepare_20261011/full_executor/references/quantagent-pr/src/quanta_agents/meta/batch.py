"""Persistent development batches driven exclusively through a localhost API.

A creation intent is committed before its one allowed POST.  Uncertain POSTs
are reconciled by request_key using GET only; absence from the ledger is not
permission to pay again. This scheduler never constructs an Engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .ashare_tasks import TASKS

MODEL = "gpt-6-astra"
EFFORT = "xhigh"
DEFAULT_TASKS = ("relative_strength", "volume_expansion")
LIMITS = {"hard_tokens": 8_000_000, "max_wall_seconds": 86_400, "reserve_tokens_per_entry": 1_000_000}
ENTRY_TERMINAL = {"completed", "failed", "budget_exceeded"}
LOOP_STOP = {"completed", "cancelled", "budget_exceeded", "frozen_mismatch", "attention"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic(path: Path, body: Any) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class BatchLock:
    """One dispatcher per independent batch directory, across processes."""
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.file = (root / ".batch.lock").open("a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"1")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("another dispatcher owns this batch directory") from exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("API redirects are disabled")


class HTTPClient:
    def __init__(self, api_url: str, timeout: float = 20):
        parsed = urlsplit(api_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "192.0.2.10"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.port is None):
            raise ValueError("api_url must be an explicit http://localhost:port or http://192.0.2.10:port")
        self.api_url, self.timeout = api_url.rstrip("/"), timeout
        self.opener = build_opener(_NoRedirect())

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else None
        request = Request(self.api_url + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            payload = response.read(32_000_001)
            if len(payload) > 32_000_000:
                raise ValueError("API response exceeds 32 MB")
            return json.loads(payload)

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body)


def _source(client, source_run_id: str) -> tuple[dict, dict]:
    health = client.get("/api/health")
    if health.get("model") != MODEL or health.get("effort") != EFFORT:
        raise ValueError("service model/effort does not match the frozen Astra/xhigh contract")
    if not isinstance(health.get("source_hash"), str) or not health["source_hash"]:
        raise ValueError("service did not report source_hash")
    run = client.get("/api/runs/" + quote(source_run_id, safe=""))
    config = run.get("config") or {}
    if (run.get("status") != "completed" or run.get("mode") != "live"
            or config.get("case_type") != "ashare"
            or config.get("model") != MODEL or config.get("effort") != EFFORT):
        raise ValueError("source must be a completed live A-share Astra/xhigh run")
    proposal = (run.get("steps") or {}).get("meta_proposal")
    if (not isinstance(proposal, dict) or not isinstance(proposal.get("research_instructions"), str)
            or not proposal["research_instructions"].strip()):
        raise ValueError("source run has no completed research instructions")
    return health, proposal


def create_plan(api_url: str, source_run_id: str, output_dir: str | Path,
                tasks: list[str] | tuple[str, ...] = DEFAULT_TASKS,
                repeats: int = 2, calls: int = 12, *, client=None) -> dict:
    """Freeze a plan without starting any runs or invoking any model."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", source_run_id):
        raise ValueError("source_run_id must be a run ID")
    if type(repeats) is not int or repeats < 1 or type(calls) is not int or not 6 <= calls <= 18:
        raise ValueError("repeats must be positive and research calls must be 6..18")
    if (not tasks or len(set(tasks)) != len(tasks)
            or any(type(t) is not str or t not in TASKS for t in tasks)):
        raise ValueError("tasks must be unique registered task names")
    limits = {**LIMITS, "reserve_tokens_per_entry": math.ceil(1_000_000 * calls / 12)}
    if len(tasks) * repeats * 2 * limits["reserve_tokens_per_entry"] > limits["hard_tokens"]:
        raise ValueError("planned entry reservations exceed the frozen 8M accident limit")
    api = client or HTTPClient(api_url)
    # Validate URL even when a fake HTTP client is supplied in unit tests.
    HTTPClient(api_url)
    root = Path(output_dir).expanduser().resolve()
    with BatchLock(root):
        if (root / "plan.json").exists() or (root / "state.json").exists():
            raise FileExistsError("batch plan/state already exists; use tick or run to resume")
        health, proposal = _source(api, source_run_id)
        if api.get("/api/health").get("source_hash") != health["source_hash"]:
            raise ValueError("service source changed while freezing the plan")
        namespace = uuid.uuid4().hex
        entries = []
        for task in tasks:
            for repeat in range(1, repeats + 1):
                order = ("baseline", "candidate") if repeat % 2 else ("candidate", "baseline")
                for arm in order:
                    entry_id = f"entry_{len(entries) + 1:02d}"
                    request_key = f"batch_{namespace}_{len(entries) + 1:02d}"
                    entries.append({"entry_id": entry_id, "task": task, "repeat": repeat,
                                    "architecture": arm, "request_key": request_key,
                                    "request": {"mode": "live", "case": "ashare", "task": task,
                                                "research_calls": calls, "frozen_candidate_run_id": source_run_id,
                                                "only_architecture": arm, "evaluation_split": "development",
                                                "request_key": request_key}})
        plan = {"schema_version": 1, "batch_id": root.name, "created_at": _now(),
                "api_url": api_url.rstrip("/"), "source_run_id": source_run_id,
                "source_hash": health["source_hash"], "model": MODEL, "effort": EFFORT,
                "research_instructions": proposal["research_instructions"],
                "research_instructions_sha256": _text_digest(proposal["research_instructions"]),
                "source_proposal_sha256": _digest(proposal),
                "tasks": list(tasks), "repeats": repeats, "research_calls": calls,
                "order_policy": "within each task, odd repeats AB and even repeats BA",
                "repeat_meaning": "independent model restarts, not a controllable model seed",
                "scope": "development_only", "evaluation_split": "development",
                "target_net_sharpe": 1.0, "formal_target_success": False, "promotion": False,
                "target_rule": "report development net Sharpe > 1 separately; never formal success or architecture promotion",
                "limits": limits, "entries": entries}
        plan["plan_sha256"] = _digest(plan)
        _atomic(root / "plan.json", plan)
        state = _initial_state(plan)
        _save(root, plan, state)
        return plan


def _initial_state(plan: dict) -> dict:
    return {"schema_version": 1, "plan_sha256": plan["plan_sha256"], "status": "ready",
            "started_at": None, "updated_at": _now(), "stop_reason": None, "locked_stop": False,
            "entries": [{"entry_id": e["entry_id"], "request_key": e["request_key"],
                         "status": "not_started", "run_id": None, "post_attempted": False,
                         "known_tokens": None, "usage_complete": None, "elapsed_seconds": None,
                         "run_status": None, "metric_source": None, "net_development_sharpe": None,
                         "max_drawdown": None, "execution_valid": None, "error": None}
                        for e in plan["entries"]]}


def _read(root: Path) -> tuple[dict, dict]:
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    expected = plan.get("plan_sha256")
    if expected != _digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("immutable plan hash mismatch; no dispatch permitted")
    path = root / "state.json"
    if not path.exists():
        raise ValueError("batch state is missing; manual ledger reconciliation required, no dispatch permitted")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("plan_sha256") != expected:
        raise ValueError("state belongs to a different frozen plan")
    if [(x["entry_id"], x["request_key"]) for x in state["entries"]] != [(x["entry_id"], x["request_key"]) for x in plan["entries"]]:
        raise ValueError("state entry identities differ from the frozen plan")
    return plan, state


def _elapsed(state: dict) -> float:
    start = state.get("started_at")
    return max(0.0, time.time() - datetime.fromisoformat(start).timestamp()) if start else 0.0


def _report(plan: dict, state: dict) -> dict:
    entries = []
    for spec, progress in zip(plan["entries"], state["entries"]):
        score = progress.get("net_development_sharpe")
        entries.append({**{k: spec[k] for k in ("entry_id", "task", "repeat", "architecture", "request_key")},
                        **progress, "development_sharpe_gt_1": score > 1 if score is not None else None,
                        "formal_target_success": False, "promotion": False})
    counts = {"planned": len(entries), "completed": 0, "failed": 0, "cancelled": 0,
              "not_started": 0, "attention": 0, "active": 0}
    for entry in entries:
        status = entry["status"]
        field = ("failed" if status in {"failed", "budget_exceeded"} else
                 "attention" if status in {"attention", "creation_unknown"} else
                 status if status in {"completed", "cancelled", "not_started"} else "active")
        counts[field] += 1
    unknown = sum(e["status"] != "not_started" and e.get("usage_complete") is not True for e in entries)
    reserved = sum(e["status"] not in ENTRY_TERMINAL | {"not_started", "cancelled"}
                   or (e["status"] in ENTRY_TERMINAL | {"cancelled"} and e.get("usage_complete") is not True)
                   for e in entries) * plan["limits"]["reserve_tokens_per_entry"]
    current = next((e for e in entries if e["status"] not in ENTRY_TERMINAL | {"not_started"}), None)
    return {"schema_version": 1, "batch_id": plan["batch_id"], "plan_sha256": plan["plan_sha256"],
            "status": state["status"], "updated_at": state["updated_at"], "scope": "development_only",
            "promotion": False, "formal_target_success": False, "counts": counts,
            "usage": {"known_tokens": sum(e.get("known_tokens") or 0 for e in entries),
                      "is_partial": bool(unknown), "unknown_entries": unknown, "reserved_tokens": reserved,
                      "accident_token_limit": plan["limits"]["hard_tokens"]},
            "elapsed_seconds": _elapsed(state), "limits": plan["limits"],
            "current_entry_id": current["entry_id"] if current else None,
            "current_run_id": current.get("run_id") if current else None,
            "stop_reason": state.get("stop_reason"), "entries": entries,
            "interpretation": "All planned entries remain in the denominator, including failures and unstarted runs. Development Sharpe > 1 is not out-of-sample success. No stable probability or architecture promotion is inferred."}


def _markdown(report: dict) -> str:
    def show(value):
        if value is None:
            return "未知/尚无"
        return str(value).replace("|", "\\|").replace("\n", " ")
    rows = ["# 冻结开发批次", "", f"状态：{report['status']}。全部计划项：{report['counts']['planned']}。",
            "", "仅开发期，不是样本外。开发 Sharpe > 1 单独展示；正式目标达标和架构晋升均为 false。",
            f"已报告 token：{report['usage']['known_tokens']}；用量是否不完整：{report['usage']['is_partial']}；事故预留：{report['usage']['reserved_tokens']}；上限：{report['usage']['accident_token_limit']}。",
            "", "| 项 | 任务 | 重复 | 架构 | 状态 | run | token | 活动秒 | 开发净 Sharpe | 回撤比例 | execution_valid | 开发 Sharpe > 1 |",
            "|---|---|---:|---|---|---|---:|---:|---:|---:|---|---|"]
    for e in report["entries"]:
        rows.append("| " + " | ".join(show(e.get(k)) for k in (
            "entry_id", "task", "repeat", "architecture", "status", "run_id", "known_tokens", "elapsed_seconds",
            "net_development_sharpe", "max_drawdown", "execution_valid", "development_sharpe_gt_1")) + " |")
    rows.extend(["", "停止/等待原因：" + show(report.get("stop_reason")), "",
                 "未启动、失败与未知用量不被删去；未知用量不解释为零。所有题共用市场时期，不能将其当成独立金融样本。", ""])
    return "\n".join(rows)


def _save(root: Path, plan: dict, state: dict) -> dict:
    state["updated_at"] = _now()
    _atomic(root / "state.json", state)
    report = _report(plan, state)
    _atomic(root / "report.json", report)
    temporary = root / "report.md.tmp"
    temporary.write_text(_markdown(report), encoding="utf-8")
    os.replace(temporary, root / "report.md")
    return report


def _stop(state: dict, status: str, reason: str, *, locked: bool = False) -> None:
    state.update(status=status, stop_reason=reason, locked_stop=locked)


def _find_run(client, request_key: str) -> str | None:
    body = client.get("/api/runs")
    if not isinstance(body, dict) or not isinstance(body.get("runs"), list):
        raise ValueError("run list unavailable; do not dispatch without reconciliation")
    found = [r for r in body["runs"] if r.get("request_key") == request_key]
    if len(found) > 1:
        raise ValueError("multiple runs have the same request_key")
    if not found:
        return None
    run_id = found[0].get("id", found[0].get("run_id"))
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("reconciled run has no ID")
    return run_id


def _verify_run(plan: dict, spec: dict, run: dict) -> None:
    config = run.get("config") or {}
    frozen = config.get("frozen_candidate") or {}
    expected = (run.get("request_key") == spec["request_key"]
                and run.get("mode") == "live"
                and config.get("model") == MODEL and config.get("effort") == EFFORT
                and config.get("source_manifest", {}).get("hash") == plan["source_hash"]
                and config.get("case_type") == "ashare"
                and config.get("case_config", {}).get("task") == spec["task"]
                and config.get("evaluation_split") == "development"
                and config.get("only_architecture") == spec["architecture"]
                and config.get("research_calls_per_architecture") == plan["research_calls"]
                and frozen.get("source_run_id") == plan["source_run_id"]
                and _text_digest(str(frozen.get("research_instructions", ""))) == plan["research_instructions_sha256"])
    if not expected:
        raise ValueError("run identity/configuration does not match the frozen entry")
    if run.get("confirmation_opened") or run.get("final_opened"):
        raise ValueError("development-only run opened a held-out split")


def _valid_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _usage(run: dict) -> tuple[int, bool, bool]:
    """Return known tokens, complete usage, and unresolved non-inflight call."""
    calls = run.get("calls")
    usage = run.get("usage") or {}
    if not isinstance(calls, list):
        raise ValueError("full run call ledger missing")
    total, complete, unresolved = 0, True, False
    for call in calls:
        values = call.get("usage") or {}
        known = all(_valid_number(values.get(k)) for k in ("input_tokens", "output_tokens"))
        total += sum(int(values[k]) for k in ("input_tokens", "output_tokens") if _valid_number(values.get(k)))
        complete = complete and known
        if call.get("status") in {"uncertain", "interrupted"} or (not known and call.get("status") != "running"):
            unresolved = True
    reported = usage.get("reported_total_tokens", usage.get("total_tokens"))
    if not _valid_number(reported) or int(reported) != total:
        raise ValueError("aggregate usage does not reconcile with the call ledger")
    if usage.get("unknown_calls", 0) or usage.get("is_partial", False):
        complete = False
    if not complete and run.get("status") not in {"running", "queued", "paused", "pausing", "cancelling"}:
        unresolved = True
    return total, complete, unresolved


def _result(run: dict, arm: str) -> dict | None:
    comparison = run.get("comparison") or {}
    value = (comparison.get("arm_results") or {}).get(arm) or comparison.get(arm)
    if not isinstance(value, dict):
        value = ((run.get("steps") or {}).get("final_evaluation") or {}).get(arm)
    return value if isinstance(value, dict) else None


def _observe(plan: dict, spec: dict, entry: dict, run: dict, state: dict) -> None:
    _verify_run(plan, spec, run)
    known, complete, unresolved = _usage(run)
    entry.update(known_tokens=known, usage_complete=complete, run_status=run.get("status"),
                 run_phase=run.get("phase"), elapsed_seconds=run.get("elapsed_seconds"),
                 error=run.get("last_error"), comparable=run.get("comparable"))
    result = _result(run, spec["architecture"])
    if result:
        if result.get("split") not in {None, "development"}:
            raise ValueError("result is not from the frozen development split")
        metrics = result.get("metrics") or {}
        score = result.get("score", metrics.get("sharpe_ratio"))
        if type(score) in (int, float) and math.isfinite(score):
            entry["net_development_sharpe"] = float(score)
        drawdown = metrics.get("max_ddpercent")
        if type(drawdown) in (int, float) and math.isfinite(drawdown):
            entry["max_drawdown"] = float(drawdown)
        valid = result.get("execution_valid")
        entry.update(execution_valid=valid if type(valid) is bool else None, metric_source="frozen_development_evaluation")
    run_status = run.get("status")
    if run_status == "cancelled":
        entry["status"] = "cancelled"
        _stop(state, "cancelled", "a member was cancelled; no later member will start", locked=True)
    elif unresolved or run_status in {"uncertain", "interrupted"}:
        entry["status"] = "attention"
        _stop(state, "attention", "uncertain/interrupted call or unknown settled usage requires reconciliation")
    elif run_status in ENTRY_TERMINAL:
        entry["status"] = run_status
        state.update(status="running", stop_reason=None)
    elif run_status in {"paused", "pausing"}:
        entry["status"] = run_status
        _stop(state, "paused", "member is paused; resume only through the existing run controls")
    elif run_status == "cancelling":
        entry["status"] = "cancelling"
        _stop(state, "cancelling", "member cancellation is pending; no later member will start")
    elif run_status in {"queued", "running"}:
        entry["status"] = run_status
        state.update(status="running", stop_reason=None)
    else:
        entry["status"] = "attention"
        _stop(state, "attention", f"unrecognized run status: {run_status}")


def _budget_stop(root: Path, plan: dict, state: dict, client, reason: str) -> dict:
    _stop(state, "budget_exceeded", reason, locked=True)
    active = next((e for e in state["entries"] if e.get("run_id") and
                   (e.get("run_status") in {"running", "queued", "paused", "pausing"}
                    or e.get("status") == "submitted")), None)
    if active and not active.get("budget_cancel_attempted"):
        active["budget_cancel_attempted"] = True
        _save(root, plan, state)
        try:
            client.post("/api/runs/" + quote(active["run_id"], safe="") + "/control", {"action": "cancel"})
            active["budget_cancel_requested"] = True
        except Exception as exc:
            active["budget_cancel_unknown"] = True
            _stop(state, "attention", reason + "; cancel response unknown: " + type(exc).__name__, locked=True)
    return _save(root, plan, state)


def tick(output_dir: str | Path, *, client=None) -> dict:
    """Reconcile/monitor or create at most one member. Never retries creation."""
    root = Path(output_dir).expanduser().resolve()
    with BatchLock(root):
        plan, state = _read(root)
        api = client or HTTPClient(plan["api_url"])
        if state.get("locked_stop"):
            # A stop forbids new work, but later explicit ticks can still collect
            # the final cancellation/usage receipt without another control POST.
            original = {k: state[k] for k in ("status", "stop_reason", "locked_stop")}
            for spec, entry in zip(plan["entries"], state["entries"]):
                if entry.get("run_id") and (entry.get("usage_complete") is not True
                        or entry.get("run_status") in {"running", "queued", "paused", "pausing", "cancelling"}):
                    try:
                        observed = api.get("/api/runs/" + quote(entry["run_id"], safe=""))
                        _observe(plan, spec, entry, observed, state)
                    except Exception:
                        pass
                    break
            state.update(original)
            return _save(root, plan, state)
        if state["status"] == "completed":
            return _save(root, plan, state)
        try:
            health, proposal = _source(api, plan["source_run_id"])
            if (health["source_hash"] != plan["source_hash"]
                    or _text_digest(proposal["research_instructions"]) != plan["research_instructions_sha256"]
                    or _digest(proposal) != plan["source_proposal_sha256"]):
                _stop(state, "frozen_mismatch", "frozen service source or source research proposal changed", locked=True)
                return _save(root, plan, state)
            if _elapsed(state) >= plan["limits"]["max_wall_seconds"]:
                return _budget_stop(root, plan, state, api, "24-hour batch wall-time accident limit reached")
            pending = next((i for i, e in enumerate(state["entries"]) if e["status"] not in ENTRY_TERMINAL), None)
            if pending is None:
                state.update(status="completed", stop_reason=None)
                return _save(root, plan, state)
            spec, entry = plan["entries"][pending], state["entries"][pending]
            if not entry.get("run_id"):
                found = _find_run(api, spec["request_key"])
                if found:
                    entry.update(run_id=found, post_attempted=True, status="submitted")
                    if not state["started_at"]:
                        state["started_at"] = plan["created_at"]
                elif entry["post_attempted"]:
                    entry["status"] = "creation_unknown"
                    _stop(state, "attention", "creation outcome unknown; request_key not found; GET reconciliation only, no repeated POST")
                    return _save(root, plan, state)
                else:
                    if health.get("active_run"):
                        _stop(state, "waiting", "service has another active run; batch will not compete for its worker")
                        return _save(root, plan, state)
                    known = _report(plan, state)["usage"]["known_tokens"]
                    if known + plan["limits"]["reserve_tokens_per_entry"] > plan["limits"]["hard_tokens"]:
                        return _budget_stop(root, plan, state, api,
                            f"next {plan['limits']['reserve_tokens_per_entry']} token entry reservation would exceed the 8M accident limit")
                    if not state["started_at"]:
                        state["started_at"] = _now()
                    entry.update(status="creating", post_attempted=True, create_intent_at=_now())
                    state.update(status="running", stop_reason=None)
                    _save(root, plan, state)  # Durable intent precedes the paid action.
                    try:
                        response = api.post("/api/runs", spec["request"])
                        run_id = response.get("run_id")
                        if not isinstance(run_id, str) or not run_id:
                            raise ValueError("creation response has no run_id")
                        entry.update(run_id=run_id, status="submitted", create_acknowledged_at=_now())
                    except Exception as exc:
                        entry.update(status="creation_unknown", error=type(exc).__name__)
                        _stop(state, "attention", "creation response unknown; reconcile request_key using GET before any further action")
                    return _save(root, plan, state)
            run = api.get("/api/runs/" + quote(entry["run_id"], safe=""))
            _observe(plan, spec, entry, run, state)
            if _report(plan, state)["usage"]["known_tokens"] >= plan["limits"]["hard_tokens"]:
                return _budget_stop(root, plan, state, api, "8M reported-token accident limit reached")
            if all(e["status"] in ENTRY_TERMINAL for e in state["entries"]):
                state.update(status="completed", stop_reason=None)
            return _save(root, plan, state)
        except (ValueError, KeyError, TypeError) as exc:
            for progress in state["entries"]:
                if progress["status"] not in ENTRY_TERMINAL | {"not_started", "cancelled"}:
                    progress.update(status="attention", usage_complete=False)
                    break
            _stop(state, "attention", f"API/ledger validation failed: {exc}")
            return _save(root, plan, state)
        except Exception as exc:
            _stop(state, "attention", "read-only API reconciliation unavailable: " + type(exc).__name__)
            return _save(root, plan, state)


def status(output_dir: str | Path) -> dict:
    """Read locally without API access or changing the plan/state."""
    plan, state = _read(Path(output_dir).expanduser().resolve())
    return _report(plan, state)


def run(output_dir: str | Path, *, poll_seconds: float = 15) -> dict:
    if not math.isfinite(poll_seconds) or poll_seconds < 1:
        raise ValueError("poll_seconds must be at least one second")
    previous = None
    while True:
        report = tick(output_dir)
        fingerprint = _digest({"status": report["status"], "reason": report["stop_reason"],
                               "entries": [(e["status"], e.get("run_phase"), e.get("known_tokens")) for e in report["entries"]]})
        if fingerprint != previous:
            print(json.dumps({k: report[k] for k in ("batch_id", "status", "counts", "usage", "current_run_id", "stop_reason")}, ensure_ascii=False), flush=True)
            previous = fingerprint
        if report["status"] in LOOP_STOP:
            return report
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent localhost A-share development batch")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--api-url", required=True)
    plan.add_argument("--source-run-id", required=True)
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--tasks", nargs="+", default=list(DEFAULT_TASKS))
    plan.add_argument("--repeats", type=int, default=2)
    plan.add_argument("--calls", type=int, default=12)
    for name in ("run", "tick", "status"):
        command = sub.add_parser(name)
        command.add_argument("--output-dir", required=True)
        if name == "run":
            command.add_argument("--poll-seconds", type=float, default=15)
    args = parser.parse_args()
    if args.command == "plan":
        result = create_plan(args.api_url, args.source_run_id, args.output_dir, args.tasks, args.repeats, args.calls)
        print(json.dumps({"batch_id": result["batch_id"], "planned": len(result["entries"]), "plan_sha256": result["plan_sha256"]}, ensure_ascii=False))
    elif args.command == "run":
        run(args.output_dir, poll_seconds=args.poll_seconds)
    else:
        print(json.dumps((tick if args.command == "tick" else status)(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
