# -*- coding: utf-8 -*-

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import build_shared_market_minute_stats as shared_market
import build_trial_minute_features as target


def test_vsa_trial_uses_strictly_more_than_half_volume() -> None:
    close = np.array([[10.0, 10.0, 11.0, 12.0]])
    volume = np.array([[40.0, 10.0, 30.0, 20.0]])
    result = target.compute_vsa_low_close_ratio(close, volume)
    np.testing.assert_allclose(result, [(12.0 - 10.0) / 12.0])


def test_abnormal_volume_uses_previous_valid_day_same_block() -> None:
    volume = np.vstack(
        [
            np.arange(1.0, 49.0),
            2.0 * np.arange(1.0, 49.0),
            3.0 * np.arange(1.0, 49.0),
        ]
    )
    result = target.compute_intraday_abnormal_volume(volume)
    assert np.isnan(result[0])
    np.testing.assert_allclose(result[1:], [2.0, 1.5])


def test_emd_and_hp_trial_values_are_finite() -> None:
    minute = np.arange(target.MINUTES_PER_DAY, dtype=np.float64)
    close = (
        10.0
        + 0.01 * minute
        + 0.08 * np.sin(2.0 * np.pi * minute / 5.0)
        + 0.2 * np.sin(2.0 * np.pi * minute / 30.0)
    )
    snr = target.compute_intraday_snr(close[None, :])
    assert np.isfinite(snr[0])

    five_minute = np.arange(20 * target.FIVE_MINUTE_BLOCKS, dtype=np.float64)
    close_5m = (
        10.0
        + 0.0005 * five_minute
        + 0.05 * np.sin(2.0 * np.pi * five_minute / 17.0)
    ).reshape(20, target.FIVE_MINUTE_BLOCKS)
    resiliency = target.compute_high_frequency_resiliency(close_5m)
    assert np.isnan(resiliency[:19]).all()
    assert np.isfinite(resiliency[19])
    assert resiliency[19] > 0


def test_naive_active_share_is_half_for_no_price_change() -> None:
    close = np.full((1, target.MINUTES_PER_DAY), 10.0)
    amount = np.ones_like(close)
    result = target.compute_naive_active_share(close, np.array([10.0]), amount)
    np.testing.assert_allclose(result, [0.5])


def test_market_query_builds_one_shared_stat_table(tmp_path: Path) -> None:
    paths = []
    timestamps = pd.date_range("2025-01-02 09:31", periods=8, freq="min")
    for index, code in enumerate(["sh600000", "sh600004", "sh600006"], start=1):
        returns = np.array([0.001, 0.002, -0.001, 0.003, -0.002, 0.001, 0.004, -0.003])
        returns = returns * index
        close = 10.0 * np.cumprod(1.0 + returns)
        path = tmp_path / f"{code}.parquet"
        pd.DataFrame(
            {
                "datetime": timestamps,
                "open": np.r_[10.0, close[:-1]],
                "close": close,
                "amount": np.arange(1.0, len(timestamps) + 1.0),
            }
        ).to_parquet(path, index=False)
        paths.append(path)
    with duckdb.connect() as connection:
        result = connection.execute(
            target.build_market_query(paths, "2025-01-02", "2025-01-02")
        ).fetchdf()
    assert result.columns.tolist() == list(shared_market.OUTPUT_COLUMNS)
    assert len(result) == len(timestamps)
    assert result["return_stock_count"].eq(3).all()
    assert result["market_mean_return"].notna().all()


def test_daily_frame_produces_all_nine_trial_inputs() -> None:
    days = pd.bdate_range("2025-01-02", periods=21)
    rows = []
    for day_index, day in enumerate(days):
        minute_times = [
            day + pd.Timedelta(minutes=int(value))
            for value in target.TRADING_MINUTES
        ]
        position = np.arange(target.MINUTES_PER_DAY, dtype=np.float64)
        close = (
            10.0
            + 0.01 * day_index
            + 0.0005 * position
            + 0.006 * np.sin(position * 1.7 + day_index)
            + 0.02 * np.sin(position / 7.0 + day_index)
        )
        previous = np.r_[close[0], close[:-1]]
        simple = close / previous - 1.0
        for minute, timestamp in enumerate(minute_times):
            rows.append(
                {
                    "date": day,
                    "datetime": timestamp,
                    "minute_open": previous[minute],
                    "minute_high": max(previous[minute], close[minute]) + 0.01,
                    "minute_low": min(previous[minute], close[minute]) - 0.01,
                    "minute_close": close[minute],
                    "minute_volume": 1000.0 + minute + day_index,
                    "minute_amount": (1000.0 + minute + day_index) * close[minute],
                    "market_mean_return": simple[minute] * 0.8,
                    "market_std_return_pop": abs(simple[minute]) + 0.001,
                    "positive_return_mean": max(simple[minute] * 0.8, 0.0001),
                    "negative_return_mean": min(simple[minute] * 0.8, -0.0001),
                    "return_stock_count": 3,
                    "deviation_skew": 0.5,
                }
            )
    result = target.build_daily_frame(pd.DataFrame(rows), "sh600000", 3)
    assert result.columns.tolist() == target.OUTPUT_COLUMNS
    assert len(result) == len(days)
    assert result["trial_cache_version"].eq(target.CACHE_VERSION).all()
    assert result["trial_min_market_stocks"].eq(3).all()
    assert result["formula_mode"].eq("trial").all()
    assert result["formula_label"].eq("试算").all()
    direct_columns = [
        "m15_vsa_low_close_gap_trial",
        "a22_intraday_snr_trial",
        "j06_d02_jump_indicator_trial",
        "j06_d20_rm_min_trial",
        "j06_d20_std_min_trial",
        "j09_d07_brave_peak_daily_trial",
        "j09_d11_naive_active_share_trial",
        "j09_d19_skewed_momentum_daily_trial",
    ]
    assert result[direct_columns].notna().all().all()
    assert result["may13_intraday_abnormal_volume_trial"].notna().sum() == 20
    assert result["j06_d07_high_frequency_resiliency_trial"].notna().sum() == 2
