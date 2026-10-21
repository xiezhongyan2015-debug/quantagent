# -*- coding: utf-8 -*-
"""复用 v2 日频结果，只补算 v3 新增字段，并按股票保存完整 v3 文件。"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Sequence

import duckdb
import pandas as pd
import psutil

import duckdb_factor_store as factor_store
from build_minute_daily_extra import BASE_DIR, _sql_path, build_query
from build_smart_money_numpy import compute_smart_money_numpy


DEFAULT_V2_DIR = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_by_stock_v2"
)
DEFAULT_V2_MANIFEST = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_manifest_v2.json"
)
DEFAULT_CACHE_DIR = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_by_stock_v3_incremental"
)
DEFAULT_MANIFEST = (
    BASE_DIR / "duckdb_factor_store" / "minute_daily_extra_incremental_manifest_v3.json"
)

CACHE_VERSION = "3-incremental-1"

V2_COLUMNS = [
    "code",
    "date",
    "minute_count",
    "absret_lag_amount_corr",
    "lag_absret_amount_corr",
    "morning_afternoon_volume_ratio",
    "amplitude_split_daily",
    "top1pct_minute_return",
    "normal_active_share",
    "uniform_active_share",
    "amount_cv_1m",
    "volume_std_1m",
    "dazzling_return_base",
    "time_avg_relative_price",
    "intraday_trend_ratio_5m",
    "realized_bipower_var_5m",
    "realized_tripower_var_5m",
    "intraday_max_drawdown_5m",
    "realized_jump_volatility_5m",
    "up_jump_volatility_5m",
    "volume_share_skew_10m",
    "volume_share_std_60m",
]

HISTORY_COLUMNS = [
    "adj_lag_absret_amount_corr_daily",
    "fuzzy_amount_corr_daily",
    "volume_peak_return_daily",
    "valley_relative_vwap_daily",
    "ridge_relative_vwap_daily",
    "ridge_minute_count_daily",
    "current_day_volume_peak_count",
    "peak_gap_count",
    "peak_gap_sum1",
    "peak_gap_sum2",
    "peak_gap_sum3",
    "peak_gap_sum4",
    "valley_price_percentile_daily",
]

EARLY_COLUMNS = [
    "dazzling_volatility_daily",
    "high_volume_realized_skew_daily",
    "p_shaped_volume_distribution_daily",
    "top_return_time_1",
    "top_return_time_2",
    "top_return_time_3",
    "top_return_time_4",
    "top_return_time_5",
]

TIDE_COLUMNS = ["volume_tide_price_rate_daily"]
SMART_COLUMNS = ["smart_money_vwap_ratio_10d"]
NEW_COLUMNS = HISTORY_COLUMNS + EARLY_COLUMNS + TIDE_COLUMNS + SMART_COLUMNS

EXPECTED_COLUMNS = [
    "code",
    "date",
    "minute_count",
    "absret_lag_amount_corr",
    "lag_absret_amount_corr",
    "adj_lag_absret_amount_corr_daily",
    "fuzzy_amount_corr_daily",
    "morning_afternoon_volume_ratio",
    "amplitude_split_daily",
    "top1pct_minute_return",
    "normal_active_share",
    "uniform_active_share",
    "amount_cv_1m",
    "volume_std_1m",
    "dazzling_return_base",
    "dazzling_volatility_daily",
    "time_avg_relative_price",
    "volume_peak_return_daily",
    "valley_relative_vwap_daily",
    "ridge_relative_vwap_daily",
    "ridge_minute_count_daily",
    "current_day_volume_peak_count",
    "smart_money_vwap_ratio_10d",
    "volume_tide_price_rate_daily",
    "peak_gap_count",
    "peak_gap_sum1",
    "peak_gap_sum2",
    "peak_gap_sum3",
    "peak_gap_sum4",
    "valley_price_percentile_daily",
    "high_volume_realized_skew_daily",
    "p_shaped_volume_distribution_daily",
    "top_return_time_1",
    "top_return_time_2",
    "top_return_time_3",
    "top_return_time_4",
    "top_return_time_5",
    "intraday_trend_ratio_5m",
    "realized_bipower_var_5m",
    "realized_tripower_var_5m",
    "intraday_max_drawdown_5m",
    "realized_jump_volatility_5m",
    "up_jump_volatility_5m",
    "volume_share_skew_10m",
    "volume_share_std_60m",
]

_WORKER_CONNECTION: duckdb.DuckDBPyConnection | None = None
_WORKER_TEMP_DIR: Path | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-dir", default=str(DEFAULT_V2_DIR))
    parser.add_argument("--v2-manifest", default=str(DEFAULT_V2_MANIFEST))
    parser.add_argument(
        "--seed-dir",
        help="可选：复用另一个 v3 目录里已经验证完整的文件，不复制文件内容。",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-05-29")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-memory-limit", default="1200MB")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--code",
        action="append",
        dest="codes",
        help="只处理指定股票，可重复填写，例如 --code sh600000。",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出和临时文件必须位于当前工作区内。")
    return resolved


def _date_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_time = pd.Timestamp(start)
    end_time = pd.Timestamp(end)
    if start_time > end_time:
        raise ValueError("开始日期不能晚于结束日期。")
    return start_time, end_time + pd.Timedelta(days=1)


def _source_prefix(source_path: str, start: str, end: str) -> str:
    start_time, end_exclusive = _date_bounds(start, end)
    source = _sql_path(source_path)
    return f"""
        FROM read_parquet(
            '{source}', union_by_name = true, filename = true
        )
        WHERE datetime >= TIMESTAMP '{start_time:%Y-%m-%d 00:00:00}'
          AND datetime < TIMESTAMP '{end_exclusive:%Y-%m-%d 00:00:00}'
    """


def build_history_query(source_path: str, start: str, end: str) -> str:
    source_sql = _source_prefix(source_path, start, end)
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                regexp_extract(filename, '([a-z]{{2}}[0-9]{{6}})\\.parquet$', 1)
                    AS code,
                CAST(datetime AS DATE) AS date,
                datetime,
                row_number() OVER (
                    PARTITION BY filename, CAST(datetime AS DATE)
                    ORDER BY datetime
                ) - 1 AS minute_index,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume,
                CAST(amount AS DOUBLE) AS amount,
                CAST(qfq_ratio AS DOUBLE) AS qfq_ratio
            {source_sql}
        ),
        minute0 AS MATERIALIZED (
            SELECT
                *,
                ln(close / coalesce(lag(close) OVER day_window, open)) AS ret1m
            FROM base
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY datetime
            )
        ),
        minute1 AS MATERIALIZED (
            SELECT
                *,
                lag(ret1m) OVER day_window AS ret1m_lag1,
                count(volume) OVER same_minute_history AS volume_history_count,
                avg(volume) OVER same_minute_history AS volume_history_mean,
                stddev_pop(volume) OVER same_minute_history AS volume_history_std,
                count(amount) OVER same_minute_history AS amount_history_count,
                avg(amount) OVER same_minute_history AS amount_history_mean,
                stddev_pop(amount) OVER same_minute_history AS amount_history_std,
                count(ret1m) OVER trailing_five AS volatility_count,
                stddev_samp(ret1m) OVER trailing_five AS volatility_5m_raw,
                avg(volume) OVER day_partition AS day_volume_mean,
                stddev_pop(volume) OVER day_partition AS day_volume_std,
                max(minute_index) OVER day_partition AS max_minute_index
            FROM minute0
            WINDOW
                day_window AS (PARTITION BY code, date ORDER BY datetime),
                day_partition AS (PARTITION BY code, date),
                same_minute_history AS (
                    PARTITION BY code, minute_index ORDER BY date
                    ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                ),
                trailing_five AS (
                    PARTITION BY code, date ORDER BY minute_index
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                )
        ),
        minute2 AS MATERIALIZED (
            SELECT
                *,
                volume_history_count = 20 AS volume_history_ready,
                volume_history_count = 20
                    AND volume > volume_history_mean + volume_history_std
                    AS volume_erupt,
                volume > day_volume_mean + day_volume_std AS daily_volume_erupt,
                CASE
                    WHEN amount_history_count = 20 AND amount_history_std > 0
                    THEN (amount - amount_history_mean) / amount_history_std
                END AS adjusted_amount,
                CASE WHEN volatility_count = 5 THEN volatility_5m_raw END
                    AS volatility_5m
            FROM minute1
        ),
        minute3 AS MATERIALIZED (
            SELECT
                *,
                lag(volume_erupt) OVER day_window AS previous_volume_erupt,
                lead(volume_erupt) OVER day_window AS next_volume_erupt,
                max(minute_index) FILTER (WHERE daily_volume_erupt) OVER (
                    PARTITION BY code, date ORDER BY minute_index
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS previous_daily_erupt_index,
                count(volatility_5m) OVER trailing_five AS fuzzy_count,
                stddev_samp(volatility_5m) OVER trailing_five AS fuzzy_raw
            FROM minute2
            WINDOW
                day_window AS (PARTITION BY code, date ORDER BY minute_index),
                trailing_five AS (
                    PARTITION BY code, date ORDER BY minute_index
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                )
        ),
        minute4 AS MATERIALIZED (
            SELECT
                *,
                volume_history_ready
                    AND volume_erupt
                    AND NOT coalesce(previous_volume_erupt, false)
                    AND NOT coalesce(next_volume_erupt, false)
                    AS is_volume_peak,
                volume_history_ready
                    AND volume_erupt
                    AND (
                        coalesce(previous_volume_erupt, false)
                        OR coalesce(next_volume_erupt, false)
                    ) AS is_volume_ridge,
                volume_history_ready AND NOT volume_erupt AS is_volume_valley,
                CASE WHEN fuzzy_count = 5 THEN fuzzy_raw END AS fuzzy_ambiguity
            FROM minute3
        ),
        peak_positions AS (
            SELECT
                code,
                date,
                minute_index - lag(minute_index) OVER (
                    PARTITION BY code, date ORDER BY minute_index
                ) AS peak_gap
            FROM minute4
            WHERE is_volume_peak
        ),
        peak_gap_daily AS (
            SELECT
                code,
                date,
                count(peak_gap) AS peak_gap_count,
                sum(peak_gap) AS peak_gap_sum1,
                sum(pow(peak_gap, 2)) AS peak_gap_sum2,
                sum(pow(peak_gap, 3)) AS peak_gap_sum3,
                sum(pow(peak_gap, 4)) AS peak_gap_sum4
            FROM peak_positions
            GROUP BY code, date
        ),
        daily_raw AS (
            SELECT
                code,
                date,
                corr(abs(ret1m_lag1), adjusted_amount) FILTER (
                    WHERE ret1m_lag1 <> 0
                ) AS adj_lag_absret_amount_corr_daily,
                corr(fuzzy_ambiguity, amount) FILTER (
                    WHERE minute_index > 0 AND minute_index < max_minute_index
                ) AS fuzzy_amount_corr_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN coalesce(sum(ret1m) FILTER (WHERE is_volume_peak), 0.0)
                END AS volume_peak_return_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN (
                        sum(amount * qfq_ratio) FILTER (WHERE is_volume_valley)
                        / nullif(sum(volume) FILTER (WHERE is_volume_valley), 0)
                    ) / nullif(
                        sum(amount * qfq_ratio) / nullif(sum(volume), 0), 0
                    )
                END AS valley_relative_vwap_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN (
                        sum(amount * qfq_ratio) FILTER (WHERE is_volume_ridge)
                        / nullif(sum(volume) FILTER (WHERE is_volume_ridge), 0)
                    ) / nullif(
                        sum(amount * qfq_ratio) / nullif(sum(volume), 0), 0
                    )
                END AS ridge_relative_vwap_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN count(*) FILTER (WHERE is_volume_ridge)
                END AS ridge_minute_count_daily,
                count(*) FILTER (
                    WHERE daily_volume_erupt
                      AND (
                        previous_daily_erupt_index IS NULL
                        OR minute_index - previous_daily_erupt_index > 1
                      )
                ) AS current_day_volume_peak_count,
                bool_or(volume_history_ready) AS volume_history_ready_daily,
                max(high) AS day_high,
                min(low) AS day_low,
                arg_max(close, datetime) AS day_close,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN sum(amount * qfq_ratio) FILTER (WHERE is_volume_valley)
                        / nullif(sum(volume) FILTER (WHERE is_volume_valley), 0)
                END AS valley_vwap_daily
            FROM minute4
            WHERE code <> ''
            GROUP BY code, date
        ),
        daily_with_previous AS (
            SELECT
                *,
                lag(day_close) OVER (PARTITION BY code ORDER BY date)
                    AS previous_close
            FROM daily_raw
        )
        SELECT
            raw.code,
            raw.date,
            raw.adj_lag_absret_amount_corr_daily,
            raw.fuzzy_amount_corr_daily,
            raw.volume_peak_return_daily,
            raw.valley_relative_vwap_daily,
            raw.ridge_relative_vwap_daily,
            raw.ridge_minute_count_daily,
            raw.current_day_volume_peak_count,
            CASE WHEN raw.volume_history_ready_daily
                THEN coalesce(gaps.peak_gap_count, 0)
            END AS peak_gap_count,
            CASE WHEN raw.volume_history_ready_daily
                THEN coalesce(gaps.peak_gap_sum1, 0)
            END AS peak_gap_sum1,
            CASE WHEN raw.volume_history_ready_daily
                THEN coalesce(gaps.peak_gap_sum2, 0)
            END AS peak_gap_sum2,
            CASE WHEN raw.volume_history_ready_daily
                THEN coalesce(gaps.peak_gap_sum3, 0)
            END AS peak_gap_sum3,
            CASE WHEN raw.volume_history_ready_daily
                THEN coalesce(gaps.peak_gap_sum4, 0)
            END AS peak_gap_sum4,
            (raw.valley_vwap_daily - least(raw.day_low, raw.previous_close))
                / nullif(
                    greatest(raw.day_high, raw.previous_close)
                        - least(raw.day_low, raw.previous_close),
                    0
                ) AS valley_price_percentile_daily
        FROM daily_with_previous AS raw
        LEFT JOIN peak_gap_daily AS gaps USING (code, date)
        ORDER BY raw.date, raw.code
    """


def build_early_query(source_path: str, start: str, end: str) -> str:
    source_sql = _source_prefix(source_path, start, end)
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                regexp_extract(filename, '([a-z]{{2}}[0-9]{{6}})\\.parquet$', 1)
                    AS code,
                CAST(datetime AS DATE) AS date,
                datetime,
                row_number() OVER (
                    PARTITION BY filename, CAST(datetime AS DATE)
                    ORDER BY datetime
                ) - 1 AS minute_index,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume
            {source_sql}
        ),
        minute0 AS MATERIALIZED (
            SELECT
                *,
                close / coalesce(lag(close) OVER day_window, open) - 1.0
                    AS simple_ret1m,
                volume - lag(volume) OVER day_window AS volume_diff
            FROM base
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY datetime
            )
        ),
        minute1 AS MATERIALIZED (
            SELECT
                *,
                avg(volume_diff) OVER day_partition AS volume_diff_mean,
                stddev_samp(volume_diff) OVER day_partition AS volume_diff_std,
                count(*) OVER day_partition AS day_minute_count,
                row_number() OVER (
                    PARTITION BY code, date ORDER BY volume DESC, datetime
                ) AS volume_desc_rank,
                count(simple_ret1m) OVER forward_five AS forward_return_count,
                stddev_samp(simple_ret1m) OVER forward_five
                    AS forward_return_std
            FROM minute0
            WINDOW
                day_partition AS (PARTITION BY code, date),
                forward_five AS (
                    PARTITION BY code, date ORDER BY minute_index
                    ROWS BETWEEN CURRENT ROW AND 4 FOLLOWING
                )
        ),
        dazzling_daily AS (
            SELECT
                code,
                date,
                avg(forward_return_std) FILTER (
                    WHERE volume_diff > volume_diff_mean + volume_diff_std
                      AND forward_return_count = 5
                ) AS dazzling_volatility_daily
            FROM minute1
            WHERE code <> ''
            GROUP BY code, date
        ),
        high_volume_selected AS MATERIALIZED (
            SELECT
                code,
                date,
                simple_ret1m,
                count(simple_ret1m) OVER selected_day AS selected_count,
                avg(simple_ret1m) OVER selected_day AS selected_mean
            FROM minute1
            WHERE volume_desc_rank <= ceil(day_minute_count / 3.0)
              AND simple_ret1m IS NOT NULL
            WINDOW selected_day AS (PARTITION BY code, date)
        ),
        high_volume_moments AS (
            SELECT
                code,
                date,
                max(selected_count) AS selected_count,
                sum(pow(simple_ret1m - selected_mean, 2)) AS centered_sum2,
                sum(pow(simple_ret1m - selected_mean, 3)) AS centered_sum3
            FROM high_volume_selected
            GROUP BY code, date
        ),
        high_volume_daily AS (
            SELECT
                code,
                date,
                CASE
                    WHEN selected_count > 2 AND centered_sum2 > 0
                    THEN centered_sum3
                        / pow(centered_sum2 / (selected_count - 2), 1.5)
                        / (selected_count - 1)
                END AS high_volume_realized_skew_daily
            FROM high_volume_moments
        ),
        price_volume AS MATERIALIZED (
            SELECT code, date, close AS price, sum(volume) AS price_volume
            FROM minute1
            WHERE close IS NOT NULL AND volume > 0
            GROUP BY code, date, close
        ),
        support_ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY code, date ORDER BY price_volume DESC, price
                ) AS support_rank,
                sum(price_volume) OVER (PARTITION BY code, date) AS total_day_volume
            FROM price_volume
        ),
        price_ordered AS (
            SELECT
                values_at_price.*,
                support.price AS support_price,
                support.total_day_volume,
                sum(values_at_price.price_volume) OVER (
                    PARTITION BY values_at_price.code, values_at_price.date
                    ORDER BY
                        abs(values_at_price.price - support.price),
                        values_at_price.price_volume DESC,
                        values_at_price.price
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_volume
            FROM price_volume AS values_at_price
            JOIN support_ranked AS support
              ON support.code = values_at_price.code
             AND support.date = values_at_price.date
             AND support.support_rank = 1
        ),
        p_shaped_daily AS (
            SELECT
                ordered.code,
                ordered.date,
                max(day_price.day_high)
                    - min(ordered.price) FILTER (
                        WHERE ordered.cumulative_volume - ordered.price_volume
                            <= 0.50 * ordered.total_day_volume
                    ) AS p_shaped_volume_distribution_daily
            FROM price_ordered AS ordered
            JOIN (
                SELECT code, date, max(high) AS day_high
                FROM minute1
                GROUP BY code, date
            ) AS day_price USING (code, date)
            GROUP BY ordered.code, ordered.date
        ),
        top_return_times_raw AS (
            SELECT
                code,
                date,
                list(
                    extract(hour FROM datetime) * 60
                        + extract(minute FROM datetime)
                    ORDER BY simple_ret1m DESC, datetime
                ) FILTER (WHERE simple_ret1m IS NOT NULL) AS top_times
            FROM minute1
            GROUP BY code, date
        ),
        top_return_times_daily AS (
            SELECT
                code,
                date,
                list_extract(top_times, 1) AS top_return_time_1,
                list_extract(top_times, 2) AS top_return_time_2,
                list_extract(top_times, 3) AS top_return_time_3,
                list_extract(top_times, 4) AS top_return_time_4,
                list_extract(top_times, 5) AS top_return_time_5
            FROM top_return_times_raw
        )
        SELECT
            dazzling.code,
            dazzling.date,
            dazzling.dazzling_volatility_daily,
            high_volume.high_volume_realized_skew_daily,
            p_shaped.p_shaped_volume_distribution_daily,
            top_times.top_return_time_1,
            top_times.top_return_time_2,
            top_times.top_return_time_3,
            top_times.top_return_time_4,
            top_times.top_return_time_5
        FROM dazzling_daily AS dazzling
        LEFT JOIN high_volume_daily AS high_volume USING (code, date)
        LEFT JOIN p_shaped_daily AS p_shaped USING (code, date)
        LEFT JOIN top_return_times_daily AS top_times USING (code, date)
        ORDER BY dazzling.date, dazzling.code
    """


def build_tide_query(source_path: str, start: str, end: str) -> str:
    source_sql = _source_prefix(source_path, start, end)
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                regexp_extract(filename, '([a-z]{{2}}[0-9]{{6}})\\.parquet$', 1)
                    AS code,
                CAST(datetime AS DATE) AS date,
                row_number() OVER (
                    PARTITION BY filename, CAST(datetime AS DATE)
                    ORDER BY datetime
                ) - 1 AS minute_index,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume
            {source_sql}
        ),
        smooth AS MATERIALIZED (
            SELECT
                *,
                count(volume) OVER centered_nine AS regional_volume_count,
                sum(volume) OVER centered_nine AS regional_volume
            FROM base
            WINDOW centered_nine AS (
                PARTITION BY code, date ORDER BY minute_index
                ROWS BETWEEN 4 PRECEDING AND 4 FOLLOWING
            )
        ),
        peak AS (
            SELECT
                code,
                date,
                arg_max(minute_index, regional_volume) FILTER (
                    WHERE regional_volume_count = 9
                      AND minute_index BETWEEN 5 AND 231
                ) AS peak_minute
            FROM smooth
            GROUP BY code, date
        ),
        points AS (
            SELECT
                peak.code,
                peak.date,
                arg_min(smooth.minute_index, smooth.regional_volume) FILTER (
                    WHERE smooth.minute_index BETWEEN 4 AND peak.peak_minute - 1
                ) AS surge_minute,
                arg_min(smooth.close, smooth.regional_volume) FILTER (
                    WHERE smooth.minute_index BETWEEN 4 AND peak.peak_minute - 1
                ) AS surge_close,
                arg_min(smooth.minute_index, smooth.regional_volume) FILTER (
                    WHERE smooth.minute_index BETWEEN peak.peak_minute + 1 AND 232
                ) AS ebb_minute,
                arg_min(smooth.close, smooth.regional_volume) FILTER (
                    WHERE smooth.minute_index BETWEEN peak.peak_minute + 1 AND 232
                ) AS ebb_close
            FROM peak
            JOIN smooth USING (code, date)
            GROUP BY peak.code, peak.date, peak.peak_minute
        )
        SELECT
            code,
            date,
            (ebb_close - surge_close)
                / nullif(surge_close, 0)
                / nullif(ebb_minute - surge_minute, 0)
                AS volume_tide_price_rate_daily
        FROM points
        ORDER BY date, code
    """


def build_smart_query(source_path: str, start: str, end: str) -> str:
    source_sql = _source_prefix(source_path, start, end)
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                regexp_extract(filename, '([a-z]{{2}}[0-9]{{6}})\\.parquet$', 1)
                    AS code,
                CAST(datetime AS DATE) AS date,
                datetime,
                dense_rank() OVER (
                    PARTITION BY filename ORDER BY CAST(datetime AS DATE)
                ) - 1 AS day_index,
                CAST(open AS DOUBLE) AS open,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume,
                CAST(amount AS DOUBLE) AS amount,
                CAST(qfq_ratio AS DOUBLE) AS qfq_ratio
            {source_sql}
        ),
        minute0 AS MATERIALIZED (
            SELECT
                *,
                ln(close / coalesce(lag(close) OVER day_window, open)) AS ret1m
            FROM base
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY datetime
            )
        ),
        smart_inputs AS MATERIALIZED (
            SELECT
                target.code,
                target.date,
                history.datetime,
                history.volume,
                history.amount,
                history.qfq_ratio,
                abs(history.ret1m) / pow(history.volume, 0.25) AS smart_score
            FROM (
                SELECT DISTINCT code, date, day_index
                FROM minute0
                WHERE code <> ''
            ) AS target
            JOIN minute0 AS history
              ON history.code = target.code
             AND history.day_index BETWEEN target.day_index - 9 AND target.day_index
            WHERE target.day_index >= 9
              AND history.volume > 0
              AND history.amount >= 0
        ),
        smart_ranked AS (
            SELECT
                *,
                sum(volume) OVER target_day AS total_volume,
                sum(volume) OVER (
                    PARTITION BY code, date
                    ORDER BY smart_score DESC, datetime
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_volume
            FROM smart_inputs
            WINDOW target_day AS (PARTITION BY code, date)
        )
        SELECT
            code,
            date,
            (
                sum(amount * qfq_ratio) FILTER (
                    WHERE cumulative_volume - volume < 0.20 * total_volume
                )
                / nullif(
                    sum(volume) FILTER (
                        WHERE cumulative_volume - volume < 0.20 * total_volume
                    ),
                    0
                )
            ) / nullif(
                sum(amount * qfq_ratio) / nullif(sum(volume), 0),
                0
            ) AS smart_money_vwap_ratio_10d
        FROM smart_ranked
        GROUP BY code, date
        ORDER BY date, code
    """


def _quoted(columns: Sequence[str], alias: str | None = None) -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f'{prefix}"{column}"' for column in columns)


def _meta_path(output: Path) -> Path:
    return output.with_suffix(".meta.json")


def _fingerprint(path: Path) -> dict[str, object]:
    info = path.stat()
    return {
        "path": str(path.resolve()),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _schema(connection: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
    ).fetchall()
    return [str(row[0]) for row in rows]


def _raw_day_summary(
    connection: duckdb.DuckDBPyConnection,
    source: Path,
    start: str,
    end: str,
) -> tuple[int, object, object]:
    row = connection.execute(
        """
        SELECT
            count(DISTINCT CAST(datetime AS DATE)),
            min(CAST(datetime AS DATE)),
            max(CAST(datetime AS DATE))
        FROM read_parquet(?)
        WHERE datetime >= CAST(? AS TIMESTAMP)
          AND datetime < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY
        """,
        [str(source), pd.Timestamp(start), pd.Timestamp(end)],
    ).fetchone()
    return int(row[0]), row[1], row[2]


def _output_summary(
    connection: duckdb.DuckDBPyConnection, path: Path
) -> tuple[int, int, object, object, str | None]:
    row = connection.execute(
        """
        SELECT
            count(*),
            count(DISTINCT (code, date)),
            min(date),
            max(date),
            CASE WHEN count(DISTINCT code) = 1 THEN min(code) END
        FROM read_parquet(?)
        """,
        [str(path)],
    ).fetchone()
    return int(row[0]), int(row[1]), row[2], row[3], row[4]


def _cache_meta(
    output: Path,
    source: Path,
    v2_path: Path,
    start: str,
    end: str,
    summary: tuple[int, int, object, object, str | None],
) -> dict[str, object]:
    return {
        "cache_version": CACHE_VERSION,
        "code": source.stem.lower(),
        "start": start,
        "end": end,
        "source": _fingerprint(source),
        "v2": _fingerprint(v2_path),
        "columns": EXPECTED_COLUMNS,
        "rows": int(summary[0]),
        "first_date": str(summary[2]),
        "last_date": str(summary[3]),
        "output_size": output.stat().st_size,
    }


def _write_meta(output: Path, meta: dict[str, object]) -> None:
    meta_path = _meta_path(output)
    temp_meta = meta_path.with_suffix(".tmp.json")
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    temp_meta.replace(meta_path)


def _adopt_seed(
    connection: duckdb.DuckDBPyConnection,
    seed: Path,
    output: Path,
    source: Path,
    v2_path: Path,
    start: str,
    end: str,
) -> bool:
    """验证旧 v3 文件后建立硬链接；不改动旧文件。"""

    if not seed.is_file() or seed.name.endswith(".tmp.parquet"):
        return False
    try:
        if _schema(connection, seed) != EXPECTED_COLUMNS:
            return False
        raw = _raw_day_summary(connection, source, start, end)
        saved = _output_summary(connection, seed)
        if (
            saved[0] != saved[1]
            or saved[0] != raw[0]
            or saved[2] != raw[1]
            or saved[3] != raw[2]
            or str(saved[4]).lower() != source.stem.lower()
        ):
            return False
        temp_link = output.with_name(f"{output.stem}.seed.tmp.parquet")
        temp_link.unlink(missing_ok=True)
        os.link(seed, temp_link)
        temp_link.replace(output)
        _write_meta(
            output,
            _cache_meta(output, source, v2_path, start, end, saved),
        )
        return True
    except (OSError, ValueError, TypeError, duckdb.Error):
        return False


def _is_complete(
    connection: duckdb.DuckDBPyConnection,
    output: Path,
    source: Path,
    v2_path: Path,
    start: str,
    end: str,
) -> bool:
    meta_path = _meta_path(output)
    if not output.is_file() or output.stat().st_size <= 0 or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
        if meta.get("cache_version") != CACHE_VERSION:
            return False
        if meta.get("start") != start or meta.get("end") != end:
            return False
        if meta.get("source") != _fingerprint(source):
            return False
        if meta.get("v2") != _fingerprint(v2_path):
            return False
        if meta.get("columns") != EXPECTED_COLUMNS:
            return False
        if meta.get("output_size") != output.stat().st_size:
            return False
        if _schema(connection, output) != EXPECTED_COLUMNS:
            return False
        summary = _output_summary(connection, output)
        return (
            int(summary[0]) == int(meta.get("rows", -1))
            and int(summary[1]) == int(summary[0])
            and str(summary[2]) == meta.get("first_date")
            and str(summary[3]) == meta.get("last_date")
        )
    except (OSError, ValueError, TypeError, duckdb.Error):
        return False


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION, _WORKER_TEMP_DIR
    _WORKER_TEMP_DIR = Path(temp_dir) / f"minute_incremental_worker_{os.getpid()}"
    _WORKER_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(
        f"SET temp_directory = '{_sql_path(_WORKER_TEMP_DIR)}'"
    )
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def _build_one(
    minute_path: str,
    v2_path: str,
    output_path: str,
    v2_coverage_end: str,
    start: str,
    end: str,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程没有初始化。")
    connection = _WORKER_CONNECTION
    source = Path(minute_path)
    v2 = Path(v2_path)
    output = Path(output_path)
    temp = output.with_name(f"{output.stem}.tmp.parquet")
    temp_meta = _meta_path(output).with_suffix(".tmp.json")
    temp.unlink(missing_ok=True)
    temp_meta.unlink(missing_ok=True)
    started = time.perf_counter()
    group_seconds: dict[str, float] = {}

    for table in ("inc_history", "inc_early", "inc_tide", "inc_smart", "inc_common"):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    group_started = time.perf_counter()
    connection.execute(
        f"CREATE TEMP TABLE inc_history AS ({build_history_query(str(source), start, end)})"
    )
    group_seconds["history"] = time.perf_counter() - group_started
    group_started = time.perf_counter()
    connection.execute(
        f"CREATE TEMP TABLE inc_early AS ({build_early_query(str(source), start, end)})"
    )
    group_seconds["early"] = time.perf_counter() - group_started
    group_started = time.perf_counter()
    connection.execute(
        f"CREATE TEMP TABLE inc_tide AS ({build_tide_query(str(source), start, end)})"
    )
    group_seconds["tide"] = time.perf_counter() - group_started
    group_started = time.perf_counter()
    smart_frame = compute_smart_money_numpy(source, start, end)
    connection.register("inc_smart_frame", smart_frame)
    try:
        connection.execute(
            """
            CREATE TEMP TABLE inc_smart AS
            SELECT
                CAST(code AS VARCHAR) AS code,
                CAST(date AS DATE) AS date,
                CAST(smart_money_vwap_ratio_10d AS DOUBLE)
                    AS smart_money_vwap_ratio_10d
            FROM inc_smart_frame
            """
        )
    finally:
        connection.unregister("inc_smart_frame")
        del smart_frame
        gc.collect()
    group_seconds["smart_numpy"] = time.perf_counter() - group_started

    v2_start = pd.Timestamp(start)
    v2_end = min(pd.Timestamp(end), pd.Timestamp(v2_coverage_end))
    if v2_start <= v2_end:
        connection.execute(
            f"""
            CREATE TEMP TABLE inc_common AS
            SELECT {_quoted(V2_COLUMNS)}
            FROM read_parquet(?)
            WHERE date BETWEEN ? AND ?
            """,
            [str(v2), v2_start, v2_end],
        )
    else:
        connection.execute(
            f"""
            CREATE TEMP TABLE inc_common AS
            SELECT {_quoted(V2_COLUMNS)}
            FROM read_parquet(?)
            WHERE false
            """,
            [str(v2)],
        )

    recent_start = max(pd.Timestamp(start), pd.Timestamp(v2_coverage_end) + pd.Timedelta(days=1))
    if recent_start <= pd.Timestamp(end):
        recent_query = build_query(str(source), str(recent_start.date()), end)
        connection.execute(
            f"""
            INSERT INTO inc_common
            SELECT {_quoted(V2_COLUMNS)}
            FROM ({recent_query})
            """
        )

    projection = []
    for column in EXPECTED_COLUMNS:
        if column in V2_COLUMNS:
            projection.append(f'common."{column}"')
        elif column in HISTORY_COLUMNS:
            projection.append(f'history."{column}"')
        elif column in EARLY_COLUMNS:
            projection.append(f'early."{column}"')
        elif column in TIDE_COLUMNS:
            projection.append(f'tide."{column}"')
        elif column in SMART_COLUMNS:
            projection.append(f'smart."{column}"')
        else:
            raise AssertionError(f"没有找到字段来源：{column}")
    connection.execute(
        f"""
        COPY (
            SELECT {', '.join(projection)}
            FROM inc_common AS common
            INNER JOIN inc_history AS history USING (code, date)
            LEFT JOIN inc_early AS early USING (code, date)
            LEFT JOIN inc_tide AS tide USING (code, date)
            LEFT JOIN inc_smart AS smart USING (code, date)
            ORDER BY common.date, common.code
        )
        TO '{_sql_path(temp)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )

    if _schema(connection, temp) != EXPECTED_COLUMNS:
        raise ValueError(f"{source.stem} 输出字段不完整。")
    raw_summary = _raw_day_summary(connection, source, start, end)
    output_summary = _output_summary(connection, temp)
    if int(output_summary[0]) != int(output_summary[1]):
        raise ValueError(f"{source.stem} 输出有重复日期。")
    if (
        int(output_summary[0]) != int(raw_summary[0])
        or output_summary[2] != raw_summary[1]
        or output_summary[3] != raw_summary[2]
    ):
        raise ValueError(
            f"{source.stem} 输出日期不完整：分钟数据 {raw_summary}，输出 {output_summary}。"
        )

    temp.replace(output)
    meta = _cache_meta(output, source, v2, start, end, output_summary)
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    temp_meta.replace(_meta_path(output))
    return {
        "code": source.stem.lower(),
        "rows": int(output_summary[0]),
        "seconds": time.perf_counter() - started,
        "size_bytes": output.stat().st_size,
        "status": "completed",
        "group_seconds": {
            key: round(value, 6) for key, value in group_seconds.items()
        },
    }


def _load_v2_manifest(path: Path) -> tuple[str, str]:
    manifest = json.loads(path.read_text("utf-8"))
    if int(manifest.get("cache_version", -1)) != 2:
        raise ValueError("v2 说明文件的版本不是 2。")
    if int(manifest.get("completed_outputs", -1)) <= 0 or manifest.get("failures"):
        raise ValueError("v2 说明文件显示结果没有完整生成。")
    return str(manifest["start"]), str(manifest["end"])


def _source_files(v2_dir: Path, codes: Sequence[str] | None) -> list[tuple[Path, Path]]:
    requested = {code.lower() for code in codes or []}
    pairs = []
    for v2_path in sorted(v2_dir.glob("*.parquet")):
        code = v2_path.stem.lower()
        if requested and code not in requested:
            continue
        minute_path = factor_store.DEFAULT_MINUTE_DIR / f"{code}.parquet"
        if not minute_path.is_file():
            raise FileNotFoundError(f"缺少分钟文件：{minute_path}")
        pairs.append((minute_path, v2_path))
    missing_codes = sorted(requested - {item[0].stem.lower() for item in pairs})
    if missing_codes:
        raise FileNotFoundError(f"v2 没有这些股票：{', '.join(missing_codes)}")
    return pairs


def _memory_sample() -> tuple[float, float]:
    parent = psutil.Process(os.getpid())
    child_mib = []
    for child in parent.children(recursive=True):
        try:
            if child.is_running():
                child_mib.append(child.memory_info().rss / 1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sum(child_mib), max(child_mib, default=0.0)


def _definition_hash() -> str:
    payload = "|".join(EXPECTED_COLUMNS + [CACHE_VERSION])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1:
        raise ValueError("workers 必须至少为 1。")
    v2_dir = _assert_workspace_path(Path(args.v2_dir))
    v2_manifest = _assert_workspace_path(Path(args.v2_manifest))
    seed_dir = _assert_workspace_path(Path(args.seed_dir)) if args.seed_dir else None
    cache_dir = _assert_workspace_path(Path(args.cache_dir))
    manifest_path = _assert_workspace_path(Path(args.manifest))
    temp_dir = _assert_workspace_path(Path(args.temp_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    v2_start, v2_end = _load_v2_manifest(v2_manifest)
    if pd.Timestamp(args.start) < pd.Timestamp(v2_start):
        raise ValueError(f"开始日期不能早于 v2 的 {v2_start}。")
    pairs = _source_files(v2_dir, args.codes)
    if args.limit:
        pairs = pairs[: args.limit]

    validation_connection = duckdb.connect()
    jobs = []
    skipped = 0
    adopted = 0
    try:
        for source, v2_path in pairs:
            output = cache_dir / f"{source.stem.lower()}.parquet"
            if not args.force and _is_complete(
                validation_connection,
                output,
                source,
                v2_path,
                args.start,
                args.end,
            ):
                skipped += 1
            elif (
                not args.force
                and seed_dir is not None
                and _adopt_seed(
                    validation_connection,
                    seed_dir / output.name,
                    output,
                    source,
                    v2_path,
                    args.start,
                    args.end,
                )
            ):
                adopted += 1
            else:
                jobs.append((source, v2_path, output))
    finally:
        validation_connection.close()

    started = time.perf_counter()
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    peak_total_child_mib = 0.0
    peak_single_child_mib = 0.0
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(str(temp_dir), args.worker_memory_limit),
    ) as executor:
        future_map = {
            executor.submit(
                _build_one,
                str(source),
                str(v2_path),
                str(output),
                v2_end,
                args.start,
                args.end,
            ): source.stem.lower()
            for source, v2_path, output in jobs
        }
        pending = set(future_map)
        completed = 0
        while pending:
            done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            total_mib, single_mib = _memory_sample()
            peak_total_child_mib = max(peak_total_child_mib, total_mib)
            peak_single_child_mib = max(peak_single_child_mib, single_mib)
            for future in done:
                code = future_map[future]
                completed += 1
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                print(
                    f"已处理 {completed}/{len(jobs)}；失败 {len(failures)}；"
                    f"单个进程内存最高 {peak_single_child_mib:.0f} MiB",
                    flush=True,
                )

    elapsed = time.perf_counter() - started
    completed_files = sorted(
        path
        for path in cache_dir.glob("*.parquet")
        if not path.name.endswith(".tmp.parquet")
    )
    manifest = {
        "cache_version": CACHE_VERSION,
        "definition_hash": _definition_hash(),
        "definition": "复用 v2 的 22 列，分四组补算 23 列，再合并为 45 列 v3。",
        "columns": EXPECTED_COLUMNS,
        "cache_dir": str(cache_dir),
        "start": args.start,
        "end": args.end,
        "requested_files": len(pairs),
        "jobs_run": len(jobs),
        "skipped_verified": skipped,
        "adopted_verified_seed": adopted,
        "completed_outputs": len(completed_files),
        "failures": failures,
        "rows_written": sum(int(item["rows"]) for item in results),
        "size_bytes_written_this_run": sum(
            int(item["size_bytes"]) for item in results
        ),
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "worker_memory_limit": args.worker_memory_limit,
        "peak_total_child_mib": round(peak_total_child_mib, 1),
        "peak_single_child_mib": round(peak_single_child_mib, 1),
        "group_seconds_total": {
            group: round(
                sum(
                    float(item.get("group_seconds", {}).get(group, 0.0))
                    for item in results
                ),
                3,
            )
            for group in ("history", "early", "tide", "smart_numpy")
        },
    }
    temp_manifest = manifest_path.with_suffix(".tmp.json")
    temp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
    )
    temp_manifest.replace(manifest_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    if failures:
        raise RuntimeError(f"有 {len(failures)} 只股票处理失败。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
