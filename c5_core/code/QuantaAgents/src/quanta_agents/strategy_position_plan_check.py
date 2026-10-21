from __future__ import annotations

import ast
from copy import deepcopy
import json
from typing import Any

import numpy as np
import pandas as pd

from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)


_POSITION_PLAN_FUNCTION = "build_daily_position_plan_weights"
_POST_PLAN_RESTRICTION_MARKERS = (
    "membership",
    "member",
    "universe",
    "eligible",
    "eligibility",
    "availability",
    "available",
    "constraint",
    "filter",
    "mask",
    "where",
)


class PositionPlanValidationError(ValueError):
    """生成代码没有保持每批持仓计划原定的目标比例。"""


def _output_weights_calls_position_plan_function(code_text: str) -> bool:
    tree = ast.parse(code_text, filename="generated_strategy.py")
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "output_weights":
            continue
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == _POSITION_PLAN_FUNCTION
            for child in ast.walk(node)
        )
    return False


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _referenced_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _identifier_texts(node: ast.AST) -> set[str]:
    identifiers: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            identifiers.add(child.id.lower())
        elif isinstance(child, ast.Attribute):
            identifiers.add(child.attr.lower())
    return identifiers


def _contains_position_plan_call(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == _POSITION_PLAN_FUNCTION
        for child in ast.walk(node)
    )


def _statement_targets(statement: ast.stmt) -> set[str]:
    if isinstance(statement, ast.Assign):
        targets: set[str] = set()
        for target in statement.targets:
            targets.update(_assigned_names(target))
        return targets
    if isinstance(statement, ast.AnnAssign):
        return _assigned_names(statement.target)
    return set()


def _assert_no_post_plan_restriction(code_text: str) -> None:
    """固定计划生成后，不得再按每日范围或可交易状态改写它。"""

    tree = ast.parse(code_text, filename="generated_strategy.py")
    output_function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "output_weights"
        ),
        None,
    )
    if output_function is None:
        return

    planned_names: set[str] = set()
    plan_created = False
    for statement in output_function.body:
        if _contains_position_plan_call(statement):
            planned_names.update(_statement_targets(statement))
            plan_created = True
            continue
        if not plan_created or not planned_names:
            continue

        referenced = _referenced_names(statement)
        uses_planned_result = bool(referenced.intersection(planned_names))
        identifiers = _identifier_texts(statement)
        applies_daily_restriction = any(
            marker in identifier
            for identifier in identifiers
            for marker in _POST_PLAN_RESTRICTION_MARKERS
        )
        if uses_planned_result and applies_daily_restriction:
            raise PositionPlanValidationError(
                "build_daily_position_plan_weights 生成固定持有计划后，"
                "不得再按每日历史成分、股票范围或当日可交易状态把原目标比例清零或改小。"
                "历史成分只用于决定是否允许新建或增加目标；已经开始的计划必须保持到原退出日。"
            )

        if uses_planned_result:
            planned_names.update(_statement_targets(statement))


def _load_position_plan_function(code_text: str) -> Any:
    validate_generated_strategy_code(code_text, event_mode=False)
    namespace: dict[str, object] = {
        "__name__": "__generated_strategy_position_plan_check__",
        "json": json,
        "np": np,
        "pd": pd,
    }
    exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
    function = namespace.get(_POSITION_PLAN_FUNCTION)
    if not callable(function):
        raise PositionPlanValidationError(
            "日线策略代码必须定义 build_daily_position_plan_weights 函数，"
            "由它保存每批新计划开始时确定的目标比例。"
        )
    if not _output_weights_calls_position_plan_function(code_text):
        raise PositionPlanValidationError(
            "output_weights 必须实际调用 build_daily_position_plan_weights；"
            "不能只定义函数而继续按当天全部 active_codes 重新等权。"
        )
    return function


def _synthetic_position_plans() -> tuple[
    list[pd.Timestamp],
    dict[pd.Timestamp, list[dict[str, object]]],
]:
    dates = list(pd.date_range("2024-01-02", periods=8, freq="B"))
    plans: dict[pd.Timestamp, list[dict[str, object]]] = {
        dates[0]: [
            {"code": "FIRST_A", "last_target_date": dates[5]},
            {"code": "FIRST_B", "last_target_date": dates[5]},
        ],
        dates[2]: [
            {"code": f"LATER_{number:02d}", "last_target_date": dates[3]}
            for number in range(1, 11)
        ],
    }
    return dates, plans


def _synthetic_zero_weight_retry_plans() -> tuple[
    list[pd.Timestamp],
    dict[pd.Timestamp, list[dict[str, object]]],
]:
    dates = list(pd.date_range("2024-02-01", periods=4, freq="B"))
    plans: dict[pd.Timestamp, list[dict[str, object]]] = {
        dates[0]: [
            {"code": "FULL_WEIGHT", "last_target_date": dates[1]},
        ],
        dates[1]: [
            {"code": "ZERO_RETRY", "last_target_date": dates[3]},
        ],
        dates[2]: [
            {"code": "ZERO_RETRY", "last_target_date": dates[3]},
            *(
                {"code": f"RETRY_PEER_{number:02d}", "last_target_date": dates[3]}
                for number in range(1, 10)
            ),
        ],
    }
    return dates, plans


def _normalized_result(
    result: object,
    dates: list[pd.Timestamp],
) -> pd.DataFrame:
    if not isinstance(result, pd.DataFrame):
        raise PositionPlanValidationError(
            "build_daily_position_plan_weights 必须返回 pandas DataFrame。"
        )
    if "trade_date" not in result.columns:
        raise PositionPlanValidationError(
            "build_daily_position_plan_weights 的结果必须有 trade_date 列。"
        )
    parsed_dates = pd.to_datetime(result["trade_date"], errors="coerce")
    if bool(parsed_dates.isna().any()):
        raise PositionPlanValidationError("持仓计划结果含有无法识别的 trade_date。")
    if bool(parsed_dates.duplicated().any()):
        raise PositionPlanValidationError("持仓计划结果的 trade_date 不能重复。")

    normalized = result.drop(columns=["trade_date"]).copy()
    normalized.index = pd.DatetimeIndex(parsed_dates)
    expected_index = pd.DatetimeIndex(dates)
    if not normalized.index.equals(expected_index):
        raise PositionPlanValidationError(
            "持仓计划结果必须按传入的全部交易日原顺序各返回一行。"
        )
    return normalized


def _numeric_column(frame: pd.DataFrame, column: str) -> np.ndarray:
    if column not in frame.columns:
        raise PositionPlanValidationError(f"持仓计划结果缺少 {column} 列。")
    converted = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    if not bool(np.isfinite(converted).all()):
        raise PositionPlanValidationError(f"持仓计划结果的 {column} 不是有限数值。")
    return converted


def _assert_close(
    frame: pd.DataFrame,
    column: str,
    expected: list[float],
) -> None:
    actual = _numeric_column(frame, column)
    expected_values = np.asarray(expected, dtype=float)
    equal = np.isclose(actual, expected_values, rtol=0.0, atol=1e-9)
    if bool(equal.all()):
        return
    position = int(np.flatnonzero(~equal)[0])
    raise PositionPlanValidationError(
        f"持仓计划比例错误：日期={frame.index[position].date()}，"
        f"股票={column}，应为={expected_values[position]:.10g}，"
        f"实际={actual[position]:.10g}。"
        "旧计划在原结束日前不得因其他计划开始或结束而加减仓；"
        "当日新计划的分配办法也不得改写已有计划。"
    )


def _run_position_plan_check(
    function: Any,
    dates: list[pd.Timestamp],
    plans: dict[pd.Timestamp, list[dict[str, object]]],
    single_stock_weight: float,
) -> pd.DataFrame:
    plans_copy = deepcopy(plans)
    before = repr(plans_copy)
    try:
        result = function(dates.copy(), plans_copy, single_stock_weight)
    except Exception as exc:
        raise PositionPlanValidationError(
            f"小型持仓计划检查无法运行: {exc}"
        ) from exc
    if repr(plans_copy) != before:
        raise PositionPlanValidationError(
            "build_daily_position_plan_weights 不得修改传入的新计划。"
        )
    return _normalized_result(result, dates)


def check_generated_daily_position_plans(code_text: str) -> dict[str, object]:
    """用先后开始、先后结束的两批小计划检查日线目标比例。"""

    function = _load_position_plan_function(code_text)
    _assert_no_post_plan_restriction(code_text)
    dates, plans = _synthetic_position_plans()
    frame = _run_position_plan_check(function, dates, plans, 0.10)
    first_expected = [0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.0, 0.0]
    _assert_close(frame, "FIRST_A", first_expected)
    _assert_close(frame, "FIRST_B", first_expected)

    later_expected = [0.0, 0.0, 0.08, 0.08, 0.0, 0.0, 0.0, 0.0]
    for number in range(1, 11):
        _assert_close(frame, f"LATER_{number:02d}", later_expected)

    cash_expected = [0.80, 0.80, 0.0, 0.0, 0.80, 0.80, 1.0, 1.0]
    _assert_close(frame, "cash", cash_expected)

    required_columns = [
        "FIRST_A",
        "FIRST_B",
        *(f"LATER_{number:02d}" for number in range(1, 11)),
        "cash",
    ]
    unexpected_columns = sorted(set(frame.columns) - set(required_columns))
    if unexpected_columns:
        raise PositionPlanValidationError(
            "小型持仓计划结果含有未传入的股票列: "
            + ", ".join(unexpected_columns)
        )
    row_sums = np.zeros(len(frame), dtype=float)
    for column in required_columns:
        row_sums += _numeric_column(frame, column)
    if not bool(np.isclose(row_sums, 1.0, rtol=0.0, atol=1e-9).all()):
        raise PositionPlanValidationError(
            "小型持仓计划的每行股票比例与现金之和必须等于1。"
        )

    retry_dates, retry_plans = _synthetic_zero_weight_retry_plans()
    retry_frame = _run_position_plan_check(function, retry_dates, retry_plans, 1.0)
    _assert_close(retry_frame, "FULL_WEIGHT", [1.0, 1.0, 0.0, 0.0])
    # 首次出现时没有现金的股票不能成为有效计划，否则会挡住次日的新信号。
    _assert_close(retry_frame, "ZERO_RETRY", [0.0, 0.0, 0.10, 0.10])
    for number in range(1, 10):
        _assert_close(retry_frame, f"RETRY_PEER_{number:02d}", [0.0, 0.0, 0.10, 0.10])
    _assert_close(retry_frame, "cash", [0.0, 0.0, 0.0, 0.0])

    return {
        "passed": True,
        "checked_dates": [date.isoformat() for date in dates],
        "first_batch_size": 2,
        "later_batch_size": 10,
        "first_batch_weight": 0.10,
        "later_batch_weight": 0.08,
        "zero_weight_retry_weight": 0.10,
        "post_plan_daily_restriction": "not found",
    }
