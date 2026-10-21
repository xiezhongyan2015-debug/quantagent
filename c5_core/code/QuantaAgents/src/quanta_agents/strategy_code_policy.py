from __future__ import annotations

import ast
from types import CodeType
from typing import Iterable


FORBIDDEN_EVENT_RESULT_FIELDS = frozenset(
    {
        "volume_hit",
        "next_up",
        "joint_minute_hit",
        "target_minute_return",
        "forward_5m",
        "forward_10m",
        "forward_20m",
    }
)

# 事件策略只需要数值计算、时间和常用容器。其他模块一律不允许导入，
# 避免通过系统、进程、网络或文件模块绕过已经读取好的数据字典。
_SAFE_IMPORT_MODULES = frozenset(
    {
        "collections",
        "collections.abc",
        "datetime",
        "json",
        "math",
        "numbers",
        "numpy",
        "numpy.typing",
        "pandas",
        "re",
        "scipy.sparse",
        "statistics",
        "typing",
    }
)

_SAFE_PANDAS_FROM_IMPORTS = frozenset(
    {
        "Categorical",
        "DataFrame",
        "DatetimeIndex",
        "Index",
        "NA",
        "NaT",
        "Series",
        "SparseDtype",
        "Timedelta",
        "Timestamp",
        "bdate_range",
        "concat",
        "crosstab",
        "cut",
        "date_range",
        "isna",
        "merge",
        "notna",
        "pivot_table",
        "qcut",
        "to_datetime",
        "to_numeric",
        "unique",
    }
)

_SAFE_NUMPY_FROM_IMPORTS = frozenset(
    {
        "abs",
        "arange",
        "array",
        "asarray",
        "argsort",
        "bool_",
        "clip",
        "concatenate",
        "datetime64",
        "diff",
        "empty",
        "exp",
        "flatnonzero",
        "float32",
        "float64",
        "full",
        "hstack",
        "inf",
        "int16",
        "int32",
        "int64",
        "int8",
        "isfinite",
        "isnan",
        "log",
        "log1p",
        "maximum",
        "mean",
        "median",
        "minimum",
        "nan",
        "ndarray",
        "nonzero",
        "ones",
        "percentile",
        "quantile",
        "rint",
        "searchsorted",
        "sort",
        "sqrt",
        "stack",
        "std",
        "sum",
        "timedelta64",
        "unique",
        "vstack",
        "where",
        "zeros",
    }
)

_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "getenv",
        "glob",
        "globals",
        "iglob",
        "input",
        "listdir",
        "locals",
        "open",
        "read_bytes",
        "read_excel",
        "read_feather",
        "read_json",
        "read_orc",
        "read_parquet",
        "read_pickle",
        "read_sql",
        "read_sql_query",
        "read_text",
        "rglob",
        "scandir",
        "setattr",
        "vars",
    }
)

_FORBIDDEN_IO_MEMBERS = frozenset(
    {
        "DataSource",
        "ExcelFile",
        "ExcelWriter",
        "HDFStore",
        "environ",
        "fromfile",
        "genfromtxt",
        "io",
        "load",
        "loadtxt",
        "memmap",
        "npyio",
        "open_memmap",
        "recfromcsv",
        "recfromtxt",
        "save",
        "savetxt",
        "savez",
        "savez_compressed",
        "to_csv",
        "to_excel",
        "to_feather",
        "to_hdf",
        "to_orc",
        "to_parquet",
        "to_pickle",
        "to_sql",
        "to_stata",
        "tofile",
    }
)

_FORBIDDEN_ENV_NAMES = frozenset(
    {
        "QUANTA_EVENT_FEATURES_PATH",
        "QUANTA_MINUTE_EVENT_FEATURES_PATH",
        "QUANTA_LEVEL2_EVENT_FEATURES_PATH",
    }
)

_RUNTIME_DATA_NAMES = frozenset({"train_data_bundle", "validate_data_bundle"})

_FUTURE_FILL_METHODS = frozenset({"bfill", "backfill"})


def _static_number_value(node: ast.AST | None) -> float | None:
    """读取简单数值常量，不执行生成的策略代码。"""

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _static_number_value(node.operand)
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left = _static_number_value(node.left)
        right = _static_number_value(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
        except (ArithmeticError, ValueError):
            return None
    return None


def _call_argument(
    node: ast.Call,
    keyword_name: str,
    positional_index: int,
) -> ast.AST | None:
    for keyword in node.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    if len(node.args) > positional_index:
        return node.args[positional_index]
    return None


def _static_string_value(node: ast.AST | None) -> str | None:
    """尽量还原由常量组成的字符串，不执行用户代码。"""

    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left)
        right = _static_string_value(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _static_string_value(node.left)
        if template is None:
            return None
        try:
            value = ast.literal_eval(node.right)
            rendered = template % value
        except (TypeError, ValueError, SyntaxError):
            return None
        return rendered if isinstance(rendered, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if not isinstance(value, ast.FormattedValue):
                return None
            try:
                rendered = ast.literal_eval(value.value)
            except (TypeError, ValueError, SyntaxError):
                return None
            parts.append(str(rendered))
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = _static_string_value(node.func.value)
        if base is None:
            return None
        if node.func.attr == "join" and len(node.args) == 1 and not node.keywords:
            try:
                values = ast.literal_eval(node.args[0])
            except (TypeError, ValueError, SyntaxError):
                return None
            if isinstance(values, (list, tuple)) and all(
                isinstance(value, str) for value in values
            ):
                return base.join(values)
        if node.func.attr == "format":
            try:
                args = [ast.literal_eval(arg) for arg in node.args]
                kwargs = {
                    keyword.arg: ast.literal_eval(keyword.value)
                    for keyword in node.keywords
                    if keyword.arg is not None
                }
                if len(kwargs) != len(node.keywords):
                    return None
                rendered = base.format(*args, **kwargs)
            except (KeyError, IndexError, TypeError, ValueError, SyntaxError):
                return None
            return rendered
    return None


def _is_forbidden_string(value: str, *, forbid_result_fields: bool) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    return (
        lowered.startswith("analysis_only_")
        or (forbid_result_fields and lowered in FORBIDDEN_EVENT_RESULT_FIELDS)
        or normalized in _FORBIDDEN_ENV_NAMES
        or (forbid_result_fields and lowered == "forward_")
    )


def _is_forbidden_member(name: str, *, forbid_result_fields: bool) -> bool:
    lowered = name.strip().lower()
    return (
        not lowered
        or lowered.startswith("analysis_only_")
        or (forbid_result_fields and lowered in FORBIDDEN_EVENT_RESULT_FIELDS)
        or lowered in {value.lower() for value in _FORBIDDEN_CALL_NAMES}
        or lowered in {value.lower() for value in _FORBIDDEN_IO_MEMBERS}
        or lowered.startswith("open_")
        or lowered.startswith("read_")
        or lowered.startswith("load_")
        or lowered.startswith("save_")
        or (lowered.startswith("__") and lowered != "__name__")
    )


def _validate_from_import(node: ast.ImportFrom, errors: list[str]) -> None:
    module = str(node.module or "")
    if module == "__future__":
        if any(alias.name != "annotations" for alias in node.names):
            errors.append("__future__ 只允许导入 annotations")
        return

    if module == "scipy":
        if any(alias.name != "sparse" for alias in node.names):
            errors.append("scipy 只允许导入 sparse")
        return

    if module not in _SAFE_IMPORT_MODULES:
        errors.append(f"禁止从 {module or '<相对模块>'} 导入")
        return

    for alias in node.names:
        imported_name = alias.name
        if imported_name == "*":
            errors.append(f"禁止从 {module} 使用星号导入")
            continue
        if _is_forbidden_member(imported_name, forbid_result_fields=True):
            errors.append(f"禁止从 {module} 导入 {imported_name}")
            continue
        if module == "pandas" and imported_name not in _SAFE_PANDAS_FROM_IMPORTS:
            errors.append(f"pandas 不允许直接导入 {imported_name}")
        if module == "numpy" and imported_name not in _SAFE_NUMPY_FROM_IMPORTS:
            errors.append(f"numpy 不允许直接导入 {imported_name}")


class _EventStrategyPolicyVisitor(ast.NodeVisitor):
    def __init__(self, *, forbid_result_fields: bool) -> None:
        self.errors: list[str] = []
        self.forbid_result_fields = forbid_result_fields
        self._iteration_depth = 0

    def _visit_iteration(self, node: ast.AST, *, depth: int = 1) -> None:
        self._iteration_depth += max(1, int(depth))
        try:
            self.generic_visit(node)
        finally:
            self._iteration_depth -= max(1, int(depth))

    def _check_static_string(self, node: ast.AST) -> None:
        value = _static_string_value(node)
        if value is not None and _is_forbidden_string(
            value,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(f"禁止在策略代码中引用结果字段或环境变量 {value}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in _SAFE_IMPORT_MODULES:
                self.errors.append(f"禁止导入 {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _validate_from_import(node, self.errors)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if (
            function_name == "_is_member_on_date"
            and self._iteration_depth >= 2
        ):
            self.errors.append(
                "禁止在日期与股票的双重循环或推导中逐只调用 "
                "_is_member_on_date；请先按日期生成成员集合 "
                "member_codes_by_date，再在日期循环中直接读取"
            )
        if _is_forbidden_member(
            function_name,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(f"禁止调用 {function_name or '<动态函数>'}")

        lowered_name = function_name.strip().lower()
        if lowered_name == "shift":
            periods_node = _call_argument(node, "periods", 0)
            periods = _static_number_value(periods_node)
            if (periods is not None and periods < 0) or (
                isinstance(periods_node, ast.UnaryOp)
                and isinstance(periods_node.op, ast.USub)
            ):
                self.errors.append("禁止使用负向 shift，过去时点不能读取未来行")

        if lowered_name in _FUTURE_FILL_METHODS:
            self.errors.append(f"禁止使用 {lowered_name}，它会用未来值填补过去缺失值")

        if lowered_name == "fillna":
            method = _static_string_value(_call_argument(node, "method", 1))
            if isinstance(method, str) and method.strip().lower() in _FUTURE_FILL_METHODS:
                self.errors.append(f"禁止使用 fillna(method={method!r})，它会用未来值填补过去缺失值")

        if lowered_name in {"rolling", "expanding"}:
            center_node = (
                _call_argument(node, "center", 2)
                if lowered_name == "rolling"
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "center"),
                    None,
                )
            )
            if center_node is not None:
                try:
                    center_value = ast.literal_eval(center_node)
                except (ValueError, SyntaxError):
                    center_value = None
                if center_value is not False and center_value != 0:
                    self.errors.append(
                        f"禁止使用 {lowered_name}(..., center=True)，窗口只能使用当前及过去数据"
                    )
        self._check_static_string(node)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_iteration(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_iteration(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_iteration(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_iteration(node, depth=len(node.generators))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_iteration(node, depth=len(node.generators))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_iteration(node, depth=len(node.generators))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_iteration(node, depth=len(node.generators))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _is_forbidden_member(
            node.attr,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(f"禁止访问属性 {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if _is_forbidden_member(
            node.id,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(f"禁止引用 {node.id}")
        if _is_forbidden_string(
            node.id,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(f"禁止在策略代码中引用结果字段 {node.id}")

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _is_forbidden_string(
            node.value,
            forbid_result_fields=self.forbid_result_fields,
        ):
            self.errors.append(
                f"禁止在策略代码中引用结果字段或环境变量 {node.value.strip()}"
            )

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self._check_static_string(node)
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self._check_static_string(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._check_static_string(node.slice)
        self.generic_visit(node)


def validate_generated_strategy_code(
    code_text: str,
    *,
    event_mode: bool,
    forbid_result_fields: bool = True,
) -> None:
    try:
        tree = ast.parse(code_text)
    except SyntaxError as exc:
        raise ValueError(f"策略代码语法错误: {exc}") from exc

    visitor = _EventStrategyPolicyVisitor(
        forbid_result_fields=forbid_result_fields and event_mode,
    )
    visitor.visit(tree)
    if visitor.errors:
        unique_errors = list(dict.fromkeys(visitor.errors))
        prefix = "事件策略代码检查失败：" if event_mode else "日线策略代码检查失败："
        raise ValueError(prefix + "；".join(unique_errors[:8]))


def _contains_runtime_data(nodes: Iterable[ast.AST | None]) -> bool:
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id in _RUNTIME_DATA_NAMES
            ):
                return True
    return False


def _contains_call(nodes: Iterable[ast.AST | None]) -> bool:
    return any(
        isinstance(child, ast.Call)
        for node in nodes
        if node is not None
        for child in ast.walk(node)
    )


def _function_definition_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST | None]:
    expressions: list[ast.AST | None] = [
        *node.args.defaults,
        *node.args.kw_defaults,
        node.returns,
    ]
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    expressions.extend(argument.annotation for argument in arguments)
    expressions.extend(getattr(node, "type_params", []))
    return expressions


def _validate_function_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    if node.decorator_list:
        raise ValueError(f"函数 {node.name} 不允许使用会在载入时执行的装饰器")
    expressions = _function_definition_expressions(node)
    if _contains_runtime_data(expressions):
        raise ValueError(f"函数 {node.name} 的默认值或注解不得读取回测时期数据")
    if _contains_call(expressions):
        raise ValueError(f"函数 {node.name} 的默认值或注解不得在载入时调用函数")


def _validate_class_definition(node: ast.ClassDef) -> None:
    if node.decorator_list:
        raise ValueError(f"类 {node.name} 不允许使用会在载入时执行的装饰器")
    header_expressions: list[ast.AST | None] = [
        *node.bases,
        *(keyword.value for keyword in node.keywords),
        *getattr(node, "type_params", []),
    ]
    if _contains_runtime_data(header_expressions):
        raise ValueError(f"类 {node.name} 的定义不得读取回测时期数据")
    if _contains_call(header_expressions):
        raise ValueError(f"类 {node.name} 的定义不得在载入时调用函数")

    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _validate_function_definition(child)
            continue
        if isinstance(child, ast.ClassDef):
            _validate_class_definition(child)
            continue
        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
            continue
        if isinstance(child, ast.Pass):
            continue
        if isinstance(child, ast.Assign):
            expressions = [child.value]
        elif isinstance(child, ast.AnnAssign):
            expressions = [child.annotation, child.value]
        else:
            raise ValueError(f"类 {node.name} 含有会在载入时执行的语句")
        if _contains_runtime_data(expressions):
            raise ValueError(f"类 {node.name} 的类变量不得读取回测时期数据")
        if _contains_call(expressions):
            raise ValueError(f"类 {node.name} 的类变量不得在载入时调用函数")


def _safe_module_statement(node: ast.stmt) -> ast.stmt | None:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _validate_function_definition(node)
        return node
    if isinstance(node, ast.ClassDef):
        _validate_class_definition(node)
        return node
    if isinstance(node, ast.Try):
        if node.orelse or node.finalbody:
            return None
        if not node.handlers:
            return None
        if any(not isinstance(item, (ast.Import, ast.ImportFrom)) for item in node.body):
            return None
        for handler in node.handlers:
            if not isinstance(handler.type, ast.Name) or handler.type.id not in {
                "ImportError",
                "ModuleNotFoundError",
            }:
                return None
            for item in handler.body:
                if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                    return None
                value = item.value
                if _contains_runtime_data([value]) or _contains_call([value]):
                    return None
        return node
    if isinstance(node, ast.Expr):
        return node if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) else None
    if isinstance(node, ast.Assign):
        expressions: list[ast.AST | None] = [node.value]
    elif isinstance(node, ast.AnnAssign):
        if _contains_runtime_data([node.annotation]) or _contains_call([node.annotation]):
            raise ValueError("顶层变量注解不得读取回测时期数据或调用函数")
        expressions = [node.value]
    else:
        return None

    # 顶层赋值可以保留纯常量和名称组合；任何函数调用或数据读取都不执行。
    if _contains_runtime_data(expressions) or _contains_call(expressions):
        return None
    return node


def compile_strategy_definitions(
    code_text: str,
    filename: str = "generated_strategy.py",
) -> CodeType:
    """只载入安全定义和静态常量，不执行任何顶层函数调用。"""

    tree = ast.parse(code_text, filename=filename)
    safe_body: list[ast.stmt] = []
    for node in tree.body:
        safe_node = _safe_module_statement(node)
        if safe_node is not None:
            safe_body.append(safe_node)
    tree.body = safe_body
    ast.fix_missing_locations(tree)
    return compile(tree, filename, "exec")
