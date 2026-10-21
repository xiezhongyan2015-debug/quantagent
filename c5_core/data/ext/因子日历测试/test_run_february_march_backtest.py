# -*- coding: utf-8 -*-

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run_february_march_backtest as target


def test_load_panel_only_reads_requested_columns(tmp_path: Path) -> None:
    path = tmp_path / "sz000001.parquet"
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-01-03"]),
            "code": ["SZ000001", "SZ000001"],
            "open": [10.0, 10.1],
            "unused": [1.0, 2.0],
        }
    ).to_parquet(path, index=False)

    panel = target.load_panel(
        [path], "2020-01-01", "2020-12-31", ["date", "code", "open"]
    )

    assert panel.columns.tolist() == ["date", "code", "open"]
    assert panel["open"].dtype == np.float32
    assert panel["code"].tolist() == ["sz000001", "sz000001"]


def test_load_panel_reports_missing_cache_columns(tmp_path: Path) -> None:
    path = tmp_path / "sz000001.parquet"
    pd.DataFrame(
        {"date": pd.to_datetime(["2020-01-02"]), "code": ["sz000001"]}
    ).to_parquet(path, index=False)

    with pytest.raises(ValueError, match="缺少本轮所需列"):
        target.load_panel(
            [path], "2020-01-01", "2020-12-31", ["date", "code", "rvjp_5m"]
        )


def test_rolling_ratio_uses_ratio_of_twenty_day_totals() -> None:
    numerator = np.arange(1.0, 22.0)[:, None]
    denominator = np.full((21, 1), 2.0)

    result = target.rolling_ratio(numerator, denominator, 20)

    assert np.isnan(result[18, 0])
    assert result[19, 0] == pytest.approx(np.arange(1.0, 21.0).sum() / 40.0)
    assert result[20, 0] == pytest.approx(np.arange(2.0, 22.0).sum() / 40.0)


def test_tcc_matches_direct_twenty_day_formula() -> None:
    returns = np.tile(np.array([-0.01, 0.0, 0.01]), (20, 1))
    close = np.ones((21, 3), dtype=np.float64)
    for index in range(1, len(close)):
        close[index] = close[index - 1] * (1.0 + returns[index - 1])
    membership = np.ones(close.shape, dtype=bool)

    result = target.compute_tcc(close, membership, 20, 3)

    assert result[20, 0] == pytest.approx(2.0 / 3.0, rel=1e-6)
    assert np.isnan(result[20, 1])
    assert result[20, 2] == pytest.approx(2.0 / 3.0, rel=1e-6)


def _synthetic_panel() -> tuple[pd.DataFrame, pd.DatetimeIndex, list[str], dict, dict]:
    dates = pd.bdate_range("2020-01-01", periods=25)
    stocks = [f"sz{index:06d}" for index in range(8)]
    rows = []
    for day, date in enumerate(dates):
        for stock_index, stock in enumerate(stocks):
            previous = 10.0 * (1.0 + 0.001 * stock_index) ** max(day - 1, 0)
            close = 10.0 * (1.0 + 0.001 * stock_index) ** day
            row = {
                "date": date,
                "code": stock,
                "open": close / (1.0 + 0.0005 * (stock_index - 3.5)),
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "amount": 10_000_000.0,
                "qfq_ratio": 1.0,
                "prev_close": previous,
                "vwap_outside_range_1pct": False,
                "rvjn_5m": 1.0,
                "rvjp_5m": 3.0,
                "golden_split_return_daily": 0.001,
                "gu_1m": 100.0 + stock_index,
                "gd_1m": 120.0 + 2.0 * stock_index,
                "intraday_jumpiness_all_daily": float(stock_index),
                "realized_kurtosis_1m": 3.0,
                "valley_ridge_vwap_ratio_daily": 1.1,
                "peak_amount_daily": 3.0,
                "ridge_amount_daily": 1.0,
                "rbar_up17": 0.01 + stock_index * 0.001,
                "rbar_down17": 0.02 + stock_index * 0.001,
                "r_0931_1000": stock_index * 0.0001,
                "r_1001_1030": stock_index * 0.0002,
                "overnight_return": 0.01,
                "amount_share_entropy_5m": 2.0,
                "upside_vol_share_5m": 0.6,
                "weighted_skew_5m": -0.2,
                "morning_fog_daily": 1.5,
                "large_up_jump_5m": 0.003,
                "intraday_jumpiness_ex_open_close_daily": 0.001 * day
                + 0.0001 * stock_index,
                "burst_follow_amount_daily": float(day + 1),
                "burst_amount_daily": float(2 * (day + 1)),
            }
            rows.append(row)
    panel = pd.DataFrame(rows)
    date_map = {date: index for index, date in enumerate(dates)}
    stock_map = {stock: index for index, stock in enumerate(stocks)}
    return panel, dates, stocks, date_map, stock_map


def test_build_factor_matrices_keeps_key_formula_distinctions() -> None:
    panel, dates, stocks, date_map, stock_map = _synthetic_panel()
    membership = np.ones((len(dates), len(stocks)), dtype=bool)

    factors, _ = target.build_factor_matrices(
        panel, dates, stocks, date_map, stock_map, membership, min_stocks=6
    )

    assert len(factors) == 18
    assert target.FACTOR_META["M03_AMOUNT_SHARE_ENTROPY_5M"]["expected_direction"] == "positive"
    assert target.FACTOR_META["F02_D16_REALIZED_KURTOSIS_1M_20D"]["expected_direction"] == "negative"
    assert set(factors) == {
        code for code, meta in target.FACTOR_META.items() if meta["signal_frequency"] != "unavailable"
    }
    assert factors["F02_D06_JUMP_ASYMMETRY_5M"][-1, 0] == pytest.approx(2.0)
    assert factors["F02_D28_PEAK_RIDGE_AMOUNT_RATIO_20D"][-1, 0] == pytest.approx(3.0)
    assert factors["M31_VOLUME_BURST_FOLLOW_20D"][-1, 0] == pytest.approx(0.5)
    assert factors["F02_D15_CORRECTED_AMPLITUDE_20D"][-1, 0] < 0
    assert factors["F02_D15_CORRECTED_AMPLITUDE_20D"][-1, -1] > 0

    jumpiness = panel.pivot(index="date", columns="code", values="intraday_jumpiness_ex_open_close_daily").to_numpy()
    expected_mean, expected_std = target.january.rolling_mean_std(jumpiness, 20)
    expected = 0.5 * expected_mean + 0.5 * expected_std
    np.testing.assert_allclose(
        factors["M27_INTRADAY_JUMPINESS_20D"][-1], expected[-1], rtol=1e-6
    )
