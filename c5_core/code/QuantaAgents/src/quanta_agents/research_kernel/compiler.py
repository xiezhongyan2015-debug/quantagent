"""Bounded JSON strategy language, independent of any account implementation.

Only caller-supplied, causally registered factor frames are operands. This
compiler cannot certify a misleadingly named input's provenance. Labels and
known label-role frames are rejected; Python source is never evaluated.

All expression nodes use ``args`` except factor(id) and constant(value). Rank
uses average percentile ranks of the supplied eligible universe, std uses ddof1,
and rolling windows require every observation. Directions are explicit nodes or
weights. Identity normalizes documented syntax aliases/defaults, not algebra.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Mapping

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_complex_dtype, is_numeric_dtype

LANGUAGE_VERSION = 1
MAX_DEPTH = 32
MAX_NODES = 512
MAX_ARGS = 32
MAX_WINDOW = 2520
MAX_CONSTANT = 1e12
MAX_METADATA_BYTES = 65536
ALIASES = {"cs_rank": "rank", "neg": "negate", "mul": "multiply", "sub": "subtract", "div": "divide",
           "rolling_mean": "mean", "rolling_std": "std"}
UNARY = frozenset({"rank", "negate", "abs", "lag", "mean", "std"})
BINARY = frozenset({"subtract", "divide", "gt", "lt", "ge", "le"})
WINDOWED = frozenset({"lag", "mean", "std"})
OPERATIONS = UNARY | BINARY | {"factor", "constant", "add", "multiply", "weighted_sum", "where"}
ALLOCATION_DEFAULTS = {"top_n": 20, "gross_exposure": 1., "max_stock_weight": .05, "weighting": "equal",
    "rebalance_sessions": 5, "rebalance_schedule": "sessions", "membership_buffer": 0}


class CapabilityGap(ValueError):
    """The declaration needs an operation this language has not implemented."""


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _number(value, label):
    _require(type(value) in (int, float), label + " must be a JSON number, not a boolean")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + " must be finite and bounded") from exc
    _require(math.isfinite(number) and abs(number) <= MAX_CONSTANT, label + " must be finite and bounded")
    # 1, 1.0 and -0.0/0 denote the same numeric operand. Fractional arithmetic
    # order and weights are otherwise unchanged by canonicalization.
    return 0 if number == 0 else int(number) if number.is_integer() else number


def _factor_id(value):
    _require(isinstance(value, str) and 0 < len(value) <= 128, "factor id must be a nonempty bounded string")
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is not None, "factor id must be an opaque identifier, not code or a path")
    words = re.split(r"[_.:-]", value.lower())
    _require(not any(word.startswith(("label", "future", "forward", "target", "outcome"))
                     or word in {"y", "ytrue"} for word in words), "label/future operands are forbidden")
    return value


def canonical_expression(expr):
    """Validate and return an independent normalized tree, without execution."""
    visited = 0

    def visit(node, depth):
        nonlocal visited
        visited += 1
        _require(depth <= MAX_DEPTH and visited <= MAX_NODES, "expression depth/node budget exceeded")
        _require(type(node) is dict and isinstance(node.get("op"), str), "expression nodes must be JSON objects with an op")
        raw_op = node["op"].strip().lower()
        op = ALIASES.get(raw_op, raw_op)
        if op not in OPERATIONS:
            raise CapabilityGap("unsupported expression operation: " + raw_op)
        keys = {"op", "id"} if op == "factor" else {"op", "value"} if op == "constant" else {"op", "args"}
        if op in WINDOWED:
            keys.add("window")
        if op == "weighted_sum":
            keys.add("weights")
        _require(set(node) == keys, "missing or unknown fields for " + op)
        if op == "factor":
            return {"op": op, "id": _factor_id(node["id"])}
        if op == "constant":
            return {"op": op, "value": _number(node["value"], "constant")}
        args = node["args"]
        _require(type(args) is list and len(args) <= MAX_ARGS, "args must be a bounded JSON list")
        required = 1 if op in UNARY else 2 if op in BINARY else 3 if op == "where" else None
        _require(len(args) == required if required else len(args) >= (1 if op == "weighted_sum" else 2), "invalid arity for " + op)
        result = {"op": op, "args": [visit(arg, depth + 1) for arg in args]}
        if op in WINDOWED:
            window = node["window"]
            _require(type(window) is int and 1 <= window <= MAX_WINDOW, "window must be a positive bounded integer; future shifts are forbidden")
            result["window"] = window
        if op == "weighted_sum":
            weights = node["weights"]
            _require(type(weights) is list and len(weights) == len(args), "one explicit weight per argument required")
            result["weights"] = [_number(weight, "weight") for weight in weights]
        return result

    return visit(expr, 1)


def factor_ids(expr):
    """Return the validated tree's factor dependencies, including zero weights."""
    tree, result = canonical_expression(expr), set()

    def walk(node):
        if node["op"] == "factor":
            result.add(node["id"])
        for child in node.get("args", []):
            walk(child)

    walk(tree)
    return result


def expression_id(expr):
    return hashlib.sha256(_json({"language_version": LANGUAGE_VERSION, "expression": canonical_expression(expr)}).encode("utf-8")).hexdigest()


def _axes(frames, eligible):
    _require(isinstance(eligible, pd.DataFrame) and not eligible.empty, "eligible must be a nonempty DataFrame")
    _require(not isinstance(eligible.index, pd.MultiIndex) and not isinstance(eligible.columns, pd.MultiIndex), "one date axis and one stock axis required")
    _require(eligible.index.is_unique and eligible.index.is_monotonic_increasing and not eligible.index.hasnans
             and eligible.columns.is_unique and not eligible.columns.hasnans, "unique ordered date axis and unique stock columns required")
    _require(all(is_bool_dtype(dtype) for dtype in eligible.dtypes) and not eligible.isna().any().any(), "eligible must be boolean without missing values")
    _require(isinstance(frames, Mapping), "frames must map factor identifiers to DataFrames")
    for name, frame in frames.items():
        _factor_id(name)
        _require(isinstance(frame, pd.DataFrame) and frame.index.equals(eligible.index)
                 and frame.columns.equals(eligible.columns), "factor axes must exactly equal eligible: " + name)
        _require(all(is_numeric_dtype(dtype) and not is_complex_dtype(dtype) for dtype in frame.dtypes), "factor frames must be real numeric: " + name)
        role = str(frame.attrs.get("role", frame.attrs.get("field_role", ""))).lower()
        _require(frame.attrs.get("is_label") is not True and not any(word in role for word in ("label", "future", "forward", "outcome", "target")),
                 "label-role factor frames are forbidden: " + name)


def evaluate_expression(expr, frames, eligible):
    """Evaluate causal registered factors; never mutate frames or fill NaNs.

    Histories remain observed histories: eligibility masks rank at each historic
    session and the final output, without erasing price/factor observations before
    lag or rolling. Comparisons return 0/1/NaN. ``where`` accepts 0/1/NaN masks;
    an unknown condition remains unknown and only the chosen branch is required.
    Local memoization shares identical subtrees within this evaluation only.
    """
    tree = canonical_expression(expr)
    _axes(frames, eligible)
    missing = factor_ids(tree) - set(frames)
    _require(not missing, "missing factor dependencies: " + ", ".join(sorted(missing)))
    cache = {}

    def clean(value):
        return value.where(np.isfinite(value))

    def evaluate(node):
        key = _json(node)
        if key in cache:
            return cache[key]
        op = node["op"]
        if op == "factor":
            value = clean(frames[node["id"]].astype(float, copy=True))
        elif op == "constant":
            value = pd.DataFrame(float(node["value"]), index=eligible.index, columns=eligible.columns)
        else:
            args = [evaluate(child) for child in node["args"]]
            with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                if op == "rank":
                    value = args[0].where(eligible).rank(axis=1, method="average", pct=True)
                elif op == "negate":
                    value = -args[0]
                elif op == "abs":
                    value = args[0].abs()
                elif op == "lag":
                    value = args[0].shift(node["window"])
                elif op in {"mean", "std"}:
                    rolling = args[0].rolling(node["window"], min_periods=node["window"])
                    value = rolling.mean() if op == "mean" else rolling.std(ddof=1)
                elif op in {"add", "multiply", "weighted_sum"}:
                    value = args[0] * node["weights"][0] if op == "weighted_sum" else args[0]
                    for i, arg in enumerate(args[1:], 1):
                        value = value * arg if op == "multiply" else value + (arg * node["weights"][i] if op == "weighted_sum" else arg)
                elif op == "subtract":
                    value = args[0] - args[1]
                elif op == "divide":
                    value = args[0] / args[1].where(args[1].ne(0))
                elif op in {"gt", "lt", "ge", "le"}:
                    valid = args[0].notna() & args[1].notna()
                    value = getattr(args[0], op)(args[1]).astype(float).where(valid)
                else:  # only where remains after the closed operation validator
                    mask = args[0]
                    _require((mask.isna() | mask.eq(0) | mask.eq(1)).all().all(), "where condition must be 0, 1 or missing")
                    value = args[1].where(mask.eq(1), args[2]).where(mask.notna())
            value = clean(value)
        cache[key] = value
        return value

    return evaluate(tree).where(eligible).copy(deep=True)


def _metadata(value):
    """Copy bounded JSON metadata without invoking arbitrary serializers."""
    count = 0

    def visit(item, depth=0):
        nonlocal count
        count += 1
        _require(depth <= MAX_DEPTH and count <= 4096, "metadata depth/node budget exceeded")
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float:
            _require(math.isfinite(item), "metadata numbers must be finite")
            return item
        if type(item) is list:
            return [visit(child, depth + 1) for child in item]
        _require(type(item) is dict and all(type(key) is str for key in item), "metadata must contain only JSON values")
        return {key: visit(child, depth + 1) for key, child in item.items()}

    _require(type(value) is dict, "metadata must be a JSON object")
    copied = visit(value)
    _require(len(_json(copied).encode("utf-8")) <= MAX_METADATA_BYTES, "metadata byte budget exceeded")
    return copied


def validate_strategy(spec):
    """Normalize defaults and reject unsupported or silently ignored controls."""
    _require(type(spec) is dict and {"name", "score"} <= set(spec)
             and set(spec) <= {"version", "name", "score", "allocation", "gate", "risk_score", "metadata"}, "missing/unknown strategy fields")
    version = spec.get("version", 1)
    _require(type(version) is int and version == 1, "unsupported strategy version")
    name = spec["name"]
    _require(isinstance(name, str) and bool(name.strip()) and len(name) <= 128, "bounded nonempty strategy name required")
    allocation = spec.get("allocation", {})
    _require(type(allocation) is dict and set(allocation) <= set(ALLOCATION_DEFAULTS), "unknown allocation control")
    allocation = {**ALLOCATION_DEFAULTS, **deepcopy(allocation)}
    for field, limit in (("top_n", 1000), ("rebalance_sessions", 120)):
        value = allocation[field]
        _require(type(value) is int and 1 <= value <= limit, field + " must be a positive bounded integer")
    for field in ("gross_exposure", "max_stock_weight"):
        value = _number(allocation[field], field)
        _require(0 <= value <= 1 and (field != "max_stock_weight" or value > 0), "long-only unlevered allocation bounds required")
        allocation[field] = float(value)
    _require(isinstance(allocation["weighting"], str) and allocation["weighting"] in {"equal", "inverse_volatility"}, "unsupported allocation weighting")
    _require(isinstance(allocation["rebalance_schedule"], str) and allocation["rebalance_schedule"] in {"sessions", "weekly_last_session"}, "unsupported rebalance schedule")
    buffer = allocation["membership_buffer"]
    _require(type(buffer) is int and 0 <= buffer <= 10000 and (buffer == 0 or buffer >= allocation["top_n"]), "holding buffer must be zero or >= top_n and bounded")
    risk = spec.get("risk_score")
    _require((allocation["weighting"] == "inverse_volatility") == (risk is not None), "inverse_volatility requires explicit risk_score; equal weighting cannot silently ignore it")
    return {"version": version, "name": name, "score": canonical_expression(spec["score"]), "allocation": allocation,
            "gate": canonical_expression(spec["gate"]) if spec.get("gate") is not None else None,
            "risk_score": canonical_expression(risk) if risk is not None else None, "metadata": _metadata(spec.get("metadata", {}))}


def strategy_id(spec):
    normalized = validate_strategy(spec)
    semantic = {key: value for key, value in normalized.items() if key not in {"name", "metadata"}}
    return hashlib.sha256(_json({"language_version": LANGUAGE_VERSION, "strategy": semantic}).encode("utf-8")).hexdigest()
