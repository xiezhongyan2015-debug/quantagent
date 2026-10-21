from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from quanta_agents.strategy_code_policy import (
    compile_strategy_definitions,
    validate_generated_strategy_code,
)


_DATE_COLUMN_NAMES = (
    "trade_date",
    "date",
    "datetime",
    "trigger_ts",
    "timestamp",
)
_MEMBERSHIP_COLUMNS = {"code", "start_date", "end_date"}


class FutureDataInfluenceError(ValueError):
    """完整数据改变了过去权重时抛出。"""


def _normalized_datetimes(values: Any) -> pd.Series:
    if isinstance(values, pd.PeriodIndex):
        values = values.to_timestamp()
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    if isinstance(parsed, pd.Series):
        normalized = parsed.dt.tz_convert(None)
        return normalized.reset_index(drop=True)
    if isinstance(parsed, pd.DatetimeIndex):
        parsed = parsed.tz_convert(None)
    return pd.Series(parsed).reset_index(drop=True)


def _find_date_axis(
    frame: pd.DataFrame,
    preferred_column: str | None = None,
) -> tuple[str, object | None, pd.Series] | None:
    column_lookup = {str(column).strip().lower(): column for column in frame.columns}
    candidates: list[str] = []
    if isinstance(preferred_column, str) and preferred_column.strip():
        candidates.append(preferred_column.strip().lower())
    candidates.extend(name for name in _DATE_COLUMN_NAMES if name not in candidates)

    for candidate in candidates:
        column = column_lookup.get(candidate)
        if column is None:
            continue
        parsed = _normalized_datetimes(frame[column])
        if bool(parsed.notna().any()):
            return "column", column, parsed

    index = frame.index
    if isinstance(index, (pd.DatetimeIndex, pd.PeriodIndex)):
        parsed = _normalized_datetimes(index)
        if bool(parsed.notna().any()):
            return "index", None, parsed

    if isinstance(index, pd.MultiIndex):
        for level_number in range(index.nlevels):
            level_values = index.get_level_values(level_number)
            level_name = str(index.names[level_number] or "").strip().lower()
            if not isinstance(level_values, (pd.DatetimeIndex, pd.PeriodIndex)) and level_name not in _DATE_COLUMN_NAMES:
                continue
            parsed = _normalized_datetimes(level_values)
            if bool(parsed.notna().any()):
                return "index", level_number, parsed

    index_name = str(index.name or "").strip().lower()
    if index_name in _DATE_COLUMN_NAMES:
        parsed = _normalized_datetimes(index)
        if bool(parsed.notna().any()):
            return "index", None, parsed
    return None


def has_dated_validation_data(validate_data_bundle: Mapping[str, object]) -> bool:
    return any(
        isinstance(value, pd.DataFrame) and _find_date_axis(value) is not None
        for value in validate_data_bundle.values()
    )


def _validation_dates(validate_data_bundle: Mapping[str, object]) -> pd.DatetimeIndex:
    best_dates = pd.DatetimeIndex([])
    for value in validate_data_bundle.values():
        if not isinstance(value, pd.DataFrame):
            continue
        axis = _find_date_axis(value)
        if axis is None:
            continue
        dates = pd.DatetimeIndex(axis[2].dropna().unique()).sort_values()
        if len(dates) > len(best_dates):
            best_dates = dates
    return best_dates


def _choose_cutoffs(
    validate_data_bundle: Mapping[str, object],
    cutoff_count: int,
) -> list[pd.Timestamp]:
    dates = _validation_dates(validate_data_bundle)
    if len(dates) < 3:
        raise FutureDataInfluenceError("验证期至少需要三个不同日期，才能做多日期截断检查")

    usable = dates[:-1]
    count = min(max(2, int(cutoff_count)), len(usable))
    positions = np.linspace(0, len(usable) - 1, count + 2)[1:-1]
    selected = sorted({int(round(position)) for position in positions})
    if len(selected) < 2:
        selected = sorted({0, len(usable) - 1})
    return [pd.Timestamp(usable[position]) for position in selected]


def _truncate_frame(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    if _MEMBERSHIP_COLUMNS.issubset(set(frame.columns)):
        starts = pd.to_datetime(frame["start_date"], errors="coerce").dt.normalize()
        ends = pd.to_datetime(frame["end_date"], errors="coerce").dt.normalize()
        visible_rows = starts.notna() & starts.le(cutoff)
        truncated = frame.iloc[np.flatnonzero(visible_rows.to_numpy())].copy()
        visible_ends = ends.iloc[np.flatnonzero(visible_rows.to_numpy())]
        clipped_ends = visible_ends.where(
            visible_ends.notna() & visible_ends.le(cutoff),
            cutoff,
        )
        truncated["end_date"] = clipped_ends.to_numpy()
        return truncated.reset_index(drop=True)

    axis = _find_date_axis(frame)
    if axis is None:
        return frame.copy(deep=False)
    parsed = axis[2]
    mask = parsed.notna() & parsed.le(cutoff)
    return frame.iloc[np.flatnonzero(mask.to_numpy())].copy(deep=False)


def _copy_bundle(
    bundle: Mapping[str, object],
    *,
    cutoff: pd.Timestamp | None = None,
) -> dict[str, object]:
    copied: dict[str, object] = {}
    for name, value in bundle.items():
        if isinstance(value, pd.DataFrame):
            copied[str(name)] = (
                _truncate_frame(value, cutoff)
                if cutoff is not None
                else value.copy(deep=False)
            )
        else:
            copied[str(name)] = value
    return copied


def _run_output_weights(
    output_weights: Callable[[dict[str, object], dict[str, object], dict[str, object]], object],
    train_data_bundle: Mapping[str, object],
    validate_data_bundle: Mapping[str, object],
    params: Mapping[str, object],
    *,
    cutoff: pd.Timestamp | None,
) -> pd.DataFrame:
    call_params = deepcopy(dict(params))
    params_before = repr(call_params)
    train_copy = _copy_bundle(train_data_bundle)
    validate_copy = _copy_bundle(validate_data_bundle, cutoff=cutoff)
    result = output_weights(train_copy, validate_copy, call_params)
    if repr(call_params) != params_before:
        raise FutureDataInfluenceError("output_weights 修改了传入的参数")
    if not isinstance(result, pd.DataFrame):
        raise FutureDataInfluenceError("output_weights 必须返回 pandas DataFrame")
    return result


def _output_before_cutoff(
    frame: pd.DataFrame,
    cutoff: pd.Timestamp,
    datetime_column: str | None,
) -> pd.DataFrame:
    axis = _find_date_axis(frame, datetime_column)
    if axis is None:
        raise FutureDataInfluenceError("权重表找不到日期列或日期索引")
    kind, label, parsed = axis
    if bool(parsed.isna().any()):
        raise FutureDataInfluenceError("权重表含有无法识别的日期")
    mask = parsed.le(cutoff)
    positions = np.flatnonzero(mask.to_numpy())
    payload = frame.iloc[positions].copy(deep=False)
    times = parsed.iloc[positions].reset_index(drop=True)

    if kind == "column" and label in payload.columns:
        payload = payload.drop(columns=[label])
    payload = payload.reset_index(drop=True)
    occurrences = times.groupby(times, sort=False).cumcount()
    payload.index = pd.MultiIndex.from_arrays(
        [times, occurrences],
        names=["__decision_time__", "__occurrence__"],
    )
    if not payload.columns.is_unique:
        raise FutureDataInfluenceError("权重表含有重复列名")
    return payload


def _numeric_array(series: pd.Series) -> np.ndarray | None:
    converted = pd.to_numeric(series, errors="coerce")
    invalid = series.notna() & converted.isna()
    if bool(invalid.any()):
        return None
    return converted.astype(float).to_numpy(copy=False)


def _first_index_text(index: pd.Index, position: int) -> str:
    try:
        return str(index[position])
    except Exception:
        return str(position)


def _compare_outputs(
    full: pd.DataFrame,
    truncated: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
    datetime_column: str | None,
    rtol: float,
    atol: float,
) -> None:
    full_past = _output_before_cutoff(full, cutoff, datetime_column)
    truncated_past = _output_before_cutoff(truncated, cutoff, datetime_column)
    if not full_past.index.equals(truncated_past.index):
        raise FutureDataInfluenceError(
            f"截断到 {cutoff} 后，过去权重的日期或行数发生变化"
        )

    all_columns = list(dict.fromkeys([*full_past.columns, *truncated_past.columns]))
    for column in all_columns:
        full_missing = column not in full_past.columns
        truncated_missing = column not in truncated_past.columns
        full_values = (
            np.zeros(len(full_past), dtype=float)
            if full_missing
            else _numeric_array(full_past[column])
        )
        truncated_values = (
            np.zeros(len(truncated_past), dtype=float)
            if truncated_missing
            else _numeric_array(truncated_past[column])
        )

        if full_values is not None and truncated_values is not None:
            equal = np.isclose(
                full_values,
                truncated_values,
                rtol=rtol,
                atol=atol,
                equal_nan=True,
            )
        else:
            if full_missing or truncated_missing:
                equal = np.zeros(len(full_past), dtype=bool)
            else:
                left = full_past[column].astype("object")
                right = truncated_past[column].astype("object")
                equal = (left.eq(right) | (left.isna() & right.isna())).to_numpy()

        if bool(np.all(equal)):
            continue
        position = int(np.flatnonzero(~equal)[0])
        full_value = 0.0 if full_missing else full_past[column].iloc[position]
        truncated_value = 0.0 if truncated_missing else truncated_past[column].iloc[position]
        decision_time = _first_index_text(full_past.index, position)
        raise FutureDataInfluenceError(
            "未来数据影响了过去权重："
            f"截断日={cutoff}，权重日={decision_time}，列={column!r}，"
            f"完整数据={full_value!r}，截断数据={truncated_value!r}"
        )


def check_output_weights_truncation_consistency(
    output_weights: Callable[[dict[str, object], dict[str, object], dict[str, object]], object],
    train_data_bundle: Mapping[str, object],
    validate_data_bundle: Mapping[str, object],
    params: Mapping[str, object],
    *,
    datetime_column: str | None = None,
    cutoff_count: int = 3,
    rtol: float = 1e-7,
    atol: float = 1e-9,
) -> dict[str, object]:
    """比较完整数据和多个截断日期下的历史权重是否一致。"""

    cutoffs = _choose_cutoffs(validate_data_bundle, cutoff_count)
    full_output = _run_output_weights(
        output_weights,
        train_data_bundle,
        validate_data_bundle,
        params,
        cutoff=None,
    )
    checked: list[str] = []
    for cutoff in cutoffs:
        truncated_output = _run_output_weights(
            output_weights,
            train_data_bundle,
            validate_data_bundle,
            params,
            cutoff=cutoff,
        )
        _compare_outputs(
            full_output,
            truncated_output,
            cutoff=cutoff,
            datetime_column=datetime_column,
            rtol=rtol,
            atol=atol,
        )
        checked.append(cutoff.isoformat())
    return {
        "passed": True,
        "checked_cutoffs": checked,
        "rtol": float(rtol),
        "atol": float(atol),
    }


def check_generated_daily_strategy(
    code_text: str,
    strategy_result: Mapping[str, object],
    *,
    cutoff_count: int = 3,
    rtol: float = 1e-7,
    atol: float = 1e-9,
) -> dict[str, object]:
    """载入生成代码，并执行日线策略的多日期截断检查。"""

    validate_generated_strategy_code(code_text, event_mode=False)
    try:
        import scipy.sparse as scipy_sparse
    except ImportError:
        scipy_sparse = None  # type: ignore[assignment]

    namespace: dict[str, object] = {
        "__name__": "__generated_strategy_future_check__",
        "json": json,
        "np": np,
        "pd": pd,
        "scipy_sparse": scipy_sparse,
        "universe": strategy_result.get("universe", []),
    }
    exec(compile_strategy_definitions(code_text), namespace)  # noqa: S102
    output_weights = namespace.get("output_weights")
    if not callable(output_weights):
        raise FutureDataInfluenceError("生成代码缺少 output_weights 函数")

    train_bundle = strategy_result.get("train_data_bundle")
    validate_bundle = strategy_result.get("validate_data_bundle")
    if not isinstance(train_bundle, Mapping) or not isinstance(validate_bundle, Mapping):
        raise FutureDataInfluenceError("策略结果缺少训练期或验证期数据")
    params = strategy_result.get("params", {})
    if not isinstance(params, Mapping):
        raise FutureDataInfluenceError("策略参数必须是字典")

    datetime_column: str | None = None
    strategy_output = strategy_result.get("strategy_output")
    if isinstance(strategy_output, Mapping):
        output_meta = strategy_output.get("output_weights_df")
        if isinstance(output_meta, Mapping):
            raw_datetime_column = output_meta.get("datetime_column")
            if isinstance(raw_datetime_column, str) and raw_datetime_column.strip():
                datetime_column = raw_datetime_column.strip()

    return check_output_weights_truncation_consistency(
        output_weights,  # type: ignore[arg-type]
        train_bundle,
        validate_bundle,
        params,
        datetime_column=datetime_column,
        cutoff_count=cutoff_count,
        rtol=rtol,
        atol=atol,
    )
