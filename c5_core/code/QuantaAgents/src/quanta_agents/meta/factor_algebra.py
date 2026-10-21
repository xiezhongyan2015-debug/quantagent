"""Causal factor expressions interpreted without executing user Python.

Rows are the caller's common chronological session grid; columns are stocks.
This language computes factors only. Data access, portfolio execution and
evaluation remain the responsibility of the trusted research runner.
"""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Collection, Mapping
from typing import Any

import numpy as np
import pandas as pd


MAX_EXPRESSION_LENGTH = 4096
MAX_NODES = 256
MAX_DEPTH = 24
MAX_WINDOW = 120
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub,
               ast.Mult: operator.mul, ast.Div: operator.truediv}
_COMPARISONS = {ast.Lt: operator.lt, ast.LtE: operator.le,
                ast.Gt: operator.gt, ast.GtE: operator.ge,
                ast.Eq: operator.eq, ast.NotEq: operator.ne}
_ROLLING = {"rolling_mean": "mean", "rolling_std": "std", "rolling_min": "min",
            "rolling_max": "max", "rolling_sum": "sum"}
_ARITIES = {**{name: 2 for name in _ROLLING}, "lag": 2, "pct_change": 2,
            "cs_rank": 1, "abs": 1, "log": 1, "clip": 3, "where": 3}


def _number(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _number(node.operand)
        if value is not None:
            return value if isinstance(node.op, ast.UAdd) else -value
    return None


def _parse(expression: str, available_fields: Collection[str]) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be nonempty text")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"expression exceeds {MAX_EXPRESSION_LENGTH} characters")
    names = set(available_fields)
    if any(not isinstance(name, str) or not name.isidentifier() or name.startswith("__")
           or name in _ARITIES for name in names):
        raise ValueError("field names must be identifiers and must not shadow functions or dunder names")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError, ValueError) as exc:
        raise ValueError("invalid expression syntax") from exc
    pending: list[tuple[ast.AST, int]] = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("expression exceeds node or depth limit")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    def check(node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if type(node.value) is bool:
                return
            if type(node.value) not in (int, float):
                raise ValueError("only finite numeric and boolean constants are allowed")
            try:
                finite = math.isfinite(node.value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("only finite numeric constants are allowed")
        elif isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unknown field: {node.id}")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _ARITHMETIC and not isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
                raise ValueError("unsupported binary operator")
            check(node.left)
            check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert, ast.Not)):
                raise ValueError("unsupported unary operator")
            check(node.operand)
        elif isinstance(node, ast.BoolOp):
            if not isinstance(node.op, (ast.And, ast.Or)):
                raise ValueError("unsupported boolean operator")
            for value in node.values:
                check(value)
        elif isinstance(node, ast.Compare):
            if any(type(op) not in _COMPARISONS for op in node.ops):
                raise ValueError("unsupported comparison")
            check(node.left)
            for value in node.comparators:
                check(value)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ARITIES:
                raise ValueError("only named allowlisted functions may be called")
            name = node.func.id
            if node.keywords or len(node.args) != _ARITIES[name]:
                raise ValueError(f"{name} requires {_ARITIES[name]} positional arguments")
            for value in node.args:
                check(value)
            if name in _ROLLING or name in {"lag", "pct_change"}:
                window = _number(node.args[1])
                minimum = 2 if name in _ROLLING else 1
                if type(window) is not int or not minimum <= window <= MAX_WINDOW:
                    raise ValueError(f"{name} window must be a literal integer in [{minimum}, {MAX_WINDOW}]")
            if name == "clip":
                low, high = _number(node.args[1]), _number(node.args[2])
                if low is None or high is None or low > high:
                    raise ValueError("clip bounds must be finite numeric literals with lower <= upper")
        else:
            raise ValueError(f"unsupported expression node: {type(node).__name__}")

    check(tree.body)
    return tree


def validate_expression(expression: str, available_fields: Collection[str]) -> None:
    """Validate names, operations and resource bounds without computing a factor."""
    _parse(expression, available_fields)


def evaluate_expression(expression: str, fields: dict[str, pd.DataFrame], *,
                        rank_universe: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return a float matrix on the original grid; boolean values are 1/0/NaN.

    Missing/nonfinite observations never receive forward/backward filling.
    Lag and rolling periods count rows in the supplied common session grid.
    The caller must supply ordered point-in-time fields on that grid.
    """
    if not isinstance(fields, Mapping) or not fields:
        raise ValueError("fields must be a nonempty mapping of aligned DataFrames")
    tree = _parse(expression, fields.keys())
    reference = next(iter(fields.values()))
    if not isinstance(reference, pd.DataFrame) or reference.empty:
        raise ValueError("fields must contain nonempty DataFrames")
    if not reference.index.is_unique or not reference.columns.is_unique:
        raise ValueError("field axes must be unique")
    if not reference.index.is_monotonic_increasing:
        raise ValueError("field rows must be in chronological increasing order")
    if rank_universe is not None:
        if (not isinstance(rank_universe, pd.DataFrame)
                or not rank_universe.index.equals(reference.index)
                or not rank_universe.columns.equals(reference.columns)):
            raise ValueError('rank_universe must have the same coordinates as fields')
        if not rank_universe.isin([True, False, 1, 0]).all(axis=None):
            raise ValueError('rank_universe must be a complete boolean mask')
        rank_universe = rank_universe.astype(bool)
    for name, frame in fields.items():
        if not isinstance(frame, pd.DataFrame):
            raise ValueError(f"field {name} is not a DataFrame")
        if not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns):
            raise ValueError(f"field {name} has a different index or columns")
        if any(not (pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype))
               or pd.api.types.is_complex_dtype(dtype) for dtype in frame.dtypes):
            raise ValueError(f"field {name} must contain real numeric or boolean values")
    cache: dict[str, pd.DataFrame] = {}

    def matrix(value: Any) -> pd.DataFrame:
        if isinstance(value, pd.DataFrame):
            return value
        return pd.DataFrame(float(value), index=reference.index, columns=reference.columns)

    def clean(value: Any) -> pd.DataFrame:
        frame = matrix(value).astype(float)
        return frame.where(np.isfinite(frame))

    def boolean(value: Any) -> pd.DataFrame:
        frame = clean(value)
        return frame.ne(0).where(frame.notna()).astype("boolean")

    def logical(left: Any, right: Any, op: ast.AST) -> pd.DataFrame:
        a, b = boolean(left), boolean(right)
        if isinstance(op, (ast.And, ast.BitAnd)):
            result = a & b
        elif isinstance(op, (ast.Or, ast.BitOr)):
            result = a | b
        else:
            result = a ^ b
        return result.astype(float)

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in cache:
                cache[node.id] = clean(fields[node.id])
            return cache[node.id]
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if type(node.op) in _ARITHMETIC:
                return clean(_ARITHMETIC[type(node.op)](matrix(left), matrix(right)))
            return logical(left, right, node.op)
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return clean(-matrix(value))
            return (~boolean(value)).astype(float)
        if isinstance(node, ast.BoolOp):
            result = visit(node.values[0])
            for value in node.values[1:]:
                result = logical(result, visit(value), node.op)
            return result
        if isinstance(node, ast.Compare):
            left = clean(visit(node.left))
            result: Any = True
            for op, value in zip(node.ops, node.comparators):
                right = clean(visit(value))
                comparison = _COMPARISONS[type(op)](left, right).astype(float)
                comparison = comparison.where(left.notna() & right.notna())
                result = logical(result, comparison, ast.And())
                left = right
            return result
        # All other syntax has already been rejected by _parse.
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        name = node.func.id
        frame = clean(visit(node.args[0]))
        if name in _ROLLING:
            window = int(_number(node.args[1]))  # type: ignore[arg-type]
            rolling = frame.rolling(window, min_periods=window, center=False)
            if name == "rolling_std":
                return clean(rolling.std(ddof=1))
            if name == "rolling_mean":
                return clean(rolling.mean())
            if name == "rolling_min":
                return clean(rolling.min())
            if name == "rolling_max":
                return clean(rolling.max())
            return clean(rolling.sum())
        if name in {"lag", "pct_change"}:
            previous = frame.shift(int(_number(node.args[1])))  # type: ignore[arg-type]
            return previous if name == "lag" else clean(frame / previous - 1.0)
        if name == "cs_rank":
            if rank_universe is not None:
                frame = frame.where(rank_universe)
            return frame.rank(axis=1, method="average", pct=True, na_option="keep")
        if name == "abs":
            return frame.abs()
        if name == "log":
            return clean(np.log(frame.where(frame > 0)))
        if name == "clip":
            return frame.clip(lower=_number(node.args[1]), upper=_number(node.args[2]))
        condition = boolean(frame)
        if_true, if_false = matrix(visit(node.args[1])), matrix(visit(node.args[2]))
        return clean(if_true.where(condition.fillna(False), if_false)).where(condition.notna())

    with np.errstate(divide="ignore", invalid="ignore", over="ignore", under="ignore"):
        result = clean(visit(tree.body))
    # Return an independent result even when the expression is a bare field.
    return result.copy(deep=True)


def expression_guide() -> dict[str, Any]:
    """Machine-readable language contract for the model-facing research tools."""
    return {
        "language": "causal_factor_algebra_v1",
        "fields": "Use only the caller-provided field names; every field is a date-by-stock matrix.",
        "operators": ["+", "-", "*", "/", "<", "<=", ">", ">=", "==", "!=",
                      "&", "|", "^", "~", "and", "or", "not"],
        "functions": {
            "lag(x, periods)": "Earlier rows only; literal integer periods 1..120.",
            "pct_change(x, periods)": "x / lag(x, periods) - 1; integer periods 1..120; no fill.",
            "rolling_mean(x, window)": "Per-stock trailing mean including current row; window 2..120.",
            "rolling_std(x, window)": "Per-stock trailing sample std (ddof=1); window 2..120.",
            "rolling_min(x, window)": "Per-stock trailing minimum; window 2..120.",
            "rolling_max(x, window)": "Per-stock trailing maximum; window 2..120.",
            "rolling_sum(x, window)": "Per-stock trailing sum; window 2..120.",
            "cs_rank(x)": "Same-row cross-sectional percentile rank within the controller's point-in-time eligible universe; average ties, nonmembers excluded at every nested rank.",
            "abs(x)": "Elementwise absolute value.",
            "log(x)": "Natural logarithm; nonpositive inputs become NaN.",
            "clip(x, lower, upper)": "Elementwise clipping to finite numeric literal bounds.",
            "where(condition, if_true, if_false)": "Elementwise selection; unknown condition yields NaN.",
        },
        "semantics": {
            "windows": "Literal integer row counts on the shared chronological session grid; complete windows required.",
            "missing": "No filling. Nonfinite values, zero division and invalid arithmetic become NaN.",
            "booleans": "Output is 1/0/NaN. Comparisons with missing inputs yield NaN; and/or use three-valued logic. Numeric zero is false, nonzero true. Unknown final filters must be excluded by the runner.",
            "timing": "Current row is available only after its field publication time; the runner controls execution lag and point-in-time eligibility.",
            "alignment": "All inputs need identical unique axes and increasing rows; output preserves those axes. No field is mutated.",
        },
        "limits": {"characters": MAX_EXPRESSION_LENGTH, "ast_nodes": MAX_NODES,
                   "ast_depth": MAX_DEPTH, "max_window": MAX_WINDOW},
        "forbidden": ["attribute access", "subscripts", "imports", "arbitrary functions", "keywords",
                      "future shifts", "full-sample statistics", "comprehensions", "assignments", "power"],
    }
