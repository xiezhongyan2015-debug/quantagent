from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from quanta_agents.agents import (
    HypothesisAgent,
    ManagerAgent,
    ResearchDiagnosticsAgent,
    StrategyAgent,
    StrategyTester,
    ValidateAgent,
)
from quanta_agents.config import get_max_epochs, get_max_strategy_validate_rounds
from quanta_agents.state import (
    WorkflowState,
    init_state,
    period_starts_after,
    periods_overlap,
    resolve_development_period,
    resolve_final_test_period,
)


def _run_strategy_step(strategy_agent: StrategyAgent, state: WorkflowState) -> WorkflowState:
    """生成策略代码后，由程序强制进入验证。"""
    result = strategy_agent.run(state)
    if isinstance(result.get("strategy_result"), dict) and result["strategy_result"]:
        generated_phase = str(result.get("phase", ""))
        if generated_phase != "validate":
            result["history"].append(
                f"Epoch {result['epoch_index']}: workflow ignored generated next phase {generated_phase!r} and required validation"
            )
        result["phase"] = "validate"
        return result

    result["phase"] = "done"
    result["manager_notes"] = "Strategy generation ended without an executable strategy."
    return result


def _reset_candidate_after_epoch_change(state: WorkflowState, previous_epoch: int) -> None:
    current_epoch = int(state.get("epoch_index", previous_epoch))
    if current_epoch == previous_epoch:
        return
    state["current_candidate_id"] = f"candidate_{current_epoch:03d}"
    state["quality_passed"] = False
    state["final_quality_passed"] = False
    state["ready_for_final"] = False
    state["candidate_frozen"] = False
    state["awaiting_final_test_approval"] = False


def _run_validation_step(validate_agent: ValidateAgent, state: WorkflowState) -> WorkflowState:
    """验证通过后进入开发回测。"""
    previous_epoch = int(state.get("epoch_index", 1))
    result = validate_agent.run(state)
    if result.get("phase") == "test":
        result["evaluation_stage"] = "development"
        result["phase"] = "backtest"
        return result

    if result.get("phase") in {"strategy", "hypothesis", "done"}:
        result["technical_retry_count"] = int(result.get("technical_retry_count", 0)) + 1
    _reset_candidate_after_epoch_change(result, previous_epoch)
    return result


def _activate_test_period(state: WorkflowState, period_kind: str) -> bool:
    spec = state.get("experiment_spec", {})
    if not isinstance(spec, dict):
        spec = {}
    else:
        spec = dict(spec)

    if period_kind == "final_test":
        period = state.get("final_test_period", {})
        if not isinstance(period, dict) or not period:
            period = resolve_final_test_period(spec)
    else:
        period = state.get("development_period", {})
        if not isinstance(period, dict) or not period:
            period = resolve_development_period(spec)

    start = period.get("start") if isinstance(period, dict) else None
    end = period.get("end") if isinstance(period, dict) else None
    if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
        return False

    if period_kind == "final_test":
        development = state.get("development_period", {})
        if not isinstance(development, dict) or not development:
            development = resolve_development_period(spec)
        if (
            not development
            or periods_overlap(development, period)
            or not period_starts_after(development, period)
        ):
            return False

    # StrategyTester 会继续通过这些日期字段读取本地 Parquet 数据。
    spec["backtest_start"] = start
    spec["backtest_end"] = end
    spec["active_period_kind"] = period_kind
    state["experiment_spec"] = spec
    return True


def _run_tester_step(
    tester: StrategyTester,
    state: WorkflowState,
    *,
    period_kind: str,
) -> WorkflowState:
    """运行一次开发回测或最终测试，并把两类结果分开保存。"""
    state["evaluation_stage"] = "final_test" if period_kind == "final_test" else "development"
    expected_phase = "final_test" if period_kind == "final_test" else "backtest"
    previous_epoch = int(state.get("epoch_index", 1))
    previous_round = int(state.get("strategy_validate_round", 1))
    previous_candidate_id = str(state.get("current_candidate_id", ""))

    if not _activate_test_period(state, period_kind):
        state["test_result"] = None
        period_label = "Final test" if period_kind == "final_test" else "Development"
        state["last_test_error"] = (
            f"{period_label} period is missing, invalid, overlaps another period, or is out of order."
        )
        state["backtest_feedback"] = ""
        if period_kind == "final_test":
            state["phase"] = "final_test"
        else:
            state["manager_notes"] = (
                "Development backtest stopped because validate_start and validate_end "
                "were not valid explicit dates."
            )
            state["phase"] = "done"
        return state

    result = tester.run(state)
    if result.get("test_result") is not None:
        result["phase"] = expected_phase
        result["last_test_error"] = ""
        if period_kind == "final_test":
            result["backtest_feedback"] = ""
        return result

    result["technical_retry_count"] = int(result.get("technical_retry_count", 0)) + 1
    if period_kind == "final_test":
        result["epoch_index"] = previous_epoch
        result["strategy_validate_round"] = previous_round
        result["current_candidate_id"] = previous_candidate_id
        error = str(result.get("backtest_feedback", "")).strip()
        if not error:
            error = str(result.get("manager_notes", "")).strip()
        if not error:
            error = "StrategyTester did not return a final-test result."
        result["last_test_error"] = error
        result["backtest_feedback"] = ""
        result["phase"] = "final_test"
    else:
        _reset_candidate_after_epoch_change(result, previous_epoch)
    return result


def build_workflow(checkpointer: Any | None = None) -> CompiledStateGraph:
    manager = ManagerAgent()
    hypothesis_agent = HypothesisAgent()
    diagnostics_agent = ResearchDiagnosticsAgent()
    strategy_agent = StrategyAgent()
    validate_agent = ValidateAgent()
    tester = StrategyTester()

    graph = StateGraph(WorkflowState)

    graph.add_node("manager", manager.dispatch)
    graph.add_node("hypothesis", hypothesis_agent.run)
    graph.add_node("diagnostics", diagnostics_agent.run)
    graph.add_node("strategy", lambda state: _run_strategy_step(strategy_agent, state))
    graph.add_node("validate", lambda state: _run_validation_step(validate_agent, state))
    graph.add_node("backtest", lambda state: _run_tester_step(tester, state, period_kind="development"))
    graph.add_node("final_test", lambda state: _run_tester_step(tester, state, period_kind="final_test"))
    graph.add_node("test", lambda state: _run_tester_step(tester, state, period_kind="development"))

    graph.set_entry_point("manager")

    graph.add_conditional_edges(
        "manager",
        lambda state: state["phase"],
        {
            "hypothesis": "hypothesis",
            "diagnostics": "diagnostics",
            "strategy": "strategy",
            "validate": "validate",
            "backtest": "backtest",
            "final_test": "final_test",
            "test": "test",
            "done": END,
        },
    )

    graph.add_edge("hypothesis", "manager")
    graph.add_edge("diagnostics", "manager")
    graph.add_edge("strategy", "manager")
    graph.add_edge("validate", "manager")
    graph.add_edge("backtest", "manager")
    graph.add_edge("final_test", "manager")
    graph.add_edge("test", "manager")

    if checkpointer is None:
        return graph.compile()
    return graph.compile(checkpointer=checkpointer)


def run_workflow(
    user_idea: str,
    max_epochs: int | None = None,
    experiment_spec: dict[str, object] | None = None,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
) -> WorkflowState:
    app = build_workflow(checkpointer=checkpointer)
    effective_max_epochs = get_max_epochs() if max_epochs is None else max_epochs
    initial_state = init_state(
        user_idea=user_idea,
        max_epochs=effective_max_epochs,
        experiment_spec=experiment_spec,
    )
    validate_rounds = get_max_strategy_validate_rounds()
    recursion_limit = max(200, effective_max_epochs * (6 * validate_rounds + 12) + 10)
    invoke_config: dict[str, Any] = {"recursion_limit": recursion_limit}
    effective_thread_id = thread_id.strip() if isinstance(thread_id, str) else ""
    if checkpointer is not None and not effective_thread_id:
        experiment_id = str(initial_state.get("experiment_spec", {}).get("experiment_id", "")).strip()
        effective_thread_id = experiment_id or f"quanta-{uuid4().hex}"
    if effective_thread_id:
        invoke_config["configurable"] = {"thread_id": effective_thread_id}

    final_state = app.invoke(initial_state, config=invoke_config)
    return cast(WorkflowState, final_state)


def continue_workflow(
    state: WorkflowState,
    *,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
) -> WorkflowState:
    """从已经恢复的研究状态继续运行，不重新创建第一轮。"""

    app = build_workflow(checkpointer=checkpointer)
    max_epochs = int(state.get("max_epochs", 1))
    validate_rounds = get_max_strategy_validate_rounds()
    recursion_limit = max(200, max_epochs * (6 * validate_rounds + 16) + 20)
    config: dict[str, Any] = {"recursion_limit": recursion_limit}
    effective_thread_id = thread_id.strip() if isinstance(thread_id, str) else ""
    if effective_thread_id:
        config["configurable"] = {"thread_id": effective_thread_id}
    final_state = app.invoke(state, config=config)
    return cast(WorkflowState, final_state)
