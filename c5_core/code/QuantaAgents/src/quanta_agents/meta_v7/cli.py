"""Usable application entry point; no model-authored experiment Python needed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

from .controller import V7ResearchKernel as ResearchKernel
from quanta_agents.research_kernel.store import serial, write_json


def run_supervised(kernel):
    from quanta_agents.meta_v6.jobs import run_job
    folder = kernel.root / "jobs" / ("job_" + uuid4().hex)
    repo = Path(__file__).resolve().parents[3]
    result_path = folder / "worker_result.json"
    result = run_job([sys.executable, "-m", "quanta_agents.meta_v7.cli", "run-worker",
                      "--root", str(kernel.root), "--result", str(result_path)],
                     cwd=repo, output_dir=folder,
                     timeout_seconds=kernel.config["budget"]["job_timeout_seconds"],
                     max_output_bytes=128 * 1024**2, cancel_file=kernel.root / "cancel.request",
                     env={"PYTHONPATH": str(repo / "src"), "PYTHONIOENCODING": "utf-8"})
    answer = {"job_status": result.get("status"), "job_directory": str(folder),
              "process_outcome": result}
    if result.get("status") == "completed":
        answer["result"] = json.loads(result_path.read_text(encoding="utf-8"))
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description="QuantaAgents persistent factor research program")
    parser.add_argument("command", choices=["init", "register", "import-v6", "import-catalog", "action",
        "import-calendar", "status", "context", "evidence", "run", "run-worker", "iterate", "recover-call", "retry", "stop"])
    parser.add_argument("--root", required=True)
    parser.add_argument("--file")
    parser.add_argument("--source")
    parser.add_argument("--source-name", default="factor_calendar")
    parser.add_argument("--id")
    parser.add_argument("--pointer", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--model-timeout", type=int, default=900)
    parser.add_argument("--result")
    args = parser.parse_args(argv)
    kernel = ResearchKernel(args.root)
    if args.command in {"init", "register", "action"}:
        if not args.file:
            parser.error("--file is required for this command")
        payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    if args.command == "init":
        result = kernel.initialize(payload)
    elif args.command == "register":
        result = [kernel.assets.register(row) for row in (payload if isinstance(payload, list) else [payload])]
    elif args.command == "import-v6":
        result = kernel.assets.import_v6(Path(args.source))
    elif args.command == "import-catalog":
        result = kernel.assets.import_metadata(Path(args.source), args.source_name)
    elif args.command == "action":
        result = kernel.apply_action(payload, action_id=args.id)
    elif args.command == "import-calendar":
        result = kernel.assets.ingest_calendar(Path(args.source))
    elif args.command == "status":
        result = kernel.status()
    elif args.command == "context":
        from .protocol import build_context
        result = build_context(kernel)
    elif args.command == "evidence":
        result = kernel.store.evidence(args.id, pointer=args.pointer, limit=args.limit, offset=args.offset)
    elif args.command == "run-worker":
        result = kernel.execute_pending()
    elif args.command == "run":
        result = run_supervised(kernel)
    elif args.command == "retry":
        result = kernel.retry_run(args.id)
    elif args.command == "stop":
        result = kernel.apply_action({"action": "stop", "reason": "User requested stop", "payload_json": "{}"})
    elif args.command == "recover-call":
        from .model import recover_model_call
        result = recover_model_call(kernel, args.id)
    else:
        from .model import model_step
        if args.steps < 1 or args.steps > kernel.config["budget"]["max_model_calls"]:
            parser.error("--steps must be within the study model-call budget")
        result = []
        # Finish existing queue before consuming another research decision.
        if kernel.pending():
            resumed = run_supervised(kernel)
            result.append(resumed)
            if resumed["job_status"] != "completed":
                if args.result:
                    write_json(args.result, result)
                print(serial(result))
                return result
        for _ in range(args.steps):
            decision = model_step(kernel, timeout_seconds=args.model_timeout)
            result.append(decision)
            if decision.get("status") in {"stopped", "model_budget_exhausted"}:
                break
            if kernel.pending():
                execution = run_supervised(kernel)
                result.append(execution)
                if execution["job_status"] != "completed":
                    break
    if args.result:
        write_json(args.result, result)
    # Raw original model sessions are preserved separately, not echoed to UI.
    print(serial(result))
    return result


if __name__ == "__main__":
    main()
