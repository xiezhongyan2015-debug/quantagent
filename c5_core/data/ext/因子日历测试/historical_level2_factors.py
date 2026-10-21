# -*- coding: utf-8 -*-
"""把 Level2 日度基础表转换为历史与 2026 因子长表。

本文件只处理已经整理成“每个交易日、每只股票一行”的日度基础表。
它不会查找原始逐笔文件，也不会启动回测。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from scipy.stats import rankdata


BASE = Path(__file__).resolve().parent
DEFAULT_CATALOG = BASE / "combined_level2_factor_catalog.csv"

# 原文没有写死的参数集中放在这里，使用这些参数的结果都标为 trial。
ROLLING_DAYS = 20
OPENING_DAYS = 20
OCVP_ALPHA = 0.5
IDEAL_REVERSAL_GROUP_DAYS = 10
ACTIVE_SPLIT_TAIL_FRACTION = 0.30
GAME_DAYS = 21
INSTITUTION_ORDER_AMOUNT = 1_000_000.0
SMALL_ORDER_AMOUNT = 40_000.0
EPS = 1e-12

TRIAL_PARAMETERS = {
    "rolling_days": ROLLING_DAYS,
    "opening_days": OPENING_DAYS,
    "ocvp_alpha": OCVP_ALPHA,
    "ideal_reversal_group_days": IDEAL_REVERSAL_GROUP_DAYS,
    "active_split_tail_fraction": ACTIVE_SPLIT_TAIL_FRACTION,
    "game_days": GAME_DAYS,
    "institution_order_amount": INSTITUTION_ORDER_AMOUNT,
    "small_order_amount": SMALL_ORDER_AMOUNT,
}

# 调用方可以据此检查日度基础表；并非每个因子都需要全部字段。
DAILY_BASE_COLUMNS = (
    "date", "code", "close", "open", "daily_return", "float_shares", "market_return",
    "trade_amount", "trade_volume", "trade_count", "buy_amount", "sell_amount",
    "buy_volume", "sell_volume", "buy_count", "sell_count", "open_auction_volume",
    "close_5m_volume", "active_buy_share", "open_net_buy_share", "open_net_buy_strength",
    "avg_trade_amount", "trade_amount_q10_position", "avg_trade_volume_diff_std",
    "mte_daily", "mts_daily", "avg_trade_volume_return_corr", "large_order_push_return",
    "large_net_inflow_rate", "avg_outflow_trade_amount_ratio", "high_volume_avg_trade_amount",
    "high_volume_sell_avg_trade_amount", "pv_divergence_avg_trade_amount",
    "pv_divergence_sell_avg_trade_amount", "down_avg_trade_amount", "price_impact_bias",
    "informed_buy_share", "informed_sell_share", "dpin", "small_dpin", "large_dpin",
    "herding", "siphon_effect", "active_buy_singularity", "residual_noniso_imbalance",
    "small_buy_amount_40k", "small_sell_amount_40k", "small_active_buy_amount_50k",
    "large_buy_amount", "large_sell_amount", "super_buy_amount", "super_sell_amount",
    "ordinary_large_buy_amount", "ordinary_large_sell_amount", "active_large_buy_amount",
    "active_large_sell_amount", "open_large_net_buy_share", "open_large_net_buy_strength",
    "buy_order_hhi", "active_flexible_chip", "profit_adjusted_active_flexible_chip",
    "open_adjusted_active_flexible_chip", "super_order_price_move",
    "big_order_price_move_ex_super", "long_big_order_composite",
    "small_sell_rebound_deviation", "buy_floating_loss_share", "buy_floating_loss_bias",
    "sell_rebound_share", "sell_rebound_deviation", "corrected_net_inflow", "retail_fear",
    "close_spread", "spread_mean", "midprice_change", "midprice_change_skew",
    "midprice_change_max", "five_level_soir", "mofi", "bidask_price_ratio",
    "multilevel_order_slope", "ask_depth", "bid_depth", "average_depth", "effective_depth",
    "net_bid_change_rate", "order_trade_corr", "market_price_bias", "marginal_trading_cost",
    "liq_premium_equal", "liq_premium_weighted", "game_factor", "prsi", "order_imbalance",
    "order_imbalance_rate", "open_net_bid_share", "open_buy_willingness_share",
    "open_buy_willingness_strength", "pin", "toxic_liquidity", "vpin", "vwpin",
    "retail_bsi", "small_flow", "large_flow", "super_flow",
)

VALUE_COLUMNS = [
    "factor_id",
    "date",
    "code",
    "value",
    "frequency",
    "expected_direction",
    "direction_source",
    "implementation_quality",
    "method_note",
]

STATUS_COLUMNS = [
    "factor_id",
    "name",
    "source_scope",
    "frequency",
    "expected_direction",
    "direction_source",
    "status",
    "implementation_quality",
    "method_note",
    "missing_columns",
    "row_count",
    "first_date",
    "last_date",
]


class MissingColumnsError(KeyError):
    """某一个因子缺少计算字段。"""

    def __init__(self, columns: Sequence[str]):
        self.columns = tuple(dict.fromkeys(str(column) for column in columns))
        super().__init__("、".join(self.columns))


@dataclass(frozen=True)
class FactorResult:
    """单个因子的计算结果。"""

    value: pd.Series | None
    quality: str
    note: str
    missing_columns: tuple[str, ...] = ()


def _first_text(frame: pd.DataFrame, columns: Sequence[str]) -> str:
    for column in columns:
        if column not in frame:
            continue
        for value in frame[column].tolist():
            text = "" if pd.isna(value) else str(value).strip()
            if text:
                return text
    return ""


def load_factor_catalog(path: str | Path = DEFAULT_CATALOG) -> pd.DataFrame:
    """读取目录；保留文本原样，避免编号被自动改成数字。"""

    catalog = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    if catalog.empty:
        raise ValueError("因子目录为空。")
    return catalog


def _normalize_frequency(text: str, factor_id: str) -> str:
    suffix = factor_id.rsplit("__", 1)[-1].lower() if "__" in factor_id else ""
    if suffix == "monthly":
        return "monthly"
    if suffix == "daily":
        return "daily"
    lowered = text.lower()
    if "月" in text or "monthly" in lowered or "month" in lowered:
        return "monthly"
    return "daily"


def prepare_catalog_targets(catalog: pd.DataFrame) -> pd.DataFrame:
    """把历史重复记录合成 77 个计算项，并保留 69 个 2026 计算项。"""

    work = catalog.copy()
    work.columns = [str(column).strip() for column in work.columns]
    for column in work.columns:
        work[column] = work[column].fillna("").astype(str).str.strip()

    if "source_scope" not in work:
        work["source_scope"] = "custom"

    target_ids: list[str] = []
    for _, row in work.iterrows():
        scope = row.get("source_scope", "")
        if scope == "historical":
            candidates = ("unit_id", "code_or_unit", "factor_id", "source_record_id")
        elif scope == "current_2026":
            candidates = ("factor_code", "code_or_unit", "factor_id", "source_record_id")
        else:
            candidates = ("factor_id", "code_or_unit", "factor_code", "unit_id", "source_record_id")
        target_ids.append(next((row.get(column, "") for column in candidates if row.get(column, "")), ""))
    work["_factor_id"] = target_ids
    if (work["_factor_id"] == "").any():
        raise ValueError("因子目录存在没有目标编号的记录。")

    targets: list[dict[str, str]] = []
    for factor_id, group in work.groupby("_factor_id", sort=False):
        frequency_text = "；".join(
            text
            for text in (
                _first_text(group, ("signal_frequency", "frequency")),
                _first_text(group, ("source_frequency",)),
            )
            if text
        )
        targets.append(
            {
                "factor_id": factor_id,
                "name": _first_text(group, ("name", "factor_name")),
                "source_scope": _first_text(group, ("source_scope",)) or "custom",
                "formula_key": _first_text(group, ("formula_key",)),
                "formula": _first_text(group, ("formula", "formula_text")),
                "implementation_ref": _first_text(
                    group, ("historical_implementation_ref", "implementation_ref", "factor_code")
                ),
                "frequency": _normalize_frequency(frequency_text, factor_id),
                "expected_direction": _first_text(group, ("direction", "expected_direction"))
                or "未说明",
                "direction_source": "catalog:direction",
            }
        )
    return pd.DataFrame(targets)


def read_daily_base(path: str | Path) -> pd.DataFrame:
    """读取调用方指定的日度基础表。"""

    source = Path(path)
    if source.is_dir():
        files = sorted({*source.rglob("*.parquet"), *source.rglob("*.pq")})
        if not files:
            raise FileNotFoundError(f"目录中没有 Parquet 日度基础表：{source}")
        return pd.concat((pd.read_parquet(file) for file in files), ignore_index=True)
    if source.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(source)
    return pd.read_parquet(source)


def prepare_daily_base(base: pd.DataFrame) -> pd.DataFrame:
    """检查主键并按股票、日期排序。"""

    missing = [column for column in ("date", "code") if column not in base]
    if missing:
        raise ValueError(f"日度基础表缺少主键字段：{'、'.join(missing)}")
    result = base.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    if result["date"].isna().any():
        raise ValueError("日度基础表存在无法识别的日期。")
    result["code"] = result["code"].astype(str).str.strip()
    if (result["code"] == "").any():
        raise ValueError("日度基础表存在空股票代码。")
    if result.duplicated(["date", "code"]).any():
        raise ValueError("日度基础表的 date+code 不是唯一记录。")
    return result.sort_values(["code", "date"], kind="mergesort").reset_index(drop=True)


class DailyFactorEngine:
    """在同一张日度基础表上重复使用已算出的日序列。"""

    def __init__(self, data: pd.DataFrame):
        self.data = data
        self.codes = data["code"]
        self._groups = [np.asarray(index, dtype=np.int64) for index in data.groupby("code", sort=False).indices.values()]
        self._cache: dict[str, object] = {}

    def column(self, column: str) -> pd.Series:
        if column not in self.data:
            raise MissingColumnsError((column,))
        key = f"column:{column}"
        if key not in self._cache:
            self._cache[key] = pd.to_numeric(self.data[column], errors="coerce").astype(float)
        return self._cache[key]  # type: ignore[return-value]

    def first_column(self, columns: Sequence[str]) -> tuple[pd.Series, str]:
        for column in columns:
            if column in self.data:
                return self.column(column), column
        raise MissingColumnsError(columns)

    @staticmethod
    def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
        left = numerator.to_numpy(dtype=float, copy=False)
        right = denominator.to_numpy(dtype=float, copy=False)
        output = np.full(len(left), np.nan, dtype=float)
        valid = np.isfinite(left) & np.isfinite(right) & (np.abs(right) > EPS)
        output[valid] = left[valid] / right[valid]
        return pd.Series(output, index=numerator.index, dtype=float)

    def rolling_mean(self, value: pd.Series, key: str, window: int = ROLLING_DAYS) -> pd.Series:
        cache_key = f"rolling_mean:{window}:{key}"
        if cache_key not in self._cache:
            self._cache[cache_key] = value.groupby(self.codes, sort=False).transform(
                lambda item: item.rolling(window, min_periods=window).mean()
            )
        return self._cache[cache_key]  # type: ignore[return-value]

    def rolling_sum(self, value: pd.Series, key: str, window: int = ROLLING_DAYS) -> pd.Series:
        cache_key = f"rolling_sum:{window}:{key}"
        if cache_key not in self._cache:
            self._cache[cache_key] = value.groupby(self.codes, sort=False).transform(
                lambda item: item.rolling(window, min_periods=window).sum()
            )
        return self._cache[cache_key]  # type: ignore[return-value]

    def rolling_std(self, value: pd.Series, key: str, window: int = ROLLING_DAYS) -> pd.Series:
        cache_key = f"rolling_std:{window}:{key}"
        if cache_key not in self._cache:
            self._cache[cache_key] = value.groupby(self.codes, sort=False).transform(
                lambda item: item.rolling(window, min_periods=window).std(ddof=0)
            )
        return self._cache[cache_key]  # type: ignore[return-value]

    def ewm_mean(self, value: pd.Series, key: str, span: int = ROLLING_DAYS) -> pd.Series:
        cache_key = f"ewm:{span}:{key}"
        if cache_key not in self._cache:
            self._cache[cache_key] = value.groupby(self.codes, sort=False).transform(
                lambda item: item.ewm(span=span, adjust=False, min_periods=span).mean()
            )
        return self._cache[cache_key]  # type: ignore[return-value]

    def rolling_spearman(
        self,
        left: pd.Series,
        right: pd.Series,
        key: str,
        window: int = ROLLING_DAYS,
    ) -> pd.Series:
        cache_key = f"spearman:{window}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        output = np.full(len(self.data), np.nan, dtype=float)
        left_values = left.to_numpy(dtype=float, copy=False)
        right_values = right.to_numpy(dtype=float, copy=False)
        for indices in self._groups:
            if len(indices) < window:
                continue
            left_windows = sliding_window_view(left_values[indices], window)
            right_windows = sliding_window_view(right_values[indices], window)
            valid = np.isfinite(left_windows).all(axis=1) & np.isfinite(right_windows).all(axis=1)
            if not valid.any():
                continue
            left_rank = rankdata(left_windows[valid], axis=1, method="average")
            right_rank = rankdata(right_windows[valid], axis=1, method="average")
            left_rank -= left_rank.mean(axis=1, keepdims=True)
            right_rank -= right_rank.mean(axis=1, keepdims=True)
            denominator = np.sqrt((left_rank * left_rank).sum(axis=1) * (right_rank * right_rank).sum(axis=1))
            correlation = np.full(valid.sum(), np.nan, dtype=float)
            usable = denominator > EPS
            correlation[usable] = (left_rank[usable] * right_rank[usable]).sum(axis=1) / denominator[usable]
            positions = indices[window - 1 :][valid]
            output[positions] = correlation
        result = pd.Series(output, index=self.data.index, dtype=float)
        self._cache[cache_key] = result
        return result

    def lag(self, value: pd.Series, key: str, periods: int = 1) -> pd.Series:
        cache_key = f"lag:{periods}:{key}"
        if cache_key not in self._cache:
            self._cache[cache_key] = value.groupby(self.codes, sort=False).shift(periods)
        return self._cache[cache_key]  # type: ignore[return-value]

    def cross_sectional_residual(self, y: pd.Series, x: pd.Series, key: str) -> pd.Series:
        cache_key = f"cross_residual:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        result = np.full(len(self.data), np.nan, dtype=float)
        y_value = y.to_numpy(dtype=float, copy=False)
        x_value = x.to_numpy(dtype=float, copy=False)
        for indices in self.data.groupby("date", sort=False).indices.values():
            positions = np.asarray(indices, dtype=np.int64)
            valid = np.isfinite(y_value[positions]) & np.isfinite(x_value[positions])
            if valid.sum() < 3:
                continue
            selected = positions[valid]
            selected_x = x_value[selected]
            selected_y = y_value[selected]
            centered_x = selected_x - selected_x.mean()
            denominator = np.dot(centered_x, centered_x)
            if denominator <= EPS:
                continue
            beta = np.dot(centered_x, selected_y - selected_y.mean()) / denominator
            result[selected] = selected_y - selected_y.mean() - beta * centered_x
        series = pd.Series(result, index=self.data.index, dtype=float)
        self._cache[cache_key] = series
        return series

    def cross_sectional_multi_residual(
        self,
        y: pd.Series,
        explanatory: Sequence[pd.Series],
        key: str,
    ) -> pd.Series:
        cache_key = f"cross_multi_residual:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        result = np.full(len(self.data), np.nan, dtype=float)
        y_value = y.to_numpy(dtype=float, copy=False)
        x_values = [item.to_numpy(dtype=float, copy=False) for item in explanatory]
        for indices in self.data.groupby("date", sort=False).indices.values():
            positions = np.asarray(indices, dtype=np.int64)
            valid = np.isfinite(y_value[positions])
            for values in x_values:
                valid &= np.isfinite(values[positions])
            if valid.sum() < len(x_values) + 2:
                continue
            selected = positions[valid]
            design = np.column_stack(
                [np.ones(len(selected)), *(values[selected] for values in x_values)]
            )
            if np.linalg.matrix_rank(design) < design.shape[1]:
                continue
            coefficients, *_ = np.linalg.lstsq(design, y_value[selected], rcond=None)
            result[selected] = y_value[selected] - design @ coefficients
        series = pd.Series(result, index=self.data.index, dtype=float)
        self._cache[cache_key] = series
        return series

    def rolling_two_variable_coefficients(self) -> tuple[pd.Series, pd.Series]:
        cache_key = "buy_sell_illiquidity_coefficients"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        y = self.column("daily_return")
        sell = self.column("sell_amount")
        buy = self.column("buy_amount")

        mean_y = self.rolling_mean(y, "illiq_y")
        mean_sell = self.rolling_mean(sell, "illiq_sell")
        mean_buy = self.rolling_mean(buy, "illiq_buy")
        var_sell = self.rolling_mean(sell * sell, "illiq_sell_square") - mean_sell * mean_sell
        var_buy = self.rolling_mean(buy * buy, "illiq_buy_square") - mean_buy * mean_buy
        cov_sell_buy = self.rolling_mean(sell * buy, "illiq_sell_buy") - mean_sell * mean_buy
        cov_sell_y = self.rolling_mean(sell * y, "illiq_sell_y") - mean_sell * mean_y
        cov_buy_y = self.rolling_mean(buy * y, "illiq_buy_y") - mean_buy * mean_y
        determinant = var_sell * var_buy - cov_sell_buy * cov_sell_buy
        scale = np.maximum((var_sell.abs() * var_buy.abs()).to_numpy(dtype=float), EPS)
        usable = np.isfinite(determinant) & (np.abs(determinant.to_numpy(dtype=float)) > EPS * scale)
        sell_beta = pd.Series(np.nan, index=self.data.index, dtype=float)
        buy_beta = pd.Series(np.nan, index=self.data.index, dtype=float)
        sell_beta.loc[usable] = (
            (cov_sell_y * var_buy - cov_buy_y * cov_sell_buy).loc[usable]
            / determinant.loc[usable]
        )
        buy_beta.loc[usable] = (
            (cov_buy_y * var_sell - cov_sell_y * cov_sell_buy).loc[usable]
            / determinant.loc[usable]
        )
        self._cache[cache_key] = (sell_beta, buy_beta)
        return sell_beta, buy_beta

    def flow(self, size: str) -> pd.Series:
        cache_key = f"flow:{size}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        direct_column = {"small": "small_flow", "large": "large_flow", "super": "super_flow"}[size]
        if direct_column in self.data:
            value = self.column(direct_column)
        else:
            buy_column, sell_column = {
                "small": ("small_buy_amount_40k", "small_sell_amount_40k"),
                "large": ("large_buy_amount", "large_sell_amount"),
                "super": ("super_buy_amount", "super_sell_amount"),
            }[size]
            buy = self.column(buy_column)
            sell = self.column(sell_column)
            value = self.safe_divide(buy - sell, buy.abs() + sell.abs())
        self._cache[cache_key] = value
        return value

    def flow_strength(self, size: str) -> pd.Series:
        cache_key = f"flow_strength:{size}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        if size == "small" and {"small_buy_amount_40k", "small_sell_amount_40k"}.issubset(self.data):
            net = self.column("small_buy_amount_40k") - self.column("small_sell_amount_40k")
        elif size == "large" and {"large_buy_amount", "large_sell_amount"}.issubset(self.data):
            net = self.column("large_buy_amount") - self.column("large_sell_amount")
        else:
            net = self.flow(size)
        numerator = self.rolling_sum(net, f"{size}_flow_net")
        denominator = self.rolling_sum(net.abs(), f"{size}_flow_abs")
        value = self.safe_divide(numerator, denominator)
        self._cache[cache_key] = value
        return value

    def residual_flow(self, size: str) -> pd.Series:
        strength = self.flow_strength(size)
        reversal = self.rolling_sum(self.column("daily_return"), "return_20d")
        return self.cross_sectional_residual(strength, reversal, f"{size}_flow_on_return")

    def residual_reversal(self, size: str) -> pd.Series:
        strength = self.flow_strength(size)
        reversal = -self.rolling_sum(self.column("daily_return"), "return_20d")
        return self.cross_sectional_residual(reversal, strength, f"reversal_on_{size}_flow")

    def ideal_reversal(self) -> pd.Series:
        cache_key = "ideal_reversal"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        amount = self.column("avg_trade_amount").to_numpy(dtype=float, copy=False)
        returns = self.column("daily_return").to_numpy(dtype=float, copy=False)
        output = np.full(len(self.data), np.nan, dtype=float)
        window = ROLLING_DAYS
        group_days = IDEAL_REVERSAL_GROUP_DAYS
        for indices in self._groups:
            if len(indices) < window:
                continue
            amount_windows = sliding_window_view(amount[indices], window)
            return_windows = sliding_window_view(returns[indices], window)
            valid = np.isfinite(amount_windows).all(axis=1) & np.isfinite(return_windows).all(axis=1)
            if not valid.any():
                continue
            order = np.argsort(amount_windows[valid], axis=1, kind="mergesort")
            low_index = order[:, :group_days]
            high_index = order[:, -group_days:]
            selected_return = return_windows[valid]
            low_return = np.take_along_axis(selected_return, low_index, axis=1).sum(axis=1)
            high_return = np.take_along_axis(selected_return, high_index, axis=1).sum(axis=1)
            output[indices[window - 1 :][valid]] = high_return - low_return
        result = pd.Series(output, index=self.data.index, dtype=float)
        self._cache[cache_key] = result
        return result

    def active_buy_sell_split(self) -> pd.Series:
        cache_key = "active_buy_sell_split"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        if {"active_large_buy_amount", "active_large_sell_amount"}.issubset(self.data):
            large_buy = self.column("active_large_buy_amount")
            large_sell = self.column("active_large_sell_amount")
        else:
            large_buy = self.column("large_buy_amount")
            large_sell = self.column("large_sell_amount")
        small_buy = self.column("small_buy_amount_40k")
        small_sell = self.column("small_sell_amount_40k")
        large_imbalance = self.safe_divide(large_buy - large_sell, large_buy + large_sell)
        small_imbalance = self.safe_divide(small_buy - small_sell, small_buy + small_sell)
        returns = self.column("daily_return").to_numpy(dtype=float, copy=False)
        large = large_imbalance.to_numpy(dtype=float, copy=False)
        small = small_imbalance.to_numpy(dtype=float, copy=False)
        output = np.full(len(self.data), np.nan, dtype=float)
        tail_days = max(1, int(np.ceil(ROLLING_DAYS * ACTIVE_SPLIT_TAIL_FRACTION)))
        for indices in self._groups:
            if len(indices) < ROLLING_DAYS:
                continue
            return_windows = sliding_window_view(returns[indices], ROLLING_DAYS)
            large_windows = sliding_window_view(large[indices], ROLLING_DAYS)
            small_windows = sliding_window_view(small[indices], ROLLING_DAYS)
            valid = (
                np.isfinite(return_windows).all(axis=1)
                & np.isfinite(large_windows).all(axis=1)
                & np.isfinite(small_windows).all(axis=1)
            )
            if not valid.any():
                continue
            order = np.argsort(return_windows[valid], axis=1, kind="mergesort")
            low_index = order[:, :tail_days]
            high_index = order[:, -tail_days:]
            high_large = np.take_along_axis(large_windows[valid], high_index, axis=1).mean(axis=1)
            low_small = np.take_along_axis(small_windows[valid], low_index, axis=1).mean(axis=1)
            output[indices[ROLLING_DAYS - 1 :][valid]] = high_large - low_small
        result = pd.Series(output, index=self.data.index, dtype=float)
        self._cache[cache_key] = result
        return result

    def ocvp(self) -> pd.Series:
        ratio = self.safe_divide(self.column("open_auction_volume"), self.column("trade_volume"))
        return self.rolling_mean(ratio, "ocvp_ratio")

    def bcvp(self) -> pd.Series:
        ratio = self.safe_divide(self.column("close_5m_volume"), self.column("trade_volume"))
        return self.rolling_mean(ratio, "bcvp_ratio")

    def obcvp(self) -> pd.Series:
        return OCVP_ALPHA * self.ocvp() + (1.0 - OCVP_ALPHA) * self.bcvp()

    def opening_mean(self, column: str) -> pd.Series:
        return self.rolling_mean(self.column(column), f"opening:{column}", OPENING_DAYS)

    def retail_fear(self) -> pd.Series:
        daily = self.column("retail_fear")
        mean = self.rolling_mean(daily, "retail_fear")
        std = self.rolling_std(daily, "retail_fear")
        return 0.5 * mean + 0.5 * std

    def weighted_game(self) -> pd.Series:
        cache_key = "weighted_game"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        buy = self.column("buy_volume").to_numpy(dtype=float, copy=False)
        sell = self.column("sell_volume").to_numpy(dtype=float, copy=False)
        weights = np.arange(1.0, GAME_DAYS + 1.0)
        output = np.full(len(self.data), np.nan, dtype=float)
        for indices in self._groups:
            if len(indices) < GAME_DAYS:
                continue
            buy_windows = sliding_window_view(buy[indices], GAME_DAYS)
            sell_windows = sliding_window_view(sell[indices], GAME_DAYS)
            valid = np.isfinite(buy_windows).all(axis=1) & np.isfinite(sell_windows).all(axis=1)
            numerator = (buy_windows[valid] * weights).sum(axis=1)
            denominator = (sell_windows[valid] * weights).sum(axis=1)
            values = np.full(valid.sum(), np.nan, dtype=float)
            usable = np.abs(denominator) > EPS
            values[usable] = numerator[usable] / denominator[usable]
            output[indices[GAME_DAYS - 1 :][valid]] = values
        result = pd.Series(output, index=self.data.index, dtype=float)
        self._cache[cache_key] = result
        return result

    def residual_noniso_imbalance(self) -> pd.Series:
        daily = self.column("residual_noniso_imbalance")
        average = self.rolling_mean(daily, "residual_noniso_imbalance")
        market_value = self.column("close") * self.column("float_shares")
        log_market_value = pd.Series(
            np.where(market_value > 0, np.log(market_value), np.nan),
            index=self.data.index,
            dtype=float,
        )
        reversal = self.rolling_sum(self.column("daily_return"), "return_20d")
        return self.cross_sectional_multi_residual(
            average,
            (log_market_value, reversal),
            "noniso_on_size_return",
        )

    def buy_or_sell_strength(self, column: str, key: str) -> pd.Series:
        value = self.column(column)
        mean = self.rolling_mean(value, key)
        std = self.rolling_std(value, key)
        return self.safe_divide(mean, std)


# 这些字段已经是日度因子值，不再改动公式。
DIRECT_CURRENT_COLUMNS = {
    "F02_D09_NET_BID_CHANGE_RATE": "net_bid_change_rate",
    "F02_D13_ASK_DEPTH": "ask_depth",
    "J06_D05_PIN": "pin",
    "J06_D14_HERDING": "herding",
    "J06_D16_FIVE_LEVEL_SOIR": "five_level_soir",
    "J06_D21_SELL_REBOUND_SMALL_ORDER": "small_sell_rebound_deviation",
    "J06_D26_PRSI": "prsi",
    "J06_D28_HIGH_VOLUME_AVG_TRADE_AMOUNT": "high_volume_avg_trade_amount",
    "J06_D30_LONG_BIG_ORDER_COMPOSITE": "long_big_order_composite",
    "J07_D04_VPIN": "vpin",
    "J07_D11_MOFI_WEIGHTED": "mofi",
    "J07_D13_CNIR": "corrected_net_inflow",
    "J07_D28_HIGHVOL_SELL_ATD": "high_volume_sell_avg_trade_amount",
    "J08_D04_VWVPIN": "vwpin",
    "J08_D07_SUPERORDER_PRICE_MOVE": "super_order_price_move",
    "J08_D11_BIDASK_PRICE_RATIO": "bidask_price_ratio",
    "J08_D28_PV_DIVERGENCE_ATD": "pv_divergence_avg_trade_amount",
    "J09_D01_BIG_ORDER_RETURN_EX_SUPER": "big_order_price_move_ex_super",
    "J09_D02_DPIN_BASE": "dpin",
    "J09_D08_MARGINAL_TRADING_COST": "marginal_trading_cost",
    "J09_D13_BUY_FLOATING_LOSS_SHARE": "buy_floating_loss_share",
    "J09_D16_MARKET_PRICE_BIAS": "market_price_bias",
    "J09_D30_PV_DIVERGENCE_SELL_ATD": "pv_divergence_sell_avg_trade_amount",
    "J10_D01_SMALL_DPIN": "small_dpin",
    "J10_D08_ML_ORDER_SLOPE": "multilevel_order_slope",
    "J10_D11_SELL_REBOUND_SHARE": "sell_rebound_share",
    "J10_D17_MIDPRICE_CHANGE": "midprice_change",
    "J11_D02_LARGE_ORDER_DPIN": "large_dpin",
    "J11_D19_BUY_FLOATING_LOSS_BIAS": "buy_floating_loss_bias",
    "J12_D17_MIDPRICE_CHANGE_SKEW": "midprice_change_skew",
    "J12_D19_SELL_REBOUND_DEVIATION": "sell_rebound_deviation",
    "J12_D30_DOWN_ATD": "down_avg_trade_amount",
}


DIRECT_HISTORICAL_COLUMNS = {
    "HF0149": "order_trade_corr",
    "HF0153": "midprice_change",
    "HF0157": "market_price_bias",
    "HF0162": "midprice_change_skew",
    "HF0166": "midprice_change_max",
    "HF0176": "sell_rebound_deviation",
    "HF0179": "small_dpin",
    "HF0186": "large_dpin",
    "HF0190": "order_imbalance",
    "HF0203": "order_imbalance_rate",
    "HF0211": "five_level_soir",
    "HF0221": "mofi",
    "HF0223": "bidask_price_ratio",
    "HF0226": "net_bid_change_rate",
    "HF0231": "multilevel_order_slope",
    "HF0232": "sell_rebound_share",
    "HF0235": "marginal_trading_cost",
    "HF0241": "avg_trade_volume_return_corr",
    "HF0244": "herding",
    "HF0252": "corrected_net_inflow",
    "HF0256": "buy_floating_loss_share",
    "HF0268": "ask_depth",
    "HF0282": "bid_depth",
    "HF0288": "average_depth",
    "HF0306": "effective_depth",
    "HF0319": "pin",
    "HF0320": "big_order_price_move_ex_super",
    "HF0322": "vpin",
    "HF0326": "vwpin",
    "HF0329": "super_order_price_move",
    "HF0330": "buy_floating_loss_bias",
}


def _available(value: pd.Series, quality: str, note: str) -> FactorResult:
    clean = pd.to_numeric(value, errors="coerce").astype(float)
    clean.loc[~np.isfinite(clean.to_numpy(dtype=float, copy=False))] = np.nan
    return FactorResult(clean, quality, note)


def _trial(value: pd.Series, note: str) -> FactorResult:
    return _available(value, "trial", note)


def _strict(value: pd.Series, note: str) -> FactorResult:
    return _available(value, "strict", note)


def _large_buy_share(engine: DailyFactorEngine) -> pd.Series:
    buy = engine.column("large_buy_amount")
    if "trade_amount" in engine.data:
        return engine.safe_divide(buy, engine.column("trade_amount"))
    sell = engine.column("large_sell_amount")
    return engine.safe_divide(buy, buy + sell)


def _ordinary_large_buy_share(engine: DailyFactorEngine) -> pd.Series:
    buy = engine.column("ordinary_large_buy_amount")
    sell = engine.column("ordinary_large_sell_amount")
    return engine.safe_divide(buy, buy + sell)


def _super_buy_share(engine: DailyFactorEngine) -> pd.Series:
    buy = engine.column("super_buy_amount")
    sell = engine.column("super_sell_amount")
    return engine.safe_divide(buy, buy + sell)


def _retail_bsi(engine: DailyFactorEngine) -> pd.Series:
    if "retail_bsi" in engine.data:
        return engine.column("retail_bsi")
    buy = engine.column("small_buy_amount_40k")
    sell = engine.column("small_sell_amount_40k")
    return engine.safe_divide(buy - sell, buy + sell)


def _small_active_buy_share(engine: DailyFactorEngine) -> pd.Series:
    active = engine.column("small_active_buy_amount_50k")
    total = engine.column("small_buy_amount_40k")
    return engine.safe_divide(active, total)


def _institutional_heat(engine: DailyFactorEngine) -> pd.Series:
    buy = engine.column("super_buy_amount")
    sell = engine.column("super_sell_amount")
    market_value = engine.column("close") * engine.column("float_shares")
    return engine.safe_divide(buy + sell, market_value)


def _improved_large_order_share(engine: DailyFactorEngine) -> pd.Series:
    buy = engine.column("large_buy_amount")
    sell = engine.column("large_sell_amount")
    return engine.safe_divide(buy + sell, engine.column("trade_amount"))


def _average_two(left: pd.Series, right: pd.Series) -> pd.Series:
    return (left + right) / 2.0


def calculate_target(engine: DailyFactorEngine, target: pd.Series) -> FactorResult:
    """计算单个目录目标；任何缺字段错误只返回给这个目标。"""

    factor_id = str(target["factor_id"])
    formula_key = str(target.get("formula_key", ""))
    reference = str(target.get("implementation_ref", ""))
    current_code = factor_id if factor_id.startswith(("F02_", "J")) else reference

    # 原图该式分母与分子完全相同，无法从文字确定正确公式。
    if factor_id == "J12_D31_PRICE_IMPACT_BIAS" or formula_key == "HF0072" or reference == "J12_D31_PRICE_IMPACT_BIAS":
        return FactorResult(
            None,
            "unavailable",
            "原图公式的分母与分子相同，无法确定正确写法；不使用 price_impact_bias 猜测替代。",
        )

    try:
        if current_code in DIRECT_CURRENT_COLUMNS:
            column = DIRECT_CURRENT_COLUMNS[current_code]
            return _strict(engine.column(column), f"直接使用日度基础字段 {column}。")

        if current_code == "F02_D01_QUA":
            value = engine.rolling_mean(engine.column("trade_amount_q10_position"), "qua")
            return _strict(value, "单日分位位置取近20日等权均值。")
        if current_code == "F02_D03_SMALL_ORDER_LAG_RANKCORR":
            small = engine.flow("small")
            lagged = engine.lag(small, "small_flow")
            value = engine.rolling_spearman(small, lagged, "small_flow_lag")
            return _strict(value, "小单净流入与其前一交易日值计算20日Spearman秩相关。")
        if current_code == "F02_D04_SELL_ORDER_ILLIQUIDITY":
            sell_beta, _ = engine.rolling_two_variable_coefficients()
            return _trial(sell_beta, "日度表无法还原分钟回归；采用近20日买卖金额双变量回归的卖出金额系数。")
        if current_code == "F02_D08_INSTITUTIONAL_TRADING_HEAT":
            return _trial(_institutional_heat(engine), "以超大单金额代替原文100万元机构单阈值。")
        if current_code == "F02_D18_INFORMED_BUY_SHARE":
            value = engine.rolling_mean(engine.column("informed_buy_share"), "informed_buy")
            return _strict(value, "知情主买日值取近20日等权均值。")
        if current_code == "F02_D22_TOXIC_LIQUIDITY":
            value = engine.rolling_mean(engine.column("toxic_liquidity"), "toxic_liquidity")
            return _strict(value, "有毒流动性日值取近20日等权均值。")
        if current_code == "F02_D24_LARGE_ORDER_PUSH_UP_RETURN":
            value = engine.rolling_mean(engine.column("large_order_push_return"), "large_order_push")
            return _trial(value, "输出频率未写明，统一采用近20日均值。")
        if current_code == "F02_D25_DOWN_TOP10_SELL_AVG_TRADE":
            return _strict(engine.column("down_avg_trade_amount"), "直接使用跌幅前10%分钟的主卖平均单笔金额日值。")
        if current_code == "F02_D26_IMPROVED_LARGE_ORDER_SHARE":
            return _trial(_improved_large_order_share(engine), "以已划分的大单买卖金额占总成交额代替原图未说明的改进项。")
        if current_code == "J06_D22_RESIDUAL_TRADE_IMBALANCE":
            return _trial(engine.column("residual_noniso_imbalance"), "日度表只有非孤立成交残差值，作为相近定义试算。")
        if current_code == "J06_D25_LARGE_BUY_SHARE":
            return _strict(_large_buy_share(engine), "大单买入额除以对应期间成交额。")
        if current_code == "J07_D02_CLOSE_SPREAD":
            value = engine.safe_divide(engine.column("close_spread"), engine.column("trade_amount"))
            return _strict(value, "收盘买卖一价差比例除以当日成交额。")
        if current_code == "J07_D16_RETAIL_FEAR":
            return _strict(engine.retail_fear(), "日度个人交易惊恐值的20日均值和总体标准差各占一半。")
        if current_code == "J07_D20_RESIDUAL_NONISO_IMB":
            value = engine.residual_noniso_imbalance()
            return _strict(value, "先取20日均值，再逐日同时剔除流通市值和20日收益的一次关系。")
        if current_code == "J07_D22_OPEN_BIG_NETBUY":
            return _trial(engine.opening_mean("open_large_net_buy_strength"), "原文T未写死，采用近20日均值。")
        if current_code == "J07_D26_LIQ_PREMIUM_EQUAL":
            value = engine.rolling_mean(engine.column("liq_premium_equal"), "liq_premium_equal")
            return _trial(value, "日度表没有保留每次模拟撮合的分子分母，采用近20日日值均值。")
        if current_code == "J08_D02_LIQ_PREMIUM_WEIGHTED":
            value = engine.rolling_mean(engine.column("liq_premium_weighted"), "liq_premium_weighted")
            return _trial(value, "日度表没有保留每次模拟撮合的分子分母，采用近20日日值均值。")
        if current_code == "J08_D13_GAME":
            return _trial(engine.weighted_game(), f"21日与42日两种写法并存，采用{GAME_DAYS}日线性递增权重。")
        if current_code == "J08_D20_OPEN_CLOSE_VOLUME_SHARE":
            return _trial(engine.obcvp(), f"OCVP与BCVP均取20日等权均值，复合权重 alpha={OCVP_ALPHA}。")
        if current_code == "J08_D23_BUY_CONCENTRATION":
            value = engine.rolling_mean(engine.column("buy_order_hhi"), "buy_order_hhi")
            return _strict(value, "每日买单金额集中度取近20日均值。")
        if current_code == "J08_D30_ACTIVE_BUY_SINGULARITY":
            value = engine.rolling_mean(engine.column("active_buy_singularity"), "active_buy_singularity")
            return _strict(value, "单日主买特异次数取近20日均值。")
        if current_code == "J09_D28_SIPHON_EFFECT":
            value = engine.rolling_mean(engine.column("siphon_effect"), "siphon_effect")
            return _strict(value, "虹吸效应日值取近20日均值。")
        if current_code == "J09_D20_ACTIVE_BUY_SHARE":
            buy = engine.rolling_sum(engine.column("buy_amount"), "active_buy_amount")
            total = engine.rolling_sum(engine.column("trade_amount"), "active_buy_total")
            return _trial(engine.safe_divide(buy, total), "周度与月度两种写法并存，采用近20日主买额占比。")
        if current_code == "J10_D02_AVG_TRADE_VOL_DIFF_STD":
            value = engine.rolling_mean(engine.column("avg_trade_volume_diff_std"), "avg_trade_volume_diff_std")
            return _trial(value, "原文只写对20日日值取统计量，采用20日均值。")
        if current_code == "J10_D13_MAIN_TRADE_SENTIMENT":
            value = engine.rolling_mean(engine.column("mte_daily"), "mte_daily")
            return _strict(value, "每日交易情绪秩相关取近20日均值。")
        if current_code == "J10_D23_OPEN_NET_ACTIVE_BUY":
            return _trial(engine.opening_mean("open_net_buy_share"), "原文T未写死，采用近20日均值。")
        if current_code == "J10_D30_ACTIVE_BUY_SELL_SPLIT":
            return _trial(
                engine.active_buy_sell_split(),
                f"近20日收益两端各取{ACTIVE_SPLIT_TAIL_FRACTION:.0%}；用高收益日大单不平衡减低收益日小单不平衡。",
            )
        if current_code == "J11_D23_BIG_BUY_INTENSITY":
            value = engine.buy_or_sell_strength("active_large_buy_amount", "large_buy_intensity")
            return _trial(value, "原文没有给出均值和标准差的组合方式，采用20日均值除以总体标准差。")
        if current_code == "J11_D26_IDEAL_REVERSAL":
            return _trial(engine.ideal_reversal(), "日度表无日内13/16分位值；按20日平均单笔金额排序，取高低各10日收益和之差。")
        if current_code == "J11_D24_ACTIVE_FLEXIBLE_CHIP":
            value = engine.rolling_mean(engine.column("active_flexible_chip"), "active_flexible_chip")
            return _trial(value, "日度表只保留单日比例，采用近20日均值。")
        if current_code == "J11_D30_LARGE_RESIDUAL_FLOW":
            return _strict(engine.residual_flow("large"), "先计算20日大单资金流强度，再逐日剔除其与20日收益的一次关系。")
        if current_code == "J11_D30_SMALL_RESIDUAL_FLOW":
            return _strict(engine.residual_flow("small"), "先计算20日小单资金流强度，再逐日剔除其与20日收益的一次关系。")
        if current_code == "J12_D08_ORDERBOOK_SPREAD":
            value = engine.ewm_mean(engine.column("spread_mean"), "spread_mean")
            return _strict(value, "日内平均盘口价差再取20日指数平均。")
        if current_code == "J12_D23_ACTIVE_BUY_STRENGTH":
            value = engine.buy_or_sell_strength("buy_amount", "active_buy_strength")
            return _trial(value, "日度表无分钟主买金额序列，采用20日主买金额均值除以总体标准差。")
        if current_code == "J12_D03_PROFIT_ADJ_ACTIVE_FLEX_CHIP":
            value = engine.rolling_mean(
                engine.column("profit_adjusted_active_flexible_chip"),
                "profit_adjusted_active_flexible_chip",
            )
            return _trial(value, "日度表只保留单日修正比例，采用近20日均值。")
        if current_code == "J12_D29_OPEN_ADJ_ACTIVE_FLEX_CHIP":
            value = engine.rolling_mean(
                engine.column("open_adjusted_active_flexible_chip"),
                "open_adjusted_active_flexible_chip",
            )
            return _trial(value, "日度表只保留单日修正比例，采用近20日均值。")

        if formula_key == "HF0136":
            value = engine.rolling_mean(
                engine.column("avg_outflow_trade_amount_ratio"),
                "avg_outflow_trade_amount_ratio",
            )
            return _trial(value, "原文T未写死，采用近20日均值。")
        if formula_key == "HF0173":
            value = engine.rolling_mean(engine.column("large_net_inflow_rate"), "large_net_inflow_rate")
            return _trial(value, "原文跨日天数未写死，采用近20日均值。")
        if formula_key == "HF0199":
            buy = engine.rolling_sum(engine.column("buy_amount"), "active_buy_amount")
            total = engine.rolling_sum(engine.column("trade_amount"), "active_buy_total")
            return _trial(engine.safe_divide(buy, total), "原文跨日天数未写死，采用近20日主买额占比。")
        if formula_key == "HF0192":
            value = engine.rolling_mean(engine.column("buy_order_hhi"), "buy_order_hhi")
            return _strict(value, "每日买单金额集中度取近20日均值。")
        if formula_key == "HF0257":
            return _trial(engine.weighted_game(), f"21日与42日两种写法并存，采用{GAME_DAYS}日线性递增权重。")
        if formula_key == "HF0321":
            value = engine.rolling_mean(engine.column("liq_premium_weighted"), "liq_premium_weighted")
            return _trial(value, "日度表没有保留每次模拟撮合的分子分母，采用近20日日值均值。")
        if formula_key == "HF0327":
            value = engine.rolling_mean(engine.column("liq_premium_equal"), "liq_premium_equal")
            return _trial(value, "日度表没有保留每次模拟撮合的分子分母，采用近20日日值均值。")

        if formula_key in DIRECT_HISTORICAL_COLUMNS:
            column = DIRECT_HISTORICAL_COLUMNS[formula_key]
            return _strict(engine.column(column), f"直接使用日度基础字段 {column}。")

        if formula_key == "HF0002":
            return _trial(engine.ocvp(), "原文时间权重未写死，采用20日等权均值。")
        if formula_key == "HF0003":
            return _strict(engine.bcvp(), "收盘前5分钟成交量占全天成交量的比例取近20日均值。")
        if formula_key == "HF0004":
            return _trial(engine.obcvp(), f"OCVP与BCVP均取20日等权均值，复合权重 alpha={OCVP_ALPHA}。")
        if formula_key == "HF0050":
            sell_beta, _ = engine.rolling_two_variable_coefficients()
            return _trial(sell_beta, "日度表无法还原分钟回归；采用近20日日度双变量回归卖出金额系数。")
        if formula_key == "HF0070":
            _, buy_beta = engine.rolling_two_variable_coefficients()
            return _trial(buy_beta, "日度表无法还原分钟回归；采用近20日日度双变量回归买入金额系数。")
        if formula_key in {"HF0071", "HF0165"}:
            value = _average_two(engine.residual_reversal("large"), engine.residual_reversal("small"))
            return _trial(value, "原文没有说明大小单残差如何合成，采用两者等权平均。")
        if formula_key == "HF0084":
            return _strict(_retail_bsi(engine), "散户买卖金额差除以散户买卖金额和。")
        if formula_key == "HF0103":
            value = _average_two(engine.residual_flow("large"), engine.residual_flow("small"))
            return _trial(value, "原文没有说明大小单残差如何合成，采用两者等权平均。")
        if formula_key == "HF0108":
            value = engine.rolling_spearman(engine.flow("super"), engine.flow("small"), "super_small_flow")
            return _strict(value, "超大单与小单净流入计算20日Spearman秩相关。")
        if formula_key == "HF0110":
            small = engine.flow("small")
            value = engine.rolling_spearman(small, engine.lag(small, "small_flow"), "small_flow_lag")
            return _strict(value, "小单净流入与其前一交易日值计算20日Spearman秩相关。")
        if formula_key == "HF0114":
            value = engine.rolling_mean(engine.column("large_order_push_return"), "large_order_push")
            return _trial(value, "原文T未写死，采用近20日均值。")
        if formula_key == "HF0117":
            return _trial(engine.opening_mean("open_large_net_buy_strength"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0118":
            return _trial(engine.opening_mean("open_large_net_buy_share"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0123":
            return _trial(engine.opening_mean("open_buy_willingness_share"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0126":
            small = engine.flow("small")
            value = engine.rolling_spearman(
                engine.column("daily_return"), engine.lag(small, "small_flow"), "retail_herding"
            )
            return _strict(value, "日收益与前一交易日小单净流入计算20日Spearman秩相关。")
        if formula_key == "HF0134":
            return _trial(engine.opening_mean("open_net_buy_strength"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0139":
            return _trial(engine.opening_mean("open_net_buy_share"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0141":
            return _trial(engine.opening_mean("open_net_bid_share"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0142":
            return _trial(engine.opening_mean("open_buy_willingness_strength"), "原文T未写死，采用近20日均值。")
        if formula_key == "HF0150":
            return _strict(engine.column("dpin"), "直接使用动态知情交易概率日值。")
        if formula_key == "HF0151":
            value = engine.rolling_mean(engine.column("avg_trade_volume_diff_std"), "avg_trade_volume_diff_std")
            return _trial(value, "原文未说明20日日值取哪一种统计量，采用均值。")
        if formula_key == "HF0155":
            return _trial(engine.ideal_reversal(), "日度表无日内13/16分位值；按20日平均单笔金额排序，取高低各10日收益和之差。")
        if formula_key == "HF0177":
            return _trial(engine.column("large_order_push_return"), "日度表只有按高平均单笔金额筛选的推动收益，作为局部反转试算值。")
        if formula_key == "HF0183":
            return _strict(_large_buy_share(engine), "大单买入额除以对应期间成交额。")
        if formula_key == "HF0202":
            return _trial(
                engine.buy_or_sell_strength("active_large_buy_amount", "large_buy_intensity"),
                "原文T未写死，采用20日均值除以总体标准差。",
            )
        if formula_key == "HF0204":
            return _strict(engine.retail_fear(), "日度个人交易惊恐值的20日均值和总体标准差各占一半。")
        if formula_key == "HF0213":
            return _trial(
                engine.buy_or_sell_strength("buy_amount", "active_buy_strength"),
                "日度表无分钟主买金额序列，采用20日主买金额均值除以总体标准差。",
            )
        if formula_key == "HF0216":
            value = engine.rolling_mean(engine.column("mte_daily"), "mte_daily")
            return _strict(value, "每日交易情绪秩相关取近20日均值。")
        if formula_key == "HF0263":
            value = engine.ewm_mean(engine.column("spread_mean"), "spread_mean")
            return _strict(value, "日内平均盘口价差再取20日指数平均。")
        if formula_key == "HF0289":
            value = engine.rolling_mean(engine.column("informed_buy_share"), "informed_buy")
            return _strict(value, "知情主买日值取近20日均值。")
        if formula_key == "HF0296":
            value = engine.rolling_mean(engine.column("informed_sell_share"), "informed_sell")
            return _strict(value, "知情主卖日值取近20日均值。")
        if formula_key == "HF0299":
            return _strict(_ordinary_large_buy_share(engine), "普通大单买入额除以普通大单买卖金额和。")
        if formula_key == "HF0300":
            value = engine.rolling_mean(engine.column("trade_amount_q10_position"), "qua")
            return _strict(value, "单日分位位置取近20日等权均值。")
        if formula_key == "HF0305":
            value = engine.rolling_mean(engine.column("mts_daily"), "mts_daily")
            return _strict(value, "每日主力交易强度秩相关取近20日均值。")
        if formula_key == "HF0308":
            return _strict(_super_buy_share(engine), "超大单买入额除以超大单买卖金额和。")
        if formula_key == "HF0315":
            daily = _small_active_buy_share(engine)
            value = engine.rolling_mean(daily, "small_active_buy_share")
            return _trial(value, "采用近20日均值；主动买入为5万元阈值、总小买单为4万元阈值，口径不完全相同。")

        return FactorResult(None, "unavailable", "目录公式尚未对应到日度基础字段。")
    except MissingColumnsError as error:
        return FactorResult(
            None,
            "unavailable",
            f"缺少计算字段：{'、'.join(error.columns)}。",
            error.columns,
        )


def iter_factor_results(
    base: pd.DataFrame,
    catalog: pd.DataFrame | str | Path = DEFAULT_CATALOG,
) -> Iterator[tuple[pd.Series, FactorResult]]:
    """依目录顺序逐个给出计算结果，便于大表逐段写入。"""

    prepared = prepare_daily_base(base)
    catalog_frame = load_factor_catalog(catalog) if isinstance(catalog, (str, Path)) else catalog
    targets = prepare_catalog_targets(catalog_frame)
    engine = DailyFactorEngine(prepared)
    for _, target in targets.iterrows():
        yield target, calculate_target(engine, target)


def _value_frame(base: pd.DataFrame, target: pd.Series, result: FactorResult) -> pd.DataFrame:
    if result.value is None:
        return pd.DataFrame(columns=VALUE_COLUMNS)
    return pd.DataFrame(
        {
            "factor_id": str(target["factor_id"]),
            "date": base["date"].to_numpy(),
            "code": base["code"].to_numpy(),
            "value": result.value.to_numpy(dtype=float, copy=False),
            "frequency": str(target["frequency"]),
            "expected_direction": str(target["expected_direction"]),
            "direction_source": str(target["direction_source"]),
            "implementation_quality": result.quality,
            "method_note": result.note,
        },
        columns=VALUE_COLUMNS,
    )


def _status_row(target: pd.Series, result: FactorResult) -> dict[str, object]:
    finite_count = 0
    first_date = ""
    last_date = ""
    if result.value is not None:
        valid = np.isfinite(result.value.to_numpy(dtype=float, copy=False))
        finite_count = int(valid.sum())
    return {
        "factor_id": str(target["factor_id"]),
        "name": str(target["name"]),
        "source_scope": str(target["source_scope"]),
        "frequency": str(target["frequency"]),
        "expected_direction": str(target["expected_direction"]),
        "direction_source": str(target["direction_source"]),
        "status": "available" if result.value is not None else "unavailable",
        "implementation_quality": result.quality,
        "method_note": result.note,
        "missing_columns": ";".join(result.missing_columns),
        "row_count": finite_count,
        "first_date": first_date,
        "last_date": last_date,
    }


def compute_level2_factors(
    base: pd.DataFrame,
    catalog: pd.DataFrame | str | Path = DEFAULT_CATALOG,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回因子长表和状态表；适合测试或较小数据。"""

    prepared = prepare_daily_base(base)
    catalog_frame = load_factor_catalog(catalog) if isinstance(catalog, (str, Path)) else catalog
    targets = prepare_catalog_targets(catalog_frame)
    engine = DailyFactorEngine(prepared)
    values: list[pd.DataFrame] = []
    statuses: list[dict[str, object]] = []
    for _, target in targets.iterrows():
        result = calculate_target(engine, target)
        if result.value is not None:
            values.append(_value_frame(prepared, target, result))
        status = _status_row(target, result)
        if result.value is not None:
            valid = np.isfinite(result.value.to_numpy(dtype=float, copy=False))
            if valid.any():
                status["first_date"] = prepared.loc[valid, "date"].min()
                status["last_date"] = prepared.loc[valid, "date"].max()
        statuses.append(status)
    value_frame = pd.concat(values, ignore_index=True) if values else pd.DataFrame(columns=VALUE_COLUMNS)
    status_frame = pd.DataFrame(statuses, columns=STATUS_COLUMNS)
    if not value_frame.empty and value_frame.duplicated(["factor_id", "date", "code"]).any():
        raise RuntimeError("因子长表出现重复的 factor_id+date+code。")
    return value_frame, status_frame


# 名称更贴近命令行产物，供其他脚本直接调用。
build_factor_values = compute_level2_factors


def write_level2_factor_outputs(
    base: pd.DataFrame,
    output_dir: str | Path,
    catalog: pd.DataFrame | str | Path = DEFAULT_CATALOG,
) -> tuple[Path, Path, pd.DataFrame]:
    """逐个因子写 Parquet，避免一次拼成很大的内存表。"""

    import pyarrow as pa
    import pyarrow.parquet as pq

    prepared = prepare_daily_base(base)
    catalog_frame = load_factor_catalog(catalog) if isinstance(catalog, (str, Path)) else catalog
    targets = prepare_catalog_targets(catalog_frame)
    engine = DailyFactorEngine(prepared)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    value_path = destination / "factor_values.parquet"
    status_path = destination / "factor_status.csv"

    writer: pq.ParquetWriter | None = None
    statuses: list[dict[str, object]] = []
    try:
        for _, target in targets.iterrows():
            result = calculate_target(engine, target)
            status = _status_row(target, result)
            if result.value is not None:
                valid = np.isfinite(result.value.to_numpy(dtype=float, copy=False))
                if valid.any():
                    status["first_date"] = prepared.loc[valid, "date"].min()
                    status["last_date"] = prepared.loc[valid, "date"].max()
                piece = _value_frame(prepared, target, result)
                table = pa.Table.from_pandas(piece, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(value_path, table.schema, compression="zstd")
                writer.write_table(table)
            statuses.append(status)
    finally:
        if writer is not None:
            writer.close()

    if writer is None:
        empty = pd.DataFrame(
            {
                "factor_id": pd.Series(dtype="string"),
                "date": pd.Series(dtype="datetime64[ns]"),
                "code": pd.Series(dtype="string"),
                "value": pd.Series(dtype="float64"),
                "frequency": pd.Series(dtype="string"),
                "expected_direction": pd.Series(dtype="string"),
                "direction_source": pd.Series(dtype="string"),
                "implementation_quality": pd.Series(dtype="string"),
                "method_note": pd.Series(dtype="string"),
            }
        )
        empty.to_parquet(value_path, index=False)

    status_frame = pd.DataFrame(statuses, columns=STATUS_COLUMNS)
    status_frame.to_csv(status_path, index=False, encoding="utf-8-sig")
    return value_path, status_path, status_frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把 Level2 日度基础表转换为历史与2026因子长表。")
    parser.add_argument("--input", required=True, help="日度基础 Parquet 或 CSV。")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="合并后的 Level2 因子目录。")
    parser.add_argument("--output-dir", required=True, help="结果目录。")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    base = read_daily_base(args.input)
    value_path, status_path, status = write_level2_factor_outputs(
        base,
        args.output_dir,
        args.catalog,
    )
    available = int((status["status"] == "available").sum())
    unavailable = int((status["status"] == "unavailable").sum())
    print(f"factor_values={value_path}")
    print(f"factor_status={status_path}")
    print(f"available={available} unavailable={unavailable}")


if __name__ == "__main__":
    main()
