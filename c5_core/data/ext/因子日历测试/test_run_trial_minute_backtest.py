# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

import run_january_minute_backtest as january
import run_trial_minute_backtest as target


def test_reverse_percentile_uses_descending_average_rank() -> None:
    values = np.array([[4.0, 2.0, 2.0, 1.0]])
    membership = np.ones(values.shape, dtype=bool)
    result = target.cross_sectional_reverse_percentile(values, membership, 4)
    np.testing.assert_allclose(result[0], [0.25, 0.625, 0.625, 1.0])


def test_rolling_statistics_require_twenty_finite_days() -> None:
    values = np.arange(1.0, 22.0)[:, None]
    mean, standard = target._rolling_mean_std(values, 20)
    assert np.isnan(mean[:19]).all()
    np.testing.assert_allclose(mean[19, 0], 10.5)
    np.testing.assert_allclose(standard[19, 0], np.std(np.arange(1.0, 21.0)))


def test_factor_matrices_create_nine_trial_factors() -> None:
    dates = pd.bdate_range("2025-01-02", periods=25)
    stocks = ["sh600000", "sh600004", "sh600006"]
    rows = []
    for day_index, day in enumerate(dates):
        for stock_index, code in enumerate(stocks):
            base = float(day_index + stock_index + 1)
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "open": 10.0 + base * 0.01,
                    "high": 10.2 + base * 0.01,
                    "low": 9.8 + base * 0.01,
                    "close": 10.1 + base * 0.01,
                    "volume": 1_000_000.0,
                    "amount": 10_000_000.0 + base,
                    "float_shares": 100_000_000.0,
                    "qfq_ratio": 1.0,
                    "vwap_outside_range_1pct": False,
                    "m15_vsa_low_close_gap_trial": 0.01 * base,
                    "a22_intraday_snr_trial": 0.02 * base,
                    "may13_intraday_abnormal_volume_trial": 1.0 + 0.01 * base,
                    "j06_d02_jump_indicator_trial": float(day_index % 2),
                    "j06_d07_high_frequency_resiliency_trial": 0.001 * base,
                    "j06_d20_rm_min_trial": -0.001 * base,
                    "j06_d20_std_min_trial": 0.01 + 0.0001 * stock_index,
                    "j09_d07_brave_peak_daily_trial": 0.1 * base,
                    "j09_d11_naive_active_share_trial": 0.5 + 0.001 * base,
                    "j09_d19_skewed_momentum_daily_trial": 0.01 * base,
                }
            )
    panel = pd.DataFrame(rows)
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {code: index for index, code in enumerate(stocks)}
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    settings = january.Settings(
        cache_dir=Path("."),
        output_dir=Path("."),
        qlib_dir=Path("."),
        start=str(dates[0].date()),
        backtest_start=str(dates[0].date()),
        end=str(dates[-1].date()),
        min_stocks=2,
    )
    factors, arrays = target.build_factor_matrices(
        panel,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )
    assert set(factors) == set(target.FACTOR_META)
    assert arrays["open"].shape == membership.shape
    assert np.isfinite(factors["J06_D20_CO_MIN"]).all()
    assert np.isnan(factors["J06_D02_JUMP_INTENSITY"][:19]).all()
    assert np.isfinite(factors["J06_D02_JUMP_INTENSITY"][19:]).all()
    assert np.isfinite(factors["J09_D07_BRAVE_PEAK"][19:]).all()
    assert np.isfinite(factors["J09_D19_SKEWED_MOMENTUM_RANGE"][19:]).all()


def test_all_metadata_and_status_rows_are_marked_trial() -> None:
    factors = {code: np.ones((1, 1)) for code in target.FACTOR_META}
    status = target.build_status_frame(factors)
    assert len(status) == 9
    assert status["formula_mode"].eq("trial").all()
    assert status["formula_label"].eq("试算").all()
