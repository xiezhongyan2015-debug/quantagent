from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype


_DATE = "__daily_research_date"
_CODE = "__daily_research_code"
_RETURN = "__daily_research_return"
_HORIZON = "__daily_research_horizon"


def _require_columns(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame 必须是 pandas DataFrame")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"研究帧缺少字段: {', '.join(missing)}")


def _normalise_names(values: Sequence[str], *, field: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} 必须是字段名列表")
    names: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{field} 只能包含非空字段名")
        name = raw.strip()
        if name in seen:
            raise ValueError(f"{field} 含有重复字段: {name}")
        seen.add(name)
        names.append(name)
    return names


def _prepare_frame(
    frame: pd.DataFrame,
    *,
    date_column: str,
    code_column: str,
    return_column: str,
    additional_columns: Sequence[str] = (),
) -> pd.DataFrame:
    _require_columns(
        frame,
        [date_column, code_column, return_column, *additional_columns],
    )
    prepared = frame.copy()

    dates = pd.to_datetime(prepared[date_column], errors="coerce")
    if bool(dates.isna().any()):
        raise ValueError(f"{date_column} 含有无效日期")
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    prepared[_DATE] = dates.dt.normalize()

    if bool(prepared[code_column].isna().any()):
        raise ValueError(f"{code_column} 含有空值")
    codes = prepared[code_column].astype(str).str.strip()
    if bool(codes.eq("").any()):
        raise ValueError(f"{code_column} 含有空代码")
    prepared[_CODE] = codes

    returns = pd.to_numeric(prepared[return_column], errors="coerce")
    prepared[_RETURN] = returns.where(np.isfinite(returns), np.nan)
    return prepared


def _rule_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    _require_columns(frame, [column])
    values = frame[column]
    if is_bool_dtype(values.dtype):
        return values.fillna(False).astype(bool)

    numeric = pd.to_numeric(values, errors="coerce")
    invalid_text = values.notna() & numeric.isna()
    if bool(invalid_text.any()):
        raise ValueError(f"规则字段 {column} 只能使用布尔值或 0/1")
    unique_values = set(float(value) for value in numeric.dropna().unique())
    if not unique_values.issubset({0.0, 1.0}):
        raise ValueError(f"规则字段 {column} 只能使用布尔值或 0/1")
    return numeric.fillna(0.0).eq(1.0)


def _combined_rule_mask(frame: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    mask = pd.Series(True, index=frame.index, dtype=bool)
    for column in columns:
        mask &= _rule_mask(frame, column)
    return mask


def _scope_mask(frame: pd.DataFrame, eligibility_column: str | None) -> pd.Series:
    if eligibility_column is None:
        return pd.Series(True, index=frame.index, dtype=bool)
    if not isinstance(eligibility_column, str) or not eligibility_column.strip():
        raise ValueError("eligibility_column 必须是非空字段名或 None")
    return _rule_mask(frame, eligibility_column.strip())


def _safe_mean(values: pd.Series) -> float | None:
    if values.empty:
        return None
    value = float(values.mean())
    return value if math.isfinite(value) else None


def _safe_median(values: pd.Series) -> float | None:
    if values.empty:
        return None
    value = float(values.median())
    return value if math.isfinite(value) else None


def _summary_without_years(frame: pd.DataFrame, mask: pd.Series) -> dict[str, object]:
    selected = frame.loc[mask]
    valid = selected.loc[selected[_RETURN].notna()]
    returns = valid[_RETURN].astype(float)
    daily_returns = valid.groupby(_DATE, sort=True)[_RETURN].mean().astype(float)
    return {
        "selected_row_count": int(len(selected)),
        "sample_count": int(len(valid)),
        "invalid_return_count": int(len(selected) - len(valid)),
        "signal_day_count": int(selected[_DATE].nunique()),
        "valid_return_day_count": int(valid[_DATE].nunique()),
        "stock_count": int(selected[_CODE].nunique()),
        "mean_return": _safe_mean(returns),
        "median_return": _safe_median(returns),
        "up_rate": float((returns > 0.0).mean()) if not returns.empty else None,
        "daily_equal_weight_mean": _safe_mean(daily_returns),
        "daily_equal_weight_median": _safe_median(daily_returns),
        "daily_equal_weight_up_rate": (
            float((daily_returns > 0.0).mean())
            if not daily_returns.empty
            else None
        ),
    }


def _summarize(frame: pd.DataFrame, mask: pd.Series) -> dict[str, object]:
    summary = _summary_without_years(frame, mask)
    selected_years = frame.loc[mask, _DATE].dt.year
    year_order = [str(int(year)) for year in sorted(selected_years.unique())]
    yearly: list[dict[str, object]] = []
    for year_text in year_order:
        year = int(year_text)
        row: dict[str, object] = {"year": year_text}
        row.update(
            _summary_without_years(
                frame,
                mask & frame[_DATE].dt.year.eq(year),
            )
        )
        yearly.append(row)
    summary["year_order"] = year_order
    summary["yearly"] = yearly
    return summary


def _numeric_grid(
    values: Sequence[float],
    *,
    field: str,
    sort_values: bool,
) -> list[float]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} 必须是数值列表")
    resolved: list[float] = []
    seen: set[float] = set()
    for raw in values:
        if isinstance(raw, bool):
            raise ValueError(f"{field} 不能包含布尔值")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 只能包含有限数值") from exc
        if not math.isfinite(value):
            raise ValueError(f"{field} 只能包含有限数值")
        if value not in seen:
            seen.add(value)
            resolved.append(value)
    if not resolved:
        raise ValueError(f"{field} 不能为空")
    return sorted(resolved) if sort_values else resolved


def _display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else float(value)


def _number_text(value: float) -> str:
    return f"{value:g}"


def _compare_feature(
    feature: pd.Series,
    threshold: float,
    operator: str,
) -> pd.Series:
    if operator == "<=":
        return feature.le(threshold)
    if operator == "<":
        return feature.lt(threshold)
    if operator == ">=":
        return feature.ge(threshold)
    if operator == ">":
        return feature.gt(threshold)
    raise ValueError("operator 只支持 <=、<、>=、>")


def parameter_response(
    frame: pd.DataFrame,
    *,
    feature_column: str,
    thresholds: Sequence[float],
    operator: str = "<=",
    rule_columns: Sequence[str] = (),
    current_value: float | None = None,
    eligibility_column: str | None = None,
    date_column: str = "decision_date",
    code_column: str = "code",
    return_column: str = "future_return",
) -> dict[str, object]:
    """比较同一连续特征在不同门槛下的结果，并给出互不重叠的数值分组。

    函数只汇总 ``return_column`` 中已有的数值，不读取价格，也不计算未来收益。
    ``rule_columns`` 始终保持不变，只有 ``feature_column`` 的门槛发生变化。
    """

    rules = _normalise_names(rule_columns, field="rule_columns")
    prepared = _prepare_frame(
        frame,
        date_column=date_column,
        code_column=code_column,
        return_column=return_column,
        additional_columns=[feature_column, *rules],
    )
    grid = _numeric_grid(thresholds, field="thresholds", sort_values=True)
    if current_value is not None:
        current_grid = _numeric_grid(
            [current_value],
            field="current_value",
            sort_values=False,
        )
        resolved_current: int | float | None = _display_number(current_grid[0])
    else:
        resolved_current = None

    feature = pd.to_numeric(prepared[feature_column], errors="coerce")
    feature = feature.where(np.isfinite(feature), np.nan)
    base_mask = _scope_mask(prepared, eligibility_column) & _combined_rule_mask(
        prepared,
        rules,
    )

    groups: list[dict[str, object]] = []
    group_order: list[str] = []
    for threshold in grid:
        label = f"{feature_column}{operator}{_number_text(threshold)}"
        group_order.append(label)
        groups.append(
            {
                "label": label,
                "threshold": _display_number(threshold),
                "summary": _summarize(
                    prepared,
                    base_mask & _compare_feature(feature, threshold, operator),
                ),
            }
        )

    bands: list[dict[str, object]] = []
    band_order: list[str] = []
    lower: float | None = None
    for upper in [*grid, None]:
        if lower is None and upper is not None:
            band_mask = feature.le(upper)
            label = f"(-inf,{_number_text(upper)}]"
        elif lower is not None and upper is not None:
            band_mask = feature.gt(lower) & feature.le(upper)
            label = f"({_number_text(lower)},{_number_text(upper)}]"
        elif lower is not None:
            band_mask = feature.gt(lower)
            label = f"({_number_text(lower)},inf)"
        else:  # pragma: no cover - grid 已保证非空
            continue
        band_order.append(label)
        bands.append(
            {
                "label": label,
                "lower_bound": None if lower is None else _display_number(lower),
                "upper_bound": None if upper is None else _display_number(upper),
                "lower_inclusive": False,
                "upper_inclusive": upper is not None,
                "summary": _summarize(prepared, base_mask & band_mask),
            }
        )
        lower = upper

    strictness_order = (
        list(group_order)
        if operator in {"<=", "<"}
        else list(reversed(group_order))
    )
    return {
        "id": "parameter_response",
        "date_column": date_column,
        "code_column": code_column,
        "return_column": return_column,
        "return_handling": "aggregate_provided_column_only",
        "feature_column": feature_column,
        "operator": operator,
        "current_value": resolved_current,
        "fixed_rule_columns": rules,
        "eligibility_column": eligibility_column,
        "base_summary": _summarize(prepared, base_mask),
        "threshold_order": [_display_number(value) for value in grid],
        "group_order": group_order,
        "strictness_order": strictness_order,
        "groups": groups,
        "band_order": band_order,
        "bands": bands,
    }


def rule_removal(
    frame: pd.DataFrame,
    *,
    rule_columns: Sequence[str],
    eligibility_column: str | None = None,
    date_column: str = "decision_date",
    code_column: str = "code",
    return_column: str = "future_return",
) -> dict[str, object]:
    """比较完整规则与逐条删除规则后的结果。

    删除某条规则时，其他规则保持不变。输出同时报告因此新增的样本，避免只看
    整体均值后无法判断差异来自哪里。
    """

    rules = _normalise_names(rule_columns, field="rule_columns")
    if not rules:
        raise ValueError("rule_columns 至少需要一个规则字段")
    if eligibility_column in rules:
        raise ValueError("eligibility_column 不能同时出现在 rule_columns 中")
    prepared = _prepare_frame(
        frame,
        date_column=date_column,
        code_column=code_column,
        return_column=return_column,
        additional_columns=rules,
    )
    scope = _scope_mask(prepared, eligibility_column)
    baseline_mask = scope & _combined_rule_mask(prepared, rules)
    variants: list[dict[str, object]] = [
        {
            "label": "baseline",
            "removed_rule": None,
            "summary": _summarize(prepared, baseline_mask),
        }
    ]
    group_order = ["baseline"]
    for removed_rule in rules:
        remaining = [rule for rule in rules if rule != removed_rule]
        variant_mask = scope & _combined_rule_mask(prepared, remaining)
        added_mask = variant_mask & ~baseline_mask
        label = f"without:{removed_rule}"
        group_order.append(label)
        variants.append(
            {
                "label": label,
                "removed_rule": removed_rule,
                "remaining_rules": remaining,
                "summary": _summarize(prepared, variant_mask),
                "newly_included_summary": _summarize(prepared, added_mask),
            }
        )
    return {
        "id": "rule_removal",
        "date_column": date_column,
        "code_column": code_column,
        "return_column": return_column,
        "return_handling": "aggregate_provided_column_only",
        "rule_columns": rules,
        "eligibility_column": eligibility_column,
        "group_order": group_order,
        "variants": variants,
    }


def signal_increment(
    frame: pd.DataFrame,
    *,
    base_rule_columns: Sequence[str],
    new_signal_column: str,
    replace_rule_column: str | None = None,
    eligibility_column: str | None = None,
    date_column: str = "decision_date",
    code_column: str = "code",
    return_column: str = "future_return",
) -> dict[str, object]:
    """比较原信号、新信号、两者交集，以及可选的规则替换版本。"""

    base_rules = _normalise_names(
        base_rule_columns,
        field="base_rule_columns",
    )
    if not isinstance(new_signal_column, str) or not new_signal_column.strip():
        raise ValueError("new_signal_column 必须是非空字段名")
    new_signal = new_signal_column.strip()
    if new_signal in base_rules:
        raise ValueError("new_signal_column 不能同时属于 base_rule_columns")
    if eligibility_column in [*base_rules, new_signal]:
        raise ValueError("eligibility_column 不能同时作为策略规则")
    replacement = None
    if replace_rule_column is not None:
        if not isinstance(replace_rule_column, str) or not replace_rule_column.strip():
            raise ValueError("replace_rule_column 必须是非空字段名或 None")
        replacement = replace_rule_column.strip()
        if replacement not in base_rules:
            raise ValueError("replace_rule_column 必须属于 base_rule_columns")

    prepared = _prepare_frame(
        frame,
        date_column=date_column,
        code_column=code_column,
        return_column=return_column,
        additional_columns=[*base_rules, new_signal],
    )
    scope = _scope_mask(prepared, eligibility_column)
    baseline_mask = scope & _combined_rule_mask(prepared, base_rules)
    new_mask = scope & _rule_mask(prepared, new_signal)
    combined_mask = baseline_mask & new_mask

    variant_masks: list[tuple[str, pd.Series, dict[str, object]]] = [
        ("baseline", baseline_mask, {}),
        ("new_signal_only", new_mask, {}),
        ("baseline_plus_new", combined_mask, {}),
    ]
    if replacement is not None:
        remaining_rules = [rule for rule in base_rules if rule != replacement]
        replacement_mask = (
            scope
            & _combined_rule_mask(prepared, remaining_rules)
            & _rule_mask(prepared, new_signal)
        )
        variant_masks.append(
            (
                f"replace:{replacement}:with:{new_signal}",
                replacement_mask,
                {
                    "replaced_rule": replacement,
                    "remaining_rules": remaining_rules,
                },
            )
        )

    variants: list[dict[str, object]] = []
    for label, mask, extra in variant_masks:
        row: dict[str, object] = {"label": label, "summary": _summarize(prepared, mask)}
        row.update(extra)
        variants.append(row)

    partition_masks = [
        ("baseline_passes_new", combined_mask),
        ("baseline_fails_new", baseline_mask & ~new_mask),
        ("new_outside_baseline", new_mask & ~baseline_mask),
    ]
    partitions = [
        {"label": label, "summary": _summarize(prepared, mask)}
        for label, mask in partition_masks
    ]
    baseline_count = int(baseline_mask.sum())
    combined_count = int(combined_mask.sum())
    return {
        "id": "signal_increment",
        "date_column": date_column,
        "code_column": code_column,
        "return_column": return_column,
        "return_handling": "aggregate_provided_column_only",
        "base_rule_columns": base_rules,
        "new_signal_column": new_signal,
        "replace_rule_column": replacement,
        "eligibility_column": eligibility_column,
        "group_order": [label for label, _, _ in variant_masks],
        "variants": variants,
        "partition_order": [label for label, _ in partition_masks],
        "partitions": partitions,
        "baseline_retention_rate": (
            float(combined_count / baseline_count)
            if baseline_count > 0
            else None
        ),
    }


def horizon_response(
    frame: pd.DataFrame,
    *,
    horizon_column: str = "horizon_days",
    horizons: Sequence[float] | None = None,
    rule_columns: Sequence[str] = (),
    eligibility_column: str | None = None,
    date_column: str = "decision_date",
    code_column: str = "code",
    return_column: str = "future_return",
) -> dict[str, object]:
    """汇总长表中已经提供的多个持有期收益。

    一行表示一个信号在一个持有期上的结果。函数不会从价格推导任何收益；为保证
    比较公平，还会另行报告在全部持有期都有有效结果的共同样本。
    """

    rules = _normalise_names(rule_columns, field="rule_columns")
    prepared = _prepare_frame(
        frame,
        date_column=date_column,
        code_column=code_column,
        return_column=return_column,
        additional_columns=[horizon_column, *rules],
    )
    raw_horizons = pd.to_numeric(prepared[horizon_column], errors="coerce")
    invalid_horizons = raw_horizons.isna() | ~np.isfinite(raw_horizons) | raw_horizons.le(0.0)
    if bool(invalid_horizons.any()):
        raise ValueError(f"{horizon_column} 只能包含大于 0 的有限数值")
    prepared[_HORIZON] = raw_horizons.astype(float)

    if horizons is None:
        resolved_horizons = sorted(float(value) for value in prepared[_HORIZON].unique())
        if not resolved_horizons:
            raise ValueError("研究帧没有持有期")
    else:
        resolved_horizons = _numeric_grid(
            horizons,
            field="horizons",
            sort_values=False,
        )
        if any(value <= 0.0 for value in resolved_horizons):
            raise ValueError("horizons 只能包含大于 0 的数值")

    scope = _scope_mask(prepared, eligibility_column) & _combined_rule_mask(
        prepared,
        rules,
    )
    requested_mask = prepared[_HORIZON].isin(resolved_horizons)
    scoped = prepared.loc[scope & requested_mask]
    duplicate_keys = scoped.duplicated([_DATE, _CODE, _HORIZON], keep=False)
    if bool(duplicate_keys.any()):
        raise ValueError("同一 decision_date、code、horizon 只能有一行")

    event_keys = scoped.loc[:, [_DATE, _CODE]].drop_duplicates()
    union_signal_count = int(len(event_keys))
    groups: list[dict[str, object]] = []
    for horizon in resolved_horizons:
        mask = scope & prepared[_HORIZON].eq(horizon)
        summary = _summarize(prepared, mask)
        valid_count = int(summary["sample_count"])
        groups.append(
            {
                "horizon": _display_number(horizon),
                "summary": summary,
                "valid_coverage_rate": (
                    float(valid_count / union_signal_count)
                    if union_signal_count > 0
                    else None
                ),
            }
        )

    valid_scoped = scoped.loc[scoped[_RETURN].notna()]
    available_counts = valid_scoped.groupby([_DATE, _CODE], sort=False)[
        _HORIZON
    ].nunique()
    common_index = available_counts.loc[
        available_counts.eq(len(resolved_horizons))
    ].index
    row_keys = pd.MultiIndex.from_frame(prepared.loc[:, [_DATE, _CODE]])
    common_key_mask = pd.Series(row_keys.isin(common_index), index=prepared.index)
    common_groups: list[dict[str, object]] = []
    for horizon in resolved_horizons:
        common_mask = (
            scope
            & common_key_mask
            & prepared[_HORIZON].eq(horizon)
        )
        common_groups.append(
            {
                "horizon": _display_number(horizon),
                "summary": _summarize(prepared, common_mask),
            }
        )

    group_order = [_display_number(value) for value in resolved_horizons]
    return {
        "id": "horizon_response",
        "date_column": date_column,
        "code_column": code_column,
        "return_column": return_column,
        "return_handling": "aggregate_provided_column_only",
        "horizon_column": horizon_column,
        "rule_columns": rules,
        "eligibility_column": eligibility_column,
        "group_order": group_order,
        "union_signal_count": union_signal_count,
        "groups": groups,
        "common_signal_count": int(len(common_index)),
        "common_group_order": group_order,
        "common_groups": common_groups,
    }


__all__ = [
    "horizon_response",
    "parameter_response",
    "rule_removal",
    "signal_increment",
]
