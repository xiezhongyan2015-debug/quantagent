from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from quanta_agents.state import WorkflowState


def _slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9_\-]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_") or "unknown"


def _resolve_experiment_id(state: WorkflowState) -> str:
    spec = state.get("experiment_spec", {})
    value = spec.get("experiment_id") if isinstance(spec, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown_experiment"


def get_trace_run_dir(state: WorkflowState) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    trace_root = project_root / "experiment_traces"
    experiment_id = _slugify(_resolve_experiment_id(state))
    max_epochs = int(state.get("max_epochs", 1))
    run_dir = trace_root / experiment_id / f"epochs_{max_epochs}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def get_agent_trace_dir(
    state: WorkflowState,
    *,
    agent_name: str,
    stage: str | None = None,
) -> Path:
    run_dir = get_trace_run_dir(state)
    epoch_index = int(state.get("epoch_index", 1))
    trace_dir = run_dir / f"epoch_{epoch_index:03d}" / _slugify(agent_name)

    if trace_dir.name in {"strategyagent", "validateagent"}:
        round_index = max(int(state.get("strategy_validate_round", 1)), 1)
        trace_dir = trace_dir / f"round_{round_index:03d}"

    if isinstance(stage, str) and stage.strip():
        trace_dir = trace_dir / _slugify(stage)

    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir


def write_trace_text(
    state: WorkflowState,
    *,
    agent_name: str,
    stage: str,
    text: str,
    attempt: int | None = None,
) -> Path:
    trace_dir = get_agent_trace_dir(state, agent_name=agent_name, stage=stage)

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    if isinstance(attempt, int):
        file_name = f"attempt_{attempt:02d}_{ts}.txt"
    else:
        file_name = f"{ts}.txt"
    file_path = trace_dir / file_name

    file_path.write_text(text, encoding="utf-8")
    return file_path


def write_trace_json(
    state: WorkflowState,
    *,
    agent_name: str,
    stage: str,
    payload: dict[str, Any],
    attempt: int | None = None,
) -> Path:
    if _slugify(stage) == "run_start":
        return get_trace_run_dir(state)

    def _humanize_multiline(value: Any) -> Any:
        if isinstance(value, str) and "\n" in value:
            return value.splitlines()
        if isinstance(value, dict):
            return {str(key): _humanize_multiline(val) for key, val in value.items()}
        if isinstance(value, list):
            return [_humanize_multiline(item) for item in value]
        return value

    def _to_json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): _to_json_safe(val) for key, val in value.items()}
        if isinstance(value, list):
            return [_to_json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [_to_json_safe(item) for item in value]
        if isinstance(value, set):
            return [_to_json_safe(item) for item in value]

        # Normalize numpy/pandas scalar-like values (e.g. numpy.bool_, numpy.float64).
        item_method = getattr(value, "item", None)
        if callable(item_method):
            try:
                return _to_json_safe(item_method())
            except Exception:
                pass

        return str(value)

    normalized_payload: dict[str, Any] = payload
    if _slugify(stage) in {"code_execution", "structured_output"}:
        normalized_payload = _humanize_multiline(payload)

    wrapped: dict[str, Any] = {
        "logged_at": datetime.now(tz=timezone.utc).isoformat(),
        "agent": agent_name,
        "stage": stage,
        "epoch_index": int(state.get("epoch_index", 1)),
        "strategy_validate_round": int(state.get("strategy_validate_round", 1)),
        "payload": normalized_payload,
    }
    safe_wrapped = _to_json_safe(wrapped)
    try:
        text = json.dumps(safe_wrapped, ensure_ascii=False, indent=2)
    except Exception as exc:
        fallback_payload = {
            "logged_at": datetime.now(tz=timezone.utc).isoformat(),
            "agent": agent_name,
            "stage": stage,
            "epoch_index": int(state.get("epoch_index", 1)),
            "strategy_validate_round": int(state.get("strategy_validate_round", 1)),
            "trace_serialize_error": str(exc),
            "payload_repr": repr(normalized_payload),
        }
        text = json.dumps(fallback_payload, ensure_ascii=False, indent=2, default=str)
    trace_dir = get_agent_trace_dir(state, agent_name=agent_name, stage=stage)

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    if isinstance(attempt, int):
        file_name = f"attempt_{attempt:02d}_{ts}.json"
    else:
        file_name = f"{ts}.json"
    file_path = trace_dir / file_name

    file_path.write_text(text, encoding="utf-8")
    return file_path


def print_agent_progress(state: WorkflowState, *, agent_name: str, message: str, output: str | None = None) -> None:
    experiment_id = _resolve_experiment_id(state)
    epoch_index = int(state.get("epoch_index", 1))
    prefix_parts = [f"[{agent_name}]", f"[exp={experiment_id}]", f"[epoch={epoch_index}]"]
    if agent_name in {"StrategyAgent", "ValidateAgent"}:
        round_index = max(int(state.get("strategy_validate_round", 1)), 1)
        prefix_parts.append(f"[round={round_index}]")
    prefix = "".join(prefix_parts)
    try:
        print(f"{prefix} {message}")
        if isinstance(output, str) and output.strip():
            compact = output.strip().replace("\n", " ")
            if len(compact) > 320:
                compact = compact[:317] + "..."
            print(f"{prefix} output: {compact}")
    except (OSError, ValueError):
        # 进度文字写不出去时，不能影响研究或回测本身。
        return
