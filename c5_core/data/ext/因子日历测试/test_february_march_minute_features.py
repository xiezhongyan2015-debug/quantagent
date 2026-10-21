# -*- coding: utf-8 -*-

import numpy as np

import build_january_minute_cache as target
from test_build_january_minute_cache import make_day


def test_realized_kurtosis_matches_card_formula():
    values = np.array([[1.0, -1.0, 1.0, -1.0]])
    assert np.isclose(target._realized_kurtosis(values)[0], 1.0)


def test_upside_volatility_share_uses_squared_positive_returns():
    values = np.array([[1.0, -2.0, 3.0, -4.0]])
    expected = (1.0**2 + 3.0**2) / (1.0**2 + 2.0**2 + 3.0**2 + 4.0**2)
    assert np.isclose(target._upside_volatility_share(values)[0], expected)


def test_large_up_jump_never_exceeds_total_up_jump():
    values = np.array([[0.01, -0.005, 0.002, 0.03, -0.004] * 10], dtype=float)
    rvjp, rvjn, large_up = target._jump_variations(values)
    assert rvjp[0] >= 0
    assert rvjn[0] >= 0
    assert 0 <= large_up[0] <= rvjp[0]


def test_weighted_skew_is_zero_for_symmetric_prices_and_equal_volume():
    close = np.array([[1.0, 2.0, 3.0]])
    volume = np.ones_like(close)
    assert np.isclose(target._weighted_skew(close, volume)[0], 0.0)


def test_morning_fog_is_unchanged_when_volume_units_change():
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 0.001, size=(3, target.MINUTES_PER_DAY))
    volume = rng.lognormal(10.0, 0.5, size=(3, target.MINUTES_PER_DAY))
    first = target._morning_fog_tvalue_dispersion(returns, volume)
    second = target._morning_fog_tvalue_dispersion(returns, volume * 100.0)
    assert np.isfinite(first).all()
    assert np.allclose(first, second, rtol=1e-9, atol=1e-12)


def test_daily_frame_contains_february_and_march_inputs():
    frame = make_day("2020-01-02", base_price=10.0)
    daily = target.build_daily_frame(frame, "sz000001")
    expected = {
        "rvjp_5m",
        "large_up_jump_5m",
        "golden_split_return_daily",
        "intraday_jumpiness_all_daily",
        "intraday_jumpiness_ex_open_close_daily",
        "realized_kurtosis_1m",
        "amount_share_entropy_5m",
        "upside_vol_share_5m",
        "weighted_skew_5m",
        "morning_fog_daily",
        "burst_follow_amount_daily",
        "burst_amount_daily",
    }
    assert expected.issubset(daily.columns)
