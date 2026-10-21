# -*- coding: utf-8 -*-
"""计算四月量价因子，并沿用一月、二三月的回测口径完成评价。"""

from __future__ import annotations

import argparse
import gc
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import run_february_march_backtest as february_march
import run_january_minute_backtest as january


BASE_DIR = Path(__file__).resolve().parent


FACTOR_META = {
    "A03_TAIL_VOLUME_SHARE_20D": {
        "name": "尾盘成交占比",
        "calendar_month": 4,
        "calendar_day": 3,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "分钟标签表示区间结束时点，14:30至15:00按14:31至15:00共30根计算；日值再取20日均值。",
    },
    "A05_SMALL_BOARD_CSAD_20D": {
        "name": "CSAD模型（基于“小型”板块）",
        "calendar_month": 4,
        "calendar_day": 5,
        "source_frequency": "月频，120日相关与20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_standardization_window",
        "note": "原图没有说明CSAD时序标准化采用多少日；该选择会直接改变结果，本轮不自行设定。",
    },
    "A06_DAZZLING_VOLATILITY_20D": {
        "name": "耀眼波动率",
        "calendar_month": 4,
        "calendar_day": 6,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_change_and_return_definition",
        "note": "原图没有说明成交量“环比差值”用相减还是变化率，也未说明分钟收益和标准差的具体取法。",
    },
    "A10_P_SHAPED_VOLUME_DISTRIBUTION": {
        "name": "p型成交量分布",
        "calendar_month": 4,
        "calendar_day": 10,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_interval_rules",
        "note": "原图没有说明成交量支撑区向外扩展时的价格步长、并列次序和差值方向。",
    },
    "A12_SMALL_DOWN_JUMP_VOL_5M": {
        "name": "小的下行跳跃波动率",
        "calendar_month": 4,
        "calendar_day": 12,
        "source_frequency": "日频，5分钟",
        "signal_frequency": "daily",
        "expected_direction": "positive",
        "strict_status": "runnable",
        "note": "RVSJN=RVJN-RVLJN，5分钟时Δ=1/48、α=4；沿用此前检查，绝对5分钟收益超过15%时按0处理。",
    },
    "A13_CORRECTED_OVERNIGHT_REVERSAL_20D": {
        "name": "修正的隔夜反转",
        "calendar_month": 4,
        "calendar_day": 13,
        "source_frequency": "月频，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "先算隔夜收益与当日横截面均值的绝对距离；20日总体标准差低于当日横截面均值时将20日均值取反。",
    },
    "A14_SHADOW_CLOSE_STD": {
        "name": "上下影线/收盘价的标准差",
        "calendar_month": 4,
        "calendar_day": 14,
        "source_frequency": "月频，日线输入",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_window_and_definition",
        "note": "原图没有给观察天数，也没有写明上下影线长度及标准差的具体取法。",
    },
    "A15_NOON_ANCIENT_TREE": {
        "name": "午蔽古木",
        "calendar_month": 4,
        "calendar_day": 15,
        "source_frequency": "日频，1分钟",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "boundary_interpreted",
        "note": "取成交量差回归的截距t值绝对值；F值低于当日横截面均值时取反。因首分钟缺少前值，从第7分钟起回归；原图正文又写作“日午蔽古木”。",
    },
    "A17_ACTIVE_CHIP_SHARE_UPPER_LOWER": {
        "name": "上方/下方活动筹码占比",
        "calendar_month": 4,
        "calendar_day": 17,
        "source_frequency": "日频",
        "signal_frequency": "unavailable",
        "expected_direction": "unknown",
        "strict_status": "missing_chip_distribution",
        "note": "没有逐价位筹码分布、筹码生成方法和完整的历史涨跌停规则。",
    },
    "A19_BURST_AMOUNT_CORR_20D": {
        "name": "喷发成交额相关性",
        "calendar_month": 4,
        "calendar_day": 19,
        "source_frequency": "日频，1分钟，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "在同一个最近20日内，按同分钟均值加1倍总体标准差识别喷发点，再计算喷发时点成交额与下一交易分钟成交额的相关系数；异常成交额日不进入结果。",
    },
    "A21_DOWNSIDE_VOL_SHARE_5M_20D": {
        "name": "高频下行波动占比（5分钟）",
        "calendar_month": 4,
        "calendar_day": 21,
        "source_frequency": "月频，5分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "positive",
        "strict_status": "runnable_primary_frequency",
        "note": "采用5分钟方案，日值为1减高频上行波动占比，月末取20日均值。",
    },
    "A22_INTRADAY_SNR": {
        "name": "日内信噪比",
        "calendar_month": 4,
        "calendar_day": 22,
        "source_frequency": "日频，分钟输入",
        "signal_frequency": "unavailable",
        "expected_direction": "positive",
        "strict_status": "missing_emd_rules",
        "note": "原图把EMD分解步骤留给参考文献，未给端点、停止条件和二层或三层的确定规则。",
    },
    "A23_CHIP_RETURN_250D": {
        "name": "筹码收益",
        "calendar_month": 4,
        "calendar_day": 23,
        "source_frequency": "日频，250日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "incomplete_return_formula",
        "note": "图片公式只给历史筹码加权成本，没有写出当前成交均价相对该成本的收益计算式。",
    },
    "A24_SNR_ENHANCED_REVERSAL_20D": {
        "name": "信噪比增强反转",
        "calendar_month": 4,
        "calendar_day": 24,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "unavailable",
        "expected_direction": "negative",
        "strict_status": "missing_base_factors",
        "note": "依赖未能严格计算的日内信噪比，且原图没有给20日反转收益的具体定义。",
    },
    "A25_PV_CORR_STD_20D_NEUTRAL": {
        "name": "价量相关性波动性因子",
        "calendar_month": 4,
        "calendar_day": 25,
        "source_frequency": "月频，1分钟，20日窗口",
        "signal_frequency": "monthly",
        "expected_direction": "negative",
        "strict_status": "runnable",
        "note": "每日计算分钟收盘价与分钟成交量相关，取20日总体标准差，再对log原始总市值逐日回归取残差。",
    },
    "A30_PCA_IDIOSYNCRATIC_VOL_20D": {
        "name": "基于PCA的特质波动率",
        "calendar_month": 4,
        "calendar_day": 30,
        "source_frequency": "日频，20日窗口",
        "signal_frequency": "daily",
        "expected_direction": "unknown",
        "strict_status": "runnable",
        "note": "当日股票范围内用20日日收益做协方差PCA，只减均值不除标准差；第一项解释不足20%时使用前两项，取总体残差标准差。",
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
    "total_shares",
    "qfq_ratio",
    "prev_close",
    "vwap_outside_range_1pct",
    "overnight_return",
    "upside_vol_share_5m",
]


APRIL_COLUMNS = [
    "date",
    "code",
    "tail_volume_share_daily",
    "small_down_jump_vol_5m",
    "noon_ancient_tree_abs_t",
    "noon_ancient_tree_f_all",
    "burst_amount_corr_20d",
    "pv_corr_daily",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default=str(BASE_DIR / "february_march_results" / "cache" / "daily_by_stock"),
        help="已有每日价格及二三月字段目录",
    )
    parser.add_argument(
        "--april-cache-dir",
        default=str(
            BASE_DIR
            / "april_factor_results"
            / "cache"
            / "april_daily_by_stock"
        ),
        help="四月新增每日字段目录",
    )
    parser.add_argument(
        "--output-dir", default=str(BASE_DIR / "april_factor_results")
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
    files: list[Path],
    start: str,
    end: str,
    columns: list[str],
    label: str,
) -> pd.DataFrame:
    """只读取本轮所需列，并统一日期、股票代码和数值类型。"""
    if not files:
        raise FileNotFoundError(f"没有找到{label}文件")
    requested = list(dict.fromkeys(columns))
    available = set(pq.ParquetFile(files[0]).schema_arrow.names)
    missing = [column for column in requested if column not in available]
    if missing:
        raise ValueError(f"{label}缺少本轮所需列：" + ", ".join(missing))

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
            if column in {"date", "code"} or pd.api.types.is_bool_dtype(
                frame[column].dtype
            ):
                continue
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(
                np.float32
            )
        frames.append(frame)
        if index % 500 == 0:
            print(f"  {label}已读取 {index}/{len(files)} 只股票", flush=True)
    panel = pd.concat(frames, ignore_index=True)
    return panel.sort_values(["date", "code"]).drop_duplicates(
        ["date", "code"], keep="last"
    )


def merge_panels(base_panel: pd.DataFrame, april_panel: pd.DataFrame) -> pd.DataFrame:
    """以已有价格记录为准，按日期和股票代码补入四月字段。"""
    merged = base_panel.merge(
        april_panel,
        on=["date", "code"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    matched = int((merged["_merge"] == "both").sum())
    if matched != len(base_panel) or len(april_panel) != len(base_panel):
        missing = len(base_panel) - matched
        extra = len(april_panel) - matched
        raise ValueError(
            "已有缓存与四月缓存必须完整一一对应："
            f"已有{len(base_panel)}行，四月{len(april_panel)}行，"
            f"缺{missing}行，多{extra}行"
        )
    merged = merged.drop(columns="_merge").sort_values(["date", "code"])
    merged.attrs["april_matched_rows"] = matched
    merged.attrs["base_rows"] = len(base_panel)
    return merged


def load_merged_panel(
    base_files: list[Path],
    april_files: list[Path],
    start: str,
    end: str,
) -> pd.DataFrame:
    base_panel = load_panel(base_files, start, end, BASE_COLUMNS, "已有缓存")
    april_panel = load_panel(april_files, start, end, APRIL_COLUMNS, "四月缓存")
    return merge_panels(base_panel, april_panel)


def cross_sectional_distance(
    values: np.ndarray, membership: np.ndarray, min_stocks: int
) -> np.ndarray:
    """逐日计算个股值与当日股票范围均值的绝对距离。"""
    result = np.full(values.shape, np.nan, dtype=np.float64)
    for index in range(len(values)):
        valid = membership[index] & np.isfinite(values[index])
        if valid.sum() < min_stocks:
            continue
        indices = np.flatnonzero(valid)
        current = values[index, indices].astype(np.float64)
        result[index, indices] = np.abs(current - current.mean())
    return result


def pca_residual_volatility_one_window(
    window_returns: np.ndarray,
    first_component_threshold: float = 0.20,
) -> tuple[np.ndarray, int, float]:
    """对一个20日收益块做协方差PCA，返回各股总体残差标准差。"""
    values = np.asarray(window_returns, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 2:
        raise ValueError("PCA收益块至少需要2个交易日和2只股票")
    if not np.isfinite(values).all():
        raise ValueError("PCA收益块不能含缺值")

    centered = values - values.mean(axis=0, keepdims=True)
    time_covariance = centered @ centered.T
    eigenvalues, eigenvectors = np.linalg.eigh(time_covariance)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    total = float(eigenvalues.sum())
    if total <= 1e-30:
        return np.full(values.shape[1], np.nan), 0, np.nan

    first_ratio = float(eigenvalues[-1] / total)
    component_count = 2 if first_ratio < first_component_threshold else 1
    positive_count = int(np.sum(eigenvalues > total * 1e-14))
    component_count = min(component_count, positive_count)
    if component_count == 0:
        return np.full(values.shape[1], np.nan), 0, first_ratio

    directions = eigenvectors[:, -component_count:]
    fitted = directions @ (directions.T @ centered)
    residual = centered - fitted
    return np.std(residual, axis=0, ddof=0), component_count, first_ratio


def compute_pca_idiosyncratic_volatility(
    close: np.ndarray,
    membership: np.ndarray,
    window: int,
    min_stocks: int,
    first_component_threshold: float = 0.20,
) -> np.ndarray:
    """每日只使用当日股票范围内具有完整20日收益的股票。"""
    returns = january.daily_returns(np.asarray(close, dtype=np.float64))
    result = np.full(returns.shape, np.nan, dtype=np.float64)
    for index in range(window - 1, len(returns)):
        block = returns[index - window + 1 : index + 1]
        valid = membership[index] & np.isfinite(block).all(axis=0)
        if valid.sum() < min_stocks:
            continue
        indices = np.flatnonzero(valid)
        volatility, _, _ = pca_residual_volatility_one_window(
            block[:, indices], first_component_threshold
        )
        result[index, indices] = volatility
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
        "total_shares": matrix("total_shares"),
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

    def cached(column: str) -> np.ndarray:
        return matrix(column)

    print("[3/6] 计算四月8个可复原因子...", flush=True)
    factors: dict[str, np.ndarray] = {}

    tail_volume_share_daily = cached("tail_volume_share_daily")
    factors["A03_TAIL_VOLUME_SHARE_20D"] = compact(
        january.rolling_mean(tail_volume_share_daily, 20)
    )
    factors["A12_SMALL_DOWN_JUMP_VOL_5M"] = compact(
        cached("small_down_jump_vol_5m")
    )

    overnight_distance = cross_sectional_distance(
        cached("overnight_return"), membership, min_stocks
    )
    overnight_mean, overnight_std = january.rolling_mean_std(
        overnight_distance, 20
    )
    factors["A13_CORRECTED_OVERNIGHT_REVERSAL_20D"] = compact(
        february_march.correct_below_cross_section_mean(
            overnight_mean, overnight_std, membership, min_stocks
        )
    )
    del overnight_distance, overnight_mean, overnight_std

    factors["A15_NOON_ANCIENT_TREE"] = compact(
        february_march.correct_below_cross_section_mean(
            cached("noon_ancient_tree_abs_t"),
            cached("noon_ancient_tree_f_all"),
            membership,
            min_stocks,
        )
    )
    burst_amount_corr = cached("burst_amount_corr_20d").astype(
        np.float64, copy=True
    )
    _, observed_day_count = january.rolling_sum_count(
        tail_volume_share_daily, 20
    )
    # 每股缓存按已有日期保存；停牌会造成相邻两行并非相邻市场交易日。
    # 合并到市场日历后，只保留最近20个市场交易日都有分钟数据的结果。
    burst_amount_corr[observed_day_count < 20] = np.nan
    factors["A19_BURST_AMOUNT_CORR_20D"] = compact(burst_amount_corr)
    factors["A21_DOWNSIDE_VOL_SHARE_5M_20D"] = compact(
        january.rolling_mean(1.0 - cached("upside_vol_share_5m"), 20)
    )

    _, pv_corr_std = january.rolling_mean_std(cached("pv_corr_daily"), 20)
    market_cap = arrays["raw_close"].astype(np.float64) * arrays[
        "total_shares"
    ].astype(np.float64)
    log_market_cap = np.full(market_cap.shape, np.nan, dtype=np.float64)
    valid_market_cap = np.isfinite(market_cap) & (market_cap > 0)
    log_market_cap[valid_market_cap] = np.log(market_cap[valid_market_cap])
    factors["A25_PV_CORR_STD_20D_NEUTRAL"] = compact(
        february_march.cross_sectional_residual(
            pv_corr_std, [log_market_cap], membership, min_stocks
        )
    )
    del pv_corr_std, market_cap, log_market_cap

    factors["A30_PCA_IDIOSYNCRATIC_VOL_20D"] = compact(
        compute_pca_idiosyncratic_volatility(
            arrays["close"], membership, 20, min_stocks, 0.20
        )
    )
    return factors, arrays


def build_status_frame(factors: dict[str, np.ndarray]) -> pd.DataFrame:
    rows = []
    for code, meta in FACTOR_META.items():
        rows.append({"factor": code, **meta, "calculated": code in factors})
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
    april_cache_dir = Path(args.april_cache_dir)
    started = time.time()
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    # 一月公共函数会从模块变量读取因子频率，只替换当前进程中的表。
    january.FACTOR_META = FACTOR_META

    print("[1/6] 读取两份每日缓存和历史指数成分...", flush=True)
    base_files = january.list_cache_files(settings.cache_dir)
    april_files = january.list_cache_files(april_cache_dir)
    dates = january.read_calendar(settings.qlib_dir, settings.start, settings.end)
    panel = load_merged_panel(
        base_files, april_files, settings.start, settings.end
    )
    matched_rows = int(panel.attrs.get("april_matched_rows", 0))
    base_rows = int(panel.attrs.get("base_rows", len(panel)))
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
        f"  {len(dates)}个交易日，{len(stocks)}只缓存股票；四月字段匹配"
        f"{matched_rows}/{base_rows}行",
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
        "april_cache_dir": str(april_cache_dir),
        "factor_meta": FACTOR_META,
        "loaded_base_cache_columns": BASE_COLUMNS,
        "loaded_april_cache_columns": APRIL_COLUMNS,
        "cache_merge": {
            "keys": ["date", "code"],
            "method": "以已有价格缓存为准左连接四月字段",
            "matched_rows": matched_rows,
            "base_rows": base_rows,
        },
        "backtest_conventions": {
            "training_rule": "未来收益退出日不晚于2020-12-31",
            "later_period_rule": "入场日晚于2020-12-31",
            "daily_icir_annualizer": "按实际有效信号数/年再除以5后开平方，因为未来5日收益相互重叠",
            "daily_trade_frequency": "日频因子每5个交易日选一次并持有5日",
            "monthly_trade_frequency": "月末信号，次月首个交易日开盘买入并在当月最后交易日收盘退出",
            "cost": "买入0.03%，卖出0.13%",
            "universe": "按当时日期取中证800与中证1000合集",
            "eligibility": "原始价格不低于3元，过去20日平均成交额不低于500万元",
            "amount_quality": "价格与成交额明显不一致的日期不进入成交额筛选窗口",
            "april_burst_amount_quality": "A19在四月缓存中检查成交额异常；20日窗口内含异常日时相关值留空",
            "april_burst_definition": "A19每个信号日只取最近20日，在这20日内按同分钟均值加1倍总体标准差识别喷发点，并在同一20日内计算喷发时点成交额与下一交易分钟成交额的相关系数",
            "april_burst_calendar_check": "A19合并到市场日历后，只保留最近20个市场交易日都有分钟数据的结果；停牌或缺日窗口留空",
            "small_down_jump_quality": "A12沿用此前分钟收益检查，绝对5分钟收益超过15%时按0处理",
            "noon_ancient_tree": "A15因首分钟没有成交量差前值，当前值及5阶滞后全部可用时从第7分钟起回归",
            "pca": "当日股票范围内使用过去20日日收益；协方差做法只减均值不除标准差；第一主成分解释不足20%时使用前两项；总体残差标准差为因子",
        },
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

    daily_ic = (
        pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    )
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
