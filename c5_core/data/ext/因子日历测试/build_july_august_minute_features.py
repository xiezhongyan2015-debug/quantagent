# -*- coding: utf-8 -*-
"""生成七八月新增分钟因子的轻量每日数据，支持中断后继续。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
from statistics import NormalDist
from typing import Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd
import psutil
import pyarrow.parquet as pq

import build_shared_market_minute_stats as shared_market
import duckdb_factor_store as factor_store


BASE_DIR = Path(__file__).resolve().parent
STORE_DIR = BASE_DIR / "duckdb_factor_store"
DEFAULT_MARKET_FILE = shared_market.DEFAULT_OUTPUT
DEFAULT_STOCK_DIR = STORE_DIR / "july_august_minute_daily_by_stock"
DEFAULT_LONE_GOOSE_DIR = STORE_DIR / "lone_goose_daily_by_year"
DEFAULT_MANIFEST = STORE_DIR / "july_august_minute_manifest.json"

TRIAL_PARAMETERS = {
    "J07_D05_SIG_JUMP_ASYM": {
        "formula_mode": "trial",
        "frequency": "5分钟，48段",
        "alpha": 0.01,
        "window_days": 20,
        "daily_jump_asymmetry": "RVJP-RVJN；连续方差使用三幂变差；绝对5分钟收益超过15%按0处理",
        "weight": "max(T_SwV/Phi_0.99, 0)",
    },
    "J07_D08_AMOUNT_AUTOCORR": {
        "formula_mode": "trial",
        "lag_minutes": 1,
        "daily_formula": "corr(Amount_t-1, Amount_t)",
    },
    "J08_D08_ABSRET_AMOUNT_CORR": {
        "formula_mode": "trial",
        "return": "1分钟对数收益，首分钟使用开盘价",
        "daily_formula": "corr(abs(log_return_1m), Amount_t)",
        "monthly_window_days": 10,
    },
    "J08_D25_AMT_MIN": {
        "formula_mode": "trial",
        "selected_minutes": "按标题取1分钟收益最低30分钟",
        "daily_formula": "所选分钟前一分钟成交额市场份额之和/全天份额均值",
        "conflict": "图片标题和说明写最低，注释第3点误写最高；试算采用标题",
    },
}

STOCK_OUTPUT_COLUMNS = [
    "date",
    "code",
    "fuzzy_quantity_ratio_daily",
    "extreme_return_daily",
    "pre_extreme_return_daily",
    "late_amount_share_30m",
    "amt_max_30_daily",
    "amount_autocorr_lag1_daily_trial",
    "absret_amount_corr_daily_trial",
    "amt_min_30_daily_trial",
    "sig_jump_asym_20d_trial",
    "disaster_rebuild_daily",
    "minute_amount_variance_daily",
    "large_volume_price_corr_daily",
    "ideal_amplitude_10d",
    "minute_count",
]

LONE_GOOSE_SEQUENCE_COLUMNS = [
    "date",
    "code",
    "amount_sequence",
    "selected_minute_count",
]

_WORKER_CONNECTION: duckdb.DuckDBPyConnection | None = None


def _sql_path(value: str | Path) -> str:
    return str(Path(value).resolve()).replace("\\", "/").replace("'", "''")


def _assert_workspace_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("输出必须位于当前工作区内。")
    return resolved


def _date_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_time = pd.Timestamp(start)
    end_exclusive = pd.Timestamp(end) + pd.Timedelta(days=1)
    if end_exclusive <= start_time:
        raise ValueError("结束日期必须不早于开始日期。")
    return start_time, end_exclusive


def _read_parquet_sql(paths: Sequence[str | Path], filename: bool = False) -> str:
    if not paths:
        raise ValueError("至少需要一个分钟文件。")
    quoted = ", ".join(f"'{_sql_path(path)}'" for path in paths)
    extra = ", filename = true" if filename else ""
    return f"read_parquet([{quoted}], union_by_name = true{extra})"


def build_market_query(
    minute_paths: Sequence[str | Path], start: str, end: str
) -> str:
    """保留原入口，但生成统一的共用市场分钟表。"""

    return shared_market.build_query(minute_paths, start, end)


def build_stock_daily_query(
    minute_path: str | Path,
    market_path: str | Path,
    start: str,
    end: str,
    source_relation: str | None = None,
    code: str | None = None,
) -> str:
    """一次读取一只股票，计算七八月八项分钟日值和理想振幅所需列表。"""

    start_time, end_exclusive = _date_bounds(start, end)
    source = source_relation or _read_parquet_sql([minute_path])
    market_source = shared_market.market_read_sql(market_path)
    stock_code = (code or Path(minute_path).stem.lower()).replace("'", "''")
    return f"""
        WITH base AS MATERIALIZED (
            SELECT
                '{stock_code}' AS code,
                CAST(s.datetime AS DATE) AS date,
                s.datetime,
                CAST(s.open AS DOUBLE) AS minute_open,
                CAST(s.high AS DOUBLE) AS minute_high,
                CAST(s.low AS DOUBLE) AS minute_low,
                CAST(s.close AS DOUBLE) AS minute_close,
                CAST(s.volume AS DOUBLE) AS minute_volume,
                CAST(s.amount AS DOUBLE) AS minute_amount,
                m.market_amount_total,
                m.market_day_index
            FROM {source} AS s
            INNER JOIN {market_source} AS m
                USING (datetime)
            WHERE s.datetime >= TIMESTAMP '{start_time:%Y-%m-%d 00:00:00}'
              AND s.datetime < TIMESTAMP '{end_exclusive:%Y-%m-%d 00:00:00}'
        ),
        minute0 AS MATERIALIZED (
            SELECT
                *,
                row_number() OVER day_window - 1 AS minute_index,
                count(*) OVER day_partition AS day_minute_count,
                ln(
                    minute_close / nullif(
                        coalesce(
                            lag(minute_close) OVER day_window,
                            minute_open
                        ),
                        0
                    )
                ) AS log_return_1m,
                minute_close / nullif(
                    coalesce(
                        lag(minute_close) OVER day_window,
                        minute_open
                    ),
                    0
                ) - 1.0 AS simple_return_1m,
                minute_amount / nullif(market_amount_total, 0)
                    AS amount_percent,
                sum(
                    minute_open + minute_high + minute_low + minute_close
                ) OVER last_five AS price_sum_20,
                sum(
                    minute_open * minute_open
                    + minute_high * minute_high
                    + minute_low * minute_low
                    + minute_close * minute_close
                ) OVER last_five AS price_square_sum_20,
                count(minute_open) OVER last_five
                    + count(minute_high) OVER last_five
                    + count(minute_low) OVER last_five
                    + count(minute_close) OVER last_five
                    AS price_count_20
            FROM base
            WINDOW
                day_window AS (
                    PARTITION BY code, date ORDER BY datetime
                ),
                day_partition AS (PARTITION BY code, date),
                last_five AS (
                    PARTITION BY code, date ORDER BY datetime
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                )
        ),
        minute1 AS MATERIALIZED (
            SELECT
                *,
                CASE
                    WHEN minute_index > 0
                     AND minute_index < day_minute_count - 1
                     AND count(
                        CASE
                            WHEN minute_index > 0
                             AND minute_index < day_minute_count - 1
                            THEN log_return_1m
                        END
                     ) OVER last_five = 5
                    THEN stddev_pop(
                        CASE
                            WHEN minute_index > 0
                             AND minute_index < day_minute_count - 1
                            THEN log_return_1m
                        END
                    ) OVER last_five
                END AS return_volatility_5m,
                lag(simple_return_1m) OVER day_window
                    AS prior_simple_return_1m,
                lag(amount_percent) OVER day_window
                    AS prior_amount_percent,
                lag(minute_amount) OVER day_window
                    AS prior_minute_amount,
                median(simple_return_1m) OVER day_partition
                    AS daily_return_median,
                CASE
                    WHEN price_count_20 = 20
                     AND price_sum_20 <> 0
                    THEN greatest(
                        (
                            price_square_sum_20
                            - price_sum_20 * price_sum_20 / 20.0
                        ) / 19.0,
                        0.0
                    ) / pow(price_sum_20 / 20.0, 2)
                END AS better_volatility
            FROM minute0
            WINDOW
                day_window AS (
                    PARTITION BY code, date ORDER BY datetime
                ),
                day_partition AS (PARTITION BY code, date),
                last_five AS (
                    PARTITION BY code, date ORDER BY datetime
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                )
        ),
        minute2 AS MATERIALIZED (
            SELECT
                *,
                CASE
                    WHEN minute_index > 0
                     AND minute_index < day_minute_count - 1
                     AND count(return_volatility_5m) OVER last_five = 5
                    THEN stddev_pop(return_volatility_5m) OVER last_five
                END AS fuzziness_5m,
                simple_return_1m / nullif(better_volatility, 0)
                    AS return_volatility_ratio
            FROM minute1
            WINDOW last_five AS (
                PARTITION BY code, date ORDER BY datetime
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            )
        ),
        minute3 AS MATERIALIZED (
            SELECT
                *,
                avg(fuzziness_5m) OVER day_partition
                    AS daily_fuzziness_mean,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY
                        abs(simple_return_1m - daily_return_median) DESC NULLS LAST,
                        datetime
                ) AS extreme_rank,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY simple_return_1m DESC NULLS LAST, datetime
                ) AS return_rank,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY simple_return_1m ASC NULLS LAST, datetime
                ) AS low_return_rank,
                row_number() OVER (
                    PARTITION BY code, date
                    ORDER BY minute_volume DESC NULLS LAST, datetime
                ) AS volume_rank
            FROM minute2
            WINDOW day_partition AS (PARTITION BY code, date)
        )
        SELECT
            date,
            code,
            avg(minute_volume) FILTER (
                WHERE fuzziness_5m > daily_fuzziness_mean
            ) / nullif(
                avg(minute_volume) FILTER (
                    WHERE minute_index > 0
                      AND minute_index < day_minute_count - 1
                ),
                0
            ) AS fuzzy_quantity_ratio_daily,
            max(simple_return_1m) FILTER (
                WHERE extreme_rank = 1
            ) AS extreme_return_daily,
            max(prior_simple_return_1m) FILTER (
                WHERE extreme_rank = 1
            ) AS pre_extreme_return_daily,
            sum(minute_amount) FILTER (
                WHERE minute_index >= day_minute_count - 30
            ) / nullif(sum(minute_amount), 0) AS late_amount_share_30m,
            sum(prior_amount_percent) FILTER (
                WHERE return_rank <= 30
            ) / nullif(avg(amount_percent), 0) AS amt_max_30_daily,
            corr(prior_minute_amount, minute_amount)
                AS amount_autocorr_lag1_daily_trial,
            corr(abs(log_return_1m), minute_amount)
                AS absret_amount_corr_daily_trial,
            sum(prior_amount_percent) FILTER (
                WHERE low_return_rank <= 30
            ) / nullif(avg(amount_percent), 0) AS amt_min_30_daily_trial,
            covar_samp(
                return_volatility_ratio,
                better_volatility
            ) FILTER (
                WHERE minute_index > 0
                  AND minute_index < day_minute_count - 1
            ) AS disaster_rebuild_daily,
            var_pop(minute_amount) AS minute_amount_variance_daily,
            corr(minute_volume, minute_close) FILTER (
                WHERE volume_rank <= ceil(day_minute_count / 3.0)
            ) AS large_volume_price_corr_daily,
            list(minute_close ORDER BY datetime) AS minute_close_list,
            list(
                minute_high / nullif(minute_low, 0) - 1.0
                ORDER BY datetime
            ) AS minute_amplitude_list,
            arg_min(minute_open, datetime) AS day_open,
            max(market_day_index) AS market_day_index,
            count(*) AS minute_count
        FROM minute3
        GROUP BY date, code
        ORDER BY date
    """


def compute_ideal_amplitude(
    daily: pd.DataFrame,
    window_days: int = 10,
    fraction: float = 0.25,
) -> np.ndarray:
    """按最近十个市场交易日的全部有效分钟计算理想振幅。"""

    if window_days < 1:
        raise ValueError("交易日窗口必须大于0。")
    if not 0 < fraction < 0.5:
        raise ValueError("高低价分钟占比必须在0和0.5之间。")
    required = {"market_day_index", "minute_close_list", "minute_amplitude_list"}
    missing = required - set(daily.columns)
    if missing:
        raise KeyError(f"缺少字段：{', '.join(sorted(missing))}")

    result = np.full(len(daily), np.nan, dtype=np.float64)
    day_indices = daily["market_day_index"].to_numpy(dtype=np.int64)
    close_lists = daily["minute_close_list"].tolist()
    amplitude_lists = daily["minute_amplitude_list"].tolist()
    left = 0
    for right, current_day in enumerate(day_indices):
        earliest = current_day - window_days + 1
        while left <= right and day_indices[left] < earliest:
            left += 1
        window_indices = day_indices[left : right + 1]
        if (
            len(window_indices) != window_days
            or not np.array_equal(
                window_indices, np.arange(earliest, current_day + 1)
            )
        ):
            continue
        minute_lengths = {
            len(values) for values in close_lists[left : right + 1]
        } | {len(values) for values in amplitude_lists[left : right + 1]}
        if len(minute_lengths) != 1:
            continue
        closes = np.concatenate(
            [np.asarray(values, dtype=np.float64) for values in close_lists[left : right + 1]]
        )
        amplitudes = np.concatenate(
            [
                np.asarray(values, dtype=np.float64)
                for values in amplitude_lists[left : right + 1]
            ]
        )
        valid = np.isfinite(closes) & np.isfinite(amplitudes)
        closes = closes[valid]
        amplitudes = amplitudes[valid]
        take = int(np.floor(len(closes) * fraction))
        if take < 1 or take * 2 > len(closes):
            continue
        order = np.argsort(closes, kind="stable")
        low_mean = float(np.mean(amplitudes[order[:take]]))
        high_mean = float(np.mean(amplitudes[order[-take:]]))
        result[right] = high_mean - low_mean
    return result


def compute_daily_jump_asymmetry_and_statistic(
    daily: pd.DataFrame,
    return_cap: float = 0.15,
) -> tuple[np.ndarray, np.ndarray]:
    """计算5分钟SRJV与Jiang–Oomen SwV单侧统计量。"""

    required = {"minute_close_list", "day_open"}
    missing = required - set(daily.columns)
    if missing:
        raise KeyError(f"缺少字段：{', '.join(sorted(missing))}")
    if return_cap <= 0:
        raise ValueError("异常收益界限必须大于0。")

    srjv = np.full(len(daily), np.nan, dtype=np.float64)
    statistic = np.full(len(daily), np.nan, dtype=np.float64)
    mu_two_thirds = (
        2.0 ** (1.0 / 3.0)
        * math.gamma(5.0 / 6.0)
        / math.sqrt(math.pi)
    )
    mu_one = math.sqrt(2.0 / math.pi)
    mu_six = 15.0
    mu_three_halves = (
        2.0 ** 0.75 * math.gamma(1.25) / math.sqrt(math.pi)
    )

    for index, row in enumerate(daily.itertuples(index=False)):
        minute_close = np.asarray(row.minute_close_list, dtype=np.float64)
        day_open = float(row.day_open)
        if (
            len(minute_close) != 240
            or not np.isfinite(minute_close).all()
            or not np.isfinite(day_open)
            or day_open <= 0
        ):
            continue
        close_5m = minute_close[4::5]
        prior_close = np.r_[day_open, close_5m[:-1]]
        if (close_5m <= 0).any() or (prior_close <= 0).any():
            continue
        simple_return = close_5m / prior_close - 1.0
        log_return = np.log(close_5m / prior_close)
        if not np.isfinite(simple_return).all() or not np.isfinite(log_return).all():
            continue

        jump_return = log_return.copy()
        jump_return[np.abs(jump_return) > return_cap] = 0.0
        power = np.abs(jump_return) ** (2.0 / 3.0)
        integrated_variance = mu_two_thirds ** -3 * np.sum(
            power[2:] * power[1:-1] * power[:-2]
        )
        positive_variance = np.sum(
            np.where(jump_return > 0, jump_return * jump_return, 0.0)
        )
        negative_variance = np.sum(
            np.where(jump_return < 0, jump_return * jump_return, 0.0)
        )
        rvjp = max(positive_variance - 0.5 * integrated_variance, 0.0)
        rvjn = max(negative_variance - 0.5 * integrated_variance, 0.0)
        srjv[index] = rvjp - rvjn

        count = len(log_return)
        realized_variance = float(np.sum(log_return * log_return))
        swap_variance = float(2.0 * np.sum(simple_return - log_return))
        bipower = (
            mu_one ** -2
            * count
            / (count - 1.0)
            * np.sum(np.abs(log_return[1:]) * np.abs(log_return[:-1]))
        )
        powered = np.abs(log_return) ** 1.5
        omega = (
            (mu_six / 9.0)
            * (count**3 / (count - 3.0))
            * mu_three_halves ** -4
            * np.sum(
                powered[:-3]
                * powered[1:-2]
                * powered[2:-1]
                * powered[3:]
            )
        )
        if swap_variance > 0 and omega > 0 and np.isfinite(bipower):
            statistic[index] = (
                bipower
                / math.sqrt(omega / count)
                * (1.0 - realized_variance / swap_variance)
            )
    return srjv, statistic


def compute_significance_weighted_jump_asymmetry(
    daily: pd.DataFrame,
    window_days: int = 20,
    alpha: float = 0.01,
) -> np.ndarray:
    """用非负T_SwV显著程度权重合成20日SRJV试算值。"""

    if window_days < 1:
        raise ValueError("交易日窗口必须大于0。")
    if not 0 < alpha < 0.5:
        raise ValueError("显著性水平必须在0和0.5之间。")
    if "market_day_index" not in daily.columns:
        raise KeyError("缺少字段：market_day_index")
    srjv, statistic = compute_daily_jump_asymmetry_and_statistic(daily)
    critical = NormalDist().inv_cdf(1.0 - alpha)
    weight = np.maximum(statistic / critical, 0.0)
    market_day = daily["market_day_index"].to_numpy(dtype=np.int64)
    result = np.full(len(daily), np.nan, dtype=np.float64)
    for end in range(window_days - 1, len(daily)):
        left = end - window_days + 1
        expected_days = np.arange(
            market_day[end] - window_days + 1,
            market_day[end] + 1,
        )
        if not np.array_equal(market_day[left : end + 1], expected_days):
            continue
        current_value = srjv[left : end + 1]
        current_weight = weight[left : end + 1]
        valid = np.isfinite(current_value) & np.isfinite(current_weight)
        if not valid.all():
            continue
        denominator = float(np.sum(current_weight))
        if denominator > 0:
            result[end] = float(
                np.sum(current_value * current_weight) / denominator
            )
    return result


def compute_lone_goose_day(
    codes: Sequence[str],
    amount_sequences: Sequence[Sequence[float]],
    block_size: int = 512,
) -> pd.DataFrame:
    """计算一天内每只股票与其余股票成交额相关绝对值的均值。"""

    if len(codes) != len(amount_sequences):
        raise ValueError("股票代码与成交额序列数量不一致。")
    if block_size < 1:
        raise ValueError("每批股票数必须大于0。")
    kept_codes: list[str] = []
    kept_values: list[np.ndarray] = []
    expected_length: int | None = None
    for code, raw_values in zip(codes, amount_sequences):
        values = np.asarray(raw_values, dtype=np.float64)
        if expected_length is None:
            expected_length = len(values)
        if len(values) != expected_length or len(values) < 2:
            continue
        if not np.isfinite(values).all():
            continue
        standard = float(np.std(values, ddof=1))
        if not np.isfinite(standard) or standard <= 0:
            continue
        kept_codes.append(str(code).lower())
        kept_values.append(((values - np.mean(values)) / standard).astype(np.float32))

    count = len(kept_values)
    if count < 2:
        return pd.DataFrame(columns=["code", "lone_goose_daily"])
    normalized = np.vstack(kept_values)
    transpose = normalized.T
    denominator = float(normalized.shape[1] - 1)
    result = np.empty(count, dtype=np.float64)
    for start in range(0, count, block_size):
        end = min(start + block_size, count)
        correlation = normalized[start:end] @ transpose / denominator
        diagonal = correlation[
            np.arange(end - start), np.arange(start, end)
        ]
        result[start:end] = (
            np.sum(np.abs(correlation), axis=1) - np.abs(diagonal)
        ) / (count - 1)
    return pd.DataFrame(
        {
            "code": kept_codes,
            "lone_goose_daily": result.astype(np.float32),
        }
    )


def build_lone_goose_query(
    minute_paths: Sequence[str | Path],
    market_path: str | Path,
    start: str,
    end: str,
    order_output: bool = True,
) -> str:
    """按日期、股票返回不分化时刻的成交额列表，避免传回逐分钟长表。"""

    start_time, end_exclusive = _date_bounds(start, end)
    source = _read_parquet_sql(minute_paths, filename=True)
    market_source = shared_market.market_read_sql(market_path)
    order_clause = "ORDER BY date, code" if order_output else ""
    return f"""
        WITH chosen AS MATERIALIZED (
            SELECT
                date,
                datetime,
                count(*) OVER (PARTITION BY date) AS selected_minute_count
            FROM {market_source}
            WHERE is_non_differentiation_pop
              AND datetime >= TIMESTAMP '{start_time:%Y-%m-%d 00:00:00}'
              AND datetime < TIMESTAMP '{end_exclusive:%Y-%m-%d 00:00:00}'
        ),
        picked AS (
            SELECT
                chosen.date,
                chosen.datetime,
                chosen.selected_minute_count,
                regexp_extract(
                    filename,
                    '([a-z]{{2}}[0-9]{{6}})\\.parquet$',
                    1
                ) AS code,
                CAST(s.amount AS DOUBLE) AS minute_amount
            FROM {source} AS s
            INNER JOIN chosen USING (datetime)
        )
        SELECT
            date,
            code,
            list(minute_amount ORDER BY datetime) AS amount_sequence,
            max(selected_minute_count) AS selected_minute_count
        FROM picked
        WHERE code <> ''
        GROUP BY date, code
        HAVING count(minute_amount) = max(selected_minute_count)
        {order_clause}
    """


def _init_worker(temp_dir: str, memory_limit: str) -> None:
    global _WORKER_CONNECTION
    worker_temp = Path(temp_dir) / f"julaug_worker_{os.getpid()}"
    worker_temp.mkdir(parents=True, exist_ok=True)
    _WORKER_CONNECTION = duckdb.connect()
    _WORKER_CONNECTION.execute("SET threads = 1")
    _WORKER_CONNECTION.execute(f"SET memory_limit = '{memory_limit}'")
    _WORKER_CONNECTION.execute(
        f"SET temp_directory = '{_sql_path(worker_temp)}'"
    )
    _WORKER_CONNECTION.execute("SET preserve_insertion_order = false")


def _build_one_stock(
    minute_path: str,
    market_path: str,
    output_path: str,
    start: str,
    end: str,
) -> dict[str, object]:
    if _WORKER_CONNECTION is None:
        raise RuntimeError("工作进程没有初始化。")
    source = Path(minute_path)
    output = Path(output_path)
    temp = output.with_name(f"{output.stem}.tmp.parquet")
    temp.unlink(missing_ok=True)
    started = time.perf_counter()
    daily = _WORKER_CONNECTION.execute(
        build_stock_daily_query(source, market_path, start, end)
    ).fetchdf()
    if daily.empty:
        return {
            "code": source.stem.lower(),
            "rows": 0,
            "seconds": time.perf_counter() - started,
            "size_bytes": 0,
            "status": "empty",
        }
    daily = finalize_stock_daily_frame(daily)
    daily.to_parquet(temp, compression="zstd", index=False)
    temp.replace(output)
    return {
        "code": source.stem.lower(),
        "rows": len(daily),
        "seconds": time.perf_counter() - started,
        "size_bytes": output.stat().st_size,
        "status": "completed",
    }


def finalize_stock_daily_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """把查询结果整理成固定的七八月逐股输出格式。"""

    daily = daily.copy()
    daily["ideal_amplitude_10d"] = compute_ideal_amplitude(daily)
    daily["sig_jump_asym_20d_trial"] = compute_significance_weighted_jump_asymmetry(
        daily
    )
    daily = daily.drop(
        columns=[
            "minute_close_list",
            "minute_amplitude_list",
            "market_day_index",
            "day_open",
        ]
    )
    daily["date"] = pd.to_datetime(daily["date"])
    daily["code"] = daily["code"].astype("string")
    for column in STOCK_OUTPUT_COLUMNS[2:-1]:
        daily[column] = pd.to_numeric(daily[column], errors="coerce").astype(
            np.float32
        )
    daily["minute_count"] = daily["minute_count"].astype(np.int16)
    daily = daily[STOCK_OUTPUT_COLUMNS]
    return daily


def _source_files(
    universe: str, minute_data_dir: Path, base_cache_glob: Path
) -> list[Path]:
    if universe == "all":
        return sorted(minute_data_dir.glob("*.parquet"))
    if universe != "base":
        raise ValueError(f"不支持的股票范围：{universe}")
    base_pattern = base_cache_glob
    codes = sorted(
        path.stem.lower() for path in base_pattern.parent.glob(base_pattern.name)
    )
    return [
        minute_data_dir / f"{code}.parquet"
        for code in codes
        if (minute_data_dir / f"{code}.parquet").exists()
    ]


def _connection(
    temp_dir: Path, threads: int, memory_limit: str
) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(f"SET threads = {max(1, threads)}")
    connection.execute(f"SET memory_limit = '{memory_limit}'")
    connection.execute(f"SET temp_directory = '{_sql_path(temp_dir)}'")
    connection.execute("SET preserve_insertion_order = false")
    return connection


def _require_market_columns(path: Path, required: set[str]) -> None:
    files = shared_market.list_market_stat_files(path)
    for file in files:
        try:
            columns = set(pq.ParquetFile(file).schema_arrow.names)
        except Exception as exc:
            raise ValueError(f"无法读取共用市场分钟表：{file}；{exc}") from exc
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                f"共用市场分钟表{file.name}缺少字段：{', '.join(missing)}。"
                "请先运行 build_shared_market_minute_stats.py。"
            )


def _build_market_file(
    files: Sequence[Path],
    output: Path,
    start: str,
    end: str,
    temp_dir: Path,
    threads: int,
    memory_limit: str,
    force: bool,
) -> dict[str, object]:
    if output.exists() and output.stat().st_size > 0 and not force:
        _require_market_columns(output, set(shared_market.OUTPUT_COLUMNS))
        return {"status": "skipped", "path": str(output)}
    temp = output.with_name(f"{output.stem}.tmp.parquet")
    temp.unlink(missing_ok=True)
    started = time.perf_counter()
    with _connection(temp_dir, threads, memory_limit) as connection:
        query = build_market_query(files, start, end)
        connection.execute(
            f"""
            COPY ({query})
            TO '{_sql_path(temp)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
        rows = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(temp)]
            ).fetchone()[0]
        )
    if rows == 0:
        temp.unlink(missing_ok=True)
        raise RuntimeError("市场分钟表没有生成任何记录。")
    temp.replace(output)
    return {
        "status": "completed",
        "path": str(output),
        "rows": rows,
        "size_bytes": output.stat().st_size,
        "seconds": time.perf_counter() - started,
    }


def _memory_sample() -> tuple[float, float]:
    parent = psutil.Process(os.getpid())
    children = [
        child for child in parent.children(recursive=True) if child.is_running()
    ]
    values: list[float] = []
    for child in children:
        try:
            values.append(child.memory_info().rss / 1024**2)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sum(values), max(values, default=0.0)


def _build_stock_files(
    files: Sequence[Path],
    market_path: Path,
    output_dir: Path,
    start: str,
    end: str,
    temp_dir: Path,
    workers: int,
    worker_memory_limit: str,
    force: bool,
) -> dict[str, object]:
    jobs: list[tuple[Path, Path]] = []
    skipped = 0
    for source in files:
        output = output_dir / f"{source.stem.lower()}.parquet"
        if output.exists() and output.stat().st_size > 0 and not force:
            skipped += 1
        else:
            jobs.append((source, output))

    started = time.perf_counter()
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    peak_total = 0.0
    peak_one = 0.0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(str(temp_dir), worker_memory_limit),
    ) as executor:
        future_map = {
            executor.submit(
                _build_one_stock,
                str(source),
                str(market_path),
                str(output),
                start,
                end,
            ): source.stem.lower()
            for source, output in jobs
        }
        pending = set(future_map)
        completed = 0
        while pending:
            done, pending = wait(
                pending, timeout=1.0, return_when=FIRST_COMPLETED
            )
            total, one = _memory_sample()
            peak_total = max(peak_total, total)
            peak_one = max(peak_one, one)
            for future in done:
                completed += 1
                code = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failures.append({"code": code, "error": repr(exc)})
                if completed % 25 == 0 or completed == len(jobs):
                    print(
                        f"股票日值 {completed}/{len(jobs)}；失败 {len(failures)}；"
                        f"子进程内存最高合计 {peak_total:.0f} MiB",
                        flush=True,
                    )
    summary = {
        "status": "completed" if not failures else "failed",
        "requested_files": len(files),
        "jobs_run": len(jobs),
        "skipped_existing": skipped,
        "completed": sum(item["status"] == "completed" for item in results),
        "empty": sum(item["status"] == "empty" for item in results),
        "rows": int(sum(int(item["rows"]) for item in results)),
        "failures": failures,
        "seconds": time.perf_counter() - started,
        "peak_total_child_mib": round(peak_total, 1),
        "peak_single_child_mib": round(peak_one, 1),
    }
    if failures:
        raise RuntimeError(f"有 {len(failures)} 只股票生成失败。")
    return summary


def _process_lone_goose_rows(
    rows: Iterable[pd.DataFrame], block_size: int
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    outputs: list[pd.DataFrame] = []
    diagnostics: list[dict[str, object]] = []
    current_date: pd.Timestamp | None = None
    current_codes: list[str] = []
    current_sequences: list[Sequence[float]] = []
    current_selected = 0

    def finish_day() -> None:
        nonlocal current_codes, current_sequences
        if current_date is None or not current_codes:
            return
        daily = compute_lone_goose_day(
            current_codes, current_sequences, block_size=block_size
        )
        daily.insert(0, "date", current_date)
        daily["selected_minute_count"] = np.int16(current_selected)
        daily["participating_stock_count"] = np.int32(len(daily))
        outputs.append(daily)
        diagnostics.append(
            {
                "date": str(pd.Timestamp(current_date).date()),
                "selected_minutes": current_selected,
                "participating_stocks": len(daily),
            }
        )
        current_codes = []
        current_sequences = []

    for frame in rows:
        for row in frame.itertuples(index=False):
            row_date = pd.Timestamp(row.date)
            if current_date is not None and row_date != current_date:
                finish_day()
            if current_date is None or row_date != current_date:
                current_date = row_date
                current_selected = int(row.selected_minute_count)
            current_codes.append(str(row.code))
            current_sequences.append(row.amount_sequence)
    finish_day()
    if not outputs:
        return (
            pd.DataFrame(
                columns=[
                    "date",
                    "code",
                    "lone_goose_daily",
                    "selected_minute_count",
                    "participating_stock_count",
                ]
            ),
            diagnostics,
        )
    return pd.concat(outputs, ignore_index=True), diagnostics


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _lone_goose_sequence_signature(
    files: Sequence[Path],
    market_path: Path,
    start: str,
    end: str,
    stock_batch_size: int,
) -> tuple[dict[str, object], str]:
    market_files = shared_market.list_market_stat_files(market_path)
    signature: dict[str, object] = {
        "version": 1,
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "stock_batch_size": int(stock_batch_size),
        "source": shared_market.source_fingerprint(list(files)),
        "market": shared_market.source_fingerprint(market_files),
        "columns": LONE_GOOSE_SEQUENCE_COLUMNS,
    }
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return signature, hashlib.sha256(encoded).hexdigest()[:20]


def _sequence_batch_reusable(
    output: Path,
    meta_path: Path,
    expected: dict[str, object],
) -> bool:
    if not output.is_file() or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        parquet = pq.ParquetFile(output)
        columns = parquet.schema_arrow.names
        rows = parquet.metadata.num_rows
        parquet.close()
    except Exception:
        return False
    recorded = meta.get("output")
    return bool(
        meta.get("status") == "completed"
        and meta.get("batch") == expected
        and isinstance(recorded, dict)
        and columns == LONE_GOOSE_SEQUENCE_COLUMNS
        and rows == recorded.get("rows")
        and output.stat().st_size == recorded.get("size_bytes")
    )


def _build_lone_goose_sequence_batches(
    files: Sequence[Path],
    market_path: Path,
    start: str,
    end: str,
    temp_dir: Path,
    threads: int,
    memory_limit: str,
    stock_batch_size: int,
    force: bool,
) -> tuple[list[Path], dict[str, object]]:
    if stock_batch_size < 1:
        raise ValueError("孤雁股票批量必须至少为1。")
    signature, cache_key = _lone_goose_sequence_signature(
        files, market_path, start, end, stock_batch_size
    )
    cache_dir = temp_dir / "lone_goose_sequence_cache" / cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    batches = [
        list(files[index : index + stock_batch_size])
        for index in range(0, len(files), stock_batch_size)
    ]
    records: list[dict[str, object]] = []
    outputs: list[Path] = []
    started = time.perf_counter()
    spill_dir = temp_dir / "lone_goose_sequence_spill" / cache_key
    spill_dir.mkdir(parents=True, exist_ok=True)
    with _connection(spill_dir, threads, memory_limit) as connection:
        for batch_index, batch_files in enumerate(batches):
            output = cache_dir / f"batch_{batch_index:05d}.parquet"
            meta_path = cache_dir / f"batch_{batch_index:05d}.json"
            batch_identity: dict[str, object] = {
                "cache_key": cache_key,
                "index": batch_index,
                "source_count": len(batch_files),
                "codes": [path.stem.lower() for path in batch_files],
            }
            if (
                not force
                and _sequence_batch_reusable(output, meta_path, batch_identity)
            ):
                parquet = pq.ParquetFile(output)
                rows = int(parquet.metadata.num_rows)
                parquet.close()
                records.append(
                    {
                        "batch": batch_index,
                        "status": "skipped",
                        "source_count": len(batch_files),
                        "rows": rows,
                        "size_bytes": output.stat().st_size,
                    }
                )
                outputs.append(output)
                continue

            temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
            temporary.unlink(missing_ok=True)
            batch_started = time.perf_counter()
            try:
                query = build_lone_goose_query(
                    batch_files,
                    market_path,
                    start,
                    end,
                    order_output=True,
                )
                connection.execute(
                    f"""
                    COPY ({query})
                    TO '{_sql_path(temporary)}'
                    (
                        FORMAT PARQUET,
                        COMPRESSION ZSTD,
                        ROW_GROUP_SIZE 10000
                    )
                    """
                )
                parquet = pq.ParquetFile(temporary)
                columns = parquet.schema_arrow.names
                rows = int(parquet.metadata.num_rows)
                parquet.close()
                if columns != LONE_GOOSE_SEQUENCE_COLUMNS:
                    raise RuntimeError(
                        "孤雁临时序列字段不正确："
                        f"{columns}"
                    )
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)

            one_record = {
                "batch": batch_index,
                "status": "completed",
                "source_count": len(batch_files),
                "rows": rows,
                "size_bytes": output.stat().st_size,
                "seconds": time.perf_counter() - batch_started,
            }
            _atomic_write_json(
                meta_path,
                {
                    "status": "completed",
                    "batch": batch_identity,
                    "output": {
                        "path": str(output.resolve()),
                        "rows": rows,
                        "size_bytes": output.stat().st_size,
                    },
                },
            )
            records.append(one_record)
            outputs.append(output)
            print(
                f"孤雁临时序列 {batch_index + 1}/{len(batches)}："
                f"{len(batch_files)}只股票，{rows:,}条，"
                f"{one_record['seconds']:.1f}秒",
                flush=True,
            )

    summary = {
        "status": "completed",
        "cache_key": cache_key,
        "cache_dir": str(cache_dir.resolve()),
        "signature": signature,
        "stock_batch_size": stock_batch_size,
        "batches": len(batches),
        "completed_now": sum(item["status"] == "completed" for item in records),
        "skipped_existing": sum(item["status"] == "skipped" for item in records),
        "rows": int(sum(int(item["rows"]) for item in records)),
        "size_bytes": int(sum(int(item["size_bytes"]) for item in records)),
        "seconds": time.perf_counter() - started,
        "batch_results": records,
    }
    _atomic_write_json(cache_dir / "manifest.json", summary)
    return outputs, summary


def build_lone_goose_sequence_read_query(
    sequence_paths: Sequence[str | Path],
    start: str,
    end: str,
) -> str:
    start_time, end_exclusive = _date_bounds(start, end)
    source = _read_parquet_sql(sequence_paths)
    return f"""
        SELECT
            date,
            code,
            amount_sequence,
            selected_minute_count
        FROM {source}
        WHERE date >= DATE '{start_time:%Y-%m-%d}'
          AND date < DATE '{end_exclusive:%Y-%m-%d}'
        ORDER BY date, code
    """


def _build_lone_goose_files_batched(
    files: Sequence[Path],
    market_path: Path,
    output_dir: Path,
    start: str,
    end: str,
    temp_dir: Path,
    threads: int,
    memory_limit: str,
    block_size: int,
    force: bool,
    stock_batch_size: int,
) -> dict[str, object]:
    first_year = pd.Timestamp(start).year
    last_year = pd.Timestamp(end).year
    years = list(range(first_year, last_year + 1))
    pending_years = [
        year
        for year in years
        if force
        or not (output_dir / f"lone_goose_{year}.parquet").is_file()
        or (output_dir / f"lone_goose_{year}.parquet").stat().st_size == 0
    ]
    if not pending_years:
        return {
            "status": "completed",
            "mode": "stock_batches",
            "stock_batch_size": stock_batch_size,
            "sequence_cache": {"status": "not_needed"},
            "years": [{"year": year, "status": "skipped"} for year in years],
        }

    sequence_paths, cache_summary = _build_lone_goose_sequence_batches(
        files,
        market_path,
        start,
        end,
        temp_dir,
        threads,
        memory_limit,
        stock_batch_size,
        force,
    )
    results: list[dict[str, object]] = []
    merge_temp = temp_dir / "lone_goose_year_merge" / str(
        cache_summary["cache_key"]
    )
    merge_temp.mkdir(parents=True, exist_ok=True)
    with _connection(merge_temp, threads, memory_limit) as connection:
        for year in years:
            year_start = max(
                pd.Timestamp(start), pd.Timestamp(year=year, month=1, day=1)
            )
            year_end = min(
                pd.Timestamp(end), pd.Timestamp(year=year, month=12, day=31)
            )
            output = output_dir / f"lone_goose_{year}.parquet"
            if output.exists() and output.stat().st_size > 0 and not force:
                results.append({"year": year, "status": "skipped"})
                continue
            temporary = output.with_name(f"{output.stem}.tmp.parquet")
            temporary.unlink(missing_ok=True)
            year_started = time.perf_counter()
            query = build_lone_goose_sequence_read_query(
                sequence_paths,
                str(year_start.date()),
                str(year_end.date()),
            )
            reader = connection.execute(query).to_arrow_reader(4096)
            frames = (batch.to_pandas() for batch in reader)
            daily, diagnostics = _process_lone_goose_rows(frames, block_size)
            if daily.empty:
                results.append({"year": year, "status": "empty"})
                continue
            daily["date"] = pd.to_datetime(daily["date"])
            daily["code"] = daily["code"].astype("string")
            daily.to_parquet(temporary, compression="zstd", index=False)
            temporary.replace(output)
            one_result = {
                "year": year,
                "status": "completed",
                "rows": len(daily),
                "size_bytes": output.stat().st_size,
                "seconds": time.perf_counter() - year_started,
                "first_day": diagnostics[0] if diagnostics else None,
                "last_day": diagnostics[-1] if diagnostics else None,
            }
            results.append(one_result)
            print(
                f"孤雁出群 {year}：{len(daily):,} 条，"
                f"{one_result['seconds']:.1f} 秒",
                flush=True,
            )
    return {
        "status": "completed",
        "mode": "stock_batches",
        "stock_batch_size": stock_batch_size,
        "sequence_cache": cache_summary,
        "years": results,
    }


def _build_lone_goose_files_yearly(
    files: Sequence[Path],
    market_path: Path,
    output_dir: Path,
    start: str,
    end: str,
    temp_dir: Path,
    threads: int,
    memory_limit: str,
    block_size: int,
    force: bool,
) -> dict[str, object]:
    first_year = pd.Timestamp(start).year
    last_year = pd.Timestamp(end).year
    results: list[dict[str, object]] = []
    for year in range(first_year, last_year + 1):
        year_start = max(pd.Timestamp(start), pd.Timestamp(year=year, month=1, day=1))
        year_end = min(pd.Timestamp(end), pd.Timestamp(year=year, month=12, day=31))
        output = output_dir / f"lone_goose_{year}.parquet"
        if output.exists() and output.stat().st_size > 0 and not force:
            results.append({"year": year, "status": "skipped"})
            continue
        temp = output.with_name(f"{output.stem}.tmp.parquet")
        temp.unlink(missing_ok=True)
        started = time.perf_counter()
        with _connection(temp_dir, threads, memory_limit) as connection:
            query = build_lone_goose_query(
                files,
                market_path,
                str(year_start.date()),
                str(year_end.date()),
            )
            reader = connection.execute(query).to_arrow_reader(4096)
            frames = (batch.to_pandas() for batch in reader)
            daily, diagnostics = _process_lone_goose_rows(frames, block_size)
        if daily.empty:
            results.append({"year": year, "status": "empty"})
            continue
        daily["date"] = pd.to_datetime(daily["date"])
        daily["code"] = daily["code"].astype("string")
        daily.to_parquet(temp, compression="zstd", index=False)
        temp.replace(output)
        results.append(
            {
                "year": year,
                "status": "completed",
                "rows": len(daily),
                "size_bytes": output.stat().st_size,
                "seconds": time.perf_counter() - started,
                "first_day": diagnostics[0] if diagnostics else None,
                "last_day": diagnostics[-1] if diagnostics else None,
            }
        )
        print(
            f"孤雁出群 {year}：{len(daily):,} 条，"
            f"{time.perf_counter() - started:.1f} 秒",
            flush=True,
        )
    return {"status": "completed", "years": results}


def _build_lone_goose_files(
    files: Sequence[Path],
    market_path: Path,
    output_dir: Path,
    start: str,
    end: str,
    temp_dir: Path,
    threads: int,
    memory_limit: str,
    block_size: int,
    force: bool,
    stock_batch_size: int = 0,
) -> dict[str, object]:
    if stock_batch_size > 0:
        return _build_lone_goose_files_batched(
            files,
            market_path,
            output_dir,
            start,
            end,
            temp_dir,
            threads,
            memory_limit,
            block_size,
            force,
            stock_batch_size,
        )
    return _build_lone_goose_files_yearly(
        files,
        market_path,
        output_dir,
        start,
        end,
        temp_dir,
        threads,
        memory_limit,
        block_size,
        force,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=["all", "market", "stock", "lone-goose"], default="all"
    )
    parser.add_argument("--universe", choices=["base", "all"], default="all")
    parser.add_argument(
        "--minute-data-dir", type=Path, default=factor_store.DEFAULT_MINUTE_DIR
    )
    parser.add_argument(
        "--base-cache-glob", type=Path, default=factor_store.DEFAULT_SOURCES["base_daily"]
    )
    parser.add_argument("--market-file", default=str(DEFAULT_MARKET_FILE))
    parser.add_argument("--stock-dir", default=str(DEFAULT_STOCK_DIR))
    parser.add_argument("--lone-goose-dir", default=str(DEFAULT_LONE_GOOSE_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--temp-dir", default=str(factor_store.DEFAULT_TEMP_DIR))
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-12-31")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--worker-memory-limit", default="2GB")
    parser.add_argument("--correlation-block-size", type=int, default=512)
    parser.add_argument(
        "--lone-goose-stock-batch-size",
        type=int,
        default=40,
        help="孤雁计算每次读取的股票数；设为0可使用原来的逐年全股票方式。",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1 or args.threads < 1:
        raise ValueError("进程数和线程数必须大于0。")
    if args.limit is not None and args.limit < 1:
        raise ValueError("测试股票数必须大于0。")
    if args.lone_goose_stock_batch_size < 0:
        raise ValueError("孤雁股票批量不能小于0。")

    market_path = _assert_workspace_path(Path(args.market_file))
    stock_dir = _assert_workspace_path(Path(args.stock_dir))
    lone_goose_dir = _assert_workspace_path(Path(args.lone_goose_dir))
    manifest_path = _assert_workspace_path(Path(args.manifest))
    temp_dir = _assert_workspace_path(Path(args.temp_dir))
    for path in [market_path.parent, stock_dir, lone_goose_dir, manifest_path.parent, temp_dir]:
        path.mkdir(parents=True, exist_ok=True)

    minute_data_dir = Path(args.minute_data_dir).resolve()
    base_cache_glob = Path(args.base_cache_glob).resolve()
    files = _source_files(args.universe, minute_data_dir, base_cache_glob)
    if args.limit:
        files = files[: args.limit]
    if not files:
        raise FileNotFoundError("没有找到可处理的分钟文件。")

    manifest: dict[str, object] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
    manifest.update(
        {
            "definition_version": 3,
            "universe": args.universe,
            "minute_data_dir": str(minute_data_dir),
            "base_cache_glob": str(base_cache_glob),
            "source_files": len(files),
            "start": args.start,
            "end": args.end,
            "shared_market_file": str(market_path),
            "trial_parameters": TRIAL_PARAMETERS,
            "updated_at": pd.Timestamp.now().isoformat(),
        }
    )

    if args.stage in {"all", "market"}:
        print("生成全市场分钟小表...", flush=True)
        manifest["market"] = _build_market_file(
            files,
            market_path,
            args.start,
            args.end,
            temp_dir,
            args.threads,
            args.memory_limit,
            args.force,
        )
    if args.stage in {"all", "stock"}:
        _require_market_columns(
            market_path,
            {"datetime", "market_amount_total", "market_day_index"},
        )
        print("按股票生成七八月分钟日值...", flush=True)
        manifest["stock"] = _build_stock_files(
            files,
            market_path,
            stock_dir,
            args.start,
            args.end,
            temp_dir,
            args.workers,
            args.worker_memory_limit,
            args.force,
        )
    if args.stage in {"all", "lone-goose"}:
        _require_market_columns(
            market_path,
            {"date", "datetime", "is_non_differentiation_pop"},
        )
        print("按年生成孤雁出群每日值...", flush=True)
        manifest["lone_goose"] = _build_lone_goose_files(
            files,
            market_path,
            lone_goose_dir,
            args.start,
            args.end,
            temp_dir,
            args.threads,
            args.memory_limit,
            args.correlation_block_size,
            args.force,
            args.lone_goose_stock_batch_size,
        )

    temp_manifest = manifest_path.with_suffix(".tmp.json")
    temp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
    )
    temp_manifest.replace(manifest_path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
