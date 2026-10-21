# -*- coding: utf-8 -*-
"""六月回测脚本的轻量检查。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_june_minute_features as june_cache
import run_june_backtest as target


CALCULATED_FREQUENCIES = {
    "J06_D01_STRUCTURED_REVERSAL_21D_10M": "daily",
    "J06_D04_FUZZY_AMOUNT_RATIO_20D": "monthly",
    "J06_D06_CSAD_TIME_SERIES_120D": "monthly",
    "J06_D08_WAITING_RESCUE_20D": "monthly",
    "J06_D10_VOLATILITY_PANIC_20D": "monthly",
    "J06_D11_DOWNSIDE_REALIZED_VOL_5M": "daily",
    "J06_D13_PATV_5M": "daily",
    "J06_D17_PV_CORR_TREND_20D": "monthly",
    "J06_D19_INTRADAY_CVAR_5PCT": "monthly",
    "J06_D23_IDIOSYNCRATIC_OVERNIGHT_VOL_20D": "daily",
}


def make_patv_panel() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    date = pd.Timestamp("2025-06-03")
    for code in ["a", "b", "c"]:
        row: dict[str, object] = {"date": date, "code": code}
        for position, column in enumerate(june_cache.ATV_COLUMNS):
            if code == "a":
                value = 1.0 if position % 2 == 0 else 3.0
            elif code == "b":
                value = 2.0
            else:
                value = 3.0 if position % 2 == 0 else 1.0
            row[column] = value
        rows.append(row)
    return pd.DataFrame(rows)


def test_factor_set_frequency_and_required_fields() -> None:
    calculated = {
        code: meta["signal_frequency"]
        for code, meta in target.FACTOR_META.items()
        if meta["signal_frequency"] != "unavailable"
    }
    assert calculated == CALCULATED_FREQUENCIES
    assert target.FACTOR_META["J06_D19_INTRADAY_CVAR_5PCT"]["strict_status"] == (
        "formula_approximated"
    )
    assert target.BASE_COLUMNS == [
        "date",
        "code",
        "open",
        "close",
        "volume",
        "amount",
        "float_shares",
        "qfq_ratio",
        "vwap_outside_range_1pct",
        "overnight_return",
    ]
    assert target.JUNE_COLUMNS == [
        "date",
        "code",
        "structure_reversal_21d_10m",
        "fuzzy_amount_ratio_daily",
        "wait_rescue_follow_daily",
        "downside_realized_vol_5m",
        "intraday_cvar_5pct_daily",
        "intraday_volatility_1m",
        "pv_corr_daily",
        *june_cache.ATV_COLUMNS,
    ]


def test_rolling_slope_return_and_month_mean() -> None:
    values = np.column_stack([np.arange(5.0), np.arange(5.0) * 2.0])
    values[2, 1] = np.nan
    slope = target._rolling_slope(values, 3)
    assert np.isnan(slope[:2]).all()
    assert slope[2:, 0] == pytest.approx(np.ones(3))
    assert np.isnan(slope[2:, 1]).all()

    close = np.array([[10.0], [11.0], [12.0], [9.0], [18.0]])
    rolling_return = target._rolling_return(close, 2)
    assert np.isnan(rolling_return[:2]).all()
    assert rolling_return[2:, 0] == pytest.approx([0.2, 9.0 / 11.0 - 1.0, 0.5])

    dates = pd.DatetimeIndex(
        ["2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04"]
    )
    daily = np.array([[1.0, np.nan], [3.0, 2.0], [5.0, 4.0], [np.nan, 6.0]])
    month_mean = target._month_to_date_mean(daily, dates)
    expected = np.array([[1.0, np.nan], [2.0, 2.0], [5.0, 4.0], [5.0, 5.0]])
    np.testing.assert_allclose(month_mean, expected, equal_nan=True)


def test_mean_std_composite_and_volatility_panic_formula() -> None:
    values = np.arange(1.0, 5.0).reshape(4, 1)
    composite = target._mean_std_composite(values, 3)
    assert np.isnan(composite[:2]).all()
    expected_first = 0.5 * 2.0 + 0.5 * np.std([1.0, 2.0, 3.0], ddof=0)
    expected_second = 0.5 * 3.0 + 0.5 * np.std([2.0, 3.0, 4.0], ddof=0)
    assert composite[2:, 0] == pytest.approx([expected_first, expected_second])

    stock = np.array([[0.10], [-0.20], [0.30]], dtype=np.float32)
    market = np.array([0.00, 0.10, -0.10], dtype=np.float32)
    intraday = np.array([[2.0], [1.0], [0.5]], dtype=np.float32)
    fear = np.abs(stock[:, 0] - market) / (
        np.abs(stock[:, 0]) + np.abs(market) + 0.1
    )
    adjusted = fear * stock[:, 0] * intraday[:, 0]
    expected = 0.5 * np.mean(adjusted) + 0.5 * np.std(adjusted, ddof=0)
    result = target._compute_volatility_panic(stock, market, intraday, 3)
    assert result.dtype == np.dtype("float32")
    assert np.isnan(result[:2]).all()
    assert result[2, 0] == pytest.approx(expected)


def test_qlib_market_return_uses_fallback_only_when_primary_pair_is_missing(
    tmp_path: Path,
) -> None:
    qlib_dir = tmp_path / "qlib"
    calendar_dir = qlib_dir / "calendars"
    calendar_dir.mkdir(parents=True)
    dates = pd.date_range("2025-01-01", periods=5)
    (calendar_dir / "day.txt").write_text(
        "\n".join(day.strftime("%Y-%m-%d") for day in dates) + "\n",
        encoding="utf-8",
    )
    closes = {
        "sh000985": [100.0, 110.0, np.nan, 121.0, 133.1],
        "sh000906": [200.0, 202.0, 204.02, 206.0602, 208.120802],
    }
    for instrument, values in closes.items():
        feature_dir = qlib_dir / "features" / instrument
        feature_dir.mkdir(parents=True)
        np.array([0.0, *values], dtype="<f4").tofile(
            feature_dir / "close.day.bin"
        )

    result = target._read_qlib_close_return(
        qlib_dir, "sh000985", dates, fallback_instrument="sh000906"
    )

    np.testing.assert_allclose(
        result,
        [np.nan, 0.10, 0.01, 0.01, 0.10],
        rtol=1e-5,
        equal_nan=True,
    )


def test_patv_ranks_all_available_stocks_and_uses_pearson_kurtosis() -> None:
    panel = make_patv_panel()
    result = target._compute_patv_long(panel)

    assert result.dtype == np.dtype("float32")
    # a、c的48个排名各有一半为1/3、一半为1：均值/标准差=2，总体峰度=1。
    assert result[0] == pytest.approx(3.0)
    assert np.isnan(result[1])
    assert result[2] == pytest.approx(3.0)

    collapsed = target._collapse_june_panel(panel.copy())
    assert not set(june_cache.ATV_COLUMNS) & set(collapsed.columns)
    assert collapsed.columns.tolist() == ["date", "code", "patv_daily"]
    assert collapsed["patv_daily"].dtype == np.dtype("float32")


def test_csad_monthly_matches_twenty_over_one_hundred_twenty_ratio() -> None:
    dates = pd.bdate_range("2024-01-02", periods=120)
    x = np.linspace(0.0, 8.0 * np.pi, 120)
    columns = []
    for stock in range(10):
        columns.append(
            np.sin(x * (1.0 + stock * 0.015))
            + 0.15 * np.cos(x * (stock + 1) / 5.0)
            + stock * np.arange(120) * 0.0002
        )
    daily_return = np.column_stack(columns)
    membership = np.ones_like(daily_return, dtype=bool)

    result = target._compute_csad_monthly(
        daily_return, membership, dates, min_stocks=10
    )

    center = daily_return[:, 0]
    csad = np.sum(np.abs(daily_return[:, 1:] - center[:, None]), axis=1) / 10.0
    long_ratio = np.std(csad, ddof=0) / np.std(center, ddof=0)
    short_ratio = np.std(csad[-20:], ddof=0) / np.std(center[-20:], ddof=0)
    assert np.isnan(result[:-1]).all()
    assert result[-1, 0] == pytest.approx(-short_ratio / long_ratio)


def test_overnight_pca_uses_every_complete_stock() -> None:
    x = np.linspace(-2.0, 2.0, 20)
    values = np.column_stack(
        [
            x + 0.10 * np.sin(np.arange(20)),
            -0.5 * x + 0.08 * np.cos(np.arange(20)),
            np.sin(x * 2.0) + 0.03 * x,
            np.cos(x * 1.5) - 0.04 * x,
        ]
    )

    result = target._compute_overnight_idiosyncratic_volatility(
        values, min_stocks=4, window=20
    )

    centered = values - np.mean(values, axis=0, keepdims=True)
    u, singular, _ = np.linalg.svd(centered, full_matrices=False)
    variance = singular * singular
    component_count = 1 if variance[0] / np.sum(variance) >= 0.20 else 2
    scores = u[:, :component_count] * singular[:component_count]
    design = np.column_stack([np.ones(20), scores])
    coefficients, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
    expected = np.std(values - design @ coefficients, axis=0, ddof=0)

    assert result.dtype == np.dtype("float32")
    assert np.isnan(result[:-1]).all()
    np.testing.assert_allclose(result[-1], expected, rtol=1e-6, atol=1e-7)
