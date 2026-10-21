from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, cast
from uuid import uuid4

import numpy as np
import pandas as pd

from quanta_agents.agents.baseline_builder_v2 import BaselineBuilderV2
from quanta_agents.agents.input_interpreter_v2 import InputInterpreterV2
from quanta_agents.agents.research_planner_v2 import ResearchPlannerV2
from quanta_agents.agents.result_judge_v2 import ResultJudgeV2
from quanta_agents.agents.strategy_agent import StrategyAgent
from quanta_agents.agents.strategy_tester import StrategyTester
from quanta_agents.agents.validate_agent import ValidateAgent
from quanta_agents.candidate_snapshot import (
    capture_candidate_snapshot,
    restore_candidate_snapshot,
    restore_accepted_snapshot,
)
from quanta_agents.calculation_contract import (
    CalculationContractError,
    check_generated_calculation_contracts,
    check_generated_consecutive_market_days,
)
from quanta_agents.state import BacktestResult, WorkflowState, init_state
from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)
from quanta_agents.strategy_future_data_check import (
    FutureDataInfluenceError,
    check_generated_daily_strategy,
    has_dated_validation_data,
)
from quanta_agents.strategy_mechanism_signal_check import (
    MechanismSignalValidationError,
    check_generated_mechanism_signal,
    mechanism_signal_reference,
)
from quanta_agents.strategy_position_plan_check import (
    check_generated_daily_position_plans,
)
from quanta_agents.trace_logger import get_trace_run_dir, write_trace_json
from quanta_agents.v2_observer import audit_v2_loop
from quanta_agents.v2_runtime import build_stage_spec, default_cn_daily_v2_profile
from quanta_agents.web_research import WebResearchClient


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BacktestResult):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return str(value)


def _actual_transaction_cost_rule(
    result: object,
    backtest_debug: object = None,
) -> dict[str, object] | None:
    sources: list[object] = []
    if isinstance(result, Mapping):
        sources.append(result.get("transaction_cost_rule"))
        for key in ("backtest_debug", "_backtest_debug"):
            debug = result.get(key)
            if isinstance(debug, Mapping):
                sources.append(debug.get("transaction_cost_rule"))
    if isinstance(backtest_debug, Mapping):
        sources.append(backtest_debug.get("transaction_cost_rule"))
    for source in sources:
        if isinstance(source, Mapping) and source:
            return deepcopy(dict(source))
    return None


def _result_to_final_report(
    result: object,
    *,
    backtest_debug: object = None,
) -> dict[str, object]:
    if isinstance(result, BacktestResult):
        report = {
            "passed": result.passed,
            "annual_return": result.annual_return,
            "sharpe": result.sharpe,
            "max_drawdown": result.max_drawdown,
            "max_ddpercent": result.max_ddpercent,
            "trade_count": result.trade_count,
            "win_rate": result.win_rate,
            "profit_loss_ratio": result.profit_loss_ratio,
            "summary": result.summary,
        }
    elif isinstance(result, Mapping):
        report = dict(result)
    else:
        return {}
    cost_rule = _actual_transaction_cost_rule(result, backtest_debug)
    if cost_rule is not None:
        report["transaction_cost_rule"] = cost_rule
    return report


def _params_diff(old: object, new: object) -> list[dict[str, object]]:
    old_map = old if isinstance(old, Mapping) else {}
    new_map = new if isinstance(new, Mapping) else {}
    rows: list[dict[str, object]] = []
    for key in sorted(set(old_map) | set(new_map), key=str):
        before = old_map.get(key)
        after = new_map.get(key)
        if before != after:
            rows.append({"field": str(key), "old": before, "new": after})
    return rows


def _formal_candidate_from_probe_decision(
    probe_decision: Mapping[str, object],
    probe_results: list[dict[str, object]],
) -> dict[str, Any]:
    """取出正式候选，并去掉会造成循环引用的反向内容。"""

    raw_candidate = probe_decision.get("formal_candidate")
    if not isinstance(raw_candidate, Mapping):
        raise RuntimeError("数值比较没有生成正式候选")
    candidate = deepcopy(dict(raw_candidate))
    candidate["probe_results"] = deepcopy(probe_results)
    candidate["probe_decision"] = {
        key: deepcopy(value)
        for key, value in probe_decision.items()
        if key != "formal_candidate"
    }
    return candidate


def _finite_metric(report: Mapping[str, object], key: str) -> float:
    value = report.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("-inf")
    number = float(value)
    return number if number == number and abs(number) != float("inf") else float("-inf")


def _research_rank(report: Mapping[str, object]) -> tuple[float, float, float, float]:
    """只用研究期资料按固定顺序挑一个版本进入确认期。"""

    return (
        1.0 if report.get("passed") is True else 0.0,
        _finite_metric(report, "worst_fold_sharpe"),
        _finite_metric(report, "median_sharpe"),
        _finite_metric(report, "median_annual_return"),
    )


def _meets_confirmation_requirements(
    report: Mapping[str, object],
    requirements: Mapping[str, object],
) -> bool:
    def minimum(name: str, default: float) -> float:
        value = requirements.get(name, default)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    worst_drawdown = _finite_metric(report, "worst_fold_drawdown")
    return bool(
        report.get("passed") is True
        and report.get("beats_cash_in_all_folds") is True
        and report.get("cost_stress_complete") is True
        and report.get("cost_stress_passed") is True
        and report.get("delay_stress_complete") is True
        and report.get("delay_stress_passed") is True
        and _finite_metric(report, "median_sharpe")
        >= minimum("confirmation_min_median_sharpe", 1.0)
        and _finite_metric(report, "median_annual_return")
        >= minimum("confirmation_min_median_annual_return", 0.08)
        and _finite_metric(report, "worst_fold_sharpe")
        >= minimum("confirmation_min_worst_fold_sharpe", 0.0)
        and worst_drawdown != float("-inf")
        and abs(worst_drawdown)
        <= minimum("confirmation_max_worst_fold_drawdown", 0.20)
        and _finite_metric(report, "total_trade_count")
        >= minimum("confirmation_min_total_trade_count", 300.0)
        and _finite_metric(report, "cost_stress_median_sharpe")
        >= minimum("confirmation_min_cost_stress_sharpe", 0.50)
        and _finite_metric(report, "delay_stress_median_sharpe")
        >= minimum("confirmation_min_delay_stress_sharpe", 0.50)
    )


def _meets_final_requirements(
    report: Mapping[str, object],
    requirements: Mapping[str, object],
) -> bool:
    def minimum(name: str, default: float) -> float:
        value = requirements.get(name, default)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    max_drawdown = _finite_metric(report, "max_ddpercent")
    return bool(
        report.get("passed") is True
        and _finite_metric(report, "annual_return")
        >= minimum("final_min_annual_return", 0.05)
        and _finite_metric(report, "sharpe") >= minimum("final_min_sharpe", 0.75)
        and max_drawdown != float("-inf")
        and abs(max_drawdown) <= minimum("final_max_drawdown", 0.20)
        and _finite_metric(report, "trade_count")
        >= minimum("final_min_trade_count", 100.0)
    )


class ResearchLoopV2:
    """从一句话开始，按解释、候选、回测、判断的顺序持续研究。"""

    def __init__(
        self,
        *,
        interpreter: InputInterpreterV2 | None = None,
        baseline_builder: BaselineBuilderV2 | None = None,
        planner: ResearchPlannerV2 | None = None,
        judge: ResultJudgeV2 | None = None,
        strategy_agent: StrategyAgent | None = None,
        validate_agent: ValidateAgent | None = None,
        tester: StrategyTester | None = None,
    ) -> None:
        self.interpreter = interpreter or InputInterpreterV2()
        self.baseline_builder = baseline_builder or BaselineBuilderV2()
        self.planner = planner
        self.judge = judge or ResultJudgeV2()
        self.strategy_agent = strategy_agent or StrategyAgent()
        self.validate_agent = validate_agent or ValidateAgent()
        self.tester = tester or StrategyTester()
        self._next_epoch = 1

    @staticmethod
    def _profile_budget(profile: Mapping[str, object]) -> tuple[int, list[str]]:
        budget = profile.get("research_budget")
        if not isinstance(budget, Mapping):
            return 7, []
        max_candidates = int(budget.get("max_formal_candidates", 7))
        raw_categories = budget.get("minimum_categories", [])
        categories = (
            [str(item).strip() for item in raw_categories if str(item).strip()]
            if isinstance(raw_categories, list)
            else []
        )
        return max(1, max_candidates), categories

    @staticmethod
    def _available_data(profile: Mapping[str, object]) -> list[dict[str, object]]:
        data = profile.get("data")
        if not isinstance(data, Mapping):
            raise ValueError("运行设置缺少data")
        table_key = str(data.get("table_key", "")).strip()
        fields = data.get("available_fields")
        if not table_key or not isinstance(fields, list):
            raise ValueError("运行设置中的资料表或字段缺失")
        return [
            {
                "table_key": table_key,
                "type": "time_series",
                "fields": [str(field) for field in fields],
            }
        ]

    @staticmethod
    def _accepted_strategy_descriptor(state: Mapping[str, object]) -> dict[str, object]:
        snapshot = state.get("accepted_snapshot")
        source = snapshot if isinstance(snapshot, Mapping) else state
        meta = source.get("hypothesis_generation_meta")
        result = source.get("strategy_result")
        descriptor = dict(meta) if isinstance(meta, Mapping) else {}
        if isinstance(result, Mapping):
            descriptor["params"] = deepcopy(result.get("params", {}))
            descriptor["required_data"] = deepcopy(result.get("required_data", descriptor.get("required_data", [])))
        descriptor["candidate_id"] = str(state.get("accepted_candidate_id", ""))
        return descriptor

    @staticmethod
    def _compact_strategy_state(state: WorkflowState) -> None:
        result = state.get("strategy_result")
        if not isinstance(result, dict):
            return
        compact = dict(result)
        compact["train_data_bundle"] = {}
        compact["validate_data_bundle"] = {}
        state["strategy_result"] = compact

    @staticmethod
    def _prepare_candidate_state(
        state: WorkflowState,
        candidate: Mapping[str, object],
        *,
        candidate_id: str,
        epoch_index: int,
        experiment_spec: Mapping[str, object],
    ) -> None:
        meta = deepcopy(dict(candidate))
        state["experiment_spec"] = deepcopy(dict(experiment_spec))
        state["current_candidate_id"] = candidate_id
        state["epoch_index"] = epoch_index
        state["hypothesis_generation_meta"] = deepcopy(meta)
        state["strategy_generation_meta"] = deepcopy(meta)
        state["strategy_validate_round"] = 1
        state["validation_result"] = {}
        state["validation_summary"] = {}
        state["validation_feedback"] = {}
        state["development_report"] = {}
        state["test_result"] = None
        state["last_test_error"] = ""
        state["backtest_feedback"] = ""
        state["evaluation_stage"] = "development"
        state["phase"] = "strategy"

    def _allocate_epoch(self) -> int:
        value = self._next_epoch
        self._next_epoch += 1
        return value

    @staticmethod
    def _check_daily_future_data(
        state: WorkflowState,
        experiment_spec: Mapping[str, object],
    ) -> dict[str, object]:
        if str(experiment_spec.get("backtest_mode", "")).strip().lower() == "event_parquet":
            return {"passed": True, "skipped": True, "reason": "非日线策略"}

        strategy_result = state.get("strategy_result")
        if not isinstance(strategy_result, Mapping):
            raise FutureDataInfluenceError("策略结果为空，无法检查未来数据")
        validate_bundle = strategy_result.get("validate_data_bundle")
        if not isinstance(validate_bundle, Mapping) or not has_dated_validation_data(validate_bundle):
            raise FutureDataInfluenceError("没有可截断的日线验证数据")

        code_text = str(state.get("strategy_code") or state.get("code_text") or "")
        if not code_text.strip():
            raise FutureDataInfluenceError("策略代码为空，无法检查未来数据")
        future_data_report = check_generated_daily_strategy(code_text, strategy_result)
        position_plan_contract = experiment_spec.get("position_plan_contract")
        if (
            isinstance(position_plan_contract, Mapping)
            and position_plan_contract.get("mode") == "fixed_target_until_exit"
        ):
            position_plan_report = check_generated_daily_position_plans(code_text)
        else:
            position_plan_report = {
                "passed": True,
                "skipped": True,
                "reason": "策略没有声明固定目标比例持仓计划",
            }
        return {
            "passed": True,
            "future_data_check": future_data_report,
            "position_plan_check": position_plan_report,
        }

    @staticmethod
    def _check_parameter_probe_values(
        state: WorkflowState,
        candidate: Mapping[str, object],
        experiment_spec: Mapping[str, object],
    ) -> dict[str, object]:
        """用同一份策略代码逐个试运行数值比较里的所有值。"""

        probe = candidate.get("parameter_probe")
        if not isinstance(probe, Mapping):
            return {"passed": True, "skipped": True, "reason": "不是数值比较候选"}
        param_name = str(probe.get("param_name", "")).strip()
        raw_values = probe.get("values")
        if not param_name or not isinstance(raw_values, list) or not raw_values:
            raise ValueError("parameter_probe不完整")
        try:
            values = [float(value) for value in raw_values]
        except (TypeError, ValueError) as exc:
            raise ValueError("parameter_probe.values必须都是数值") from exc

        strategy_result = state.get("strategy_result")
        if not isinstance(strategy_result, Mapping):
            raise ValueError("数值比较缺少策略结果")
        base_params = strategy_result.get("params")
        if not isinstance(base_params, Mapping) or param_name not in base_params:
            raise ValueError(f"数值比较参数不存在: {param_name}")
        train_bundle = strategy_result.get("train_data_bundle")
        validate_bundle = strategy_result.get("validate_data_bundle")
        if not isinstance(train_bundle, Mapping) or not isinstance(validate_bundle, Mapping):
            raise ValueError("数值比较缺少训练期或验证期数据")
        code_text = str(state.get("strategy_code") or state.get("code_text") or "")
        if not code_text.strip():
            raise ValueError("数值比较缺少策略代码")

        event_mode = (
            str(experiment_spec.get("backtest_mode", "")).strip().lower()
            == "event_parquet"
        )
        validate_generated_strategy_code(code_text, event_mode=event_mode)
        try:
            import scipy.sparse as scipy_sparse
        except ImportError:
            scipy_sparse = None  # type: ignore[assignment]
        namespace: dict[str, object] = {
            "__name__": "__parameter_probe_value_check__",
            "json": json,
            "np": np,
            "pd": pd,
            "scipy_sparse": scipy_sparse,
            "universe": strategy_result.get("universe", []),
        }
        exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
        output_weights = namespace.get("output_weights")
        if not callable(output_weights):
            raise ValueError("数值比较代码缺少output_weights函数")

        def copy_bundle(bundle: Mapping[str, object]) -> dict[str, object]:
            return {
                str(name): value.copy(deep=False)
                if isinstance(value, pd.DataFrame)
                else value
                for name, value in bundle.items()
            }

        checked_values: list[float] = []
        for value in values:
            call_params = deepcopy(dict(base_params))
            call_params[param_name] = value
            params_before = repr(call_params)
            train_copy = copy_bundle(train_bundle)
            validate_copy = copy_bundle(validate_bundle)
            namespace["params"] = call_params
            namespace["train_data_bundle"] = train_copy
            namespace["validate_data_bundle"] = validate_copy
            try:
                output = output_weights(train_copy, validate_copy, call_params)
            except Exception as exc:
                raise ValueError(
                    f"同一份数值比较代码不能接受{param_name}={value!r}: {exc}"
                ) from exc
            if repr(call_params) != params_before:
                raise ValueError(
                    f"数值比较代码在{param_name}={value!r}时修改了传入参数"
                )
            if not isinstance(output, pd.DataFrame):
                raise ValueError(
                    f"数值比较代码在{param_name}={value!r}时没有返回DataFrame"
                )
            checked_values.append(value)
        return {
            "passed": True,
            "param_name": param_name,
            "checked_values": checked_values,
        }

    def _generate_and_validate(
        self,
        state: WorkflowState,
        candidate: Mapping[str, object],
        *,
        candidate_id: str,
        experiment_spec: Mapping[str, object],
        mechanism_signal_reference_source: str | None = None,
        max_technical_rounds: int = 3,
    ) -> None:
        generation_candidate = deepcopy(dict(candidate))
        if (
            str(generation_candidate.get("category", "")) == "mechanism_signal"
            and not str(generation_candidate.get("variant_mode", "")).strip()
        ):
            generation_candidate["variant_mode"] = "added_to_base"
        if mechanism_signal_reference_source:
            generation_candidate["_mechanism_signal_reference_source"] = (
                mechanism_signal_reference_source
            )
        self._prepare_candidate_state(
            state,
            generation_candidate,
            candidate_id=candidate_id,
            epoch_index=self._allocate_epoch(),
            experiment_spec=experiment_spec,
        )
        last_message = ""
        for technical_round in range(1, max_technical_rounds + 1):
            if technical_round > 1:
                state["strategy_generation_meta"] = deepcopy(generation_candidate)
            self.strategy_agent.run(state)
            if not isinstance(state.get("strategy_result"), dict) or not state["strategy_result"]:
                raise RuntimeError("策略代码没有生成可执行结果")

            strategy_result = state.get("strategy_result")
            strategy_meta = state.get("strategy_generation_meta")
            if str(candidate.get("category", "")) == "mechanism_signal":
                try:
                    mechanism_signal_report = check_generated_mechanism_signal(
                        str(state.get("strategy_code") or state.get("code_text") or ""),
                        strategy_result,
                        reference_source=mechanism_signal_reference_source,
                        event_mode=(
                            str(experiment_spec.get("backtest_mode", ""))
                            .strip()
                            .lower()
                            == "event_parquet"
                        ),
                    )
                except (MechanismSignalValidationError, ValueError) as exc:
                    mechanism_signal_report = {"passed": False, "error": str(exc)}
                    state["validation_summary"] = {
                        "passed": False,
                        "mechanism_signal_check": mechanism_signal_report,
                        "suggestions": [
                            "把完整新信号写入 build_mechanism_signal；"
                            "output_weights 必须直接使用它的返回结果。"
                            "若这是对照版本，原样复制 added_to_base 已通过的函数及其辅助定义。"
                        ],
                    }
                    state["validation_feedback"] = deepcopy(
                        state["validation_summary"]
                    )
                    state["phase"] = "strategy"
                    last_message = str(exc)
                    write_trace_json(
                        state,
                        agent_name="ResearchLoopV2",
                        stage="mechanism_signal_check",
                        payload=mechanism_signal_report,
                        attempt=technical_round,
                    )
                    continue
                state["mechanism_signal_check"] = mechanism_signal_report  # type: ignore[typeddict-unknown-key]
                if isinstance(strategy_result, dict):
                    strategy_result["mechanism_signal_check"] = deepcopy(
                        mechanism_signal_report
                    )
                if isinstance(strategy_meta, dict):
                    strategy_meta["mechanism_signal_check"] = deepcopy(
                        mechanism_signal_report
                    )
                write_trace_json(
                    state,
                    agent_name="ResearchLoopV2",
                    stage="mechanism_signal_check",
                    payload=mechanism_signal_report,
                    attempt=technical_round,
                )

            try:
                future_data_report = self._check_daily_future_data(state, experiment_spec)
            except (FutureDataInfluenceError, ValueError) as exc:
                future_data_report = {"passed": False, "error": str(exc)}
                state["validation_summary"] = {
                    "passed": False,
                    "future_data_check": future_data_report,
                    "suggestions": [
                        "修改策略，使任一日期的权重只依赖该日期及更早的数据；"
                        "每批新持仓计划开始时确定目标比例并保持到原结束日，"
                        "不得按当天全部持仓重新等权；分到0比例的新信号不得保存为"
                        "有效计划，也不得阻止同一股票之后的新信号。"
                    ],
                }
                state["validation_feedback"] = deepcopy(state["validation_summary"])
                state["phase"] = "strategy"
                last_message = str(exc)
                write_trace_json(
                    state,
                    agent_name="ResearchLoopV2",
                    stage="future_data_check",
                    payload=future_data_report,
                    attempt=technical_round,
                )
                continue

            state["future_data_check"] = future_data_report  # type: ignore[typeddict-unknown-key]
            position_plan_report = future_data_report.get("position_plan_check")
            if isinstance(position_plan_report, Mapping):
                state["position_plan_check"] = deepcopy(  # type: ignore[typeddict-unknown-key]
                    dict(position_plan_report)
                )
            if isinstance(strategy_result, dict):
                strategy_result["future_data_check"] = deepcopy(future_data_report)
                if isinstance(position_plan_report, Mapping):
                    strategy_result["position_plan_check"] = deepcopy(
                        dict(position_plan_report)
                    )
            if isinstance(strategy_meta, dict):
                strategy_meta["future_data_check"] = deepcopy(future_data_report)
                if isinstance(position_plan_report, Mapping):
                    strategy_meta["position_plan_check"] = deepcopy(
                        dict(position_plan_report)
                    )
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="future_data_check",
                payload=future_data_report,
                attempt=technical_round,
            )
            try:
                consecutive_day_report = check_generated_consecutive_market_days(
                    str(state.get("strategy_code") or state.get("code_text") or ""),
                    candidate,
                    continuity_contract=experiment_spec.get(
                        "market_day_continuity_contract"
                    ),
                )
            except (CalculationContractError, ValueError) as exc:
                consecutive_day_report = {"passed": False, "error": str(exc)}
                state["validation_summary"] = {
                    "passed": False,
                    "consecutive_market_day_check": consecutive_day_report,
                    "suggestions": [
                        "连续N日必须按全市场交易日期逐日检查；"
                        "信号日缺少紧邻的前一市场日K线时不能退到更早一条记录。"
                    ],
                }
                state["validation_feedback"] = deepcopy(state["validation_summary"])
                state["phase"] = "strategy"
                last_message = str(exc)
                write_trace_json(
                    state,
                    agent_name="ResearchLoopV2",
                    stage="consecutive_market_day_check",
                    payload=consecutive_day_report,
                    attempt=technical_round,
                )
                continue
            state["consecutive_market_day_check"] = consecutive_day_report  # type: ignore[typeddict-unknown-key]
            if isinstance(strategy_result, dict):
                strategy_result["consecutive_market_day_check"] = deepcopy(
                    consecutive_day_report
                )
            if isinstance(strategy_meta, dict):
                strategy_meta["consecutive_market_day_check"] = deepcopy(
                    consecutive_day_report
                )
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="consecutive_market_day_check",
                payload=consecutive_day_report,
                attempt=technical_round,
            )
            if isinstance(candidate.get("calculation_contracts"), list) and candidate.get(
                "calculation_contracts"
            ):
                try:
                    calculation_report = check_generated_calculation_contracts(
                        str(state.get("strategy_code") or state.get("code_text") or ""),
                        candidate.get("calculation_contracts"),
                    )
                except (CalculationContractError, ValueError) as exc:
                    calculation_report = {"passed": False, "error": str(exc)}
                    state["validation_summary"] = {
                        "passed": False,
                        "calculation_contract_check": calculation_report,
                        "suggestions": [
                            "按统一市场交易日的精确日期取共同股票样本；"
                            "允许中间缺行时，不要用股票分组后的shift(k)代替t-k。"
                        ],
                    }
                    state["validation_feedback"] = deepcopy(
                        state["validation_summary"]
                    )
                    state["phase"] = "strategy"
                    last_message = str(exc)
                    write_trace_json(
                        state,
                        agent_name="ResearchLoopV2",
                        stage="calculation_contract_check",
                        payload=calculation_report,
                        attempt=technical_round,
                    )
                    continue
                state["calculation_contract_check"] = calculation_report  # type: ignore[typeddict-unknown-key]
                if isinstance(strategy_result, dict):
                    strategy_result["calculation_contract_check"] = deepcopy(
                        calculation_report
                    )
                if isinstance(strategy_meta, dict):
                    strategy_meta["calculation_contract_check"] = deepcopy(
                        calculation_report
                    )
                write_trace_json(
                    state,
                    agent_name="ResearchLoopV2",
                    stage="calculation_contract_check",
                    payload=calculation_report,
                    attempt=technical_round,
                )
            if isinstance(candidate.get("parameter_probe"), Mapping):
                try:
                    probe_value_report = self._check_parameter_probe_values(
                        state,
                        candidate,
                        experiment_spec,
                    )
                except ValueError as exc:
                    probe_value_report = {"passed": False, "error": str(exc)}
                    state["validation_summary"] = {
                        "passed": False,
                        "parameter_probe_value_check": probe_value_report,
                        "suggestions": [
                            "让同一份策略代码接受parameter_probe.values中的每个值，"
                            "不要把第一个值写成固定限制"
                        ],
                    }
                    state["validation_feedback"] = deepcopy(
                        state["validation_summary"]
                    )
                    state["phase"] = "strategy"
                    last_message = str(exc)
                    write_trace_json(
                        state,
                        agent_name="ResearchLoopV2",
                        stage="parameter_probe_value_check",
                        payload=probe_value_report,
                        attempt=technical_round,
                    )
                    continue
                state["parameter_probe_value_check"] = probe_value_report  # type: ignore[typeddict-unknown-key]
                if isinstance(strategy_result, dict):
                    strategy_result["parameter_probe_value_check"] = deepcopy(
                        probe_value_report
                    )
                if isinstance(strategy_meta, dict):
                    strategy_meta["parameter_probe_value_check"] = deepcopy(
                        probe_value_report
                    )
                write_trace_json(
                    state,
                    agent_name="ResearchLoopV2",
                    stage="parameter_probe_value_check",
                    payload=probe_value_report,
                    attempt=technical_round,
                )
            self.validate_agent.run(state)
            if state.get("phase") == "test":
                return
            summary = state.get("validation_summary")
            last_message = str(summary or state.get("manager_notes", ""))
            if state.get("phase") != "strategy":
                break
        raise RuntimeError(f"策略固定检查未通过: {last_message}")

    def _run_development_test(
        self,
        state: WorkflowState,
        *,
        experiment_spec: Mapping[str, object],
    ) -> dict[str, object]:
        state["experiment_spec"] = deepcopy(dict(experiment_spec))
        state["evaluation_stage"] = "development"
        state["phase"] = "backtest"
        state["development_report"] = {}
        state["test_result"] = None
        self.tester.run(state)
        report = state.get("development_report")
        if not isinstance(report, dict) or not report:
            message = str(state.get("backtest_feedback") or state.get("last_test_error") or "未知错误")
            raise RuntimeError(f"开发期回测没有生成报告: {message}")
        compact_report = ResearchPlannerV2.compact_development_report(report)
        if not compact_report:
            raise RuntimeError("开发期回测报告缺少可用于研究判断的指标")
        state["development_report"] = deepcopy(compact_report)
        return compact_report

    def _run_formal_candidate(
        self,
        state: WorkflowState,
        candidate: Mapping[str, object],
        *,
        candidate_id: str,
        experiment_spec: Mapping[str, object],
        mechanism_signal_reference_source: str | None = None,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        accepted = state.get("accepted_snapshot")
        accepted_params: object = {}
        if isinstance(accepted, Mapping):
            accepted_result = accepted.get("strategy_result")
            if isinstance(accepted_result, Mapping):
                accepted_params = accepted_result.get("params", {})
        self._generate_and_validate(
            state,
            candidate,
            candidate_id=candidate_id,
            experiment_spec=experiment_spec,
            mechanism_signal_reference_source=mechanism_signal_reference_source,
        )
        result = state.get("strategy_result")
        new_params = result.get("params", {}) if isinstance(result, Mapping) else {}
        parameter_changes = _params_diff(accepted_params, new_params)
        report = self._run_development_test(state, experiment_spec=experiment_spec)
        return report, parameter_changes

    def _run_parameter_probe(
        self,
        state: WorkflowState,
        candidate: Mapping[str, object],
        *,
        experiment_spec: Mapping[str, object],
        existing_probe_results: list[dict[str, object]] | None = None,
        on_probe_result: Any | None = None,
        saved_probe_code: str | None = None,
        saved_probe_generation_meta: Mapping[str, object] | None = None,
        saved_probe_code_sha256: str | None = None,
    ) -> list[dict[str, object]]:
        probe = candidate.get("parameter_probe")
        if not isinstance(probe, Mapping):
            raise ValueError("候选缺少parameter_probe")
        param_name = str(probe.get("param_name", "")).strip()
        raw_values = probe.get("values")
        if not param_name or not isinstance(raw_values, list):
            raise ValueError("parameter_probe不完整")
        values = [float(value) for value in raw_values]

        restore_accepted_snapshot(cast(Any, state))
        accepted_result = state.get("strategy_result")
        accepted_params = deepcopy(
            accepted_result.get("params", {})
            if isinstance(accepted_result, Mapping)
            else {}
        )
        probe_candidate = deepcopy(dict(candidate))
        probe_values_text = ", ".join(repr(value) for value in values)
        probe_candidate["strategy_modification"] = (
            f"这是共用一份代码的相邻数值临时比较。params['{param_name}']的初始值设为"
            f"{values[0]!r}，随后固定程序会依次传入[{probe_values_text}]。"
            f"代码必须接受这些数值，不能写成只允许初始值{values[0]!r}；"
            + str(probe_candidate.get("strategy_modification", ""))
        )
        probe_candidate["hypothesis"] = (
            f"【本次临时比较】params['{param_name}']初始为{values[0]!r}，"
            f"同一份代码还会收到[{probe_values_text}]中的其他数值。"
            "不得检查该参数必须等于第一个数值。\n"
            + str(probe_candidate.get("hypothesis", ""))
        )
        saved_parts = (
            saved_probe_code,
            saved_probe_generation_meta,
            saved_probe_code_sha256,
        )
        restored_saved_code = any(part is not None for part in saved_parts)
        if restored_saved_code:
            if not (
                isinstance(saved_probe_code, str)
                and saved_probe_code
                and isinstance(saved_probe_generation_meta, Mapping)
                and isinstance(saved_probe_code_sha256, str)
                and saved_probe_code_sha256
            ):
                raise ValueError("续跑数值比较的旧代码资料不完整")
            self._restore_saved_candidate_strategy(
                state,
                candidate=probe_candidate,
                candidate_id="probe_code",
                code_text=saved_probe_code,
                saved_generation_meta=saved_probe_generation_meta,
                expected_hash=saved_probe_code_sha256,
                experiment_spec=experiment_spec,
            )
            probe_value_report = self._check_parameter_probe_values(
                state,
                probe_candidate,
                experiment_spec,
            )
            state["parameter_probe_value_check"] = probe_value_report  # type: ignore[typeddict-unknown-key]
            restored_result = state.get("strategy_result")
            if isinstance(restored_result, dict):
                restored_result["parameter_probe_value_check"] = deepcopy(
                    probe_value_report
                )
            restored_meta = state.get("strategy_generation_meta")
            if isinstance(restored_meta, dict):
                restored_meta["parameter_probe_value_check"] = deepcopy(
                    probe_value_report
                )
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="parameter_probe_value_check",
                payload=probe_value_report,
                attempt=1,
            )
        else:
            self._generate_and_validate(
                state,
                probe_candidate,
                candidate_id="probe_code",
                experiment_spec=experiment_spec,
            )
        result = state.get("strategy_result")
        if not isinstance(result, dict) or not isinstance(result.get("params"), dict):
            raise RuntimeError("临时比较代码没有params")
        base_params = deepcopy(result["params"])
        if param_name not in base_params:
            raise RuntimeError(f"临时比较参数不存在: {param_name}")
        try:
            restored_value = float(base_params[param_name])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"临时比较参数不是数值: {param_name}") from exc
        if restored_value != values[0]:
            raise RuntimeError("续跑代码中的起始数值与原比较计划不一致")
        parameter_changes = _params_diff(accepted_params, base_params)
        if (
            len(parameter_changes) != 1
            or parameter_changes[0].get("field") != param_name
            or parameter_changes[0].get("new") != base_params[param_name]
        ):
            raise RuntimeError("数值比较代码除了指定参数外还改变了其他参数")

        existing_by_value: dict[float, dict[str, object]] = {}
        for item in existing_probe_results or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("param_name", "")) != param_name:
                raise ValueError("续跑的数值比较参数名与当前候选不一致")
            raw_value = item.get("value")
            report = item.get("development_report")
            if isinstance(raw_value, (int, float)) and isinstance(report, Mapping):
                compact = ResearchPlannerV2.compact_development_report(report)
                if compact:
                    existing_by_value[float(raw_value)] = {
                        "param_name": param_name,
                        "value": float(raw_value),
                        "development_report": compact,
                        "resumed_from_completed_probe": True,
                    }

        reports: list[dict[str, object]] = []
        for index, value in enumerate(values, start=1):
            if value in existing_by_value:
                reports.append(deepcopy(existing_by_value[value]))
                if callable(on_probe_result):
                    on_probe_result(deepcopy(reports))
                continue
            params = deepcopy(base_params)
            params[param_name] = value
            state["strategy_result"] = dict(state["strategy_result"])
            state["strategy_result"]["params"] = params
            state["current_candidate_id"] = f"probe_{param_name}_{index:02d}"
            state["epoch_index"] = self._allocate_epoch()
            report = self._run_development_test(state, experiment_spec=experiment_spec)
            reports.append(
                {
                    "param_name": param_name,
                    "value": value,
                    "development_report": report,
                }
            )
            if callable(on_probe_result):
                on_probe_result(deepcopy(reports))
        restore_accepted_snapshot(cast(Any, state))
        return reports

    @staticmethod
    def _record_candidate(
        run_record: dict[str, Any],
        state: WorkflowState,
        *,
        candidate_id: str,
        candidate: Mapping[str, object],
        report: Mapping[str, object] | None,
        status: str,
        judge_result: Mapping[str, object] | None = None,
        parameter_changes: list[dict[str, object]] | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        category = str(candidate.get("category", "baseline"))
        row: dict[str, object] = {
            "candidate_id": candidate_id,
            "research_category": category,
            "category": category,
            "mechanism_id": candidate.get("mechanism_id"),
            "research_question": candidate.get("research_question"),
            "change_kind": candidate.get("change_kind"),
            "changed_fields": deepcopy(candidate.get("changed_fields", [])),
            "change_spec": {
                "strategy_modification": candidate.get("strategy_modification"),
                "changed_fields": deepcopy(candidate.get("changed_fields", [])),
            },
            "predicted_changes": deepcopy(candidate.get("predicted_changes", [])),
            "status": status,
            "development_report": (
                ResearchPlannerV2.compact_development_report(report)
                if isinstance(report, Mapping)
                else {}
            ),
            "judge_result": deepcopy(dict(judge_result)) if isinstance(judge_result, Mapping) else {},
            "adopted": bool(judge_result and judge_result.get("adopted") is True),
            "parameter_changes": deepcopy(parameter_changes or []),
            "strategy_code_change_ratio": (
                state.get("strategy_generation_meta", {}).get("code_change_ratio")
                if isinstance(state.get("strategy_generation_meta"), dict)
                else None
            ),
        }
        if category == "mechanism_signal":
            row["new_signal"] = {
                "changed_fields": deepcopy(candidate.get("changed_fields", [])),
                "strategy_modification": candidate.get("strategy_modification"),
            }
        if extra:
            row.update(deepcopy(dict(extra)))
        compact_rows = ResearchPlannerV2.compact_candidate_history([row])
        if compact_rows:
            row = compact_rows[0]
        run_record["candidate_records"].append(row)
        state["candidate_records"] = deepcopy(run_record["candidate_records"])
        return row

    @staticmethod
    def _save_record(state: WorkflowState, run_record: dict[str, Any]) -> Path:
        run_dir = get_trace_run_dir(state)
        path = run_dir / "research_loop_v2_result.json"
        path.write_text(
            json.dumps(_json_safe(run_record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _read_trace_payload(path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"记录不是 JSON 对象: {path}")
        payload = raw.get("payload")
        return dict(payload) if isinstance(payload, Mapping) else raw

    @staticmethod
    def _candidate_number(candidate_id: object) -> int | None:
        text = str(candidate_id or "").strip()
        prefix = "candidate_"
        suffix = text.removeprefix(prefix)
        if not text.startswith(prefix) or not suffix.isdigit():
            return None
        number = int(suffix)
        return number if number >= 1 else None

    @classmethod
    def _research_only_resume_value(cls, value: object) -> object:
        """移除旧运行中研究结束后才产生的资料。"""

        excluded_fields = {
            "confirmation_report",
            "confirmation_candidates",
            "confirmation_stable",
            "selected_development_report",
            "final_report",
            "final_test_report",
            "final_test_result",
            "held_out_report",
        }
        if isinstance(value, list):
            return [cls._research_only_resume_value(item) for item in value]
        if isinstance(value, Mapping):
            return {
                str(key): cls._research_only_resume_value(item)
                for key, item in value.items()
                if str(key).strip().lower() not in excluded_fields
            }
        return deepcopy(value)

    @classmethod
    def _load_checked_strategy_from_epoch(
        cls,
        run_dir: Path,
        epoch_index: int,
    ) -> dict[str, object]:
        """读取旧轮次里已经通过检查的代码，并按保存的 SHA 唯一定位。"""

        epoch_dir = run_dir / f"epoch_{epoch_index:03d}"
        if not epoch_dir.is_dir():
            raise FileNotFoundError(f"找不到旧候选轮次目录: {epoch_dir}")
        validation_files = sorted(
            epoch_dir.glob("validateagent/round_*/structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        expected_hash = ""
        selected_round = ""
        for validation_file in validation_files:
            payload = cls._read_trace_payload(validation_file)
            summary = payload.get("validation_summary")
            if not isinstance(summary, Mapping) or summary.get("passed") is not True:
                continue
            value = summary.get("strategy_code_sha256")
            if isinstance(value, str) and value.strip():
                expected_hash = value.strip().lower()
                selected_round = validation_file.parent.parent.name
                break
        if not expected_hash or not selected_round.startswith("round_"):
            raise ValueError("旧候选没有保存通过检查的策略代码 SHA")

        code_matches: list[tuple[Path, str]] = []
        for code_path in epoch_dir.glob(
            f"strategyagent/{selected_round}/step_*.py"
        ):
            code_text = code_path.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
            if actual_hash == expected_hash:
                code_matches.append((code_path, code_text))
        if len(code_matches) != 1:
            raise ValueError("无法唯一找到与旧候选代码 SHA 一致的文件")
        code_path, code_text = code_matches[0]

        generation_meta: dict[str, object] = {}
        for meta_path in sorted(
            code_path.parent.glob("structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            payload = cls._read_trace_payload(meta_path)
            value = payload.get("strategy_generation_meta")
            if isinstance(value, Mapping):
                generation_meta = deepcopy(dict(value))
                break
        if not generation_meta:
            raise ValueError("旧候选缺少策略生成资料")
        return {
            "strategy_code": code_text,
            "strategy_code_path": str(code_path),
            "strategy_code_sha256": expected_hash,
            "strategy_generation_meta": generation_meta,
        }

    @classmethod
    def _load_selected_resume_source_bundle(
        cls,
        provenance: Mapping[str, object],
        *,
        visited_result_paths: frozenset[Path],
    ) -> dict[str, Any]:
        """核对续跑来源，并递归找到来源中通过检查的入选代码。"""

        if provenance.get("selected_research_report_reused") is not True:
            raise ValueError("续跑来源没有确认复用了研究期入选报告")

        source_experiment_id = str(provenance.get("experiment_id", "")).strip()
        source_candidate_id = str(
            provenance.get("selected_candidate_id", "")
        ).strip()
        source_result_text = str(provenance.get("result_path", "")).strip()
        source_code_text = str(
            provenance.get("strategy_code_path", "")
        ).strip()
        expected_hash = str(
            provenance.get("strategy_code_sha256", "")
        ).strip().lower()
        if not source_experiment_id:
            raise ValueError("续跑来源缺少实验编号")
        if cls._candidate_number(source_candidate_id) is None:
            raise ValueError("续跑来源缺少有效的入选候选编号")
        if not source_result_text:
            raise ValueError("续跑来源缺少结果文件路径")
        if not source_code_text:
            raise ValueError("续跑来源缺少策略代码路径")
        if len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            raise ValueError("续跑来源缺少有效的策略代码 SHA")

        source_result_path = Path(source_result_text).expanduser()
        source_code_path = Path(source_code_text).expanduser()
        if not source_result_path.is_absolute():
            raise ValueError("续跑来源的结果文件路径必须是绝对路径")
        if not source_code_path.is_absolute():
            raise ValueError("续跑来源的策略代码路径必须是绝对路径")
        source_result_path = source_result_path.resolve()
        source_code_path = source_code_path.resolve()

        source_bundle = cls._load_selected_candidate_resume_bundle(
            source_result_path,
            allow_stopped=True,
            _visited_result_paths=visited_result_paths,
        )
        if str(source_bundle.get("source_experiment_id", "")) != source_experiment_id:
            raise ValueError("续跑来源的实验编号与来源结果不一致")
        if str(source_bundle.get("selected_candidate_id", "")) != source_candidate_id:
            raise ValueError("续跑来源的入选候选编号与来源结果不一致")
        if str(source_bundle.get("strategy_code_sha256", "")).lower() != expected_hash:
            raise ValueError("续跑来源的策略代码 SHA 与来源结果不一致")

        loaded_code_path = Path(
            str(source_bundle.get("strategy_code_path", ""))
        ).expanduser()
        if (
            not loaded_code_path.is_absolute()
            or loaded_code_path.resolve() != source_code_path
        ):
            raise ValueError("续跑来源的策略代码路径与来源结果不一致")
        loaded_code = str(source_bundle.get("strategy_code", ""))
        actual_hash = hashlib.sha256(loaded_code.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("续跑来源的策略代码内容与保存的 SHA 不一致")

        saved_finished_at = str(
            provenance.get("source_finished_at", "")
        ).strip()
        if saved_finished_at != str(
            source_bundle.get("source_finished_at", "")
        ).strip():
            raise ValueError("续跑来源保存的完成时间与来源结果不一致")
        if provenance.get("source_was_stopped") is not bool(
            source_bundle.get("source_was_stopped", False)
        ):
            raise ValueError("续跑来源保存的运行状态与来源结果不一致")
        return source_bundle

    @classmethod
    def _load_selected_candidate_resume_bundle(
        cls,
        result_path: str | Path,
        *,
        allow_stopped: bool = False,
        _visited_result_paths: frozenset[Path] | None = None,
    ) -> dict[str, Any]:
        """从 V2 运行的研究期入选版本继续，不读取确认期表现。"""

        path = Path(result_path).expanduser().resolve()
        visited_result_paths = set(_visited_result_paths or ())
        if path in visited_result_paths:
            raise ValueError(f"续跑来源形成循环: {path}")
        visited_result_paths.add(path)
        frozen_visited_paths = frozenset(visited_result_paths)
        if not path.is_file():
            raise FileNotFoundError(f"找不到旧结果文件: {path}")
        old_record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(old_record, dict):
            raise ValueError("旧结果文件不是 JSON 对象")
        if str(old_record.get("schema_version", "")) != "research_loop_v2":
            raise ValueError("旧结果不是 V2 研究记录")
        source_finished_at = str(old_record.get("finished_at", "")).strip()
        if not source_finished_at and not allow_stopped:
            raise ValueError("只能从已经完成的 V2 运行继续研究")

        source_text = str(old_record.get("source_text", "")).strip()
        interpretation = old_record.get("input_interpretation")
        theory_book = old_record.get("theory_book")
        profile = old_record.get("runtime_profile")
        if not source_text or not isinstance(interpretation, Mapping):
            raise ValueError("旧结果缺少原始输入或输入整理结果")
        if not isinstance(theory_book, Mapping) or not theory_book:
            raise ValueError("旧结果缺少研究理论")
        if not isinstance(profile, Mapping):
            raise ValueError("旧结果缺少运行设置")

        selected_id = str(old_record.get("selected_candidate_id", "")).strip()
        selected_number = cls._candidate_number(selected_id)
        if selected_number is None:
            raise ValueError("旧结果缺少有效的 selected_candidate_id")
        raw_history = old_record.get("candidate_records")
        if not isinstance(raw_history, list):
            raise ValueError("旧结果缺少研究期候选记录")
        selected_record = next(
            (
                item
                for item in raw_history
                if isinstance(item, Mapping)
                and str(item.get("candidate_id", "")).strip() == selected_id
            ),
            None,
        )
        if not isinstance(selected_record, Mapping):
            raise ValueError("selected_candidate_id 在研究期候选记录中不存在")
        if (
            str(selected_record.get("status", "")).strip() != "adopted"
            or selected_record.get("adopted") is not True
        ):
            raise ValueError("selected_candidate_id 不是研究期已采用候选，不能作为起点")

        selected_report = cls._compact_resume_report(
            old_record.get("selected_research_report")
        )
        record_report = cls._compact_resume_report(
            selected_record.get("development_report")
        )
        if not selected_report or selected_report != record_report:
            raise ValueError("selected_research_report 与研究期候选记录不一致")
        if str(selected_report.get("period_kind", "")).lower() != "development":
            raise ValueError("入选报告不是研究期结果")
        if str(selected_report.get("candidate_id", "")) != selected_id:
            raise ValueError("入选报告的候选编号不正确")
        selected_epoch = int(selected_report.get("epoch_index", 0))
        if selected_epoch < 1:
            raise ValueError("入选研究期报告缺少轮次编号")

        research_history = ResearchPlannerV2.compact_candidate_history(
            cls._research_only_resume_value(raw_history)
        )
        if not research_history:
            raise ValueError("旧结果没有可恢复的研究期候选历史")
        existing_numbers = [
            number
            for row in research_history
            if (number := cls._candidate_number(row.get("candidate_id"))) is not None
        ]
        if selected_number not in existing_numbers:
            raise ValueError("入选候选没有出现在研究期历史中")

        raw_provenance = old_record.get("resumed_selected_from")
        source_bundle: dict[str, Any] | None = None
        if raw_provenance is not None:
            if not isinstance(raw_provenance, Mapping):
                raise ValueError("续跑来源记录不是 JSON 对象")
            source_bundle = cls._load_selected_resume_source_bundle(
                raw_provenance,
                visited_result_paths=frozen_visited_paths,
            )

        provenance_candidate_id = str(
            raw_provenance.get("selected_candidate_id", "")
            if isinstance(raw_provenance, Mapping)
            else ""
        ).strip()
        if source_bundle is not None and provenance_candidate_id == selected_id:
            source_report = cls._compact_resume_report(
                source_bundle.get("selected_research_report")
            )
            if source_report != selected_report:
                raise ValueError("续跑复用的研究期入选报告与来源结果不一致")
            checked_strategy = {
                "strategy_code": source_bundle["strategy_code"],
                "strategy_code_path": source_bundle["strategy_code_path"],
                "strategy_code_sha256": source_bundle["strategy_code_sha256"],
                "strategy_generation_meta": source_bundle[
                    "strategy_generation_meta"
                ],
            }
        else:
            checked_strategy = cls._load_checked_strategy_from_epoch(
                path.parent,
                selected_epoch,
            )
        planned = next(
            (
                item
                for item in old_record.get("planned_candidates", [])
                if isinstance(item, Mapping)
                and str(item.get("candidate_id", "")).strip() == selected_id
            ),
            {},
        )
        selected_candidate = deepcopy(dict(planned)) if isinstance(planned, Mapping) else {}
        selected_candidate.update(
            deepcopy(
                cast(
                    dict[str, object],
                    checked_strategy["strategy_generation_meta"],
                )
            )
        )
        selected_candidate["candidate_id"] = selected_id
        selected_candidate.setdefault(
            "category", str(selected_record.get("category", "baseline"))
        )

        _, required_categories = cls._profile_budget(profile)
        allowed_categories = set(required_categories)
        covered_categories: list[str] = []
        for item in research_history:
            if cls._candidate_number(item.get("candidate_id")) is None:
                continue
            if str(item.get("status", "")).strip() not in {"adopted", "rejected"}:
                continue
            category = str(
                item.get("category", item.get("research_category", ""))
            ).strip()
            if (
                category in allowed_categories
                and category not in covered_categories
            ):
                covered_categories.append(category)
        return {
            "source_result_path": str(path),
            "source_experiment_id": str(old_record.get("experiment_id", "")),
            "source_finished_at": source_finished_at,
            "source_was_stopped": not bool(source_finished_at),
            "source_text": source_text,
            "runtime_profile": deepcopy(dict(profile)),
            "input_interpretation": deepcopy(dict(interpretation)),
            "theory_book": deepcopy(dict(theory_book)),
            "candidate_history": research_history,
            "selected_candidate": selected_candidate,
            "selected_candidate_id": selected_id,
            "selected_research_report": selected_report,
            "covered_categories": covered_categories,
            "next_candidate_number": max(existing_numbers) + 1,
            **checked_strategy,
        }

    @classmethod
    def _load_baseline_resume_bundle(
        cls,
        result_path: str | Path,
    ) -> dict[str, Any]:
        """读取一次已完成的 V2 基准，后续不重复计算基准四段。"""

        path = Path(result_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到旧结果文件: {path}")
        old_record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(old_record, dict):
            raise ValueError("旧结果文件不是 JSON 对象")
        if str(old_record.get("schema_version", "")) != "research_loop_v2":
            raise ValueError("旧结果不是 V2 研究记录")

        source_text = str(old_record.get("source_text", "")).strip()
        interpretation = old_record.get("input_interpretation")
        theory_book = old_record.get("theory_book")
        profile = old_record.get("runtime_profile")
        if not source_text or not isinstance(interpretation, Mapping):
            raise ValueError("旧结果缺少原始输入或输入整理结果")
        if not isinstance(theory_book, Mapping) or not theory_book:
            raise ValueError("旧结果缺少竞争解释")
        if not isinstance(profile, Mapping):
            raise ValueError("旧结果缺少运行设置")

        baseline_record = next(
            (
                item
                for item in old_record.get("candidate_records", [])
                if isinstance(item, Mapping)
                and str(item.get("candidate_id", "")) == "candidate_001"
            ),
            None,
        )
        if not isinstance(baseline_record, Mapping):
            raise ValueError("旧结果缺少 candidate_001 基准记录")
        baseline_report = cls._compact_resume_report(
            baseline_record.get("development_report")
        )
        if not baseline_report:
            raise ValueError("旧结果缺少完整的基准开发期指标")
        if str(baseline_report.get("period_kind", "")).lower() != "development":
            raise ValueError("旧基准报告不是开发期结果")
        if str(baseline_report.get("candidate_id", "")) != "candidate_001":
            raise ValueError("旧基准报告的候选编号不正确")
        baseline_epoch_index = int(baseline_report.get("epoch_index", 0))
        if baseline_epoch_index < 1:
            raise ValueError("旧基准报告缺少轮次编号")

        run_dir = path.parent
        baseline_epoch_dir = run_dir / f"epoch_{baseline_epoch_index:03d}"
        if not baseline_epoch_dir.is_dir():
            raise FileNotFoundError(f"找不到旧基准轮次目录: {baseline_epoch_dir}")
        baseline_files = sorted(
            baseline_epoch_dir.glob("baselinebuilderv2/structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        )
        if not baseline_files:
            raise FileNotFoundError("旧实验目录里找不到基准定义")
        baseline = cls._read_trace_payload(baseline_files[-1])

        validation_files = sorted(
            baseline_epoch_dir.glob("validateagent/round_*/structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        )
        expected_hash = ""
        for validation_file in reversed(validation_files):
            payload = cls._read_trace_payload(validation_file)
            summary = payload.get("validation_summary")
            if isinstance(summary, Mapping):
                value = summary.get("strategy_code_sha256")
                if isinstance(value, str) and value.strip():
                    expected_hash = value.strip().lower()
                    break
        if not expected_hash:
            raise ValueError("旧实验没有保存通过检查的策略代码校验值")

        code_matches: list[tuple[Path, str]] = []
        for code_path in sorted(
            baseline_epoch_dir.glob("strategyagent/round_*/step_*.py"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            code_text = code_path.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
            if actual_hash == expected_hash:
                code_matches.append((code_path, code_text))
        if len(code_matches) != 1:
            raise ValueError("无法唯一找到与校验值一致的旧基准代码")
        matched_code_path, matched_code = code_matches[0]

        strategy_meta: dict[str, Any] = {}
        for meta_path in sorted(
            matched_code_path.parent.glob("structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            payload = cls._read_trace_payload(meta_path)
            value = payload.get("strategy_generation_meta")
            if isinstance(value, Mapping):
                strategy_meta = dict(value)
                break

        return {
            "source_result_path": str(path),
            "source_experiment_id": str(old_record.get("experiment_id", "")),
            "source_text": source_text,
            "runtime_profile": deepcopy(dict(profile)),
            "input_interpretation": deepcopy(dict(interpretation)),
            "theory_book": deepcopy(dict(theory_book)),
            "baseline": baseline,
            "baseline_report": baseline_report,
            "strategy_code": matched_code,
            "strategy_code_path": str(matched_code_path),
            "strategy_code_sha256": expected_hash,
            "strategy_generation_meta": strategy_meta,
        }

    @staticmethod
    def _json_sha256(value: object) -> str:
        encoded = json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _load_rule_fix_recheck_bundle(
        cls,
        result_path: str | Path,
    ) -> dict[str, Any]:
        """读取旧基准与004代码，但不复用它们原来的开发期报告。"""

        requested_path = Path(result_path).expanduser().resolve()
        if not requested_path.is_file():
            raise FileNotFoundError(f"找不到旧结果文件: {requested_path}")

        source_chain: list[tuple[Path, dict[str, Any]]] = []
        visited: set[Path] = set()
        current_path = requested_path
        while True:
            if current_path in visited:
                raise ValueError(f"规则修复重跑来源形成循环: {current_path}")
            visited.add(current_path)
            raw_record = json.loads(current_path.read_text(encoding="utf-8"))
            if not isinstance(raw_record, dict):
                raise ValueError("规则修复重跑来源不是 JSON 对象")
            if str(raw_record.get("schema_version", "")) != "research_loop_v2":
                raise ValueError("规则修复重跑来源不是 V2 研究记录")
            source_chain.append((current_path, raw_record))

            has_baseline = any(
                isinstance(item, Mapping)
                and str(item.get("candidate_id", "")) == "candidate_001"
                for item in raw_record.get("candidate_records", [])
            )
            has_candidate_004_plan = any(
                isinstance(item, Mapping)
                and str(item.get("candidate_id", "")) == "candidate_004"
                for item in raw_record.get("planned_candidates", [])
            )
            if has_baseline and has_candidate_004_plan:
                definition_path = current_path
                definition_record = raw_record
                break

            provenance = raw_record.get("resumed_selected_from")
            if not isinstance(provenance, Mapping):
                raise ValueError("规则修复重跑来源缺少 candidate_004 完整计划")
            parent_text = str(provenance.get("result_path", "")).strip()
            if not parent_text:
                raise ValueError("规则修复重跑来源缺少上一次结果文件路径")
            parent_path = Path(parent_text).expanduser()
            if not parent_path.is_absolute():
                raise ValueError("规则修复重跑的上一次结果文件路径必须是绝对路径")
            current_path = parent_path.resolve()
            if not current_path.is_file():
                raise FileNotFoundError(
                    f"找不到规则修复重跑的上一次结果文件: {current_path}"
                )

        requested_record = source_chain[0][1]
        selected_bundle = cls._load_selected_candidate_resume_bundle(
            requested_path,
            allow_stopped=True,
        )
        if str(selected_bundle.get("selected_candidate_id", "")) != "candidate_004":
            raise ValueError("规则修复重跑只接受 candidate_004 为旧研究期入选版本")

        baseline_bundle = cls._load_baseline_resume_bundle(definition_path)
        for key in (
            "source_text",
            "input_interpretation",
            "theory_book",
            "runtime_profile",
        ):
            if selected_bundle.get(key) != baseline_bundle.get(key):
                raise ValueError(f"基准与 candidate_004 的来源资料不一致: {key}")

        candidate_004_plan = next(
            (
                deepcopy(dict(item))
                for item in definition_record.get("planned_candidates", [])
                if isinstance(item, Mapping)
                and str(item.get("candidate_id", "")) == "candidate_004"
            ),
            None,
        )
        if not isinstance(candidate_004_plan, dict):
            raise ValueError("规则修复重跑来源缺少 candidate_004 完整计划")
        required_plan_fields = (
            "hypothesis",
            "strategy_modification",
            "category",
            "candidate_mode",
            "required_data",
            "backtest_datasets",
        )
        missing_plan_fields = [
            field
            for field in required_plan_fields
            if field not in candidate_004_plan
            or candidate_004_plan.get(field) in (None, "", [], {})
        ]
        if missing_plan_fields:
            raise ValueError(
                "candidate_004 完整计划缺少字段: "
                + ", ".join(missing_plan_fields)
            )

        raw_history = requested_record.get("candidate_records")
        if not isinstance(raw_history, list):
            raise ValueError("规则修复重跑来源缺少旧候选记录")
        old_candidate_004_record = next(
            (
                item
                for item in raw_history
                if isinstance(item, Mapping)
                and str(item.get("candidate_id", "")) == "candidate_004"
            ),
            None,
        )
        if not isinstance(old_candidate_004_record, Mapping):
            raise ValueError("规则修复重跑来源缺少 candidate_004 旧记录")
        if (
            str(old_candidate_004_record.get("status", "")) != "adopted"
            or old_candidate_004_record.get("adopted") is not True
        ):
            raise ValueError("candidate_004 旧记录不是已采用版本")

        profile = cast(Mapping[str, object], selected_bundle["runtime_profile"])
        _, required_categories = cls._profile_budget(profile)
        allowed_categories = set(required_categories)
        covered_categories: list[str] = []
        legacy_history: list[dict[str, object]] = []
        completed_numbers: set[int] = set()
        legacy_note = (
            "旧实现受历史成分边界偏差影响，估计约1%-1.7%；"
            "这里只保留以前试过的事实，旧指标不作为规则修复后的结果。"
        )
        for raw_item in raw_history:
            if not isinstance(raw_item, Mapping):
                continue
            candidate_id = str(raw_item.get("candidate_id", "")).strip()
            number = cls._candidate_number(candidate_id)
            status = str(raw_item.get("status", "")).strip()
            if number is None:
                continue
            if status in {"adopted", "rejected"}:
                completed_numbers.add(number)
                category = str(
                    raw_item.get("category", raw_item.get("research_category", ""))
                ).strip()
                if category in allowed_categories and category not in covered_categories:
                    covered_categories.append(category)
            if candidate_id in {"candidate_001", "candidate_004"}:
                continue
            if status != "rejected":
                continue

            old_report = cls._compact_resume_report(raw_item.get("development_report"))
            old_judge = raw_item.get("judge_result")
            category = str(
                raw_item.get("category", raw_item.get("research_category", ""))
            ).strip()
            legacy_row: dict[str, object] = {
                "candidate_id": candidate_id,
                "category": category,
                "research_category": category,
                "mechanism_id": deepcopy(raw_item.get("mechanism_id")),
                "research_question": deepcopy(raw_item.get("research_question")),
                "change_kind": deepcopy(raw_item.get("change_kind")),
                "changed_fields": deepcopy(raw_item.get("changed_fields", [])),
                "change_spec": deepcopy(raw_item.get("change_spec", {})),
                "predicted_changes": deepcopy(
                    raw_item.get("predicted_changes", [])
                ),
                "status": "rejected",
                "development_report": {},
                "judge_result": {},
                "adopted": False,
                "legacy_evidence_only": True,
                "legacy_result_status": "rejected_before_rule_fix",
                "legacy_comparison_note": legacy_note,
            }
            if category == "mechanism_signal":
                legacy_row["new_signal"] = deepcopy(raw_item.get("new_signal", {}))
            if old_report:
                legacy_row["legacy_development_report_sha256"] = cls._json_sha256(
                    old_report
                )
            if isinstance(old_judge, Mapping) and old_judge:
                legacy_row["legacy_judge_result_sha256"] = cls._json_sha256(
                    old_judge
                )
            legacy_history.append(legacy_row)

        if any(number >= 8 for number in completed_numbers):
            raise ValueError("来源中已有 candidate_008 或更后面的正式结果，不能重复编号")

        has_number_007 = any(
            cls._candidate_number(item.get("candidate_id")) == 7
            for item in legacy_history
        )
        if not has_number_007:
            candidate_007_plan: dict[str, object] | None = None
            for _, record in source_chain:
                candidate_007_plan = next(
                    (
                        deepcopy(dict(item))
                        for item in record.get("planned_candidates", [])
                        if isinstance(item, Mapping)
                        and str(item.get("candidate_id", "")) == "candidate_007"
                    ),
                    None,
                )
                if candidate_007_plan is not None:
                    break
            if candidate_007_plan is None:
                raise ValueError("来源缺少 candidate_007 历史记录或计划，无法从008继续")
            legacy_history.append(
                {
                    "candidate_id": "candidate_007",
                    "category": candidate_007_plan.get("category"),
                    "research_category": candidate_007_plan.get("category"),
                    "mechanism_id": candidate_007_plan.get("mechanism_id"),
                    "research_question": candidate_007_plan.get("research_question"),
                    "change_kind": candidate_007_plan.get("change_kind"),
                    "changed_fields": deepcopy(
                        candidate_007_plan.get("changed_fields", [])
                    ),
                    "change_spec": {
                        "strategy_modification": candidate_007_plan.get(
                            "strategy_modification"
                        ),
                        "changed_fields": deepcopy(
                            candidate_007_plan.get("changed_fields", [])
                        ),
                    },
                    "predicted_changes": deepcopy(
                        candidate_007_plan.get("predicted_changes", [])
                    ),
                    "status": "legacy_unusable",
                    "development_report": {},
                    "judge_result": {},
                    "adopted": False,
                    "legacy_evidence_only": True,
                    "legacy_result_status": "incomplete_before_rule_fix",
                    "legacy_comparison_note": (
                        "旧 candidate_007 没有可用的完整正式结果，只保留编号和计划；"
                        "不计入类别覆盖，也不作为规则修复后的结果。"
                    ),
                }
            )

        legacy_history.sort(
            key=lambda item: cls._candidate_number(item.get("candidate_id")) or 0
        )
        return {
            "source_result_path": str(requested_path),
            "source_experiment_id": str(requested_record.get("experiment_id", "")),
            "source_finished_at": str(requested_record.get("finished_at", "")),
            "source_was_stopped": not bool(
                str(requested_record.get("finished_at", "")).strip()
            ),
            "definition_result_path": str(definition_path),
            "source_text": selected_bundle["source_text"],
            "runtime_profile": deepcopy(dict(profile)),
            "input_interpretation": deepcopy(
                cast(dict[str, object], selected_bundle["input_interpretation"])
            ),
            "theory_book": deepcopy(
                cast(dict[str, object], selected_bundle["theory_book"])
            ),
            "baseline": deepcopy(
                cast(dict[str, object], baseline_bundle["baseline"])
            ),
            "baseline_old_report_sha256": cls._json_sha256(
                baseline_bundle["baseline_report"]
            ),
            "baseline_strategy_code": baseline_bundle["strategy_code"],
            "baseline_strategy_code_path": baseline_bundle["strategy_code_path"],
            "baseline_strategy_code_sha256": baseline_bundle[
                "strategy_code_sha256"
            ],
            "baseline_strategy_generation_meta": baseline_bundle[
                "strategy_generation_meta"
            ],
            "candidate_004_plan": candidate_004_plan,
            "candidate_004_old_report_sha256": cls._json_sha256(
                selected_bundle["selected_research_report"]
            ),
            "candidate_004_strategy_code": selected_bundle["strategy_code"],
            "candidate_004_strategy_code_path": selected_bundle[
                "strategy_code_path"
            ],
            "candidate_004_strategy_code_sha256": selected_bundle[
                "strategy_code_sha256"
            ],
            "candidate_004_strategy_generation_meta": selected_bundle[
                "strategy_generation_meta"
            ],
            "legacy_history": legacy_history,
            "covered_categories": covered_categories,
            "next_candidate_number": 8,
            "old_reports_reused": False,
            "old_confirmation_reused": False,
            "old_final_test_reused": False,
        }

    @staticmethod
    def _compact_resume_report(value: object) -> dict[str, object]:
        return ResearchPlannerV2.compact_development_report(
            value if isinstance(value, Mapping) else {}
        )

    @classmethod
    def _load_probe_resume_bundle(
        cls,
        trace_path: str | Path,
    ) -> dict[str, Any]:
        """读取尚未完成的相邻数值比较，复用已经算完的数值。"""

        path = Path(trace_path).expanduser().resolve()
        if path.is_file():
            run_dir = path.parent
        elif path.is_dir() and (path / "research_loop_v2_result.json").is_file():
            run_dir = path
        elif path.is_dir():
            candidates = sorted(
                path.glob("epochs_*/research_loop_v2_result.json"),
                key=lambda item: item.stat().st_mtime_ns,
            )
            if not candidates:
                raise FileNotFoundError(f"找不到部分运行结果: {path}")
            run_dir = candidates[-1].parent
        else:
            raise FileNotFoundError(f"找不到部分运行目录: {path}")

        result_path = run_dir / "research_loop_v2_result.json"
        partial_record = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(partial_record, Mapping):
            raise ValueError("部分运行结果不是JSON对象")
        if str(partial_record.get("schema_version", "")) != "research_loop_v2":
            raise ValueError("部分运行结果不是V2研究记录")
        partial_source_text = str(partial_record.get("source_text", "")).strip()
        partial_profile = partial_record.get("runtime_profile")
        partial_baseline = partial_record.get("resumed_from")
        if not partial_source_text or not isinstance(partial_profile, Mapping):
            raise ValueError("部分运行缺少原始输入或运行设置")
        if not isinstance(partial_baseline, Mapping):
            raise ValueError("部分运行没有记录其基准来源")
        partial_baseline_sha = str(
            partial_baseline.get("strategy_code_sha256", "")
        ).strip().lower()
        if not partial_baseline_sha:
            raise ValueError("部分运行没有记录基准代码校验值")

        planned_files = sorted(
            run_dir.glob("epoch_*/researchplannerv2/planned_candidate/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        )
        if not planned_files:
            raise FileNotFoundError("部分运行里没有已经规划的候选")
        candidate = cls._read_trace_payload(planned_files[-1])
        probe = candidate.get("parameter_probe")
        if not isinstance(probe, Mapping):
            raise ValueError("部分运行的最后一个候选不是数值比较")
        param_name = str(probe.get("param_name", "")).strip()
        raw_values = probe.get("values")
        if not param_name or not isinstance(raw_values, list):
            raise ValueError("部分运行的数值比较定义不完整")
        values = [float(value) for value in raw_values]
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if candidate_id != "candidate_002":
            raise ValueError("目前只允许从第一个正式改进候选继续")

        completed_by_index: dict[int, dict[str, object]] = {}
        prefix = f"probe_{param_name}_"
        for report_path in sorted(
            run_dir.glob("epoch_*/strategytester/development_evaluation/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        ):
            payload = cls._read_trace_payload(report_path)
            report_candidate_id = str(payload.get("candidate_id", ""))
            if not report_candidate_id.startswith(prefix):
                continue
            suffix = report_candidate_id[len(prefix):]
            try:
                index = int(suffix)
            except ValueError:
                continue
            if index < 1 or index > len(values):
                continue
            if index in completed_by_index:
                raise ValueError(f"数值比较编号重复: {report_candidate_id}")
            compact = cls._compact_resume_report(payload)
            if compact:
                completed_by_index[index] = {
                    "param_name": param_name,
                    "value": values[index - 1],
                    "development_report": compact,
                    "source_report_path": str(report_path),
                }
        if not completed_by_index:
            raise ValueError("部分运行没有已完成的数值比较结果")

        completed_indexes = sorted(completed_by_index)
        expected_prefix = list(range(1, max(completed_indexes) + 1))
        if completed_indexes != expected_prefix:
            raise ValueError("已完成的数值比较不是连续前缀，无法安全继续")
        if len(completed_indexes) >= len(values):
            raise ValueError("全部相邻数值都已完成，不应按部分数值继续")

        report_epochs = {
            int(
                cast(Mapping[str, object], completed_by_index[index]["development_report"]).get(
                    "epoch_index", 0
                )
            )
            for index in completed_indexes
        }
        if any(epoch < 2 for epoch in report_epochs):
            raise ValueError("已完成的数值报告缺少有效轮次编号")
        first_report_epoch = min(report_epochs)
        code_epoch = first_report_epoch - 1
        code_epoch_dir = run_dir / f"epoch_{code_epoch:03d}"
        validation_files = sorted(
            code_epoch_dir.glob("validateagent/round_*/structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        )
        expected_hash = ""
        selected_round = ""
        for validation_file in reversed(validation_files):
            payload = cls._read_trace_payload(validation_file)
            summary = payload.get("validation_summary")
            if not isinstance(summary, Mapping) or summary.get("passed") is not True:
                continue
            value = summary.get("strategy_code_sha256")
            if isinstance(value, str) and value.strip():
                expected_hash = value.strip().lower()
                selected_round = validation_file.parent.parent.name
                break
        if not expected_hash or not selected_round.startswith("round_"):
            raise ValueError("部分运行没有通过检查的数值比较代码")
        try:
            round_number = int(selected_round.removeprefix("round_"))
        except ValueError as exc:
            raise ValueError("数值比较代码的检查轮次无法识别") from exc

        code_matches: list[tuple[Path, str]] = []
        for code_path in code_epoch_dir.glob(
            f"strategyagent/{selected_round}/step_*.py"
        ):
            code_text = code_path.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
            if actual_hash == expected_hash:
                code_matches.append((code_path, code_text))
        if len(code_matches) != 1:
            raise ValueError("无法唯一找到已通过检查的数值比较代码")
        probe_code_path, probe_code = code_matches[0]

        future_checks = sorted(
            code_epoch_dir.glob(
                "researchloopv2/future_data_check/"
                f"attempt_{round_number:02d}_*.json"
            ),
            key=lambda item: item.stat().st_mtime_ns,
        )
        if not any(
            cls._read_trace_payload(item).get("passed") is True
            for item in future_checks
        ):
            raise ValueError("旧数值比较代码没有通过未来数据检查")

        probe_generation_meta: dict[str, object] = {}
        for meta_path in sorted(
            probe_code_path.parent.glob("structured_output/*.json"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        ):
            payload = cls._read_trace_payload(meta_path)
            value = payload.get("strategy_generation_meta")
            if isinstance(value, Mapping):
                probe_generation_meta = deepcopy(dict(value))
                break

        return {
            "source_run_dir": str(run_dir),
            "candidate": candidate,
            "candidate_id": candidate_id,
            "probe_results": [
                completed_by_index[index]
                for index in completed_indexes
            ],
            "completed_values": [values[index - 1] for index in completed_indexes],
            "remaining_values": values[len(completed_indexes):],
            "probe_strategy_code": probe_code,
            "probe_strategy_code_path": str(probe_code_path),
            "probe_strategy_code_sha256": expected_hash,
            "probe_strategy_generation_meta": probe_generation_meta,
            "source_text": partial_source_text,
            "runtime_profile": deepcopy(dict(partial_profile)),
            "baseline_strategy_code_sha256": partial_baseline_sha,
        }

    def _restore_saved_candidate_strategy(
        self,
        state: WorkflowState,
        *,
        candidate: Mapping[str, object],
        candidate_id: str,
        code_text: str,
        saved_generation_meta: Mapping[str, object],
        expected_hash: str,
        experiment_spec: Mapping[str, object],
    ) -> None:
        """重新装载通过检查的候选代码，不重复已有的开发期计算。"""

        actual_hash = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash.lower():
            raise ValueError("续跑候选代码的校验值发生变化")
        self._prepare_candidate_state(
            state,
            candidate,
            candidate_id=candidate_id,
            epoch_index=self._allocate_epoch(),
            experiment_spec=experiment_spec,
        )
        strategy_context = self.strategy_agent._build_strategy_context(state)
        namespace = self.strategy_agent._build_sandbox_namespace(strategy_context)
        output, success = self.strategy_agent._execute_code(code_text, namespace)
        if not success:
            raise RuntimeError(f"续跑候选代码无法执行: {output}")
        valid, message = self.strategy_agent._validate_output(code_text, namespace)
        if not valid:
            raise RuntimeError(f"续跑候选代码固定检查失败: {message}")

        strategy_result = {
            "output_weights": namespace.get("output_weights_df"),
            "strategy_output": namespace.get("strategy_output"),
            "params": namespace.get("params", {}),
            "required_data": strategy_context.get("required_data", []),
            "required_data_descriptions": strategy_context.get(
                "required_data_descriptions", ""
            ),
            "universe": strategy_context.get("universe", []),
            "backtest_datasets": strategy_context.get("backtest_datasets", []),
            "train_data_bundle": strategy_context.get("train_data_bundle", {}),
            "validate_data_bundle": strategy_context.get("validate_data_bundle", {}),
            "train_period": {
                "start": strategy_context.get("train_start"),
                "end": strategy_context.get("train_end"),
            },
            "validate_period": {
                "start": strategy_context.get("validate_start"),
                "end": strategy_context.get("validate_end"),
            },
        }
        generation_meta = self.strategy_agent._build_strategy_generation_meta(
            strategy_context,
            strategy_result,
        )
        generation_meta.update(deepcopy(dict(saved_generation_meta)))
        generation_meta["resumed_from_checked_baseline"] = True
        generation_meta["strategy_code_sha256"] = actual_hash
        state["strategy_code"] = code_text
        state["code_text"] = code_text
        state["strategy_result"] = strategy_result
        state["strategy_generation_meta"] = generation_meta
        state["phase"] = "validate"

        future_data_report = self._check_daily_future_data(state, experiment_spec)
        if future_data_report.get("passed") is not True:
            raise RuntimeError("续跑候选没有通过未来数据检查")
        strategy_result["future_data_check"] = deepcopy(future_data_report)
        generation_meta["future_data_check"] = deepcopy(future_data_report)
        position_plan_report = future_data_report.get("position_plan_check")
        if isinstance(position_plan_report, Mapping):
            state["position_plan_check"] = deepcopy(  # type: ignore[typeddict-unknown-key]
                dict(position_plan_report)
            )
            strategy_result["position_plan_check"] = deepcopy(
                dict(position_plan_report)
            )
            generation_meta["position_plan_check"] = deepcopy(
                dict(position_plan_report)
            )
        write_trace_json(
            state,
            agent_name="ResearchLoopV2",
            stage="resume_future_data_check",
            payload=future_data_report,
        )
        self.validate_agent.run(state)
        if state.get("phase") != "test":
            raise RuntimeError("续跑候选没有通过固定输出检查")

    def _restore_saved_baseline_strategy(
        self,
        state: WorkflowState,
        *,
        baseline: Mapping[str, object],
        code_text: str,
        saved_generation_meta: Mapping[str, object],
        expected_hash: str,
        experiment_spec: Mapping[str, object],
    ) -> None:
        self._restore_saved_candidate_strategy(
            state,
            candidate=baseline,
            candidate_id="candidate_001",
            code_text=code_text,
            saved_generation_meta=saved_generation_meta,
            expected_hash=expected_hash,
            experiment_spec=experiment_spec,
        )

    @staticmethod
    def _rule_fix_recheck_candidate(
        plan: Mapping[str, object],
        *,
        candidate_id: str,
        source_code_sha256: str,
    ) -> dict[str, Any]:
        candidate = deepcopy(dict(plan))
        original_hypothesis = str(candidate.get("hypothesis", "")).strip()
        original_modification = str(
            candidate.get("strategy_modification", "")
        ).strip()
        if not original_hypothesis or not original_modification:
            raise ValueError(f"{candidate_id} 缺少完整策略定义，不能做规则修复重跑")
        candidate["candidate_id"] = candidate_id
        candidate["candidate_mode"] = "modify_one_rule"
        candidate["hypothesis"] = (
            "【规则修复后重算】下面的信号、数值、股票范围、买卖时间、持仓比例、"
            "费用和滑点定义保持不变；本轮不提出新策略。\n"
            + original_hypothesis
        )
        candidate["strategy_modification"] = (
            "以保存的已检查旧代码为起点，只做通过当前固定检查所需的最小技术修正。"
            "按决定日和下一实际执行日正确检查历史指数成分边界；固定持仓计划开始后，"
            "不得因持有期间退出指数而改动原目标。不得改变任何信号条件、数值、"
            "股票范围、买卖时间、持仓比例、费用或滑点。原策略修改定义为："
            + original_modification
        )
        candidate["rule_fix_recheck"] = {
            "source_code_sha256": source_code_sha256,
            "economic_rules_must_remain_unchanged": True,
            "old_development_report_reused": False,
        }
        return candidate

    @staticmethod
    def _ensure_fresh_rule_fix_report(
        report: Mapping[str, object],
        *,
        candidate_id: str,
        epoch_index: int,
        experiment_spec: Mapping[str, object],
    ) -> None:
        if str(report.get("period_kind", "")).strip().lower() != "development":
            raise RuntimeError(f"{candidate_id} 修正后报告不是开发期报告")
        if str(report.get("candidate_id", "")) != candidate_id:
            raise RuntimeError(f"{candidate_id} 修正后报告编号不正确")
        if int(report.get("epoch_index", 0)) != int(epoch_index):
            raise RuntimeError(f"{candidate_id} 修正后报告不是本次新计算结果")
        expected_fold_count = int(experiment_spec.get("development_folds", 0))
        actual_fold_count = int(report.get("fold_count", 0))
        folds = report.get("folds")
        if (
            expected_fold_count != 4
            or actual_fold_count != expected_fold_count
            or not isinstance(folds, list)
            or len(folds) != expected_fold_count
        ):
            raise RuntimeError(
                f"{candidate_id} 必须重新完成2018至2021年的四折开发期计算"
            )

    def _run_rule_fix_rechecks(
        self,
        state: WorkflowState,
        run_record: dict[str, Any],
        bundle: Mapping[str, object],
        *,
        experiment_spec: Mapping[str, object],
    ) -> dict[str, object]:
        """修正并重算001与004，再用通常的固定规则决定从谁继续。"""

        baseline_candidate = self._rule_fix_recheck_candidate(
            cast(Mapping[str, object], bundle["baseline"]),
            candidate_id="candidate_001",
            source_code_sha256=str(bundle["baseline_strategy_code_sha256"]),
        )
        candidate_004 = self._rule_fix_recheck_candidate(
            cast(Mapping[str, object], bundle["candidate_004_plan"]),
            candidate_id="candidate_004",
            source_code_sha256=str(bundle["candidate_004_strategy_code_sha256"]),
        )
        run_record["planned_candidates"].extend(
            [deepcopy(baseline_candidate), deepcopy(candidate_004)]
        )
        write_trace_json(
            state,
            agent_name="ResearchLoopV2",
            stage="rule_fix_recheck_plan",
            payload={
                "candidate_001": baseline_candidate,
                "candidate_004": candidate_004,
                "old_reports_reused": False,
            },
        )
        self._save_record(state, run_record)

        def repair_and_test(
            candidate: Mapping[str, object],
            *,
            candidate_id: str,
            old_code: str,
            old_code_sha256: str,
        ) -> tuple[dict[str, object], str]:
            state["strategy_code"] = old_code
            state["code_text"] = old_code
            self._generate_and_validate(
                state,
                candidate,
                candidate_id=candidate_id,
                experiment_spec=experiment_spec,
            )
            new_code = str(state.get("strategy_code") or state.get("code_text") or "")
            new_sha256 = hashlib.sha256(new_code.encode("utf-8")).hexdigest()
            if not new_code.strip() or new_sha256 == old_code_sha256.lower():
                raise RuntimeError(f"{candidate_id} 没有生成规则修复后的新代码")
            report = self._run_development_test(
                state,
                experiment_spec=experiment_spec,
            )
            self._ensure_fresh_rule_fix_report(
                report,
                candidate_id=candidate_id,
                epoch_index=int(state.get("epoch_index", 0)),
                experiment_spec=experiment_spec,
            )
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="rule_fix_recheck_completed",
                payload={
                    "candidate_id": candidate_id,
                    "source_code_sha256": old_code_sha256,
                    "repaired_code_sha256": new_sha256,
                    "development_report": report,
                    "old_development_report_reused": False,
                },
            )
            return report, new_sha256

        try:
            baseline_report, baseline_new_sha = repair_and_test(
                baseline_candidate,
                candidate_id="candidate_001",
                old_code=str(bundle["baseline_strategy_code"]),
                old_code_sha256=str(bundle["baseline_strategy_code_sha256"]),
            )
            self._record_candidate(
                run_record,
                state,
                candidate_id="candidate_001",
                candidate=baseline_candidate,
                report=baseline_report,
                status="adopted",
                judge_result={
                    "adopted": True,
                    "adoption_kind": "starting_baseline_after_rule_fix",
                },
                extra={
                    "rule_fix_recheck": {
                        "source_code_sha256": bundle[
                            "baseline_strategy_code_sha256"
                        ],
                        "repaired_code_sha256": baseline_new_sha,
                        "old_development_report_sha256": bundle[
                            "baseline_old_report_sha256"
                        ],
                        "old_development_report_reused": False,
                    }
                },
            )
            self._compact_strategy_state(state)
            baseline_snapshot = capture_candidate_snapshot(
                cast(Any, state),
                candidate_id="candidate_001",
                save_as_accepted=True,
            )
            accepted_snapshots: list[dict[str, object]] = [
                {
                    "candidate_id": "candidate_001",
                    "snapshot": baseline_snapshot,
                    "research_report": deepcopy(baseline_report),
                }
            ]

            run_record["candidate_records"].extend(
                deepcopy(cast(list[dict[str, object]], bundle["legacy_history"]))
            )
            state["candidate_records"] = deepcopy(run_record["candidate_records"])

            candidate_004_report, candidate_004_new_sha = repair_and_test(
                candidate_004,
                candidate_id="candidate_004",
                old_code=str(bundle["candidate_004_strategy_code"]),
                old_code_sha256=str(bundle["candidate_004_strategy_code_sha256"]),
            )
            judge_result = self.judge.run(
                baseline_report,
                candidate_004_report,
                {
                    "predicted_changes": deepcopy(
                        candidate_004.get("predicted_changes", [])
                    ),
                    "mechanism_id": candidate_004.get("mechanism_id"),
                    "rule_fix_recheck": True,
                },
                {
                    "require_cost_stress": False,
                    "require_delay_stress": False,
                    "min_fold_count": int(experiment_spec["development_folds"]),
                    "min_trade_count": 100,
                },
            )
            status = "adopted" if judge_result.get("adopted") is True else "rejected"
            self._record_candidate(
                run_record,
                state,
                candidate_id="candidate_004",
                candidate=candidate_004,
                report=candidate_004_report,
                status=status,
                judge_result=judge_result,
                extra={
                    "rule_fix_recheck": {
                        "source_code_sha256": bundle[
                            "candidate_004_strategy_code_sha256"
                        ],
                        "repaired_code_sha256": candidate_004_new_sha,
                        "old_development_report_sha256": bundle[
                            "candidate_004_old_report_sha256"
                        ],
                        "old_development_report_reused": False,
                        "compared_against_fresh_candidate_001": True,
                    }
                },
            )
            run_record["mechanism_updates"].extend(
                deepcopy(judge_result.get("mechanism_updates", []))
            )
            if judge_result.get("adopted") is True:
                self._compact_strategy_state(state)
                candidate_004_snapshot = capture_candidate_snapshot(
                    cast(Any, state),
                    candidate_id="candidate_004",
                    save_as_accepted=True,
                )
                accepted_snapshots = [
                    {
                        "candidate_id": "candidate_004",
                        "snapshot": candidate_004_snapshot,
                        "research_report": deepcopy(candidate_004_report),
                    }
                ]
                accepted_report = deepcopy(candidate_004_report)
                selected_id = "candidate_004"
            else:
                restore_accepted_snapshot(cast(Any, state))
                accepted_report = deepcopy(baseline_report)
                selected_id = "candidate_001"

            run_record["candidate_records"].sort(
                key=lambda item: self._candidate_number(item.get("candidate_id")) or 0
            )
            state["candidate_records"] = deepcopy(run_record["candidate_records"])
            run_record["selected_candidate_id"] = selected_id
            run_record["selected_research_report"] = deepcopy(accepted_report)
            recheck_meta = cast(dict[str, Any], run_record["rule_fix_recheck_from"])
            recheck_meta.update(
                {
                    "status": "completed",
                    "candidate_001_repaired_code_sha256": baseline_new_sha,
                    "candidate_004_repaired_code_sha256": candidate_004_new_sha,
                    "candidate_004_adopted_after_recheck": (
                        judge_result.get("adopted") is True
                    ),
                    "selected_candidate_id_after_recheck": selected_id,
                }
            )
            return {
                "accepted_snapshots": accepted_snapshots,
                "accepted_report": accepted_report,
                "covered_categories": deepcopy(bundle["covered_categories"]),
                "first_candidate_number": int(bundle["next_candidate_number"]),
            }
        except Exception as exc:
            recheck_meta = cast(dict[str, Any], run_record["rule_fix_recheck_from"])
            recheck_meta["status"] = "failed"
            recheck_meta["error"] = str(exc)
            self._save_record(state, run_record)
            raise RuntimeError(f"规则修复重跑失败: {exc}") from exc

    def run(
        self,
        source_text: str,
        *,
        runtime_profile: dict[str, object] | None = None,
        experiment_id: str | None = None,
        run_final_if_confirmed: bool = True,
        resume_from_result: str | Path | None = None,
        resume_probe_from: str | Path | None = None,
        resume_selected_from_result: str | Path | None = None,
        recheck_after_rule_fix_from_result: str | Path | None = None,
        additional_candidates: int | None = None,
        allow_stopped_selected_resume: bool = False,
    ) -> dict[str, Any]:
        resume_source_count = sum(
            value is not None
            for value in (
                resume_from_result,
                resume_selected_from_result,
                recheck_after_rule_fix_from_result,
            )
        )
        if resume_source_count > 1:
            raise ValueError(
                "基准续跑、已选版本续跑和规则修复重跑只能选择一种"
            )
        if recheck_after_rule_fix_from_result is not None and resume_probe_from is not None:
            raise ValueError("规则修复重跑不能同时继续旧数值比较")
        if resume_selected_from_result is not None and resume_probe_from is not None:
            raise ValueError("从 selected_candidate 继续时，不能同时继续旧数值比较")
        if additional_candidates is not None and additional_candidates < 0:
            raise ValueError("additional_candidates 不能小于 0")
        if (
            additional_candidates is not None
            and resume_selected_from_result is None
            and recheck_after_rule_fix_from_result is None
        ):
            raise ValueError(
                "additional_candidates 只能和已选版本续跑或规则修复重跑一起使用"
            )
        if allow_stopped_selected_resume and resume_selected_from_result is None:
            raise ValueError(
                "allow_stopped_selected_resume 只能和 "
                "resume_selected_from_result 一起使用"
            )
        resume_bundle = (
            self._load_baseline_resume_bundle(resume_from_result)
            if resume_from_result is not None
            else None
        )
        selected_resume_bundle = (
            self._load_selected_candidate_resume_bundle(
                resume_selected_from_result,
                allow_stopped=allow_stopped_selected_resume,
            )
            if resume_selected_from_result is not None
            else None
        )
        rule_fix_recheck_bundle = (
            self._load_rule_fix_recheck_bundle(
                recheck_after_rule_fix_from_result
            )
            if recheck_after_rule_fix_from_result is not None
            else None
        )
        probe_resume_bundle = (
            self._load_probe_resume_bundle(resume_probe_from)
            if resume_probe_from is not None
            else None
        )
        if probe_resume_bundle is not None and resume_bundle is None:
            raise ValueError("继续部分数值比较时必须同时指定旧基准结果")
        if isinstance(probe_resume_bundle, Mapping) and isinstance(resume_bundle, Mapping):
            if str(probe_resume_bundle.get("source_text", "")).strip() != str(
                resume_bundle.get("source_text", "")
            ).strip():
                raise ValueError("数值比较与旧基准的原始输入不一致")
            if str(
                probe_resume_bundle.get("baseline_strategy_code_sha256", "")
            ).lower() != str(resume_bundle.get("strategy_code_sha256", "")).lower():
                raise ValueError("数值比较并非建立在本次恢复的基准代码上")
            partial_profile = probe_resume_bundle.get("runtime_profile")
            baseline_profile = resume_bundle.get("runtime_profile")
            if not isinstance(partial_profile, Mapping) or not isinstance(
                baseline_profile, Mapping
            ):
                raise ValueError("数值比较或旧基准缺少运行设置")
            for section in (
                "research_design",
                "data",
                "universe",
                "execution",
                "baseline_defaults",
            ):
                if partial_profile.get(section) != baseline_profile.get(section):
                    raise ValueError(f"数值比较与旧基准的运行设置不一致: {section}")
        saved_profile = (
            rule_fix_recheck_bundle.get("runtime_profile")
            if isinstance(rule_fix_recheck_bundle, Mapping)
            else (
                selected_resume_bundle.get("runtime_profile")
                if isinstance(selected_resume_bundle, Mapping)
                else (
                    resume_bundle.get("runtime_profile")
                    if isinstance(resume_bundle, Mapping)
                    else None
                )
            )
        )
        profile = deepcopy(
            runtime_profile
            or (dict(saved_profile) if isinstance(saved_profile, Mapping) else None)
            or default_cn_daily_v2_profile()
        )
        max_candidates, required_categories = self._profile_budget(profile)
        formal_candidate_limit = (
            int(additional_candidates if additional_candidates is not None else 4)
            if isinstance(selected_resume_bundle, Mapping)
            or isinstance(rule_fix_recheck_bundle, Mapping)
            else max_candidates
        )
        raw_stability = profile.get("stability_requirements", {})
        stability_requirements = (
            dict(raw_stability) if isinstance(raw_stability, Mapping) else {}
        )
        resolved_id = experiment_id or (
            "exp_research_loop_v2_"
            + datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_")
            + uuid4().hex[:6]
        )
        resume_source_bundle = (
            rule_fix_recheck_bundle
            if isinstance(rule_fix_recheck_bundle, Mapping)
            else selected_resume_bundle
        )
        if isinstance(resume_source_bundle, Mapping) and resolved_id == str(
            resume_source_bundle.get("source_experiment_id", "")
        ):
            raise ValueError("继续研究必须使用新的 experiment_id，旧实验目录只读")
        research_spec = build_stage_spec(
            profile, experiment_id=resolved_id, stage="research"
        )
        state = init_state(
            source_text,
            max_epochs=max(64, formal_candidate_limit * 8),
            experiment_spec=research_spec,
        )
        state["history"].append("V2: started from open text input")
        self._next_epoch = 1
        run_record: dict[str, Any] = {
            "schema_version": "research_loop_v2",
            "experiment_id": resolved_id,
            "source_text": source_text,
            "runtime_profile": profile,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "input_interpretation": {},
            "theory_book": {},
            "mechanism_hypotheses": [],
            "planned_candidates": [],
            "candidate_records": [],
            "mechanism_updates": [],
            "selected_candidate_id": "",
            "selected_research_report": {},
            "selected_development_report": {},
            "confirmation_report": {},
            "confirmation_candidates": [],
            "final_test_report": {},
            "observer_requirements": {
                "require_cost_stress": True,
                "require_delay_stress": True,
                **deepcopy(stability_requirements),
            },
        }

        web_client = WebResearchClient(
            record_path=get_trace_run_dir(state) / "web_sources.jsonl"
        )
        planner = self.planner or ResearchPlannerV2(web_client=web_client)
        research_design = profile.get("research_design")
        if not isinstance(research_design, Mapping):
            raise ValueError("运行设置缺少research_design")
        rule_fix_recheck_result: dict[str, object] | None = None
        if isinstance(rule_fix_recheck_bundle, Mapping):
            if (
                str(rule_fix_recheck_bundle.get("source_text", "")).strip()
                != source_text.strip()
            ):
                raise ValueError("规则修复重跑的输入与旧实验原句不一致")
            old_profile = rule_fix_recheck_bundle.get("runtime_profile")
            if not isinstance(old_profile, Mapping):
                raise ValueError("规则修复重跑来源缺少运行设置")
            for section in (
                "research_design",
                "data",
                "universe",
                "execution",
                "baseline_defaults",
            ):
                if profile.get(section) != old_profile.get(section):
                    raise ValueError(
                        f"规则修复重跑时不能改变旧策略的运行设置: {section}"
                    )

            interpretation = deepcopy(
                cast(
                    dict[str, Any],
                    rule_fix_recheck_bundle["input_interpretation"],
                )
            )
            theory_book = deepcopy(
                cast(dict[str, Any], rule_fix_recheck_bundle["theory_book"])
            )
            run_record["input_interpretation"] = interpretation
            run_record["theory_book"] = theory_book
            run_record["mechanism_hypotheses"] = deepcopy(
                theory_book.get("theories", [])
            )
            run_record["rule_fix_recheck_from"] = {
                "experiment_id": rule_fix_recheck_bundle.get(
                    "source_experiment_id"
                ),
                "result_path": rule_fix_recheck_bundle.get("source_result_path"),
                "definition_result_path": rule_fix_recheck_bundle.get(
                    "definition_result_path"
                ),
                "source_finished_at": rule_fix_recheck_bundle.get(
                    "source_finished_at"
                ),
                "source_was_stopped": rule_fix_recheck_bundle.get(
                    "source_was_stopped", False
                ),
                "candidate_001_source_code_path": rule_fix_recheck_bundle.get(
                    "baseline_strategy_code_path"
                ),
                "candidate_001_source_code_sha256": rule_fix_recheck_bundle.get(
                    "baseline_strategy_code_sha256"
                ),
                "candidate_004_source_code_path": rule_fix_recheck_bundle.get(
                    "candidate_004_strategy_code_path"
                ),
                "candidate_004_source_code_sha256": rule_fix_recheck_bundle.get(
                    "candidate_004_strategy_code_sha256"
                ),
                "old_development_reports_reused": False,
                "old_confirmation_reused": False,
                "old_final_test_reused": False,
                "legacy_membership_boundary_bias_note": (
                    "旧实现受历史成分边界偏差影响，估计约1%-1.7%；"
                    "旧失败指标不可当作修正规则后的结果。"
                ),
                "next_candidate_number": 8,
                "additional_candidates": formal_candidate_limit,
                "status": "running",
            }
            self._save_record(state, run_record)
            rule_fix_recheck_result = self._run_rule_fix_rechecks(
                state,
                run_record,
                rule_fix_recheck_bundle,
                experiment_spec=research_spec,
            )
        elif isinstance(selected_resume_bundle, Mapping):
            if (
                str(selected_resume_bundle.get("source_text", "")).strip()
                != source_text.strip()
            ):
                raise ValueError("继续研究的输入与旧实验原句不一致")
            old_profile = selected_resume_bundle.get("runtime_profile")
            if not isinstance(old_profile, Mapping):
                raise ValueError("旧实验缺少运行设置")
            for section in (
                "research_design",
                "data",
                "universe",
                "execution",
                "baseline_defaults",
            ):
                if profile.get(section) != old_profile.get(section):
                    raise ValueError(
                        f"继续研究时不能改变旧策略的运行设置: {section}"
                    )

            interpretation = deepcopy(
                cast(
                    dict[str, Any],
                    selected_resume_bundle["input_interpretation"],
                )
            )
            theory_book = deepcopy(
                cast(dict[str, Any], selected_resume_bundle["theory_book"])
            )
            selected_candidate = deepcopy(
                cast(
                    dict[str, Any],
                    selected_resume_bundle["selected_candidate"],
                )
            )
            selected_id = str(
                selected_resume_bundle["selected_candidate_id"]
            )
            selected_report = self._compact_resume_report(
                selected_resume_bundle["selected_research_report"]
            )
            run_record["candidate_records"] = deepcopy(
                cast(
                    list[dict[str, object]],
                    selected_resume_bundle["candidate_history"],
                )
            )
            run_record["resumed_selected_from"] = {
                "experiment_id": selected_resume_bundle.get(
                    "source_experiment_id"
                ),
                "result_path": selected_resume_bundle.get(
                    "source_result_path"
                ),
                "selected_candidate_id": selected_id,
                "strategy_code_path": selected_resume_bundle.get(
                    "strategy_code_path"
                ),
                "strategy_code_sha256": selected_resume_bundle.get(
                    "strategy_code_sha256"
                ),
                "selected_research_report_reused": True,
                "old_confirmation_reused": False,
                "old_final_test_reused": False,
                "source_finished_at": selected_resume_bundle.get(
                    "source_finished_at"
                ),
                "source_was_stopped": selected_resume_bundle.get(
                    "source_was_stopped", False
                ),
                "additional_candidates": formal_candidate_limit,
            }
            self._restore_saved_candidate_strategy(
                state,
                candidate=selected_candidate,
                candidate_id=selected_id,
                code_text=str(selected_resume_bundle["strategy_code"]),
                saved_generation_meta=cast(
                    Mapping[str, object],
                    selected_resume_bundle.get(
                        "strategy_generation_meta", {}
                    ),
                ),
                expected_hash=str(
                    selected_resume_bundle["strategy_code_sha256"]
                ),
                experiment_spec=research_spec,
            )
            state["development_report"] = deepcopy(selected_report)
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="resumed_selected_candidate",
                payload={
                    "source_experiment_id": selected_resume_bundle.get(
                        "source_experiment_id"
                    ),
                    "selected_candidate_id": selected_id,
                    "strategy_code_sha256": selected_resume_bundle.get(
                        "strategy_code_sha256"
                    ),
                    "selected_research_report_reused": True,
                    "old_confirmation_reused": False,
                    "old_final_test_reused": False,
                },
            )
        elif isinstance(resume_bundle, Mapping):
            if str(resume_bundle.get("source_text", "")).strip() != source_text.strip():
                raise ValueError("续跑输入与旧实验原句不一致")
            old_profile = resume_bundle.get("runtime_profile")
            if not isinstance(old_profile, Mapping):
                raise ValueError("旧实验缺少运行设置")
            for section in (
                "research_design",
                "data",
                "universe",
                "execution",
                "baseline_defaults",
            ):
                if profile.get(section) != old_profile.get(section):
                    raise ValueError(f"续跑时不能改变旧基准的运行设置: {section}")

            interpretation = deepcopy(
                cast(dict[str, Any], resume_bundle["input_interpretation"])
            )
            theory_book = deepcopy(cast(dict[str, Any], resume_bundle["theory_book"]))
            baseline = deepcopy(cast(dict[str, Any], resume_bundle["baseline"]))
            baseline_report = self._compact_resume_report(
                resume_bundle.get("baseline_report")
            )
            run_record["resumed_from"] = {
                "experiment_id": resume_bundle.get("source_experiment_id"),
                "result_path": resume_bundle.get("source_result_path"),
                "strategy_code_path": resume_bundle.get("strategy_code_path"),
                "strategy_code_sha256": resume_bundle.get("strategy_code_sha256"),
                "baseline_development_reused": True,
            }
            self._restore_saved_baseline_strategy(
                state,
                baseline=baseline,
                code_text=str(resume_bundle["strategy_code"]),
                saved_generation_meta=cast(
                    Mapping[str, object],
                    resume_bundle.get("strategy_generation_meta", {}),
                ),
                expected_hash=str(resume_bundle["strategy_code_sha256"]),
                experiment_spec=research_spec,
            )
            state["development_report"] = deepcopy(baseline_report)
            write_trace_json(
                state,
                agent_name="ResearchLoopV2",
                stage="resumed_baseline",
                payload={
                    "source_experiment_id": resume_bundle.get("source_experiment_id"),
                    "strategy_code_sha256": resume_bundle.get("strategy_code_sha256"),
                    "baseline_development_reused": True,
                    "baseline_report": baseline_report,
                },
            )
        else:
            interpretation = self.interpreter.run(
                source_text,
                profile,
                automatic_research=True,
            )
            write_trace_json(
                state,
                agent_name="InputInterpreterV2",
                stage="structured_output",
                payload=interpretation,
            )
            theory_book = planner.build_theories(
                source_text,
                source_cutoff_date=str(research_design["external_source_cutoff"]),
                interpreted_input=interpretation,
                available_fields=[
                    str(item)
                    for item in cast(Mapping[str, object], profile["data"])["available_fields"]  # type: ignore[index]
                ],
            )
            write_trace_json(
                state,
                agent_name="ResearchPlannerV2",
                stage="theory_book",
                payload=theory_book,
            )
            baseline = self.baseline_builder.run(interpretation, profile)
            write_trace_json(
                state,
                agent_name="BaselineBuilderV2",
                stage="structured_output",
                payload=baseline,
            )
            self._prepare_candidate_state(
                state,
                baseline,
                candidate_id="candidate_001",
                epoch_index=self._allocate_epoch(),
                experiment_spec=research_spec,
            )
            # _generate_and_validate 会自行分配轮次，因此这里直接恢复为未分配状态。
            self._next_epoch -= 1
            self._generate_and_validate(
                state,
                baseline,
                candidate_id="candidate_001",
                experiment_spec=research_spec,
            )
            baseline_report = self._run_development_test(
                state,
                experiment_spec=research_spec,
            )

        run_record["input_interpretation"] = interpretation
        run_record["theory_book"] = theory_book
        run_record["mechanism_hypotheses"] = deepcopy(theory_book.get("theories", []))
        if isinstance(rule_fix_recheck_result, Mapping):
            accepted_snapshots = deepcopy(
                cast(
                    list[dict[str, object]],
                    rule_fix_recheck_result["accepted_snapshots"],
                )
            )
            accepted_report = deepcopy(
                cast(
                    dict[str, object],
                    rule_fix_recheck_result["accepted_report"],
                )
            )
            covered_categories = deepcopy(
                cast(
                    list[str],
                    rule_fix_recheck_result["covered_categories"],
                )
            )
            first_candidate_number = int(
                rule_fix_recheck_result["first_candidate_number"]
            )
        elif isinstance(selected_resume_bundle, Mapping):
            self._compact_strategy_state(state)
            starting_id = str(selected_resume_bundle["selected_candidate_id"])
            starting_report = self._compact_resume_report(
                selected_resume_bundle["selected_research_report"]
            )
            starting_snapshot = capture_candidate_snapshot(
                cast(Any, state),
                candidate_id=starting_id,
                save_as_accepted=True,
            )
            accepted_snapshots: list[dict[str, object]] = [
                {
                    "candidate_id": starting_id,
                    "snapshot": starting_snapshot,
                    "research_report": deepcopy(starting_report),
                }
            ]
            accepted_report = deepcopy(starting_report)
            run_record["selected_candidate_id"] = starting_id
            run_record["selected_research_report"] = deepcopy(accepted_report)
            covered_categories = deepcopy(
                cast(
                    list[str],
                    selected_resume_bundle.get("covered_categories", []),
                )
            )
            first_candidate_number = int(
                selected_resume_bundle["next_candidate_number"]
            )
        else:
            self._record_candidate(
                run_record,
                state,
                candidate_id="candidate_001",
                candidate=baseline,
                report=baseline_report,
                status="adopted",
                judge_result={
                    "adopted": True,
                    "adoption_kind": "starting_baseline",
                },
            )
            self._compact_strategy_state(state)
            baseline_snapshot = capture_candidate_snapshot(
                cast(Any, state),
                candidate_id="candidate_001",
                save_as_accepted=True,
            )
            accepted_snapshots = [
                {
                    "candidate_id": "candidate_001",
                    "snapshot": baseline_snapshot,
                    "research_report": deepcopy(baseline_report),
                }
            ]
            accepted_report = deepcopy(baseline_report)
            run_record["selected_candidate_id"] = "candidate_001"
            run_record["selected_research_report"] = deepcopy(accepted_report)
            covered_categories = []
            first_candidate_number = 2
        self._save_record(state, run_record)

        formal_number = 0
        planning_failures = 0
        pending_resume_candidate = deepcopy(probe_resume_bundle)
        if isinstance(probe_resume_bundle, Mapping):
            run_record["resumed_probe"] = {
                "source_run_dir": probe_resume_bundle.get("source_run_dir"),
                "candidate_id": probe_resume_bundle.get("candidate_id"),
                "completed_values": deepcopy(
                    probe_resume_bundle.get("completed_values", [])
                ),
                "remaining_values": deepcopy(
                    probe_resume_bundle.get("remaining_values", [])
                ),
                "probe_strategy_code_path": probe_resume_bundle.get(
                    "probe_strategy_code_path"
                ),
                "probe_strategy_code_sha256": probe_resume_bundle.get(
                    "probe_strategy_code_sha256"
                ),
            }
            self._save_record(state, run_record)
        while formal_number < formal_candidate_limit:
            resumed_probe_results: list[dict[str, object]] = []
            resumed_probe_code: dict[str, object] = {}
            if isinstance(pending_resume_candidate, Mapping):
                candidate = deepcopy(
                    cast(dict[str, Any], pending_resume_candidate["candidate"])
                )
                resumed_probe_results = deepcopy(
                    cast(
                        list[dict[str, object]],
                        pending_resume_candidate.get("probe_results", []),
                    )
                )
                pending_candidate_id = str(
                    pending_resume_candidate.get("candidate_id", "")
                )
                resumed_probe_code = {
                    "code": pending_resume_candidate.get("probe_strategy_code"),
                    "meta": pending_resume_candidate.get(
                        "probe_strategy_generation_meta", {}
                    ),
                    "sha256": pending_resume_candidate.get(
                        "probe_strategy_code_sha256"
                    ),
                }
                pending_resume_candidate = None
            else:
                pending_candidate_id = ""
                try:
                    candidate = planner.propose_candidate(
                        source_text=source_text,
                        theory_book=theory_book,
                        accepted_strategy=self._accepted_strategy_descriptor(state),
                        development_report=accepted_report,
                        candidate_history=deepcopy(run_record["candidate_records"]),
                        covered_categories=covered_categories,
                        available_data=self._available_data(profile),
                    )
                except Exception as exc:
                    planning_failures += 1
                    run_record.setdefault("planning_failures", []).append(
                        {
                            "attempt": planning_failures,
                            "error": str(exc),
                            "covered_categories": deepcopy(covered_categories),
                        }
                    )
                    self._save_record(state, run_record)
                    if planning_failures >= 3:
                        break
                    continue
            formal_number += 1
            candidate_id = (
                f"candidate_{first_candidate_number + formal_number - 1:03d}"
            )
            if pending_candidate_id and pending_candidate_id != candidate_id:
                raise ValueError("部分数值比较的候选编号与当前顺序不一致")
            planned = deepcopy(candidate)
            planned["candidate_id"] = candidate_id
            planned["status"] = "planned"
            run_record["planned_candidates"].append(planned)
            planned_row = run_record["planned_candidates"][-1]
            write_trace_json(
                state,
                agent_name="ResearchPlannerV2",
                stage="planned_candidate",
                payload=planned,
            )
            self._save_record(state, run_record)

            try:
                if isinstance(candidate.get("parameter_probe"), Mapping):
                    def save_probe_progress(items: list[dict[str, object]]) -> None:
                        planned_row["probe_results"] = deepcopy(items)
                        run_record["active_probe"] = {
                            "candidate_id": candidate_id,
                            "completed_count": len(items),
                            "probe_results": deepcopy(items),
                        }
                        self._save_record(state, run_record)

                    probe_results = self._run_parameter_probe(
                        state,
                        candidate,
                        experiment_spec=research_spec,
                        existing_probe_results=resumed_probe_results,
                        on_probe_result=save_probe_progress,
                        saved_probe_code=cast(
                            str | None, resumed_probe_code.get("code")
                        ),
                        saved_probe_generation_meta=cast(
                            Mapping[str, object] | None,
                            resumed_probe_code.get("meta"),
                        ) if resumed_probe_code else None,
                        saved_probe_code_sha256=cast(
                            str | None, resumed_probe_code.get("sha256")
                        ),
                    )
                    run_record.pop("active_probe", None)
                    probe_decision = planner.finalize_parameter_probe(
                        planned_candidate=candidate,
                        accepted_strategy=self._accepted_strategy_descriptor(state),
                        probe_results=probe_results,
                    )
                    if probe_decision.get("decision") == "reject_probe":
                        self._record_candidate(
                            run_record,
                            state,
                            candidate_id=candidate_id,
                            candidate=candidate,
                            report=None,
                            status="rejected",
                            extra={
                                "probe_results": probe_results,
                                "probe_decision": probe_decision,
                            },
                        )
                        if str(candidate["category"]) not in covered_categories:
                            covered_categories.append(str(candidate["category"]))
                        self._save_record(state, run_record)
                        continue
                    candidate = _formal_candidate_from_probe_decision(
                        probe_decision,
                        probe_results,
                    )

                restore_accepted_snapshot(cast(Any, state))
                report, param_changes = self._run_formal_candidate(
                    state,
                    candidate,
                    candidate_id=candidate_id,
                    experiment_spec=research_spec,
                )
                mechanism_comparison_results: dict[str, object] = {}
                if str(candidate.get("category", "")) == "mechanism_signal":
                    main_mechanism_signal_report = deepcopy(
                        state.get("mechanism_signal_check", {})
                    )
                    main_snapshot = capture_candidate_snapshot(
                        cast(Any, state),
                        candidate_id=candidate_id,
                        save_as_accepted=False,
                    )
                    main_code = str(main_snapshot.get("strategy_code", ""))
                    main_reference = mechanism_signal_reference(main_code)
                    main_reference_source = str(main_reference["source"])
                    comparison = candidate.get("mechanism_comparison")
                    if not isinstance(comparison, Mapping):
                        raise RuntimeError("机制信号缺少新增与单独使用的对照")
                    mechanism_comparison_results["added_to_base"] = {
                        "candidate_id": candidate_id,
                        "report": deepcopy(report),
                        "mechanism_signal_check": main_mechanism_signal_report,
                    }
                    for variant_name in ("signal_only", "replace_related_rule"):
                        variant = comparison.get(variant_name)
                        if not isinstance(variant, Mapping):
                            continue
                        restore_candidate_snapshot(cast(Any, state), main_snapshot)
                        variant_id = f"{candidate_id}_{variant_name}"
                        variant_report, _ = self._run_formal_candidate(
                            state,
                            variant,
                            candidate_id=variant_id,
                            experiment_spec=research_spec,
                            mechanism_signal_reference_source=main_reference_source,
                        )
                        mechanism_comparison_results[variant_name] = {
                            "candidate_id": variant_id,
                            "report": deepcopy(variant_report),
                            "mechanism_signal_check": deepcopy(
                                state.get("mechanism_signal_check", {})
                            ),
                        }
                    restore_candidate_snapshot(cast(Any, state), main_snapshot)
                    state["mechanism_signal_check"] = main_mechanism_signal_report  # type: ignore[typeddict-unknown-key]
                judge_result = self.judge.run(
                    accepted_report,
                    report,
                    {
                        "predicted_changes": deepcopy(
                            candidate.get("predicted_changes", [])
                        ),
                        "mechanism_id": candidate.get("mechanism_id"),
                        "new_signal_definition": deepcopy(
                            candidate.get("new_signal_definition", {})
                        ),
                        "mechanism_comparison_results": deepcopy(
                            mechanism_comparison_results
                        ),
                    },
                    {
                        "require_cost_stress": False,
                        "require_delay_stress": False,
                        "min_fold_count": int(research_spec["development_folds"]),
                        "min_trade_count": 100,
                    },
                )
                status = "adopted" if judge_result.get("adopted") is True else "rejected"
                self._record_candidate(
                    run_record,
                    state,
                    candidate_id=candidate_id,
                    candidate=candidate,
                    report=report,
                    status=status,
                    judge_result=judge_result,
                    parameter_changes=param_changes,
                    extra={
                        "probe_results": deepcopy(candidate.get("probe_results", [])),
                        "probe_decision": deepcopy(candidate.get("probe_decision", {})),
                        "mechanism_comparison_results": deepcopy(
                            mechanism_comparison_results
                        ),
                    },
                )
                run_record["mechanism_updates"].extend(
                    deepcopy(judge_result.get("mechanism_updates", []))
                )
                if judge_result.get("adopted") is True:
                    self._compact_strategy_state(state)
                    accepted_snapshot = capture_candidate_snapshot(
                        cast(Any, state),
                        candidate_id=candidate_id,
                        save_as_accepted=True,
                    )
                    accepted_snapshots.append(
                        {
                            "candidate_id": candidate_id,
                            "snapshot": accepted_snapshot,
                            "research_report": deepcopy(report),
                        }
                    )
                    accepted_report = deepcopy(report)
                    run_record["selected_candidate_id"] = candidate_id
                    run_record["selected_research_report"] = deepcopy(report)
                else:
                    restore_accepted_snapshot(cast(Any, state))
                if str(candidate["category"]) not in covered_categories:
                    covered_categories.append(str(candidate["category"]))
            except Exception as exc:
                restore_accepted_snapshot(cast(Any, state))
                self._record_candidate(
                    run_record,
                    state,
                    candidate_id=candidate_id,
                    candidate=candidate,
                    report=None,
                    status="technical_failure",
                    extra={"error": str(exc)},
                )
            self._save_record(state, run_record)

        run_record["covered_categories"] = covered_categories
        run_record["required_categories"] = required_categories
        run_record["category_coverage_complete"] = all(
            category in covered_categories for category in required_categories
        )

        confirmation_spec = build_stage_spec(
            profile, experiment_id=resolved_id, stage="confirmation"
        )
        # 确认期只能检查一个在研究期已经选定的版本，不能看完确认期再挑。
        research_winner = max(
            accepted_snapshots,
            key=lambda item: _research_rank(
                cast(Mapping[str, object], item["research_report"])
            ),
        )
        selected_id = str(research_winner["candidate_id"])
        restore_candidate_snapshot(
            cast(Any, state), cast(dict[str, Any], research_winner["snapshot"])
        )
        run_record["selected_candidate_id"] = selected_id
        run_record["selected_research_report"] = deepcopy(
            research_winner["research_report"]
        )
        state["epoch_index"] = self._allocate_epoch()
        try:
            confirmation_report = self._run_development_test(
                state, experiment_spec=confirmation_spec
            )
            confirmation_stable = _meets_confirmation_requirements(
                confirmation_report,
                stability_requirements,
            )
            run_record["confirmation_candidates"].append(
                {
                    "candidate_id": selected_id,
                    "stable": confirmation_stable,
                    "report": deepcopy(confirmation_report),
                }
            )
        except Exception as exc:
            confirmation_report = {}
            confirmation_stable = False
            run_record["confirmation_candidates"].append(
                {
                    "candidate_id": selected_id,
                    "stable": False,
                    "report": {},
                    "error": str(exc),
                }
            )
        run_record["confirmation_report"] = deepcopy(confirmation_report)
        run_record["selected_development_report"] = deepcopy(confirmation_report)
        run_record["confirmation_stable"] = confirmation_stable

        if confirmation_stable and run_final_if_confirmed:
            final_spec = build_stage_spec(
                profile, experiment_id=resolved_id, stage="final"
            )
            state["experiment_spec"] = final_spec
            state["evaluation_stage"] = "final_test"
            state["epoch_index"] = self._allocate_epoch()
            state["phase"] = "final_test"
            state["test_result"] = None
            state["backtest_debug"] = {}
            try:
                self.tester.run(state)
                test_result = state.get("test_result")
                if test_result is None:
                    message = str(
                        state.get("backtest_feedback")
                        or state.get("last_test_error")
                        or "最终期没有生成回测结果"
                    )
                    raise RuntimeError(message)
                final_report = _result_to_final_report(
                    test_result,
                    backtest_debug=state.get("backtest_debug"),
                )
                if not final_report:
                    raise RuntimeError("最终期结果格式为空")
                final_report["meets_v2_stability_requirements"] = (
                    _meets_final_requirements(final_report, stability_requirements)
                )
                state["final_test_result"] = test_result
                run_record["final_test_report"] = final_report
            except Exception as exc:
                run_record["final_test_report"] = {
                    "passed": False,
                    "calculation_valid": False,
                    "error": str(exc),
                }
        else:
            run_record["final_test_skipped_reason"] = (
                "确认期没有达到稳定要求"
                if not confirmation_stable
                else "调用方关闭了最终期检查"
            )

        run_record["observer_audit"] = audit_v2_loop(run_record)
        run_record["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
        result_path = self._save_record(state, run_record)
        run_record["result_path"] = str(result_path)
        self._save_record(state, run_record)
        return run_record


def run_research_loop_v2(
    source_text: str,
    *,
    runtime_profile: dict[str, object] | None = None,
    experiment_id: str | None = None,
    run_final_if_confirmed: bool = True,
    resume_from_result: str | Path | None = None,
    resume_probe_from: str | Path | None = None,
    resume_selected_from_result: str | Path | None = None,
    recheck_after_rule_fix_from_result: str | Path | None = None,
    additional_candidates: int | None = None,
    allow_stopped_selected_resume: bool = False,
) -> dict[str, Any]:
    return ResearchLoopV2().run(
        source_text,
        runtime_profile=runtime_profile,
        experiment_id=experiment_id,
        run_final_if_confirmed=run_final_if_confirmed,
        resume_from_result=resume_from_result,
        resume_probe_from=resume_probe_from,
        resume_selected_from_result=resume_selected_from_result,
        recheck_after_rule_fix_from_result=recheck_after_rule_fix_from_result,
        additional_candidates=additional_candidates,
        allow_stopped_selected_resume=allow_stopped_selected_resume,
    )


__all__ = ["ResearchLoopV2", "run_research_loop_v2"]
