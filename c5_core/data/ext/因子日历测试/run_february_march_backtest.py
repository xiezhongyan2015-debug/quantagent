# -*- coding: utf-8 -*-
"""计算二月、三月量价因子，并沿用一月回测口径完成评价。"""

from __future__ import annotations

import argparse
import gc
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent


FACTOR_META = {
    "F02_D01_QUA": {
        "name": "单笔成交金额分位数",
        "calendar_month": 2,
        "calendar_day": 1,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_count",
        "note": "分钟库没有成交笔数，不能还原单笔成交金额。",
    },
    "F02_D03_SMALL_ORDER_LAG_RANKCORR": {
        "name": "小单净流入错位秩相关",
        "calendar_month": 2,
        "calendar_day": 3,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_direction",
        "note": "缺逐笔金额和主动买卖方向，原图正文还出现两套不同公式。",
    },
    "F02_D04_SELL_ORDER_ILLIQUIDITY": {
        "name": "卖单非流动性",
        "calendar_month": 2,
        "calendar_day": 4,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_direction",
        "note": "缺主动买入额和主动卖出额。",
    },
    "F02_D06_JUMP_ASYMMETRY_5M": {
        "name": "上下行跳跃波动的不对称性",
        "calendar_month": 2,
        "calendar_day": 6,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "formula_typo_corrected",
        "note": "按SRVJ=RVJP-RVJN计算；原图第一式的RVLP按正文改为RVJP。",
    },
    "F02_D07_VOLUME_BIN_ENTROPY_STD20": {
        "name": "成交量分桶熵",
        "calendar_month": 2,
        "calendar_day": 7,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_parameter",
        "note": "原图没有给成交量等宽分桶数maxbin。",
    },
    "F02_D08_INSTITUTIONAL_TRADING_HEAT": {
        "name": "机构交易热度",
        "calendar_month": 2,
        "calendar_day": 8,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_tick_data",
        "note": "缺逐笔成交金额和主动买卖方向。",
    },
    "F02_D09_NET_BID_CHANGE_RATE": {
        "name": "净委买变化率",
        "calendar_month": 2,
        "calendar_day": 9,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_order_book",
        "note": "缺盘口逐次变化数据。",
    },
    "F02_D10_GOLDEN_SPLIT_REVERSAL_20D": {
        "name": "日内黄金分割反转",
        "calendar_month": 2,
        "calendar_day": 10,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "过去20日逐日累加ln(收盘价/10:00分钟收盘价)。",
    },
    "F02_D11_TCC_20D": {
        "name": "时间网络相对中心度",
        "calendar_month": 2,
        "calendar_day": 11,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "每日收益按当日股票范围标准化，TCC为过去20日标准化收益平方均值的倒数。",
    },
    "F02_D12_LONE_GOOSE": {
        "name": "孤雁出群",
        "calendar_month": 2,
        "calendar_day": 12,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "separate_cross_sectional_minute_calculation",
        "note": "数据可计算，但需按日期组织全市场分钟成交额并计算大量两两相关，本次每股轻量文件不适用。",
    },
    "F02_D13_ASK_DEPTH": {
        "name": "卖盘深度",
        "calendar_month": 2,
        "calendar_day": 13,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_order_book",
        "note": "缺五档盘口。",
    },
    "F02_D14_CSSD_HERDING": {
        "name": "CSSD羊群效应",
        "calendar_month": 2,
        "calendar_day": 14,
        "source_frequency": "日频，市场状态",
        "signal_frequency": "unavailable",
        "expected_direction": "not_applicable",
        "strict_status": "market_level_and_missing_parameters",
        "note": "这是全市场单值，不适合个股横截面IC；原图还缺极端阈值和回归窗口。",
    },
    "F02_D15_CORRECTED_AMPLITUDE_20D": {
        "name": "修正的振幅",
        "calendar_month": 2,
        "calendar_day": 15,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "使用全天所有分钟的日内跳跃度；低于当日横截面均值时把日振幅取反，月末取20日均值。",
    },
    "F02_D16_REALIZED_KURTOSIS_1M_20D": {
        "name": "高频已实现峰度（1分钟）",
        "calendar_month": 2,
        "calendar_day": 16,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_example_frequency",
        "note": "采用原图允许的1分钟方案，月末取过去20日均值。",
    },
    "F02_D18_INFORMED_BUY_SHARE": {
        "name": "知情主买占比",
        "calendar_month": 2,
        "calendar_day": 18,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_direction",
        "note": "缺主动买卖方向，且原图的主买、主卖表述互相冲突。",
    },
    "F02_D19_VOLUME_SURGE_VOLATILITY": {
        "name": "量涌波动率",
        "calendar_month": 2,
        "calendar_day": 19,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_stop_rule",
        "note": "递归切分区间的停止条件未给出，无法严格复原。",
    },
    "F02_D20_CHIP_CONCENTRATION": {
        "name": "筹码集中度",
        "calendar_month": 2,
        "calendar_day": 20,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_chip_distribution",
        "note": "没有逐价位筹码分布及其生成方法。",
    },
    "F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D": {
        "name": "谷岭加权价格比",
        "calendar_month": 2,
        "calendar_day": 21,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "text_interpreted",
        "note": "每日量谷成交量加权价除以量岭成交量加权价，再取20日均值；分母严格使用量岭。",
    },
    "F02_D22_TOXIC_LIQUIDITY": {
        "name": "有毒流动性",
        "calendar_month": 2,
        "calendar_day": 22,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_cancel_events",
        "note": "缺撤单事件和秒级时间。",
    },
    "F02_D23_SIMILAR_LOW_VOL": {
        "name": "相似低波",
        "calendar_month": 2,
        "calendar_day": 23,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_parameters",
        "note": "缺超额收益基准、持有期、半衰期和最终合成方法。",
    },
    "F02_D24_LARGE_ORDER_PUSH_UP_RETURN": {
        "name": "大单推动上涨收益",
        "calendar_month": 2,
        "calendar_day": 24,
        "source_frequency": "周频或月频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_trade_count",
        "note": "缺分钟成交笔数，不能计算分钟平均单笔金额。",
    },
    "F02_D25_DOWN_TOP10_SELL_AVG_TRADE": {
        "name": "下跌前10%分钟主卖笔均金额",
        "calendar_month": 2,
        "calendar_day": 25,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_tick_data",
        "note": "缺主动卖出方向和成交笔数。",
    },
    "F02_D26_IMPROVED_LARGE_ORDER_SHARE": {
        "name": "改进大单交易占比",
        "calendar_month": 2,
        "calendar_day": 26,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_order_ids",
        "note": "缺委托编号和买卖方向。",
    },
    "F02_D27_ENHANCED_TGD_20D": {
        "name": "时间重心偏离（增强版）",
        "calendar_month": 2,
        "calendar_day": 27,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "text_interpreted",
        "note": "按原图的横截面回归文字采用线性最小二乘；依次剔除四个干扰变量，再回归跌幅残差与涨幅残差。",
    },
    "F02_D28_PEAK_RIDGE_AMOUNT_RATIO_20D": {
        "name": "峰岭成交比",
        "calendar_month": 2,
        "calendar_day": 28,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "runnable",
        "note": "过去20日量峰成交额合计除以量岭成交额合计。",
    },
    "M03_AMOUNT_SHARE_ENTROPY_5M": {
        "name": "成交额占比熵",
        "calendar_month": 3,
        "calendar_day": 3,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "runnable",
        "note": "把全天分成48个5分钟段，计算成交额占比的香农熵。",
    },
    "M06_UPSIDE_VOL_SHARE_5M_20D": {
        "name": "高频上行波动占比（5分钟）",
        "calendar_month": 3,
        "calendar_day": 6,
        "source_frequency": "月频，5分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable_primary_frequency",
        "note": "原图允许1、5、10分钟，本次主版采用5分钟并在月末取20日均值。",
    },
    "M08_CSAD_HERDING": {
        "name": "CSAD羊群效应",
        "calendar_month": 3,
        "calendar_day": 8,
        "source_frequency": "日频，市场状态",
        "signal_frequency": "unavailable",
        "expected_direction": "not_applicable",
        "strict_status": "market_level_and_missing_window",
        "note": "这是全市场单值，不适合个股横截面IC，原图也没有给回归窗口。",
    },
    "M10_OVERNIGHT_GAP_20D": {
        "name": "隔夜跳空",
        "calendar_month": 3,
        "calendar_day": 10,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "过去20日累加|ln(当日开盘价/前收盘价)|。",
    },
    "M11_NETWORK_CENTER_COMPOSITE_20D": {
        "name": "股票网络中心度",
        "calendar_month": 3,
        "calendar_day": 11,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "按原图直接计算0.5×SCC+0.5×TCC。",
    },
    "M12_WEIGHTED_SKEW_5M": {
        "name": "加权偏度",
        "calendar_month": 3,
        "calendar_day": 12,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "采用48个5分钟收盘价和对应成交量权重。",
    },
    "M13_MORNING_FOG_20D": {
        "name": "朝没晨雾",
        "calendar_month": 3,
        "calendar_day": 13,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "按原图使用1分钟成交量；日内回归取五个滞后项t值的总体标准差，月末取20日均值。",
    },
    "M15_VSA_LOW_CLOSE_GAP": {
        "name": "VSA低收盘价差",
        "calendar_month": 3,
        "calendar_day": 15,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_formula_direction",
        "note": "原图没有写差值方向、是否除以收盘价以及扩展和并列规则。",
    },
    "M16_LARGE_UP_JUMP_VOL_5M": {
        "name": "大的上行跳跃波动率",
        "calendar_month": 3,
        "calendar_day": 16,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "按原图α=4计算阈值，取RVJP与大上行收益平方和的较小值。",
    },
    "M17_LOSS_SELLING_PROPENSITY": {
        "name": "亏损卖出倾向",
        "calendar_month": 3,
        "calendar_day": 17,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_parameters",
        "note": "N、T未给，原图两个求和范围也不一致。",
    },
    "M21_CORRECTED_INTRADAY_REVERSAL_20D": {
        "name": "修正的日内反转",
        "calendar_month": 3,
        "calendar_day": 21,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "月末计算20日日内收益均值和标准差；标准差低于当日横截面均值时把均值取反。",
    },
    "M23_ACTIVE_CHIP_SHARE": {
        "name": "活跃筹码占比",
        "calendar_month": 3,
        "calendar_day": 23,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_chip_distribution",
        "note": "没有逐价位筹码分布及其生成方法。",
    },
    "M25_HIGH_VOLUME_REALIZED_SKEW": {
        "name": "高成交量已实现偏度",
        "calendar_month": 3,
        "calendar_day": 25,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_return_definition",
        "note": "原图没有说明分钟收益使用简单收益还是对数收益，本次不擅自选择。",
    },
    "M27_INTRADAY_JUMPINESS_20D": {
        "name": "日内跳跃度",
        "calendar_month": 3,
        "calendar_day": 27,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "严格剔除首尾分钟；月末把过去20日均值和总体标准差各乘0.5后相加。",
    },
    "M31_VOLUME_BURST_FOLLOW_20D": {
        "name": "喷发成交额跟随比例",
        "calendar_month": 3,
        "calendar_day": 31,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "过去20日喷发点下一交易分钟成交额合计除以喷发点成交额合计；15:00不跨日。",
    },
}


BASE_COLUMNS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "amount",
    "qfq_ratio",
    "prev_close",
    "vwap_outside_range_1pct",
]


FACTOR_COLUMNS = [
    "rvjn_5m",
    "rvjp_5m",
    "golden_split_return_daily",
    "gu_1m",
    "gd_1m",
    "intraday_jumpiness_all_daily",
    "realized_kurtosis_1m",
    "valley_ridge_vwap_ratio_daily",
    "peak_amount_daily",
    "ridge_amount_daily",
    "rbar_up17",
    "rbar_down17",
    "r_0931_1000",
    "r_1001_1030",
    "overnight_return",
    "amount_share_entropy_5m",
    "upside_vol_share_5m",
    "weighted_skew_5m",
    "morning_fog_daily",
    "large_up_jump_5m",
    "intraday_jumpiness_ex_open_close_daily",
    "burst_follow_amount_daily",
    "burst_amount_daily",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default=str(BASE_DIR / "february_march_results" / "cache" / "daily_by_stock"),
    )
    parser.add_argument(
        "--output-dir", default=str(BASE_DIR / "february_march_results")
    )
    parser.add_argument("--qlib-dir", default=r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def load_panel(
    files: list[Path], start: str, end: str, columns: list[str]
) -> pd.DataFrame:
    """只读取本轮计算需要的列，并把数值列压成float32。"""
    if not files:
        raise FileNotFoundError("没有找到每日轻量文件")
    requested = list(dict.fromkeys(columns))
    available = set(pq.ParquetFile(files[0]).schema_arrow.names)
    missing = [column for column in requested if column not in available]
    if missing:
        raise ValueError(
            "每日轻量文件缺少本轮所需列："
            + ", ".join(missing)
            + "。请使用带二三月字段的新目录重新生成缓存。"
        )

    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end)
    frames: list[pd.DataFrame] = []
    for index, path in enumerate(files, start=1):
        try:
            frame = pd.read_parquet(path, columns=requested)
        except Exception as exc:
            raise ValueError(f"读取{path}所需列失败：{exc}") from exc
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame[(frame["date"] >= lower) & (frame["date"] <= upper)].copy()
        frame["code"] = frame["code"].astype("string").str.lower()
        for column in requested:
            if column not in {"date", "code"} and not pd.api.types.is_bool_dtype(
                frame[column].dtype
            ):
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(
                    np.float32
                )
        frames.append(frame)
        if index % 500 == 0:
            print(f"  已读取 {index}/{len(files)} 只股票", flush=True)
    panel = pd.concat(frames, ignore_index=True)
    panel = panel.sort_values(["date", "code"]).drop_duplicates(
        ["date", "code"], keep="last"
    )
    return panel


def rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    sums, counts = january.rolling_sum_count(values, window)
    result = sums
    result[counts < window] = np.nan
    return result


def rolling_ratio(
    numerator: np.ndarray, denominator: np.ndarray, window: int
) -> np.ndarray:
    numerator_sum, numerator_count = january.rolling_sum_count(numerator, window)
    denominator_sum, denominator_count = january.rolling_sum_count(denominator, window)
    result = np.full(numerator_sum.shape, np.nan, dtype=np.float64)
    valid = (
        (numerator_count >= window)
        & (denominator_count >= window)
        & np.isfinite(denominator_sum)
        & (denominator_sum > 0)
    )
    np.divide(numerator_sum, denominator_sum, out=result, where=valid)
    return result


def compute_tcc(
    close: np.ndarray, membership: np.ndarray, window: int, min_stocks: int
) -> np.ndarray:
    returns = january.daily_returns(close)
    standardized = np.full(returns.shape, np.nan, dtype=np.float32)
    for index in range(len(returns)):
        valid = membership[index] & np.isfinite(returns[index])
        if valid.sum() < min_stocks:
            continue
        values = returns[index, valid].astype(np.float64)
        standard = values.std(ddof=0)
        if standard <= 1e-15:
            continue
        standardized[index, np.flatnonzero(valid)] = (
            (values - values.mean()) / standard
        ).astype(np.float32)
    mean_square = january.rolling_mean(standardized * standardized, window)
    result = np.full(mean_square.shape, np.nan, dtype=np.float64)
    np.divide(1.0, mean_square, out=result, where=mean_square > 1e-15)
    return result


def cross_sectional_residual(
    dependent: np.ndarray,
    explanatory: list[np.ndarray],
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    """逐日做带截距的横截面线性回归并返回残差。"""
    result = np.full(dependent.shape, np.nan, dtype=np.float64)
    for index in range(len(dependent)):
        valid = membership[index] & np.isfinite(dependent[index])
        for values in explanatory:
            valid &= np.isfinite(values[index])
        if valid.sum() < max(min_stocks, len(explanatory) + 2):
            continue
        indices = np.flatnonzero(valid)
        design = np.column_stack(
            [np.ones(len(indices), dtype=np.float64)]
            + [values[index, indices] for values in explanatory]
        )
        target = dependent[index, indices]
        coefficients, _, _, _ = np.linalg.lstsq(design, target, rcond=None)
        result[index, indices] = target - design @ coefficients
    return result


def correct_below_cross_section_mean(
    values: np.ndarray,
    classifier: np.ndarray,
    membership: np.ndarray,
    min_stocks: int,
) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=np.float64)
    for index in range(len(values)):
        valid = (
            membership[index]
            & np.isfinite(values[index])
            & np.isfinite(classifier[index])
        )
        if valid.sum() < min_stocks:
            continue
        indices = np.flatnonzero(valid)
        current = values[index, indices].astype(np.float64, copy=True)
        flip = classifier[index, indices] < classifier[index, indices].mean()
        current[flip] *= -1.0
        result[index, indices] = current
    return result


def compact(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    min_stocks: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    print("[2/6] 生成价格、成交和筛选矩阵...", flush=True)

    def matrix(column: str, dtype=np.float32) -> np.ndarray:
        return january.panel_to_matrix(
            panel, column, dates, stocks, date_map, stock_map, dtype=dtype
        )

    arrays = {
        "open": matrix("open"),
        "high": matrix("high"),
        "low": matrix("low"),
        "close": matrix("close"),
        "amount": matrix("amount"),
        "qfq_ratio": matrix("qfq_ratio"),
        "prev_close": matrix("prev_close"),
    }
    arrays["raw_close"] = arrays["close"] / arrays["qfq_ratio"]
    arrays["price_amount_bad"] = january.build_price_amount_bad_matrix(
        panel, dates, stocks, date_map, stock_map
    )
    arrays["open_exit_mark"], arrays["close_exit_mark"] = (
        january.build_exit_price_matrices(arrays["open"], arrays["close"])
    )

    def cache(column: str, amount_related: bool = False) -> np.ndarray:
        values = matrix(column)
        if amount_related:
            values = january.mask_bad_days(values, arrays["price_amount_bad"])
        return values

    print("[3/6] 计算二月8个、三月10个因子...", flush=True)
    factors: dict[str, np.ndarray] = {}

    rvjp = cache("rvjp_5m")
    rvjn = cache("rvjn_5m")
    factors["F02_D06_JUMP_ASYMMETRY_5M"] = compact(rvjp - rvjn)
    del rvjp, rvjn

    factors["F02_D10_GOLDEN_SPLIT_REVERSAL_20D"] = compact(
        rolling_sum(cache("golden_split_return_daily"), 20)
    )

    tcc = compute_tcc(arrays["close"], membership, 20, min_stocks)
    factors["F02_D11_TCC_20D"] = compact(tcc)

    amplitude = np.full(arrays["close"].shape, np.nan, dtype=np.float64)
    valid_amplitude = np.isfinite(arrays["prev_close"]) & (arrays["prev_close"] > 0)
    np.divide(
        arrays["high"] - arrays["low"],
        arrays["prev_close"],
        out=amplitude,
        where=valid_amplitude,
    )
    corrected_amplitude = correct_below_cross_section_mean(
        amplitude,
        cache("intraday_jumpiness_all_daily"),
        membership,
        min_stocks,
    )
    factors["F02_D15_CORRECTED_AMPLITUDE_20D"] = compact(
        january.rolling_mean(corrected_amplitude, 20)
    )
    del amplitude, corrected_amplitude

    factors["F02_D16_REALIZED_KURTOSIS_1M_20D"] = compact(
        january.rolling_mean(cache("realized_kurtosis_1m"), 20)
    )
    factors["F02_D21_VALLEY_RIDGE_VWAP_RATIO_20D"] = compact(
        january.rolling_mean(
            cache("valley_ridge_vwap_ratio_daily", amount_related=True), 20
        )
    )

    gu = cache("gu_1m")
    gd = cache("gd_1m")
    r1 = cache("r_0931_1000")
    r2 = cache("r_1001_1030")
    overnight = cache("overnight_return")
    epsilon_up = cross_sectional_residual(
        gu,
        [cache("rbar_up17"), r1, r2, overnight],
        membership,
        min_stocks,
    )
    epsilon_down = cross_sectional_residual(
        gd,
        [cache("rbar_down17"), r1, r2, overnight],
        membership,
        min_stocks,
    )
    tgd_daily = cross_sectional_residual(
        epsilon_down, [epsilon_up], membership, min_stocks
    )
    factors["F02_D27_ENHANCED_TGD_20D"] = compact(
        january.rolling_mean(tgd_daily, 20)
    )
    del gu, gd, r1, r2, epsilon_up, epsilon_down, tgd_daily

    factors["F02_D28_PEAK_RIDGE_AMOUNT_RATIO_20D"] = compact(
        rolling_ratio(
            cache("peak_amount_daily", amount_related=True),
            cache("ridge_amount_daily", amount_related=True),
            20,
        )
    )

    factors["M03_AMOUNT_SHARE_ENTROPY_5M"] = compact(
        cache("amount_share_entropy_5m", amount_related=True)
    )
    factors["M06_UPSIDE_VOL_SHARE_5M_20D"] = compact(
        january.rolling_mean(cache("upside_vol_share_5m"), 20)
    )

    overnight_log = np.full(overnight.shape, np.nan, dtype=np.float64)
    valid_overnight = np.isfinite(overnight) & (overnight > -1.0)
    overnight_log[valid_overnight] = np.abs(np.log1p(overnight[valid_overnight]))
    factors["M10_OVERNIGHT_GAP_20D"] = compact(rolling_sum(overnight_log, 20))
    del overnight, overnight_log

    scc = january.compute_scc(arrays["close"], membership, 20, min_stocks)
    factors["M11_NETWORK_CENTER_COMPOSITE_20D"] = compact(0.5 * scc + 0.5 * tcc)
    del scc, tcc

    factors["M12_WEIGHTED_SKEW_5M"] = compact(cache("weighted_skew_5m"))
    factors["M13_MORNING_FOG_20D"] = compact(
        january.rolling_mean(cache("morning_fog_daily"), 20)
    )
    factors["M16_LARGE_UP_JUMP_VOL_5M"] = compact(cache("large_up_jump_5m"))

    intraday_return = np.full(arrays["close"].shape, np.nan, dtype=np.float64)
    valid_intraday = np.isfinite(arrays["open"]) & (arrays["open"] > 0)
    np.divide(
        arrays["close"], arrays["open"], out=intraday_return, where=valid_intraday
    )
    intraday_return -= 1.0
    intraday_mean, intraday_std = january.rolling_mean_std(intraday_return, 20)
    factors["M21_CORRECTED_INTRADAY_REVERSAL_20D"] = compact(
        correct_below_cross_section_mean(
            intraday_mean, intraday_std, membership, min_stocks
        )
    )
    del intraday_return, intraday_mean, intraday_std

    jumpiness = cache("intraday_jumpiness_ex_open_close_daily")
    jumpiness_mean, jumpiness_std = january.rolling_mean_std(jumpiness, 20)
    factors["M27_INTRADAY_JUMPINESS_20D"] = compact(
        0.5 * jumpiness_mean + 0.5 * jumpiness_std
    )
    del jumpiness, jumpiness_mean, jumpiness_std

    factors["M31_VOLUME_BURST_FOLLOW_20D"] = compact(
        rolling_ratio(
            cache("burst_follow_amount_daily", amount_related=True),
            cache("burst_amount_daily", amount_related=True),
            20,
        )
    )
    return factors, arrays


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        row = {"factor": code, **meta, "calculated": code in factors}
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["calendar_month", "calendar_day"])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = january.Settings(
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
    )
    started = time.time()
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    # 一月公共函数有四处从模块变量读取因子频率；这里只替换内存中的表，不改一月文件。
    january.FACTOR_META = FACTOR_META

    print("[1/6] 读取每日轻量文件和历史指数成分...", flush=True)
    files = january.list_cache_files(settings.cache_dir)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    panel = load_panel(files, settings.start, settings.end, BASE_COLUMNS + FACTOR_COLUMNS)
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
        settings.min_stocks,
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
            "daily_icir_annualizer": "按实际有效信号数/年再除以5后开平方，因为未来5日收益相互重叠",
            "daily_trade_frequency": "日频因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，下月首日开盘买入并在当月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "amount_quality": "分钟成交额推算均价异常日不进入成交额相关因子窗口和成交额筛选窗口",
            "february_jumpiness": "二月15日使用全天所有分钟intraday_jumpiness_all_daily",
            "march_jumpiness": "三月27日使用剔除首尾分钟intraday_jumpiness_ex_open_close_daily",
            "march_burst_follow": "先分别累计20日跟随成交额和喷发成交额再相除；午休后的下一交易分钟为13:01，15:00不跨日",
            "enhanced_tgd": "原图f_u和f_d未给函数形式，本次采用带截距的横截面线性最小二乘",
        },
        "loaded_cache_columns": BASE_COLUMNS + FACTOR_COLUMNS,
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
            f"  {position}/{len(factors)} {factor_name}: 统一5日IC={mean_ic:+.4f}，"
            f"耗时{time.time() - factor_started:.1f}秒",
            flush=True,
        )

    daily_ic = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    original_frequency_ic = (
        pd.concat(original_frequency_frames, ignore_index=True)
        if original_frequency_frames
        else pd.DataFrame()
    )
    monthly_ic = original_frequency_ic.loc[
        original_frequency_ic["signal_frequency"] == "monthly"
    ].copy()
    rebalance = (
        pd.concat(rebalance_frames, ignore_index=True)
        if rebalance_frames
        else pd.DataFrame()
    )
    original_rebalance = (
        pd.concat(original_rebalance_frames, ignore_index=True)
        if original_rebalance_frames
        else pd.DataFrame()
    )
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
