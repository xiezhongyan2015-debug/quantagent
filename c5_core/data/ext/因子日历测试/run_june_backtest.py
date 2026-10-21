# -*- coding: utf-8 -*-
"""计算六月量价因子，并沿用既有统一方法完成评价。"""

from __future__ import annotations

import argparse
import gc
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

import build_june_minute_features as june_cache
from market_index_returns import combine_close_returns
import run_february_march_backtest as feb_mar
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent


FACTOR_META = {
    "J06_D01_STRUCTURED_REVERSAL_21D_10M": {
        "name": "结构化反转",
        "calendar_month": 6,
        "calendar_day": 1,
        "source_frequency": "日频，10分钟，21日窗口",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "formula_typo_corrected",
        "note": "低量10%时段按成交量倒数加权，其余时段按成交量加权；最终式Ret_mom按前文改为Rev_mom。",
    },
    "J06_D02_JUMP_INTENSITY": {
        "name": "跳跃强度",
        "calendar_month": 6,
        "calendar_day": 2,
        "source_frequency": "月频示例，分钟频率未定",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_parameters",
        "note": "显著性水平alpha和分钟间隔N未给，不能自行选择。",
    },
    "J06_D04_FUZZY_AMOUNT_RATIO_20D": {
        "name": "模糊金额比",
        "calendar_month": 6,
        "calendar_day": 4,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "两层5分钟总体标准差识别起雾时刻；月末把20日均值和标准差各乘0.5后相加。",
    },
    "J06_D05_PIN": {
        "name": "知情交易概率",
        "calendar_month": 6,
        "calendar_day": 5,
        "source_frequency": "区间频率未给",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_arrivals",
        "note": "缺买卖委托单、订单方向和到达时点，不能估计混合泊松模型。",
    },
    "J06_D06_CSAD_TIME_SERIES_120D": {
        "name": "CSAD模型（基于时序回归变形）",
        "calendar_month": 6,
        "calendar_day": 6,
        "source_frequency": "月频，120日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "月末为每只股票选过去120日收益相关性最高的9只股票，比较20日与120日CSAD波动比。",
    },
    "J06_D07_HIGH_FREQUENCY_RESILIENCY": {
        "name": "高频弹性因子",
        "calendar_month": 6,
        "calendar_day": 7,
        "source_frequency": "1分钟或5分钟，窗口未给",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_parameters",
        "note": "HP平滑参数、滚动窗口及频率恢复时间定义未给，不能严格复原。",
    },
    "J06_D08_WAITING_RESCUE_20D": {
        "name": "待著而救因子",
        "calendar_month": 6,
        "calendar_day": 8,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "相邻海量点不足5个实际分钟时删后一个；跟随量为其后同一交易时段5分钟成交量合计。",
    },
    "J06_D10_VOLATILITY_PANIC_20D": {
        "name": "波动率加剧-惊恐因子",
        "calendar_month": 6,
        "calendar_day": 10,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "市场日收益优先用中证全指SH000985；当日或前日收盘缺失时改用SH000906当日收益。20日加权收益均值和标准差各乘0.5后相加。",
    },
    "J06_D11_DOWNSIDE_REALIZED_VOL_5M": {
        "name": "下行已实现波动率",
        "calendar_month": 6,
        "calendar_day": 11,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "runnable_example_frequency",
        "note": "采用图片示例的5分钟对数收益，累加非正收益平方。",
    },
    "J06_D13_PATV_5M": {
        "name": "持续异常交易量",
        "calendar_month": 6,
        "calendar_day": 13,
        "source_frequency": "日频，5分钟，1日历史",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_example_parameters",
        "note": "采用图片允许的前1个交易日和5分钟方案；每段在全部有数据股票中排名，峰度采用总体Pearson峰度。",
    },
    "J06_D14_HERDING": {
        "name": "羊群行为",
        "calendar_month": 6,
        "calendar_day": 14,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_direction",
        "note": "缺主动买入和主动卖出成交单数。",
    },
    "J06_D15_LOCKED_CHIP_SHARE": {
        "name": "上方/下方锁定筹码占比",
        "calendar_month": 6,
        "calendar_day": 15,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_chip_distribution",
        "note": "缺逐价位筹码分布及其每日生成、迁移方法。",
    },
    "J06_D16_FIVE_LEVEL_SOIR": {
        "name": "逐档订单失衡率",
        "calendar_month": 6,
        "calendar_day": 16,
        "source_frequency": "盘口即时频率，日度汇总未给",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_book",
        "note": "缺五档委买量和委卖量，图片也未给日度汇总方法。",
    },
    "J06_D17_PV_CORR_TREND_20D": {
        "name": "价量相关性趋势因子",
        "calendar_month": 6,
        "calendar_day": 17,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "每日价量相关系数对时间回归取斜率，再横截面剔除对数市值、20日反转、换手率和波动率影响。",
    },
    "J06_D18_RETURN_MAGNITUDE_ID": {
        "name": "基于收益规模的信息离散度",
        "calendar_month": 6,
        "calendar_day": 18,
        "source_frequency": "月频推测，区间N未给",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_interval_definition",
        "note": "PRET窗口明确，但求和区间N及月末取值方法没有写清。",
    },
    "J06_D19_INTRADAY_CVAR_5PCT": {
        "name": "日内条件在险价值",
        "calendar_month": 6,
        "calendar_day": 19,
        "source_frequency": "月频，1分钟",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "formula_approximated",
        "note": "原图两式不能直接衔接；近似为每日取收益最低5%的分钟，以成交量加权计算条件均值，月末取当月日值均值。",
    },
    "J06_D20_CO_MIN": {
        "name": "个股收益最低时的价变共振",
        "calendar_month": 6,
        "calendar_day": 20,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "formula_conflict_and_missing_normalization",
        "note": "正文写最低5分钟，变量说明却写最高5分钟；反向排名标准化方法也未给。",
    },
    "J06_D21_SELL_REBOUND_SMALL_ORDER": {
        "name": "卖出反弹偏离小单因子",
        "calendar_month": 6,
        "calendar_day": 21,
        "source_frequency": "日频或尾盘",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_tick_data",
        "note": "缺逐笔成交、订单拆分关系、买卖方向和单笔成交量。",
    },
    "J06_D22_RESIDUAL_TRADE_IMBALANCE": {
        "name": "反转残差成交不平衡因子",
        "calendar_month": 6,
        "calendar_day": 22,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_trade_direction",
        "note": "缺逐笔主买和主卖成交单数，不能计算成交不平衡。",
    },
    "J06_D23_IDIOSYNCRATIC_OVERNIGHT_VOL_20D": {
        "name": "特质隔夜收益波动率",
        "calendar_month": 6,
        "calendar_day": 23,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "用全部具备完整20日数据的股票做PCA；先去均值、不做方差标准化。",
    },
    "J06_D25_LARGE_BUY_SHARE": {
        "name": "大单买入占比",
        "calendar_month": 6,
        "calendar_day": 25,
        "source_frequency": "月频20日或周频5日",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_ids",
        "note": "缺逐笔订单号、叫买叫卖号、买卖方向和订单级成交额。",
    },
    "J06_D26_PRSI": {
        "name": "单位成交额的百分比实现价差",
        "calendar_month": 6,
        "calendar_day": 26,
        "source_frequency": "逐笔频率，日度汇总未给",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_book_and_tick_data",
        "note": "缺逐笔成交、买一卖一报价和买卖方向；价格时点表述也不一致。",
    },
    "J06_D27_CHIP_PENETRATION": {
        "name": "筹码穿透率",
        "calendar_month": 6,
        "calendar_day": 27,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_chip_distribution",
        "note": "缺盈利筹码占比winner及其逐价位筹码生成方法。",
    },
    "J06_D28_HIGH_VOLUME_AVG_TRADE_AMOUNT": {
        "name": "成交量最高时刻笔均成交金额",
        "calendar_month": 6,
        "calendar_day": 28,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_count",
        "note": "分钟库没有成交笔数DealNum，不能计算笔均成交金额。",
    },
    "J06_D29_ENHANCED_CHIP_RETURN": {
        "name": "筹码收益增强",
        "calendar_month": 6,
        "calendar_day": 29,
        "source_frequency": "日频，250日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "formula_conflict",
        "note": "卡片把加权成本直接称为收益，最终合成式两个权重也都写成1-ret20，不能擅自修正。",
    },
    "J06_D30_LONG_BIG_ORDER_COMPOSITE": {
        "name": "基于大小单和成交时长的精选复合交易占比",
        "calendar_month": 6,
        "calendar_day": 30,
        "source_frequency": "日频，逐笔",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_ids_and_duration",
        "note": "缺委托编号、逐笔时间、买卖方向和订单成交时长。",
    },
}


BASE_COLUMNS = [
    "date",
    "code",
    "open",
    "close",
    "volume",
    "amount",
    "float_shares",
    "qfq_ratio",
    "vwap_outside_range_1pct",
    "overnight_return",
]

JUNE_COLUMNS = [
    "date",
    "code",
    "structure_reversal_21d_10m",
    "fuzzy_amount_ratio_daily",
    "wait_rescue_follow_daily",
    "downside_realized_vol_5m",
    "intraday_cvar_5pct_daily",
    "intraday_volatility_1m",
    "pv_corr_daily",
    *june_cache.ATV_COLUMNS,
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-cache-dir",
        default=str(
            BASE_DIR / "february_march_results" / "cache" / "daily_by_stock"
        ),
    )
    parser.add_argument(
        "--june-cache-dir",
        default=str(
            BASE_DIR / "june_factor_results" / "cache" / "june_daily_by_stock"
        ),
    )
    parser.add_argument(
        "--output-dir", default=str(BASE_DIR / "june_factor_results" / "final")
    )
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def _compact(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


def _rolling_slope(values: np.ndarray, window: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    if source.ndim != 2:
        raise ValueError("滚动斜率输入必须是二维矩阵")
    result = np.full(source.shape, np.nan, dtype=np.float64)
    x = np.arange(window, dtype=np.float64)
    x -= x.mean()
    denominator = float(x @ x)
    for end in range(window - 1, len(source)):
        block = source[end - window + 1 : end + 1]
        valid = np.isfinite(block).all(axis=0)
        if valid.any():
            result[end, valid] = (x @ block[:, valid]) / denominator
    return result


def _rolling_return(close: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(close, dtype=np.float64)
    result = np.full(values.shape, np.nan, dtype=np.float64)
    previous = values[:-window]
    current = values[window:]
    valid = np.isfinite(current) & np.isfinite(previous) & (previous > 0)
    np.divide(current, previous, out=result[window:], where=valid)
    result[window:] -= 1.0
    return result


def _month_to_date_mean(values: np.ndarray, dates: pd.DatetimeIndex) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    result = np.full(source.shape, np.nan, dtype=np.float64)
    periods = dates.to_period("M")
    starts = np.r_[0, np.flatnonzero(periods[1:] != periods[:-1]) + 1]
    ends = np.r_[starts[1:], len(dates)]
    for start, end in zip(starts, ends):
        block = source[start:end]
        cumulative = np.nancumsum(np.where(np.isfinite(block), block, 0.0), axis=0)
        count = np.cumsum(np.isfinite(block), axis=0)
        np.divide(cumulative, count, out=result[start:end], where=count > 0)
    return result


def _mean_std_composite(values: np.ndarray, window: int = 20) -> np.ndarray:
    """把滚动均值和总体标准差各乘0.5后相加。"""
    mean, std = january.rolling_mean_std(values, window)
    return _compact(0.5 * mean + 0.5 * std)


def _compute_volatility_panic(
    daily_return: np.ndarray,
    market_return: np.ndarray,
    intraday_volatility: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    """按中证全指收益和日内波动率计算波动率加剧-惊恐因子。"""
    stock = np.asarray(daily_return, dtype=np.float32)
    market = np.asarray(market_return, dtype=np.float32)
    intraday = np.asarray(intraday_volatility, dtype=np.float32)
    if stock.ndim != 2 or intraday.shape != stock.shape:
        raise ValueError("个股收益和日内波动率必须是形状相同的二维矩阵")
    if market.ndim != 1 or len(market) != len(stock):
        raise ValueError("市场收益必须是一维数组，且交易日数量与个股收益一致")
    market_by_day = market[:, None]
    denominator = np.abs(stock) + np.abs(market_by_day) + np.float32(0.1)
    fear = np.abs(stock - market_by_day) / denominator
    adjusted_return = fear * stock * intraday
    return _mean_std_composite(adjusted_return, window)


def _read_qlib_close(
    qlib_dir: Path, instrument: str, dates: pd.DatetimeIndex
) -> pd.Series:
    path = qlib_dir / "features" / instrument.lower() / "close.day.bin"
    if not path.exists():
        raise FileNotFoundError(f"没有找到指数收盘文件：{path}")
    raw = np.fromfile(path, dtype="<f4")
    if len(raw) < 2:
        raise ValueError(f"指数收盘文件内容不完整：{path}")
    start = int(raw[0])
    calendar = pd.to_datetime(
        pd.read_csv(qlib_dir / "calendars" / "day.txt", header=None)[0]
    )
    index_dates = pd.DatetimeIndex(calendar[start : start + len(raw) - 1])
    close = pd.Series(raw[1:].astype(np.float64), index=index_dates)
    return close


def _read_qlib_close_return(
    qlib_dir: Path,
    instrument: str,
    dates: pd.DatetimeIndex,
    fallback_instrument: str | None = None,
) -> np.ndarray:
    """读取指数收益，可在主指数相邻收盘缺失时使用备选指数。"""
    primary_series = _read_qlib_close(qlib_dir, instrument, dates)
    if fallback_instrument is None:
        calendar = primary_series.index
        fallback_close = np.full(len(calendar), np.nan, dtype=np.float64)
    else:
        fallback_series = _read_qlib_close(qlib_dir, fallback_instrument, dates)
        calendar = primary_series.index.union(fallback_series.index).sort_values()
        fallback_close = fallback_series.reindex(calendar).to_numpy(dtype=np.float64)
    primary_close = primary_series.reindex(calendar).to_numpy(dtype=np.float64)
    combined = pd.Series(
        combine_close_returns(primary_close, fallback_close), index=calendar
    )
    return combined.reindex(dates).to_numpy(dtype=np.float64)


def _compute_patv_long(
    panel: pd.DataFrame,
) -> np.ndarray:
    """在全部有数据股票中逐段排名，再按行计算PATV。"""
    row_count = len(panel)
    count = np.zeros(row_count, dtype=np.uint8)
    sums = [np.zeros(row_count, dtype=np.float64) for _ in range(4)]

    for position, column in enumerate(june_cache.ATV_COLUMNS, start=1):
        source = panel[column]
        rank = source.groupby(panel["date"], sort=False).rank(
            method="average", pct=True
        ).to_numpy(dtype=np.float64)
        valid = np.isfinite(rank)
        count[valid] += 1
        power = rank.copy()
        for order in range(4):
            sums[order][valid] += power[valid]
            power[valid] *= rank[valid]
        if position % 12 == 0:
            print(f"  PATV横截面排名 {position}/48", flush=True)

    result = np.full(row_count, np.nan, dtype=np.float32)
    chunk_size = 250_000
    for start in range(0, row_count, chunk_size):
        stop = min(start + chunk_size, row_count)
        valid = count[start:stop] == len(june_cache.ATV_COLUMNS)
        if not valid.any():
            continue
        n = float(len(june_cache.ATV_COLUMNS))
        mean = sums[0][start:stop][valid] / n
        raw2 = sums[1][start:stop][valid] / n
        raw3 = sums[2][start:stop][valid] / n
        raw4 = sums[3][start:stop][valid] / n
        second = np.maximum(raw2 - mean * mean, 0.0)
        fourth = (
            raw4
            - 4.0 * mean * raw3
            + 6.0 * mean * mean * raw2
            - 3.0 * mean**4
        )
        good = second > 1e-15
        current = np.full(len(mean), np.nan, dtype=np.float64)
        current[good] = (
            mean[good] / np.sqrt(second[good])
            + fourth[good] / second[good] ** 2
        )
        current_result = result[start:stop]
        current_result[valid] = current.astype(np.float32)
    return result


def _collapse_june_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """先算PATV，再立即移除48个ATV字段以减少后续合并占用。"""
    panel["patv_daily"] = _compute_patv_long(panel)
    panel.drop(columns=june_cache.ATV_COLUMNS, inplace=True)
    return panel


def _compute_csad_monthly(
    daily_return: np.ndarray,
    membership: np.ndarray,
    dates: pd.DatetimeIndex,
    min_stocks: int,
    window: int = 120,
    short_window: int = 20,
) -> np.ndarray:
    """按月末为每只股票建立9只最相关股票的CSAD组合。"""
    result = np.full(daily_return.shape, np.nan, dtype=np.float32)
    month_end = january.original_signal_mask(dates, "monthly", str(dates[0].date()))
    for sequence, end in enumerate(np.flatnonzero(month_end), start=1):
        if end < window - 1:
            continue
        block = daily_return[end - window + 1 : end + 1].astype(np.float64)
        complete = np.isfinite(block).sum(axis=0) >= window - 20
        current = membership[end] & complete
        indices = np.flatnonzero(current)
        if len(indices) < max(min_stocks, 10):
            continue
        correlation = pd.DataFrame(block[:, indices]).corr(
            min_periods=window - 20
        ).to_numpy(dtype=np.float64)
        for local, stock in enumerate(indices):
            row = correlation[local].copy()
            row[local] = np.nan
            candidates = np.flatnonzero(np.isfinite(row))
            if len(candidates) < 9:
                continue
            nearest = candidates[np.argpartition(row[candidates], -9)[-9:]]
            related = indices[nearest]
            center = block[:, stock]
            peers = block[:, related]
            valid_days = np.isfinite(center) & np.isfinite(peers).all(axis=1)
            if valid_days.sum() < window - 20:
                continue
            csad = np.full(window, np.nan, dtype=np.float64)
            csad[valid_days] = np.sum(
                np.abs(peers[valid_days] - center[valid_days, None]), axis=1
            ) / 10.0
            long_valid = np.isfinite(csad) & np.isfinite(center)
            short_valid = long_valid[-short_window:]
            if long_valid.sum() < window - 20 or short_valid.sum() < short_window:
                continue
            center_long_std = np.std(center[long_valid], ddof=0)
            center_short_std = np.std(center[-short_window:][short_valid], ddof=0)
            if center_long_std <= 0 or center_short_std <= 0:
                continue
            long_ratio = np.std(csad[long_valid], ddof=0) / center_long_std
            short_ratio = (
                np.std(csad[-short_window:][short_valid], ddof=0)
                / center_short_std
            )
            if np.isfinite(long_ratio) and long_ratio > 0:
                result[end, stock] = -short_ratio / long_ratio
        if sequence % 12 == 0:
            print(f"  CSAD已计算到 {dates[end].date()}", flush=True)
    return result


def _compute_overnight_idiosyncratic_volatility(
    overnight_return: np.ndarray,
    min_stocks: int,
    window: int = 20,
) -> np.ndarray:
    """用全部完整股票做滚动PCA，并返回各股票残差标准差。"""
    values = np.asarray(overnight_return, dtype=np.float64)
    result = np.full(values.shape, np.nan, dtype=np.float32)
    for end in range(window - 1, len(values)):
        block = values[end - window + 1 : end + 1]
        complete = np.isfinite(block).all(axis=0)
        indices = np.flatnonzero(complete)
        if len(indices) < min_stocks:
            continue
        current = block[:, indices]
        centered = current - np.mean(current, axis=0, keepdims=True)
        if not np.isfinite(centered).all():
            continue
        u, singular, _ = np.linalg.svd(centered, full_matrices=False)
        variance = singular * singular
        total = float(np.sum(variance))
        if total <= 0:
            continue
        component_count = 1 if variance[0] / total >= 0.20 else 2
        scores = u[:, :component_count] * singular[:component_count]
        design = np.column_stack([np.ones(window), scores])
        coefficients, _, _, _ = np.linalg.lstsq(design, current, rcond=None)
        residual = current - design @ coefficients
        result[end, indices] = np.std(residual, axis=0, ddof=0).astype(np.float32)
    return result


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        rows.append({"factor": code, **meta, "calculated": code in factors})
    return pd.DataFrame(rows).sort_values(["calendar_month", "calendar_day"])


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    settings: january.Settings,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    print("[2/6] 生成六月价格、成交和筛选矩阵...", flush=True)
    def matrix(column: str, dtype=np.float32) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=dtype
        )

    arrays = {
        "open": matrix("open"),
        "close": matrix("close"),
        "volume": matrix("volume"),
        "amount": matrix("amount"),
        "float_shares": matrix("float_shares"),
        "qfq_ratio": matrix("qfq_ratio"),
    }
    arrays["raw_close"] = arrays["close"] / arrays["qfq_ratio"]
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    def cached(column: str) -> np.ndarray:
        return matrix(column)

    print("[3/6] 计算六月10个可计算因子...", flush=True)
    factors: dict[str, np.ndarray] = {}
    factors["J06_D01_STRUCTURED_REVERSAL_21D_10M"] = _compact(
        cached("structure_reversal_21d_10m")
    )

    fuzzy = cached("fuzzy_amount_ratio_daily")
    factors["J06_D04_FUZZY_AMOUNT_RATIO_20D"] = _mean_std_composite(fuzzy, 20)
    del fuzzy

    daily_return = january.daily_returns(arrays["close"])
    factors["J06_D06_CSAD_TIME_SERIES_120D"] = _compute_csad_monthly(
        daily_return, membership, dates, settings.min_stocks
    )

    follow = cached("wait_rescue_follow_daily")
    factors["J06_D08_WAITING_RESCUE_20D"] = _mean_std_composite(follow, 20)
    del follow

    market_return = _read_qlib_close_return(
        settings.qlib_dir, "sh000985", dates, fallback_instrument="sh000906"
    )
    factors["J06_D10_VOLATILITY_PANIC_20D"] = _compute_volatility_panic(
        daily_return,
        market_return,
        cached("intraday_volatility_1m"),
        20,
    )

    factors["J06_D11_DOWNSIDE_REALIZED_VOL_5M"] = _compact(
        cached("downside_realized_vol_5m")
    )
    factors["J06_D13_PATV_5M"] = _compact(cached("patv_daily"))

    pv_slope = _rolling_slope(cached("pv_corr_daily"), 20)
    market_cap = arrays["raw_close"] * arrays["float_shares"]
    log_market_cap = np.full(market_cap.shape, np.nan, dtype=np.float64)
    valid_cap = np.isfinite(market_cap) & (market_cap > 0)
    log_market_cap[valid_cap] = np.log(market_cap[valid_cap])
    reversal_20d = _rolling_return(arrays["close"], 20)
    turnover = np.full(arrays["volume"].shape, np.nan, dtype=np.float64)
    np.divide(
        arrays["volume"],
        arrays["float_shares"],
        out=turnover,
        where=np.isfinite(arrays["float_shares"]) & (arrays["float_shares"] > 0),
    )
    turnover_20d = january.rolling_mean(turnover, 20)
    _, volatility_20d = january.rolling_mean_std(daily_return, 20)
    factors["J06_D17_PV_CORR_TREND_20D"] = _compact(
        feb_mar.cross_sectional_residual(
            pv_slope,
            [log_market_cap, reversal_20d, turnover_20d, volatility_20d],
            membership,
            settings.min_stocks,
        )
    )
    del (
        pv_slope,
        market_cap,
        log_market_cap,
        reversal_20d,
        turnover,
        turnover_20d,
        volatility_20d,
    )

    factors["J06_D19_INTRADAY_CVAR_5PCT"] = _compact(
        _month_to_date_mean(cached("intraday_cvar_5pct_daily"), dates)
    )
    factors["J06_D23_IDIOSYNCRATIC_OVERNIGHT_VOL_20D"] = (
        _compute_overnight_idiosyncratic_volatility(
            cached("overnight_return"), settings.min_stocks
        )
    )
    return factors, arrays


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = january.Settings(
        cache_dir=Path(args.base_cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
    )
    started = time.time()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    january.FACTOR_META = FACTOR_META

    print("[1/6] 读取基础每日文件、六月文件和历史指数成分...", flush=True)
    base_files = january.list_cache_files(Path(args.base_cache_dir))
    june_files = january.list_cache_files(Path(args.june_cache_dir))
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    june_panel = feb_mar.load_panel(
        june_files, settings.start, settings.end, JUNE_COLUMNS
    )
    print("  在合并前计算PATV并释放48个ATV字段...", flush=True)
    june_panel = _collapse_june_panel(june_panel)
    gc.collect()
    base_panel = feb_mar.load_panel(
        base_files, settings.start, settings.end, BASE_COLUMNS
    )
    panel = base_panel.merge(
        june_panel,
        on=["date", "code"],
        how="inner",
        validate="one_to_one",
    )
    del base_panel, june_panel
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    stocks, date_map, stock_map = january.make_axes(panel, dates)
    membership_rows = january.read_membership_rows(
        settings.qlib_dir, settings.start, settings.end
    )
    membership, missing_members = january.build_membership(
        dates, stocks, membership_rows
    )
    january.atomic_write_csv(
        missing_members, settings.output_dir / "qlib_members_without_cache.csv"
    )
    print(
        f"  {len(dates)}个交易日，{len(stocks)}只缓存股票；每日成分中位数"
        f"{int(np.median(membership.sum(axis=1)))}只",
        flush=True,
    )

    factors, arrays = build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )
    original_signals = january.build_original_signals(
        factors, dates, settings.backtest_start
    )
    del panel
    gc.collect()

    january.atomic_write_csv(
        build_status_frame(factors), settings.output_dir / "factor_status.csv"
    )
    methodology = {
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        },
        "factor_meta": FACTOR_META,
        "backtest_conventions": {
            "training_rule": "未来收益退出日不晚于2020-12-31",
            "later_period_rule": "入场日晚于2020-12-31",
            "daily_trade_frequency": "日频因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "amount_quality": "异常成交额日不进入流动性筛选",
            "structured_reversal": "10分钟、21日、低量10%；最终Ret_mom按前文改为Rev_mom",
            "fuzzy_and_waiting_rescue": "20日均值和标准差各乘0.5后相加",
            "volatility_panic": "市场日收益优先使用SH000985在当日和前日收盘都有效时的收益，否则使用SH000906当日收益；只合并收益，不直接拼接两个指数的点位",
            "patv": "采用前1个交易日和5分钟；每段在全部有数据股票中排名，采用总体Pearson峰度",
            "intraday_cvar": "原图两式不能直接衔接；近似为每日取最低5%分钟收益并按成交量加权，月末取当月日值均值",
            "overnight_pca": "全部具备完整20日样本的股票参与PCA，去均值但不做方差标准化",
        },
        "loaded_base_columns": BASE_COLUMNS,
        "loaded_june_columns": JUNE_COLUMNS,
        "created_at": pd.Timestamp.now().isoformat(),
    }
    january.atomic_write_json(methodology, settings.output_dir / "methodology.json")

    if args.save_factor_values:
        january.save_factor_values(factors, dates, stocks, settings.output_dir)
        january.save_factor_values(
            original_signals,
            dates,
            stocks,
            settings.output_dir,
            directory_name="original_signals",
        )
    if args.skip_backtest:
        print(f"仅生成因子，耗时 {time.time() - started:.1f} 秒", flush=True)
        return 0

    print("[4/6] 计算原频率IC和统一五日评价...", flush=True)
    eligible = january.build_eligible(
        arrays, membership, settings.min_price, settings.min_avg_amount
    )
    future_return = january.future_open_returns(
        arrays["open"], settings.hold_days, arrays["open_exit_mark"]
    )
    daily_frames = []
    original_frequency_frames = []
    rebalance_frames = []
    original_rebalance_frames = []
    for position, (factor_name, factor) in enumerate(factors.items(), start=1):
        factor_started = time.time()
        daily = january.calculate_daily_ic(
            factor_name, factor, future_return, eligible, dates, settings
        )
        original_frequency = january.calculate_original_frequency_ic(
            factor_name,
            original_signals[factor_name],
            future_return,
            arrays,
            eligible,
            dates,
            settings,
        )
        daily_frames.append(daily)
        original_frequency_frames.append(original_frequency)
        rebalance_frames.append(
            january.run_five_day_backtest(
                factor_name, factor, future_return, eligible, arrays, dates, settings
            )
        )
        original_rebalance_frames.append(
            january.run_original_frequency_backtest(
                factor_name,
                original_signals[factor_name],
                future_return,
                eligible,
                arrays,
                dates,
                settings,
            )
        )
        mean_ic = daily["ic"].mean() if not daily.empty else np.nan
        print(
            f"  {position}/{len(factors)} {factor_name}: 统一5日IC="
            f"{mean_ic:+.4f}，耗时{time.time() - factor_started:.1f}秒",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True)
    original_frequency_ic = pd.concat(
        original_frequency_frames, ignore_index=True
    )
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = pd.concat(rebalance_frames, ignore_index=True)
    original_rebalance = pd.concat(original_rebalance_frames, ignore_index=True)
    daily_summary = january.summarize_ic(
        daily_ic, "date", "unified_5d", settings.split_date
    )
    original_frequency_summary = january.summarize_original_frequency_ic(
        original_frequency_ic, settings.split_date
    )
    backtest_summary, yearly_backtest = january.summarize_backtest(
        rebalance, daily_summary, settings.split_date, settings.hold_days
    )
    original_backtest_summary, original_yearly_backtest = (
        january.summarize_original_frequency_backtest(
            original_rebalance, original_frequency_summary, settings.split_date
        )
    )

    print("[5/6] 保存结果...", flush=True)
    outputs = {
        "daily_ic.csv": daily_ic,
        "monthly_ic.csv": monthly_ic,
        "original_frequency_ic.csv": original_frequency_ic,
        "original_frequency_ic_summary.csv": original_frequency_summary,
        "ic_summary.csv": pd.concat(
            [daily_summary, original_frequency_summary], ignore_index=True
        ),
        "rebalance_returns.csv": rebalance,
        "backtest_summary.csv": backtest_summary,
        "yearly_backtest.csv": yearly_backtest,
        "original_frequency_rebalance_returns.csv": original_rebalance,
        "original_frequency_backtest_summary.csv": original_backtest_summary,
        "original_frequency_yearly_backtest.csv": original_yearly_backtest,
    }
    for name, frame in outputs.items():
        january.atomic_write_csv(frame, settings.output_dir / name)

    print("[6/6] 完成", flush=True)
    print(f"总耗时 {time.time() - started:.1f} 秒", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
