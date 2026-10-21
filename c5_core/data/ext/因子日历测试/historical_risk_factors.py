# -*- coding: utf-8 -*-
"""由日度矩阵计算历史风险、流动性和收益统计因子。

所有可计算函数接收 ``arrays`` 字典和可选的 ``membership`` 布尔矩阵，
返回与股票收益矩阵同形状的 ``float32`` 矩阵。时间在第一维，股票在第二维。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

import numpy as np


ArrayMap = Mapping[str, np.ndarray]
FactorFunction = Callable[..., np.ndarray]


class UnsupportedFactorError(ValueError):
    """因子严格计算所需的数据不在本模块输入范围内。"""


def normalize_chinese_name(name: str) -> str:
    """统一中文因子名称，供三年目录合并和查找。"""

    value = str(name).strip().lower()
    value = re.sub(r"[\s·•—_\-（）()\[\]【】：《》:：/、，,]+", "", value)
    value = re.sub(r"因子$", "", value)
    return value


@dataclass(frozen=True)
class FactorSpec:
    name: str
    function: FactorFunction | None
    status: str = "strict"
    aliases: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    defaults: Mapping[str, object] = field(default_factory=dict)
    note: str = ""

    @property
    def normalized_name(self) -> str:
        return normalize_chinese_name(self.name)


def _matrix(value: np.ndarray, label: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.ndim != 2:
        raise ValueError(f"{label}必须是二维矩阵")
    return result


def _get(arrays: ArrayMap, *names: str) -> np.ndarray:
    for name in names:
        if name in arrays:
            return _matrix(arrays[name], name)
    raise KeyError(f"缺少字段，任选其一：{', '.join(names)}")


def _same_shape(reference: np.ndarray, value: np.ndarray, label: str) -> np.ndarray:
    result = _matrix(value, label)
    if result.shape != reference.shape:
        raise ValueError(f"{label}形状应为{reference.shape}，实际为{result.shape}")
    return result


def _membership(reference: np.ndarray, membership: np.ndarray | None) -> np.ndarray:
    if membership is None:
        return np.ones(reference.shape, dtype=bool)
    result = np.asarray(membership, dtype=bool)
    if result.shape != reference.shape:
        raise ValueError("membership与输入矩阵形状不一致")
    return result


def _returns(arrays: ArrayMap) -> np.ndarray:
    if "returns" in arrays:
        return _matrix(arrays["returns"], "returns")
    close = _get(arrays, "close")
    result = np.full(close.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(close[1:]) & np.isfinite(close[:-1]) & (close[:-1] != 0)
    current = result[1:]
    np.divide(close[1:], close[:-1], out=current, where=valid)
    current[valid] -= 1.0
    return result


def _turnover(arrays: ArrayMap) -> np.ndarray:
    if "turnover" in arrays:
        return _matrix(arrays["turnover"], "turnover")
    volume = _get(arrays, "volume")
    shares = _same_shape(volume, _get(arrays, "float_shares"), "float_shares")
    result = np.full(volume.shape, np.nan, dtype=np.float64)
    np.divide(volume, shares, out=result, where=np.isfinite(volume) & (shares > 0))
    return result


def market_equal_weight_return(
    arrays: ArrayMap, membership: np.ndarray | None = None
) -> np.ndarray:
    """读取市场收益；未提供时用当日股票等权收益计算。"""

    returns = _returns(arrays)
    if "market_return" in arrays:
        raw = np.asarray(arrays["market_return"], dtype=np.float64)
        if raw.ndim == 2:
            if raw.shape == returns.shape:
                raw = np.nanmean(raw, axis=1)
            elif raw.shape == (len(returns), 1):
                raw = raw[:, 0]
        if raw.ndim != 1 or len(raw) != len(returns):
            raise ValueError("market_return应为交易日长度的一维数组")
        return raw
    member = _membership(returns, membership)
    valid = member & np.isfinite(returns)
    count = valid.sum(axis=1)
    total = np.where(valid, returns, 0.0).sum(axis=1)
    return np.divide(total, count, out=np.full(len(returns), np.nan), where=count > 0)


def _rolling_sum_count(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    if window < 1:
        raise ValueError("window必须大于0")
    valid = np.isfinite(values)
    filled = np.where(valid, values, 0.0)
    sums = np.cumsum(filled, axis=0, dtype=np.float64)
    counts = np.cumsum(valid.astype(np.int32), axis=0, dtype=np.int32)
    out_sum = sums.copy()
    out_count = counts.copy()
    if window < len(values):
        out_sum[window:] = sums[window:] - sums[:-window]
        out_count[window:] = counts[window:] - counts[:-window]
    if window > 1:
        out_sum[: window - 1] = np.nan
        out_count[: window - 1] = 0
    return out_sum, out_count


def _rolling_mean(values: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    sums, counts = _rolling_sum_count(values, window)
    result = np.divide(sums, counts, out=np.full(values.shape, np.nan), where=counts > 0)
    result[counts < minimum] = np.nan
    return result


def _rolling_std(
    values: np.ndarray, window: int, min_periods: int | None = None, ddof: int = 1
) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    sums, counts = _rolling_sum_count(values, window)
    squares, _ = _rolling_sum_count(values * values, window)
    variance = np.full(values.shape, np.nan)
    valid = counts >= max(minimum, ddof + 1)
    numerator = squares - np.divide(sums * sums, counts, out=np.zeros_like(sums), where=counts > 0)
    np.divide(numerator, counts - ddof, out=variance, where=valid)
    return np.sqrt(np.maximum(variance, 0.0))


def _rolling_pair_sums(
    left: np.ndarray, right: np.ndarray, window: int
) -> tuple[np.ndarray, ...]:
    if right.ndim == 1:
        right = np.broadcast_to(right[:, None], left.shape)
    right = _same_shape(left, right, "市场收益")
    valid = np.isfinite(left) & np.isfinite(right)

    def roll(value: np.ndarray) -> np.ndarray:
        return _rolling_sum_count(np.where(valid, value, np.nan), window)[0]

    count = _rolling_sum_count(np.where(valid, 1.0, np.nan), window)[1]
    return count, roll(left), roll(right), roll(left * left), roll(right * right), roll(left * right)


def _rolling_beta(
    left: np.ndarray, right: np.ndarray, window: int, min_periods: int | None = None
) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    count, sx, sy, _, syy, sxy = _rolling_pair_sums(left, right, window)
    covariance = sxy - np.divide(sx * sy, count, out=np.zeros_like(sx), where=count > 0)
    variance = syy - np.divide(sy * sy, count, out=np.zeros_like(sy), where=count > 0)
    valid = (count >= minimum) & (variance > 0)
    return np.divide(covariance, variance, out=np.full(left.shape, np.nan), where=valid)


def _rolling_product(values: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    log_value = np.full(values.shape, np.nan)
    valid = np.isfinite(values) & (values > -1.0)
    log_value[valid] = np.log1p(values[valid])
    sums, counts = _rolling_sum_count(log_value, window)
    result = np.expm1(sums)
    result[counts < minimum] = np.nan
    return result


def _log_market_cap(arrays: ArrayMap, reference: np.ndarray) -> np.ndarray:
    if "log_market_cap" in arrays:
        return _same_shape(reference, arrays["log_market_cap"], "log_market_cap")
    for name in ("market_cap", "total_market_cap"):
        if name in arrays:
            value = _same_shape(reference, arrays[name], name)
            return np.log(np.where(value > 0, value, np.nan))
    if {"raw_close", "total_shares"}.issubset(arrays):
        price = _same_shape(reference, arrays["raw_close"], "raw_close")
        shares = _same_shape(reference, arrays["total_shares"], "total_shares")
        value = price * shares
        return np.log(np.where(value > 0, value, np.nan))
    raise KeyError("缺少log_market_cap、market_cap或raw_close+total_shares")


def _cross_sectional_residual(
    values: np.ndarray,
    controls: list[np.ndarray],
    membership: np.ndarray | None,
    min_stocks: int,
) -> np.ndarray:
    member = _membership(values, membership)
    result = np.full(values.shape, np.nan)
    for day in range(len(values)):
        valid = member[day] & np.isfinite(values[day])
        for control in controls:
            valid &= np.isfinite(control[day])
        if int(valid.sum()) < max(min_stocks, len(controls) + 2):
            continue
        design = np.column_stack(
            [np.ones(valid.sum()), *[control[day, valid] for control in controls]]
        )
        coefficients, *_ = np.linalg.lstsq(design, values[day, valid], rcond=None)
        result[day, valid] = values[day, valid] - design @ coefficients
    return result


def total_volatility(arrays: ArrayMap, membership=None, window: int = 252, min_periods=None) -> np.ndarray:
    return _rolling_std(_returns(arrays), window, min_periods).astype(np.float32)


def total_skewness(arrays: ArrayMap, membership=None, window: int = 252, min_periods=None) -> np.ndarray:
    values = _returns(arrays)
    minimum = window if min_periods is None else int(min_periods)
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        count = np.isfinite(sample).sum(axis=0)
        mean = np.nanmean(sample, axis=0)
        centered = sample - mean
        second = np.nanmean(centered * centered, axis=0)
        third = np.nanmean(centered**3, axis=0)
        valid = (count >= minimum) & (second > 0)
        result[end, valid] = third[valid] / np.power(second[valid], 1.5)
    return result.astype(np.float32)


def negative_coefficient_of_skewness(
    arrays: ArrayMap, membership=None, window: int = 252, min_periods=None
) -> np.ndarray:
    values = _returns(arrays)
    minimum = window if min_periods is None else int(min_periods)
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        count = np.isfinite(sample).sum(axis=0).astype(np.float64)
        mean = np.nanmean(sample, axis=0)
        centered = sample - mean
        sum2 = np.nansum(centered**2, axis=0)
        sum3 = np.nansum(centered**3, axis=0)
        denominator = (count - 1.0) * (count - 2.0) * np.power(sum2, 1.5)
        numerator = -count * np.power(count - 1.0, 1.5) * sum3
        valid = (count >= max(minimum, 3)) & (denominator != 0)
        result[end, valid] = numerator[valid] / denominator[valid]
    return result.astype(np.float32)


def maximum_return(
    arrays: ArrayMap, membership=None, window: int = 20, top_n: int = 1, min_periods=None
) -> np.ndarray:
    values = _returns(arrays)
    minimum = window if min_periods is None else int(min_periods)
    if not 1 <= top_n <= window:
        raise ValueError("top_n应在1到window之间")
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        count = np.isfinite(sample).sum(axis=0)
        ordered = np.sort(np.where(np.isfinite(sample), sample, -np.inf), axis=0)
        selected = ordered[-top_n:]
        valid = count >= max(minimum, top_n)
        result[end, valid] = selected[:, valid].mean(axis=0)
    return result.astype(np.float32)


def cumulative_return(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    return _rolling_product(_returns(arrays), window, min_periods).astype(np.float32)


def turnover_mean(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    return _rolling_mean(_turnover(arrays), window, min_periods).astype(np.float32)


def turnover_volatility(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    return _rolling_std(_turnover(arrays), window, min_periods).astype(np.float32)


def turnover_cv(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    values = _turnover(arrays)
    mean = _rolling_mean(values, window, min_periods)
    standard = _rolling_std(values, window, min_periods)
    return np.divide(standard, mean, out=np.full(values.shape, np.nan), where=mean != 0).astype(np.float32)


def abnormal_turnover(
    arrays: ArrayMap, membership=None, short_window: int = 20, long_window: int = 250
) -> np.ndarray:
    values = _turnover(arrays)
    short = _rolling_mean(values, short_window)
    long = _rolling_mean(values, long_window)
    return np.divide(short, long, out=np.full(values.shape, np.nan), where=long != 0).astype(np.float32)


def amount_volatility(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    return _rolling_std(_get(arrays, "amount"), window, min_periods).astype(np.float32)


def amount_volatility_coefficient(
    arrays: ArrayMap, membership=None, window: int = 20, min_periods=None
) -> np.ndarray:
    amount = _get(arrays, "amount")
    mean = _rolling_mean(amount, window, min_periods)
    standard = _rolling_std(amount, window, min_periods)
    return np.divide(mean, standard, out=np.full(amount.shape, np.nan), where=standard > 0).astype(np.float32)


def amihud_illiquidity(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    returns = _returns(arrays)
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    daily = np.divide(np.abs(returns), amount, out=np.full(returns.shape, np.nan), where=amount > 0)
    return _rolling_mean(daily, window, min_periods).astype(np.float32)


def negative_return_illiquidity(
    arrays: ArrayMap, membership=None, window: int = 20, min_periods: int = 1
) -> np.ndarray:
    returns = _returns(arrays)
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    daily = np.full(returns.shape, np.nan)
    valid = (returns < 0) & (amount > 0)
    daily[valid] = np.abs(returns[valid]) / amount[valid]
    return _rolling_mean(daily, window, min_periods).astype(np.float32)


def illiquidity_cv(arrays: ArrayMap, membership=None, window: int = 20, min_periods=None) -> np.ndarray:
    returns = _returns(arrays)
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    daily = np.divide(np.abs(returns), amount, out=np.full(returns.shape, np.nan), where=amount > 0)
    mean = _rolling_mean(daily, window, min_periods)
    standard = _rolling_std(daily, window, min_periods)
    return np.divide(standard, mean, out=np.full(daily.shape, np.nan), where=mean != 0).astype(np.float32)


def liquidity_shock(
    arrays: ArrayMap, membership=None, current_window: int = 20, history_window: int = 240
) -> np.ndarray:
    current = amihud_illiquidity(arrays, window=current_window).astype(np.float64)
    history = _rolling_mean(current, history_window)
    ratio = np.divide(current, history, out=np.full(current.shape, np.nan), where=history != 0)
    return (1.0 - ratio).astype(np.float32)


def market_beta(arrays: ArrayMap, membership=None, window: int = 252, min_periods=None) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    return _rolling_beta(returns, market, window, min_periods).astype(np.float32)


def downside_beta(arrays: ArrayMap, membership=None, window: int = 252, min_periods: int = 20) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    minimum = int(min_periods)
    result = np.full(returns.shape, np.nan)
    for end in range(window - 1, len(returns)):
        m = market[end - window + 1 : end + 1]
        threshold = np.nanmean(m)
        select = np.isfinite(m) & (m < threshold)
        if select.sum() < minimum:
            continue
        y = returns[end - window + 1 : end + 1][select]
        x = m[select]
        for stock in range(returns.shape[1]):
            valid = np.isfinite(y[:, stock]) & np.isfinite(x)
            if valid.sum() < minimum:
                continue
            xv = x[valid] - x[valid].mean()
            yv = y[valid, stock] - y[valid, stock].mean()
            denominator = np.dot(xv, xv)
            if denominator > 0:
                result[end, stock] = np.dot(xv, yv) / denominator
    return result.astype(np.float32)


def semibeta(
    arrays: ArrayMap,
    membership=None,
    window: int = 252,
    market_sign: str = "negative",
    asset_sign: str = "positive",
    min_periods: int = 20,
) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    result = np.full(returns.shape, np.nan)
    market_condition = market < 0 if market_sign == "negative" else market > 0
    for end in range(window - 1, len(returns)):
        r = returns[end - window + 1 : end + 1]
        m = market[end - window + 1 : end + 1]
        mc = market_condition[end - window + 1 : end + 1, None]
        ac = r < 0 if asset_sign == "negative" else r > 0
        valid = mc & ac & np.isfinite(r) & np.isfinite(m[:, None])
        count = valid.sum(axis=0)
        numerator = np.where(valid, r * m[:, None], 0.0).sum(axis=0)
        denominator = np.where(valid, m[:, None] ** 2, 0.0).sum(axis=0)
        sign = -1.0 if market_sign != asset_sign else 1.0
        usable = (count >= min_periods) & (denominator > 0)
        result[end, usable] = sign * numerator[usable] / denominator[usable]
    return result.astype(np.float32)


def semibeta_negative_market_positive_asset(arrays, membership=None, **params):
    return semibeta(arrays, membership, market_sign="negative", asset_sign="positive", **params)


def semibeta_positive_market_negative_asset(arrays, membership=None, **params):
    return semibeta(arrays, membership, market_sign="positive", asset_sign="negative", **params)


def semibeta_negative_market_negative_asset(arrays, membership=None, **params):
    return semibeta(arrays, membership, market_sign="negative", asset_sign="negative", **params)


def frazzini_pedersen_beta(
    arrays: ArrayMap, membership=None, correlation_window: int = 252, volatility_window: int = 252
) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    three_stock = _rolling_product(returns, 3)
    three_market = _rolling_product(market[:, None], 3)[:, 0]
    count, sx, sy, sxx, syy, sxy = _rolling_pair_sums(three_stock, three_market, correlation_window)
    cov = sxy - sx * sy / np.maximum(count, 1)
    var_x = sxx - sx * sx / np.maximum(count, 1)
    var_y = syy - sy * sy / np.maximum(count, 1)
    corr = np.divide(cov, np.sqrt(np.maximum(var_x * var_y, 0.0)), out=np.full(returns.shape, np.nan), where=(count >= correlation_window) & (var_x > 0) & (var_y > 0))
    stock_vol = _rolling_std(returns, volatility_window)
    market_vol = _rolling_std(np.broadcast_to(market[:, None], returns.shape), volatility_window)
    return np.divide(corr * stock_vol, market_vol, out=np.full(returns.shape, np.nan), where=market_vol > 0).astype(np.float32)


def dimson_beta(arrays: ArrayMap, membership=None, window: int = 252, min_periods=None) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    minimum = window if min_periods is None else int(min_periods)
    predictors = np.column_stack([np.roll(market, 1), market, np.roll(market, -1)])
    predictors[0, 0] = np.nan
    predictors[-1, 2] = np.nan
    result = np.full(returns.shape, np.nan)
    for end in range(window - 1, len(returns)):
        x = predictors[end - window + 1 : end + 1]
        y = returns[end - window + 1 : end + 1]
        for stock in range(returns.shape[1]):
            valid = np.isfinite(y[:, stock]) & np.isfinite(x).all(axis=1)
            if valid.sum() < max(minimum, 5):
                continue
            design = np.column_stack([np.ones(valid.sum()), x[valid]])
            coefficients, *_ = np.linalg.lstsq(design, y[valid, stock], rcond=None)
            result[end, stock] = coefficients[1:].sum()
    return result.astype(np.float32)


def coskewness(arrays: ArrayMap, membership=None, window: int = 252, min_periods=None) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    minimum = window if min_periods is None else int(min_periods)
    result = np.full(returns.shape, np.nan)
    for end in range(window - 1, len(returns)):
        m = market[end - window + 1 : end + 1]
        y = returns[end - window + 1 : end + 1]
        for stock in range(returns.shape[1]):
            valid = np.isfinite(m) & np.isfinite(y[:, stock])
            if valid.sum() < max(minimum, 3):
                continue
            x = m[valid]
            current = y[valid, stock]
            design = np.column_stack([np.ones(len(x)), x])
            residual = current - design @ np.linalg.lstsq(design, current, rcond=None)[0]
            centered_market = x - x.mean()
            variance_market = np.mean(centered_market**2)
            std_residual = np.sqrt(np.mean(residual**2))
            if variance_market > 0 and std_residual > 0:
                result[end, stock] = np.mean(residual * centered_market**2) / (std_residual * variance_market)
    return result.astype(np.float32)


def duvol(arrays: ArrayMap, membership=None, window: int = 252, min_periods: int = 20) -> np.ndarray:
    values = _returns(arrays)
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        mean = np.nanmean(sample, axis=0)
        up = sample > mean
        down = sample < mean
        n_up = (up & np.isfinite(sample)).sum(axis=0)
        n_down = (down & np.isfinite(sample)).sum(axis=0)
        up_sum = np.where(up, (sample - mean) ** 2, 0.0).sum(axis=0)
        down_sum = np.where(down, (sample - mean) ** 2, 0.0).sum(axis=0)
        ratio = np.divide((n_up - 1) * down_sum, (n_down - 1) * up_sum, out=np.full(values.shape[1], np.nan), where=(n_up > 1) & (n_down > 1) & (up_sum > 0))
        valid = (n_up + n_down >= min_periods) & (ratio > 0)
        result[end, valid] = np.log(ratio[valid])
    return result.astype(np.float32)


def value_at_risk(
    arrays: ArrayMap, membership=None, window: int = 252, alpha: float = 0.05, min_periods=None
) -> np.ndarray:
    if not 0 < alpha < 0.5:
        raise ValueError("alpha应在0和0.5之间")
    values = _returns(arrays)
    minimum = window if min_periods is None else int(min_periods)
    result = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        count = np.isfinite(sample).sum(axis=0)
        quantile = np.nanquantile(sample, alpha, axis=0)
        result[end, count >= minimum] = quantile[count >= minimum]
    return result.astype(np.float32)


def tail_beta(
    arrays: ArrayMap, membership=None, window: int = 252, alpha: float = 0.05, min_periods: int = 20
) -> np.ndarray:
    returns = _returns(arrays)
    market = market_equal_weight_return(arrays, membership)
    result = np.full(returns.shape, np.nan)
    for end in range(window - 1, len(returns)):
        m = market[end - window + 1 : end + 1]
        threshold = np.nanquantile(m, alpha)
        select = np.isfinite(m) & (m <= threshold)
        x = m[select]
        if len(x) < min_periods:
            continue
        y = returns[end - window + 1 : end + 1][select]
        for stock in range(returns.shape[1]):
            valid = np.isfinite(y[:, stock])
            if valid.sum() < min_periods:
                continue
            xv = x[valid] - x[valid].mean()
            yv = y[valid, stock] - y[valid, stock].mean()
            local_denominator = np.dot(xv, xv)
            if local_denominator > 0:
                result[end, stock] = np.dot(xv, yv) / local_denominator
    return result.astype(np.float32)


def market_tail_exposure(
    arrays: ArrayMap, membership=None, window: int = 252, tail_fraction: float = 0.25, min_periods=None
) -> np.ndarray:
    returns = _returns(arrays)
    member = _membership(returns, membership)
    tail_series = np.full(len(returns), np.nan)
    for day in range(len(returns)):
        sample = returns[day, member[day] & np.isfinite(returns[day])]
        if len(sample):
            cutoff = np.quantile(sample, tail_fraction)
            tail_series[day] = sample[sample <= cutoff].mean()
    return _rolling_beta(returns, tail_series, window, min_periods).astype(np.float32)


def pv_corr_mean(
    arrays: ArrayMap,
    membership=None,
    window: int = 20,
    min_periods=None,
    neutralize: bool = True,
    min_stocks: int = 50,
) -> np.ndarray:
    result = _rolling_mean(_get(arrays, "pv_corr_daily"), window, min_periods)
    if neutralize:
        result = _cross_sectional_residual(
            result, [_log_market_cap(arrays, result)], membership, min_stocks
        )
    return result.astype(np.float32)


def pv_corr_volatility(
    arrays: ArrayMap,
    membership=None,
    window: int = 20,
    min_periods=None,
    neutralize: bool = True,
    min_stocks: int = 50,
) -> np.ndarray:
    result = _rolling_std(_get(arrays, "pv_corr_daily"), window, min_periods)
    if neutralize:
        result = _cross_sectional_residual(
            result, [_log_market_cap(arrays, result)], membership, min_stocks
        )
    return result.astype(np.float32)


def pv_corr_trend(
    arrays: ArrayMap,
    membership=None,
    window: int = 20,
    min_periods=None,
    neutralize: bool = True,
    min_stocks: int = 50,
) -> np.ndarray:
    values = _get(arrays, "pv_corr_daily")
    minimum = window if min_periods is None else int(min_periods)
    result = np.full(values.shape, np.nan)
    time = np.arange(window, dtype=np.float64)
    centered = time - time.mean()
    denominator = np.dot(centered, centered)
    for end in range(window - 1, len(values)):
        sample = values[end - window + 1 : end + 1]
        valid = np.isfinite(sample)
        complete = valid.sum(axis=0) >= minimum
        if minimum == window:
            result[end, complete] = np.dot(centered, sample[:, complete]) / denominator
        else:
            for stock in np.flatnonzero(complete):
                keep = valid[:, stock]
                x = time[keep]
                x = x - x.mean()
                result[end, stock] = np.dot(x, sample[keep, stock]) / np.dot(x, x)
    if neutralize:
        controls = [
            _log_market_cap(arrays, result),
            _same_shape(result, _get(arrays, "return_20d"), "return_20d"),
            _same_shape(result, _get(arrays, "turnover_20d"), "turnover_20d"),
            _same_shape(result, _get(arrays, "volatility_20d"), "volatility_20d"),
        ]
        result = _cross_sectional_residual(result, controls, membership, min_stocks)
    return result.astype(np.float32)


def _unsupported(*args, **kwargs) -> np.ndarray:
    raise UnsupportedFactorError("该因子缺少严格计算所需的数据")


SPECS = (
    FactorSpec("总波动率", total_volatility, status="trial", aliases=("收益率波动率",), required=("returns或close",), defaults={"window": 252}, note="原文以K个月表示窗口；默认按252个交易日试算，可显式传window。"),
    FactorSpec("总偏度", total_skewness, status="trial", required=("returns或close",), defaults={"window": 252}, note="原文以K个月表示窗口；默认按252个交易日试算，可显式传window。"),
    FactorSpec("负偏度系数NCSKEW", negative_coefficient_of_skewness, status="trial", aliases=("负偏度系数", "NCSKEW"), required=("returns或close",), defaults={"window": 252}, note="原文未统一给出n；默认252日，可显式传window。"),
    FactorSpec("最大收益率", maximum_return, status="trial", required=("returns或close",), defaults={"window": 20, "top_n": 1}, note="原文允许最高1日或最高n日均值；默认20日内最高1日。"),
    FactorSpec("前K个月动量", cumulative_return, status="trial", aliases=("短期反转", "累计收益率"), required=("returns或close",), defaults={"window": 20}, note="K未固定；默认20个交易日，可显式传window。"),
    FactorSpec("换手率", turnover_mean, status="trial", required=("turnover或volume+float_shares",), defaults={"window": 20}, note="K个月未固定；默认20个交易日，可显式传window。"),
    FactorSpec("换手波动", turnover_volatility, status="trial", required=("turnover或volume+float_shares",), defaults={"window": 20}, note="K个月未固定；默认20个交易日，可显式传window。"),
    FactorSpec("换手率变异系数", turnover_cv, required=("turnover或volume+float_shares",), defaults={"window": 20}),
    FactorSpec("月度异常换手率", abnormal_turnover, required=("turnover或volume+float_shares",), defaults={"short_window": 20, "long_window": 250}),
    FactorSpec("成交额波动", amount_volatility, status="trial", required=("amount",), defaults={"window": 20}, note="K个月未固定；默认20个交易日，可显式传window。"),
    FactorSpec("成交额波动系数", amount_volatility_coefficient, required=("amount",), defaults={"window": 20}),
    FactorSpec("Amihud非流动性", amihud_illiquidity, aliases=("Amihud非流动性因子",), required=("returns或close", "amount"), defaults={"window": 20}),
    FactorSpec("负收益非流动性", negative_return_illiquidity, required=("returns或close", "amount"), defaults={"window": 20}),
    FactorSpec("非流动性的变异系数", illiquidity_cv, aliases=("CVILLIQ",), required=("returns或close", "amount"), defaults={"window": 20}),
    FactorSpec("市场Beta", market_beta, aliases=("基于日度收益的市场Beta",), required=("returns或close", "market_return或membership"), defaults={"window": 252}),
    FactorSpec("下行贝塔", downside_beta, required=("returns或close", "market_return或membership"), defaults={"window": 252, "min_periods": 20}),
    FactorSpec("Frazzini-Pedersen贝塔", frazzini_pedersen_beta, aliases=("FP贝塔",), required=("returns或close", "market_return或membership"), defaults={"correlation_window": 252, "volatility_window": 252}),
    FactorSpec("Dimson贝塔", dimson_beta, status="trial", required=("returns或close", "market_return或membership"), defaults={"window": 252}, note="原文未统一给出回归窗口；默认252日，可显式传window。"),
    FactorSpec("协偏度", coskewness, aliases=("协偏度系统偏度", "系统偏度"), required=("returns或close", "market_return或membership"), defaults={"window": 252}),
    FactorSpec("SemiBeta负市场收益正资产收益", semibeta_negative_market_positive_asset, aliases=("SemiBeta（负市场收益、正资产收益）",), required=("returns或close", "market_return或membership"), defaults={"window": 252}),
    FactorSpec("SemiBeta正市场收益负资产收益", semibeta_positive_market_negative_asset, aliases=("SemiBeta（正市场收益、负资产收益）",), required=("returns或close", "market_return或membership"), defaults={"window": 252}),
    FactorSpec("SemiBeta负市场收益负资产收益", semibeta_negative_market_negative_asset, aliases=("SemiBeta（负市场收益、负资产收益）",), required=("returns或close", "market_return或membership"), defaults={"window": 252}),
    FactorSpec("上下行波动率DUVOL", duvol, status="trial", aliases=("上下行波动率",), required=("returns或close",), defaults={"window": 252}, note="原文未统一给出窗口；默认252日，可显式传window。"),
    FactorSpec("在险价值", value_at_risk, status="trial", aliases=("VaR",), required=("returns或close",), defaults={"window": 252, "alpha": 0.05}, note="原文未统一给出置信水平；默认左尾5%。"),
    FactorSpec("尾部Beta", tail_beta, required=("returns或close", "market_return或membership"), defaults={"window": 252, "alpha": 0.05}),
    FactorSpec("尾部风险", None, status="unsupported", note="目录公式先构造月度市场尾部风险；本模块没有月份标记，不能用日度左尾均值替代。"),
    FactorSpec("价量相关性平均数", pv_corr_mean, aliases=("PV_corr_avg",), required=("pv_corr_daily", "market_cap"), defaults={"window": 20, "neutralize": True, "min_stocks": 50}),
    FactorSpec("价量相关性波动性", pv_corr_volatility, aliases=("PV_corr_std", "价量相关性波动性因子"), required=("pv_corr_daily", "market_cap"), defaults={"window": 20, "neutralize": True, "min_stocks": 50}),
    FactorSpec("价量相关性趋势", pv_corr_trend, aliases=("PV_corr_trend", "价量相关性趋势因子"), required=("pv_corr_daily", "market_cap", "return_20d", "turnover_20d", "volatility_20d"), defaults={"window": 20, "neutralize": True, "min_stocks": 50}),
    FactorSpec("月均换手率", None, status="unsupported", note="严格月均值需要月份标记，不能固定用20日代替自然月。"),
    FactorSpec("流动性冲击", None, status="unsupported", note="严格公式使用当月Amihud及过去12个自然月，需要月份标记。"),
    FactorSpec("基于月度收益的市场Beta", None, status="unsupported", note="严格计算需要月收益矩阵或月份标记。"),
    FactorSpec("特质波动率", None, status="unsupported", note="目录定义可能依赖FF3；不能把CAPM版本冒充为同一公式。"),
    FactorSpec("月度特质波动率", None, status="unsupported", note="严格计算需要FF3的MKT、SMB、HML及月度分组。"),
    FactorSpec("特质偏度", None, status="unsupported", note="目录定义可能依赖FF3；不能静默改为CAPM。"),
    FactorSpec("高频特异波动率", None, status="unsupported", note="严格计算需要市场、规模、价值、反转和流动性因子。"),
    FactorSpec("非线性高频波动率", None, status="unsupported", note="严格计算需要多因子回归得到的特异率。"),
    FactorSpec("极端下行风险", None, status="unsupported", note="三年目录定义不一致，2024版本还严格依赖FF3残差。"),
    FactorSpec("价量相关性趋势行业中性", None, status="unsupported", note="严格计算需要行业分类与完整中性化字段。"),
    FactorSpec("公告跳跃", None, status="unsupported", note="严格计算需要公告日期。"),
)


FACTOR_REGISTRY: dict[str, FactorSpec] = {}
for _spec in SPECS:
    for _name in (_spec.name, *_spec.aliases):
        _key = normalize_chinese_name(_name)
        previous = FACTOR_REGISTRY.get(_key)
        if previous is not None and previous is not _spec:
            raise RuntimeError(f"因子名称重复：{_name}")
        FACTOR_REGISTRY[_key] = _spec


def get_factor_spec(name: str) -> FactorSpec:
    key = normalize_chinese_name(name)
    if key not in FACTOR_REGISTRY:
        raise KeyError(f"没有登记因子：{name}")
    return FACTOR_REGISTRY[key]


def compute_factor(
    name: str,
    arrays: ArrayMap,
    membership: np.ndarray | None = None,
    **parameters: object,
) -> np.ndarray:
    spec = get_factor_spec(name)
    if spec.status == "unsupported" or spec.function is None:
        raise UnsupportedFactorError(f"{spec.name}暂不支持严格计算：{spec.note}")
    options = dict(spec.defaults)
    options.update(parameters)
    result = spec.function(arrays, membership=membership, **options)
    result = np.asarray(result)
    if result.ndim != 2:
        raise ValueError(f"{spec.name}没有返回二维矩阵")
    return result.astype(np.float32, copy=False)


def factor_metadata() -> list[dict[str, object]]:
    """返回去重后的登记信息，便于写入总表。"""

    return [
        {
            "name": spec.name,
            "normalized_name": spec.normalized_name,
            "status": spec.status,
            "aliases": list(spec.aliases),
            "required": list(spec.required),
            "defaults": dict(spec.defaults),
            "note": spec.note,
        }
        for spec in SPECS
    ]
