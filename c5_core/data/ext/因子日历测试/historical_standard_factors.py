# -*- coding: utf-8 -*-
"""三年因子日历中的标准日线因子。

输入是 ``字段名 -> (交易日, 股票)`` 的二维 NumPy 数组。每次只计算一个
因子，返回同形状的 ``float32`` 数组。这里不负责股票池选择和回测。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np


ArrayMap = Mapping[str, np.ndarray]
Calculator = Callable[[ArrayMap], np.ndarray]
EPS = 1e-12


@dataclass(frozen=True)
class FactorSpec:
    """一个因子的计算说明。"""

    name: str
    calculator: Calculator
    required_fields: tuple[str, ...]
    note: str
    aliases: tuple[str, ...] = ()


FACTOR_REGISTRY: dict[str, FactorSpec] = {}


def normalize_factor_name(name: str) -> str:
    """统一全半角、大小写、空格和常见标点。"""

    text = unicodedata.normalize("NFKC", str(name)).strip().lower()
    text = text.replace("（重复页合并）", "")
    return re.sub(r"[\s_\-—·:：,，。()（）/\\“”‘’\[\]【】]+", "", text)


def register_factor(
    name: str,
    required_fields: Sequence[str],
    note: str,
    aliases: Sequence[str] = (),
) -> Callable[[Calculator], Calculator]:
    """登记因子名称和计算函数。"""

    def decorator(function: Calculator) -> Calculator:
        spec = FactorSpec(
            name=name,
            calculator=function,
            required_fields=tuple(required_fields),
            note=note,
            aliases=tuple(aliases),
        )
        for item in (name, *aliases):
            key = normalize_factor_name(item)
            if key in FACTOR_REGISTRY:
                if FACTOR_REGISTRY[key] is spec:
                    continue
                raise ValueError(f"因子名称重复：{item}")
            FACTOR_REGISTRY[key] = spec
        return function

    return decorator


def _panel(data: ArrayMap, field: str) -> np.ndarray:
    if field not in data:
        raise KeyError(f"缺少字段：{field}")
    value = np.asarray(data[field], dtype=np.float64)
    if value.ndim != 2:
        raise ValueError(f"字段 {field} 必须是二维数组")
    return value


def _shape(data: ArrayMap, fields: Sequence[str]) -> tuple[int, int]:
    shapes = {_panel(data, field).shape for field in fields}
    if len(shapes) != 1:
        raise ValueError(f"字段形状不一致：{sorted(shapes)}")
    return next(iter(shapes))


def _finish(value: np.ndarray) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64)
    result[~np.isfinite(result)] = np.nan
    return result.astype(np.float32)


def calculate_factor(name: str, data: ArrayMap) -> np.ndarray:
    """按名称计算一个因子。"""

    key = normalize_factor_name(name)
    if key not in FACTOR_REGISTRY:
        raise KeyError(f"没有登记这个因子：{name}")
    spec = FACTOR_REGISTRY[key]
    _shape(data, spec.required_fields)
    result = _finish(spec.calculator(data))
    if result.shape != _panel(data, spec.required_fields[0]).shape:
        raise ValueError(f"因子 {spec.name} 返回了错误形状：{result.shape}")
    return result


def get_factor_metadata(name: str) -> dict[str, object]:
    """返回名称、所需字段和参数说明。"""

    spec = FACTOR_REGISTRY[normalize_factor_name(name)]
    return {
        "name": spec.name,
        "required_fields": spec.required_fields,
        "note": spec.note,
        "aliases": spec.aliases,
    }


def supported_factor_names() -> list[str]:
    """返回不含别名的因子名称。"""

    return sorted({spec.name for spec in FACTOR_REGISTRY.values()})


def _lag(value: np.ndarray, periods: int = 1) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    if periods < len(value):
        result[periods:] = value[:-periods]
    return result


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    result = np.full(np.broadcast_shapes(numerator.shape, denominator.shape), np.nan)
    np.divide(numerator, denominator, out=result, where=np.abs(denominator) > EPS)
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
    mean_square = _rolling_mean(value * value, window)
    return np.sqrt(np.maximum(mean_square - mean * mean, 0.0))


def _rolling_extreme(value: np.ndarray, window: int, mode: str) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    operation = np.min if mode == "min" else np.max
    for index in range(window - 1, len(value)):
        block = value[index - window + 1 : index + 1]
        complete = np.isfinite(block).all(axis=0)
        if complete.any():
            result[index, complete] = operation(block[:, complete], axis=0)
    return result


def _rolling_quantile(value: np.ndarray, window: int, quantile: float) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    for index in range(window - 1, len(value)):
        block = value[index - window + 1 : index + 1]
        complete = np.isfinite(block).all(axis=0)
        if complete.any():
            result[index, complete] = np.quantile(block[:, complete], quantile, axis=0)
    return result


def _rolling_skew(value: np.ndarray, window: int, negative: bool = False) -> np.ndarray:
    mean = _rolling_mean(value, window)
    second = _rolling_mean(value * value, window)
    third = _rolling_mean(value * value * value, window)
    variance = np.maximum(second - mean * mean, 0.0)
    central3 = third - 3.0 * mean * second + 2.0 * mean**3
    skew = _safe_divide(central3, np.power(variance, 1.5))
    if window > 2:
        skew *= np.sqrt(window * (window - 1.0)) / (window - 2.0)
    return -skew if negative else skew


def _ema(value: np.ndarray, window: int) -> np.ndarray:
    alpha = 2.0 / (window + 1.0)
    result = np.full(value.shape, np.nan)
    state = np.full(value.shape[1], np.nan)
    for index, row in enumerate(value):
        valid = np.isfinite(row)
        new = valid & ~np.isfinite(state)
        old = valid & np.isfinite(state)
        state[new] = row[new]
        state[old] = alpha * row[old] + (1.0 - alpha) * state[old]
        result[index, valid] = state[valid]
    return result


def _returns(close: np.ndarray, periods: int = 1) -> np.ndarray:
    return _safe_divide(close, _lag(close, periods)) - 1.0


def _true_range(data: ArrayMap) -> np.ndarray:
    high, low, close = (_panel(data, key) for key in ("high", "low", "close"))
    previous = _lag(close)
    return np.maximum.reduce([high - low, np.abs(high - previous), np.abs(low - previous)])


def _turnover(data: ArrayMap) -> np.ndarray:
    return _safe_divide(_panel(data, "volume"), _panel(data, "float_shares"))


def _capitalization_price(data: ArrayMap) -> np.ndarray:
    """返回用于计算市值的未复权价格。"""

    if "raw_close" in data:
        return _panel(data, "raw_close")
    if "qfq_ratio" in data:
        return _safe_divide(_panel(data, "close"), _panel(data, "qfq_ratio"))
    return _panel(data, "close")


def _typical(data: ArrayMap) -> np.ndarray:
    return (_panel(data, "high") + _panel(data, "low") + _panel(data, "close")) / 3.0


STANDARD_NOTE = "原文未给具体窗口；按常用参数试算。"


@register_factor("总市值", ("close", "total_shares"), "按原式：未复权收盘价×总股本。")
def total_market_value(data: ArrayMap) -> np.ndarray:
    return _capitalization_price(data) * _panel(data, "total_shares")


@register_factor("对数总市值", ("close", "total_shares"), "按原式取总市值自然对数。")
def log_total_market_value(data: ArrayMap) -> np.ndarray:
    return np.log(total_market_value(data))


@register_factor(
    "流通股市值",
    ("close", "float_shares"),
    "用数据中的流通股本；它不一定等同于指数公司口径的自由流通股本。",
    aliases=("自由流通股市值",),
)
def float_market_value(data: ArrayMap) -> np.ndarray:
    return _capitalization_price(data) * _panel(data, "float_shares")


@register_factor(
    "对数流通市值",
    ("close", "float_shares"),
    "用数据中的流通股本计算并取自然对数。",
    aliases=("对数自由流通市值",),
)
def log_float_market_value(data: ArrayMap) -> np.ndarray:
    return np.log(float_market_value(data))


@register_factor("换手率", ("volume", "float_shares"), "原文 K 月未定；按近 20 个交易日日换手率均值试算。", aliases=("月均换手率",))
def turnover_20d(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_turnover(data), 20)


@register_factor("换手波动", ("volume", "float_shares"), "原文 K 月未定；按近 20 个交易日日换手率标准差试算。")
def turnover_volatility(data: ArrayMap) -> np.ndarray:
    return _rolling_std(_turnover(data), 20)


@register_factor("换手率变异系数", ("volume", "float_shares"), "按近 20 日换手率标准差/均值试算。")
def turnover_cv(data: ArrayMap) -> np.ndarray:
    turnover = _turnover(data)
    return _safe_divide(_rolling_std(turnover, 20), _rolling_mean(turnover, 20))


@register_factor("月度异常换手率", ("volume", "float_shares"), "按原文使用 20 日均值/250 日均值。")
def abnormal_turnover(data: ArrayMap) -> np.ndarray:
    turnover = _turnover(data)
    return _safe_divide(_rolling_mean(turnover, 20), _rolling_mean(turnover, 250))


@register_factor("股票交易周转率", ("amount", "close", "total_shares"), "原文区间未定；按 20 日成交额/期初期末平均总市值试算。")
def stock_trading_turnover(data: ArrayMap) -> np.ndarray:
    market_value = total_market_value(data)
    average_end_value = (market_value + _lag(market_value, 19)) / 2.0
    return _safe_divide(_rolling_sum(_panel(data, "amount"), 20), average_end_value)


def _equity_issuance(data: ArrayMap, days: int) -> np.ndarray:
    market_value = total_market_value(data)
    close = _panel(data, "close")
    return np.log(_safe_divide(market_value, _lag(market_value, days))) - np.log(
        _safe_divide(close, _lag(close, days))
    )


@register_factor("综合股权发行（年度）", ("close", "total_shares"), "按 252 个交易日代表一年试算。", aliases=("综合股权发行-年度",))
def annual_equity_issuance(data: ArrayMap) -> np.ndarray:
    return _equity_issuance(data, 252)


@register_factor("综合股权发行（5 年）", ("close", "total_shares"), "按 1260 个交易日代表五年试算。")
def five_year_equity_issuance(data: ArrayMap) -> np.ndarray:
    return _equity_issuance(data, 1260)


@register_factor("短期反转", ("close",), "按总表举例使用 20 日累计收益。")
def short_reversal(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    return _returns(close, 20)


@register_factor("前 K 个月动量", ("close",), "K 未定；按 6 个月、126 个交易日试算。")
def medium_momentum(data: ArrayMap) -> np.ndarray:
    return _returns(_panel(data, "close"), 126)


@register_factor("长期反转", ("close",), "按原文第 t-59 月至 t-12 月，交易日近似为 1260 日至 252 日。")
def long_reversal(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    return _safe_divide(_lag(close, 252), _lag(close, 1260)) - 1.0


@register_factor("最近 52 周最高价因子", ("close",), "按 252 个交易日代表 52 周。", aliases=("最近 52 周的最高价",))
def high_52_week(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    return _safe_divide(close, _rolling_extreme(close, 252, "max"))


@register_factor("动量指标 MTM", ("close",), "N 未定；按常用 N=12 试算。")
def mtm(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    return close - _lag(close, 12)


@register_factor("变动速率 ROC", ("close",), "按常用 N=12、M=6，输出 ROC 的 6 日均值。")
def roc(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_returns(_panel(data, "close"), 12), 6)


@register_factor("趋势分数指标 TS", ("close",), "N 未定；按 N=20 试算。")
def trend_score(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    direction = np.where(close >= _lag(close), 1.0, -1.0)
    direction[~np.isfinite(close) | ~np.isfinite(_lag(close))] = np.nan
    return _rolling_sum(direction, 20)


@register_factor("乖离率 BIAS", ("close",), "N 未定；按 N=20 试算。")
def bias(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    average = _rolling_mean(close, 20)
    return 100.0 * _safe_divide(close - average, average)


@register_factor("异同离差乖离率 DBCD", ("close",), "按常用 N1=6、N2=16、N3=3 试算。")
def dbcd(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    average = _rolling_mean(close, 6)
    base = 100.0 * _safe_divide(close - average, average)
    return _ema(base - _lag(base, 16), 3)


@register_factor("多空指数 BBI", ("close",), "按原文取 3、6、12、20 日均线的平均。")
def bbi(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    return sum(_rolling_mean(close, window) for window in (3, 6, 12, 20)) / 4.0


@register_factor("平滑异同均线指标 MACD", ("close",), "按原文 12、26、9，输出 2×(DIF-DEA)。")
def macd(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    difference = _ema(close, 12) - _ema(close, 26)
    return 2.0 * (difference - _ema(difference, 9))


@register_factor("三重指数移动平均指标 TEMA", ("close",), "N 未定；按 N=20 试算。")
def tema(data: ArrayMap) -> np.ndarray:
    first = _ema(_panel(data, "close"), 20)
    second = _ema(first, 20)
    third = _ema(second, 20)
    return 3.0 * first - 3.0 * second + third


@register_factor("三角移动均线 TMA", ("close",), "N 未定；按 N=20，先 10 日再 11 日均线试算。")
def tma(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_rolling_mean(_panel(data, "close"), 10), 11)


@register_factor("相对强弱指标 RSI", ("close",), "按原文默认 N=14。")
def rsi(data: ArrayMap) -> np.ndarray:
    difference = _panel(data, "close") - _lag(_panel(data, "close"))
    up = np.maximum(difference, 0.0)
    down = np.maximum(-difference, 0.0)
    return 100.0 * _safe_divide(_ema(up, 14), _ema(up, 14) + _ema(down, 14))


@register_factor("钱德动量摆动指标 CMO", ("close",), "按常用 N=14 试算。")
def cmo(data: ArrayMap) -> np.ndarray:
    difference = _panel(data, "close") - _lag(_panel(data, "close"))
    up = _rolling_sum(np.maximum(difference, 0.0), 14)
    down = _rolling_sum(np.maximum(-difference, 0.0), 14)
    return 100.0 * _safe_divide(up - down, up + down)


@register_factor("相对动量指标 RMI", ("close",), "按常用动量间隔 4 日、平滑 14 日试算。")
def rmi(data: ArrayMap) -> np.ndarray:
    difference = _panel(data, "close") - _lag(_panel(data, "close"), 4)
    up = _ema(np.maximum(difference, 0.0), 14)
    down = _ema(np.maximum(-difference, 0.0), 14)
    return 100.0 * _safe_divide(up, up + down)


@register_factor("心理线 PSY", ("close",), "按原文默认 N=12。")
def psy(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    up = np.where(close > _lag(close), 1.0, 0.0)
    up[~np.isfinite(close) | ~np.isfinite(_lag(close))] = np.nan
    return 100.0 * _rolling_mean(up, 12)


@register_factor("真实强度指数 TSI", ("close",), "按常用 25、13 双重 EMA 试算。")
def tsi(data: ArrayMap) -> np.ndarray:
    momentum = _panel(data, "close") - _lag(_panel(data, "close"))
    numerator = _ema(_ema(momentum, 25), 13)
    denominator = _ema(_ema(np.abs(momentum), 25), 13)
    return 100.0 * _safe_divide(numerator, denominator)


@register_factor("优瑟指数 UI", ("close",), "N 未定；按 N=14 试算。")
def ulcer_index(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    drawdown = 100.0 * (_safe_divide(close, _rolling_extreme(close, 14, "max")) - 1.0)
    return np.sqrt(_rolling_mean(drawdown * drawdown, 14))


@register_factor("垂直水平过滤指标 VHF", ("high", "low", "close"), "N 未定；按常用 N=28 试算。")
def vhf(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    numerator = _rolling_extreme(_panel(data, "high"), 28, "max") - _rolling_extreme(
        _panel(data, "low"), 28, "min"
    )
    return _safe_divide(numerator, _rolling_sum(np.abs(close - _lag(close)), 28))


@register_factor("阿隆指标 AROON", ("high", "low"), "按原文默认 N=20。", aliases=("阿隆指数 AROON",))
def aroon(data: ArrayMap) -> np.ndarray:
    high, low = _panel(data, "high"), _panel(data, "low")
    result = np.full(high.shape, np.nan)
    window = 20
    for index in range(window - 1, len(high)):
        high_block = high[index - window + 1 : index + 1]
        low_block = low[index - window + 1 : index + 1]
        complete = np.isfinite(high_block).all(axis=0) & np.isfinite(low_block).all(axis=0)
        if complete.any():
            since_high = window - 1 - np.argmax(high_block[:, complete], axis=0)
            since_low = window - 1 - np.argmin(low_block[:, complete], axis=0)
            result[index, complete] = 100.0 * (since_low - since_high) / window
    return result


@register_factor("真实波动范围 TR", ("high", "low", "close"), "按原式计算。")
def true_range(data: ArrayMap) -> np.ndarray:
    return _true_range(data)


@register_factor("平均真实波幅 ATR", ("high", "low", "close"), "N 未定；按常用 N=14 的 EMA 试算。")
def atr(data: ArrayMap) -> np.ndarray:
    return _ema(_true_range(data), 14)


@register_factor("人气指数 AR", ("open", "high", "low"), "N 未定；按常用 N=26 试算。")
def ar(data: ArrayMap) -> np.ndarray:
    high, low, opening = (_panel(data, key) for key in ("high", "low", "open"))
    return 100.0 * _safe_divide(_rolling_sum(high - opening, 26), _rolling_sum(opening - low, 26))


@register_factor("买卖意愿指标 BR", ("high", "low", "close"), "N 未定；按常用 N=26 试算。")
def br(data: ArrayMap) -> np.ndarray:
    high, low, close = (_panel(data, key) for key in ("high", "low", "close"))
    previous = _lag(close)
    return 100.0 * _safe_divide(
        _rolling_sum(np.maximum(high - previous, 0.0), 26),
        _rolling_sum(np.maximum(previous - low, 0.0), 26),
    )


@register_factor("能量指标 CR", ("high", "low", "close"), "N 未定；按常用 N=26 试算。")
def cr(data: ArrayMap) -> np.ndarray:
    typical_previous = _lag(_typical(data))
    return 100.0 * _safe_divide(
        _rolling_sum(np.maximum(_panel(data, "high") - typical_previous, 0.0), 26),
        _rolling_sum(np.maximum(typical_previous - _panel(data, "low"), 0.0), 26),
    )


@register_factor("商品通道指标 CCI", ("high", "low", "close"), "按原文默认 N=20。")
def cci(data: ArrayMap) -> np.ndarray:
    typical = _typical(data)
    average = _rolling_mean(typical, 20)
    deviation = np.full(typical.shape, np.nan)
    for index in range(19, len(typical)):
        block = typical[index - 19 : index + 1]
        complete = np.isfinite(block).all(axis=0)
        deviation[index, complete] = np.mean(
            np.abs(block[:, complete] - average[index, complete]), axis=0
        )
    return _safe_divide(typical - average, 0.015 * deviation)


@register_factor("蔡金货币流量指标 CMF", ("high", "low", "close", "volume"), "N 未定；按常用 N=20 试算。")
def cmf(data: ArrayMap) -> np.ndarray:
    high, low, close, volume = (_panel(data, key) for key in ("high", "low", "close", "volume"))
    multiplier = _safe_divide(2.0 * close - high - low, high - low)
    return _safe_divide(_rolling_sum(multiplier * volume, 20), _rolling_sum(volume, 20))


@register_factor("货币流量指标 MFI", ("high", "low", "close", "volume"), "N 未定；按常用 N=14 试算。")
def mfi(data: ArrayMap) -> np.ndarray:
    typical = _typical(data)
    flow = typical * _panel(data, "volume")
    positive = np.where(typical > _lag(typical), flow, 0.0)
    negative = np.where(typical < _lag(typical), flow, 0.0)
    ratio = _safe_divide(_rolling_sum(positive, 14), _rolling_sum(negative, 14))
    return 100.0 - 100.0 / (1.0 + ratio)


@register_factor("日内动量指标 IMI", ("open", "close"), "N 未定；按常用 N=14 试算。")
def imi(data: ArrayMap) -> np.ndarray:
    body = _panel(data, "close") - _panel(data, "open")
    up = _rolling_sum(np.maximum(body, 0.0), 14)
    down = _rolling_sum(np.maximum(-body, 0.0), 14)
    return 100.0 * _safe_divide(up, up + down)


@register_factor("随机指标 KDJ", ("high", "low", "close"), "按常用 9、3、3，输出 J。")
def kdj(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    lowest = _rolling_extreme(_panel(data, "low"), 9, "min")
    highest = _rolling_extreme(_panel(data, "high"), 9, "max")
    rsv = 100.0 * _safe_divide(close - lowest, highest - lowest)
    k_value = _ema(rsv, 3)
    d_value = _ema(k_value, 3)
    return 3.0 * k_value - 2.0 * d_value


@register_factor("终极震荡指标 UOS", ("high", "low", "close"), "按常用 7、14、28 日窗口试算。")
def uos(data: ArrayMap) -> np.ndarray:
    high, low, close = (_panel(data, key) for key in ("high", "low", "close"))
    previous = _lag(close)
    buying_pressure = close - np.minimum(low, previous)
    true_range_value = np.maximum(high, previous) - np.minimum(low, previous)
    ratios = [
        _safe_divide(_rolling_sum(buying_pressure, window), _rolling_sum(true_range_value, window))
        for window in (7, 14, 28)
    ]
    return 100.0 * (4.0 * ratios[0] + 2.0 * ratios[1] + ratios[2]) / 7.0


@register_factor("梅斯线 Mass Index", ("high", "low"), "按常用 9、9、25 参数试算。")
def mass_index(data: ArrayMap) -> np.ndarray:
    spread = _panel(data, "high") - _panel(data, "low")
    first = _ema(spread, 9)
    second = _ema(first, 9)
    return _rolling_sum(_safe_divide(first, second), 25)


@register_factor("佳庆离散指标 CV", ("high", "low"), "按总表默认 N=20，比较 20 日前值。", aliases=("蔡金波动率指标 CVI",))
def chaikin_volatility(data: ArrayMap) -> np.ndarray:
    spread_ema = _ema(_panel(data, "high") - _panel(data, "low"), 20)
    return 100.0 * _safe_divide(spread_ema - _lag(spread_ema, 20), _lag(spread_ema, 20))


def _cumulative_signed_volume(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    direction = np.sign(close - _lag(close))
    increment = direction * volume
    result = np.full(close.shape, np.nan)
    state = np.zeros(close.shape[1])
    for index in range(len(close)):
        valid = np.isfinite(increment[index])
        state[valid] += increment[index, valid]
        result[index, np.isfinite(close[index])] = state[np.isfinite(close[index])]
    return result


@register_factor("能量指标 OBV", ("close", "volume"), "按原式累计涨跌方向成交量。")
def obv(data: ArrayMap) -> np.ndarray:
    return _cumulative_signed_volume(_panel(data, "close"), _panel(data, "volume"))


@register_factor("价量趋势指标 PVT", ("close", "volume"), "按原式累计收益率×成交量。")
def pvt(data: ArrayMap) -> np.ndarray:
    close, volume = _panel(data, "close"), _panel(data, "volume")
    increment = _returns(close) * volume
    result = np.full(close.shape, np.nan)
    state = np.zeros(close.shape[1])
    for index in range(len(close)):
        valid = np.isfinite(increment[index])
        state[valid] += increment[index, valid]
        result[index, np.isfinite(close[index])] = state[np.isfinite(close[index])]
    return result


def _volume_index(data: ArrayMap, positive: bool) -> np.ndarray:
    close, volume = _panel(data, "close"), _panel(data, "volume")
    result = np.full(close.shape, np.nan)
    state = np.full(close.shape[1], 1000.0)
    for index in range(1, len(close)):
        valid = np.isfinite(close[index]) & np.isfinite(close[index - 1]) & np.isfinite(volume[index]) & np.isfinite(volume[index - 1])
        condition = volume[index] > volume[index - 1] if positive else volume[index] < volume[index - 1]
        selected = valid & condition
        state[selected] *= close[index, selected] / close[index - 1, selected]
        result[index, valid] = state[valid]
    return result


@register_factor("正量指标 PVI", ("close", "volume"), "按原式，初值 1000。")
def pvi(data: ArrayMap) -> np.ndarray:
    return _volume_index(data, True)


@register_factor("负量指标 NVI", ("close", "volume"), "按原式，初值 1000。")
def nvi(data: ArrayMap) -> np.ndarray:
    return _volume_index(data, False)


@register_factor("成交量变动速率 VROC", ("volume",), "N 未定；按常用 N=12 试算。")
def vroc(data: ArrayMap) -> np.ndarray:
    volume = _panel(data, "volume")
    return 100.0 * _safe_divide(volume - _lag(volume, 12), _lag(volume, 12))


@register_factor("成交量摆动指标 VO", ("volume",), "N1、N2 未定；按常用 5、10 日试算。")
def volume_oscillator(data: ArrayMap) -> np.ndarray:
    volume = _panel(data, "volume")
    short, long = _rolling_mean(volume, 5), _rolling_mean(volume, 10)
    return 100.0 * _safe_divide(short - long, long)


@register_factor("成交量平滑异同均线指标 VMACD", ("volume",), "按 12、26、9 试算，输出柱值。")
def volume_macd(data: ArrayMap) -> np.ndarray:
    volume = _panel(data, "volume")
    difference = _ema(volume, 12) - _ema(volume, 26)
    return 2.0 * (difference - _ema(difference, 9))


@register_factor("成交量加权移动均线 VAMA", ("close", "volume"), "N 未定；以收盘价为 REAL，按 N=20 试算。")
def vama(data: ArrayMap) -> np.ndarray:
    close, volume = _panel(data, "close"), _panel(data, "volume")
    return _safe_divide(_rolling_sum(close * volume, 20), _rolling_sum(volume, 20))


@register_factor("成交量相对强弱指标 VRSI", ("close", "volume"), "N 未定；按 N=14 的 EMA 试算。")
def vrsi(data: ArrayMap) -> np.ndarray:
    close, volume = _panel(data, "close"), _panel(data, "volume")
    difference = close - _lag(close)
    up = np.where(difference > 0, volume, np.where(difference == 0, volume / 2.0, 0.0))
    down = np.where(difference < 0, volume, np.where(difference == 0, volume / 2.0, 0.0))
    return 100.0 * _safe_divide(_ema(up, 14), _ema(up, 14) + _ema(down, 14))


@register_factor("成交量比率指标 VR", ("close", "volume"), "N 未定；按常用 N=26 试算。")
def volume_ratio(data: ArrayMap) -> np.ndarray:
    close, volume = _panel(data, "close"), _panel(data, "volume")
    difference = close - _lag(close)
    up = np.where(difference > 0, volume, 0.0)
    down = np.where(difference < 0, volume, 0.0)
    return 100.0 * _safe_divide(_rolling_sum(up, 26), _rolling_sum(down, 26))


@register_factor("累积/派发线 AD", ("high", "low", "close", "volume"), "按原式累计资金流量乘数×成交量。")
def accumulation_distribution(data: ArrayMap) -> np.ndarray:
    high, low, close, volume = (_panel(data, key) for key in ("high", "low", "close", "volume"))
    increment = _safe_divide(2.0 * close - high - low, high - low) * volume
    return np.nancumsum(increment, axis=0)


@register_factor("佳庆指标 CHO", ("high", "low", "close", "volume"), "N1、N2 未定；按常用 3、10 日试算。")
def chaikin_oscillator(data: ArrayMap) -> np.ndarray:
    line = accumulation_distribution(data)
    return _ema(line, 3) - _ema(line, 10)


@register_factor("简易波动指标 EMV", ("high", "low", "volume"), "按原式先算单日 EMV，再取 14 日均值试算。")
def emv(data: ArrayMap) -> np.ndarray:
    high, low, volume = (_panel(data, key) for key in ("high", "low", "volume"))
    midpoint_move = (high + low - _lag(high) - _lag(low)) / 2.0
    box_ratio = _safe_divide(volume, high - low)
    return _rolling_mean(_safe_divide(midpoint_move, box_ratio), 14)


@register_factor("Amihud 非流动性因子", ("close", "amount"), "按原文取近 20 日 abs(日收益)/成交额均值。")
def amihud(data: ArrayMap) -> np.ndarray:
    close, amount = _panel(data, "close"), _panel(data, "amount")
    return _rolling_mean(_safe_divide(np.abs(_returns(close)), amount), 20)


@register_factor("负收益非流动性", ("close", "amount"), "按近 20 日下跌日 abs(日收益)/成交额均值试算。")
def negative_amihud(data: ArrayMap) -> np.ndarray:
    returns = _returns(_panel(data, "close"))
    value = _safe_divide(np.abs(returns), _panel(data, "amount"))
    value[returns >= 0] = np.nan
    result = np.full(value.shape, np.nan)
    for index in range(19, len(value)):
        block = value[index - 19 : index + 1]
        counts = np.isfinite(block).sum(axis=0)
        sums = np.nansum(block, axis=0)
        np.divide(sums, counts, out=result[index], where=counts > 0)
    return result


@register_factor("非流动性的变异系数 CVILLIQ", ("close", "amount"), "按原文使用 20 日窗口。")
def cv_illiquidity(data: ArrayMap) -> np.ndarray:
    daily = _safe_divide(np.abs(_returns(_panel(data, "close"))), _panel(data, "amount"))
    return _safe_divide(_rolling_std(daily, 20), _rolling_mean(daily, 20))


@register_factor("总波动率", ("close",), "K 未定；按 20 日日收益标准差试算。")
def total_volatility(data: ArrayMap) -> np.ndarray:
    return _rolling_std(_returns(_panel(data, "close")), 20)


@register_factor("总偏度", ("close",), "K 未定；按 20 日日收益偏度试算。")
def total_skewness(data: ArrayMap) -> np.ndarray:
    return _rolling_skew(_returns(_panel(data, "close")), 20)


@register_factor("负偏度系数 NCSKEW", ("close",), "按总表默认 n=20。", aliases=("负偏度系数",))
def negative_skewness(data: ArrayMap) -> np.ndarray:
    return _rolling_skew(_returns(_panel(data, "close")), 20, negative=True)


@register_factor("在险价值 VaR", ("close",), "置信水平未定；按 20 日 5% 收益分位数的相反数试算。")
def value_at_risk(data: ArrayMap) -> np.ndarray:
    return -_rolling_quantile(_returns(_panel(data, "close")), 20, 0.05)


@register_factor("条件风险价值 CVaR", ("close",), "置信水平未定；按 20 日 5% 左尾收益均值的相反数试算。")
def conditional_value_at_risk(data: ArrayMap) -> np.ndarray:
    returns = _returns(_panel(data, "close"))
    threshold = _rolling_quantile(returns, 20, 0.05)
    result = np.full(returns.shape, np.nan)
    for index in range(19, len(returns)):
        block = returns[index - 19 : index + 1]
        selected = block <= threshold[index]
        counts = selected.sum(axis=0)
        sums = np.where(selected, block, 0.0).sum(axis=0)
        np.divide(-sums, counts, out=result[index], where=counts > 0)
    return result


@register_factor("最大收益率", ("close",), "K、n 未定；按近 20 日最大单日收益试算。")
def maximum_return(data: ArrayMap) -> np.ndarray:
    return _rolling_extreme(_returns(_panel(data, "close")), 20, "max")


@register_factor("最小收益率", ("close",), "K、n 未定；按近 20 日最小单日收益的相反数试算。")
def minimum_return(data: ArrayMap) -> np.ndarray:
    return -_rolling_extreme(_returns(_panel(data, "close")), 20, "min")


@register_factor("隔夜收益率", ("open", "close"), "输出单日隔夜收益；周月汇总由调用方处理。")
def overnight_return(data: ArrayMap) -> np.ndarray:
    return _safe_divide(_panel(data, "open"), _lag(_panel(data, "close"))) - 1.0


@register_factor("日内收益率", ("open", "close"), "输出单日日内收益；周月汇总由调用方处理。")
def intraday_return(data: ArrayMap) -> np.ndarray:
    return _safe_divide(_panel(data, "close"), _panel(data, "open")) - 1.0


@register_factor("隔夜跳空", ("open", "close"), "按原文累计近 20 日隔夜对数收益绝对值。")
def overnight_gap(data: ArrayMap) -> np.ndarray:
    value = np.abs(np.log(_safe_divide(_panel(data, "open"), _lag(_panel(data, "close")))))
    return _rolling_sum(value, 20)


@register_factor("正向日内逆转的频率", ("open", "close"), "月度按 20 个交易日试算。")
def positive_intraday_reversal_frequency(data: ArrayMap) -> np.ndarray:
    overnight = overnight_return(data)
    intraday = intraday_return(data)
    return _rolling_mean(((overnight < 0) & (intraday > 0)).astype(float), 20)


@register_factor("反向日内逆转的频率", ("open", "close"), "月度按 20 个交易日试算。")
def negative_intraday_reversal_frequency(data: ArrayMap) -> np.ndarray:
    overnight = overnight_return(data)
    intraday = intraday_return(data)
    return _rolling_mean(((overnight > 0) & (intraday < 0)).astype(float), 20)


@register_factor("Q 棒指标 QST", ("open", "close"), "N 未定；按 N=20 试算。")
def qst(data: ArrayMap) -> np.ndarray:
    return _rolling_mean(_panel(data, "close") - _panel(data, "open"), 20)


@register_factor("信息离散度 ID", ("close",), "按原文用过去 12 月、跳过最近 1 月；交易日近似为 252 日和 21 日。", aliases=("信息离散度",))
def information_discreteness(data: ArrayMap) -> np.ndarray:
    returns = _returns(_panel(data, "close"))
    result = np.full(returns.shape, np.nan)
    for index in range(252, len(returns)):
        block = returns[index - 252 : index - 21]
        complete = np.isfinite(block).all(axis=0)
        cumulative = np.prod(1.0 + block[:, complete], axis=0) - 1.0
        down_share = np.mean(block[:, complete] < 0, axis=0)
        up_share = np.mean(block[:, complete] > 0, axis=0)
        result[index, complete] = np.sign(cumulative) * (down_share - up_share)
    return result


@register_factor("基于振幅切割的动量", ("high", "low", "close"), "按原文 160 日窗口，保留振幅最低 70% 日期。", aliases=("振幅切割后的长端动量",))
def amplitude_cut_momentum(data: ArrayMap) -> np.ndarray:
    amplitude = _safe_divide(_panel(data, "high"), _panel(data, "low")) - 1.0
    returns = _returns(_panel(data, "close"))
    result = np.full(amplitude.shape, np.nan)
    for index in range(159, len(amplitude)):
        amp = amplitude[index - 159 : index + 1]
        ret = returns[index - 159 : index + 1]
        complete = np.isfinite(amp).all(axis=0) & np.isfinite(ret).all(axis=0)
        if complete.any():
            cutoff = np.quantile(amp[:, complete], 0.7, axis=0)
            result[index, complete] = np.where(amp[:, complete] <= cutoff, ret[:, complete], 0.0).sum(axis=0)
    return result


@register_factor("理想振幅因子", ("high", "low", "close"), "N、λ 未定；按 20 日、上下各 20% 试算。")
def ideal_amplitude(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    amplitude = _safe_divide(_panel(data, "high"), _panel(data, "low")) - 1.0
    result = np.full(close.shape, np.nan)
    for index in range(19, len(close)):
        prices = close[index - 19 : index + 1]
        amps = amplitude[index - 19 : index + 1]
        complete = np.isfinite(prices).all(axis=0) & np.isfinite(amps).all(axis=0)
        if complete.any():
            order = np.argsort(prices[:, complete], axis=0)
            sorted_amp = np.take_along_axis(amps[:, complete], order, axis=0)
            result[index, complete] = sorted_amp[-4:].mean(axis=0) - sorted_amp[:4].mean(axis=0)
    return result


@register_factor("加速度动量", ("close",), "n 未定；按 20 日二次回归的 t² 系数试算。")
def acceleration_momentum(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    window = 20
    design = np.column_stack([np.ones(window), np.arange(window), np.arange(window) ** 2])
    projection = np.linalg.pinv(design)
    result = np.full(close.shape, np.nan)
    for index in range(window - 1, len(close)):
        block = close[index - window + 1 : index + 1]
        complete = np.isfinite(block).all(axis=0)
        if complete.any():
            result[index, complete] = (projection @ block[:, complete])[2]
    return result


@register_factor("动量加速度", ("close",), "按总表将后 6 个月累计收益减前 6 个月累计收益，半年按 126 日。")
def momentum_acceleration(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    recent = _safe_divide(close, _lag(close, 126)) - 1.0
    prior = _safe_divide(_lag(close, 126), _lag(close, 252)) - 1.0
    return recent - prior


@register_factor("收集派发指标 ACD", ("high", "low", "close"), "N 未定；按 20 日 DIF 累计值试算。")
def acd(data: ArrayMap) -> np.ndarray:
    high, low, close = (_panel(data, key) for key in ("high", "low", "close"))
    previous = _lag(close)
    real_low = np.minimum(low, previous)
    real_high = np.maximum(high, previous)
    difference = np.where(
        close > previous,
        close - real_low,
        np.where(close < previous, close - real_high, 0.0),
    )
    difference[~np.isfinite(previous) | ~np.isfinite(close)] = np.nan
    return _rolling_sum(difference, 20)


@register_factor("动态买卖气指标 ADTM", ("open", "high", "low"), "原文窗口未定；按常用 N=23 试算。")
def adtm(data: ArrayMap) -> np.ndarray:
    opening, high, low = (_panel(data, key) for key in ("open", "high", "low"))
    previous = _lag(opening)
    dtm = np.where(
        opening > previous,
        np.maximum(high - opening, opening - previous),
        0.0,
    )
    dbm = np.where(
        opening < previous,
        np.maximum(opening - low, previous - opening),
        0.0,
    )
    stm, sbm = _rolling_sum(dtm, 23), _rolling_sum(dbm, 23)
    return _safe_divide(stm - sbm, np.maximum(stm, sbm))


@register_factor("方向标准离差指标 DDI", ("high", "low"), "原文窗口未定；按常用 N=13 试算。")
def ddi(data: ArrayMap) -> np.ndarray:
    high, low = _panel(data, "high"), _panel(data, "low")
    previous_high, previous_low = _lag(high), _lag(low)
    movement = np.maximum(np.abs(high - previous_high), np.abs(low - previous_low))
    current_sum, previous_sum = high + low, previous_high + previous_low
    dmz = np.where(current_sum > previous_sum, movement, 0.0)
    dmf = np.where(current_sum < previous_sum, movement, 0.0)
    sum_dmz, sum_dmf = _rolling_sum(dmz, 13), _rolling_sum(dmf, 13)
    total = sum_dmz + sum_dmf
    return _safe_divide(sum_dmz, total) - _safe_divide(sum_dmf, total)


@register_factor("随机相对强弱指标 SRSI", ("close",), "RSI 与归一化窗口未定；均按 14 日试算。")
def stochastic_rsi(data: ArrayMap) -> np.ndarray:
    base = rsi(data)
    lowest = _rolling_extreme(base, 14, "min")
    highest = _rolling_extreme(base, 14, "max")
    return 100.0 * _safe_divide(base - lowest, highest - lowest)


@register_factor("随机动量指标 SMI", ("high", "low", "close"), "原文窗口未定；按 14、3、3 试算。")
def stochastic_momentum_index(data: ArrayMap) -> np.ndarray:
    highest = _rolling_extreme(_panel(data, "high"), 14, "max")
    lowest = _rolling_extreme(_panel(data, "low"), 14, "min")
    midpoint_distance = _panel(data, "close") - (highest + lowest) / 2.0
    half_range = (highest - lowest) / 2.0
    numerator = _ema(_ema(midpoint_distance, 3), 3)
    denominator = _ema(_ema(half_range, 3), 3)
    return 100.0 * _safe_divide(numerator, denominator)


@register_factor("相对波动率指标 RVI", ("high", "low"), "原文窗口未定；价格标准差取 10 日，方向平滑取 14 日。")
def relative_volatility_index(data: ArrayMap) -> np.ndarray:
    outputs = []
    for field in ("high", "low"):
        value = _panel(data, field)
        volatility = _rolling_std(value, 10)
        change = value - _lag(value)
        up = _ema(np.where(change > 0, volatility, 0.0), 14)
        down = _ema(np.where(change < 0, volatility, 0.0), 14)
        outputs.append(100.0 * _safe_divide(up, up + down))
    return (outputs[0] + outputs[1]) / 2.0


def _weighted_moving_average(value: np.ndarray, window: int) -> np.ndarray:
    result = np.full(value.shape, np.nan)
    weights = np.arange(1.0, window + 1.0)[:, None]
    divisor = float(weights.sum())
    for index in range(window - 1, len(value)):
        block = value[index - window + 1 : index + 1]
        complete = np.isfinite(block).all(axis=0)
        if complete.any():
            result[index, complete] = (block[:, complete] * weights).sum(axis=0) / divisor
    return result


@register_factor("估波指标 Coppock", ("close",), "原文窗口未定；按常用 ROC 11、14 月与 10 期加权均线试算。", aliases=("估波指标",))
def coppock(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    combined = 100.0 * (_returns(close, 231) + _returns(close, 294))
    return _weighted_moving_average(combined, 210)


@register_factor("钱德动量摆动平均指数 VIDYA", ("close",), "原文参数未定；CMO 取 14 日，基础平滑取 20 日。")
def vidya(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    adaptive = np.abs(cmo(data)) / 100.0 * (2.0 / 21.0)
    result = np.full(close.shape, np.nan)
    state = np.full(close.shape[1], np.nan)
    for index, row in enumerate(close):
        valid = np.isfinite(row)
        new = valid & ~np.isfinite(state)
        old = valid & np.isfinite(state) & np.isfinite(adaptive[index])
        state[new] = row[new]
        state[old] += adaptive[index, old] * (row[old] - state[old])
        result[index, valid] = state[valid]
    return result


@register_factor("动态动量指标 DMI", ("close",), "原文 N1、N2 未定；均按 20 日试算。", aliases=("相对强弱指标（原文 DMI 公式）",))
def dynamic_momentum_indicator(data: ArrayMap) -> np.ndarray:
    close = _panel(data, "close")
    volatility = _rolling_std(close, 20)
    return 14.0 * _safe_divide(_rolling_mean(volatility, 20), volatility)


@register_factor("克林格成交量摆动指标 KVO", ("high", "low", "close", "volume"), "按总表重复页明确给出的 34、55 日 EMA 试算。", aliases=("克林格成交量摆动指标 KVO（重复页合并）",))
def klinger_volume_oscillator(data: ArrayMap) -> np.ndarray:
    high, low, close, volume = (_panel(data, key) for key in ("high", "low", "close", "volume"))
    price_sum = high + low + close
    trend = np.sign(price_sum - _lag(price_sum))
    dm = high - low
    cm = np.full(dm.shape, np.nan)
    state = np.zeros(dm.shape[1])
    previous_trend = np.full(dm.shape[1], np.nan)
    for index in range(len(dm)):
        valid = np.isfinite(dm[index]) & np.isfinite(trend[index])
        same = valid & (trend[index] == previous_trend)
        changed = valid & ~same
        state[same] += dm[index, same]
        state[changed] = dm[index, changed]
        cm[index, valid] = state[valid]
        previous_trend[valid] = trend[index, valid]
    force = volume * np.abs(2.0 * (_safe_divide(dm, cm) - 1.0)) * trend * 100.0
    return _ema(force, 34) - _ema(force, 55)
