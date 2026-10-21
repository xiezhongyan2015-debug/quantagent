from __future__ import annotations

import ast
import json
import re
from typing import Mapping

import numpy as np
import pandas as pd

from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)


SAMPLE_FUNCTION_NAME = "calculation_contract_sample"
CONSECUTIVE_SAMPLE_FUNCTION_NAME = "consecutive_market_day_sample"
_DATE_TOKEN = re.compile(r"^t(?:-(\d+))?$")
_CHINESE_DAY_NUMBERS = {
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_CONSECUTIVE_TEXT = re.compile(
    r"连续\s*(?P<count>\d+|[两二三四五六七八九十])\s*(?:个)?(?:天|[^，。；,;\n]{0,12}?日)"
)
_PER_SYMBOL_SEQUENCE_MARKERS = (
    "per_symbol_observation_sequence",
    "每只股票自己的记录",
    "每只股票自己的有效日",
    "个股自身观察序列",
)

_REPEATED_BLOCK_TYPES = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
_SMALL_FRAME_METHODS = {
    "copy",
    "dropna",
    "rename",
    "reset_index",
    "sort_index",
    "sort_values",
}
_DATE_LOOKUP_METHODS = {"get", "get_group"}


class CalculationContractError(ValueError):
    """生成代码没有遵守研究计划中的日期和样本口径。"""


def required_date_offsets(required_dates: object) -> list[int]:
    """把 t、t-1、t-30 这类日期写法转成统一市场日偏移。"""

    if not isinstance(required_dates, list) or not required_dates:
        raise CalculationContractError("calculation_contract.required_dates 必须是非空数组")
    offsets: list[int] = []
    for raw in required_dates:
        token = str(raw).strip().lower()
        match = _DATE_TOKEN.fullmatch(token)
        if match is None:
            raise CalculationContractError(
                "calculation_contract.required_dates 只能使用 t、t-1、t-30 这类写法"
            )
        offset = int(match.group(1) or 0)
        if offset not in offsets:
            offsets.append(offset)
    if 0 not in offsets:
        raise CalculationContractError("calculation_contract.required_dates 必须包含 t")
    return sorted(offsets)


def _assignment_target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_assignment_target_names(item))
        return names
    return set()


def _node_parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _nearest_repeated_block(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> ast.AST | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _REPEATED_BLOCK_TYPES):
            return current
        current = parents.get(current)
    return None


def _scope_owner(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> ast.AST | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return current
        current = parents.get(current)
    return None


def _repeated_target_names(
    repeated_block: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> set[str]:
    names: set[str] = set()
    current: ast.AST | None = repeated_block
    while current is not None:
        if isinstance(current, (ast.For, ast.AsyncFor)):
            names.update(_assignment_target_names(current.target))
        elif isinstance(current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in current.generators:
                names.update(_assignment_target_names(generator.target))
        current = parents.get(current)
    return names


def _is_reusable_date_lookup_value(value: ast.AST) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if not isinstance(value, ast.Call):
        return False
    if isinstance(value.func, ast.Name):
        return value.func.id in {"dict", "defaultdict"}
    if isinstance(value.func, ast.Attribute):
        return value.func.attr in {"groupby", "set_index"}
    return False


def _reusable_date_lookup_names(
    tree: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if _nearest_repeated_block(node, parents) is not None:
            continue
        if isinstance(node, ast.Assign) and _is_reusable_date_lookup_value(node.value):
            for target in node.targets:
                names.update(_assignment_target_names(target))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_reusable_date_lookup_value(node.value)
        ):
            names.update(_assignment_target_names(node.target))
    return names


def _latest_assignment_in_repeated_block(
    name: str,
    repeated_block: ast.AST,
    call: ast.Call,
    parents: Mapping[ast.AST, ast.AST],
) -> ast.AST | None:
    call_line = int(getattr(call, "lineno", 0))
    call_scope = _scope_owner(call, parents)
    matches: list[tuple[int, ast.AST]] = []
    for node in ast.walk(repeated_block):
        if _scope_owner(node, parents) is not call_scope:
            continue
        value: ast.AST | None = None
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            value = node.value
            targets = [node.target]
        if value is None or not any(
            name in _assignment_target_names(target) for target in targets
        ):
            continue
        line = int(getattr(node, "lineno", 0))
        if line <= call_line:
            matches.append((line, value))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _is_small_frame_expression(
    expression: ast.AST,
    *,
    repeated_block: ast.AST,
    call: ast.Call,
    parents: Mapping[ast.AST, ast.AST],
    reusable_date_lookups: set[str],
    seen_names: set[str] | None = None,
) -> bool:
    """判断传入取样函数的表是否已缩小到所需日期。"""

    seen = set(seen_names or set())
    repeated_names = _repeated_target_names(repeated_block, parents)

    if isinstance(expression, ast.Name):
        if expression.id in repeated_names:
            return True
        if expression.id in seen:
            return False
        assigned_value = _latest_assignment_in_repeated_block(
            expression.id,
            repeated_block,
            call,
            parents,
        )
        if assigned_value is None:
            return False
        seen.add(expression.id)
        return _is_small_frame_expression(
            assigned_value,
            repeated_block=repeated_block,
            call=call,
            parents=parents,
            reusable_date_lookups=reusable_date_lookups,
            seen_names=seen,
        )

    if isinstance(expression, ast.Subscript):
        if isinstance(expression.value, ast.Attribute) and expression.value.attr in {
            "loc",
            "iloc",
        }:
            base = expression.value.value
            if isinstance(base, ast.Name) and base.id in reusable_date_lookups:
                return True
            return _is_small_frame_expression(
                base,
                repeated_block=repeated_block,
                call=call,
                parents=parents,
                reusable_date_lookups=reusable_date_lookups,
                seen_names=seen,
            )
        if _is_small_frame_expression(
            expression.value,
            repeated_block=repeated_block,
            call=call,
            parents=parents,
            reusable_date_lookups=reusable_date_lookups,
            seen_names=seen,
        ):
            return True
        return (
            isinstance(expression.value, ast.Name)
            and expression.value.id in reusable_date_lookups
        )

    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Attribute):
            method = expression.func.attr
            owner = expression.func.value
            if method in _SMALL_FRAME_METHODS:
                return _is_small_frame_expression(
                    owner,
                    repeated_block=repeated_block,
                    call=call,
                    parents=parents,
                    reusable_date_lookups=reusable_date_lookups,
                    seen_names=seen,
                )
            if method in _DATE_LOOKUP_METHODS:
                if isinstance(owner, ast.Name) and owner.id in reusable_date_lookups:
                    return True
                return _is_small_frame_expression(
                    owner,
                    repeated_block=repeated_block,
                    call=call,
                    parents=parents,
                    reusable_date_lookups=reusable_date_lookups,
                    seen_names=seen,
                )
            if method == "concat" and isinstance(owner, ast.Name) and owner.id == "pd":
                return any(
                    _is_small_frame_expression(
                        argument,
                        repeated_block=repeated_block,
                        call=call,
                        parents=parents,
                        reusable_date_lookups=reusable_date_lookups,
                        seen_names=seen,
                    )
                    for argument in expression.args
                )
        return False

    if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        return bool(expression.elts) and all(
            _is_small_frame_expression(
                item,
                repeated_block=repeated_block,
                call=call,
                parents=parents,
                reusable_date_lookups=reusable_date_lookups,
                seen_names=seen,
            )
            for item in expression.elts
        )

    if isinstance(expression, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _is_small_frame_expression(
            expression.elt,
            repeated_block=repeated_block,
            call=call,
            parents=parents,
            reusable_date_lookups=reusable_date_lookups,
            seen_names=seen,
        )

    if isinstance(expression, ast.IfExp):
        return all(
            _is_small_frame_expression(
                branch,
                repeated_block=repeated_block,
                call=call,
                parents=parents,
                reusable_date_lookups=reusable_date_lookups,
                seen_names=seen,
            )
            for branch in (expression.body, expression.orelse)
        )

    return False


def check_calculation_contract_call_performance(code_text: str) -> dict[str, object]:
    """执行策略前拦住循环中反复处理整张历史表的写法。"""

    try:
        tree = ast.parse(code_text)
    except SyntaxError as exc:
        raise CalculationContractError(f"策略代码语法错误: {exc}") from exc

    parents = _node_parents(tree)
    reusable_date_lookups = _reusable_date_lookup_names(tree, parents)
    repeated_calls: list[dict[str, object]] = []

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == SAMPLE_FUNCTION_NAME
        ):
            continue
        repeated_block = _nearest_repeated_block(node, parents)
        if repeated_block is None or not node.args:
            continue
        daily_frame = node.args[0]
        if not _is_small_frame_expression(
            daily_frame,
            repeated_block=repeated_block,
            call=node,
            parents=parents,
            reusable_date_lookups=reusable_date_lookups,
        ):
            try:
                argument_text = ast.unparse(daily_frame)
            except Exception:
                argument_text = "daily_frame"
            line = int(getattr(node, "lineno", 0))
            raise CalculationContractError(
                f"{SAMPLE_FUNCTION_NAME} 性能检查不通过：第{line}行位于循环内，"
                f"却把循环外的整段数据 `{argument_text}` 反复传入取样函数。"
                "请先在循环外按规范化交易日期建立可重复使用的字典或日期索引；"
                "循环内只取 contract.required_offsets 对应的 t 与 t-k 小表，再传给取样函数。"
                "不要在循环内对整张 history 反复执行 copy、sort_values、loc/isin 或 groupby。"
                "required_offsets=[0] 时，可以直接传入按交易日期 groupby 得到的单日 date_rows。"
            )
        repeated_calls.append(
            {
                "line": int(getattr(node, "lineno", 0)),
                "daily_frame": ast.unparse(daily_frame),
            }
        )

    return {
        "passed": True,
        "skipped": not repeated_calls,
        "checked_repeated_calls": repeated_calls,
    }


def _function_calls(code_text: str) -> tuple[set[str], dict[str, set[str]]]:
    try:
        tree = ast.parse(code_text)
    except SyntaxError as exc:
        raise CalculationContractError(f"策略代码语法错误: {exc}") from exc
    functions: set[str] = set()
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        functions.add(node.name)
        calls[node.name] = {
            child.func.id
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
    return functions, calls


def _is_reachable(calls: Mapping[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        direct = calls.get(name, set())
        if target in direct:
            return True
        pending.extend(direct - visited)
    return False


def _symbols_from_result(value: object) -> set[str]:
    if isinstance(value, pd.Series):
        raw = value.tolist()
    elif isinstance(value, pd.Index):
        raw = value.tolist()
    elif isinstance(value, np.ndarray):
        raw = value.tolist()
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raise CalculationContractError(
            f"{SAMPLE_FUNCTION_NAME} 必须返回股票代码列表、集合、Series 或 Index"
        )
    return {str(item) for item in raw}


def required_consecutive_market_days(candidate: object) -> list[int]:
    """找出候选中明确要求的连续市场交易日数量。"""

    if not isinstance(candidate, Mapping):
        return []
    searchable = str(candidate.get("hypothesis", ""))
    if any(marker in searchable for marker in _PER_SYMBOL_SEQUENCE_MARKERS):
        return []

    counts: set[int] = set()
    decision_map = candidate.get("decision_map")
    if isinstance(decision_map, list):
        for item in decision_map:
            if not isinstance(item, Mapping):
                continue
            field = str(item.get("field", "")).strip().lower()
            if "consecutive" not in field:
                continue
            try:
                count = int(item.get("value", 0))
            except (TypeError, ValueError):
                continue
            if count >= 2:
                counts.add(count)

    parameters = candidate.get("baseline_parameters")
    if isinstance(parameters, Mapping):
        for field, raw_value in parameters.items():
            if "consecutive" not in str(field).strip().lower():
                continue
            try:
                count = int(raw_value)
            except (TypeError, ValueError):
                continue
            if count >= 2:
                counts.add(count)

    for match in _CONSECUTIVE_TEXT.finditer(searchable):
        raw_count = match.group("count")
        count = int(raw_count) if raw_count.isdigit() else _CHINESE_DAY_NUMBERS[raw_count]
        if count >= 2:
            counts.add(count)
    return sorted(counts)


def _contract_consecutive_market_days(contract: object) -> list[int] | None:
    if contract is None:
        return None
    if not isinstance(contract, Mapping):
        raise CalculationContractError("market_day_continuity_contract 必须是对象")
    if not contract:
        return None
    if contract.get("mode") != "consecutive_market_trading_days":
        raise CalculationContractError(
            "market_day_continuity_contract.mode 必须是 consecutive_market_trading_days"
        )
    raw_days = contract.get("consecutive_days")
    if isinstance(raw_days, bool):
        raise CalculationContractError(
            "market_day_continuity_contract.consecutive_days 必须是至少2的整数"
        )
    try:
        consecutive_days = int(raw_days)
    except (TypeError, ValueError) as exc:
        raise CalculationContractError(
            "market_day_continuity_contract.consecutive_days 必须是至少2的整数"
        ) from exc
    if consecutive_days < 2 or consecutive_days != raw_days:
        raise CalculationContractError(
            "market_day_continuity_contract.consecutive_days 必须是至少2的整数"
        )
    if contract.get("missing_bar_action") != "condition_false":
        raise CalculationContractError(
            "market_day_continuity_contract.missing_bar_action 必须是 condition_false"
        )
    return [consecutive_days]


def _pairs_from_result(value: object) -> set[tuple[str, pd.Timestamp]]:
    if isinstance(value, pd.DataFrame):
        lookup = {str(column).strip().lower(): column for column in value.columns}
        symbol_column = lookup.get("symbol") or lookup.get("code")
        date_column = lookup.get("trade_date") or lookup.get("date")
        if symbol_column is None or date_column is None:
            raise CalculationContractError(
                f"{CONSECUTIVE_SAMPLE_FUNCTION_NAME} 返回DataFrame时必须包含股票代码列和日期列"
            )
        raw_pairs = list(zip(value[symbol_column], value[date_column]))
    elif isinstance(value, pd.MultiIndex):
        raw_pairs = value.tolist()
    elif isinstance(value, (list, tuple, set, pd.Index, np.ndarray)):
        raw_pairs = list(value)
    else:
        raise CalculationContractError(
            f"{CONSECUTIVE_SAMPLE_FUNCTION_NAME} 必须返回(股票代码, 结束日期)组成的列表、集合、MultiIndex或DataFrame"
        )

    pairs: set[tuple[str, pd.Timestamp]] = set()
    for item in raw_pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise CalculationContractError(
                f"{CONSECUTIVE_SAMPLE_FUNCTION_NAME} 的每项必须是(股票代码, 结束日期)"
            )
        symbol, raw_date = item
        date = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(date):
            raise CalculationContractError(
                f"{CONSECUTIVE_SAMPLE_FUNCTION_NAME} 返回了无法识别的日期"
            )
        pairs.add((str(symbol), pd.Timestamp(date)))
    return pairs


def check_generated_consecutive_market_days(
    code_text: str,
    candidate: object,
    *,
    continuity_contract: object = None,
) -> dict[str, object]:
    """用缺少紧邻K线的小数据检查“连续N个实际交易日”。"""

    contract_day_counts = _contract_consecutive_market_days(continuity_contract)
    day_counts = (
        contract_day_counts
        if contract_day_counts is not None
        else required_consecutive_market_days(candidate)
    )
    if not day_counts:
        return {"passed": True, "skipped": True, "reason": "候选没有连续市场交易日条件"}

    validate_generated_strategy_code(code_text, event_mode=False)
    functions, calls = _function_calls(code_text)
    if CONSECUTIVE_SAMPLE_FUNCTION_NAME not in functions:
        raise CalculationContractError(
            f"策略含连续交易日条件，必须定义 {CONSECUTIVE_SAMPLE_FUNCTION_NAME}"
        )
    if not _is_reachable(calls, "output_weights", CONSECUTIVE_SAMPLE_FUNCTION_NAME):
        raise CalculationContractError(
            f"output_weights 的实际信号计算必须调用 {CONSECUTIVE_SAMPLE_FUNCTION_NAME}，不能只定义不用"
        )

    namespace: dict[str, object] = {
        "__name__": "__generated_consecutive_market_day_check__",
        "json": json,
        "np": np,
        "pd": pd,
        "universe": [],
    }
    exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
    sample_function = namespace.get(CONSECUTIVE_SAMPLE_FUNCTION_NAME)
    if not callable(sample_function):
        raise CalculationContractError(f"无法载入 {CONSECUTIVE_SAMPLE_FUNCTION_NAME}")

    reports: list[dict[str, object]] = []
    for consecutive_days in day_counts:
        market_dates = pd.bdate_range("2020-01-02", periods=consecutive_days + 3)
        gap_position = len(market_dates) - 2
        rows: list[dict[str, object]] = []
        for symbol, positions in (
            ("FULL", range(len(market_dates))),
            ("GAP", [position for position in range(len(market_dates)) if position != gap_position]),
        ):
            rows.extend(
                {
                    "symbol": symbol,
                    "trade_date": market_dates[position],
                    "close": float(100 + position),
                }
                for position in positions
            )
        frame = pd.DataFrame(rows)
        try:
            selected = _pairs_from_result(
                sample_function(
                    frame.copy(deep=True),
                    market_dates.copy(),
                    consecutive_days,
                    "symbol",
                    "trade_date",
                )
            )
        except CalculationContractError:
            raise
        except Exception as exc:
            raise CalculationContractError(
                f"{CONSECUTIVE_SAMPLE_FUNCTION_NAME} 在缺K小数据上运行失败: {exc}"
            ) from exc

        expected: set[tuple[str, pd.Timestamp]] = set()
        available_by_symbol = {
            "FULL": set(range(len(market_dates))),
            "GAP": set(range(len(market_dates))) - {gap_position},
        }
        for symbol, available in available_by_symbol.items():
            for end_position in range(consecutive_days - 1, len(market_dates)):
                required = set(
                    range(end_position - consecutive_days + 1, end_position + 1)
                )
                if required.issubset(available):
                    expected.add((symbol, pd.Timestamp(market_dates[end_position])))
        if selected != expected:
            raise CalculationContractError(
                f"连续{consecutive_days}个实际交易日检查不通过：缺少紧邻市场日K线时不能退到更早一条股票记录。"
                f"应返回{sorted(expected)}，实际返回{sorted(selected)}"
            )
        reports.append(
            {
                "consecutive_days": consecutive_days,
                "selected_pair_count": len(selected),
            }
        )
    return {
        "passed": True,
        "sample_function": CONSECUTIVE_SAMPLE_FUNCTION_NAME,
        "requirement_source": (
            "market_day_continuity_contract"
            if contract_day_counts is not None
            else "candidate"
        ),
        "checked_requirements": reports,
    }


def _build_probe_frame(
    offsets: list[int],
    value_columns: list[str],
    *,
    allow_missing_intermediate_rows: bool,
) -> tuple[pd.DataFrame, pd.DatetimeIndex, pd.Timestamp, set[str]]:
    max_offset = max(offsets)
    market_dates = pd.bdate_range("2020-01-02", periods=max_offset + 2)
    decision_date = pd.Timestamp(market_dates[-1])
    required_positions = {len(market_dates) - 1 - offset for offset in offsets}

    rows: list[dict[str, object]] = []

    def add_symbol(symbol: str, positions: set[int], *, missing_value_at: int | None = None) -> None:
        for position in sorted(positions):
            row: dict[str, object] = {
                "symbol": symbol,
                "trade_date": market_dates[position],
            }
            for column_index, column in enumerate(value_columns):
                row[column] = float(100 + position + column_index)
            if missing_value_at is not None and position == missing_value_at:
                row[value_columns[-1]] = np.nan
            rows.append(row)

    all_positions = set(range(len(market_dates)))
    add_symbol("FULL", all_positions)
    add_symbol("MIDDLE_MISSING", required_positions)

    missing_required_position = min(required_positions)
    if missing_required_position == len(market_dates) - 1 and len(required_positions) > 1:
        missing_required_position = sorted(required_positions)[-2]
    add_symbol("MISSING_REQUIRED", all_positions - {missing_required_position})

    earliest_required = min(required_positions)
    substitute_positions = all_positions - {earliest_required}
    add_symbol("EARLY_SUBSTITUTE", substitute_positions)

    value_missing_position = max(required_positions)
    add_symbol(
        "MISSING_COMMON_VALUE",
        all_positions,
        missing_value_at=value_missing_position,
    )

    expected = {"FULL"}
    if allow_missing_intermediate_rows:
        expected.add("MIDDLE_MISSING")
    return pd.DataFrame(rows), market_dates, decision_date, expected


def check_generated_calculation_contracts(
    code_text: str,
    contracts: object,
) -> dict[str, object]:
    """用小型缺行情数据检查统一市场交易日和共同样本的实现。"""

    if not isinstance(contracts, list) or not contracts:
        return {"passed": True, "skipped": True, "reason": "候选没有计算口径约定"}

    checked_contracts = [
        item
        for item in contracts
        if isinstance(item, Mapping)
        and item.get("date_basis") == "shared_market_trading_calendar"
        and item.get("use_common_sample") is True
    ]
    if not checked_contracts:
        return {
            "passed": True,
            "skipped": True,
            "reason": "没有需要统一市场交易日共同样本检查的约定",
        }

    validate_generated_strategy_code(code_text, event_mode=False)
    functions, calls = _function_calls(code_text)
    if SAMPLE_FUNCTION_NAME not in functions:
        raise CalculationContractError(
            f"使用统一市场交易日共同样本时必须定义 {SAMPLE_FUNCTION_NAME}"
        )
    if not _is_reachable(calls, "output_weights", SAMPLE_FUNCTION_NAME):
        raise CalculationContractError(
            f"output_weights 的实际计算必须调用 {SAMPLE_FUNCTION_NAME}，不能只定义不用"
        )

    namespace: dict[str, object] = {
        "__name__": "__generated_calculation_contract_check__",
        "json": json,
        "np": np,
        "pd": pd,
        "universe": [],
    }
    exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
    sample_function = namespace.get(SAMPLE_FUNCTION_NAME)
    if not callable(sample_function):
        raise CalculationContractError(f"无法载入 {SAMPLE_FUNCTION_NAME}")

    reports: list[dict[str, object]] = []
    for raw_contract in checked_contracts:
        contract = dict(raw_contract)
        offsets = required_date_offsets(contract.get("required_dates"))
        raw_columns = contract.get("required_value_columns")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise CalculationContractError(
                "calculation_contract.required_value_columns 必须是非空数组"
            )
        value_columns = [str(item).strip() for item in raw_columns if str(item).strip()]
        if not value_columns:
            raise CalculationContractError(
                "calculation_contract.required_value_columns 必须包含有效列名"
            )
        allow_missing = contract.get("allow_missing_intermediate_rows") is True
        frame, market_dates, decision_date, expected = _build_probe_frame(
            offsets,
            value_columns,
            allow_missing_intermediate_rows=allow_missing,
        )
        runtime_contract = {
            **contract,
            "required_offsets": offsets,
        }
        try:
            selected = _symbols_from_result(
                sample_function(
                    frame.copy(deep=True),
                    market_dates.copy(),
                    decision_date,
                    runtime_contract,
                    "symbol",
                    "trade_date",
                )
            )
        except CalculationContractError:
            raise
        except Exception as exc:
            raise CalculationContractError(
                f"{SAMPLE_FUNCTION_NAME} 在缺行情小数据上运行失败: {exc}"
            ) from exc

        if selected != expected:
            raise CalculationContractError(
                "统一市场交易日共同样本不符合计划: "
                f"应选={sorted(expected)}，实际={sorted(selected)}；"
                "中间缺行不能改变 t-k 的日期，缺少关键日也不能用更早记录替代"
            )

        empty_frame = frame.loc[frame["symbol"].eq("MISSING_REQUIRED")].copy()
        empty_selected = _symbols_from_result(
            sample_function(
                empty_frame,
                market_dates.copy(),
                decision_date,
                runtime_contract,
                "symbol",
                "trade_date",
            )
        )
        if empty_selected:
            raise CalculationContractError("关键日期没有共同样本时，取样函数必须返回空集合")

        reports.append(
            {
                "contract_id": str(contract.get("contract_id", "")),
                "required_offsets": offsets,
                "selected_symbols": sorted(selected),
                "empty_sample_action": contract.get("empty_sample_action"),
            }
        )

    return {
        "passed": True,
        "sample_function": SAMPLE_FUNCTION_NAME,
        "checked_contracts": reports,
        "limitations": [
            "已检查精确 t-k 日期、中间缺行、共同字段样本和空样本取样；"
            "尚不能自动证明取样后的每个统计公式都与研究说明完全一致。"
        ],
    }
