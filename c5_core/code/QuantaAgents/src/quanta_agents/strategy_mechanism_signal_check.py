from __future__ import annotations

import ast
from copy import deepcopy
import difflib
import hashlib
import json
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)


MECHANISM_SIGNAL_FUNCTION_NAME = "build_mechanism_signal"
_FORBIDDEN_RUNTIME_GLOBALS = {
    "params",
    "train_data_bundle",
    "validate_data_bundle",
}
_DATE_COLUMNS = {
    "date",
    "datetime",
    "signal_date",
    "timestamp",
    "trade_date",
    "trigger_ts",
}


class MechanismSignalValidationError(ValueError):
    """生成代码没有在各机制对照中保持同一个新信号。"""


def _bound_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names}

    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets.append(node.target)

    names: set[str] = set()
    pending = list(targets)
    while pending:
        target = pending.pop()
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            pending.extend(target.elts)
    return names


def _top_level_bindings(tree: ast.Module) -> dict[str, ast.stmt]:
    bindings: dict[str, ast.stmt] = {}
    for node in tree.body:
        for name in _bound_names(node):
            if name in bindings:
                raise MechanismSignalValidationError(
                    f"策略代码在顶层重复定义 {name}，无法唯一核对新信号"
                )
            bindings[name] = node
    return bindings


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _function_arguments(function: ast.FunctionDef) -> set[str]:
    arguments = function.args
    names = {
        argument.arg
        for argument in (
            list(arguments.posonlyargs)
            + list(arguments.args)
            + list(arguments.kwonlyargs)
        )
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


def _validate_signal_signature(function: ast.FunctionDef) -> None:
    arguments = function.args
    positional = list(arguments.posonlyargs) + list(arguments.args)
    names = [argument.arg for argument in positional]
    if (
        names != ["train_data_bundle", "validate_data_bundle"]
        or arguments.vararg is not None
        or arguments.kwarg is not None
        or arguments.kwonlyargs
        or arguments.defaults
    ):
        raise MechanismSignalValidationError(
            f"{MECHANISM_SIGNAL_FUNCTION_NAME} 必须只接收 "
            "train_data_bundle 和 validate_data_bundle 两个参数；"
            "新信号的固定数值要写在该函数或它专用的辅助定义中"
        )


def _direct_signal_calls(function: ast.FunctionDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == MECHANISM_SIGNAL_FUNCTION_NAME
    ]


def _parent_map(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }


def _assignment_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for child in target.elts:
            names.update(_assignment_names(child))
        return names
    return set()


def _call_result_has_a_later_use(
    function: ast.FunctionDef,
    call: ast.Call,
    parents: Mapping[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Expr):
            return False
        if isinstance(parent, ast.Assign):
            names: set[str] = set()
            for target in parent.targets:
                names.update(_assignment_names(target))
            if not names or names == {"_"}:
                return False
            assignment_line = int(getattr(parent, "lineno", 0))
            return any(
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in names
                and int(getattr(node, "lineno", 0)) > assignment_line
                for node in ast.walk(function)
            )
        if isinstance(parent, ast.AnnAssign):
            names = _assignment_names(parent.target)
            if not names or names == {"_"}:
                return False
            assignment_line = int(getattr(parent, "lineno", 0))
            return any(
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in names
                and int(getattr(node, "lineno", 0)) > assignment_line
                for node in ast.walk(function)
            )
        if isinstance(parent, (ast.Return, ast.If, ast.While, ast.comprehension)):
            return True
        current = parent
    return False


def _validate_output_weights_usage(tree: ast.Module) -> None:
    output_functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "output_weights"
    ]
    if len(output_functions) != 1:
        raise MechanismSignalValidationError("策略代码必须唯一地定义 output_weights")
    output_function = output_functions[0]
    calls = _direct_signal_calls(output_function)
    if not calls:
        raise MechanismSignalValidationError(
            f"output_weights 必须直接调用 {MECHANISM_SIGNAL_FUNCTION_NAME}，"
            "并用返回结果生成最终股票权重"
        )
    parents = _parent_map(output_function)
    if not any(
        _call_result_has_a_later_use(output_function, call, parents)
        for call in calls
    ):
        raise MechanismSignalValidationError(
            f"output_weights 虽然调用了 {MECHANISM_SIGNAL_FUNCTION_NAME}，"
            "但丢弃了返回结果"
        )


def _reference_nodes(
    code_text: str,
    *,
    require_output_usage: bool = True,
) -> tuple[list[ast.stmt], list[str]]:
    try:
        tree = ast.parse(code_text, filename="generated_strategy.py")
    except SyntaxError as exc:
        raise MechanismSignalValidationError(f"策略代码语法错误: {exc}") from exc
    bindings = _top_level_bindings(tree)
    signal_node = bindings.get(MECHANISM_SIGNAL_FUNCTION_NAME)
    if not isinstance(signal_node, ast.FunctionDef):
        raise MechanismSignalValidationError(
            f"机制信号策略必须定义顶层函数 {MECHANISM_SIGNAL_FUNCTION_NAME}"
        )
    _validate_signal_signature(signal_node)
    if require_output_usage:
        _validate_output_weights_usage(tree)

    selected_nodes: dict[int, ast.stmt] = {}
    dependency_names: set[str] = set()
    pending = [MECHANISM_SIGNAL_FUNCTION_NAME]
    visited_names: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited_names:
            continue
        visited_names.add(name)
        node = bindings.get(name)
        if node is None:
            continue
        selected_nodes[id(node)] = node
        dependency_names.update(_bound_names(node))
        pending.extend(sorted(_loaded_names(node) & set(bindings)))

    if "output_weights" in dependency_names:
        raise MechanismSignalValidationError(
            f"{MECHANISM_SIGNAL_FUNCTION_NAME} 不能反过来调用 output_weights"
        )

    for node in selected_nodes.values():
        if not isinstance(node, ast.FunctionDef):
            continue
        local_names = _function_arguments(node)
        local_names.update(
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        )
        bad_globals = (
            _loaded_names(node) - local_names
        ) & _FORBIDDEN_RUNTIME_GLOBALS
        if bad_globals:
            raise MechanismSignalValidationError(
                f"{MECHANISM_SIGNAL_FUNCTION_NAME} 及其辅助函数不能读取全局 "
                + ", ".join(sorted(bad_globals))
                + "；新信号必须只由传入的训练和验证资料计算"
            )

    ordered = [node for node in tree.body if id(node) in selected_nodes]
    return ordered, sorted(dependency_names)


def mechanism_signal_reference(
    code_text: str,
    *,
    require_output_usage: bool = True,
) -> dict[str, object]:
    """提取新信号函数和它实际依赖的顶层定义。"""

    nodes, names = _reference_nodes(
        code_text,
        require_output_usage=require_output_usage,
    )
    canonical_parts = [
        (sorted(_bound_names(node)), ast.dump(node, include_attributes=False))
        for node in nodes
    ]
    canonical_parts.sort(key=lambda item: item[0])
    canonical = json.dumps(canonical_parts, ensure_ascii=False, separators=(",", ":"))
    source = "\n\n".join(ast.unparse(node) for node in nodes)
    return {
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "source": source,
        "dependency_names": names,
    }


def _copy_runtime_value(value: object) -> object:
    if isinstance(value, (pd.DataFrame, pd.Series)):
        # 这里只增加一层表对象，避免大型日线资料在固定检查中再次占用数倍内存。
        return value.copy(deep=False)
    if isinstance(value, Mapping):
        return {str(key): _copy_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_runtime_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_runtime_value(item) for item in value)
    try:
        return deepcopy(value)
    except Exception:
        return value


def _false_signal(value: object) -> object:
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return value.copy()
        boolean_columns = [
            column
            for column in value.columns
            if pd.api.types.is_bool_dtype(value[column].dtype)
            or str(column).strip().lower() in {"is_signal", "selected", "signal"}
        ]
        if boolean_columns:
            result = value.copy()
            for column in boolean_columns:
                result[column] = False
            return result
        numeric_columns = [
            column
            for column in value.columns
            if pd.api.types.is_numeric_dtype(value[column].dtype)
            and str(column).strip().lower() not in _DATE_COLUMNS
        ]
        if numeric_columns and len(numeric_columns) == len(value.columns):
            result = value.copy()
            result.loc[:, numeric_columns] = 0
            return result
        # 长表若每行本身代表一次入选，空表就是“没有任何新信号”。
        return value.iloc[0:0].copy()
    if isinstance(value, pd.Series):
        if pd.api.types.is_bool_dtype(value.dtype):
            return pd.Series(False, index=value.index, name=value.name, dtype=bool)
        if pd.api.types.is_numeric_dtype(value.dtype):
            return pd.Series(0, index=value.index, name=value.name, dtype=value.dtype)
        return value.iloc[0:0].copy()
    if isinstance(value, (pd.Index, pd.MultiIndex)):
        return value[:0]
    if isinstance(value, np.ndarray):
        if value.dtype.kind not in "biufc":
            raise MechanismSignalValidationError(
                f"{MECHANISM_SIGNAL_FUNCTION_NAME} 返回的数组必须是布尔或数值信号"
            )
        return np.zeros_like(value)
    if isinstance(value, Mapping):
        if value and all(isinstance(item, (bool, np.bool_)) for item in value.values()):
            return {key: False for key in value}
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, tuple):
        return ()
    if isinstance(value, set):
        return set()
    if isinstance(value, (bool, np.bool_)):
        return False
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return type(value)(0)
    raise MechanismSignalValidationError(
        f"{MECHANISM_SIGNAL_FUNCTION_NAME} 必须返回布尔表、布尔序列、"
        "入选键集合或其他可清空的信号结果"
    )


def _assert_zero_stock_weights(value: object) -> int:
    if not isinstance(value, pd.DataFrame):
        raise MechanismSignalValidationError(
            "把新信号改成全部不成立后，output_weights 必须仍返回 DataFrame"
        )
    numeric = value.select_dtypes(include="number")
    stock_columns = [
        column
        for column in numeric.columns
        if str(column).strip().lower() not in _DATE_COLUMNS | {"cash"}
    ]
    if not stock_columns:
        raise MechanismSignalValidationError(
            "output_weights 没有可检查的股票权重列"
        )
    for column in stock_columns:
        series = numeric[column]
        if isinstance(series.dtype, pd.SparseDtype):
            values = series.sparse.to_dense().to_numpy(dtype=float)
        else:
            values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
        if not bool(np.isfinite(values).all()):
            raise MechanismSignalValidationError(
                "把新信号改成全部不成立后，股票权重含有非有限数值"
            )
        if bool((np.abs(values) > 1e-12).any()):
            raise MechanismSignalValidationError(
                f"output_weights 调用了 {MECHANISM_SIGNAL_FUNCTION_NAME}，"
                "但新信号全部不成立时仍产生了非零股票权重；"
                "返回结果没有真正参与最终信号"
            )
    return len(stock_columns)


def _runtime_false_signal_check(
    code_text: str,
    strategy_result: Mapping[str, object],
) -> dict[str, object]:
    namespace: dict[str, Any] = {
        "__name__": "__generated_mechanism_signal_check__",
        "json": json,
        "np": np,
        "pd": pd,
        "universe": deepcopy(strategy_result.get("universe", [])),
        "calculation_contracts": deepcopy(
            strategy_result.get("calculation_contracts", [])
        ),
    }
    exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
    signal_function = namespace.get(MECHANISM_SIGNAL_FUNCTION_NAME)
    output_weights = namespace.get("output_weights")
    if not callable(signal_function) or not callable(output_weights):
        raise MechanismSignalValidationError(
            "无法载入新信号函数或 output_weights"
        )

    calls = 0

    def all_false_signal(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        try:
            original = signal_function(*args, **kwargs)
        except Exception as exc:
            raise MechanismSignalValidationError(
                f"{MECHANISM_SIGNAL_FUNCTION_NAME} 无法独立运行: {exc}"
            ) from exc
        return _false_signal(original)

    namespace[MECHANISM_SIGNAL_FUNCTION_NAME] = all_false_signal
    train_bundle = strategy_result.get("train_data_bundle")
    validate_bundle = strategy_result.get("validate_data_bundle")
    if not isinstance(train_bundle, Mapping) or not isinstance(validate_bundle, Mapping):
        raise MechanismSignalValidationError("策略结果缺少训练或验证资料")
    params = strategy_result.get("params", {})
    if not isinstance(params, Mapping):
        raise MechanismSignalValidationError("策略结果的 params 必须是对象")
    try:
        false_output = output_weights(
            _copy_runtime_value(train_bundle),
            _copy_runtime_value(validate_bundle),
            deepcopy(dict(params)),
        )
    except MechanismSignalValidationError:
        raise
    except Exception as exc:
        raise MechanismSignalValidationError(
            f"把新信号改成全部不成立后，output_weights 无法运行: {exc}"
        ) from exc
    if calls == 0:
        raise MechanismSignalValidationError(
            f"output_weights 实际运行时没有调用 {MECHANISM_SIGNAL_FUNCTION_NAME}"
        )
    checked_columns = _assert_zero_stock_weights(false_output)
    return {
        "function_calls": calls,
        "checked_stock_columns": checked_columns,
        "all_false_produces_zero_stock_weights": True,
    }


def check_generated_mechanism_signal(
    code_text: str,
    strategy_result: Mapping[str, object],
    *,
    reference_source: str | None = None,
    event_mode: bool = False,
) -> dict[str, object]:
    """核对机制函数、依赖定义和它对最终股票权重的实际影响。"""

    validate_generated_strategy_code(code_text, event_mode=event_mode)
    current = mechanism_signal_reference(code_text)
    expected_hash = ""
    if isinstance(reference_source, str) and reference_source.strip():
        reference = mechanism_signal_reference(
            reference_source,
            require_output_usage=False,
        )
        expected_hash = str(reference["sha256"])
        if current["sha256"] != expected_hash:
            diff = "\n".join(
                difflib.unified_diff(
                    str(reference["source"]).splitlines(),
                    str(current["source"]).splitlines(),
                    fromfile="added_to_base_signal",
                    tofile="comparison_signal",
                    lineterm="",
                    n=2,
                )
            )
            raise MechanismSignalValidationError(
                "机制对照改写了主版本的新信号函数或它依赖的定义。"
                f"应为 {expected_hash}，实际为 {current['sha256']}。\n"
                + diff[:4000]
            )

    runtime_report = _runtime_false_signal_check(code_text, strategy_result)
    return {
        "passed": True,
        "function_name": MECHANISM_SIGNAL_FUNCTION_NAME,
        "definition_sha256": current["sha256"],
        "reference_sha256": expected_hash or current["sha256"],
        "dependency_names": current["dependency_names"],
        "definition_matches_added_to_base": bool(expected_hash),
        "runtime_usage": runtime_report,
    }


__all__ = [
    "MECHANISM_SIGNAL_FUNCTION_NAME",
    "MechanismSignalValidationError",
    "check_generated_mechanism_signal",
    "mechanism_signal_reference",
]
