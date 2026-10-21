# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

import run_may_backtest as target


def test_metadata_has_sixteen_candidates_and_nine_calculated_codes():
    calculated = {
        "MAY04_B_VOLUME_DISTRIBUTION",
        "MAY05_SALIENCE_RETURN",
        "MAY07_LARGE_JUMP_ASYMMETRY_5M",
        "MAY10_SINGLE_AMOUNT_SHARE_ENTROPY_5M",
        "MAY14_PANIC_20D",
        "MAY17_REALIZED_VOLATILITY_5M",
        "MAY19_NIGHT_SLEEP_FROST_ROAD_20D",
        "MAY23_SAME_TIME_PEAK_RIDGE_CORR_20D",
        "MAY27_IDIOSYNCRATIC_INTRADAY_RETURN_VOL_20D",
    }
    assert len(target.FACTOR_META) == 16
    assert sum(
        meta["signal_frequency"] != "unavailable"
        for meta in target.FACTOR_META.values()
    ) == 9
    assert calculated == {
        code
        for code, meta in target.FACTOR_META.items()
        if meta["signal_frequency"] != "unavailable"
    }


def test_salience_return_is_written_only_at_month_end():
    dates = pd.bdate_range("2025-05-01", "2025-06-30")
    first = np.linspace(10.0, 12.0, len(dates))
    second = np.linspace(10.0, 9.0, len(dates))
    close = np.column_stack([first, second])
    membership = np.ones(close.shape, dtype=bool)
    result = target.compute_salience_return(close, membership, dates)
    month_ends = target._month_end_indices(dates)
    other = np.ones(len(dates), dtype=bool)
    other[month_ends] = False
    assert np.isfinite(result[month_ends[0]]).all()
    assert np.isnan(result[other]).all()


def test_panic_factor_matches_hand_calculation_after_twenty_days():
    stock_return = np.array([0.01, -0.02] * 11, dtype=np.float64)
    market_return = np.array([0.005, -0.01] * 11, dtype=np.float64)
    stock_close = np.cumprod(1.0 + stock_return)[:, None]
    market_close = np.cumprod(1.0 + market_return)
    result = target.compute_panic_factor(stock_close, market_close, 20)
    actual_stock = stock_close[1:, 0] / stock_close[:-1, 0] - 1.0
    actual_market = market_close[1:] / market_close[:-1] - 1.0
    adjusted = (
        np.abs(actual_stock - actual_market)
        / (np.abs(actual_stock) + np.abs(actual_market) + 0.1)
        * actual_stock
    )
    expected = 0.5 * adjusted[-20:].mean() + 0.5 * adjusted[-20:].std(ddof=0)
    np.testing.assert_allclose(result[-1, 0], expected)


def test_panic_market_close_uses_fallback_returns_without_level_jump():
    primary_close = np.array([100.0, 110.0, np.nan, 121.0, 133.1])
    fallback_close = np.array([200.0, 202.0, 204.02, 206.0602, 208.120802])

    market_return = target.combine_close_returns(primary_close, fallback_close)
    market_close = target.synthetic_close_from_returns(market_return)

    np.testing.assert_allclose(
        market_return, [np.nan, 0.10, 0.01, 0.01, 0.10], equal_nan=True
    )
    np.testing.assert_allclose(
        market_close, [1.0, 1.1, 1.111, 1.12211, 1.234321]
    )
    np.testing.assert_allclose(
        market_close[1:] / market_close[:-1] - 1.0, market_return[1:]
    )


def test_night_sleep_factor_includes_self_and_uses_only_past_twenty_days():
    dates = pd.bdate_range("2025-01-01", periods=25)
    base = np.arange(25, dtype=np.float64)
    values = np.column_stack([base, base * 2.0, -base])
    membership = np.ones(values.shape, dtype=bool)
    result = target.compute_night_sleep_factor(values, membership, dates, window=20)
    month_end = target._month_end_indices(dates)[0]
    assert month_end >= 19
    np.testing.assert_allclose(result[month_end], [1.0, 1.0, 1.0])


def test_idiosyncratic_intraday_volatility_is_small_for_common_series():
    rng = np.random.default_rng(8)
    days = 25
    stocks = 60
    common = rng.normal(0.0, 0.01, days)
    intraday = common[:, None] * np.linspace(0.8, 1.2, stocks)[None, :]
    intraday[:, -1] += rng.normal(0.0, 0.02, days)
    open_price = np.full((days, stocks), 10.0)
    close = open_price * (1.0 + intraday)
    membership = np.ones((days, stocks), dtype=bool)
    result = target.compute_idiosyncratic_intraday_volatility(
        open_price, close, membership, window=20, min_stocks=20
    )
    assert result[-1, -1] > np.nanmedian(result[-1, :-1])


def test_idiosyncratic_intraday_volatility_skips_constant_stock():
    rng = np.random.default_rng(20260721)
    open_price = np.full((20, 5), 10.0)
    close = open_price * (1.0 + rng.normal(0.0, 0.01, size=(20, 5)))
    close[:, 0] = open_price[:, 0]
    membership = np.ones((20, 5), dtype=bool)
    result = target.compute_idiosyncratic_intraday_volatility(
        open_price, close, membership, window=20, min_stocks=3
    )
    assert np.isnan(result[-1, 0])
    assert np.isfinite(result[-1, 1:]).all()


def test_market_window_is_blank_when_a_market_day_is_missing():
    values = np.ones((21, 2), dtype=np.float64)
    availability = np.ones((21, 2), dtype=np.float64)
    availability[5, 0] = np.nan
    result = target.require_complete_market_window(values, availability, 20)
    assert np.isnan(result[:19]).all()
    assert np.isnan(result[19:, 0]).all()
    assert np.isfinite(result[19:, 1]).all()


def test_qlib_reader_maps_binary_offset(tmp_path: Path):
    qlib = tmp_path / "qlib"
    feature_dir = qlib / "features" / "sh000985"
    feature_dir.mkdir(parents=True)
    calendar_dir = qlib / "calendars"
    calendar_dir.mkdir()
    (calendar_dir / "day.txt").write_text(
        "2025-01-01\n2025-01-02\n2025-01-03\n2025-01-04\n",
        encoding="utf-8",
    )
    np.array([1.0, 10.0, 11.0], dtype="<f4").tofile(
        feature_dir / "close.day.bin"
    )
    dates = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"])
    result = target.read_qlib_feature(qlib, "sh000985", "close", dates)
    np.testing.assert_allclose(result, [np.nan, 10.0, 11.0], equal_nan=True)


def test_default_directories_do_not_point_to_formal_workbook():
    assert target.DEFAULT_OUTPUT_DIR.name == "final"
    assert target.DEFAULT_MAY_CACHE.name == "may_daily_by_stock"
    assert Path("因子日历2026因子库.xlsx") not in target.DEFAULT_OUTPUT_DIR.parents
