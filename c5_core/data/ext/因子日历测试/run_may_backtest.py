# -*- coding: utf-8 -*-
"""计算五月量价因子，并沿用一月至三月的统一回测办法。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from market_index_returns import combine_close_returns, synthetic_close_from_returns
import run_february_march_backtest as previous
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_CACHE = BASE_DIR / "february_march_results" / "cache" / "daily_by_stock"
DEFAULT_MAY_CACHE = BASE_DIR / "may_factor_results" / "cache" / "may_daily_by_stock"
DEFAULT_OUTPUT_DIR = BASE_DIR / "may_factor_results" / "final"


FACTOR_META = {
    "MAY04_B_VOLUME_DISTRIBUTION": {
        "name": "b型成交量分布",
        "calendar_month": 5,
        "calendar_day": 4,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "从VSP起按价格距离由近到远扩展；同距离先取成交量更大的价位，再算VSA上沿减当日最低价。",
    },
    "MAY05_SALIENCE_RETURN": {
        "name": "凸显性收益",
        "calendar_month": 5,
        "calendar_day": 5,
        "source_frequency": "月频，当月日收益",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "θ=0.1、δ=0.7，市场收益使用当日股票范围收益中位数，月底计算STR。",
    },
    "MAY07_LARGE_JUMP_ASYMMETRY_5M": {
        "name": "大的上下行跳跃波动不对称性",
        "calendar_month": 5,
        "calendar_day": 7,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "cross_card_definition",
        "note": "按1月6日、2月6日和3月16日卡片补全三幂变差、上下行半方差、α=4和Δ=1/48。",
    },
    "MAY09_PEER_CSAD": {
        "name": "CSAD模型（基于板块中地位）",
        "calendar_month": 5,
        "calendar_day": 9,
        "source_frequency": "月频，120日选股与20日汇总",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_standardization_window",
        "note": "卡片没有说明CSAD时序标准化的均值和标准差使用多长日期范围。",
    },
    "MAY10_SINGLE_AMOUNT_SHARE_ENTROPY_5M": {
        "name": "单一成交额占比熵",
        "calendar_month": 5,
        "calendar_day": 10,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "严格使用(分段成交量/全天成交量)×(分段收盘价/全天分段收盘价合计)，不再次归一化。",
    },
    "MAY13_INTRADAY_ABNORMAL_VOLUME": {
        "name": "日内异常交易量",
        "calendar_month": 5,
        "calendar_day": 13,
        "source_frequency": "分钟频率，可用5分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_daily_aggregation",
        "note": "卡片只定义每分钟ATV，没有说明如何合成一个日值或月值。",
    },
    "MAY14_PANIC_20D": {
        "name": "惊恐因子",
        "calendar_month": 5,
        "calendar_day": 14,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "市场日收益优先用中证全指SH000985；当日或前日收盘缺失时改用SH000906当日收益。收益累乘为连续指数后，计算过去20日惊恐调整收益均值和总体标准差的等权和。",
    },
    "MAY17_REALIZED_VOLATILITY_5M": {
        "name": "已实现波动率",
        "calendar_month": 5,
        "calendar_day": 17,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable_example_frequency",
        "note": "采用卡片示例的48个5分钟对数收益平方和。",
    },
    "MAY19_NIGHT_SLEEP_FROST_ROAD_20D": {
        "name": "夜眠霜路因子",
        "calendar_month": 5,
        "calendar_day": 19,
        "source_frequency": "月频，1分钟回归与20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "runnable",
        "note": "每日第6至240分钟回归收益与当期及5阶成交量增量；月底计算过去20日截距t值与当期股票范围内所有股票的平均绝对相关。",
    },
    "MAY21_REALIZED_PROFIT_RATIO": {
        "name": "已实现盈利比率",
        "calendar_month": 5,
        "calendar_day": 21,
        "source_frequency": "日频，历史递推",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_chip_initialization",
        "note": "首日筹码、价位成交量是否按日内占比处理，以及前复权价位划分办法均未写。",
    },
    "MAY22_MULTILAYER_INTRADAY_SNR": {
        "name": "多层加权日内信噪比",
        "calendar_month": 5,
        "calendar_day": 22,
        "source_frequency": "日频，日内",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "formula_conflict_and_missing_parameter",
        "note": "SNR第二层、第三层和δ未给，且权重公式与文字方向相反。",
    },
    "MAY23_SAME_TIME_PEAK_RIDGE_CORR_20D": {
        "name": "同时点峰岭数相关性",
        "calendar_month": 5,
        "calendar_day": 23,
        "source_frequency": "日频，1分钟，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "text_interpreted",
        "note": "在同一个最近20日窗口内按同一分钟均值加1倍总体标准差识别喷发，午休两边不视为相邻，再计算240个同时点峰数与岭数的相关系数。",
    },
    "MAY25_TOP_RETURN_PRICE_RESONANCE": {
        "name": "个股收益最高时的价变共振",
        "calendar_month": 5,
        "calendar_day": 25,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_return_and_rank_definition",
        "note": "卡片未说明分钟收益用简单收益还是对数收益，也未说明正序名次归一化的具体算法。",
    },
    "MAY27_IDIOSYNCRATIC_INTRADAY_RETURN_VOL_20D": {
        "name": "特质日内收益波动率",
        "calendar_month": 5,
        "calendar_day": 27,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "text_interpreted",
        "note": "PCA按每只股票20日日内收益去均值，不额外缩放；第一成分解释率低于20%时使用前两成分。",
    },
    "MAY28_CHIP_RETURN_ADJUSTMENT": {
        "name": "筹码收益调整",
        "calendar_month": 5,
        "calendar_day": 28,
        "source_frequency": "日频，250日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "formula_conflict",
        "note": "印刷公式算出的是历史成本价格，不是文字所称的相对收益，不能与-2%阈值直接比较。",
    },
    "MAY29_PRICE_VOLUME_CORR_COMPOSITE": {
        "name": "价量相关性综合因子",
        "calendar_month": 5,
        "calendar_day": 29,
        "source_frequency": "未说明",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_base_definition",
        "note": "PVcorravg、PVcorrstd的基础相关系数、分钟间隔、历史长度和频率均未写。",
    },
}


BASE_COLUMNS = [
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "qfq_ratio",
    "prev_close",
    "vwap_outside_range_1pct",
]

MAY_COLUMNS = [
    "b_volume_distribution_gap",
    "large_jump_asymmetry_5m",
    "single_amount_share_entropy_5m",
    "realized_volatility_5m",
    "night_sleep_t_intercept",
    "same_time_peak_ridge_corr_20d",
]

_MAY_CACHE_DIR = DEFAULT_MAY_CACHE
_QLIB_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin")
_BASE_LOADER = previous.load_panel


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_BASE_CACHE)
    parser.add_argument("--may-cache-dir", type=Path, default=DEFAULT_MAY_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--qlib-dir", type=Path, default=Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin"))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--backtest-start", default="2016-01-04")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--split-date", default="2020-12-31")
    parser.add_argument("--save-factor-values", action="store_true")
    parser.add_argument("--skip-backtest", action="store_true")
    return parser.parse_args(argv)


def load_combined_panel(
    files: list[Path], start: str, end: str, columns: list[str]
) -> pd.DataFrame:
    """分别读取原每日文件和五月新增文件，再按股票、日期合并。"""
    base = _BASE_LOADER(files, start, end, BASE_COLUMNS)
    may_files = january.list_cache_files(_MAY_CACHE_DIR)
    may = _BASE_LOADER(may_files, start, end, ["date", "code", *MAY_COLUMNS])
    combined = base.merge(
        may,
        on=["date", "code"],
        how="left",
        validate="one_to_one",
    )
    return combined.loc[:, list(dict.fromkeys(columns))]


def read_qlib_feature(
    qlib_dir: Path, instrument: str, field: str, dates: pd.DatetimeIndex
) -> np.ndarray:
    """读取一个Qlib日线字段并放到指定交易日。"""
    path = qlib_dir / "features" / instrument.lower() / f"{field}.day.bin"
    if not path.exists():
        raise FileNotFoundError(f"Qlib字段不存在：{path}")
    raw = np.fromfile(path, dtype="<f4")
    if len(raw) < 2 or not np.isfinite(raw[0]):
        raise ValueError(f"Qlib字段文件无效：{path}")
    start_index = int(raw[0])
    calendar = pd.to_datetime(
        pd.read_csv(qlib_dir / "calendars" / "day.txt", header=None)[0],
        errors="coerce",
    )
    values = raw[1:].astype(np.float64)
    right = start_index + len(values)
    if start_index < 0 or right > len(calendar):
        raise ValueError(f"Qlib字段日期范围无效：{path}")
    series = pd.Series(values, index=pd.DatetimeIndex(calendar.iloc[start_index:right]))
    return series.reindex(dates).to_numpy(dtype=np.float64)


def _month_end_indices(dates: pd.DatetimeIndex) -> np.ndarray:
    periods = dates.to_period("M")
    return np.flatnonzero(np.r_[periods[:-1] != periods[1:], True])


def compute_salience_return(
    close: np.ndarray,
    membership: np.ndarray,
    dates: pd.DatetimeIndex,
    theta: float = 0.1,
    delta: float = 0.7,
) -> np.ndarray:
    """按自然月计算凸显性收益，只在月末写入结果。"""
    returns = january.daily_returns(close)
    market_median = np.full(len(dates), np.nan, dtype=np.float64)
    for day in range(len(dates)):
        valid = membership[day] & np.isfinite(returns[day])
        if valid.any():
            market_median[day] = float(np.median(returns[day, valid]))
    result = np.full(returns.shape, np.nan, dtype=np.float64)
    periods = dates.to_period("M")
    for period in periods.unique():
        indices = np.flatnonzero(periods == period)
        values = returns[indices].astype(np.float64, copy=True)
        active = membership[indices]
        values[~active] = np.nan
        benchmark = market_median[indices, None]
        salience = np.abs(values - benchmark) / (
            np.abs(values) + np.abs(benchmark) + theta
        )
        ranks = pd.DataFrame(salience).rank(
            axis=0, method="average", ascending=False, na_option="keep"
        ).to_numpy(dtype=np.float64)
        powers = delta**ranks
        denominator = np.nansum(powers / len(indices), axis=0)
        weights = np.full(powers.shape, np.nan, dtype=np.float64)
        np.divide(
            powers,
            denominator[None, :],
            out=weights,
            where=np.isfinite(denominator[None, :]) & (denominator[None, :] > 0),
        )
        for stock in range(values.shape[1]):
            valid = np.isfinite(values[:, stock]) & np.isfinite(weights[:, stock])
            if valid.sum() < 2:
                continue
            stock_return = values[valid, stock]
            stock_weight = weights[valid, stock]
            result[indices[-1], stock] = np.cov(
                stock_weight, stock_return, ddof=1
            )[0, 1]
    return result


def compute_panic_factor(
    close: np.ndarray, market_close: np.ndarray, window: int = 20
) -> np.ndarray:
    """计算惊恐调整收益的20日均值与总体标准差等权和。"""
    stock_return = january.daily_returns(close)
    market_return = np.full(len(market_close), np.nan, dtype=np.float64)
    market_return[1:] = market_close[1:] / market_close[:-1] - 1.0
    degree = np.abs(stock_return - market_return[:, None]) / (
        np.abs(stock_return) + np.abs(market_return[:, None]) + 0.1
    )
    adjusted = degree * stock_return
    mean, standard = january.rolling_mean_std(adjusted, window)
    return 0.5 * mean + 0.5 * standard


def compute_night_sleep_factor(
    t_intercept: np.ndarray,
    membership: np.ndarray,
    dates: pd.DatetimeIndex,
    window: int = 20,
    block_size: int = 512,
) -> np.ndarray:
    """月底计算每只股票与当期股票范围内所有股票的平均绝对相关。"""
    result = np.full(t_intercept.shape, np.nan, dtype=np.float64)
    for day in _month_end_indices(dates):
        if day + 1 < window:
            continue
        history = t_intercept[day + 1 - window : day + 1].astype(
            np.float64, copy=False
        )
        valid = membership[day] & np.isfinite(history).all(axis=0)
        indices = np.flatnonzero(valid)
        if len(indices) < 2:
            continue
        values = history[:, indices]
        centered = values - values.mean(axis=0, keepdims=True)
        norm = np.sqrt(np.sum(centered * centered, axis=0))
        usable = np.isfinite(norm) & (norm > 0)
        indices = indices[usable]
        if len(indices) < 2:
            continue
        standardized = centered[:, usable] / norm[usable]
        means = np.empty(len(indices), dtype=np.float64)
        for left in range(0, len(indices), block_size):
            right = min(left + block_size, len(indices))
            correlations = standardized[:, left:right].T @ standardized
            means[left:right] = np.mean(np.abs(correlations), axis=1)
        result[day, indices] = means
    return result


def compute_idiosyncratic_intraday_volatility(
    open_price: np.ndarray,
    close: np.ndarray,
    membership: np.ndarray,
    window: int = 20,
    min_stocks: int = 50,
) -> np.ndarray:
    """用过去20日日内收益的主成分解释共同变化，再取残差总体标准差。"""
    intraday = np.full(close.shape, np.nan, dtype=np.float64)
    valid_price = np.isfinite(open_price) & np.isfinite(close) & (open_price > 0)
    np.divide(close, open_price, out=intraday, where=valid_price)
    intraday -= 1.0
    result = np.full(intraday.shape, np.nan, dtype=np.float64)
    for day in range(window - 1, len(intraday)):
        history = intraday[day + 1 - window : day + 1]
        valid = membership[day] & np.isfinite(history).all(axis=0)
        indices = np.flatnonzero(valid)
        if len(indices) < min_stocks:
            continue
        values = history[:, indices].astype(np.float64, copy=False)
        centered = values - values.mean(axis=0, keepdims=True)
        variation = np.sum(centered * centered, axis=0)
        usable = np.isfinite(variation) & (variation > 1e-20)
        indices = indices[usable]
        values = values[:, usable]
        centered = centered[:, usable]
        if len(indices) < min_stocks:
            continue
        gram = centered @ centered.T
        eigenvalues, eigenvectors = np.linalg.eigh(gram)
        order = np.argsort(eigenvalues)[::-1]
        eigenvalues = np.maximum(eigenvalues[order], 0.0)
        eigenvectors = eigenvectors[:, order]
        total = float(eigenvalues.sum())
        if total <= 0:
            continue
        component_count = 1 if eigenvalues[0] / total >= 0.2 else 2
        component_count = min(component_count, int((eigenvalues > 1e-20).sum()))
        if component_count < 1:
            continue
        scores = eigenvectors[:, :component_count] * np.sqrt(
            eigenvalues[:component_count]
        )
        design = np.column_stack([np.ones(window), scores])
        coefficients, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
        residual = values - design @ coefficients
        result[day, indices] = np.std(residual, axis=0, ddof=0)
    return result


def require_complete_market_window(
    values: np.ndarray, availability: np.ndarray, window: int
) -> np.ndarray:
    """只保留最近若干个市场交易日都具备分钟数据的结果。"""
    if values.shape != availability.shape or values.ndim != 2:
        raise ValueError("结果和可用标记必须是形状相同的二维数据")
    if window < 1:
        raise ValueError("window 必须至少为1")
    valid = np.isfinite(availability)
    cumulative = np.cumsum(valid, axis=0, dtype=np.int16)
    rolling_count = cumulative.copy()
    if window < len(rolling_count):
        rolling_count[window:] = cumulative[window:] - cumulative[:-window]
    complete = rolling_count == window
    complete[: window - 1] = False
    result = np.asarray(values, dtype=np.float64).copy()
    result[~complete] = np.nan
    return result


def build_factor_matrices(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    stocks: list[str],
    date_map: dict,
    stock_map: dict,
    membership: np.ndarray,
    min_stocks: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """生成价格、筛选数据和9个可计算的五月因子。"""

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

    def compact(values: np.ndarray) -> np.ndarray:
        return np.asarray(values, dtype=np.float32)

    factors: dict[str, np.ndarray] = {}
    factors["MAY04_B_VOLUME_DISTRIBUTION"] = compact(
        matrix("b_volume_distribution_gap")
    )
    factors["MAY05_SALIENCE_RETURN"] = compact(
        compute_salience_return(arrays["close"], membership, dates)
    )
    factors["MAY07_LARGE_JUMP_ASYMMETRY_5M"] = compact(
        matrix("large_jump_asymmetry_5m")
    )
    factors["MAY10_SINGLE_AMOUNT_SHARE_ENTROPY_5M"] = compact(
        matrix("single_amount_share_entropy_5m")
    )
    primary_market_close = read_qlib_feature(
        _QLIB_DIR, "sh000985", "close", dates
    )
    fallback_market_close = read_qlib_feature(
        _QLIB_DIR, "sh000906", "close", dates
    )
    market_return = combine_close_returns(
        primary_market_close, fallback_market_close
    )
    market_close = synthetic_close_from_returns(market_return)
    factors["MAY14_PANIC_20D"] = compact(
        compute_panic_factor(arrays["close"], market_close, 20)
    )
    factors["MAY17_REALIZED_VOLATILITY_5M"] = compact(
        matrix("realized_volatility_5m")
    )
    factors["MAY19_NIGHT_SLEEP_FROST_ROAD_20D"] = compact(
        compute_night_sleep_factor(
            matrix("night_sleep_t_intercept"), membership, dates, 20
        )
    )
    factors["MAY23_SAME_TIME_PEAK_RIDGE_CORR_20D"] = compact(
        require_complete_market_window(
            matrix("same_time_peak_ridge_corr_20d"), matrix("volume"), 20
        )
    )
    factors["MAY27_IDIOSYNCRATIC_INTRADAY_RETURN_VOL_20D"] = compact(
        compute_idiosyncratic_intraday_volatility(
            arrays["open"], arrays["close"], membership, 20, min_stocks
        )
    )
    return factors, arrays


def _write_methodology(args: argparse.Namespace) -> None:
    settings = january.Settings(
        cache_dir=Path(args.cache_dir),
        output_dir=Path(args.output_dir),
        qlib_dir=Path(args.qlib_dir),
        start=args.start,
        backtest_start=args.backtest_start,
        end=args.end,
        split_date=args.split_date,
    )
    methodology = {
        "settings": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        },
        "may_cache_dir": str(Path(args.may_cache_dir)),
        "factor_meta": FACTOR_META,
        "calculated_factor_count": 9,
        "unavailable_factor_count": 7,
        "backtest_conventions": {
            "training_period": "2016-01-04至2020-12-31只用于决定选高值还是低值",
            "later_period": "2021-01-01至2025-12-31单独检查",
            "universe": "按日期取中证800与中证1000历史成分合集",
            "eligibility": "未复权价格不低于3元，过去20日平均成交额不低于500万元",
            "amount_window": "沿用前期程序：过去20个市场交易日内至少有10个有效成交额时计算平均值",
            "daily_trade": "每5个交易日选一次，次日开盘买入并持有5个交易日",
            "monthly_trade": "月末信号，下月首日开盘买入并在月末收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "missing_rule": "公式、参数或日度合成办法不完整的因子只登记原因，不计算数值",
            "b_volume_distribution": "从VSP按价格距离升序扩展，同距离按成交量降序、价格升序；累计成交量严格超过50%时停止",
            "jump_definition": "复用1月6日、2月6日、3月16日卡片的5分钟三幂变差和α=4定义",
            "panic_market_return": "市场日收益优先使用SH000985在当日和前日收盘都有效时的收益，否则使用SH000906当日收益；不拼接指数点位。五月公式需要市场收盘价，因此把合成收益从1开始累乘为连续指数。",
            "peak_ridge": "同一个最近20日窗口内先用同一分钟均值加1倍总体标准差分类，再统计峰岭；午休两边不相邻；最近20个市场交易日任一日分钟数据不完整就留空",
            "pca": "逐股20日去均值、不额外缩放；第一成分解释率低于20%时使用前两成分",
            "formal_output": "五月正式结果使用original_frequency_*文件；普通backtest_summary只作统一五日比较，不作为月频因子的正式结果",
        },
    }
    january.atomic_write_json(methodology, Path(args.output_dir) / "methodology.json")


def main(argv: list[str] | None = None) -> int:
    global _MAY_CACHE_DIR, _QLIB_DIR
    args = parse_args(argv)
    _MAY_CACHE_DIR = Path(args.may_cache_dir)
    _QLIB_DIR = Path(args.qlib_dir)

    previous.FACTOR_META = FACTOR_META
    previous.BASE_COLUMNS = BASE_COLUMNS
    previous.FACTOR_COLUMNS = MAY_COLUMNS
    previous.load_panel = load_combined_panel
    previous.build_factor_matrices = build_factor_matrices
    previous.parse_args = lambda _argv=None: args
    result = previous.main(None)
    _write_methodology(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
