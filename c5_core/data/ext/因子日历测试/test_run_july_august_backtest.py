# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

import run_july_august_backtest as target


def test_month_to_date_statistics() -> None:
    dates = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-02-03"])
    values = np.array([[1.0], [3.0], [7.0]])
    mean = target._month_to_date_stat(values, dates, "mean")
    maximum = target._month_to_date_stat(values, dates, "max")
    np.testing.assert_allclose(mean[:, 0], [1.0, 2.0, 7.0])
    np.testing.assert_allclose(maximum[:, 0], [1.0, 3.0, 7.0])


def test_trend_clarity_is_one_for_linear_price() -> None:
    close = np.arange(1.0, 302.0)[:, None]
    result = target.compute_trend_clarity(close)
    assert np.isnan(result[239, 0])
    np.testing.assert_allclose(result[240:, 0], 1.0, atol=1e-6)


def test_high_low_spread_uses_previous_and_current_day() -> None:
    high = np.array([[11.0], [12.0], [13.0]])
    low = np.array([[10.0], [10.0], [11.0]])
    amount = np.full((3, 1), 1_000.0)
    ratio = np.ones((3, 1))
    result = target.compute_high_low_illiq(high, low, amount, ratio)
    assert np.isnan(result[0, 0])
    assert np.isfinite(result[1:, 0]).all()


def test_turnover_iv_returns_values_after_full_window() -> None:
    rng = np.random.default_rng(42)
    common = rng.normal(size=(25, 1))
    turnover = common + rng.normal(scale=0.2, size=(25, 60))
    result = target.compute_turnover_iv(turnover, min_stocks=50, window=20)
    assert np.isnan(result[:19]).all()
    assert np.isfinite(result[19:]).all()


def test_rolling_mean_std_ignores_blanks_inside_window() -> None:
    values = np.array([[1.0], [np.nan], [3.0]])
    mean, std = target._rolling_mean_std_available(values, 3)
    np.testing.assert_allclose(mean[2, 0], 2.0)
    np.testing.assert_allclose(std[2, 0], 1.0)


def test_raw_close_division_matches_adjustment_ratio() -> None:
    close = np.array([[12.0, 20.0]], dtype=np.float32)
    ratio = np.array([[1.2, 2.0]], dtype=np.float32)
    raw = np.full(close.shape, np.nan, dtype=np.float32)
    np.divide(close, ratio, out=raw, where=ratio > 0)
    np.testing.assert_allclose(raw, [[10.0, 10.0]])


def test_cross_sectional_rank_uses_average_rank_for_ties() -> None:
    values = np.array([[1.0, 2.0, 2.0, 4.0]])
    membership = np.ones(values.shape, dtype=bool)
    result = target.cross_sectional_rank(values, membership, min_stocks=4)
    np.testing.assert_allclose(result[0], [0.25, 0.625, 0.625, 1.0])


def test_adjusted_fuzzy_spread_keeps_positive_values_and_negative_sum() -> None:
    values = np.array(
        [
            [-1.0, 1.0, -2.0, 2.0],
            [-2.0, 2.0, -1.0, 1.0],
            [-3.0, 3.0, -4.0, 4.0],
        ]
    )
    membership = np.ones(values.shape, dtype=bool)
    result = target.compute_adjusted_fuzzy_spread(
        values, membership, min_stocks=4, window=2
    )
    assert np.isnan(result[0]).all()
    for day in [1, 2]:
        positive = values[day] >= 0
        negative = values[day] < 0
        np.testing.assert_allclose(result[day, positive], values[day, positive])
        np.testing.assert_allclose(
            np.sum(result[day, negative]), np.sum(values[day, negative])
        )


def test_new_minute_factor_metadata_is_present() -> None:
    expected = {
        "F02_D12_LONE_GOOSE",
        "J07_D01_FUZZY_QTY_RATIO",
        "J07_D07_EXTREME_REVERSAL",
        "J07_D10_CLOSE_AMOUNT_SHARE",
        "J07_D25_AMT_MAX",
        "J08_D01_ADJ_FUZZY_SPREAD",
        "J08_D05_DISASTER_REBUILD",
        "J08_D10_AMOUNT_VARIANCE",
        "J08_D17_LARGE_VOL_PV_CORR",
        "J08_D26_IDEAL_AMPLITUDE",
        "J07_D05_SIG_JUMP_ASYM",
        "J07_D08_AMOUNT_AUTOCORR",
        "J08_D08_ABSRET_AMOUNT_CORR",
        "J08_D25_AMT_MIN",
    }
    assert expected.issubset(target.FACTOR_META)


def test_four_parameter_fixed_factors_are_marked_trial() -> None:
    names = {
        "J07_D05_SIG_JUMP_ASYM",
        "J07_D08_AMOUNT_AUTOCORR",
        "J08_D08_ABSRET_AMOUNT_CORR",
        "J08_D25_AMT_MIN",
    }
    for name in names:
        assert target.FACTOR_META[name]["formula_mode"] == "trial"
        assert target.FACTOR_META[name]["strict_status"] == "trial_fixed_parameters"
        assert target.TRIAL_PARAMETERS[name]["formula_mode"] == "trial"


def test_absret_amount_trial_uses_ten_day_mean() -> None:
    values = np.arange(1.0, 12.0)[:, None]
    mean, _ = target._rolling_mean_std_available(values, 10)
    assert np.isnan(mean[:9]).all()
    np.testing.assert_allclose(mean[9, 0], 5.5)
    np.testing.assert_allclose(mean[10, 0], 6.5)


def test_completion_factor_list_returns_before_unneeded_formulas(monkeypatch) -> None:
    dates = pd.bdate_range("2024-01-02", periods=25)
    codes = ["a", "b", "c", "d"]
    row_count = len(dates) * len(codes)
    rng = np.random.default_rng(20260723)
    panel = pd.DataFrame(
        {"date": np.repeat(dates, len(codes)), "code": np.tile(codes, len(dates))}
    )
    columns = dict.fromkeys(
        target.BASE_COLUMNS
        + target.MAY_COLUMNS
        + target.JUNE_COLUMNS
        + target.JULY_AUGUST_COLUMNS
        + target.LONE_GOOSE_COLUMNS
    )
    for column in columns:
        if column == "vwap_outside_range_1pct":
            panel[column] = False
        elif column == "qfq_ratio":
            panel[column] = np.ones(row_count, dtype=np.float32)
        elif column in {"open", "high", "low", "close"}:
            panel[column] = rng.uniform(3.0, 30.0, row_count).astype(np.float32)
        elif column in {"volume", "amount", "float_shares"}:
            panel[column] = rng.uniform(1e6, 1e8, row_count).astype(np.float32)
        else:
            panel[column] = rng.normal(size=row_count).astype(np.float32)
    stocks, date_map, stock_map = target.january.make_axes(panel, dates)
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    settings = target.january.Settings(
        cache_dir=Path("."),
        output_dir=Path("."),
        qlib_dir=Path("."),
        min_stocks=len(stocks),
    )
    requested = [
        "F02_D12_LONE_GOOSE",
        "J07_D01_FUZZY_QTY_RATIO",
        "J07_D07_EXTREME_REVERSAL",
        "J07_D10_CLOSE_AMOUNT_SHARE",
        "J07_D25_AMT_MAX",
        "J08_D01_ADJ_FUZZY_SPREAD",
        "J08_D05_DISASTER_REBUILD",
        "J08_D10_AMOUNT_VARIANCE",
        "J08_D17_LARGE_VOL_PV_CORR",
        "J08_D26_IDEAL_AMPLITUDE",
        "J07_D05_SIG_JUMP_ASYM",
        "J07_D08_AMOUNT_AUTOCORR",
        "J08_D08_ABSRET_AMOUNT_CORR",
        "J08_D25_AMT_MIN",
    ]
    monkeypatch.setattr(
        target,
        "compute_turnover_iv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("不应计算名单外因子")
        ),
    )

    factors, arrays = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
        requested_codes=requested,
    )

    assert list(factors) == requested
    assert all(values.shape == membership.shape for values in factors.values())
    assert arrays["open"].shape == membership.shape
