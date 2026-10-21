from __future__ import annotations

import glob
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TIME_COLUMN_CANDIDATES = (
    "trigger_ts",
    "datetime",
    "timestamp",
    "signal_time",
    "date",
    "trade_date",
)
CASH_COLUMN_NAMES = {"cash", "cash_weight"}
SUPPORTED_HORIZONS = {5, 10, 20}
EVALUATION_COLUMNS = (
    "volume_hit",
    "next_up",
    "joint_minute_hit",
    "target_buy_up_60",
    "target_buy_up_70",
    "target_buy_up_75",
)


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    proportion = float(successes) / float(total)
    z_squared = float(z) ** 2
    denominator = 1.0 + z_squared / total
    centre = (proportion + z_squared / (2.0 * total)) / denominator
    radius = (
        float(z)
        * math.sqrt(
            proportion * (1.0 - proportion) / total
            + z_squared / (4.0 * total * total)
        )
        / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def normalize_stock_code(value: object) -> str:
    """把常见股票代码转成 sh600000 或 sz000001。"""

    text = str(value).strip().upper()
    if not text:
        raise ValueError("股票代码不能为空")

    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text.lower()

    if "." in text:
        code, exchange = text.rsplit(".", maxsplit=1)
        if len(code) == 6 and code.isdigit():
            if exchange in {"SH", "SSE"}:
                return f"sh{code}"
            if exchange in {"SZ", "SZSE"}:
                return f"sz{code}"
            if exchange in {"BJ", "BSE"}:
                return f"bj{code}"

    if len(text) == 6 and text.isdigit():
        if text[0] in {"4", "8"} or text.startswith("92"):
            exchange = "bj"
        else:
            exchange = "sh" if text[0] in {"5", "6", "9"} else "sz"
        return f"{exchange}{text}"

    raise ValueError(f"无法识别股票代码: {value}")


def _as_date(value: object, name: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} 不是有效日期: {value}")
    timestamp = pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _as_naive_datetime_index(values: object, name: str) -> pd.DatetimeIndex:
    parsed = pd.to_datetime(values, errors="coerce")
    try:
        timestamps = pd.DatetimeIndex(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 含有无法识别的时间") from exc
    if bool(timestamps.isna().any()):
        raise ValueError(f"{name} 含有无法识别的时间")
    if timestamps.tz is not None:
        timestamps = timestamps.tz_localize(None)
    return timestamps


def _resolve_event_paths(event_path: str | os.PathLike[str]) -> list[str]:
    raw_path = str(event_path).strip()
    if not raw_path:
        raise ValueError("必须提供事件 Parquet 路径")

    expanded = Path(os.path.expandvars(raw_path)).expanduser()
    if not expanded.is_absolute():
        expanded = PROJECT_ROOT / expanded
    if expanded.is_dir():
        expanded = expanded / "*.parquet"

    matches = sorted(glob.iglob(str(expanded)))
    if not matches:
        raise FileNotFoundError(f"没有找到事件 Parquet 文件: {expanded}")
    return matches


def _load_events(
    paths: list[str],
    horizon_minutes: int,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    import pyarrow as pa
    import pyarrow.parquet as pq

    preferred_return = f"forward_{horizon_minutes}m"
    compatible_return = f"return_{horizon_minutes}m"
    requested_columns = {
        "code",
        "trigger_ts",
        "trigger_position",
        "candidate_id",
        preferred_return,
        compatible_return,
        *EVALUATION_COLUMNS,
    }
    frames: list[pd.DataFrame] = []
    for path in paths:
        schema = pq.read_schema(path)
        available_columns = set(schema.names)
        read_columns = sorted(requested_columns & available_columns)
        filters = None
        if "trigger_ts" in available_columns:
            trigger_type = schema.field("trigger_ts").type
            end_exclusive = end_date + pd.Timedelta(days=1)
            if pa.types.is_timestamp(trigger_type) or pa.types.is_date(trigger_type):
                lower: object = start_date.to_pydatetime()
                upper: object = end_exclusive.to_pydatetime()
            else:
                lower = start_date.strftime("%Y-%m-%d 00:00:00")
                upper = end_exclusive.strftime("%Y-%m-%d 00:00:00")
            filters = [
                ("trigger_ts", ">=", lower),
                ("trigger_ts", "<", upper),
            ]
        frames.append(
            pd.read_parquet(
                path,
                columns=read_columns,
                filters=filters,
            )
        )
    events = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0].copy()

    required_columns = {"code", "trigger_ts", "trigger_position"}
    missing = sorted(required_columns - set(events.columns))
    if missing:
        raise ValueError(f"事件表缺少字段: {', '.join(missing)}")

    if preferred_return not in events.columns and compatible_return not in events.columns:
        raise ValueError(
            f"事件表缺少收益字段: {preferred_return} 或 {compatible_return}"
        )

    selected_columns = ["code", "trigger_ts", "trigger_position"]
    if "candidate_id" in events.columns:
        selected_columns.append("candidate_id")
    selected_columns.extend(
        column for column in EVALUATION_COLUMNS if column in events.columns
    )
    selected = events.loc[:, selected_columns].copy()

    if preferred_return in events.columns:
        raw_return = pd.to_numeric(events[preferred_return], errors="coerce")
        return_column = pd.Series(preferred_return, index=events.index, dtype="object")
        if compatible_return in events.columns:
            compatible_values = pd.to_numeric(events[compatible_return], errors="coerce")
            use_compatible = raw_return.isna() & compatible_values.notna()
            raw_return = raw_return.fillna(compatible_values)
            return_column.loc[use_compatible] = compatible_return
    else:
        raw_return = pd.to_numeric(events[compatible_return], errors="coerce")
        return_column = pd.Series(compatible_return, index=events.index, dtype="object")

    selected["raw_forward_return"] = raw_return.to_numpy()
    selected["return_column"] = return_column.to_numpy()
    selected["_source_order"] = range(len(selected))

    selected["trigger_ts"] = _as_naive_datetime_index(
        selected["trigger_ts"], "事件表 trigger_ts"
    )
    try:
        selected["code"] = selected["code"].map(normalize_stock_code)
    except ValueError as exc:
        raise ValueError(f"事件表含有无效股票代码: {exc}") from exc

    valid_return = selected["raw_forward_return"].map(
        lambda value: pd.notna(value) and math.isfinite(float(value))
    )
    raw_event_count = int(len(selected))
    valid_event_count = int(valid_return.sum())
    result = selected.loc[valid_return].reset_index(drop=True)
    result.attrs["flow_counts"] = {
        "raw_event_count": raw_event_count,
        "valid_return_event_count": valid_event_count,
        "invalid_return_event_count": raw_event_count - valid_event_count,
    }
    return result


def _prepare_weight_signals(
    output_weights_df: pd.DataFrame,
    datetime_column: str | None,
) -> pd.DataFrame:
    if not isinstance(output_weights_df, pd.DataFrame) or output_weights_df.empty:
        raise ValueError("output_weights_df 必须是非空 DataFrame")

    source = output_weights_df.copy()
    resolved_time_column = datetime_column
    if resolved_time_column is None:
        resolved_time_column = next(
            (name for name in TIME_COLUMN_CANDIDATES if name in source.columns),
            None,
        )

    if resolved_time_column is not None:
        if resolved_time_column not in source.columns:
            raise ValueError(f"找不到时间列: {resolved_time_column}")
        timestamps = _as_naive_datetime_index(
            source.pop(resolved_time_column), "目标权重时间"
        )
    elif isinstance(source.index, pd.DatetimeIndex):
        timestamps = _as_naive_datetime_index(source.index, "目标权重时间")
    else:
        raise ValueError("目标权重必须有时间列或 DatetimeIndex")
    source.index = timestamps

    cash_columns = [
        column
        for column in source.columns
        if str(column).strip().lower() in CASH_COLUMN_NAMES
    ]
    if cash_columns:
        source = source.drop(columns=cash_columns)

    normalized_columns: dict[object, str] = {}
    seen_codes: set[str] = set()
    for column in source.columns:
        code = normalize_stock_code(column)
        if code in seen_codes:
            raise ValueError(f"股票代码重复: {column} 与另一列表示同一股票")
        seen_codes.add(code)
        normalized_columns[column] = code
    if not normalized_columns:
        raise ValueError("目标权重至少要有一只股票")

    weights = source.rename(columns=normalized_columns)
    all_sparse = all(isinstance(dtype, pd.SparseDtype) for dtype in weights.dtypes)
    zero_fill_sparse = all_sparse and all(
        math.isfinite(float(weights[column].array.fill_value))
        and abs(float(weights[column].array.fill_value)) <= 1e-12
        for column in weights.columns
    )
    if zero_fill_sparse and not weights.index.has_duplicates:
        signal_parts: list[pd.DataFrame] = []
        for column in weights.columns:
            sparse_array = weights[column].array
            values = pd.to_numeric(
                pd.Series(sparse_array.sp_values),
                errors="coerce",
            ).to_numpy(dtype=float, copy=False)
            if not bool(np.isfinite(values).all()):
                raise ValueError("目标权重含有空值、非数值或非有限数值")
            positions = sparse_array.sp_index.to_int_index().indices
            nonzero = np.abs(values) > 1e-12
            if not bool(nonzero.any()):
                continue
            signal_parts.append(
                pd.DataFrame(
                    {
                        "trigger_ts": weights.index.take(positions[nonzero]),
                        "code": str(column),
                        "weight": values[nonzero],
                    }
                )
            )
        if not signal_parts:
            return pd.DataFrame(columns=["trigger_ts", "code", "weight"])
        return pd.concat(signal_parts, ignore_index=True).sort_values(
            ["trigger_ts", "code"],
            kind="stable",
            ignore_index=True,
        )

    numeric_weights = weights.apply(pd.to_numeric, errors="coerce")
    if bool(numeric_weights.isna().any(axis=None)):
        raise ValueError("目标权重含有空值或非数值")
    if not all(
        math.isfinite(float(value))
        for value in numeric_weights.to_numpy().ravel()
    ):
        raise ValueError("目标权重含有非有限数值")

    # 同一时刻重复出现时，以最后一行作为该时刻的目标权重。
    numeric_weights = numeric_weights.groupby(level=0, sort=True).last()
    numeric_weights.index.name = "trigger_ts"
    matrix = numeric_weights.to_numpy(dtype=float, copy=False)
    row_positions, column_positions = np.nonzero(np.abs(matrix) > 1e-12)
    return pd.DataFrame(
        {
            "trigger_ts": numeric_weights.index.take(row_positions),
            "code": np.asarray(numeric_weights.columns, dtype=object)[column_positions],
            "weight": matrix[row_positions, column_positions],
        }
    ).sort_values(["trigger_ts", "code"], kind="stable", ignore_index=True)


def _apply_cooldown(events: pd.DataFrame, cooldown_minutes: int) -> pd.DataFrame:
    if events.empty or cooldown_minutes <= 0:
        return events.sort_values(
            ["trigger_ts", "code", "_source_order"], kind="stable"
        ).reset_index(drop=True)

    ordered = events.sort_values(
        ["date", "code", "trigger_ts", "_source_order"], kind="stable"
    )
    cooldown = pd.Timedelta(minutes=cooldown_minutes)
    last_kept: dict[tuple[pd.Timestamp, str], pd.Timestamp] = {}
    keep_indices: list[int] = []
    for index, row in ordered.iterrows():
        key = (pd.Timestamp(row["date"]), str(row["code"]))
        timestamp = pd.Timestamp(row["trigger_ts"])
        previous = last_kept.get(key)
        if previous is None or timestamp - previous >= cooldown:
            keep_indices.append(index)
            last_kept[key] = timestamp

    return (
        ordered.loc[keep_indices]
        .sort_values(["trigger_ts", "code", "_source_order"], kind="stable")
        .reset_index(drop=True)
    )


def _build_trades(
    events: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    buy_cost: float,
    sell_cost: float,
    slippage: float,
    cooldown_minutes: int,
    horizon_minutes: int,
) -> tuple[pd.DataFrame, dict[str, int]]:
    matched = events.merge(
        signals,
        how="inner",
        on=["trigger_ts", "code"],
        validate="many_to_one",
    )
    matched["date"] = matched["trigger_ts"].dt.normalize()
    matched_before_cooldown_count = int(len(matched))
    matched_signal_count = int(
        matched.loc[:, ["trigger_ts", "code"]].drop_duplicates().shape[0]
    )
    matched = _apply_cooldown(matched, cooldown_minutes)
    matched_after_cooldown_count = int(len(matched))

    matched["side"] = matched["weight"].map(
        lambda value: "long" if float(value) > 0 else "short"
    )
    matched["gross_return"] = matched["raw_forward_return"] * matched["weight"].map(
        lambda value: 1.0 if float(value) > 0 else -1.0
    )
    matched["commission_rate"] = float(buy_cost) + float(sell_cost)
    matched["slippage_rate"] = 2.0 * float(slippage)
    matched["cost_rate"] = matched["commission_rate"] + matched["slippage_rate"]
    matched["net_return"] = matched["gross_return"] - matched["cost_rate"]
    matched["weighted_gross_return"] = matched["gross_return"] * matched["weight"].abs()
    matched["weighted_net_return"] = matched["net_return"] * matched["weight"].abs()
    matched["weighted_commission_rate"] = (
        matched["commission_rate"] * matched["weight"].abs()
    )
    matched["weighted_slippage_rate"] = (
        matched["slippage_rate"] * matched["weight"].abs()
    )
    matched["holding_minutes"] = int(horizon_minutes)
    return matched.drop(columns=["_source_order"], errors="ignore"), {
        "matched_before_cooldown_count": matched_before_cooldown_count,
        "matched_signal_count": matched_signal_count,
        "unmatched_signal_count": max(int(len(signals)) - matched_signal_count, 0),
        "cooldown_removed_count": (
            matched_before_cooldown_count - matched_after_cooldown_count
        ),
        "final_event_count": matched_after_cooldown_count,
    }


def _build_daily_results(
    trades: pd.DataFrame,
    calendar_dates: pd.DatetimeIndex,
    capital: float,
) -> pd.DataFrame:
    columns = [
        "date",
        "balance",
        "net_pnl",
        "gross_pnl",
        "return",
        "gross_return",
        "commission",
        "slippage",
        "turnover",
        "trade_count",
        "drawdown",
        "drawdown_percent",
    ]
    if calendar_dates.empty:
        return pd.DataFrame(columns=columns)

    if trades.empty:
        grouped = pd.DataFrame(index=calendar_dates)
    else:
        grouped = trades.groupby("date", sort=True).agg(
            return_value=("weighted_net_return", "sum"),
            gross_return=("weighted_gross_return", "sum"),
            commission_rate=("weighted_commission_rate", "sum"),
            slippage_rate=("weighted_slippage_rate", "sum"),
            turnover=("weight", lambda values: float(values.abs().sum()) * 2.0),
            trade_count=("code", "size"),
        )
    grouped = grouped.reindex(calendar_dates).fillna(0.0)

    rows: list[dict[str, Any]] = []
    previous_balance = float(capital)
    for date, row in grouped.iterrows():
        daily_return = float(row.get("return_value", 0.0))
        gross_return = float(row.get("gross_return", 0.0))
        balance = previous_balance * (1.0 + daily_return)
        rows.append(
            {
                "date": pd.Timestamp(date),
                "balance": balance,
                "net_pnl": balance - previous_balance,
                "gross_pnl": previous_balance * gross_return,
                "return": daily_return,
                "gross_return": gross_return,
                "commission": previous_balance * float(row.get("commission_rate", 0.0)),
                "slippage": previous_balance * float(row.get("slippage_rate", 0.0)),
                "turnover": float(row.get("turnover", 0.0)),
                "trade_count": int(row.get("trade_count", 0)),
            }
        )
        previous_balance = balance

    daily = pd.DataFrame(rows)
    high_watermark = daily["balance"].cummax().clip(lower=float(capital))
    daily["drawdown"] = daily["balance"] - high_watermark
    daily["drawdown_percent"] = daily["drawdown"] / high_watermark.where(
        high_watermark != 0
    )
    return daily.loc[:, columns]


def _summarize_event_periods(
    trades: pd.DataFrame,
    *,
    frequency: str,
    success_column: str | None,
) -> list[dict[str, Any]]:
    if trades.empty:
        return []

    source = trades.copy()
    dates = pd.to_datetime(source["date"], errors="coerce")
    if frequency == "year":
        source["_period"] = dates.dt.strftime("%Y")
    elif frequency == "month":
        source["_period"] = dates.dt.strftime("%Y-%m")
    else:  # pragma: no cover - only internal fixed values are used
        raise ValueError(f"不支持的事件汇总频率: {frequency}")

    rows: list[dict[str, Any]] = []
    for period, group in source.groupby("_period", sort=True, dropna=True):
        gross = pd.to_numeric(group["gross_return"], errors="coerce").dropna()
        net = pd.to_numeric(group["net_return"], errors="coerce").dropna()
        label_observation_count = 0
        label_positive_count = 0
        label_positive_rate: float | None = None
        if success_column and success_column in group.columns:
            labels = pd.to_numeric(
                group.loc[group["side"] == "long", success_column],
                errors="coerce",
            ).dropna()
            label_observation_count = int(len(labels))
            if label_observation_count:
                label_positive_count = int(labels.astype(bool).sum())
                label_positive_rate = float(
                    label_positive_count / label_observation_count
                )

        rows.append(
            {
                "period": str(period),
                "event_count": int(len(group)),
                "gross_mean_return": float(gross.mean()) if len(gross) else 0.0,
                "gross_median_return": float(gross.median()) if len(gross) else 0.0,
                "net_mean_return": float(net.mean()) if len(net) else 0.0,
                "net_median_return": float(net.median()) if len(net) else 0.0,
                "gross_up_rate": float((gross > 0).mean()) if len(gross) else 0.0,
                "net_win_rate": float((net > 0).mean()) if len(net) else 0.0,
                "label_column": success_column,
                "label_observation_count": label_observation_count,
                "label_positive_count": label_positive_count,
                "label_positive_rate": label_positive_rate,
            }
        )
    return rows


def _calculate_stats(
    trades: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    capital: float,
    risk_free_rate: float,
    horizon_minutes: int,
    cooldown_minutes: int,
    buy_cost: float,
    sell_cost: float,
    slippage: float,
) -> dict[str, Any]:
    if daily.empty:
        end_balance = float(capital)
        total_return = 0.0
        annual_return = 0.0
        annual_volatility = 0.0
        sharpe_ratio = 0.0
        max_drawdown = 0.0
        max_ddpercent = 0.0
    else:
        end_balance = float(daily["balance"].iloc[-1])
        total_return = end_balance / float(capital) - 1.0
        periods = max(1, len(daily) - 1)
        if end_balance > 0:
            annual_return = float(
                (end_balance / float(capital)) ** (252.0 / periods) - 1.0
            )
        else:
            annual_return = -1.0

        daily_returns = daily["return"].astype(float)
        daily_std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
        annual_volatility = daily_std * math.sqrt(252.0)
        daily_risk_free_rate = (1.0 + float(risk_free_rate)) ** (1.0 / 252.0) - 1.0
        sharpe_ratio = (
            float(
                (daily_returns.mean() - daily_risk_free_rate)
                / daily_std
                * math.sqrt(252.0)
            )
            if daily_std > 0
            else 0.0
        )
        max_drawdown = float(daily["drawdown"].min())
        max_ddpercent = float(daily["drawdown_percent"].min())

    winning_returns = trades.loc[trades["net_return"] > 0, "net_return"].astype(float)
    losing_returns = trades.loc[trades["net_return"] < 0, "net_return"].astype(float)
    win_rate = float(len(winning_returns) / len(trades)) if len(trades) else 0.0
    profit_loss_ratio: float | None = None
    if len(winning_returns) and len(losing_returns):
        profit_loss_ratio = float(winning_returns.mean() / abs(losing_returns.mean()))

    gross_mean_return = (
        float(trades["gross_return"].astype(float).mean()) if len(trades) else 0.0
    )
    gross_median_return = (
        float(trades["gross_return"].astype(float).median()) if len(trades) else 0.0
    )
    gross_up_rate = (
        float((trades["gross_return"].astype(float) > 0).mean()) if len(trades) else 0.0
    )
    net_mean_return = (
        float(trades["net_return"].astype(float).mean()) if len(trades) else 0.0
    )
    net_median_return = (
        float(trades["net_return"].astype(float).median()) if len(trades) else 0.0
    )
    event_success_column: str | None = None
    event_success_rate: float | None = None
    event_success_count: int | None = None
    event_success_positive_count: int | None = None
    event_success_ci_low: float | None = None
    event_success_ci_high: float | None = None
    event_joint_minute_hit_rate: float | None = None
    event_joint_minute_hit_count: int | None = None
    event_joint_minute_hit_positive_count: int | None = None
    if "joint_minute_hit" in trades.columns:
        joint_values = pd.to_numeric(
            trades.loc[trades["side"] == "long", "joint_minute_hit"],
            errors="coerce",
        ).dropna()
        if not joint_values.empty:
            event_joint_minute_hit_count = int(len(joint_values))
            event_joint_minute_hit_positive_count = int(
                joint_values.astype(bool).sum()
            )
            event_joint_minute_hit_rate = float(
                joint_values.astype(bool).mean()
            )
    for column in ("target_buy_up_70", "joint_minute_hit"):
        if column not in trades.columns:
            continue
        values = trades.loc[trades["side"] == "long", column].dropna()
        if values.empty:
            continue
        numeric = pd.to_numeric(values, errors="coerce").dropna()
        if numeric.empty:
            continue
        event_success_column = column
        event_success_count = int(len(numeric))
        event_success_rate = float(numeric.astype(bool).mean())
        success_total = int(numeric.astype(bool).sum())
        event_success_positive_count = success_total
        event_success_ci_low, event_success_ci_high = _wilson_interval(
            success_total,
            event_success_count,
        )
        break

    cost_rate_mean = (
        float(trades["cost_rate"].astype(float).mean()) if len(trades) else 0.0
    )
    cost_scenarios = [
        {
            "cost_multiplier": multiplier,
            "net_mean_return": float(
                gross_mean_return - float(multiplier) * cost_rate_mean
            ),
        }
        for multiplier in (0, 1, 2)
    ]
    yearly_event_stats = _summarize_event_periods(
        trades,
        frequency="year",
        success_column=event_success_column,
    )
    monthly_event_stats = _summarize_event_periods(
        trades,
        frequency="month",
        success_column=event_success_column,
    )
    event_return_columns = (
        sorted(
            {
                str(value)
                for value in trades.get("return_column", pd.Series(dtype="object"))
                .dropna()
                .tolist()
                if str(value).strip()
            }
        )
        if len(trades)
        else []
    )

    return {
        "capital": float(capital),
        "end_balance": end_balance,
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility),
        "sharpe_ratio": float(sharpe_ratio),
        "max_drawdown": float(max_drawdown),
        "max_ddpercent": float(max_ddpercent),
        "total_trade_count": int(len(trades)),
        "total_commission": float(daily["commission"].sum()) if not daily.empty else 0.0,
        "total_slippage": float(daily["slippage"].sum()) if not daily.empty else 0.0,
        "win_rate": win_rate,
        "profit_loss_ratio": profit_loss_ratio,
        "event_gross_mean_return": gross_mean_return,
        "event_gross_median_return": gross_median_return,
        "event_gross_up_rate": gross_up_rate,
        "event_net_mean_return": net_mean_return,
        "event_net_median_return": net_median_return,
        "event_net_win_rate": win_rate,
        "event_cost_rate_mean": cost_rate_mean,
        "event_success_column": event_success_column,
        "event_success_count": event_success_count,
        "event_success_observation_count": event_success_count,
        "event_success_positive_count": event_success_positive_count,
        "event_success_rate": event_success_rate,
        "event_success_ci_low": event_success_ci_low,
        "event_success_ci_high": event_success_ci_high,
        "event_joint_minute_hit_rate": event_joint_minute_hit_rate,
        "event_joint_minute_hit_count": event_joint_minute_hit_count,
        "event_joint_minute_hit_positive_count": event_joint_minute_hit_positive_count,
        "yearly_event_stats": yearly_event_stats,
        "monthly_event_stats": monthly_event_stats,
        "cost_scenarios": cost_scenarios,
        "event_return_column": (
            event_return_columns[0]
            if len(event_return_columns) == 1
            else ",".join(event_return_columns)
        ),
        "_backtest_debug": {
            "holding_minutes": int(horizon_minutes),
            "event_return_columns": event_return_columns,
            "cooldown_minutes": int(cooldown_minutes),
            "buy_cost_rate": float(buy_cost),
            "sell_cost_rate": float(sell_cost),
            "slippage_rate_each_side": float(slippage),
            "matching_rule": "股票代码和 trigger_ts 完全相同",
            "result_note": "A股受T+1限制，分钟结果用于事件研究，不代表当天可完成买卖",
        },
        "_daily_df": daily,
        "_trades_df": trades,
    }


def run_event_parquet_backtest(
    output_weights_df: pd.DataFrame,
    *,
    event_path: str | os.PathLike[str],
    start: object,
    end: object,
    horizon_minutes: int = 5,
    capital: float = 1_000_000.0,
    buy_cost: float = 0.0003,
    sell_cost: float = 0.0008,
    slippage: float = 0.0,
    cooldown_minutes: int = 20,
) -> dict[str, Any]:
    """按分钟候选事件和目标权重计算交易结果。"""

    resolved_horizon = int(horizon_minutes)
    if resolved_horizon not in SUPPORTED_HORIZONS:
        raise ValueError("持有时间只能是 5、10 或 20 分钟")
    if int(cooldown_minutes) < 0:
        raise ValueError("冷却分钟数不能为负数")
    for name, value in (
        ("buy_cost", buy_cost),
        ("sell_cost", sell_cost),
        ("slippage", slippage),
    ):
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} 必须是非负有限数值")
    if not math.isfinite(float(capital)) or float(capital) <= 0:
        raise ValueError("capital 必须大于 0")

    start_date = _as_date(start, "start")
    end_date = _as_date(end, "end")
    if start_date > end_date:
        raise ValueError("start 不能晚于 end")

    paths = _resolve_event_paths(event_path)
    events = _load_events(
        paths,
        resolved_horizon,
        start_date,
        end_date,
    )
    event_load_counts = dict(events.attrs.get("flow_counts", {}))
    events["date"] = events["trigger_ts"].dt.normalize()
    events = events.loc[
        events["date"].between(start_date, end_date, inclusive="both")
    ].copy()
    calendar_dates = pd.DatetimeIndex(
        events["date"].drop_duplicates().sort_values().tolist()
    )

    signals = _prepare_weight_signals(output_weights_df, None)
    signal_times = _as_naive_datetime_index(
        signals["trigger_ts"],
        "目标权重时间",
    )
    signals = signals.copy()
    signals["trigger_ts"] = pd.Series(
        signal_times,
        index=signals.index,
        dtype="datetime64[ns]",
    )
    signals = signals.loc[
        signals["trigger_ts"].dt.normalize().between(
            start_date, end_date, inclusive="both"
        )
    ].copy()
    trades, trade_flow_counts = _build_trades(
        events,
        signals,
        buy_cost=float(buy_cost),
        sell_cost=float(sell_cost),
        slippage=float(slippage),
        cooldown_minutes=int(cooldown_minutes),
        horizon_minutes=resolved_horizon,
    )
    daily = _build_daily_results(trades, calendar_dates, float(capital))
    stats = _calculate_stats(
        trades,
        daily,
        capital=float(capital),
        risk_free_rate=0.0,
        horizon_minutes=resolved_horizon,
        cooldown_minutes=int(cooldown_minutes),
        buy_cost=float(buy_cost),
        sell_cost=float(sell_cost),
        slippage=float(slippage),
    )
    debug = stats.get("_backtest_debug")
    if isinstance(debug, dict):
        debug.update(event_load_counts)
        debug.update(
            {
                "nonzero_signal_count": int(len(signals)),
                **trade_flow_counts,
            }
        )
    return stats
