"""Strict, JSON-importable V9A proposal contracts; no data or account access."""
from __future__ import annotations

import ast
from dataclasses import MISSING, asdict, dataclass, field
import hashlib
import itertools
import json
import math
import operator
import re
import string
from typing import Any, Mapping

from quanta_agents.meta_v6.factors import FactorSpec

VERSION = "factor_research_contracts_v1"
ALLOWED_FIELDS = frozenset({"open", "high", "low", "close", "volume", "amount"})
ARMS = frozenset({"existing_library", "rule_perturbation", "llm_structure"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_OPS = {"lt": operator.lt, "le": operator.le, "gt": operator.gt,
        "ge": operator.ge, "eq": operator.eq}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _id(value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("identity must be a short reference, never a path")


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError("required text must be nonempty and bounded")


def strict_keys(value: Mapping, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, Mapping) or set(value) - allowed or required - set(value):
        raise ValueError(f"invalid keys; allowed={sorted(allowed)}, required={sorted(required)}")
    return dict(value)


def required_fields(expression: str) -> set[str]:
    tree = ast.parse(expression, mode="eval")
    functions = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in functions}


def template_parameters(template: str) -> set[str]:
    _text(template)
    result = set()
    for _, key, fmt, conversion in string.Formatter().parse(template):
        if key is not None:
            if not key.isidentifier() or fmt or conversion:
                raise ValueError("templates allow plain named integer placeholders only")
            result.add(key)
    return result


@dataclass(frozen=True)
class ParameterRange:
    values: tuple[int, ...]
    meaning: str

    def __post_init__(self):
        object.__setattr__(self, "values", tuple(self.values))
        if (not self.values or len(self.values) > 24 or len(set(self.values)) != len(self.values)
                or any(type(v) is not int or not 1 <= v <= 120 for v in self.values)):
            raise ValueError("parameter values must be unique literal integers in [1,120]")
        _text(self.meaning)

    @classmethod
    def from_dict(cls, value):
        return cls(**strict_keys(value, {"values", "meaning"}, {"values", "meaning"}))


@dataclass(frozen=True)
class JointConstraint:
    left: str
    op: str
    right: str | int

    def __post_init__(self):
        if not isinstance(self.left, str) or not self.left.isidentifier() or self.op not in _OPS:
            raise ValueError("invalid joint constraint")
        if not (type(self.right) is int or isinstance(self.right, str) and self.right.isidentifier()):
            raise ValueError("constraint right must be parameter name or integer")

    def accepts(self, parameters: Mapping[str, int]) -> bool:
        right = parameters[self.right] if isinstance(self.right, str) else self.right
        return bool(_OPS[self.op](parameters[self.left], right))

    @classmethod
    def from_dict(cls, value):
        return cls(**strict_keys(value, {"left", "op", "right"}, {"left", "op", "right"}))


@dataclass(frozen=True)
class ControlSpec:
    control_id: str
    expression_template: str
    purpose: str
    main_effect: bool = False

    def __post_init__(self):
        _id(self.control_id)
        template_parameters(self.expression_template)
        _text(self.purpose)
        if type(self.main_effect) is not bool:
            raise ValueError("main_effect must be boolean")

    @classmethod
    def from_dict(cls, value):
        return cls(**strict_keys(value, {"control_id", "expression_template", "purpose", "main_effect"},
                                 {"control_id", "expression_template", "purpose"}))


@dataclass(frozen=True)
class FalsifierSpec:
    falsifier_id: str
    kind: str
    metric: str
    control_refs: tuple[str, ...]
    threshold: float = 0.0
    comparator: str = "gt"
    split: str = "development_check"
    min_days: int = 60
    interpretation: str = "A non-positive paired gain contradicts the proposed added information."

    def __post_init__(self):
        _id(self.falsifier_id)
        object.__setattr__(self, "control_refs", tuple(self.control_refs))
        if self.kind not in {"paired_ic_gain", "fixed_baseline_increment", "interaction_increment"}:
            raise ValueError("falsifier must bind a supported executable comparison")
        if self.metric != "paired_delta_pearson_ic" or self.comparator not in _OPS:
            raise ValueError("unsupported falsifier metric or comparator")
        if self.split != "development_check":
            raise ValueError("generation falsifiers may only use development_check")
        if type(self.threshold) not in (int, float) or not math.isfinite(self.threshold):
            raise ValueError("threshold must be finite")
        if type(self.min_days) is not int or self.min_days < 1:
            raise ValueError("min_days must be a positive integer")
        if len(set(self.control_refs)) != len(self.control_refs):
            raise ValueError("duplicate control reference")
        if self.kind in {"paired_ic_gain", "interaction_increment"} and not self.control_refs:
            raise ValueError("matched comparison requires executable control references")
        _text(self.interpretation)

    def verdict(self, value: float | None, paired_days: int) -> str:
        if (type(paired_days) is not int or paired_days < self.min_days or value is None
                or type(value) not in (int, float) or not math.isfinite(value)):
            return "not_evaluable"
        return "supported" if _OPS[self.comparator](value, self.threshold) else "falsified"

    @classmethod
    def from_dict(cls, value):
        keys = set(cls.__dataclass_fields__)
        return cls(**strict_keys(value, keys, {"falsifier_id", "kind", "metric", "control_refs"}))


@dataclass(frozen=True)
class FactorFamily:
    family_id: str
    arm: str
    mechanism: str
    expected_relation: str
    expression_template: str
    parameters: Mapping[str, ParameterRange]
    constraints: tuple[JointConstraint, ...]
    controls: tuple[ControlSpec, ...]
    falsifiers: tuple[FalsifierSpec, ...]
    parents: tuple[str, ...]
    modification: str
    available_fields: tuple[str, ...]
    expected_direction: int = 1
    role: str = "return"
    primary_horizon: int = 5
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _id(self.family_id)
        if self.arm not in ARMS or self.role not in {"return", "risk", "condition", "interaction"}:
            raise ValueError("unsupported arm or role")
        if self.modification not in {"existing_definition", "window_only", "structural", "hypothesis_revision"}:
            raise ValueError("unsupported modification type")
        if self.arm == "llm_structure" and self.modification not in {"structural", "hypothesis_revision"}:
            raise ValueError("LLM structure arm must change structure")
        if self.arm == "rule_perturbation" and self.modification != "window_only":
            raise ValueError("rule arm is limited to window changes")
        if self.arm == "existing_library" and (self.modification != "existing_definition" or self.parameters):
            raise ValueError("existing arm must preserve exact fixed definitions")
        for value in (self.mechanism, self.expected_relation):
            _text(value)
        if type(self.expected_direction) is not int or self.expected_direction not in (-1, 1):
            raise ValueError("expected_direction must be -1 or +1")
        if type(self.primary_horizon) is not int or self.primary_horizon != 5:
            raise ValueError("V9A v1 primary horizon is frozen to five sessions")
        parameters = dict(self.parameters)
        if len(parameters) > 4 or any(not k.isidentifier() or not isinstance(v, ParameterRange)
                                      for k, v in parameters.items()):
            raise ValueError("at most four typed parameter domains are allowed")
        if math.prod(len(v.values) for v in parameters.values()) > 256:
            raise ValueError("family expansion exceeds hard grid bound")
        for name in ("constraints", "controls", "falsifiers", "parents", "available_fields"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "provenance", json.loads(canonical_json(dict(self.provenance))))
        if not self.available_fields or set(self.available_fields) - ALLOWED_FIELDS:
            raise ValueError("v1 permits only declared observable daily price/volume fields")
        for parent in self.parents:
            _id(parent)
        if self.arm != "existing_library" and not self.parents:
            raise ValueError("new and rule proposals must preserve parent references")
        control_ids = [c.control_id for c in self.controls]
        if len(set(control_ids)) != len(control_ids):
            raise ValueError("duplicate control ids")
        if not self.falsifiers or len({f.falsifier_id for f in self.falsifiers}) != len(self.falsifiers):
            raise ValueError("unique executable falsifiers are required")
        if self.arm == "llm_structure" and not self.controls:
            raise ValueError("structural proposals need matched controls")
        for f in self.falsifiers:
            if set(f.control_refs) - set(control_ids):
                raise ValueError("falsifier references absent control")
            if f.kind == "interaction_increment":
                main_ids = {c.control_id for c in self.controls if c.main_effect}
                if len(main_ids) < 2 or set(f.control_refs) != main_ids:
                    raise ValueError("interaction comparison must contain both main effects")
        for c in self.constraints:
            if c.left not in parameters or isinstance(c.right, str) and c.right not in parameters:
                raise ValueError("constraint references absent parameter")
        templates = [self.expression_template, *(c.expression_template for c in self.controls)]
        if set().union(*(template_parameters(t) for t in templates)) != set(parameters):
            raise ValueError("parameters must match exactly the placeholders used")
        if any(template_parameters(t) - set(parameters) for t in templates):
            raise ValueError("undeclared parameter placeholder")
        # Every admissible point is compiled up front. Models cannot smuggle a
        # forbidden field into an untested corner of the parameter range.
        points = self.parameter_points()
        if not points:
            raise ValueError("joint constraints admit no parameter point")
        for point in points:
            for template in templates:
                expression = template.format(**point)
                spec = FactorSpec(self.family_id, expression)
                if required_fields(spec.expression) - set(self.available_fields):
                    raise ValueError("expression uses fields outside its declaration")

    def parameter_points(self) -> tuple[dict[str, int], ...]:
        names = sorted(self.parameters)
        points = []
        for values in itertools.product(*(self.parameters[n].values for n in names)):
            point = dict(zip(names, values))
            if all(c.accepts(point) for c in self.constraints):
                points.append(point)
        return tuple(points)

    @property
    def proposal_id(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict:
        return json.loads(canonical_json(asdict(self)))

    @classmethod
    def from_dict(cls, value):
        keys = set(cls.__dataclass_fields__)
        required = {k for k, v in cls.__dataclass_fields__.items()
                    if v.default is MISSING and v.default_factory is MISSING}
        row = strict_keys(value, keys, required)
        if not isinstance(row["parameters"], Mapping):
            raise ValueError("parameters must be an object")
        row["parameters"] = {k: ParameterRange.from_dict(v) for k, v in row["parameters"].items()}
        for key, cls_ in (("constraints", JointConstraint), ("controls", ControlSpec), ("falsifiers", FalsifierSpec)):
            row[key] = tuple(cls_.from_dict(v) for v in row[key])
        return cls(**row)


@dataclass(frozen=True)
class ExpandedCandidate:
    spec: FactorSpec
    family_id: str
    arm: str
    kind: str
    parameters: Mapping[str, int]
    control_ids: Mapping[str, str]
    main_effect_ids: tuple[str, ...]
    falsifiers: tuple[FalsifierSpec, ...]
    trial_id: str
    proposal_id: str

    def to_dict(self) -> dict:
        return {"spec": self.spec.to_dict(), "family_id": self.family_id, "arm": self.arm,
                "kind": self.kind, "parameters": dict(self.parameters), "control_ids": dict(self.control_ids),
                "main_effect_ids": list(self.main_effect_ids), "falsifiers": [asdict(f) for f in self.falsifiers],
                "trial_id": self.trial_id, "proposal_id": self.proposal_id}


@dataclass(frozen=True)
class ExpansionResult:
    candidates: tuple[ExpandedCandidate, ...]
    attempts: tuple[dict, ...]
    summary: Mapping[str, Any]
