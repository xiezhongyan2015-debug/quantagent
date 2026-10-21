# -*- coding: utf-8 -*-
"""用 DuckDB 一次读取分钟数据，生成可重复使用的轻量日频结果。"""

from __future__ import annotations

import argparse
import math
import re
import time
from pathlib import Path
from typing import Sequence

import pandas as pd

import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = (
    BASE_DIR
    / "duckdb_factor_store"
    / "minute_daily_extra_v3_2015_2025.parquet"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-glob",
        default=str(factor_store.DEFAULT_MINUTE_DIR / "*.parquet"),
    )
    parser.add_argument(
        "--sample-code",
        help="只处理一只股票，例如 sh600000；用于先测速度和内存。",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--database", default=str(factor_store.DEFAULT_DATABASE))
    parser.add_argument("--temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="8GB")
    return parser.parse_args(argv)


def _sql_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").replace("'", "''")


def _normal_cdf_sql(value: str) -> str:
    """返回正态分布函数的高精度近似 SQL 表达式。"""

    absolute = f"abs({value})"
    t_value = f"(1.0 / (1.0 + 0.2316419 * {absolute}))"
    polynomial = (
        f"(0.319381530 * {t_value}"
        f" - 0.356563782 * pow({t_value}, 2)"
        f" + 1.781477937 * pow({t_value}, 3)"
        f" - 1.821255978 * pow({t_value}, 4)"
        f" + 1.330274429 * pow({t_value}, 5))"
    )
    upper = (
        f"(1.0 - exp(-0.5 * pow({absolute}, 2))"
        f" / sqrt(2.0 * pi()) * {polynomial})"
    )
    return f"CASE WHEN {value} >= 0 THEN {upper} ELSE 1.0 - {upper} END"


def build_query(source_glob: str, start: str, end: str) -> str:
    start_time = pd.Timestamp(start)
    end_exclusive = pd.Timestamp(end) + pd.Timedelta(days=1)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0) * math.gamma(5.0 / 6.0) / math.sqrt(math.pi)
    )
    tripower_scale = mu_two_thirds ** -3.0
    normal_cdf = _normal_cdf_sql("ret1m / 0.1 * 1.96")
    source = _sql_path(source_glob)
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                regexp_extract(filename, '([a-z]{{2}}[0-9]{{6}})\\.parquet$', 1) AS code,
                CAST(datetime AS DATE) AS date,
                datetime,
                row_number() OVER (
                    PARTITION BY filename, CAST(datetime AS DATE)
                    ORDER BY datetime
                ) - 1 AS minute_index,
                dense_rank() OVER (
                    PARTITION BY filename
                    ORDER BY CAST(datetime AS DATE)
                ) - 1 AS day_index,
                CAST(open AS DOUBLE) AS open,
                CAST(high AS DOUBLE) AS high,
                CAST(low AS DOUBLE) AS low,
                CAST(close AS DOUBLE) AS close,
                CAST(volume AS DOUBLE) AS volume,
                CAST(amount AS DOUBLE) AS amount,
                CAST(qfq_ratio AS DOUBLE) AS qfq_ratio
            FROM read_parquet(
                '{source}',
                union_by_name = true,
                filename = true
            )
            WHERE datetime >= TIMESTAMP '{start_time:%Y-%m-%d 00:00:00}'
              AND datetime < TIMESTAMP '{end_exclusive:%Y-%m-%d 00:00:00}'
        ),
        minute0 AS MATERIALIZED (
            SELECT
                *,
                ln(
                    close / coalesce(
                        lag(close) OVER day_window,
                        open
                    )
                ) AS ret1m,
                close / coalesce(
                    lag(close) OVER day_window,
                    open
                ) - 1.0 AS simple_ret1m,
                extract(hour FROM datetime) * 60
                    + extract(minute FROM datetime) AS minute_of_day,
                lag(amount) OVER day_window AS amount_lag1,
                volume - lag(volume) OVER day_window AS volume_diff
            FROM base
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY datetime
            )
        ),
        minute1 AS MATERIALIZED (
            SELECT
                *,
                lag(ret1m) OVER day_window AS ret1m_lag1,
                quantile_cont(ret1m, 0.20) OVER day_partition AS ret_q20,
                quantile_cont(ret1m, 0.80) OVER day_partition AS ret_q80,
                quantile_cont(ret1m, 0.99) OVER day_partition AS ret_q99,
                avg(volume_diff) OVER day_partition AS volume_diff_mean,
                stddev_samp(volume_diff) OVER day_partition AS volume_diff_std,
                avg(volume) OVER day_partition AS day_volume_mean,
                stddev_pop(volume) OVER day_partition AS day_volume_std,
                count(*) OVER day_partition AS day_minute_count,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY volume DESC, datetime
                ) AS volume_desc_rank,
                max(minute_index) OVER day_partition AS max_minute_index,
                count(volume) OVER same_minute_history AS volume_history_count,
                avg(volume) OVER same_minute_history AS volume_history_mean,
                stddev_pop(volume) OVER same_minute_history AS volume_history_std,
                count(amount) OVER same_minute_history AS amount_history_count,
                avg(amount) OVER same_minute_history AS amount_history_mean,
                stddev_pop(amount) OVER same_minute_history AS amount_history_std,
                count(ret1m) OVER trailing_five AS volatility_count,
                stddev_samp(ret1m) OVER trailing_five AS volatility_5m_raw,
                count(simple_ret1m) OVER forward_five AS forward_return_count,
                stddev_samp(simple_ret1m) OVER forward_five
                    AS forward_return_std
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
                ),
                forward_five AS (
                    PARTITION BY code, date ORDER BY minute_index
                    ROWS BETWEEN CURRENT ROW AND 4 FOLLOWING
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
                    WHEN amount_history_count = 20
                     AND amount_history_std > 0
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
        minute_smooth0 AS MATERIALIZED (
            SELECT
                *,
                count(volume) OVER centered_nine AS regional_volume_count,
                sum(volume) OVER centered_nine AS regional_volume
            FROM minute0
            WINDOW centered_nine AS (
                PARTITION BY code, date ORDER BY minute_index
                ROWS BETWEEN 4 PRECEDING AND 4 FOLLOWING
            )
        ),
        tide_peak AS (
            SELECT
                code,
                date,
                arg_max(minute_index, regional_volume) FILTER (
                    WHERE regional_volume_count = 9
                      AND minute_index BETWEEN 5 AND 231
                ) AS peak_minute
            FROM minute_smooth0
            GROUP BY code, date
        ),
        tide_points AS (
            SELECT
                p.code,
                p.date,
                arg_min(m.minute_index, m.regional_volume) FILTER (
                    WHERE m.minute_index BETWEEN 4 AND p.peak_minute - 1
                ) AS surge_minute,
                arg_min(m.close, m.regional_volume) FILTER (
                    WHERE m.minute_index BETWEEN 4 AND p.peak_minute - 1
                ) AS surge_close,
                arg_min(m.minute_index, m.regional_volume) FILTER (
                    WHERE m.minute_index BETWEEN p.peak_minute + 1 AND 232
                ) AS ebb_minute,
                arg_min(m.close, m.regional_volume) FILTER (
                    WHERE m.minute_index BETWEEN p.peak_minute + 1 AND 232
                ) AS ebb_close
            FROM tide_peak AS p
            JOIN minute_smooth0 AS m USING (code, date)
            GROUP BY p.code, p.date, p.peak_minute
        ),
        tide_daily AS (
            SELECT
                code,
                date,
                (ebb_close - surge_close)
                    / nullif(surge_close, 0)
                    / nullif(ebb_minute - surge_minute, 0)
                    AS volume_tide_price_rate_daily
            FROM tide_points
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
        ),
        smart_daily AS (
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
                )
                    AS smart_money_vwap_ratio_10d
            FROM smart_ranked
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
        high_volume_skew_daily AS (
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
            SELECT
                code,
                date,
                close AS price,
                sum(volume) AS price_volume
            FROM minute1
            WHERE close IS NOT NULL AND volume > 0
            GROUP BY code, date, close
        ),
        price_volume_vsp_ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY price_volume DESC, price
                ) AS support_rank,
                sum(price_volume) OVER (PARTITION BY code, date)
                    AS total_day_volume
            FROM price_volume
        ),
        price_volume_ordered AS (
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
            JOIN price_volume_vsp_ranked AS support
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
            FROM price_volume_ordered AS ordered
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
                    minute_of_day ORDER BY simple_ret1m DESC, minute_of_day
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
        ),
        daily1_raw AS (
            SELECT
                code,
                date,
                count(*) AS minute_count,
                corr(abs(ret1m), amount_lag1) AS absret_lag_amount_corr,
                corr(abs(ret1m_lag1), amount) AS lag_absret_amount_corr,
                corr(abs(ret1m_lag1), adjusted_amount) FILTER (
                    WHERE ret1m_lag1 <> 0
                ) AS adj_lag_absret_amount_corr_daily,
                corr(fuzzy_ambiguity, amount) FILTER (
                    WHERE minute_index > 0
                      AND minute_index < max_minute_index
                ) AS fuzzy_amount_corr_daily,
                CASE
                    WHEN sum(volume) FILTER (WHERE minute_index < 30) > 0
                     AND sum(volume) FILTER (
                        WHERE minute_index >= 120 AND minute_index < 150
                     ) > 0
                    THEN sum(volume) FILTER (WHERE minute_index < 30)
                         / sum(volume) FILTER (
                            WHERE minute_index >= 120 AND minute_index < 150
                         )
                END AS morning_afternoon_volume_ratio,
                avg(high / nullif(low, 0) - 1.0)
                    FILTER (WHERE ret1m >= ret_q80)
                  - avg(high / nullif(low, 0) - 1.0)
                    FILTER (WHERE ret1m <= ret_q20)
                    AS amplitude_split_daily,
                exp(sum(ret1m) FILTER (WHERE ret1m >= ret_q99)) - 1.0
                    AS top1pct_minute_return,
                sum(amount * ({normal_cdf})) / nullif(sum(amount), 0)
                    AS normal_active_share,
                sum(
                    amount * greatest(
                        0.0,
                        least(1.0, (ret1m + 0.1) / 0.2)
                    )
                ) / nullif(sum(amount), 0) AS uniform_active_share,
                stddev_samp(amount) / nullif(avg(amount), 0)
                    AS amount_cv_1m,
                stddev_samp(volume) AS volume_std_1m,
                avg(ret1m) FILTER (
                    WHERE volume_diff > volume_diff_mean + volume_diff_std
                ) AS dazzling_return_base,
                avg(forward_return_std) FILTER (
                    WHERE volume_diff > volume_diff_mean + volume_diff_std
                      AND forward_return_count = 5
                ) AS dazzling_volatility_daily,
                (
                    avg((open + high + low + close) / 4.0) - min(low)
                ) / nullif(max(high) - min(low), 0)
                    AS time_avg_relative_price,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN coalesce(
                        sum(ret1m) FILTER (WHERE is_volume_peak), 0.0
                    )
                END AS volume_peak_return_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN (
                        sum(amount * qfq_ratio) FILTER (WHERE is_volume_valley)
                        / nullif(sum(volume) FILTER (WHERE is_volume_valley), 0)
                    ) / nullif(
                        sum(amount * qfq_ratio) / nullif(sum(volume), 0),
                        0
                    )
                END AS valley_relative_vwap_daily,
                CASE
                    WHEN count(*) FILTER (WHERE volume_history_ready) > 0
                    THEN (
                        sum(amount * qfq_ratio) FILTER (WHERE is_volume_ridge)
                        / nullif(sum(volume) FILTER (WHERE is_volume_ridge), 0)
                    ) / nullif(
                        sum(amount * qfq_ratio) / nullif(sum(volume), 0),
                        0
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
        daily1_with_prev AS (
            SELECT
                *,
                lag(day_close) OVER (PARTITION BY code ORDER BY date)
                    AS previous_close
            FROM daily1_raw
        ),
        daily1 AS (
            SELECT
                raw.* EXCLUDE (
                    volume_history_ready_daily,
                    day_high,
                    day_low,
                    day_close,
                    valley_vwap_daily,
                    previous_close
                ),
                smart.smart_money_vwap_ratio_10d,
                tide.volume_tide_price_rate_daily,
                CASE
                    WHEN raw.volume_history_ready_daily
                    THEN coalesce(gaps.peak_gap_count, 0)
                END AS peak_gap_count,
                CASE
                    WHEN raw.volume_history_ready_daily
                    THEN coalesce(gaps.peak_gap_sum1, 0)
                END AS peak_gap_sum1,
                CASE
                    WHEN raw.volume_history_ready_daily
                    THEN coalesce(gaps.peak_gap_sum2, 0)
                END AS peak_gap_sum2,
                CASE
                    WHEN raw.volume_history_ready_daily
                    THEN coalesce(gaps.peak_gap_sum3, 0)
                END AS peak_gap_sum3,
                CASE
                    WHEN raw.volume_history_ready_daily
                    THEN coalesce(gaps.peak_gap_sum4, 0)
                END AS peak_gap_sum4,
                (raw.valley_vwap_daily - least(raw.day_low, raw.previous_close))
                    / nullif(
                        greatest(raw.day_high, raw.previous_close)
                            - least(raw.day_low, raw.previous_close),
                        0
                    ) AS valley_price_percentile_daily,
                high_volume.high_volume_realized_skew_daily,
                p_shaped.p_shaped_volume_distribution_daily,
                top_times.top_return_time_1,
                top_times.top_return_time_2,
                top_times.top_return_time_3,
                top_times.top_return_time_4,
                top_times.top_return_time_5
            FROM daily1_with_prev AS raw
            LEFT JOIN smart_daily AS smart USING (code, date)
            LEFT JOIN tide_daily AS tide USING (code, date)
            LEFT JOIN peak_gap_daily AS gaps USING (code, date)
            LEFT JOIN high_volume_skew_daily AS high_volume USING (code, date)
            LEFT JOIN p_shaped_daily AS p_shaped USING (code, date)
            LEFT JOIN top_return_times_daily AS top_times USING (code, date)
        ),
        bars5 AS MATERIALIZED (
            SELECT
                code,
                date,
                floor(minute_index / 5) AS slot,
                arg_min(open, datetime) AS open5,
                max(high) AS high5,
                min(low) AS low5,
                arg_max(close, datetime) AS close5,
                sum(volume) AS volume5,
                sum(amount) AS amount5
            FROM base
            WHERE code <> ''
            GROUP BY code, date, floor(minute_index / 5)
            HAVING count(*) = 5
        ),
        bars5_ret AS MATERIALIZED (
            SELECT
                *,
                ln(
                    close5 / coalesce(
                        lag(close5) OVER day_window,
                        open5
                    )
                ) AS ret5,
                close5 - coalesce(
                    lag(close5) OVER day_window,
                    open5
                ) AS close_change5
            FROM bars5
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY slot
            )
        ),
        bars5_calc AS MATERIALIZED (
            SELECT
                *,
                lag(ret5, 1) OVER day_window AS ret5_lag1,
                lag(ret5, 2) OVER day_window AS ret5_lag2,
                max(close5) OVER (
                    PARTITION BY code, date ORDER BY slot
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_max_close5
            FROM bars5_ret
            WINDOW day_window AS (
                PARTITION BY code, date ORDER BY slot
            )
        ),
        daily5_raw AS (
            SELECT
                code,
                date,
                (
                    arg_max(close5, slot) - arg_min(open5, slot)
                ) / nullif(sum(abs(close_change5)), 0)
                    AS intraday_trend_ratio_5m,
                pi() / 2.0 * sum(abs(ret5) * abs(ret5_lag1))
                    AS realized_bipower_var_5m,
                {tripower_scale:.16f} * sum(
                    pow(abs(ret5), 2.0 / 3.0)
                    * pow(abs(ret5_lag1), 2.0 / 3.0)
                    * pow(abs(ret5_lag2), 2.0 / 3.0)
                ) AS realized_tripower_var_5m,
                min(close5 / nullif(running_max_close5, 0) - 1.0)
                    AS intraday_max_drawdown_5m,
                sum(ret5 * ret5) AS realized_variance_5m,
                sum(ret5 * ret5) FILTER (WHERE ret5 > 0)
                    AS upside_realized_variance_5m
            FROM bars5_calc
            GROUP BY code, date
        ),
        daily5 AS (
            SELECT
                *,
                greatest(
                    realized_variance_5m - realized_tripower_var_5m,
                    0.0
                ) AS realized_jump_volatility_5m,
                greatest(
                    upside_realized_variance_5m
                        - 0.5 * realized_tripower_var_5m,
                    0.0
                ) AS up_jump_volatility_5m
            FROM daily5_raw
        ),
        bars10 AS (
            SELECT
                code,
                date,
                floor(minute_index / 10) AS slot,
                sum(volume) AS volume10
            FROM base
            WHERE code <> ''
            GROUP BY code, date, floor(minute_index / 10)
            HAVING count(*) = 10
        ),
        bars10_share AS (
            SELECT
                *,
                volume10 / nullif(
                    sum(volume10) OVER (PARTITION BY code, date),
                    0
                ) AS volume_share10
            FROM bars10
        ),
        daily10 AS (
            SELECT
                code,
                date,
                skewness(volume_share10) AS volume_share_skew_10m
            FROM bars10_share
            GROUP BY code, date
        ),
        bars60 AS (
            SELECT
                code,
                date,
                floor(minute_index / 60) AS slot,
                sum(volume) AS volume60
            FROM base
            WHERE code <> ''
            GROUP BY code, date, floor(minute_index / 60)
            HAVING count(*) = 60
        ),
        bars60_share AS (
            SELECT
                *,
                volume60 / nullif(
                    sum(volume60) OVER (PARTITION BY code, date),
                    0
                ) AS volume_share60
            FROM bars60
        ),
        daily60 AS (
            SELECT
                code,
                date,
                stddev_samp(volume_share60) AS volume_share_std_60m
            FROM bars60_share
            GROUP BY code, date
        )
        SELECT
            daily1.*,
            daily5.intraday_trend_ratio_5m,
            daily5.realized_bipower_var_5m,
            daily5.realized_tripower_var_5m,
            daily5.intraday_max_drawdown_5m,
            daily5.realized_jump_volatility_5m,
            daily5.up_jump_volatility_5m,
            daily10.volume_share_skew_10m,
            daily60.volume_share_std_60m
        FROM daily1
        LEFT JOIN daily5 USING (code, date)
        LEFT JOIN daily10 USING (code, date)
        LEFT JOIN daily60 USING (code, date)
        ORDER BY date, code
    """


def _assert_safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出文件必须位于当前工作区内。")
    if resolved.suffix.lower() != ".parquet":
        raise ValueError("输出文件必须使用 .parquet 后缀。")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sample_code:
        if not re.fullmatch(r"[a-z]{2}[0-9]{6}", args.sample_code):
            raise ValueError("sample-code 格式应类似 sh600000。")
        args.source_glob = str(
            factor_store.DEFAULT_MINUTE_DIR / f"{args.sample_code}.parquet"
        )
    output_path = _assert_safe_output(Path(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.stem}.tmp.parquet")
    if temp_path.exists():
        temp_path.unlink()

    query = build_query(args.source_glob, args.start, args.end)
    settings = factor_store.DuckDBSettings(
        database_path=Path(args.database),
        temp_dir=Path(args.temp_dir),
        threads=args.threads,
        memory_limit=args.memory_limit,
    )
    started = time.perf_counter()
    with factor_store.connect(settings) as connection:
        connection.execute(
            f"""
            COPY ({query})
            TO '{_sql_path(temp_path)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
        temp_path.replace(output_path)
        if output_path == DEFAULT_OUTPUT.resolve() and not args.sample_code:
            factor_store.register_sources(
                connection, {"minute_extra_daily": output_path}
            )
        summary = connection.execute(
            """
            SELECT
                count(*) AS rows,
                count(DISTINCT code) AS stocks,
                min(date) AS first_date,
                max(date) AS last_date
            FROM read_parquet(?)
            """,
            [str(output_path)],
        ).fetchdf()
    elapsed = time.perf_counter() - started
    print(summary.to_string(index=False))
    print(
        f"已生成 {output_path}，{output_path.stat().st_size / 1024**2:.1f} MiB，"
        f"耗时 {elapsed:.1f} 秒。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
