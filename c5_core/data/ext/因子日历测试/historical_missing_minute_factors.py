# -*- coding: utf-8 -*-
"""把补算出的分钟级每日字段组合成历史因子。

输入格式与 ``historical_additional_factors`` 相同：字段名映射到
``(交易日, 股票)`` 二维数组。严格复现和试算版本在元数据中分开标明。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

import historical_standard_factors as standard


ArrayMap = Mapping[str, np.ndarray]
Calculator = Callable[[ArrayMap, np.ndarray | None, Sequence[object] | None], np.ndarray]
EPS = 1e-12
WINDOW = 20


@dataclass(frozen=True)
class FactorSpec:
    name: str
    function: Calculator
    status: str
    required: tuple[str, ...]
    note: str
    aliases: tuple[str, ...] = ()


def _normalize(name: str) -> str:
    return standard.normalize_factor_name(name)


def _matrix(value: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    if result.ndim != 2:
        raise ValueError(f"字段 {name} 必须是二维数组")
    return result


def _get(arrays: ArrayMap, name: str) -> np.ndarray:
    if name not in arrays:
        raise KeyError(f"缺少每日字段：{name}")
    return _matrix(arrays[name], name)


def _same_shape(reference: np.ndarray, value: np.ndarray, name: str) -> np.ndarray:
    result = _matrix(value, name)
    if result.shape != reference.shape:
        raise ValueError(f"字段 {name} 的形状是 {result.shape}，应为 {reference.shape}")
    return result


def _membership(reference: np.ndarray, membership: np.ndarray | None) -> np.ndarray:
    if membership is None:
        return np.ones(reference.shape, dtype=bool)
    result = np.asarray(membership, dtype=bool)
    if result.shape != reference.shape:
        raise ValueError(f"membership 的形状是 {result.shape}，应为 {reference.shape}")
    return result


def _masked_input(value: np.ndarray, membership: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    member = _membership(value, membership)
    return np.where(member, value, np.nan), member


def _finish(value: np.ndarray, membership: np.ndarray | None = None) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64).copy()
    if membership is not None:
        member = _membership(result, membership)
        result[~member] = np.nan
    result[~np.isfinite(result)] = np.nan
    return result.astype(np.float32)


def _rolling_mean(value: np.ndarray, window: int = WINDOW) -> np.ndarray:
    valid = np.isfinite(value)
    filled = np.where(valid, value, 0.0)
    sums = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(filled, axis=0)])
    counts = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(valid, axis=0)])
    result = np.full(value.shape, np.nan)
    if len(value) >= window:
        block_sum = sums[window:] - sums[:-window]
        block_count = counts[window:] - counts[:-window]
        result[window - 1 :] = np.where(block_count == window, block_sum / window, np.nan)
    return result


def _rolling_std(value: np.ndarray, window: int = WINDOW) -> np.ndarray:
    valid = np.isfinite(value)
    filled = np.where(valid, value, 0.0)
    sums = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(filled, axis=0)])
    squares = np.vstack(
        [np.zeros((1, value.shape[1])), np.cumsum(filled * filled, axis=0)]
    )
    counts = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(valid, axis=0)])
    result = np.full(value.shape, np.nan)
    if len(value) >= window:
        block_sum = sums[window:] - sums[:-window]
        block_square = squares[window:] - squares[:-window]
        block_count = counts[window:] - counts[:-window]
        variance = (block_square - block_sum * block_sum / window) / (window - 1)
        result[window - 1 :] = np.where(
            block_count == window, np.sqrt(np.maximum(variance, 0.0)), np.nan
        )
    return result


def _cross_section_zscore(value: np.ndarray, member: np.ndarray) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    for day in range(len(value)):
        valid = member[day] & np.isfinite(value[day])
        if valid.sum() < 2:
            continue
        row = value[day, valid]
        deviation = float(np.std(row, ddof=0))
        if deviation > EPS:
            result[day, valid] = (row - float(np.mean(row))) / deviation
    return result


def _market_cap(arrays: ArrayMap, reference: np.ndarray) -> np.ndarray | None:
    if "market_cap" in arrays:
        return _same_shape(reference, arrays["market_cap"], "market_cap")
    if "close" in arrays and "total_shares" in arrays:
        close = _same_shape(reference, arrays["close"], "close")
        shares = _same_shape(reference, arrays["total_shares"], "total_shares")
        return close * shares
    return None


def _remove_size(value: np.ndarray, arrays: ArrayMap, member: np.ndarray) -> np.ndarray:
    cap = _market_cap(arrays, value)
    if cap is None:
        return value.copy()
    log_cap = np.where(cap > 0, np.log(cap), np.nan)
    result = np.full(value.shape, np.nan)
    for day in range(len(value)):
        valid = member[day] & np.isfinite(value[day]) & np.isfinite(log_cap[day])
        if valid.sum() < 3:
            continue
        y = value[day, valid]
        x = log_cap[day, valid]
        centered_x = x - np.mean(x)
        denominator = float(np.dot(centered_x, centered_x))
        if denominator <= EPS:
            result[day, valid] = y - np.mean(y)
            continue
        slope = float(np.dot(centered_x, y - np.mean(y)) / denominator)
        result[day, valid] = y - np.mean(y) - slope * centered_x
    return result


def _rolling_field(arrays: ArrayMap, name: str, membership: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    value, member = _masked_input(_get(arrays, name), membership)
    return _rolling_mean(value), member


def bcvp(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    value, _ = _rolling_field(arrays, "close_auction_volume_share_5m", membership)
    return value


def _single_autocorrelation(
    arrays: ArrayMap,
    membership: np.ndarray | None,
    positive_name: str,
    negative_name: str,
) -> np.ndarray:
    positive, member = _rolling_field(arrays, positive_name, membership)
    negative, _ = _rolling_field(arrays, negative_name, membership)
    positive = _remove_size(positive, arrays, member)
    negative = _remove_size(negative, arrays, member)
    return _cross_section_zscore(positive, member) - _cross_section_zscore(negative, member)


def _pair_autocorrelation(
    arrays: ArrayMap,
    membership: np.ndarray | None,
    positive_name: str,
    negative_name: str,
) -> np.ndarray:
    positive, member = _rolling_field(arrays, positive_name, membership)
    negative, _ = _rolling_field(arrays, negative_name, membership)
    return _cross_section_zscore(positive, member) + _cross_section_zscore(negative, member)


def price_single(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    return _single_autocorrelation(
        arrays, membership, "price_diff_pos_corr", "price_diff_neg_corr"
    )


def price_pair(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    return _pair_autocorrelation(
        arrays, membership, "price_diff_pos_pair_corr", "price_diff_neg_pair_corr"
    )


def volume_single(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    return _single_autocorrelation(
        arrays, membership, "volume_diff_pos_corr", "volume_diff_neg_corr"
    )


def volume_pair(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    return _pair_autocorrelation(
        arrays, membership, "volume_diff_pos_pair_corr", "volume_diff_neg_pair_corr"
    )


def _direct(name: str) -> Calculator:
    def calculate(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
        return _get(arrays, name)

    return calculate


def volume_entropy(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    value, _ = _masked_input(_get(arrays, "minute_volume_bin_entropy_trial"), membership)
    return _rolling_std(value)


def _market_mean(value: np.ndarray, member: np.ndarray) -> np.ndarray:
    valid = member & np.isfinite(value)
    total = np.sum(np.where(valid, value, 0.0), axis=1)
    count = np.sum(valid, axis=1)
    return np.divide(total, count, out=np.full(len(value), np.nan), where=count > 0)


def _remove_momentum(stat: np.ndarray, close: np.ndarray, member: np.ndarray) -> np.ndarray:
    momentum = np.full(close.shape, np.nan)
    if len(close) > WINDOW:
        previous = close[:-WINDOW]
        momentum[WINDOW:] = np.divide(
            close[WINDOW:],
            previous,
            out=np.full(close[WINDOW:].shape, np.nan),
            where=np.isfinite(previous) & (np.abs(previous) > EPS),
        ) - 1.0
    result = np.full(stat.shape, np.nan)
    for day in range(len(stat)):
        valid = member[day] & np.isfinite(stat[day]) & np.isfinite(momentum[day])
        if valid.sum() < 3:
            continue
        y = stat[day, valid]
        x = momentum[day, valid]
        centered_x = x - np.mean(x)
        denominator = float(np.dot(centered_x, centered_x))
        if denominator <= EPS:
            result[day, valid] = y - np.mean(y)
            continue
        slope = float(np.dot(centered_x, y - np.mean(y)) / denominator)
        result[day, valid] = y - np.mean(y) - slope * centered_x
    return result


def apm(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    """用股票池上午、下午平均收益代替指数收益计算 APM。"""

    morning = _get(arrays, "morning_return")
    afternoon = _same_shape(morning, _get(arrays, "afternoon_return"), "afternoon_return")
    close = _same_shape(morning, _get(arrays, "close"), "close")
    member = _membership(morning, membership)
    market_morning = _market_mean(morning, member)
    market_afternoon = _market_mean(afternoon, member)
    stat = np.full(morning.shape, np.nan)

    for day in range(WINDOW - 1, len(morning)):
        rows = slice(day - WINDOW + 1, day + 1)
        x = np.concatenate([market_morning[rows], market_afternoon[rows]])
        y = np.vstack([morning[rows], afternoon[rows]])
        complete = member[rows].all(axis=0) & np.isfinite(y).all(axis=0) & np.isfinite(x).all()
        if not complete.any():
            continue
        selected = y[:, complete]
        centered_x = x - np.mean(x)
        denominator = float(np.dot(centered_x, centered_x))
        if denominator <= EPS:
            residual = selected - np.mean(selected, axis=0)
        else:
            centered_y = selected - np.mean(selected, axis=0)
            slopes = centered_x @ centered_y / denominator
            residual = centered_y - centered_x[:, None] * slopes[None, :]
        difference = residual[:WINDOW] - residual[WINDOW:]
        mean_difference = np.mean(difference, axis=0)
        deviation = np.std(difference, axis=0, ddof=1)
        values = np.divide(
            mean_difference * np.sqrt(WINDOW),
            deviation,
            out=np.full(mean_difference.shape, np.nan),
            where=deviation > EPS,
        )
        stat[day, complete] = values
    return _remove_momentum(stat, close, member)


def _month_end_indices(length: int, dates: Sequence[object] | None) -> np.ndarray:
    if length == 0:
        return np.array([], dtype=np.int64)
    if dates is None:
        return np.arange(WINDOW - 1, length, WINDOW, dtype=np.int64)
    if len(dates) != length:
        raise ValueError(f"dates 长度是 {len(dates)}，应为 {length}")
    months = np.asarray(dates, dtype="datetime64[M]")
    return np.flatnonzero(np.r_[months[:-1] != months[1:], True]).astype(np.int64)


def _code_order(arrays: ArrayMap, stock_count: int) -> np.ndarray:
    if "codes" not in arrays:
        return np.arange(stock_count, dtype=np.int64)
    codes = np.asarray(arrays["codes"])
    if codes.ndim != 1 or len(codes) != stock_count:
        raise ValueError(f"codes 必须是一维数组，长度应为 {stock_count}")
    return np.argsort(codes.astype(str), kind="stable")


def follow_wave(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    """月末计算绝对秩相关均值，并沿用到下一个月末之前。"""

    base = _get(arrays, "follow_wave_base")
    member = _membership(base, membership)
    result = np.full(base.shape, np.nan)
    stock_count = base.shape[1]
    neighbor_count = min(50, stock_count - 1)
    if neighbor_count <= 0:
        return result
    code_order = _code_order(arrays, stock_count)
    offsets = np.arange(stock_count)

    month_ends = _month_end_indices(len(base), dates)
    for day in month_ends:
        if day < WINDOW - 1:
            continue
        rows = slice(day - WINDOW + 1, day + 1)
        complete = member[rows].all(axis=0) & np.isfinite(base[rows]).all(axis=0)
        usable = np.flatnonzero(complete)
        if len(usable) < 2:
            continue
        values = base[rows]
        order = np.argsort(values, axis=0, kind="stable")
        ranks = np.empty_like(values)
        np.put_along_axis(
            ranks, order, np.arange(WINDOW, dtype=np.float64)[:, None], axis=0
        )
        ranks -= np.mean(ranks, axis=0)
        norms = np.sqrt(np.sum(ranks * ranks, axis=0))
        ranks = np.divide(
            ranks, norms[None, :], out=np.full(ranks.shape, np.nan), where=norms[None, :] > EPS
        )
        total = np.zeros(stock_count, dtype=np.float64)
        count = np.zeros(stock_count, dtype=np.int16)
        for offset in range(1, neighbor_count + 1):
            neighbor = np.empty(stock_count, dtype=np.int64)
            neighbor[code_order] = code_order[(offsets + offset) % stock_count]
            valid = complete & complete[neighbor]
            correlation = np.sum(ranks * ranks[:, neighbor], axis=0)
            total[valid] += np.abs(correlation[valid])
            count[valid] += 1
        valid = complete & (count > 0)
        result[day, valid] = total[valid] / count[valid]

    for position, day in enumerate(month_ends):
        next_end = (
            int(month_ends[position + 1])
            if position + 1 < len(month_ends)
            else len(result)
        )
        result[day + 1 : next_end] = result[day]
    return result


def multilayer_intraday_snr(arrays: ArrayMap, membership=None, dates=None) -> np.ndarray:
    """明确试算：第二层用原字段，第三层暂用其 3 日均值，二者各占一半。"""

    layer2, _ = _masked_input(_get(arrays, "a22_intraday_snr_trial"), membership)
    layer3_proxy = _rolling_mean(layer2, 3)
    return 0.5 * layer2 + 0.5 * layer3_proxy


SPECS = (
    FactorSpec(
        "收盘集合竞价成交量占比 BCVP", bcvp, "strict",
        ("close_auction_volume_share_5m",), "每日占比的20日均值。", ("BCVP",),
    ),
    FactorSpec(
        "价格自相关性因子（单序列差分）", price_single, "strict",
        ("price_diff_pos_corr", "price_diff_neg_corr"),
        "两个每日相关系数取20日均值；有市值字段时先剔除对数市值影响，再分别标准化后相减。",
        ("CDPP",),
    ),
    FactorSpec(
        "价格自相关性合成因子（双序列差分）", price_pair, "strict",
        ("price_diff_pos_pair_corr", "price_diff_neg_pair_corr"),
        "两个每日相关系数取20日均值，分别标准化后相加。", ("CDPDP",),
    ),
    FactorSpec(
        "成交量自相关性因子（单序列差分）", volume_single, "strict",
        ("volume_diff_pos_corr", "volume_diff_neg_corr"),
        "两个每日相关系数取20日均值；有市值字段时先剔除对数市值影响，再分别标准化后相减。",
        ("CDVV",),
    ),
    FactorSpec(
        "成交量自相关性合成因子（双序列差分）", volume_pair, "strict",
        ("volume_diff_pos_pair_corr", "volume_diff_neg_pair_corr"),
        "两个每日相关系数取20日均值，分别标准化后相加。", ("CDVDV",),
    ),
    FactorSpec(
        "SemiBeta（正市场收益、正资产收益）", _direct("semibeta_market_pos_asset_pos"), "strict",
        ("semibeta_market_pos_asset_pos",), "直接使用每日分钟收益计算值。",
        ("SemiBeta（正市场收益&正资产收益）",),
    ),
    FactorSpec(
        "SemiBeta（负市场收益、正资产收益）", _direct("semibeta_market_neg_asset_pos"), "strict",
        ("semibeta_market_neg_asset_pos",), "直接使用每日分钟收益计算值。",
        ("SemiBeta（负市场收益&正资产收益）",),
    ),
    FactorSpec(
        "SemiBeta（正市场收益、负资产收益）", _direct("semibeta_market_pos_asset_neg"), "strict",
        ("semibeta_market_pos_asset_neg",), "直接使用每日分钟收益计算值。",
        ("SemiBeta（正市场收益&负资产收益）",),
    ),
    FactorSpec(
        "SemiBeta（负市场收益、负资产收益）", _direct("semibeta_market_neg_asset_neg"), "strict",
        ("semibeta_market_neg_asset_neg",), "直接使用每日分钟收益计算值。",
        ("SemiBeta（负市场收益&负资产收益）",),
    ),
    FactorSpec(
        "成交量分桶熵", volume_entropy, "trial", ("minute_volume_bin_entropy_trial",),
        "每日熵的20日样本标准差；每日熵使用固定20桶试算。",
    ),
    FactorSpec(
        "最大涨幅", _direct("top10pct_minute_return"), "strict",
        ("top10pct_minute_return",), "直接使用当日前10%分钟收益的复合收益。",
    ),
    FactorSpec(
        "APM 因子", apm, "trial", ("morning_return", "afternoon_return", "close"),
        "20日40组收益共同回归，取上午减下午残差的t值，再剔除20日动量；市场收益用股票池平均收益代替指数。",
        ("APM",),
    ),
    FactorSpec(
        "异常高波动下的收益波动比", _direct("abnormal_high_vol_return_ratio_trial"), "trial",
        ("abnormal_high_vol_return_ratio_trial",), "直接使用每日试算字段。",
    ),
    FactorSpec(
        "随波逐流（50只代码邻居试算）", follow_wave, "trial", ("follow_wave_base",),
        "月末计算后沿用到下一个月末之前；新月末缺失时从当天停止沿用。按代码排序后取后续50只股票，计算20日高低额差序列绝对秩相关均值。未传codes时假定列已按代码排序。",
        ("随波逐流", "“随波逐流”因子"),
    ),
    FactorSpec(
        "多层加权日内信噪比（试算）", multilayer_intraday_snr, "trial",
        ("a22_intraday_snr_trial",),
        "明确试算：a22字段作第二层，3日均值作第三层替代值，固定各占一半；不是原文EMD第三层。",
        ("多层加权日内信噪比",),
    ),
)


FACTOR_REGISTRY: dict[str, FactorSpec] = {}
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {}
for _spec in SPECS:
    for _name in (_spec.name, *_spec.aliases):
        _key = _normalize(_name)
        if _key in FACTOR_REGISTRY and FACTOR_REGISTRY[_key] is not _spec:
            raise RuntimeError(f"因子名称重复：{_name}")
        FACTOR_REGISTRY[_key] = _spec
        REQUIRED_COLUMNS[_name] = _spec.required


def get_factor_metadata(name: str) -> dict[str, object]:
    key = _normalize(name)
    if key not in FACTOR_REGISTRY:
        raise KeyError(f"没有登记因子：{name}")
    spec = FACTOR_REGISTRY[key]
    return {
        "name": spec.name,
        "status": spec.status,
        "required": spec.required,
        "required_columns": spec.required,
        "note": spec.note,
        "aliases": spec.aliases,
    }


def compute_factor(
    name: str,
    arrays: ArrayMap,
    membership: np.ndarray | None = None,
    dates: Sequence[object] | None = None,
) -> np.ndarray:
    """按名称计算因子，返回与输入每日数组同形状的 float32 数组。"""

    key = _normalize(name)
    if key not in FACTOR_REGISTRY:
        raise KeyError(f"没有登记因子：{name}")
    reference = next(
        (_matrix(value, field) for field, value in arrays.items() if np.asarray(value).ndim == 2),
        None,
    )
    if reference is None:
        raise ValueError("arrays中至少要有一个二维每日数组")
    spec = FACTOR_REGISTRY[key]
    result = _matrix(spec.function(arrays, membership, dates), spec.name)
    if result.shape != reference.shape:
        raise ValueError(f"{spec.name} 返回形状 {result.shape}，应为 {reference.shape}")
    return _finish(result, membership)


calculate_factor = compute_factor


def supported_factor_names() -> list[str]:
    return [spec.name for spec in SPECS]


def factor_metadata() -> list[dict[str, object]]:
    return [get_factor_metadata(spec.name) for spec in SPECS]
