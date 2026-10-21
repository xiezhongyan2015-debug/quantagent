from __future__ import annotations

import glob
import importlib
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATE_COLUMN_CANDIDATES = ("datetime", "date", "trade_date", "timestamp")
CASH_COLUMN_NAMES = {"cash", "cash_weight"}
CHINEXT_TWENTY_PERCENT_START = pd.Timestamp("2020-08-24")


def normalize_stock_code(value: object) -> str:
    """把常见股票代码转成 Parquet 文件使用的 sh/sz 形式。"""

    text = str(value).strip().upper()
    if not text:
        raise ValueError("股票代码不能为空")

    if len(text) == 8 and text[:2] in {"SH", "SZ"} and text[2:].isdigit():
        return text.lower()

    if "." in text:
        code, exchange = text.rsplit(".", maxsplit=1)
        if len(code) == 6 and code.isdigit():
            if exchange in {"SH", "SSE"}:
                return f"sh{code}"
            if exchange in {"SZ", "SZSE"}:
                return f"sz{code}"

    if len(text) == 6 and text.isdigit():
        exchange = "sh" if text[0] in {"5", "6", "9"} else "sz"
        return f"{exchange}{text}"

    raise ValueError(f"无法识别股票代码: {value}")


def _resolve_parquet_path(parquet_path: str | os.PathLike[str] | None) -> str:
    raw_path = str(
        parquet_path
        or os.getenv("QUANTA_PARQUET_DATA_GLOB")
        or os.getenv("QUANTA_PARQUET_ROOT")
        or "./data/parquet"
    ).strip()
    expanded = Path(os.path.expandvars(raw_path)).expanduser()
    if not expanded.is_absolute():
        expanded = PROJECT_ROOT / expanded
    if expanded.is_dir():
        expanded = expanded / "*.parquet"

    resolved = str(expanded)
    if next(glob.iglob(resolved), None) is None:
        raise FileNotFoundError(f"没有找到 Parquet 行情文件: {resolved}")
    return resolved


def _as_date(value: object, name: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} 不是有效日期: {value}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _prepare_weights(
    output_weights_df: pd.DataFrame,
    datetime_column: str | None,
) -> tuple[pd.DataFrame, str | None]:
    if not isinstance(output_weights_df, pd.DataFrame) or output_weights_df.empty:
        raise ValueError("output_weights_df 必须是非空 DataFrame")

    source = output_weights_df.copy()
    resolved_datetime_column = datetime_column
    if resolved_datetime_column is None:
        resolved_datetime_column = next(
            (name for name in DATE_COLUMN_CANDIDATES if name in source.columns),
            None,
        )

    if resolved_datetime_column is not None:
        if resolved_datetime_column not in source.columns:
            raise ValueError(f"找不到日期列: {resolved_datetime_column}")
        dates = pd.to_datetime(source.pop(resolved_datetime_column), errors="coerce")
    elif isinstance(source.index, pd.DatetimeIndex):
        dates = pd.to_datetime(source.index, errors="coerce")
    else:
        raise ValueError("目标权重必须有日期列或 DatetimeIndex")

    if bool(pd.isna(dates).any()):
        raise ValueError("目标权重含有无效日期")
    date_index = pd.DatetimeIndex(dates)
    if date_index.tz is not None:
        date_index = date_index.tz_localize(None)
    source.index = date_index.normalize()

    cash_column = next(
        (column for column in source.columns if str(column).strip().lower() in CASH_COLUMN_NAMES),
        None,
    )
    cash = None
    if cash_column is not None:
        cash = pd.to_numeric(source.pop(cash_column), errors="coerce")

    normalized_columns: dict[object, str] = {}
    seen_codes: set[str] = set()
    for column in source.columns:
        raw_code = normalize_stock_code(column)
        if raw_code in seen_codes:
            raise ValueError(f"股票代码重复: {column} 与另一列表示同一股票")
        seen_codes.add(raw_code)
        normalized_columns[column] = raw_code

    if not normalized_columns:
        raise ValueError("目标权重至少要有一只股票")

    weights = source.rename(columns=normalized_columns)
    weights = weights.apply(pd.to_numeric, errors="coerce")
    values = weights.to_numpy(dtype="float64")
    if not bool(pd.notna(weights).all(axis=None)) or not bool(pd.DataFrame(values).map(math.isfinite).all(axis=None)):
        raise ValueError("目标权重含有空值或非有限数值")
    if bool((weights < -1e-9).any(axis=None)):
        raise ValueError("A股目标权重不能为负数")
    weights = weights.clip(lower=0.0)

    stock_sums = weights.sum(axis=1)
    if bool((stock_sums > 1.0 + 1e-6).any()):
        raise ValueError("股票目标权重之和不能超过 1")

    if cash is not None:
        cash_values = cash.to_numpy(dtype="float64")
        if not bool(pd.notna(cash).all()) or not all(math.isfinite(float(value)) for value in cash_values):
            raise ValueError("现金权重含有空值或非有限数值")
        if bool(((cash < -1e-9) | (cash > 1.0 + 1e-6)).any()):
            raise ValueError("现金权重必须在 0 到 1 之间")
        total_weights = stock_sums + cash
        if bool(((total_weights - 1.0).abs() > 1e-6).any()):
            raise ValueError("每行股票权重加现金权重必须等于 1")

    weights = weights.groupby(level=0, sort=True).last()
    return weights.sort_index(), resolved_datetime_column


def _resolve_backtest_dates(
    weights: pd.DataFrame,
    start: object | None,
    end: object | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    resolved_start = (
        _as_date(start, "start")
        if start is not None
        else pd.Timestamp(weights.index.min())
    )
    resolved_end = (
        _as_date(end, "end")
        if end is not None
        else pd.Timestamp(weights.index.max()) + pd.Timedelta(days=14)
    )
    if resolved_end < resolved_start:
        raise ValueError("end 不能早于 start")
    return resolved_start, resolved_end


def _load_market_data(
    parquet_path: str,
    raw_codes: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    allow_empty: bool = False,
) -> pd.DataFrame:
    try:
        duckdb = importlib.import_module("duckdb")
    except Exception as exc:  # pragma: no cover - 取决于本机是否安装
        raise RuntimeError("读取本地 Parquet 需要安装 duckdb") from exc

    escaped_path = parquet_path.replace("\\", "/").replace("'", "''")
    placeholders = ", ".join("?" for _ in raw_codes)
    query = f"""
        WITH source AS (
            SELECT
                CASE
                    WHEN regexp_matches(lower(CAST(code AS VARCHAR)), '^sh[0-9]{{6}}$')
                        THEN lower(CAST(code AS VARCHAR))
                    WHEN regexp_matches(lower(CAST(code AS VARCHAR)), '^sz[0-9]{{6}}$')
                        THEN lower(CAST(code AS VARCHAR))
                    WHEN regexp_matches(upper(CAST(code AS VARCHAR)), '^[0-9]{{6}}\\.(SH|SSE)$')
                        THEN 'sh' || substr(CAST(code AS VARCHAR), 1, 6)
                    WHEN regexp_matches(upper(CAST(code AS VARCHAR)), '^[0-9]{{6}}\\.(SZ|SZSE)$')
                        THEN 'sz' || substr(CAST(code AS VARCHAR), 1, 6)
                    ELSE lower(CAST(code AS VARCHAR))
                END AS raw_code,
                CAST(date AS DATE) AS trade_date,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume,
                CAST(qfq_ratio AS DOUBLE) AS qfq_ratio,
                CAST(prev_close AS DOUBLE) AS prev_close,
                CAST(raw_open AS DOUBLE) AS raw_open,
                CAST(raw_prev_close AS DOUBLE) AS raw_prev_close,
                CAST(is_st AS BOOLEAN) AS is_st,
                CAST(is_delisting AS BOOLEAN) AS is_delisting
            FROM read_parquet('{escaped_path}', union_by_name=true)
        )
        SELECT raw_code, trade_date, open, high, low, close, volume, qfq_ratio,
               prev_close, raw_open, raw_prev_close, is_st, is_delisting
        FROM source
        WHERE raw_code IN ({placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY trade_date, raw_code
    """
    params: list[object] = [*raw_codes, start.date(), end.date()]
    connection = duckdb.connect(":memory:")
    try:
        try:
            market = connection.execute(query, params).fetchdf()
        except Exception as exc:
            required_limit_fields = (
                "qfq_ratio",
                "raw_open",
                "raw_prev_close",
                "is_st",
                "is_delisting",
            )
            if any(field in str(exc) for field in required_limit_fields):
                raise ValueError(
                    "行情必须包含 qfq_ratio、raw_open、raw_prev_close、"
                    "is_st 和 is_delisting，无法正确判断涨跌停"
                ) from exc
            raise
    finally:
        connection.close()

    if market.empty and not allow_empty:
        raise ValueError("所选股票和日期范围没有行情")
    if not market.empty:
        qfq_ratios = pd.to_numeric(market["qfq_ratio"], errors="coerce")
        invalid_qfq = ~qfq_ratios.map(_finite_positive)
        if bool(invalid_qfq.any()):
            bad_rows = market.loc[invalid_qfq, ["raw_code", "trade_date"]].head(3)
            examples = ", ".join(
                f"{row.raw_code}@{row.trade_date}"
                for row in bad_rows.itertuples(index=False)
            )
            raise ValueError(f"行情包含无效 qfq_ratio: {examples}")
        market["qfq_ratio"] = qfq_ratios.astype("float64")
        for column in ("raw_open", "raw_prev_close"):
            values = pd.to_numeric(market[column], errors="coerce")
            invalid = ~values.map(_finite_positive)
            if bool(invalid.any()):
                bad_rows = market.loc[invalid, ["raw_code", "trade_date"]].head(3)
                examples = ", ".join(
                    f"{row.raw_code}@{row.trade_date}"
                    for row in bad_rows.itertuples(index=False)
                )
                raise ValueError(f"行情包含无效 {column}: {examples}")
            market[column] = values.astype("float64")
        for column in ("is_st", "is_delisting"):
            if bool(market[column].isna().any()):
                raise ValueError(f"行情包含无效 {column}")
            market[column] = market[column].astype("bool")
    market["trade_date"] = pd.to_datetime(market["trade_date"]).dt.normalize()
    market = market.drop_duplicates(subset=["trade_date", "raw_code"], keep="last")
    return market


def _load_market_calendar(
    parquet_path: str,
    raw_codes: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    try:
        duckdb = importlib.import_module("duckdb")
    except Exception as exc:  # pragma: no cover - 取决于本机是否安装
        raise RuntimeError("读取本地 Parquet 需要安装 duckdb") from exc

    escaped_path = parquet_path.replace("\\", "/").replace("'", "''")
    placeholders = ", ".join("?" for _ in raw_codes)
    query = f"""
        WITH source AS (
            SELECT
                CASE
                    WHEN regexp_matches(lower(CAST(code AS VARCHAR)), '^sh[0-9]{{6}}$')
                        THEN lower(CAST(code AS VARCHAR))
                    WHEN regexp_matches(lower(CAST(code AS VARCHAR)), '^sz[0-9]{{6}}$')
                        THEN lower(CAST(code AS VARCHAR))
                    WHEN regexp_matches(upper(CAST(code AS VARCHAR)), '^[0-9]{{6}}\\.(SH|SSE)$')
                        THEN 'sh' || substr(CAST(code AS VARCHAR), 1, 6)
                    WHEN regexp_matches(upper(CAST(code AS VARCHAR)), '^[0-9]{{6}}\\.(SZ|SZSE)$')
                        THEN 'sz' || substr(CAST(code AS VARCHAR), 1, 6)
                    ELSE lower(CAST(code AS VARCHAR))
                END AS raw_code,
                CAST(date AS DATE) AS trade_date
            FROM read_parquet('{escaped_path}', union_by_name=true)
        )
        SELECT DISTINCT trade_date
        FROM source
        WHERE raw_code IN ({placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY trade_date
    """
    params: list[object] = [*raw_codes, start.date(), end.date()]
    connection = duckdb.connect(":memory:")
    try:
        calendar = connection.execute(query, params).fetchdf()
    finally:
        connection.close()

    if calendar.empty:
        raise ValueError("所选股票和日期范围没有行情")
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["trade_date"]).dt.normalize())
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    dates = dates.normalize()
    if hasattr(dates, "as_unit"):
        dates = dates.as_unit("ns")
    return pd.DatetimeIndex(dates, name="trade_date")


def load_market_dates(
    parquet_path: str | os.PathLike[str] | None,
    codes: list[object] | tuple[object, ...] | set[object],
    start: object,
    end: object,
) -> pd.DatetimeIndex:
    """读取指定股票和时期内真实存在行情的交易日期。"""

    resolved_start = _as_date(start, "start")
    resolved_end = _as_date(end, "end")
    if resolved_end < resolved_start:
        raise ValueError("end 不能早于 start")

    if isinstance(codes, (str, bytes)):
        code_values: list[object] = [codes]
    else:
        code_values = list(codes)
    if not code_values:
        raise ValueError("codes 至少要有一个股票代码")

    raw_codes = list(dict.fromkeys(normalize_stock_code(code) for code in code_values))
    resolved_path = _resolve_parquet_path(parquet_path)
    return _load_market_calendar(
        resolved_path,
        raw_codes,
        resolved_start,
        resolved_end,
    )


def _finite_positive(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _bar_can_trade(bar: pd.Series) -> bool:
    _require_qfq_ratio(bar)
    _raw_price_to_cent(bar, "raw_open")
    _raw_price_to_cent(bar, "raw_prev_close")
    _require_bool_field(bar, "is_st")
    _require_bool_field(bar, "is_delisting")
    return all(
        _finite_positive(bar.get(column))
        for column in ("open", "close", "volume")
    )


def _bar_can_trade_at_open(bar: pd.Series) -> bool:
    """Validate open-time fields only; a final daily volume is not known at open."""
    ratio = _require_qfq_ratio(bar)
    _require_bool_field(bar, "is_st")
    _require_bool_field(bar, "is_delisting")
    if not _finite_positive(bar.get("open")) or not _finite_positive(bar.get("raw_open")):
        return False
    _raw_price_to_cent(bar, "raw_open")
    _raw_price_to_cent(bar, "raw_prev_close")
    if not math.isclose(
        float(bar["open"]), float(bar["raw_open"]) * ratio,
        rel_tol=1e-6, abs_tol=1e-6,
    ):
        raise ValueError("open 与 raw_open * qfq_ratio 不一致，无法换算真实股数")
    return True


def _round_raw_lot_units(units: float, ratio: float, lot_size: int) -> float:
    """Position accounting uses adjusted units; exchange lots use raw shares."""
    lots = math.floor(max(0.0, units * ratio) / lot_size + 1e-10)
    return lots * lot_size / ratio


def _raw_share_execution_evidence(
    trades: list[dict[str, Any]], raw_share_lots: bool,
) -> dict[str, Any]:
    """Describe the existing approximation without altering orders or returns."""
    tolerance = 1e-6
    available = raw_share_lots and all("raw_shares" in trade for trade in trades)
    distances = [abs(float(row["raw_shares"]) - round(float(row["raw_shares"])))
                 for row in trades] if available else []
    anomalous = [(row, distance) for row, distance in zip(trades, distances)
                 if distance > tolerance]
    return {
        "accounting_mode": "adjusted_price_units_approximation",
        "raw_share_inventory_cash_ledger_valid": False,
        "validation_reason": (
            "Raw-share inventory and corporate-action cash flows are not separately booked. "
            "Zero observed fractional orders does not validate the approximation."
        ),
        "measurement_available": available,
        "observed_orders": len(trades) if available else 0,
        "integer_tolerance_shares": tolerance,
        "non_integer_order_count": len(anomalous) if available else None,
        "non_integer_buy_order_count": sum(row["side"] == "buy" for row, _ in anomalous) if available else None,
        "non_integer_sell_order_count": sum(row["side"] == "sell" for row, _ in anomalous) if available else None,
        "max_distance_to_integer_shares": max(distances, default=0.0) if available else None,
        "total_distance_to_integer_shares": sum(distances) if available else None,
        "fractional_order_examples": [
            {"date": str(row["date"]), "code": row["code"], "side": row["side"],
             "raw_shares": float(row["raw_shares"]), "distance_to_integer_shares": distance}
            for row, distance in anomalous[:5]
        ],
        "scope": "Order-field arithmetic only; no return adjustment, rounding, deletion or external execution verification.",
    }


def _require_qfq_ratio(bar: pd.Series) -> float:
    value = bar.get("qfq_ratio")
    if not _finite_positive(value):
        code = str(bar.get("raw_code", "unknown"))
        date = str(bar.get("trade_date", "unknown"))
        raise ValueError(f"{code}@{date} 缺少有效 qfq_ratio，不能判断涨跌停")
    return float(value)


def _require_bool_field(bar: pd.Series, field: str) -> bool:
    value = bar.get(field)
    if pd.isna(value):
        raise ValueError(f"缺少有效 {field}，不能判断涨跌停或退市整理状态")
    if value in (True, 1):
        return True
    if value in (False, 0):
        return False
    raise ValueError(f"{field} 必须是布尔值")


def _daily_limit_ratio(
    raw_code: str,
    trade_date: object | None = None,
    *,
    is_st: bool = False,
) -> float:
    code = normalize_stock_code(raw_code)
    if code.startswith(("sh688", "sh689", "sz301")):
        return 0.20
    if code.startswith(("sz300", "sz302")):
        if trade_date is None:
            raise ValueError("sz300/sz302股票需要交易日期才能判断涨跌幅限制")
        date = _as_date(trade_date, "trade_date")
        if date >= CHINEXT_TWENTY_PERCENT_START:
            return 0.20
        return 0.05 if is_st else 0.10
    return 0.05 if is_st else 0.10


def _is_limit_up(bar: pd.Series, raw_code: str) -> bool:
    if _delisting_outside_ordinary_limits(bar, raw_code):
        return False
    raw_open_price = _raw_price_to_cent(bar, "raw_open")
    raw_limit_price = _raw_limit_price(bar, raw_code, direction=1)
    return raw_open_price >= raw_limit_price


def _is_limit_down(bar: pd.Series, raw_code: str) -> bool:
    if _delisting_outside_ordinary_limits(bar, raw_code):
        return False
    raw_open_price = _raw_price_to_cent(bar, "raw_open")
    raw_limit_price = _raw_limit_price(bar, raw_code, direction=-1)
    return raw_open_price <= raw_limit_price


def _delisting_outside_ordinary_limits(
    bar: pd.Series,
    raw_code: str,
) -> bool:
    if not _require_bool_field(bar, "is_delisting"):
        return False
    raw_open_price = _raw_price_to_cent(bar, "raw_open")
    raw_limit_down = _raw_limit_price(bar, raw_code, direction=-1)
    raw_limit_up = _raw_limit_price(bar, raw_code, direction=1)
    return raw_open_price < raw_limit_down or raw_open_price > raw_limit_up


def _limit_up_price(bar: pd.Series, raw_code: str) -> float:
    return _adjusted_limit_price(bar, raw_code, direction=1)


def _limit_down_price(bar: pd.Series, raw_code: str) -> float:
    return _adjusted_limit_price(bar, raw_code, direction=-1)


def _adjusted_limit_price(
    bar: pd.Series,
    raw_code: str,
    *,
    direction: int,
) -> float:
    raw_limit_price = _raw_limit_price(bar, raw_code, direction=direction)
    qfq_ratio = _require_qfq_ratio(bar)
    return float(raw_limit_price * Decimal(str(qfq_ratio)))


def _raw_price_to_cent(bar: pd.Series, field: str) -> Decimal:
    raw_price = bar.get(field)
    if not _finite_positive(raw_price):
        raise ValueError(f"缺少有效 {field}，不能判断涨跌停")
    return Decimal(str(float(raw_price))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _raw_limit_price(
    bar: pd.Series,
    raw_code: str,
    *,
    direction: int,
) -> Decimal:
    raw_previous_close = _raw_price_to_cent(bar, "raw_prev_close")
    limit_ratio = Decimal(
        str(
            _daily_limit_ratio(
                raw_code,
                bar.get("trade_date"),
                is_st=_require_bool_field(bar, "is_st"),
            )
        )
    )
    multiplier = Decimal("1") + Decimal(direction) * limit_ratio
    return (raw_previous_close * multiplier).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _resolve_sell_cost_rule(
    sell_cost: float,
    sell_cost_before_change: float | None,
    sell_cost_change_date: object | None,
) -> tuple[float | None, pd.Timestamp | None, dict[str, object]]:
    has_old_rate = sell_cost_before_change is not None
    has_change_date = sell_cost_change_date is not None
    if has_old_rate != has_change_date:
        raise ValueError(
            "sell_cost_before_change 和 sell_cost_change_date 必须同时提供"
        )
    if not has_old_rate:
        return None, None, {
            "type": "constant",
            "sell_rate": float(sell_cost),
            "description": f"卖出费固定为 {float(sell_cost):.6f}",
        }

    old_rate = float(sell_cost_before_change)
    if not 0 <= old_rate < 1:
        raise ValueError("sell_cost_before_change 必须在 0 到 1 之间")
    change_date = _as_date(sell_cost_change_date, "sell_cost_change_date")
    previous_day = change_date - pd.Timedelta(days=1)
    return old_rate, change_date, {
        "type": "dated",
        "sell_rate_before_change": old_rate,
        "sell_rate_before_end": previous_day.strftime("%Y-%m-%d"),
        "sell_rate_from_change": float(sell_cost),
        "sell_rate_change_date": change_date.strftime("%Y-%m-%d"),
        "description": (
            f"{previous_day:%Y-%m-%d}及以前卖出费 {old_rate:.6f}；"
            f"{change_date:%Y-%m-%d}起卖出费 {float(sell_cost):.6f}"
        ),
    }


def _prepare_membership_intervals(
    membership_df: pd.DataFrame | None,
) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    membership_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    if membership_df is None:
        return membership_intervals

    required_columns = {"code", "start_date", "end_date"}
    if not isinstance(membership_df, pd.DataFrame) or membership_df.empty:
        raise ValueError("历史指数成分表为空")
    if not required_columns.issubset(set(membership_df.columns)):
        raise ValueError("历史指数成分表必须包含 code/start_date/end_date")
    membership = membership_df.loc[:, ["code", "start_date", "end_date"]].copy()
    membership["raw_code"] = membership["code"].map(normalize_stock_code)
    membership["start_date"] = pd.to_datetime(
        membership["start_date"], errors="coerce"
    ).dt.normalize()
    membership["end_date"] = pd.to_datetime(
        membership["end_date"], errors="coerce"
    ).dt.normalize()
    membership = membership.dropna(subset=["raw_code", "start_date", "end_date"])
    for row in membership.itertuples(index=False):
        membership_intervals.setdefault(str(row.raw_code), []).append(
            (pd.Timestamp(row.start_date), pd.Timestamp(row.end_date))
        )
    return membership_intervals


def _is_historical_member(
    raw_code: str,
    date: pd.Timestamp,
    membership_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
) -> bool:
    if not membership_intervals:
        return True
    return any(
        start <= date <= end
        for start, end in membership_intervals.get(raw_code, [])
    )


def _build_execution_schedule(
    weights: pd.DataFrame,
    market_dates: pd.DatetimeIndex,
    membership_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] | None = None,
) -> dict[pd.Timestamp, pd.Series]:
    """把决定日目标移到下一市场日，并只拦截不合格的新建或增加目标。"""

    active_intervals = membership_intervals or {}
    schedule: dict[pd.Timestamp, pd.Series] = {}
    accepted_target = pd.Series(0.0, index=weights.columns, dtype="float64")
    for signal_date, target in weights.iterrows():
        position = int(market_dates.searchsorted(pd.Timestamp(signal_date), side="right"))
        if position < len(market_dates):
            execution_date = pd.Timestamp(market_dates[position])
            execution_target = target.copy()
            if active_intervals:
                for code in execution_target.index:
                    requested_weight = float(execution_target.loc[code])
                    previous_weight = float(accepted_target.loc[code])
                    if requested_weight <= previous_weight + 1e-12:
                        accepted_target.loc[code] = requested_weight
                        continue
                    valid_on_decision_date = _is_historical_member(
                        str(code), pd.Timestamp(signal_date), active_intervals
                    )
                    valid_on_execution_date = _is_historical_member(
                        str(code), execution_date, active_intervals
                    )
                    if not (valid_on_decision_date and valid_on_execution_date):
                        execution_target.loc[code] = previous_weight
                    else:
                        accepted_target.loc[code] = requested_weight
            else:
                accepted_target = execution_target.copy()
            schedule[execution_date] = execution_target
    return schedule


def _active_weight_codes_for_period(
    weights: pd.DataFrame,
    market_dates: pd.DatetimeIndex,
) -> list[str]:
    """找出测试期内真正会送入执行日的非零股票列。"""

    execution_rows: dict[pd.Timestamp, int] = {}
    for row_number, signal_date in enumerate(weights.index):
        position = int(
            market_dates.searchsorted(pd.Timestamp(signal_date), side="right")
        )
        if position < len(market_dates):
            execution_rows[pd.Timestamp(market_dates[position])] = row_number
    if not execution_rows:
        return []

    scheduled_weights = weights.iloc[list(execution_rows.values())]
    active_mask = (scheduled_weights > 0.0).any(axis=0)
    return [str(code) for code in weights.columns if bool(active_mask.loc[code])]


def _prepare_market_bundle(
    weights: pd.DataFrame,
    resolved_path: str,
    resolved_start: pd.Timestamp,
    resolved_end: pd.Timestamp,
) -> dict[str, Any]:
    all_raw_codes = [str(column) for column in weights.columns]
    market_dates = _load_market_calendar(
        resolved_path,
        all_raw_codes,
        resolved_start,
        resolved_end,
    )
    active_raw_codes = _active_weight_codes_for_period(weights, market_dates)
    # 全部为零时仍按旧方式读取所有股票，以保留纯现金策略的日期行为。
    raw_codes = active_raw_codes or all_raw_codes
    market = _load_market_data(
        resolved_path,
        raw_codes,
        resolved_start,
        resolved_end,
        allow_empty=bool(active_raw_codes),
    )
    bars_by_date = {
        pd.Timestamp(trade_date): frame.set_index("raw_code", drop=False)
        for trade_date, frame in market.groupby("trade_date", sort=True)
    }
    return {
        "start": resolved_start,
        "end": resolved_end,
        "calendar_codes": tuple(all_raw_codes),
        "raw_codes": tuple(raw_codes),
        "market_dates": market_dates,
        "bars_by_date": bars_by_date,
    }


def prepare_target_weight_backtest_market(
    output_weights_df: pd.DataFrame,
    parquet_path: str | os.PathLike[str] | None = None,
    start: object | None = None,
    end: object | None = None,
    datetime_column: str | None = None,
) -> dict[str, Any]:
    """读取一次本折所需行情，供普通、费用和延迟测试共同使用。"""

    weights, _ = _prepare_weights(output_weights_df, datetime_column)
    resolved_start, resolved_end = _resolve_backtest_dates(weights, start, end)
    resolved_path = _resolve_parquet_path(parquet_path)
    return _prepare_market_bundle(
        weights,
        resolved_path,
        resolved_start,
        resolved_end,
    )


def run_target_weight_backtest(
    output_weights_df: pd.DataFrame,
    parquet_path: str | os.PathLike[str] | None = None,
    start: object | None = None,
    end: object | None = None,
    capital: float = 1_000_000.0,
    buy_cost: float = 0.0003,
    sell_cost: float = 0.0013,
    slippage: float = 0.0,
    lot_size: int = 100,
    membership_df: pd.DataFrame | None = None,
    risk_free_rate: float = 0.02,
    datetime_column: str | None = None,
    prepared_market: dict[str, Any] | None = None,
    sell_cost_before_change: float | None = None,
    sell_cost_change_date: object | None = None,
    raw_share_lots: bool = False,
    min_commission: float = 0.0,
) -> dict[str, Any]:
    """用本地日频行情按目标权重回测，信号在下一交易日开盘执行。"""

    if not math.isfinite(float(capital)) or float(capital) <= 0:
        raise ValueError("capital 必须大于 0")
    if not 0 <= float(buy_cost) < 1 or not 0 <= float(sell_cost) < 1:
        raise ValueError("交易费率必须在 0 到 1 之间")
    (
        resolved_sell_cost_before_change,
        resolved_sell_cost_change_date,
        transaction_cost_rule,
    ) = _resolve_sell_cost_rule(
        float(sell_cost),
        sell_cost_before_change,
        sell_cost_change_date,
    )
    transaction_cost_rule["buy_rate"] = float(buy_cost)
    transaction_cost_rule["slippage_rate_each_side"] = float(slippage)
    if not 0 <= float(slippage) < 1:
        raise ValueError("成交价偏差必须在 0 到 1 之间")
    if int(lot_size) < 0:
        raise ValueError("lot_size 不能小于 0")
    if type(raw_share_lots) is not bool:
        raise ValueError("raw_share_lots 必须是布尔值")
    if not math.isfinite(float(min_commission)) or float(min_commission) < 0:
        raise ValueError("min_commission 必须是非负有限数")
    if raw_share_lots and (int(lot_size) <= 0 or int(lot_size) != lot_size):
        raise ValueError("真实股数模式要求 lot_size 为正整数")
    # The common buy rate represents the commission component; any excess sell
    # rate is the dated sell-side levy, which must not disappear under the minimum.
    if float(min_commission) > 0 and any(
        rate is not None and float(rate) < float(buy_cost)
        for rate in (sell_cost, resolved_sell_cost_before_change)
    ):
        raise ValueError("最低佣金模式要求卖出总费率不低于共同买入佣金费率")

    def trade_fee(turnover: float, rate: float) -> float:
        if float(min_commission) == 0:
            return turnover * rate
        return max(turnover * float(buy_cost), float(min_commission)) + turnover * (
            rate - float(buy_cost)
        )

    can_trade = _bar_can_trade_at_open if raw_share_lots else _bar_can_trade
    if raw_share_lots or float(min_commission) > 0:
        transaction_cost_rule["minimum_commission"] = float(min_commission)
        transaction_cost_rule["minimum_commission_basis"] = (
            "common buy_cost component; sell_cost minus buy_cost remains an additional levy"
        )

    weights, _ = _prepare_weights(output_weights_df, datetime_column)
    resolved_start, resolved_end = _resolve_backtest_dates(weights, start, end)
    if prepared_market is None:
        resolved_path = _resolve_parquet_path(parquet_path)
        market_bundle = _prepare_market_bundle(
            weights,
            resolved_path,
            resolved_start,
            resolved_end,
        )
    else:
        market_bundle = prepared_market
        if (
            market_bundle.get("start") != resolved_start
            or market_bundle.get("end") != resolved_end
            or tuple(weights.columns) != tuple(market_bundle.get("calendar_codes", ()))
        ):
            raise ValueError("预读行情与本次回测的日期或股票列不一致")

    market_dates = pd.DatetimeIndex(market_bundle["market_dates"])
    raw_codes = [str(code) for code in market_bundle["raw_codes"]]
    required_codes = set(_active_weight_codes_for_period(weights, market_dates))
    if not required_codes.issubset(set(raw_codes)):
        raise ValueError("预读行情缺少本次回测的非零权重股票")
    weights = weights.loc[:, raw_codes]
    membership_intervals = _prepare_membership_intervals(membership_df)
    schedule = _build_execution_schedule(weights, market_dates, membership_intervals)
    bars_by_date = market_bundle["bars_by_date"]
    empty_day_bars = pd.DataFrame(
        columns=(
            "raw_code",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "qfq_ratio",
            "prev_close",
            "raw_open",
            "raw_prev_close",
            "is_st",
            "is_delisting",
        )
    ).set_index("raw_code", drop=False)

    cash = float(capital)
    positions = {code: 0.0 for code in raw_codes}
    average_cost = {code: 0.0 for code in raw_codes}
    last_close: dict[str, float] = {}
    daily_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    realized_pnls: list[float] = []
    previous_balance = float(capital)
    blocked_membership_buy_count = 0

    for trade_date in market_dates:
        date = pd.Timestamp(trade_date)
        day_bars = bars_by_date.get(date, empty_day_bars)
        day_commission = 0.0
        day_slippage = 0.0
        day_turnover = 0.0
        day_trade_count = 0

        target = schedule.get(date)
        if target is not None:
            open_value = cash
            for code, shares in positions.items():
                if shares <= 0:
                    continue
                if code in day_bars.index and _finite_positive(day_bars.loc[code].get("open")):
                    value_price = float(day_bars.loc[code]["open"])
                else:
                    value_price = last_close.get(code, 0.0)
                open_value += shares * value_price

            target_shares: dict[str, float] = {}
            for code in raw_codes:
                bar = day_bars.loc[code] if code in day_bars.index else None
                if bar is None or not can_trade(bar):
                    target_shares[code] = positions[code]
                    continue
                target_value = open_value * float(target.get(code, 0.0))
                raw_target_shares = max(0.0, target_value / float(bar["open"]))
                if raw_share_lots:
                    # Round each order increment below, not total adjusted units.
                    target_shares[code] = raw_target_shares
                elif int(lot_size) > 0:
                    target_shares[code] = float(
                        int(raw_target_shares // int(lot_size)) * int(lot_size)
                    )
                else:
                    target_shares[code] = raw_target_shares

            # 先卖出，卖出的现金可用于同一天随后买入。
            for code in raw_codes:
                current_shares = positions[code]
                desired_shares = target_shares[code]
                if current_shares <= desired_shares or code not in day_bars.index:
                    continue
                bar = day_bars.loc[code]
                if not can_trade(bar) or _is_limit_down(bar, code):
                    continue
                quantity = current_shares - desired_shares
                if raw_share_lots and desired_shares > 1e-10:
                    quantity = _round_raw_lot_units(
                        quantity, _require_qfq_ratio(bar), int(lot_size)
                    )
                # Complete exits may sell the residual odd lot created by an
                # adjustment; partial reductions conservatively use whole lots.
                if raw_share_lots and quantity <= 1e-10:
                    continue
                reference_price = float(bar["open"])
                slipped_price = reference_price * (1.0 - float(slippage))
                if _delisting_outside_ordinary_limits(bar, code):
                    price = slipped_price
                else:
                    price = max(
                        slipped_price,
                        _limit_down_price(bar, code),
                    )
                slippage_cost = quantity * (reference_price - price)
                turnover = quantity * price
                active_sell_cost = (
                    float(resolved_sell_cost_before_change)
                    if resolved_sell_cost_change_date is not None
                    and date < resolved_sell_cost_change_date
                    else float(sell_cost)
                )
                commission = trade_fee(turnover, active_sell_cost)
                if cash + turnover < commission:
                    # Never fund a commission with borrowing; retain inventory.
                    continue
                cash += turnover - commission
                realized_pnl = turnover - commission - quantity * average_cost[code]
                realized_pnls.append(realized_pnl)
                positions[code] -= quantity
                if positions[code] == 0:
                    average_cost[code] = 0.0
                day_commission += commission
                day_slippage += slippage_cost
                day_turnover += turnover
                day_trade_count += 1
                trade_rows.append(
                    {
                        "date": date,
                        "code": code,
                        "side": "sell",
                        "reason": "rebalance",
                        "reference_price": reference_price,
                        "price": price,
                        "shares": quantity,
                        **({"raw_shares": quantity * _require_qfq_ratio(bar)} if raw_share_lots else {}),
                        "turnover": turnover,
                        "commission_rate": active_sell_cost,
                        "commission": commission,
                        "slippage": slippage_cost,
                        "realized_pnl": realized_pnl,
                    }
                )

            for code in raw_codes:
                current_shares = positions[code]
                desired_shares = target_shares[code]
                if current_shares >= desired_shares or code not in day_bars.index:
                    continue
                if membership_intervals and not _is_historical_member(
                    code, date, membership_intervals
                ):
                    blocked_membership_buy_count += 1
                    continue
                bar = day_bars.loc[code]
                if (
                    not can_trade(bar)
                    or _require_bool_field(bar, "is_delisting")
                    or _is_limit_up(bar, code)
                ):
                    continue
                reference_price = float(bar["open"])
                price = min(
                    reference_price * (1.0 + float(slippage)),
                    _limit_up_price(bar, code),
                )
                wanted = desired_shares - current_shares
                raw_affordable = max(0.0, cash / (price * (1.0 + float(buy_cost))))
                if float(min_commission) > 0:
                    raw_affordable = min(
                        raw_affordable, max(0.0, (cash - float(min_commission)) / price)
                    )
                if raw_share_lots:
                    ratio = _require_qfq_ratio(bar)
                    wanted = _round_raw_lot_units(wanted, ratio, int(lot_size))
                    affordable = _round_raw_lot_units(raw_affordable, ratio, int(lot_size))
                elif int(lot_size) > 0:
                    affordable = float(
                        int(raw_affordable // int(lot_size)) * int(lot_size)
                    )
                else:
                    affordable = raw_affordable
                quantity = min(wanted, max(0, affordable))
                if quantity <= 0:
                    continue
                turnover = quantity * price
                slippage_cost = quantity * (price - reference_price)
                commission = trade_fee(turnover, float(buy_cost))
                total_cost = turnover + commission
                if (raw_share_lots or float(min_commission) > 0) and total_cost > cash + 1e-8:
                    raise ValueError("买入费用超过可用现金，拒绝透支")
                previous_position_cost = current_shares * average_cost[code]
                cash -= total_cost
                if (raw_share_lots or float(min_commission) > 0) and -1e-8 < cash < 0:
                    cash = 0.0
                positions[code] += quantity
                average_cost[code] = (previous_position_cost + total_cost) / positions[code]
                day_commission += commission
                day_slippage += slippage_cost
                day_turnover += turnover
                day_trade_count += 1
                trade_rows.append(
                    {
                        "date": date,
                        "code": code,
                        "side": "buy",
                        "reason": "rebalance",
                        "reference_price": reference_price,
                        "price": price,
                        "shares": quantity,
                        **({"raw_shares": quantity * _require_qfq_ratio(bar)} if raw_share_lots else {}),
                        "turnover": turnover,
                        "commission_rate": float(buy_cost),
                        "commission": commission,
                        "slippage": slippage_cost,
                        "realized_pnl": None,
                    }
                )

        for code in raw_codes:
            if code not in day_bars.index:
                continue
            close_price = day_bars.loc[code].get("close")
            if _finite_positive(close_price):
                last_close[code] = float(close_price)

        position_value = sum(
            shares * last_close.get(code, 0.0)
            for code, shares in positions.items()
        )
        balance = cash + position_value
        net_pnl = balance - previous_balance
        daily_return = net_pnl / previous_balance if previous_balance > 0 else 0.0
        daily_rows.append(
            {
                "date": date,
                "balance": balance,
                "net_pnl": net_pnl,
                "return": daily_return,
                "turnover": day_turnover,
                "commission": day_commission,
                "slippage": day_slippage,
                "cash": cash,
                "position_value": position_value,
                "trade_count": day_trade_count,
            }
        )
        previous_balance = balance

    daily_df = pd.DataFrame(daily_rows)
    equity = daily_df["balance"].astype(float)
    high_watermark = equity.cummax()
    if raw_share_lots:
        high_watermark = high_watermark.clip(lower=float(capital))
    drawdown = equity - high_watermark
    drawdown_ratio = drawdown / high_watermark.where(high_watermark > 0)
    daily_df["drawdown"] = drawdown

    total_return = float(equity.iloc[-1] / float(capital) - 1.0)
    periods = max(1, len(daily_df) - 1)
    annual_return = float((equity.iloc[-1] / float(capital)) ** (252.0 / periods) - 1.0)
    daily_returns = daily_df["return"].astype(float)
    annual_volatility = float(daily_returns.std(ddof=1) * math.sqrt(252.0)) if len(daily_returns) > 1 else 0.0
    daily_risk_free_rate = (1.0 + float(risk_free_rate)) ** (1.0 / 252.0) - 1.0
    sharpe_ratio = (
        float(
            (daily_returns.mean() - daily_risk_free_rate)
            / daily_returns.std(ddof=1)
            * math.sqrt(252.0)
        )
        if len(daily_returns) > 1 and float(daily_returns.std(ddof=1)) > 0
        else 0.0
    )

    winning_pnls = [value for value in realized_pnls if value > 0]
    losing_pnls = [value for value in realized_pnls if value < 0]
    win_rate = len(winning_pnls) / len(realized_pnls) if realized_pnls else 0.0
    profit_loss_ratio = None
    if winning_pnls and losing_pnls:
        profit_loss_ratio = (
            sum(winning_pnls) / len(winning_pnls)
        ) / abs(sum(losing_pnls) / len(losing_pnls))
    raw_share_evidence = _raw_share_execution_evidence(trade_rows, raw_share_lots)

    return {
        "capital": float(capital),
        "end_balance": float(equity.iloc[-1]),
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": float(drawdown.min()),
        "max_ddpercent": float(drawdown_ratio.min()),
        "total_trade_count": len(trade_rows),
        "total_commission": float(daily_df["commission"].sum()),
        "total_slippage": float(daily_df["slippage"].sum()),
        "win_rate": win_rate,
        "profit_loss_ratio": profit_loss_ratio,
        "raw_share_inventory_cash_ledger_valid": False,
        "raw_share_execution_evidence": raw_share_evidence,
        "_backtest_debug": {
            "execution": "target weights execute at the next trading day open",
            "lot_size": int(lot_size),
            "position_mode": "fractional adjusted-price units" if int(lot_size) == 0 else "board lots",
            "raw_share_lots": raw_share_lots,
            "raw_share_inventory_cash_ledger_valid": False,
            "raw_share_execution_evidence": raw_share_evidence,
            "minimum_commission": float(min_commission),
            "raw_share_conversion": (
                "raw shares = adjusted units * current qfq_ratio; buy increments and partial "
                "sales use raw board lots; full exits may include residual odd lots"
                if raw_share_lots else None
            ),
            "corporate_action_accounting": (
                "adjusted-price approximation; cash dividends and share distributions are not "
                "separately booked; implied residual raw shares may be fractional"
                if raw_share_lots else "legacy adjusted-price units"
            ),
            "open_availability_fields": (
                "open/raw_open/raw_prev_close/qfq_ratio/status only; no same-day close or volume"
                if raw_share_lots else "legacy open/close/volume"
            ),
            "capacity_verified": False,
            "slippage_rate_each_side": float(slippage),
            "buy_cost_rate": float(buy_cost),
            "sell_cost_rate": float(sell_cost),
            "sell_cost_before_change": resolved_sell_cost_before_change,
            "sell_cost_change_date": (
                resolved_sell_cost_change_date.strftime("%Y-%m-%d")
                if resolved_sell_cost_change_date is not None
                else None
            ),
            "transaction_cost_rule": transaction_cost_rule,
            "historical_membership_filter": membership_df is not None,
            "forced_membership_exit_count": 0,
            "blocked_membership_buy_count": blocked_membership_buy_count,
            "price_limit_rule": (
                "sz300/sz302: 10% before 2020-08-24 and 20% from that date; "
                "sz300/sz302 ST: 5% before 2020-08-24 and 20% from that date; "
                "sz301/STAR 20% including ST; main-board ST 5%; other stocks 10%; "
                "raw_open and official raw_prev_close are rounded to CNY 0.01 "
                "with ROUND_HALF_UP; qfq_ratio is used only to map the raw limit "
                "price back to the adjusted execution-price scale"
            ),
            "delisting_entry_rule": (
                "is_delisting blocks new buys and position increases; existing "
                "positions may sell; when official raw_open is strictly outside "
                "the ordinary board/ST limits, ordinary limit blocking and the "
                "adjusted limit-price floor are not applied; equality remains blocked"
            ),
        },
        "_daily_df": daily_df,
        "_trades_df": pd.DataFrame(trade_rows),
    }
