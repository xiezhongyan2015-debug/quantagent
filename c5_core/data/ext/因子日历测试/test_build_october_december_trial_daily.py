# -*- coding: utf-8 -*-

import math

import numpy as np
import pandas as pd
from scipy.special import gamma
from scipy.stats import t as student_t

import build_october_december_trial_daily as target


def test_t_active_share_uses_log_return_sample_sigma_and_amount_weights() -> None:
    returns = np.array([[0.01, -0.02, 0.03, 0.00]], dtype=float)
    amount = np.array([[1.0, 2.0, 3.0, 4.0]], dtype=float)
    sigma = np.std(returns[0], ddof=1)
    expected = np.sum(
        amount[0] * student_t.cdf(returns[0] / sigma, df=5)
    ) / amount[0].sum()
    actual = target.compute_t_active_share(returns, amount)
    np.testing.assert_allclose(actual[0], expected, rtol=1e-12)


def test_consistent_volume_shares_split_buy_and_sell() -> None:
    open_5m = np.array([[10.0, 10.0, 10.0, 10.0]])
    high_5m = np.array([[11.0, 11.0, 11.0, 11.0]])
    low_5m = np.array([[9.0, 9.0, 9.0, 9.0]])
    close_5m = np.array([[10.1, 9.9, 10.3, 10.0]])
    volume_5m = np.array([[10.0, 20.0, 30.0, 40.0]])
    total, buy, sell = target.compute_consistent_volume_shares(
        open_5m,
        high_5m,
        low_5m,
        close_5m,
        volume_5m,
        alpha=0.10,
    )
    np.testing.assert_allclose(total, [0.70])
    np.testing.assert_allclose(buy, [0.10])
    np.testing.assert_allclose(sell, [0.20])


def _manual_js(log_return: np.ndarray) -> float:
    count = len(log_return)
    simple = np.expm1(log_return)
    rv = np.sum(log_return**2)
    swv = 2.0 * np.sum(simple - log_return)
    mu_one = math.sqrt(2.0 / math.pi)
    bipower = (
        mu_one**-2
        * count
        / (count - 1.0)
        * np.sum(np.abs(log_return[1:]) * np.abs(log_return[:-1]))
    )
    product_sum = sum(
        np.prod(np.abs(log_return[start : start + 6]))
        for start in range(count - 5)
    )
    mu_six = 2.0**3 * gamma(3.5) / math.sqrt(math.pi)
    omega = (
        mu_six
        / 9.0
        * count**3
        * mu_one**-6
        / (count - 5.0)
        * product_sum
    )
    return count * bipower / math.sqrt(omega) * (1.0 - rv / swv)


def test_jiang_oomen_uses_p6_omega_and_signed_jump_direction() -> None:
    positive_returns = np.linspace(-0.001, 0.001, 48)
    positive_returns[20] = 0.03
    negative_returns = np.linspace(-0.001, 0.001, 48)
    negative_returns[20] = -0.03
    close = np.vstack(
        [
            10.0 * np.exp(np.cumsum(positive_returns)),
            10.0 * np.exp(np.cumsum(negative_returns)),
        ]
    )
    actual = target.compute_jiang_oomen_daily(close, np.array([10.0, 10.0]))
    np.testing.assert_allclose(
        actual["statistic"],
        [_manual_js(positive_returns), _manual_js(negative_returns)],
        rtol=1e-10,
    )
    np.testing.assert_array_equal(actual["direction"], [1.0, -1.0])
    np.testing.assert_allclose(actual["positive_jump_return"], [0.03, 0.0])
    np.testing.assert_allclose(actual["negative_jump_return"], [0.0, -0.03])
    np.testing.assert_allclose(
        actual["nonjump_return"],
        [positive_returns.sum() - 0.03, negative_returns.sum() + 0.03],
    )


def test_adjusted_amount_uses_only_prior_same_minute_days() -> None:
    amount = np.array(
        [
            [1.0, 4.0, 8.0],
            [3.0, 6.0, 12.0],
            [5.0, 8.0, 16.0],
        ]
    )
    actual = target.adjusted_amount_zscore(amount, history_days=2)
    expected_mean = amount[:2].mean(axis=0)
    expected_std = amount[:2].std(axis=0, ddof=0)
    assert np.isnan(actual[:2]).all()
    np.testing.assert_allclose(
        actual[2], (amount[2] - expected_mean) / expected_std
    )


def test_build_daily_frame_keeps_complete_days_and_all_trial_columns() -> None:
    rows = []
    dates = pd.bdate_range("2026-01-05", periods=21)
    for day, date in enumerate(dates):
        previous = 10.0 + day * 0.02
        for position, minute_of_day in enumerate(target.TRADING_MINUTES):
            timestamp = date + pd.Timedelta(minutes=int(minute_of_day))
            minute_return = 0.0002 * np.sin(position / 9.0 + day / 5.0)
            close = previous * np.exp(minute_return)
            rows.append(
                {
                    "datetime": timestamp,
                    "minute_open": previous,
                    "minute_high": max(previous, close) + 0.01,
                    "minute_low": min(previous, close) - 0.01,
                    "minute_close": close,
                    "minute_volume": 1000.0 + position,
                    "minute_amount": (1000.0 + position) * close * (1 + day * 0.001),
                }
            )
            previous = close
    daily = target.build_daily_frame(pd.DataFrame(rows), "sh600000")
    assert daily.columns.tolist() == target.OUTPUT_COLUMNS
    assert len(daily) == 21
    assert daily["date"].max() == dates[-1]
    assert daily["j10_d10_t_active_share_daily_trial"].notna().all()
    assert daily["j11_d04_adj_absret_lag_amount_corr_daily_trial"].iloc[-1:].notna().all()
    assert daily["formula_mode"].eq("trial").all()
