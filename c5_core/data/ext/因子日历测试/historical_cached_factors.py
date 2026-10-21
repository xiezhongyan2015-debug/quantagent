# -*- coding: utf-8 -*-
"""用已有每日 Parquet 字段计算历史因子。

输入仍是 ``字段名 -> (交易日, 股票)`` 的二维 NumPy 数组。需要分钟明细、
市场分钟收益或股票之间两两关系的项目列在 ``UNSUPPORTED`` 中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

import historical_standard_factors as standard


ArrayMap = Mapping[str, np.ndarray]
Calculator = Callable[[ArrayMap], np.ndarray]
EPS = 1e-12


@dataclass(frozen=True)
class CachedSpec:
    name: str
    calculator: Calculator
    required_columns: tuple[str, ...]
    note: str
    aliases: tuple[str, ...] = ()


_SPECS: dict[str, CachedSpec] = {}
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {}


def _normalize(name: str) -> str:
    return standard.normalize_factor_name(name)


def _register(
    name: str,
    columns: Sequence[str],
    note: str,
    aliases: Sequence[str] = (),
) -> Callable[[Calculator], Calculator]:
    def decorator(function: Calculator) -> Calculator:
        spec = CachedSpec(name, function, tuple(columns), note, tuple(aliases))
        for item in (name, *aliases):
            key = _normalize(item)
            if key in _SPECS and _SPECS[key] is not spec:
                raise ValueError(f"因子名称重复：{item}")
            _SPECS[key] = spec
            REQUIRED_COLUMNS[item] = spec.required_columns
        return function

    return decorator


def _array(data: ArrayMap, column: str) -> np.ndarray:
    if column not in data:
        raise KeyError(f"缺少每日字段：{column}")
    value = np.asarray(data[column], dtype=np.float64)
    if value.ndim != 2:
        raise ValueError(f"字段 {column} 必须是二维数组")
    return value


def _finish(value: np.ndarray) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    result[~np.isfinite(result)] = np.nan
    return result.astype(np.float32)


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan)
    np.divide(numerator, denominator, out=result, where=np.abs(denominator) > EPS)
    return result


def _lag(value: np.ndarray, periods: int = 1) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    if periods < len(value):
        result[periods:] = value[:-periods]
    return result


def _rolling_sum(value: np.ndarray, window: int) -> np.ndarray:
    valid = np.isfinite(value)
    filled = np.where(valid, value, 0.0)
    total = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(filled, axis=0)])
    count = np.vstack([np.zeros((1, value.shape[1])), np.cumsum(valid, axis=0)])
    result = np.full(value.shape, np.nan)
    if window <= len(value):
        sums = total[window:] - total[:-window]
        counts = count[window:] - count[:-window]
        result[window - 1 :] = np.where(counts == window, sums, np.nan)
    return result


def _rolling_mean(value: np.ndarray, window: int) -> np.ndarray:
    return _rolling_sum(value, window) / float(window)


def _rolling_std(value: np.ndarray, window: int) -> np.ndarray:
    mean = _rolling_mean(value, window)
    square_mean = _rolling_mean(value * value, window)
    return np.sqrt(np.maximum(square_mean - mean * mean, 0.0))


def _field_mean(column: str, window: int = 20) -> Calculator:
    return lambda data: _rolling_mean(_array(data, column), window)


def _field(column: str) -> Calculator:
    return lambda data: _array(data, column)


def _delegate(name: str, aliases: Sequence[str] = ()) -> None:
    metadata = standard.get_factor_metadata(name)

    @_register(
        name,
        metadata["required_fields"],
        f"由每日 OHLCV 字段计算；{metadata['note']}",
        aliases=aliases,
    )
    def delegated(data: ArrayMap, target: str = name) -> np.ndarray:
        return standard.calculate_factor(target, data)


@_register(
    "成交量比值",
    ("morning_afternoon_volume_ratio",),
    "每日字段已保存上午 30 分钟量/下午 30 分钟量；按近 20 日均值试算。",
)
def volume_time_ratio(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "morning_afternoon_volume_ratio"), 20)


@_register(
    "时间加权平均的相对价格位置 ARPP",
    ("time_avg_relative_price",),
    "直接使用分钟阶段已算好的每日时间平均相对价格位置。",
    aliases=("时间加权平均的股票相对价格位置 ARPP",),
)
def arpp(data: ArrayMap) -> np.ndarray:
    return _array(data, "time_avg_relative_price")


@_register(
    "高频收益偏度",
    ("realized_skew_1m",),
    "每日字段为 1 分钟收益偏度；按原文取近 20 日均值。",
)
def high_frequency_skewness(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "realized_skew_1m"), 20)


@_register(
    "高频已实现偏度",
    ("realized_skew_5m",),
    "采用每日5分钟已实现偏度，并取近20日均值。",
)
def high_frequency_realized_skewness_5m(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "realized_skew_5m"), 20)


def _neutralize_size(value: np.ndarray, close: np.ndarray, shares: np.ndarray) -> np.ndarray:
    market_value = close * shares
    result = np.full(value.shape, np.nan)
    for index in range(len(value)):
        y = value[index]
        x = np.log(market_value[index])
        valid = np.isfinite(y) & np.isfinite(x) & (market_value[index] > 0)
        if valid.sum() < 3:
            continue
        centered_x = x[valid] - x[valid].mean()
        denominator = np.dot(centered_x, centered_x)
        beta = np.dot(centered_x, y[valid] - y[valid].mean()) / denominator if denominator > EPS else 0.0
        result[index, valid] = y[valid] - y[valid].mean() - beta * centered_x
    return result


@_register(
    "价量相关性平均数因子",
    ("pv_corr_daily", "close", "total_shares"),
    "先取每日分钟收盘价与成交量相关系数的 20 日均值，再逐日剔除对数总市值的一次关系。",
)
def average_price_volume_correlation(data: ArrayMap) -> np.ndarray:
    average = _rolling_mean(_array(data, "pv_corr_daily"), 20)
    return _neutralize_size(average, _array(data, "close"), _array(data, "total_shares"))


@_register(
    "绝对收益与滞后成交量相关性",
    ("absret_lag_amount_corr",),
    "每日分钟相关系数取近 10 日均值。",
)
def abs_return_lag_amount_corr(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "absret_lag_amount_corr"), 10)


@_register(
    "滞后绝对收益与成交量相关性",
    ("lag_absret_amount_corr",),
    "每日分钟相关系数取近 10 日均值。",
)
def lag_abs_return_amount_corr(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "lag_absret_amount_corr"), 10)


@_register(
    "绝对收益与调整后滞后成交量相关性",
    ("j11_d04_adj_absret_lag_amount_corr_daily_trial",),
    "每日同分钟标准化后的相关系数取近 10 日均值。",
)
def adjusted_abs_return_lag_amount_corr(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(
        _array(data, "j11_d04_adj_absret_lag_amount_corr_daily_trial"), 10
    )


@_register(
    "朴素主动占比",
    ("j09_d11_naive_active_share_trial",),
    "直接使用早期试算每日文件中的朴素主动占比。",
)
def naive_active_share(data: ArrayMap) -> np.ndarray:
    return _array(data, "j09_d11_naive_active_share_trial")


@_register(
    "一致交易 TCV",
    ("consistent_trading_share_daily_trial",),
    "每日 5 分钟一致交易成交量占比取近 20 日均值。",
)
def consistent_trading(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "consistent_trading_share_daily_trial"), 20)


@_register(
    "一致买入交易 PCV",
    ("consistent_buying_share_daily_trial",),
    "每日 5 分钟一致买入成交量占比取近 20 日均值。",
    aliases=("一致买入交易因子 PCV", "一致买入交易因子", "一致买入交易"),
)
def consistent_buying(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "consistent_buying_share_daily_trial"), 20)


@_register(
    "一致卖出交易 NCV",
    ("consistent_selling_share_daily_trial",),
    "每日 5 分钟一致卖出成交量占比取近 20 日均值。",
)
def consistent_selling(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "consistent_selling_share_daily_trial"), 20)


@_register(
    "大的上行跳跃波动率",
    ("large_up_jump_5m",),
    "直接使用每日 5 分钟大幅上行跳跃平方和。",
)
def large_up_jump(data: ArrayMap) -> np.ndarray:
    return _array(data, "large_up_jump_5m")


@_register(
    "大的下行跳跃波动率",
    ("rvjn_5m", "small_down_jump_vol_5m"),
    "每日下行跳跃波动率减去每日小幅下行跳跃波动率。",
)
def large_down_jump(data: ArrayMap) -> np.ndarray:
    return np.maximum(_array(data, "rvjn_5m") - _array(data, "small_down_jump_vol_5m"), 0.0)


@_register(
    "小的上行跳跃波动率",
    ("up_jump_volatility_5m", "large_up_jump_5m"),
    "每日上行跳跃波动率减去每日大幅上行跳跃波动率。",
)
def small_up_jump(data: ArrayMap) -> np.ndarray:
    return np.maximum(_array(data, "up_jump_volatility_5m") - _array(data, "large_up_jump_5m"), 0.0)


@_register(
    "小的上下行跳跃波动不对称性",
    ("up_jump_volatility_5m", "large_up_jump_5m", "small_down_jump_vol_5m"),
    "小幅上行跳跃波动率减去小幅下行跳跃波动率。",
)
def small_jump_asymmetry(data: ArrayMap) -> np.ndarray:
    return small_up_jump(data) - _array(data, "small_down_jump_vol_5m")


def _abnormal_reversal(data: ArrayMap, positive: bool) -> np.ndarray:
    overnight = _array(data, "overnight_return")
    intraday = _safe_divide(_array(data, "close"), _array(data, "open")) - 1.0
    valid = np.isfinite(overnight) & np.isfinite(intraday)
    event = ((overnight < 0) & (intraday > 0)) if positive else ((overnight > 0) & (intraday < 0))
    event_value = np.where(valid, event.astype(float), np.nan)
    current = _rolling_mean(event_value, 20)
    history = _rolling_mean(event_value, 240)
    return _safe_divide(current, history)


@_register(
    "反向日内逆转的异常频率",
    ("overnight_return", "open", "close"),
    "没有自然月编号时，以 20 日频率/240 日频率近似当月/过去 12 月。",
)
def abnormal_negative_reversal(data: ArrayMap) -> np.ndarray:
    return _abnormal_reversal(data, False)


@_register(
    "正向日内逆转的异常频率",
    ("overnight_return", "open", "close"),
    "没有自然月编号时，以 20 日频率/240 日频率近似当月/过去 12 月。",
)
def abnormal_positive_reversal(data: ArrayMap) -> np.ndarray:
    return _abnormal_reversal(data, True)


@_register("成交额", ("amount",), "K 未定；按近 20 日日成交额均值试算。")
def average_amount(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_array(data, "amount"), 20)


@_register("成交额变动", ("amount",), "K 未定；按近 20 日日成交额标准差试算。", aliases=("成交额波动",))
def amount_volatility(data: ArrayMap) -> np.ndarray:
    return _rolling_std(_array(data, "amount"), 20)


@_register("成交额波动系数", ("amount",), "按原文排版使用近 20 日均值/标准差。")
def amount_volatility_ratio(data: ArrayMap) -> np.ndarray:
    amount = _array(data, "amount")
    return _safe_divide(_rolling_mean(amount, 20), _rolling_std(amount, 20))


@_register(
    "换手率分布均匀度 UTD",
    ("volume_std_1m", "float_shares"),
    "用每日分钟成交量标准差/流通股本近似分钟换手率标准差，再算 20 日标准差/均值；未做市值处理。",
)
def turnover_distribution_uniformity(data: ArrayMap) -> np.ndarray:
    daily = _safe_divide(_array(data, "volume_std_1m"), _array(data, "float_shares"))
    return _safe_divide(_rolling_std(daily, 20), _rolling_mean(daily, 20))


@_register(
    "累计振动升降指标 ASI",
    ("open", "high", "low", "close"),
    "按总表 SI=16×X/R×K，N 未定；按 26 日累计试算。",
)
def asi(data: ArrayMap) -> np.ndarray:
    opening, high, low, close = (_array(data, key) for key in ("open", "high", "low", "close"))
    previous_open, previous_close = _lag(opening), _lag(close)
    a = np.abs(high - previous_close)
    b = np.abs(low - previous_close)
    c = np.abs(high - low)
    d = np.abs(previous_close - previous_open)
    r = np.where(
        (a >= b) & (a >= c),
        a + 0.5 * b + 0.25 * d,
        np.where((b >= a) & (b >= c), b + 0.5 * a + 0.25 * d, c + 0.25 * d),
    )
    x = close - previous_close + 0.5 * (close - opening) + 0.25 * (previous_close - previous_open)
    si = 16.0 * _safe_divide(x, r) * np.maximum(a, b)
    return _rolling_sum(si, 26)


# 这些项目虽在 factor_mapping.csv 中写为 cached，但当前每日文件没有足够字段。
UNSUPPORTED: dict[str, str] = {
    "收盘集合竞价成交量占比 BCVP": "每日文件没有收盘前 5 分钟成交量，需要重新读取分钟数据。",
    "SemiBeta（正市场收益、正资产收益）": "缺少个股与市场逐分钟正负收益乘积。",
    "SemiBeta（负市场收益、正资产收益）": "缺少个股与市场逐分钟正负收益乘积。",
    "SemiBeta（正市场收益、负资产收益）": "缺少个股与市场逐分钟正负收益乘积。",
    "SemiBeta（负市场收益、负资产收益）": "缺少个股与市场逐分钟正负收益乘积。",
    "价格自相关性因子（单序列差分）": "每日文件没有分钟价差分组相关统计。",
    "价格自相关性合成因子（双序列差分）": "每日文件没有分钟价差双序列相关统计。",
    "成交量自相关性因子（单序列差分）": "每日文件没有分钟量差分组相关统计。",
    "成交量自相关性合成因子（双序列差分）": "每日文件没有分钟量差双序列相关统计。",
    "换手率调整的日成交量为零的数量": "现有缓存只保留有效交易日，无法还原零成交量日和实际应交易日数。",
    "最大涨幅": "现有 top1pct_minute_return 与原式最高 10% 分钟收益不一致。",
    "“随波逐流”因子": "还需要逐日跨股票秩相关和流通市值处理。",
    "异常高波动下的收益波动比": "每日文件没有异常分钟的收益波动比与波动率协方差。",
}


for _name, _aliases in (
    ("股票交易周转率", ()),
    ("换手率", ("月均换手率",)),
    ("换手波动", ()),
    ("换手率变异系数", ()),
    ("人气指数 AR", ()),
    ("非流动性的变异系数 CVILLIQ", ("非流动性的变异系数",)),
    ("心理线 PSY", ("心理线",)),
    ("变动速率 ROC", ("变动速率",)),
    ("动态买卖气指标 ADTM", ("动态买卖气指标",)),
    ("乖离率 BIAS", ("乖离率",)),
    ("异同离差乖离率 DBCD", ("异同离差乖离率",)),
    ("随机指标 KDJ", ("随机指标",)),
    ("平均真实波幅 ATR", ("平均真实波幅",)),
    ("月度异常换手率", ()),
    ("负收益非流动性", ()),
    ("佳庆离散指标 CV", ("佳庆离散指标",)),
    ("多空指数 BBI", ("多空指数",)),
    ("佳庆指标 CHO", ("佳庆指标",)),
    ("累积/派发线 AD", ("累积/派发线",)),
    ("在险价值 VaR", ("在险价值",)),
    ("梅斯线 Mass Index", ("梅斯线",)),
    ("Amihud 非流动性因子", ("Amihud 非流动性",)),
):
    _delegate(_name, _aliases)


def compute_factor(name: str, data: ArrayMap) -> np.ndarray:
    """按名称计算一个缓存因子，返回二维 float32。"""

    key = _normalize(name)
    if key not in _SPECS:
        unsupported = next((reason for item, reason in UNSUPPORTED.items() if _normalize(item) == key), None)
        if unsupported is not None:
            raise NotImplementedError(f"{name}：{unsupported}")
        raise KeyError(f"没有登记这个缓存因子：{name}")
    spec = _SPECS[key]
    shapes = {_array(data, column).shape for column in spec.required_columns}
    if len(shapes) != 1:
        raise ValueError(f"因子 {spec.name} 的字段形状不一致：{sorted(shapes)}")
    result = _finish(spec.calculator(data))
    expected = next(iter(shapes))
    if result.shape != expected:
        raise ValueError(f"因子 {spec.name} 返回了错误形状：{result.shape}")
    return result


calculate_factor = compute_factor


def get_factor_metadata(name: str) -> dict[str, object]:
    spec = _SPECS[_normalize(name)]
    return {
        "name": spec.name,
        "required_columns": spec.required_columns,
        "note": spec.note,
        "aliases": spec.aliases,
    }


def supported_factor_names() -> list[str]:
    return sorted({spec.name for spec in _SPECS.values()})
