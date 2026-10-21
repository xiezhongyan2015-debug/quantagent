from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, TypedDict, cast


Phase = Literal[
    "hypothesis",   # 产生或修改研究假设
    "diagnostics",  # 用固定程序补算研究证据
    "strategy",     # 生成策略代码
    "validate",     # 用固定程序检查策略
    "backtest",     # 可反复运行的开发回测，结果可以用于下一轮研究
    "final_test",   # 最终时期只运行一次，结果不得反馈
    "test",         # 旧名称，等同于开发回测
    "done",
]

EvaluationStage = Literal["development", "final_test"]
ResearchStage = Literal["translation", "initial_research", "iteration"]
EventSuccessMetric = Literal[
    "legacy_event_success_rate",
    "gross_up_rate",
    "joint_minute_hit",
]
DEFAULT_EVENT_SUCCESS_METRIC: EventSuccessMetric = "legacy_event_success_rate"
EVENT_SUCCESS_METRICS = frozenset(
    {DEFAULT_EVENT_SUCCESS_METRIC, "gross_up_rate", "joint_minute_hit"}
)


@dataclass
class BacktestResult:
    annual_return: float
    sharpe: float
    max_drawdown: float
    passed: bool
    summary: str
    max_ddpercent: float | None = None
    trade_count: int | None = None
    win_rate: float | None = None
    profit_loss_ratio: float | None = None
    event_net_mean_return: float | None = None
    event_gross_up_rate: float | None = None
    event_joint_minute_hit_rate: float | None = None
    event_success_rate: float | None = None
    event_success_column: str | None = None
    event_success_metric: EventSuccessMetric | None = None
    event_success_metric_value: float | None = None


class WorkflowState(TypedDict):
    experiment_spec: dict[str, object]
    phase: Phase
    epoch_index: int
    max_epochs: int
    strategy_validate_round: int
    # ── hypothesis agent output ──────────────────────────────────────────────
    hypothesis_generation_meta: dict[str, object]
    # ── strategy agent output ───────────────────────────────────────────────
    strategy_code: str
    strategy_generation_meta: dict[str, object]
    strategy_result: dict[str, object]
    # ── validate agent output ───────────────────────────────────────────────────
    validation_result: dict[str, object]
    validation_summary: dict[str, object]
    validation_feedback: dict[str, object]
    development_report: dict[str, object]
    diagnostic_request: dict[str, object]
    diagnostic_report: dict[str, object]
    diagnostic_records: list[dict[str, object]]
    diagnostic_round: int
    research_stage: ResearchStage
    translation_meta: dict[str, object]
    # ── coder / tester shared ────────────────────────────────────────────────
    selected_template: str
    selected_base_class: str
    backtest_feedback: str
    code_text: str
    test_result: BacktestResult | None
    final_test_result: BacktestResult | None
    evaluation_stage: EvaluationStage
    development_period: dict[str, str]
    final_test_period: dict[str, str]
    current_candidate_id: str
    candidate_records: list[dict[str, object]]
    experiment_records: list[dict[str, object]]
    seen_periods: list[dict[str, object]]
    technical_retry_count: int
    research_trial_count: int
    final_test_count: int
    quality_passed: bool
    final_quality_passed: bool
    ready_for_final: bool
    candidate_frozen: bool
    awaiting_final_test_approval: bool
    last_test_error: str
    manager_notes: str
    history: list[str]


@dataclass
class QualityGate:
    min_sharpe: float | None = None
    min_annual_return: float | None = None
    max_drawdown: float | None = None
    min_trade_count: int | None = None
    min_event_net_mean: float | None = None
    min_event_success_rate: float | None = None
    event_success_metric: EventSuccessMetric = DEFAULT_EVENT_SUCCESS_METRIC


def select_event_success_value(
    metric: str,
    *,
    legacy_rate: float | None,
    legacy_column: str | None,
    gross_up_rate: float | None,
    joint_minute_hit_rate: float | None,
) -> tuple[float | None, str]:
    if metric == "gross_up_rate":
        return gross_up_rate, "gross_up_rate"
    if metric == "joint_minute_hit":
        value = joint_minute_hit_rate
        if value is None and legacy_column == "joint_minute_hit":
            value = legacy_rate
        return value, "joint_minute_hit"
    return legacy_rate, str(legacy_column or "event_success_rate")


def resolve_quality_gate(experiment_spec: dict[str, object] | None) -> QualityGate | None:
    if not isinstance(experiment_spec, dict):
        return None

    evaluation = experiment_spec.get("evaluation")
    if not isinstance(evaluation, dict):
        return None

    def _coerce_float(value: object) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str) and value.strip():
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    raw_success_metric = evaluation.get("event_success_metric")
    if raw_success_metric is None:
        event_success_metric: EventSuccessMetric = DEFAULT_EVENT_SUCCESS_METRIC
    elif (
        isinstance(raw_success_metric, str)
        and raw_success_metric.strip() in EVENT_SUCCESS_METRICS
    ):
        event_success_metric = cast(EventSuccessMetric, raw_success_metric.strip())
    else:
        raise ValueError(
            "evaluation.event_success_metric 只允许 "
            "gross_up_rate、joint_minute_hit 或 legacy_event_success_rate"
        )

    min_event_success_rate = _coerce_float(
        evaluation.get("min_event_success_rate")
    )
    if min_event_success_rate is not None and not 0.0 <= min_event_success_rate <= 1.0:
        raise ValueError("evaluation.min_event_success_rate 必须在 0 到 1 之间")

    gate = QualityGate(
        min_sharpe=_coerce_float(evaluation.get("min_sharpe_ratio")),
        min_annual_return=_coerce_float(evaluation.get("min_return_rate")),
        max_drawdown=_coerce_float(evaluation.get("max_drawdown")),
        min_trade_count=(
            int(value)
            if (value := _coerce_float(evaluation.get("min_trade_count"))) is not None
            and value >= 0
            else None
        ),
        min_event_net_mean=_coerce_float(evaluation.get("min_event_net_mean")),
        min_event_success_rate=min_event_success_rate,
        event_success_metric=event_success_metric,
    )

    if (
        gate.min_sharpe is None
        and gate.min_annual_return is None
        and gate.max_drawdown is None
        and gate.min_trade_count is None
        and gate.min_event_net_mean is None
        and gate.min_event_success_rate is None
    ):
        return None

    return gate


def get_hypothesis(state: "WorkflowState") -> str:
    """Extract the hypothesis text from hypothesis_generation_meta."""
    meta = state.get("hypothesis_generation_meta")
    if isinstance(meta, dict):
        return str(meta.get("hypothesis", "")).strip()
    return ""


def _read_period(
    spec: dict[str, object],
    start_keys: tuple[str, ...],
    end_keys: tuple[str, ...],
) -> dict[str, str]:
    def _first(keys: tuple[str, ...]) -> str:
        for key in keys:
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    start = _first(start_keys)
    end = _first(end_keys)
    if not start or not end:
        return {}
    return {"start": start, "end": end}


def resolve_development_period(spec: dict[str, object] | None) -> dict[str, str]:
    """返回可反复使用的开发回测时期。

    循环阶段重复使用验证期，最后回测期只留给一次最终检查。
    """
    if not isinstance(spec, dict):
        return {}
    return _read_period(
        spec,
        ("development_start", "validate_start"),
        ("development_end", "validate_end"),
    )


def resolve_final_test_period(spec: dict[str, object] | None) -> dict[str, str]:
    """返回最终时期，绝不拿开发时期代替。"""
    if not isinstance(spec, dict):
        return {}
    return _read_period(
        spec,
        ("final_test_start", "holdout_start", "backtest_start"),
        ("final_test_end", "holdout_end", "backtest_end"),
    )


def periods_overlap(first: dict[str, str], second: dict[str, str]) -> bool:
    """两个日期区间有重叠或无法核实时返回 True。"""
    try:
        first_start = date.fromisoformat(first["start"][:10])
        first_end = date.fromisoformat(first["end"][:10])
        second_start = date.fromisoformat(second["start"][:10])
        second_end = date.fromisoformat(second["end"][:10])
    except (KeyError, TypeError, ValueError):
        return True

    if first_start > first_end or second_start > second_end:
        return True
    return max(first_start, second_start) <= min(first_end, second_end)


def period_starts_after(first: dict[str, str], second: dict[str, str]) -> bool:
    """第二个日期区间严格晚于第一个区间时返回 True。"""
    try:
        first_end = date.fromisoformat(first["end"][:10])
        second_start = date.fromisoformat(second["start"][:10])
    except (KeyError, TypeError, ValueError):
        return False
    return second_start > first_end


def derive_experiment_periods(spec: dict[str, object]) -> dict[str, tuple[str, str]]:
    """读取训练、验证、开发回测和最终时期。"""
    def _s(v: object) -> str:
        return v.strip() if isinstance(v, str) else ""

    periods: dict[str, tuple[str, str]] = {}
    ts, te = _s(spec.get("train_start")), _s(spec.get("train_end"))
    if ts and te:
        periods["train_period"] = (ts, te)
    vs, ve = _s(spec.get("validate_start")), _s(spec.get("validate_end"))
    if vs and ve:
        periods["valid_period"] = (vs, ve)
    development = resolve_development_period(spec)
    if development:
        development_tuple = (development["start"], development["end"])
        periods["development_period"] = development_tuple
    final_test = resolve_final_test_period(spec)
    if final_test:
        final_tuple = (final_test["start"], final_test["end"])
        periods["final_test_period"] = final_tuple
        periods["test_period"] = final_tuple  # 兼容旧名称
    return periods


def init_state(
    user_idea: str,
    max_epochs: int = 5,
    experiment_spec: dict[str, object] | None = None,
) -> WorkflowState:
    spec = dict(experiment_spec) if experiment_spec else {}
    if "user_idea" not in spec or not str(spec.get("user_idea", "")).strip():
        spec["user_idea"] = user_idea

    development_period = resolve_development_period(spec)
    final_test_period = resolve_final_test_period(spec)

    return WorkflowState(
        experiment_spec=spec,
        phase="hypothesis",
        max_epochs=max_epochs,
        strategy_validate_round=1,
        hypothesis_generation_meta={},
        strategy_code="",
        strategy_generation_meta={},
        strategy_result={},
        validation_result={},
        validation_summary={},
        validation_feedback={},
        development_report={},
        diagnostic_request={},
        diagnostic_report={},
        diagnostic_records=[],
        diagnostic_round=0,
        research_stage="translation",
        translation_meta={},

        selected_template="",
        selected_base_class="",
        backtest_feedback="",
        code_text="",
        epoch_index=1,
        test_result=None,
        final_test_result=None,
        evaluation_stage="development",
        development_period=development_period,
        final_test_period=final_test_period,
        current_candidate_id="candidate_001",
        candidate_records=[],
        experiment_records=[],
        seen_periods=[],
        technical_retry_count=0,
        research_trial_count=0,
        final_test_count=0,
        quality_passed=False,
        final_quality_passed=False,
        ready_for_final=False,
        candidate_frozen=False,
        awaiting_final_test_approval=False,
        last_test_error="",
        manager_notes="",
        history=[],

    )
