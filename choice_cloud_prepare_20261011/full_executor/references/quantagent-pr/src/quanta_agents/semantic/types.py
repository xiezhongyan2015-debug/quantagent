from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DataRequirement:
    dataset_name: str
    required_fields: list[str] = field(default_factory=list)
    required_factors: list[str] = field(default_factory=list)
    universe_name: str = "cn_all_a"
    granularity: str = "1d"
    start_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class SignalToken:
    kind: str
    value: str
    position: int


class SignalAstNode:
    pass


@dataclass(frozen=True)
class NumberNode(SignalAstNode):
    value: float


@dataclass(frozen=True)
class IdentifierNode(SignalAstNode):
    name: str


@dataclass(frozen=True)
class FunctionCallNode(SignalAstNode):
    name: str
    args: list[SignalAstNode]


@dataclass(frozen=True)
class BinaryOpNode(SignalAstNode):
    op: str
    left: SignalAstNode
    right: SignalAstNode


@dataclass(frozen=True)
class UnaryOpNode(SignalAstNode):
    op: str
    operand: SignalAstNode


@dataclass(frozen=True)
class ConditionalNode(SignalAstNode):
    condition: SignalAstNode
    if_true: SignalAstNode
    if_false: SignalAstNode