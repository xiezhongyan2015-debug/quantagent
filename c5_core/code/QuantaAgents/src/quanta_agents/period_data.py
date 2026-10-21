from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from quanta_agents.semantic.metadata import (
    HISTORICAL_INDEX_UNIVERSES,
    historical_index_membership_table,
    normalize_historical_index_universe,
)


_MEMBERSHIP_COLUMNS = {"code", "start_date", "end_date"}
_HISTORICAL_MEMBERSHIP_TABLES = {
    table_key
    for name in HISTORICAL_INDEX_UNIVERSES
    if (table_key := historical_index_membership_table(name)) is not None
}


def _parse_period_date(value: object, field_name: str) -> pd.Timestamp:
    """把时期边界转成没有时分秒的日期。"""

    if isinstance(value, (date, datetime, pd.Timestamp)):
        parsed = pd.Timestamp(value)
    elif isinstance(value, str) and value.strip():
        parsed = pd.to_datetime(value.strip(), errors="coerce")
    else:
        raise ValueError(f"{field_name} is required")
    if pd.isna(parsed):
        raise ValueError(f"{field_name} is not a valid date")
    return pd.Timestamp(parsed).normalize()


def filter_membership_for_period(
    membership_df: Any,
    period_start: object,
    period_end: object,
) -> pd.DataFrame:
    """只返回请求时期内可以看到的成分记录，不暴露之后的退出日期。"""

    if not isinstance(membership_df, pd.DataFrame):
        raise TypeError("membership data must be a pandas DataFrame")
    missing_columns = _MEMBERSHIP_COLUMNS.difference(membership_df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"membership data is missing columns: {missing_text}")

    start_bound = _parse_period_date(period_start, "period_start")
    end_bound = _parse_period_date(period_end, "period_end")
    if start_bound > end_bound:
        raise ValueError("period_start must not be after period_end")

    scoped = membership_df.copy(deep=True)
    if scoped.empty:
        return scoped

    raw_end_dates = scoped["end_date"]
    missing_end_dates = raw_end_dates.isna()
    if pd.api.types.is_object_dtype(raw_end_dates.dtype) or pd.api.types.is_string_dtype(
        raw_end_dates.dtype
    ):
        missing_end_dates = missing_end_dates | raw_end_dates.astype("string").str.strip().eq("")

    start_dates = pd.to_datetime(scoped["start_date"], errors="coerce").dt.normalize()
    end_dates = pd.to_datetime(raw_end_dates, errors="coerce").dt.normalize()
    valid_end_dates = end_dates.notna() | missing_end_dates
    intersects_period = (
        start_dates.notna()
        & valid_end_dates
        & start_dates.le(end_bound)
        & (missing_end_dates | end_dates.ge(start_bound))
    )

    scoped = scoped.loc[intersects_period].copy()
    visible_start_dates = start_dates.loc[intersects_period]
    visible_end_dates = end_dates.loc[intersects_period]
    scoped["start_date"] = visible_start_dates
    scoped["end_date"] = visible_end_dates.where(
        visible_end_dates.notna() & visible_end_dates.le(end_bound),
        end_bound,
    )
    return scoped.reset_index(drop=True)


def is_dated_membership_item(item: object) -> bool:
    """判断数据说明是否表示带生效日期的成分表。"""

    if not isinstance(item, dict):
        return False
    table_key = str(item.get("table_key", item.get("name", ""))).strip().lower()
    fields = item.get("fields")
    normalized_fields = {
        str(field).strip().lower()
        for field in fields
        if isinstance(field, str) and str(field).strip()
    } if isinstance(fields, list) else set()
    return (
        table_key in _HISTORICAL_MEMBERSHIP_TABLES
        or "membership" in table_key
        or _MEMBERSHIP_COLUMNS.issubset(normalized_fields)
    )


def historical_membership_table_keys(required_data: object) -> list[str]:
    """找出本次策略实际使用的历史指数成分表。"""

    if not isinstance(required_data, list):
        return []
    resolved: list[str] = []
    for item in required_data:
        if not isinstance(item, dict):
            continue
        table_key = str(item.get("table_key", item.get("name", ""))).strip()
        if table_key in _HISTORICAL_MEMBERSHIP_TABLES and table_key not in resolved:
            resolved.append(table_key)
        universe = item.get("universe")
        if not isinstance(universe, dict) or universe.get("type") != "named_pool":
            continue
        historical_index = normalize_historical_index_universe(universe.get("value"))
        membership_table = historical_index_membership_table(historical_index)
        if membership_table is not None and membership_table not in resolved:
            resolved.append(membership_table)
    return resolved


def combine_historical_membership_data(
    data_bundle: object,
    required_data: object,
) -> pd.DataFrame | None:
    """按本次选定的股票范围合并历史成分区间，供检查和回测使用。"""

    membership_tables = historical_membership_table_keys(required_data)
    if not membership_tables:
        return None
    if not isinstance(data_bundle, dict):
        raise ValueError("历史成分数据字典缺失")

    frames: list[pd.DataFrame] = []
    for table_key in membership_tables:
        frame = data_bundle.get(table_key)
        if not isinstance(frame, pd.DataFrame):
            raise ValueError(f"历史成分数据缺失: {table_key}")
        missing_columns = _MEMBERSHIP_COLUMNS.difference(frame.columns)
        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(f"{table_key} 缺少字段: {missing_text}")
        frames.append(frame.loc[:, ["code", "start_date", "end_date"]].copy())

    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(
        subset=["code", "start_date", "end_date"],
        keep="last",
    ).reset_index(drop=True)
