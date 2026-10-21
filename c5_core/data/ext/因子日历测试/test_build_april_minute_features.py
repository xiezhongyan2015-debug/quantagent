# -*- coding: utf-8 -*-
"""四月分钟因子每日数据的定向检查。"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import build_april_minute_features as target


def make_day(date: str, base_price: float = 10.0) -> pd.DataFrame:
    day = pd.Timestamp(date)
    minute = np.arange(target.MINUTES_PER_DAY, dtype=np.float64)
    close = base_price + minute * 0.001
    return pd.DataFrame(
        {
            "datetime": [
                day + pd.Timedelta(minutes=int(value))
                for value in target.TRADING_MINUTES
            ],
            "qfq_open": close - 0.01,
            "qfq_high": close + 0.02,
            "qfq_low": close - 0.02,
            "qfq_close": close,
            "volume": np.full(target.MINUTES_PER_DAY, 100.0),
            "amount": close * 50.0,
            "float_shares": np.full(target.MINUTES_PER_DAY, 1_000_000.0),
            "total_shares": np.full(target.MINUTES_PER_DAY, 2_000_000.0),
            "qfq_ratio": np.full(target.MINUTES_PER_DAY, 2.0),
            "source_id": np.ones(target.MINUTES_PER_DAY, dtype=np.uint8),
        }
    )


def test_daily_frame_keeps_complete_days_and_uses_exact_tail_minutes():
    complete = make_day("2025-04-01")
    tail_start = target.MINUTE_TO_POSITION[14 * 60 + 31]
    complete.loc[tail_start:, "volume"] = 200.0
    complete["amount"] = complete["qfq_close"] * complete["volume"]
    incomplete = make_day("2025-04-02").iloc[:-1]

    daily = target.build_daily_frame(
        pd.concat([complete, incomplete], ignore_index=True), "SZ000001"
    )

    expected = (30.0 * 200.0) / (210.0 * 100.0 + 30.0 * 200.0)
    assert daily.columns.tolist() == target.OUTPUT_COLUMNS
    assert daily["date"].tolist() == [pd.Timestamp("2025-04-01")]
    assert daily.loc[0, "code"] == "sz000001"
    assert np.isclose(daily.loc[0, "tail_volume_share_daily"], expected)
    expected_corr = np.corrcoef(complete["qfq_close"], complete["volume"])[0, 1]
    assert np.isclose(daily.loc[0, "pv_corr_daily"], expected_corr)


def test_small_down_jump_subtracts_alpha_four_large_down_part():
    values = np.array(
        [[0.002, -0.001, 0.0015, -0.002, 0.001, -0.025] * 8],
        dtype=np.float64,
    )
    power = np.abs(values) ** (2.0 / 3.0)
    mu = 2.0 ** (1.0 / 3.0) * math.gamma(5.0 / 6.0) / math.sqrt(math.pi)
    integrated = mu ** -3 * np.sum(
        power[:, 2:] * power[:, 1:-1] * power[:, :-2], axis=1
    )
    downside = np.sum(np.where(values < 0, values * values, 0.0), axis=1)
    rvjn = np.maximum(downside - 0.5 * integrated, 0.0)
    threshold = 4.0 * (1.0 / 48.0) ** 0.49 * np.sqrt(integrated)
    large = np.sum(
        np.where(values < -threshold[:, None], values * values, 0.0), axis=1
    )
    expected = np.maximum(rvjn - np.minimum(rvjn, large), 0.0)

    result = target._small_down_jump_variation(values, alpha=4.0)

    assert np.allclose(result, expected)
    assert np.all(result >= 0)
    assert np.all(result <= rvjn)


def test_noon_ancient_tree_matches_ordinary_least_squares():
    rng = np.random.default_rng(17)
    volume = 10_000.0 + np.cumsum(
        rng.normal(0.0, 20.0, size=target.MINUTES_PER_DAY)
    )
    difference = np.diff(volume)
    x = np.column_stack(
        [difference[5 - lag : len(difference) - lag] for lag in range(6)]
    )
    y = (
        0.0003
        + x @ np.array([2.0, -1.0, 0.8, 0.0, -0.6, 0.4]) * 1e-6
        + rng.normal(0.0, 0.0001, size=len(x))
    )
    returns = np.full(target.MINUTES_PER_DAY, np.nan)
    returns[6:] = y

    absolute_t, all_f = target._noon_ancient_tree_statistics(
        returns[None, :], volume[None, :]
    )

    design = np.column_stack([np.ones(len(y)), x])
    beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ beta
    degrees = len(y) - design.shape[1]
    square_sum = float(residual @ residual)
    covariance = square_sum / degrees * np.linalg.inv(design.T @ design)
    expected_t = abs(beta[0]) / math.sqrt(covariance[0, 0])
    restricted = y - np.mean(y)
    restricted_square_sum = float(restricted @ restricted)
    expected_f = ((restricted_square_sum - square_sum) / 6.0) / (
        square_sum / degrees
    )
    assert np.isclose(absolute_t[0], expected_t, rtol=1e-9)
    assert np.isclose(all_f[0], expected_f, rtol=1e-9)


def test_burst_correlation_joins_lunch_but_never_crosses_close():
    days = 40
    volume = np.full((days, target.MINUTES_PER_DAY), 10.0)
    amount = np.full((days, target.MINUTES_PER_DAY), 5.0)
    # 11:30后的下一交易分钟必须取13:01。
    volume[20, 119] = 30.0
    amount[20, 119] = 1.0
    amount[20, 120] = 2.0
    volume[21, 118] = 30.0
    amount[21, 118] = 3.0
    amount[21, 119] = 6.0
    # 15:00虽是喷发点，也不能接到次日09:31。
    volume[22, 239] = 30.0
    amount[22, 239] = 100.0
    amount[23, 0] = -100.0

    result = target._rolling_burst_amount_correlation(volume, amount)

    assert np.isnan(result[:21]).all()
    assert np.isclose(result[21], 1.0)
    assert np.isclose(result[39], 1.0)


def test_burst_correlation_rejects_window_with_bad_amount_day():
    days = 41
    volume = np.full((days, target.MINUTES_PER_DAY), 10.0)
    amount = np.full((days, target.MINUTES_PER_DAY), 5.0)
    for day, minute in ((20, 10), (21, 11), (22, 12)):
        volume[day, minute] = 30.0
        amount[day, minute] = float(day)
        amount[day, minute + 1] = float(day * 2)
    good_days = np.ones(days, dtype=bool)
    good_days[25] = False

    result = target._rolling_burst_amount_correlation(
        volume, amount, valid_days=good_days
    )

    assert np.isnan(result[39])
    assert np.isnan(result[40])


def test_process_writes_only_april_columns_and_skips_existing(tmp_path: Path):
    input_path = tmp_path / "sz000001.parquet"
    output_dir = tmp_path / "output"
    pq.write_table(
        pa.Table.from_pandas(make_day("2025-04-01"), preserve_index=False),
        input_path,
    )

    first = target.process_one_stock(
        input_path, output_dir, "2025-04-01", "2025-04-30"
    )
    output_path = output_dir / "sz000001.parquet"
    saved = pq.read_table(output_path).to_pandas()
    modified_time = output_path.stat().st_mtime_ns
    second = target.process_one_stock(
        input_path, output_dir, "2025-04-01", "2025-04-30"
    )

    assert first == {"code": "sz000001", "status": "ok", "days": 1}
    assert saved.columns.tolist() == target.OUTPUT_COLUMNS
    assert second["status"] == "skipped"
    assert output_path.stat().st_mtime_ns == modified_time
    assert not list(output_dir.glob(".*.tmp"))


def test_default_output_directory_is_april_cache():
    expected = (
        target.BASE_DIR
        / "april_factor_results"
        / "cache"
        / "april_daily_by_stock"
    )
    assert target.DEFAULT_OUTPUT_DIR == expected
