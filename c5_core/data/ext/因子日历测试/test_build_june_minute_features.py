# -*- coding: utf-8 -*-
"""六月量价特征的定向检查。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import build_june_minute_features as target


EXPECTED_OUTPUT_COLUMNS = [
    "date",
    "code",
    "structure_reversal_21d_10m",
    "fuzzy_amount_ratio_daily",
    "wait_rescue_follow_daily",
    "downside_realized_vol_5m",
    "intraday_cvar_5pct_daily",
    "intraday_volatility_1m",
    "pv_corr_daily",
    *[f"atv_5m_{index:02d}" for index in range(48)],
]


def make_day(date: pd.Timestamp, day_number: int = 0) -> pd.DataFrame:
    """生成一天完整的240条分钟数据。"""
    minute_number = np.arange(240, dtype=float)
    close = (
        10.0
        + day_number * 0.02
        + minute_number * 0.0005
        + np.sin(minute_number / 11.0) * 0.003
    )
    volume = 100.0 + ((minute_number + day_number * 7) % 37)
    timestamps = [
        pd.Timestamp(date).normalize() + pd.Timedelta(minutes=int(minute))
        for minute in target.TRADING_MINUTES
    ]
    return pd.DataFrame(
        {
            "datetime": timestamps,
            "qfq_open": close - 0.001,
            "qfq_high": close + 0.003,
            "qfq_low": close - 0.003,
            "qfq_close": close,
            "volume": volume,
            "amount": close * volume,
            "float_shares": np.full(240, 1_000_000.0),
            "total_shares": np.full(240, 1_200_000.0),
            "qfq_ratio": np.ones(240, dtype=float),
            "source_id": np.ones(240, dtype=np.uint8),
        }
    )


def test_structured_reversal_uses_complete_504_segments_and_exact_weights() -> None:
    volume = np.arange(1.0, 505.0, dtype=float).reshape(21, 24)
    returns = np.concatenate(
        [np.full(50, 0.02, dtype=float), np.full(454, -0.01, dtype=float)]
    ).reshape(21, 24)

    flat_return = returns.reshape(-1)
    flat_volume = volume.reshape(-1)
    order = np.argsort(flat_volume, kind="stable")
    low_count = int(np.floor(flat_volume.size * 0.10))
    low = order[:low_count]
    high = order[low_count:]
    low_return = np.sum(flat_return[low] / flat_volume[low]) / np.sum(
        1.0 / flat_volume[low]
    )
    high_return = np.sum(flat_return[high] * flat_volume[high]) / np.sum(
        flat_volume[high]
    )
    expected = high_return - low_return

    result = target._structured_reversal_21d_10m(returns, volume)

    assert result.shape == (21,)
    assert np.isnan(result[:20]).all()
    # 排名百分位不高于10%：504段中固定取成交量最低的50段。
    assert low_count == 50
    assert result[20] == pytest.approx(expected)


def test_structured_reversal_rejects_incomplete_21_day_window() -> None:
    returns = np.full((22, 24), 0.001, dtype=float)
    volume = np.tile(np.arange(1, 25, dtype=float), (22, 1))

    missing_return = returns.copy()
    missing_return[0, 0] = np.nan
    missing_result = target._structured_reversal_21d_10m(missing_return, volume)
    assert np.isnan(missing_result[20])
    assert np.isfinite(missing_result[21])

    zero_volume = volume.copy()
    zero_volume[0, 0] = 0.0
    zero_result = target._structured_reversal_21d_10m(returns, zero_volume)
    assert np.isnan(zero_result[20])
    assert np.isfinite(zero_result[21])


def test_fuzzy_amount_ratio_uses_two_five_point_population_stds() -> None:
    returns = np.array(
        [
            [
                5.00,
                0.01,
                -0.03,
                0.02,
                0.04,
                -0.02,
                0.06,
                -0.01,
                0.08,
                -0.04,
                0.05,
                -0.07,
                5.00,
            ]
        ],
        dtype=float,
    )
    amount = np.arange(10.0, 140.0, 10.0, dtype=float).reshape(1, 13)
    amount[0, 0] = 100_000.0
    amount[0, -1] = 200_000.0

    kept_returns = returns.copy()
    kept_returns[:, 0] = np.nan
    kept_returns[:, -1] = np.nan
    first_std = np.full(13, np.nan, dtype=float)
    for end in range(4, 13):
        block = kept_returns[0, end - 4 : end + 1]
        if np.isfinite(block).all():
            first_std[end] = np.std(block, ddof=0)
    fuzziness = np.full(13, np.nan, dtype=float)
    for end in range(4, 13):
        block = first_std[end - 4 : end + 1]
        if np.isfinite(block).all():
            fuzziness[end] = np.std(block, ddof=0)
    usable = np.isfinite(fuzziness)
    usable[[0, -1]] = False
    fuzzy_minutes = usable & (fuzziness > np.mean(fuzziness[usable]))
    expected = np.mean(amount[0, fuzzy_minutes]) / np.mean(amount[0, 1:-1])

    result = target._daily_fuzzy_amount_ratio(returns, amount)

    assert np.flatnonzero(usable).tolist() == [9, 10, 11]
    assert np.flatnonzero(fuzzy_minutes).tolist() == [11]
    assert result[0] == pytest.approx(expected)


def test_fuzzy_amount_ratio_handles_missing_and_no_fuzzy_minute() -> None:
    returns = np.arange(10, dtype=float).reshape(1, 10) / 100.0
    amount = np.ones((1, 10), dtype=float)

    missing = returns.copy()
    missing[0, 4] = np.nan
    assert np.isnan(target._daily_fuzzy_amount_ratio(missing, amount)[0])

    constant = np.zeros((1, 10), dtype=float)
    assert np.isnan(target._daily_fuzzy_amount_ratio(constant, amount)[0])


def test_wait_rescue_uses_clock_gap_sessions_and_minute_16_start() -> None:
    volume = np.ones((1, 240), dtype=float)
    volume[0, 14] = 1_000.0
    special = {
        15: 100.0,
        18: 90.0,
        21: 80.0,
        29: 70.0,
        119: 60.0,
        120: 50.0,
        150: 45.0,
        234: 40.0,
        235: 30.0,
        236: 20.0,
    }
    for position, value in special.items():
        volume[0, position] = value

    result = target._waiting_rescue_follow_coefficient(volume)

    # 18、235、236距前一个保留点不足5分钟；119后面没有同一时段的5分钟。
    expected_parts = [94.0 / 100.0, 5.0 / 80.0, 5.0 / 70.0]
    expected_parts.extend([5.0 / 50.0, 5.0 / 45.0, 53.0 / 40.0])
    assert result[0] == pytest.approx(np.mean(expected_parts))

    missing_follower = volume.copy()
    missing_follower[0, 16] = np.nan
    missing_result = target._waiting_rescue_follow_coefficient(missing_follower)
    assert missing_result[0] == pytest.approx(np.mean(expected_parts[1:]))


def test_wait_rescue_keeps_points_exactly_five_clock_minutes_apart() -> None:
    volume = np.ones((1, 240), dtype=float)
    volume[0, 15] = 100.0
    volume[0, 20] = 90.0

    result = target._waiting_rescue_follow_coefficient(volume, top_count=2)

    expected = np.mean([94.0 / 100.0, 5.0 / 90.0])
    assert result[0] == pytest.approx(expected)


def test_downside_realized_volatility_sums_only_nonpositive_returns() -> None:
    returns = np.array(
        [
            [0.10, -0.20, 0.00, -0.30],
            [0.10, np.nan, -0.20, -0.30],
        ],
        dtype=float,
    )

    result = target._downside_realized_volatility_5m(returns)

    assert result[0] == pytest.approx(0.20**2 + 0.30**2)
    assert np.isnan(result[1])


def test_cvar_approximation_uses_left_five_percent_and_volume_weights() -> None:
    # 原图两条公式不能直接衔接；这里只固定当前采用的近似算法。
    returns = np.zeros((2, 40), dtype=float)
    returns[0, :2] = [-0.40, -0.20]
    returns[1, :] = np.nan
    volume = np.ones((2, 40), dtype=float)
    volume[0, 1] = 3.0

    result = target._left_tail_volume_weighted_cvar(returns, volume)

    assert result[0] == pytest.approx((-0.40 * 1.0 - 0.20 * 3.0) / 4.0)
    assert np.isnan(result[1])

    with pytest.raises(ValueError):
        target._left_tail_volume_weighted_cvar(returns, volume, alpha=0.0)
    with pytest.raises(ValueError):
        target._left_tail_volume_weighted_cvar(returns, volume, alpha=1.0)


def test_intraday_std_and_price_volume_correlation_missing_rules() -> None:
    returns = np.array(
        [[0.01, -0.01, 0.03, -0.03], [0.01, np.nan, 0.03, -0.03]],
        dtype=float,
    )
    std_result = target._intraday_return_std(returns)
    assert std_result[0] == pytest.approx(np.std(returns[0], ddof=0))
    assert np.isnan(std_result[1])

    close = np.array(
        [[1.0, 2.0, 3.0, 4.0], [1.0, np.nan, 3.0, 4.0], [1.0, np.nan, np.nan, np.nan]],
        dtype=float,
    )
    volume = np.array(
        [[4.0, 3.0, 2.0, 1.0], [4.0, 3.0, 2.0, 1.0], [4.0, 3.0, 2.0, 1.0]],
        dtype=float,
    )
    corr_result = target._daily_price_volume_correlation(close, volume)
    assert corr_result[0] == pytest.approx(-1.0)
    assert corr_result[1] == pytest.approx(-1.0)
    assert np.isnan(corr_result[2])


def test_atv_compares_each_segment_with_previous_trading_day() -> None:
    volume = np.vstack(
        [
            np.arange(1.0, 49.0),
            np.arange(1.0, 49.0) * 2.0,
            np.arange(1.0, 49.0) * 3.0,
        ]
    )
    volume[0, 0] = 0.0
    volume[1, 1] = np.nan

    result = target._abnormal_trading_volume_5m(volume)

    assert result.shape == (3, 48)
    assert np.isnan(result[0]).all()
    assert np.isnan(result[1, 0])
    assert np.isnan(result[1, 1])
    assert result[1, 2:] == pytest.approx(np.full(46, 2.0))
    assert result[2, 0] == pytest.approx(1.5)
    assert np.isnan(result[2, 1])
    assert result[2, 2:] == pytest.approx(np.full(46, 1.5))


def test_output_columns_and_full_daily_frame() -> None:
    assert target.OUTPUT_COLUMNS == EXPECTED_OUTPUT_COLUMNS
    assert target.ATV_COLUMNS == EXPECTED_OUTPUT_COLUMNS[-48:]

    dates = pd.bdate_range("2025-01-02", periods=22)
    frame = pd.concat(
        [make_day(date, day_number) for day_number, date in enumerate(dates)],
        ignore_index=True,
    )
    result = target.build_daily_frame(frame, "SH.600000")

    assert result.columns.tolist() == EXPECTED_OUTPUT_COLUMNS
    assert result.shape == (22, len(EXPECTED_OUTPUT_COLUMNS))
    assert result["code"].eq("sh.600000").all()
    assert all(result[column].dtype == np.dtype("float32") for column in EXPECTED_OUTPUT_COLUMNS[2:])
    assert result["structure_reversal_21d_10m"].iloc[:20].isna().all()
    assert np.isfinite(result["structure_reversal_21d_10m"].iloc[20:]).all()
    assert result.loc[0, target.ATV_COLUMNS].isna().all()
    assert np.isfinite(result.loc[1, target.ATV_COLUMNS].to_numpy(dtype=float)).all()
