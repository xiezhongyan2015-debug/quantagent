from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
DEFAULT_EVENT_PATH = (
    PROJECT_ROOT
    / "data_cache"
    / "periodic_active_buying"
    / "minute_events_2022_2025.parquet"
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from quanta_agents.agents import ManagerAgent, StrategyTester
from quanta_agents.resume import restore_frozen_candidate_for_final_test
from quanta_agents.trace_logger import write_trace_json
from quanta_agents.workflow import _run_tester_step


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对已冻结候选只执行一次最终测试"
    )
    parser.add_argument("--source-experiment", required=True)
    parser.add_argument(
        "--source-yaml",
        default="experiments/periodic_volume_minute.yaml",
    )
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--expected-code-sha256", required=True)
    parser.add_argument("--new-experiment", required=True)
    parser.add_argument("--max-epochs", type=int, default=15)
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument(
        "--technical-retry-of",
        help="仅用于重试一次没有产出结果的技术错误",
    )
    return parser.parse_args(argv)


def run_final_test_only(state):
    """只调用最终测试和结果记录，不进入任何研究或写代码阶段。"""

    if state.get("phase") != "final_test":
        raise ValueError("冻结状态必须从 final_test 开始")
    if state.get("candidate_frozen") is not True:
        raise ValueError("候选尚未冻结")
    if state.get("ready_for_final") is not True:
        raise ValueError("候选尚未获准进行最终测试")
    if int(state.get("final_test_count", 0)) != 0:
        raise RuntimeError("该状态已经执行过最终测试")

    strategy_code = str(state.get("strategy_code", "")).replace("\r\n", "\n")
    expected_sha256 = str(
        state.get("experiment_spec", {}).get(
            "frozen_strategy_code_sha256", ""
        )
    ).strip().lower()
    actual_sha256 = hashlib.sha256(strategy_code.encode("utf-8")).hexdigest()
    if not expected_sha256 or actual_sha256 != expected_sha256:
        raise ValueError("运行前策略代码摘要检查失败")

    strategy_result = state.get("strategy_result", {})
    if not isinstance(strategy_result, dict):
        raise ValueError("冻结策略结果无效")
    frozen_params = deepcopy(strategy_result.get("params"))
    if not isinstance(frozen_params, dict) or not frozen_params:
        raise ValueError("冻结参数为空")
    frozen_strategy_output = deepcopy(strategy_result.get("strategy_output"))
    if not isinstance(frozen_strategy_output, dict) or not frozen_strategy_output:
        raise ValueError("冻结代码的 strategy_output 为空")
    output_weights_meta = frozen_strategy_output.get("output_weights_df")
    if not isinstance(output_weights_meta, dict):
        raise ValueError("冻结代码缺少 output_weights_df 输出说明")
    datetime_column = output_weights_meta.get("datetime_column")
    if not isinstance(datetime_column, str) or not datetime_column.strip():
        raise ValueError("冻结代码缺少输出时间列说明")

    experiment_spec = state.get("experiment_spec", {})
    if not isinstance(experiment_spec, dict):
        raise ValueError("最终测试配置无效")
    final_test_attempt = int(experiment_spec.get("final_test_attempt", 0))
    if final_test_attempt not in {1, 2}:
        raise ValueError("最终测试尝试次数无效")

    write_trace_json(
        state,
        agent_name="FinalTestRunner",
        stage="final_test_started",
        payload={
            "candidate_id": state.get("current_candidate_id", ""),
            "strategy_code_sha256": expected_sha256,
            "strategy_params": deepcopy(frozen_params),
            "strategy_output": deepcopy(frozen_strategy_output),
            "final_test_period": deepcopy(state.get("final_test_period", {})),
            "final_test_count_before_start": 0,
            "final_test_attempt": final_test_attempt,
            "technical_retry_of": str(
                experiment_spec.get("technical_retry_of", "")
            ).strip(),
        },
    )

    tested_state = _run_tester_step(
        StrategyTester(),
        state,
        period_kind="final_test",
    )
    if tested_state.get("phase") != "final_test":
        raise RuntimeError("最终测试意外跳到了其他阶段")
    tested_code = str(tested_state.get("strategy_code", "")).replace(
        "\r\n", "\n"
    )
    tested_sha256 = hashlib.sha256(tested_code.encode("utf-8")).hexdigest()
    if tested_sha256 != expected_sha256:
        raise RuntimeError("最终测试修改了冻结代码")
    tested_strategy_result = tested_state.get("strategy_result", {})
    if (
        not isinstance(tested_strategy_result, dict)
        or tested_strategy_result.get("params") != frozen_params
    ):
        raise RuntimeError("最终测试修改了冻结参数")
    if tested_strategy_result.get("strategy_output") != frozen_strategy_output:
        raise RuntimeError("最终测试修改了冻结输出说明")

    final_state = ManagerAgent().dispatch(tested_state)
    if final_state.get("phase") != "done":
        raise RuntimeError("最终测试完成后没有停止")
    if int(final_state.get("final_test_count", 0)) != 1:
        raise RuntimeError("最终测试次数不是1")
    return final_state


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    event_path = Path(args.event_path)
    if not event_path.is_absolute():
        event_path = PROJECT_ROOT / event_path
    event_path = event_path.resolve()
    if not event_path.exists() or not event_path.is_file():
        raise FileNotFoundError(f"分钟事件文件不存在: {event_path}")

    os.environ["QUANTA_DATA_ENGINE"] = "research_parquet"
    os.environ["QUANTA_BACKTEST_DB_BACKEND"] = "event_parquet"
    os.environ["QUANTA_EVENT_FEATURES_PATH"] = str(event_path)
    os.environ["QUANTA_MINUTE_EVENT_FEATURES_PATH"] = str(event_path)
    os.environ["QUANTA_RUN_FINAL_TEST"] = "true"
    os.environ["QUANTA_ALLOW_FINAL_TEST"] = "true"

    state = restore_frozen_candidate_for_final_test(
        source_experiment_id=args.source_experiment,
        source_yaml=args.source_yaml,
        expected_candidate_id=args.candidate,
        expected_code_sha256=args.expected_code_sha256,
        new_experiment_id=args.new_experiment,
        max_epochs=args.max_epochs,
        technical_retry_of=args.technical_retry_of,
    )
    print(
        json.dumps(
            {
                "experiment_id": state["experiment_spec"]["experiment_id"],
                "source_experiment": args.source_experiment,
                "candidate_id": state["current_candidate_id"],
                "phase": state["phase"],
                "strategy_code_sha256": state["experiment_spec"][
                    "frozen_strategy_code_sha256"
                ],
                "final_test_period": state["final_test_period"],
                "final_test_count": state["final_test_count"],
                "final_test_attempt": state["experiment_spec"][
                    "final_test_attempt"
                ],
                "technical_retry_of": state["experiment_spec"].get(
                    "technical_retry_of", ""
                ),
                "event_path": str(event_path),
            },
            ensure_ascii=False,
        )
    )

    final_state = run_final_test_only(state)
    final_result = final_state.get("final_test_result")
    print(
        json.dumps(
            {
                "experiment_id": final_state["experiment_spec"][
                    "experiment_id"
                ],
                "candidate_id": final_state["current_candidate_id"],
                "phase": final_state["phase"],
                "final_test_count": final_state["final_test_count"],
                "final_quality_passed": final_state.get(
                    "final_quality_passed"
                ),
                "result_produced": final_result is not None,
                "summary": str(getattr(final_result, "summary", "")),
                "manager_notes": final_state.get("manager_notes", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0 if final_result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
