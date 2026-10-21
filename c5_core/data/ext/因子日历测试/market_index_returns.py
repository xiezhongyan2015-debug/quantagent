# -*- coding: utf-8 -*-
"""生成带备选指数的连续市场收益和指数序列。"""

from __future__ import annotations

import numpy as np


def _daily_close_returns(close: np.ndarray) -> np.ndarray:
    """只在相邻两个收盘价都有效时计算当日收益。"""
    values = np.asarray(close, dtype=np.float64)
    if values.ndim != 1:
        raise ValueError("指数收盘价必须是一维数组")
    result = np.full(values.shape, np.nan, dtype=np.float64)
    if len(values) < 2:
        return result
    previous = values[:-1]
    current = values[1:]
    valid = np.isfinite(previous) & np.isfinite(current) & (previous != 0)
    divided = np.full(previous.shape, np.nan, dtype=np.float64)
    np.divide(current, previous, out=divided, where=valid)
    divided[valid] -= 1.0
    result[1:] = divided
    return result


def combine_close_returns(
    primary_close: np.ndarray, fallback_close: np.ndarray
) -> np.ndarray:
    """优先取主指数收益，主指数当日收益无效时改用备选指数收益。"""
    primary = np.asarray(primary_close, dtype=np.float64)
    fallback = np.asarray(fallback_close, dtype=np.float64)
    if primary.shape != fallback.shape:
        raise ValueError("主指数和备选指数收盘价的形状必须相同")
    primary_return = _daily_close_returns(primary)
    fallback_return = _daily_close_returns(fallback)
    return np.where(np.isfinite(primary_return), primary_return, fallback_return)


def synthetic_close_from_returns(market_return: np.ndarray) -> np.ndarray:
    """把市场收益累乘为起点为1的连续指数序列。"""
    returns = np.asarray(market_return, dtype=np.float64)
    if returns.ndim != 1:
        raise ValueError("市场收益必须是一维数组")
    result = np.full(returns.shape, np.nan, dtype=np.float64)
    if len(result) == 0:
        return result
    result[0] = 1.0
    for day in range(1, len(result)):
        if np.isfinite(returns[day]) and np.isfinite(result[day - 1]):
            result[day] = result[day - 1] * (1.0 + returns[day])
    return result
