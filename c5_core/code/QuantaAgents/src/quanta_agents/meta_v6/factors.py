"""Causal score construction and reusable, descriptive factor diagnostics.

This module has no file reader, model caller, portfolio weights or tax ledger.
The caller supplies an already scoped point-in-time panel. Forward returns are
adjusted-price diagnostics, not executable account returns or significance tests.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quanta_agents.meta.factor_algebra import (
    MAX_EXPRESSION_LENGTH, MAX_WINDOW, evaluate_expression, validate_expression,
    expression_guide as _base_guide,
)

LANGUAGE_VERSION = "causal_factor_algebra_v6_1"
_EXTENSIONS = {"ema": 2, "rolling_corr": 3, "rolling_residual": 3, "ts_rank": 2}
_FORBIDDEN = re.compile(r"(^|_)(future|forward|fwd|label|labels|target|lead|next)(_|$)", re.I)
_NON_SIGNAL = {"adjustment_factor", "qfq_ratio", "open_observed", "bar_observed"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _field_names(tree: ast.AST) -> set[str]:
    functions = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in functions}


def _is_signal_name(name: str) -> bool:
    return (not name.startswith("raw_") and name not in _NON_SIGNAL
            and not _FORBIDDEN.search(name) and not name.startswith("_v6cache"))


def _parse(expression: str, available_fields: set[str] | None = None) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be nonempty text")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("expression exceeds length limit")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError("invalid expression syntax") from exc
    names = _field_names(tree)
    if any(not _is_signal_name(name) for name in names):
        raise ValueError("factor expression cannot access future, label or execution-only fields")
    if names & _EXTENSIONS.keys():
        raise ValueError("field names cannot shadow factor functions")

    class LowerForValidation(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id in _EXTENSIONS:
                name = node.func.id
                if node.keywords or len(node.args) != _EXTENSIONS[name]:
                    raise ValueError(f"{name} requires {_EXTENSIONS[name]} positional arguments")
                window = node.args[-1]
                if (not isinstance(window, ast.Constant) or type(window.value) is not int
                        or not 2 <= window.value <= MAX_WINDOW):
                    raise ValueError(f"{name} window must be a literal integer in [2, {MAX_WINDOW}]")
                # Existing validation still checks every original operand and AST bound.
                node.func = ast.Name(id="rolling_mean" if len(node.args) == 2 else "where",
                                     ctx=ast.Load())
            return node

    try:
        lowered = LowerForValidation().visit(copy.deepcopy(tree))
        validate_expression(ast.unparse(lowered), available_fields if available_fields is not None else names)
    except RecursionError as exc:
        raise ValueError("expression exceeds depth limit") from exc
    return tree


def canonical_expression(expression: str) -> str:
    """Normalize syntax, not algebra: whitespace/parentheses do not create identities."""
    return ast.unparse(_parse(expression))


def expression_guide() -> dict[str, Any]:
    guide = _base_guide()
    guide["language"] = LANGUAGE_VERSION
    guide["functions"].update({
        "ema(x, window)": "Causal EWM span=window, adjust=False, min_periods=window, ignore_na=False; missing current x stays NaN.",
        "rolling_corr(x, y, window)": "Trailing Pearson correlation with window complete paired observations.",
        "rolling_residual(y, x, window)": "Current residual from trailing-window OLS y~1+x, complete paired window; zero regressor variance yields NaN.",
        "ts_rank(x, window)": "Percentile rank of current x in the complete trailing window; average ties.",
    })
    guide["semantics"]["extensions"] = "Extension windows are literal integers 2..120; no global or future fit."
    guide["semantics"]["field_roles"] = "Execution-only/raw/adjustment/label fields cannot be factor operands; custom feature point-in-time authenticity is the provider's responsibility."
    return guide


@dataclass(frozen=True)
class FactorSpec:
    name: str
    expression: str
    version: str = "1"
    parents: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("factor name and version must be nonempty")
        object.__setattr__(self, "expression", canonical_expression(self.expression))
        object.__setattr__(self, "parents", tuple(self.parents))
        object.__setattr__(self, "metadata", copy.deepcopy(dict(self.metadata)))

    @property
    def factor_id(self) -> str:
        return _digest({"language": LANGUAGE_VERSION, "expression": self.expression})

    @property
    def spec_id(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["language"] = LANGUAGE_VERSION
        return result


@dataclass
class FactorEvaluation:
    spec: FactorSpec
    data_fingerprint: str
    scores: pd.DataFrame
    summary: dict[str, Any]
    daily_ic: pd.DataFrame
    annual: pd.DataFrame
    quantile_returns: pd.DataFrame
    turnover: pd.DataFrame
    coverage: pd.DataFrame
    correlations: pd.DataFrame
    incremental: pd.DataFrame

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe metrics for evidence storage; scores are a separate artifact."""
        result = {"spec": self.spec.to_dict(), "data_fingerprint": self.data_fingerprint,
                  "summary": self.summary}
        for key in ("daily_ic", "annual", "quantile_returns", "turnover", "coverage",
                    "correlations", "incremental"):
            frame = getattr(self, key)
            result[key] = json.loads(frame.reset_index(drop=True).to_json(orient="records", date_format="iso"))
        return result


def _rank_ic(a: pd.DataFrame, b: pd.DataFrame, minimum: int) -> tuple[pd.Series, pd.Series]:
    valid = a.notna() & b.notna()
    n = valid.sum(axis=1)
    ar, br = a.where(valid).rank(axis=1), b.where(valid).rank(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ic = ar.corrwith(br, axis=1)
    return ic.where((n >= minimum) & (ar.nunique(axis=1) > 1) & (br.nunique(axis=1) > 1)), n


def _finite(value: float) -> float | None:
    return float(value) if pd.notna(value) and np.isfinite(value) else None


class FactorEngine:
    """An immutable panel snapshot with shared expression DAG and horizon caches.

    panel.fields: mapping[str, date-by-stock DataFrame]; panel.eligible: boolean
    matrix; panel.provenance: JSON-like metadata. Only the caller's supplied scope
    is accessed. Custom feature publication timing remains the data provider's duty.
    """

    def __init__(self, panel: Any, *, max_cache_bytes: int = 512 * 1024 * 1024):
        if type(max_cache_bytes) is not int or max_cache_bytes < 1:
            raise ValueError("max_cache_bytes must be a positive integer")
        fields = getattr(panel, "fields", None)
        eligible = getattr(panel, "eligible", None)
        if not isinstance(fields, Mapping) or not fields or "open" not in fields:
            raise ValueError("panel requires fields including adjusted signal open")
        reference = fields["open"]
        if (not isinstance(reference, pd.DataFrame) or reference.empty
                or not isinstance(reference.index, pd.DatetimeIndex)
                or not reference.index.is_unique or not reference.columns.is_unique
                or not reference.index.is_monotonic_increasing):
            raise ValueError("panel requires unique, chronological DatetimeIndex and stock columns")
        if (not isinstance(eligible, pd.DataFrame) or not eligible.index.equals(reference.index)
                or not eligible.columns.equals(reference.columns)
                or not eligible.isin([True, False, 0, 1]).all(axis=None)):
            raise ValueError("eligible must be a complete boolean matrix on the panel grid")
        self._fields: dict[str, pd.DataFrame] = {}
        for name, values in fields.items():
            if (not isinstance(name, str) or not name.isidentifier()
                    or not isinstance(values, pd.DataFrame)
                    or not values.index.equals(reference.index)
                    or not values.columns.equals(reference.columns)
                    or any(not pd.api.types.is_numeric_dtype(t) or pd.api.types.is_complex_dtype(t)
                           for t in values.dtypes)):
                raise ValueError(f"field {name!r} must be a real numeric matrix on the panel grid")
            self._fields[name] = values.astype(float).copy(deep=True)
        self._eligible = eligible.astype(bool).copy(deep=True)
        self.provenance = copy.deepcopy(dict(getattr(panel, "provenance", {}) or {}))
        excluded = set(self.provenance.get("execution_only_fields", []))
        roles = self.provenance.get("field_roles", {})
        self.feature_names = frozenset(name for name in fields if _is_signal_name(name)
                                      and name not in excluded
                                      and roles.get(name, "feature") not in {"label", "future", "execution"})
        fingerprint = hashlib.sha256(_json(self.provenance).encode("utf-8"))
        fingerprint.update(_json({"columns": list(reference.columns), "index_dtype": str(reference.index.dtype),
                                 "index_name": reference.index.name, "columns_name": reference.columns.name}).encode("utf-8"))
        for name, frame in sorted({**self._fields, "__eligible__": self._eligible}.items()):
            fingerprint.update(name.encode("utf-8"))
            fingerprint.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
        self.data_fingerprint = fingerprint.hexdigest()
        self._nodes: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._max_cache_bytes = max_cache_bytes
        self._cache_bytes = 0
        self._labels: dict[int, pd.DataFrame] = {}
        self._node_evaluations = 0
        self._label_evaluations = 0

    @property
    def cache_info(self) -> dict[str, Any]:
        return {"data_fingerprint": self.data_fingerprint, "nodes": len(self._nodes),
                "node_evaluations": self._node_evaluations,
                "cache_bytes": self._cache_bytes, "max_cache_bytes": self._max_cache_bytes,
                "horizons": sorted(self._labels), "label_evaluations": self._label_evaluations}

    def _node(self, node: ast.expr) -> pd.DataFrame:
        key = ast.dump(node, include_attributes=False)
        if key in self._nodes:
            self._nodes.move_to_end(key)
            return self._nodes[key]
        if isinstance(node, ast.Name):
            result = self._fields[node.id].where(np.isfinite(self._fields[node.id]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _EXTENSIONS:
            name, window = node.func.id, int(node.args[-1].value)
            x = self._node(node.args[0])
            rolling = x.rolling(window, min_periods=window)
            if name == "ema":
                result = x.ewm(span=window, adjust=False, min_periods=window, ignore_na=False).mean().where(x.notna())
            elif name == "ts_rank":
                result = rolling.rank(method="average", pct=True)
            else:
                y = self._node(node.args[1])
                if name == "rolling_corr":
                    result = rolling.corr(y)
                else:
                    # y argument here is the regressor; x is the dependent variable.
                    paired_x, paired_y = x.where(y.notna()), y.where(x.notna())
                    rx, ry = paired_x.rolling(window, min_periods=window), paired_y.rolling(window, min_periods=window)
                    beta = rx.cov(paired_y, ddof=0) / ry.var(ddof=0)
                    result = paired_x - (rx.mean() + beta * (paired_y - ry.mean()))
        else:
            lowered = copy.deepcopy(node)
            operands: dict[str, pd.DataFrame] = {}

            def replace(value: ast.expr) -> ast.expr:
                if isinstance(value, ast.Constant) or (isinstance(value, ast.UnaryOp)
                        and isinstance(value.op, (ast.UAdd, ast.USub)) and isinstance(value.operand, ast.Constant)):
                    return value
                name = f"_v6cache{len(operands)}"
                operands[name] = self._node(value)
                return ast.Name(id=name, ctx=ast.Load())

            for attr, value in ast.iter_fields(lowered):
                if isinstance(lowered, ast.Call) and attr == "func":
                    continue
                if isinstance(value, ast.expr):
                    setattr(lowered, attr, replace(value))
                elif isinstance(value, list):
                    setattr(lowered, attr, [replace(v) if isinstance(v, ast.expr) else v for v in value])
            if not operands:
                operands["_v6cache0"] = self._fields["open"]
            result = evaluate_expression(ast.unparse(lowered), operands, rank_universe=self._eligible)
        result = result.astype(float).where(np.isfinite(result))
        size = int(result.memory_usage(index=True, deep=True).sum())
        if size <= self._max_cache_bytes:
            while self._nodes and self._cache_bytes + size > self._max_cache_bytes:
                _, old = self._nodes.popitem(last=False)
                self._cache_bytes -= int(old.memory_usage(index=True, deep=True).sum())
            self._nodes[key] = result
            self._cache_bytes += size
        self._node_evaluations += 1
        return result

    def compute(self, spec: FactorSpec) -> pd.DataFrame:
        tree = _parse(spec.expression, set(self.feature_names))
        return self._node(tree.body).where(self._eligible).copy(deep=True)

    def _label(self, horizon: int) -> pd.DataFrame:
        if type(horizon) is not int or horizon < 1:
            raise ValueError("horizon must be a positive integer trading-session count")
        if horizon not in self._labels:
            opening = self._fields["open"]
            valid = np.isfinite(opening) & opening.gt(0)
            if "open_observed" in self._fields:
                valid &= self._fields["open_observed"].eq(1)
            entry, exit_ = opening.where(valid).shift(-1), opening.where(valid).shift(-(horizon + 1))
            self._labels[horizon] = (exit_ / entry - 1).where(self._eligible)
            self._label_evaluations += 1
        return self._labels[horizon]

    def labels(self, horizon: int) -> pd.DataFrame:
        """Signal t -> adjusted open[t+1] entry -> open[t+1+h] exit; no fill."""
        return self._label(horizon).copy(deep=True)

    def _align_existing(self, existing: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        result = {}
        for name, frame in existing.items():
            if (not isinstance(frame, pd.DataFrame) or not frame.index.equals(self._eligible.index)
                    or not frame.columns.equals(self._eligible.columns)):
                raise ValueError(f"existing factor {name!r} has different coordinates")
            result[name] = frame.astype(float).where(np.isfinite(frame)).where(self._eligible)
        return result

    @staticmethod
    def _residual_scores(scores: pd.DataFrame, existing: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        residual = pd.DataFrame(np.nan, index=scores.index, columns=scores.columns)
        for date in scores.index:
            cross = pd.concat([scores.loc[date].rename("candidate")]
                              + [frame.loc[date].rename(f"x{i}") for i, frame in enumerate(existing.values())], axis=1).dropna()
            if len(cross) < len(existing) + 3:
                continue
            ranks = cross.rank(axis=0, pct=True)
            x = np.column_stack([np.ones(len(cross)), ranks.iloc[:, 1:].to_numpy()])
            y = ranks.iloc[:, 0].to_numpy()
            error = y - x @ np.linalg.lstsq(x, y, rcond=None)[0]
            if np.std(error) > 1e-12:
                residual.loc[date, cross.index] = error
        return residual

    def evaluate(self, spec: FactorSpec, *, horizons: tuple[int, ...] = (1, 5, 20),
                 quantiles: int = 5, existing_factors: Mapping[str, pd.DataFrame] | None = None,
                 min_cross_section: int = 3) -> FactorEvaluation:
        if type(quantiles) is not int or quantiles < 2:
            raise ValueError("quantiles must be an integer >= 2")
        if type(min_cross_section) is not int or min_cross_section < 3:
            raise ValueError("min_cross_section must be an integer >= 3")
        if not horizons or len(set(horizons)) != len(horizons):
            raise ValueError("horizons must be nonempty and unique")
        for h in horizons:
            self._label(h)
        scores = self.compute(spec)
        existing = self._align_existing(existing_factors or {})
        residual = self._residual_scores(scores, existing) if existing else None
        eligible_n, factor_n = self._eligible.sum(axis=1), scores.notna().sum(axis=1)
        # Average ties stay together. Empty buckets remain unknown, never zero.
        groups = np.ceil(scores.rank(axis=1, method="average", pct=True) * quantiles)
        turnover_frames = []
        for q in range(1, quantiles + 1):
            membership = groups.eq(q)
            n = membership.sum(axis=1)
            new = (membership & ~membership.shift(1, fill_value=False)).sum(axis=1)
            turnover = new.div(n.where(n > 0)).where(membership.shift(1, fill_value=False).any(axis=1))
            turnover_frames.append(pd.DataFrame({"date": scores.index, "quantile": q,
                "members": n.to_numpy(), "turnover": turnover.to_numpy()}))
        ic_frames, coverage_frames, quantile_frames, annual_rows, incremental_rows = [], [], [], [], []
        horizon_summary = {}
        for h in horizons:
            label = self._label(h)
            ic, n = _rank_ic(scores, label, min_cross_section)
            ic_frames.append(pd.DataFrame({"date": scores.index, "horizon": h, "rank_ic": ic.to_numpy(),
                                           "paired_count": n.to_numpy()}))
            coverage_frames.append(pd.DataFrame({"date": scores.index, "horizon": h,
                "eligible_count": eligible_n.to_numpy(), "factor_count": factor_n.to_numpy(),
                "label_count": label.notna().sum(axis=1).to_numpy(), "paired_count": n.to_numpy(),
                "factor_coverage": factor_n.div(eligible_n.where(eligible_n > 0)).to_numpy(),
                "evaluation_coverage": n.div(eligible_n.where(eligible_n > 0)).to_numpy()}))
            means = {}
            for q in range(1, quantiles + 1):
                values = label.where(groups.eq(q))
                means[q] = values.mean(axis=1)
                quantile_frames.append(pd.DataFrame({"date": scores.index, "horizon": h, "quantile": q,
                    "mean_forward_return": means[q].to_numpy(), "count": values.notna().sum(axis=1).to_numpy()}))
            spread = means[quantiles] - means[1]
            horizon_summary[str(h)] = {"mean_rank_ic": _finite(ic.mean()), "ic_days": int(ic.count()),
                "paired_observations": int(n.sum()), "eligible_observations": int(eligible_n.sum()),
                "mean_top_minus_bottom": _finite(spread.mean())}
            for year in sorted(set(scores.index.year)):
                mask = scores.index.year == year
                annual_ic, annual_spread = ic.loc[mask], spread.loc[mask]
                std = annual_ic.std(ddof=1)
                annual_rows.append({"year": int(year), "horizon": h, "signal_days": int(mask.sum()),
                    "ic_days": int(annual_ic.count()), "mean_rank_ic": annual_ic.mean(),
                    "std_rank_ic": std, "ic_ir_unannualized": annual_ic.mean() / std if std > 0 else np.nan,
                    "eligible_observations": int(eligible_n.loc[mask].sum()),
                    "paired_observations": int(n.loc[mask].sum()),
                    "mean_top_minus_bottom": annual_spread.mean(), "spread_days": int(annual_spread.count())})
            if residual is not None:
                residual_ic, rn = _rank_ic(residual, label, min_cross_section)
                # Candidate and residual comparison uses exactly the same available pairs.
                common_ic, _ = _rank_ic(scores.where(residual.notna()), label, min_cross_section)
                incremental_rows.append({"horizon": h, "existing_factor_count": len(existing),
                    "candidate_mean_rank_ic_common": _finite(common_ic.mean()),
                    "residual_mean_rank_ic": _finite(residual_ic.mean()),
                    "residual_ic_days": int(residual_ic.count()), "paired_observations": int(rn.sum()),
                    "method": "daily_rank_ols_residual_descriptive_only"})
        correlations = []
        for name, frame in existing.items():
            corr, n = _rank_ic(scores, frame, min_cross_section)
            correlations.append({"existing_factor": name, "mean_rank_correlation": _finite(corr.mean()),
                                 "days": int(corr.count()), "paired_observations": int(n.sum())})
        summary = {"factor_id": spec.factor_id, "spec_id": spec.spec_id, "horizons": horizon_summary,
                   "scope": copy.deepcopy(self.provenance), "score_timing": "after_signal_session_fields_available",
                   "label_timing": "adjusted_open[t+1+h] / adjusted_open[t+1] - 1",
                   "quantile_ties": "average_rank_ties_kept_together_empty_buckets_missing",
                   "turnover": "new_members/current_members_vs_previous_signal_session; unknown_if_either_empty",
                   "limitations": ["descriptive_same_sample_diagnostics", "overlapping_forward_returns",
                       "no_transaction_costs_or_tax", "no_fill_or_delisting_settlement_simulation",
                       "no_statistical_significance_or_independent_validation_claim"],
                   "execution_certified": False, "full_account_backtest": False,
                   "cache": self.cache_info}
        return FactorEvaluation(spec, self.data_fingerprint, scores, summary,
            pd.concat(ic_frames, ignore_index=True), pd.DataFrame(annual_rows), pd.concat(quantile_frames, ignore_index=True),
            pd.concat(turnover_frames, ignore_index=True), pd.concat(coverage_frames, ignore_index=True),
            pd.DataFrame(correlations), pd.DataFrame(incremental_rows))
