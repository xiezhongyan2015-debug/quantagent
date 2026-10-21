from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_RAW_MINUTE_DATA_DIR = Path(
    r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data"
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# 直接运行恢复脚本时，默认沿用分钟研究的模型和重试设置；外部显式设置仍优先。
os.environ.setdefault("QUANTA_LLM_PROVIDER", "codex_exec")
os.environ.setdefault("CODEX_EXEC_MODEL", "gpt-5.6-sol")
os.environ.setdefault("CODEX_EXEC_REASONING_EFFORT", "ultra")
os.environ.setdefault("CODEX_EXEC_MULTI_AGENT", "false")
os.environ.setdefault("CODEX_EXEC_TIMEOUT_SECONDS", "1800")
os.environ.setdefault("AGENT_MAX_RETRIES", "2")
os.environ.setdefault("MAX_STRATEGY_VALIDATE_ROUNDS", "3")
os.environ.setdefault("QUANTA_DATA_ENGINE", "research_parquet")
os.environ.setdefault("QUANTA_BACKTEST_DB_BACKEND", "event_parquet")

from quanta_agents.resume import (
    restore_latest_diagnostics_state_from_trace,
    restore_next_hypothesis_state_from_trace,
    restore_research_state_from_trace,
)
from quanta_agents.workflow import continue_workflow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从已有实验的研究诊断位置继续运行")
    parser.add_argument("--source-experiment", required=True)
    parser.add_argument(
        "--source-yaml",
        default="experiments/periodic_volume_minute.yaml",
    )
    parser.add_argument("--new-experiment")
    parser.add_argument("--max-epochs", type=int, default=3)
    parser.add_argument(
        "--resume-point",
        choices=("diagnostics", "latest-diagnostics", "next-hypothesis"),
        default="diagnostics",
        help="从旧诊断、最新 diagnose_only 请求或最近完成的开发期候选继续",
    )
    parser.add_argument(
        "--expected-candidate",
        help="仅在最近完成的开发期候选与该编号相同时继续，例如 candidate_003",
    )
    parser.add_argument(
        "--event-path",
        default="data_cache/periodic_active_buying/minute_events_2022_2025.parquet",
    )
    parser.add_argument(
        "--raw-minute-dir",
        default=os.environ.get(
            "QUANTA_RAW_MINUTE_DATA_DIR", str(DEFAULT_RAW_MINUTE_DATA_DIR)
        ),
        help="前复权原始分钟 Parquet 所在目录",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    event_path = Path(args.event_path)
    if not event_path.is_absolute():
        event_path = PROJECT_ROOT / event_path
    os.environ["QUANTA_EVENT_FEATURES_PATH"] = str(event_path)
    os.environ["QUANTA_MINUTE_EVENT_FEATURES_PATH"] = str(event_path)
    raw_minute_dir = Path(args.raw_minute_dir).expanduser()
    if not raw_minute_dir.is_absolute():
        raw_minute_dir = PROJECT_ROOT / raw_minute_dir
    os.environ["QUANTA_RAW_MINUTE_DATA_DIR"] = str(raw_minute_dir)
    os.environ["QUANTA_RUN_FINAL_TEST"] = "false"
    os.environ["QUANTA_ALLOW_FINAL_TEST"] = "false"

    if args.resume_point == "next-hypothesis":
        state = restore_next_hypothesis_state_from_trace(
            source_experiment_id=args.source_experiment,
            source_yaml=args.source_yaml,
            new_experiment_id=args.new_experiment,
            max_epochs=max(1, args.max_epochs),
            expected_candidate_id=args.expected_candidate,
        )
    elif args.resume_point == "latest-diagnostics":
        state = restore_latest_diagnostics_state_from_trace(
            source_experiment_id=args.source_experiment,
            source_yaml=args.source_yaml,
            new_experiment_id=args.new_experiment,
            max_epochs=max(1, args.max_epochs),
            expected_candidate_id=args.expected_candidate,
        )
    else:
        state = restore_research_state_from_trace(
            source_experiment_id=args.source_experiment,
            source_yaml=args.source_yaml,
            new_experiment_id=args.new_experiment,
            max_epochs=max(1, args.max_epochs),
        )
    print(
        json.dumps(
            {
                "resumed_from": args.source_experiment,
                "new_experiment_id": state["experiment_spec"]["experiment_id"],
                "epoch_index": state["epoch_index"],
                "phase": state["phase"],
                "resume_point": args.resume_point,
                "final_period_read": False,
                "llm_provider": os.environ.get("QUANTA_LLM_PROVIDER", ""),
                "llm_model": os.environ.get("CODEX_EXEC_MODEL", ""),
                "reasoning_effort": os.environ.get(
                    "CODEX_EXEC_REASONING_EFFORT", ""
                ),
                "raw_minute_dir": os.environ.get(
                    "QUANTA_RAW_MINUTE_DATA_DIR", ""
                ),
            },
            ensure_ascii=False,
        )
    )
    final_state = continue_workflow(state)
    print(
        json.dumps(
            {
                "experiment_id": final_state["experiment_spec"].get("experiment_id"),
                "phase": final_state.get("phase"),
                "epoch_index": final_state.get("epoch_index"),
                "quality_passed": final_state.get("quality_passed"),
                "research_trial_count": final_state.get("research_trial_count"),
                "diagnostic_round": final_state.get("diagnostic_round"),
                "manager_notes": final_state.get("manager_notes"),
                "final_test_count": final_state.get("final_test_count"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
