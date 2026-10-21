from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACE_ROOT = PROJECT_ROOT / "experiment_traces"
WEB_ROOT = Path(__file__).resolve().parent / "web"
RUNTIME_LOG_FILE = TRACE_ROOT / "terminal.log"
EXPERIMENTS_ROOT = PROJECT_ROOT / "experiments"

STAGE_ORDER = ["manager", "hypothesis", "strategy", "validate", "test", "done"]
AGENT_TO_STAGE = {
    "manageragent": "manager",
    "hypothesisagent": "hypothesis",
    "strategyagent": "strategy",
    "validateagent": "validate",
    "strategytester": "test",
}


@dataclass
class ExperimentSummary:
    experiment_id: str
    path: Path
    last_updated: str
    source_file: str
    source_path: Path | None


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_epoch(name: str) -> int | None:
    if not name.startswith("epoch_"):
        return None
    try:
        return int(name.split("_", 1)[1])
    except Exception:
        return None


def _list_experiments() -> list[ExperimentSummary]:
    if not TRACE_ROOT.exists():
        return []

    experiments: list[ExperimentSummary] = []
    for item in TRACE_ROOT.iterdir():
        if not item.is_dir():
            continue
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        experiments.append(
            ExperimentSummary(
                experiment_id=item.name,
                path=item,
                last_updated=_iso_utc(mtime),
                source_file="",
                source_path=None,
            )
        )

    for summary in experiments:
        source_path = _resolve_source_file_for_experiment(summary.experiment_id)
        summary.source_path = source_path
        summary.source_file = source_path.name if source_path is not None else ""

    experiments.sort(key=lambda x: x.last_updated, reverse=True)
    return experiments


def _resolve_source_file_for_experiment(experiment_id: str) -> Path | None:
    if not EXPERIMENTS_ROOT.exists() or not EXPERIMENTS_ROOT.is_dir():
        return None

    candidates = sorted(
        [
            path
            for path in EXPERIMENTS_ROOT.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ],
        key=lambda p: len(p.stem),
        reverse=True,
    )

    for path in candidates:
        stem = path.stem.replace(" ", "_")
        if experiment_id.startswith(f"exp_{stem}_"):
            return path

    return None


def _list_source_files() -> list[dict[str, Any]]:
    if not EXPERIMENTS_ROOT.exists() or not EXPERIMENTS_ROOT.is_dir():
        return []

    source_files: list[dict[str, Any]] = []
    for path in sorted(EXPERIMENTS_ROOT.rglob("*"), key=lambda item: str(item).lower()):
        if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        try:
            relative_path = path.resolve().relative_to(PROJECT_ROOT.resolve())
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        source_files.append(
            {
                "name": path.name,
                "relative_path": str(relative_path).replace("\\", "/"),
                "absolute_path": str(path.resolve()),
                "last_updated": _iso_utc(mtime),
            }
        )

    return source_files


def _resolve_source_file(path: str) -> Path:
    relative = Path(path)
    target = (PROJECT_ROOT / relative).resolve()
    experiments_root_resolved = EXPERIMENTS_ROOT.resolve()
    if experiments_root_resolved not in target.parents and target != experiments_root_resolved:
        raise HTTPException(status_code=400, detail="path must be under experiments")
    if target.suffix.lower() not in {".yaml", ".yml"}:
        raise HTTPException(status_code=400, detail="path must reference a yaml file")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="source file not found")
    return target


def _source_file_payload(source_path: Path) -> dict[str, Any]:
    relative_path = source_path.resolve().relative_to(PROJECT_ROOT.resolve())
    content = source_path.read_text(encoding="utf-8", errors="replace")
    return {
        "source_file": source_path.name,
        "source_relative_path": str(relative_path).replace("\\", "/"),
        "source_absolute_path": str(source_path.resolve()),
        "content": content,
    }


def _get_latest_epoch_dir(experiment_dir: Path) -> tuple[str, Path] | None:
    epoch_dirs: list[tuple[int, Path]] = []
    for epochs_root in experiment_dir.glob("epochs_*"):
        if not epochs_root.is_dir():
            continue
        for epoch_dir in epochs_root.iterdir():
            if not epoch_dir.is_dir():
                continue
            idx = _parse_epoch(epoch_dir.name)
            if idx is None:
                continue
            epoch_dirs.append((idx, epoch_dir))

    if not epoch_dirs:
        return None

    epoch_dirs.sort(key=lambda x: x[0], reverse=True)
    latest_idx, latest_dir = epoch_dirs[0]
    return f"epoch_{latest_idx:03d}", latest_dir


def _latest_agent_stage_from_files(epoch_dir: Path) -> tuple[str | None, str | None]:
    latest_file: Path | None = None
    latest_mtime = -1.0

    for agent_dir in epoch_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        for file_path in agent_dir.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_file = file_path

    if latest_file is None:
        return None, None

    try:
        relative_parts = latest_file.relative_to(epoch_dir).parts
        agent_slug = relative_parts[0] if relative_parts else ""
    except Exception:
        agent_slug = ""
    return AGENT_TO_STAGE.get(agent_slug, None), _iso_utc(latest_mtime)


def _tail_lines(path: Path, max_lines: int = 300) -> list[str]:
    if not path.exists() or not path.is_file():
        return []

    q: deque[str] = deque(maxlen=max_lines)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                q.append(line.rstrip("\n"))
    except OSError:
        return []

    return list(q)


def _latest_stage_from_terminal(experiment_id: str, terminal_lines: list[str]) -> tuple[str | None, str | None]:
    pattern = re.compile(
        r"^\[(?P<agent>[^\]]+)\]\[exp=(?P<exp>[^\]]+)\]\[epoch=(?P<epoch>\d+)\]\s+(?P<msg>.*)$"
    )

    for line in reversed(terminal_lines):
        match = pattern.match(line)
        if not match:
            continue
        if match.group("exp") != experiment_id:
            continue

        agent = match.group("agent").strip().lower()
        msg = match.group("msg").strip().lower()

        if agent == "manageragent":
            phase_match = re.search(r"phase=([a-z_]+)", msg)
            if phase_match:
                phase = phase_match.group(1)
                if phase in STAGE_ORDER:
                    return phase, line
            return "manager", line

        stage = AGENT_TO_STAGE.get(agent)
        if stage:
            return stage, line

    return None, None


def _get_completed_stages(epoch_dir: Path) -> list[str]:
    completed: list[str] = []
    for slug, stage in (
        ("hypothesisagent", "hypothesis"),
        ("strategyagent", "strategy"),
        ("validateagent", "validate"),
        ("strategytester", "test"),
    ):
        agent_dir = epoch_dir / slug
        if not agent_dir.exists():
            continue
        has_file = any(path.is_file() for path in agent_dir.rglob("*"))
        if has_file:
            completed.append(stage)

    return completed


def _build_workflow_status(experiment_dir: Path, terminal_lines: list[str]) -> dict[str, Any]:
    latest_epoch = _get_latest_epoch_dir(experiment_dir)
    if latest_epoch is None:
        return {
            "active_stage": "manager",
            "latest_epoch": None,
            "completed_stages": [],
            "last_event_at": None,
            "terminal_hint": None,
        }

    latest_epoch_name, epoch_dir = latest_epoch
    from_terminal, terminal_hint = _latest_stage_from_terminal(experiment_dir.name, terminal_lines)
    from_files, last_event_at = _latest_agent_stage_from_files(epoch_dir)
    active = from_terminal or from_files or "manager"

    return {
        "active_stage": active,
        "latest_epoch": latest_epoch_name,
        "completed_stages": _get_completed_stages(epoch_dir),
        "last_event_at": last_event_at,
        "terminal_hint": terminal_hint,
    }


def _safe_relative_to_trace_root(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(TRACE_ROOT.resolve())
    except Exception:
        return ""
    return str(rel).replace("\\", "/")


def _build_tree(root: Path, max_depth: int = 12, depth: int = 0) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": root.name,
        "type": "dir" if root.is_dir() else "file",
        "relative_path": _safe_relative_to_trace_root(root),
        "children": [],
    }

    if not root.is_dir() or depth >= max_depth:
        if root.is_dir() and depth >= max_depth:
            node["truncated"] = True
        return node

    children: list[dict[str, Any]] = []
    items = sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    for item in items:
        children.append(_build_tree(item, max_depth=max_depth, depth=depth + 1))

    node["children"] = children
    return node


def _build_snapshot() -> dict[str, Any]:
    terminal_lines = _tail_lines(RUNTIME_LOG_FILE, max_lines=300)
    experiments = _list_experiments()

    exp_payload: list[dict[str, Any]] = []
    workflow_map: dict[str, dict[str, Any]] = {}

    for summary in experiments:
        status = _build_workflow_status(summary.path, terminal_lines)
        exp_payload.append(
            {
                "experiment_id": summary.experiment_id,
                "last_updated": summary.last_updated,
                "active_stage": status.get("active_stage", "manager"),
                "source_file": summary.source_file,
                "source_relative_path": str(summary.source_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/") if summary.source_path else "",
            }
        )
        workflow_map[summary.experiment_id] = status

    return {
        "type": "snapshot",
        "experiments": exp_payload,
        "workflow_by_experiment": workflow_map,
        "terminal_lines": terminal_lines,
    }


def _resolve_trace_file(path: str) -> Path:
    target = (TRACE_ROOT / path).resolve()
    trace_root_resolved = TRACE_ROOT.resolve()
    if trace_root_resolved not in target.parents and target != trace_root_resolved:
        raise HTTPException(status_code=400, detail="path must be under experiment_traces")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return target


app = FastAPI(title="QuantaAgents Monitor")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/experiments")
def list_experiments() -> dict[str, Any]:
    payload = [
        {
            "experiment_id": exp.experiment_id,
            "last_updated": exp.last_updated,
            "source_file": exp.source_file,
            "source_relative_path": str(exp.source_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/") if exp.source_path else "",
        }
        for exp in _list_experiments()
    ]
    return {"experiments": payload}


@app.get("/api/source-files")
def list_source_files() -> dict[str, Any]:
    return {"files": _list_source_files()}


@app.get("/api/source-file")
def get_source_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    source_path = _resolve_source_file(path)
    return _source_file_payload(source_path)


@app.put("/api/source-file")
def update_source_file(
    path: str = Query(..., min_length=1),
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    source_path = _resolve_source_file(path)
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    source_path.write_text(content, encoding="utf-8")
    return {
        "saved": True,
        "source_file": source_path.name,
        "source_relative_path": str(source_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/"),
        "source_absolute_path": str(source_path.resolve()),
    }


@app.get("/api/experiments/{experiment_id}/source")
def get_experiment_source(experiment_id: str) -> dict[str, Any]:
    source_path = _resolve_source_file_for_experiment(experiment_id)
    if source_path is None or not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="source file not found")

    return {"experiment_id": experiment_id, **_source_file_payload(source_path)}


@app.put("/api/experiments/{experiment_id}/source")
def update_experiment_source(
    experiment_id: str,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    source_path = _resolve_source_file_for_experiment(experiment_id)
    if source_path is None or not source_path.exists() or not source_path.is_file():
        raise HTTPException(status_code=404, detail="source file not found")

    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    source_path.write_text(content, encoding="utf-8")
    return {
        "experiment_id": experiment_id,
        "saved": True,
        "source_file": source_path.name,
        "source_relative_path": str(source_path.resolve().relative_to(PROJECT_ROOT.resolve())).replace("\\", "/"),
        "source_absolute_path": str(source_path.resolve()),
    }


@app.get("/api/experiments/{experiment_id}/tree")
def experiment_tree(experiment_id: str) -> dict[str, Any]:
    target = TRACE_ROOT / experiment_id
    if not target.exists() or not target.is_dir():
        return {"experiment_id": experiment_id, "tree": {"name": experiment_id, "type": "dir", "children": []}}
    return {"experiment_id": experiment_id, "tree": _build_tree(target)}


@app.get("/api/file")
def read_file_text(path: str = Query(..., min_length=1)) -> dict[str, Any]:
    target = _resolve_trace_file(path)

    max_chars = 200_000
    text = target.read_text(encoding="utf-8", errors="replace")
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return {
        "path": _safe_relative_to_trace_root(target),
        "absolute_path": str(target),
        "truncated": truncated,
        "content": text,
    }


@app.get("/api/file/raw")
def read_file_raw(path: str = Query(..., min_length=1)) -> FileResponse:
    target = _resolve_trace_file(path)
    return FileResponse(target)


@app.get("/api/experiments/{experiment_id}/workflow")
def experiment_workflow(experiment_id: str) -> dict[str, Any]:
    target = TRACE_ROOT / experiment_id
    if not target.exists() or not target.is_dir():
        return {
            "experiment_id": experiment_id,
            "active_stage": "manager",
            "latest_epoch": None,
            "completed_stages": [],
            "last_event_at": None,
        }

    terminal_lines = _tail_lines(RUNTIME_LOG_FILE, max_lines=300)
    status = _build_workflow_status(target, terminal_lines)
    return {"experiment_id": experiment_id, **status}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            snapshot = _build_snapshot()
            await ws.send_text(json.dumps(snapshot, ensure_ascii=False))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


def main() -> None:
    import uvicorn

    port = int(os.getenv("QUANTA_AGENTS_WEBAPP_PORT", os.getenv("PORT", "8000")))
    uvicorn.run(
        "quanta_agents.webapp:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
