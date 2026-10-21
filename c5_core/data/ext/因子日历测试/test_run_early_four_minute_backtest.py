# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd

import run_early_four_minute_backtest as target
import run_january_minute_backtest as january


def test_rolling_mean_and_sample_std_use_exact_complete_window() -> None:
    values = np.arange(1.0, 26.0, dtype=np.float32)[:, None]
    mean, std = target.rolling_mean_sample_std(values, window=20, block_size=1)

    assert np.isnan(mean[18, 0])
    np.testing.assert_allclose(mean[19, 0], np.mean(np.arange(1.0, 21.0)))
    np.testing.assert_allclose(
        std[19, 0], np.std(np.arange(1.0, 21.0), ddof=1)
    )

    values[10, 0] = np.nan
    mean_with_gap, std_with_gap = target.rolling_mean_sample_std(
        values, window=20, block_size=1
    )
    assert np.isnan(mean_with_gap[19, 0])
    assert np.isnan(std_with_gap[19, 0])


def _synthetic_inputs():
    dates = pd.bdate_range("2026-04-20", periods=25)
    stocks = ["sh600000", "sh600001", "sz000001"]
    rows = []
    dazzling_offsets = [0.0, 1.0, 3.0]
    for day, date in enumerate(dates):
        for stock_number, code in enumerate(stocks):
            row = {
                "date": date,
                "code": code,
                "open": 10.0 + stock_number,
                "close": 10.1 + stock_number,
                "amount": 10_000_000.0,
                "qfq_ratio": 1.0,
                "vwap_outside_range_1pct": False,
                "high_volume_realized_skew_daily": day + stock_number * 0.1,
                "dazzling_volatility_daily": day + dazzling_offsets[stock_number],
                "p_shaped_volume_distribution_daily": 0.2 + stock_number * 0.01,
            }
            for number in range(1, 6):
                row[f"top_return_time_{number}"] = (
                    569 + number + stock_number
                )
            rows.append(row)
    panel = pd.DataFrame(rows)

    market_rows = []
    for day, date in enumerate(dates):
        for minute in range(570, 577):
            market_rows.append(
                {
                    "date": date,
                    "minute_of_day": minute,
                    "return_stock_count": 3,
                    "market_mean_return": day * 0.00001 + minute * 0.000001,
                    "market_std_return_samp": 0.02,
                }
            )
    return panel, pd.DataFrame(market_rows), dates, stocks


def test_builds_all_four_factor_values_from_small_inputs() -> None:
    panel, market, dates, stocks = _synthetic_inputs()
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {code: index for index, code in enumerate(stocks)}
    membership = np.ones((len(dates), len(stocks)), dtype=bool)
    settings = january.Settings(
        cache_dir=Path("."),
        output_dir=Path("."),
        qlib_dir=Path("."),
        start=str(dates[0].date()),
        end=str(dates[-1].date()),
        min_stocks=2,
    )

    factors, arrays = target.build_factor_matrices(
        panel,
        market,
        dates,
        stocks,
        date_map,
        stock_map,
        membership,
        settings,
    )

    assert set(factors) == set(target.FACTOR_META)
    np.testing.assert_allclose(
        factors["M25_HIGH_VOLUME_REALIZED_SKEW"][-1],
        [24.0, 24.1, 24.2],
        rtol=1e-6,
    )
    np.testing.assert_allclose(
        factors["A10_P_SHAPED_VOLUME_DISTRIBUTION"][-1],
        [0.2, 0.21, 0.22],
        rtol=1e-6,
    )
    deviations = np.abs(np.array([0.0, 1.0, 3.0]) - 4.0 / 3.0)
    np.testing.assert_allclose(
        factors["A06_DAZZLING_VOLATILITY_20D"][-1],
        0.5 * deviations,
        rtol=1e-6,
        atol=1e-6,
    )
    np.testing.assert_allclose(
        factors["MAY25_TOP_RETURN_PRICE_RESONANCE"][-1],
        np.array([1.0 / 3.0, 2.0 / 3.0, 1.0]) / 0.02,
        rtol=1e-6,
    )
    assert arrays["open"].shape == (25, 3)


def test_factor_status_marks_only_four_strict_formulas() -> None:
    dummy = {
        code: np.ones((2, 2), dtype=np.float32) for code in target.FACTOR_META
    }
    status = target.build_status_frame(dummy)
    assert len(status) == 4
    assert status["calculated"].all()
    assert status["strict_status"].eq("strict_formula").all()
    assert status["finite_values"].eq(4).all()
