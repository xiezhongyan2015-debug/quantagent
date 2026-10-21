from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import lightgbm as lgb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
REPORT_WORK_DIR = (
    ROOT / ".codex-artifact" / "019fa45e-0ee2-7991-8e24-165b4d6d1f91"
)
REPORT_FACTOR_FILE = REPORT_WORK_DIR / "data" / "all_factors.csv"
REPORT_MONTHLY_IC_FILE = REPORT_WORK_DIR / "data" / "monthly_detail.csv"
REPORT_RETURN_HELPER = REPORT_WORK_DIR / "prepare_factor_return_metrics.py"
HISTORICAL_FACTOR_MAPPING_FILE = (
    ROOT / "historical_calendar_backtest_results" / "factor_mapping.csv"
)
NON_LEVEL2_RANKING_FILE = (
    ROOT
    / "outputs"
    / "icir_ic_ranking_market_2022_2026"
    / "data"
    / "all_factors_ranking.csv"
)

DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "lightgbm_factor_selection_20260803"
NON_LEVEL2_OUTPUT_DIR = (
    ROOT / "outputs" / "lightgbm_non_level2_ranker_walkforward_20260803"
)
AS_OF_MONTH = "2026-05"
LAST_COMPLETE_TARGET_MONTH = str(pd.Period(AS_OF_MONTH, freq="M") - 1)
TRAIN_END = "2023-12"
VALID_START = "2024-01"
VALID_END = "2024-12"
TEST_START = "2025-01"
TEST_END = LAST_COMPLETE_TARGET_MONTH
RANDOM_SEED = 20260803
MODEL_TOP_K = 20
SIMILARITY_LIMIT = 0.85


FEATURE_COLUMNS = [
    "history_months",
    "return_observed_months",
    "return_missing_rate",
    "pre2021_ic",
    "pre2021_abs_ic",
    "monthly_return_last",
    "directed_ic_last",
    "absolute_ic_last",
    "period_count_last",
    "average_cost_last",
    "return_expanding_mean",
    "return_expanding_std",
    "return_expanding_positive_rate",
    "ic_expanding_mean",
    "ic_expanding_std",
    "ic_expanding_positive_rate",
    "return_mean_3",
    "return_std_3",
    "return_sharpe_3",
    "return_positive_rate_3",
    "return_max_drawdown_3",
    "ic_mean_3",
    "ic_std_3",
    "ic_positive_rate_3",
    "absolute_ic_mean_3",
    "return_mean_6",
    "return_std_6",
    "return_sharpe_6",
    "return_positive_rate_6",
    "return_max_drawdown_6",
    "ic_mean_6",
    "ic_std_6",
    "ic_positive_rate_6",
    "absolute_ic_mean_6",
    "return_mean_12",
    "return_std_12",
    "return_sharpe_12",
    "return_positive_rate_12",
    "return_max_drawdown_12",
    "ic_mean_12",
    "ic_std_12",
    "ic_positive_rate_12",
    "absolute_ic_mean_12",
    "return_mean_12_rank",
    "return_sharpe_12_rank",
    "ic_mean_12_rank",
    "ic_positive_rate_12_rank",
    "frequency_daily",
    "frequency_monthly",
    "frequency_other",
    "quality_formal",
    "quality_trial",
]


@dataclass(frozen=True)
class ModelFitResult:
    model: Any
    best_iteration: int
    parameters: dict[str, Any]
    feature_bounds: pd.DataFrame
    model_kind: str = "regressor"


def model_feature_sets() -> dict[str, list[str]]:
    no_time_counts = [
        column
        for column in FEATURE_COLUMNS
        if column
        not in {
            "history_months",
            "return_observed_months",
            "return_missing_rate",
        }
    ]
    rolling_only = [
        column
        for column in FEATURE_COLUMNS
        if (
            column.startswith(("return_", "ic_", "absolute_ic_"))
            or column
            in {
                "pre2021_ic",
                "pre2021_abs_ic",
                "monthly_return_last",
                "directed_ic_last",
                "period_count_last",
                "average_cost_last",
                "frequency_daily",
                "frequency_monthly",
                "frequency_other",
                "quality_formal",
                "quality_trial",
            }
        )
        and "expanding" not in column
        and column
        not in {
            "history_months",
            "return_observed_months",
            "return_missing_rate",
        }
    ]
    return {
        "全部输入": list(FEATURE_COLUMNS),
        "去掉历史月份数": no_time_counts,
        "仅近期滚动指标": rolling_only,
    }


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def restrict_to_non_level2(
    metadata: pd.DataFrame,
    monthly_ic: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    filtered_metadata = metadata[
        metadata["data_group"].astype(str).eq("非Level2")
    ].copy()
    factor_ids = set(filtered_metadata["factor_id"].astype(str))
    filtered_monthly_ic = monthly_ic[
        monthly_ic["data_group"].astype(str).eq("非Level2")
        & monthly_ic["factor_id"].astype(str).isin(factor_ids)
    ].copy()
    return filtered_metadata, filtered_monthly_ic


def add_factor_availability(metadata: pd.DataFrame) -> pd.DataFrame:
    """按因子首次出现在日历中的日期，标记最早可使用时间。"""
    current = metadata.copy()

    def normalize_frequency(value: object) -> str:
        text = str(value).strip()
        if text in {"daily", "monthly", "every_5_days", "weekly_tuesday"}:
            return text
        if "每5个交易日" in text:
            return "every_5_days"
        if "每周二" in text:
            return "weekly_tuesday"
        if text in {"每日", "日度"}:
            return "daily"
        if text == "月末":
            return "monthly"
        return text

    historical_dates: dict[tuple[str, str], pd.Timestamp] = {}
    if HISTORICAL_FACTOR_MAPPING_FILE.exists():
        mapping = read_csv(HISTORICAL_FACTOR_MAPPING_FILE)
        mapping["date"] = pd.to_datetime(mapping["date"], errors="coerce")
        mapping = mapping.dropna(subset=["formula_key", "date"]).copy()
        mapping["formula_key"] = mapping["formula_key"].astype(str)
        mapping["signal_frequency"] = mapping["signal_frequency"].map(
            normalize_frequency
        )
        historical_dates = (
            mapping.groupby(["formula_key", "signal_frequency"])["date"]
            .min()
            .to_dict()
        )

    def first_calendar_year(value: object) -> int:
        years = [int(item) for item in re.findall(r"20\d{2}", str(value))]
        if not years:
            raise ValueError(f"calendar_editions中没有年份：{value}")
        return min(years)

    def infer_date(row: pd.Series) -> tuple[pd.Timestamp, str]:
        factor_id = str(row["factor_id"])
        base_id = factor_id.split("__", 1)[0]
        frequency = normalize_frequency(
            row.get("frequency", factor_id.rsplit("__", 1)[-1])
        )
        historical_key = (base_id, frequency)
        if historical_key in historical_dates:
            return historical_dates[historical_key], "历史因子日历同频率最早记录日"

        year = first_calendar_year(row["calendar_editions"])
        code = factor_id.rsplit("__", 1)[-1]
        patterns = [
            (r"J(0[1-9]|1[0-2])_D(\d{2})", None),
            (r"F(0[1-9]|1[0-2])_D(\d{2})", None),
            (r"MAY(\d{2})", 5),
            (r"^M(\d{2})_", 3),
            (r"^A(\d{2})_", 4),
        ]
        for pattern, fixed_month in patterns:
            matched = re.search(pattern, code)
            if not matched:
                continue
            if fixed_month is None:
                month, day = int(matched.group(1)), int(matched.group(2))
            else:
                month, day = fixed_month, int(matched.group(1))
            return pd.Timestamp(year=year, month=month, day=day), "因子编号中的日历日期"
        return pd.Timestamp(year=year, month=1, day=1), "日历年份内未记录具体日期，按1月1日"

    inferred = current.apply(infer_date, axis=1)
    current["available_date"] = [value[0].strftime("%Y-%m-%d") for value in inferred]
    current["availability_source"] = [value[1] for value in inferred]
    current["available_from_month"] = current["available_date"].str[:7]
    current["availability_rule"] = "月末选择时，只使用发布日期不晚于该月末的因子"
    return current


def load_return_helper() -> Any:
    spec = importlib.util.spec_from_file_location(
        "factor_return_source", REPORT_RETURN_HELPER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法读取收益整理程序：{REPORT_RETURN_HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compound_returns(values: Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return np.nan
    return float(np.prod(1.0 + array) - 1.0)


def maximum_drawdown(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return np.nan
    nav = np.r_[1.0, np.cumprod(1.0 + array)]
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def aggregate_monthly_returns(
    frame: pd.DataFrame,
    factor_column: str,
    date_column: str = "exit_date",
) -> pd.DataFrame:
    current = frame.copy()
    current["month"] = pd.to_datetime(current[date_column]).dt.strftime("%Y-%m")
    rows: list[dict[str, Any]] = []
    for (factor_id, month), part in current.groupby(
        [factor_column, "month"], sort=True
    ):
        rows.append(
            {
                "factor_id": str(factor_id),
                "month": month,
                "monthly_return": compound_returns(part["net_long_short_return"]),
                "period_count": int(len(part)),
                "average_cost": float(
                    pd.to_numeric(part["transaction_cost"], errors="coerce").mean()
                ),
                "monthly_period_drawdown": maximum_drawdown(
                    pd.to_numeric(
                        part["net_long_short_return"], errors="coerce"
                    ).to_numpy(dtype=float)
                ),
            }
        )
    return pd.DataFrame(rows)


def aggregate_entry_month_targets(
    frame: pd.DataFrame,
    factor_column: str,
) -> pd.DataFrame:
    """按建仓月份汇总收益，作为上月末选择后的下一月目标。"""
    target = aggregate_monthly_returns(
        frame,
        factor_column,
        date_column="entry_date",
    ).rename(
        columns={
            "month": "target_month",
            "monthly_return": "target_return",
            "period_count": "target_period_count",
            "average_cost": "target_average_cost",
            "monthly_period_drawdown": "target_period_drawdown",
        }
    )
    return target[target["target_month"].le(LAST_COMPLETE_TARGET_MONTH)].copy()


def return_timing_audit(frame: pd.DataFrame) -> pd.DataFrame:
    signal = pd.to_datetime(frame["signal_date"], errors="coerce")
    entry = pd.to_datetime(frame["entry_date"], errors="coerce")
    exit_date = pd.to_datetime(frame["exit_date"], errors="coerce")
    holding_days = (exit_date - entry).dt.days
    total = int(len(frame))
    signal_cross = int(signal.dt.to_period("M").ne(exit_date.dt.to_period("M")).sum())
    entry_cross = int(entry.dt.to_period("M").ne(exit_date.dt.to_period("M")).sum())
    rows = [
        ("交易期数", total, "原始多空交易记录"),
        ("信号月与退出月不同的期数", signal_cross, f"占比{signal_cross / total:.2%}"),
        ("建仓月与退出月不同的期数", entry_cross, f"占比{entry_cross / total:.2%}"),
        ("持有天数中位数", float(holding_days.median()), "自然日"),
        ("持有天数95%位置", float(holding_days.quantile(0.95)), "自然日"),
        ("最长持有天数", float(holding_days.max()), "自然日"),
        ("历史输入归属", "退出月份", "只使用月末时已经实现的收益"),
        ("预测目标归属", "建仓月份", "月末选择后评价下一月新建仓"),
        ("最后完整目标月", LAST_COMPLETE_TARGET_MONTH, "当月信号均有足够时间退出"),
    ]
    return pd.DataFrame(rows, columns=["check_item", "value", "note"])


def load_non_level2_monthly_returns(
    return_helper: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    master = read_csv(NON_LEVEL2_RANKING_FILE)
    master["unit_id"] = master["unit_id"].astype(str)
    returns = return_helper.load_non_level2_returns(set(master["unit_id"]))
    code_map, _, _ = return_helper.build_code_map()
    directions = return_helper.direction_table(master, returns, code_map)
    direction_map = directions.set_index("factor_id")["selected_direction"].to_dict()

    pieces: list[pd.DataFrame] = []
    for factor_id, part in returns.groupby("unit_id", sort=False):
        direction = str(direction_map[str(factor_id)])
        traded = return_helper.add_long_short_return(part, direction)
        traded["unit_id"] = str(factor_id)
        pieces.append(traded)
    traded_all = pd.concat(pieces, ignore_index=True)
    monthly = aggregate_monthly_returns(traded_all, "unit_id")
    targets = aggregate_entry_month_targets(traded_all, "unit_id")
    timing_audit = return_timing_audit(traded_all)
    directions = directions.rename(columns={"direction_value": "pre2021_ic"})
    directions["direction_sign"] = np.where(
        directions["selected_direction"].eq("high"), 1.0, -1.0
    )
    return monthly, targets, directions, timing_audit


def complete_monthly_ic(monthly_ic: pd.DataFrame) -> pd.DataFrame:
    current = monthly_ic.copy()
    current["month"] = current["return_month"].astype(str)
    current["monthly_ic"] = pd.to_numeric(current["monthly_ic"], errors="coerce")
    current = current[current["month"].le(AS_OF_MONTH)].copy()
    current = current[
        current["month_status"].astype(str).eq("完整月份")
        | current["data_group"].astype(str).eq("非Level2")
    ].copy()
    return current


def load_level2_monthly_returns(
    return_helper: Any,
    monthly_ic: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    paired = return_helper.load_level2_returns()
    paired["month"] = pd.to_datetime(paired["exit_date"]).dt.strftime("%Y-%m")
    paired = paired[paired["month"].le(AS_OF_MONTH)].copy()

    ic_source = monthly_ic[
        monthly_ic["data_group"].astype(str).eq("Level2")
    ][["factor_id", "month", "monthly_ic"]].copy()
    ic_source["factor_id"] = ic_source["factor_id"].astype(str)

    monthly_parts: list[pd.DataFrame] = []
    direction_rows: list[dict[str, Any]] = []
    for factor_id, factor_returns in paired.groupby("factor_id", sort=False):
        factor_id = str(factor_id)
        factor_ic = ic_source[ic_source["factor_id"].eq(factor_id)].sort_values(
            "month"
        )
        ic_by_month = factor_ic.set_index("month")["monthly_ic"]
        for month, month_returns in factor_returns.groupby("month", sort=True):
            past_ic = pd.to_numeric(
                ic_by_month[ic_by_month.index < month], errors="coerce"
            ).dropna()
            if past_ic.empty:
                continue
            direction = "high" if float(past_ic.mean()) >= 0 else "low"
            traded = return_helper.add_long_short_return(month_returns, direction)
            traded["factor_id"] = factor_id
            monthly_parts.append(traded)

        known_ic = pd.to_numeric(
            ic_by_month[ic_by_month.index <= AS_OF_MONTH], errors="coerce"
        ).dropna()
        if len(known_ic):
            selected_direction = "high" if float(known_ic.mean()) >= 0 else "low"
            direction_rows.append(
                {
                    "factor_id": factor_id,
                    "selected_direction": selected_direction,
                    "direction_sign": 1.0 if selected_direction == "high" else -1.0,
                    "pre2021_ic": np.nan,
                    "direction_source": "仅使用当时以前的月度IC",
                }
            )

    if monthly_parts:
        traded_all = pd.concat(monthly_parts, ignore_index=True)
        monthly = aggregate_monthly_returns(traded_all, "factor_id")
    else:
        monthly = pd.DataFrame(
            columns=[
                "factor_id",
                "month",
                "monthly_return",
                "period_count",
                "average_cost",
                "monthly_period_drawdown",
            ]
        )
    return monthly, pd.DataFrame(direction_rows)


def rolling_drawdown(series: pd.Series, window: int, minimum: int) -> pd.Series:
    return series.rolling(window, min_periods=minimum).apply(
        maximum_drawdown, raw=True
    )


def build_feature_panel(
    metadata: pd.DataFrame,
    monthly_ic: pd.DataFrame,
    non_level2_returns: pd.DataFrame,
    non_level2_directions: pd.DataFrame,
    level2_returns: pd.DataFrame,
    level2_directions: pd.DataFrame,
    target_returns: pd.DataFrame,
) -> pd.DataFrame:
    ic_columns = [
        "factor_id",
        "factor_name",
        "data_group",
        "frequency",
        "month",
        "monthly_ic",
    ]
    panel = monthly_ic[ic_columns].copy()
    panel["factor_id"] = panel["factor_id"].astype(str)

    returns = pd.concat(
        [non_level2_returns, level2_returns], ignore_index=True, sort=False
    )
    panel = panel.merge(returns, on=["factor_id", "month"], how="left")

    direction_data = pd.concat(
        [non_level2_directions, level2_directions], ignore_index=True, sort=False
    )
    direction_data = direction_data.drop_duplicates("factor_id", keep="last")
    panel = panel.merge(
        direction_data[
            [
                "factor_id",
                "selected_direction",
                "direction_sign",
                "pre2021_ic",
                "direction_source",
            ]
        ],
        on="factor_id",
        how="left",
    )

    static_columns = [
        "factor_id",
        "implementation_quality",
        "status",
        "source_kind",
        "calendar_editions",
        "available_date",
        "availability_source",
        "available_from_month",
        "availability_rule",
    ]
    panel = panel.merge(
        metadata[static_columns].drop_duplicates("factor_id"),
        on="factor_id",
        how="left",
    )
    panel = panel.sort_values(["factor_id", "month"], kind="stable").reset_index(
        drop=True
    )
    panel["is_available"] = panel["month"].ge(panel["available_from_month"])

    # Level2每个月采用进入该月前已经知道的方向，普通因子采用2016—2020年方向。
    level2_mask = panel["data_group"].eq("Level2")
    for factor_id, index in panel[level2_mask].groupby("factor_id").groups.items():
        part = panel.loc[index].sort_values("month")
        signs: list[float] = []
        past_values: list[float] = []
        for value in pd.to_numeric(part["monthly_ic"], errors="coerce"):
            if past_values:
                signs.append(1.0 if float(np.mean(past_values)) >= 0 else -1.0)
            else:
                signs.append(np.nan)
            if np.isfinite(value):
                past_values.append(float(value))
        panel.loc[part.index, "direction_sign"] = signs

    panel["directed_ic"] = (
        pd.to_numeric(panel["monthly_ic"], errors="coerce")
        * pd.to_numeric(panel["direction_sign"], errors="coerce")
    )
    panel["absolute_ic"] = pd.to_numeric(
        panel["monthly_ic"], errors="coerce"
    ).abs()
    panel["pre2021_ic"] = pd.to_numeric(panel["pre2021_ic"], errors="coerce")
    panel["pre2021_abs_ic"] = panel["pre2021_ic"].abs()
    panel["monthly_return"] = pd.to_numeric(
        panel["monthly_return"], errors="coerce"
    )
    panel["period_count"] = pd.to_numeric(panel["period_count"], errors="coerce")
    panel["average_cost"] = pd.to_numeric(panel["average_cost"], errors="coerce")

    grouped = panel.groupby("factor_id", sort=False, group_keys=False)
    panel["history_months"] = grouped.cumcount() + 1
    panel["return_observed_months"] = grouped["monthly_return"].transform(
        lambda values: values.notna().cumsum()
    )
    panel["return_missing_rate"] = 1.0 - (
        panel["return_observed_months"] / panel["history_months"]
    )
    panel["monthly_return_last"] = panel["monthly_return"]
    panel["directed_ic_last"] = panel["directed_ic"]
    panel["absolute_ic_last"] = panel["absolute_ic"]
    panel["period_count_last"] = panel["period_count"]
    panel["average_cost_last"] = panel["average_cost"]

    for column, prefix in (("monthly_return", "return"), ("directed_ic", "ic")):
        panel[f"{prefix}_expanding_mean"] = grouped[column].transform(
            lambda values: values.expanding(min_periods=2).mean()
        )
        panel[f"{prefix}_expanding_std"] = grouped[column].transform(
            lambda values: values.expanding(min_periods=2).std(ddof=1)
        )
        panel[f"{prefix}_expanding_positive_rate"] = grouped[column].transform(
            lambda values: values.gt(0).where(values.notna()).expanding(min_periods=2).mean()
        )

    for window, minimum in ((3, 2), (6, 3), (12, 6)):
        return_mean = grouped["monthly_return"].transform(
            lambda values, w=window, m=minimum: values.rolling(w, min_periods=m).mean()
        )
        return_std = grouped["monthly_return"].transform(
            lambda values, w=window, m=minimum: values.rolling(w, min_periods=m).std(ddof=1)
        )
        panel[f"return_mean_{window}"] = return_mean
        panel[f"return_std_{window}"] = return_std
        panel[f"return_sharpe_{window}"] = (
            return_mean / return_std.replace(0.0, np.nan) * math.sqrt(12.0)
        )
        panel[f"return_positive_rate_{window}"] = grouped[
            "monthly_return"
        ].transform(
            lambda values, w=window, m=minimum: values.gt(0)
            .where(values.notna())
            .rolling(w, min_periods=m)
            .mean()
        )
        panel[f"return_max_drawdown_{window}"] = grouped[
            "monthly_return"
        ].transform(
            lambda values, w=window, m=minimum: rolling_drawdown(values, w, m)
        )
        panel[f"ic_mean_{window}"] = grouped["directed_ic"].transform(
            lambda values, w=window, m=minimum: values.rolling(w, min_periods=m).mean()
        )
        panel[f"ic_std_{window}"] = grouped["directed_ic"].transform(
            lambda values, w=window, m=minimum: values.rolling(w, min_periods=m).std(ddof=1)
        )
        panel[f"ic_positive_rate_{window}"] = grouped["directed_ic"].transform(
            lambda values, w=window, m=minimum: values.gt(0)
            .where(values.notna())
            .rolling(w, min_periods=m)
            .mean()
        )
        panel[f"absolute_ic_mean_{window}"] = grouped["absolute_ic"].transform(
            lambda values, w=window, m=minimum: values.rolling(w, min_periods=m).mean()
        )

    for column in (
        "return_mean_12",
        "return_sharpe_12",
        "ic_mean_12",
        "ic_positive_rate_12",
    ):
        panel[f"{column}_rank"] = panel.groupby("month")[column].rank(
            pct=True, method="average"
        )

    frequency = panel["frequency"].astype(str)
    panel["frequency_daily"] = frequency.eq("daily").astype(int)
    panel["frequency_monthly"] = frequency.eq("monthly").astype(int)
    panel["frequency_other"] = (~frequency.isin(["daily", "monthly"])).astype(int)
    quality = panel["implementation_quality"].astype(str)
    panel["quality_formal"] = quality.isin(["strict", "正式复测"]).astype(int)
    panel["quality_trial"] = quality.eq("trial").astype(int)

    current_period = pd.PeriodIndex(panel["month"], freq="M")
    panel["target_month"] = (current_period + 1).astype(str)
    targets = target_returns.copy()
    targets["factor_id"] = targets["factor_id"].astype(str)
    target_columns = [
        "factor_id",
        "target_month",
        "target_return",
        "target_period_count",
        "target_average_cost",
        "target_period_drawdown",
    ]
    panel = panel.merge(targets[target_columns], on=["factor_id", "target_month"], how="left")
    target_period = pd.PeriodIndex(panel["target_month"], freq="M")
    current_period = pd.PeriodIndex(panel["month"], freq="M")
    panel["target_is_next_month"] = (target_period - current_period).map(
        lambda value: value.n
    )
    return panel


def quality_priority(value: object) -> int:
    return {
        "strict": 4,
        "正式复测": 4,
        "trial": 2,
        "unavailable": 0,
    }.get(str(value), 1)


def source_priority(value: object) -> int:
    return {
        "current_2026": 4,
        "2026因子日历独立因子": 3,
        "历史因子日历": 2,
        "historical": 1,
    }.get(str(value), 0)


def factor_series_signature(part: pd.DataFrame) -> str:
    values: list[tuple[str, float | None, float | None]] = []
    finite_count = 0
    for row in part.sort_values("month").itertuples(index=False):
        monthly_ic = pd.to_numeric(pd.Series([row.monthly_ic]), errors="coerce").iloc[0]
        monthly_return = pd.to_numeric(
            pd.Series([row.monthly_return]), errors="coerce"
        ).iloc[0]
        if np.isfinite(monthly_ic):
            finite_count += 1
        if np.isfinite(monthly_return):
            finite_count += 1
        values.append(
            (
                str(row.month),
                round(float(monthly_ic), 10) if np.isfinite(monthly_ic) else None,
                round(float(monthly_return), 10)
                if np.isfinite(monthly_return)
                else None,
            )
        )
    if finite_count < 3:
        return ""
    text = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def assign_duplicate_groups(
    panel: pd.DataFrame,
    metadata: pd.DataFrame,
    cutoff_month: str,
) -> pd.DataFrame:
    source = panel[panel["month"].le(cutoff_month)].copy()
    signature_rows: list[dict[str, str]] = []
    for factor_id, part in source.groupby("factor_id", sort=True):
        signature_rows.append(
            {
                "factor_id": str(factor_id),
                "series_signature": factor_series_signature(part),
            }
        )
    signatures = pd.DataFrame(signature_rows)
    all_ids = metadata[["factor_id"]].copy()
    all_ids["factor_id"] = all_ids["factor_id"].astype(str)
    signatures = all_ids.merge(signatures, on="factor_id", how="left")
    signatures["series_signature"] = signatures["series_signature"].fillna("")
    signatures.loc[
        signatures["series_signature"].eq(""), "series_signature"
    ] = "unique:" + signatures["factor_id"]

    metadata_rank = metadata[
        [
            "factor_id",
            "implementation_quality",
            "source_kind",
            "full_months",
            "available_from_month",
        ]
    ].copy()
    metadata_rank["factor_id"] = metadata_rank["factor_id"].astype(str)
    metadata_rank["quality_priority"] = metadata_rank[
        "implementation_quality"
    ].map(quality_priority)
    metadata_rank["source_priority"] = metadata_rank["source_kind"].map(
        source_priority
    )
    metadata_rank["full_months"] = pd.to_numeric(
        metadata_rank["full_months"], errors="coerce"
    ).fillna(0)
    signatures = signatures.merge(metadata_rank, on="factor_id", how="left")

    output: list[dict[str, Any]] = []
    duplicate_number = 0
    for _, part in signatures.groupby("series_signature", sort=True):
        ranked = part.sort_values(
            [
                "quality_priority",
                "source_priority",
                "available_from_month",
                "full_months",
                "factor_id",
            ],
            ascending=[False, False, True, False, True],
            kind="stable",
        )
        representative = str(ranked.iloc[0]["factor_id"])
        size = int(len(part))
        group_name = ""
        if size > 1:
            duplicate_number += 1
            group_name = f"重复组{duplicate_number:03d}"
        for factor_id in part["factor_id"].astype(str):
            output.append(
                {
                    "factor_id": factor_id,
                    "duplicate_group": group_name,
                    "duplicate_size": size,
                    "representative_factor_id": representative,
                    "is_representative": factor_id == representative,
                }
            )
    return pd.DataFrame(output)


def fit_feature_bounds(
    frame: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for column in feature_columns:
        values = pd.to_numeric(frame[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        valid = values.dropna()
        if valid.empty:
            lower = np.nan
            upper = np.nan
        elif column.startswith("frequency_") or column.startswith("quality_"):
            lower = float(valid.min())
            upper = float(valid.max())
        else:
            lower = float(valid.quantile(0.01))
            upper = float(valid.quantile(0.99))
        rows.append(
            {
                "feature": column,
                "lower_1pct": lower,
                "upper_99pct": upper,
                "training_missing_rate": float(values.isna().mean()),
            }
        )
    return pd.DataFrame(rows)


def apply_feature_bounds(
    frame: pd.DataFrame,
    feature_columns: list[str],
    bounds: pd.DataFrame,
) -> pd.DataFrame:
    result = frame[feature_columns].copy()
    bound_map = bounds.set_index("feature").to_dict("index")
    for column in feature_columns:
        values = pd.to_numeric(result[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        lower = bound_map[column]["lower_1pct"]
        upper = bound_map[column]["upper_99pct"]
        if np.isfinite(lower) and np.isfinite(upper):
            values = values.clip(lower=float(lower), upper=float(upper))
        result[column] = values.astype(float)
    return result


def equal_month_weights(months: pd.Series) -> np.ndarray:
    counts = months.astype(str).value_counts()
    weights = months.astype(str).map(lambda value: 1.0 / counts[value]).to_numpy()
    return weights * (len(weights) / weights.sum())


def make_rank_labels(frame: pd.DataFrame, bins: int = 5) -> pd.Series:
    """把每个月的收益转成0至bins-1档，最高收益对应最高档。"""
    labels = pd.Series(index=frame.index, dtype="int32")
    for _, part in frame.groupby("target_month", sort=False):
        returns = pd.to_numeric(part["target_return"], errors="coerce")
        ranks = returns.rank(method="average")
        current = np.floor((ranks - 1.0) * bins / len(part)).clip(
            0, bins - 1
        ).astype("int32")
        labels.loc[part.index] = current
    if labels.isna().any():
        raise ValueError("排名标签存在缺失值")
    return labels.astype("int32")


def prepare_rank_frame(
    frame: pd.DataFrame,
    feature_columns: list[str],
    bounds: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    ordered = frame.sort_values(
        ["target_month", "factor_id"], kind="stable"
    ).copy()
    matrix = apply_feature_bounds(ordered, feature_columns, bounds)
    labels = make_rank_labels(ordered).to_numpy(dtype=np.int32)
    groups = (
        ordered.groupby("target_month", sort=False)
        .size()
        .to_numpy(dtype=np.int32)
    )
    if int(groups.sum()) != len(ordered):
        raise RuntimeError("LightGBM每月分组行数与样本数不一致")
    weights = equal_month_weights(ordered["target_month"])
    return ordered, matrix, labels, groups, weights


def ranking_parameter_candidates() -> list[dict[str, Any]]:
    return [
        {
            "objective": objective,
            "num_leaves": num_leaves,
            "min_child_samples": min_child_samples,
            "max_bin": max_bin,
        }
        for objective, num_leaves, min_child_samples, max_bin in itertools.product(
            ("lambdarank", "rank_xendcg"),
            (7, 15),
            (60, 120),
            (63, 127),
        )
    ]


def ranking_model_parameters(
    overrides: dict[str, Any],
    n_estimators: int = 600,
    top_k: int = MODEL_TOP_K,
) -> dict[str, Any]:
    return {
        "n_estimators": n_estimators,
        "learning_rate": 0.03,
        "max_depth": -1,
        "subsample": 0.85,
        "subsample_freq": 1,
        "feature_fraction": 0.85,
        "reg_alpha": 0.10,
        "reg_lambda": 5.00,
        "metric": "ndcg",
        "label_gain": [0, 1, 3, 7, 15],
        "lambdarank_truncation_level": top_k + 3,
        "random_state": RANDOM_SEED,
        "n_jobs": 8,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        **overrides,
    }


def fit_fixed_ranker(
    train: pd.DataFrame,
    feature_columns: list[str],
    parameters: dict[str, Any],
    n_estimators: int,
    top_k: int,
) -> ModelFitResult:
    bounds = fit_feature_bounds(train, feature_columns)
    ordered, matrix, labels, groups, weights = prepare_rank_frame(
        train, feature_columns, bounds
    )
    model = lgb.LGBMRanker(
        **ranking_model_parameters(
            parameters,
            n_estimators=max(1, n_estimators),
            top_k=top_k,
        )
    )
    model.fit(matrix, labels, group=groups, sample_weight=weights)
    return ModelFitResult(
        model=model,
        best_iteration=max(1, n_estimators),
        parameters=parameters,
        feature_bounds=bounds,
        model_kind="ranker",
    )


def tune_ranker_walkforward(
    model_rows: pd.DataFrame,
    panel: pd.DataFrame,
    feature_columns: list[str],
    top_k: int,
) -> tuple[ModelFitResult, pd.DataFrame, pd.DataFrame, bool]:
    folds = [
        ("2024上半年", "2023-12", "2024-01", "2024-06"),
        ("2024下半年", "2024-06", "2024-07", "2024-12"),
    ]
    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for sequence, candidate in enumerate(ranking_parameter_candidates(), start=1):
        candidate_details: list[dict[str, Any]] = []
        for fold_name, train_end, valid_start, valid_end in folds:
            train = model_rows[model_rows["target_month"].le(train_end)].copy()
            validation = model_rows[
                model_rows["target_month"].between(valid_start, valid_end)
            ].copy()
            if train.empty or validation.empty:
                raise RuntimeError(f"{fold_name}时间检查没有可用样本")
            bounds = fit_feature_bounds(train, feature_columns)
            (
                ordered_train,
                x_train,
                y_train,
                train_groups,
                train_weights,
            ) = prepare_rank_frame(train, feature_columns, bounds)
            (
                ordered_validation,
                x_validation,
                y_validation,
                validation_groups,
                _,
            ) = prepare_rank_frame(validation, feature_columns, bounds)
            model = lgb.LGBMRanker(
                **ranking_model_parameters(candidate, top_k=top_k)
            )
            model.fit(
                x_train,
                y_train,
                group=train_groups,
                sample_weight=train_weights,
                eval_set=[(x_validation, y_validation)],
                eval_group=[validation_groups],
                eval_at=[top_k],
                callbacks=[
                    lgb.early_stopping(
                        40,
                        first_metric_only=True,
                        verbose=False,
                        min_delta=1e-4,
                    )
                ],
            )
            predictions = pd.Series(
                model.predict(
                    x_validation,
                    num_iteration=model.best_iteration_,
                ),
                index=ordered_validation.index,
            ).reindex(validation.index)
            scored = validation.copy()
            scored["ranking_prediction"] = predictions
            metrics, _ = evaluate_selection_process(
                scored,
                panel,
                "ranking_prediction",
                top_k,
                {},
                require_positive_score=False,
            )
            fold_passed = bool(
                metrics.get("top_excess_mean", -np.inf) > 0
                and metrics.get("rank_ic_mean", -np.inf) > 0
            )
            detail = {
                "setting_sequence": sequence,
                **candidate,
                "fold": fold_name,
                "train_end": train_end,
                "validation_start": valid_start,
                "validation_end": valid_end,
                "train_rows": int(len(ordered_train)),
                "validation_rows": int(len(ordered_validation)),
                "best_iteration": int(model.best_iteration_ or 1),
                "fold_passed": fold_passed,
                **metrics,
            }
            detail_rows.append(detail)
            candidate_details.append(detail)

        mean_excess = float(np.mean([row["top_excess_mean"] for row in candidate_details]))
        mean_rank_ic = float(np.mean([row["rank_ic_mean"] for row in candidate_details]))
        pass_count = int(sum(bool(row["fold_passed"]) for row in candidate_details))
        summary_rows.append(
            {
                "setting_sequence": sequence,
                **candidate,
                "fold_pass_count": pass_count,
                "all_folds_passed": pass_count == len(folds),
                "mean_top_excess": mean_excess,
                "mean_rank_ic": mean_rank_ic,
                "validation_score": mean_excess + 0.01 * mean_rank_ic,
                "best_iteration": int(
                    round(np.median([row["best_iteration"] for row in candidate_details]))
                ),
            }
        )

    search = pd.DataFrame(summary_rows).sort_values(
        ["all_folds_passed", "fold_pass_count", "validation_score"],
        ascending=[False, False, False],
        kind="stable",
    ).reset_index(drop=True)
    search.insert(0, "overall_rank", np.arange(1, len(search) + 1))
    best = search.iloc[0]
    parameters = {
        "objective": str(best["objective"]),
        "num_leaves": int(best["num_leaves"]),
        "min_child_samples": int(best["min_child_samples"]),
        "max_bin": int(best["max_bin"]),
    }
    best_iteration = int(best["best_iteration"])
    fit_source = model_rows[model_rows["target_month"].le(VALID_END)].copy()
    fitted = fit_fixed_ranker(
        fit_source,
        feature_columns,
        parameters,
        best_iteration,
        top_k,
    )
    details = pd.DataFrame(detail_rows)
    selected_details = details[
        details["setting_sequence"].eq(int(best["setting_sequence"]))
    ].copy()
    return fitted, search, selected_details, bool(best["all_folds_passed"])


def rank_correlation(actual: pd.Series, predicted: pd.Series) -> float:
    valid = actual.notna() & predicted.notna()
    if int(valid.sum()) < 3:
        return np.nan
    return float(actual[valid].rank().corr(predicted[valid].rank()))


def strategy_drawdown(monthly_returns: pd.Series) -> float:
    return maximum_drawdown(pd.to_numeric(monthly_returns, errors="coerce").to_numpy())


def evaluate_predictions(
    frame: pd.DataFrame,
    prediction_column: str,
    top_k: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for month, part in frame.groupby("target_month", sort=True):
        valid = part[["factor_id", "target_return", prediction_column]].dropna()
        if valid.empty:
            continue
        count = min(top_k, len(valid))
        ranked = valid.sort_values(prediction_column, ascending=False, kind="stable")
        top = ranked.head(count)
        baseline = valid["target_return"]
        rows.append(
            {
                "month": str(month),
                "candidate_count": int(len(valid)),
                "top_count": int(count),
                "rank_ic": rank_correlation(
                    valid["target_return"], valid[prediction_column]
                ),
                "top_return": float(top["target_return"].mean()),
                "all_factor_return": float(baseline.mean()),
                "top_excess_return": float(
                    top["target_return"].mean() - baseline.mean()
                ),
                "top_positive_rate": float((top["target_return"] > 0).mean()),
                "selected_factors": " | ".join(top["factor_id"].astype(str)),
            }
        )
    monthly = pd.DataFrame(rows)
    return summarize_monthly_performance(monthly)


def summarize_monthly_performance(
    monthly: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if monthly.empty:
        return {}, monthly
    metrics = {
        "months": int(len(monthly)),
        "rank_ic_mean": float(monthly["rank_ic"].mean()),
        "rank_ic_median": float(monthly["rank_ic"].median()),
        "top_return_mean": float(monthly["top_return"].mean()),
        "all_factor_return_mean": float(monthly["all_factor_return"].mean()),
        "top_excess_mean": float(monthly["top_excess_return"].mean()),
        "top_positive_month_rate": float((monthly["top_return"] > 0).mean()),
        "top_cumulative_return": compound_returns(monthly["top_return"]),
        "all_factor_cumulative_return": compound_returns(
            monthly["all_factor_return"]
        ),
        "top_max_drawdown": strategy_drawdown(monthly["top_return"]),
    }
    return metrics, monthly


def filter_formal_candidates(
    frame: pd.DataFrame,
    score_column: str,
    top_k: int,
    require_positive_score: bool = True,
) -> pd.DataFrame:
    numeric_score = pd.to_numeric(frame[score_column], errors="coerce")
    score_mask = numeric_score.notna()
    if require_positive_score:
        score_mask &= numeric_score.gt(0)
    candidates = frame[score_mask].copy()
    candidates["positive_evidence_count"] = (
        candidates["return_mean_12"].gt(0).astype(int)
        + candidates["ic_mean_12"].gt(0).astype(int)
        + candidates["return_positive_rate_12"].ge(0.5).astype(int)
    )
    filtered = candidates[
        candidates["positive_evidence_count"].ge(2)
        & candidates["return_max_drawdown_12"].gt(-0.40)
    ].copy()
    return filtered


def evaluate_selection_process(
    frame: pd.DataFrame,
    panel: pd.DataFrame,
    prediction_column: str,
    top_k: int,
    correlation_cache: dict[str, pd.DataFrame] | None = None,
    require_positive_score: bool = True,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if correlation_cache is None:
        correlation_cache = {}
    rows: list[dict[str, Any]] = []
    for target_month, part in frame.groupby("target_month", sort=True):
        valid = part[
            [
                "factor_id",
                "month",
                "target_return",
                prediction_column,
                "history_months",
                "return_mean_12",
                "return_positive_rate_12",
                "return_max_drawdown_12",
                "ic_mean_12",
            ]
        ].dropna(subset=["target_return", prediction_column])
        valid = valid[valid["history_months"].ge(24)].copy()
        if valid.empty:
            continue
        as_of_month = str(valid["month"].iloc[0])
        if as_of_month not in correlation_cache:
            cutoff = pd.Period(as_of_month, freq="M") - 23
            history = panel[
                panel["month"].between(str(cutoff), as_of_month)
                & panel["factor_id"].isin(set(valid["factor_id"].astype(str)))
            ]
            return_wide = history.pivot(
                index="month", columns="factor_id", values="monthly_return"
            )
            correlation_cache[as_of_month] = return_wide.corr(min_periods=12)
        filtered = filter_formal_candidates(
            valid,
            prediction_column,
            top_k,
            require_positive_score=require_positive_score,
        )
        selected, _ = select_distinct_factors(
            filtered,
            panel,
            top_k,
            as_of_month,
            SIMILARITY_LIMIT,
            prediction_column,
            correlation_cache[as_of_month],
        )
        selected_ids = selected["factor_id"].astype(str).tolist()
        top = valid[valid["factor_id"].astype(str).isin(selected_ids)]
        top_return = float(top["target_return"].mean()) if len(top) else np.nan
        all_return = float(valid["target_return"].mean())
        rows.append(
            {
                "month": str(target_month),
                "candidate_count": int(len(valid)),
                "top_count": int(len(top)),
                "rank_ic": rank_correlation(
                    valid["target_return"], valid[prediction_column]
                ),
                "top_return": top_return,
                "all_factor_return": all_return,
                "top_excess_return": top_return - all_return,
                "top_positive_rate": (
                    float((top["target_return"] > 0).mean()) if len(top) else np.nan
                ),
                "selected_factors": " | ".join(selected_ids),
            }
        )
    return summarize_monthly_performance(pd.DataFrame(rows))


def parameter_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for num_leaves, min_child_samples, feature_fraction in itertools.product(
        (7, 15, 31, 63), (40, 100), (0.70, 1.00)
    ):
        candidates.append(
            {
                "num_leaves": num_leaves,
                "min_child_samples": min_child_samples,
                "feature_fraction": feature_fraction,
            }
        )
    return candidates


def model_parameters(overrides: dict[str, Any], n_estimators: int = 800) -> dict[str, Any]:
    return {
        "objective": "huber",
        "alpha": 0.90,
        "n_estimators": n_estimators,
        "learning_rate": 0.03,
        "max_depth": -1,
        "subsample": 0.85,
        "subsample_freq": 1,
        "reg_alpha": 0.10,
        "reg_lambda": 1.00,
        "random_state": RANDOM_SEED,
        "n_jobs": 8,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        **overrides,
    }


def tune_model(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    panel: pd.DataFrame,
    feature_columns: list[str],
    top_k: int,
    correlation_cache: dict[str, pd.DataFrame],
) -> tuple[ModelFitResult, pd.DataFrame]:
    bounds = fit_feature_bounds(train, feature_columns)
    x_train = apply_feature_bounds(train, feature_columns, bounds)
    x_validation = apply_feature_bounds(validation, feature_columns, bounds)
    y_train = pd.to_numeric(train["target_return"], errors="coerce")
    y_validation = pd.to_numeric(validation["target_return"], errors="coerce")
    lower_target = float(y_train.quantile(0.01))
    upper_target = float(y_train.quantile(0.99))
    clipped_target = y_train.clip(lower_target, upper_target)
    train_weights = equal_month_weights(train["target_month"])

    search_rows: list[dict[str, Any]] = []
    best_model: lgb.LGBMRegressor | None = None
    best_parameters: dict[str, Any] | None = None
    best_iteration = 0
    best_score = -np.inf
    for sequence, candidate in enumerate(parameter_candidates(), start=1):
        model = lgb.LGBMRegressor(**model_parameters(candidate))
        model.fit(
            x_train,
            clipped_target,
            sample_weight=train_weights,
            eval_set=[(x_validation, y_validation)],
            eval_metric="l2",
            callbacks=[lgb.early_stopping(60, verbose=False)],
        )
        predictions = model.predict(
            x_validation,
            num_iteration=model.best_iteration_,
        )
        scored = validation.copy()
        scored["prediction"] = predictions
        metrics, _ = evaluate_selection_process(
            scored[scored["quality_formal"].eq(1)],
            panel,
            "prediction",
            top_k,
            correlation_cache,
        )
        validation_score = float(
            metrics.get("top_excess_mean", -np.inf)
            + 0.02 * metrics.get("rank_ic_mean", -np.inf)
        )
        search_rows.append(
            {
                "sequence": sequence,
                **candidate,
                "best_iteration": int(model.best_iteration_ or 1),
                "validation_score": validation_score,
                **{f"validation_{key}": value for key, value in metrics.items()},
            }
        )
        if validation_score > best_score:
            best_score = validation_score
            best_model = model
            best_parameters = candidate
            best_iteration = int(model.best_iteration_ or 1)

    if best_model is None or best_parameters is None:
        raise RuntimeError("LightGBM没有得到可用结果")
    search = pd.DataFrame(search_rows).sort_values(
        "validation_score", ascending=False, kind="stable"
    )
    return (
        ModelFitResult(
            model=best_model,
            best_iteration=best_iteration,
            parameters=best_parameters,
            feature_bounds=bounds,
        ),
        search,
    )


def tune_model_and_features(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    panel: pd.DataFrame,
    top_k: int,
    correlation_cache: dict[str, pd.DataFrame],
) -> tuple[ModelFitResult, str, list[str], pd.DataFrame]:
    best_fit: ModelFitResult | None = None
    best_feature_set = ""
    best_columns: list[str] = []
    best_score = -np.inf
    searches: list[pd.DataFrame] = []
    for feature_set, columns in model_feature_sets().items():
        fitted, search = tune_model(
            train,
            validation,
            panel,
            columns,
            top_k,
            correlation_cache,
        )
        current = search.rename(columns={"sequence": "setting_sequence"}).copy()
        current.insert(0, "feature_set", feature_set)
        current.insert(1, "feature_count", len(columns))
        searches.append(current)
        score = float(current["validation_score"].iloc[0])
        if score > best_score:
            best_score = score
            best_fit = fitted
            best_feature_set = feature_set
            best_columns = list(columns)
    if best_fit is None:
        raise RuntimeError("LightGBM没有得到可用的输入组合")
    combined = pd.concat(searches, ignore_index=True).sort_values(
        "validation_score", ascending=False, kind="stable"
    )
    combined.insert(0, "overall_rank", np.arange(1, len(combined) + 1))
    return best_fit, best_feature_set, best_columns, combined


def fit_fixed_model(
    train: pd.DataFrame,
    feature_columns: list[str],
    parameters: dict[str, Any],
    n_estimators: int,
) -> ModelFitResult:
    bounds = fit_feature_bounds(train, feature_columns)
    x_train = apply_feature_bounds(train, feature_columns, bounds)
    y_train = pd.to_numeric(train["target_return"], errors="coerce")
    clipped_target = y_train.clip(y_train.quantile(0.01), y_train.quantile(0.99))
    model = lgb.LGBMRegressor(
        **model_parameters(parameters, n_estimators=max(1, n_estimators))
    )
    model.fit(
        x_train,
        clipped_target,
        sample_weight=equal_month_weights(train["target_month"]),
    )
    return ModelFitResult(
        model=model,
        best_iteration=max(1, n_estimators),
        parameters=parameters,
        feature_bounds=bounds,
    )


def predict_with_model(
    fitted: ModelFitResult,
    frame: pd.DataFrame,
    feature_columns: list[str],
) -> np.ndarray:
    matrix = apply_feature_bounds(frame, feature_columns, fitted.feature_bounds)
    return fitted.model.predict(matrix, num_iteration=fitted.best_iteration)


def correlation_with_selected(
    candidate: str,
    selected: list[str],
    return_correlation: pd.DataFrame,
) -> tuple[float, str]:
    if not selected or candidate not in return_correlation.index:
        return np.nan, ""
    best_value = np.nan
    best_factor = ""
    for factor_id in selected:
        if factor_id not in return_correlation.columns:
            continue
        value = float(return_correlation.at[candidate, factor_id])
        if not np.isfinite(value):
            continue
        if not np.isfinite(best_value) or abs(value) > abs(best_value):
            best_value = value
            best_factor = factor_id
    return best_value, best_factor


def select_distinct_factors(
    candidates: pd.DataFrame,
    panel: pd.DataFrame,
    top_k: int,
    as_of_month: str,
    similarity_limit: float,
    score_column: str,
    return_correlation: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    decision_columns = [
        "factor_id",
        "selection_decision",
        "highest_selected_correlation",
        "most_similar_selected_factor",
    ]
    if candidates.empty:
        selected_frame = candidates.copy()
        selected_frame.insert(
            0, "selection_rank", pd.Series(dtype="int64")
        )
        selected_frame["highest_selected_correlation"] = pd.Series(dtype="float64")
        selected_frame["most_similar_selected_factor"] = pd.Series(dtype="object")
        return selected_frame, pd.DataFrame(columns=decision_columns)

    if return_correlation is None:
        cutoff = pd.Period(as_of_month, freq="M") - 23
        history = panel[
            panel["month"].between(str(cutoff), as_of_month)
            & panel["factor_id"].isin(set(candidates["factor_id"].astype(str)))
        ]
        return_wide = history.pivot(
            index="month", columns="factor_id", values="monthly_return"
        )
        return_correlation = return_wide.corr(min_periods=12)

    correlation_ids = return_correlation.index.astype(str).tolist()
    correlation_positions = {
        factor_id: position for position, factor_id in enumerate(correlation_ids)
    }
    correlation_column_positions = {
        factor_id: position
        for position, factor_id in enumerate(return_correlation.columns.astype(str))
    }
    correlation_values = return_correlation.to_numpy(dtype=float, copy=False)

    def fastest_correlation(candidate: str, selected_ids: list[str]) -> tuple[float, str]:
        row_position = correlation_positions.get(candidate)
        if row_position is None or not selected_ids:
            return np.nan, ""
        present = [
            (factor_id, correlation_column_positions[factor_id])
            for factor_id in selected_ids
            if factor_id in correlation_column_positions
        ]
        if not present:
            return np.nan, ""
        values = correlation_values[
            row_position,
            np.asarray([position for _, position in present], dtype=int),
        ]
        finite = np.isfinite(values)
        if not finite.any():
            return np.nan, ""
        finite_positions = np.flatnonzero(finite)
        best_local = int(finite_positions[np.argmax(np.abs(values[finite]))])
        return float(values[best_local]), present[best_local][0]

    selected: list[str] = []
    decisions: list[dict[str, Any]] = []
    for row in candidates.sort_values(
        score_column, ascending=False, kind="stable"
    ).itertuples(index=False):
        factor_id = str(row.factor_id)
        correlation, similar_factor = fastest_correlation(factor_id, selected)
        accepted = not (
            np.isfinite(correlation) and abs(correlation) >= similarity_limit
        )
        if accepted and len(selected) < top_k:
            selected.append(factor_id)
            reason = "入选"
        elif accepted:
            reason = "名额已满"
        else:
            reason = f"与{similar_factor}过于相似"
        decisions.append(
            {
                "factor_id": factor_id,
                "selection_decision": reason,
                "highest_selected_correlation": correlation,
                "most_similar_selected_factor": similar_factor,
            }
        )
    decision_frame = pd.DataFrame(decisions, columns=decision_columns)
    selected_frame = candidates[
        candidates["factor_id"].astype(str).isin(selected)
    ].copy()
    selected_frame = selected_frame.merge(
        decision_frame, on="factor_id", how="left"
    ).sort_values(score_column, ascending=False, kind="stable")
    selected_frame.insert(0, "selection_rank", np.arange(1, len(selected_frame) + 1))
    return selected_frame, decision_frame


def current_factor_scores(
    panel: pd.DataFrame,
    metadata: pd.DataFrame,
    duplicates: pd.DataFrame,
    final_model: ModelFitResult | None,
    feature_columns: list[str],
) -> pd.DataFrame:
    current = panel[
        panel["month"].eq(AS_OF_MONTH) & panel["is_available"]
    ].copy()
    if final_model is None:
        current["lightgbm_rank_score"] = np.nan
        current["predicted_next_month_return"] = np.nan
    else:
        current["lightgbm_rank_score"] = predict_with_model(
            final_model, current, feature_columns
        )
        current["predicted_next_month_return"] = (
            current["lightgbm_rank_score"]
            if final_model.model_kind == "regressor"
            else np.nan
        )
    current["model_percentile"] = current["lightgbm_rank_score"].rank(
        pct=True, method="average"
    )
    current = current.merge(
        duplicates,
        on="factor_id",
        how="left",
    )
    report_columns = [
        "factor_id",
        "common_ic",
        "common_icir",
        "full_ic",
        "full_icir",
        "common_composite_rank",
    ]
    report_values = metadata[report_columns].copy()
    current = current.merge(report_values, on="factor_id", how="left")
    return current


def make_preprocessing_table(
    metadata: pd.DataFrame,
    panel: pd.DataFrame,
    duplicates: pd.DataFrame,
) -> pd.DataFrame:
    current = panel[panel["month"].eq(AS_OF_MONTH)][
        [
            "factor_id",
            "history_months",
            "return_observed_months",
            "return_missing_rate",
            "selected_direction",
            "direction_source",
            "return_mean_12",
            "return_sharpe_12",
            "return_max_drawdown_12",
            "ic_mean_12",
            "ic_positive_rate_12",
        ]
    ].copy()
    table = metadata[
        [
            "factor_id",
            "factor_name",
            "data_group",
            "frequency",
            "implementation_quality",
            "status",
            "status_detail",
            "source_kind",
            "calendar_editions",
            "available_date",
            "availability_source",
            "available_from_month",
            "availability_rule",
            "full_months",
            "common_ic",
            "common_icir",
            "full_ic",
            "full_icir",
        ]
    ].copy()
    table["factor_id"] = table["factor_id"].astype(str)
    table = table.merge(current, on="factor_id", how="left")
    table = table.merge(duplicates, on="factor_id", how="left")

    results: list[str] = []
    notes: list[str] = []
    for row in table.itertuples(index=False):
        if str(row.status) != "可排名":
            result = "排除"
            note = str(row.status_detail)
        elif not bool(row.is_representative):
            result = "重复项合并"
            note = f"保留{row.representative_factor_id}"
        elif str(row.available_from_month) > AS_OF_MONTH:
            result = "尚未可用"
            note = f"日历发布日期为{row.available_date}"
        elif str(row.data_group) == "Level2":
            result = "短期观察"
            note = "只有四个共同月份，不参加长期检验"
        elif str(row.implementation_quality) == "trial":
            result = "试算候选"
            note = "计算方法为试算，单独列出"
        else:
            result = "正式候选"
            note = f"从{row.available_from_month}起可参加当时的选择"
        results.append(result)
        notes.append(note)
    table["processing_result"] = results
    table["processing_note"] = notes
    return table.sort_values(
        ["processing_result", "data_group", "factor_id"], kind="stable"
    )


def make_availability_audit(
    metadata: pd.DataFrame,
    panel: pd.DataFrame,
) -> pd.DataFrame:
    counts = (
        panel.assign(pre_availability=~panel["is_available"])
        .groupby("factor_id", as_index=False)
        .agg(
            panel_first_month=("month", "min"),
            panel_last_month=("month", "max"),
            pre_availability_rows_excluded=("pre_availability", "sum"),
        )
    )
    columns = [
        "factor_id",
        "factor_name",
        "data_group",
        "implementation_quality",
        "source_kind",
        "calendar_editions",
        "available_date",
        "availability_source",
        "available_from_month",
        "availability_rule",
    ]
    result = metadata[columns].copy().merge(counts, on="factor_id", how="left")
    result["first_eligible_target_month"] = (
        pd.PeriodIndex(result["available_from_month"], freq="M") + 1
    ).astype(str)
    return result.sort_values(["available_from_month", "factor_id"], kind="stable")


def feature_importance_table(
    fitted: ModelFitResult,
    feature_columns: list[str],
) -> pd.DataFrame:
    booster = fitted.model.booster_
    gain = booster.feature_importance(importance_type="gain")
    split = booster.feature_importance(importance_type="split")
    total_gain = float(gain.sum())
    table = pd.DataFrame(
        {
            "feature": feature_columns,
            "gain_importance": gain,
            "split_count": split,
        }
    )
    table["gain_share"] = (
        table["gain_importance"] / total_gain if total_gain > 0 else 0.0
    )
    return table.sort_values("gain_importance", ascending=False, kind="stable")


def monthly_comparison_table(
    model_monthly: pd.DataFrame,
    baseline_monthly: pd.DataFrame,
) -> pd.DataFrame:
    model = model_monthly.rename(
        columns={
            "top_return": "lightgbm_top_return",
            "top_excess_return": "lightgbm_excess_return",
            "rank_ic": "lightgbm_rank_ic",
            "selected_factors": "lightgbm_selected_factors",
        }
    )[
        [
            "month",
            "candidate_count",
            "lightgbm_rank_ic",
            "lightgbm_top_return",
            "all_factor_return",
            "lightgbm_excess_return",
            "lightgbm_selected_factors",
        ]
    ]
    baseline = baseline_monthly.rename(
        columns={
            "top_return": "trailing_12m_top_return",
            "top_excess_return": "trailing_12m_excess_return",
            "rank_ic": "trailing_12m_rank_ic",
            "selected_factors": "trailing_12m_selected_factors",
        }
    )[
        [
            "month",
            "trailing_12m_rank_ic",
            "trailing_12m_top_return",
            "trailing_12m_excess_return",
            "trailing_12m_selected_factors",
        ]
    ]
    result = model.merge(baseline, on="month", how="left")
    result["lightgbm_nav"] = (
        1.0 + result["lightgbm_top_return"].fillna(0.0)
    ).cumprod()
    result["trailing_12m_nav"] = (
        1.0 + result["trailing_12m_top_return"].fillna(0.0)
    ).cumprod()
    result["all_factor_nav"] = (
        1.0 + result["all_factor_return"].fillna(0.0)
    ).cumprod()
    return result


def metric_rows(
    model_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
) -> pd.DataFrame:
    names = sorted(set(model_metrics) | set(baseline_metrics))
    return pd.DataFrame(
        [
            {
                "metric": name,
                "lightgbm": model_metrics.get(name),
                "trailing_12m_baseline": baseline_metrics.get(name),
            }
            for name in names
        ]
    )


def lightgbm_passes_method_comparison(
    model_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    model_error: str = "",
) -> bool:
    return bool(
        not model_error
        and model_metrics.get("top_excess_mean", -np.inf) > 0
        and model_metrics.get("rank_ic_mean", -np.inf) > 0
        and model_metrics.get("top_excess_mean", -np.inf)
        >= baseline_metrics.get("top_excess_mean", np.inf)
    )


def format_percent(value: object) -> str:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return "—" if not np.isfinite(numeric) else f"{float(numeric):.2%}"


def build_markdown_report(
    preprocessing: pd.DataFrame,
    selected: pd.DataFrame,
    trial_candidates: pd.DataFrame,
    level2_watchlist: pd.DataFrame,
    validation_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    baseline_test_metrics: dict[str, Any],
    best_parameters: dict[str, Any],
    best_iteration: int,
    best_feature_set: str,
    feature_count: int,
    selection_method: str,
    lightgbm_passed: bool,
    top_k: int,
    model_error: str,
    non_level2_only: bool,
) -> str:
    counts = preprocessing["processing_result"].value_counts().to_dict()
    report_title = (
        "# 非Level2因子 LightGBM排名与滚动检查报告"
        if non_level2_only
        else "# LightGBM排名与滚动检查报告"
    )
    if non_level2_only:
        factor_summary = (
            f"数据截至{AS_OF_MONTH}。共检查{len(preprocessing)}个非Level2因子："
            f"正式候选{counts.get('正式候选', 0)}个、试算候选"
            f"{counts.get('试算候选', 0)}个、尚未发布{counts.get('尚未可用', 0)}个。"
        )
        scope_note = "Level2收益文件没有读取，后续计算也没有使用Level2因子。"
        level2_limit = "- 本次没有读取或使用Level2数据。"
        closing_note = f"另列试算候选{len(trial_candidates)}个，不进入正式名单。"
    else:
        factor_summary = f"数据截至{AS_OF_MONTH}，共检查{len(preprocessing)}个因子。"
        scope_note = ""
        level2_limit = "- Level2历史月份较少，只列观察名单。"
        closing_note = f"Level2观察名单共{len(level2_watchlist)}个。"

    if model_error:
        model_decision = f"LightGBM运行失败（{model_error}），正式名单改用简单规则。"
    elif lightgbm_passed:
        model_decision = "LightGBM在两段滚动检查和后段比较中均通过，正式名单采用模型名次。"
    else:
        model_decision = (
            "LightGBM没有同时通过两段滚动检查和后段比较，正式名单采用表现更好的"
            "过去12个月平均收益规则。"
        )

    lines = [
        report_title,
        "",
        factor_summary,
        scope_note,
        "",
        "## 结论",
        "",
        f"最终选出{len(selected)}个正式因子，采用“{selection_method}”。"
        "所有输入只使用月末收盘后已经实现的IC和收益。",
        "",
        f"{TEST_START}至{TEST_END}：LightGBM前{top_k}个月均收益"
        f"{format_percent(test_metrics.get('top_return_mean'))}，月均超出全部候选"
        f"{format_percent(test_metrics.get('top_excess_mean'))}，累计收益"
        f"{format_percent(test_metrics.get('top_cumulative_return'))}。",
        "",
        f"同一时期，过去12个月规则月均超出全部候选"
        f"{format_percent(baseline_test_metrics.get('top_excess_mean'))}，累计收益"
        f"{format_percent(baseline_test_metrics.get('top_cumulative_return'))}。",
        "",
        model_decision,
        "",
        "## 正式入选",
        "",
        "|排名|因子ID|名称|发布日期|排序分数|模型名次百分位|近12月平均收益|近12月定向IC|近12月最大回撤|",
        "|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in selected.head(30).itertuples(index=False):
        lines.append(
            f"|{row.selection_rank}|{row.factor_id}|{row.factor_name}|{row.available_date}|"
            f"{format_percent(row.selection_score)}|{format_percent(row.model_percentile)}|"
            f"{format_percent(row.return_mean_12)}|{format_percent(row.ic_mean_12)}|"
            f"{format_percent(row.return_max_drawdown_12)}|"
        )

    lines.extend(
        [
            "",
            "## 时间检查",
            "",
            "- 第一段：训练到2023-12，检查2024-01至2024-06。",
            "- 第二段：扩展训练到2024-06，检查2024-07至2024-12。",
            f"- 方法比较：{TEST_START}至{TEST_END}。这段结果参与方法选择，"
            "不是最终做法的一段全新检验。",
            f"- 当前评分：使用截至{AS_OF_MONTH}月末已经知道的数据，为下一月排序。",
            "",
            "## 数据处理",
            "",
            f"- 排除截至{AS_OF_MONTH}尚未发布的{counts.get('尚未可用', 0)}个因子。",
            "- 历史表现按退出月份记录，确保使用时收益已经实现。",
            "- 预测目标按建仓月份记录，避免把选择前已经开仓的交易算成新机会。",
            "- 数值完全相同的计算项只保留一个代表项。",
            "- 每项输入只按训练期的1%和99%位置限制极端值。",
            "- 缺失值直接交给LightGBM处理，不用未来月份补填。",
            f"- 正式名单剔除近24个月收益相关系数绝对值达到{SIMILARITY_LIMIT:.2f}的相似项。",
            "",
            "## 模型",
            "",
            f"比较16组排名设置，包括lambdarank和rank_xendcg。使用“{best_feature_set}”"
            f"中的{feature_count}项历史指标。",
            f"最佳设置：`{json.dumps(best_parameters, ensure_ascii=False, sort_keys=True)}`，"
            f"树的轮数为{best_iteration}。",
            f"两段滚动检查的平均名次相关为{validation_metrics.get('rank_ic_mean', np.nan):.4f}，"
            f"月均超出全部候选{format_percent(validation_metrics.get('top_excess_mean'))}；"
            f"后段比较名次相关为{test_metrics.get('rank_ic_mean', np.nan):.4f}。",
            "" if lightgbm_passed else "结果前后不稳定，模型只保留为检查记录，不作正式推荐。",
            "",
            "## 限制",
            "",
            "- 这是因子历史表现筛选，不是逐只股票的选股模型。",
            level2_limit,
            "- 多空收益未计实际卖空限制和融券费用。",
            "- LightGBM输出是相对名次分数，不是收益率或收益保证。",
            "",
            closing_note,
        ]
    )
    return "\n".join(lines) + "\n"


def save_summary_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def run_analysis(
    output_dir: Path,
    top_k: int = MODEL_TOP_K,
    non_level2_only: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = add_factor_availability(read_csv(REPORT_FACTOR_FILE))
    metadata["factor_id"] = metadata["factor_id"].astype(str)
    raw_monthly_ic = read_csv(REPORT_MONTHLY_IC_FILE)
    if non_level2_only:
        metadata, raw_monthly_ic = restrict_to_non_level2(
            metadata,
            raw_monthly_ic,
        )
    monthly_ic = complete_monthly_ic(raw_monthly_ic)
    return_helper = load_return_helper()

    (
        non_l2_returns,
        non_l2_targets,
        non_l2_directions,
        timing_audit,
    ) = load_non_level2_monthly_returns(return_helper)
    load_seconds = time.perf_counter() - started
    if non_level2_only:
        level2_returns = non_l2_returns.iloc[0:0].copy()
        level2_directions = non_l2_directions.iloc[0:0].copy()
    else:
        level2_returns, level2_directions = load_level2_monthly_returns(
            return_helper, monthly_ic
        )
    panel = build_feature_panel(
        metadata,
        monthly_ic,
        non_l2_returns,
        non_l2_directions,
        level2_returns,
        level2_directions,
        non_l2_targets,
    )
    panel_seconds = time.perf_counter() - started - load_seconds

    current_duplicates = assign_duplicate_groups(panel, metadata, AS_OF_MONTH)
    representative_ids = set(
        current_duplicates.loc[
            current_duplicates["is_representative"], "factor_id"
        ].astype(str)
    )

    model_rows = panel[
        panel["data_group"].eq("非Level2")
        & panel["factor_id"].isin(representative_ids)
        & panel["status"].eq("可排名")
        & panel["quality_formal"].eq(1)
        & panel["is_available"]
        & panel["target_is_next_month"].eq(1)
        & panel["target_return"].notna()
        & panel["history_months"].ge(3)
        & panel["target_month"].le(LAST_COMPLETE_TARGET_MONTH)
    ].copy()
    train = model_rows[model_rows["target_month"].le(TRAIN_END)].copy()
    validation = model_rows[
        model_rows["target_month"].between(VALID_START, VALID_END)
    ].copy()
    test = model_rows[
        model_rows["target_month"].between(TEST_START, TEST_END)
    ].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise RuntimeError(
            f"时间分段后样本为空：train={len(train)}, validation={len(validation)}, test={len(test)}"
        )

    score_columns = [
        "factor_id",
        "month",
        "target_month",
        "target_return",
        "return_mean_12",
        "return_positive_rate_12",
        "return_max_drawdown_12",
        "ic_mean_12",
        "history_months",
        "quality_formal",
    ]
    test_scored = test[score_columns].copy()
    test_scored["trailing_12m_prediction"] = pd.to_numeric(
        test_scored["return_mean_12"], errors="coerce"
    )
    formal_test_scored = test_scored[test_scored["quality_formal"].eq(1)].copy()
    validation_correlation_cache: dict[str, pd.DataFrame] = {}
    test_correlation_cache: dict[str, pd.DataFrame] = {}
    baseline_test_metrics, baseline_test_monthly = evaluate_selection_process(
        formal_test_scored,
        panel,
        "trailing_12m_prediction",
        top_k,
        test_correlation_cache,
    )

    model_started = time.perf_counter()
    model_error = ""
    validation_metrics: dict[str, Any] = {}
    test_metrics: dict[str, Any] = {}
    test_monthly = pd.DataFrame()
    parameter_search = pd.DataFrame()
    walkforward_folds = pd.DataFrame()
    selected_parameters: dict[str, Any] = {}
    selected_iteration = 0
    selected_feature_set = "仅近期滚动指标"
    selected_feature_columns = model_feature_sets()[selected_feature_set]
    walkforward_passed = False
    final_model: ModelFitResult | None = None
    try:
        (
            tuned,
            parameter_search,
            walkforward_folds,
            walkforward_passed,
        ) = tune_ranker_walkforward(
            model_rows,
            panel,
            selected_feature_columns,
            top_k,
        )
        parameter_search.insert(1, "feature_set", selected_feature_set)
        parameter_search.insert(2, "feature_count", len(selected_feature_columns))
        selected_parameters = tuned.parameters
        selected_iteration = tuned.best_iteration
        validation_metrics = {
            "folds": int(len(walkforward_folds)),
            "all_folds_passed": walkforward_passed,
            "rank_ic_mean": float(walkforward_folds["rank_ic_mean"].mean()),
            "top_return_mean": float(walkforward_folds["top_return_mean"].mean()),
            "all_factor_return_mean": float(
                walkforward_folds["all_factor_return_mean"].mean()
            ),
            "top_excess_mean": float(
                walkforward_folds["top_excess_mean"].mean()
            ),
        }
        test_scored["lightgbm_prediction"] = predict_with_model(
            tuned, test, selected_feature_columns
        )
        test_metrics, test_monthly = evaluate_selection_process(
            test_scored,
            panel,
            "lightgbm_prediction",
            top_k,
            test_correlation_cache,
            require_positive_score=False,
        )

        final_fit_source = model_rows[
            model_rows["target_month"].le(TEST_END)
        ].copy()
        final_model = fit_fixed_ranker(
            final_fit_source,
            selected_feature_columns,
            tuned.parameters,
            tuned.best_iteration,
            top_k,
        )
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
        test_scored["lightgbm_prediction"] = np.nan
    model_seconds = time.perf_counter() - model_started

    if test_monthly.empty:
        test_monthly = baseline_test_monthly[
            ["month", "candidate_count", "all_factor_return"]
        ].copy()
        test_monthly["rank_ic"] = np.nan
        test_monthly["top_return"] = np.nan
        test_monthly["top_excess_return"] = np.nan
        test_monthly["selected_factors"] = ""
    monthly_comparison = monthly_comparison_table(
        test_monthly,
        baseline_test_monthly,
    )

    current_scores = current_factor_scores(
        panel,
        metadata,
        current_duplicates,
        final_model,
        selected_feature_columns,
    )

    lightgbm_passed = bool(
        walkforward_passed
        and lightgbm_passes_method_comparison(
            test_metrics,
            baseline_test_metrics,
            model_error,
        )
    )
    selection_method = (
        "LightGBM因子名次" if lightgbm_passed else "过去12个月平均收益"
    )
    current_scores["selection_method"] = selection_method
    current_scores["selection_score"] = (
        current_scores["model_percentile"]
        if lightgbm_passed
        else current_scores["return_mean_12"]
    )

    formal_base = current_scores[
        current_scores["data_group"].eq("非Level2")
        & current_scores["status"].eq("可排名")
        & current_scores["is_representative"].fillna(False)
        & current_scores["implementation_quality"].isin(["strict", "正式复测"])
        & current_scores["history_months"].ge(24)
    ].copy()
    filtered_formal = filter_formal_candidates(
        formal_base,
        "selection_score",
        top_k,
        require_positive_score=not lightgbm_passed,
    )
    selected, formal_decisions = select_distinct_factors(
        filtered_formal,
        panel,
        top_k,
        AS_OF_MONTH,
        SIMILARITY_LIMIT,
        "selection_score",
    )

    if model_error:
        lightgbm_candidates = formal_base.iloc[0:0].copy()
    else:
        lightgbm_candidates = formal_base.sort_values(
            "model_percentile", ascending=False, kind="stable"
        ).head(top_k)
    lightgbm_candidates.insert(
        0, "model_rank", np.arange(1, len(lightgbm_candidates) + 1)
    )
    lightgbm_candidates["recommendation_status"] = (
        "通过后段比较"
        if lightgbm_passed
        else (
            "模型运行失败，不作推荐"
            if model_error
            else "未通过后段比较，不作推荐"
        )
    )

    trial_candidates = current_scores[
        current_scores["data_group"].eq("非Level2")
        & current_scores["status"].eq("可排名")
        & current_scores["is_representative"].fillna(False)
        & current_scores["implementation_quality"].eq("trial")
    ].copy()
    trial_candidates = trial_candidates.sort_values(
        "selection_score", ascending=False, kind="stable"
    ).head(15)
    trial_candidates.insert(
        0, "candidate_rank", np.arange(1, len(trial_candidates) + 1)
    )

    level2_watchlist = current_scores[
        current_scores["data_group"].eq("Level2")
        & current_scores["status"].eq("可排名")
        & current_scores["is_representative"].fillna(False)
        & current_scores["history_months"].ge(3)
    ].copy()
    level2_watchlist["short_term_score"] = (
        level2_watchlist["return_mean_3"].rank(pct=True).fillna(0.0) * 0.5
        + level2_watchlist["ic_mean_3"].rank(pct=True).fillna(0.0) * 0.5
    )
    level2_watchlist = level2_watchlist.sort_values(
        "short_term_score", ascending=False, kind="stable"
    ).head(20)
    level2_watchlist.insert(
        0, "watch_rank", np.arange(1, len(level2_watchlist) + 1)
    )
    level2_watchlist["evidence_level"] = "短期观察，不参加长期检验"

    preprocessing = make_preprocessing_table(
        metadata, panel, current_duplicates
    )
    availability_audit = make_availability_audit(metadata, panel)
    label_frame = model_rows[["target_month", "target_return"]].copy()
    label_frame["rank_label"] = make_rank_labels(model_rows)
    label_distribution = (
        label_frame.groupby(["target_month", "rank_label"])
        .size()
        .unstack(fill_value=0)
        .rename(columns=lambda value: f"label_{int(value)}")
        .reset_index()
    )
    label_distribution["candidate_count"] = label_distribution.filter(
        like="label_"
    ).sum(axis=1)
    if final_model is None:
        importance = pd.DataFrame(
            columns=["feature", "gain_importance", "split_count", "gain_share"]
        )
        bounds = pd.DataFrame(
            columns=["feature", "lower_1pct", "upper_99pct", "training_missing_rate"]
        )
    else:
        importance = feature_importance_table(
            final_model, selected_feature_columns
        )
        bounds = final_model.feature_bounds.copy()
    metrics = metric_rows(test_metrics, baseline_test_metrics)

    selected_columns = [
        "selection_rank",
        "factor_id",
        "factor_name",
        "frequency",
        "implementation_quality",
        "selected_direction",
        "selection_method",
        "selection_score",
        "available_date",
        "availability_source",
        "lightgbm_rank_score",
        "predicted_next_month_return",
        "model_percentile",
        "return_mean_12",
        "return_sharpe_12",
        "return_positive_rate_12",
        "return_max_drawdown_12",
        "ic_mean_12",
        "ic_positive_rate_12",
        "pre2021_ic",
        "common_ic",
        "common_icir",
        "full_ic",
        "full_icir",
        "highest_selected_correlation",
        "most_similar_selected_factor",
    ]
    selected = selected[selected_columns]

    lightgbm_candidate_columns = [
        "model_rank",
        "factor_id",
        "factor_name",
        "frequency",
        "implementation_quality",
        "available_date",
        "availability_source",
        "lightgbm_rank_score",
        "predicted_next_month_return",
        "model_percentile",
        "return_mean_12",
        "return_sharpe_12",
        "return_positive_rate_12",
        "return_max_drawdown_12",
        "ic_mean_12",
        "common_ic",
        "common_icir",
        "recommendation_status",
    ]
    lightgbm_candidates = lightgbm_candidates[lightgbm_candidate_columns]

    trial_candidate_columns = [
        "candidate_rank",
        "factor_id",
        "factor_name",
        "frequency",
        "selection_method",
        "selection_score",
        "available_date",
        "availability_source",
        "lightgbm_rank_score",
        "predicted_next_month_return",
        "return_mean_12",
        "return_sharpe_12",
        "return_positive_rate_12",
        "return_max_drawdown_12",
        "ic_mean_12",
        "common_ic",
        "common_icir",
    ]
    trial_candidates = trial_candidates[trial_candidate_columns]

    level2_watch_columns = [
        "watch_rank",
        "factor_id",
        "factor_name",
        "frequency",
        "implementation_quality",
        "history_months",
        "return_observed_months",
        "short_term_score",
        "return_mean_3",
        "return_sharpe_3",
        "return_positive_rate_3",
        "return_max_drawdown_3",
        "ic_mean_3",
        "ic_positive_rate_3",
        "predicted_next_month_return",
        "common_ic",
        "common_icir",
        "evidence_level",
    ]
    level2_watchlist = level2_watchlist[level2_watch_columns]

    write_csv(preprocessing, output_dir / "factor_preprocessing.csv")
    write_csv(availability_audit, output_dir / "factor_availability.csv")
    write_csv(timing_audit, output_dir / "return_timing_audit.csv")
    write_csv(label_distribution, output_dir / "ranking_label_distribution.csv")
    write_csv(selected, output_dir / "selected_factors.csv")
    write_csv(lightgbm_candidates, output_dir / "lightgbm_candidates.csv")
    write_csv(trial_candidates, output_dir / "trial_candidates.csv")
    if not non_level2_only:
        write_csv(level2_watchlist, output_dir / "level2_watchlist.csv")
    write_csv(parameter_search, output_dir / "parameter_search.csv")
    write_csv(walkforward_folds, output_dir / "walkforward_folds.csv")
    write_csv(importance, output_dir / "feature_importance.csv")
    write_csv(bounds, output_dir / "feature_bounds.csv")
    write_csv(metrics, output_dir / "test_metrics.csv")
    write_csv(monthly_comparison, output_dir / "test_monthly_performance.csv")
    write_csv(formal_decisions, output_dir / "selection_decisions.csv")
    run_checks = pd.DataFrame(
        [
            {
                "check_item": "模型行早于因子可用月份",
                "error_count": int((~model_rows["is_available"]).sum()),
            },
            {
                "check_item": "目标晚于最后完整建仓月",
                "error_count": int(
                    model_rows["target_month"].gt(LAST_COMPLETE_TARGET_MONTH).sum()
                ),
            },
            {
                "check_item": "当前评分包含尚未发布因子",
                "error_count": int(
                    current_scores["available_from_month"].gt(AS_OF_MONTH).sum()
                ),
            },
            {
                "check_item": "排名标签越界",
                "error_count": int(
                    (~label_frame["rank_label"].between(0, 4)).sum()
                ),
            },
        ]
    )
    write_csv(run_checks, output_dir / "run_checks.csv")
    runtime = pd.DataFrame(
        [
            ("读取并整理数据", load_seconds),
            ("生成月度输入", panel_seconds),
            ("模型选择、训练和检查", model_seconds),
            ("总耗时", time.perf_counter() - started),
        ],
        columns=["stage", "seconds"],
    )
    write_csv(runtime, output_dir / "runtime_metrics.csv")
    # LightGBM在Windows下不能直接写入含中文的路径，先转成文本再保存。
    if final_model is None:
        model_text = f"模型未生成：{model_error}"
    else:
        model_text = final_model.model.booster_.model_to_string(
            num_iteration=final_model.best_iteration
        )
    (output_dir / "lightgbm_model.txt").write_text(model_text, encoding="utf-8")

    report = build_markdown_report(
        preprocessing,
        selected,
        trial_candidates,
        level2_watchlist,
        validation_metrics,
        test_metrics,
        baseline_test_metrics,
        selected_parameters,
        selected_iteration,
        selected_feature_set,
        len(selected_feature_columns),
        selection_method,
        lightgbm_passed,
        top_k,
        model_error,
        non_level2_only,
    )
    (output_dir / "factor_selection_report.md").write_text(report, encoding="utf-8")

    payload = {
        "source_report": str(
            ROOT
            / "outputs"
            / "019fa45e-0ee2-7991-8e24-165b4d6d1f91"
            / "全部因子_IC_ICIR_收益回撤排序大表_含Level2_截至20260716.xlsx"
        ),
        "as_of_month": AS_OF_MONTH,
        "last_complete_target_month": LAST_COMPLETE_TARGET_MONTH,
        "scope": {
            "non_level2_only": non_level2_only,
            "description": (
                "只处理和使用非Level2因子"
                if non_level2_only
                else "非Level2正式筛选并保留Level2短期观察"
            ),
        },
        "factor_counts": {
            "report_total": int(len(metadata)),
            "report_eligible": int(metadata["status"].eq("可排名").sum()),
            "non_level2": int(metadata["data_group"].eq("非Level2").sum()),
            "level2": int(metadata["data_group"].eq("Level2").sum()),
            "selected_formal": int(len(selected)),
            "trial_candidates": int(len(trial_candidates)),
            "level2_watchlist": int(len(level2_watchlist)),
            "available_by_as_of": int(
                metadata["available_from_month"].le(AS_OF_MONTH).sum()
            ),
        },
        "time_split": {
            "train_end": TRAIN_END,
            "validation": [VALID_START, VALID_END],
            "test": [TEST_START, TEST_END],
        },
        "sample_counts": {
            "train": int(len(train)),
            "validation": int(len(validation)),
            "test": int(len(test)),
        },
        "model": {
            "library": "lightgbm",
            "version": lgb.__version__,
            "kind": "LGBMRanker",
            "parameters": selected_parameters,
            "best_iteration": selected_iteration,
            "feature_set": selected_feature_set,
            "feature_set_candidates": [selected_feature_set],
            "features": selected_feature_columns,
            "random_seed": RANDOM_SEED,
            "walkforward_all_folds_passed": walkforward_passed,
            "error": model_error,
        },
        "selection": {
            "method": selection_method,
            "lightgbm_passed_method_comparison": lightgbm_passed,
            "decision_rule": (
                f"LightGBM只有同时取得正的名次相关、正的前{top_k}差额，"
                "且差额不低于完整简单规则时才采用。"
            ),
        },
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "baseline_test_metrics": baseline_test_metrics,
        "timing_policy": {
            "decision_time": "每月最后交易日收盘后",
            "historical_return_month": "按退出日期所在月，确保当时已经实现",
            "target_month": "按建仓日期所在月，评价上月末选择后的新交易",
            "monthly_ic_month": "按退出日期所在月，月末收盘后可知",
        },
        "checks": run_checks.set_index("check_item")["error_count"].to_dict(),
        "runtime_seconds": runtime.set_index("stage")["seconds"].to_dict(),
        "selected_factor_ids": selected["factor_id"].astype(str).tolist(),
        "software": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "lightgbm": lgb.__version__,
        },
        "limitations": [
            "模型使用因子层面的逐月历史表现，不是股票逐日收益模型。",
            (
                "本次没有读取Level2收益文件，后续计算也没有使用Level2因子。"
                if non_level2_only
                else "Level2历史只有约四个共同月份，只列观察名单。"
            ),
            "多空收益未计实际卖空限制和融券费用。",
            f"{TEST_START}至{TEST_END}用于选择最终方法，不是最终做法的全新检验。",
            "因子发布日期按日历记录；只有缺少具体日期的项目才按该日历年份1月1日处理。",
            "没有逐只股票的借券可用量，因此不能在本报告中模拟真实卖空成交。",
        ],
    }
    save_summary_json(output_dir / "analysis_summary.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据回测报告用LightGBM挑选下一月更值得优先使用的因子"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="结果目录；不填写时按分析范围选择默认目录",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=MODEL_TOP_K,
        help="正式入选数量",
    )
    parser.add_argument(
        "--non-level2-only",
        action="store_true",
        help="后续处理和计算只使用非Level2因子",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_k <= 0:
        raise ValueError("top-k必须大于0")
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = (
            NON_LEVEL2_OUTPUT_DIR
            if args.non_level2_only
            else DEFAULT_OUTPUT_DIR
        )
    summary = run_analysis(
        output_dir.resolve(),
        args.top_k,
        args.non_level2_only,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
