# -*- coding: utf-8 -*-
"""用现有日度矩阵补算因子日历中尚未登记的方法。

输入字段均为 ``(交易日, 股票)`` 二维数组。自然月因子在提供 ``dates`` 时
按真实月份计算；没有日期时按每 21 个交易日近似一个月，相关说明标为试算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

import historical_risk_factors as risk


ArrayMap = Mapping[str, np.ndarray]
Calculator = Callable[[ArrayMap, np.ndarray | None, Sequence[object] | None], np.ndarray]
EPS = 1e-12


class UnsupportedFactorError(ValueError):
    """计算所需资料不在日度矩阵中。"""


@dataclass(frozen=True)
class FactorSpec:
    name: str
    function: Calculator
    status: str
    required: tuple[str, ...]
    note: str
    aliases: tuple[str, ...] = ()


def _normalize(name: str) -> str:
    return risk.normalize_chinese_name(name)


def _matrix(value: np.ndarray, label: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.ndim != 2:
        raise ValueError(f"{label}必须是二维数组")
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


def _finish(value: np.ndarray) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    result[~np.isfinite(result)] = np.nan
    return result.astype(np.float32)


def _membership(reference: np.ndarray, membership: np.ndarray | None) -> np.ndarray:
    if membership is None:
        return np.ones(reference.shape, dtype=bool)
    result = np.asarray(membership, dtype=bool)
    if result.shape != reference.shape:
        raise ValueError("membership与输入矩阵形状不一致")
    return result


def _lag(value: np.ndarray, periods: int = 1) -> np.ndarray:
    if periods == 0:
        return np.asarray(value, dtype=np.float64).copy()
    result = np.full(value.shape, np.nan)
    if 0 < periods < len(value):
        result[periods:] = value[:-periods]
    return result


def _returns(arrays: ArrayMap) -> np.ndarray:
    if "returns" in arrays:
        return _matrix(arrays["returns"], "returns")
    close = _get(arrays, "close")
    previous = _lag(close)
    return np.divide(
        close,
        previous,
        out=np.full(close.shape, np.nan),
        where=np.isfinite(previous) & (previous != 0),
    ) - 1.0


def _market_return(
    arrays: ArrayMap, membership: np.ndarray | None, reference: np.ndarray
) -> np.ndarray:
    if "market_return" in arrays:
        value = np.asarray(arrays["market_return"], dtype=np.float64)
        if value.ndim == 2:
            if value.shape == reference.shape:
                value = np.nanmean(value, axis=1)
            elif value.shape == (len(reference), 1):
                value = value[:, 0]
        if value.ndim != 1 or len(value) != len(reference):
            raise ValueError("market_return应为交易日长度的一维数组")
        return value
    member = _membership(reference, membership)
    valid = member & np.isfinite(reference)
    count = valid.sum(axis=1)
    total = np.where(valid, reference, 0.0).sum(axis=1)
    return np.divide(total, count, out=np.full(len(reference), np.nan), where=count > 0)


def _turnover(arrays: ArrayMap) -> np.ndarray:
    if "turnover" in arrays:
        return _matrix(arrays["turnover"], "turnover")
    volume = _get(arrays, "volume")
    shares = _same_shape(volume, _get(arrays, "float_shares"), "float_shares")
    return np.divide(
        volume,
        shares,
        out=np.full(volume.shape, np.nan),
        where=np.isfinite(volume) & (shares > 0),
    )


def _rolling_sum_count(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(values)
    filled = np.where(valid, values, 0.0)
    sums = np.cumsum(filled, axis=0, dtype=np.float64)
    counts = np.cumsum(valid.astype(np.int32), axis=0)
    result_sum = sums.copy()
    result_count = counts.copy()
    if window < len(values):
        result_sum[window:] = sums[window:] - sums[:-window]
        result_count[window:] = counts[window:] - counts[:-window]
    if window > 1:
        result_sum[: window - 1] = np.nan
        result_count[: window - 1] = 0
    return result_sum, result_count


def _rolling_mean(
    values: np.ndarray, window: int, min_periods: int | None = None
) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    sums, counts = _rolling_sum_count(values, window)
    result = np.divide(
        sums, counts, out=np.full(values.shape, np.nan), where=counts > 0
    )
    result[counts < minimum] = np.nan
    return result


def _column_nanmean(values: np.ndarray) -> np.ndarray:
    valid = np.isfinite(values)
    count = valid.sum(axis=0)
    return np.divide(
        np.where(valid, values, 0.0).sum(axis=0),
        count,
        out=np.full(values.shape[1], np.nan),
        where=count > 0,
    )


def _rolling_std(
    values: np.ndarray, window: int, min_periods: int | None = None
) -> np.ndarray:
    minimum = window if min_periods is None else int(min_periods)
    sums, counts = _rolling_sum_count(values, window)
    squares, _ = _rolling_sum_count(values * values, window)
    numerator = squares - np.divide(
        sums * sums, counts, out=np.zeros(values.shape), where=counts > 0
    )
    result = np.divide(
        numerator,
        counts,
        out=np.full(values.shape, np.nan),
        where=(counts >= minimum) & (counts > 0),
    )
    return np.sqrt(np.maximum(result, 0.0))


def _ema(values: np.ndarray, window: int) -> np.ndarray:
    alpha = 2.0 / (window + 1.0)
    result = np.full(values.shape, np.nan)
    state = np.full(values.shape[1], np.nan)
    for index, row in enumerate(values):
        valid = np.isfinite(row)
        first = valid & ~np.isfinite(state)
        later = valid & np.isfinite(state)
        state[first] = row[first]
        state[later] = alpha * row[later] + (1.0 - alpha) * state[later]
        result[index, valid] = state[valid]
    return result


def _rolling_min_max(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    low = np.full(values.shape, np.nan)
    high = np.full(values.shape, np.nan)
    for end in range(window - 1, len(values)):
        block = values[end - window + 1 : end + 1]
        complete = np.isfinite(block).all(axis=0)
        if complete.any():
            low[end, complete] = np.min(block[:, complete], axis=0)
            high[end, complete] = np.max(block[:, complete], axis=0)
    return low, high


def _cross_sectional_zscore(
    values: np.ndarray, membership: np.ndarray | None, min_stocks: int = 3
) -> np.ndarray:
    member = _membership(values, membership)
    result = np.full(values.shape, np.nan)
    for day in range(len(values)):
        valid = member[day] & np.isfinite(values[day])
        if valid.sum() < min_stocks:
            continue
        sample = values[day, valid]
        standard = sample.std(ddof=0)
        if standard > EPS:
            result[day, valid] = (sample - sample.mean()) / standard
        else:
            result[day, valid] = 0.0
    return result


def _cross_sectional_residual(
    values: np.ndarray,
    controls: Sequence[np.ndarray],
    membership: np.ndarray | None,
    weights: np.ndarray | None = None,
    min_stocks: int = 3,
) -> np.ndarray:
    member = _membership(values, membership)
    result = np.full(values.shape, np.nan)
    for day in range(len(values)):
        valid = member[day] & np.isfinite(values[day])
        for control in controls:
            valid &= np.isfinite(control[day])
        if weights is not None:
            valid &= np.isfinite(weights[day]) & (weights[day] > 0)
        if valid.sum() < max(min_stocks, len(controls) + 2):
            continue
        design = np.column_stack(
            [np.ones(valid.sum()), *[control[day, valid] for control in controls]]
        )
        target = values[day, valid]
        if weights is None:
            coefficient, *_ = np.linalg.lstsq(design, target, rcond=None)
        else:
            root_weight = np.sqrt(weights[day, valid])
            coefficient, *_ = np.linalg.lstsq(
                design * root_weight[:, None], target * root_weight, rcond=None
            )
        result[day, valid] = target - design @ coefficient
    return result


def _common_design_r2(
    targets: np.ndarray, design: np.ndarray, min_periods: int
) -> np.ndarray:
    """同一组解释变量同时回归多只股票，返回各股票拟合优度。"""

    values = np.asarray(targets, dtype=np.float64)
    x = np.asarray(design, dtype=np.float64)
    if values.ndim != 2 or x.ndim != 2 or len(values) != len(x):
        raise ValueError("回归目标和解释变量形状不一致")
    finite_x = np.isfinite(x).all(axis=1)
    x = x[finite_x]
    values = values[finite_x]
    valid = np.isfinite(values)
    result = np.full(values.shape[1], np.nan)
    packed = np.packbits(valid.T, axis=1)
    _, groups = np.unique(packed, axis=0, return_inverse=True)
    for group in range(groups.max(initial=-1) + 1):
        columns = np.flatnonzero(groups == group)
        rows = valid[:, columns[0]]
        if rows.sum() < min_periods:
            continue
        target = values[rows][:, columns]
        local_x = x[rows]
        coefficients = np.linalg.lstsq(local_x, target, rcond=None)[0]
        residual = target - local_x @ coefficients
        total = np.sum((target - target.mean(axis=0)) ** 2, axis=0)
        usable = total > EPS
        result[columns[usable]] = 1.0 - np.sum(
            residual[:, usable] ** 2, axis=0
        ) / total[usable]
    return result


def _solve_symmetric_3x3(gram: np.ndarray, right: np.ndarray) -> np.ndarray:
    """批量求解三元线性方程；奇异组返回空值。"""

    matrix = np.asarray(gram, dtype=np.float64)
    vector = np.asarray(right, dtype=np.float64)
    a, b, c = matrix[:, 0, 0], matrix[:, 0, 1], matrix[:, 0, 2]
    d, e, f = matrix[:, 1, 1], matrix[:, 1, 2], matrix[:, 2, 2]
    determinant = a * (d * f - e * e) - b * (b * f - c * e) + c * (b * e - c * d)
    determinant_scale = np.maximum(np.abs(a * d * f), EPS)
    usable = np.isfinite(determinant) & (
        np.abs(determinant) > EPS * determinant_scale
    )
    result = np.full(vector.shape, np.nan)
    r0, r1, r2 = vector[:, 0], vector[:, 1], vector[:, 2]
    result[usable, 0] = (
        (d[usable] * f[usable] - e[usable] ** 2) * r0[usable]
        + (c[usable] * e[usable] - b[usable] * f[usable]) * r1[usable]
        + (b[usable] * e[usable] - c[usable] * d[usable]) * r2[usable]
    ) / determinant[usable]
    result[usable, 1] = (
        (c[usable] * e[usable] - b[usable] * f[usable]) * r0[usable]
        + (a[usable] * f[usable] - c[usable] ** 2) * r1[usable]
        + (b[usable] * c[usable] - a[usable] * e[usable]) * r2[usable]
    ) / determinant[usable]
    result[usable, 2] = (
        (b[usable] * e[usable] - c[usable] * d[usable]) * r0[usable]
        + (b[usable] * c[usable] - a[usable] * e[usable]) * r1[usable]
        + (a[usable] * d[usable] - b[usable] ** 2) * r2[usable]
    ) / determinant[usable]
    return result


def _batched_variable_lstsq(
    design: np.ndarray, targets: np.ndarray, valid: np.ndarray
) -> np.ndarray:
    """按有效行分组，一次求解组内全部股票的最小二乘回归。"""

    x = np.asarray(design, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    mask = np.asarray(valid, dtype=bool)
    if x.ndim != 3 or y.shape != x.shape[:2] or mask.shape != y.shape:
        raise ValueError("批量回归输入形状不一致")
    result = np.full((x.shape[1], x.shape[2]), np.nan)
    packed = np.packbits(mask.T, axis=1)
    _, groups = np.unique(packed, axis=0, return_inverse=True)
    for group in range(groups.max(initial=-1) + 1):
        columns = np.flatnonzero(groups == group)
        rows = mask[:, columns[0]]
        if not rows.any():
            continue
        local_x = np.transpose(x[rows][:, columns], (1, 0, 2))
        local_y = y[rows][:, columns].T
        u, singular, vh = np.linalg.svd(local_x, full_matrices=False)
        projection = np.einsum("bmp,bm->bp", u, local_y, optimize=True)
        cutoff = (
            np.finfo(np.float64).eps
            * max(local_x.shape[1], local_x.shape[2])
            * singular[:, :1]
        )
        scaled = np.divide(
            projection,
            singular,
            out=np.zeros_like(projection),
            where=singular > cutoff,
        )
        result[columns] = np.einsum("bij,bi->bj", vh, scaled, optimize=True)
    return result


def _quadratic_window_residuals(
    targets: np.ndarray, market: np.ndarray, min_periods: int = 30
) -> np.ndarray:
    """一次计算窗口内全部股票的二次市场模型标准化残差。"""

    values = np.asarray(targets, dtype=np.float64)
    x = np.asarray(market, dtype=np.float64)
    finite_x = np.isfinite(x)
    values = values[finite_x]
    x = x[finite_x]
    design = np.column_stack([np.ones(len(x)), x, x * x])
    valid = np.isfinite(values)
    filled = np.where(valid, values, 0.0)
    count = valid.sum(axis=0)
    gram = np.einsum("tp,tn,tq->npq", design, valid, design, optimize=True)
    right = np.einsum("tp,tn->np", design, filled, optimize=True)
    coefficients = _solve_symmetric_3x3(gram, right)
    fitted = design @ coefficients.T
    residual = np.where(valid, values - fitted, np.nan)
    mean = np.divide(
        np.nansum(residual, axis=0),
        count,
        out=np.full(values.shape[1], np.nan),
        where=count > 0,
    )
    centered = residual - mean
    variance = np.divide(
        np.nansum(centered * centered, axis=0),
        count - 1,
        out=np.full(values.shape[1], np.nan),
        where=count > 1,
    )
    standard = np.sqrt(np.maximum(variance, 0.0))
    usable = (count >= min_periods) & np.isfinite(coefficients).all(axis=1) & (standard > EPS)
    standardized = np.divide(
        centered,
        standard,
        out=np.full(centered.shape, np.nan),
        where=usable[None, :],
    )
    return standardized


def _month_groups(
    length: int, dates: Sequence[object] | None
) -> tuple[list[np.ndarray], np.ndarray]:
    if dates is None:
        groups = [np.arange(start, min(start + 21, length)) for start in range(0, length, 21)]
        groups = [group for group in groups if len(group) == 21]
        return groups, np.array([group[-1] for group in groups], dtype=int)
    date_array = np.asarray(dates, dtype="datetime64[D]")
    if date_array.ndim != 1 or len(date_array) != length:
        raise ValueError("dates长度必须等于交易日数")
    months = date_array.astype("datetime64[M]")
    groups = [np.flatnonzero(months == month) for month in np.unique(months)]
    return groups, np.array([group[-1] for group in groups], dtype=int)


def _monthly_returns(
    arrays: ArrayMap, membership: np.ndarray | None, dates: Sequence[object] | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    groups, ends = _month_groups(len(returns), dates)
    stock_values = np.full((len(groups), returns.shape[1]), np.nan)
    market_values = np.full(len(groups), np.nan)
    for month, rows in enumerate(groups):
        stock_block = returns[rows]
        valid_count = np.isfinite(stock_block).sum(axis=0)
        product = np.prod(np.where(np.isfinite(stock_block), 1.0 + stock_block, 1.0), axis=0)
        stock_values[month, valid_count > 0] = product[valid_count > 0] - 1.0
        market_block = market[rows]
        valid_market = np.isfinite(market_block)
        if valid_market.any():
            market_values[month] = np.prod(1.0 + market_block[valid_market]) - 1.0
    return stock_values, market_values, ends


def _expand_monthly(
    monthly: np.ndarray, ends: np.ndarray, shape: tuple[int, int]
) -> np.ndarray:
    values = np.asarray(monthly, dtype=np.float64)
    month_ends = np.asarray(ends, dtype=int)
    if values.shape != (len(month_ends), shape[1]):
        raise ValueError(
            f"月度结果形状应为{(len(month_ends), shape[1])}，实际为{values.shape}"
        )
    if len(month_ends) and (
        month_ends[0] < 0
        or month_ends[-1] >= shape[0]
        or np.any(month_ends[1:] <= month_ends[:-1])
    ):
        raise ValueError("月末位置必须递增且在日度矩阵范围内")
    result = np.full(shape, np.nan)
    for month, start in enumerate(month_ends):
        stop = month_ends[month + 1] if month + 1 < len(month_ends) else shape[0]
        result[start:stop] = values[month]
    return result


def regional_index(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    high = _get(arrays, "high")
    low = _same_shape(high, _get(arrays, "low"), "low")
    close = _same_shape(high, _get(arrays, "close"), "close")
    previous = _lag(close)
    true_range = np.maximum.reduce(
        [high - low, np.abs(previous - high), np.abs(previous - low)]
    )
    change = close - previous
    w = np.where(
        close > previous,
        np.divide(true_range, change, out=np.full(close.shape, np.nan), where=change != 0),
        true_range,
    )
    low_w, high_w = _rolling_min_max(w, 20)
    spread = high_w - low_w
    sr = np.where(
        spread > 0,
        100.0 * (w - low_w) / spread,
        100.0 * (w - low_w),
    )
    return _ema(sr, 5)


def swing_index(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    opening = _get(arrays, "open")
    high = _same_shape(opening, _get(arrays, "high"), "high")
    low = _same_shape(opening, _get(arrays, "low"), "low")
    close = _same_shape(opening, _get(arrays, "close"), "close")
    previous_close = _lag(close)
    previous_open = _lag(opening)
    k1 = high - previous_close
    k2 = low - previous_close
    k3 = high - close
    k4 = previous_close + previous_open
    maximum = np.maximum.reduce([k1, k2, k3])
    r = np.where(
        maximum == k1,
        k1 + 0.5 * k2 + 0.25 * k4,
        np.where(maximum == k2, k2 + 0.5 * k1 + 0.25 * k4, k3 + 0.25 * k4),
    )
    k = np.maximum(k1, k2)
    numerator = (close - previous_close) + 0.5 * (close - opening) + 0.25 * (
        previous_close + previous_open
    )
    denominator = r * close * 0.01
    return np.divide(
        50.0 * numerator * k,
        denominator,
        out=np.full(close.shape, np.nan),
        where=np.abs(denominator) > EPS,
    )


def size_adjusted_turnover(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    turnover = _turnover(arrays)
    close = _get(arrays, "raw_close", "close")
    shares = _same_shape(close, _get(arrays, "float_shares"), "float_shares")
    float_cap = close * shares
    log_turnover = np.log(np.where(turnover > 0, turnover, np.nan))
    log_cap = np.log(np.where(float_cap > 0, float_cap, np.nan))
    return _cross_sectional_residual(log_turnover, [log_cap], membership)


def trend_momentum(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    close = _get(arrays, "close")
    signals = []
    for window in (3, 5, 10, 20, 50, 100, 200, 400, 600, 800, 1000):
        if window <= len(close):
            signals.append(
                np.divide(
                    _rolling_mean(close, window),
                    close,
                    out=np.full(close.shape, np.nan),
                    where=close != 0,
                )
            )
    if not signals:
        return np.full(close.shape, np.nan)
    stack = np.stack(signals)
    count = np.isfinite(stack).sum(axis=0)
    signal = np.divide(
        np.nansum(stack, axis=0), count, out=np.full(close.shape, np.nan), where=count > 0
    )
    controls = []
    try:
        controls.append(_rolling_mean(_turnover(arrays), 20))
    except KeyError:
        pass
    if "float_shares" in arrays and "total_shares" in arrays:
        floating = _matrix(arrays["float_shares"], "float_shares")
        total = _same_shape(floating, arrays["total_shares"], "total_shares")
        controls.append(
            np.divide(floating, total, out=np.full(close.shape, np.nan), where=total > 0)
        )
    if controls:
        signal = _cross_sectional_residual(signal, controls, membership)
    return signal


def price_delay(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    groups, ends = _month_groups(len(returns), dates)
    monthly = np.full((len(groups), returns.shape[1]), np.nan)
    market_lags = np.column_stack([_lag(market[:, None], lag)[:, 0] for lag in range(6)])
    for month, rows in enumerate(groups):
        x = market_lags[rows]
        full = np.column_stack([np.ones(len(x)), x])
        valid_predictors = np.isfinite(full).all(axis=1)
        local_returns = returns[rows][valid_predictors]
        full = full[valid_predictors]
        r2_full = _common_design_r2(local_returns, full, 10)
        r2_restricted = _common_design_r2(local_returns, full[:, :2], 10)
        usable = np.isfinite(r2_full) & np.isfinite(r2_restricted) & (r2_full > EPS)
        monthly[month, usable] = 1.0 - r2_restricted[usable] / r2_full[usable]
    return _expand_monthly(monthly, ends, returns.shape)


def spread_bias(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    close = _get(arrays, "close")
    returns = _returns(arrays)
    member = _membership(close, membership)
    _, ends = _month_groups(len(close), dates)
    monthly = np.full((len(ends), close.shape[1]), np.nan)
    for month, end in enumerate(ends):
        if end < 249:
            continue
        start = end - 249
        block = returns[start : end + 1]
        usable = member[end] & (np.isfinite(block).sum(axis=0) >= 200)
        stocks = np.flatnonzero(usable)
        if len(stocks) < 3:
            continue
        correlation_input = block[:, stocks].copy()
        column_mean = np.nanmean(correlation_input, axis=0)
        missing = ~np.isfinite(correlation_input)
        correlation_input[missing] = np.broadcast_to(
            column_mean, correlation_input.shape
        )[missing]
        correlation = np.corrcoef(correlation_input, rowvar=False)
        np.fill_diagonal(correlation, -np.inf)
        price_start = close[start, stocks]
        normalized = np.divide(
            close[start : end + 1, stocks],
            price_start,
            out=np.full((250, len(stocks)), np.nan),
            where=np.isfinite(price_start) & (price_start != 0),
        )
        peer_count = min(10, len(stocks) - 1)
        safe_correlation = np.where(np.isfinite(correlation), correlation, -np.inf)
        peer_indices = np.argpartition(
            safe_correlation, len(stocks) - peer_count, axis=1
        )[:, -peer_count:]
        peer_prices = normalized[-60:, peer_indices]
        peer_valid = np.isfinite(peer_prices)
        peer_number = peer_valid.sum(axis=2)
        reference = np.divide(
            np.where(peer_valid, peer_prices, 0.0).sum(axis=2),
            peer_number,
            out=np.full((60, len(stocks)), np.nan),
            where=peer_number > 0,
        )
        own = normalized[-60:]
        spread_history = np.full((60, len(stocks)), np.nan)
        valid_history = (own > 0) & (reference > 0)
        spread_history[valid_history] = (
            np.log(own[valid_history]) - np.log(reference[valid_history])
        )
        history_count = np.isfinite(spread_history).sum(axis=0)
        history_sum = np.nansum(spread_history, axis=0)
        mean = np.divide(
            history_sum,
            history_count,
            out=np.full(len(stocks), np.nan),
            where=history_count > 0,
        )
        centered = spread_history - mean
        standard = np.sqrt(
            np.divide(
                np.nansum(centered * centered, axis=0),
                history_count - 1,
                out=np.full(len(stocks), np.nan),
                where=history_count > 1,
            )
        )
        valid = np.isfinite(spread_history[-1]) & (standard > EPS)
        monthly[month, stocks[valid]] = (
            spread_history[-1, valid] - mean[valid]
        ) / standard[valid]
    return _expand_monthly(monthly, ends, close.shape)


def monthly_market_beta(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    stock, market, ends = _monthly_returns(arrays, membership, dates)
    result = np.full(stock.shape, np.nan)
    for month in range(23, len(stock)):
        start = max(0, month - 59)
        x = market[start : month + 1]
        y = stock[start : month + 1]
        valid = np.isfinite(x[:, None]) & np.isfinite(y)
        count = valid.sum(axis=0)
        x_values = np.where(valid, x[:, None], 0.0)
        y_values = np.where(valid, y, 0.0)
        sum_x = x_values.sum(axis=0)
        sum_y = y_values.sum(axis=0)
        covariance = (x_values * y_values).sum(axis=0) - np.divide(
            sum_x * sum_y, count, out=np.zeros(stock.shape[1]), where=count > 0
        )
        variance = (x_values * x_values).sum(axis=0) - np.divide(
            sum_x * sum_x, count, out=np.zeros(stock.shape[1]), where=count > 0
        )
        usable = (count >= 24) & (variance > EPS)
        result[month, usable] = covariance[usable] / variance[usable]
    return _expand_monthly(result, ends, _returns(arrays).shape)


def return_seasonality(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    stock, market, ends = _monthly_returns(arrays, membership, dates)
    excess = stock - market[:, None]
    result = np.full(excess.shape, np.nan)
    if len(excess) > 12:
        result[12:] = excess[:-12]
    return _expand_monthly(result, ends, _returns(arrays).shape)


def return_seasonality_reversal(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    stock, market, ends = _monthly_returns(arrays, membership, dates)
    excess = stock - market[:, None]
    result = np.full(excess.shape, np.nan)
    for month in range(11, len(excess)):
        result[month] = np.nanmean(excess[month - 11 : month], axis=0)
    return _expand_monthly(result, ends, _returns(arrays).shape)


def zhu_coskewness(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    x = np.broadcast_to(market[:, None], returns.shape)
    valid = np.isfinite(x) & np.isfinite(returns)

    def roll(values: np.ndarray) -> np.ndarray:
        return _rolling_sum_count(np.where(valid, values, np.nan), 20)[0]

    count = _rolling_sum_count(np.where(valid, 1.0, np.nan), 20)[1].astype(np.float64)
    sx = roll(x)
    sx2 = roll(x * x)
    sx3 = roll(x * x * x)
    sy = roll(returns)
    syx = roll(returns * x)
    syx2 = roll(returns * x * x)
    mean_x = np.divide(sx, count, out=np.full(returns.shape, np.nan), where=count > 0)
    mean_y = np.divide(sy, count, out=np.full(returns.shape, np.nan), where=count > 0)
    centered_x2_sum = sx2 - 2.0 * mean_x * sx + count * mean_x**2
    numerator = (
        syx2
        - 2.0 * mean_x * syx
        + mean_x**2 * sy
        - mean_y * centered_x2_sum
    )
    denominator = sx3 - 3.0 * mean_x * sx2 + 3.0 * mean_x**2 * sx - count * mean_x**3
    result = np.divide(
        numerator,
        denominator,
        out=np.full(returns.shape, np.nan),
        where=(count >= 15) & (np.abs(denominator) > EPS),
    )
    return result


def nonlinear_market_cap(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    if "market_cap" in arrays:
        cap = _matrix(arrays["market_cap"], "market_cap")
    elif "total_market_cap" in arrays:
        cap = _matrix(arrays["total_market_cap"], "total_market_cap")
    else:
        close = _get(arrays, "raw_close", "close")
        shares = _same_shape(close, _get(arrays, "total_shares"), "total_shares")
        cap = close * shares
    log_cap = np.log(np.where(cap > 0, cap, np.nan))
    residual = _cross_sectional_residual(
        log_cap**3, [log_cap], membership, weights=np.sqrt(cap)
    )
    member = _membership(residual, membership)
    clipped = np.full(residual.shape, np.nan)
    for day in range(len(residual)):
        valid = member[day] & np.isfinite(residual[day])
        if valid.sum() < 3:
            continue
        sample = residual[day, valid]
        median = np.median(sample)
        mad = np.median(np.abs(sample - median))
        if mad > EPS:
            sample = np.clip(sample, median - 3.0 * 1.4826 * mad, median + 3.0 * 1.4826 * mad)
        clipped[day, valid] = sample
    return _cross_sectional_zscore(clipped, membership)


def rank_momentum(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    member = _membership(returns, membership)
    daily_rank = np.full(returns.shape, np.nan)
    for day in range(len(returns)):
        valid = member[day] & np.isfinite(returns[day])
        count = int(valid.sum())
        if count < 2:
            continue
        values = returns[day, valid]
        order = np.argsort(values, kind="stable")
        ranks = np.empty(count, dtype=np.float64)
        ranks[order] = np.arange(1.0, count + 1.0)
        denominator = np.sqrt((count + 1.0) * (count - 1.0) / 12.0)
        daily_rank[day, valid] = (ranks - (count + 1.0) / 2.0) / denominator
    groups, ends = _month_groups(len(returns), dates)
    monthly = (
        np.vstack([_column_nanmean(daily_rank[rows]) for rows in groups])
        if groups
        else np.empty((0, returns.shape[1]))
    )
    result = np.full(monthly.shape, np.nan)
    for month in range(13, len(monthly)):
        result[month] = np.nanmean(monthly[month - 13 : month - 1], axis=0)
    return _expand_monthly(result, ends, returns.shape)


def abnormal_tail_e(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    result = np.full(returns.shape, np.nan)
    for end in range(62, len(returns)):
        residual = _quadratic_window_residuals(
            returns[end - 62 : end + 1], market[end - 62 : end + 1]
        )
        valid_count = np.isfinite(residual).sum(axis=0)
        positive = np.sum(residual >= 1.5, axis=0)
        negative = np.sum(residual <= -1.5, axis=0)
        usable = valid_count >= 30
        result[end, usable] = (positive[usable] - negative[usable]) / valid_count[usable]
    return result


def abnormal_tail_s(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    result = np.full(returns.shape, np.nan)
    bins = np.linspace(1.5, 4.5, 13)
    for end in range(62, len(returns)):
        residual = _quadratic_window_residuals(
            returns[end - 62 : end + 1], market[end - 62 : end + 1]
        )
        positive = np.zeros((len(bins) - 1, returns.shape[1]), dtype=np.float64)
        negative = np.zeros_like(positive)
        mirrored = -residual
        for index in range(len(bins) - 1):
            upper_rule = residual <= bins[index + 1] if index == len(bins) - 2 else residual < bins[index + 1]
            positive[index] = np.sum((residual >= bins[index]) & upper_rule, axis=0)
            mirrored_upper = mirrored <= bins[index + 1] if index == len(bins) - 2 else mirrored < bins[index + 1]
            negative[index] = np.sum((mirrored >= bins[index]) & mirrored_upper, axis=0)
        positive_total = positive.sum(axis=0)
        negative_total = negative.sum(axis=0)
        p = np.divide(
            positive,
            positive_total,
            out=np.zeros_like(positive),
            where=positive_total[None, :] > 0,
        )
        q = np.divide(
            negative,
            negative_total,
            out=np.zeros_like(negative),
            where=negative_total[None, :] > 0,
        )
        distance = 0.5 * np.sum((np.sqrt(p) - np.sqrt(q)) ** 2, axis=0)
        valid_count = np.isfinite(residual).sum(axis=0)
        e_value = np.sum(residual >= 1.5, axis=0) - np.sum(residual <= -1.5, axis=0)
        usable = valid_count >= 30
        result[end, usable] = np.sign(e_value[usable]) * distance[usable]
    return result


def capital_gain_overhang(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    close = _get(arrays, "close")
    vwap = _get(arrays, "vwap_qfq", "vwap", "close")
    turnover = np.clip(_turnover(arrays), 0.0, 1.0)
    window = 100
    weighted = np.zeros(close.shape)
    weight_sum = np.zeros(close.shape)
    survival = np.ones(close.shape)
    for lag in range(window):
        target = slice(lag, None)
        source = slice(None) if lag == 0 else slice(None, -lag)
        rate = turnover[source]
        weight = rate * survival[target]
        weighted[target] += weight * vwap[source]
        weight_sum[target] += weight
        survival[target] *= 1.0 - rate
    reference = np.divide(
        weighted, weight_sum, out=np.full(close.shape, np.nan), where=weight_sum > EPS
    )
    result = np.divide(
        close - reference,
        close,
        out=np.full(close.shape, np.nan),
        where=np.isfinite(close) & (close != 0),
    )
    result[: window - 1] = np.nan
    return result


def _pv_parts(
    arrays: ArrayMap, membership: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    daily = _get(arrays, "pv_corr_daily")
    average = _rolling_mean(daily, 20)
    volatility = _rolling_std(daily, 20)
    trend = risk.pv_corr_trend(
        arrays, membership=membership, window=20, neutralize=False
    ).astype(np.float64)
    return average, volatility, trend


def pv_corr_std(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    _, result, _ = _pv_parts(arrays, membership)
    if any(name in arrays for name in ("log_market_cap", "market_cap", "total_market_cap")):
        reference = result
        if "log_market_cap" in arrays:
            control = _same_shape(reference, arrays["log_market_cap"], "log_market_cap")
        else:
            cap = _get(arrays, "market_cap", "total_market_cap")
            control = np.log(np.where(cap > 0, cap, np.nan))
        result = _cross_sectional_residual(result, [control], membership)
    return result


def pv_corr_trend(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    return _pv_parts(arrays, membership)[2]


def pv_corr_composite(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    average, volatility, _ = _pv_parts(arrays, membership)
    return _cross_sectional_zscore(average, membership) + _cross_sectional_zscore(
        volatility, membership
    )


def cpv(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    average, volatility, trend = _pv_parts(arrays, membership)
    returns20 = np.full(average.shape, np.nan)
    close = _get(arrays, "close")
    previous = _lag(close, 20)
    np.divide(close, previous, out=returns20, where=np.isfinite(previous) & (previous != 0))
    returns20 -= 1.0
    adjusted_average = _cross_sectional_residual(average, [returns20], membership)
    adjusted_volatility = _cross_sectional_residual(volatility, [returns20], membership)
    first = _cross_sectional_zscore(adjusted_average, membership) + _cross_sectional_zscore(
        adjusted_volatility, membership
    )
    return _cross_sectional_zscore(first, membership) + _cross_sectional_zscore(
        trend, membership
    )


def liquidity_shock(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    daily = np.divide(
        np.abs(returns), amount, out=np.full(returns.shape, np.nan), where=amount > 0
    )
    groups, ends = _month_groups(len(returns), dates)
    monthly = (
        np.vstack([_column_nanmean(daily[rows]) for rows in groups])
        if groups
        else np.empty((0, returns.shape[1]))
    )
    result = np.full(monthly.shape, np.nan)
    for month in range(12, len(monthly)):
        result[month] = np.nanmean(monthly[month - 12 : month], axis=0) - monthly[month]
    return _expand_monthly(result, ends, returns.shape)


def conditional_liquidity_shock(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    daily = np.divide(
        np.abs(returns), amount, out=np.full(returns.shape, np.nan), where=amount > 0
    )
    groups, ends = _month_groups(len(returns), dates)
    monthly = (
        np.vstack([_column_nanmean(daily[rows]) for rows in groups])
        if groups
        else np.empty((0, returns.shape[1]))
    )
    result = np.full(monthly.shape, np.nan)
    for month in range(24, len(monthly)):
        start = max(0, month - 60)
        history = monthly[start:month]
        previous = history[:-1]
        current = history[1:]
        valid = np.isfinite(previous) & np.isfinite(current)
        count = valid.sum(axis=0)
        x = np.where(valid, previous, 0.0)
        y = np.where(valid, current, 0.0)
        sum_x = x.sum(axis=0)
        sum_y = y.sum(axis=0)
        centered_sum = (x * y).sum(axis=0) - np.divide(
            sum_x * sum_y, count, out=np.zeros(monthly.shape[1]), where=count > 0
        )
        variance = (x * x).sum(axis=0) - np.divide(
            sum_x * sum_x, count, out=np.zeros(monthly.shape[1]), where=count > 0
        )
        slope = np.divide(
            centered_sum,
            variance,
            out=np.full(monthly.shape[1], np.nan),
            where=variance > 0.0,
        )
        intercept = np.divide(
            sum_y - slope * sum_x,
            count,
            out=np.full(monthly.shape[1], np.nan),
            where=count > 0,
        )
        expected = intercept + slope * monthly[month - 1]
        constant = (count >= 23) & (variance <= 0.0)
        expected[constant] = np.divide(
            sum_y[constant],
            count[constant],
            out=np.full(constant.sum(), np.nan),
            where=count[constant] > 0,
        )
        usable = (
            (count >= 23)
            & np.isfinite(monthly[month - 1])
            & np.isfinite(monthly[month])
            & np.isfinite(expected)
        )
        result[month, usable] = expected[usable] - monthly[month, usable]
    return _expand_monthly(result, ends, returns.shape)


def gammar(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    returns = _returns(arrays)
    market = _market_return(arrays, membership, returns)
    excess = returns - market[:, None]
    amount = _same_shape(returns, _get(arrays, "amount"), "amount")
    groups, ends = _month_groups(len(returns), dates)
    monthly = np.full((len(groups), returns.shape[1]), np.nan)
    for month, rows in enumerate(groups):
        if len(rows) < 9:
            continue
        current_rows = rows[:-1]
        next_rows = rows[1:]
        x1 = returns[current_rows]
        x2 = np.sign(excess[current_rows]) * amount[current_rows]
        y = excess[next_rows]
        valid = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
        count = valid.sum(axis=0)
        design = np.stack([np.ones(x1.shape), x1, x2], axis=2)
        coefficients = _batched_variable_lstsq(design, y, valid)
        usable = (count >= 8) & np.isfinite(coefficients[:, 2])
        monthly[month, usable] = -np.abs(coefficients[usable, 2])
    return _expand_monthly(monthly, ends, returns.shape)


def snr_enhanced_reversal(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    snr = _get(arrays, "a22_intraday_snr_trial", "intraday_snr", "snr_daily")
    close = _same_shape(snr, _get(arrays, "close"), "close")
    previous = _lag(close, 20)
    reversal = 1.0 - np.divide(
        close,
        previous,
        out=np.full(close.shape, np.nan),
        where=np.isfinite(previous) & (previous != 0),
    )
    member = _membership(snr, membership)
    weight = np.full(snr.shape, np.nan)
    for day in range(len(snr)):
        valid = member[day] & np.isfinite(snr[day])
        if valid.sum() < 2:
            continue
        low = np.min(snr[day, valid])
        high = np.max(snr[day, valid])
        if high - low > EPS:
            weight[day, valid] = (snr[day, valid] - low) / (high - low)
        else:
            weight[day, valid] = 0.0
    return weight * reversal


def tsmom(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    stock, _, ends = _monthly_returns(arrays, membership, dates)
    delta = 0.9
    expected = np.full(stock.shape[1], np.nan)
    variance = np.full(stock.shape[1], np.nan)
    excess = np.full(stock.shape, np.nan)
    result = np.full(stock.shape, np.nan)
    for month, value in enumerate(stock):
        valid = np.isfinite(value)
        first = valid & ~np.isfinite(expected)
        excess[month, valid & np.isfinite(expected)] = value[valid & np.isfinite(expected)] - expected[
            valid & np.isfinite(expected)
        ]
        if month >= 12:
            direction = np.sign(np.nanmean(excess[month - 12 : month], axis=0))
            usable = valid & np.isfinite(excess[month]) & np.isfinite(variance) & (variance > EPS)
            result[month, usable] = direction[usable] * excess[month, usable] / np.sqrt(
                variance[usable]
            )
        expected[first] = value[first]
        variance[first] = 0.0
        later = valid & ~first
        innovation = value[later] - expected[later]
        variance[later] = delta * variance[later] + (1.0 - delta) * innovation**2
        expected[later] = delta * expected[later] + (1.0 - delta) * value[later]
    return _expand_monthly(result, ends, _returns(arrays).shape)


def calendar_trend_factor(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    """用多尺度均线价格比保留原文的趋势信号部分。"""

    close = _get(arrays, "close")
    signals = []
    for window in (5, 10, 20, 60, 120, 250):
        average = _rolling_mean(close, window)
        signals.append(
            np.divide(
                average,
                close,
                out=np.full(close.shape, np.nan),
                where=np.isfinite(close) & (close != 0),
            )
            - 1.0
        )
    stacked = np.stack(signals)
    valid = np.isfinite(stacked)
    count = valid.sum(axis=0)
    return np.divide(
        np.where(valid, stacked, 0.0).sum(axis=0),
        count,
        out=np.full(close.shape, np.nan),
        where=count > 0,
    )


SPECS = (
    FactorSpec("趋势因子", calendar_trend_factor, "trial", ("close",), "保留原文多尺度均线/最新价信号并等权平均；未复现12个月因子收益预测回归。"),
    FactorSpec("区域指数 RI", regional_index, "strict", ("high", "low", "close"), "按原式N1=20、N2=5。", ("RI",)),
    FactorSpec("摇摆指数 SWI", swing_index, "strict", ("open", "high", "low", "close"), "按扫描页分段公式逐项计算。", ("SWI",)),
    FactorSpec("市值调整换手率", size_adjusted_turnover, "strict", ("turnover或volume+float_shares", "close", "float_shares"), "逐日回归ln(换手率)对ln(流通市值)，取残差。"),
    FactorSpec("趋势动量", trend_momentum, "trial", ("close",), "把原文11个时间尺度的信号等权平均；有换手率和流通股占比时再逐日剔除其一次关系。"),
    FactorSpec("价格时滞", price_delay, "strict", ("returns或close", "market_return或membership"), "按自然月回归当期及5阶滞后市场收益；无dates时每21日近似一个月。"),
    FactorSpec("价差偏离度", spread_bias, "trial", ("close", "全市场股票池"), "按250日相关性选择最多10只相似股票，使用标准化价格构造参考组合，再计算60日标准分。"),
    FactorSpec("基于月度收益的市场 Beta", monthly_market_beta, "trial", ("returns或close", "market_return或membership"), "传dates时使用真实月收益；无dates时每21日近似一个月。60月窗口、至少24月。", ("月度市场Beta", "基于月度收益Beta")),
    FactorSpec("收益季节性", return_seasonality, "strict", ("returns或close", "market_return或membership"), "取去年同月超额收益；无dates时每21日近似一个月。"),
    FactorSpec("收益季节性反转", return_seasonality_reversal, "strict", ("returns或close", "market_return或membership"), "取前1至11个月超额收益均值；无dates时每21日近似一个月。"),
    FactorSpec("协偏度（朱剑涛版）", zhu_coskewness, "strict", ("returns或close", "market_return或membership"), "20日窗口，至少15个有效收益。", ("朱剑涛协偏度",)),
    FactorSpec("非线性市值", nonlinear_market_cap, "trial", ("market_cap或close+total_shares",), "按根号总市值加权回归；残差以3倍MAD去极值后逐日标准化。"),
    FactorSpec("基于排序的动量", rank_momentum, "trial", ("returns或close", "全市场股票池"), "每日横截面排名标准化；默认取跳过最近1个月的过去12个月均值。", ("排序动量", "基于排序动量")),
    FactorSpec("异常尾部概率 E", abnormal_tail_e, "strict", ("returns或close", "market_return或membership"), "63日二次市场模型特质收益标准化，k=1.5。", ("异常尾部概率E",)),
    FactorSpec("异常尾部概率 S", abnormal_tail_s, "trial", ("returns或close", "market_return或membership"), "用左右尾直方分布的Hellinger距离近似原文高斯核积分。", ("异常尾部概率S",)),
    FactorSpec("资本收益过剩 CGO", capital_gain_overhang, "trial", ("close", "vwap_qfq", "turnover或volume+float_shares"), "复用现有日度CGO算法，采用100日持有存续权重；不是原文260周版本。", ("CGO", "资本利得突出量")),
    FactorSpec("价量相关性波动性因子 PV_corr_std", pv_corr_std, "strict", ("pv_corr_daily",), "每日分钟价量相关系数的20日标准差；有市值字段时再剔除对数市值关系。", ("PV_corr_std", "价量相关性波动性因子")),
    FactorSpec("价量相关性趋势因子 PV_corr_trend", pv_corr_trend, "strict", ("pv_corr_daily",), "每日分钟价量相关系数的20日时间斜率。", ("PV_corr_trend", "价量相关性趋势因子")),
    FactorSpec("价量相关性综合因子 PV_corr", pv_corr_composite, "trial", ("pv_corr_daily",), "20日均值和标准差逐日标准化后等权相加。", ("PV_corr", "价量相关性综合因子")),
    FactorSpec("价量相关性因子 CPV", cpv, "trial", ("pv_corr_daily", "close"), "均值和波动先剔除20日收益关系，再与趋势分项标准化相加。", ("CPV", "价量相关性因子")),
    FactorSpec("流动性冲击 LIQU", liquidity_shock, "strict", ("returns或close", "amount"), "当月Amihud相对过去12月均值的负变化；无dates时每21日近似一个月。", ("流动性冲击",)),
    FactorSpec("带条件的流动性冲击", conditional_liquidity_shock, "trial", ("returns或close", "amount"), "用滚动60月AR(1)残差近似原文ARMA(1,1)，至少24月。", ("带条件冲击",)),
    FactorSpec("非流动性因子 Gammar", gammar, "strict", ("returns或close", "market_return或membership", "amount"), "按月回归下一日超额收益，取成交额项系数的负绝对值；无dates时每21日近似一个月。", ("Gammar",)),
    FactorSpec("信噪比增强反转", snr_enhanced_reversal, "trial", ("日内信噪比每日字段", "close"), "将每日信噪比做横截面0至1缩放后乘20日反转。"),
    FactorSpec("时间序列动量 TSMOM", tsmom, "trial", ("returns或close",), "自然月收益采用delta=0.9的指数均值和波动率；无dates时每21日近似一个月。", ("TSMOM", "时间序列动量")),
)


UNSUPPORTED: dict[str, str] = {
    "特质波动率": "严格版本需要FF3因子收益。",
    "月度特质波动率": "严格版本需要FF3因子收益。",
    "特质偏度": "目录版本需要FF3回归残差。",
    "残差动量-基于 FF3": "缺少MKT、SMB、HML三因子收益。",
    "高频特异波动率": "缺少市场、规模、价值、反转和流动性分钟因子。",
    "非线性高频波动率": "缺少多因子回归得到的高频特质收益。",
    "极端下行风险": "2024版严格依赖FF3残差，三年定义也不一致。",
    "盈余公告回报": "缺少盈余公告日期。",
    "盈余公告股票跳动": "缺少盈余公告日期。",
    "公告跳跃": "缺少公告日期。",
    "行业动量-纵向切割": "缺少逐日行业分类。",
    "行业动量-横向切割": "缺少逐日行业分类。",
    "价量相关性趋势行业中性": "缺少逐日行业分类。",
    "收盘集合竞价成交量占比 BCVP": "日度矩阵没有收盘前5分钟成交量。",
    "价格自相关性合成因子（双序列差分）": "日度矩阵没有原公式所需的分钟分组序列。",
    "价格自相关性因子（单序列差分）": "日度矩阵没有原公式所需的分钟分组序列。",
    "成交量自相关性因子（单序列差分）": "日度矩阵没有原公式所需的分钟分组序列。",
    "成交量自相关性合成因子（双序列差分）": "日度矩阵没有原公式所需的分钟分组序列。",
    "APM 因子": "目录未给完整的开收盘超额收益修正办法。",
    "多层加权日内信噪比": "第二层、第三层及参数未给，且权重公式与文字矛盾。",
    "尾部风险": "严格公式先构造自然月市场尾部风险，目录没有统一的完整口径。",
}


FACTOR_REGISTRY: dict[str, FactorSpec] = {}
for _spec in SPECS:
    for _name in (_spec.name, *_spec.aliases):
        _key = _normalize(_name)
        if _key in FACTOR_REGISTRY and FACTOR_REGISTRY[_key] is not _spec:
            raise RuntimeError(f"因子名称重复：{_name}")
        FACTOR_REGISTRY[_key] = _spec

_UNSUPPORTED_NORMALIZED = {_normalize(name): (name, reason) for name, reason in UNSUPPORTED.items()}


def get_factor_metadata(name: str) -> dict[str, object]:
    key = _normalize(name)
    if key in FACTOR_REGISTRY:
        spec = FACTOR_REGISTRY[key]
        return {
            "name": spec.name,
            "status": spec.status,
            "required": spec.required,
            "note": spec.note,
            "aliases": spec.aliases,
        }
    if key in _UNSUPPORTED_NORMALIZED:
        original, reason = _UNSUPPORTED_NORMALIZED[key]
        return {
            "name": original,
            "status": "unsupported",
            "required": (),
            "note": reason,
            "aliases": (),
        }
    raise KeyError(f"没有登记因子：{name}")


def compute_factor(
    name: str,
    arrays: ArrayMap,
    membership: np.ndarray | None = None,
    dates: Sequence[object] | None = None,
) -> np.ndarray:
    """按名称计算因子，返回与输入日度矩阵同形状的float32数组。"""

    key = _normalize(name)
    if key in _UNSUPPORTED_NORMALIZED:
        original, reason = _UNSUPPORTED_NORMALIZED[key]
        raise UnsupportedFactorError(f"{original}暂不支持：{reason}")
    if key not in FACTOR_REGISTRY:
        raise KeyError(f"没有登记因子：{name}")
    spec = FACTOR_REGISTRY[key]
    result = _finish(spec.function(arrays, membership, dates))
    reference = next(
        (_matrix(value, field) for field, value in arrays.items() if np.asarray(value).ndim == 2),
        None,
    )
    if reference is None:
        raise ValueError("arrays中至少要有一个二维日度矩阵")
    if result.shape != reference.shape:
        raise ValueError(f"{spec.name}返回形状{result.shape}，应为{reference.shape}")
    return result


def supported_factor_names() -> list[str]:
    return [spec.name for spec in SPECS]


def factor_metadata() -> list[dict[str, object]]:
    rows = [get_factor_metadata(spec.name) for spec in SPECS]
    rows.extend(get_factor_metadata(name) for name in UNSUPPORTED)
    return rows
