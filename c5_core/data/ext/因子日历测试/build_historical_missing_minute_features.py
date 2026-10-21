# -*- coding: utf-8 -*-
"""补算历史因子仍缺少的每日分钟特征。

每只股票只用一条 DuckDB 查询读取原始分钟文件，并在同一条查询中按时刻
接入已有的市场分钟平均收益。结果按股票保存为轻量 Parquet。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Sequence

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MINUTE_DIR = Path(r"/Users/leslie/Downloads/中证500量化60min/c5_core/data/minute/A股 1min 数据 2000-2026年/分钟数据_前复权_Parquet/data")
DEFAULT_MARKET_DIR = BASE_DIR / "minute_completion_runs" / "shared_market" / "yearly"
DEFAULT_OUTPUT_DIR = (
    BASE_DIR
    / "historical_calendar_backtest_results"
    / "cache"
    / "missing_minute_by_stock"
)
FEATURE_VERSION = "historical_missing_minute_v1"
CODE_PATTERN = re.compile(r"^(sh|sz|bj)\d{6}$", re.IGNORECASE)


OUTPUT_COLUMNS = [
    "date",
    "code",
    "minute_count",
    "market_minute_count",
    "close_auction_volume_share_5m",
    "price_diff_pos_corr",
    "price_diff_neg_corr",
    "price_diff_pos_pair_corr",
    "price_diff_neg_pair_corr",
    "volume_diff_pos_corr",
    "volume_diff_neg_corr",
    "volume_diff_pos_pair_corr",
    "volume_diff_neg_pair_corr",
    "minute_volume_bin_entropy_trial",
    "top10pct_minute_return",
    "morning_return",
    "afternoon_return",
    "semibeta_market_pos_asset_pos",
    "semibeta_market_neg_asset_pos",
    "semibeta_market_pos_asset_neg",
    "semibeta_market_neg_asset_neg",
    "abnormal_high_vol_return_ratio_trial",
    "follow_wave_base",
]


FEATURE_NOTES = {
    "close_auction_volume_share_5m": "14:56—15:00 成交量/全日成交量。",
    "price_diff_pos_corr": "当 ΔP_t>0 时 corr(ΔP_t, P_{t+1})。",
    "price_diff_neg_corr": "当 ΔP_t<0 时 corr(ΔP_t, P_{t+1})。",
    "price_diff_pos_pair_corr": "当 ΔP_t、ΔP_{t+1} 均大于 0 时 corr(ΔP_t, ΔP_{t+1})。",
    "price_diff_neg_pair_corr": "当 ΔP_t、ΔP_{t+1} 均小于 0 时 corr(ΔP_t, ΔP_{t+1})。",
    "volume_diff_pos_corr": "当 ΔV_t>0 时 corr(ΔV_t, V_{t+1})。",
    "volume_diff_neg_corr": "当 ΔV_t<0 时 corr(ΔV_t, V_{t+1})。",
    "volume_diff_pos_pair_corr": "当 ΔV_t、ΔV_{t+1} 均大于 0 时 corr(ΔV_t, ΔV_{t+1})。",
    "volume_diff_neg_pair_corr": "当 ΔV_t、ΔV_{t+1} 均小于 0 时 corr(ΔV_t, ΔV_{t+1})。",
    "minute_volume_bin_entropy_trial": "原文未给 maxbin；试算固定最多 20 个等距桶。",
    "top10pct_minute_return": "复合当日简单收益最高 10% 的分钟收益。",
    "morning_return": "复合 09:31—11:30 简单分钟收益。",
    "afternoon_return": "复合 13:01—15:00 简单分钟收益。",
    "semibeta_market_pos_asset_pos": "正市场分钟收益与正个股分钟收益部分。",
    "semibeta_market_neg_asset_pos": "负市场分钟收益与正个股分钟收益部分，乘积取负。",
    "semibeta_market_pos_asset_neg": "正市场分钟收益与负个股分钟收益部分，乘积取负。",
    "semibeta_market_neg_asset_neg": "负市场分钟收益与负个股分钟收益部分。",
    "abnormal_high_vol_return_ratio_trial": (
        "用最近 5 分钟共 20 个 OHLC 价格的变异系数平方作为更优波动率；"
        "原文 n 未明确，试算以当日均值+1倍标准差筛选，并取总体协方差。"
    ),
    "follow_wave_base": "按近 20 日日内收益均值划分当日高低价时刻，成交额差除以收盘流通市值。",
}


@dataclass(frozen=True)
class BuildTask:
    code: str
    minute_path: Path
    market_files: tuple[Path, ...]
    start: str
    end: str
    output_dir: Path
    memory_limit: str


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _normalize_code(value: str) -> str:
    code = value.strip().lower()
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError(f"股票代码格式错误：{value}")
    return code


def parse_codes(value: str | None, minute_dir: Path) -> list[str]:
    """读取代码文字、代码文件、Parquet 目录，或分钟目录内全部代码。"""

    if not value:
        return sorted(path.stem.lower() for path in minute_dir.glob("*.parquet") if CODE_PATTERN.fullmatch(path.stem))
    candidate = Path(value)
    if candidate.is_dir():
        return sorted(
            path.stem.lower()
            for path in candidate.glob("*.parquet")
            if CODE_PATTERN.fullmatch(path.stem)
        )
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8-sig")
        items = re.split(r"[,\s]+", text)
    else:
        items = re.split(r"[,\s]+", value)
    return sorted({_normalize_code(item) for item in items if item.strip()})


def market_files_for_range(market_dir: Path, start: str, end: str) -> tuple[Path, ...]:
    first_year, last_year = _parse_date(start).year, _parse_date(end).year
    files = tuple(
        market_dir / f"shared_market_minute_stats_{year}.parquet"
        for year in range(first_year, last_year + 1)
    )
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少市场分钟文件：{missing}")
    return files


def _read_stock_once(task: BuildTask) -> pd.DataFrame:
    """用一条 DuckDB 查询读取一只股票并接入市场分钟收益。"""

    end_exclusive = _parse_date(task.end) + timedelta(days=1)
    connection = duckdb.connect()
    try:
        connection.execute(f"SET memory_limit='{task.memory_limit}'")
        connection.execute("SET threads=1")
        query = """
            WITH stock AS (
                SELECT
                    datetime,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(volume AS DOUBLE) AS volume,
                    CAST(amount AS DOUBLE) AS amount,
                    CAST(float_shares AS DOUBLE) AS float_shares
                FROM read_parquet(?)
                WHERE datetime >= CAST(? AS TIMESTAMP)
                  AND datetime < CAST(? AS TIMESTAMP)
            ), market AS (
                SELECT datetime, CAST(market_mean_return AS DOUBLE) AS market_mean_return
                FROM read_parquet(?)
                WHERE datetime >= CAST(? AS TIMESTAMP)
                  AND datetime < CAST(? AS TIMESTAMP)
            )
            SELECT stock.*, market.market_mean_return
            FROM stock
            LEFT JOIN market USING (datetime)
            ORDER BY datetime
        """
        return connection.execute(
            query,
            [
                str(task.minute_path),
                task.start,
                end_exclusive.isoformat(),
                [str(path) for path in task.market_files],
                task.start,
                end_exclusive.isoformat(),
            ],
        ).fetchdf()
    finally:
        connection.close()


def _read_stock_daily(task: BuildTask) -> pd.DataFrame:
    """让 DuckDB 直接从分钟文件算出一只股票的每日特征。"""

    end_exclusive = _parse_date(task.end) + timedelta(days=1)
    connection = duckdb.connect()
    try:
        connection.execute(f"SET memory_limit='{task.memory_limit}'")
        connection.execute("SET threads=1")
        query = r"""
            WITH stock AS (
                SELECT
                    datetime,
                    CAST(datetime AS DATE) AS date,
                    CAST(open AS DOUBLE) AS open,
                    CAST(high AS DOUBLE) AS high,
                    CAST(low AS DOUBLE) AS low,
                    CAST(close AS DOUBLE) AS close,
                    CAST(volume AS DOUBLE) AS volume,
                    CAST(amount AS DOUBLE) AS amount,
                    CAST(float_shares AS DOUBLE) AS float_shares,
                    EXTRACT(hour FROM datetime) * 60 + EXTRACT(minute FROM datetime) AS minute_of_day
                FROM read_parquet(?)
                WHERE datetime >= CAST(? AS TIMESTAMP)
                  AND datetime < CAST(? AS TIMESTAMP)
            ), market AS (
                SELECT datetime, CAST(market_mean_return AS DOUBLE) AS market_mean_return
                FROM read_parquet(?)
                WHERE datetime >= CAST(? AS TIMESTAMP)
                  AND datetime < CAST(? AS TIMESTAMP)
            ), joined AS (
                SELECT stock.*, market.market_mean_return
                FROM stock
                LEFT JOIN market USING (datetime)
            ), sequenced AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER day_order AS row_number,
                    LAG(close, 1) OVER day_order AS previous_close,
                    LAG(close, 2) OVER day_order AS second_previous_close,
                    LAG(volume, 1) OVER day_order AS previous_volume,
                    LAG(volume, 2) OVER day_order AS second_previous_volume,
                    LAG(open, 1) OVER day_order AS open_lag_1,
                    LAG(high, 1) OVER day_order AS high_lag_1,
                    LAG(low, 1) OVER day_order AS low_lag_1,
                    LAG(open, 2) OVER day_order AS open_lag_2,
                    LAG(high, 2) OVER day_order AS high_lag_2,
                    LAG(low, 2) OVER day_order AS low_lag_2,
                    LAG(open, 3) OVER day_order AS open_lag_3,
                    LAG(high, 3) OVER day_order AS high_lag_3,
                    LAG(low, 3) OVER day_order AS low_lag_3,
                    LAG(close, 3) OVER day_order AS close_lag_3,
                    LAG(open, 4) OVER day_order AS open_lag_4,
                    LAG(high, 4) OVER day_order AS high_lag_4,
                    LAG(low, 4) OVER day_order AS low_lag_4,
                    LAG(close, 4) OVER day_order AS close_lag_4,
                    FIRST_VALUE(open) OVER day_full AS day_open,
                    LAST_VALUE(close) OVER day_full AS day_close,
                    LAST_VALUE(float_shares) OVER day_full AS day_float_shares
                FROM joined
                WINDOW
                    day_order AS (PARTITION BY date ORDER BY datetime),
                    day_full AS (
                        PARTITION BY date ORDER BY datetime
                        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                    )
            ), price_blocks AS (
                SELECT
                    *,
                    [
                        open_lag_4, high_lag_4, low_lag_4, close_lag_4,
                        open_lag_3, high_lag_3, low_lag_3, close_lag_3,
                        open_lag_2, high_lag_2, low_lag_2, second_previous_close,
                        open_lag_1, high_lag_1, low_lag_1, previous_close,
                        open, high, low, close
                    ] AS price_block_5
                FROM sequenced
            ), minute_values AS (
                SELECT
                    *,
                    CASE
                        WHEN row_number = 1 AND isfinite(open) AND open > 0 THEN close / open - 1.0
                        WHEN row_number > 1 AND isfinite(previous_close) AND previous_close > 0
                            THEN close / previous_close - 1.0
                    END AS minute_return,
                    previous_close - second_previous_close AS price_diff,
                    close - previous_close AS next_price_diff,
                    previous_volume - second_previous_volume AS volume_diff,
                    volume - previous_volume AS next_volume_diff,
                    CASE
                        WHEN row_number >= 5
                             AND list_count(list_filter(price_block_5, value -> isfinite(value))) = 20
                             AND abs(list_avg(price_block_5)) > 0
                        THEN power(
                            list_stddev_pop(price_block_5) / list_avg(price_block_5), 2
                        )
                    END AS better_volatility
                FROM price_blocks
            ), correlation_means AS (
                SELECT
                    date,
                    AVG(price_diff) FILTER (WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)) AS pp_x,
                    AVG(close) FILTER (WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)) AS pp_y,
                    AVG(price_diff) FILTER (WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)) AS pn_x,
                    AVG(close) FILTER (WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)) AS pn_y,
                    AVG(price_diff) FILTER (WHERE price_diff > 0 AND next_price_diff > 0 AND isfinite(price_diff) AND isfinite(next_price_diff)) AS ppp_x,
                    AVG(next_price_diff) FILTER (WHERE price_diff > 0 AND next_price_diff > 0 AND isfinite(price_diff) AND isfinite(next_price_diff)) AS ppp_y,
                    AVG(price_diff) FILTER (WHERE price_diff < 0 AND next_price_diff < 0 AND isfinite(price_diff) AND isfinite(next_price_diff)) AS pnn_x,
                    AVG(next_price_diff) FILTER (WHERE price_diff < 0 AND next_price_diff < 0 AND isfinite(price_diff) AND isfinite(next_price_diff)) AS pnn_y,
                    AVG(volume_diff) FILTER (WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)) AS vp_x,
                    AVG(volume) FILTER (WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)) AS vp_y,
                    AVG(volume_diff) FILTER (WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)) AS vn_x,
                    AVG(volume) FILTER (WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)) AS vn_y,
                    AVG(volume_diff) FILTER (WHERE volume_diff > 0 AND next_volume_diff > 0 AND isfinite(volume_diff) AND isfinite(next_volume_diff)) AS vpp_x,
                    AVG(next_volume_diff) FILTER (WHERE volume_diff > 0 AND next_volume_diff > 0 AND isfinite(volume_diff) AND isfinite(next_volume_diff)) AS vpp_y,
                    AVG(volume_diff) FILTER (WHERE volume_diff < 0 AND next_volume_diff < 0 AND isfinite(volume_diff) AND isfinite(next_volume_diff)) AS vnn_x,
                    AVG(next_volume_diff) FILTER (WHERE volume_diff < 0 AND next_volume_diff < 0 AND isfinite(volume_diff) AND isfinite(next_volume_diff)) AS vnn_y,
                    AVG(better_volatility) FILTER (
                        WHERE isfinite(better_volatility) AND better_volatility >= 0
                          AND isfinite(minute_return)
                    ) AS better_mean
                FROM minute_values
                GROUP BY date
            ), daily_stats AS (
                SELECT
                    date,
                    COUNT(*) AS minute_count,
                    COUNT(*) FILTER (WHERE isfinite(market_mean_return)) AS market_minute_count,
                    MAX(day_open) AS day_open,
                    MAX(day_close) AS day_close,
                    MAX(day_float_shares) AS day_float_shares,
                    CASE
                        WHEN SUM(volume) FILTER (WHERE isfinite(volume)) > 0
                        THEN COALESCE(
                            SUM(volume) FILTER (
                                WHERE isfinite(volume) AND minute_of_day BETWEEN 896 AND 900
                            ), 0.0
                        ) / SUM(volume) FILTER (WHERE isfinite(volume))
                    END AS close_auction_volume_share_5m,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)
                    ) >= 3 THEN
                        SUM((price_diff - means.pp_x) * (close - means.pp_y)) FILTER (
                            WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)
                        ) / NULLIF(sqrt(
                            SUM(power(price_diff - means.pp_x, 2)) FILTER (
                                WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)
                            ) * SUM(power(close - means.pp_y, 2)) FILTER (
                                WHERE price_diff > 0 AND isfinite(price_diff) AND isfinite(close)
                            )
                        ), 0.0) END AS price_diff_pos_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)
                    ) >= 3 THEN
                        SUM((price_diff - means.pn_x) * (close - means.pn_y)) FILTER (
                            WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)
                        ) / NULLIF(sqrt(
                            SUM(power(price_diff - means.pn_x, 2)) FILTER (
                                WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)
                            ) * SUM(power(close - means.pn_y, 2)) FILTER (
                                WHERE price_diff < 0 AND isfinite(price_diff) AND isfinite(close)
                            )
                        ), 0.0) END AS price_diff_neg_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE price_diff > 0 AND next_price_diff > 0
                          AND isfinite(price_diff) AND isfinite(next_price_diff)
                    ) >= 3 THEN
                        SUM((price_diff - means.ppp_x) * (next_price_diff - means.ppp_y)) FILTER (
                            WHERE price_diff > 0 AND next_price_diff > 0
                              AND isfinite(price_diff) AND isfinite(next_price_diff)
                        ) / NULLIF(sqrt(
                            SUM(power(price_diff - means.ppp_x, 2)) FILTER (
                                WHERE price_diff > 0 AND next_price_diff > 0
                                  AND isfinite(price_diff) AND isfinite(next_price_diff)
                            ) * SUM(power(next_price_diff - means.ppp_y, 2)) FILTER (
                                WHERE price_diff > 0 AND next_price_diff > 0
                                  AND isfinite(price_diff) AND isfinite(next_price_diff)
                            )
                        ), 0.0) END AS price_diff_pos_pair_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE price_diff < 0 AND next_price_diff < 0
                          AND isfinite(price_diff) AND isfinite(next_price_diff)
                    ) >= 3 THEN
                        SUM((price_diff - means.pnn_x) * (next_price_diff - means.pnn_y)) FILTER (
                            WHERE price_diff < 0 AND next_price_diff < 0
                              AND isfinite(price_diff) AND isfinite(next_price_diff)
                        ) / NULLIF(sqrt(
                            SUM(power(price_diff - means.pnn_x, 2)) FILTER (
                                WHERE price_diff < 0 AND next_price_diff < 0
                                  AND isfinite(price_diff) AND isfinite(next_price_diff)
                            ) * SUM(power(next_price_diff - means.pnn_y, 2)) FILTER (
                                WHERE price_diff < 0 AND next_price_diff < 0
                                  AND isfinite(price_diff) AND isfinite(next_price_diff)
                            )
                        ), 0.0) END AS price_diff_neg_pair_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)
                    ) >= 3 THEN
                        SUM((volume_diff - means.vp_x) * (volume - means.vp_y)) FILTER (
                            WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)
                        ) / NULLIF(sqrt(
                            SUM(power(volume_diff - means.vp_x, 2)) FILTER (
                                WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)
                            ) * SUM(power(volume - means.vp_y, 2)) FILTER (
                                WHERE volume_diff > 0 AND isfinite(volume_diff) AND isfinite(volume)
                            )
                        ), 0.0) END AS volume_diff_pos_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)
                    ) >= 3 THEN
                        SUM((volume_diff - means.vn_x) * (volume - means.vn_y)) FILTER (
                            WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)
                        ) / NULLIF(sqrt(
                            SUM(power(volume_diff - means.vn_x, 2)) FILTER (
                                WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)
                            ) * SUM(power(volume - means.vn_y, 2)) FILTER (
                                WHERE volume_diff < 0 AND isfinite(volume_diff) AND isfinite(volume)
                            )
                        ), 0.0) END AS volume_diff_neg_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE volume_diff > 0 AND next_volume_diff > 0
                          AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                    ) >= 3 THEN
                        SUM((volume_diff - means.vpp_x) * (next_volume_diff - means.vpp_y)) FILTER (
                            WHERE volume_diff > 0 AND next_volume_diff > 0
                              AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                        ) / NULLIF(sqrt(
                            SUM(power(volume_diff - means.vpp_x, 2)) FILTER (
                                WHERE volume_diff > 0 AND next_volume_diff > 0
                                  AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                            ) * SUM(power(next_volume_diff - means.vpp_y, 2)) FILTER (
                                WHERE volume_diff > 0 AND next_volume_diff > 0
                                  AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                            )
                        ), 0.0) END AS volume_diff_pos_pair_corr,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE volume_diff < 0 AND next_volume_diff < 0
                          AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                    ) >= 3 THEN
                        SUM((volume_diff - means.vnn_x) * (next_volume_diff - means.vnn_y)) FILTER (
                            WHERE volume_diff < 0 AND next_volume_diff < 0
                              AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                        ) / NULLIF(sqrt(
                            SUM(power(volume_diff - means.vnn_x, 2)) FILTER (
                                WHERE volume_diff < 0 AND next_volume_diff < 0
                                  AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                            ) * SUM(power(next_volume_diff - means.vnn_y, 2)) FILTER (
                                WHERE volume_diff < 0 AND next_volume_diff < 0
                                  AND isfinite(volume_diff) AND isfinite(next_volume_diff)
                            )
                        ), 0.0) END AS volume_diff_neg_pair_corr,
                    quantile_cont(minute_return, 0.90) FILTER (
                        WHERE isfinite(minute_return)
                    ) AS top_return_cutoff,
                    CASE
                        WHEN COUNT(*) FILTER (
                            WHERE minute_of_day <= 690 AND isfinite(minute_return)
                        ) > 0
                         AND COUNT(*) FILTER (
                            WHERE minute_of_day <= 690 AND isfinite(minute_return)
                              AND minute_return <= -1.0
                        ) = 0
                        THEN exp(SUM(ln(CASE WHEN minute_return > -1.0 THEN 1.0 + minute_return END))
                            FILTER (WHERE minute_of_day <= 690 AND isfinite(minute_return))) - 1.0
                    END AS morning_return,
                    CASE
                        WHEN COUNT(*) FILTER (
                            WHERE minute_of_day >= 781 AND isfinite(minute_return)
                        ) > 0
                         AND COUNT(*) FILTER (
                            WHERE minute_of_day >= 781 AND isfinite(minute_return)
                              AND minute_return <= -1.0
                        ) = 0
                        THEN exp(SUM(ln(CASE WHEN minute_return > -1.0 THEN 1.0 + minute_return END))
                            FILTER (WHERE minute_of_day >= 781 AND isfinite(minute_return))) - 1.0
                    END AS afternoon_return,
                    COUNT(*) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semibeta_count,
                    SUM(greatest(minute_return, 0.0) * greatest(market_mean_return, 0.0)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_pp_numerator,
                    SUM(-greatest(minute_return, 0.0) * least(market_mean_return, 0.0)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_np_numerator,
                    SUM(-least(minute_return, 0.0) * greatest(market_mean_return, 0.0)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_pn_numerator,
                    SUM(least(minute_return, 0.0) * least(market_mean_return, 0.0)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_nn_numerator,
                    SUM(power(greatest(market_mean_return, 0.0), 2)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_pos_denominator,
                    SUM(power(least(market_mean_return, 0.0), 2)) FILTER (
                        WHERE isfinite(minute_return) AND isfinite(market_mean_return)
                    ) AS semi_neg_denominator,
                    MIN(volume) FILTER (WHERE isfinite(volume)) AS volume_min,
                    MAX(volume) FILTER (WHERE isfinite(volume)) AS volume_max,
                    COUNT(*) FILTER (WHERE isfinite(volume)) AS valid_volume_count,
                    MAX(means.better_mean) AS better_mean,
                    sqrt(AVG(power(better_volatility - means.better_mean, 2)) FILTER (
                        WHERE isfinite(better_volatility) AND better_volatility >= 0
                          AND isfinite(minute_return)
                    )) AS better_std
                FROM minute_values
                JOIN correlation_means AS means USING (date)
                GROUP BY date
            ), fair_returns AS (
                SELECT
                    *,
                    CASE WHEN COUNT(
                        CASE WHEN day_open > 0 AND isfinite(day_close / day_open - 1.0)
                             THEN day_close / day_open - 1.0 END
                    ) OVER trailing_twenty = 20
                    THEN AVG(
                        CASE WHEN day_open > 0 AND isfinite(day_close / day_open - 1.0)
                             THEN day_close / day_open - 1.0 END
                    ) OVER trailing_twenty END AS fair_return
                FROM daily_stats
                WINDOW trailing_twenty AS (
                    ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                )
            ), volume_bins AS (
                SELECT
                    value.date,
                    CASE
                        WHEN stats.volume_min = stats.volume_max THEN 0
                        ELSE least(
                            stats.valid_volume_count - 1,
                            least(20, stats.valid_volume_count) - 1,
                            CAST(floor(
                                (value.volume - stats.volume_min)
                                / (stats.volume_max - stats.volume_min)
                                * least(20, stats.valid_volume_count)
                            ) AS BIGINT)
                        )
                    END AS bin_number
                FROM minute_values AS value
                JOIN daily_stats AS stats USING (date)
                WHERE isfinite(value.volume)
            ), volume_bin_counts AS (
                SELECT date, bin_number, COUNT(*) AS bin_size
                FROM volume_bins
                GROUP BY date, bin_number
            ), entropy AS (
                SELECT
                    counts.date,
                    CASE WHEN stats.volume_min = stats.volume_max THEN 0.0
                         ELSE -SUM(
                             counts.bin_size::DOUBLE / stats.valid_volume_count
                             * ln(counts.bin_size::DOUBLE / stats.valid_volume_count)
                         ) END AS minute_volume_bin_entropy_trial
                FROM volume_bin_counts AS counts
                JOIN daily_stats AS stats USING (date)
                GROUP BY counts.date, stats.volume_min, stats.volume_max, stats.valid_volume_count
            ), final_daily AS (
                SELECT
                    value.date,
                    MAX(stats.minute_count) AS minute_count,
                    MAX(stats.market_minute_count) AS market_minute_count,
                    MAX(stats.close_auction_volume_share_5m) AS close_auction_volume_share_5m,
                    MAX(stats.price_diff_pos_corr) AS price_diff_pos_corr,
                    MAX(stats.price_diff_neg_corr) AS price_diff_neg_corr,
                    MAX(stats.price_diff_pos_pair_corr) AS price_diff_pos_pair_corr,
                    MAX(stats.price_diff_neg_pair_corr) AS price_diff_neg_pair_corr,
                    MAX(stats.volume_diff_pos_corr) AS volume_diff_pos_corr,
                    MAX(stats.volume_diff_neg_corr) AS volume_diff_neg_corr,
                    MAX(stats.volume_diff_pos_pair_corr) AS volume_diff_pos_pair_corr,
                    MAX(stats.volume_diff_neg_pair_corr) AS volume_diff_neg_pair_corr,
                    MAX(entropy.minute_volume_bin_entropy_trial) AS minute_volume_bin_entropy_trial,
                    CASE
                        WHEN COUNT(*) FILTER (
                            WHERE isfinite(value.minute_return)
                              AND value.minute_return >= stats.top_return_cutoff
                        ) > 0
                         AND COUNT(*) FILTER (
                            WHERE isfinite(value.minute_return)
                              AND value.minute_return >= stats.top_return_cutoff
                              AND value.minute_return <= -1.0
                        ) = 0
                        THEN exp(SUM(ln(CASE WHEN value.minute_return > -1.0
                                            THEN 1.0 + value.minute_return END)) FILTER (
                            WHERE isfinite(value.minute_return)
                              AND value.minute_return >= stats.top_return_cutoff
                        )) - 1.0
                    END AS top10pct_minute_return,
                    MAX(stats.morning_return) AS morning_return,
                    MAX(stats.afternoon_return) AS afternoon_return,
                    CASE WHEN MAX(stats.semibeta_count) >= 3 AND MAX(stats.semi_pos_denominator) > 0
                         THEN MAX(stats.semi_pp_numerator) / MAX(stats.semi_pos_denominator) END
                        AS semibeta_market_pos_asset_pos,
                    CASE WHEN MAX(stats.semibeta_count) >= 3 AND MAX(stats.semi_neg_denominator) > 0
                         THEN MAX(stats.semi_np_numerator) / MAX(stats.semi_neg_denominator) END
                        AS semibeta_market_neg_asset_pos,
                    CASE WHEN MAX(stats.semibeta_count) >= 3 AND MAX(stats.semi_pos_denominator) > 0
                         THEN MAX(stats.semi_pn_numerator) / MAX(stats.semi_pos_denominator) END
                        AS semibeta_market_pos_asset_neg,
                    CASE WHEN MAX(stats.semibeta_count) >= 3 AND MAX(stats.semi_neg_denominator) > 0
                         THEN MAX(stats.semi_nn_numerator) / MAX(stats.semi_neg_denominator) END
                        AS semibeta_market_neg_asset_neg,
                    CASE WHEN COUNT(*) FILTER (
                        WHERE isfinite(value.better_volatility)
                          AND isfinite(value.minute_return)
                          AND value.better_volatility > 0
                          AND value.better_volatility >= stats.better_mean + stats.better_std
                    ) >= 2 THEN covar_pop(
                        value.minute_return / value.better_volatility,
                        value.better_volatility
                    ) FILTER (
                        WHERE isfinite(value.better_volatility)
                          AND isfinite(value.minute_return)
                          AND value.better_volatility > 0
                          AND value.better_volatility >= stats.better_mean + stats.better_std
                    ) END AS abnormal_high_vol_return_ratio_trial,
                    CASE WHEN MAX(stats.fair_return) IS NOT NULL
                               AND MAX(stats.day_close * stats.day_float_shares) > 0
                         THEN (
                             COALESCE(SUM(value.amount) FILTER (
                                 WHERE isfinite(value.amount) AND stats.day_open > 0
                                   AND value.close / stats.day_open - 1.0 > stats.fair_return
                             ), 0.0)
                             - COALESCE(SUM(value.amount) FILTER (
                                 WHERE isfinite(value.amount) AND stats.day_open > 0
                                   AND value.close / stats.day_open - 1.0 < stats.fair_return
                             ), 0.0)
                         ) / MAX(stats.day_close * stats.day_float_shares) END AS follow_wave_base
                FROM minute_values AS value
                JOIN fair_returns AS stats USING (date)
                LEFT JOIN entropy USING (date)
                GROUP BY value.date
            )
            SELECT
                CAST(date AS TIMESTAMP) AS date,
                CAST(? AS VARCHAR) AS code,
                minute_count,
                market_minute_count,
                close_auction_volume_share_5m,
                price_diff_pos_corr,
                price_diff_neg_corr,
                price_diff_pos_pair_corr,
                price_diff_neg_pair_corr,
                volume_diff_pos_corr,
                volume_diff_neg_corr,
                volume_diff_pos_pair_corr,
                volume_diff_neg_pair_corr,
                minute_volume_bin_entropy_trial,
                top10pct_minute_return,
                morning_return,
                afternoon_return,
                semibeta_market_pos_asset_pos,
                semibeta_market_neg_asset_pos,
                semibeta_market_pos_asset_neg,
                semibeta_market_neg_asset_neg,
                abnormal_high_vol_return_ratio_trial,
                follow_wave_base
            FROM final_daily
            ORDER BY date
        """
        result = connection.execute(
            query,
            [
                str(task.minute_path),
                task.start,
                end_exclusive.isoformat(),
                [str(path) for path in task.market_files],
                task.start,
                end_exclusive.isoformat(),
                task.code,
            ],
        ).fetchdf()
        result["date"] = pd.to_datetime(result["date"]).astype("datetime64[ns]")
        return result.loc[:, OUTPUT_COLUMNS]
    finally:
        connection.close()


def _correlation(left: np.ndarray, right: np.ndarray, mask: np.ndarray) -> float:
    valid = mask & np.isfinite(left) & np.isfinite(right)
    if valid.sum() < 3:
        return math.nan
    x, y = left[valid], right[valid]
    if np.std(x) <= 0 or np.std(y) <= 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def _compound(returns: np.ndarray) -> float:
    valid = returns[np.isfinite(returns)]
    if not len(valid):
        return math.nan
    if np.any(valid <= -1.0):
        return math.nan
    return float(np.expm1(np.log1p(valid).sum()))


def _simple_minute_returns(frame: pd.DataFrame) -> np.ndarray:
    close = frame["close"].to_numpy(dtype=np.float64)
    opening = frame["open"].to_numpy(dtype=np.float64)
    previous = np.r_[np.nan, close[:-1]]
    denominator = np.where(np.arange(len(close)) == 0, opening, previous)
    result = np.full(len(close), np.nan)
    np.divide(close, denominator, out=result, where=np.isfinite(denominator) & (denominator > 0))
    return result - 1.0


def _volume_entropy_trial(volume: np.ndarray, max_bins: int = 20) -> float:
    valid = volume[np.isfinite(volume)]
    if not len(valid):
        return math.nan
    if np.all(valid == valid[0]):
        return 0.0
    bin_count = min(max_bins, len(valid))
    counts, _ = np.histogram(valid, bins=bin_count, range=(valid.min(), valid.max()))
    probabilities = counts[counts > 0].astype(np.float64) / counts.sum()
    return float(-(probabilities * np.log(probabilities)).sum())


def _semibetas(asset: np.ndarray, market: np.ndarray) -> tuple[float, float, float, float]:
    valid = np.isfinite(asset) & np.isfinite(market)
    if valid.sum() < 3:
        return (math.nan,) * 4
    asset_value, market_value = asset[valid], market[valid]
    asset_pos, asset_neg = np.maximum(asset_value, 0.0), np.minimum(asset_value, 0.0)
    market_pos, market_neg = np.maximum(market_value, 0.0), np.minimum(market_value, 0.0)

    def ratio(numerator: float, denominator_values: np.ndarray) -> float:
        denominator = float(np.square(denominator_values).sum())
        return float(numerator / denominator) if denominator > 0 else math.nan

    return (
        ratio(float((asset_pos * market_pos).sum()), market_pos),
        ratio(float(-(asset_pos * market_neg).sum()), market_neg),
        ratio(float(-(asset_neg * market_pos).sum()), market_pos),
        ratio(float((asset_neg * market_neg).sum()), market_neg),
    )


def _abnormal_high_vol_trial(frame: pd.DataFrame, returns: np.ndarray) -> float:
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=np.float64)
    better = np.full(len(frame), np.nan)
    for index in range(4, len(frame)):
        block = prices[index - 4 : index + 1].reshape(-1)
        if np.isfinite(block).all() and abs(block.mean()) > 0:
            better[index] = float((block.std(ddof=0) / block.mean()) ** 2)
    valid = np.isfinite(better) & np.isfinite(returns) & (better > 0)
    if valid.sum() < 3:
        return math.nan
    threshold = better[valid].mean() + better[valid].std(ddof=0)
    selected = valid & (better >= threshold)
    if selected.sum() < 2:
        return math.nan
    ratio = returns[selected] / better[selected]
    volatility = better[selected]
    return float(np.mean((ratio - ratio.mean()) * (volatility - volatility.mean())))


def _daily_row(
    code: str,
    day: pd.Timestamp,
    frame: pd.DataFrame,
    fair_return: float,
) -> dict[str, object]:
    frame = frame.sort_values("datetime", kind="stable")
    close = frame["close"].to_numpy(dtype=np.float64)
    volume = frame["volume"].to_numpy(dtype=np.float64)
    amount = frame["amount"].to_numpy(dtype=np.float64)
    market = frame["market_mean_return"].to_numpy(dtype=np.float64)
    returns = _simple_minute_returns(frame)
    times = pd.DatetimeIndex(frame["datetime"])
    minute_of_day = times.hour.to_numpy() * 60 + times.minute.to_numpy()

    price_diff = np.diff(close)
    next_price = close[2:]
    first_price_diff = price_diff[:-1]
    next_price_diff = price_diff[1:]
    volume_diff = np.diff(volume)
    next_volume = volume[2:]
    first_volume_diff = volume_diff[:-1]
    next_volume_diff = volume_diff[1:]

    close_auction = (minute_of_day >= 14 * 60 + 56) & (minute_of_day <= 15 * 60)
    total_volume = np.nansum(volume)
    close_share = float(np.nansum(volume[close_auction]) / total_volume) if total_volume > 0 else math.nan

    valid_returns = returns[np.isfinite(returns)]
    if len(valid_returns):
        cutoff = float(np.quantile(valid_returns, 0.90))
        top_return = _compound(valid_returns[valid_returns >= cutoff])
    else:
        top_return = math.nan

    semi_pp, semi_np, semi_pn, semi_nn = _semibetas(returns, market)
    opening = float(frame["open"].iloc[0])
    closing = float(frame["close"].iloc[-1])
    shares = float(frame["float_shares"].iloc[-1])
    market_value = closing * shares
    relative_open = close / opening - 1.0 if opening > 0 else np.full(len(close), np.nan)
    if np.isfinite(fair_return) and market_value > 0:
        high_amount = np.nansum(amount[relative_open > fair_return])
        low_amount = np.nansum(amount[relative_open < fair_return])
        follow_wave = float((high_amount - low_amount) / market_value)
    else:
        follow_wave = math.nan

    return {
        "date": pd.Timestamp(day).normalize(),
        "code": code,
        "minute_count": int(len(frame)),
        "market_minute_count": int(np.isfinite(market).sum()),
        "close_auction_volume_share_5m": close_share,
        "price_diff_pos_corr": _correlation(first_price_diff, next_price, first_price_diff > 0),
        "price_diff_neg_corr": _correlation(first_price_diff, next_price, first_price_diff < 0),
        "price_diff_pos_pair_corr": _correlation(
            first_price_diff, next_price_diff, (first_price_diff > 0) & (next_price_diff > 0)
        ),
        "price_diff_neg_pair_corr": _correlation(
            first_price_diff, next_price_diff, (first_price_diff < 0) & (next_price_diff < 0)
        ),
        "volume_diff_pos_corr": _correlation(first_volume_diff, next_volume, first_volume_diff > 0),
        "volume_diff_neg_corr": _correlation(first_volume_diff, next_volume, first_volume_diff < 0),
        "volume_diff_pos_pair_corr": _correlation(
            first_volume_diff, next_volume_diff, (first_volume_diff > 0) & (next_volume_diff > 0)
        ),
        "volume_diff_neg_pair_corr": _correlation(
            first_volume_diff, next_volume_diff, (first_volume_diff < 0) & (next_volume_diff < 0)
        ),
        "minute_volume_bin_entropy_trial": _volume_entropy_trial(volume),
        "top10pct_minute_return": top_return,
        "morning_return": _compound(returns[minute_of_day <= 11 * 60 + 30]),
        "afternoon_return": _compound(returns[minute_of_day >= 13 * 60 + 1]),
        "semibeta_market_pos_asset_pos": semi_pp,
        "semibeta_market_neg_asset_pos": semi_np,
        "semibeta_market_pos_asset_neg": semi_pn,
        "semibeta_market_neg_asset_neg": semi_nn,
        "abnormal_high_vol_return_ratio_trial": _abnormal_high_vol_trial(frame, returns),
        "follow_wave_base": follow_wave,
    }


def compute_daily_features(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    """把一只股票的分钟记录汇总为每日特征。"""

    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = frame.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime", kind="stable")
    frame["date"] = frame["datetime"].dt.normalize()
    recent_intraday: deque[float] = deque(maxlen=20)
    rows: list[dict[str, object]] = []
    for day, daily in frame.groupby("date", sort=True):
        opening = float(daily.sort_values("datetime", kind="stable")["open"].iloc[0])
        closing = float(daily.sort_values("datetime", kind="stable")["close"].iloc[-1])
        intraday = closing / opening - 1.0 if opening > 0 else math.nan
        recent_intraday.append(intraday)
        fair = float(np.mean(recent_intraday)) if len(recent_intraday) == 20 and np.isfinite(recent_intraday).all() else math.nan
        rows.append(_daily_row(code, day, daily, fair))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _metadata(start: str, end: str) -> dict[bytes, bytes]:
    return {
        b"feature_version": FEATURE_VERSION.encode(),
        b"requested_start": start.encode(),
        b"requested_end": end.encode(),
        b"feature_notes_json": json.dumps(FEATURE_NOTES, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    }


def output_is_current(path: Path, start: str, end: str) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        parquet = pq.ParquetFile(path)
        metadata = parquet.schema_arrow.metadata or {}
        current = (
            metadata.get(b"feature_version", b"").decode() == FEATURE_VERSION
            and metadata.get(b"requested_start", b"").decode() == start
            and metadata.get(b"requested_end", b"").decode() == end
            and parquet.schema_arrow.names == OUTPUT_COLUMNS
        )
        parquet.close()
        return current
    except (OSError, ValueError, pa.ArrowException):
        return False


def atomic_write_parquet(frame: pd.DataFrame, path: Path, start: str, end: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame.loc[:, OUTPUT_COLUMNS], preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata.update(_metadata(start, end))
    table = table.replace_schema_metadata(metadata)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp.parquet")
    try:
        pq.write_table(table, temporary, compression="zstd")
        check = pq.ParquetFile(temporary)
        valid = check.schema_arrow.names == OUTPUT_COLUMNS and check.metadata.num_rows == len(frame)
        check.close()
        if not valid:
            raise ValueError(f"临时 Parquet 检查失败：{temporary}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_one_stock(task: BuildTask) -> dict[str, object]:
    output = task.output_dir / f"{task.code}.parquet"
    if output_is_current(output, task.start, task.end):
        parquet = pq.ParquetFile(output)
        rows = parquet.metadata.num_rows
        parquet.close()
        return {"code": task.code, "status": "skipped", "rows": rows}
    daily = _read_stock_daily(task)
    atomic_write_parquet(daily, output, task.start, task.end)
    return {"code": task.code, "status": "written", "rows": len(daily), "output": str(output)}


def build_tasks(args: argparse.Namespace) -> list[BuildTask]:
    minute_dir = Path(args.minute_dir)
    codes = parse_codes(args.codes, minute_dir)
    market_files = market_files_for_range(Path(args.market_dir), args.start, args.end)
    tasks = []
    for code in codes:
        minute_path = minute_dir / f"{code}.parquet"
        if not minute_path.is_file():
            raise FileNotFoundError(f"没有找到分钟文件：{minute_path}")
        tasks.append(
            BuildTask(
                code=code,
                minute_path=minute_path,
                market_files=market_files,
                start=args.start,
                end=args.end,
                output_dir=Path(args.output_dir),
                memory_limit=args.memory_limit,
            )
        )
    return tasks


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补算历史因子缺少的每日分钟特征")
    parser.add_argument(
        "--codes",
        help="逗号分隔代码、每行一个代码的文本文件，或以股票代码命名Parquet的目录；不填时处理全部股票",
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--memory-limit", default="2GB")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--minute-dir", type=Path, default=DEFAULT_MINUTE_DIR)
    parser.add_argument("--market-dir", type=Path, default=DEFAULT_MARKET_DIR)
    args = parser.parse_args(argv)
    if _parse_date(args.start) > _parse_date(args.end):
        parser.error("--start 不能晚于 --end")
    if args.workers < 1:
        parser.error("--workers 必须至少为 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tasks = build_tasks(args)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    if args.workers == 1:
        for index, task in enumerate(tasks, 1):
            result = build_one_stock(task)
            results.append(result)
            print(f"[{index}/{len(tasks)}] {result}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(build_one_stock, task): task.code for task in tasks}
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                print(f"[{index}/{len(tasks)}] {result}", flush=True)
    written = sum(result["status"] == "written" for result in results)
    skipped = sum(result["status"] == "skipped" for result in results)
    print(f"完成：写入 {written}，跳过 {skipped}，共 {len(results)} 只。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
