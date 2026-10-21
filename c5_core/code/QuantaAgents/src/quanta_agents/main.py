from __future__ import annotations

import faulthandler
import io
import os
import sys
from pathlib import Path

# Force a non-GUI matplotlib backend to avoid Qt teardown issues in headless runs.
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quanta_agents.experiment_runner import run_experiments
from quanta_agents.state import WorkflowState, get_hypothesis


class _TeeTextIO(io.TextIOBase):
    def __init__(self, *streams: io.TextIOBase) -> None:
        self._streams = streams

    def write(self, s: str) -> int:
        for stream in self._streams:
            stream.write(s)
        return len(s)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _is_truthy_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(0, value)


def _configure_runtime() -> None:
    if _is_truthy_env(os.getenv("QUANTA_ENABLE_FAULTHANDLER"), default=True):
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            pass


def _configure_runtime_logging(project_root: Path) -> None:
    trace_root = project_root / "experiment_traces"
    trace_root.mkdir(parents=True, exist_ok=True)
    log_file = Path(os.getenv("QUANTA_RUNTIME_LOG_FILE", str(trace_root / "terminal.log"))).expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        handle = log_file.open("a", encoding="utf-8")
    except Exception:
        return

    if isinstance(sys.stdout, io.TextIOBase):
        sys.stdout = _TeeTextIO(sys.stdout, handle)
    if isinstance(sys.stderr, io.TextIOBase):
        sys.stderr = _TeeTextIO(sys.stderr, handle)


def _print_success_result(source_file: str, final_state: WorkflowState) -> None:
    print(f"=== [{source_file}] Manager Notes ===")
    print(str(final_state["manager_notes"]))
    print()

    print(f"=== [{source_file}] Final Hypothesis ===")
    print(get_hypothesis(final_state))
    print()

    print(f"=== [{source_file}] Final Backtest Summary ===")
    test_result = final_state["test_result"]
    if test_result is not None:
        print(test_result.summary)
    else:
        print("No backtest result")
    print()

    print(f"=== [{source_file}] Execution History ===")
    history_items = [str(item) for item in final_state["history"]]
    history_tail = _get_non_negative_int_env("QUANTA_HISTORY_TAIL", default=20)

    if history_tail == 0:
        print(f"(suppressed {len(history_items)} history entries)")
        print()
        return

    items_to_print = history_items[-history_tail:]
    if len(history_items) > len(items_to_print):
        hidden = len(history_items) - len(items_to_print)
        print(
            f"(truncated: showing last {len(items_to_print)} of {len(history_items)} entries, {hidden} hidden)"
        )

    for item in items_to_print:
        print(f"- {item}")
    print()


def main() -> None:
    _configure_runtime()

    project_root = Path(__file__).resolve().parents[2]
    _configure_runtime_logging(project_root)
    experiments_dir = project_root / "experiments"

    outcomes = run_experiments(experiments_dir=experiments_dir)
    if not outcomes:
        print("=== Workflow Failed ===")
        print(f"No valid idea files found in: {experiments_dir}")
        raise SystemExit(1)

    success_count = 0
    failure_count = 0

    for outcome in outcomes:
        if outcome.success and outcome.final_state is not None:
            success_count += 1
            _print_success_result(outcome.source_file, outcome.final_state)
            continue

        failure_count += 1
        print(f"=== [{outcome.source_file}] Workflow Failed ===")
        print(outcome.error)
        print()

    print("=== Experiments Summary ===")
    print(f"Total: {len(outcomes)}")
    print(f"Success: {success_count}")
    print(f"Failure: {failure_count}")

    if failure_count > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
