"""One bounded, audited Codex invocation for the meta research harness.

This adapter does not retry model calls. CLI tool switches and a read-only
sandbox reduce the tool surface; an observed tool event aborts the invocation.
They are not an OS boundary protecting a hidden evaluation dataset. Only public
or synthetic inputs belong in this gateway's working environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable


MODEL = "gpt-6-astra"
EFFORT = "xhigh"
_USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"
)
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "multi_agent", "multi_agent_v2", "enable_fanout",
    "apps", "enable_mcp_apps", "plugins", "remote_plugin", "plugin_sharing",
    "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "computer_use", "in_app_browser", "code_mode", "code_mode_host", "code_mode_only",
    "image_generation", "artifact", "workspace_dependencies", "memories", "goals",
    "hooks", "tool_suggest", "skill_mcp_dependency_install", "auth_elicitation",
    "request_permissions_tool", "standalone_web_search", "shell_snapshot",
)
_REQUIRED_FEATURES = {"shell_tool", "multi_agent", "apps", "plugins", "browser_use",
                      "computer_use", "code_mode_host"}


class GatewayError(RuntimeError):
    """A failed invocation, which the caller must not blindly retry."""


class GatewayCancelled(GatewayError):
    pass


class GatewayTimeout(GatewayError):
    pass


class ToolPolicyViolation(GatewayError):
    pass


class ModelPolicyViolation(GatewayError):
    pass


def _event(kind: str, text: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "text": text, "data": data}


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage_from(raw: dict[str, Any]) -> dict[str, int | None] | None:
    usage = raw.get("usage")
    response = raw.get("response")
    if not isinstance(usage, dict) and isinstance(response, dict):
        usage = response.get("usage")
    if not isinstance(usage, dict):
        msg = raw.get("msg", raw)
        info = msg.get("info") if isinstance(msg, dict) else None
        usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return None
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    return {
        "input_tokens": _number(usage.get("input_tokens")),
        "cached_input_tokens": _number(usage.get("cached_input_tokens", input_details.get("cached_tokens"))),
        # Provider output_tokens already includes reasoning; never add it twice.
        "output_tokens": _number(usage.get("output_tokens")),
        "reasoning_output_tokens": _number(usage.get("reasoning_output_tokens", output_details.get("reasoning_tokens"))),
    }


class _EventParser:
    def __init__(self, on_event: Callable[[dict], None]) -> None:
        self.on_event = on_event
        self.usage: dict[str, int | None] = dict.fromkeys(_USAGE_KEYS)
        self.reported_model: str | None = None
        self.reported_effort: str | None = None
        self.failed: str | None = None
        self.turn_completed = False

    def feed(self, raw: dict[str, Any]) -> None:
        kind = str(raw.get("type", ""))
        # Never treat model-generated final text as runtime identity evidence.
        if kind in {"session_configured", "session.started", "session.created",
                    "session.updated", "session.header", "turn.started",
                    "response.created", "response.completed"}:
            identity = raw.get("session", raw.get("response", raw))
            if isinstance(identity, dict):
                model = identity.get("model")
                reasoning = identity.get("reasoning")
                effort = identity.get("reasoning_effort", identity.get("model_reasoning_effort"))
                if effort is None and isinstance(reasoning, dict):
                    effort = reasoning.get("effort")
                if model is not None:
                    if model != MODEL:
                        raise ModelPolicyViolation(f"Runtime reported unexpected model: {model}")
                    self.reported_model = model
                if effort is not None:
                    if effort != EFFORT:
                        raise ModelPolicyViolation(f"Runtime reported unexpected reasoning effort: {effort}")
                    self.reported_effort = effort
        usage = _usage_from(raw)
        if usage is not None:
            for key, value in usage.items():
                if value is not None:
                    self.usage[key] = value
            self.on_event(_event("usage", "Provider usage", dict(self.usage)))
        item = raw.get("item")
        item_kind = str(item.get("type", "")) if isinstance(item, dict) else ""
        if isinstance(item, dict):
            if item_kind == 'error':
                # CLI startup diagnostics are informational items, not executable tools.
                # turn.failed / absence of turn.completed decides whether the call failed.
                self.on_event(_event('error', str(item.get('message', 'CLI diagnostic')), raw))
                return
            if item_kind in {"reasoning", "agent_message", "message"}:
                text = item.get("text", item.get("delta", ""))
                if isinstance(text, str) and text:
                    self.on_event(_event("reasoning" if item_kind == "reasoning" else "message", text, raw))
                return
            # Unknown executable item types are not silently allowed.
            self.on_event(_event("tool", f"Disallowed item: {item_kind}", raw))
            raise ToolPolicyViolation(f"tool_policy_violation: {item_kind or 'unknown item'}")
        if any(token in kind.lower() for token in ("tool", "command", "file_change", "function_call", "web_search")):
            self.on_event(_event("tool", f"Disallowed event: {kind}", raw))
            raise ToolPolicyViolation(f"tool_policy_violation: {kind}")
        if kind in {"error", "turn.failed", "response.failed"}:
            error = raw.get("error", raw)
            message = error.get("message", kind) if isinstance(error, dict) else str(error)
            self.failed = str(message)
            self.on_event(_event("error", self.failed, raw))
        elif kind in {"turn.completed", "response.completed"}:
            self.turn_completed = True
            self.on_event(_event("status", kind, raw))
        elif "reasoning_summary" in kind and isinstance(raw.get("delta"), str):
            self.on_event(_event("reasoning", raw["delta"], raw))
        else:
            self.on_event(_event("status", kind or "provider_event", raw))


def _process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _create_process_job(process: subprocess.Popen) -> Any:
    """A Windows job closes descendant processes even if their parent exits."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise GatewayError(f"Cannot create Windows process job: {ctypes.get_last_error()}")
    limits = ExtendedLimits()
    limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise GatewayError(f"Cannot attach Codex to its Windows process job: {error}")

    class Job:
        def close(self) -> None:
            nonlocal handle
            if handle:
                kernel.CloseHandle(handle)
                handle = None

    return Job()


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Numeric PID from this Popen only; no shell interpolation or name matching.
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise GatewayError("Unable to confirm Codex process cleanup") from exc


class CodexGateway:
    def __init__(self, executable: str | None = None, timeout_seconds: float = 1800) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.executable = self._resolve_executable(executable)
        self.timeout_seconds = float(timeout_seconds)
        self._feature_flags: tuple[str, ...] | None = None

    @staticmethod
    def _resolve_executable(executable: str | None) -> str:
        if executable:
            selected = Path(shutil.which(executable) or executable)
        else:
            selected = Path(shutil.which("codex.exe" if os.name == "nt" else "codex")
                            or shutil.which("codex.cmd") or "codex")
        if os.name == "nt" and selected.suffix.lower() in {".cmd", ".ps1"}:
            npm_root = selected.parent / "node_modules" / "@openai" / "codex"
            matches = list(npm_root.glob("node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"))
            if len(matches) != 1:
                raise GatewayError("Use a native codex.exe path; cannot safely resolve npm wrapper")
            selected = matches[0]
        return str(selected)

    def _preflight(self) -> tuple[str, ...]:
        if self._feature_flags is not None:
            return self._feature_flags
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            help_result = subprocess.run([self.executable, "exec", "--help"], capture_output=True,
                                         text=True, encoding="utf-8", errors="replace", timeout=20, **options)
            features_result = subprocess.run([self.executable, "features", "list"], capture_output=True,
                                             text=True, encoding="utf-8", errors="replace", timeout=20, **options)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GatewayError("Codex safety preflight failed before model invocation") from exc
        required_options = ("--json", "--output-schema", "--output-last-message", "--strict-config",
                            "--ignore-user-config", "--ignore-rules", "--ephemeral", "--sandbox")
        if help_result.returncode or any(option not in help_result.stdout for option in required_options):
            raise GatewayError("Installed Codex lacks required isolation or output options")
        available = {line.split()[0] for line in features_result.stdout.splitlines() if line.split()}
        if features_result.returncode or not _REQUIRED_FEATURES.issubset(available):
            raise GatewayError("Installed Codex cannot disable the required tool features")
        self._feature_flags = tuple(name for name in _DISABLED_FEATURES if name in available)
        return self._feature_flags

    def _command(self, schema_path: Path, response_path: Path, feature_flags: tuple[str, ...]) -> list[str]:
        command = [self.executable, "exec", "--json", "--strict-config", "--ignore-user-config",
                   "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                   "--color", "never", "-m", MODEL]
        settings = (
            f'model_reasoning_effort="{EFFORT}"', 'model_reasoning_summary="concise"',
            'model_provider="openai"',
            'approval_policy="never"', 'approvals_reviewer="user"', 'web_search="disabled"',
            'agents.enabled=false', 'mcp_servers={}',
            'project_doc_max_bytes=0', 'shell_environment_policy.inherit="none"',
            'hide_agent_reasoning=false', 'history.persistence="none"',
        )
        for setting in settings:
            command.extend(["-c", setting])
        for feature in feature_flags:
            command.extend(["--disable", feature])
        command.extend(["--output-schema", str(schema_path), "-o", str(response_path), "-"])
        return command

    def run(self, *, prompt: str, schema: dict, workdir: Path,
            on_event: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(schema, dict):
            raise ValueError("A non-empty prompt and JSON schema object are required")
        if cancelled():
            raise GatewayCancelled("Cancelled before model invocation")
        features = self._preflight()
        if cancelled():
            raise GatewayCancelled("Cancelled during safety preflight")
        workdir = Path(workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        paths = {name: workdir / filename for name, filename in {
            "schema": "output_schema.json", "response": "response.json",
            "events": "codex_events.jsonl", "stderr": "codex_stderr.log",
            "request": "codex_request.json"}.items()}
        if any(path.exists() for path in paths.values()):
            raise GatewayError("Invocation artifacts already exist; use a new attempt directory")
        paths["schema"].write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        command = self._command(paths["schema"], paths["response"], features)
        paths["request"].write_text(json.dumps({"command": command, "model": MODEL, "effort": EFFORT,
            "tool_policy": "disabled CLI surfaces; read-only; abort on any tool event; not OS-level holdout isolation"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        parser = _EventParser(on_event)
        started = time.monotonic()
        process = None
        process_job = None
        threads: list[threading.Thread] = []
        channel: queue.Queue = queue.Queue()
        env = os.environ.copy()
        for name in list(env):
            if name.startswith("OPENAI_") or name.startswith("CODEX_EXEC_") or name in {"CODEX_THREAD_ID", "CODEX_PARENT_THREAD_ID"}:
                env.pop(name, None)
        try:
            # No inherited project config, AGENTS.md, or research files in the child cwd.
            with tempfile.TemporaryDirectory(prefix="quanta-meta-codex-") as isolated_cwd, \
                    paths["events"].open("x", encoding="utf-8") as events_file, \
                    paths["stderr"].open("x", encoding="utf-8") as stderr_file:
                process = subprocess.Popen(command, cwd=isolated_cwd, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, **_process_options())
                process_job = _create_process_job(process)

                def read_stdout() -> None:
                    try:
                        for line in process.stdout:
                            events_file.write(line)
                            events_file.flush()
                            channel.put(line)
                    except Exception as exc:
                        channel.put(exc)
                    finally:
                        channel.put(None)

                def read_stderr() -> None:
                    for line in process.stderr:
                        stderr_file.write(line)
                        stderr_file.flush()

                def write_stdin() -> None:
                    try:
                        process.stdin.write(prompt)
                        process.stdin.close()
                    except (BrokenPipeError, OSError, ValueError):
                        # Exit status/stderr remain the authoritative failure report.
                        pass

                for target in (read_stdout, read_stderr, write_stdin):
                    thread = threading.Thread(target=target, daemon=True)
                    thread.start()
                    threads.append(thread)
                stdout_done = False
                try:
                    while not stdout_done or process.poll() is None:
                        if cancelled():
                            raise GatewayCancelled("Codex invocation cancelled")
                        if time.monotonic() - started > self.timeout_seconds:
                            raise GatewayTimeout(f"Codex invocation exceeded {self.timeout_seconds:g}s")
                        try:
                            line = channel.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        if line is None:
                            stdout_done = True
                            continue
                        if isinstance(line, Exception):
                            raise GatewayError("Failed to preserve Codex JSONL output") from line
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise GatewayError("Codex emitted invalid JSONL; raw output was retained") from exc
                        if not isinstance(raw, dict):
                            raise GatewayError("Codex event was not a JSON object")
                        parser.feed(raw)
                    process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        _kill_process_tree(process)
                    if process_job is not None:
                        process_job.close()
                    for thread in threads:
                        thread.join(timeout=2)
                if process.returncode != 0:
                    raise GatewayError(f"Codex exited with status {process.returncode}; see codex_stderr.log")
                if parser.failed:
                    raise GatewayError(parser.failed)
                if not parser.turn_completed:
                    raise GatewayError("Codex exited without a completed turn")
            try:
                response = json.loads(paths["response"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise GatewayError("Codex did not save a valid final JSON response") from exc
            if not isinstance(response, dict):
                raise GatewayError("Final response must be a JSON object")
            verified = parser.reported_model == MODEL and parser.reported_effort == EFFORT
            verification = ("Runtime session events reported the requested model and effort."
                if verified else "Request locked to gpt-6-astra/xhigh; provider events did not confirm both settings.")
            verification += " CLI tool surfaces disabled; no tool event observed. This is not proof of OS-level isolation or an empty provider tool list."
            return {"response": response, "usage": parser.usage,
                    "duration_seconds": time.monotonic() - started,
                    "model": MODEL, "effort": EFFORT, "model_verified": verified,
                    "verification": verification, "command": command}
        except BaseException as exc:
            if process is not None and process.poll() is None:
                _kill_process_tree(process)
            if process_job is not None:
                process_job.close()
            if isinstance(exc, Exception):
                setattr(exc, "usage", dict(parser.usage))
                on_event(_event("error", str(exc), {"usage": dict(parser.usage)}))
            raise


__all__ = ["CodexGateway", "GatewayError", "GatewayCancelled", "GatewayTimeout",
           "ToolPolicyViolation", "ModelPolicyViolation"]
